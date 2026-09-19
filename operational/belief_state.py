"""Deterministic OperationalBeliefState reducer. No planners, routing, or LLM."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from operational.errors import UnknownSiteError
from operational.ids import max_time, next_state_version, parse_time, schema_version
from operational.incident_fusion import IncidentFusion
from operational.location_resolver import LocationResolver
from operational.site_registry import SiteRegistry
from operational.validation import validate_belief_state, validate_observation
from operational.worksite import materialize_worksite


@dataclass
class OperationalState:
    """In-memory operational store.

    `belief` is the contract OperationalBeliefState. Unresolved and ambiguous
    observations are preserved here for audit and later re-resolution because
    the current belief-state schema has no dedicated unresolved field.
    """

    belief: dict
    unresolved: list[dict] = field(default_factory=list)
    evidence: dict[str, dict] = field(default_factory=dict)
    processed_observation_ids: set[str] = field(default_factory=set)
    fused_observation_ids: set[str] = field(default_factory=set)
    location_resolutions: dict[str, dict] = field(default_factory=dict)
    last_trace: dict = field(default_factory=dict)

    def to_belief_state(self) -> dict:
        return validate_belief_state(deepcopy(self.belief))

    @property
    def state_version(self) -> str:
        return self.belief["state_version"]

    def copy(self) -> "OperationalState":
        return OperationalState(
            belief=deepcopy(self.belief),
            unresolved=deepcopy(self.unresolved),
            evidence=deepcopy(self.evidence),
            processed_observation_ids=set(self.processed_observation_ids),
            fused_observation_ids=set(self.fused_observation_ids),
            location_resolutions=deepcopy(self.location_resolutions),
            last_trace=deepcopy(self.last_trace),
        )


def empty_state(scenario_time: str) -> OperationalState:
    parse_time(scenario_time)
    belief = {
        "schema_version": schema_version(),
        "state_version": "0",
        "scenario_time": scenario_time,
        "sites": [],
        "worksites": [],
        "incidents": [],
        "resources": [],
        "road_states": [],
        "movement_states": [],
        "active_routes": [],
        "pending_reassignment_proposals": [],
        "updated_at": scenario_time,
    }
    return OperationalState(belief=validate_belief_state(belief))


def apply_observation(
    previous: OperationalState,
    observation: dict,
    registry: SiteRegistry,
    *,
    resolver: LocationResolver | None = None,
    fusion: IncidentFusion | None = None,
    scenario_time: str | None = None,
    retry: bool = False,
) -> OperationalState:
    """Reduce one StructuredObservation into the next operational state.

    Duplicate observation_ids are idempotent no-ops unless `retry=True` is used
    to re-resolve an observation that is still in the unresolved queue.
    """
    validate_observation(observation)
    observation_id = observation["observation_id"]
    scenario_time = scenario_time or observation["timestamp"]
    resolver = resolver or LocationResolver()
    fusion = fusion or IncidentFusion()

    if observation_id in previous.processed_observation_ids and not retry:
        return _noop(previous, observation_id, "duplicate_observation")
    if retry and observation_id in previous.fused_observation_ids:
        return _noop(previous, observation_id, "duplicate_observation")

    resolution = resolver.resolve(observation, registry)
    state = previous.copy()
    state.location_resolutions[observation_id] = resolution
    state.processed_observation_ids.add(observation_id)

    if resolution["resolution_status"] != "resolved":
        return _store_unresolved(state, observation, resolution, scenario_time)

    site_id = resolution["resolved_site_id"]
    site = registry.get_site(site_id)
    if site is None:
        raise UnknownSiteError(f"Registry returned unknown site_id {site_id}")

    existing = [item for item in state.belief["incidents"] if item["site_id"] == site_id]
    result = fusion.fuse(observation, resolution, existing, scenario_time=scenario_time)
    if result.action == "none" or result.incident is None:
        if observation_id in state.fused_observation_ids:
            return _noop(previous, observation_id, result.reason)
        return _store_unresolved(state, observation, resolution, scenario_time, extra=result.reason)

    _drop_unresolved(state, observation_id)
    if result.evidence:
        state.evidence[result.evidence["evidence_id"]] = result.evidence
    state.fused_observation_ids.add(observation_id)
    _upsert_incident(state, result.incident)
    _upsert_site(state, site)
    updated_at = max_time(state.belief["updated_at"], observation["timestamp"], scenario_time)
    site_incidents = [item for item in state.belief["incidents"] if item["site_id"] == site_id]
    previous_worksite = next((item for item in state.belief["worksites"] if item["site_id"] == site_id), None)
    worksite = materialize_worksite(
        site,
        site_incidents,
        updated_at=updated_at,
        previous=previous_worksite,
        last_observation_id=observation_id,
    )
    _upsert_worksite(state, worksite)
    _sort_collections(state)
    state.belief["scenario_time"] = max_time(state.belief["scenario_time"], scenario_time, observation["timestamp"])
    state.belief["updated_at"] = updated_at
    state.belief["state_version"] = next_state_version(state.belief["state_version"])
    state.last_trace = {
        "observation_id": observation_id,
        "state_version": state.belief["state_version"],
        "changed": True,
        "location_status": resolution["resolution_status"],
        "location_rule": (resolution.get("metadata") or {}).get("resolution_rule"),
        "resolved_site_id": site_id,
        "candidate_site_ids": list(resolution.get("candidate_site_ids") or []),
        "incident_action": result.action,
        "incident_id": result.incident["incident_id"],
        "worksite_id": worksite["worksite_id"],
        "contributing_incident_ids": list(worksite.get("active_incident_ids") or []),
        "reason": result.reason,
    }
    validate_belief_state(state.belief)
    return state


def retry_unresolved(
    previous: OperationalState,
    registry: SiteRegistry,
    *,
    resolver: LocationResolver | None = None,
    fusion: IncidentFusion | None = None,
) -> OperationalState:
    """Re-resolve queued unresolved/ambiguous observations against the registry."""
    pending = list(previous.unresolved)
    state = previous
    resolver = resolver or LocationResolver()
    fusion = fusion or IncidentFusion()
    for item in pending:
        state = apply_observation(
            state,
            item["observation"],
            registry,
            resolver=resolver,
            fusion=fusion,
            scenario_time=state.belief["scenario_time"],
            retry=True,
        )
    return state


def _noop(previous: OperationalState, observation_id: str, reason: str) -> OperationalState:
    state = previous.copy()
    resolution = state.location_resolutions.get(observation_id) or {}
    state.last_trace = {
        "observation_id": observation_id,
        "state_version": state.belief["state_version"],
        "changed": False,
        "location_status": resolution.get("resolution_status"),
        "location_rule": (resolution.get("metadata") or {}).get("resolution_rule"),
        "resolved_site_id": resolution.get("resolved_site_id"),
        "candidate_site_ids": list(resolution.get("candidate_site_ids") or []),
        "incident_action": "none",
        "incident_id": None,
        "worksite_id": None,
        "contributing_incident_ids": [],
        "reason": reason,
    }
    return state


def _store_unresolved(
    state: OperationalState,
    observation: dict,
    resolution: dict,
    scenario_time: str,
    extra: str | None = None,
) -> OperationalState:
    existing = next((item for item in state.unresolved if item["observation"]["observation_id"] == observation["observation_id"]), None)
    record = {
        "observation": deepcopy(observation),
        "location_resolution": deepcopy(resolution),
    }
    if existing is None:
        state.unresolved.append(record)
        changed = True
    else:
        existing.update(record)
        changed = False
    if changed:
        state.belief["scenario_time"] = max_time(state.belief["scenario_time"], scenario_time, observation["timestamp"])
        state.belief["updated_at"] = max_time(state.belief["updated_at"], scenario_time, observation["timestamp"])
        state.belief["state_version"] = next_state_version(state.belief["state_version"])
    state.last_trace = {
        "observation_id": observation["observation_id"],
        "state_version": state.belief["state_version"],
        "changed": changed,
        "location_status": resolution["resolution_status"],
        "location_rule": (resolution.get("metadata") or {}).get("resolution_rule"),
        "resolved_site_id": resolution.get("resolved_site_id"),
        "candidate_site_ids": list(resolution.get("candidate_site_ids") or []),
        "incident_action": "none",
        "incident_id": None,
        "worksite_id": None,
        "contributing_incident_ids": [],
        "reason": extra or f"location_{resolution['resolution_status']}",
    }
    validate_belief_state(state.belief)
    return state


def _drop_unresolved(state: OperationalState, observation_id: str) -> None:
    state.unresolved = [
        item for item in state.unresolved
        if item["observation"]["observation_id"] != observation_id
    ]


def _upsert_incident(state: OperationalState, incident: dict) -> None:
    incidents = [item for item in state.belief["incidents"] if item["incident_id"] != incident["incident_id"]]
    incidents.append(incident)
    state.belief["incidents"] = incidents


def _upsert_site(state: OperationalState, site: dict) -> None:
    sites = [item for item in state.belief["sites"] if item["site_id"] != site["site_id"]]
    sites.append(deepcopy(site))
    state.belief["sites"] = sites


def _upsert_worksite(state: OperationalState, worksite: dict) -> None:
    worksites = [item for item in state.belief["worksites"] if item["worksite_id"] != worksite["worksite_id"]]
    worksites.append(worksite)
    state.belief["worksites"] = worksites


def _sort_collections(state: OperationalState) -> None:
    state.belief["sites"].sort(key=lambda item: item["site_id"])
    state.belief["incidents"].sort(key=lambda item: item["incident_id"])
    state.belief["worksites"].sort(key=lambda item: item["worksite_id"])
