"""Deterministic operational-state pipeline.

AI extracts meaning into StructuredObservation. This package establishes
identity, fuses evidence, materializes worksites, and versions
OperationalBeliefState. It never invents canonical Site, Incident, or
Worksite IDs, priority scores, or resource assignments.
"""

from operational.belief_state import (
    OperationalState,
    apply_observation,
    empty_state,
    retry_unresolved,
)
from operational.errors import InvalidObservationError, OperationalError, UnknownSiteError
from operational.incident_fusion import DEFAULT_FUSION_WINDOW, FusionConfig, IncidentFusion
from operational.location_resolver import LocationResolver, ResolverConfig
from operational.site_registry import InMemorySiteRegistry, ScenarioPackSiteRegistry, SiteRegistry
from operational.snapshot import get_operational_snapshot
from operational.worksite import materialize_worksite

__all__ = [
    "DEFAULT_FUSION_WINDOW",
    "FusionConfig",
    "InMemorySiteRegistry",
    "IncidentFusion",
    "InvalidObservationError",
    "LocationResolver",
    "OperationalError",
    "OperationalState",
    "ResolverConfig",
    "ScenarioPackSiteRegistry",
    "SiteRegistry",
    "UnknownSiteError",
    "apply_observation",
    "empty_state",
    "get_operational_snapshot",
    "materialize_worksite",
    "retry_unresolved",
]
