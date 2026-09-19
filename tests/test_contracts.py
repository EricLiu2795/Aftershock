"""Validate every v2 contract example against its canonical schema."""

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, RefResolver


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"


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
}


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class ContractExamplesTest(unittest.TestCase):
    def test_example_mapping_covers_every_example_file(self):
        example_files = {path.name for path in (CONTRACTS / "examples").glob("*.json")}
        self.assertEqual(set(EXAMPLE_SCHEMA_MAP), example_files)

    def test_every_schema_is_a_valid_draft_2020_12_schema(self):
        for schema_path in CONTRACTS.glob("*.schema.json"):
            with self.subTest(schema=schema_path.name):
                Draft202012Validator.check_schema(load_json(schema_path))

    def test_every_example_validates_against_its_schema(self):
        schema_documents = {
            path.name: load_json(path) for path in CONTRACTS.glob("*.schema.json")
        }
        schema_store = {
            path.as_uri(): document
            for path, document in (
                (CONTRACTS / name, document) for name, document in schema_documents.items()
            )
        }

        for example_name, schema_name in EXAMPLE_SCHEMA_MAP.items():
            with self.subTest(example=example_name, schema=schema_name):
                schema_path = CONTRACTS / schema_name
                schema = schema_documents[schema_name]
                resolver = RefResolver(
                    base_uri=schema_path.as_uri(),
                    referrer=schema,
                    store=schema_store,
                )
                validator = Draft202012Validator(
                    schema,
                    resolver=resolver,
                    format_checker=FormatChecker(),
                )
                errors = sorted(
                    validator.iter_errors(load_json(CONTRACTS / "examples" / example_name)),
                    key=lambda error: list(error.path),
                )
                self.assertEqual(
                    [],
                    errors,
                    msg="; ".join(error.message for error in errors),
                )


if __name__ == "__main__":
    unittest.main()
