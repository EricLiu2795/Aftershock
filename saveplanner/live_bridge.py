"""JSON stdin/stdout bridge for live Save on canonical OperationalBeliefState.

The Fastify API owns per-session persistence. This process never writes
operational belief, never advances `state_version`, and never uses the
standalone SaveDemo pack player as live truth.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

from .controller import SaveController, format_time, parse_time
from .execution import ScenarioExecutionIndex
from .live_sync import decide_save_sync
from .roadgraph import RoadGraph

DEFAULT_SIM_SPEED = 10.0
_INDEX = None
_GRAPH_TEMPLATE = None


def run(request):
    command = request.get("command") or "tick"
    if command not in {"tick", "status", "decide", "warmup"}:
        raise ValueError("Unknown Save command")
    if command == "warmup":
        _runtime()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {**_waiting(now, request.get("scenario_id") or "caraballeda"), "warm": True}
    snapshot = request.get("operational_snapshot")
    record = request.get("record")
    scenario_id = request.get("scenario_id") or "caraballeda"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    decision = decide_save_sync(record, snapshot)
    if decision["action"] == "uninitialized":
        return _waiting(now, scenario_id)

    belief = decision["state"]
    graph, index = _runtime()
    controller = SaveController(graph, index)
    if record:
        controller.restore_execution(record.get("execution"))
    sim_speed = float((record or {}).get("simulation_speed") or request.get("simulation_speed") or DEFAULT_SIM_SPEED)
    elapsed = 0.0
    if command == "tick" and record and record.get("last_wall_at"):
        elapsed = max(0.0, (parse_time(now) - parse_time(record["last_wall_at"])).total_seconds() * sim_speed)

    sync_action = decision["action"]
    if record is None or sync_action == "initialize":
        controller.plan(belief, "initialize from OperationalBeliefState")
        sync_action = "initialize"
    elif sync_action == "update":
        controller.plan(belief, f"operational state_version {belief['state_version']}")
        if elapsed and command == "tick":
            controller.advance(belief, elapsed)
    else:
        controller.adopt(belief)
        if command == "decide":
            approve = bool(request.get("approve"))
            proposal_id = request.get("proposal_id")
            current = controller.proposal or {}
            if proposal_id and current.get("proposal_id") != proposal_id:
                raise ValueError("Unknown or stale Save proposal")
            controller.decide_proposal(belief, approve)
        elif command == "tick" and elapsed:
            controller.advance(belief, elapsed)

    execution_revision = int((record or {}).get("execution_revision") or 0) + 1
    out = {
        "initialized": True,
        "waiting": False,
        "scenario_id": scenario_id,
        "last_wall_at": now,
        "simulation_speed": sim_speed,
        "sync_action": sync_action,
        "last_consumed_operational_state_version": belief["state_version"],
        "execution_revision": execution_revision,
        "execution": controller.export_execution(),
        "view": presentation(controller, belief, index, sim_speed=sim_speed),
    }
    return out


def presentation(controller, belief, index, *, sim_speed=DEFAULT_SIM_SPEED):
    worksites = []
    for worksite in belief.get("worksites") or []:
        decision = controller.decisions.get(worksite["worksite_id"])
        site = next((item for item in belief.get("sites") or [] if item["site_id"] == worksite["site_id"]), None)
        worksites.append({
            "worksite_id": worksite["worksite_id"],
            "site_id": worksite["site_id"],
            "key": index.label(worksite["site_id"]),
            "display_name": worksite.get("display_name") or (site or {}).get("display_name"),
            "position": (site or {}).get("centroid"),
            "evidence_status": worksite.get("evidence_status"),
            "operational_status": worksite.get("operational_status"),
            "eligible": decision is not None,
            "save_priority": decision["save_priority"] if decision else None,
            "score_breakdown": decision["score_breakdown"] if decision else None,
            "candidate_assignments": decision["candidate_assignments"] if decision else [],
            "recommended_resource_id": decision["recommended_resource_id"] if decision else None,
            "recommended_task": decision["recommended_task"] if decision else None,
            "reasons": decision["reasons"] if decision else [],
            "assigned_resource_ids": [
                assignment["resource_id"]
                for assignment in controller.assignments.values()
                if assignment["status"] == "active" and assignment["worksite_id"] == worksite["worksite_id"]
            ],
            "simulated": False,
        })
    resources = []
    start = parse_time(index.start_time)
    for resource in controller.rescue_units(belief):
        state = controller.movement.get(resource["resource_id"], {})
        objective = controller.objective_of(resource["resource_id"])
        resources.append({
            "resource_id": resource["resource_id"],
            "display_name": resource["display_name"],
            "status": "en_route" if state.get("movement_mode") == "ground_route" else resource.get("status"),
            "capability_tags": resource.get("capability_tags", []),
            "current_worksite_id": objective,
            "position": state.get("position") or index.staging_position,
            "heading_deg": state.get("heading_deg"),
            "movement_mode": state.get("movement_mode", "stationary"),
            "route_progress": state.get("route_progress", 0.0),
            "route_id": state.get("route_id"),
            "distance_traveled_m": state.get("distance_traveled_m", 0.0),
            "distance_remaining_m": state.get("distance_remaining_m", 0.0),
            "eta_s": (
                state.get("distance_remaining_m", 0.0) / state["speed_mps"]
                if state.get("speed_mps") else None
            ),
        })
    proposal = None
    if controller.proposal:
        proposal = dict(controller.proposal)
        proposal["seconds_left"] = max(0.0, (proposal.get("expires_at_wall") or 0) - time.time())
    return {
        "initialized": True,
        "waiting": False,
        "state_version": belief["state_version"],
        "last_consumed_operational_state_version": controller.state_version,
        "scenario": index.scenario_id,
        "scenario_event": None,
        "scenario_time": format_time(controller.scenario_time),
        "elapsed_s": (controller.scenario_time - start).total_seconds(),
        "sim_speed": sim_speed,
        "running": True,
        "worksites": worksites,
        "resources": resources,
        "assignments": [item for item in controller.assignments.values() if item["status"] == "active"],
        "routes": [
            {
                "route_id": route["route_id"],
                "resource_id": route["resource_id"],
                "worksite_id": route["worksite_id"],
                "path": route["path"],
                "road_edge_ids": route["road_edge_ids"],
                "estimated_travel_time_s": route["estimated_travel_time_s"],
                "total_distance_m": route["total_distance_m"],
                "route_status": route.get("route_status"),
                "generated_from_state_version": route.get("generated_from_state_version"),
            }
            for route in controller.active_routes()
        ],
        "blocked_edge_ids": controller.graph.blocked_edge_ids(),
        "limited_edge_ids": controller.graph.limited_edge_ids(),
        "proposal": proposal,
        "trace": controller.trace,
    }


def _waiting(now, scenario_id):
    return {
        "initialized": False,
        "waiting": True,
        "uninitialized": True,
        "scenario_id": scenario_id,
        "last_wall_at": now,
        "simulation_speed": DEFAULT_SIM_SPEED,
        "sync_action": "uninitialized",
        "last_consumed_operational_state_version": None,
        "execution_revision": 0,
        "execution": None,
        "view": {
            "initialized": False,
            "waiting": True,
            "state_version": "0",
            "last_consumed_operational_state_version": None,
            "scenario": scenario_id,
            "scenario_event": None,
            "scenario_time": now,
            "elapsed_s": 0,
            "sim_speed": DEFAULT_SIM_SPEED,
            "running": False,
            "worksites": [],
            "resources": [],
            "assignments": [],
            "routes": [],
            "blocked_edge_ids": [],
            "limited_edge_ids": [],
            "proposal": None,
            "trace": [],
            "sync_action": "uninitialized",
        },
    }


def _runtime():
    global _INDEX, _GRAPH_TEMPLATE
    if _INDEX is None:
        _INDEX = ScenarioExecutionIndex.load()
        _GRAPH_TEMPLATE = RoadGraph.load(_INDEX.graph_path)
    for edge in _GRAPH_TEMPLATE.edges.values():
        edge.status = "open"
        edge.cost_multiplier = 1.0
    return _GRAPH_TEMPLATE, _INDEX


if __name__ == "__main__":
    def emit(payload):
        print(json.dumps(payload, allow_nan=False), flush=True)

    if "--ndjson" in sys.argv:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                emit({"record": run(json.loads(line))})
            except (ValueError, KeyError, TypeError) as exc:
                emit({"error": str(exc)})
            except Exception:
                emit({"error": "Save bridge failed"})
    else:
        try:
            emit({"record": run(json.load(sys.stdin))})
        except (ValueError, KeyError, TypeError) as exc:
            emit({"error": str(exc)})
        except Exception:
            emit({"error": "Save bridge failed"})
            raise SystemExit(1)
