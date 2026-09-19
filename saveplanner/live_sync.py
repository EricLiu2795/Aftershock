"""Adapter: canonical OperationalBeliefState → live Save initialize/update.

Live Save must not use `belief_from_pack`, `intended_referent`, or
`expected_downstream_interpretation`. Agent 1 owns worksites, incidents,
road_states, and `state_version`. This module only:

- prepares a Save-ready planning view (Worksite/Site IDs unchanged)
- overlays static pack routing configuration when canonical collections are empty
- decides initialize vs movement-tick vs replan from `state_version`
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from operational.ids import schema_version
from operational.validation import validate_belief_state

ROOT = Path(__file__).resolve().parents[1]
CARABALLEDA_PACK = ROOT / "data" / "scenarios" / "caraballeda"
REQUIRED_COLLECTIONS = (
    "sites",
    "worksites",
    "incidents",
    "resources",
    "road_states",
    "movement_states",
    "active_routes",
    "pending_reassignment_proposals",
)


def usable_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        return None
    version = snapshot.get("state_version")
    worksites = snapshot.get("worksites") or []
    if (not version or version == "0") and not worksites:
        return None
    return snapshot


def decide_save_sync(record, snapshot):
    """Pure lifecycle decision. Does not call the planner."""
    snapshot = usable_snapshot(snapshot)
    if snapshot is None:
        return {"action": "uninitialized" if record is None else "noop", "state": None}
    version = snapshot.get("state_version")
    if not isinstance(version, str) or not version:
        raise ValueError("Operational snapshot is missing state_version")
    planning = planning_snapshot(snapshot, scenario_id=_scenario_id(snapshot, record))
    if record is None:
        return {"action": "initialize", "state": planning}
    previous = record.get("last_consumed_operational_state_version")
    if previous is None:
        previous = (record.get("execution") or {}).get("state_version")
    if previous == version:
        return {"action": "noop", "state": planning}
    return {"action": "update", "state": planning}


def planning_snapshot(snapshot, *, scenario_id=None):
    """Save-ready OperationalBeliefState from Agent 1's canonical snapshot.

    Worksites, incidents, and identities are taken from the snapshot. Scenario
    pack resources, sites, and initial road states are overlaid only when the
    snapshot has none. Pack `intended_referent` is never consulted.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("Operational snapshot must be an object")
    state = deepcopy(snapshot)
    if not state.get("schema_version"):
        state["schema_version"] = schema_version()
    for key in REQUIRED_COLLECTIONS:
        state.setdefault(key, [])
    if not state.get("state_version"):
        raise ValueError("Operational snapshot is missing state_version")
    if not state.get("scenario_time"):
        raise ValueError("Operational snapshot is missing scenario_time")
    if not state.get("updated_at"):
        state["updated_at"] = state["scenario_time"]
    _overlay_scenario_config(state, scenario_id=scenario_id)
    keep = {
        "schema_version",
        "state_version",
        "scenario_time",
        "updated_at",
        *REQUIRED_COLLECTIONS,
    }
    state = {key: state[key] for key in keep if key in state}
    return validate_belief_state(state)


def _overlay_scenario_config(state, *, scenario_id=None):
    pack = _scenario_pack(scenario_id)
    if pack is None:
        return
    if not state["resources"]:
        state["resources"] = deepcopy(pack["resources"])
    if not state["movement_states"]:
        movements = deepcopy(pack["initial_movement_states"])
        now = state["scenario_time"]
        for movement in movements:
            if movement.get("movement_mode") == "stationary":
                movement["last_updated_at"] = now
                if movement.get("started_at"):
                    movement["started_at"] = now
        state["movement_states"] = movements
    if not state["road_states"]:
        state["road_states"] = deepcopy(pack.get("initial_road_states") or [])
    if not state["sites"] and state["worksites"]:
        _overlay_canonical_sites(state)


def _overlay_canonical_sites(state):
    wanted = {item["site_id"] for item in state["worksites"] if item.get("site_id")}
    if not wanted:
        return
    pack_sites = _pack_sites()
    state["sites"] = [deepcopy(pack_sites[site_id]) for site_id in sorted(wanted) if site_id in pack_sites]


def _scenario_id(snapshot, record):
    return (
        (snapshot or {}).get("scenario_id")
        or (record or {}).get("scenario_id")
        or "caraballeda"
    )


def _scenario_pack(scenario_id=None):
    if scenario_id not in {None, "caraballeda", "caraballeda-v0"}:
        return None
    path = CARABALLEDA_PACK / "scenario.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _pack_sites():
    payload = json.loads((CARABALLEDA_PACK / "active_sites.json").read_text(encoding="utf-8"))
    return {item["site"]["site_id"]: item["site"] for item in payload.get("active_worksites") or []}


__all__ = [
    "decide_save_sync",
    "planning_snapshot",
    "usable_snapshot",
]
