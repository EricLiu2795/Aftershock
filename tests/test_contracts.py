"""Validate every checked-in contract example against its canonical schema."""

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, RefResolver


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"


EXAMPLE_SCHEMA_MAP = {
    "radio_observation.json": "observation.schema.json",
    "social_observation.json": "observation.schema.json",
    "drone_observation.json": "observation.schema.json",
    "worksite_d3.json": "worksite.schema.json",
    "search_decision.json": "search.schema.json",
    "save_decision.json": "save.schema.json",
    "assignment.json": "assignment.schema.json",
    "reassignment_pending.json": "reassignment.schema.json",
    "reassignment_expired.json": "reassignment.schema.json",
    "reassignment_invalidated.json": "reassignment.schema.json",
}


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class ContractExamplesTest(unittest.TestCase):
    def test_example_mapping_covers_every_example_file(self):
        example_files = {
            path.name for path in (CONTRACTS / "examples").glob("*.json")
        }
        self.assertEqual(set(EXAMPLE_SCHEMA_MAP), example_files)

    def test_every_example_validates_against_its_schema(self):
        common = load_json(CONTRACTS / "common.schema.json")
        common_uri = (CONTRACTS / "common.schema.json").as_uri()

        for example_name, schema_name in EXAMPLE_SCHEMA_MAP.items():
            with self.subTest(example=example_name, schema=schema_name):
                schema_path = CONTRACTS / schema_name
                schema = load_json(schema_path)
                resolver = RefResolver(
                    base_uri=schema_path.as_uri(),
                    referrer=schema,
                    store={common_uri: common},
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
