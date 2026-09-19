"""Live Save against Agent 1 OperationalBeliefState — not the pack player."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import unittest

from operational.ids import worksite_id
from saveplanner.live_bridge import run as save_run
from tests.test_runtime_bridge import e01_observation, e02_observation, invoke, load_events


W1 = "osm:way:1533952351"
W2 = "osm:way:1533952352"
W1_WORKSITE = worksite_id(W1)
R1 = "osm:way:376529129:segment:1-12"
PACK = Path(__file__).resolve().parents[1] / "data" / "scenarios" / "caraballeda"
W2 = "osm:way:1533952352"
W1_WORKSITE = worksite_id(W1)
R1 = "osm:way:376529129:segment:1-12"


def past_wall(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def apply_all(*observations, record=None):
    snapshot = None
    for observation in observations:
        result = invoke({
            "command": "apply",
            "scenario_id": "caraballeda",
            "record": record,
            "observation": observation,
        })
        record = result["record"]
        snapshot = result["snapshot"]
        snapshot["scenario_id"] = "caraballeda"
    return record, snapshot


def block_r1(record):
    result = invoke({
        "command": "apply_road",
        "scenario_id": "caraballeda",
        "record": record,
        "road_state": {"road_id": R1, "status": "blocked"},
    })
    snapshot = result["snapshot"]
    snapshot["scenario_id"] = "caraballeda"
    return result["record"], snapshot


def solo_team(snapshot):
    pack = json.loads((PACK / "scenario.json").read_text(encoding="utf-8"))
    unit = next(item for item in pack["resources"] if item["resource_id"] == "rescue-alpha")
    movement = next(item for item in pack["initial_movement_states"] if item["resource_id"] == "rescue-alpha")
    out = deepcopy(snapshot)
    out["resources"] = [deepcopy(unit)]
    out["movement_states"] = [deepcopy(movement)]
    return out


def add_verified_w2(snapshot):
    sites = json.loads((PACK / "active_sites.json").read_text(encoding="utf-8"))
    w2_entry = next(item for item in sites["active_worksites"] if item["worksite_key"] == "W2")
    extra = deepcopy(snapshot["worksites"][0])
    extra["worksite_id"] = worksite_id(W2)
    extra["site_id"] = W2
    extra["evidence_status"] = "verified"
    extra["display_name"] = w2_entry.get("display_name")
    extra["metadata"] = {
        **(extra.get("metadata") or {}),
        "search_features": {
            "criticality": 1,
            "potential_impact": 1,
            "uncertainty": 0,
            "evidence_conflict": 0,
        },
        "scenario_operational_overlay": {"scenario_criticality": 1.0},
    }
    extra["active_incident_ids"] = ["incident-w2-test"]
    if extra.get("victim_summary"):
        extra["victim_summary"] = deepcopy(extra["victim_summary"])
        extra["victim_summary"]["indication"]["count_min"] = 20
        extra["victim_summary"]["count_min"] = 20
        extra["victim_summary"]["best_estimate"] = 20
    incident = deepcopy(snapshot["incidents"][0])
    incident["incident_id"] = "incident-w2-test"
    incident["site_id"] = W2
    incident["observation_ids"] = ["obs-w2-test"]
    incident["evidence_ids"] = ["evidence-w2-test"]
    out = deepcopy(snapshot)
    out["worksites"].append(extra)
    w1 = out["worksites"][0]
    if w1.get("victim_summary"):
        w1["victim_summary"] = deepcopy(w1["victim_summary"])
        w1["victim_summary"]["indication"]["type"] = "possible_individual"
        w1["victim_summary"]["best_estimate"] = 1
        w1["victim_summary"]["count_min"] = 1
    out["sites"] = list(out.get("sites") or []) + [deepcopy(w2_entry["site"])]
    out["incidents"].append(incident)
    out["state_version"] = str(int(snapshot["state_version"]) + 1)
    return out


def tick(snapshot, record=None, wall_s=0, speed=10):
    if record is not None and wall_s:
        record = deepcopy(record)
        record["last_wall_at"] = past_wall(wall_s)
        record["simulation_speed"] = speed
    return save_run({
        "command": "tick",
        "scenario_id": "caraballeda",
        "operational_snapshot": snapshot,
        "record": record,
        "simulation_speed": speed,
    })


class LiveSaveBeliefTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = load_events()

    def test_a_same_canonical_worksite(self):
        _, snapshot = apply_all(e01_observation(self.events), e02_observation(self.events))
        result = tick(snapshot)
        w1 = next(item for item in result["view"]["worksites"] if item["site_id"] == W1)
        self.assertEqual(w1["worksite_id"], W1_WORKSITE)
        self.assertEqual(w1["site_id"], snapshot["worksites"][0]["site_id"])
        self.assertEqual(w1["worksite_id"], snapshot["worksites"][0]["worksite_id"])
        assignment = result["view"]["assignments"][0]
        self.assertEqual(assignment["worksite_id"], W1_WORKSITE)

    def test_b_evidence_gate(self):
        _, rumor = apply_all(e01_observation(self.events))
        rumor_save = tick(rumor)
        self.assertEqual(rumor["worksites"][0]["evidence_status"], "unverified")
        self.assertEqual(rumor_save["view"]["assignments"], [])
        _, supported = apply_all(e01_observation(self.events), e02_observation(self.events))
        supported_save = tick(supported)
        self.assertTrue(supported_save["view"]["assignments"])
        self.assertTrue(supported_save["view"]["routes"])
        self.assertEqual(supported_save["view"]["assignments"][0]["worksite_id"], W1_WORKSITE)

    def test_c_state_version_lifecycle(self):
        waiting = tick(None)
        self.assertTrue(waiting["waiting"])
        _, snapshot = apply_all(e01_observation(self.events), e02_observation(self.events))
        first = tick(snapshot)
        self.assertEqual(first["sync_action"], "initialize")
        version = snapshot["state_version"]
        second = tick(snapshot, first)
        self.assertEqual(second["sync_action"], "noop")
        self.assertEqual(second["last_consumed_operational_state_version"], version)
        snapshot = deepcopy(snapshot)
        snapshot["state_version"] = str(int(version) + 1)
        third = tick(snapshot, second)
        self.assertEqual(third["sync_action"], "update")
        self.assertEqual(third["last_consumed_operational_state_version"], snapshot["state_version"])
        self.assertEqual(first["view"]["state_version"], version)

    def test_d_r1_reroute_same_objective_no_proposal(self):
        record, snapshot = apply_all(e01_observation(self.events), e02_observation(self.events))
        first = tick(snapshot)
        assignment = first["view"]["assignments"][0]
        resource_id = assignment["resource_id"]
        old_route = assignment["route_id"]
        moved = tick(snapshot, first, wall_s=8, speed=10)
        position = next(
            item["position"] for item in moved["view"]["resources"]
            if item["resource_id"] == resource_id
        )
        self.assertGreater(
            next(item["distance_traveled_m"] for item in moved["execution"]["movement"].values()
                 if item["resource_id"] == resource_id),
            0,
        )
        _, blocked = block_r1(record)
        self.assertGreater(int(blocked["state_version"]), int(snapshot["state_version"]))
        self.assertTrue(any(item["road_id"] == R1 and item["status"] == "blocked"
                            for item in blocked["road_states"]))
        rerouted = tick(blocked, moved)
        self.assertEqual(rerouted["sync_action"], "update")
        still = next(item for item in rerouted["view"]["assignments"] if item["status"] == "active")
        self.assertEqual(still["resource_id"], resource_id)
        self.assertEqual(still["worksite_id"], W1_WORKSITE)
        self.assertNotEqual(still["route_id"], old_route)
        self.assertTrue(all(R1 not in route["road_edge_ids"] for route in rerouted["view"]["routes"]))
        self.assertIsNone(rerouted["view"]["proposal"])
        start = rerouted["view"]["routes"][0]["path"][0]
        self.assertLess(
            abs(start["latitude"] - position["latitude"]) + abs(start["longitude"] - position["longitude"]),
            0.01,
        )

    def test_e_resolved_worksite_is_dropped(self):
        _, snapshot = apply_all(e01_observation(self.events), e02_observation(self.events))
        first = tick(snapshot)
        self.assertTrue(first["view"]["assignments"])
        resolved = deepcopy(snapshot)
        resolved["worksites"][0]["operational_status"] = "resolved"
        resolved["state_version"] = str(int(snapshot["state_version"]) + 1)
        later = tick(resolved, first)
        self.assertEqual(later["view"]["assignments"], [])
        self.assertFalse(any(item["eligible"] for item in later["view"]["worksites"]))

    def test_f_objective_change_requires_proposal(self):
        _, snapshot = apply_all(e01_observation(self.events), e02_observation(self.events))
        snapshot = solo_team(snapshot)
        first = tick(snapshot)
        current = first["view"]["assignments"][0]["worksite_id"]
        enriched = add_verified_w2(snapshot)
        later = tick(enriched, first)
        still = next(item for item in later["view"]["assignments"] if item["status"] == "active")
        self.assertEqual(still["worksite_id"], current)
        proposal = later["view"]["proposal"]
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(proposal["current_worksite_id"], current)
        self.assertEqual(proposal["proposed_worksite_id"], worksite_id(W2))
        self.assertGreater(proposal["reassignment_gain"], proposal["threshold"])

    def test_g_stale_proposal_revalidates(self):
        _, snapshot = apply_all(e01_observation(self.events), e02_observation(self.events))
        snapshot = solo_team(snapshot)
        first = tick(snapshot)
        enriched = add_verified_w2(snapshot)
        proposed = tick(enriched, first)
        proposal = proposed["view"]["proposal"]
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["status"], "pending")
        stale = deepcopy(enriched)
        stale["worksites"][1]["evidence_status"] = "unverified"
        stale["state_version"] = str(int(enriched["state_version"]) + 1)
        decided = save_run({
            "command": "decide",
            "scenario_id": "caraballeda",
            "operational_snapshot": stale,
            "record": proposed,
            "proposal_id": proposal["proposal_id"],
            "approve": True,
        })
        self.assertEqual(decided["execution"]["proposal"]["status"], "invalidated")
        active = [item for item in decided["view"]["assignments"] if item["status"] == "active"]
        self.assertEqual(active[0]["worksite_id"], W1_WORKSITE)

    def test_save_does_not_advance_canonical_state_version(self):
        _, snapshot = apply_all(e01_observation(self.events), e02_observation(self.events))
        before = snapshot["state_version"]
        result = tick(snapshot)
        self.assertEqual(snapshot["state_version"], before)
        self.assertEqual(result["last_consumed_operational_state_version"], before)
        self.assertNotEqual(result["execution_revision"], before)

    def test_apply_road_is_canonical(self):
        record, snapshot = apply_all(e01_observation(self.events), e02_observation(self.events))
        _, blocked = block_r1(record)
        self.assertNotEqual(blocked["state_version"], snapshot["state_version"])
        r1 = next(item for item in blocked["road_states"] if item["road_id"] == R1)
        self.assertEqual(r1["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
