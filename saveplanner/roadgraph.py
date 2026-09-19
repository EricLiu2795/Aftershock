"""Routable road graph with a strictly binary damage state.

The graph topology comes from the geograph export (OpenStreetMap via Geofabrik).
Google Maps is only ever a basemap image in this system; no Google routing API is
called and no Google road identifier is stored. Edge identities stay the OSM
`osm:way:N:segment:A-B` strings so a Route can be handed back through
`Route.road_edge_ids` and re-checked with `GraphStore.assess_route`.

Road damage is binary here: an edge is BLOCKED or it is not. The shared
`road_state.schema.json` contract still carries the frozen v2.1 enum
(open/limited/blocked) because other modules depend on it, so `blocked_from_labels`
performs the single, centralised collapse to two states. Nothing else in the Save
planner is allowed to look at a three-valued road condition.
"""
from __future__ import annotations

import heapq
import json
import math
from pathlib import Path

# Static OSM access, not damage. A footway is not a "blocked road" -- it is simply
# not part of the vehicle network. Keeping this separate preserves the two-state rule.
NON_VEHICLE_HIGHWAYS = {"footway", "path", "steps", "pedestrian", "cycleway", "bridleway"}

# Free-flow speeds in km/h by OSM highway class, used when `maxspeed` is absent.
DEFAULT_SPEED_KPH = {
    "primary": 50.0,
    "primary_link": 40.0,
    "secondary": 45.0,
    "secondary_link": 35.0,
    "tertiary": 40.0,
    "unclassified": 30.0,
    "residential": 30.0,
    "living_street": 20.0,
    "service": 20.0,
    "track": 15.0,
}
FALLBACK_SPEED_KPH = 25.0

# Post-earthquake ground movement is far slower than free-flow traffic. Applied to
# every edge so ETAs stay plausible without pretending to model live traffic.
DISASTER_SPEED_FACTOR = 0.55

EARTH_RADIUS_M = 6371008.8


def haversine(a, b):
    """Great-circle distance in metres between two [longitude, latitude] points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.asin(min(1.0, math.sqrt(h)))


def blocked_from_labels(labels):
    """Collapse the frozen three-value RoadState enum to the Save planner's binary.

    Only an explicit `blocked` report blocks an edge. `limited` means passable with
    difficulty, and `null` means no assessment exists -- the real Caraballeda export
    ships with every edge unassessed, so treating unknown as blocked would make the
    entire town unroutable. Unknown is therefore passable *for routing*, and the
    uncertainty is surfaced to the commander through `assessment_status` instead.
    """
    return (labels or {}).get("reported_status") == "blocked"


def speed_mps(tags):
    """Traversal speed for an edge, honouring `maxspeed` when OSM supplies a number."""
    raw = tags.get("maxspeed")
    kph = None
    if raw:
        head = str(raw).split()[0]
        if head.replace(".", "", 1).isdigit():
            kph = float(head)
            if "mph" in str(raw).lower():
                kph *= 1.609344
    if kph is None:
        kph = DEFAULT_SPEED_KPH.get(tags.get("highway"), FALLBACK_SPEED_KPH)
    return kph * DISASTER_SPEED_FACTOR / 3.6


def vehicle_accessible(tags):
    """Whether a ground vehicle may use this edge at all, ignoring damage."""
    if tags.get("highway") in NON_VEHICLE_HIGHWAYS:
        return str(tags.get("motor_vehicle") or tags.get("vehicle") or "") in {"yes", "designated", "permissive"}
    return str(tags.get("motor_vehicle") or tags.get("vehicle") or tags.get("access") or "") not in {"no", "private"}


def traversals(tags):
    """Allowed directions as (forward, backward) booleans, from the one-way tags."""
    oneway = str(tags.get("oneway", "yes" if tags.get("junction") == "roundabout" else "no")).lower()
    if oneway in {"yes", "1", "true"}:
        return True, False
    if oneway == "-1":
        return False, True
    return True, True


class Edge:
    """One routable road segment. `path` keeps the OSM bends, source -> target."""

    __slots__ = ("edge_id", "road_id", "source", "target", "length_m", "path", "tags",
                 "name", "speed_mps", "seconds", "vehicle", "forward", "backward", "damage_blocked")

    def __init__(self, raw):
        self.edge_id = raw["edge_id"]
        self.road_id = raw["road_id"]
        self.source = raw["source"]
        self.target = raw["target"]
        self.length_m = float(raw["length_m"])
        self.path = [list(p) for p in raw["path"]]
        self.tags = raw.get("osm_tags") or {}
        self.name = self.tags.get("name") or self.tags.get("highway") or "unnamed road"
        self.speed_mps = speed_mps(self.tags)
        self.seconds = self.length_m / self.speed_mps if self.speed_mps > 0 else math.inf
        self.vehicle = vehicle_accessible(self.tags)
        self.forward, self.backward = traversals(self.tags)
        # Initial damage state imported from the export's stored assessments.
        self.damage_blocked = blocked_from_labels(raw.get("labels"))

    def geometry(self, from_node):
        """Edge shape oriented for travel starting at `from_node`."""
        return self.path if from_node == self.source else list(reversed(self.path))

    def midpoint(self):
        return self.path[len(self.path) // 2]


class RoadGraph:
    """Adjacency over OSM junctions with a live, binary blocked-edge set."""

    def __init__(self, nodes, edges):
        self.nodes = nodes                      # node_id -> [lon, lat]
        self.edges = {e.edge_id: e for e in edges}
        self.adjacency = {node_id: [] for node_id in nodes}
        for edge in edges:
            if edge.source not in nodes or edge.target not in nodes:
                continue
            if edge.forward:
                self.adjacency[edge.source].append((edge.target, edge))
            if edge.backward:
                self.adjacency[edge.target].append((edge.source, edge))
        self.max_speed_mps = max((e.speed_mps for e in edges if e.vehicle), default=1.0)

    @classmethod
    def load(cls, path):
        """Build a graph from a `geograph` export (labeled_graph.schema.json)."""
        graph = json.loads(Path(path).read_text(encoding="utf-8"))
        nodes = {n["node_id"]: [n["position"]["longitude"], n["position"]["latitude"]]
                 for n in graph["nodes"]}
        return cls(nodes, [Edge(raw) for raw in graph["edges"]])

    # -- binary road state -------------------------------------------------

    def blocked_edge_ids(self):
        return sorted(e.edge_id for e in self.edges.values() if e.damage_blocked)

    def set_blocked(self, edge_id, blocked):
        """Set the binary damage state of one edge. Returns True when it changed."""
        edge = self.edges.get(edge_id)
        if edge is None:
            raise KeyError(f"Unknown edge: {edge_id}")
        if edge.damage_blocked == bool(blocked):
            return False
        edge.damage_blocked = bool(blocked)
        return True

    def passable(self, edge):
        """An edge is usable when it carries vehicles and is not reported blocked."""
        return edge.vehicle and not edge.damage_blocked

    # -- geometry helpers --------------------------------------------------

    def nearest_node(self, point, require_passable=True):
        """Closest junction to a [lon, lat] point.

        `require_passable` keeps units and destinations off junctions that damage has
        completely cut off, so the planner does not start or end inside an island.
        """
        candidates = self.nodes.items()
        if require_passable:
            reachable = {n for n, links in self.adjacency.items()
                         if any(self.passable(e) for _, e in links)}
            candidates = [(n, p) for n, p in self.nodes.items() if n in reachable] or list(self.nodes.items())
        return min(candidates, key=lambda item: haversine(point, item[1]))[0]

    def reachable_from(self, origin):
        """Every junction reachable from `origin` over currently passable edges.

        Cheaper than running a full search per destination, so scenario setup can
        check "is anything stranded?" after each candidate block.
        """
        seen = {origin}
        stack = [origin]
        while stack:
            node_id = stack.pop()
            for neighbour, edge in self.adjacency[node_id]:
                if neighbour not in seen and self.passable(edge):
                    seen.add(neighbour)
                    stack.append(neighbour)
        return seen

    # -- routing -----------------------------------------------------------

    def route(self, origin, destination):
        """A* over travel time, skipping blocked edges entirely.

        This is the "modified Dijkstra" of the architecture spec: an ordinary
        shortest-path search whose successor function refuses to expand a blocked
        edge, so avoidance is structural rather than a penalty that can be
        out-weighed. With a zero heuristic it is exactly Dijkstra; the admissible
        great-circle heuristic below only changes the expansion order.

        Returns None when damage has disconnected the destination.
        """
        if origin not in self.nodes or destination not in self.nodes:
            raise KeyError("Route endpoints must be graph junctions")
        if origin == destination:
            return {"node_ids": [origin], "edge_ids": [], "path": [self.nodes[origin]],
                    "distance_m": 0.0, "seconds": 0.0}

        goal = self.nodes[destination]
        ceiling = self.max_speed_mps or 1.0

        def heuristic(node_id):
            return haversine(self.nodes[node_id], goal) / ceiling

        best = {origin: 0.0}
        came_from = {}
        queue = [(heuristic(origin), 0.0, origin)]
        visited = set()
        while queue:
            _, cost, node_id = heapq.heappop(queue)
            if node_id in visited:
                continue
            if node_id == destination:
                return self._reconstruct(came_from, destination, cost)
            visited.add(node_id)
            for neighbour, edge in self.adjacency[node_id]:
                if neighbour in visited or not self.passable(edge):
                    continue
                candidate = cost + edge.seconds
                if candidate < best.get(neighbour, math.inf):
                    best[neighbour] = candidate
                    came_from[neighbour] = (node_id, edge)
                    heapq.heappush(queue, (candidate + heuristic(neighbour), candidate, neighbour))
        return None

    def _reconstruct(self, came_from, destination, seconds):
        node_ids, edge_ids, path, distance = [destination], [], [], 0.0
        cursor = destination
        while cursor in came_from:
            previous, edge = came_from[cursor]
            edge_ids.append(edge.edge_id)
            node_ids.append(previous)
            distance += edge.length_m
            cursor = previous
        node_ids.reverse()
        edge_ids.reverse()
        for index, edge_id in enumerate(edge_ids):
            shape = self.edges[edge_id].geometry(node_ids[index])
            path.extend(shape if not path else shape[1:])
        return {"node_ids": node_ids, "edge_ids": edge_ids, "path": path,
                "distance_m": distance, "seconds": seconds}
