# Caraballeda Scenario Pack v0

Deterministic scenario inputs for Aftershock Phase 2. Downstream systems
(LocationResolver, fusion, Search, Save, routing, movement, frontend) consume
this pack. This folder does not implement those systems.

The full Caraballeda map extent remains approximately 6 km × 5 km. Only eight
Sites formed the original scenario. The integrated demo now includes 20 active Sites (W1–W20).

## Extended demo buildings (W9–W20)

The twelve additions are Mariola, Maribel, Coral Beach, Residencias Miramar,
Residencia Los Corales, Residencia Vistamar, Rita Sol Palace, Residencias Tahiti,
Mariana Mar, Residencias Albatros, Coral Bella, and Residencias Roca-Park.
Their canonical identities, coordinates, and existing damage tags are copied from
the checked-in OSM graph, not invented. The preliminary building list is at
https://prensapuntofijo.com/listado-de-edificaciones-la-guaira-sufrio-el-mayor-colapso-de-estructuras-y-lo-declaran-zona-de-desastre/.
These sources do not establish current habitability or historical casualty totals.
All occupant counts, live reports, structural/access scan results, and rescue assignments remain simulated.

Each added site has a reachable vehicle-network arrival node within 140 m of its
centroid; this is a scenario access choice, not a verified entrance. The full map
extent and original W1–W8 scenarios are unchanged.

W9–W20 images are real post-event **satellite neighborhood tiles**, not drone
photographs. They contain each building's canonical centroid; adjacent buildings
can share a tile. They are unmodified 256×256 JPEGs from Planet Labs PBC's
27 June 2026 SkySat scene, distributed by HOTOSM/OpenAerialMap under **CC BY-NC 4.0**.
Noncommercial use and attribution are required. Do not infer occupants from imagery.
Exact XYZ coordinates, source URLs, acquisition time, credit, and license are in
`verification_assets.json`; see `assets/drone/README.md` for the source dataset.

Start Demo emits the opening social/radio reports at 0/5/10 seconds, followed by
one additional building report every 4–6 wall-clock seconds in a session-seeded
varied order. Changing simulation speed does not flood the evidence feed.

## Real versus simulated

The map, road network, building geometry, and canonical Site identities are derived from real geospatial data. Victims, operational incidents, rescue resources, report timing, and active road disruptions are simulated for the Aftershock demonstration.

| Layer | Source | Treat as |
| --- | --- | --- |
| Canonical `site_id`, coordinates, footprints, OSM names | OSM / `data/graph_exports/caraballeda.json` | Real geographic identity |
| OSM `damage:*` / `destroyed:building` tags | Already present on some buildings in OSM | Historical/damage mapping context, not live casualty counts |
| Fire station name and location | OSM node `13998439701` | Real facility |
| Staging node `osm:node:925978252` | Scenario choice near the fire station | Simulated staging, not a verified driveway |
| Arrival nodes | Scenario freeze | Simulated access policy; GraphStore `access_connections` stay non-routable |
| Victim state, counts, timed reports, drone findings | `scenario_ground_truth.json` / `scenario_events.json` | Simulated |
| R1 / R2 / R3 road status | Scenario RoadState | Simulated. Not a claim of real earthquake road damage |
| Baseline OPEN roads | Scenario policy | Simulation baseline. Do not mutate OSM to claim roads were open |
| Drone mission timing / whether a drone was sent | `scenario_events.json` | Simulated |
| Scan-revealed images | `verification_assets.json` / `assets/drone/` | Original W1–W8 imagery is AI-generated simulation. W9–W20 use credited historical satellite neighborhood tiles. Neither establishes occupant counts or current operational truth |
| Contextual verification photographs | Wikimedia Commons originals in `assets/context/` | Real historical earthquake photos reused as analogical incident-type context. Not Caraballeda site matches and not 2026 news-site images |

Never copy `scenario_ground_truth.json` into `OperationalBeliefState`.

## Drone-verification media

The pack includes a hidden verification-asset layer so a drone event can
reveal a site-specific building image during the demo:

`site_id` → hidden verification asset(s) → drone event reveals asset →
frontend can display the image → downstream backend may also consume the
structured `DroneObservationInput`.

- Assets live in `verification_assets.json`. Image files live under
  `assets/drone/` and `assets/context/`.
- They are for **demo visualization / verification evidence**.
- W2 and W7 images are **AI-generated simulated drone verification imagery**.
  They are **simulated renders tied to real OSM sites** for demo purposes.
  They are **not** historical photographs. They are **not** ground truth.
  Operational interpretation still comes from StructuredObservation /
  IncidentFusion.
- Do **not** treat a revealed image as operational truth by itself. The
  structured drone observation remains the machine-readable finding.
- Assets stay hidden until a drone event lists them in `revealed_asset_ids`.
  In v0, **E05** reveals `va-w2-drone-oblique` and **E12** reveals
  `va-w7-drone-topdown`. E10 keeps the existing R1 contextual road-blockage
  JPEG. W1 and W5 still have reserved placeholder drone assets that no v0
  drone event reveals.

This pack does not generate, fetch, or render images at runtime.

## Files

| File | Role |
| --- | --- |
| `scenario.json` | Manifest, bbox, graph pointer, baseline road policy, resources, initial RoadState |
| `active_sites.json` | Twenty active Worksites plus unused candidates |
| `scenario_ground_truth.json` | Hidden simulator truth |
| `scenario_events.json` | Timed raw radio / social / drone inputs |
| `verification_assets.json` | Media inventory, licenses, and file status |
| `assets/context/` | Seven original Wikimedia Commons JPEGs (analogical, not site photos) |
| `assets/drone/` | AI-generated simulated W2 / W7 drone verification PNGs |
| `README.md` | This document |

Do not duplicate the Caraballeda graph export here. Reference
`data/graph_exports/caraballeda.json` (same bbox as `geograph` export:
west, south, east, north = `-66.87 10.59 -66.81 10.64`).

## Rescue base

- Facility: Estación de bomberos Braulio José Lugo (`osm:node:13998439701`)
- Staging node: `osm:node:925978252`
- All three Rescue Teams and both drones start at the staging node

Rescue Teams are generic. There is no light/heavy vehicle split. Road
status values are only `open`, `limited`, and `blocked`, with optional
`travel_cost_multiplier`.

## Active Worksites

| Key | Role | Canonical `site_id` | Arrival node |
| --- | --- | --- | --- |
| W1 | Primary rescue / R1 reroute | `osm:way:1533952351` Residencias Caribe A | `osm:node:925977824` |
| W2 | Search-first | `osm:way:1533952352` Residencias Caribe B | `osm:node:925952318` |
| W3 | Conflicting evidence | `osm:way:1533750411` Costa Brava (Urbanización Caribe) | `osm:node:925975798` |
| W4 | Severe damage, unknown occupants | `osm:way:1533564319` Parque Caraballeda | `osm:node:925974809` |
| W5 | Late escalation | `osm:way:1533265291` La Gabarra | `osm:node:14129448288` |
| W6 | Distant / lower evidence | `osm:way:953861531` Residencias Las Perlas | `osm:node:925979782` |
| W7 | High-criticality rumor, later unsupported | `osm:node:13976280529` Colegio Tanaguarenas | `osm:node:262484375` |
| W8 | Access / evacuation | `osm:way:1533730691` Rita Luna Palaca | `osm:node:9210087287` |

Name identity is not merged:

- Perlamar is **not** Las Perlas. OSM `Residencias Perlamar` is in Nueva Esparta, outside this graph.
- Ritamar Palace is **not** in the current graph. Rita Luna Palaca and Rita Sol Palace are distinct OSM buildings.
- Two OSM buildings are named Costa Brava. W3 is `osm:way:1533750411` only.

W2 arrival `osm:node:925952318` is a scenario choice: the downstream endpoint of
the unnamed oneway residential street (`osm:way:79290859`) that also holds the
W1 arrival node. It is not a verified building entrance.

W7 arrival `osm:node:262484375` is a scenario choice on Avenida Principal de
Caribe near the school (the node already appears in `data/graph_examples`).

Avoid depending on `osm:way:79290883:segment:8-9` (can block tested departures)
and `osm:way:1552994005:segment:0-2` (can disconnect the La Gabarra arrival).

Active sites may list `verification_asset_ids`. W2 and W7 are the
drone-revealed sites in this timeline. W1 and W5 keep reserved hidden
drone placeholders. Contextual JPEGs are analogical incident-type photos,
not site matches.

## Road disruptions (all simulated)

| ID | Edge | Initial | Later | Why |
| --- | --- | --- | --- | --- |
| R1 | `osm:way:376529129:segment:1-12` Avenida Principal de Caribe | OPEN | BLOCKED at T+205s | Live reroute for Bravo → W1. Alternate remains connected (~1382 m → ~1874 m). |
| R2 | `osm:way:79138347:segment:0-1` Avenida La Costanera (bridge) | LIMITED ×2.0 | — | Slows generic Rescue Teams; still traversable |
| R3 | `osm:way:1205110985:segment:0-2` Avenida Principal de Caribe | LIMITED ×1.3 | — | Minor congestion / debris |

Simulation baseline: every other road is treated as OPEN for routing. That
policy does not change OSM and does not make unassessed GraphStore edges
assess as `open` inside `assess_route()`.

### Expected R1 demo sequence (not implemented here)

1. Rescue Team Bravo is assigned to W1.
2. Initial route uses R1 (`osm:way:376529129:segment:1-12`).
3. After Bravo has traveled about 373 m, event E10 reports the obstruction.
4. `RoadState(R1)` becomes `blocked`.
5. Target remains W1. No ReassignmentProposal / human approval.
6. The old Route is superseded.
7. Downstream A* / Dijkstra recomputes from `MovementState.position`.
8. Bravo follows the longer alternate (~1010 m remaining → ~1609 m).

This pack only encodes the event and the expected transition.

## Timed events

Start: `2026-09-19T14:00:00Z`. About five minutes.

| Event | T+ | Source | Referent | Intent |
| --- | --- | --- | --- | --- |
| E01 | 20s | social | W1 | Possible trapped people at Residencias Caribe |
| E02 | 38s | radio | W1 | Collapse + at least 4 voices |
| E03 | 58s | social | W2 | Search-first, count unknown |
| E04 | 80s | radio | W3 | Damage yes, victims unconfirmed |
| E13 | 92s | social | W6 | Weak unverified damage near Residencias Las Perlas; no occupant claim |
| E05 | 105s | drone-1 | W2 | Strengthens W2 without a count. Reveals `va-w2-drone-oblique` |
| E06 | 125s | social | W4 | Building destroyed; occupant status unknown |
| E07 | 148s | radio | W1 | Enough for initial Save assignment (Bravo) |
| E08 | 165s | radio | W8 | Evacuation / limited access |
| E09 | 185s | radio | W5 | Late audible survivors (possible reassignment proposal) |
| E10 | 205s | radio | R1 / W1 | Dynamic blockage, same target, auto-reroute. Reveals `r1_road_blockage` |
| E11 | 235s | social | W7 | School rumor |
| E12 | 268s | drone-2 | W7 | Rumor unsupported. Reveals `va-w7-drone-topdown` |

Raw social/radio/drone payloads are compatible with current v2.1 contracts and
the existing OpenAI extraction pipeline. The demo should send those raw
payloads through ingestion. `expected_downstream_interpretation` is test
guidance, not fusion output. `verification_summary` on drone events is also
test guidance.

## How to consume

1. Load the graph export for geography.
2. Load `scenario.json` for resources, staging, baseline road policy, and initial RoadState (R1 OPEN, R2/R3 LIMITED).
3. Register the twenty Sites from `active_sites.json` with their frozen arrival nodes.
4. Keep `scenario_ground_truth.json` inside the simulator.
5. Play `scenario_events.json` by `scenario_time_s`.
6. Social/radio events go through ingestion (no pre-baked StructuredObservation).
7. Drone events are `DroneObservationInput` (coordinates allowed; `resolved_site_id` left null).
8. When E10 arrives, apply RoadState `blocked` on R1 and request a same-target reroute.
9. When a drone event includes `revealed_asset_ids`, look those IDs up in
   `verification_assets.json`. W2 / W7 PNGs are AI-generated simulated drone
   verification imagery, not historical photographs and not ground truth.
   Operational interpretation still comes from StructuredObservation /
   IncidentFusion. If `file_status` is `placeholder`, keep the metadata and
   do not invent a real photograph. Backend consumers should still read the
   structured drone observation.

## Out of scope

LocationResolver, IncidentFusion, Search/Save planners, A*/Dijkstra, movement
simulation, frontend, image generation, 3D rendering, vehicle subclasses, and
contract schema changes.
