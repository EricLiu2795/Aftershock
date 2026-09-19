"""Adapter: canonical OperationalBeliefState → Search initialize/update.

The live app must not reinitialize Search on every dashboard tick, must not
rebuild Worksites from raw reports, and must not inject `data/search_demo`
fixture Worksites onto the real demo path.

Agent 1 owns snapshot persistence. This module only:
- reads a snapshot through `get_operational_snapshot`
- prepares a Search-ready planning view (Worksites/IDs unchanged)
- decides initialize vs update vs no-replan from `state_version`
- dispatches remaining idle drones after a version-changing update
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from operational.ids import schema_version
from operational.snapshot import get_operational_snapshot
from operational.validation import validate_belief_state

from .planner import plan_search
from .reassignment import initialize, step
from .runtime import start_mission

ROOT = Path(__file__).resolve().parents[1]
CARABALLEDA_PACK = ROOT / "data" / "scenarios" / "caraballeda"
ACTIVE_PHASES = frozenset({"en_route", "verifying", "reporting"})
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


def decide_search_sync(record, snapshot, *, scenario_id=None):
    """Pure lifecycle decision. Does not call the planner.

    if Search has no state: initialize
    else if belief.state_version changed: update
    else: no replan
    """
    if snapshot is None:
        return {"action": "uninitialized" if record is None else "noop", "state": None}
    version = snapshot.get("state_version")
    if not isinstance(version, str) or not version:
        raise ValueError("Operational snapshot is missing state_version")
    if record is None:
        return {"action": "initialize", "state": planning_snapshot(snapshot, scenario_id=scenario_id)}
    previous = record.get("operational_state_version")
    if previous is None:
        previous = (record.get("state") or {}).get("state_version")
    if previous == version:
        return {"action": "noop", "state": None}
    return {"action": "update", "state": planning_snapshot(snapshot, scenario_id=scenario_id)}


def sync_search_from_operational_state(record, snapshot, wall_now, *, scenario_id=None):
    """Apply the lifecycle and return `{record, action, replanned}`."""
    decision = decide_search_sync(record, snapshot, scenario_id=scenario_id)
    action = decision["action"]
    if action == "uninitialized":
        return {"record": None, "action": action, "replanned": False}
    if action == "noop":
        return {"record": record, "action": action, "replanned": False}
    if action == "initialize":
        out = initialize(decision["state"], wall_now)
        mark_synced(out, decision["state"]["state_version"], action)
        return {"record": out, "action": action, "replanned": True}
    out = step(record, wall_now, state=decision["state"])
    out = refresh_plan_and_dispatch_idle(out)
    mark_synced(out, decision["state"]["state_version"], action)
    return {"record": out, "action": action, "replanned": True}


def planning_snapshot(snapshot, *, scenario_id=None):
    """Search-ready OperationalBeliefState from Agent 1's canonical snapshot.

    Worksites, incidents, and identities are taken from the snapshot. Scenario
    pack resources are overlaid only when the snapshot has none. Search demo
    fixture Worksites are never consulted.
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
    if not state["resources"] or not state["movement_states"]:
        _overlay_scenario_resources(state, scenario_id=scenario_id)
    if not state["sites"] and state["worksites"]:
        _overlay_canonical_sites(state)
    return validate_belief_state(state)


def refresh_plan_and_dispatch_idle(record):
    """Replan from current Search-owned state and dispatch remaining idle drones.

    Existing in-flight missions are left untouched. Scoring stays in `plan_search`.
    """
    out = deepcopy(record)
    plan = plan_search(out["state"])
    out["plan"] = plan
    busy = {
        mission["resource"]["resource_id"]
        for mission in out["missions"]
        if mission["phase"] in ACTIVE_PHASES
    }
    by_drone = {
        mission["resource"]["resource_id"]: index
        for index, mission in enumerate(out["missions"])
    }
    for assignment in plan["assignments"]:
        resource_id = assignment["resource_id"]
        if resource_id in busy:
            continue
        mission = start_mission(plan, resource_id, out["state"])
        if resource_id in by_drone:
            out["missions"][by_drone[resource_id]] = mission
        else:
            by_drone[resource_id] = len(out["missions"])
            out["missions"].append(mission)
    return out


def mark_synced(record, state_version, action):
    record["operational_state_version"] = state_version
    record["sync_action"] = action
    return record


def _overlay_scenario_resources(state, *, scenario_id=None):
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
    if not state.get("road_states"):
        state["road_states"] = deepcopy(pack.get("initial_road_states") or [])


def _overlay_canonical_sites(state):
    wanted = {item["site_id"] for item in state["worksites"] if item.get("site_id")}
    if not wanted:
        return
    pack_sites = _pack_sites()
    state["sites"] = [deepcopy(pack_sites[site_id]) for site_id in sorted(wanted) if site_id in pack_sites]


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
    "decide_search_sync",
    "get_operational_snapshot",
    "mark_synced",
    "planning_snapshot",
    "refresh_plan_and_dispatch_idle",
    "sync_search_from_operational_state",
]
