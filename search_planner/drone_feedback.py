"""Turn a completed demo drone scan into fusion-ready StructuredObservations.

Trusted verification is an authored completed scan (DemoVerification revealed an
event and media after the site scan finished). Sensor type alone never upgrades
a claim. Orchestration keys such as intended_referent and mission site_id are
stripped before the observation reaches OperationalBeliefState.
"""
from copy import deepcopy
from geograph.drone import normalize_drone
from operational.validation import validate_observation


# Producer-only bookkeeping. Never copied onto operational StructuredObservation.
ORCHESTRATION_KEYS = {
    "intended_referent", "hidden_truth_linkage", "expected_downstream_interpretation",
    "hidden_from_operational_belief_state", "verification_summary", "verification_assets",
    "trusted_site_id", "canonical_site_id", "worksite_key", "planner_hint", "frontend_hint",
    "backend_hint", "scenario_event_id", "scenario_original_input_id",
    "scenario_original_timestamp", "scenario_original_drone_id", "mission_id",
    "raw_report",
}


def is_authored_completed_demo_scan(report):
    """True only after DemoVerification published an authored scan with revealed media."""
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    if not metadata.get("scenario_event_id"):
        return False
    assets = metadata.get("verification_assets")
    if not isinstance(assets, list) or not assets:
        return False
    return any(
        isinstance(asset, dict)
        and asset.get("file_status") in {"present", "available"}
        and not asset.get("placeholder")
        for asset in assets
    )


def operational_observations_from_report(report, *, trusted=None):
    """Normalize a drone report into resolver-safe operational observations.

    `trusted=True` is reserved for authored completed demo scans. Callers that
    omit it use `is_authored_completed_demo_scan`. Manual or sensor-only reports
    stay `verification_status=unverified`.
    """
    if trusted is None:
        trusted = is_authored_completed_demo_scan(report)
    cleaned = _operational_report(report)
    observations = []
    for item in normalize_drone(cleaned):
        observation = deepcopy(item)
        observation["location"] = _observable_location(observation.get("location") or {})
        observation["verification_status"] = "verified" if trusted else "unverified"
        observation["metadata"] = _operational_metadata(
            observation.get("metadata") or {},
            report,
            trusted=trusted,
        )
        blob = _json_blob(observation)
        for leaked in ("intended_referent", "hidden_truth_linkage", "expected_downstream_interpretation"):
            if leaked in blob:
                raise ValueError(f"Operational drone observation leaked {leaked}")
        observations.append(validate_observation(observation))
    return observations


def operational_observations_from_missions(missions):
    """Stable, de-duplicated operational observations from published scan events.

    Search does not apply these. The API sends them to Agent 1's
    `applySessionObservations` against `operational_belief_sessions`.
    """
    seen = set()
    result = []
    for mission in missions or []:
        for event in mission.get("events") or []:
            if event.get("event_type") != "DRONE_REPORT_READY":
                continue
            report = (event.get("payload") or {}).get("drone_input")
            if not isinstance(report, dict):
                continue
            for observation in operational_observations_from_report(report):
                observation_id = observation["observation_id"]
                if observation_id in seen:
                    continue
                seen.add(observation_id)
                result.append(observation)
    return result


def _operational_report(report):
    cleaned = deepcopy(report)
    cleaned["location"] = _observable_location(cleaned.get("location") or {})
    metadata = cleaned.get("metadata") if isinstance(cleaned.get("metadata"), dict) else {}
    cleaned["metadata"] = _strip_orchestration(metadata)
    return cleaned


def _observable_location(location):
    return {
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "raw_text": location.get("raw_text"),
        "label": location.get("label"),
        "accuracy": location.get("accuracy"),
        "resolved_site_id": None,
    }


def _strip_orchestration(value):
    if isinstance(value, dict):
        return {key: _strip_orchestration(item) for key, item in value.items() if key not in ORCHESTRATION_KEYS}
    if isinstance(value, list):
        return [_strip_orchestration(item) for item in value]
    return deepcopy(value)


def _operational_metadata(metadata, report, *, trusted):
    cleaned = _strip_orchestration(metadata)
    cleaned.pop("raw_report", None)
    cleaned["bypassed_llm"] = True
    cleaned["sensor_type"] = cleaned.get("sensor_type") or "infrared"
    cleaned["authored_demo_verification"] = bool(trusted)
    if report.get("imagery_reference"):
        cleaned["imagery_reference"] = report["imagery_reference"]
    return cleaned


def _json_blob(value):
    import json
    return json.dumps(value, default=str)
