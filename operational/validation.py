"""Validate operational payloads against canonical /contracts schemas."""

import json

from ingestion.validation import schema_documents, validator

from operational.errors import InvalidObservationError, OperationalError


def validate_payload(schema_name: str, value: dict) -> dict:
    json.dumps(value, allow_nan=False)
    try:
        validator(schema_name).validate(value)
    except Exception as exc:
        raise OperationalError(f"{schema_name}: {exc}") from exc
    return value


def validate_observation(observation: dict) -> dict:
    try:
        return validate_payload("observation.schema.json", observation)
    except OperationalError as exc:
        raise InvalidObservationError(str(exc)) from exc


def validate_location_resolution(value: dict) -> dict:
    return validate_payload("location_resolution.schema.json", value)


def validate_incident(value: dict) -> dict:
    return validate_payload("incident.schema.json", value)


def validate_evidence(value: dict) -> dict:
    return validate_payload("evidence_item.schema.json", value)


def validate_worksite(value: dict) -> dict:
    return validate_payload("worksite.schema.json", value)


def validate_site(value: dict) -> dict:
    return validate_payload("site.schema.json", value)


def validate_belief_state(value: dict) -> dict:
    return validate_payload("operational_belief_state.schema.json", value)
