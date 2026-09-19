# Assessment graph handoff

This additive module reads OpenStreetMap PBF geography and stores a simplified road graph,
buildings, original radio/social labels, drone observations and road assessments in SQLite.
Existing ingestion and shared schemas are unchanged. `contracts/labeled_graph.schema.json`
and `contracts/road_assessment.schema.json` reference the existing v2.1 contracts.

## Files to share

- `data/maps/venezuela-latest.osm.pbf`: original country extract (local, not Git tracked).
- `data/maps/venezuela.sqlite`: persistent country graph and future assessments (local).
- `data/graph_exports/caraballeda.json`: bounded nodes/edges/sites and their current labels.
- `data/graph_exports/caraballeda.geojson`: same roads and buildings for a map data layer.
- `data/graph_examples/`: separate explicitly simulated labeled demonstration.

OSM geography does not contain live earthquake damage assessments. The initial real-map
export has no assessed road condition and empty incident labels until resolved reports arrive.
Existing example reports reference Los Angeles or unresolved names, and are never silently
relocated to Venezuela. Use the current ingestion outputs, then pass a canonical resolved
site ID from your team's location resolver. No fuzzy name match is treated as verified.

## Setup and commands (from project root)

```sh
python3.12 -m venv .venv-graph
.venv-graph/bin/pip install -r requirements-graph.txt
.venv-graph/bin/python -m geograph import-osm data/maps/venezuela-latest.osm.pbf data/maps/venezuela.sqlite
.venv-graph/bin/python -m geograph stats data/maps/venezuela.sqlite
.venv-graph/bin/python -m geograph export data/maps/venezuela.sqlite data/graph_exports/caraballeda.json --bbox -66.87 10.59 -66.81 10.64
.venv-graph/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Generate the isolated simulated handoff with:

```sh
.venv-graph/bin/python -m geograph.demo data/graph_exports/caraballeda.json data/graph_examples
```

The database already exists after the delivered import. Import deliberately refuses to
replace an existing database. Change the output path when importing another region.
`unresolved_observations` retains unlocated labels for the teammate to resolve. Canonical road states can be ingested with `road-state`; they appear on the graph without inventing per-profile passability.

The region exporter uses west, south, east, north. Crossing road segments remain whole;
site selection uses the stored centroid. Exports across the antimeridian need two boxes.

```sh
.venv-graph/bin/python -m geograph ingest data/maps/venezuela.sqlite observation resolved_observations.json
.venv-graph/bin/python -m geograph ingest data/maps/venezuela.sqlite observation reports.json --site-id osm:way:YOUR_SITE_ID
.venv-graph/bin/python -m geograph ingest data/maps/venezuela.sqlite drone drone_report.json --site-id osm:way:YOUR_SITE_ID
.venv-graph/bin/python -m geograph ingest data/maps/venezuela.sqlite road-assessment road_assessments.json
```

Use Python 3.12+ for the full existing test suite. JSON input may be one object or an array; `.jsonl` files from `ingestion.demo` are supported. All writes in a command are atomic. Incident
snapshots can be ingested with `incident`, and canonical OperationalBeliefState with
`snapshot`. Upstream Incident records are used as authoritative fused incident labels;
this module preserves observation claims separately and does not redo incident fusion.
Unresolved observations are stored but not attached to an arbitrary building.

## Teammate interface

`GraphStore.export(bbox)` returns:
- `nodes`: road junction IDs and WGS84 coordinates.
- `edges`: stable way/segment IDs, source/target junctions, length, full shape, OSM tags,
  assessment history and latest reported state (with missing/conflicting assessments separate from road condition).
- `sites`: existing Site objects, optional footprints, canonical Incident versions and
  original StructuredObservation labels. A site can have several incident categories.
- `access_connections`: nearby junction candidates within 200 m, always non-routable
  until a real entrance/access path is established. Never route through a building by
  treating these straight proximity lines as traversable roads.

`geograph.store.geojson(graph)` returns a FeatureCollection with [longitude, latitude]
coordinates. It can be supplied to a Google Maps GeoJSON data layer by the frontend.
No Google credentials, Google routing calls or uploads are implemented; the exact Google
API has not been chosen. Backend topology comes from OSM, not Google road identifiers.

Ground teams can pass their existing ordered road edge IDs into:

```python
store.assess_route(edge_ids, profile="foot", at="2026-09-19T14:30:00Z", max_age_s=3600)
```

The result reports each segment and the overall `open/limited/blocked` status (or `null` when no condition can be determined).
Unknown or stale evidence is never open. Foot and vehicle passability are separate.
Static access prohibitions and basic one-way rules are checked; turn restrictions and
conditional access require a full routing engine before operational navigation. This is
an assessment function for a supplied path, not a route optimizer or movement simulator.
Current positions can be mapped to the team's route edge IDs by the routing component.

## Drone input and road conditions

`normalize_drone` accepts the existing DroneObservationInput. It generates zero or more
StructuredObservation records, retaining the full report under metadata and preserving
uncertainty. Infrared sensor origin does not by itself mean confirmed victims, confirmed
structural safety or verified passability. No image analysis or drone route planner.

Existing drone fields cannot represent detailed medical/evacuation conditions. Those
labels arrive as existing radio/social StructuredObservations. Optional
`metadata.assessed_incident_types` identifies explicit negative assessments for
structural_damage, trapped_victims or access_blocked; otherwise all-unknown reports
produce no invented incident. Raw reports are still stored for audit.

Road reports use the new RoadAssessment wrapper: existing RoadState +
per-profile passability + existing EvidenceItem objects + verification status. The only road conditions are `blocked`, `limited`, and `open`, listed in
`geograph/road_conditions.json`. Missing or conflicting assessments have a null
condition; they are not extra road-condition types. `road_state.road_id` can target the whole OSM way or one segment.
Repeated identical IDs are idempotent; reuse with different content is rejected.
Timestamped updates remain append-only. Equally recent conflicting reports are visible.
`assess_route` ignores future reports and expires evidence after the caller's age limit.
RoadState alone from a belief snapshot is preserved but not promoted to a verified
assessment without the evidence/passability wrapper.

## Limitations and provenance

Road graph simplification removes intermediate shape nodes only; it preserves junctions,
way boundaries and curved geometry. OSM relations for building multipolygons are read.
Sites use vertex-average display centroids (possibly outside a concave footprint), never
verified entrance coordinates. Criticality defaults to zero with explicit unknown
assessment metadata, and must be supplied by the operational context before scoring.
Not every OSM access/barrier/turn restriction is modeled. This is a hackathon data and
assessment layer, not certified emergency navigation. SQLite is intended for local/demo
use; exports can be bounded per region instead of loading a planet graph in memory.

Source: https://download.geofabrik.de/south-america/venezuela.html
© OpenStreetMap contributors, ODbL 1.0: https://www.openstreetmap.org/copyright
Retain attribution in UI and exports. Raw download checksum and SHA-256 provenance are
stored alongside/in the database. Map/database files exceed normal GitHub file limits;
share separately or use an agreed data-storage workflow. Small example/export files and
code can be committed normally. Nothing is committed or pushed by this implementation.

### Road reports inside existing drone JSON

The existing DroneObservationInput permits metadata. To ingest a road scan directly,
include the following metadata and set top-level `access_status` to an explicit
`open`, `limited` or `blocked`. No schema changes to DroneObservationInput are needed:

```json
{
  "road_assessment": {
    "road_id": "osm:way:123:segment:0-2",
    "passability": {"foot":"limited","vehicle":"blocked"},
    "travel_cost_multiplier": 2
  }
}
```

This is the contents of `metadata`, not a complete drone report. Replace the road ID
with one from the graph. The drone ingest command atomically stores the raw report,
observations, evidence and RoadAssessment. It rejects unknown road IDs. No passability
is inferred from an infrared heat signature. `possibly_blocked` remains an uncertain
observation and cannot be promoted to a definite RoadState by this adapter.
