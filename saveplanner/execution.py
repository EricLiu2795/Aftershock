"""Static routing facts for the frozen scenario, keyed by canonical `site_id`.

Save needs two different kinds of input and must not confuse them:

  * Dynamic operational truth -- worksites, evidence, incidents, resources, road
    states -- comes from `OperationalBeliefState`. Save never invents or stores it.
  * Static routing facts -- which graph node a worksite is entered from, where the
    rescue base stages -- are scenario configuration, not belief. They live here.

Everything is read from the Caraballeda Scenario Pack. Nothing is synthesised: the
arrival nodes are the pack's audited freeze, which exists precisely so routing does
not have to guess a node from a building centroid and land on bad topology.
"""
from __future__ import annotations

import json
from pathlib import Path

SCENARIO_DIR = Path("data/scenarios/caraballeda")
MANIFEST = SCENARIO_DIR / "scenario.json"
ACTIVE_SITES = SCENARIO_DIR / "active_sites.json"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ScenarioExecutionIndex:
    """Scenario routing configuration. Read-only; holds no operational belief."""

    def __init__(self, manifest, active_sites):
        self.manifest = manifest
        self.scenario_id = manifest["scenario_id"]
        self.start_time = manifest["start_time"]
        self.graph_path = manifest["graph"]["labeled_graph"]

        base = manifest["rescue_base"]
        self.staging_node_id = base["staging_node_id"]
        self.staging_position = base["staging_position"]
        self.base_site_id = base["site"]["site_id"]

        # Canonical resources and their starting movement, straight from the pack.
        self.resources = {r["resource_id"]: r for r in manifest["resources"]}
        self.initial_movement_states = {m["resource_id"]: m
                                        for m in manifest["initial_movement_states"]}
        self.initial_road_states = list(manifest["initial_road_states"])
        self.road_disruptions = {d["disruption_id"]: d for d in manifest["road_disruptions"]}
        self.primary_worksite_key = manifest.get("primary_worksite_key")

        # site_id -> audited arrival node. The pack is explicit that GraphStore
        # access_connections are NOT routable, so this freeze is the access policy.
        self.arrival = {}
        self.by_key = {}
        for entry in active_sites["active_worksites"]:
            site_id = entry["site"]["site_id"]
            record = {
                "worksite_key": entry["worksite_key"],
                "site_id": site_id,
                "display_name": entry.get("display_name"),
                "arrival_node_id": entry["arrival_node_id"],
                "arrival_position": entry.get("arrival_position"),
                "baseline_distance_m": entry.get("baseline_road_distance_from_staging_m"),
                "suggested_worksite_id": entry.get("suggested_worksite_id"),
            }
            self.arrival[site_id] = record
            self.by_key[entry["worksite_key"]] = record

    @classmethod
    def load(cls, manifest=MANIFEST, active_sites=ACTIVE_SITES):
        return cls(_read(manifest), _read(active_sites))

    # -- lookups -----------------------------------------------------------

    def arrival_node(self, site_id):
        """Audited arrival node for a Site, or None when it is not an active worksite."""
        record = self.arrival.get(site_id)
        return record["arrival_node_id"] if record else None

    def worksite_key(self, site_id):
        record = self.arrival.get(site_id)
        return record["worksite_key"] if record else None

    def label(self, site_id):
        """Commander-facing short label. Scenario presentation, never an identity."""
        record = self.arrival.get(site_id)
        return record["worksite_key"] if record else site_id

    def is_rescue_unit(self, resource):
        return resource.get("resource_type") == "rescue_unit"
