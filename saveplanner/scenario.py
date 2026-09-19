"""Deterministic demo scenario built on top of the real Caraballeda geography.

The geography, road topology and building identities are real OpenStreetMap data.
The operational labels are not: OSM does not carry live earthquake damage, and the
geograph handoff is explicit that fictional claims must never be silently attached
to real Venezuelan buildings. Every worksite produced here therefore carries
`metadata.simulated = True` and the frontend labels the scenario as simulated.

Seeded and ordered, so the same export always yields the same demo.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from .roadgraph import haversine

SCENARIO_SEED = 20260919
SCENARIO_NAME = "2026 Venezuela Earthquake - Caraballeda"

# Worksite templates, echoing the scenario walked through in the architecture spec.
# `sector` is the commander-facing label; `incidents` drives Save scoring.
WORKSITE_TEMPLATES = [
    {"sector": "D3", "incidents": ["structural_damage", "trapped_victims"], "evidence": "corroborated",
     "victims": {"type": "confirmed_multiple", "best": 7}, "structural": "severe", "access": "limited",
     "criticality": 0.9, "label": "Collapsed apartment complex"},
    {"sector": "C4", "incidents": ["structural_damage"], "evidence": "unverified",
     "victims": {"type": "possible_individual", "best": 1}, "structural": "major", "access": "unknown",
     "criticality": 0.6, "label": "Structural collapse (unverified)"},
    {"sector": "B2", "incidents": ["evacuation_need"], "evidence": "corroborated",
     "victims": {"type": "possible_multiple", "best": 4}, "structural": "minor", "access": "open",
     "criticality": 0.5, "label": "People on roof - need evacuation"},
    {"sector": "E2", "incidents": ["structural_damage"], "evidence": "corroborated",
     "victims": {"type": "unknown", "best": None}, "structural": "major", "access": "limited",
     "criticality": 0.4, "label": "Damage assessment"},
    {"sector": "A1", "incidents": ["trapped_victims", "medical_need"], "evidence": "verified",
     "victims": {"type": "confirmed_individual", "best": 2}, "structural": "major", "access": "open",
     "criticality": 0.7, "label": "Trapped resident, medical need"},
    {"sector": "F1", "incidents": ["fire_or_secondary_hazard"], "evidence": "corroborated",
     "victims": {"type": "possible_multiple", "best": 3}, "structural": "severe", "access": "limited",
     "criticality": 0.8, "label": "Secondary hazard near fuel store"},
    {"sector": "G2", "incidents": ["structural_damage"], "evidence": "potential",
     "victims": {"type": "unknown", "best": None}, "structural": "unknown", "access": "unknown",
     "criticality": 0.3, "label": "Reported damage, unconfirmed"},
    {"sector": "H4", "incidents": ["evacuation_need"], "evidence": "unverified",
     "victims": {"type": "possible_individual", "best": 1}, "structural": "minor", "access": "open",
     "criticality": 0.3, "label": "Assistance requested"},
]

RESCUE_UNITS = [
    {"resource_id": "rescue-alpha", "display_name": "Alpha", "capability_tags": ["technical_rescue", "medical"]},
    {"resource_id": "rescue-bravo", "display_name": "Bravo", "capability_tags": ["technical_rescue"]},
    {"resource_id": "rescue-charlie", "display_name": "Charlie", "capability_tags": ["medical", "hazmat"]},
]


def schema_version():
    return "2.1"


def _spread(candidates, count, minimum_separation_m=220.0):
    """Pick `count` well-separated sites so worksites are not all on one street."""
    chosen = []
    for site in candidates:
        point = [site["centroid"]["longitude"], site["centroid"]["latitude"]]
        if all(haversine(point, other) >= minimum_separation_m for other in
               ([s["centroid"]["longitude"], s["centroid"]["latitude"]] for s in chosen)):
            chosen.append(site)
        if len(chosen) == count:
            break
    return chosen


def build(graph_export_path, graph, created_at="2026-09-19T14:00:00Z"):
    """Return worksites, rescue units and initially blocked edges for the demo."""
    raw = json.loads(Path(graph_export_path).read_text(encoding="utf-8"))
    rng = random.Random(SCENARIO_SEED)

    # Prefer named buildings so the commander sees something meaningful on screen.
    sites = [entry["site"] for entry in raw["sites"]
             if entry["site"].get("display_name") and entry["site"].get("facility_type") != "unknown"]
    sites.sort(key=lambda s: s["site_id"])
    rng.shuffle(sites)
    selected = _spread(sites, len(WORKSITE_TEMPLATES))

    worksites = []
    for template, site in zip(WORKSITE_TEMPLATES, selected):
        centroid = site["centroid"]
        access_node = graph.nearest_node([centroid["longitude"], centroid["latitude"]])
        worksites.append({
            "schema_version": schema_version(),
            "worksite_id": "worksite-" + template["sector"].lower(),
            "site_id": site["site_id"],
            "display_name": f"{template['sector']} {template['label']}",
            "evidence_status": template["evidence"],
            "operational_status": "unassessed",
            "active_incident_ids": [f"incident-{template['sector'].lower()}-{kind}" for kind in template["incidents"]],
            "evidence_ids": [],
            "victim_summary": {
                "indication": {"type": template["victims"]["type"]},
                "best_estimate": template["victims"]["best"],
            },
            "structural_summary": {"severity": template["structural"]},
            "access_summary": {"status": template["access"]},
            "criticality": template["criticality"],
            "assigned_resource_ids": [],
            "created_at": created_at,
            "updated_at": created_at,
            "metadata": {
                # Real geography, simulated operational claim. Never conflate the two.
                "simulated": True,
                "scenario": SCENARIO_NAME,
                "sector": template["sector"],
                "incident_types": template["incidents"],
                "position": {"latitude": centroid["latitude"], "longitude": centroid["longitude"]},
                "access_node_id": access_node,
                "access_distance_m": haversine(
                    [centroid["longitude"], centroid["latitude"]], graph.nodes[access_node]),
                "site_display_name": site.get("display_name"),
            },
        })

    # Stage the units away from the worksites so the first routes are visible.
    origins = _staging_nodes(graph, worksites, len(RESCUE_UNITS), rng)
    resources = []
    for unit, node_id in zip(RESCUE_UNITS, origins):
        longitude, latitude = graph.nodes[node_id]
        resources.append({
            "schema_version": schema_version(),
            "resource_id": unit["resource_id"],
            "display_name": unit["display_name"],
            "resource_type": "rescue_unit",
            "status": "available",
            "current_assignment_id": None,
            "current_worksite_id": None,
            "movement_state_id": "movement-" + unit["resource_id"],
            "capability_tags": unit["capability_tags"],
            "speed": {"value": 30.0, "unit": "km_per_h"},
            "metadata": {"home_node_id": node_id,
                         "position": {"latitude": latitude, "longitude": longitude}},
        })

    return {"worksites": worksites, "resources": resources,
            "blocked_edge_ids": initial_blocks(graph, worksites, origins, rng),
            "scenario": SCENARIO_NAME, "created_at": created_at}


def _staging_nodes(graph, worksites, count, rng):
    """Junctions that are far from every worksite, used as unit start positions."""
    targets = [[w["metadata"]["position"]["longitude"], w["metadata"]["position"]["latitude"]]
               for w in worksites]
    scored = []
    for node_id, point in graph.nodes.items():
        if not any(graph.passable(edge) for _, edge in graph.adjacency[node_id]):
            continue
        scored.append((min(haversine(point, target) for target in targets), node_id))
    scored.sort(reverse=True)
    # Not the farthest junctions in the bbox: a unit that needs a quarter hour to
    # appear on screen makes a bad demo. Start a third of the way in, so every unit
    # still visibly drives a route but arrives within the pitch.
    scored = scored[len(scored) // 3:] or scored
    picked, chosen = [], []
    for _, node_id in scored:
        point = graph.nodes[node_id]
        if all(haversine(point, graph.nodes[other]) > 300 for other in chosen):
            chosen.append(node_id)
            picked.append(node_id)
        if len(picked) == count:
            break
    while len(picked) < count:                      # tiny graphs: reuse deterministically
        picked.append(scored[rng.randrange(len(scored))][1])
    return picked


def initial_blocks(graph, worksites, staging_nodes, rng, limit=4):
    """Block a few approach roads so avoidance is visible from the first frame.

    Candidates sit one hop out from a worksite's access junction rather than on it:
    blocking the final link usually just strands the building, whereas blocking the
    corridor forces a genuine detour, which is the behaviour worth showing.

    Blocks are applied cumulatively and each one is kept only if every worksite is
    still reachable from every staging point afterwards. A demo that opens with an
    unreachable worksite demonstrates nothing -- the commander creates that situation
    themselves by clicking roads once the map is live.
    """
    targets = [w["metadata"]["access_node_id"] for w in worksites]

    def nothing_stranded():
        for origin in staging_nodes:
            reachable = graph.reachable_from(origin)
            if not set(targets) <= reachable:
                return False
        return True

    candidates = {}
    for worksite in worksites:
        access = worksite["metadata"]["access_node_id"]
        for neighbour, _ in graph.adjacency[access]:
            for _, edge in graph.adjacency[neighbour]:
                if edge.source != access and edge.target != access and graph.passable(edge):
                    candidates[edge.edge_id] = edge
    ordered = sorted(candidates)
    rng.shuffle(ordered)

    accepted = []
    for edge_id in ordered:
        if len(accepted) >= limit:
            break
        graph.set_blocked(edge_id, True)
        if nothing_stranded():
            accepted.append(edge_id)
        else:
            graph.set_blocked(edge_id, False)
    for edge_id in accepted:            # the runtime owns applying the scenario state
        graph.set_blocked(edge_id, False)
    return sorted(accepted)
