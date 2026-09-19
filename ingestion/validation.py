"""Read canonical schemas locally; never fetch $refs over the network."""

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


@lru_cache(maxsize=1)
def schema_documents():
    return {p.name: json.loads(p.read_text(encoding="utf-8"))
            for p in CONTRACTS.glob("*.schema.json")}


@lru_cache(maxsize=None)
def validator(name="observation.schema.json"):
    docs = schema_documents()
    registry = Registry().with_resources(
        (doc["$id"], Resource.from_contents(doc)) for doc in docs.values()
    )
    return Draft202012Validator(docs[name], registry=registry, format_checker=FormatChecker())


def validate_structured_observation(observation):
    # jsonschema alone accepts Python NaN as a number; forbid non-JSON values.
    json.dumps(observation, allow_nan=False)
    validator().validate(observation)


def expanded_observation_schema():
    """Inline canonical local refs for provider portability, without a schema copy."""
    docs = schema_documents()

    def expand(node, filename):
        if isinstance(node, list):
            return [expand(item, filename) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            filepart, _, fragment = node["$ref"].partition("#")
            target_file = filepart or filename
            target = docs[target_file]
            for part in fragment.strip("/").split("/") if fragment else []:
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return expand(target, target_file)
        return {key: expand(value, filename) for key, value in node.items()
                if key not in ("$id", "$schema", "description", "title")}

    return expand(deepcopy(docs["observation.schema.json"]), "observation.schema.json")


def extraction_schema():
    """Narrow canonical output to facts; metadata/provenance is added by code.

    Native structured outputs require closed objects with all fields required.
    Select existing canonical fields rather than changing their optional types.
    Every observation accepted here is still valid against the full contract.
    """
    observation = expanded_observation_schema()
    props = observation["properties"]
    selected = set(observation["required"]) | {"source_id"}
    observation["properties"] = {k: v for k, v in props.items() if k in selected}
    location = props["location"]
    location["properties"] = {k: v for k, v in location["properties"].items()
                              if k in {"latitude", "longitude", "raw_text", "accuracy"}}
    # Text ingestion never asks the model to provide coordinates. Provider
    # event centroids remain source metadata, not precise rescue locations.
    for name in ("latitude", "longitude"):
        location["properties"][name] = {"type": "null"}

    def close(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "object":
            node["required"] = list(node["properties"])
            node["additionalProperties"] = False
        for child in node.get("properties", {}).values():
            close(child)
    close(observation)
    return {"type": "object", "additionalProperties": False,
            "required": ["observations"], "properties": {
                "observations": {"type": "array", "maxItems": 32, "items": observation}}}
