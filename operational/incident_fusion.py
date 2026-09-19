"""Deterministic Incident fusion. AI does not create or merge Incidents.

Corroboration requires agreement: two or more independent sources must support
the same operational claim. Contradictory sources are conflict, not
corroboration.

Source independence is provenance-derived, not observation-id based.
Social/Ushahidi prefer metadata.ingestion.source_metadata.author_id.
Without an author, a generic platform source_id (for example `replay`) is one
identity and does not corroborate with another unattributed post.

Field precedence is not a single universal rank:

- Victim: verified responder/field > corroborated independent evidence >
  drone indication > single social report.
- Structural / access: drone or field assessment > responder radio > social.

The fusion window uses contributing observation timestamps, never processing
or scenario_time. `last_updated_at` may still reflect processing freshness.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from operational.ids import (
    evidence_id,
    format_time,
    incident_id,
    parse_time,
    schema_version,
    unique,
)
from operational.validation import validate_evidence, validate_incident


DEFAULT_FUSION_WINDOW = timedelta(minutes=30)

TRUSTED_SOURCE_TYPES = frozenset({"radio", "drone", "field_note"})
RESPONDER_SOURCE_TYPES = frozenset({"radio", "field_note"})
SOCIAL_SOURCE_TYPES = frozenset({"social", "ushahidi"})
GENERIC_PLATFORM_SOURCE_IDS = frozenset({
    "replay", "ushahidi", "social", "twitter", "mastodon", "facebook",
    "community", "platform",
})
POSITIVE_VICTIM_TYPES = frozenset({
    "possible_individual", "possible_multiple", "confirmed_individual", "confirmed_multiple",
})
POSITIVE_STRUCTURAL = frozenset({"minor", "major", "severe"})
POSITIVE_ACCESS = frozenset({"limited", "blocked"})

# Field-specific source ranks. Drone and field are equal for structural/access
# and both outrank radio even when verification_status is equal.
VICTIM_SOURCE_RANK = {
    "radio": 40,
    "field_note": 40,
    "drone": 20,
    "ushahidi": 8,
    "social": 5,
    "reliefweb": 5,
    "gdacs": 5,
    "system": 0,
}
STRUCTURAL_SOURCE_RANK = {
    "drone": 40,
    "field_note": 40,
    "radio": 20,
    "ushahidi": 8,
    "social": 5,
    "reliefweb": 5,
    "gdacs": 5,
    "system": 0,
}
ACCESS_SOURCE_RANK = dict(STRUCTURAL_SOURCE_RANK)
VERIFICATION_RANK = {
    "verified": 40,
    "corroborated": 20,
    "unverified": 10,
    "potential": 0,
}
INDICATION_RANK = {
    "confirmed_multiple": 50,
    "confirmed_individual": 40,
    "possible_multiple": 30,
    "possible_individual": 20,
    "unknown": 10,
    "none": 0,
}
STRUCTURAL_RANK = {
    "severe": 40,
    "major": 30,
    "minor": 20,
    "none": 10,
    "unknown": 0,
}
ACCESS_RANK = {
    "blocked": 40,
    "limited": 30,
    "open": 20,
    "unknown": 0,
}
STRUCTURAL_FROM_HAZARD = {
    "none": "none",
    "reported_damage": "minor",
    "minor_damage": "minor",
    "major_damage": "major",
    "severe_collapse": "severe",
    "active_secondary_hazard": "severe",
    "unknown": "unknown",
}
ACCESS_FROM_STATUS = {
    "open": "open",
    "limited": "limited",
    "possibly_blocked": "limited",
    "blocked": "blocked",
    "unknown": "unknown",
}


@dataclass(frozen=True)
class FusionConfig:
    """Temporal proximity window for same-site, same-type fusion.

    Default is 30 minutes for the hackathon MVP. The window is measured from
    first/latest contributing observation time, not last_updated_at /
    scenario_time. Observations outside this window create a new Incident even
    at the same Site and incident_type. Compatible types are exact
    incident_type equality only.
    """

    window: timedelta = DEFAULT_FUSION_WINDOW


@dataclass(frozen=True)
class FusionResult:
    action: str  # created | updated | none
    incident: dict | None
    evidence: dict | None
    reason: str


class IncidentFusion:
    def __init__(self, config: FusionConfig | None = None):
        self.config = config or FusionConfig()

    def fuse(
        self,
        observation: dict,
        resolution: dict,
        existing_incidents: list[dict],
        *,
        scenario_time: str | None = None,
    ) -> FusionResult:
        if resolution.get("resolution_status") != "resolved" or not resolution.get("resolved_site_id"):
            return FusionResult("none", None, None, "location_not_resolved")

        site_id = resolution["resolved_site_id"]
        incident_type = observation["incident_type"]
        observation_id = observation["observation_id"]
        observed_at = observation["timestamp"]
        evidence = build_evidence(observation)

        compatible = [
            incident
            for incident in existing_incidents
            if incident["site_id"] == site_id
            and incident["incident_type"] == incident_type
            and incident["status"] == "active"
            and within_fusion_window(incident, observed_at, self.config.window)
        ]
        compatible.sort(key=lambda item: (latest_evidence_at(item), item["incident_id"]))

        if compatible:
            current = deepcopy(compatible[-1])
            if observation_id in current["observation_ids"]:
                return FusionResult("none", current, evidence, "observation_already_fused")
            updated = apply_observation_to_incident(current, observation, evidence, scenario_time)
            return FusionResult("updated", updated, evidence, "same_site_type_within_window")

        created = build_incident(observation, site_id, evidence, scenario_time)
        return FusionResult("created", created, evidence, "no_compatible_incident")


def latest_evidence_at(incident: dict) -> str:
    metadata = incident.get("metadata") or {}
    return metadata.get("latest_evidence_at") or incident["first_observed_at"]


def within_fusion_window(incident: dict, observed_at: str, window: timedelta) -> bool:
    start = parse_time(incident["first_observed_at"])
    end = parse_time(latest_evidence_at(incident))
    if end < start:
        start, end = end, start
    instant = parse_time(observed_at)
    return (start - window) <= instant <= (end + window)


def build_evidence(observation: dict) -> dict:
    field_support = ["incident_type"]
    location = observation.get("location") or {}
    if location.get("raw_text") or location.get("label") or location.get("latitude") is not None:
        field_support.append("location")
    if (observation.get("victim_indication") or {}).get("type") not in {None, "unknown"}:
        field_support.append("victim_indication")
    if observation.get("structural_hazard") not in {None, "unknown"}:
        field_support.append("structural_hazard")
    if observation.get("access_status") not in {None, "unknown"}:
        field_support.append("access_status")
    field_support.append("verification_status")
    payload = {
        "schema_version": schema_version(),
        "evidence_id": evidence_id(observation["observation_id"]),
        "observation_id": observation["observation_id"],
        "source_type": observation["source_type"],
        "source_id": observation.get("source_id"),
        "summary": _evidence_summary(observation),
        "verification_status": observation["verification_status"],
        "timestamp": observation["timestamp"],
        "field_support": unique(field_support),
    }
    return validate_evidence(payload)


def build_incident(observation: dict, site_id: str, evidence: dict, scenario_time: str | None) -> dict:
    identity = incident_id(site_id, observation["incident_type"], observation["observation_id"])
    observed_at = observation["timestamp"]
    updated_at = observed_at
    if scenario_time:
        updated_at = format_time(max(parse_time(updated_at), parse_time(scenario_time)))
    incident = {
        "schema_version": schema_version(),
        "incident_id": identity,
        "site_id": site_id,
        "incident_type": observation["incident_type"],
        "status": "active",
        "verification_status": "unverified",
        "observation_ids": [observation["observation_id"]],
        "evidence_ids": [evidence["evidence_id"]],
        "first_observed_at": observed_at,
        "last_updated_at": updated_at,
        "metadata": {
            "latest_evidence_at": observed_at,
            "decision_trace": {
                "fusion_action": "created",
                "created_from_observation_id": observation["observation_id"],
                "last_observation_id": observation["observation_id"],
            },
        },
    }
    contributions = [_contribution(observation)]
    return _finalize_incident(incident, contributions, observation["incident_type"])


def apply_observation_to_incident(
    incident: dict,
    observation: dict,
    evidence: dict,
    scenario_time: str | None,
) -> dict:
    contributions = _contributions_from_incident(incident)
    contributions.append(_contribution(observation))
    incident["observation_ids"] = unique(incident["observation_ids"] + [observation["observation_id"]])
    incident["evidence_ids"] = unique(incident["evidence_ids"] + [evidence["evidence_id"]])
    last_updated = max(parse_time(incident["last_updated_at"]), parse_time(observation["timestamp"]))
    if scenario_time:
        last_updated = max(last_updated, parse_time(scenario_time))
    if parse_time(observation["timestamp"]) < parse_time(incident["first_observed_at"]):
        incident["first_observed_at"] = observation["timestamp"]
    incident["last_updated_at"] = format_time(last_updated)
    metadata = dict(incident.get("metadata") or {})
    evidence_times = [parse_time(item["timestamp"]) for item in contributions if item.get("timestamp")]
    metadata["latest_evidence_at"] = format_time(max(evidence_times)) if evidence_times else observation["timestamp"]
    trace = dict(metadata.get("decision_trace") or {})
    trace["fusion_action"] = "updated"
    trace["last_observation_id"] = observation["observation_id"]
    metadata["decision_trace"] = trace
    incident["metadata"] = metadata
    return _finalize_incident(incident, contributions, incident["incident_type"])


def _finalize_incident(incident: dict, contributions: list[dict], incident_type: str) -> dict:
    classification = classify_evidence(contributions, incident_type)
    incident["verification_status"] = aggregate_verification(contributions, incident_type)
    _attach_states(incident, contributions, incident_type, classification["independent_support_count"])
    if should_invalidate(incident, contributions):
        incident["status"] = "invalidated"
        classification = classify_evidence(contributions, incident_type)
    metadata = dict(incident.get("metadata") or {})
    metadata["evidence_conflict"] = classification["evidence_conflict"]
    metadata["supporting_observation_ids"] = classification["supporting_observation_ids"]
    metadata["conflicting_observation_ids"] = classification["conflicting_observation_ids"]
    if incident["status"] == "invalidated":
        invalidation = dict(metadata.get("invalidation") or {})
        invalidation.setdefault("reason", "trusted_negative_verification")
        negatives = [
            item["observation_id"] for item in contributions
            if _is_trusted_verified(item) and claim_polarity(item, incident_type) == "conflict"
        ]
        if negatives:
            invalidation["by_observation_ids"] = unique(negatives)
        metadata["invalidation"] = invalidation
        trace = dict(metadata.get("decision_trace") or {})
        trace["invalidated"] = True
        metadata["decision_trace"] = trace
    incident["metadata"] = metadata
    store_contributions(incident, contributions)
    return validate_incident(incident)


def independent_source_key(observation: dict) -> tuple[str, str, str]:
    """Deterministic independence identity for corroboration.

    Social/Ushahidi: prefer author_id from ingestion provenance. Without an
    author, do not treat distinct observation IDs on the same generic platform
    source_id as independent. Radio/drone/field_note use the unit/responder id.
    """
    source_type = observation.get("source_type") or "system"
    source_id = _nonempty(observation.get("source_id"))
    if source_type in SOCIAL_SOURCE_TYPES:
        author_id = _author_id(observation)
        if author_id:
            return (source_type, "author", author_id)
        return (source_type, "platform", source_id or "unattributed")
    if source_type == "radio":
        unit = _speaker_id(observation) or source_id or observation.get("observation_id") or "radio"
        return ("radio", "unit", unit)
    if source_type == "drone":
        return ("drone", "unit", source_id or observation.get("observation_id") or "drone")
    if source_type == "field_note":
        return ("field_note", "unit", source_id or observation.get("observation_id") or "field")
    return (source_type, "id", source_id or observation.get("observation_id") or source_type)


def source_key_string(key: tuple[str, str, str]) -> str:
    return "|".join(key)


def claim_polarity(item: dict, incident_type: str) -> str:
    """support | conflict | neutral for the Incident's operational claim."""
    if incident_type == "trapped_victims":
        victim = item.get("victim_type") or "unknown"
        if victim in POSITIVE_VICTIM_TYPES:
            return "support"
        if victim == "none":
            return "conflict"
        return "neutral"
    if incident_type == "structural_damage":
        structural = item.get("structural") or "unknown"
        if structural in POSITIVE_STRUCTURAL:
            return "support"
        if structural == "none":
            return "conflict"
        return "neutral"
    if incident_type == "access_blocked":
        access = item.get("access") or "unknown"
        if access in POSITIVE_ACCESS:
            return "support"
        if access == "open":
            return "conflict"
        return "neutral"
    victim = item.get("victim_type") or "unknown"
    structural = item.get("structural") or "unknown"
    access = item.get("access") or "unknown"
    if victim in POSITIVE_VICTIM_TYPES or structural in POSITIVE_STRUCTURAL or access in POSITIVE_ACCESS:
        return "support"
    if victim == "none" or structural == "none" or access == "open":
        return "conflict"
    return "neutral"


def classify_evidence(contributions: list[dict], incident_type: str) -> dict:
    supporting = []
    conflicting = []
    for item in contributions:
        polarity = claim_polarity(item, incident_type)
        if polarity == "support":
            supporting.append(item)
        elif polarity == "conflict":
            conflicting.append(item)
    independent_support = {_identity(item) for item in supporting}
    return {
        "evidence_conflict": bool(supporting) and bool(conflicting),
        "supporting_observation_ids": unique(item["observation_id"] for item in supporting if item.get("observation_id")),
        "conflicting_observation_ids": unique(item["observation_id"] for item in conflicting if item.get("observation_id")),
        "independent_support_count": len(independent_support),
        "supporting": supporting,
        "conflicting": conflicting,
    }


def aggregate_verification(contributions: list[dict], incident_type: str | None = None) -> str:
    """Categorical evidence progression: potential → unverified → corroborated → verified.

    corroborated requires two or more independent sources supporting the same
    operational claim. A trusted verified observation only yields verified when
    it supports the claim. Trusted verified absence is invalidation, not
    verification of the original rumor.
    """
    if not contributions:
        return "potential"
    inferred_type = incident_type or _infer_incident_type(contributions)
    classification = classify_evidence(contributions, inferred_type)
    trusted_verified_support = any(
        _is_trusted_verified(item) and claim_polarity(item, inferred_type) == "support"
        for item in contributions
    )
    if trusted_verified_support:
        return "verified"
    if classification["independent_support_count"] >= 2:
        return "corroborated"
    statuses = {item.get("verification_status") or "unverified" for item in contributions}
    if classification["independent_support_count"] == 1:
        return "unverified"
    if statuses <= {"potential"} and not classification["supporting"]:
        return "potential"
    if classification["supporting"] or "unverified" in statuses or "corroborated" in statuses:
        return "unverified"
    if statuses <= {"potential"}:
        return "potential"
    return "unverified"


def should_invalidate(incident: dict, contributions: list[dict]) -> bool:
    """Trusted explicit negative verification may invalidate unverified/potential Incidents."""
    if incident.get("status") != "active":
        return False
    if incident.get("verification_status") not in {"unverified", "potential"}:
        return False
    incident_type = incident["incident_type"]
    return any(
        _is_trusted_verified(item) and claim_polarity(item, incident_type) == "conflict"
        for item in contributions
    )


def aggregate_victim_state(contributions: list[dict], independent_support_count: int | None = None) -> dict:
    """Preserve uncertainty. Do not take a raw max count as ground truth.

    unknown = source provided no victim information.
    none = source explicitly established no victims.
    Primary indication follows victim field precedence, not maximum severity.
    best_estimate comes from the highest-ranked source that reported a count;
    count_max retains the envelope of all counts.
    """
    if independent_support_count is None:
        independent_support_count = classify_evidence(contributions, "trapped_victims")["independent_support_count"]
    relevant = [item for item in contributions if item.get("victim_type") not in {None, "unknown"}]
    if not relevant:
        return {
            "indication": {"type": "unknown", "count_min": None, "count_max": None},
            "best_estimate": None,
            "count_min": None,
            "count_max": None,
            "supporting_observation_ids": unique(
                item["observation_id"] for item in contributions
                if item.get("victim_type") == "unknown" and item.get("observation_id")
            ),
        }

    chosen = max(relevant, key=lambda item: (*victim_precedence(item, independent_support_count), item.get("observation_id") or ""))
    indication = chosen["victim_type"]
    used = relevant if indication == "none" else [item for item in relevant if item["victim_type"] != "none"]
    if not used:
        used = relevant

    count_min_values = [item["count_min"] for item in used if item.get("count_min") is not None]
    count_max_values = [item["count_max"] for item in used if item.get("count_max") is not None]
    all_reported = count_min_values + count_max_values

    ranked_with_count = [
        item for item in used
        if item.get("count_min") is not None or item.get("count_max") is not None
    ]
    best_estimate = None
    strongest_min = None
    if ranked_with_count:
        ranked_with_count.sort(
            key=lambda item: (*victim_precedence(item, independent_support_count), item.get("observation_id") or "")
        )
        strongest = ranked_with_count[-1]
        strongest_min = strongest.get("count_min")
        if strongest.get("count_min") is not None:
            best_estimate = strongest["count_min"]
        elif strongest.get("count_max") is not None:
            best_estimate = strongest["count_max"]

    envelope_max = max(all_reported) if all_reported else None
    if strongest_min is not None:
        count_min = strongest_min
    elif count_min_values:
        count_min = min(count_min_values)
    else:
        count_min = None
    count_max = envelope_max

    if indication in {"confirmed_individual", "confirmed_multiple"}:
        high = count_max if count_max is not None else count_min
        if high is not None and high >= 2:
            indication = "confirmed_multiple"
    elif indication in {"possible_individual", "possible_multiple"}:
        high = count_max if count_max is not None else count_min
        if high is not None and high >= 2:
            indication = "possible_multiple"

    supporting = unique(item["observation_id"] for item in used if item.get("observation_id"))
    return {
        "indication": {"type": indication, "count_min": count_min, "count_max": count_max},
        "best_estimate": best_estimate,
        "count_min": count_min,
        "count_max": count_max,
        "supporting_observation_ids": supporting,
    }


def aggregate_structural_state(contributions: list[dict]) -> dict:
    known = [item for item in contributions if item.get("structural") not in {None, "unknown"}]
    used = known or contributions
    if not used:
        return {"severity": "unknown", "supporting_observation_ids": []}
    chosen = max(used, key=lambda item: (*structural_precedence(item), item.get("observation_id") or ""))
    supporting = unique(
        item["observation_id"] for item in used
        if item.get("observation_id") and item.get("structural") == chosen.get("structural")
    )
    return {"severity": chosen.get("structural") or "unknown", "supporting_observation_ids": supporting}


def aggregate_access_state(contributions: list[dict]) -> dict:
    known = [item for item in contributions if item.get("access") not in {None, "unknown"}]
    used = known or contributions
    if not used:
        return {"status": "unknown", "supporting_observation_ids": []}
    chosen = max(used, key=lambda item: (*access_precedence(item), item.get("observation_id") or ""))
    supporting = unique(
        item["observation_id"] for item in used
        if item.get("observation_id") and item.get("access") == chosen.get("access")
    )
    return {"status": chosen.get("access") or "unknown", "supporting_observation_ids": supporting}


def victim_precedence(item: dict, independent_support_count: int) -> tuple:
    """verified responder/field > corroborated independent > drone > social."""
    source_type = item.get("source_type") or "system"
    verified = item.get("verification_status") == "verified"
    timestamp = item.get("timestamp") or ""
    if source_type in RESPONDER_SOURCE_TYPES:
        tier = 4 if verified else 3
        return (tier, VERIFICATION_RANK.get(item.get("verification_status"), 0), timestamp)
    if independent_support_count >= 2 and claim_polarity(item, "trapped_victims") == "support":
        drone_bonus = 1 if source_type == "drone" else 0
        return (2, drone_bonus, timestamp)
    if source_type == "drone":
        return (1, 1 if verified else 0, timestamp)
    return (0, VICTIM_SOURCE_RANK.get(source_type, 0), timestamp)


def structural_precedence(item: dict) -> tuple:
    """drone or field > radio > social. Verification does not invert that order."""
    source_type = item.get("source_type") or "system"
    return (
        STRUCTURAL_SOURCE_RANK.get(source_type, 0),
        VERIFICATION_RANK.get(item.get("verification_status"), 0),
        item.get("timestamp") or "",
        STRUCTURAL_RANK.get(item.get("structural"), 0),
    )


def access_precedence(item: dict) -> tuple:
    source_type = item.get("source_type") or "system"
    return (
        ACCESS_SOURCE_RANK.get(source_type, 0),
        VERIFICATION_RANK.get(item.get("verification_status"), 0),
        item.get("timestamp") or "",
        ACCESS_RANK.get(item.get("access"), 0),
    )


def _attach_states(
    incident: dict,
    contributions: list[dict],
    incident_type: str,
    independent_support_count: int,
) -> None:
    victim_relevant = [
        item for item in contributions
        if incident_type == "trapped_victims" or item.get("victim_type") not in {"unknown", "none", None}
    ]
    structural_relevant = [
        item for item in contributions
        if incident_type == "structural_damage" or item.get("structural") not in {"unknown", None}
    ]
    access_relevant = [
        item for item in contributions
        if incident_type == "access_blocked" or item.get("access") not in {"unknown", None}
    ]
    if incident_type == "trapped_victims" or victim_relevant:
        incident["victim_state"] = aggregate_victim_state(
            contributions if incident_type == "trapped_victims" else victim_relevant,
            independent_support_count=independent_support_count if incident_type == "trapped_victims" else None,
        )
        if incident_type != "trapped_victims" and not victim_relevant:
            incident.pop("victim_state", None)
    if incident_type == "structural_damage" or structural_relevant:
        incident["structural_state"] = aggregate_structural_state(
            contributions if incident_type == "structural_damage" else structural_relevant
        )
    if incident_type == "access_blocked" or access_relevant:
        incident["access_state"] = aggregate_access_state(
            contributions if incident_type == "access_blocked" else access_relevant
        )


def _contribution(observation: dict) -> dict:
    victim = observation.get("victim_indication") or {"type": "unknown"}
    identity = independent_source_key(observation)
    return {
        "observation_id": observation["observation_id"],
        "source_type": observation["source_type"],
        "source_id": observation.get("source_id"),
        "source_key": source_key_string(identity),
        "source_identity": list(identity),
        "verification_status": observation["verification_status"],
        "timestamp": observation["timestamp"],
        "victim_type": victim.get("type") or "unknown",
        "count_min": victim.get("count_min"),
        "count_max": victim.get("count_max"),
        "structural": STRUCTURAL_FROM_HAZARD.get(observation.get("structural_hazard") or "unknown", "unknown"),
        "access": ACCESS_FROM_STATUS.get(observation.get("access_status") or "unknown", "unknown"),
    }


def _contributions_from_incident(incident: dict) -> list[dict]:
    metadata = incident.get("metadata") or {}
    stored = metadata.get("contributions")
    if isinstance(stored, list) and stored:
        return deepcopy(stored)
    contributions = []
    for observation_id in incident.get("observation_ids", []):
        contributions.append({
            "observation_id": observation_id,
            "source_type": "system",
            "source_id": None,
            "source_key": source_key_string(("system", "id", observation_id)),
            "source_identity": ["system", "id", observation_id],
            "verification_status": incident.get("verification_status") or "unverified",
            "timestamp": incident.get("first_observed_at"),
            "victim_type": ((incident.get("victim_state") or {}).get("indication") or {}).get("type") or "unknown",
            "count_min": (incident.get("victim_state") or {}).get("count_min"),
            "count_max": (incident.get("victim_state") or {}).get("count_max"),
            "structural": (incident.get("structural_state") or {}).get("severity") or "unknown",
            "access": (incident.get("access_state") or {}).get("status") or "unknown",
        })
    return contributions


def _identity(item: dict) -> tuple[str, str, str]:
    stored = item.get("source_identity")
    if isinstance(stored, (list, tuple)) and len(stored) == 3:
        return (str(stored[0]), str(stored[1]), str(stored[2]))
    key = item.get("source_key")
    if isinstance(key, str) and "|" in key:
        parts = key.split("|", 2)
        if len(parts) == 3:
            return (parts[0], parts[1], parts[2])
    source_type = item.get("source_type") or "system"
    source_id = item.get("source_id") or item.get("source_key") or item.get("observation_id") or source_type
    if source_type in SOCIAL_SOURCE_TYPES:
        return (source_type, "platform", str(source_id))
    if source_type in TRUSTED_SOURCE_TYPES:
        return (source_type, "unit", str(source_id))
    return (source_type, "id", str(source_id))


def _is_trusted_verified(item: dict) -> bool:
    return (
        item.get("verification_status") == "verified"
        and item.get("source_type") in TRUSTED_SOURCE_TYPES
    )


def _infer_incident_type(contributions: list[dict]) -> str:
    if any(item.get("victim_type") in POSITIVE_VICTIM_TYPES or item.get("victim_type") == "none" for item in contributions):
        return "trapped_victims"
    if any(item.get("structural") in POSITIVE_STRUCTURAL or item.get("structural") == "none" for item in contributions):
        return "structural_damage"
    if any(item.get("access") in POSITIVE_ACCESS or item.get("access") == "open" for item in contributions):
        return "access_blocked"
    return "trapped_victims"


def _author_id(observation: dict) -> str | None:
    metadata = observation.get("metadata") if isinstance(observation.get("metadata"), dict) else {}
    ingestion = metadata.get("ingestion") if isinstance(metadata.get("ingestion"), dict) else {}
    source_metadata = ingestion.get("source_metadata") if isinstance(ingestion.get("source_metadata"), dict) else {}
    nested = source_metadata.get("source_metadata") if isinstance(source_metadata.get("source_metadata"), dict) else {}
    for candidate in (
        source_metadata.get("author_id"),
        nested.get("author_id"),
        metadata.get("author_id"),
        ingestion.get("author_id"),
    ):
        value = _nonempty(candidate)
        if value:
            return value
    return None


def _speaker_id(observation: dict) -> str | None:
    metadata = observation.get("metadata") if isinstance(observation.get("metadata"), dict) else {}
    ingestion = metadata.get("ingestion") if isinstance(metadata.get("ingestion"), dict) else {}
    source_metadata = ingestion.get("source_metadata") if isinstance(ingestion.get("source_metadata"), dict) else {}
    for candidate in (
        source_metadata.get("speaker_id"),
        metadata.get("speaker_id"),
        observation.get("source_id"),
    ):
        value = _nonempty(candidate)
        if value:
            return value
    return None


def _evidence_summary(observation: dict) -> str:
    location = observation.get("location") or {}
    place = _nonempty(location.get("raw_text")) or _nonempty(location.get("label")) or "unspecified location"
    victim = (observation.get("victim_indication") or {}).get("type") or "unknown"
    return (
        f"{observation['source_type']} {observation['incident_type']} "
        f"victim={victim} structural={observation.get('structural_hazard')} "
        f"access={observation.get('access_status')} at {place}"
    )


def _nonempty(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def store_contributions(incident: dict, contributions: list[dict]) -> dict:
    metadata = dict(incident.get("metadata") or {})
    metadata["contributions"] = deepcopy(contributions)
    incident["metadata"] = metadata
    return incident
