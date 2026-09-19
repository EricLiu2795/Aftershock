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

    def find_contained_normalized_alias(self, text: str) -> list[str]: ...

    def find_sites_near(self, lat: float, lon: float, radius_m: float) -> list[tuple[str, float]]: ...

    def list_candidates(self, text: str) -> list[str]: ...

    def list_sites(self) -> list[dict]: ...


class InMemorySiteRegistry:
    """Test and runtime registry. Aliases are data, not resolver hard-coding."""

    def __init__(self, records: Iterable[dict[str, Any]] | None = None):
        self._sites: dict[str, dict] = {}
        self._exact: dict[str, list[str]] = {}
        self._normalized: dict[str, list[str]] = {}
        self._overlays: dict[str, dict] = {}
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
        overlay = record.get("scenario_overlay")
        if isinstance(overlay, dict) and overlay:
            metadata = dict(site.get("metadata") or {})
            metadata["scenario_operational_overlay"] = deepcopy(overlay)
            site["metadata"] = metadata
            self._overlays[site["site_id"]] = deepcopy(overlay)
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

    def find_contained_normalized_alias(self, text: str) -> list[str]:
        """Longest unique token-phrase match contained in `text`.

        Matching is exact on normalized tokens. No edit-distance, no LLM.
        Duplicate short phrases such as "Residencias Caribe" stay ambiguous
        when they are the longest contained match. A longer unique phrase
        wins when it is also contained.
        """
        haystack = _normalized_tokens(text)
        if not haystack:
            return []
        matches: list[tuple[int, str, list[str]]] = []
        for alias, site_ids in self._normalized.items():
            needle = tuple(alias.split())
            if _contains_token_phrase(haystack, needle):
                matches.append((len(needle), alias, site_ids))
        if not matches:
            return []
        longest = max(length for length, _alias, _ids in matches)
        site_ids = unique(
            site_id
            for length, _alias, ids in matches
            if length == longest
            for site_id in ids
        )
        return site_ids

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
        return unique(
            self.find_exact_alias(text)
            + self.find_normalized_alias(text)
            + self.find_contained_normalized_alias(text)
        )

    def get_scenario_overlay(self, site_id: str) -> dict | None:
        overlay = self._overlays.get(site_id)
        return deepcopy(overlay) if overlay is not None else None

    def list_sites(self) -> list[dict]:
        return [deepcopy(self._sites[key]) for key in sorted(self._sites)]


class ScenarioPackSiteRegistry(InMemorySiteRegistry):
    """Load Sites from a scenario-pack JSON document.

    This adapter is intentionally format-stable for:

        data/scenarios/caraballeda/active_sites.json

    Callers pass a concrete file. Accepted shapes:

        {"active_worksites": [ {"worksite_key", "aliases", "site": {<Site>}}, ... ]}
        {"sites": [ {<Site fields>, "aliases": [...]}, ... ]}
        [ {<Site fields>, "aliases": [...]}, ... ]

    Canonical identity always comes from the nested Site. Wrapper
    `worksite_key` is scenario metadata, never a Site ID.
    `scenario_criticality` is retained as an operational overlay and is
    not written onto Site.criticality.
    """

    def __init__(self, path: str | Path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        super().__init__(_records_from_pack(payload))


# Observable context phrases that uniquely identify duplicate-named Sites in
# the Caraballeda pack. These are scenario aliases, not OSM truth and not
# hidden intended_referent values. Plain "Residencias Caribe" stays ambiguous.
SCENARIO_CONTEXT_ALIASES = {
    "osm:way:1533952351": (
        "Residencias Caribe on Avenida Principal de Caribe",
        "Residencias Caribe Avenida Principal de Caribe",
        "Caribe A",
    ),
    "osm:way:1533952352": (
        "other Residencias Caribe building toward Avenida La Playa",
        "Residencias Caribe toward Avenida La Playa",
        "Caribe B",
    ),
}


def _records_from_pack(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and "active_worksites" in payload:
        return _records_from_active_worksites(payload["active_worksites"])
    if isinstance(payload, dict) and "sites" in payload:
        return list(payload["sites"])
    if isinstance(payload, list):
        return payload
    raise ValueError(
        "Scenario pack must be an object with active_worksites or sites, or a site list"
    )


def _records_from_active_worksites(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise ValueError("active_worksites must be an array")
    records = []
    for entry in entries:
        if not isinstance(entry, dict) or "site" not in entry:
            raise ValueError("Each active_worksites entry must contain a nested site")
        site = deepcopy(entry["site"])
        site_id = site.get("site_id")
        aliases = [name for name in list(entry.get("aliases") or []) if isinstance(name, str)]
        display_name = entry.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            aliases.append(display_name)
        if isinstance(site_id, str):
            aliases.extend(SCENARIO_CONTEXT_ALIASES.get(site_id, ()))
        overlay = {
            "worksite_key": entry.get("worksite_key"),
            "display_name": display_name,
            "scenario_criticality": entry.get("scenario_criticality"),
            "source": "scenario_pack.active_worksites",
            "not_osm_criticality": True,
        }
        records.append({"site": site, "aliases": aliases, "scenario_overlay": overlay})
    return records


def _normalized_tokens(text: str | None) -> tuple[str, ...]:
    normalized = normalize_alias(text)
    if not normalized:
        return ()
    return tuple(normalized.split())


def _contains_token_phrase(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    for index in range(len(haystack) - width + 1):
        if haystack[index:index + width] == needle:
            return True
    return False


def _site_and_aliases(record: dict[str, Any]) -> tuple[dict, list[str]]:
    aliases = list(record.get("aliases") or [])
    site = {key: deepcopy(record[key]) for key in SITE_CONTRACT_FIELDS if key in record}
    return site, aliases
