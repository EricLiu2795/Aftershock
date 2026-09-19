"""Completed demo drone scans emit fusion-ready observations for Agent 1's runtime."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest

from operational.ids import evidence_id
from operational.runtime_bridge import run as apply_runtime
from search_planner.approval_bridge import run as run_bridge
from search_planner.common import stamp, time, validate
from search_planner.drone_feedback import (
    is_authored_completed_demo_scan,
    operational_observations_from_missions,
)
from search_planner.planner import plan_search
from search_planner.runtime import advance_mission, start_mission
from search_planner.verification import DemoVerification, PACK
from tests.operational_fixtures import make_observation
from tests.test_drone_verification import scan_state


EVENTS = json.loads((PACK / "scenario_events.json").read_text())["events"]
W7 = "osm:node:13976280529"


def event_by_id(event_id):
    return next(item for item in EVENTS if item["event_id"] == event_id)


def rumor_e11():
    e11 = event_by_id("E11")
    raw = e11["raw_input"]
    return make_observation(
        "obs-e11",
        timestamp=raw["timestamp"],
        source_type="social",
        source_id=e11["source_id"],
        author_id=raw["author_id"],
        raw_text=raw["text"],
        incident_type="trapped_victims",
        victim_type="possible_multiple",
    )


def complete_scan(key, start="2026-09-19T14:00:00Z", event="2026-09-19T14:04:28Z"):
    state = scan_state(key, start=start)
    mission = start_mission(plan_search(state), state["resources"][0]["resource_id"], state)
    ready = max(
        time(mission["launched_at"])
        + timedelta(seconds=mission["outbound_route"]["estimated_travel_time_s"] + mission["config"]["verification_duration_s"]),
        time(event),
    )
    return DemoVerification().advance(mission, stamp(ready + timedelta(seconds=2)))


def trapped(observations):
    matches = [item for item in observations if item["incident_type"] == "trapped_victims"]
    if len(matches) != 1:
        raise AssertionError(f"expected one trapped_victims observation, got {len(matches)}")
    return matches[0]


def apply_many(observations, record=None):
    return apply_runtime({
        "command": "apply_many",
        "scenario_id": "caraballeda",
        "record": record,
        "observations": observations,
    })


def incidents_for(snapshot, site_id, incident_type=None):
    items = [item for item in snapshot["incidents"] if item["site_id"] == site_id]
    if incident_type is not None:
        items = [item for item in items if item["incident_type"] == incident_type]
    return items


def worksite_for(snapshot, site_id):
    return next(item for item in snapshot["worksites"] if item["site_id"] == site_id)


class DroneBeliefFeedbackTests(unittest.TestCase):
    def test_w7_authored_scan_emits_verified_negative_without_intended_referent(self):
        mission = complete_scan("W7")
        report = mission["drone_report"]
        self.assertTrue(is_authored_completed_demo_scan(report))
        self.assertEqual(report["location"]["resolved_site_id"], mission["site_id"])
        observations = operational_observations_from_missions([mission])
        drone = trapped(observations)
        validate("observation.schema.json", drone)
        self.assertEqual(drone["source_type"], "drone")
        self.assertEqual(drone["verification_status"], "verified")
        self.assertEqual(drone["victim_indication"], {"type": "none", "count_min": 0, "count_max": 0})
        e12 = event_by_id("E12")["raw_input"]
        self.assertEqual(drone["structural_hazard"], e12["structural_damage"])
        self.assertEqual(drone["access_status"], e12["access_status"])
        self.assertEqual(drone["location"]["raw_text"], e12["location"]["raw_text"])
        self.assertEqual(drone["location"]["label"], e12["location"]["label"])
        self.assertEqual(drone["location"]["latitude"], e12["location"]["latitude"])
        self.assertEqual(drone["location"]["longitude"], e12["location"]["longitude"])
        self.assertIsNone(drone["location"]["resolved_site_id"])
        blob = json.dumps(drone)
        for leaked in (
            "intended_referent", "hidden_truth_linkage", "expected_downstream_interpretation",
            "verification_assets", "worksite_key", "osm:node:13976280529",
        ):
            self.assertNotIn(leaked, blob)
        self.assertTrue(drone["metadata"].get("authored_demo_verification"))

    def test_abstraction_also_covers_w2_authored_scan(self):
        mission = complete_scan("W2", event="2026-09-19T14:01:45Z")
        observations = operational_observations_from_missions([mission])
        drone = trapped(observations)
        self.assertEqual(drone["verification_status"], "verified")
        self.assertEqual(drone["victim_indication"]["type"], "possible_multiple")
        self.assertEqual(drone["structural_hazard"], "major_damage")
        self.assertEqual(drone["access_status"], "limited")
        self.assertIsNone(drone["location"]["resolved_site_id"])
        self.assertNotIn("intended_referent", json.dumps(drone))

    def test_w7_rumor_then_completed_scan_invalidates_and_resolves(self):
        rumor = rumor_e11()
        after_rumor = apply_many([rumor])
        snapshot = after_rumor["snapshot"]
        self.assertEqual(after_rumor["trace"]["resolved_site_id"], W7)
        incident = incidents_for(snapshot, W7, "trapped_victims")[0]
        self.assertEqual(incident["status"], "active")
        self.assertIn(incident["verification_status"], {"unverified", "potential"})
        worksite = worksite_for(snapshot, W7)
        self.assertEqual(worksite["operational_status"], "pending")
        self.assertEqual(worksite["active_incident_ids"], [incident["incident_id"]])
        previous_version = snapshot["state_version"]

        mission = complete_scan("W7")
        drone = trapped(operational_observations_from_missions([mission]))
        after_drone = apply_many([drone], after_rumor["record"])
        snapshot = after_drone["snapshot"]
        self.assertEqual(after_drone["trace"]["resolved_site_id"], W7)
        self.assertTrue(after_drone["trace"]["changed"])
        self.assertNotEqual(snapshot["state_version"], previous_version)

        updated = incidents_for(snapshot, W7, "trapped_victims")[0]
        self.assertEqual(updated["incident_id"], incident["incident_id"])
        self.assertEqual(updated["status"], "invalidated")
        self.assertNotEqual(updated["verification_status"], "corroborated")
        self.assertIn("obs-e11", updated["metadata"]["supporting_observation_ids"])
        self.assertIn(drone["observation_id"], updated["metadata"]["conflicting_observation_ids"])
        self.assertEqual(set(updated["observation_ids"]), {"obs-e11", drone["observation_id"]})
        self.assertEqual(set(updated["evidence_ids"]), {evidence_id("obs-e11"), evidence_id(drone["observation_id"])})

        worksite = worksite_for(snapshot, W7)
        self.assertEqual(worksite["operational_status"], "resolved")
        self.assertEqual(worksite["active_incident_ids"], [])
        self.assertEqual(worksite["metadata"]["invalidated_incident_ids"], [updated["incident_id"]])
        self.assertEqual(set(worksite["evidence_ids"]), set(updated["evidence_ids"]))

    def test_same_scan_applied_twice_does_not_duplicate_evidence(self):
        rumor = apply_many([rumor_e11()])
        mission = complete_scan("W7")
        observations = operational_observations_from_missions([mission])
        once = apply_many(observations, rumor["record"])
        victim = incidents_for(once["snapshot"], W7, "trapped_victims")[0]
        twice = apply_many(observations, once["record"])
        self.assertEqual(twice["snapshot"]["state_version"], once["snapshot"]["state_version"])
        self.assertFalse(twice["trace"]["changed"])
        self.assertEqual(twice["trace"]["reason"], "duplicate_observation")
        again = incidents_for(twice["snapshot"], W7, "trapped_victims")[0]
        self.assertEqual(again["incident_id"], victim["incident_id"])
        self.assertEqual(again["evidence_ids"], victim["evidence_ids"])
        self.assertEqual(again["observation_ids"], victim["observation_ids"])

    def test_unverified_drone_cannot_invalidate(self):
        after_rumor = apply_many([rumor_e11()])
        incident = incidents_for(after_rumor["snapshot"], W7, "trapped_victims")[0]
        mission = complete_scan("W7")
        drone = deepcopy(trapped(operational_observations_from_missions([mission])))
        drone["verification_status"] = "unverified"
        drone["observation_id"] = drone["observation_id"] + "-untrusted"
        after = apply_many([drone], after_rumor["record"])
        self.assertEqual(after["trace"]["resolved_site_id"], W7)
        updated = incidents_for(after["snapshot"], W7, "trapped_victims")[0]
        self.assertEqual(updated["incident_id"], incident["incident_id"])
        self.assertEqual(updated["status"], "active")
        self.assertNotEqual(updated["status"], "invalidated")
        worksite = worksite_for(after["snapshot"], W7)
        self.assertEqual(worksite["operational_status"], "pending")
        self.assertEqual(worksite["active_incident_ids"], [incident["incident_id"]])

    def test_manual_report_without_authored_verification_stays_unverified(self):
        state = scan_state("W7")
        mission = start_mission(plan_search(state), state["resources"][0]["resource_id"], state)
        verify = time(mission["launched_at"]) + timedelta(
            seconds=mission["outbound_route"]["estimated_travel_time_s"] + mission["config"]["verification_duration_s"])
        report = deepcopy(event_by_id("E12")["raw_input"])
        report["input_id"] = "manual-w7-report"
        report["drone_id"] = mission["resource"]["resource_id"]
        report["timestamp"] = stamp(verify)
        report["location"] = {
            **report["location"],
            "latitude": mission["outbound_route"]["path"][-1]["latitude"],
            "longitude": mission["outbound_route"]["path"][-1]["longitude"],
        }
        published = advance_mission(advance_mission(mission, stamp(verify), report), stamp(verify + timedelta(seconds=2)))
        self.assertFalse(is_authored_completed_demo_scan(published["drone_report"]))
        drone = trapped(operational_observations_from_missions([published]))
        self.assertEqual(drone["verification_status"], "unverified")
        self.assertFalse(drone["metadata"].get("authored_demo_verification"))

        after = apply_many([rumor_e11(), drone])
        updated = incidents_for(after["snapshot"], W7, "trapped_victims")[0]
        self.assertEqual(updated["status"], "active")

    def test_approval_bridge_emits_observations_without_search_belief_state(self):
        state = scan_state("W7")
        initialized = run_bridge({"command": "initialize", "scenario_id": "caraballeda", "state": state})
        search_version = initialized["state"]["state_version"]
        self.assertEqual(initialized.get("operational_observations"), [])
        self.assertNotIn("operational_runtime", initialized)
        initialized["last_wall_at"] = "2026-09-19T14:00:00.000000Z"
        initialized["missions"] = [complete_scan("W7")]
        initialized["state"]["scenario_time"] = initialized["missions"][0]["last_updated_at"]
        result = run_bridge({
            "command": "tick",
            "scenario_id": "caraballeda",
            "record": initialized,
        })
        self.assertEqual(result["state"]["state_version"], search_version)
        self.assertNotIn("operational_runtime", result)
        drone = trapped(result["operational_observations"])
        self.assertEqual(drone["verification_status"], "verified")
        self.assertIsNone(drone["location"]["resolved_site_id"])
        self.assertNotIn("intended_referent", json.dumps(drone))


if __name__ == "__main__":
    unittest.main()
