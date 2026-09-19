# Sofia Search layer

This module implements Sofia's deterministic information-acquisition scope:
OperationalBeliefState -> SearchDecision -> drone verification task, with direct flight
routes and an explicit-time drone lifecycle. It does not implement Save scoring,
ground routing, location resolution, incident fusion or a UI.

## Start here

From the repository root, use Python 3.12+ and the existing requirements.txt dependency:

```sh
python -m search_planner.demo data/search_demo
python -m search_planner plan data/search_demo/input_state.json /tmp/search-plan.json
python -m search_planner dispatch data/search_demo/input_state.json /tmp/search-plan.json drone-2 /tmp/mission.json
python -m unittest discover -s tests -p 'test_*.py'
```

`data/search_demo/search_plan.json` is the frontend integration example. It uses the
existing Los Angeles contract fixture coordinates and explicitly simulated observations;
it is not actual Venezuelan damage evidence. The full lifecycle appears in
`mission_timeline.json` and ends with a canonical `updated_state.json`.

## Pure planner API

```python
from search_planner import SearchConfig, plan_search
plan = plan_search(operational_belief_state, SearchConfig())
```

No network or model calls, random IDs or wall-clock reads occur. Input snapshots are not
mutated. Identity hashes and tie breaks are deterministic; reordering top-level entity
arrays does not change results. Duplicate entity identities fail validation.

### Compatible outputs

Existing SearchDecision is a closed schema. Its existing score_breakdown cannot carry
all the new terms or reason codes. Rather than changing a teammate's contract, the new
`contracts/search_plan.schema.json` wraps:

- `decisions`: unchanged canonical SearchDecision payloads.
- `explanations`: full numeric traces and reason_codes, joined by decision_id.
- `tasks`: canonical SearchTask for selected pairs.
- `assignments`: canonical proposed Assignment linked by source_decision_id.
- `routes`: canonical planned Route with route_type flight and a direct two-point path.
- `exclusions`: why a resource or worksite could not participate.
- `config`, `state_version`, `state_fingerprint`, `created_at`: reproducibility context.

There is at most one selected drone per worksite and one worksite per drone per plan.
The caller dispatches the proposed work, then applies mission updates to its operational
state before replanning. Decisions for unselected eligible worksites retain their priority,
full candidate utilities and reasons; no execution task is created for them.

## Exact deterministic explanations

SearchPriority = potential_impact * uncertainty + 0.25 * criticality + 0.15 * evidence_conflict
DroneUtility = SearchPriority - 0.30 * normalized_verification_eta

`explanations[].score_breakdown` includes all four input features,
impact_uncertainty_term, criticality_contribution, evidence_conflict_contribution,
search_priority, total, verification_eta_s, verification_eta, eta_penalty, drone_utility.
`score` is selected DroneUtility; it is null if no drone was selected. `total` is the
SearchPriority, not the final DroneUtility. The canonical decision.verification_eta
contains the normalized feature, not seconds. Candidates expose both units too.

For inputs 0.85, 0.80, 0.60 and 0.53, the terms are 0.68 + 0.15 + 0.0795 = 0.9095.
At normalized ETA 0.60, utility is 0.9095 - 0.18 = 0.7295. Formatting to two decimals is
only a frontend display choice; selection uses the unrounded calculated values.
Decimal arithmetic is used for score contributions; JSON numbers retain standard
floating-point representation, so reconciliation tests use numerical tolerance.

Fixed codes in stable order:
- uncertainty > 0.7: HIGH_UNCERTAINTY
- potential_impact > 0.7: HIGH_POTENTIAL_IMPACT
- evidence_conflict > 0.5: CONFLICTING_EVIDENCE
- criticality > 0.7: CRITICAL_FACILITY
- fallback: VERIFICATION_RECOMMENDED

Selection appends DRONE_SELECTED, NO_AVAILABLE_DRONE, UTILITY_BELOW_THRESHOLD or
DRONES_ALLOCATED. `decisions[].reasons` uses fixed English templates. AJ may localize the
same codes in the UI. No LLM is used to generate explanations.

### Features supplied by the information layer

Junkun can populate the already-permitted `Worksite.metadata.search_features`:

```json
{"potential_impact":0.85,"uncertainty":0.80,"criticality":0.60,"evidence_conflict":0.53}
```

All values must be finite numbers in [0,1]. The new search_features schema documents the
complete bundle; the planner also permits partial overrides. Unknown keys are rejected.
The planner exposes the origin of each feature in feature_sources.

Without overrides, documented MVP heuristics use:
- Potential impact: none 0; unknown 0.5; possible individual 0.3; possible multiple 0.6;
  confirmed individual 0.5; confirmed multiple 0.8. These are scenario scores, not casualty
  estimates or clinical probabilities.
- Uncertainty: max(evidence uncertainty, fraction of victim/structure/access summaries
  that are unknown). Evidence uncertainty: potential 1; unverified 0.8; corroborated 0.4;
  verified 0.
- Criticality: Worksite.criticality. Placeholder map criticality must be replaced upstream
  if the scenario needs meaningful facility priority.
- Evidence conflict: 0 unless explicitly provided by the fusion layer. A count range is
  not automatically treated as contradictory evidence.

### ETA and dispatch policy

Verification ETA in seconds = great-circle flight time + verification_duration_s.
Normalize with min(ETA seconds / eta_normalization_s, 1). Defaults: verification 30 s,
reporting 2 s, ETA horizon 600 s, minimum utility 0, maximum movement age 300 s.
These configurable simulation choices are not operational standards. Saturating ETA can
create distant ties; change the documented normalization horizon for the scenario.

Only idle, unassigned drones with stationary, current MovementState and positive explicit
speed are eligible. Speed accepts m_per_s, km_per_h and minutes_per_km. Busy/returning
resources, missing/future/stale positions and conflicting active routes are excluded.
Resolved worksites, missing canonical sites, already-verifying targets, and worksites with
zero uncertainty and zero conflict are excluded. All eligible drone/worksite pairs are
sorted by descending utility, then ascending worksite_id and drone_id. Greedy selection
above the threshold is deterministic but is not a globally optimal matching algorithm.

Task selection: unknown access alone -> check_access; unknown structure alone ->
assess_damage; otherwise verify_worksite. This module does not generate road scan_route
tasks because no road-scan target contract or corridor input is supplied to Search.

## Flight and lifecycle API

```python
from search_planner import start_mission, advance_mission, apply_missions
mission = start_mission(plan, "drone-2", operational_belief_state)
mission = advance_mission(mission, simulation_timestamp, report=optional_drone_json)
updated = apply_missions(operational_belief_state, [mission], "caller-owned-state-version")
```

start_mission recomputes and checks the full plan against the current snapshot. A changed
snapshot or edited plan requires replanning. The mission records its home position at
launch unless an explicit home_position is provided. Drones use direct great-circle
flight and do not consult the road graph. Antipodal routes need a waypoint and are rejected.

Lifecycle: en_route -> verifying -> reporting -> returning -> idle. Movement is flight
while outbound/returning and stationary during verification/reporting. Arrival is exact;
positions, heading, distances, ETA and progress are derived from absolute simulation time.
Large time steps cross all applicable boundaries without frame-rate-dependent drift.
Report publication completes the Assignment. The return route retains the mission's
worksite_id for provenance even though its end_position is home. On return, the drone
becomes idle and clears current assignment/worksite IDs. Original resource movement_state_id
is preserved; the existing MovementState schema itself is keyed by resource_id.

No report is synthesized from ground truth. An external DroneObservationInput may arrive
after arrival; publication occurs after both verification and report receipt, plus the
reporting duration. Missing reports hold REPORTING indefinitely. Reports must match the
drone and target (canonical site or coordinates within 100 m), be timestamped at/after
arrival and not in the future. Identical retries are accepted; conflicting reports are
rejected. Infrared origin does not automatically set verification_status to verified.

DRONE_REPORT_READY contains the raw drone input and canonical StructuredObservations
converted by the existing geograph.drone normalizer. Deliver either the raw report into
your shared adapter or these observations into the information pipeline, not both without
deduplication. Junkun owns resolution/fusion/Worksite updates. Sofia does not rewrite belief
facts or fabricate human-approved rescue changes.

Mission events are cumulative and have stable event_id values. Consumers must deduplicate
by event_id when publishing. JSON mission checkpoints survive restart; CLI writes use an
atomic replacement. This is a local file API, not a deployed event bus or multi-process
reservation service. Use a single state owner or transactional dispatch in the integrator.

apply_missions replaces only drone ResourceUnit, MovementState and active flight Routes,
plus the caller-supplied global version/time. It preserves Worksite/evidence and ground-unit
state. All missions applied together must be ticked to the same time. Assignments and
completed routes remain in the mission output for the execution layer to store.

### Tick and checkpoint commands

```sh
python -m search_planner tick /tmp/mission.json /tmp/mission.json --at 2026-09-19T14:21:00Z
python -m search_planner tick /tmp/mission.json /tmp/mission.json --at 2026-09-19T14:21:02Z --report report.json
python -m search_planner apply state.json updated-state.json --missions /tmp/mission.json --state-version state-211
```

Caller timestamps and reports must match the actual mission. Do not call the planner on
every UI frame: tick/interpolate movement frequently, replan on meaningful state changes.
The state owner decides version increments and report-driven reevaluation. On a returned
drone, upstream fusion must reflect the completed assessment; otherwise the same unresolved
information gap can legitimately be selected again. No hidden cooldown is imposed.

## Ownership and remaining integration

- Sofia: this module, deterministic decisions/explanations and drone mission behavior.
- Junkun: explicit search features when available, canonical identities, fusion and the
  authoritative OperationalBeliefState.
- AJ: render supplied components/reason codes and authoritative movement updates.
- Anthony: Save scoring, rescue allocation, ground routes and human reassignment gate.

This implementation adds new files only. Existing SearchDecision, Worksite, movement,
route, resource, assignment and road-condition schemas are unchanged. Road conditions
remain blocked/limited/open. No Google API, database migration, frontend change or hosted
service is needed to run the deterministic demo.
