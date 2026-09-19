"""Site-level Worksite materialization. A Worksite is not an Incident."""

from __future__ import annotations

from copy import deepcopy

from operational.ids import schema_version, unique, worksite_id
from operational.incident_fusion import (
    VERIFICATION_RANK,
    aggregate_access_state,
    aggregate_structural_state,
    aggregate_victim_state,
)
from operational.validation import validate_worksite


def materialize_worksite(
    site: dict,
    incidents: list[dict],
    *,
    updated_at: str,
    previous: dict | None = None,
    last_observation_id: str | None = None,
) -> dict:
    """Aggregate operationally relevant Incident state for one Site.

    Does not compute SearchPriority, SavePriority, or resource assignments.
    Planner-owned operational_status and assigned_resource_ids are preserved
    when a previous Worksite exists; new Worksites default to pending.

    Active Incidents drive summaries. Invalidated Incidents stay out of
    active_incident_ids but their evidence IDs remain on the Worksite so audit
    history is not dropped. Summaries reuse underlying observation
    contributions and the same field-specific source precedence as fusion.
    """
    site_incidents = [item for item in incidents if item.get("site_id") == site["site_id"]]
    active = [item for item in site_incidents if item.get("status") == "active"]
    invalidated = [item for item in site_incidents if item.get("status") == "invalidated"]
    active.sort(key=lambda item: (item.get("first_observed_at") or "", item["incident_id"]))

    evidence_ids = unique(
        evidence_id
        for item in site_incidents
        for evidence_id in item.get("evidence_ids", [])
    )
    if previous:
        evidence_ids = unique(list(previous.get("evidence_ids") or []) + evidence_ids)

    created_at = previous["created_at"] if previous else (
        min((item["first_observed_at"] for item in site_incidents), default=updated_at)
    )
    operational_status = (previous or {}).get("operational_status") or "pending"
    assigned = list((previous or {}).get("assigned_resource_ids") or [])

    victim = _victim_summary(active)
    structural = _structural_summary(active)
    access = _access_summary(active)
    evidence_status = _evidence_status(active)

    worksite = {
        "schema_version": schema_version(),
        "worksite_id": (previous or {}).get("worksite_id") or worksite_id(site["site_id"]),
        "site_id": site["site_id"],
        "display_name": site.get("display_name") or site["site_id"],
        "evidence_status": evidence_status,
        "operational_status": operational_status,
        "active_incident_ids": [item["incident_id"] for item in active],
        "evidence_ids": evidence_ids,
        "victim_summary": victim,
        "structural_summary": structural,
        "access_summary": access,
        "criticality": site.get("criticality", 0),
        "assigned_resource_ids": assigned,
        "created_at": created_at,
        "updated_at": updated_at,
        "metadata": {
            "contributing_incident_ids": [item["incident_id"] for item in active],
            "invalidated_incident_ids": [item["incident_id"] for item in invalidated],
            "last_observation_id": last_observation_id,
            "evidence_status_independent_of_operational_status": True,
        },
    }
    if previous:
        for optional in ("search_priority", "save_priority"):
            if optional in previous:
                worksite[optional] = previous[optional]
        previous_meta = dict(previous.get("metadata") or {})
        previous_meta.update(worksite["metadata"])
        worksite["metadata"] = previous_meta
    return validate_worksite(worksite)


def _contributions_for_field(incidents: list[dict], field: str) -> list[dict]:
    collected = []
    for incident in incidents:
        stored = (incident.get("metadata") or {}).get("contributions")
        if isinstance(stored, list) and stored:
            for item in stored:
                contribution = deepcopy(item)
                if field == "victim":
                    victim = contribution.get("victim_type") or "unknown"
                    if victim == "unknown" and incident.get("incident_type") != "trapped_victims":
                        continue
                    if victim in {"unknown", "none"} and incident.get("incident_type") != "trapped_victims":
                        continue
                elif field == "structural":
                    if contribution.get("structural") in {None, "unknown"} and incident.get("incident_type") != "structural_damage":
                        continue
                    if contribution.get("structural") == "unknown" and incident.get("incident_type") != "structural_damage":
                        continue
                elif field == "access":
                    if contribution.get("access") in {None, "unknown"} and incident.get("incident_type") != "access_blocked":
                        continue
                    if contribution.get("access") == "unknown" and incident.get("incident_type") != "access_blocked":
                        continue
                collected.append(contribution)
            continue
        state_key = {"victim": "victim_state", "structural": "structural_state", "access": "access_state"}[field]
        state = incident.get(state_key)
        if not state:
            continue
        if field == "victim":
            indication = (state.get("indication") or {}).get("type") or "unknown"
            if indication in {"unknown", "none"} and incident.get("incident_type") != "trapped_victims":
                continue
            collected.append({
                "observation_id": (state.get("supporting_observation_ids") or incident.get("observation_ids") or [incident["incident_id"]])[0],
                "source_type": "system",
                "source_id": incident["incident_id"],
                "source_key": incident["incident_id"],
                "verification_status": incident.get("verification_status") or "unverified",
                "timestamp": incident.get("last_updated_at"),
                "victim_type": indication,
                "count_min": state.get("count_min"),
                "count_max": state.get("count_max"),
                "structural": "unknown",
                "access": "unknown",
            })
        elif field == "structural":
            severity = state.get("severity") or "unknown"
            if severity == "unknown" and incident.get("incident_type") != "structural_damage":
                continue
            collected.append({
                "observation_id": incident["incident_id"],
                "source_type": "system",
                "source_id": incident["incident_id"],
                "source_key": incident["incident_id"],
                "verification_status": incident.get("verification_status") or "unverified",
                "timestamp": incident.get("last_updated_at"),
                "victim_type": "unknown",
                "count_min": None,
                "count_max": None,
                "structural": severity,
                "access": "unknown",
            })
        else:
            status = state.get("status") or "unknown"
            if status == "unknown" and incident.get("incident_type") != "access_blocked":
                continue
            collected.append({
                "observation_id": incident["incident_id"],
                "source_type": "system",
                "source_id": incident["incident_id"],
                "source_key": incident["incident_id"],
                "verification_status": incident.get("verification_status") or "unverified",
                "timestamp": incident.get("last_updated_at"),
                "victim_type": "unknown",
                "count_min": None,
                "count_max": None,
                "structural": "unknown",
                "access": status,
            })
    return collected


def _victim_summary(incidents: list[dict]) -> dict:
    contributions = _contributions_for_field(incidents, "victim")
    if not contributions:
        return {"indication": {"type": "unknown", "count_min": None, "count_max": None}}
    if len(incidents) == 1:
        state = next((incident.get("victim_state") for incident in incidents if incident.get("victim_state")), None)
        if state:
            return {
                "indication": deepcopy(state["indication"]),
                "best_estimate": state.get("best_estimate"),
                "count_min": state.get("count_min"),
                "count_max": state.get("count_max"),
            }
    fused = aggregate_victim_state(contributions)
    return {
        "indication": fused["indication"],
        "best_estimate": fused.get("best_estimate"),
        "count_min": fused.get("count_min"),
        "count_max": fused.get("count_max"),
    }


def _structural_summary(incidents: list[dict]) -> dict:
    contributions = _contributions_for_field(incidents, "structural")
    if not contributions:
        return {"severity": "unknown"}
    fused = aggregate_structural_state(contributions)
    return {"severity": fused["severity"]}


def _access_summary(incidents: list[dict]) -> dict:
    contributions = _contributions_for_field(incidents, "access")
    if not contributions:
        return {"status": "unknown"}
    fused = aggregate_access_state(contributions)
    return {"status": fused["status"]}


def _evidence_status(incidents: list[dict]) -> str:
    if not incidents:
        return "potential"
    return max(
        (item.get("verification_status") or "potential" for item in incidents),
        key=lambda status: VERIFICATION_RANK.get(status, 0),
    )
