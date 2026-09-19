# Aftershock contracts

`/contracts` is the single source of truth for data structures that cross the
boundaries between AI ingestion, the simulator, Search, Save, and the
frontend. Cross-module payloads must validate against the JSON Schemas here.
Internal module-only data does not need to live in this directory, but a module
must not maintain an incompatible private copy of a shared entity.

The canonical format is JSON Schema Draft 2020-12 so both Python and
TypeScript implementations can consume the same contracts. `common.schema.json`
contains reusable definitions for locations, evidence, uncertainty-aware
victim indications, and timestamps.

## Contract responsibilities

- AI ingestion emits `StructuredObservation`; it does not emit operational
  commands. A structured drone observation may bypass the LLM.
- Observations are fused into a `Worksite`. Evidence status and operational
  status are separate dimensions; they are not one combined linear state
  machine.
- Search reduces uncertainty by selecting verification work, usually with a
  drone.
- Save allocates rescue capacity to a worksite.
- The frontend renders planner score breakdowns and reasons; it should not
  recompute planner logic.
- A change to an active rescue objective requires explicit human approval. A
  reassignment proposal is a first-class object. Its 30-second decision window
  does not pause the simulation, and timeout keeps the current assignment by
  default. Route-only changes for the same worksite objective do not require a
  reassignment proposal. Relevant state changes may invalidate a pending
  proposal.

## Simulation heuristics

The current example values use these simulation heuristics:

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

The default reassignment threshold is `0.15`. These are simulation heuristics
only. They are not medical survival probabilities, official USAR triage
formulas, or validated emergency-response standards.

Input features may be normalized to `[0, 1]`, but composite scores and utilities
are intentionally not constrained to `[0, 1]` by these schemas.

## Example-to-schema mapping

| Example | Schema |
| --- | --- |
| `radio_observation.json`, `social_observation.json`, `drone_observation.json` | `observation.schema.json` |
| `worksite_d3.json` | `worksite.schema.json` |
| `search_decision.json` | `search.schema.json` |
| `save_decision.json` | `save.schema.json` |
| `assignment.json` | `assignment.schema.json` |
| `reassignment_pending.json`, `reassignment_expired.json`, `reassignment_invalidated.json` | `reassignment.schema.json` |

Run the contract test after installing the small development dependency:

```text
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -p "test_*.py"
```
