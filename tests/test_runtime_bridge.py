"""Runtime stdin/stdout bridge: session record → apply_observation → snapshot."""

from pathlib import Path
import json
import subprocess
import sys
import unittest

from tests.operational_fixtures import T0, make_observation


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data" / "scenarios" / "caraballeda"
SCENARIO_EVENTS = PACK / "scenario_events.json"
W1 = "osm:way:1533952351"
PYTHON = sys.executable


def load_events():
    return json.loads(SCENARIO_EVENTS.read_text(encoding="utf-8"))


def event_by_id(events, event_id):
    return next(item for item in events["events"] if item["event_id"] == event_id)


def invoke(payload):
    proc = subprocess.run(
        [PYTHON, "-m", "operational.runtime_bridge"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


def e01_observation(events):
    event = event_by_id(events, "E01")
    return make_observation(
        "obs-e01",
        timestamp=event["raw_input"]["timestamp"],
        source_type="social",
        source_id=event["source_id"],
        author_id=event["raw_input"]["author_id"],
        raw_text=event["raw_input"]["text"],
        incident_type="trapped_victims",
        victim_type="possible_multiple",
    )


def e02_observation(events):
    event = event_by_id(events, "E02")
    return make_observation(
        "obs-e02",
        timestamp=event["raw_input"]["timestamp"],
        source_type="radio",
        source_id=event["source_id"],
        raw_text=event["raw_input"]["transcript"],
        incident_type="trapped_victims",
        victim_type="confirmed_multiple",
        count_min=4,
        structural_hazard="major_damage",
        metadata={"speaker_id": event["raw_input"]["speaker_id"]},
    )


class RuntimeBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = load_events()

    def test_a_first_w1_observation_creates_worksite(self):
        result = invoke({
            "command": "apply",
            "scenario_id": "caraballeda",
            "record": None,
            "observation": e01_observation(self.events),
        })
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["state_version"], "1")
        self.assertEqual(len(snapshot["worksites"]), 1)
        self.assertEqual(snapshot["worksites"][0]["site_id"], W1)
        self.assertEqual(snapshot["incidents"][0]["site_id"], W1)
        self.assertEqual(result["trace"]["resolved_site_id"], W1)
        self.assertNotIn("intended_referent", result["trace"])
        self.assertNotIn("resources", snapshot)
        self.assertNotIn("pending_reassignment_proposals", snapshot)
        self.assertNotIn("active_routes", snapshot)

    def test_b_independent_radio_corroborates_same_w1_worksite(self):
        first = invoke({
            "command": "apply",
            "scenario_id": "caraballeda",
            "observation": e01_observation(self.events),
        })
        second = invoke({
            "command": "apply",
            "scenario_id": "caraballeda",
            "record": first["record"],
            "observation": e02_observation(self.events),
        })
        snapshot = second["snapshot"]
        self.assertGreater(int(snapshot["state_version"]), int(first["snapshot"]["state_version"]))
        self.assertEqual(len(snapshot["worksites"]), 1)
        self.assertEqual(snapshot["worksites"][0]["site_id"], W1)
        trapped = [item for item in snapshot["incidents"] if item["incident_type"] == "trapped_victims"]
        self.assertEqual(len(trapped), 1)
        self.assertEqual(trapped[0]["site_id"], W1)
        self.assertEqual(trapped[0]["verification_status"], "corroborated")
        self.assertFalse(trapped[0]["metadata"]["evidence_conflict"])
        self.assertEqual(set(trapped[0]["observation_ids"]), {"obs-e01", "obs-e02"})

    def test_c_independent_records_do_not_share_state(self):
        left = invoke({
            "command": "apply",
            "scenario_id": "caraballeda",
            "observation": e01_observation(self.events),
        })
        right = invoke({
            "command": "snapshot",
            "scenario_id": "caraballeda",
            "record": None,
        })
        self.assertEqual(left["snapshot"]["state_version"], "1")
        self.assertEqual(right["snapshot"]["state_version"], "0")
        self.assertEqual(right["snapshot"]["worksites"], [])
        self.assertEqual(right["snapshot"]["incidents"], [])

    def test_d_reprocessing_same_observation_is_idempotent(self):
        observation = e01_observation(self.events)
        first = invoke({
            "command": "apply",
            "scenario_id": "caraballeda",
            "observation": observation,
        })
        second = invoke({
            "command": "apply",
            "scenario_id": "caraballeda",
            "record": first["record"],
            "observation": observation,
        })
        self.assertEqual(second["snapshot"]["state_version"], first["snapshot"]["state_version"])
        self.assertFalse(second["trace"]["changed"])
        self.assertEqual(second["trace"]["reason"], "duplicate_observation")
        self.assertEqual(
            second["snapshot"]["incidents"][0]["observation_ids"],
            first["snapshot"]["incidents"][0]["observation_ids"],
        )
        self.assertEqual(len(second["record"]["processed_observation_ids"]), 1)

    def test_plain_caribe_stays_ambiguous_and_js_site_id_is_ignored(self):
        observation = make_observation(
            "obs-plain-caribe",
            timestamp=T0,
            source_type="social",
            source_id="replay",
            author_id="plain-author",
            raw_text="Residencias Caribe",
            incident_type="trapped_victims",
            victim_type="possible_multiple",
            resolved_site_id="osm:way:797152586",
        )
        result = invoke({
            "command": "apply",
            "scenario_id": "caraballeda",
            "observation": observation,
        })
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["worksites"], [])
        self.assertEqual(snapshot["incidents"], [])
        self.assertEqual(len(snapshot["unresolved_observations"]), 1)
        resolution = snapshot["unresolved_observations"][0]["location_resolution"]
        self.assertEqual(resolution["resolution_status"], "ambiguous")
        self.assertEqual(set(resolution["candidate_site_ids"]), {W1, "osm:way:1533952352"})
        self.assertIsNone(result["trace"]["resolved_site_id"])
