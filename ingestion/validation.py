"""Read canonical schemas locally; never fetch $refs over the network."""

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"

# Semantic fields the model may emit. Identity, provenance, and verification
# are stamped by application code after Structured Outputs returns.
PROVIDER_OBSERVATION_FIELDS = (
    "location", "incident_type", "victim_indication", "structural_hazard", "access_status",
)


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


def _close_objects(node):
    if not isinstance(node, dict):
        return
    if node.get("type") == "object" and "properties" in node:
        node["required"] = list(node["properties"])
        node["additionalProperties"] = False
    for child in node.get("properties", {}).values():
        _close_objects(child)
    if "items" in node:
        _close_objects(node["items"])
    for key in ("anyOf", "oneOf"):
        for child in node.get(key, []):
            _close_objects(child)


def openai_strict_schema(node):
    """Rewrite inlined canonical JSON Schema for OpenAI Structured Outputs.

    `/contracts` stays the source of truth. This only adapts constructs the
    provider subset cannot express: `const` becomes a single-value `enum`, and
    `null` is kept on `type` unions rather than inside `enum` arrays.
    """
    if isinstance(node, list):
        return [openai_strict_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    rewritten = {}
    for key, value in node.items():
        if key == "const":
            rewritten.setdefault("enum", [value])
            continue
        rewritten[key] = openai_strict_schema(value)
    enum_values = rewritten.get("enum")
    if isinstance(enum_values, list):
        rewritten["enum"] = [item for item in enum_values if item is not None]
        if not rewritten["enum"]:
            rewritten.pop("enum")
    return rewritten


def extraction_schema():
    """Provider wrapper derived at runtime from the canonical observation schema.

    Native structured outputs require a closed top-level object. Identity fields
    required by the contract are omitted here and stamped after parse.
    Every observation accepted here is still validated against the full contract.
    """
    observation = expanded_observation_schema()
    props = observation["properties"]
    observation["properties"] = {name: props[name] for name in PROVIDER_OBSERVATION_FIELDS}
    observation["required"] = list(PROVIDER_OBSERVATION_FIELDS)
    location = observation["properties"]["location"]
    location["properties"] = {key: value for key, value in location["properties"].items()
                              if key in {"latitude", "longitude", "raw_text", "accuracy"}}
    # Text ingestion never asks the model to provide coordinates. Provider
    # event centroids remain source metadata, not precise rescue locations.
    for name in ("latitude", "longitude"):
        location["properties"][name] = {"type": "null"}
    _close_objects(observation)
    wrapper = {"type": "object", "additionalProperties": False,
               "required": ["observations"], "properties": {
                   "observations": {"type": "array", "maxItems": 32, "items": observation}}}
    return openai_strict_schema(wrapper)
