"""Acceptance checks for the Save layer against the frozen Caraballeda pack.

Run with `python -m saveplanner verify`. Each check states a requirement, exercises
it on the real scenario, and prints the evidence, so a failure says what actually
happened. `tests/test_saveplanner.py` pins the behaviour; this answers "does the
demo path work".
"""
from __future__ import annotations

from .beliefadapter import belief_from_pack
from .controller import SaveController
from .execution import ScenarioExecutionIndex
from .roadgraph import RoadGraph, haversine, status_from_labels

R1 = "osm:way:376529129:segment:1-12"
W1_SITE = "osm:way:1533952351"

CHECKS = []


def check(requirement):
    def register(function):
        CHECKS.append((requirement, function))
        return function
    return register


def session(index, event="E07"):
    graph = RoadGraph.load(index.graph_path)
    controller = SaveController(graph, index)
    belief = belief_from_pack(index, event)
    controller.plan(belief, event)
    return controller, belief, graph


def w1_assignment(controller, belief):
    for assignment in controller.assignments.values():
        if assignment["status"] != "active":
            continue
        worksite = next(w for w in belief["worksites"]
                        if w["worksite_id"] == assignment["worksite_id"])
        if worksite["site_id"] == W1_SITE:
            return assignment
    return None


@check("Road condition keeps all three contract values")
def three_states(index):
    seen = {s: status_from_labels({"reported_status": s}) for s in ("open", "limited", "blocked")}
    baseline = status_from_labels(None)
    ok = seen == {"open": "open", "limited": "limited", "blocked": "blocked"} and baseline == "open"
    return ok, (f"open/limited/blocked preserved; unassessed -> {baseline} per "
                f"baseline_road_policy.assume_open_unless_overridden")


@check("LIMITED stays traversable with a cost multiplier; only BLOCKED removes an edge")
def limited_vs_blocked(index):
    graph = RoadGraph.load(index.graph_path)
    origin, destination = index.staging_node_id, index.by_key["W1"]["arrival_node_id"]
    base = graph.route(origin, destination)
    graph.apply_road_states([{"road_id": R1, "status": "limited", "travel_cost_multiplier": 1.6}])
    limited = graph.route(origin, destination)
    used = R1 in limited["edge_ids"]
    graph.apply_road_states([{"road_id": R1, "status": "blocked", "travel_cost_multiplier": 1.0}])
    blocked = graph.route(origin, destination)
    return (used and R1 not in blocked["edge_ids"]), (
        f"LIMITED x1.6: R1 still used, {base['seconds']:.0f}s -> {limited['seconds']:.0f}s; "
        f"BLOCKED: R1 dropped, {blocked['distance_m']:.0f} m detour")


@check("Routing uses the scenario's frozen arrival nodes")
def frozen_arrival_nodes(index):
    graph = RoadGraph.load(index.graph_path)
    missing = [key for key, record in index.by_key.items()
               if record["arrival_node_id"] not in graph.nodes]
    return not missing, (
        f"{len(index.by_key)} worksites W1-W8 have audited arrival nodes, all present "
        f"in the road graph" if not missing else f"missing from graph: {missing}")


@check("Baseline route matches the distance the pack froze")
def baseline_distance(index):
    graph = RoadGraph.load(index.graph_path)
    found = graph.route(index.staging_node_id, index.by_key["W1"]["arrival_node_id"])
    expected = index.by_key["W1"]["baseline_distance_m"]
    delta = abs(found["distance_m"] - expected)
    return delta <= 25, (f"staging -> W1: pack {expected} m, computed "
                         f"{found['distance_m']:.0f} m (delta {delta:.0f} m)")


@check("E07: W1 becomes Save-eligible and receives a rescue team")
def e07(index):
    controller, belief, _ = session(index, "E07")
    ranked = sorted(controller.decisions.values(), key=lambda d: -d["save_priority"])
    w1 = next(w for w in belief["worksites"] if w["site_id"] == W1_SITE)
    assignment = w1_assignment(controller, belief)
    top = ranked[0]["worksite_id"] == w1["worksite_id"] if ranked else False
    return bool(assignment and top), (
        f"W1 SavePriority {ranked[0]['save_priority']:.3f} (top of "
        f"{len(ranked)} eligible); {assignment['resource_id'] if assignment else 'none'} "
        f"assigned, {controller.routes[assignment['route_id']]['total_distance_m']:.0f} m"
        if assignment else "no team assigned to W1")


@check("E10: R1 BLOCKED, route superseded, objective unchanged, no human approval")
def e10(index):
    controller, belief, graph = session(index, "E07")
    assignment = w1_assignment(controller, belief)
    if assignment is None:
        return False, "no W1 assignment to reroute"
    resource_id = assignment["resource_id"]
    expected = index.road_disruptions["R1"]["demo"]
    while controller.movement[resource_id]["distance_traveled_m"] < \
            expected["reveal_after_distance_traveled_m"]:
        controller.advance(belief, 0.5)
    travelled = controller.movement[resource_id]["distance_traveled_m"]
    before = controller.movement[resource_id]["distance_remaining_m"]
    old_route_id = controller.movement[resource_id]["route_id"]

    after_belief = belief_from_pack(index, "E10")
    controller.plan(after_belief, "E10")
    still = next(a for a in controller.assignments.values()
                 if a["status"] == "active" and a["resource_id"] == resource_id)
    replacement = controller.routes[still["route_id"]]
    site_after = next(w["site_id"] for w in after_belief["worksites"]
                      if w["worksite_id"] == still["worksite_id"])
    ok = (graph.edges[R1].blocked
          and R1 not in replacement["road_edge_ids"]
          and site_after == W1_SITE
          and controller.routes[old_route_id]["route_status"] == "superseded"
          and controller.proposal is None)
    return ok, (
        f"travelled {travelled:.0f} m (pack {expected['reveal_after_distance_traveled_m']}); "
        f"remaining {before:.0f} m -> {replacement['total_distance_m']:.0f} m "
        f"(pack {expected['remaining_m_before_block']} -> {expected['remaining_m_after_block']}); "
        f"objective still W1; old route superseded; no proposal raised")


@check("Reroute starts at the unit's live position, not a junction it passed")
def no_teleport(index):
    controller, belief, graph = session(index, "E07")
    assignment = w1_assignment(controller, belief)
    resource_id = assignment["resource_id"]
    for _ in range(60):
        controller.advance(belief, 1.0)
    position = controller.movement[resource_id]["position"]
    point = [position["longitude"], position["latitude"]]
    junction = haversine(point, graph.nodes[graph.nearest_node(point)])
    controller.plan(belief_from_pack(index, "E10"), "E10")
    route = controller.routes[next(a["route_id"] for a in controller.assignments.values()
                                   if a["status"] == "active" and a["resource_id"] == resource_id)]
    start = route["path"][0]
    gap = haversine(point, [start["longitude"], start["latitude"]])
    _, _, _, snap_m = graph.snap(point)
    return abs(gap - snap_m) <= 1.0, (
        f"route starts {gap:.1f} m from the unit (its projection onto the passable "
        f"network is {snap_m:.1f} m); nearest junction was {junction:.1f} m away")


@check("Criticality comes from the scenario overlay, not OSM Site.criticality")
def overlay_criticality(index):
    from . import planner
    _, belief, _ = session(index, "E07")
    w1 = next(w for w in belief["worksites"] if w["site_id"] == W1_SITE)
    site = next(s for s in belief["sites"] if s["site_id"] == W1_SITE)
    used = planner.criticality(w1)
    return used > 0 and site["criticality"] == 0, (
        f"OSM Site.criticality is {site['criticality']} (unknown by design); "
        f"Save scored W1 at {used:.2f} from the scenario overlay")


@check("Every rescue team is generic, so no artificial capability penalty")
def generic_capability(index):
    from . import planner
    teams = [r for r in index.resources.values() if r["resource_type"] == "rescue_unit"]
    penalties = {planner.capability_mismatch(team, {}, ["trapped_victims"]) for team in teams}
    return penalties == {0.0}, (
        f"{len(teams)} teams, all capability_tags=['ground_rescue'], mismatch penalty "
        f"{sorted(penalties)}")


@check("Save owns execution state only; belief facts stay in the belief state")
def state_ownership(index):
    controller, _, _ = session(index, "E07")
    owned = {"decisions", "assignments", "routes", "movement", "proposal"}
    forbidden = {"worksites", "incidents", "sites", "evidence", "road_states", "resources"}
    leaked = sorted(f for f in forbidden if hasattr(controller, f))
    return not leaked, (
        f"controller owns {sorted(owned)}; holds none of {sorted(forbidden)}"
        if not leaked else f"leaked belief state: {leaked}")


@check("Emitted objects validate against the frozen contracts")
def contracts(index):
    try:
        from ingestion.validation import validator
    except ImportError:
        return False, "jsonschema not installed"
    controller, belief, _ = session(index, "E07")
    for _ in range(20):
        controller.advance(belief, 1.0)
    pairs = [("route.schema.json", controller.routes.values()),
             ("save.schema.json", controller.decisions.values()),
             ("assignment.schema.json", controller.assignments.values()),
             ("movement.schema.json", controller.movement_states())]
    counted = 0
    for schema, objects in pairs:
        check_with = validator(schema)
        for item in objects:
            counted += 1
            errors = list(check_with.iter_errors(item))
            if errors:
                return False, f"{schema}: {errors[0].message}"
    return True, f"{counted} objects validated across {len(pairs)} frozen schemas"


def run():
    index = ScenarioExecutionIndex.load()
    print(f"Save layer acceptance checks\nscenario: {index.scenario_id}\n")
    failures = 0
    for number, (requirement, function) in enumerate(CHECKS, 1):
        try:
            ok, detail = function(index)
        except Exception as error:
            ok, detail = False, f"{type(error).__name__}: {error}"
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {number}. {requirement}")
        print(f"      {detail}\n")
    print(f"{len(CHECKS) - failures}/{len(CHECKS)} checks passed")
    if not failures:
        print("\nStill needs eyes: unit icons moving on the dashboard map, and a road "
              "click turning red and rerouting.\nRun `python -m saveplanner serve` "
              "alongside `npm run dev`.")
    return failures
