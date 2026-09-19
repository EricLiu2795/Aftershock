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

# Furthest a crew is assumed to cover on foot between the road and the building.
# Snapping to the nearest *passable* road means a building whose own street is
# blocked attaches to the next street instead -- which is what a crew would really
# do -- but only within reason. Beyond this the objective counts as unreachable and
# needs a clear_route task rather than an implausible cross-country walk.
MAX_ACCESS_WALK_M = 300.0

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
                 "name", "speed_mps", "seconds", "vehicle", "forward", "backward",
                 "damage_blocked", "bbox")

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
        xs = [p[0] for p in self.path]
        ys = [p[1] for p in self.path]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

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

    def snap(self, point):
        """Attach an off-graph coordinate to the road network.

        Buildings are not vertices of the road graph -- they have no edges at all --
        so any search that starts or ends at one must first project it onto a road.
        Projecting onto the nearest *junction* is the cheap version and it is wrong
        often: on this extract it averages 51 m against 29 m for projecting onto the
        nearest point of the nearest *edge*, and is more than twice as far for 45% of
        buildings, because most buildings sit along a street rather than at a corner.

        Returns (edge, t, projected_point, distance_m) where t is the fraction along
        the edge's own source -> target direction.
        """
        longitude, latitude = point
        metres_per_degree_lon = 111320.0 * math.cos(math.radians(latitude))

        def box_distance(edge):
            """Lower bound: distance from the point to the edge's bounding box."""
            west, south, east, north = edge.bbox
            dx = max(west - longitude, 0.0, longitude - east) * metres_per_degree_lon
            dy = max(south - latitude, 0.0, latitude - north) * 111320.0
            return math.hypot(dx, dy)

        # Visiting edges nearest-box-first lets the loop stop as soon as the bound
        # exceeds the best exact distance found, which keeps a snap near O(1) in
        # practice instead of scanning all 1123 edges segment by segment.
        candidates = sorted(((box_distance(e), i, e) for i, e in enumerate(self.edges.values())
                             if self.passable(e)), key=lambda item: item[0])
        best = None
        for bound, _, edge in candidates:
            if best is not None and bound > best[0]:
                break
            for index, (start, end) in enumerate(zip(edge.path, edge.path[1:])):
                px, py = longitude * metres_per_degree_lon, latitude * 111320.0
                ax, ay = start[0] * metres_per_degree_lon, start[1] * 111320.0
                bx, by = end[0] * metres_per_degree_lon, end[1] * 111320.0
                dx, dy = bx - ax, by - ay
                if dx == 0 and dy == 0:
                    fraction, distance = 0.0, math.hypot(px - ax, py - ay)
                else:
                    fraction = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
                    distance = math.hypot(px - (ax + fraction * dx), py - (ay + fraction * dy))
                if best is None or distance < best[0]:
                    projected = [start[0] + (end[0] - start[0]) * fraction,
                                 start[1] + (end[1] - start[1]) * fraction]
                    best = (distance, edge, index, fraction, projected)
        if best is None:
            raise ValueError("Graph has no passable edge to snap to")
        distance, edge, index, fraction, projected = best
        # Convert "segment index + fraction within it" into one fraction along the edge.
        before = sum(haversine(a, b) for a, b in zip(edge.path[:index + 1], edge.path[1:index + 1]))
        within = haversine(edge.path[index], projected)
        along = (before + within) / edge.length_m if edge.length_m else 0.0
        return edge, min(1.0, max(0.0, along)), projected, distance

    def route_points(self, origin, destination):
        """Route between two arbitrary coordinates, such as two buildings.

        Both endpoints are snapped onto the road network; the search then runs
        junction to junction, and the two partial edges at each end are stitched on.
        The returned `entry_m` / `exit_m` are the off-graph legs -- the walk between
        the building and the road, which is not itself a routable path.
        """
        start_edge, start_t, start_point, entry_m = self.snap(origin)
        end_edge, end_t, end_point, exit_m = self.snap(destination)
        if entry_m > MAX_ACCESS_WALK_M or exit_m > MAX_ACCESS_WALK_M:
            return None          # no passable road within a plausible walk

        if start_edge.edge_id == end_edge.edge_id:
            along = abs(end_t - start_t) * start_edge.length_m
            forward = end_t >= start_t
            if (forward and not start_edge.forward) or (not forward and not start_edge.backward):
                pass                      # one-way against us: fall through to a real search
            else:
                shape = self._slice(start_edge, start_t, end_t)
                return {"node_ids": [], "edge_ids": [start_edge.edge_id], "path": shape,
                        "distance_m": along, "seconds": along / start_edge.speed_mps,
                        "entry_m": entry_m, "exit_m": exit_m}

        # Seed the search from whichever ends of the origin edge we may drive towards.
        seeds = {}
        if start_edge.forward:
            seeds[start_edge.target] = (1 - start_t) * start_edge.seconds
        if start_edge.backward:
            seeds[start_edge.source] = start_t * start_edge.seconds
        # Accept arrival at whichever end of the destination edge can reach the target.
        exits = {}
        if end_edge.forward:
            exits[end_edge.source] = end_t * end_edge.seconds
        if end_edge.backward:
            exits[end_edge.target] = (1 - end_t) * end_edge.seconds
        if not seeds or not exits:
            return None

        best = None
        for seed_node, seed_cost in seeds.items():
            for exit_node, exit_cost in exits.items():
                found = self.route(seed_node, exit_node)
                if found is None:
                    continue
                total = seed_cost + found["seconds"] + exit_cost
                if best is None or total < best[0]:
                    best = (total, seed_node, exit_node, found, seed_cost, exit_cost)
        if best is None:
            return None

        total, seed_node, exit_node, found, seed_cost, exit_cost = best
        head = self._slice(start_edge, start_t, 1.0 if seed_node == start_edge.target else 0.0)
        tail = self._slice(end_edge, 0.0 if exit_node == end_edge.source else 1.0, end_t)
        path = head + found["path"][1:] + tail[1:]
        distance = (abs((1 if seed_node == start_edge.target else 0) - start_t) * start_edge.length_m
                    + found["distance_m"]
                    + abs(end_t - (0 if exit_node == end_edge.source else 1)) * end_edge.length_m)
        # The partial start/end edges may already appear in the searched path, and the
        # Route contract requires road_edge_ids to be unique. Order is preserved.
        ordered = [start_edge.edge_id] + found["edge_ids"] + [end_edge.edge_id]
        unique = list(dict.fromkeys(ordered))
        return {"node_ids": found["node_ids"],
                "edge_ids": unique,
                "path": path, "distance_m": distance, "seconds": total,
                "entry_m": entry_m, "exit_m": exit_m}

    @staticmethod
    def _slice(edge, from_t, to_t):
        """Geometry of a partial edge between two fractions, in travel order."""
        length = edge.length_m or 1.0
        points, travelled = [], 0.0
        lo, hi = (from_t, to_t) if from_t <= to_t else (to_t, from_t)
        for a, b in zip(edge.path, edge.path[1:]):
            step = haversine(a, b)
            start_f, end_f = travelled / length, (travelled + step) / length
            if end_f >= lo and start_f <= hi:
                for fraction in (max(lo, start_f), min(hi, end_f)):
                    local = 0.0 if end_f == start_f else (fraction - start_f) / (end_f - start_f)
                    points.append([a[0] + (b[0] - a[0]) * local, a[1] + (b[1] - a[1]) * local])
            travelled += step
        deduped = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
        if from_t > to_t:
            deduped.reverse()
        return deduped or [edge.path[0]]

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
