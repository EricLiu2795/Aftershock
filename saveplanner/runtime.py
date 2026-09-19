"""Live Save-planner runtime: scoring, routing, movement and event-driven replanning.

Responsibility split follows the architecture freeze:

  * Save planner decides *which* worksite a unit should serve (sections 19-20).
  * Route planner decides *how* it gets there, over the damaged road graph.
  * MovementState says where it physically is right now (section 18.1).
  * A route change with the same objective is automatic; an objective change needs
    a human (sections 21-23). That distinction is enforced in `_consider_reassignment`.

Planner recomputation is event driven (section 32). The movement tick runs
continuously but never invokes the planner by itself -- only arrival, completion,
a road-state change or an approved reassignment does.
"""
from __future__ import annotations

import itertools
import math
import time
from datetime import datetime, timedelta, timezone

from . import planner
from .roadgraph import haversine

REASSIGNMENT_WINDOW_S = 30.0     # real wall-clock seconds, section 24
WORK_DURATION_S = 90.0           # simulated on-site rescue effort
TRACE_LIMIT = 60

# Simulated seconds between priority re-scores. SavePriority has a time-sensitivity
# term that grows as a worksite waits, so without a periodic pass a worksite could
# become the most urgent on the board and nothing would ever notice. This is still
# event-driven planning in the sense of section 32 -- it is a coarse timer, not a
# recompute per animation frame -- and a pass that changes nothing is silent.
RESCORE_INTERVAL_S = 20.0


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_time(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SavePlannerRuntime:
    """Holds the operational belief state and advances it."""

    def __init__(self, graph, scenario):
        self.graph = graph
        self.scenario_name = scenario["scenario"]
        self.worksites = {w["worksite_id"]: w for w in scenario["worksites"]}
        self.resources = {r["resource_id"]: r for r in scenario["resources"]}
        self.movement = {}
        self.assignments = {}
        self.routes = {}
        self.save_decisions = {}
        self.proposal = None
        self.trace = []
        self.state_counter = itertools.count(1)
        self.state_version = f"state-{next(self.state_counter)}"
        self.scenario_time = parse_time(scenario["created_at"])
        self.started_at = self.scenario_time
        self.sim_speed = 20.0
        self.running = True
        self._work_until = {}
        self._since_rescore = 0.0
        self._changed = False
        self._cut_off = False

        for resource in self.resources.values():
            position = resource["metadata"]["position"]
            self.movement[resource["resource_id"]] = {
                "schema_version": "2.1",
                "resource_id": resource["resource_id"],
                "movement_mode": "stationary",
                "route_id": None,
                "position": dict(position),
                "segment_index": None,
                "segment_progress": None,
                "route_progress": 0.0,
                "speed_mps": 0.0,
                "heading_deg": None,
                "started_at": None,
                "last_updated_at": format_time(self.scenario_time),
                "estimated_arrival_at": None,
                "distance_traveled_m": 0.0,
                "distance_remaining_m": 0.0,
            }

        for edge_id in scenario["blocked_edge_ids"]:
            self.graph.set_blocked(edge_id, True)
        self.log("Scenario initialised", f"{len(self.worksites)} worksites, "
                 f"{len(self.resources)} rescue units, {len(scenario['blocked_edge_ids'])} roads blocked")
        self.replan("initial planning")

    # -- bookkeeping -------------------------------------------------------

    def bump(self):
        """Every meaningful change increments state_version (section 16)."""
        self.state_version = f"state-{next(self.state_counter)}"
        return self.state_version

    def log(self, headline, detail=""):
        self.trace.insert(0, {
            "at": format_time(self.scenario_time),
            "state_version": self.state_version,
            "headline": headline,
            "detail": detail,
        })
        del self.trace[TRACE_LIMIT:]

    def speed_of(self, resource):
        speed = resource.get("speed") or {"value": 30.0, "unit": "km_per_h"}
        value = float(speed["value"])
        if speed["unit"] == "km_per_h":
            return value * 0.55 / 3.6        # same disaster factor as the graph
        if speed["unit"] == "minutes_per_km":
            return 1000.0 / (value * 60.0) if value else 0.0
        return value

    def unit_node(self, resource_id):
        """Graph junction a unit is currently at or nearest to."""
        position = self.movement[resource_id]["position"]
        return self.graph.nearest_node([position["longitude"], position["latitude"]])

    def incident_types(self, worksite):
        return worksite.get("metadata", {}).get("incident_types", [])

    def minutes_since_report(self, worksite):
        return max(0.0, (self.scenario_time - parse_time(worksite["created_at"])).total_seconds() / 60.0)

    # -- routing -----------------------------------------------------------

    def build_route(self, resource_id, worksite_id):
        """Shortest passable route from the unit's current position to the worksite.

        Returns None when damage has severed every approach; the caller then reports
        the worksite as unreachable rather than inventing a path through a blockage.
        """
        worksite = self.worksites[worksite_id]
        # Snap both ends onto the road network rather than onto the nearest junction.
        # A unit part-way along a long edge is up to ~60 m from its nearest junction
        # here, and rebuilding the route from that junction makes the drawn path jump
        # away from the icon. The building end gains the same accuracy: most buildings
        # sit along a street, not at a corner.
        position = self.movement[resource_id]["position"]
        target = worksite["metadata"]["position"]
        found = self.graph.route_points(
            [position["longitude"], position["latitude"]],
            [target["longitude"], target["latitude"]])
        if found is None:
            return None
        path = found["path"]
        if len(path) < 2:
            path = [path[0], path[0]] if path else None
            if path is None:
                return None
        speed = self.speed_of(self.resources[resource_id]) or 1.0
        seconds = found["distance_m"] / speed
        return {
            "schema_version": "2.1",
            "route_id": f"route-{resource_id}-{worksite_id}-{self.state_version}",
            "resource_id": resource_id,
            "worksite_id": worksite_id,
            "route_type": "road",
            "path": [{"latitude": point[1], "longitude": point[0]} for point in path],
            "road_edge_ids": found["edge_ids"],
            "total_distance_m": found["distance_m"],
            "estimated_travel_time_s": seconds,
            "route_status": "planned",
            "generated_from_state_version": self.state_version,
            "created_at": format_time(self.scenario_time),
            "start_position": {"latitude": path[0][1], "longitude": path[0][0]},
            "end_position": {"latitude": path[-1][1], "longitude": path[-1][0]},
        }

    def route_is_valid(self, route):
        """A route dies the moment any edge it uses is reported blocked."""
        return all(not self.graph.edges[edge_id].damage_blocked
                   for edge_id in route.get("road_edge_ids", [])
                   if edge_id in self.graph.edges)

    # -- Save planning -----------------------------------------------------

    def eligible_worksites(self):
        """Evidence gate from section 19: weak evidence is a Search problem."""
        return [w for w in self.worksites.values()
                if w["evidence_status"] in planner.ELIGIBLE_EVIDENCE
                and w["operational_status"] != "resolved"]

    def score_worksite(self, worksite):
        return planner.save_priority(worksite, self.incident_types(worksite),
                                     self.minutes_since_report(worksite))

    def evaluate(self, resource_id, worksite_id):
        """Full AssignmentUtility breakdown for one (unit, worksite) pair."""
        worksite = self.worksites[worksite_id]
        route = self.build_route(resource_id, worksite_id)
        if route is None:
            return None
        priority, breakdown = self.score_worksite(worksite)
        types = self.incident_types(worksite)
        cost = planner.travel_cost(route["estimated_travel_time_s"])
        risk = planner.responder_risk(worksite, types)
        mismatch = planner.capability_mismatch(self.resources[resource_id], worksite, types)
        return {
            "resource_id": resource_id,
            "worksite_id": worksite_id,
            "save_priority": priority,
            "score_breakdown": breakdown,
            "travel_cost": cost,
            "responder_risk": risk,
            "capability_mismatch": mismatch,
            "assignment_utility": planner.assignment_utility(priority, cost, risk, mismatch),
            "route": route,
        }

    def replan(self, reason, depth=0, quiet=False):
        """Recompute Save decisions and assign idle units, greedily by utility.

        Greedy assignment is what the specification calls for in the MVP: take the
        highest-utility (unit, worksite) pair, commit it, and repeat without that
        unit or worksite.

        `quiet` suppresses the trace entry when the pass changed nothing, so the
        periodic re-score does not bury the commander's log in noise.
        """
        self.bump()
        self._cut_off = False
        self._changed = False
        candidates = []
        for worksite in sorted(self.eligible_worksites(), key=lambda w: w["worksite_id"]):
            priority, breakdown = self.score_worksite(worksite)
            decision = {
                "schema_version": "2.1",
                "decision_id": f"save-{worksite['worksite_id']}-{self.state_version}",
                "state_version": self.state_version,
                "worksite_id": worksite["worksite_id"],
                "save_priority": priority,
                "score_breakdown": breakdown,
                "candidate_assignments": [],
                "recommended_resource_id": None,
                "recommended_task": self.task_for(worksite),
                "reasons": self.reasons_for(worksite, priority, breakdown),
                "created_at": format_time(self.scenario_time),
            }
            worksite["save_priority"] = priority
            for resource_id in sorted(self.resources):
                evaluation = self.evaluate(resource_id, worksite["worksite_id"])
                if evaluation is None:
                    continue
                decision["candidate_assignments"].append({
                    "resource_id": resource_id,
                    "assignment_utility": evaluation["assignment_utility"],
                    "travel_cost": evaluation["travel_cost"],
                    "responder_risk": evaluation["responder_risk"],
                    "capability_mismatch": evaluation["capability_mismatch"],
                })
                candidates.append(evaluation)
            if not decision["candidate_assignments"]:
                decision["reasons"].append("No passable route from any rescue unit; roads are blocked.")
                decision["recommended_task"] = "clear_route"
                worksite["operational_status"] = "pending"
            best = max(decision["candidate_assignments"],
                       key=lambda c: c["assignment_utility"], default=None)
            if best:
                decision["recommended_resource_id"] = best["resource_id"]
            self.save_decisions[worksite["worksite_id"]] = decision

        free_units = {r["resource_id"] for r in self.resources.values() if r["status"] == "available"}
        taken_worksites = {r["current_worksite_id"] for r in self.resources.values() if r["current_worksite_id"]}
        for evaluation in sorted(candidates, key=lambda c: (-c["assignment_utility"], c["resource_id"])):
            if evaluation["resource_id"] not in free_units:
                continue
            if evaluation["worksite_id"] in taken_worksites:
                continue
            self.commit(evaluation, "initial assignment")
            free_units.discard(evaluation["resource_id"])
            taken_worksites.add(evaluation["worksite_id"])

        # Eligible but unserved worksites are queued, not unassessed. With three units
        # and more eligible worksites than units, this queue is the scarcity the
        # commander is being asked to reason about.
        for worksite in self.eligible_worksites():
            if not worksite["assigned_resource_ids"] and worksite["operational_status"] in {"unassessed", "assigned"}:
                worksite["operational_status"] = "pending"

        self.revalidate_routes()
        self._consider_reassignment(candidates)
        if self._changed or not quiet:
            self.log("Save planner recomputed", reason)
        if self._cut_off and depth == 0:
            # A unit was just released from an unreachable objective; give it work.
            self.replan("re-tasking a cut-off unit", depth + 1)

    def task_for(self, worksite):
        types = set(self.incident_types(worksite))
        if "trapped_victims" in types or "structural_damage" in types:
            return "rescue_worksite"
        if "evacuation_need" in types:
            return "evacuate_civilians"
        return "monitor_worksite"

    def reasons_for(self, worksite, priority, breakdown):
        reasons = [f"SavePriority {priority:.2f} from "
                   f"V {breakdown['victim_impact']:.2f}, T {breakdown['time_sensitivity']:.2f}, "
                   f"H {breakdown['hazard_severity']:.2f}, C {breakdown['criticality']:.2f}."]
        reasons.append(f"Evidence status is {worksite['evidence_status']}.")
        return reasons

    def commit(self, evaluation, why):
        """Attach a unit to a worksite objective and activate its route."""
        resource = self.resources[evaluation["resource_id"]]
        worksite = self.worksites[evaluation["worksite_id"]]
        assignment_id = f"assignment-{resource['resource_id']}-{worksite['worksite_id']}-{self.state_version}"
        route = evaluation["route"]
        route["route_status"] = "active"
        self.retire_routes(resource["resource_id"])
        self.routes[route["route_id"]] = route
        self.assignments[assignment_id] = {
            "schema_version": "2.1",
            "assignment_id": assignment_id,
            "resource_id": resource["resource_id"],
            "worksite_id": worksite["worksite_id"],
            "task_type": self.task_for(worksite),
            "status": "active",
            "route_id": route["route_id"],
            "source_decision_id": self.save_decisions.get(worksite["worksite_id"], {}).get("decision_id"),
            "assigned_at": format_time(self.scenario_time),
            "started_at": format_time(self.scenario_time),
            "completed_at": None,
        }
        resource["current_assignment_id"] = assignment_id
        resource["current_worksite_id"] = worksite["worksite_id"]
        resource["status"] = "en_route"
        worksite["operational_status"] = "assigned"
        self._changed = True
        worksite["assigned_resource_ids"] = [resource["resource_id"]]
        self.start_movement(resource["resource_id"], route)
        self.log(f"{resource['display_name']} assigned to {worksite['metadata']['sector']}",
                 f"{why}; utility {evaluation['assignment_utility']:.2f}, "
                 f"ETA {route['estimated_travel_time_s'] / 60:.1f} min")

    def retire_routes(self, resource_id):
        for route in self.routes.values():
            if route["resource_id"] == resource_id and route["route_status"] == "active":
                route["route_status"] = "superseded"

    # -- movement ----------------------------------------------------------

    def start_movement(self, resource_id, route):
        state = self.movement[resource_id]
        state.update({
            "movement_mode": "ground_route",
            "route_id": route["route_id"],
            "position": dict(route["path"][0]),
            "segment_index": 0,
            "segment_progress": 0.0,
            "route_progress": 0.0,
            "speed_mps": self.speed_of(self.resources[resource_id]),
            "started_at": format_time(self.scenario_time),
            "distance_traveled_m": 0.0,
            "distance_remaining_m": route["total_distance_m"],
            "estimated_arrival_at": format_time(
                self.scenario_time + timedelta(seconds=route["estimated_travel_time_s"])),
        })

    def tick(self, elapsed_wall_s):
        """Advance simulated time and move every unit. Never calls the planner directly."""
        if not self.running:
            return
        step = elapsed_wall_s * self.sim_speed
        self.scenario_time += timedelta(seconds=step)
        arrived, finished = [], []
        for resource_id, state in self.movement.items():
            if state["movement_mode"] != "ground_route":
                continue
            route = self.routes.get(state["route_id"])
            if route is None:
                continue
            if self.advance(resource_id, state, route, step * state["speed_mps"]):
                arrived.append(resource_id)
            state["last_updated_at"] = format_time(self.scenario_time)
        for resource_id in arrived:
            self.on_arrival(resource_id)
        for resource_id, until in list(self._work_until.items()):
            if self.scenario_time >= until:
                finished.append(resource_id)
        for resource_id in finished:
            self.on_work_complete(resource_id)
        self.expire_proposal()
        # Time sensitivity rises while a worksite waits, so priorities are re-scored
        # on a coarse timer. A pass that changes no assignment stays out of the trace.
        self._since_rescore += step
        if self._since_rescore >= RESCORE_INTERVAL_S:
            self._since_rescore = 0.0
            self.replan("priorities re-scored as worksites wait", quiet=True)

    def advance(self, resource_id, state, route, distance_m):
        """Walk a unit `distance_m` along its route polyline. True when it arrives."""
        path = route["path"]
        index = state["segment_index"] or 0
        progress = state["segment_progress"] or 0.0
        remaining = distance_m
        while index < len(path) - 1 and remaining > 0:
            start, end = path[index], path[index + 1]
            length = haversine([start["longitude"], start["latitude"]],
                               [end["longitude"], end["latitude"]])
            if length <= 0:
                index += 1
                progress = 0.0
                continue
            travelled = progress * length
            if travelled + remaining >= length:
                remaining -= (length - travelled)
                index += 1
                progress = 0.0
            else:
                progress = (travelled + remaining) / length
                remaining = 0.0
        if index >= len(path) - 1:
            state.update({"position": dict(path[-1]), "segment_index": len(path) - 2,
                          "segment_progress": 1.0, "route_progress": 1.0,
                          "distance_traveled_m": route["total_distance_m"],
                          "distance_remaining_m": 0.0})
            return True
        start, end = path[index], path[index + 1]
        state["position"] = {
            "latitude": start["latitude"] + (end["latitude"] - start["latitude"]) * progress,
            "longitude": start["longitude"] + (end["longitude"] - start["longitude"]) * progress,
        }
        state["segment_index"] = index
        state["segment_progress"] = progress
        state["heading_deg"] = bearing(start, end)
        covered = self.covered_distance(path, index, progress)
        state["distance_traveled_m"] = covered
        state["distance_remaining_m"] = max(0.0, route["total_distance_m"] - covered)
        state["route_progress"] = min(1.0, covered / route["total_distance_m"]) if route["total_distance_m"] else 1.0
        state["estimated_arrival_at"] = format_time(self.scenario_time + timedelta(
            seconds=state["distance_remaining_m"] / (state["speed_mps"] or 1.0)))
        return False

    @staticmethod
    def covered_distance(path, index, progress):
        total = 0.0
        for position in range(index):
            total += haversine([path[position]["longitude"], path[position]["latitude"]],
                               [path[position + 1]["longitude"], path[position + 1]["latitude"]])
        if index < len(path) - 1:
            total += progress * haversine(
                [path[index]["longitude"], path[index]["latitude"]],
                [path[index + 1]["longitude"], path[index + 1]["latitude"]])
        return total

    def on_arrival(self, resource_id):
        resource = self.resources[resource_id]
        worksite = self.worksites.get(resource["current_worksite_id"])
        state = self.movement[resource_id]
        state.update({"movement_mode": "stationary", "speed_mps": 0.0, "route_progress": 1.0})
        route = self.routes.get(state["route_id"])
        if route:
            route["route_status"] = "completed"
        resource["status"] = "working"
        if worksite:
            worksite["operational_status"] = "in_progress"
            self._work_until[resource_id] = self.scenario_time + timedelta(seconds=WORK_DURATION_S)
            self.bump()
            self.log(f"{resource['display_name']} on site at {worksite['metadata']['sector']}",
                     f"Approach on foot {worksite['metadata']['access_distance_m']:.0f} m from the road.")

    def on_work_complete(self, resource_id):
        resource = self.resources[resource_id]
        worksite = self.worksites.get(resource["current_worksite_id"])
        self._work_until.pop(resource_id, None)
        if worksite:
            worksite["operational_status"] = "resolved"
            worksite["assigned_resource_ids"] = []
        assignment = self.assignments.get(resource["current_assignment_id"])
        if assignment:
            assignment["status"] = "completed"
            assignment["completed_at"] = format_time(self.scenario_time)
        resource.update({"status": "available", "current_assignment_id": None, "current_worksite_id": None})
        self.log(f"{resource['display_name']} completed "
                 f"{worksite['metadata']['sector'] if worksite else 'task'}", "Unit is available again.")
        self.replan("task completed")

    # -- reactions to new evidence ----------------------------------------

    # Fields the information layer may revise. Save consumes belief; it never
    # invents or rewrites evidence of its own (specification section 2).
    EVIDENCE_FIELDS = {"evidence_status", "victim_indication", "best_estimate",
                       "structural_severity", "access_status", "criticality",
                       "incident_types"}

    def update_worksite(self, worksite_id, patch, source="fusion"):
        """Apply revised belief about one worksite, then re-plan.

        This is the hook for the fusion layer: a corroborating radio report or a
        drone verification raises SavePriority here, which can promote a worksite
        past the evidence gate or trigger a ReassignmentProposal. Save does not
        decide what is true; it reacts to what the information layer concluded.
        """
        worksite = self.worksites.get(worksite_id)
        if worksite is None:
            raise KeyError(f"Unknown worksite: {worksite_id}")
        unknown = set(patch) - self.EVIDENCE_FIELDS
        if unknown:
            raise ValueError(f"Not an evidence field: {', '.join(sorted(unknown))}")

        before = worksite["evidence_status"]
        if "evidence_status" in patch:
            worksite["evidence_status"] = patch["evidence_status"]
        if "victim_indication" in patch:
            worksite["victim_summary"]["indication"] = {"type": patch["victim_indication"]}
        if "best_estimate" in patch:
            worksite["victim_summary"]["best_estimate"] = patch["best_estimate"]
        if "structural_severity" in patch:
            worksite["structural_summary"]["severity"] = patch["structural_severity"]
        if "access_status" in patch:
            worksite["access_summary"]["status"] = patch["access_status"]
        if "criticality" in patch:
            worksite["criticality"] = planner.clamp(float(patch["criticality"]))
        if "incident_types" in patch:
            worksite["metadata"]["incident_types"] = list(patch["incident_types"])
        worksite["updated_at"] = format_time(self.scenario_time)

        self.bump()
        sector = worksite["metadata"]["sector"]
        detail = ", ".join(f"{k} -> {patch[k]}" for k in sorted(patch))
        self.log(f"New evidence at {sector}", f"{detail} ({source})")
        if before not in planner.ELIGIBLE_EVIDENCE and \
                worksite["evidence_status"] in planner.ELIGIBLE_EVIDENCE:
            self.log(f"{sector} passed the evidence gate",
                     f"{before} -> {worksite['evidence_status']}; now eligible for Save planning.")
        self.replan("new evidence")
        return worksite

    # -- reactions to road changes ----------------------------------------

    def set_road_blocked(self, edge_id, blocked, source="commander"):
        """The whole point of the module: a road changes and everything reacts."""
        if not self.graph.set_blocked(edge_id, blocked):
            return False
        edge = self.graph.edges[edge_id]
        self.bump()
        self.log(f"Road {'blocked' if blocked else 'reopened'}: {edge.name}",
                 f"{edge_id} ({source})")
        self.replan("road state changed")
        return True

    def revalidate_routes(self):
        """Reroute, automatically, any unit whose path now crosses a blocked edge.

        The objective does not change here, so no human approval is required
        (section 21). A unit that is genuinely cut off stops and is reported.
        """
        for resource in self.resources.values():
            if resource["status"] != "en_route" or not resource["current_worksite_id"]:
                continue
            state = self.movement[resource["resource_id"]]
            route = self.routes.get(state["route_id"])
            if route is None or self.route_is_valid(route):
                continue
            replacement = self.build_route(resource["resource_id"], resource["current_worksite_id"])
            worksite = self.worksites[resource["current_worksite_id"]]
            if replacement is None:
                # Every approach is severed. Holding a crew at a roadblock helps nobody,
                # so the objective is released and the unit re-enters planning. The
                # worksite stays eligible and now needs a clear_route task first.
                route["route_status"] = "cancelled"
                state.update({"movement_mode": "stationary", "speed_mps": 0.0, "route_id": None})
                assignment = self.assignments.get(resource["current_assignment_id"])
                if assignment:
                    assignment["status"] = "cancelled"
                worksite["operational_status"] = "pending"
                worksite["assigned_resource_ids"] = []
                resource.update({"status": "available", "current_assignment_id": None,
                                 "current_worksite_id": None})
                self._cut_off = True
                self._changed = True
                self.log(f"{resource['display_name']} is cut off from {worksite['metadata']['sector']}",
                         "Every approach is blocked; unit released for re-tasking and "
                         f"{worksite['metadata']['sector']} needs a clear-route task.")
                continue
            route["route_status"] = "superseded"
            replacement["route_status"] = "active"
            self.routes[replacement["route_id"]] = replacement
            assignment = self.assignments.get(resource["current_assignment_id"])
            if assignment:
                assignment["route_id"] = replacement["route_id"]
            self.start_movement(resource["resource_id"], replacement)
            self._changed = True
            self.log(f"{resource['display_name']} rerouted to {worksite['metadata']['sector']}",
                     f"Blocked road avoided; new ETA {replacement['estimated_travel_time_s'] / 60:.1f} min. "
                     "Objective unchanged, so no approval was required.")

    # -- human reassignment gate ------------------------------------------

    def _consider_reassignment(self, candidates):
        """Propose an objective change only when the gain clears the threshold."""
        if self.proposal and self.proposal["status"] == "pending":
            return
        best = None
        for evaluation in candidates:
            resource = self.resources[evaluation["resource_id"]]
            current_id = resource["current_worksite_id"]
            if resource["status"] != "en_route" or not current_id:
                continue
            if evaluation["worksite_id"] == current_id:
                continue
            if self.worksites[evaluation["worksite_id"]]["assigned_resource_ids"]:
                continue
            current = self.evaluate(resource["resource_id"], current_id)
            if current is None:
                continue
            progress = self.movement[resource["resource_id"]]["route_progress"]
            extra = planner.clamp(
                (evaluation["route"]["estimated_travel_time_s"] - current["route"]["estimated_travel_time_s"])
                / planner.TRAVEL_HORIZON_S)
            gain = planner.reassignment_gain(evaluation["assignment_utility"],
                                             current["assignment_utility"], progress, extra)
            if gain > planner.REASSIGNMENT_THRESHOLD and (best is None or gain > best["gain"]):
                best = {"gain": gain, "evaluation": evaluation, "current": current,
                        "progress_loss": progress, "additional_travel": extra}
        if best is None:
            return
        evaluation = best["evaluation"]
        resource = self.resources[evaluation["resource_id"]]
        self.proposal = {
            "schema_version": "2.1",
            "proposal_id": f"reassignment-{resource['resource_id']}-{self.state_version}",
            "resource_id": resource["resource_id"],
            "resource_name": resource["display_name"],
            "current_worksite_id": resource["current_worksite_id"],
            "current_sector": self.worksites[resource["current_worksite_id"]]["metadata"]["sector"],
            "proposed_worksite_id": evaluation["worksite_id"],
            "proposed_sector": self.worksites[evaluation["worksite_id"]]["metadata"]["sector"],
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
        self._changed = True
        self.log(f"Reassignment proposed for {resource['display_name']}",
                 f"{self.proposal['current_sector']} -> {self.proposal['proposed_sector']}, "
                 f"gain +{best['gain']:.2f} over threshold +{planner.REASSIGNMENT_THRESHOLD:.2f}")

    def decide_proposal(self, approve):
        """Apply the commander's answer. Movement never paused while they decided."""
        if not self.proposal or self.proposal["status"] != "pending":
            return False
        proposal = self.proposal
        if not approve:
            proposal["status"] = "rejected"
            self.bump()
            self.log(f"Reassignment rejected for {proposal['resource_name']}",
                     f"{proposal['resource_name']} keeps {proposal['current_sector']}.")
            return True
        # Route from where the unit is NOW, not from where it was when proposed.
        evaluation = self.evaluate(proposal["resource_id"], proposal["proposed_worksite_id"])
        if evaluation is None:
            proposal["status"] = "invalidated"
            self.bump()
            self.log(f"Reassignment invalidated for {proposal['resource_name']}",
                     "No passable route to the proposed worksite from the current position.")
            return True
        previous = self.worksites.get(proposal["current_worksite_id"])
        if previous:
            previous["operational_status"] = "pending"
            previous["assigned_resource_ids"] = []
        proposal["status"] = "approved"
        self.bump()
        self.commit(evaluation, "human-approved reassignment")
        self.log(f"Reassignment approved for {proposal['resource_name']}",
                 f"Rerouted from its current position to {proposal['proposed_sector']}.")
        return True

    def expire_proposal(self):
        """No answer means keep the current assignment (section 23)."""
        if not self.proposal or self.proposal["status"] != "pending":
            return
        if time.time() >= self.proposal["expires_at_wall"]:
            self.proposal["status"] = "expired"
            self.bump()
            self.log(f"Reassignment expired for {self.proposal['resource_name']}",
                     "No response in 30 s; the existing assignment stands.")

    # -- serialisation -----------------------------------------------------

    def snapshot(self):
        """Small payload pushed to the frontend on every tick."""
        active_routes = [r for r in self.routes.values() if r["route_status"] == "active"]
        worksites = []
        for worksite in sorted(self.worksites.values(), key=lambda w: w["metadata"]["sector"]):
            decision = self.save_decisions.get(worksite["worksite_id"])
            worksites.append({
                "worksite_id": worksite["worksite_id"],
                "site_id": worksite["site_id"],
                "sector": worksite["metadata"]["sector"],
                "display_name": worksite["display_name"],
                "site_display_name": worksite["metadata"].get("site_display_name"),
                "position": worksite["metadata"]["position"],
                "evidence_status": worksite["evidence_status"],
                "operational_status": worksite["operational_status"],
                "incident_types": worksite["metadata"]["incident_types"],
                "victim_summary": worksite["victim_summary"],
                "structural_summary": worksite["structural_summary"],
                "access_summary": worksite["access_summary"],
                "criticality": worksite["criticality"],
                "eligible": worksite["evidence_status"] in planner.ELIGIBLE_EVIDENCE,
                "save_priority": decision["save_priority"] if decision else None,
                "score_breakdown": decision["score_breakdown"] if decision else None,
                "candidate_assignments": decision["candidate_assignments"] if decision else [],
                "recommended_resource_id": decision["recommended_resource_id"] if decision else None,
                "reasons": decision["reasons"] if decision else [],
                "assigned_resource_ids": worksite["assigned_resource_ids"],
                "simulated": True,
            })
        resources = []
        for resource in sorted(self.resources.values(), key=lambda r: r["display_name"]):
            state = self.movement[resource["resource_id"]]
            resources.append({
                "resource_id": resource["resource_id"],
                "display_name": resource["display_name"],
                "status": resource["status"],
                "capability_tags": resource["capability_tags"],
                "current_worksite_id": resource["current_worksite_id"],
                "position": state["position"],
                "heading_deg": state["heading_deg"],
                "movement_mode": state["movement_mode"],
                "route_progress": state["route_progress"],
                "route_id": state["route_id"],
                "distance_remaining_m": state["distance_remaining_m"],
                "eta_s": (state["distance_remaining_m"] / state["speed_mps"]) if state["speed_mps"] else None,
            })
        proposal = None
        if self.proposal:
            proposal = dict(self.proposal)
            proposal["seconds_left"] = max(0.0, proposal["expires_at_wall"] - time.time())
        return {
            "state_version": self.state_version,
            "scenario": self.scenario_name,
            "scenario_time": format_time(self.scenario_time),
            "elapsed_s": (self.scenario_time - self.started_at).total_seconds(),
            "sim_speed": self.sim_speed,
            "running": self.running,
            "worksites": worksites,
            "resources": resources,
            "routes": [{"route_id": r["route_id"], "resource_id": r["resource_id"],
                        "worksite_id": r["worksite_id"], "path": r["path"],
                        "road_edge_ids": r["road_edge_ids"],
                        "estimated_travel_time_s": r["estimated_travel_time_s"],
                        "total_distance_m": r["total_distance_m"]} for r in active_routes],
            "blocked_edge_ids": self.graph.blocked_edge_ids(),
            "proposal": proposal,
            "trace": self.trace,
        }


def bearing(start, end):
    """Compass bearing in degrees, used to point the unit icon along the street."""
    lat1, lat2 = math.radians(start["latitude"]), math.radians(end["latitude"])
    delta = math.radians(end["longitude"] - start["longitude"])
    y = math.sin(delta) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
