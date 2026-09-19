"""Canonical Site lookup. LocationResolver never invents site IDs or aliases."""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Protocol
import unicodedata

from operational.ids import unique
from operational.validation import validate_site


EARTH_RADIUS_M = 6371008.8

SITE_CONTRACT_FIELDS = (
    "schema_version",
    "site_id",
    "site_type",
    "display_name",
    "canonical_source",
    "canonical_source_id",
    "address",
    "centroid",
    "geometry_reference",
    "facility_type",
    "criticality",
    "metadata",
)


def normalize_alias(text: str | None) -> str:
    """Conservative, deterministic alias normalization.

    Trim, casefold, collapse whitespace, strip punctuation, and drop combining
    marks so accented spellings compare equal. No edit-distance fuzzy matching.
    """
    if text is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = stripped.casefold().strip()
    stripped = re.sub(r"[^\w\s]", " ", stripped, flags=re.UNICODE)
    return re.sub(r"\s+", " ", stripped).strip()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    x1, y1, x2, y2 = map(math.radians, (lon1, lat1, lon2, lat2))
    h = math.sin((y2 - y1) / 2) ** 2 + math.cos(y1) * math.cos(y2) * math.sin((x2 - x1) / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.asin(min(1.0, math.sqrt(h)))


class SiteRegistry(Protocol):
    """Abstraction over canonical map/scenario Sites.

    Implementations supply Site IDs. LocationResolver only queries this
    interface and never constructs IDs from raw location strings.

    A later scenario pack can implement:

        ScenarioPackSiteRegistry("data/scenarios/caraballeda/active_sites.json")
    """

    def get_site(self, site_id: str) -> dict | None: ...

    def find_exact_alias(self, text: str) -> list[str]: ...

    def find_normalized_alias(self, text: str) -> list[str]: ...

    def find_sites_near(self, lat: float, lon: float, radius_m: float) -> list[tuple[str, float]]: ...

    def list_candidates(self, text: str) -> list[str]: ...

    def list_sites(self) -> list[dict]: ...


class InMemorySiteRegistry:
    """Test and runtime registry. Aliases are data, not resolver hard-coding."""

    def __init__(self, records: Iterable[dict[str, Any]] | None = None):
        self._sites: dict[str, dict] = {}
        self._exact: dict[str, list[str]] = {}
        self._normalized: dict[str, list[str]] = {}
        for record in records or []:
            self.add_record(record)

    @classmethod
    def from_sites(
        cls,
        sites: Iterable[dict],
        aliases_by_site: dict[str, Iterable[str]] | None = None,
    ) -> "InMemorySiteRegistry":
        aliases_by_site = aliases_by_site or {}
        records = [
            {"site": site, "aliases": list(aliases_by_site.get(site["site_id"], []))}
            for site in sites
        ]
        return cls(records)

    def add_record(self, record: dict[str, Any]) -> None:
        if "site" in record:
            site = deepcopy(record["site"])
            aliases = list(record.get("aliases") or [])
        else:
            site, aliases = _site_and_aliases(record)
        validate_site(site)
        site_id = site["site_id"]
        self._sites[site_id] = deepcopy(site)
        names = []
        names.extend(aliases)
        names.append(site.get("display_name"))
        names.append(site.get("address"))
        metadata = site.get("metadata") or {}
        if isinstance(metadata, dict):
            extra = metadata.get("aliases") or []
            if isinstance(extra, list):
                names.extend(extra)
        for name in names:
            self._index_alias(site_id, name)

    def _index_alias(self, site_id: str, name: str | None) -> None:
        if not isinstance(name, str):
            return
        exact = name.strip()
        if not exact:
            return
        self._exact.setdefault(exact, [])
        if site_id not in self._exact[exact]:
            self._exact[exact].append(site_id)
        normalized = normalize_alias(exact)
        if not normalized:
            return
        self._normalized.setdefault(normalized, [])
        if site_id not in self._normalized[normalized]:
            self._normalized[normalized].append(site_id)

    def get_site(self, site_id: str) -> dict | None:
        site = self._sites.get(site_id)
        return deepcopy(site) if site is not None else None

    def find_exact_alias(self, text: str) -> list[str]:
        if not isinstance(text, str):
            return []
        return list(self._exact.get(text.strip(), []))

    def find_normalized_alias(self, text: str) -> list[str]:
        key = normalize_alias(text)
        if not key:
            return []
        return list(self._normalized.get(key, []))

    def find_sites_near(self, lat: float, lon: float, radius_m: float) -> list[tuple[str, float]]:
        matches = []
        for site in self._sites.values():
            point = site["centroid"]
            distance = haversine_m(lat, lon, point["latitude"], point["longitude"])
            if distance <= radius_m:
                matches.append((site["site_id"], distance))
        matches.sort(key=lambda item: (item[1], item[0]))
        return matches

    def list_candidates(self, text: str) -> list[str]:
        return unique(self.find_exact_alias(text) + self.find_normalized_alias(text))

    def list_sites(self) -> list[dict]:
        return [deepcopy(self._sites[key]) for key in sorted(self._sites)]


class ScenarioPackSiteRegistry(InMemorySiteRegistry):
    """Load Sites from a scenario-pack JSON document.

    This adapter is intentionally format-stable for:

        data/scenarios/caraballeda/active_sites.json

    It does not import that path. Callers pass a concrete file once the
    scenario pack lands. Accepted shapes:

        {"sites": [ {<Site fields>, "aliases": [...]}, ... ]}
        [ {<Site fields>, "aliases": [...]}, ... ]
    """

    def __init__(self, path: str | Path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "sites" in payload:
            records = payload["sites"]
        elif isinstance(payload, list):
            records = payload
        else:
            raise ValueError("Scenario pack must be a site list or an object with a sites array")
        super().__init__(records)


def _site_and_aliases(record: dict[str, Any]) -> tuple[dict, list[str]]:
    aliases = list(record.get("aliases") or [])
    site = {key: deepcopy(record[key]) for key in SITE_CONTRACT_FIELDS if key in record}
    return site, aliases
