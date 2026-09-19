"""OperationalBeliefState → Search adapter: init, versioning, IDs, resolution."""

from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from operational.belief_state import apply_observation, empty_state
from operational.ids import worksite_id
from operational.site_registry import ScenarioPackSiteRegistry
from operational.snapshot import AGENT1_SNAPSHOT_TABLE, get_operational_snapshot
from search_planner.operational_sync import (
    decide_search_sync,
    planning_snapshot,
    sync_search_from_operational_state,
)
from search_planner.planner import extract_features
from tests.operational_fixtures import T0, make_observation


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data" / "scenarios" / "caraballeda"
WALL = "2026-09-19T15:00:00Z"
LATER = "2026-09-19T15:00:01Z"

W1 = "osm:way:1533952351"
W2 = "osm:way:1533952352"
W7 = "osm:node:13976280529"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def event_by_id(events, event_id):
    return next(item for item in events["events"] if item["event_id"] == event_id)


class LiveBeliefSearchSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = load_json(PACK / "scenario_events.json")
        cls.registry = ScenarioPackSiteRegistry(PACK / "active_sites.json")

    def _event(self, event_id):
        return event_by_id(self.events, event_id)

    def _reduce(self, *observations):
        state = empty_state(T0)
        for observation in observations:
            state = apply_observation(state, observation, self.registry)
        return state.to_belief_state()

    def _e01(self):
        e01 = self._event("E01")
        return make_observation(
            "obs-e01",
            timestamp=e01["raw_input"]["timestamp"],
            source_type="social",
            source_id=e01["source_id"],
            author_id=e01["raw_input"]["author_id"],
            raw_text=e01["raw_input"]["text"],
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )

    def _e03(self):
        e03 = self._event("E03")
        return make_observation(
            "obs-e03",
            timestamp=e03["raw_input"]["timestamp"],
            source_type="social",
            source_id=e03["source_id"],
            author_id=e03["raw_input"]["author_id"],
            raw_text=e03["raw_input"]["text"],
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )

    def _e11(self):
        e11 = self._event("E11")
        return make_observation(
            "obs-e11",
            timestamp=e11["raw_input"]["timestamp"],
            source_type="social",
            source_id=e11["source_id"],
            author_id=e11["raw_input"]["author_id"],
            raw_text=e11["raw_input"]["text"],
            incident_type="trapped_victims",
            victim_type="possible_multiple",
        )

    def _e12(self):
        e12 = self._event("E12")
        raw = e12["raw_input"]
        return make_observation(
            "obs-e12",
            timestamp=raw["timestamp"],
            source_type="drone",
            source_id=e12["source_id"],
            raw_text=raw["location"]["raw_text"],
            label=raw["location"]["label"],
            latitude=raw["location"]["latitude"],
            longitude=raw["location"]["longitude"],
            incident_type="trapped_victims",
            victim_type=raw["victim_indicator"]["type"],
            count_min=raw["victim_indicator"]["count_min"],
            count_max=raw["victim_indicator"]["count_max"],
            structural_hazard=raw["structural_damage"],
            access_status=raw["access_status"],
            verification_status="verified",
        )

    def _w1(self):
        return self._reduce(self._e01())

    def _w1_w2(self):
        return self._reduce(self._e01(), self._e03())

    def _w7_pending(self):
        return self._reduce(self._e11())

    def _w7_resolved(self):
        return self._reduce(self._e11(), self._e12())

    def test_a_uninitialized_search_initializes_and_produces_missions(self):
        snapshot = self._w1()
        result = sync_search_from_operational_state(None, snapshot, WALL)
        self.assertEqual(result["action"], "initialize")
        self.assertTrue(result["replanned"])
        record = result["record"]
        self.assertEqual(record["operational_state_version"], snapshot["state_version"])
        self.assertTrue(record["missions"])
        worksite = snapshot["worksites"][0]
        mission = record["missions"][0]
        self.assertEqual(mission["site_id"], W1)
        self.assertEqual(mission["worksite_id"], worksite["worksite_id"])
        self.assertEqual(worksite["worksite_id"], worksite_id(W1))
        self.assertNotEqual(mission["worksite_id"], "W1")
        self.assertNotIn("search-demo-", mission["worksite_id"])

    def test_b_same_state_version_does_not_replan(self):
        snapshot = self._w1()
        first = sync_search_from_operational_state(None, snapshot, WALL)
        with patch("search_planner.operational_sync.plan_search") as mocked, patch(
            "search_planner.operational_sync.initialize"
        ) as init, patch("search_planner.operational_sync.step") as stepped:
            second = sync_search_from_operational_state(first["record"], snapshot, LATER)
        self.assertEqual(second["action"], "noop")
        self.assertFalse(second["replanned"])
        self.assertIs(second["record"], first["record"])
        mocked.assert_not_called()
        init.assert_not_called()
        stepped.assert_not_called()
        self.assertEqual(decide_search_sync(first["record"], snapshot)["action"], "noop")

    def test_c_new_state_version_updates_and_replans(self):
        first_snap = self._w1()
        first = sync_search_from_operational_state(None, first_snap, WALL)
        second_snap = self._w1_w2()
        self.assertNotEqual(second_snap["state_version"], first_snap["state_version"])
        updated = sync_search_from_operational_state(first["record"], second_snap, LATER)
        self.assertEqual(updated["action"], "update")
        self.assertTrue(updated["replanned"])
        self.assertEqual(updated["record"]["operational_state_version"], second_snap["state_version"])
        planned_ids = {item["worksite_id"] for item in updated["record"]["plan"]["decisions"]}
        live_ids = {item["worksite_id"] for item in second_snap["worksites"]}
        self.assertEqual(live_ids, {worksite_id(W1), worksite_id(W2)})
        self.assertTrue(live_ids <= planned_ids | {
            item["entity_id"] for item in updated["record"]["plan"]["exclusions"] if item["entity_type"] == "worksite"
        })
        mission_ids = {item["worksite_id"] for item in updated["record"]["missions"]}
        self.assertTrue(live_ids & mission_ids)
        self.assertGreaterEqual(len(updated["record"]["missions"]), len(first["record"]["missions"]))

    def test_update_aligns_when_search_clock_is_ahead_of_belief(self):
        first = sync_search_from_operational_state(None, self._w1(), WALL)
        first["record"]["state"]["scenario_time"] = "2026-09-19T15:05:00Z"
        later = self._w1_w2()
        self.assertLess(later["scenario_time"], first["record"]["state"]["scenario_time"])
        updated = sync_search_from_operational_state(first["record"], later, "2026-09-19T15:05:01Z")
        self.assertEqual(updated["action"], "update")
        self.assertEqual(updated["record"]["operational_state_version"], later["state_version"])

    def test_d_resolved_worksite_is_no_longer_actionable(self):
        pending = self._w7_pending()
        first = sync_search_from_operational_state(None, pending, WALL)
        self.assertTrue(first["record"]["missions"])
        resolved = self._w7_resolved()
        worksite = next(item for item in resolved["worksites"] if item["site_id"] == W7)
        self.assertEqual(worksite["operational_status"], "resolved")
        updated = sync_search_from_operational_state(first["record"], resolved, LATER)
        planned = {item["worksite_id"] for item in updated["record"]["plan"]["decisions"]}
        self.assertNotIn(worksite["worksite_id"], planned)
        exclusions = [
            item for item in updated["record"]["plan"]["exclusions"]
            if item["entity_id"] == worksite["worksite_id"]
        ]
        self.assertTrue(any(item["reason_code"] == "WORKSITE_RESOLVED" for item in exclusions))

    def test_d_non_actionable_information_gap_is_excluded(self):
        snapshot = self._w1()
        worksite = snapshot["worksites"][0]
        features = dict(worksite.setdefault("metadata", {}).get("search_features") or {})
        features.update(uncertainty=0, evidence_conflict=0, potential_impact=0.6, criticality=0.78)
        worksite["metadata"]["search_features"] = features
        result = sync_search_from_operational_state(None, snapshot, WALL)
        planned = {item["worksite_id"] for item in result["record"]["plan"]["decisions"]}
        self.assertNotIn(worksite["worksite_id"], planned)
        self.assertTrue(any(
            item["entity_id"] == worksite["worksite_id"] and item["reason_code"] == "NO_INFORMATION_GAP"
            for item in result["record"]["plan"]["exclusions"]
        ))
        self.assertFalse(any(item["worksite_id"] == worksite["worksite_id"] for item in result["record"]["missions"]))

    def test_conflict_change_replans_with_upstream_search_features(self):
        first = sync_search_from_operational_state(None, self._w1(), WALL)
        changed = self._w1_w2()
        target = next(item for item in changed["worksites"] if item["site_id"] == W2)
        target["metadata"]["search_features"]["evidence_conflict"] = 1.0
        updated = sync_search_from_operational_state(first["record"], changed, LATER)
        self.assertEqual(updated["action"], "update")
        explanation = next(
            item for item in updated["record"]["plan"]["explanations"]
            if item["target_worksite_id"] == target["worksite_id"]
        )
        self.assertEqual(explanation["score_breakdown"]["evidence_conflict"], 1.0)
        self.assertIn("CONFLICTING_EVIDENCE", explanation["reason_codes"])

    def test_e_ids_remain_identical_to_operational_belief_state(self):
        snapshot = self._w1_w2()
        result = sync_search_from_operational_state(None, snapshot, WALL)
        live = {(item["site_id"], item["worksite_id"]) for item in snapshot["worksites"]}
        self.assertEqual(live, {(W1, worksite_id(W1)), (W2, worksite_id(W2))})
        for mission in result["record"]["missions"]:
            self.assertIn((mission["site_id"], mission["worksite_id"]), live)
        for decision in result["record"]["plan"]["decisions"]:
            self.assertIn(decision["worksite_id"], {item[1] for item in live})
        self.assertNotIn("W1", {item["worksite_id"] for item in snapshot["worksites"]})
        self.assertNotIn("search-demo-W3", {item["worksite_id"] for item in snapshot["worksites"]})

    def test_live_worksites_supply_search_features_without_new_formula(self):
        snapshot = planning_snapshot(self._w1())
        worksite = snapshot["worksites"][0]
        self.assertEqual(worksite["operational_status"], "pending")
        self.assertEqual(worksite["criticality"], 0)
        features, sources = extract_features(worksite)
        self.assertEqual(features["criticality"], 0.78)
        self.assertEqual(sources["criticality"], "worksite.metadata.search_features.criticality")
        self.assertEqual(features["evidence_conflict"], 0.0)
        self.assertEqual(sources["evidence_conflict"], "worksite.metadata.search_features.evidence_conflict")
        self.assertEqual(sources["potential_impact"], "victim_indication_lookup")
        self.assertEqual(sources["uncertainty"], "max_evidence_uncertainty_and_unknown_field_fraction")
        self.assertNotIn("potential_impact", worksite["metadata"]["search_features"])
        self.assertNotIn("uncertainty", worksite["metadata"]["search_features"])
        overridden = deepcopy(worksite)
        overridden["metadata"]["search_features"] = {
            "potential_impact": 0.85,
            "uncertainty": 0.8,
            "criticality": 0.6,
            "evidence_conflict": 0.53,
        }
        override_features, override_sources = extract_features(overridden)
        self.assertEqual(override_features["potential_impact"], 0.85)
        self.assertEqual(override_sources["potential_impact"], "worksite.metadata.search_features.potential_impact")

    def test_get_operational_snapshot_uses_injected_loader_not_search_demo(self):
        expected = {"state_version": "9", "worksites": [{"worksite_id": worksite_id(W1), "site_id": W1}]}
        loaded = get_operational_snapshot("session-1", loader=lambda sid: expected if sid == "session-1" else None)
        self.assertEqual(loaded["worksites"][0]["site_id"], W1)
        self.assertIsNone(get_operational_snapshot("session-1", loader=lambda _sid: None))
        self.assertIsNone(get_operational_snapshot(None))

    def test_get_operational_snapshot_reads_agent1_sqlite_table(self):
        snapshot = self._w1()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aftershock.sqlite"
            connection = sqlite3.connect(path)
            connection.execute(
                f"""CREATE TABLE {AGENT1_SNAPSHOT_TABLE} (
                    session_id TEXT PRIMARY KEY,
                    state_version TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                f"INSERT INTO {AGENT1_SNAPSHOT_TABLE} VALUES (?,?,?,?)",
                ("demo", snapshot["state_version"], json.dumps(snapshot), snapshot["updated_at"]),
            )
            connection.commit()
            connection.close()
            loaded = get_operational_snapshot("demo", sqlite_path=path)
            self.assertEqual(loaded["worksites"][0]["site_id"], W1)
            self.assertEqual(loaded["worksites"][0]["worksite_id"], worksite_id(W1))
            self.assertIsNone(get_operational_snapshot("missing", sqlite_path=path))

    def test_planning_snapshot_does_not_read_search_demo_fixtures(self):
        snapshot = self._w1()
        opened = []
        real = Path.read_text

        def wrapped(self, *args, **kwargs):
            opened.append(str(self))
            return real(self, *args, **kwargs)

        with patch.object(Path, "read_text", wrapped):
            planned = planning_snapshot(snapshot)
        self.assertTrue(any("caraballeda" in path for path in opened))
        self.assertFalse(any("search_demo" in path for path in opened))
        self.assertEqual(planned["worksites"][0]["site_id"], W1)
        self.assertTrue(any(item["resource_type"] == "drone_unit" for item in planned["resources"]))

    def test_missing_snapshot_stays_uninitialized(self):
        result = sync_search_from_operational_state(None, None, WALL)
        self.assertEqual(result["action"], "uninitialized")
        self.assertIsNone(result["record"])


if __name__ == "__main__":
    unittest.main()
