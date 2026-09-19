"""Deterministic Save-layer scoring, exactly as specified in the architecture freeze.

Nothing here calls an LLM and nothing here is stochastic: the same
OperationalBeliefState always produces the same SaveDecision, which is what makes
the score breakdown in the UI an explanation rather than a narration.

Weights come from architecture specification sections 19, 20 and 22:

    SavePriority(w)        = 0.40 V + 0.25 T + 0.20 H + 0.15 C
    AssignmentUtility(r,w) = SavePriority(w) - 0.25 Travel - 0.20 Risk - 0.20 Mismatch
    ReassignmentGain       = AU(r,new) - AU(r,current) - SwitchCost
    SwitchCost             = 0.5 ProgressLoss + 0.5 AdditionalTravel
"""
from __future__ import annotations

SAVE_WEIGHTS = {"victim_impact": 0.40, "time_sensitivity": 0.25, "hazard_severity": 0.20, "criticality": 0.15}
UTILITY_WEIGHTS = {"travel_cost": 0.25, "responder_risk": 0.20, "capability_mismatch": 0.20}
REASSIGNMENT_THRESHOLD = 0.15

# Only these evidence states may consume rescue capacity (specification section 19).
# Weaker evidence is a Search problem, not a Save problem.
ELIGIBLE_EVIDENCE = {"corroborated", "verified"}

# Section 19 simulation ranking values. These are ranking weights for a demo,
# not survival probabilities.
VICTIM_IMPACT = {
    "none": 0.0,
    "unknown": 0.15,
    "possible_individual": 0.3,
    "possible_multiple": 0.6,
    "confirmed_individual": 0.7,
    "confirmed_multiple": 0.9,
}
HAZARD_SEVERITY = {"none": 0.0, "minor": 0.2, "major": 0.6, "severe": 0.9, "unknown": 0.3}

# Travel time at which travel cost saturates at 1.0. Caraballeda is ~2 km across,
# so a quarter hour of driving already means "effectively far away".
TRAVEL_HORIZON_S = 900.0


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def victim_impact(worksite):
    """V: how many people the current evidence suggests are at stake."""
    summary = worksite.get("victim_summary") or {}
    indication = (summary.get("indication") or {}).get("type", "unknown")
    score = VICTIM_IMPACT.get(indication, 0.15)
    estimate = summary.get("best_estimate") or summary.get("count_max") or 0
    if estimate >= 5:
        score += 0.1            # large/high-impact situation, section 19
    return clamp(score)


def incident_types(worksite, incidents=()):
    """Active incident types for a worksite, from canonical Incident records.

    Read from `OperationalBeliefState.incidents` filtered by the worksite's own
    `active_incident_ids`. Save does not keep its own copy of incident labels.
    """
    active = set(worksite.get("active_incident_ids") or [])
    return {incident["incident_type"] for incident in incidents
            if incident.get("incident_id") in active
            and incident.get("status") not in {"resolved", "cancelled"}}


def criticality(worksite):
    """Scenario criticality from the operational overlay, never Site.criticality.

    Canonical OSM Sites carry `criticality: 0` with `criticality_assessment:
    unknown` on purpose -- OSM does not know which building matters operationally.
    The scenario supplies it as an overlay, which `materialize_worksite` surfaces
    as a search feature. Reading the OSM field would score every worksite at zero.
    """
    metadata = worksite.get("metadata") or {}
    features = metadata.get("search_features") or {}
    if isinstance(features.get("criticality"), (int, float)):
        return clamp(float(features["criticality"]))
    overlay = metadata.get("scenario_operational_overlay") or {}
    if isinstance(overlay.get("scenario_criticality"), (int, float)):
        return clamp(float(overlay["scenario_criticality"]))
    return clamp(float(worksite.get("criticality") or 0.0))


def hazard_severity(worksite, active_incident_types=()):
    """H: structural danger, raised to the maximum by an active secondary hazard."""
    severity = (worksite.get("structural_summary") or {}).get("severity", "unknown")
    score = HAZARD_SEVERITY.get(severity, 0.3)
    if "fire_or_secondary_hazard" in set(active_incident_types):
        score = 1.0
    return clamp(score)


def time_sensitivity(worksite, active_incident_types=(), minutes_since_report=0.0):
    """T: how much survival odds decay while this worksite waits.

    Trapped and medical incidents start urgent; everything grows more urgent with
    elapsed time, saturating at one hour so an old low-value worksite cannot
    overtake a fresh high-value one on age alone.
    """
    types = set(active_incident_types)
    base = 0.3
    if "trapped_victims" in types:
        base = 0.8
    elif "medical_need" in types:
        base = 0.7
    elif "evacuation_need" in types:
        base = 0.5
    return clamp(base + 0.2 * clamp(minutes_since_report / 60.0))


def save_priority(worksite, active_incident_types=(), minutes_since_report=0.0):
    """SavePriority with its per-feature breakdown, for the explainability panel."""
    breakdown = {
        "victim_impact": victim_impact(worksite),
        "time_sensitivity": time_sensitivity(worksite, active_incident_types, minutes_since_report),
        "hazard_severity": hazard_severity(worksite, active_incident_types),
        "criticality": criticality(worksite),
    }
    score = sum(SAVE_WEIGHTS[key] * value for key, value in breakdown.items())
    return score, breakdown


def travel_cost(seconds):
    """Normalised 0..1 travel cost. Unreachable worksites never reach this function."""
    return clamp(seconds / TRAVEL_HORIZON_S)


def responder_risk(worksite, active_incident_types=(), approach_m=0.0):
    """Risk of sending this crew here, as opposed to the value of arriving.

    Two contributions: the hazard waiting at the worksite, and the walk from the
    scenario's arrival node to the building, which is unassessed ground -- the pack
    is explicit that an arrival node is not a verified entrance. `approach_m` is
    supplied by the caller from the scenario execution index; it is neither stored
    on the Worksite nor attached to the frozen Route contract.
    """
    risk = 0.6 * hazard_severity(worksite, active_incident_types)
    risk += 0.4 * clamp(approach_m / 200.0)
    return clamp(risk)


# The scenario freezes a single generic rescue class (`vehicle_policy.classes =
# ["generic_rescue_team"]`, `no_light_heavy_split: true`). Inventing sub-classes
# would manufacture mismatch penalties the scenario explicitly does not have.
GENERIC_CAPABILITY = "ground_rescue"


def capability_mismatch(resource, worksite, active_incident_types=()):
    """0.0 for any generic ground rescue team, which is all the scenario defines.

    Kept as a term so the AssignmentUtility formula stays whole and a future
    specialised class can be scored, but with one capability class every eligible
    unit scores 0 and travel cost and risk decide the assignment.
    """
    tags = set(resource.get("capability_tags") or [])
    return 0.0 if not tags or GENERIC_CAPABILITY in tags else 1.0


def assignment_utility(priority, cost, risk, mismatch):
    """AssignmentUtility from section 20."""
    return (priority
            - UTILITY_WEIGHTS["travel_cost"] * cost
            - UTILITY_WEIGHTS["responder_risk"] * risk
            - UTILITY_WEIGHTS["capability_mismatch"] * mismatch)


def switch_cost(progress_loss, additional_travel):
    """SwitchCost from section 22: abandoning work already done is expensive."""
    return 0.5 * clamp(progress_loss) + 0.5 * clamp(additional_travel)


def reassignment_gain(utility_new, utility_current, progress_loss, additional_travel):
    """ReassignmentGain. Only a gain above REASSIGNMENT_THRESHOLD reaches a human."""
    return utility_new - utility_current - switch_cost(progress_loss, additional_travel)
