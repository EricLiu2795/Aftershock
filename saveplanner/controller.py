"""Save controller: OperationalBeliefState -> SaveDecision -> Assignment -> Route.

State ownership, which is the point of this module's shape:

  * The controller **reads** OperationalBeliefState: worksites, incidents, resources,
    road_states, scenario_time, state_version. It never writes belief facts, never
    keeps a private copy of evidence, and never advances `state_version` itself.
  * The controller **owns** execution state only: SaveDecision, Assignment, Route,
    the MovementState of ground units it is driving, and reassignment proposals.

Static routing facts (arrival nodes, staging, resource roster) come from the
scenario execution index, not from belief and not from invented metadata.

Route change versus objective change stays the hard line of sections 21-23: a road
change reroutes automatically to the same objective; changing the objective needs a
human, and the proposal is fully revalidated at approval time.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

from . import planner
from .roadgraph import haversine

REASSIGNMENT_WINDOW_S = 30.0
TRACE_LIMIT = 60

# How long a crew works a site once it arrives, scaled by how many people are
# believed trapped there. Measured in real seconds, like the reassignment window
# and unlike everything else in this file, so the dwell a commander sees on screen
# is the same at 1x and at 20x. A site with no victim estimate still takes one
# unit of work rather than zero -- an instant rescue would look like a bug.
RESCUE_SECONDS_PER_VICTIM = 3.0
MIN_RESCUE_SECONDS = RESCUE_SECONDS_PER_VICTIM


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_time(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bearing(start, end):
    lat1, lat2 = math.radians(start["latitude"]), math.radians(end["latitude"])
    delta = math.radians(end["longitude"] - start["longitude"])
    y = math.sin(delta) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


class SaveController:
    """Execution engine for the Save layer. Holds no operational belief."""

    def __init__(self, graph, index):
        self.graph = graph
        self.index = index
        # Execution state only.
        self.decisions = {}
        self.assignments = {}
        self.routes = {}
        self.movement = {}
        self.proposal = None
        self.trace = []
        # Rescue in progress: resource_id -> wall-clock deadline.
        self.rescuing = {}
        # Worksites this controller has already worked. Save cannot mark a Worksite
        # resolved -- that is a belief fact owned upstream -- so it remembers what it
        # served and stops dispatching there. Without this the freed unit would be
        # re-assigned to the site it just finished, forever.
        self.served = set()
        self._next_id = 1
        # Cached view of the last belief state this controller planned against.
        self.state_version = None
        self.scenario_time = parse_time(index.start_time)
        self._applied_road_states = None

    def _next(self):
        value = self._next_id
        self._next_id += 1
        return value

    def export_execution(self):
        """JSON-safe execution state. Never includes canonical belief facts."""
        return {
            "decisions": self.decisions,
            "assignments": self.assignments,
            "routes": self.routes,
            "movement": self.movement,
            "proposal": self.proposal,
            "trace": self.trace,
            # Wall-clock deadlines and served sites are execution state: without
            # them a live tick would restart every rescue and re-dispatch to sites
            # already worked.
            "rescuing": self.rescuing,
            "served": sorted(self.served),
            "next_id": self._next_id,
            "state_version": self.state_version,
            "scenario_time": format_time(self.scenario_time),
        }

    def restore_execution(self, payload):
        """Reload execution state onto a fresh controller/graph."""
        if not payload:
            return self
        self.decisions = dict(payload.get("decisions") or {})
        self.assignments = dict(payload.get("assignments") or {})
        self.routes = dict(payload.get("routes") or {})
        self.movement = dict(payload.get("movement") or {})
        self.proposal = payload.get("proposal")
        self.trace = list(payload.get("trace") or [])
        self.rescuing = {k: float(v) for k, v in (payload.get("rescuing") or {}).items()}
        self.served = set(payload.get("served") or [])
        self._next_id = int(payload.get("next_id") or 1)
        self.state_version = payload.get("state_version")
        if payload.get("scenario_time"):
            self.scenario_time = parse_time(payload["scenario_time"])
        # Fresh graphs must re-apply canonical road_states on the next adopt().
        self._applied_road_states = None
        return self

    # -- belief-state access ------------------------------------------------

    def adopt(self, belief):
        """Take the road network and clock from the belief state before planning."""
        self.state_version = belief["state_version"]
        belief_time = parse_time(belief["scenario_time"])
        if belief_time >= self.scenario_time:
            self.scenario_time = belief_time
        signature = [(s["road_id"], s["status"], s.get("travel_cost_multiplier", 1.0))
                     for s in belief.get("road_states", [])]
        if signature != self._applied_road_states:
            self.graph.apply_road_states(belief.get("road_states", []))
            self._applied_road_states = signature
            return True
        return False

    def seed_movement(self, belief):
        """Start from the belief state's MovementState for each ground unit."""
        for state in belief.get("movement_states", []):
            resource = self.resource(belief, state["resource_id"])
            if resource and self.index.is_rescue_unit(resource):
                self.movement.setdefault(state["resource_id"], dict(state))

    @staticmethod
    def resource(belief, resource_id):
        for item in belief.get("resources", []):
            if item["resource_id"] == resource_id:
                return item
        return None

    def rescue_units(self, belief):
        return [r for r in belief.get("resources", []) if self.index.is_rescue_unit(r)]

    def eligible(self, belief):
        """Evidence gate (section 19), plus the scenario's active-site freeze.

        A worksite the scenario has not frozen an arrival node for cannot be routed
        to, because the pack forbids treating `access_connections` as routable.
        """
        out = []
        for worksite in belief.get("worksites", []):
            if worksite.get("operational_status") == "resolved":
                continue
            if worksite.get("evidence_status") not in planner.ELIGIBLE_EVIDENCE:
                continue
            if self.index.arrival_node(worksite["site_id"]) is None:
                continue
            if worksite["worksite_id"] in self.served:
                continue
            out.append(worksite)
        return out

    def approach_m(self, belief, worksite):
        """Walk from the frozen arrival node to the building centroid."""
        record = self.index.arrival.get(worksite["site_id"])
        if not record or not record.get("arrival_position"):
            return 0.0
        site = next((s for s in belief.get("sites", []) if s["site_id"] == worksite["site_id"]), None)
        if not site:
            return 0.0
        centre, arrival = site["centroid"], record["arrival_position"]
        return haversine([centre["longitude"], centre["latitude"]],
                         [arrival["longitude"], arrival["latitude"]])

    def victims_at(self, worksite):
        """People believed trapped, for sizing the on-site rescue effort.

        Reads the fused Worksite summary rather than ground truth. An unknown count
        is treated as one person: the crew still has to work the site.
        """
        summary = worksite.get("victim_summary") or {}
        for key in ("best_estimate", "count_min", "count_max"):
            value = summary.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        return 1

    def rescue_seconds_left(self, resource_id):
        """Remaining on-site work in real seconds, or None when not rescuing."""
        deadline = self.rescuing.get(resource_id)
        return None if deadline is None else max(0.0, deadline - time.time())

    def rescue_total_seconds(self, belief, resource_id):
        """Full on-site duration for the worksite this unit is working, or None.

        Recomputed from the victim count rather than stored, so a progress bar can
        show elapsed/total without the deadline alone being ambiguous.
        """
        if resource_id not in self.rescuing:
            return None
        assignment = self.active_assignment(resource_id)
        if assignment is None:
            return None
        worksite = next((w for w in belief.get("worksites", [])
                         if w["worksite_id"] == assignment["worksite_id"]), None)
        victims = self.victims_at(worksite) if worksite else 1
        return max(MIN_RESCUE_SECONDS, RESCUE_SECONDS_PER_VICTIM * victims)

    def minutes_waiting(self, worksite):
        created = worksite.get("created_at")
        if not created:
            return 0.0
        return max(0.0, (self.scenario_time - parse_time(created)).total_seconds() / 60.0)

    def label(self, belief, worksite_id):
        for worksite in belief.get("worksites", []):
            if worksite["worksite_id"] == worksite_id:
                return self.index.label(worksite["site_id"])
        return worksite_id

    # -- routing ------------------------------------------------------------

    def unit_position(self, belief, resource_id):
        state = self.movement.get(resource_id)
        if state:
            return state["position"]
        for item in belief.get("movement_states", []):
            if item["resource_id"] == resource_id:
                return item["position"]
        return self.index.staging_position

    def build_route(self, belief, resource_id, worksite):
        """Route from the unit's live position to the worksite's frozen arrival node.

        The origin is the exact MovementState position projected onto the road, not
        the nearest junction, so a reroute mid-edge does not teleport the unit. The
        destination is the scenario's audited arrival node rather than a node guessed
        from the building centroid.
        """
        arrival_node = self.index.arrival_node(worksite["site_id"])
        if arrival_node is None or arrival_node not in self.graph.nodes:
            return None
        position = self.unit_position(belief, resource_id)
        found = self.graph.route_to_node(
            [position["longitude"], position["latitude"]], arrival_node)
        if found is None:
            return None
        path = found["path"]
        if len(path) < 2:
            path = [path[0], path[0]]
        resource = self.resource(belief, resource_id) or {}
        speed = self.speed_of(resource)
        return {
            "schema_version": "2.1",
            "route_id": f"route-{resource_id}-{worksite['worksite_id']}-{self._next()}",
            "resource_id": resource_id,
            "worksite_id": worksite["worksite_id"],
            "route_type": "road",
            "path": [{"latitude": p[1], "longitude": p[0]} for p in path],
            "road_edge_ids": found["edge_ids"],
            "total_distance_m": found["distance_m"],
            "estimated_travel_time_s": found["distance_m"] / speed if speed else found["seconds"],
            "route_status": "planned",
            "generated_from_state_version": self.state_version or "unknown",
            "created_at": format_time(self.scenario_time),
            "start_position": {"latitude": path[0][1], "longitude": path[0][0]},
            "end_position": {"latitude": path[-1][1], "longitude": path[-1][0]},
        }

    @staticmethod
    def speed_of(resource):
        speed = resource.get("speed") or {"value": 25.0, "unit": "km_per_h"}
        value = float(speed["value"])
        if speed["unit"] == "km_per_h":
            return value / 3.6
        if speed["unit"] == "minutes_per_km":
            return 1000.0 / (value * 60.0) if value else 0.0
        return value

    def route_is_valid(self, route):
        return all(not self.graph.edges[edge_id].blocked
                   for edge_id in route.get("road_edge_ids", [])
                   if edge_id in self.graph.edges)

    # -- scoring ------------------------------------------------------------

    def evaluate(self, belief, resource_id, worksite):
        route = self.build_route(belief, resource_id, worksite)
        if route is None:
            return None
        types = planner.incident_types(worksite, belief.get("incidents", []))
        priority, breakdown = planner.save_priority(worksite, types, self.minutes_waiting(worksite))
        cost = planner.travel_cost(route["estimated_travel_time_s"])
        risk = planner.responder_risk(worksite, types, self.approach_m(belief, worksite))
        mismatch = planner.capability_mismatch(self.resource(belief, resource_id) or {}, worksite, types)
        return {
            "resource_id": resource_id,
            "worksite_id": worksite["worksite_id"],
            "save_priority": priority,
            "score_breakdown": breakdown,
            "travel_cost": cost,
            "responder_risk": risk,
            "capability_mismatch": mismatch,
            "assignment_utility": planner.assignment_utility(priority, cost, risk, mismatch),
            "route": route,
            "incident_types": sorted(types),
        }

    def task_for(self, types):
        if {"trapped_victims", "structural_damage"} & set(types):
            return "rescue_worksite"
        if "evacuation_need" in set(types):
            return "evacuate_civilians"
        return "monitor_worksite"

    # -- planning -----------------------------------------------------------

    def plan(self, belief, reason="replan"):
        """Produce SaveDecisions and assign free units. Returns the changed flag."""
        self.adopt(belief)
        self.seed_movement(belief)
        changed = self.release_ineligible(belief)
        candidates = []
        self.decisions = {}

        for worksite in sorted(self.eligible(belief), key=lambda w: w["worksite_id"]):
            types = planner.incident_types(worksite, belief.get("incidents", []))
            priority, breakdown = planner.save_priority(worksite, types, self.minutes_waiting(worksite))
            decision = {
                "schema_version": "2.1",
                "decision_id": f"save-{worksite['worksite_id']}-{self.state_version}",
                "state_version": self.state_version,
                "worksite_id": worksite["worksite_id"],
                "save_priority": priority,
                "score_breakdown": breakdown,
                "candidate_assignments": [],
                "recommended_resource_id": None,
                "recommended_task": self.task_for(types),
                "reasons": [
                    f"SavePriority {priority:.2f} from V {breakdown['victim_impact']:.2f}, "
                    f"T {breakdown['time_sensitivity']:.2f}, H {breakdown['hazard_severity']:.2f}, "
                    f"C {breakdown['criticality']:.2f}.",
                    f"Evidence status is {worksite['evidence_status']}.",
                ],
                "created_at": format_time(self.scenario_time),
            }
            for resource in sorted(self.rescue_units(belief), key=lambda r: r["resource_id"]):
                evaluation = self.evaluate(belief, resource["resource_id"], worksite)
                if evaluation is None:
                    continue
                decision["candidate_assignments"].append({
                    "resource_id": evaluation["resource_id"],
                    "assignment_utility": evaluation["assignment_utility"],
                    "travel_cost": evaluation["travel_cost"],
                    "responder_risk": evaluation["responder_risk"],
                    "capability_mismatch": evaluation["capability_mismatch"],
                })
                candidates.append(evaluation)
            if decision["candidate_assignments"]:
                decision["recommended_resource_id"] = max(
                    decision["candidate_assignments"],
                    key=lambda c: c["assignment_utility"])["resource_id"]
            else:
                decision["recommended_task"] = "clear_route"
                decision["reasons"].append(
                    "No passable route from any rescue unit; the approach is blocked.")
            self.decisions[worksite["worksite_id"]] = decision

        busy = {a["resource_id"] for a in self.assignments.values() if a["status"] == "active"}
        taken = {a["worksite_id"] for a in self.assignments.values() if a["status"] == "active"}
        for evaluation in sorted(candidates, key=lambda c: (-c["assignment_utility"], c["resource_id"])):
            if evaluation["resource_id"] in busy or evaluation["worksite_id"] in taken:
                continue
            self.commit(belief, evaluation, "initial assignment")
            busy.add(evaluation["resource_id"])
            taken.add(evaluation["worksite_id"])
            changed = True

        if self.revalidate(belief):
            changed = True
        self.consider_reassignment(belief, candidates)
        self.log(reason, "")
        return changed

    def commit(self, belief, evaluation, why):
        resource_id = evaluation["resource_id"]
        route = evaluation["route"]
        route["route_status"] = "active"
        for existing in self.routes.values():
            if existing["resource_id"] == resource_id and existing["route_status"] == "active":
                existing["route_status"] = "superseded"
        self.routes[route["route_id"]] = route
        assignment_id = f"assignment-{resource_id}-{evaluation['worksite_id']}-{self._next()}"
        for existing in self.assignments.values():
            if existing["resource_id"] == resource_id and existing["status"] == "active":
                existing["status"] = "cancelled"
        self.assignments[assignment_id] = {
            "schema_version": "2.1",
            "assignment_id": assignment_id,
            "resource_id": resource_id,
            "worksite_id": evaluation["worksite_id"],
            "task_type": self.task_for(evaluation["incident_types"]),
            "status": "active",
            "route_id": route["route_id"],
            "source_decision_id": self.decisions.get(evaluation["worksite_id"], {}).get("decision_id"),
            "assigned_at": format_time(self.scenario_time),
            "started_at": format_time(self.scenario_time),
            "completed_at": None,
        }
        self.start_movement(belief, resource_id, route)
        self.log(f"{self.unit_name(belief, resource_id)} assigned to "
                 f"{self.label(belief, evaluation['worksite_id'])}",
                 f"{why}; utility {evaluation['assignment_utility']:.2f}, "
                 f"{route['total_distance_m']:.0f} m, "
                 f"ETA {route['estimated_travel_time_s'] / 60:.1f} min")

    def release_ineligible(self, belief):
        """Drop assignments whose canonical Worksite is no longer a Save target."""
        eligible_ids = {worksite["worksite_id"] for worksite in self.eligible(belief)}
        changed = False
        for assignment in list(self.assignments.values()):
            if assignment["status"] != "active":
                continue
            if assignment["worksite_id"] in eligible_ids:
                continue
            assignment["status"] = "cancelled"
            route = self.routes.get(assignment["route_id"])
            if route and route.get("route_status") == "active":
                route["route_status"] = "cancelled"
            state = self.movement.get(assignment["resource_id"])
            if state:
                state.update({
                    "movement_mode": "stationary",
                    "speed_mps": 0.0,
                    "route_id": None,
                })
            self.log(
                f"{self.unit_name(belief, assignment['resource_id'])} released",
                f"{self.label(belief, assignment['worksite_id'])} is no longer an active Save target.",
            )
            changed = True
        if self.proposal and self.proposal.get("status") == "pending":
            current_id = self.proposal.get("current_worksite_id")
            proposed_id = self.proposal.get("proposed_worksite_id")
            if current_id not in eligible_ids or proposed_id not in eligible_ids:
                self.proposal["status"] = "invalidated"
                changed = True
        return changed

    def unit_name(self, belief, resource_id):
        resource = self.resource(belief, resource_id)
        return (resource or {}).get("display_name", resource_id)

    def objective_of(self, resource_id):
        for assignment in self.assignments.values():
            if assignment["resource_id"] == resource_id and assignment["status"] == "active":
                return assignment["worksite_id"]
        return None

    def active_assignment(self, resource_id):
        for assignment in self.assignments.values():
            if assignment["resource_id"] == resource_id and assignment["status"] == "active":
                return assignment
        return None

    # -- automatic rerouting (same objective) -------------------------------

    def revalidate(self, belief):
        """Reroute any unit whose path now crosses a BLOCKED edge.

        Objective unchanged, so no human approval (section 21). The replacement is
        computed from the live MovementState position.
        """
        changed = False
        for assignment in list(self.assignments.values()):
            if assignment["status"] != "active":
                continue
            route = self.routes.get(assignment["route_id"])
            if route is None or self.route_is_valid(route):
                continue
            worksite = next((w for w in belief["worksites"]
                             if w["worksite_id"] == assignment["worksite_id"]), None)
            if worksite is None:
                continue
            before = route["total_distance_m"]
            travelled = self.movement.get(assignment["resource_id"], {}).get("distance_traveled_m", 0.0)
            replacement = self.build_route(belief, assignment["resource_id"], worksite)
            name = self.unit_name(belief, assignment["resource_id"])
            if replacement is None:
                route["route_status"] = "cancelled"
                assignment["status"] = "cancelled"
                self.log(f"{name} is cut off from {self.label(belief, worksite['worksite_id'])}",
                         "Every approach is BLOCKED; released for re-tasking, worksite needs clear_route.")
                changed = True
                continue
            route["route_status"] = "superseded"
            replacement["route_status"] = "active"
            self.routes[replacement["route_id"]] = replacement
            assignment["route_id"] = replacement["route_id"]
            self.start_movement(belief, assignment["resource_id"], replacement)
            self.log(f"{name} rerouted to {self.label(belief, worksite['worksite_id'])}",
                     f"BLOCKED road avoided. Travelled {travelled:.0f} m; remaining "
                     f"{before:.0f} m -> {replacement['total_distance_m']:.0f} m. "
                     "Objective unchanged, so no approval was required.")
            changed = True
        return changed

    # -- movement -----------------------------------------------------------

    def start_movement(self, belief, resource_id, route):
        resource = self.resource(belief, resource_id) or {}
        state = self.movement.setdefault(resource_id, {
            "schema_version": "2.1", "resource_id": resource_id,
            "movement_mode": "stationary", "route_id": None,
            "position": dict(route["path"][0]), "segment_index": None,
            "segment_progress": None, "route_progress": 0.0, "speed_mps": 0.0,
            "heading_deg": None, "started_at": None,
            "last_updated_at": format_time(self.scenario_time),
            "estimated_arrival_at": None,
            "distance_traveled_m": 0.0, "distance_remaining_m": 0.0})
        state.update({
            "movement_mode": "ground_route",
            "route_id": route["route_id"],
            "position": dict(route["path"][0]),
            "segment_index": 0,
            "segment_progress": 0.0,
            "route_progress": 0.0,
            "speed_mps": self.speed_of(resource),
            "started_at": format_time(self.scenario_time),
            "last_updated_at": format_time(self.scenario_time),
            "distance_traveled_m": 0.0,
            "distance_remaining_m": route["total_distance_m"],
            "estimated_arrival_at": format_time(
                self.scenario_time + timedelta(seconds=route["estimated_travel_time_s"])),
        })

    def advance(self, belief, elapsed_s):
        """Move every assigned unit along its route. Returns units that arrived."""
        self.scenario_time += timedelta(seconds=elapsed_s)
        arrived = []
        for resource_id, state in self.movement.items():
            if state["movement_mode"] != "ground_route":
                continue
            route = self.routes.get(state["route_id"])
            if route is None or route["route_status"] != "active":
                continue
            if self._step(state, route, elapsed_s * state["speed_mps"]):
                arrived.append(resource_id)
            state["last_updated_at"] = format_time(self.scenario_time)
        for resource_id in arrived:
            self.on_arrival(belief, resource_id)
        # A finished rescue frees the crew, which is a planning event: replan once so
        # it is dispatched to the next eligible worksite instead of idling on site.
        released = [rid for rid in self.finished_rescues()
                    if self.complete_rescue(belief, rid)]
        if released:
            self.plan(belief, "rescue completed, unit available")
        self.expire_proposal()
        return arrived

    def finished_rescues(self):
        """Units whose on-site work has run out, by wall clock."""
        now = time.time()
        return [rid for rid, deadline in list(self.rescuing.items()) if now >= deadline]

    def complete_rescue(self, belief, resource_id):
        """Close out the assignment and release the crew for the next worksite."""
        self.rescuing.pop(resource_id, None)
        assignment = self.active_assignment(resource_id)
        if assignment is None:
            return False
        assignment["status"] = "completed"
        assignment["completed_at"] = format_time(self.scenario_time)
        # Save records what it served. It does not mark the Worksite resolved,
        # because operational_status is a belief fact owned upstream.
        self.served.add(assignment["worksite_id"])
        state = self.movement.get(resource_id)
        if state:
            state.update({"movement_mode": "stationary", "speed_mps": 0.0,
                          "route_id": None, "route_progress": 0.0,
                          "distance_remaining_m": 0.0, "estimated_arrival_at": None})
        self.log(f"{self.unit_name(belief, resource_id)} completed "
                 f"{self.label(belief, assignment['worksite_id'])}",
                 "Rescue finished; unit is available for the next verified worksite.")
        return True

    def _step(self, state, route, distance_m):
        path = route["path"]
        index = state["segment_index"] or 0
        progress = state["segment_progress"] or 0.0
        remaining = distance_m
        while index < len(path) - 1 and remaining > 0:
            start, end = path[index], path[index + 1]
            length = haversine([start["longitude"], start["latitude"]],
                               [end["longitude"], end["latitude"]])
            if length <= 0:
                index, progress = index + 1, 0.0
                continue
            done = progress * length
            if done + remaining >= length:
                remaining -= (length - done)
                index, progress = index + 1, 0.0
            else:
                progress = (done + remaining) / length
                remaining = 0.0
        if index >= len(path) - 1:
            state.update({"position": dict(path[-1]), "segment_index": max(0, len(path) - 2),
                          "segment_progress": 1.0, "route_progress": 1.0,
                          "distance_traveled_m": route["total_distance_m"],
                          "distance_remaining_m": 0.0})
            return True
        start, end = path[index], path[index + 1]
        state["position"] = {
            "latitude": start["latitude"] + (end["latitude"] - start["latitude"]) * progress,
            "longitude": start["longitude"] + (end["longitude"] - start["longitude"]) * progress,
        }
        state["segment_index"], state["segment_progress"] = index, progress
        state["heading_deg"] = bearing(start, end)
        covered = self._covered(path, index, progress)
        state["distance_traveled_m"] = covered
        state["distance_remaining_m"] = max(0.0, route["total_distance_m"] - covered)
        state["route_progress"] = (covered / route["total_distance_m"]
                                   if route["total_distance_m"] else 1.0)
        state["estimated_arrival_at"] = format_time(self.scenario_time + timedelta(
            seconds=state["distance_remaining_m"] / (state["speed_mps"] or 1.0)))
        return False

    @staticmethod
    def _covered(path, index, progress):
        total = 0.0
        for i in range(index):
            total += haversine([path[i]["longitude"], path[i]["latitude"]],
                               [path[i + 1]["longitude"], path[i + 1]["latitude"]])
        if index < len(path) - 1:
            total += progress * haversine(
                [path[index]["longitude"], path[index]["latitude"]],
                [path[index + 1]["longitude"], path[index + 1]["latitude"]])
        return total

    def on_arrival(self, belief, resource_id):
        state = self.movement[resource_id]
        state.update({"movement_mode": "stationary", "speed_mps": 0.0, "route_progress": 1.0})
        route = self.routes.get(state["route_id"])
        if route:
            route["route_status"] = "completed"
        assignment = self.active_assignment(resource_id)
        if not assignment:
            return
        worksite = next((w for w in belief.get("worksites", [])
                         if w["worksite_id"] == assignment["worksite_id"]), None)
        victims = self.victims_at(worksite) if worksite else 1
        seconds = max(MIN_RESCUE_SECONDS, RESCUE_SECONDS_PER_VICTIM * victims)
        self.rescuing[resource_id] = time.time() + seconds
        self.log(f"{self.unit_name(belief, resource_id)} on site at "
                 f"{self.label(belief, assignment['worksite_id'])}",
                 f"Arrived at the scenario arrival node; final approach on foot. "
                 f"Working {victims} casualt{'y' if victims == 1 else 'ies'}, "
                 f"about {seconds:.0f}s on site.")

    # -- human reassignment gate --------------------------------------------

    def consider_reassignment(self, belief, candidates):
        if self.proposal and self.proposal["status"] == "pending":
            return
        best = None
        taken = {a["worksite_id"] for a in self.assignments.values() if a["status"] == "active"}
        for evaluation in candidates:
            resource_id = evaluation["resource_id"]
            current_id = self.objective_of(resource_id)
            if not current_id or evaluation["worksite_id"] == current_id:
                continue
            if evaluation["worksite_id"] in taken:
                continue
            current_worksite = next((w for w in belief["worksites"]
                                     if w["worksite_id"] == current_id), None)
            if current_worksite is None:
                continue
            current = self.evaluate(belief, resource_id, current_worksite)
            if current is None:
                continue
            progress = self.movement.get(resource_id, {}).get("route_progress", 0.0)
            extra = planner.clamp(
                (evaluation["route"]["estimated_travel_time_s"]
                 - current["route"]["estimated_travel_time_s"]) / planner.TRAVEL_HORIZON_S)
            gain = planner.reassignment_gain(evaluation["assignment_utility"],
                                             current["assignment_utility"], progress, extra)
            if gain > planner.REASSIGNMENT_THRESHOLD and (best is None or gain > best["gain"]):
                best = {"gain": gain, "evaluation": evaluation, "current": current,
                        "progress_loss": progress, "additional_travel": extra}
        if best is None:
            return
        evaluation = best["evaluation"]
        resource_id = evaluation["resource_id"]
        self.proposal = {
            "schema_version": "2.1",
            "proposal_id": f"reassignment-{resource_id}-{self.state_version}",
            "resource_id": resource_id,
            "resource_name": self.unit_name(belief, resource_id),
            "current_worksite_id": self.objective_of(resource_id),
            "current_label": self.label(belief, self.objective_of(resource_id)),
            "proposed_worksite_id": evaluation["worksite_id"],
            "proposed_label": self.label(belief, evaluation["worksite_id"]),
            "reassignment_gain": best["gain"],
            "threshold": planner.REASSIGNMENT_THRESHOLD,
            "breakdown": {
                "proposed_utility": evaluation["assignment_utility"],
                "current_utility": best["current"]["assignment_utility"],
                "progress_loss": best["progress_loss"],
                "additional_travel": best["additional_travel"],
                "switch_cost": planner.switch_cost(best["progress_loss"], best["additional_travel"]),
            },
            "status": "pending",
            "based_on_state_version": self.state_version,
            "expires_at_wall": time.time() + REASSIGNMENT_WINDOW_S,
            "created_at": format_time(self.scenario_time),
        }
        self.log(f"Reassignment proposed for {self.proposal['resource_name']}",
                 f"{self.proposal['current_label']} -> {self.proposal['proposed_label']}, "
                 f"gain +{best['gain']:.2f} over threshold "
                 f"+{planner.REASSIGNMENT_THRESHOLD:.2f}")

    def decide_proposal(self, belief, approve):
        """Answer the gate. Approval revalidates the proposal against current truth.

        A proposal raised 25 seconds ago may be stale in several ways at once, so
        every precondition is re-checked rather than trusting the stored numbers:
        deadline, the unit still pursuing the same objective, the target still
        eligible and unclaimed, the belief state, route feasibility from the unit's
        *current* position, and the gain still clearing the threshold.
        """
        proposal = self.proposal
        if not proposal or proposal["status"] != "pending":
            return False, "no pending proposal"
        if not approve:
            proposal["status"] = "rejected"
            self.log(f"Reassignment rejected for {proposal['resource_name']}",
                     f"{proposal['resource_name']} keeps {proposal['current_label']}.")
            return True, "rejected"

        self.adopt(belief)
        reason = self._revalidate_proposal(belief, proposal)
        if reason:
            proposal["status"] = "invalidated"
            self.log(f"Reassignment invalidated for {proposal['resource_name']}", reason)
            return True, reason

        worksite = next(w for w in belief["worksites"]
                        if w["worksite_id"] == proposal["proposed_worksite_id"])
        evaluation = self.evaluate(belief, proposal["resource_id"], worksite)
        proposal["status"] = "approved"
        self.commit(belief, evaluation, "human-approved reassignment")
        self.log(f"Reassignment approved for {proposal['resource_name']}",
                 f"Routed from the unit's current position to {proposal['proposed_label']}.")
        return True, "approved"

    def _revalidate_proposal(self, belief, proposal):
        """Return a reason string when the proposal is no longer valid, else None."""
        if time.time() >= proposal["expires_at_wall"]:
            return "The 30-second decision window had already expired."
        resource_id = proposal["resource_id"]
        if self.objective_of(resource_id) != proposal["current_worksite_id"]:
            return "The unit is no longer pursuing the objective this proposal was raised against."
        target = next((w for w in belief["worksites"]
                       if w["worksite_id"] == proposal["proposed_worksite_id"]), None)
        if target is None:
            return "The proposed worksite is no longer in the operational belief state."
        if target.get("operational_status") == "resolved":
            return "The proposed worksite has since been resolved."
        if target.get("evidence_status") not in planner.ELIGIBLE_EVIDENCE:
            return (f"Evidence for the proposed worksite fell back to "
                    f"{target.get('evidence_status')}; it no longer passes the Save gate.")
        claimed = {a["worksite_id"] for a in self.assignments.values()
                   if a["status"] == "active" and a["resource_id"] != resource_id}
        if proposal["proposed_worksite_id"] in claimed:
            return "Another unit has since been assigned to the proposed worksite."
        evaluation = self.evaluate(belief, resource_id, target)
        if evaluation is None:
            return "No passable route to the proposed worksite from the unit's current position."
        current_worksite = next((w for w in belief["worksites"]
                                 if w["worksite_id"] == proposal["current_worksite_id"]), None)
        if current_worksite is None:
            return "The current objective has left the operational belief state."
        current = self.evaluate(belief, resource_id, current_worksite)
        if current is None:
            return None            # current objective unreachable: switching is clearly right
        progress = self.movement.get(resource_id, {}).get("route_progress", 0.0)
        extra = planner.clamp(
            (evaluation["route"]["estimated_travel_time_s"]
             - current["route"]["estimated_travel_time_s"]) / planner.TRAVEL_HORIZON_S)
        gain = planner.reassignment_gain(evaluation["assignment_utility"],
                                         current["assignment_utility"], progress, extra)
        if gain <= planner.REASSIGNMENT_THRESHOLD:
            return (f"Recomputed ReassignmentGain is +{gain:.3f}, no longer above the "
                    f"+{planner.REASSIGNMENT_THRESHOLD:.2f} threshold.")
        if proposal["based_on_state_version"] != self.state_version:
            proposal["revalidated_against_state_version"] = self.state_version
        return None

    def expire_proposal(self):
        if self.proposal and self.proposal["status"] == "pending" \
                and time.time() >= self.proposal["expires_at_wall"]:
            self.proposal["status"] = "expired"
            self.log(f"Reassignment expired for {self.proposal['resource_name']}",
                     "No response in 30 s; the existing assignment stands.")

    # -- output -------------------------------------------------------------

    def log(self, headline, detail=""):
        self.trace.insert(0, {"at": format_time(self.scenario_time),
                              "state_version": self.state_version,
                              "headline": headline, "detail": detail})
        del self.trace[TRACE_LIMIT:]

    def active_routes(self):
        return [r for r in self.routes.values() if r["route_status"] == "active"]

    def movement_states(self):
        """Save-owned MovementState for the ground units it is driving."""
        return [dict(state) for state in self.movement.values()]
