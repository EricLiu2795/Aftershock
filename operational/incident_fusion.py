"""Deterministic Incident fusion. AI does not create or merge Incidents."""

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

# Source precedence is categorical, not a probability model.
# Victim: verified responder > corroborated independent sources > drone > social.
# Structural / access: drone or field assessment > responder radio > social.
SOURCE_RANK = {
    "field_note": 40,
    "radio": 30,
    "drone": 20,
    "ushahidi": 12,
    "reliefweb": 10,
    "gdacs": 10,
    "social": 10,
    "system": 0,
}
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
TRUSTED_SOURCE_TYPES = frozenset({"radio", "drone", "field_note"})


@dataclass(frozen=True)
class FusionConfig:
    """Temporal proximity window for same-site, same-type fusion.

    Default is 30 minutes for the hackathon MVP. Observations outside this
    window create a new Incident even at the same Site and incident_type.
    Compatible types are exact incident_type equality only; trapped_victims
    never merges with structural_damage or access_blocked.
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
        compatible.sort(key=lambda item: (item["last_updated_at"], item["incident_id"]))

        if compatible:
            current = deepcopy(compatible[-1])
            if observation_id in current["observation_ids"]:
                return FusionResult("none", current, evidence, "observation_already_fused")
            updated = apply_observation_to_incident(current, observation, evidence, scenario_time)
            return FusionResult("updated", updated, evidence, "same_site_type_within_window")

        created = build_incident(observation, site_id, evidence, scenario_time)
        return FusionResult("created", created, evidence, "no_compatible_incident")


def within_fusion_window(incident: dict, observed_at: str, window: timedelta) -> bool:
    start = parse_time(incident["first_observed_at"])
    end = parse_time(incident["last_updated_at"])
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
    summary = _evidence_summary(observation)
    payload = {
        "schema_version": schema_version(),
        "evidence_id": evidence_id(observation["observation_id"]),
        "observation_id": observation["observation_id"],
        "source_type": observation["source_type"],
        "source_id": observation.get("source_id"),
        "summary": summary,
        "verification_status": observation["verification_status"],
        "timestamp": observation["timestamp"],
        "field_support": unique(field_support),
    }
    return validate_evidence(payload)


def build_incident(observation: dict, site_id: str, evidence: dict, scenario_time: str | None) -> dict:
    identity = incident_id(site_id, observation["incident_type"], observation["observation_id"])
    updated_at = observation["timestamp"]
    if scenario_time:
        updated_at = format_time(max(parse_time(updated_at), parse_time(scenario_time)))
    incident = {
        "schema_version": schema_version(),
        "incident_id": identity,
        "site_id": site_id,
        "incident_type": observation["incident_type"],
        "status": "active",
        "verification_status": aggregate_verification([_contribution(observation)]),
        "observation_ids": [observation["observation_id"]],
        "evidence_ids": [evidence["evidence_id"]],
        "first_observed_at": observation["timestamp"],
        "last_updated_at": updated_at,
        "metadata": {
            "decision_trace": {
                "fusion_action": "created",
                "created_from_observation_id": observation["observation_id"],
                "last_observation_id": observation["observation_id"],
            }
        },
    }
    contributions = [_contribution(observation)]
    _attach_states(incident, contributions, observation["incident_type"])
    store_contributions(incident, contributions)
    return validate_incident(incident)


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
    incident["verification_status"] = aggregate_verification(contributions)
    last_updated = max(parse_time(incident["last_updated_at"]), parse_time(observation["timestamp"]))
    if scenario_time:
        last_updated = max(last_updated, parse_time(scenario_time))
    if parse_time(observation["timestamp"]) < parse_time(incident["first_observed_at"]):
        incident["first_observed_at"] = observation["timestamp"]
    incident["last_updated_at"] = format_time(last_updated)
    metadata = dict(incident.get("metadata") or {})
    trace = dict(metadata.get("decision_trace") or {})
    trace["fusion_action"] = "updated"
    trace["last_observation_id"] = observation["observation_id"]
    metadata["decision_trace"] = trace
    incident["metadata"] = metadata
    _attach_states(incident, contributions, incident["incident_type"])
    store_contributions(incident, contributions)
    return validate_incident(incident)


def aggregate_verification(contributions: list[dict]) -> str:
    """Categorical evidence progression: potential → unverified → corroborated → verified.

    One unverified social report never becomes verified. Verified requires an
    explicit verification_status=verified on a trusted source type (radio,
    drone, or field_note). Two or more independent sources corroborate.
    """
    if not contributions:
        return "potential"
    statuses = {item["verification_status"] for item in contributions}
    trusted_verified = any(
        item["verification_status"] == "verified" and item["source_type"] in TRUSTED_SOURCE_TYPES
        for item in contributions
    )
    if trusted_verified:
        return "verified"
    independent = {(item["source_type"], item["source_key"]) for item in contributions}
    if len(independent) >= 2:
        return "corroborated"
    if "unverified" in statuses or "corroborated" in statuses:
        return "unverified"
    if statuses <= {"potential"}:
        return "potential"
    return "unverified"


def aggregate_victim_state(contributions: list[dict]) -> dict:
    """Preserve uncertainty. Do not take a raw max count as ground truth.

    unknown = source provided no victim information.
    none = source explicitly established no victims.
    Weaker/unknown/none claims do not erase stronger confirmed or possible
    indications. best_estimate comes from the highest-ranked source that
    actually reported a count; count_max retains the envelope of all counts.
    """
    relevant = [item for item in contributions if item["victim_type"] != "unknown"]
    if not relevant:
        return {
            "indication": {"type": "unknown", "count_min": None, "count_max": None},
            "best_estimate": None,
            "count_min": None,
            "count_max": None,
            "supporting_observation_ids": unique(
                item["observation_id"] for item in contributions if item["victim_type"] == "unknown"
            ),
        }

    positive = [item for item in relevant if item["victim_type"] != "none"]
    used = positive or relevant
    indication = max(used, key=lambda item: INDICATION_RANK.get(item["victim_type"], 0))["victim_type"]
    count_min_values = [item["count_min"] for item in used if item["count_min"] is not None]
    count_max_values = [item["count_max"] for item in used if item["count_max"] is not None]
    all_reported = count_min_values + count_max_values

    ranked_with_count = [
        item for item in used
        if item["count_min"] is not None or item["count_max"] is not None
    ]
    best_estimate = None
    strongest_min = None
    if ranked_with_count:
        ranked_with_count.sort(key=lambda item: (_source_score(item), item["timestamp"], item["observation_id"]))
        strongest = ranked_with_count[-1]
        strongest_min = strongest["count_min"]
        if strongest["count_min"] is not None:
            best_estimate = strongest["count_min"]
        elif strongest["count_max"] is not None:
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
        elif indication == "confirmed_individual":
            indication = "confirmed_individual"
    elif indication in {"possible_individual", "possible_multiple"}:
        high = count_max if count_max is not None else count_min
        if high is not None and high >= 2:
            indication = "possible_multiple"

    supporting = unique(item["observation_id"] for item in used)
    return {
        "indication": {"type": indication, "count_min": count_min, "count_max": count_max},
        "best_estimate": best_estimate,
        "count_min": count_min,
        "count_max": count_max,
        "supporting_observation_ids": supporting,
    }


def aggregate_structural_state(contributions: list[dict]) -> dict:
    known = [item for item in contributions if item["structural"] != "unknown"]
    used = known or contributions
    if not used:
        return {"severity": "unknown", "supporting_observation_ids": []}
    chosen = max(used, key=lambda item: (_source_score(item), STRUCTURAL_RANK.get(item["structural"], 0), item["timestamp"]))
    supporting = unique(
        item["observation_id"] for item in used if item["structural"] == chosen["structural"] or item["structural"] != "unknown"
    )
    return {"severity": chosen["structural"], "supporting_observation_ids": supporting}


def aggregate_access_state(contributions: list[dict]) -> dict:
    known = [item for item in contributions if item["access"] != "unknown"]
    used = known or contributions
    if not used:
        return {"status": "unknown", "supporting_observation_ids": []}
    chosen = max(used, key=lambda item: (_source_score(item), ACCESS_RANK.get(item["access"], 0), item["timestamp"]))
    supporting = unique(
        item["observation_id"] for item in used if item["access"] != "unknown" or item["observation_id"] == chosen["observation_id"]
    )
    return {"status": chosen["access"], "supporting_observation_ids": supporting}


def _attach_states(incident: dict, contributions: list[dict], incident_type: str) -> None:
    victim_relevant = [
        item for item in contributions
        if incident_type == "trapped_victims" or item["victim_type"] not in {"unknown", "none"}
    ]
    structural_relevant = [
        item for item in contributions
        if incident_type == "structural_damage" or item["structural"] != "unknown"
    ]
    access_relevant = [
        item for item in contributions
        if incident_type == "access_blocked" or item["access"] != "unknown"
    ]
    if incident_type == "trapped_victims" or victim_relevant:
        incident["victim_state"] = aggregate_victim_state(contributions if incident_type == "trapped_victims" else victim_relevant)
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
    source_id = observation.get("source_id")
    return {
        "observation_id": observation["observation_id"],
        "source_type": observation["source_type"],
        "source_id": source_id,
        "source_key": source_id or observation["observation_id"],
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
    # Reconstruct a conservative contribution per observation ID. Source
    # details are filled on subsequent apply_observation_to_incident calls
    # via metadata.contributions maintained below.
    contributions = []
    for observation_id in incident.get("observation_ids", []):
        contributions.append({
            "observation_id": observation_id,
            "source_type": "system",
            "source_id": None,
            "source_key": observation_id,
            "verification_status": incident.get("verification_status") or "unverified",
            "timestamp": incident.get("first_observed_at"),
            "victim_type": ((incident.get("victim_state") or {}).get("indication") or {}).get("type") or "unknown",
            "count_min": (incident.get("victim_state") or {}).get("count_min"),
            "count_max": (incident.get("victim_state") or {}).get("count_max"),
            "structural": (incident.get("structural_state") or {}).get("severity") or "unknown",
            "access": (incident.get("access_state") or {}).get("status") or "unknown",
        })
    return contributions


def _source_score(item: dict) -> int:
    return SOURCE_RANK.get(item["source_type"], 0) + VERIFICATION_RANK.get(item["verification_status"], 0)


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
