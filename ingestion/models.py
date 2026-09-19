"""Internal adapter data, never an alternative operational contract."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Protocol


def utc_timestamp(value: str, *, assume_utc: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if not assume_utc:
            raise ValueError("timestamp must include a timezone")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class NormalizedRawInput:
    source_type: str
    source_id: str
    external_id: str | None
    timestamp: str
    content: str
    title: str | None = None
    source_url: str | None = None
    language: str | None = None
    raw_location_text: str | None = None
    structured_metadata: dict[str, Any] = field(default_factory=dict)
    raw_payload: Any = field(default=None, repr=False)

    def __post_init__(self):
        for name in ("source_type", "source_id", "content"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be nonempty text")
        if self.external_id is not None and (
            not isinstance(self.external_id, str) or not self.external_id.strip()
        ):
            raise ValueError("external_id must be nonempty text or None")
        if not isinstance(self.structured_metadata, dict):
            raise ValueError("structured_metadata must be an object")
        object.__setattr__(self, "timestamp", utc_timestamp(self.timestamp))

    @property
    def identity(self) -> str:
        # No semantic deduplication. Without an external ID, only identical input
        # envelopes share an identity. Source namespaces prevent cross-feed loss.
        key = [self.source_type, self.source_id, self.external_id]
        if self.external_id is None:
            key.extend([self.timestamp, self.title, self.content, self.source_url,
                        self.language, self.raw_location_text, self.structured_metadata])
        return sha256(json.dumps(key, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


class InformationSourceAdapter(Protocol):
    def fetch(self) -> list[NormalizedRawInput]: ...
