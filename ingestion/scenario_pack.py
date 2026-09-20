"""Observable-only Caraballeda Scenario Pack replay.

Runtime payloads are the pack's contract-valid raw_input objects. Hidden
simulator fields stay in scenario_events.json and are never copied into
ingestion or session input.
"""

from copy import deepcopy
from pathlib import Path
import json

from ingestion.config import ROOT
from ingestion.models import NormalizedRawInput
from ingestion.validation import validator

PACK_EVENTS = ROOT / "data/scenarios/caraballeda/scenario_events.json"
DEMO_STORY = "caraballeda-demo"

HIDDEN_EVENT_FIELDS = (
    "intended_referent",
    "hidden_truth_linkage",
    "expected_downstream_interpretation",
    "expected_state_transition",
    "verification_summary",
    "revealed_asset_ids",
)

# Compressed first-demo timing. E12 is drone verification, not social/radio.
DEMO_STORIES = {
    DEMO_STORY: {
        "event_ids": ("E01", "E02", "E11"),
        "delay_ms": {"E01": 0, "E02": 2000, "E11": 6000},
        "omitted": {"E12": "drone_verification"},
    }
}

FORBIDDEN_SITE_IDS = (
    "osm:way:1533952351",
    "osm:way:1533952352",
    "osm:node:13976280529",
)

FORBIDDEN_RUNTIME_MARKERS = (
    "intended_referent",
    "hidden_truth_linkage",
    "expected_downstream_interpretation",
    "expected_state_transition",
    "scenario_ground_truth",
    "verification_summary",
)


def load_events(path=PACK_EVENTS):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    events = payload["events"]
    return {event["event_id"]: event for event in events}, events


def story_spec(story=DEMO_STORY):
    spec = DEMO_STORIES.get(story)
    if spec is None:
        raise ValueError(f"unknown scenario pack story: {story}")
    return spec


def story_steps(story=DEMO_STORY):
    by_id, _ = load_events()
    spec = story_spec(story)
    steps = []
    for event_id in spec["event_ids"]:
        event = by_id[event_id]
        steps.append({
            "event_id": event_id,
            "source_type": event["source_type"],
            "delay_ms": spec["delay_ms"][event_id],
            "omitted": False,
        })
    for event_id, reason in spec["omitted"].items():
        event = by_id[event_id]
        steps.append({
            "event_id": event_id,
            "source_type": event["source_type"],
            "delay_ms": None,
            "omitted": True,
            "reason": reason,
        })
    return steps


def runtime_source_id(event):
    """Independent source identity from observable fields, never a generic replay collapse."""
    raw = event["raw_input"]
    source_type = event["source_type"]
    if source_type == "social":
        author = raw.get("author_id")
        if isinstance(author, str) and author.strip():
            return author
        platform = raw.get("platform")
        if isinstance(platform, str) and platform.strip() and platform != "replay":
            return platform
        source_id = event.get("source_id")
        if isinstance(source_id, str) and source_id.strip() and source_id != "replay":
            return source_id
        return "social"
    if source_type == "radio":
        speaker = raw.get("speaker_id") or event.get("source_id")
        if isinstance(speaker, str) and speaker.strip():
            return speaker
        return "radio"
    source_id = event.get("source_id")
    if isinstance(source_id, str) and source_id.strip():
        return source_id
    return source_type


def observable_raw_input(event):
    raw = deepcopy(event["raw_input"])
    _assert_observable(raw)
    return raw


def runtime_payload(event):
    """Scheduler envelope: routing ids plus the observable raw_input only."""
    payload = {
        "event_id": event["event_id"],
        "source_type": event["source_type"],
        "source_id": runtime_source_id(event),
        "raw_input": observable_raw_input(event),
    }
    _assert_observable(payload["raw_input"])
    return payload


def demo_runtime_payloads(story=DEMO_STORY, *, include_omitted=False):
    by_id, _ = load_events()
    spec = story_spec(story)
    payloads = []
    for event_id in spec["event_ids"]:
        payloads.append(runtime_payload(by_id[event_id]))
    if include_omitted:
        for event_id in spec["omitted"]:
            payloads.append(runtime_payload(by_id[event_id]))
    return payloads


def social_raw_records(story=DEMO_STORY, event_ids=None):
    """Canonical RawSocialInput records for the existing social replay adapter."""
    by_id, _ = load_events()
    spec = story_spec(story)
    wanted = tuple(event_ids) if event_ids is not None else spec["event_ids"]
    records = []
    for event_id in wanted:
        event = by_id[event_id]
        if event["source_type"] != "social":
            continue
        raw = observable_raw_input(event)
        validator("raw_social_input.schema.json").validate(raw)
        records.append(raw)
    return records


def to_normalized(event) -> NormalizedRawInput:
    raw = observable_raw_input(event)
    if event["source_type"] == "social":
        validator("raw_social_input.schema.json").validate(raw)
        return NormalizedRawInput(
            source_type="social",
            source_id=runtime_source_id(event),
            external_id=raw.get("external_id") or raw["input_id"],
            timestamp=raw["timestamp"],
            content=raw["text"],
            source_url=raw.get("source_url"),
            language=raw.get("language"),
            structured_metadata={
                "platform": raw.get("platform"),
                "author_id": raw.get("author_id"),
                "source_metadata": raw.get("metadata") or {},
            },
            raw_payload=raw,
        )
    if event["source_type"] == "radio":
        validator("raw_radio_input.schema.json").validate(raw)
        return NormalizedRawInput(
            source_type="radio",
            source_id=runtime_source_id(event),
            external_id=raw["input_id"],
            timestamp=raw["timestamp"],
            content=raw["transcript"],
            structured_metadata={
                "channel": raw.get("channel"),
                "speaker_id": raw.get("speaker_id"),
                "audio_reference": raw.get("audio_reference"),
                "source_metadata": raw.get("metadata") or {},
            },
            raw_payload=raw,
        )
    raise ValueError(f"unsupported pack source_type: {event['source_type']}")


class ScenarioPackSocialAdapter:
    """Same fetch cursor as SocialReplayAdapter, fed by pack observable social raw_input."""

    def __init__(self, *, story=DEMO_STORY, event_ids=None, batch_size=1, pack_path=PACK_EVENTS):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        by_id, _ = load_events(pack_path)
        spec = story_spec(story)
        wanted = tuple(event_ids) if event_ids is not None else spec["event_ids"]
        self.items = []
        for event_id in wanted:
            event = by_id[event_id]
            if event["source_type"] != "social":
                continue
            self.items.append(to_normalized(event))
        self.cursor, self.batch_size = 0, batch_size
        self.pack_path = Path(pack_path)

    def fetch(self):
        items = self.items[self.cursor:self.cursor + self.batch_size]
        self.cursor += len(items)
        return items


def payload_contains_forbidden(value) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_RUNTIME_MARKERS or key in HIDDEN_EVENT_FIELDS:
                return key
            found = payload_contains_forbidden(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = payload_contains_forbidden(item)
            if found:
                return found
    elif isinstance(value, str):
        for marker in FORBIDDEN_RUNTIME_MARKERS:
            if marker in value:
                return marker
    return None


def _assert_observable(payload):
    leaked = payload_contains_forbidden(payload)
    if leaked:
        raise ValueError(f"hidden scenario field leaked into runtime payload: {leaked}")
    blob = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    if "scenario_ground_truth" in blob:
        raise ValueError("scenario_ground_truth leaked into runtime payload")
    for field in HIDDEN_EVENT_FIELDS:
        if f'"{field}"' in blob:
            raise ValueError(f"{field} leaked into runtime payload")
    for site_id in FORBIDDEN_SITE_IDS:
        if site_id in blob:
            raise ValueError(f"canonical site id shortcut leaked into runtime payload: {site_id}")
