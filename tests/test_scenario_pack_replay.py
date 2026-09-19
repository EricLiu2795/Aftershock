"""Caraballeda pack live replay: observable payloads, provenance, order, and IDs."""

import json
import unittest

from ingestion.dashboard import collect
from ingestion.normalizer import AIInformationNormalizer, IngestionPipeline
from ingestion.providers import FixtureExtractionModel
from ingestion.scenario_pack import (
    DEMO_STORY,
    FORBIDDEN_SITE_IDS,
    ScenarioPackSocialAdapter,
    demo_runtime_payloads,
    load_events,
    payload_contains_forbidden,
    runtime_source_id,
    story_steps,
    to_normalized,
)
from ingestion.config import ROOT


PACK_EVENTS = ROOT / "data/scenarios/caraballeda/scenario_events.json"


def pack_event(event_id):
    by_id, _ = load_events()
    return by_id[event_id]


class ScenarioPackReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.by_id, cls.events = load_events()
        cls.payloads = demo_runtime_payloads()
        cls.model = FixtureExtractionModel(ROOT / "data/ingestion_ai_responses.json")

    def test_demo_story_order_is_deterministic(self):
        included = [step for step in story_steps() if not step["omitted"]]
        self.assertEqual([step["event_id"] for step in included], ["E01", "E02", "E11"])
        self.assertEqual([step["delay_ms"] for step in included], [0, 2000, 6000])
        self.assertEqual([step["source_type"] for step in included], ["social", "radio", "social"])
        self.assertEqual([item["event_id"] for item in self.payloads], ["E01", "E02", "E11"])
        omitted = [step for step in story_steps() if step["omitted"]]
        self.assertEqual(omitted, [{
            "event_id": "E12",
            "source_type": "drone",
            "delay_ms": None,
            "omitted": True,
            "reason": "drone_verification",
        }])

    def test_emits_real_scenario_pack_observable_text(self):
        e01 = pack_event("E01")["raw_input"]
        e02 = pack_event("E02")["raw_input"]
        e11 = pack_event("E11")["raw_input"]
        by_id = {item["event_id"]: item for item in self.payloads}
        self.assertEqual(by_id["E01"]["raw_input"]["text"], e01["text"])
        self.assertEqual(by_id["E02"]["raw_input"]["transcript"], e02["transcript"])
        self.assertEqual(by_id["E11"]["raw_input"]["text"], e11["text"])
        self.assertIn("Residencias Caribe", by_id["E01"]["raw_input"]["text"])
        self.assertIn("Residencias Caribe", by_id["E02"]["raw_input"]["transcript"])
        self.assertIn("Colegio Tanaguarenas", by_id["E11"]["raw_input"]["text"])
        self.assertNotIn("Residencias Las Anclas", json.dumps(self.payloads))
        self.assertNotIn("Riverside Apartments", json.dumps(self.payloads))
        self.assertNotIn("Plaza Bolivar", json.dumps(self.payloads))

        reports = collect(DEMO_STORY)
        texts = [report["text"] for report in reports]
        self.assertEqual(texts, [e01["text"], e11["text"]])

    def test_runtime_payload_has_no_intended_referent_or_ground_truth(self):
        for payload in self.payloads:
            self.assertIsNone(payload_contains_forbidden(payload))
            blob = json.dumps(payload)
            self.assertNotIn("intended_referent", blob)
            self.assertNotIn("hidden_truth_linkage", blob)
            self.assertNotIn("expected_downstream_interpretation", blob)
            self.assertNotIn("scenario_ground_truth", blob)
            self.assertNotIn("verification_summary", blob)
            for site_id in FORBIDDEN_SITE_IDS:
                self.assertNotIn(site_id, blob)
            self.assertNotIn("resolved_site_id", json.dumps(payload["raw_input"]))

        reports = collect(DEMO_STORY)
        blob = json.dumps(reports)
        self.assertNotIn("intended_referent", blob)
        self.assertNotIn("hidden_truth_linkage", blob)
        self.assertNotIn("scenario_ground_truth", blob)
        for site_id in FORBIDDEN_SITE_IDS:
            self.assertNotIn(site_id, blob)

    def test_e01_and_e02_provenance_is_independent(self):
        e01, e02, e11 = self.payloads
        self.assertEqual(e01["source_type"], "social")
        self.assertEqual(e02["source_type"], "radio")
        self.assertEqual(e01["source_id"], "demo-author-e01")
        self.assertEqual(e02["source_id"], "rescue-alpha")
        self.assertEqual(e11["source_id"], "demo-author-e11")
        self.assertNotEqual(e01["source_id"], e02["source_id"])
        self.assertNotEqual(e01["source_id"], e11["source_id"])
        self.assertNotEqual(e01["source_id"], "replay")
        self.assertNotEqual(e02["source_id"], "replay")
        self.assertEqual(runtime_source_id(pack_event("E01")), "demo-author-e01")
        self.assertEqual(runtime_source_id(pack_event("E02")), "rescue-alpha")

        pipeline = IngestionPipeline(AIInformationNormalizer(self.model))
        social_obs = pipeline.ingest(to_normalized(pack_event("E01")))
        self.assertTrue(social_obs)
        self.assertTrue(all(item["source_type"] == "social" for item in social_obs))
        self.assertTrue(all(item["source_id"] == "demo-author-e01" for item in social_obs))
        radio = to_normalized(pack_event("E02"))
        self.assertEqual(radio.source_type, "radio")
        self.assertEqual(radio.source_id, "rescue-alpha")
        self.assertNotEqual(social_obs[0]["source_id"], radio.source_id)

    def test_replay_ids_are_stable_and_deduped(self):
        first = collect(DEMO_STORY)
        second = collect(DEMO_STORY)
        self.assertEqual([report["id"] for report in first], [report["id"] for report in second])
        self.assertEqual(2, len(first))
        self.assertEqual([], collect(DEMO_STORY, known_ids=[report["id"] for report in first]))
        only_e01 = collect(DEMO_STORY, event_ids=["E01"])
        self.assertEqual(1, len(only_e01))
        self.assertEqual(only_e01[0]["id"], first[0]["id"])
        adapter = ScenarioPackSocialAdapter()
        self.assertEqual(
            [item.external_id for item in adapter.items],
            ["scenario-post-e01", "scenario-post-e11"],
        )
        self.assertEqual(
            [item.identity for item in adapter.items],
            [item.identity for item in ScenarioPackSocialAdapter().items],
        )

    def test_does_not_replay_drone_e12_as_social_or_radio(self):
        self.assertNotIn("E12", [item["event_id"] for item in self.payloads])
        reports = collect(DEMO_STORY)
        self.assertTrue(all(report["source"] == "social" for report in reports))
        self.assertTrue(all(report["mode"] == "scenario_pack" for report in reports))
        self.assertFalse(any("drone" in report["source"] for report in reports))


if __name__ == "__main__":
    unittest.main()
