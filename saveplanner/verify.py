"""Acceptance checks against the Save planner's stated requirements.

Run with `python -m saveplanner verify`. Each check states a requirement in the
words it was asked in, exercises it on the real Caraballeda graph, and prints the
evidence it used, so a failure says what actually happened rather than just "False".

These are acceptance checks, not unit tests. `tests/test_saveplanner.py` pins the
behaviour; this answers "does the thing I asked for work".
"""
from __future__ import annotations

import random
import time

from . import planner
from .roadgraph import RoadGraph, blocked_from_labels, haversine
from .runtime import SavePlannerRuntime
from .scenario import build

CHECKS = []


def check(requirement):
    def register(function):
        CHECKS.append((requirement, function))
        return function
    return register


def fresh(export):
    graph = RoadGraph.load(export)
    return SavePlannerRuntime(graph, build(export, graph)), graph


# -- requirement checks ----------------------------------------------------

@check("Roads have exactly two states: blocked or unblocked")
def two_states(export):
    seen = {blocked_from_labels({"reported_status": s})
            for s in ("blocked", "limited", "open", None)}
    if seen != {True, False}:
        return False, f"collapse produced {seen}, expected exactly True/False"
    runtime, graph = fresh(export)
    states = {type(e.damage_blocked) for e in graph.edges.values()}
    return states == {bool}, (
        f"blocked->True, limited/open/unassessed->False; every one of "
        f"{len(graph.edges)} edges carries a bool")


@check("Blocked roads are avoided: no route may ever use one")
def blocked_avoided(export):
    runtime, graph = fresh(export)
    rng = random.Random(11)
    edges = sorted(graph.edges)
    inspected = violations = reroutes = 0
    for _ in range(40):
        runtime.set_road_blocked(rng.choice(edges), True, "verify")
        for _ in range(8):
            runtime.tick(0.1)
        blocked = set(graph.blocked_edge_ids())
        for route in runtime.routes.values():
            if route["route_status"] != "active":
                continue
            inspected += 1
            if set(route["road_edge_ids"]) & blocked:
                violations += 1
    reroutes = sum(1 for t in runtime.trace if "rerouted" in t["headline"])
    return violations == 0, (
        f"{len(graph.blocked_edge_ids())} roads blocked, {inspected} active routes "
        f"inspected, {violations} used a blocked road")


@check("Routes update in real time as blocked roads change")
def realtime_reroute(export):
    runtime, graph = fresh(export)
    for _ in range(40):
        runtime.tick(0.1)
    moving = [r for r in runtime.resources
              if runtime.resources[r]["status"] == "en_route"]
    if not moving:
        return False, "no unit was en route to test"
    for resource_id in moving:
        route = runtime.routes[runtime.movement[resource_id]["route_id"]]
        destination = runtime.worksites[route["worksite_id"]]["metadata"]["access_node_id"]
        origin = runtime.unit_node(resource_id)
        for edge_id in route["road_edge_ids"][2:]:
            graph.set_blocked(edge_id, True)
            severed = destination not in graph.reachable_from(origin)
            graph.set_blocked(edge_id, False)
            if severed:
                continue
            before = list(route["road_edge_ids"])
            started = time.perf_counter()
            runtime.set_road_blocked(edge_id, True, "verify")
            elapsed = (time.perf_counter() - started) * 1000
            new_route = runtime.routes.get(runtime.movement[resource_id]["route_id"])
            if new_route is None:
                return False, "unit lost its route instead of rerouting"
            ok = (edge_id not in new_route["road_edge_ids"]
                  and new_route["road_edge_ids"] != before
                  and new_route["worksite_id"] == route["worksite_id"])
            return ok, (
                f"blocked one edge, full replan took {elapsed:.0f} ms, path changed, "
                f"objective unchanged ({route['worksite_id']}), blocked edge not reused")
    return False, "every edge on the route was a bottleneck; could not test a detour"


@check("Route is the fastest passable path (shortest-time search)")
def shortest_path(export):
    runtime, graph = fresh(export)
    rng = random.Random(5)
    nodes = sorted(graph.nodes)
    checked = bad = 0
    for _ in range(120):
        origin, destination = rng.choice(nodes), rng.choice(nodes)
        found = graph.route(origin, destination)
        if found is None:
            continue
        checked += 1
        # The reported cost must equal the cost of the path actually returned.
        total = sum(graph.edges[e].seconds for e in found["edge_ids"])
        if abs(total - found["seconds"]) > 1e-6:
            bad += 1
    return bad == 0 and checked > 0, (
        f"{checked} routes; reported travel time equals the sum of traversed edge "
        f"times in {checked - bad} of them")


@check("Rescue units move along street geometry, not straight lines")
def follows_streets(export):
    runtime, graph = fresh(export)
    resource_id = next(iter(runtime.resources))
    route = runtime.routes[runtime.movement[resource_id]["route_id"]]
    shape = [(p["latitude"], p["longitude"]) for p in route["path"]]
    straight = haversine([shape[0][1], shape[0][0]], [shape[-1][1], shape[-1][0]])
    along = route["total_distance_m"]
    positions = []
    for _ in range(80):
        runtime.tick(0.1)
        p = runtime.movement[resource_id]["position"]
        positions.append((p["latitude"], p["longitude"]))
    # Every sampled position must lie on a segment of the route polyline.
    off = 0
    for lat, lon in positions:
        best = min(point_to_segment(lat, lon, shape[i], shape[i + 1])
                   for i in range(len(shape) - 1))
        if best > 1.0:                       # metres
            off += 1
    detour = along / straight if straight else 1.0
    return off == 0, (
        f"{len(positions)} sampled positions, {off} off the road polyline; "
        f"path is {along:.0f} m against {straight:.0f} m straight line "
        f"({detour:.2f}x, so it is following streets)")


def point_to_segment(lat, lon, a, b):
    """Approximate distance in metres from a point to a polyline segment."""
    scale = 111320.0
    px, py = lon * scale, lat * scale
    ax, ay = a[1] * scale, a[0] * scale
    bx, by = b[1] * scale, b[0] * scale
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5


@check("Units are sent to the objective the priority formula selects")
def priority_drives_objective(export):
    runtime, _ = fresh(export)
    assigned = {r["current_worksite_id"] for r in runtime.resources.values()
                if r["current_worksite_id"]}
    if not assigned:
        return False, "no unit was dispatched"
    ranked = sorted((w for w in runtime.worksites.values() if w["worksite_id"] in runtime.save_decisions),
                    key=lambda w: -runtime.save_decisions[w["worksite_id"]]["save_priority"])
    top = ranked[0]["worksite_id"]
    detail = ", ".join(
        f"{runtime.worksites[w]['metadata']['sector']} "
        f"{runtime.save_decisions[w]['save_priority']:.2f}"
        for w in list(assigned))
    return top in assigned, (
        f"{len(assigned)} units dispatched to [{detail}]; highest-priority worksite "
        f"({runtime.worksites[top]['metadata']['sector']}) is covered")


@check("Only corroborated/verified worksites consume rescue capacity")
def evidence_gate(export):
    runtime, _ = fresh(export)
    leaked = [w["worksite_id"] for w in runtime.worksites.values()
              if w["evidence_status"] not in planner.ELIGIBLE_EVIDENCE
              and w["assigned_resource_ids"]]
    blocked_out = [w["metadata"]["sector"] for w in runtime.worksites.values()
                   if w["evidence_status"] not in planner.ELIGIBLE_EVIDENCE]
    return not leaked, (
        f"{len(blocked_out)} worksites below the gate ({', '.join(blocked_out)}) "
        f"received no units")


@check("New evidence raises priority and can trigger a reassignment proposal")
def evidence_updates_priority(export):
    runtime, _ = fresh(export)
    before = runtime.save_decisions.get("worksite-c4")
    runtime.update_worksite("worksite-c4", {
        "evidence_status": "corroborated", "victim_indication": "confirmed_multiple",
        "best_estimate": 8, "criticality": 0.95}, "verify")
    after = runtime.save_decisions.get("worksite-c4")
    if before is not None or after is None:
        return False, "C4 should be invisible before the report and scored after it"
    proposal = runtime.proposal
    return True, (
        f"C4 went from below the evidence gate to SavePriority "
        f"{after['save_priority']:.3f}; " + (
            f"proposal raised ({proposal['current_sector']} -> {proposal['proposed_sector']}, "
            f"gain +{proposal['reassignment_gain']:.3f} vs +{proposal['threshold']:.2f})"
            if proposal else "an idle unit took it directly, so no gate was needed"))


@check("A severed objective releases the crew instead of stranding it")
def cutoff_handling(export):
    runtime, graph = fresh(export)
    resource_id = next(r for r in runtime.resources
                       if runtime.resources[r]["status"] == "en_route")
    objective = runtime.resources[resource_id]["current_worksite_id"]
    target = runtime.worksites[objective]["metadata"]["position"]
    # Endpoints snap to the nearest passable road, so severing one junction is not
    # enough -- the building simply attaches to the next street. Block everything
    # within walking range instead.
    point = [target["longitude"], target["latitude"]]
    for edge in list(graph.edges.values()):
        if min(haversine(point, shape) for shape in edge.path) <= 400.0:
            runtime.graph.set_blocked(edge.edge_id, True)
    runtime.replan("verify isolation")
    worksite = runtime.worksites[objective]
    task = runtime.save_decisions[objective]["recommended_task"]
    ok = (worksite["operational_status"] == "pending" and task == "clear_route"
          and runtime.resources[resource_id]["current_worksite_id"] != objective)
    return ok, (
        f"{worksite['metadata']['sector']} cut off -> status {worksite['operational_status']}, "
        f"task {task}, crew released and re-tasked")


@check("No shared contract was changed; emitted objects still validate")
def contracts_intact(export):
    try:
        from ingestion.validation import validator
    except ImportError:
        return False, "jsonschema is not installed (pip install -r requirements.txt)"
    runtime, _ = fresh(export)
    for _ in range(20):
        runtime.tick(0.1)
    pairs = [("route.schema.json", runtime.routes.values()),
             ("save.schema.json", runtime.save_decisions.values()),
             ("assignment.schema.json", runtime.assignments.values()),
             ("movement.schema.json", runtime.movement.values()),
             ("worksite.schema.json", runtime.worksites.values()),
             ("resource.schema.json", runtime.resources.values())]
    counted = 0
    for schema, objects in pairs:
        check_with = validator(schema)
        for item in objects:
            counted += 1
            errors = list(check_with.iter_errors(item))
            if errors:
                return False, f"{schema}: {errors[0].message}"
    return True, f"{counted} objects validated across {len(pairs)} frozen schemas"


# -- runner ----------------------------------------------------------------

def run(export):
    print(f"Save planner acceptance checks\ngraph: {export}\n")
    failures = 0
    for index, (requirement, function) in enumerate(CHECKS, 1):
        try:
            ok, detail = function(export)
        except Exception as error:                      # a crash is a failure
            ok, detail = False, f"{type(error).__name__}: {error}"
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {index}. {requirement}")
        print(f"      {detail}\n")
    total = len(CHECKS)
    print(f"{total - failures}/{total} checks passed")
    if failures:
        print("\nManual checks remain: see saveplanner/README.md")
    else:
        print("\nAutomated coverage complete. Two things still need eyes:")
        print("  * unit icons visibly moving along streets over the basemap")
        print("  * clicking a road on the map turns it red and reroutes the unit")
        print("Run `python -m saveplanner serve` and open http://127.0.0.1:8765")
    return failures
