# Save layer

Deterministic Save scope: OperationalBeliefState -> SaveDecision -> rescue assignment ->
ground Route over a damaged road graph -> MovementState, with the human reassignment gate
from architecture specification sections 19-25. It does not implement location resolution,
incident fusion, drone verification or AI extraction.

## Start here

From the repository root, with Python 3.12+ and no dependencies beyond the standard
library:

```sh
python -m saveplanner verify        # acceptance checks against the requirements
python -m saveplanner serve         # live map on http://127.0.0.1:8765
python -m saveplanner plan          # one Save pass as SaveDecision records
python -m saveplanner route osm:node:262483885 osm:node:6113627467
python -m unittest tests.test_saveplanner
```

`verify` is the quickest way to confirm the module does what it was asked to do: it
states each requirement, exercises it on the real graph and prints the evidence
(roads blocked, routes inspected, replan latency, contract objects validated). It
exits non-zero on failure, so CI can run it. It covers everything except the two
things that need eyes: icons visibly moving, and clicking a road on the map.

`serve` starts the planner on <http://127.0.0.1:8765> and opens a standalone operational
map at that address. The React dashboard consumes the same API through Vite's
`/saveplanner` proxy, so `npm run dev` starts the API, the web app and this planner
together. The dashboard runs without it; the dispatch panel then says how to start it.

## Road damage is binary

Routing recognises exactly two road states: **blocked** or **not blocked**.

The shared `road_state.schema.json` contract still carries the frozen v2.1 enum
(`open` / `limited` / `blocked`) because geograph and the belief state depend on it, so
the collapse to two states happens in exactly one place, `roadgraph.blocked_from_labels`:

| Reported status | Routing treats it as | Why |
| --- | --- | --- |
| `blocked` | blocked | The only state that removes an edge |
| `limited` | passable | Passable with difficulty is still passable |
| `open` | passable | |
| `null` (unassessed) | passable | See below |

Unknown is passable **for routing**. The delivered Caraballeda export has no assessed
road at all, so treating unassessed as blocked would make the whole town unroutable and
the planner would refuse every worksite. This is a deliberate departure from the
geograph rule that unknown evidence is never "open": that rule governs what the system
*claims* about a road, and it still holds. Uncertainty is carried to the commander as
`assessment_status`, not smuggled into the route as a false guarantee. A route is a
proposal to a human, not a safety certificate.

Static OSM access (a footway, a `motor_vehicle=no` lane) is kept separate from damage.
It is not a third road state; it decides whether an edge is in the vehicle network at all.

## Routing

`RoadGraph.route` is A\* over travel time whose successor function refuses to expand a
blocked edge, so avoidance is structural rather than a penalty that a short path can
out-weigh. With a zero heuristic it is exactly Dijkstra; the admissible great-circle
heuristic only changes expansion order. It returns `None` when damage has severed every
approach, and the planner reports the worksite as needing a `clear_route` task instead
of inventing a path through a blockage.

Edge identities stay the OSM `osm:way:N:segment:A-B` strings, so a produced Route can be
handed straight back to `GraphStore.assess_route`, and the dashboard can colour the same
edge red on its map because both sides key on the same id.

No Google API is called. Google Maps, when a token is configured, supplies satellite
imagery only; every route, ETA and blocked road is computed here.

## Scoring

Weights are the specification's, unchanged:

```
SavePriority(w)        = 0.40 V + 0.25 T + 0.20 H + 0.15 C
AssignmentUtility(r,w) = SavePriority(w) - 0.25 Travel - 0.20 Risk - 0.20 Mismatch
ReassignmentGain       = AU(r,new) - AU(r,current) - SwitchCost
SwitchCost             = 0.5 ProgressLoss + 0.5 AdditionalTravel      threshold 0.15
```

Only `corroborated` and `verified` worksites pass the evidence gate; weaker evidence is a
Search problem. Assignment is greedy by utility, which the specification calls sufficient
for the MVP and which is deterministic but not a globally optimal matching.

`planner.py` is pure: no clock, no randomness, no I/O. `runtime.py` owns mutable state.

## Route change versus objective change

This distinction is the point of the module:

- **Same objective, new route** (a road was blocked) -> automatic, logged, no approval.
- **Different objective** -> a `ReassignmentProposal` with a 30-second wall-clock gate.

The simulation never pauses while a commander decides, and an approved reassignment
routes from the unit's position at approval time, not at proposal time. No answer means
the existing assignment stands.

A unit whose objective becomes unreachable is released and re-tasked rather than left
waiting at a roadblock; the worksite stays eligible and is flagged for `clear_route`.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/state` | current snapshot |
| GET | `/api/stream` | Server-Sent Events, one frame per tick |
| GET | `/api/roads` | every road once, for rendering |
| POST | `/api/road` | `{edge_id, blocked}` - block or reopen, then replan |
| POST | `/api/worksite` | revised belief about one worksite, then replan |
| POST | `/api/proposal` | `{approve}` - answer the reassignment gate |
| POST | `/api/control` | `{running, sim_speed}` |
| POST | `/api/replan` | force a recompute |

Movement ticks continuously; the planner recomputes on events (road change, new
evidence, arrival, completion, approved reassignment) per specification section 32,
plus a coarse 20 simulated-second re-score because SavePriority's time-sensitivity
term grows while a worksite waits. A re-score that changes no assignment is silent.

## What makes a priority change, and what it does

`POST /api/worksite` is the hook for the fusion layer. Save consumes belief and never
invents evidence, so only these fields are accepted: `evidence_status`,
`victim_indication`, `best_estimate`, `structural_severity`, `access_status`,
`criticality`, `incident_types`. Anything else is rejected.

```sh
curl -X POST localhost:8765/api/worksite -H 'content-type: application/json' \
  -d '{"worksite_id":"worksite-c4","evidence_status":"corroborated",
       "victim_indication":"confirmed_multiple","best_estimate":8,"criticality":0.95}'
```

That reproduces the section 33 demo beat: C4 is unverified and invisible to Save, the
report promotes it past the evidence gate at SavePriority 0.74, ReassignmentGain comes
out at +0.16 against the 0.15 threshold, and a proposal goes to the commander. On
approval the unit routes to C4 **from where it is now**, not from where it stood when
the proposal was raised.

A rising priority never silently redirects a moving crew. It changes the objective only
through the gate; the route itself changes automatically only when a road changes.

## Scenario and provenance

Geography, road topology and building identities are real OpenStreetMap data via
Geofabrik. The operational labels are **simulated**: OSM carries no live earthquake
damage. Every generated worksite sets `metadata.simulated` and the UI says so, following
the data boundary the geograph handoff insists on. The scenario is seeded, so the same
export always produces the same demo.

## Limitations

Turn restrictions and conditional OSM access rules are not evaluated. ETAs are derived
from OSM speed classes with a flat disaster factor, not from live traffic. The nearest
road junction is used as a worksite's access point and the final leg is treated as an
unassessed walk-in; it is not a verified building entrance. This is a hackathon planning
layer, not certified emergency navigation.

Source: <https://download.geofabrik.de/south-america/venezuela.html>
(c) OpenStreetMap contributors, ODbL 1.0. Retain attribution in UI and exports.
