"""SQLite geography and append-only assessments; no AI, network or hidden fusion."""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import uuid

from ingestion.validation import schema_documents, validator


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def version():
    return schema_documents()["common.schema.json"]["$defs"]["schemaVersion"]["const"]


def validate(name, value):
    encode(value)
    validator(name).validate(value)


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def distance(a, b):
    """Great circle distance in metres, coordinates as [longitude, latitude]."""
    x1, y1, x2, y2 = map(math.radians, (*a, *b))
    h = math.sin((y2-y1)/2)**2 + math.cos(y1)*math.cos(y2)*math.sin((x2-x1)/2)**2
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(h)))


class GraphStore:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript((Path(__file__).with_name("storage.sql")).read_text())

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        name = "tx_" + uuid.uuid4().hex
        self.db.execute("SAVEPOINT " + name)
        try:
            yield self
            self.db.execute("RELEASE SAVEPOINT " + name)
        except BaseException:
            self.db.execute("ROLLBACK TO SAVEPOINT " + name)
            self.db.execute("RELEASE SAVEPOINT " + name)
            raise

    def metadata(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, encode(value)))

    def node(self, node_id, point):
        self.db.execute("INSERT OR IGNORE INTO nodes VALUES (?,?,?)", (node_id, *point))

    def edge(self, edge_id, road_id, source, target, path, tags):
        length = sum(distance(a,b) for a,b in zip(path,path[1:]))
        xs, ys = zip(*path)
        self.db.execute("INSERT INTO edges VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            edge_id, road_id, source, target, length, encode(path), encode(tags),
            min(xs), min(ys), max(xs), max(ys), "road"))

    def site(self, site, geometry=None):
        validate("site.schema.json", site)
        point = site["centroid"]
        self.db.execute("INSERT INTO sites VALUES (?,?,?,?,?) ON CONFLICT(site_id) DO UPDATE SET payload=excluded.payload,geometry=COALESCE(excluded.geometry,sites.geometry),lon=excluded.lon,lat=excluded.lat", (
            site["site_id"], encode(site), encode(geometry) if geometry else None,
            point["longitude"], point["latitude"]))

    def _record(self, kind, identity, value, timestamp, site_id=None, road_id=None):
        payload = encode(value)
        old = self.db.execute("SELECT payload FROM records WHERE kind=? AND id=?", (kind,identity)).fetchone()
        if old:
            if old[0] != payload:
                raise ValueError(f"Conflicting immutable record ID: {kind}/{identity}")
            return False
        self.db.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", (kind,identity,site_id,road_id,timestamp,payload))
        return True

    def observation(self, value, resolved_site_id=None):
        """Preserve the original label. Caller supplies a canonical resolution if needed."""
        validate("observation.schema.json", value)
        declared = value["location"].get("resolved_site_id")
        if declared and resolved_site_id and declared != resolved_site_id:
            raise ValueError("Conflicting site resolutions")
        site_id = resolved_site_id or declared
        if site_id and not self.db.execute("SELECT 1 FROM sites WHERE site_id=?", (site_id,)).fetchone():
            raise ValueError(f"Unknown site: {site_id}")
        old = self.db.execute("SELECT site_id FROM records WHERE kind='observation' AND id=?", (value["observation_id"],)).fetchone()
        if old and old[0] and site_id and old[0] != site_id:
            raise ValueError("Observation already resolved to another site")
        inserted = self._record("observation", value["observation_id"], value, value["timestamp"], site_id)
        if not inserted and site_id:
            self.db.execute("UPDATE records SET site_id=? WHERE kind='observation' AND id=? AND site_id IS NULL", (site_id,value["observation_id"]))
        return inserted

    def incident(self, value):
        validate("incident.schema.json", value)
        if not self.db.execute("SELECT 1 FROM sites WHERE site_id=?", (value["site_id"],)).fetchone():
            raise ValueError("Incident references an unknown site")
        identity = value["incident_id"] + "@" + value["last_updated_at"]
        return self._record("incident", identity, value, value["last_updated_at"], value["site_id"])

    def road_assessment(self, value):
        validate("road_assessment.schema.json", value)
        road_id = value["road_state"]["road_id"]
        if not self.db.execute("SELECT 1 FROM edges WHERE road_id=? OR edge_id=?", (road_id,road_id)).fetchone():
            raise ValueError(f"Unknown road/edge: {road_id}")
        # Evidence is material, not just dangling IDs. Preserve it within the assessment.
        ids = {e["evidence_id"] for e in value["evidence"]}
        if not set(value["road_state"].get("evidence_ids",[])) <= ids:
            raise ValueError("RoadState evidence_ids must be included in evidence")
        return self._record("road_assessment", value["assessment_id"], value,
                            value["road_state"]["updated_at"], road_id=road_id)

    def road_state(self, value):
        validate("road_state.schema.json", value)
        road_id = value["road_id"]
        if not self.db.execute("SELECT 1 FROM edges WHERE road_id=? OR edge_id=?", (road_id,road_id)).fetchone():
            raise ValueError(f"Unknown road/edge: {road_id}")
        return self._record("road_state", hashlib.sha256(encode(value).encode()).hexdigest(),
                            value, value["updated_at"], road_id=road_id)

    def snapshot(self, value):
        """Load canonical Site/Incident records; do not infer geographic aliases."""
        validate("operational_belief_state.schema.json", value)
        for site in value["sites"]:
            self.site(site)
        for incident in value["incidents"]:
            self.incident(incident)
        # Road states without attached evidence are retained but not promoted to assessments.
        for state in value["road_states"]:
            self._record("road_state", hashlib.sha256(encode(state).encode()).hexdigest(), state,
                         state["updated_at"], road_id=state["road_id"])
        self.metadata("state_version", value["state_version"])

    def labels(self, site_id):
        observations, incidents = [], {}
        for row in self.db.execute("SELECT * FROM records WHERE site_id=? ORDER BY id", (site_id,)):
            item = json.loads(row["payload"])
            if row["kind"] == "observation":
                observations.append(item)
            elif row["kind"] == "incident":
                old = incidents.get(item["incident_id"])
                if old is None or instant(item["last_updated_at"]) > instant(old["last_updated_at"]):
                    incidents[item["incident_id"]] = item
        return {"observations": observations, "incidents": sorted(incidents.values(),key=lambda x:x["incident_id"])}

    def road_labels(self, edge):
        records = [json.loads(r[0]) for r in self.db.execute(
            "SELECT payload FROM records WHERE kind='road_assessment' AND road_id IN (?,?)", (edge["road_id"],edge["edge_id"]))]
        records.sort(key=lambda x:(instant(x["road_state"]["updated_at"]),x["assessment_id"]))
        # Retain all reports. At the newest time, contradictory states are explicit.
        newest = [r for r in records if instant(r["road_state"]["updated_at"]) == instant(records[-1]["road_state"]["updated_at"])] if records else []
        raw_states = [json.loads(r[0]) for r in self.db.execute(
            "SELECT payload FROM records WHERE kind='road_state' AND road_id IN (?,?) ORDER BY id", (edge["road_id"],edge["edge_id"]))]
        combined = [r["road_state"] for r in records] + raw_states
        latest = max((instant(r["updated_at"]) for r in combined), default=None)
        states = {r["status"] for r in combined if instant(r["updated_at"]) == latest}
        return {"assessment_status": ("conflicting" if len(states)>1 else "assessed") if combined else "unassessed",
                "reported_status": next(iter(states)) if len(states)==1 else None,
                "assessments": records, "road_states": raw_states}

    def assess_route(self, edge_ids, profile="foot", at=None, max_age_s=3600):
        """Assess an ordered ground path; never equate missing/stale evidence with safe."""
        if profile not in {"foot", "vehicle"} or not edge_ids:
            raise ValueError("Provide edges and profile foot or vehicle")
        if at is None or max_age_s < 0:
            raise ValueError("An explicit assessment time and nonnegative max_age_s are required")
        now = instant(at)
        details = []
        for identity in edge_ids:
            edge = self.db.execute("SELECT * FROM edges WHERE edge_id=?", (identity,)).fetchone()
            if edge is None:
                raise ValueError(f"Unknown edge: {identity}")
            labels = self.road_labels(edge)
            fresh = [r for r in labels["assessments"] if 0 <= (now-instant(r["road_state"]["updated_at"])).total_seconds() <= max_age_s]
            newest = [r for r in fresh if instant(r["road_state"]["updated_at"]) == max(instant(x["road_state"]["updated_at"]) for x in fresh)] if fresh else []
            statuses = {r["passability"][profile] for r in newest}
            status = next(iter(statuses)) if len(statuses)==1 else None
            tags = json.loads(edge["tags"])
            access_keys = ["foot","access"] if profile=="foot" else ["motor_vehicle","vehicle","access"]
            access = next((tags[k] for k in access_keys if k in tags), None)
            restricted = access in {"no","private"}
            if profile=="vehicle" and tags.get("highway") in {"footway","path","steps","pedestrian","cycleway","bridleway"} and access not in {"yes","designated","permissive"}:
                restricted = True
            if restricted:
                status = "blocked"
            details.append({"edge_id": identity, "road_id":edge["road_id"], "status":status,
                            "static_access_restricted": restricted, "assessments": newest})
        # Check that a directed walk exists through the ordered edges.
        possible = None
        for identity in edge_ids:
            e = self.db.execute("SELECT * FROM edges WHERE edge_id=?", (identity,)).fetchone()
            tags = json.loads(e["tags"])
            oneway = tags.get("oneway", "yes" if tags.get("junction")=="roundabout" else "no")
            if profile=="foot":
                oneway = tags.get("oneway:foot", "no")
            pairs = [(e["source"],e["target"]),(e["target"],e["source"])]
            if oneway in {"yes","1","true"}: pairs = pairs[:1]
            if oneway == "-1": pairs = pairs[1:]
            possible = {b for a,b in pairs if possible is None or a in possible}
            if not possible:
                raise ValueError("Edges are disconnected or violate direction restrictions")
        statuses = {d["status"] for d in details}
        overall = next((s for s in ("blocked",None,"limited") if s in statuses),"open")
        return {"profile":profile,"assessed_at":at,"status":overall,"edges":details,
                "limitations":["Reported conditions only; not a safety guarantee.","Turn restrictions and conditional OSM access rules are not evaluated."]}

    def export(self, bbox):
        """Export a bounded graph. Road paths retain bends; endpoints are junctions."""
        west,south,east,north = bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("bbox must be west south east north (split antimeridian areas)")
        edges, node_ids = [], set()
        for r in self.db.execute("SELECT * FROM edges WHERE maxlon>=? AND minlon<=? AND maxlat>=? AND minlat<=? ORDER BY edge_id", (west,east,south,north)):
            e = {"edge_id":r["edge_id"],"road_id":r["road_id"],"source":r["source"],"target":r["target"],
                 "length_m":r["length_m"],"path":json.loads(r["path"]),"osm_tags":json.loads(r["tags"]),
                 "labels":self.road_labels(r)}
            edges.append(e)
            node_ids.update((r["source"],r["target"]))
        nodes = []
        for identity in sorted(node_ids):
            n = self.db.execute("SELECT * FROM nodes WHERE node_id=?", (identity,)).fetchone()
            nodes.append({"node_id":identity,"kind":"road_junction","position":{"longitude":n["lon"],"latitude":n["lat"]}})
        sites = []
        for r in self.db.execute("SELECT * FROM sites WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? ORDER BY site_id", (west,east,south,north)):
            sites.append({"site":json.loads(r["payload"]),"geometry":json.loads(r["geometry"]) if r["geometry"] else None,"labels":self.labels(r["site_id"])})
        graph = {"schema_version":version(),"graph_version":"1.0","bbox":list(bbox),"attribution":"© OpenStreetMap contributors; ODbL 1.0; extract by Geofabrik",
                 "nodes":nodes,"edges":edges,"sites":sites,"access_connections":[],
                 "unresolved_observations": [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM records WHERE kind='observation' AND site_id IS NULL ORDER BY id")],
                 "metadata": {r[0]:json.loads(r[1]) for r in self.db.execute("SELECT * FROM metadata ORDER BY key")}}
        # Candidate proximity links are NOT traversable access routes.
        for site in sites:
            p = site["site"]["centroid"]
            if nodes:
                nearest = min(nodes,key=lambda n:distance([p["longitude"],p["latitude"]],[n["position"]["longitude"],n["position"]["latitude"]]))
                dist = distance([p["longitude"],p["latitude"]],[nearest["position"]["longitude"],nearest["position"]["latitude"]])
                if dist <= 200:
                    graph["access_connections"].append({"site_id":site["site"]["site_id"],"node_id":nearest["node_id"],"distance_m":dist,"status":"unverified_candidate","routable":False})
        validate("labeled_graph.schema.json",graph)
        return graph

    def stats(self):
        return {table:self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("nodes","edges","sites","records")}


def geojson(graph):
    features = []
    for edge in graph["edges"]:
        features.append({"type":"Feature","id":edge["edge_id"],"geometry":{"type":"LineString","coordinates":edge["path"]},
                         "properties":{k:v for k,v in edge.items() if k!="path"}})
    for item in graph["sites"]:
        site = item["site"]; p=site["centroid"]
        features.append({"type":"Feature","id":site["site_id"],"geometry":item["geometry"] or {"type":"Point","coordinates":[p["longitude"],p["latitude"]]},
                         "properties":{"site":site,"labels":item["labels"]}})
    return {"type":"FeatureCollection","features":features,"attribution":graph["attribution"]}
