"""Deterministic StructuredObservation → LocationResolution. No LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operational.ids import schema_version, unique
from operational.site_registry import SiteRegistry, normalize_alias
from operational.validation import validate_location_resolution, validate_observation


@dataclass(frozen=True)
class ResolverConfig:
    """Coordinate matching bounds. Alias matching never uses these radii."""

    nearest_radius_m: float = 150.0
    nearest_ambiguity_margin_m: float = 20.0


class LocationResolver:
    """Map an observation to canonical Site IDs from a SiteRegistry.

    Resolution order is fixed and fully deterministic:

    1. trusted canonical site_id already present on the observation
       (`location.resolved_site_id` or metadata `trusted_site_id` /
       `canonical_site_id`), if that ID exists in the registry
    2. exact alias match on raw location text, then label
    3. normalized alias match on raw location text, then label
    4. coordinates: nearest Sites within `nearest_radius_m`
    5. ambiguous candidate set when multiple Sites remain equally plausible
    6. unresolved

    A display string such as "Riverside Apartments" is never used as a Site ID.
    Unknown trusted IDs are ignored rather than created.
    """

    def __init__(self, config: ResolverConfig | None = None):
        self.config = config or ResolverConfig()

    def resolve(self, observation: dict, registry: SiteRegistry) -> dict:
        validate_observation(observation)
        location = observation.get("location") or {}
        metadata = observation.get("metadata") or {}
        raw_text = _nonempty(location.get("raw_text"))
        label = _nonempty(location.get("label"))
        notes: list[str] = []

        trusted = _trusted_ids(location, metadata)
        known_trusted = [site_id for site_id in trusted if registry.get_site(site_id)]
        unknown_trusted = [site_id for site_id in trusted if site_id not in known_trusted]
        if unknown_trusted:
            notes.append("ignored_unknown_site_ids:" + ",".join(unknown_trusted))
        if len(known_trusted) == 1 and len(set(trusted)) <= 1:
            return self._result(
                observation,
                status="resolved",
                site_id=known_trusted[0],
                method="manual",
                score=1.0,
                candidates=[known_trusted[0]],
                rule="trusted_site_id",
                raw_text=raw_text,
                notes=notes,
            )
        if len(set(known_trusted)) == 1 and unknown_trusted:
            return self._result(
                observation,
                status="resolved",
                site_id=known_trusted[0],
                method="manual",
                score=0.9,
                candidates=[known_trusted[0]],
                rule="trusted_site_id",
                raw_text=raw_text,
                notes=notes,
            )
        if len(set(known_trusted)) > 1:
            return self._result(
                observation,
                status="ambiguous",
                site_id=None,
                method="manual",
                score=0.4,
                candidates=unique(known_trusted),
                rule="conflicting_trusted_site_ids",
                raw_text=raw_text,
                notes=notes,
            )

        for text, rule in (
            (raw_text, "exact_alias"),
            (label, "exact_alias_label"),
        ):
            if not text:
                continue
            matches = registry.find_exact_alias(text)
            if len(matches) == 1:
                return self._result(
                    observation,
                    status="resolved",
                    site_id=matches[0],
                    method="gazetteer",
                    score=0.95,
                    candidates=matches,
                    rule=rule,
                    raw_text=text,
                    notes=notes,
                )
            if len(matches) > 1:
                return self._result(
                    observation,
                    status="ambiguous",
                    site_id=None,
                    method="gazetteer",
                    score=0.4,
                    candidates=matches,
                    rule="duplicate_exact_alias",
                    raw_text=text,
                    notes=notes,
                )

        for text, rule in (
            (raw_text, "normalized_alias"),
            (label, "normalized_alias_label"),
        ):
            if not text:
                continue
            matches = registry.find_normalized_alias(text)
            if len(matches) == 1:
                score = 0.8 if (location.get("accuracy") == "area") else 0.85
                return self._result(
                    observation,
                    status="resolved",
                    site_id=matches[0],
                    method="gazetteer",
                    score=score,
                    candidates=matches,
                    rule=rule,
                    raw_text=text,
                    notes=notes,
                    normalized=normalize_alias(text),
                )
            if len(matches) > 1:
                return self._result(
                    observation,
                    status="ambiguous",
                    site_id=None,
                    method="gazetteer",
                    score=0.4,
                    candidates=matches,
                    rule="duplicate_normalized_alias",
                    raw_text=text,
                    notes=notes,
                    normalized=normalize_alias(text),
                )

        lat, lon = location.get("latitude"), location.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            nearby = registry.find_sites_near(lat, lon, self.config.nearest_radius_m)
            if len(nearby) == 1:
                site_id, distance = nearby[0]
                return self._result(
                    observation,
                    status="resolved",
                    site_id=site_id,
                    method="nearest_site",
                    score=_distance_score(distance, self.config.nearest_radius_m),
                    candidates=[site_id],
                    rule="nearest_site",
                    raw_text=raw_text,
                    notes=notes,
                    distances_m={site_id: distance},
                )
            if len(nearby) > 1:
                closest = nearby[0][1]
                plausible = [
                    (site_id, distance)
                    for site_id, distance in nearby
                    if distance <= closest + self.config.nearest_ambiguity_margin_m
                ]
                candidates = [site_id for site_id, _ in plausible]
                distances = {site_id: distance for site_id, distance in nearby}
                if len(plausible) == 1:
                    site_id, distance = plausible[0]
                    return self._result(
                        observation,
                        status="resolved",
                        site_id=site_id,
                        method="nearest_site",
                        score=_distance_score(distance, self.config.nearest_radius_m),
                        candidates=candidates,
                        rule="nearest_site",
                        raw_text=raw_text,
                        notes=notes,
                        distances_m=distances,
                    )
                return self._result(
                    observation,
                    status="ambiguous",
                    site_id=None,
                    method="nearest_site",
                    score=0.4,
                    candidates=candidates,
                    rule="ambiguous_nearest_sites",
                    raw_text=raw_text,
                    notes=notes,
                    distances_m=distances,
                )

        if not raw_text and not label and lat is None and lon is None and not trusted:
            notes.append("no_location")
        elif raw_text or label:
            notes.append("unknown_alias")
        else:
            notes.append("no_site_within_radius")

        return self._result(
            observation,
            status="unresolved",
            site_id=None,
            method=None,
            score=0.0,
            candidates=[],
            rule="unresolved",
            raw_text=raw_text,
            notes=notes,
        )

    def _result(
        self,
        observation: dict,
        *,
        status: str,
        site_id: str | None,
        method: str | None,
        score: float | None,
        candidates: list[str],
        rule: str,
        raw_text: str | None,
        notes: list[str],
        normalized: str | None = None,
        distances_m: dict[str, float] | None = None,
    ) -> dict:
        metadata: dict[str, Any] = {
            "raw_text": raw_text,
            "label": _nonempty((observation.get("location") or {}).get("label")),
            "source_type": observation.get("source_type"),
            "source_id": observation.get("source_id"),
            "resolution_rule": rule,
            "notes": notes,
        }
        if normalized is not None:
            metadata["normalized_text"] = normalized
        if distances_m is not None:
            metadata["candidate_distances_m"] = {
                key: round(value, 3) for key, value in distances_m.items()
            }
        payload = {
            "schema_version": schema_version(),
            "observation_id": observation["observation_id"],
            "resolution_status": status,
            "resolved_site_id": site_id,
            "resolution_method": method,
            "resolution_score": score,
            "candidate_site_ids": unique(sorted(candidates) if status == "ambiguous" else candidates),
            "metadata": metadata,
        }
        return validate_location_resolution(payload)


def _trusted_ids(location: dict, metadata: dict) -> list[str]:
    values = [
        location.get("resolved_site_id"),
        metadata.get("trusted_site_id") if isinstance(metadata, dict) else None,
        metadata.get("canonical_site_id") if isinstance(metadata, dict) else None,
    ]
    return unique([value for value in values if isinstance(value, str) and value.strip()])


def _nonempty(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _distance_score(distance_m: float, radius_m: float) -> float:
    if radius_m <= 0:
        return 0.7
    ratio = max(0.0, min(1.0, 1.0 - (distance_m / radius_m)))
    return round(0.55 + 0.4 * ratio, 4)
