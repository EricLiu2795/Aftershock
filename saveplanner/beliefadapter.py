"""Build an OperationalBeliefState from the scenario pack, for Save demos and tests.

This is a **stand-in for the live information pipeline**, not a second source of
truth. In the running system, belief comes from
`operational.belief_state.apply_observation` as radio/social/drone reports arrive.
Save does not care which producer it is handed, because it only ever reads the
contract.

So that this adapter cannot drift into its own private model, it builds Sites with
the operational module's own `ScenarioPackSiteRegistry`, materializes Worksites with
`operational.worksite.materialize_worksite`, and validates the result with
`operational.validation.validate_belief_state`. The only thing it supplies itself is
the set of Incidents, taken from each scenario event's own
`expected_downstream_interpretation` -- the pack's statement of what fusion should
conclude, not an invention of this module.

`scenario_ground_truth.json` is never read here; the pack forbids copying it into
belief.
"""
from __future__ import annotations

import json
from pathlib import Path

from operational.ids import evidence_id, incident_id, schema_version, worksite_id
from operational.site_registry import ScenarioPackSiteRegistry
from operational.validation import validate_belief_state
from operational.worksite import materialize_worksite

from .execution import ACTIVE_SITES, SCENARIO_DIR

EVENTS = SCENARIO_DIR / "scenario_events.json"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# Drone/radio structural wording in the pack -> the Incident contract's severity enum.
STRUCTURAL_SEVERITY = {
    "none": "none",
    "reported_damage": "minor",
    "minor_damage": "minor",
    "major_damage": "major",
    "severe_collapse": "severe",
    "active_secondary_hazard": "severe",
    "unknown": "unknown",
}


# Sources a responder is accountable for. Public social posts corroborate context
# but do not by themselves justify committing rescue capacity (specification
# section 13: verified responder > corroborated independent sources > drone
# indication > single social report).
RESPONDER_SOURCES = {"radio", "drone", "field_note"}


def _verification(sources):
    """Evidence level implied by the sources that have reported one incident.

    Emulates the *outcome* of IncidentFusion so Save can be exercised before the
    live fusion path is wired in; it is not a second fusion implementation. A drone
    verification is the verification step, two independent responder reports
    corroborate each other, and anything less stays unverified.
    """
    if "drone" in sources:
        return "verified"
    if len([s for s in sources if s in RESPONDER_SOURCES]) >= 2:
        return "corroborated"
    return "unverified"


def _fuse(incidents, site_id, kind, event, at):
    """Create or update the single Incident for one (site, incident_type).

    Section 12: the same site with a compatible incident type updates the existing
    Incident rather than creating another one.
    """
    observation = f"obs-{event['event_id'].lower()}"
    expected = event["expected_downstream_interpretation"]
    key = (site_id, kind)
    incident = incidents.get(key)
    if incident is None:
        incident = incidents[key] = {
            "schema_version": schema_version(),
            "incident_id": incident_id(site_id, kind, observation),
            "site_id": site_id,
            "incident_type": kind,
            "status": "active",
            "verification_status": "unverified",
            "observation_ids": [],
            "evidence_ids": [],
            "first_observed_at": at,
            "last_updated_at": at,
            "metadata": {"provenance": "simulated", "scenario_event_ids": [],
                         "source_types": []},
        }
    incident["observation_ids"].append(observation)
    incident["evidence_ids"].append(evidence_id(observation))
    incident["last_updated_at"] = at
    incident["metadata"]["scenario_event_ids"].append(event["event_id"])
    incident["metadata"]["source_types"].append(event.get("source_type", "radio"))
    incident["verification_status"] = _verification(incident["metadata"]["source_types"])

    # Later reports supersede earlier ones for the fields they actually carry.
    indication = expected.get("victim_indication")
    if indication:
        victim = {"indication": {"type": indication}}
        if expected.get("count_min") is not None:
            victim["count_min"] = expected["count_min"]
            victim["best_estimate"] = expected["count_min"]
        incident["victim_state"] = victim
    hazard = expected.get("structural_hazard")
    if hazard:
        incident["structural_state"] = {"severity": STRUCTURAL_SEVERITY.get(hazard, "unknown")}
    return incident


def belief_from_pack(index, through_event=None, scenario_time=None):
    """Assemble a belief state containing every event up to `through_event`.

    `through_event` is an event id such as "E07"; events after it are ignored, which
    is how a test can stand at an exact point in the demo timeline.
    """
    manifest = index.manifest
    events = _read(EVENTS)["events"]
    if through_event is not None:
        order = [e["event_id"] for e in events]
        if through_event not in order:
            raise KeyError(f"Unknown scenario event: {through_event}")
        events = events[:order.index(through_event) + 1]

    registry = ScenarioPackSiteRegistry(ACTIVE_SITES)
    start = index.start_time

    incidents, road_states, seen_sites = {}, {}, {}
    for state in manifest["initial_road_states"]:
        road_states[state["road_id"]] = dict(state)

    for event in events:
        referent = event.get("intended_referent") or {}
        expected = event.get("expected_downstream_interpretation") or {}
        at = event["raw_input"].get("timestamp", start) if event.get("raw_input") else start

        site_id = referent.get("site_id")
        if site_id and registry.get_site(site_id):
            seen_sites[site_id] = True
            for kind in expected.get("incident_types", []):
                if kind == "access_blocked":
                    continue        # a road condition, not a worksite incident
                _fuse(incidents, site_id, kind, event, at)

        road = expected.get("road_state")
        if road:
            road_states[road["road_id"]] = {
                "schema_version": schema_version(),
                "road_id": road["road_id"],
                "status": road["status"],
                "travel_cost_multiplier": road.get("travel_cost_multiplier", 1.0),
                "evidence_ids": [evidence_id(f"obs-{event['event_id'].lower()}")],
                "reason": f"SIMULATED scenario event {event['event_id']}.",
                "based_on_state_version": None,
                "updated_at": at,
            }

    sites = [registry.get_site(site_id) for site_id in sorted(seen_sites)]
    fused = [incidents[key] for key in sorted(incidents)]
    worksites = []
    for site in sites:
        worksite = materialize_worksite(site, fused, updated_at=scenario_time or start)
        worksite["worksite_id"] = worksite_id(site["site_id"])
        worksites.append(worksite)

    belief = {
        "schema_version": schema_version(),
        "state_version": f"scenario-{through_event or 'start'}",
        "scenario_time": scenario_time or start,
        "sites": sites,
        "worksites": worksites,
        "incidents": fused,
        "resources": list(manifest["resources"]),
        "road_states": [road_states[key] for key in sorted(road_states)],
        "movement_states": list(manifest["initial_movement_states"]),
        "active_routes": [],
        "pending_reassignment_proposals": [],
        "updated_at": scenario_time or start,
    }
    return validate_belief_state(belief)
