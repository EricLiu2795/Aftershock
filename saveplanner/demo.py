"""Drives the Save layer along the frozen Caraballeda timeline.

This is the scenario *player*, not the planner. It holds the current
OperationalBeliefState, advances scenario events, ticks movement, and hands the
belief state to `SaveController`. In production the belief state arrives from
`operational.belief_state` as real observations are fused; here it is replayed from
the pack so the demo is deterministic.

Keeping the player separate from the controller is what lets the controller be
driven by the real pipeline later without touching any planning code.
"""
from __future__ import annotations

import json
from pathlib import Path

from .beliefadapter import EVENTS, belief_from_pack
from .controller import SaveController, format_time, parse_time
from .execution import ScenarioExecutionIndex
from .roadgraph import RoadGraph


class SaveDemo:
    """Belief state + controller + scenario clock."""

    def __init__(self, index=None, graph=None):
        self.index = index or ScenarioExecutionIndex.load()
        self.graph = graph or RoadGraph.load(self.index.graph_path)
        self.controller = SaveController(self.graph, self.index)
        self.events = json.loads(Path(EVENTS).read_text(encoding="utf-8"))["events"]
        self.order = [event["event_id"] for event in self.events]
        self.start = parse_time(self.index.start_time)
        self.cursor = -1
        self.belief = None
        self.sim_speed = 20.0
        self.running = True
        self.advance_to_event(0)

    # -- scenario timeline --------------------------------------------------

    @property
    def event_id(self):
        return self.order[self.cursor] if 0 <= self.cursor < len(self.order) else None

    def advance_to_event(self, cursor):
        """Replace the belief state with the one implied by events up to `cursor`."""
        self.cursor = max(0, min(cursor, len(self.order) - 1))
        scenario_time = (format_time(self.controller.scenario_time)
                         if self.belief is not None else self.index.start_time)
        self.belief = belief_from_pack(self.index, self.order[self.cursor], scenario_time)
        self.controller.plan(self.belief, f"scenario event {self.event_id}")
        return self.event_id

    def next_event(self):
        if self.cursor + 1 >= len(self.order):
            return None
        return self.advance_to_event(self.cursor + 1)

    def jump_to(self, event_id):
        if event_id not in self.order:
            raise KeyError(f"Unknown scenario event: {event_id}")
        return self.advance_to_event(self.order.index(event_id))

    def tick(self, elapsed_wall_s):
        if not self.running:
            return
        self.controller.advance(self.belief, elapsed_wall_s * self.sim_speed)
        # Auto-advance the scenario timeline when its next event is due.
        while self.cursor + 1 < len(self.order):
            due = self.events[self.cursor + 1].get("scenario_time_s", 0)
            elapsed = (self.controller.scenario_time - self.start).total_seconds()
            if elapsed < due:
                break
            self.next_event()

    def decide(self, approve):
        return self.controller.decide_proposal(self.belief, approve)

    def set_road(self, edge_id, status, multiplier=1.0):
        """Standalone harness only. Live runtime posts to canonical road state.

        Applied to the belief state's road_states so it flows through the normal
        path rather than mutating the graph behind the planner's back.
        """
        states = [s for s in self.belief["road_states"] if s["road_id"] != edge_id]
        states.append({
            "schema_version": "2.1", "road_id": edge_id, "status": status,
            "travel_cost_multiplier": multiplier,
            "evidence_ids": ["evidence-commander-override"],
            "reason": "Commander override from the operational map.",
            "based_on_state_version": self.belief["state_version"],
            "updated_at": format_time(self.controller.scenario_time),
        })
        self.belief["road_states"] = sorted(states, key=lambda s: s["road_id"])
        self.belief["state_version"] = f"{self.belief['state_version']}+road"
        self.controller._applied_road_states = None
        self.controller.plan(self.belief, f"road {edge_id} -> {status}")

    # -- presentation -------------------------------------------------------

    def snapshot(self):
        controller, belief = self.controller, self.belief
        worksites = []
        for worksite in belief["worksites"]:
            decision = controller.decisions.get(worksite["worksite_id"])
            site = next((s for s in belief["sites"] if s["site_id"] == worksite["site_id"]), None)
            worksites.append({
                "worksite_id": worksite["worksite_id"],
                "site_id": worksite["site_id"],
                "key": self.index.label(worksite["site_id"]),
                "display_name": worksite.get("display_name") or (site or {}).get("display_name"),
                "position": (site or {}).get("centroid"),
                "evidence_status": worksite["evidence_status"],
                "operational_status": worksite["operational_status"],
                "eligible": decision is not None,
                "save_priority": decision["save_priority"] if decision else None,
                "score_breakdown": decision["score_breakdown"] if decision else None,
                "candidate_assignments": decision["candidate_assignments"] if decision else [],
                "recommended_resource_id": decision["recommended_resource_id"] if decision else None,
                "recommended_task": decision["recommended_task"] if decision else None,
                "reasons": decision["reasons"] if decision else [],
                "assigned_resource_ids": [a["resource_id"] for a in controller.assignments.values()
                                          if a["status"] == "active"
                                          and a["worksite_id"] == worksite["worksite_id"]],
                "simulated": True,
            })
        resources = []
        for resource in controller.rescue_units(belief):
            state = controller.movement.get(resource["resource_id"], {})
            objective = controller.objective_of(resource["resource_id"])
            resources.append({
                "resource_id": resource["resource_id"],
                "display_name": resource["display_name"],
                "status": "en_route" if state.get("movement_mode") == "ground_route" else resource["status"],
                "capability_tags": resource.get("capability_tags", []),
                "current_worksite_id": objective,
                "position": state.get("position") or self.index.staging_position,
                "heading_deg": state.get("heading_deg"),
                "movement_mode": state.get("movement_mode", "stationary"),
                "route_progress": state.get("route_progress", 0.0),
                "route_id": state.get("route_id"),
                "distance_remaining_m": state.get("distance_remaining_m", 0.0),
                "eta_s": (state.get("distance_remaining_m", 0.0) / state["speed_mps"]
                          if state.get("speed_mps") else None),
            })
        proposal = None
        if controller.proposal:
            import time as _time
            proposal = dict(controller.proposal)
            proposal["seconds_left"] = max(0.0, proposal["expires_at_wall"] - _time.time())
        return {
            "state_version": belief["state_version"],
            "scenario": self.index.scenario_id,
            "scenario_event": self.event_id,
            "scenario_time": format_time(controller.scenario_time),
            "elapsed_s": (controller.scenario_time - self.start).total_seconds(),
            "sim_speed": self.sim_speed,
            "running": self.running,
            "worksites": worksites,
            "resources": resources,
            "routes": [{"route_id": r["route_id"], "resource_id": r["resource_id"],
                        "worksite_id": r["worksite_id"], "path": r["path"],
                        "road_edge_ids": r["road_edge_ids"],
                        "estimated_travel_time_s": r["estimated_travel_time_s"],
                        "total_distance_m": r["total_distance_m"]}
                       for r in controller.active_routes()],
            "blocked_edge_ids": self.graph.blocked_edge_ids(),
            "limited_edge_ids": self.graph.limited_edge_ids(),
            "proposal": proposal,
            "trace": controller.trace,
        }
