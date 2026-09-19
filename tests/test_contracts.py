"""Validate v2 contract examples and the radio scenario fixture pack."""

import copy
import json
import unittest
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, RefResolver


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
RADIO_FIXTURES = ROOT / "fixtures" / "radio" / "simulated_riverside"


EXAMPLE_SCHEMA_MAP = {
    "radio_observation.json": "raw_radio_input.schema.json",
    "social_observation.json": "raw_social_input.schema.json",
    "drone_observation.json": "drone_observation_input.schema.json",
    "structured_radio_observation.json": "observation.schema.json",
    "structured_social_observation.json": "observation.schema.json",
    "structured_drone_observation.json": "observation.schema.json",
    "resolved_location.json": "location_resolution.schema.json",
    "unresolved_location.json": "location_resolution.schema.json",
    "site_d3.json": "site.schema.json",
    "incident_d3_structural.json": "incident.schema.json",
    "incident_d3_trapped.json": "incident.schema.json",
    "evidence_item.json": "evidence_item.schema.json",
    "worksite_d3.json": "worksite.schema.json",
    "operational_belief_state.json": "operational_belief_state.schema.json",
    "search_task.json": "search_task.schema.json",
    "search_decision.json": "search.schema.json",
    "save_task.json": "save_task.schema.json",
    "save_decision.json": "save.schema.json",
    "route.json": "route.schema.json",
    "assignment.json": "assignment.schema.json",
    "reassignment_pending.json": "reassignment.schema.json",
    "reassignment_expired.json": "reassignment.schema.json",
    "reassignment_invalidated.json": "reassignment.schema.json",
    "human_decision.json": "human_decision.schema.json",
    "radio_finalize_request.json": "radio_finalize_request.schema.json",
    "radio_finalize_response.json": "radio_finalize_response.schema.json",
    "radio_extraction_result.json": "radio_extraction_result.schema.json",
    "radio_transmission.json": "radio_transmission.schema.json",
    "radio_session_snapshot.json": "radio_session_snapshot.schema.json",
    "dashboard_event.json": "dashboard_event.schema.json",
}

RADIO_FIXTURE_SCHEMA_MAP = {
    "raw_input.json": "raw_radio_input.schema.json",
    "finalize_request.json": "radio_finalize_request.schema.json",
    "finalize_response.json": "radio_finalize_response.schema.json",
    "extraction_result.json": "radio_extraction_result.schema.json",
    "at_least_three_extraction_result.json": "radio_extraction_result.schema.json",
    "routine_chatter_extraction_result.json": "radio_extraction_result.schema.json",
}

RADIO_TOP_LEVEL_EXAMPLES = {
    "radio_finalize_request.json": "radio_finalize_request.schema.json",
    "radio_finalize_response.json": "radio_finalize_response.schema.json",
    "radio_extraction_result.json": "radio_extraction_result.schema.json",
    "radio_transmission.json": "radio_transmission.schema.json",
    "radio_session_snapshot.json": "radio_session_snapshot.schema.json",
    "dashboard_event.json": "dashboard_event.schema.json",
}


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def parse_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def radio_semantic_errors(extraction_result):
    """Semantic checks that JSON Schema cannot express across numeric fields."""
    errors = []
    for index, observation in enumerate(extraction_result["observations"]):
        indication = observation["victim_indication"]
        indication_type = indication["type"]
        count_min = indication.get("count_min")
        count_max = indication.get("count_max")
        prefix = f"observations[{index}].victim_indication"

        if count_min is not None and count_max is not None and count_min > count_max:
            errors.append(f"{prefix} has count_min greater than count_max")
        if indication_type == "unknown" and (count_min is not None or count_max is not None):
            errors.append(f"{prefix} assigns a count to unknown victims")
        if indication_type in {"possible_multiple", "confirmed_multiple"}:
            known_counts = (count for count in (count_min, count_max) if count is not None)
            if any(count < 2 for count in known_counts):
                errors.append(f"{prefix} uses a count below two for multiple victims")

    return errors


class ContractExamplesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema_documents = {
            path.name: load_json(path) for path in CONTRACTS.glob("*.schema.json")
        }
        cls.schema_store = {}
        for name, document in cls.schema_documents.items():
            cls.schema_store[(CONTRACTS / name).as_uri()] = document
            if "$id" in document:
                cls.schema_store[document["$id"]] = document

    def validator_for(self, schema_name):
        schema_path = CONTRACTS / schema_name
        schema = self.schema_documents[schema_name]
        resolver = RefResolver(
            base_uri=schema_path.as_uri(),
            referrer=schema,
            store=self.schema_store,
        )
        return Draft202012Validator(
            schema,
            resolver=resolver,
            format_checker=FormatChecker(),
        )

    def assert_valid(self, instance, schema_name):
        errors = sorted(
            self.validator_for(schema_name).iter_errors(instance),
            key=lambda error: [str(part) for part in error.path],
        )
        self.assertEqual([], errors, msg="; ".join(error.message for error in errors))

    def test_example_mapping_covers_every_example_file(self):
        example_files = {path.name for path in (CONTRACTS / "examples").glob("*.json")}
        self.assertEqual(set(EXAMPLE_SCHEMA_MAP), example_files)

    def test_every_schema_is_a_valid_draft_2020_12_schema(self):
        for schema_path in CONTRACTS.glob("*.schema.json"):
            with self.subTest(schema=schema_path.name):
                Draft202012Validator.check_schema(load_json(schema_path))

    def test_every_example_validates_against_its_schema(self):
        for example_name, schema_name in EXAMPLE_SCHEMA_MAP.items():
            with self.subTest(example=example_name, schema=schema_name):
                instance = load_json(CONTRACTS / "examples" / example_name)
                self.assert_valid(instance, schema_name)

    def test_radio_fixture_payloads_validate(self):
        for fixture_name, schema_name in RADIO_FIXTURE_SCHEMA_MAP.items():
            with self.subTest(fixture=fixture_name, schema=schema_name):
                self.assert_valid(load_json(RADIO_FIXTURES / fixture_name), schema_name)

        observations = load_json(RADIO_FIXTURES / "structured_observations.json")
        for observation in observations["observations"]:
            with self.subTest(observation=observation["observation_id"]):
                self.assert_valid(observation, "observation.schema.json")

        resolutions = load_json(RADIO_FIXTURES / "location_resolutions.json")
        for resolution in resolutions["resolutions"]:
            with self.subTest(resolution=resolution["observation_id"]):
                self.assert_valid(resolution, "location_resolution.schema.json")

        dashboard_events = load_json(RADIO_FIXTURES / "dashboard_events.json")
        for event in dashboard_events["events"]:
            with self.subTest(event=event["event_id"]):
                self.assert_valid(event, "dashboard_event.schema.json")

    def test_radio_contracts_reject_unknown_top_level_fields(self):
        for example_name, schema_name in RADIO_TOP_LEVEL_EXAMPLES.items():
            with self.subTest(example=example_name):
                instance = load_json(CONTRACTS / "examples" / example_name)
                instance["unexpected"] = True
                self.assertFalse(self.validator_for(schema_name).is_valid(instance))

    def test_finalize_fixture_has_ordered_complete_segments(self):
        scenario = load_json(RADIO_FIXTURES / "scenario.json")
        raw_input = load_json(RADIO_FIXTURES / "raw_input.json")
        request = load_json(RADIO_FIXTURES / "finalize_request.json")
        response = load_json(RADIO_FIXTURES / "finalize_response.json")

        self.assertEqual(scenario["primary_transcript"], request["transcript"])
        self.assertEqual(request["transcript"], raw_input["transcript"])
        self.assertEqual(request["transmission_id"], response["transmission_id"])
        self.assertEqual(raw_input["input_id"], response["input_id"])
        self.assertLess(
            parse_timestamp(request["capture_started_at"]),
            parse_timestamp(request["capture_ended_at"]),
        )

        segments = request["segments"]
        self.assertEqual(list(range(len(segments))), [segment["sequence"] for segment in segments])
        self.assertEqual(request["transcript"], " ".join(segment["text"] for segment in segments))
        for previous, current in zip(segments, segments[1:]):
            self.assertLessEqual(previous["started_at_ms"], previous["ended_at_ms"])
            self.assertLessEqual(previous["ended_at_ms"], current["started_at_ms"])
        self.assertLessEqual(segments[-1]["started_at_ms"], segments[-1]["ended_at_ms"])

    def test_radio_extraction_preserves_uncertainty_and_support(self):
        result = load_json(RADIO_FIXTURES / "extraction_result.json")
        self.assertEqual([], radio_semantic_errors(result))
        by_type = {observation["incident_type"]: observation for observation in result["observations"]}

        structural = by_type["structural_damage"]
        self.assertEqual("reported_damage", structural["structural_hazard"])
        self.assertIn("partial collapse", structural["supporting_text"][0])

        victims = by_type["trapped_victims"]["victim_indication"]
        self.assertEqual("possible_multiple", victims["type"])
        self.assertIsNone(victims["count_min"])
        self.assertIsNone(victims["count_max"])

        access = by_type["access_blocked"]
        self.assertEqual("possibly_blocked", access["access_status"])
        self.assertIn("may be blocked", access["supporting_text"][0])

        forbidden_fields = {
            "site_id",
            "worksite_id",
            "resolved_site_id",
            "latitude",
            "longitude",
            "priority",
            "resource_assignment",
            "verification_status",
            "evidence_strength",
        }
        for observation in result["observations"]:
            self.assertEqual(set(), forbidden_fields.intersection(observation))
            self.assertEqual("Riverside Apartments", observation["location_text"])

    def test_at_least_three_retains_unknown_upper_bound(self):
        result = load_json(RADIO_FIXTURES / "at_least_three_extraction_result.json")
        self.assertEqual([], radio_semantic_errors(result))
        indication = result["observations"][0]["victim_indication"]
        self.assertEqual("possible_multiple", indication["type"])
        self.assertEqual(3, indication["count_min"])
        self.assertIsNone(indication["count_max"])

    def test_routine_chatter_has_no_actionable_observations(self):
        result = load_json(RADIO_FIXTURES / "routine_chatter_extraction_result.json")
        self.assertEqual("no_actionable_content", result["result_status"])
        self.assertEqual([], result["observations"])

    def test_invalid_semantic_output_cannot_pass_contract_boundary(self):
        result = load_json(RADIO_FIXTURES / "extraction_result.json")
        invented_identity = copy.deepcopy(result)
        invented_identity["observations"][0]["site_id"] = "invented-by-model"
        self.assertFalse(
            self.validator_for("radio_extraction_result.schema.json").is_valid(invented_identity)
        )

        impossible_counts = copy.deepcopy(result)
        indication = impossible_counts["observations"][1]["victim_indication"]
        indication["count_min"] = 5
        indication["count_max"] = 3
        self.assertNotEqual([], radio_semantic_errors(impossible_counts))

    def test_radio_state_conditionals_reject_incomplete_payloads(self):
        extraction = load_json(RADIO_FIXTURES / "extraction_result.json")
        extraction["observations"] = []
        self.assertFalse(
            self.validator_for("radio_extraction_result.schema.json").is_valid(extraction)
        )

        transmission = load_json(CONTRACTS / "examples" / "radio_transmission.json")
        transmission["processing_state"] = "resolved"
        self.assertFalse(self.validator_for("radio_transmission.schema.json").is_valid(transmission))

    def test_structured_fixture_adds_only_backend_provenance(self):
        semantic_result = load_json(RADIO_FIXTURES / "extraction_result.json")
        structured = load_json(RADIO_FIXTURES / "structured_observations.json")
        semantic_by_type = {
            observation["incident_type"]: observation
            for observation in semantic_result["observations"]
        }

        source_references = set()
        for observation in structured["observations"]:
            semantic = semantic_by_type[observation["incident_type"]]
            self.assertEqual("radio", observation["source_type"])
            self.assertEqual("unverified", observation["verification_status"])
            self.assertIsNone(observation["location"]["latitude"])
            self.assertIsNone(observation["location"]["longitude"])
            self.assertIsNone(observation["location"]["resolved_site_id"])
            self.assertEqual(semantic["location_text"], observation["location"]["raw_text"])
            self.assertEqual(semantic["victim_indication"], observation["victim_indication"])
            self.assertEqual(semantic["structural_hazard"], observation["structural_hazard"])
            self.assertEqual(semantic["access_status"], observation["access_status"])
            source_references.add(observation["raw_source_reference"]["value"])

        self.assertEqual({"raw-radio-demo-001"}, source_references)

    def test_resolver_fixture_uses_only_the_simulated_alias(self):
        scenario = load_json(RADIO_FIXTURES / "scenario.json")
        structured = load_json(RADIO_FIXTURES / "structured_observations.json")
        resolution_fixture = load_json(RADIO_FIXTURES / "location_resolutions.json")
        known_site = scenario["known_sites"][0]
        observation_ids = {
            observation["observation_id"] for observation in structured["observations"]
        }

        self.assertNotIn("coordinates", known_site)
        for resolution in resolution_fixture["resolutions"]:
            self.assertIn(resolution["observation_id"], observation_ids)
            self.assertEqual(known_site["site_id"], resolution["resolved_site_id"])
            self.assertIn(resolution["metadata"]["matched_alias"], known_site["aliases"])

    def test_dashboard_event_type_requires_matching_payload(self):
        fixture = load_json(RADIO_FIXTURES / "dashboard_events.json")
        event_ids = [event["event_id"] for event in fixture["events"]]
        self.assertEqual(len(event_ids), len(set(event_ids)))
        self.assertTrue(
            all(event["session_id"] == fixture["session_id"] for event in fixture["events"])
        )

        mismatched = copy.deepcopy(fixture["events"][0])
        mismatched["event_type"] = "radio.failed"
        self.assertFalse(self.validator_for("dashboard_event.schema.json").is_valid(mismatched))

    def test_every_dashboard_event_variant_accepts_only_its_typed_payload(self):
        extraction = load_json(RADIO_FIXTURES / "extraction_result.json")
        resolution = load_json(RADIO_FIXTURES / "location_resolutions.json")["resolutions"][0]
        unresolved = copy.deepcopy(resolution)
        unresolved.update(
            {
                "resolution_status": "unresolved",
                "resolved_site_id": None,
                "resolution_method": None,
                "resolution_score": None,
                "candidate_site_ids": [],
            }
        )
        payloads = {
            "radio.accepted": {
                "input_id": "raw-radio-demo-001",
                "processing_state": "accepted",
            },
            "radio.extraction_started": {
                "extraction_id": "extract-radio-demo-001",
                "processing_state": "extracting",
            },
            "radio.extraction_retrying": {
                "extraction_id": "extract-radio-demo-001",
                "attempt": 1,
                "max_attempts": 2,
                "processing_state": "retrying",
                "failure": {
                    "code": "llm_unavailable",
                    "message": "Extraction provider unavailable; retrying.",
                    "retryable": True,
                },
            },
            "radio.extracted": {"extraction_result": extraction},
            "radio.location_resolved": {
                "observation_id": resolution["observation_id"],
                "resolution": resolution,
            },
            "radio.location_unresolved": {
                "observation_id": unresolved["observation_id"],
                "resolution": unresolved,
            },
            "radio.failed": {
                "stage": "extraction",
                "failure": {
                    "code": "llm_unavailable",
                    "message": "Extraction provider unavailable.",
                    "retryable": False,
                },
            },
        }

        for index, (event_type, payload) in enumerate(payloads.items()):
            event = {
                "schema_version": "2.0",
                "event_id": f"event-variant-{index}",
                "session_id": "session-radio-demo-001",
                "transmission_id": "tx-radio-demo-001",
                "occurred_at": "2026-09-19T14:02:14Z",
                "event_type": event_type,
                "payload": payload,
            }
            with self.subTest(event_type=event_type):
                self.assert_valid(event, "dashboard_event.schema.json")


if __name__ == "__main__":
    unittest.main()
