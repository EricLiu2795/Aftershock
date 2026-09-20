"""JSON stdin/stdout bridge around the existing operational package.

The Fastify API owns per-session persistence. This process loads a serialized
OperationalState, applies StructuredObservation(s), and returns the next
record plus a planner-free snapshot. Fusion and location semantics stay in
operational.belief_state / LocationResolver / IncidentFusion.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import json
import sys

from operational.belief_state import OperationalState, apply_observation, apply_road_state, empty_state
from operational.errors import InvalidObservationError, OperationalError
from operational.incident_fusion import IncidentFusion
from operational.location_resolver import LocationResolver
from operational.site_registry import InMemorySiteRegistry, ScenarioPackSiteRegistry, SiteRegistry


ROOT = Path(__file__).resolve().parents[1]
CARABALLEDA_SITES = ROOT / "data" / "scenarios" / "caraballeda" / "active_sites.json"
CARABALLEDA_SCENARIOS = frozenset({"caraballeda", "caraballeda-v0"})
DEFAULT_SCENARIO_TIME = "2026-09-19T14:00:00Z"


def run(request: dict) -> dict:
    command = request.get("command") or "apply_many"
    scenario_id = request.get("scenario_id") or ""
    observations = _observations(request)
    scenario_time = (
        request.get("scenario_time")
        or _first_timestamp(observations)
        or DEFAULT_SCENARIO_TIME
    )
    state = deserialize_state(request.get("record"), scenario_time, scenario_id)
    registry = registry_for(scenario_id)
    resolver = LocationResolver()
    fusion = IncidentFusion()
    _seed_caraballeda_roads(state, scenario_id)

    if command == "snapshot":
        return _result(state)
    if command == "apply_road":
        update = request.get("road_state")
        if not isinstance(update, dict):
            raise ValueError("road_state is required")
        state = apply_road_state(state, update, scenario_time=state.belief["scenario_time"])
        return _result(state)
    if command not in {"apply", "apply_many"}:
        raise ValueError("Unknown operational command")
    if command == "apply" and not observations:
        raise ValueError("observation is required")

    for observation in observations:
        payload = deepcopy(observation)
        _clear_runtime_overrides(payload)
        state = apply_observation(
            state,
            payload,
            registry,
            resolver=resolver,
            fusion=fusion,
        )
    return _result(state)


def snapshot_from_state(state: OperationalState) -> dict:
    belief = state.belief
    return {
        "schema_version": belief.get("schema_version") or "2.1",
        "state_version": belief["state_version"],
        "scenario_time": belief.get("scenario_time"),
        "updated_at": belief.get("updated_at"),
        "incidents": deepcopy(belief.get("incidents") or []),
        "worksites": deepcopy(belief.get("worksites") or []),
        "sites": deepcopy(belief.get("sites") or []),
        "resources": deepcopy(belief.get("resources") or []),
        "road_states": deepcopy(belief.get("road_states") or []),
        "unresolved_observations": [
            {
                "observation_id": item["observation"]["observation_id"],
                "observation": deepcopy(item["observation"]),
                "location_resolution": deepcopy(item.get("location_resolution") or {}),
            }
            for item in state.unresolved
        ],
    }


def serialize_state(state: OperationalState) -> dict:
    return {
        "belief": deepcopy(state.belief),
        "unresolved": deepcopy(state.unresolved),
        "evidence": deepcopy(state.evidence),
        "processed_observation_ids": sorted(state.processed_observation_ids),
        "fused_observation_ids": sorted(state.fused_observation_ids),
        "location_resolutions": deepcopy(state.location_resolutions),
        "last_trace": deepcopy(state.last_trace),
    }


def deserialize_state(record: dict | None, scenario_time: str, scenario_id: str = "") -> OperationalState:
    if not record:
        state = empty_state(scenario_time)
        _seed_caraballeda_roads(state, scenario_id)
        return state
    if not isinstance(record, dict) or not isinstance(record.get("belief"), dict):
        raise ValueError("Operational record is invalid")
    return OperationalState(
        belief=deepcopy(record["belief"]),
        unresolved=deepcopy(record.get("unresolved") or []),
        evidence=deepcopy(record.get("evidence") or {}),
        processed_observation_ids=set(record.get("processed_observation_ids") or []),
        fused_observation_ids=set(record.get("fused_observation_ids") or []),
        location_resolutions=deepcopy(record.get("location_resolutions") or {}),
        last_trace=deepcopy(record.get("last_trace") or {}),
    )


@lru_cache(maxsize=8)
def registry_for(scenario_id: str) -> SiteRegistry:
    if scenario_id in CARABALLEDA_SCENARIOS:
        return ScenarioPackSiteRegistry(CARABALLEDA_SITES)
    return InMemorySiteRegistry()


def _result(state: OperationalState) -> dict:
    return {
        "record": serialize_state(state),
        "snapshot": snapshot_from_state(state),
        "trace": deepcopy(state.last_trace),
    }


def _observations(request: dict) -> list[dict]:
    if request.get("observations") is not None:
        items = request["observations"]
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("observations must be an array of objects")
        return items
    observation = request.get("observation")
    if observation is None:
        return []
    if not isinstance(observation, dict):
        raise ValueError("observation must be an object")
    return [observation]


def _first_timestamp(observations: list[dict]) -> str | None:
    for item in observations:
        timestamp = item.get("timestamp")
        if isinstance(timestamp, str) and timestamp.strip():
            return timestamp
    return None


def _seed_caraballeda_roads(state: OperationalState, scenario_id: str) -> None:
    """Copy pack initial_road_states into an empty Caraballeda belief.

    This is routing baseline configuration, not hidden incident truth. Save and
    Search then share the same canonical `road_states` collection. Seeding does
    not advance `state_version`.
    """
    if scenario_id not in CARABALLEDA_SCENARIOS:
        return
    if state.belief.get("road_states"):
        return
    path = ROOT / "data" / "scenarios" / "caraballeda" / "scenario.json"
    if not path.exists():
        return
    pack = json.loads(path.read_text(encoding="utf-8"))
    state.belief["road_states"] = deepcopy(pack.get("initial_road_states") or [])


def _clear_runtime_overrides(observation: dict) -> None:
    """JS gazetteer / display resolution must not become operational knowledge."""
    location = observation.get("location")
    if isinstance(location, dict):
        location["resolved_site_id"] = None
    metadata = observation.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("trusted_site_id", None)
        metadata.pop("canonical_site_id", None)
        metadata.pop("intended_referent", None)


if __name__ == "__main__":
    try:
        print(json.dumps(run(json.load(sys.stdin)), allow_nan=False))
    except (ValueError, KeyError, TypeError, InvalidObservationError, OperationalError) as exc:
        print(json.dumps({"error": str(exc)}))
    except Exception:
        print(json.dumps({"error": "Operational bridge failed"}))
        raise SystemExit(1)
