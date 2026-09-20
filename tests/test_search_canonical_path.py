"""Integrated path: canonical OperationalBeliefState drives Search without operator clicks.

Ground truth stays hidden until a simulated drone physically completes the scan, so
pre-scan ranking inputs may only quote observable social/radio/scan evidence.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from operational.belief_state import apply_observation, empty_state
from operational.ids import worksite_id
from operational.site_registry import ScenarioPackSiteRegistry
from search_planner.approval_bridge import run
from search_planner.building_priority import enrich_worksite
from search_planner.coverage import inspected_sites, publish_priorities
from search_planner.planner import plan_search, score_worksite
from search_planner.scenario_buildings import scenario_buildings
from search_planner.tracking_demo import prepare_tracking, sync_intake
from tests.operational_fixtures import T0, make_observation

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data" / "scenarios" / "caraballeda"
W1 = "osm:way:1533952351"
W2 = "osm:way:1533952352"
# Every key DroneApprovalPanel and contracts/drone_tracking.schema.json read back.
EXPLANATION_KEYS = {"people_count", "people_count_basis", "people_score", "damage_level", "damage_score",
                    "people_contribution", "damage_contribution", "uncertainty_contribution",
                    "observation_ids", "database_report_id", "priority"}
GROUND_TRUTH_KEYS = {"scenario_source", "simulation", "scenario_worksite_key", "scenario_id"}


def load(name):
    return json.loads((PACK / name).read_text(encoding="utf-8"))


class CanonicalSearchPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = load("scenario_events.json")["events"]
        cls.manifest = load("scenario.json")
        cls.registry = ScenarioPackSiteRegistry(PACK / "active_sites.json")

    def event(self, event_id):
        return next(item for item in self.events if item["event_id"] == event_id)

    def social(self, event_id, **kwargs):
        raw = self.event(event_id)["raw_input"]
        return make_observation("obs-" + event_id.lower(), timestamp=raw["timestamp"], source_type="social",
                               source_id=self.event(event_id)["source_id"], author_id=raw.get("author_id"),
                               raw_text=raw["text"], victim_type="possible_multiple", **kwargs)

    def radio(self, event_id, **kwargs):
        raw = self.event(event_id)["raw_input"]
        return make_observation("obs-" + event_id.lower(), timestamp=raw["timestamp"], source_type="radio",
                               source_id=self.event(event_id)["source_id"], raw_text=raw["transcript"],
                               victim_type="possible_multiple", **kwargs)

    def belief(self, *observations):
        state = empty_state(T0)
        for observation in observations:
            state = apply_observation(state, observation, self.registry)
        return state.to_belief_state()

    def with_drones(self, snapshot, *resource_ids):
        """Pin the drone fleet so the pack overlay cannot widen it."""
        out = deepcopy(snapshot)
        out["resources"] = [deepcopy(r) for r in self.manifest["resources"] if r["resource_id"] in resource_ids]
        out["movement_states"] = [deepcopy(m) for m in self.manifest["initial_movement_states"]
                                  if m["resource_id"] in resource_ids]
        for movement in out["movement_states"]:
            movement["last_updated_at"] = out["scenario_time"]
        return out

    def tick(self, record, snapshot, *, elapsed=0, command="tick"):
        if record is not None and elapsed:
            record = deepcopy(record)
            record["last_wall_at"] = (datetime.now(timezone.utc) - timedelta(seconds=elapsed)).isoformat()
        return run({"command": command, "scenario_id": "caraballeda", "session_id": "canonical",
                    "record": record, "operational_snapshot": snapshot})

    def test_waits_without_an_actionable_canonical_worksite(self):
        resolved = self.belief(self.social("E11"), self.verified_e12())
        self.assertEqual([w["operational_status"] for w in resolved["worksites"]], ["resolved"])
        for snapshot in (None, {"state_version": "0", "worksites": [], "scenario_time": T0}, resolved):
            result = self.tick(None, snapshot)
            self.assertTrue(result["uninitialized"])
            self.assertEqual(result["waiting_reason"], "NO_CANONICAL_WORKSITE")
            self.assertEqual(result["missions"], [])
            self.assertNotIn("state", result)
            self.assertEqual(result["proposals"], [])
        with self.assertRaises(ValueError):
            self.tick(None, None, command="dispatch")

    def verified_e12(self):
        """The authored drone scan that invalidates the E11 rumor and resolves W7."""
        raw = self.event("E12")["raw_input"]
        return make_observation("obs-e12", timestamp=raw["timestamp"], source_type="drone",
                                source_id=self.event("E12")["source_id"], raw_text=raw["location"]["raw_text"],
                                label=raw["location"]["label"], latitude=raw["location"]["latitude"],
                                longitude=raw["location"]["longitude"], victim_type=raw["victim_indicator"]["type"],
                                count_min=raw["victim_indicator"]["count_min"],
                                count_max=raw["victim_indicator"]["count_max"],
                                structural_hazard=raw["structural_damage"], access_status=raw["access_status"],
                                verification_status="verified")

    def test_canonical_w1_initializes_and_dispatches_without_a_dispatch_command(self):
        snapshot = self.with_drones(self.belief(self.social("E01")), "drone-1", "drone-2")
        result = self.tick(None, snapshot)
        self.assertEqual(result["sync_action"], "initialize")
        self.assertEqual(result["operational_state_version"], snapshot["state_version"])
        self.assertFalse(result["tracking_demo"])
        self.assertTrue(result["scanning_enabled"])
        self.assertTrue(result["missions"])
        for mission in result["missions"]:
            self.assertEqual(mission["site_id"], W1)
            self.assertEqual(mission["worksite_id"], worksite_id(W1))
            self.assertEqual(mission["phase"], "en_route")
        self.assertEqual([(p["rank"], p["site_id"], p["assigned"], p["people_count"])
                          for p in result["target_priorities"]], [(1, W1, True, 6)])

    def test_prepare_uses_canonical_belief_instead_of_the_tracking_harness(self):
        snapshot = self.with_drones(self.belief(self.social("E01")), "drone-1", "drone-2")
        prepared = self.tick(None, snapshot, command="prepare")
        self.assertFalse(prepared["tracking_demo"])
        self.assertTrue(prepared["scanning_enabled"])
        self.assertEqual(prepared["sync_action"], "initialize")
        self.assertEqual([m["site_id"] for m in prepared["missions"]], [W1])
        harness = self.tick(None, None, command="prepare")
        self.assertTrue(harness["tracking_demo"])
        self.assertFalse(harness["scanning_enabled"])
        self.assertEqual(harness["missions"], [])
        self.assertEqual(run({"command": "prepare", "scenario_id": "caraballeda", "session_id": "canonical",
                              "record": harness, "operational_snapshot": snapshot}), harness)

    def test_a_prepared_harness_hands_over_as_soon_as_belief_gains_w1(self):
        harness = self.tick(None, None, command="prepare")
        self.assertTrue(harness["tracking_demo"])
        self.assertFalse(harness["scanning_enabled"])
        snapshot = self.with_drones(self.belief(self.social("E01")), "drone-1", "drone-2")
        taken = self.tick(harness, snapshot)
        self.assertEqual(taken["sync_action"], "update")
        self.assertEqual(taken["operational_state_version"], snapshot["state_version"])
        self.assertFalse(taken["tracking_demo"])
        self.assertTrue(taken["scanning_enabled"])
        self.assertEqual([w["worksite_id"] for w in taken["state"]["worksites"]], [worksite_id(W1)])
        self.assertEqual([m["site_id"] for m in taken["missions"]], [W1])
        self.assertEqual(taken["missions"][0]["phase"], "en_route")

    def test_second_observation_updates_the_same_canonical_worksite(self):
        first = self.with_drones(self.belief(self.social("E01")), "drone-1", "drone-2")
        record = self.tick(None, first)
        corroborated = self.with_drones(self.belief(self.social("E01"), self.radio("E02")), "drone-1", "drone-2")
        self.assertNotEqual(corroborated["state_version"], first["state_version"])
        self.assertEqual([w["worksite_id"] for w in corroborated["worksites"]], [worksite_id(W1)])
        updated = self.tick(record, corroborated)
        self.assertEqual(updated["sync_action"], "update")
        self.assertEqual(updated["operational_state_version"], corroborated["state_version"])
        self.assertEqual({w["site_id"] for w in updated["state"]["worksites"]}, {W1})
        self.assertEqual({m["worksite_id"] for m in updated["missions"]}, {worksite_id(W1)})
        self.assertEqual([m["mission_id"] for m in updated["missions"]],
                         [m["mission_id"] for m in record["missions"]])

    def test_completed_scan_marks_the_canonical_site_inspected(self):
        snapshot = self.with_drones(self.belief(self.social("E01")), "drone-1")
        record = self.tick(None, snapshot)
        self.assertEqual(record["inspected_site_ids"], [])
        scanned = self.tick(record, snapshot, elapsed=600)
        self.assertEqual(scanned["inspected_site_ids"], [W1])
        self.assertEqual(inspected_sites(scanned), {W1})
        self.assertIsNotNone(scanned["missions"][0]["drone_report"])
        self.assertTrue(scanned["operational_observations"])

    def test_next_target_launches_from_the_current_position_not_base(self):
        snapshot = self.with_drones(self.belief(self.social("E01"), self.social("E03")), "drone-1")
        record = self.tick(None, snapshot)
        self.assertEqual(len(record["missions"]), 1)
        first = deepcopy(record["missions"][0])
        chained = self.tick(record, snapshot, elapsed=600)
        second = chained["missions"][0]
        self.assertNotEqual(second["site_id"], first["site_id"])
        self.assertEqual(second["phase"], "en_route")
        self.assertEqual(second["outbound_route"]["path"][0], first["outbound_route"]["path"][-1])
        self.assertNotEqual(second["outbound_route"]["path"][0], first["home_position"])
        self.assertEqual(second["home_position"], first["home_position"])
        self.assertFalse(any(e["event_type"] == "DRONE_RETURN_STARTED" for e in chained["history"][0]["mission"]["events"]))

    def test_a_canonical_building_is_never_inspected_twice(self):
        snapshot = self.with_drones(self.belief(self.social("E01"), self.social("E03")), "drone-1")
        record = self.tick(None, snapshot)
        for _ in range(4):
            record = self.tick(record, snapshot, elapsed=600)
        completed = [m for m in record["missions"] + [h["mission"] for h in record["history"]] if m["drone_report"]]
        self.assertEqual(sorted(m["site_id"] for m in completed), [W1, W2])
        self.assertEqual(record["inspected_site_ids"], [W1, W2])
        self.assertEqual(record["target_priorities"], [])
        again = self.tick(record, snapshot, elapsed=600, command="dispatch")
        self.assertEqual(sorted(inspected_sites(again)), [W1, W2])
        self.assertEqual([m["site_id"] for m in again["missions"]], [W2])
        self.assertTrue(all(m["phase"] == "idle" for m in again["missions"]))

    def test_two_drones_work_concurrently_on_distinct_canonical_sites(self):
        snapshot = self.with_drones(self.belief(self.social("E01"), self.social("E03")), "drone-1", "drone-2")
        record = self.tick(None, snapshot)
        self.assertEqual({m["site_id"] for m in record["missions"]}, {W1, W2})
        self.assertEqual({m["resource"]["resource_id"] for m in record["missions"]}, {"drone-1", "drone-2"})
        self.assertTrue(all(m["phase"] == "en_route" for m in record["missions"]))

    def test_demo_counts_feed_existing_scoring_without_mutating_canonical_identity(self):
        snapshot = self.with_drones(self.belief(self.social("E01"), self.social("E03")), "drone-1", "drone-2")
        canonical_ids = [(w["site_id"], w["worksite_id"]) for w in snapshot["worksites"]]
        record = self.tick(None, snapshot)
        self.assertEqual([(w["site_id"], w["worksite_id"]) for w in record["state"]["worksites"]], canonical_ids)
        self.assertEqual([p["people_count"] for p in record["target_priorities"]], [6, 3])
        for worksite in record["state"]["worksites"]:
            factors = worksite["metadata"]["building_priority"]
            self.assertTrue(EXPLANATION_KEYS <= set(factors))
            self.assertTrue(GROUND_TRUTH_KEYS & set(factors))
            self.assertEqual(factors["people_count_basis"], "scenario_simulated_count")
            self.assertEqual(score_worksite(worksite)[2]["total"], factors["priority"])
        priorities = [p["score"] for p in record["target_priorities"]]
        self.assertEqual(priorities, sorted(priorities, reverse=True))

    def test_post_scan_observations_may_influence_later_planning(self):
        before = sync_intake(prepare_tracking("2026-09-19T15:00:00Z", "post-scan"), self.intake(W1, W2))
        after = deepcopy(before)
        scanned = next(w for w in after["state"]["worksites"] if w["site_id"] == W1)
        enrich_worksite(scanned, self.observations(W1), self.stored_scan(W1, 6, "severe_collapse"))
        factors = scanned["metadata"]["building_priority"]
        self.assertEqual(factors["people_count"], 6)
        self.assertEqual(factors["people_count_basis"], "maximum_reported_bound_not_sum")
        self.assertEqual(factors["damage_level"], "severe_collapse")
        self.assertEqual(factors["database_report_id"], "scan:" + W1)
        self.assertEqual(score_worksite(scanned)[2]["total"],
                         score_worksite(next(w for w in before["state"]["worksites"] if w["site_id"] == W1))[2]["total"])
        plan = plan_search(after["state"])
        explanation = next(e for e in plan["explanations"] if e["target_worksite_id"] == scanned["worksite_id"])
        self.assertEqual(explanation["building_factors"]["people_count"], 6)
        self.assertIn("REPORTED_PEOPLE_COUNT", explanation["reason_codes"])
        self.assertEqual(plan["assignments"][0]["worksite_id"], scanned["worksite_id"])

    def observations(self, site_id):
        centroid = self.centroids()[site_id]
        return [{"schema_version": "2.1", "observation_id": "social:" + site_id,
                 "timestamp": "2026-09-19T14:00:00Z", "source_type": "social",
                 "incident_type": "trapped_victims", "location": dict(centroid, resolved_site_id=site_id),
                 "victim_indication": {"type": "possible_multiple"}, "structural_hazard": "unknown",
                 "access_status": "unknown", "verification_status": "unverified"}]

    def stored_scan(self, site_id, count, damage):
        return {"site_id": site_id, "report_id": "scan:" + site_id,
                "report": {"structural_damage": damage,
                           "victim_indicator": {"type": "confirmed_multiple", "count_min": count, "count_max": count}}}

    def centroids(self):
        return {entry["site"]["site_id"]: entry["site"]["centroid"]
                for entry in load("active_sites.json")["active_worksites"]}

    def intake(self, *site_ids):
        sites = {entry["site"]["site_id"]: entry["site"] for entry in load("active_sites.json")["active_worksites"]}
        return [{"site": sites[site_id], "observations": self.observations(site_id),
                 "report_id": "report:" + site_id} for site_id in site_ids]


if __name__ == "__main__":
    unittest.main()
