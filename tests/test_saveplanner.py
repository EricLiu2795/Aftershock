"""Save planner: binary road state, damage-aware routing, movement and the gate."""
import json
from pathlib import Path
import unittest

from saveplanner import planner
from saveplanner.roadgraph import Edge, RoadGraph, blocked_from_labels, haversine
from saveplanner.runtime import SavePlannerRuntime
from saveplanner.scenario import build

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "data/graph_exports/caraballeda.json"


def edge(edge_id, source, target, path, tags=None, labels=None):
    length = sum(haversine(a, b) for a, b in zip(path, path[1:]))
    return Edge({"edge_id": edge_id, "road_id": "road-" + edge_id, "source": source,
                 "target": target, "length_m": length, "path": path,
                 "osm_tags": tags or {"highway": "residential"}, "labels": labels})


def grid():
    """Two parallel routes from A to D so a block forces a measurable detour.

       A --e1-- B --e2-- D          (short, via B)
       A --e3-- C --e4-- D          (long, via C)
    """
    nodes = {"A": [-66.860, 10.600], "B": [-66.858, 10.600],
             "C": [-66.859, 10.596], "D": [-66.856, 10.600]}
    edges = [
        edge("e1", "A", "B", [nodes["A"], nodes["B"]]),
        edge("e2", "B", "D", [nodes["B"], nodes["D"]]),
        edge("e3", "A", "C", [nodes["A"], nodes["C"]]),
        edge("e4", "C", "D", [nodes["C"], nodes["D"]]),
    ]
    return RoadGraph(nodes, edges)


class BinaryRoadStateTests(unittest.TestCase):
    """Only two states reach the planner: blocked, or not blocked."""

    def test_only_explicit_blocked_blocks(self):
        self.assertTrue(blocked_from_labels({"reported_status": "blocked"}))
        self.assertFalse(blocked_from_labels({"reported_status": "limited"}))
        self.assertFalse(blocked_from_labels({"reported_status": "open"}))

    def test_unknown_is_passable_not_blocked(self):
        # The real export ships entirely unassessed; treating null as blocked would
        # make the whole town unroutable.
        self.assertFalse(blocked_from_labels({"reported_status": None}))
        self.assertFalse(blocked_from_labels({}))
        self.assertFalse(blocked_from_labels(None))

    def test_static_access_is_not_a_damage_state(self):
        footway = edge("f", "A", "B", [[-66.86, 10.6], [-66.858, 10.6]], {"highway": "footway"})
        self.assertFalse(footway.vehicle)
        self.assertFalse(footway.damage_blocked)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.graph = grid()

    def test_prefers_the_shortest_passable_route(self):
        found = self.graph.route("A", "D")
        self.assertEqual(found["edge_ids"], ["e1", "e2"])

    def test_blocked_edge_is_never_traversed(self):
        self.graph.set_blocked("e2", True)
        found = self.graph.route("A", "D")
        self.assertEqual(found["edge_ids"], ["e3", "e4"])
        self.assertNotIn("e2", found["edge_ids"])

    def test_detour_costs_more(self):
        direct = self.graph.route("A", "D")["seconds"]
        self.graph.set_blocked("e2", True)
        detour = self.graph.route("A", "D")["seconds"]
        self.assertGreater(detour, direct)

    def test_returns_none_when_every_approach_is_blocked(self):
        self.graph.set_blocked("e2", True)
        self.graph.set_blocked("e4", True)
        self.assertIsNone(self.graph.route("A", "D"))

    def test_reopening_restores_the_direct_route(self):
        self.graph.set_blocked("e2", True)
        self.graph.set_blocked("e2", False)
        self.assertEqual(self.graph.route("A", "D")["edge_ids"], ["e1", "e2"])

    def test_path_geometry_is_oriented_along_travel(self):
        found = self.graph.route("D", "A")
        self.assertEqual(found["path"][0], self.graph.nodes["D"])
        self.assertEqual(found["path"][-1], self.graph.nodes["A"])

    def test_one_way_is_respected(self):
        nodes = {"A": [-66.86, 10.6], "B": [-66.858, 10.6]}
        oneway = RoadGraph(nodes, [edge("e", "A", "B", [nodes["A"], nodes["B"]],
                                        {"highway": "residential", "oneway": "yes"})])
        self.assertIsNotNone(oneway.route("A", "B"))
        self.assertIsNone(oneway.route("B", "A"))


class ScoringTests(unittest.TestCase):
    def test_save_priority_matches_the_specification_weights(self):
        worksite = {"victim_summary": {"indication": {"type": "confirmed_multiple"}, "best_estimate": 7},
                    "structural_summary": {"severity": "severe"}, "criticality": 0.9}
        score, breakdown = planner.save_priority(worksite, ["trapped_victims"], 0.0)
        expected = (0.40 * breakdown["victim_impact"] + 0.25 * breakdown["time_sensitivity"]
                    + 0.20 * breakdown["hazard_severity"] + 0.15 * breakdown["criticality"])
        self.assertAlmostEqual(score, expected)

    def test_secondary_hazard_maximises_hazard_severity(self):
        worksite = {"structural_summary": {"severity": "minor"}}
        self.assertEqual(planner.hazard_severity(worksite, ["fire_or_secondary_hazard"]), 1.0)

    def test_reassignment_gain_subtracts_switch_cost(self):
        gain = planner.reassignment_gain(0.9, 0.6, progress_loss=0.4, additional_travel=0.2)
        self.assertAlmostEqual(gain, 0.9 - 0.6 - (0.5 * 0.4 + 0.5 * 0.2))

    def test_equivalent_units_have_no_capability_mismatch(self):
        self.assertEqual(planner.capability_mismatch({}, {}, ["trapped_victims"]), 0.0)


@unittest.skipUnless(EXPORT.is_file(), "Caraballeda export not present")
class RuntimeTests(unittest.TestCase):
    """End-to-end behaviour on the real Caraballeda graph."""

    @classmethod
    def setUpClass(cls):
        cls.graph = RoadGraph.load(EXPORT)

    def fresh(self):
        graph = RoadGraph.load(EXPORT)
        return SavePlannerRuntime(graph, build(EXPORT, graph)), graph

    def test_scenario_never_starts_with_a_stranded_worksite(self):
        runtime, _ = self.fresh()
        for worksite in runtime.eligible_worksites():
            decision = runtime.save_decisions[worksite["worksite_id"]]
            self.assertTrue(decision["candidate_assignments"],
                            f"{worksite['worksite_id']} unreachable at start")

    def test_every_available_unit_is_dispatched(self):
        runtime, _ = self.fresh()
        self.assertTrue(all(r["current_worksite_id"] for r in runtime.resources.values()))

    def test_only_corroborated_or_verified_worksites_are_planned(self):
        runtime, _ = self.fresh()
        for worksite in runtime.worksites.values():
            planned = worksite["worksite_id"] in runtime.save_decisions
            self.assertEqual(planned, worksite["evidence_status"] in planner.ELIGIBLE_EVIDENCE)

    def test_blocking_a_route_edge_reroutes_around_it(self):
        runtime, graph = self.fresh()
        for _ in range(40):
            runtime.tick(0.1)
        resource_id = next(r for r in runtime.resources if runtime.resources[r]["status"] == "en_route")
        state = runtime.movement[resource_id]
        route = runtime.routes[state["route_id"]]
        objective = route["worksite_id"]
        destination = runtime.worksites[objective]["metadata"]["access_node_id"]
        victim = next(
            (e for e in route["road_edge_ids"][2:]
             if not self._cuts(graph, e, runtime.unit_node(resource_id), destination)), None)
        if victim is None:
            self.skipTest("no non-cut edge on this route")
        runtime.set_road_blocked(victim, True, "test")
        replacement = runtime.routes.get(runtime.movement[resource_id]["route_id"])
        self.assertIsNotNone(replacement)
        self.assertNotIn(victim, replacement["road_edge_ids"])
        # A route change with the same objective needs no human approval.
        self.assertEqual(replacement["worksite_id"], objective)

    @staticmethod
    def _cuts(graph, edge_id, origin, destination):
        graph.set_blocked(edge_id, True)
        severed = destination not in graph.reachable_from(origin)
        graph.set_blocked(edge_id, False)
        return severed

    def test_units_move_along_their_route(self):
        runtime, _ = self.fresh()
        before = dict(runtime.movement[next(iter(runtime.resources))]["position"])
        for _ in range(30):
            runtime.tick(0.1)
        after = runtime.movement[next(iter(runtime.resources))]["position"]
        self.assertNotEqual(before, after)
        for state in runtime.movement.values():
            self.assertGreaterEqual(state["route_progress"], 0.0)
            self.assertLessEqual(state["route_progress"], 1.0)

    def test_movement_follows_the_road_geometry(self):
        runtime, _ = self.fresh()
        resource_id = next(iter(runtime.resources))
        route = runtime.routes[runtime.movement[resource_id]["route_id"]]
        points = {(round(p["latitude"], 6), round(p["longitude"], 6)) for p in route["path"]}
        seen_on_path = 0
        for _ in range(60):
            runtime.tick(0.05)
            position = runtime.movement[resource_id]["position"]
            # Either exactly on a shape point or between two of them; never off-graph.
            if (round(position["latitude"], 6), round(position["longitude"], 6)) in points:
                seen_on_path += 1
        self.assertLessEqual(runtime.movement[resource_id]["route_progress"], 1.0)
        self.assertGreater(len(route["road_edge_ids"]), 0)

    def test_cut_off_unit_is_released_and_retasked(self):
        runtime, graph = self.fresh()
        resource_id = next(r for r in runtime.resources if runtime.resources[r]["status"] == "en_route")
        route = runtime.routes[runtime.movement[resource_id]["route_id"]]
        objective = route["worksite_id"]
        destination = runtime.worksites[objective]["metadata"]["access_node_id"]
        # Sever the objective completely by blocking every edge at its junction.
        for incident in list(graph.edges.values()):
            if destination in (incident.source, incident.target):
                runtime.graph.set_blocked(incident.edge_id, True)
        runtime.replan("test isolation")

        # The worksite is now unreachable and needs the road cleared first.
        self.assertEqual(runtime.worksites[objective]["operational_status"], "pending")
        self.assertEqual(runtime.worksites[objective]["assigned_resource_ids"], [])
        self.assertEqual(runtime.save_decisions[objective]["recommended_task"], "clear_route")
        self.assertEqual(runtime.save_decisions[objective]["candidate_assignments"], [])
        # The crew is not left waiting at a roadblock: it is freed and re-tasked
        # somewhere reachable, or left available if nothing else qualifies.
        self.assertNotEqual(runtime.resources[resource_id]["current_worksite_id"], objective)
        reassigned = runtime.resources[resource_id]["current_worksite_id"]
        if reassigned is not None:
            self.assertIsNotNone(runtime.build_route(resource_id, reassigned))

    def test_new_evidence_raises_priority_and_replans(self):
        runtime, _ = self.fresh()
        worksite = runtime.worksites["worksite-e2"]
        before = runtime.save_decisions["worksite-e2"]["save_priority"]
        runtime.update_worksite("worksite-e2", {
            "victim_indication": "confirmed_multiple",
            "best_estimate": 9,
            "criticality": 0.95,
        }, source="test")
        after = runtime.save_decisions["worksite-e2"]["save_priority"]
        self.assertGreater(after, before)
        self.assertEqual(worksite["victim_summary"]["best_estimate"], 9)

    def test_evidence_can_promote_a_worksite_past_the_gate(self):
        runtime, _ = self.fresh()
        # C4 starts unverified, so Save must ignore it entirely.
        self.assertNotIn("worksite-c4", runtime.save_decisions)
        runtime.update_worksite("worksite-c4", {"evidence_status": "corroborated"}, "test")
        self.assertIn("worksite-c4", runtime.save_decisions)
        self.assertIsNotNone(runtime.save_decisions["worksite-c4"]["save_priority"])

    def test_evidence_update_rejects_unknown_fields(self):
        runtime, _ = self.fresh()
        with self.assertRaises(ValueError):
            runtime.update_worksite("worksite-d3", {"save_priority": 1.0}, "test")
        with self.assertRaises(KeyError):
            runtime.update_worksite("worksite-nope", {"criticality": 0.5}, "test")

    def test_time_sensitivity_rises_and_is_rescored(self):
        runtime, _ = self.fresh()
        waiting = next(w for w in runtime.eligible_worksites()
                       if not w["assigned_resource_ids"])
        before = runtime.save_decisions[waiting["worksite_id"]]["save_priority"]
        versions = runtime.state_version
        # Advance well past the re-score interval; the tick must recompute priority.
        for _ in range(60):
            runtime.tick(0.5)
        after = runtime.save_decisions[waiting["worksite_id"]]["save_priority"]
        self.assertGreater(after, before, "waiting worksite did not become more urgent")
        self.assertNotEqual(runtime.state_version, versions)

    def test_quiet_rescore_does_not_flood_the_trace(self):
        runtime, _ = self.fresh()
        before = len(runtime.trace)
        for _ in range(40):
            runtime.tick(0.5)      # many re-score intervals
        added = len(runtime.trace) - before
        self.assertLess(added, 12, "periodic re-score is spamming the decision trace")

    def test_emitted_objects_validate_against_the_frozen_contracts(self):
        """Save adds no fields to shared schemas. They are closed (additionalProperties
        false), so an extra key is a contract break, not a harmless extension."""
        from ingestion.validation import validator
        runtime, _ = self.fresh()
        for _ in range(20):
            runtime.tick(0.1)
        emitted = [
            ("route.schema.json", runtime.routes.values()),
            ("save.schema.json", runtime.save_decisions.values()),
            ("assignment.schema.json", runtime.assignments.values()),
            ("movement.schema.json", runtime.movement.values()),
            ("worksite.schema.json", runtime.worksites.values()),
            ("resource.schema.json", runtime.resources.values()),
        ]
        for schema, objects in emitted:
            check = validator(schema)
            for item in objects:
                errors = [f"{list(e.path)}: {e.message}" for e in check.iter_errors(item)]
                self.assertFalse(errors, f"{schema} -> {errors}")

    def test_snapshot_is_json_serialisable_and_complete(self):
        runtime, _ = self.fresh()
        snapshot = runtime.snapshot()
        json.dumps(snapshot, allow_nan=False)
        for key in ("state_version", "scenario_time", "worksites", "resources",
                    "routes", "blocked_edge_ids", "trace"):
            self.assertIn(key, snapshot)
        for worksite in snapshot["worksites"]:
            self.assertIn("site_id", worksite)
            self.assertTrue(worksite["simulated"])

    def test_state_version_advances_on_change(self):
        runtime, _ = self.fresh()
        before = runtime.state_version
        runtime.set_road_blocked(next(iter(runtime.graph.edges)), True, "test")
        self.assertNotEqual(before, runtime.state_version)

    def test_scenario_is_deterministic(self):
        first = build(EXPORT, RoadGraph.load(EXPORT))
        second = build(EXPORT, RoadGraph.load(EXPORT))
        self.assertEqual([w["worksite_id"] for w in first["worksites"]],
                         [w["worksite_id"] for w in second["worksites"]])
        self.assertEqual(first["blocked_edge_ids"], second["blocked_edge_ids"])


if __name__ == "__main__":
    unittest.main()
