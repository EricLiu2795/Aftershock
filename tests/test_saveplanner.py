"""Save layer against the frozen Caraballeda Scenario Pack.

Covers the road-condition semantics (open/limited/blocked), damage-aware routing,
the E07 -> E10 demo path, the human reassignment gate with approval-time
revalidation, and contract compliance of everything the layer emits.
"""
import json
from pathlib import Path
import time
import unittest

from ingestion.validation import validator
from saveplanner import planner
from saveplanner.beliefadapter import belief_from_pack
from saveplanner.controller import (
    MIN_RESCUE_SECONDS,
    RESCUE_SECONDS_PER_VICTIM,
    SaveController,
)
from saveplanner.execution import ScenarioExecutionIndex
from saveplanner.roadgraph import Edge, RoadGraph, haversine, status_from_labels

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data" / "scenarios" / "caraballeda"
GRAPH = ROOT / "data" / "graph_exports" / "caraballeda.json"

# Identities frozen by the pack; if these drift the scenario changed, not the planner.
R1 = "osm:way:376529129:segment:1-12"
W1_SITE = "osm:way:1533952351"
W1_ARRIVAL = "osm:node:925977824"
STAGING = "osm:node:925978252"


def edge(edge_id, source, target, path, tags=None, labels=None):
    length = sum(haversine(a, b) for a, b in zip(path, path[1:]))
    return Edge({"edge_id": edge_id, "road_id": "road-" + edge_id, "source": source,
                 "target": target, "length_m": length, "path": path,
                 "osm_tags": tags or {"highway": "residential"}, "labels": labels})


def grid():
    """A --e1-- B --e2-- D  (short)   /   A --e3-- C --e4-- D  (long)"""
    nodes = {"A": [-66.860, 10.600], "B": [-66.858, 10.600],
             "C": [-66.859, 10.596], "D": [-66.856, 10.600]}
    return RoadGraph(nodes, [
        edge("e1", "A", "B", [nodes["A"], nodes["B"]]),
        edge("e2", "B", "D", [nodes["B"], nodes["D"]]),
        edge("e3", "A", "C", [nodes["A"], nodes["C"]]),
        edge("e4", "C", "D", [nodes["C"], nodes["D"]]),
    ])


class RoadConditionTests(unittest.TestCase):
    """OPEN / LIMITED / BLOCKED, per the frozen road_state contract."""

    def setUp(self):
        self.graph = grid()

    def test_status_reader_keeps_all_three_values(self):
        for status in ("open", "limited", "blocked"):
            self.assertEqual(status_from_labels({"reported_status": status}), status)

    def test_unassessed_uses_the_scenario_baseline(self):
        # baseline_road_policy.assume_open_unless_overridden
        self.assertEqual(status_from_labels({"reported_status": None}), "open")
        self.assertEqual(status_from_labels(None), "open")

    def test_limited_stays_traversable_but_costs_more(self):
        direct = self.graph.route("A", "D")
        self.assertEqual(direct["edge_ids"], ["e1", "e2"])
        base = direct["seconds"]
        self.graph.set_road_status("e2", "limited", 1.2)
        slowed = self.graph.route("A", "D")
        self.assertEqual(slowed["edge_ids"], ["e1", "e2"], "LIMITED must not remove the edge")
        self.assertGreater(slowed["seconds"], base)

    def test_a_heavy_enough_limit_diverts_without_removing_the_road(self):
        self.graph.set_road_status("e2", "limited", 50.0)
        diverted = self.graph.route("A", "D")
        self.assertNotIn("e2", diverted["edge_ids"])
        self.assertEqual(self.graph.edges["e2"].status, "limited")
        self.assertTrue(self.graph.passable(self.graph.edges["e2"]))

    def test_blocked_removes_the_edge_entirely(self):
        self.graph.set_road_status("e2", "blocked")
        detour = self.graph.route("A", "D")
        self.assertEqual(detour["edge_ids"], ["e3", "e4"])
        self.assertFalse(self.graph.passable(self.graph.edges["e2"]))

    def test_no_multiplier_can_buy_passage_through_blocked(self):
        self.graph.set_road_status("e2", "blocked", 0.0001)
        self.graph.set_road_status("e4", "blocked")
        self.assertIsNone(self.graph.route("A", "D"))

    def test_rejects_a_status_outside_the_contract(self):
        with self.assertRaises(ValueError):
            self.graph.set_road_status("e1", "damaged")

    def test_apply_road_states_accepts_way_level_reports(self):
        changed = self.graph.apply_road_states(
            [{"road_id": "road-e1", "status": "limited", "travel_cost_multiplier": 3.0}])
        self.assertEqual(changed, ["e1"])
        self.assertEqual(self.graph.edges["e1"].cost_multiplier, 3.0)


class OffGraphEndpointTests(unittest.TestCase):
    """Buildings and live unit positions are not graph vertices."""

    def setUp(self):
        self.graph = grid()

    def test_snaps_to_the_nearest_edge_not_the_nearest_junction(self):
        beside = [-66.859, 10.6005]
        found_edge, fraction, _, distance = self.graph.snap(beside)
        self.assertEqual(found_edge.edge_id, "e1")
        self.assertTrue(0.0 < fraction < 1.0)
        self.assertLess(distance,
                        haversine(beside, self.graph.nodes[self.graph.nearest_node(beside)]))

    def test_routes_from_a_live_position_to_a_frozen_node(self):
        found = self.graph.route_to_node([-66.8595, 10.6002], "D")
        self.assertIsNotNone(found)
        self.assertEqual(len(found["edge_ids"]), len(set(found["edge_ids"])))

    def test_snapping_ignores_blocked_roads(self):
        self.graph.set_road_status("e1", "blocked")
        found_edge, _, _, _ = self.graph.snap([-66.859, 10.6005])
        self.assertNotEqual(found_edge.edge_id, "e1")


class ScoringTests(unittest.TestCase):
    def test_save_priority_matches_the_specification_weights(self):
        worksite = {"victim_summary": {"indication": {"type": "confirmed_multiple"},
                                       "best_estimate": 7},
                    "structural_summary": {"severity": "severe"},
                    "metadata": {"search_features": {"criticality": 0.78}}}
        score, breakdown = planner.save_priority(worksite, ["trapped_victims"], 0.0)
        self.assertAlmostEqual(score, 0.40 * breakdown["victim_impact"]
                               + 0.25 * breakdown["time_sensitivity"]
                               + 0.20 * breakdown["hazard_severity"]
                               + 0.15 * breakdown["criticality"])

    def test_criticality_comes_from_the_scenario_overlay_not_osm(self):
        worksite = {"criticality": 0, "metadata": {
            "scenario_operational_overlay": {"scenario_criticality": 0.78}}}
        self.assertAlmostEqual(planner.criticality(worksite), 0.78)

    def test_osm_criticality_alone_scores_zero(self):
        self.assertEqual(planner.criticality({"criticality": 0, "metadata": {}}), 0.0)

    def test_generic_rescue_teams_have_no_capability_mismatch(self):
        team = {"capability_tags": ["ground_rescue"]}
        self.assertEqual(planner.capability_mismatch(team, {}, ["trapped_victims"]), 0.0)

    def test_reassignment_gain_subtracts_switch_cost(self):
        gain = planner.reassignment_gain(0.9, 0.6, progress_loss=0.4, additional_travel=0.2)
        self.assertAlmostEqual(gain, 0.9 - 0.6 - (0.5 * 0.4 + 0.5 * 0.2))


@unittest.skipUnless(GRAPH.is_file() and PACK.is_dir(), "scenario pack or graph missing")
class ScenarioTests(unittest.TestCase):
    """The frozen W1-W8 scenario and the E07 -> E10 demo path."""

    @classmethod
    def setUpClass(cls):
        cls.index = ScenarioExecutionIndex.load()

    def fresh(self, event="E07"):
        graph = RoadGraph.load(self.index.graph_path)
        controller = SaveController(graph, self.index)
        belief = belief_from_pack(self.index, event)
        controller.plan(belief, event)
        return controller, belief, graph

    # -- scenario identity --------------------------------------------------

    def test_uses_the_frozen_arrival_nodes(self):
        self.assertEqual(self.index.arrival_node(W1_SITE), W1_ARRIVAL)
        for key, record in self.index.by_key.items():
            self.assertIsNotNone(record["arrival_node_id"], key)

    def test_uses_the_scenario_resources_and_staging(self):
        self.assertEqual(self.index.staging_node_id, STAGING)
        teams = [r for r in self.index.resources.values() if r["resource_type"] == "rescue_unit"]
        self.assertEqual({t["display_name"] for t in teams},
                         {"Rescue Team Alpha", "Rescue Team Bravo", "Rescue Team Charlie"})
        for team in teams:
            self.assertEqual(team["capability_tags"], ["ground_rescue"])
            self.assertEqual(team["speed"], {"value": 25, "unit": "km_per_h"})

    def test_reproduces_the_packs_baseline_distance_to_w1(self):
        graph = RoadGraph.load(self.index.graph_path)
        found = graph.route(STAGING, W1_ARRIVAL)
        expected = self.index.by_key["W1"]["baseline_distance_m"]
        self.assertAlmostEqual(found["distance_m"], expected, delta=25)

    # -- E07 ----------------------------------------------------------------

    def test_e07_makes_w1_save_eligible_and_top_priority(self):
        controller, belief, _ = self.fresh("E07")
        w1 = next(w for w in belief["worksites"] if w["site_id"] == W1_SITE)
        decision = controller.decisions.get(w1["worksite_id"])
        self.assertIsNotNone(decision, "W1 must pass the evidence gate after E07")
        ranked = sorted(controller.decisions.values(), key=lambda d: -d["save_priority"])
        self.assertEqual(ranked[0]["worksite_id"], w1["worksite_id"])
        self.assertEqual(decision["recommended_task"], "rescue_worksite")

    def test_e07_assigns_a_team_from_staging_to_w1(self):
        controller, belief, _ = self.fresh("E07")
        w1 = next(w for w in belief["worksites"] if w["site_id"] == W1_SITE)
        assignment = next((a for a in controller.assignments.values()
                           if a["status"] == "active" and a["worksite_id"] == w1["worksite_id"]), None)
        self.assertIsNotNone(assignment, "W1 must receive a rescue team")
        route = controller.routes[assignment["route_id"]]
        self.assertEqual(route["route_type"], "road")
        road_only = route["total_distance_m"] - self.approach_for(W1_SITE)
        self.assertAlmostEqual(road_only,
                               self.index.by_key["W1"]["baseline_distance_m"], delta=25)

    # -- on-site rescue and release ----------------------------------------

    def approach_for(self, site_id):
        """Off-road walk from the frozen arrival node to the building.

        Routes now finish at the building, so total_distance_m carries this leg on
        top of the road distance the pack froze. Subtract it to compare like for like.
        """
        record = self.index.arrival[site_id]
        arrival = record["arrival_position"]
        raw = json.loads(Path(self.index.graph_path).read_text(encoding="utf-8"))
        centre = next(s["site"]["centroid"] for s in raw["sites"]
                      if s["site"]["site_id"] == site_id)
        return haversine([arrival["longitude"], arrival["latitude"]],
                         [centre["longitude"], centre["latitude"]])

    def drive_to_site(self, controller, belief, resource_id, limit=5000):
        """Advance until the unit arrives and starts working."""
        for _ in range(limit):
            controller.advance(belief, 1.0)
            if resource_id in controller.rescuing:
                return True
        return False

    def test_arrival_starts_an_on_site_rescue_sized_by_victims(self):
        controller, belief, _ = self.fresh("E07")
        assignment = next(a for a in controller.assignments.values() if a["status"] == "active")
        resource_id = assignment["resource_id"]
        worksite = next(w for w in belief["worksites"]
                        if w["worksite_id"] == assignment["worksite_id"])
        victims = controller.victims_at(worksite)
        self.assertGreater(victims, 0)

        self.assertTrue(self.drive_to_site(controller, belief, resource_id))
        expected = max(MIN_RESCUE_SECONDS, RESCUE_SECONDS_PER_VICTIM * victims)
        self.assertAlmostEqual(controller.rescue_seconds_left(resource_id), expected, delta=1.0)

    def test_a_finished_rescue_completes_the_assignment_and_frees_the_unit(self):
        controller, belief, _ = self.fresh("E07")
        assignment = next(a for a in controller.assignments.values() if a["status"] == "active")
        resource_id, worksite_id = assignment["resource_id"], assignment["worksite_id"]
        self.assertTrue(self.drive_to_site(controller, belief, resource_id))

        controller.rescuing[resource_id] = time.time() - 0.01      # expire it now
        controller.advance(belief, 1.0)

        self.assertEqual(assignment["status"], "completed")
        self.assertIsNotNone(assignment["completed_at"])
        self.assertIn(worksite_id, controller.served)
        self.assertNotIn(resource_id, controller.rescuing)
        self.assertEqual(controller.movement[resource_id]["movement_mode"], "stationary")

    def test_a_served_worksite_is_not_dispatched_to_again(self):
        """Without this the freed unit is re-sent to the site it just finished."""
        controller, belief, _ = self.fresh("E07")
        assignment = next(a for a in controller.assignments.values() if a["status"] == "active")
        resource_id, worksite_id = assignment["resource_id"], assignment["worksite_id"]
        self.assertTrue(self.drive_to_site(controller, belief, resource_id))
        controller.rescuing[resource_id] = time.time() - 0.01
        controller.advance(belief, 1.0)

        self.assertNotIn(worksite_id, [w["worksite_id"] for w in controller.eligible(belief)])
        controller.plan(belief, "after rescue")
        self.assertNotEqual(controller.objective_of(resource_id), worksite_id)

    def test_a_freed_unit_moves_on_to_the_next_eligible_worksite(self):
        """One crew, several verified sites: it should work them one after another."""
        controller, belief, _ = self.fresh("E07")
        # Keep a single rescue team so the queue is unambiguous, and make every
        # worksite with a frozen arrival node pass the evidence gate.
        belief["resources"] = [r for r in belief["resources"]
                               if r["resource_type"] != "rescue_unit"
                               or r["resource_id"] == "rescue-alpha"]
        for worksite in belief["worksites"]:
            if self.index.arrival_node(worksite["site_id"]):
                worksite["evidence_status"] = "verified"
        controller.plan(belief, "single crew")

        available = len(controller.eligible(belief))
        self.assertGreaterEqual(available, 3, "need several sites to show a queue")

        worked = []
        for _ in range(available):
            objective = controller.objective_of("rescue-alpha")
            if objective is None:
                break
            worked.append(objective)
            self.assertTrue(self.drive_to_site(controller, belief, "rescue-alpha"))
            controller.rescuing["rescue-alpha"] = time.time() - 0.01
            controller.advance(belief, 1.0)

        self.assertGreaterEqual(len(worked), 3, f"only worked {worked}")
        self.assertEqual(len(worked), len(set(worked)), "a site was worked twice")
        self.assertEqual(set(worked), set(controller.served))

    def test_victim_count_falls_back_without_an_estimate(self):
        controller, _, _ = self.fresh("E07")
        self.assertEqual(controller.victims_at({}), 1)
        self.assertEqual(controller.victims_at({"victim_summary": {"best_estimate": 0}}), 1)
        self.assertEqual(controller.victims_at({"victim_summary": {"count_min": 5}}), 5)
        self.assertEqual(
            controller.victims_at({"victim_summary": {"best_estimate": 7, "count_min": 2}}), 7)

    def test_rescue_state_survives_a_live_tick_round_trip(self):
        """The live bridge persists execution between ticks; a dwell must not reset."""
        controller, belief, graph = self.fresh("E07")
        resource_id = next(a["resource_id"] for a in controller.assignments.values()
                           if a["status"] == "active")
        self.assertTrue(self.drive_to_site(controller, belief, resource_id))
        controller.served.add("worksite-sentinel")
        exported = controller.export_execution()

        revived = SaveController(RoadGraph.load(self.index.graph_path), self.index)
        revived.restore_execution(exported)
        self.assertIn("worksite-sentinel", revived.served)
        self.assertAlmostEqual(revived.rescue_seconds_left(resource_id),
                               controller.rescue_seconds_left(resource_id), delta=1.0)

    def test_every_route_finishes_at_the_building(self):
        """The arrival node is a road junction 21-145 m from the building depending
        on the site, so a route that stops there parks some crews on the doorstep and
        strands others a street away."""
        controller, belief, _ = self.fresh("E07")
        checked = 0
        for assignment in controller.assignments.values():
            if assignment["status"] != "active":
                continue
            route = controller.routes[assignment["route_id"]]
            worksite = next(w for w in belief["worksites"]
                            if w["worksite_id"] == assignment["worksite_id"])
            site = next(s for s in belief["sites"] if s["site_id"] == worksite["site_id"])
            end, centre = route["path"][-1], site["centroid"]
            gap = haversine([end["longitude"], end["latitude"]],
                            [centre["longitude"], centre["latitude"]])
            self.assertLess(gap, 1.0, f"route ends {gap:.0f} m from the building")
            checked += 1
        self.assertGreater(checked, 0)

    def test_the_walk_in_leg_is_not_claimed_as_road(self):
        """The final approach is off-graph; it must not appear in road_edge_ids."""
        controller, belief, graph = self.fresh("E07")
        assignment = next(a for a in controller.assignments.values() if a["status"] == "active")
        route = controller.routes[assignment["route_id"]]
        for edge_id in route["road_edge_ids"]:
            self.assertIn(edge_id, graph.edges)

    def test_worksites_without_an_arrival_node_are_not_routed_to(self):
        controller, belief, _ = self.fresh("E07")
        for worksite_id in controller.decisions:
            worksite = next(w for w in belief["worksites"] if w["worksite_id"] == worksite_id)
            self.assertIsNotNone(self.index.arrival_node(worksite["site_id"]))

    # -- E10 ----------------------------------------------------------------

    def test_e10_blocks_r1_and_reroutes_without_approval(self):
        controller, belief, graph = self.fresh("E07")
        assignment = next(a for a in controller.assignments.values()
                          if a["status"] == "active"
                          and belief_site(belief, a["worksite_id"]) == W1_SITE)
        resource_id = assignment["resource_id"]
        while controller.movement[resource_id]["distance_traveled_m"] < 373:
            controller.advance(belief, 1.0)
        remaining_before = controller.movement[resource_id]["distance_remaining_m"]

        after = belief_from_pack(self.index, "E10")
        controller.plan(after, "E10")

        self.assertTrue(graph.edges[R1].blocked)
        self.assertFalse(any(R1 in route["road_edge_ids"] for route in controller.active_routes()))
        still = next(a for a in controller.assignments.values()
                     if a["status"] == "active" and a["resource_id"] == resource_id)
        self.assertEqual(belief_site(after, still["worksite_id"]), W1_SITE,
                         "objective must remain W1")
        replacement = controller.routes[still["route_id"]]
        self.assertGreater(replacement["total_distance_m"], remaining_before)
        self.assertIsNone(controller.proposal, "a reroute must not raise a human proposal")

    def test_e10_matches_the_packs_expected_remaining_distances(self):
        expected = self.index.road_disruptions["R1"]["demo"]
        controller, belief, _ = self.fresh("E07")
        assignment = next(a for a in controller.assignments.values()
                          if a["status"] == "active"
                          and belief_site(belief, a["worksite_id"]) == W1_SITE)
        resource_id = assignment["resource_id"]
        while controller.movement[resource_id]["distance_traveled_m"] < \
                expected["reveal_after_distance_traveled_m"]:
            controller.advance(belief, 0.5)
        before = controller.movement[resource_id]["distance_remaining_m"]
        controller.plan(belief_from_pack(self.index, "E10"), "E10")
        after = controller.routes[
            next(a["route_id"] for a in controller.assignments.values()
                 if a["status"] == "active" and a["resource_id"] == resource_id)]
        approach = self.approach_for(W1_SITE)
        self.assertAlmostEqual(before - approach,
                               expected["remaining_m_before_block"], delta=40)
        self.assertAlmostEqual(after["total_distance_m"] - approach,
                               expected["remaining_m_after_block"], delta=40)

    def test_reroute_starts_from_the_live_position_not_a_junction(self):
        """A replacement route begins at the unit's projection onto the passable
        network, never at a junction it has already driven past.

        When the unit is standing on the very road that just became BLOCKED, that
        projection is necessarily the nearest *other* road -- refusing to start a
        route on a blocked edge is the point. So the invariant is that the route
        starts at the snap point, which is far tighter than any fixed tolerance.
        """
        controller, belief, graph = self.fresh("E07")
        resource_id = next(a["resource_id"] for a in controller.assignments.values()
                           if a["status"] == "active")
        for _ in range(60):
            controller.advance(belief, 1.0)
        position = controller.movement[resource_id]["position"]
        point = [position["longitude"], position["latitude"]]
        nearest_junction = haversine(point, graph.nodes[graph.nearest_node(point)])

        controller.plan(belief_from_pack(self.index, "E10"), "E10")
        route = controller.routes[
            next(a["route_id"] for a in controller.assignments.values()
                 if a["status"] == "active" and a["resource_id"] == resource_id)]
        start = route["path"][0]
        gap = haversine(point, [start["longitude"], start["latitude"]])
        _, _, _, snap_distance = graph.snap(point)

        self.assertAlmostEqual(gap, snap_distance, delta=1.0,
                               msg="route must start at the unit's projection onto the road")
        self.assertLess(gap, max(nearest_junction, 1.0) + 1.0,
                        "projecting must never be worse than snapping to a junction")

    # -- human gate ---------------------------------------------------------

    def test_approval_revalidates_an_expired_proposal(self):
        controller, belief, _ = self.fresh("E07")
        controller.proposal = self._proposal(controller, belief, expires_in=-1.0)
        handled, reason = controller.decide_proposal(belief, approve=True)
        self.assertTrue(handled)
        self.assertEqual(controller.proposal["status"], "invalidated")
        self.assertIn("expired", reason)

    def test_approval_revalidates_a_claimed_target(self):
        controller, belief, _ = self.fresh("E07")
        proposal = self._proposal(controller, belief)
        taken = next(a for a in controller.assignments.values()
                     if a["status"] == "active"
                     and a["resource_id"] != proposal["resource_id"])
        proposal["proposed_worksite_id"] = taken["worksite_id"]
        controller.proposal = proposal
        handled, reason = controller.decide_proposal(belief, approve=True)
        self.assertTrue(handled)
        self.assertEqual(controller.proposal["status"], "invalidated")
        self.assertIn("Another unit", reason)

    def test_rejecting_keeps_the_current_objective(self):
        controller, belief, _ = self.fresh("E07")
        proposal = self._proposal(controller, belief)
        controller.proposal = proposal
        before = controller.objective_of(proposal["resource_id"])
        controller.decide_proposal(belief, approve=False)
        self.assertEqual(controller.proposal["status"], "rejected")
        self.assertEqual(controller.objective_of(proposal["resource_id"]), before)

    def _proposal(self, controller, belief, expires_in=30.0):
        """A syntactically valid proposal for revalidation tests."""
        assignment = next(a for a in controller.assignments.values() if a["status"] == "active")
        other = next(w for w in belief["worksites"]
                     if w["worksite_id"] != assignment["worksite_id"]
                     and w["worksite_id"] in controller.decisions)
        return {
            "schema_version": "2.1", "proposal_id": "test-proposal",
            "resource_id": assignment["resource_id"],
            "resource_name": "Test Team",
            "current_worksite_id": assignment["worksite_id"],
            "current_label": "current",
            "proposed_worksite_id": other["worksite_id"],
            "proposed_label": "proposed",
            "reassignment_gain": 0.99, "threshold": planner.REASSIGNMENT_THRESHOLD,
            "breakdown": {}, "status": "pending",
            "based_on_state_version": controller.state_version,
            "expires_at_wall": time.time() + expires_in,
            "created_at": belief["scenario_time"],
        }

    # -- contracts ----------------------------------------------------------

    def test_emitted_objects_validate_against_the_frozen_contracts(self):
        controller, belief, _ = self.fresh("E07")
        for _ in range(20):
            controller.advance(belief, 1.0)
        emitted = [("route.schema.json", controller.routes.values()),
                   ("save.schema.json", controller.decisions.values()),
                   ("assignment.schema.json", controller.assignments.values()),
                   ("movement.schema.json", controller.movement_states())]
        for schema, objects in emitted:
            check = validator(schema)
            for item in objects:
                errors = [f"{list(e.path)}: {e.message}" for e in check.iter_errors(item)]
                self.assertFalse(errors, f"{schema} -> {errors}")

    def test_controller_holds_no_belief_of_its_own(self):
        """Save owns execution state only; belief facts stay in the belief state."""
        controller, _, _ = self.fresh("E07")
        for forbidden in ("worksites", "incidents", "sites", "evidence", "road_states"):
            self.assertFalse(hasattr(controller, forbidden),
                             f"SaveController must not own {forbidden}")


def belief_site(belief, worksite_id):
    for worksite in belief["worksites"]:
        if worksite["worksite_id"] == worksite_id:
            return worksite["site_id"]
    return None


if __name__ == "__main__":
    unittest.main()
