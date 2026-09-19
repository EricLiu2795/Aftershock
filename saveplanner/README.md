# Save layer

Deterministic Save scope, on the frozen Caraballeda Scenario Pack:

```
OperationalBeliefState -> SaveDecision -> Assignment -> Route -> MovementState
```

It does not implement location resolution, incident fusion, drone verification or AI
extraction. It consumes what those produce.

## Start here

Python 3.12+, no dependencies beyond `requirements.txt`:

```sh
python -m saveplanner verify        # acceptance checks against the frozen scenario
python -m saveplanner serve         # replay the timeline, feed the dashboard
python -m saveplanner plan --at E07 # one Save pass at a scenario event
python -m unittest tests.test_saveplanner
```

## State ownership

| Owned by | What |
| --- | --- |
| `OperationalBeliefState` | worksites, incidents, sites, evidence, resources, road_states, `state_version`, `scenario_time` |
| Scenario execution index | arrival nodes, staging node, resource roster — static routing config |
| **Save** | SaveDecision, Assignment, Route, MovementState, reassignment proposals |

`SaveController` keeps no copy of belief facts and never advances `state_version`;
there is a test asserting it has no `worksites` / `incidents` / `road_states`
attributes. Static routing facts come from
`data/scenarios/caraballeda/` via `execution.py`, never from invented worksite
metadata.

`beliefadapter.py` builds a belief state from the pack so Save can be exercised
before the live fusion path is wired in. It is a **stand-in, not a second source of
truth**: Sites come from `operational.site_registry.ScenarioPackSiteRegistry`,
Worksites from `operational.worksite.materialize_worksite`, and the result is
validated with `operational.validation.validate_belief_state`. Replace it with the
live `operational.belief_state` pipeline and nothing in the controller changes.

## Road condition

Three states, from the frozen `road_state.schema.json`:

| Status | Effect on routing |
| --- | --- |
| `open` | normal traversal cost |
| `limited` | **still traversable**, cost × `travel_cost_multiplier` |
| `blocked` | removed from the search; no multiplier can buy passage |

Only BLOCKED changes reachability. Unassessed edges use the pack's own
`baseline_road_policy.assume_open_unless_overridden` — a simulation baseline, and
explicitly *not* a claim that `GraphStore.assess_route()` should treat a missing
assessment as OPEN.

Static OSM access (a footway, `motor_vehicle=no`) is separate: it decides whether an
edge is in the vehicle network at all, and is not a fourth road state.

## Routing

`route_to_node` snaps the origin — a unit's live `MovementState.position` — onto the
nearest **edge**, not the nearest junction, and seeds the search from both ends of
that edge with partial costs. A unit part-way along a long edge can be ~60 m from
its nearest junction, so junction snapping made a mid-journey reroute visibly
teleport the icon backwards. Destinations are the pack's audited `arrival_node_id`
for each worksite; `GraphStore.access_connections` stay non-routable, as the pack
requires.

The search is A\* over travel time whose successor function refuses to expand a
BLOCKED edge, so avoidance is structural rather than a penalty a short path can
out-weigh. With a zero heuristic it is exactly Dijkstra.

Edge identities stay the OSM `osm:way:N:segment:A-B` strings, so a Route can be
handed back to `GraphStore.assess_route` and the dashboard can colour the same edge,
because both sides key on the same id.

No Google API is called. Google Maps, when a token is configured, supplies satellite
imagery only.

## Scoring

Specification weights, unchanged:

```
SavePriority(w)        = 0.40 V + 0.25 T + 0.20 H + 0.15 C
AssignmentUtility(r,w) = SavePriority(w) - 0.25 Travel - 0.20 Risk - 0.20 Mismatch
ReassignmentGain       = AU(r,new) - AU(r,current) - SwitchCost      threshold 0.15
```

Criticality comes from `metadata.search_features.criticality` /
`scenario_operational_overlay.scenario_criticality`, never from `Site.criticality` —
canonical OSM Sites carry `0` with `criticality_assessment: unknown` by design, so
reading that field would score every worksite at zero.

The scenario freezes one generic rescue class, so `capability_mismatch` is 0 for
every team and travel cost and risk decide the assignment. The term is kept so the
formula stays whole if specialised classes arrive.

`planner.py` is pure: no clock, no randomness, no I/O.

## Route change versus objective change

- **Same objective, new route** (a road became BLOCKED) → automatic, logged, no approval.
- **Different objective** → a proposal behind a 30-second wall-clock gate.

On approval the proposal is **fully revalidated** rather than trusting its stored
numbers: deadline, the unit still pursuing the same objective, the target still
present / unresolved / still past the evidence gate / unclaimed, route feasibility
from the unit's current position, and ReassignmentGain still above threshold. Any
failure invalidates it with a reason. Approval then routes from where the unit is
*now*, not where it stood when the proposal was raised.

## Demo path

```
E07  W1 passes the evidence gate -> Rescue Team Bravo assigned -> route from staging
E10  R1 BLOCKED -> old route superseded -> objective still W1 -> no approval
     -> reroute from MovementState.position
```

`verify` reproduces the pack's own frozen numbers: staging → W1 baseline 1382 m
(computed 1392 m), and at E10 remaining 1010 m → 1609 m (computed 1017 → 1612).

## Not done here

`server.py` is a **development harness**, not the production integration: it adapts
the controller to HTTP + SSE so the existing dashboard has a live feed today. The
intended home for these operations is the main API. Nothing in `controller.py`
depends on it.

Turn restrictions and conditional OSM access rules are not evaluated. The final
approach from an arrival node to the building is treated as an unassessed walk-in;
the pack is explicit that an arrival node is not a verified entrance. This is a
hackathon planning layer, not certified emergency navigation.

Source: <https://download.geofabrik.de/south-america/venezuela.html>
(c) OpenStreetMap contributors, ODbL 1.0. Retain attribution in UI and exports.
