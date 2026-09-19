# Aftershock contracts v2

`/contracts` is the single source of truth for payloads crossing AI
ingestion, location resolution, incident fusion, the simulator, Search, Save,
execution, and the frontend. The canonical format is JSON Schema Draft
2020-12, and every top-level shared payload uses `schema_version: "2.0"`.

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
| `route.json` | `route.schema.json` |
| `assignment.json` | `assignment.schema.json` |
| `reassignment_pending.json`, `reassignment_expired.json`, `reassignment_invalidated.json` | `reassignment.schema.json` |
| `human_decision.json` | `human_decision.schema.json` |

Install the existing lightweight development dependency and run validation:

```text
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -p "test_*.py"
```
