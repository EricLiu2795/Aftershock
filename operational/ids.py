"""Deterministic application IDs. The model never authors these values."""

from datetime import datetime, timezone
from typing import Iterable
import re
import unicodedata


def schema_version():
    from operational.validation import schema_documents

    return schema_documents()["common.schema.json"]["$defs"]["schemaVersion"]["const"]


def slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.strip().replace(":", "-")
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "id"


def incident_id(site_id: str, incident_type: str, first_observation_id: str) -> str:
    """Stable Incident ID from site, type, and the first contributing observation."""
    return f"incident-{slug(site_id)}-{slug(incident_type)}-{slug(first_observation_id)}"


def worksite_id(site_id: str) -> str:
    """One Worksite per canonical Site. Not an Incident ID and not a display name."""
    if site_id.startswith("site-"):
        return "worksite-" + site_id[len("site-"):]
    return "worksite-" + slug(site_id)


def evidence_id(observation_id: str) -> str:
    return f"evidence-{slug(observation_id)}"


def parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be nonempty text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def max_time(*values: str) -> str:
    return format_time(max(parse_time(item) for item in values))


def next_state_version(current: str) -> str:
    """Increment a numeric or `state-N` version string. Never invent UUIDs."""
    if current.isdigit():
        return str(int(current) + 1)
    match = re.fullmatch(r"(.*?)(\d+)", current)
    if match:
        prefix, number = match.groups()
        return f"{prefix}{int(number) + 1}"
    return f"{current}-1"


def unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
