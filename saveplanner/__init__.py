"""Aftershock Save Planner: rescue allocation and damage-aware routing.

Routing runs on an OpenStreetMap graph this project owns, so a blocked road is an
edge the search cannot expand. Google Maps, if a key is configured, contributes the
satellite basemap only -- it is never asked for a route.
"""
from .roadgraph import RoadGraph, blocked_from_labels
from .runtime import SavePlannerRuntime
from . import planner, scenario

__all__ = ["RoadGraph", "SavePlannerRuntime", "blocked_from_labels", "planner", "scenario"]
