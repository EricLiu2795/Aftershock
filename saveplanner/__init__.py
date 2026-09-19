"""Aftershock Save layer: rescue allocation and damage-aware ground routing.

Consumes `OperationalBeliefState` and the Caraballeda scenario pack; owns only
execution state (SaveDecision, Assignment, Route, MovementState, proposals).

Routing runs on the OpenStreetMap graph this project owns, so a BLOCKED road is an
edge the search cannot expand and a LIMITED road is one it pays more to use. Google
Maps, when configured, supplies satellite imagery only; it is never asked for a route.
"""
from .controller import SaveController
from .execution import ScenarioExecutionIndex
from .roadgraph import RoadGraph, status_from_labels
from . import planner

__all__ = ["SaveController", "ScenarioExecutionIndex", "RoadGraph",
           "status_from_labels", "planner"]
