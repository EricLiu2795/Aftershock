# Aftershock contracts v2.1

`/contracts` is the single source of truth for payloads crossing AI
ingestion, location resolution, incident fusion, the simulator, Search, Save,
execution, and the frontend. The canonical format is JSON Schema Draft
2020-12, and every top-level shared payload uses `schema_version: "2.1"`.

Module-private data does not need to live here. Any data crossing a module
boundary must validate against one of these schemas; modules must not maintain
incompatible copies of shared entities.

## Canonical flow

```text
Raw Input
  -> StructuredObservation
  -> LocationResolver
  -> Site
  -> Incident Fusion
  -> Incident
  -> Worksite
  -> OperationalBeliefState
  -> Search Planner / Save Planner
  -> Assignment / ReassignmentProposal
  -> Execution
```

The breaking v2 change is the separation of identities and state:

- **Site** is the canonical physical map entity. `site_id` is the backend
  identity; `display_name` and address are presentation or descriptive data.
- **Incident** is one specific problem or event at one Site. A Site can have
  multiple simultaneous Incidents, such as structural damage, trapped
  victims, and blocked access.
- **Worksite** is the operational rescue aggregate of one Site. It contains
  operational state and references to active incidents and preserved evidence;
  it is not the physical identity or an Incident.

## Responsibilities and principles

1. AI extracts meaning, not identity. AI emits raw-input-derived
   `StructuredObservation` facts and must not invent internal worksite IDs,
   rescue priorities, assignments, or recommended resources.
2. `LocationResolution` maps observations to canonical Site IDs. An unresolved
   observation remains visible in the information feed, but should not directly
   drive precise building-level rescue assignment.
3. Incident Fusion runs after location resolution. It decides whether an
   observation updates an existing Incident or creates a new Incident.
4. Individual evidence and source provenance remain preserved after fusion.
5. Search reduces uncertainty. Save allocates rescue capacity. The frontend
   renders score breakdowns and reasons rather than recomputing planner logic.
6. A target change to a different Worksite requires explicit human approval.
   A route-only change for the same Worksite may update automatically.
7. Reassignment decisions have a 30-real-second window while simulation
   continues. Timeout means `status: "expired"` and defaults to keeping the
   current assignment. An approved reassignment computes a new route from the
   resource's current position.

## Movement and execution contracts

`MovementState` is the canonical runtime source for a resource's current
physical position. `ResourceUnit.movement_state_id` points to the current
state; a ResourceUnit does not maintain an independent runtime position.
MovementState contains execution state only. It does not contain SearchPriority,
SavePriority, assignment recommendations, or rescue objective decisions.

Ground rescue movement follows a road route generated over the static road
graph. The execution flow is:

```text
Assignment -> Route Planner -> road graph -> A* / Dijkstra
  -> road Route -> MovementState -> Simulation Engine
  -> continuously updated current position
```

Ground units cannot interpolate directly through buildings. A road Route uses
`route_type: "road"` and may identify its graph edges with `road_edge_ids`.
Drone movement uses `route_type: "flight"` and may be a direct start-to-target
path or a small waypoint path. MVP contracts do not model wind, flight
physics, collision avoidance, or detailed altitude dynamics.

Movement updates may be continuous or high frequency. Search and Save planner
recomputation remains event-driven; animation frames must not trigger planner
recomputation. Planner-relevant events include a new observation, an Incident
or Worksite update, a blocked road, unit arrival, task completion, assignment
change, approved reassignment, or an aftershock/scenario event. The frontend
may smoothly interpolate markers between backend movement updates.

For the same operational objective, a new route is automatic. For example,
when Bravo is assigned to D3 and a road edge becomes blocked, the system may
reconnect the current `MovementState.position` to the road graph, compute a new
route to D3, supersede the old route, and activate the new route. No
ReassignmentProposal is required because the objective remains D3.

Changing the operational objective requires explicit human approval. A pending
proposal does not freeze movement: while Bravo's proposal from D3 to C4 is
pending, Bravo continues along the D3 route. When approval arrives, the old
route is superseded and the new C4 route starts from `MovementState.position`
at approval time, not from the position recorded when the proposal was created.

MovementMode is separate from Resource status. Rescue Unit statuses are
`available`, `en_route`, `on_site`, `working`, `completed`, and `reassigned`.
Drone statuses are `idle`, `en_route`, `verifying`, `reporting`, and
`returning`. Examples include a rescue unit with `status: "en_route"` and
`movement_mode: "ground_route"`, a verifying drone that is stationary, and a
returning drone with `movement_mode: "flight"`.

Routes may be `planned`, `active`, `completed`, `superseded`, or `cancelled`.
When Route A is replaced by Route B, Route A is `superseded` and Route B is
`active`. `RoadState` describes dynamic conditions on the static road graph;
it does not contain road geometry. A blocked or limited RoadState can trigger
automatic rerouting while the operational objective remains unchanged.

## MVP fusion rules

Fusion runs after deterministic location resolution:

```text
same site_id
+ compatible incident_type
+ reasonable temporal proximity
-> update the existing Incident

otherwise
-> create a new Incident
```

For example, `D3 + structural_damage` followed by another
`D3 + structural_damage` updates one Incident, while `D3 + trapped_victims`
creates a separate Incident on the same Site.

Initial field-level fusion is source-aware and preserves all observations; it
does not simply take maximum values:

- Victim information: verified responder > corroborated independent sources >
  drone indication > single social report.
- Structural state: drone or field assessment > responder radio > social
  report.
- Access state: drone or field confirmation > responder radio > social report.
- Location quality: GPS > canonical map feature > resolved address > named
  landmark > vague area.

For example, a social report of 10 unverified victims and a responder report of
3 verified victims should produce `best_estimate: 3`, `count_min: 3`, and
`count_max: 10`, while retaining both observations and their evidence.
These are MVP fusion heuristics, not official emergency-response standards.

## Planner heuristics

Input features are normalized to `[0, 1]`; composite scores are not required to
remain within `[0, 1]`.

```text
SearchPriority(w) = PotentialImpact * Uncertainty
                 + 0.25 * Criticality
                 + 0.15 * EvidenceConflict

DroneUtility(d,w) = SearchPriority - 0.30 * VerificationETA

SavePriority(w) = 0.40 * VictimImpact
                + 0.25 * TimeSensitivity
                + 0.20 * HazardSeverity
                + 0.15 * Criticality

AssignmentUtility(r,w) = SavePriority
                       - 0.25 * TravelCost
                       - 0.20 * ResponderRisk
                       - 0.20 * CapabilityMismatch

ReassignmentGain = ProposedAssignmentUtility
                 - CurrentAssignmentUtility
                 - SwitchCost

SwitchCost = 0.5 * ProgressLoss + 0.5 * AdditionalTravel
```

The default reassignment threshold is `0.15`. All scoring and fusion weights
are hackathon simulation heuristics only. They are not medical survival
models, official USAR triage formulas, or validated emergency-response
standards.

## Example-to-schema mapping

| Examples | Schema |
| --- | --- |
| `radio_observation.json` | `raw_radio_input.schema.json` |
| `social_observation.json` | `raw_social_input.schema.json` |
| `drone_observation.json` | `drone_observation_input.schema.json` |
| `structured_radio_observation.json`, `structured_social_observation.json`, `structured_drone_observation.json` | `observation.schema.json` |
| `resolved_location.json`, `unresolved_location.json` | `location_resolution.schema.json` |
| `evidence_item.json` | `evidence_item.schema.json` |
| `site_d3.json` | `site.schema.json` |
| `incident_d3_structural.json`, `incident_d3_trapped.json` | `incident.schema.json` |
| `worksite_d3.json` | `worksite.schema.json` |
| `operational_belief_state.json` | `operational_belief_state.schema.json` |
| `search_task.json` | `search_task.schema.json` |
| `search_decision.json` | `search.schema.json` |
| `save_task.json` | `save_task.schema.json` |
| `save_decision.json` | `save.schema.json` |
| `route.json`, `route_drone_d3.json`, `route_bravo_d3_rerouted.json`, `route_bravo_c4_reassigned.json` | `route.schema.json` |
| `movement_state_bravo.json`, `movement_state_drone_flying.json`, `movement_state_drone_stationary.json` | `movement.schema.json` |
| `road_state_blocked.json` | `road_state.schema.json` |
| `assignment.json` | `assignment.schema.json` |
| `reassignment_pending.json`, `reassignment_approved.json`, `reassignment_expired.json`, `reassignment_invalidated.json` | `reassignment.schema.json` |
| `human_decision.json` | `human_decision.schema.json` |
| `radio_finalize_request.json` | `radio_finalize_request.schema.json` |
| `radio_finalize_response.json` | `radio_finalize_response.schema.json` |
| `radio_extraction_result.json` | `radio_extraction_result.schema.json` |
| `radio_transmission.json` | `radio_transmission.schema.json` |
| `radio_session_snapshot.json` | `radio_session_snapshot.schema.json` |
| `dashboard_event.json` | `dashboard_event.schema.json` |

## Radio contract boundaries

Radio finalization accepts only a complete committed transcript and ordered
segment metadata. Session and responder authority are server-owned and are not
accepted in the finalize request. Extraction results contain semantic facts
only; backend identities, coordinates, verification upgrades, priorities, and
resource assignments are rejected as unknown fields.

The `fixtures/radio/simulated_riverside` pack is fictional demo data. Its
Riverside Apartments alias is scenario-scoped and intentionally has no
real-world coordinates. The fixture tests also enforce chronological segment
ordering, count-bound semantics that JSON Schema cannot compare directly, and
single-source provenance across multiple observations.

Extraction retries use `radio.extraction_retrying` with processing state
`retrying`. `radio.failed` is reserved for terminal failures and therefore
requires `failure.retryable: false`.

Install the existing lightweight development dependency and run validation:

```text
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -p "test_*.py"
```
