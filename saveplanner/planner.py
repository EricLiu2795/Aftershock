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
        "criticality": clamp(float(worksite.get("criticality") or 0.0)),
    }
    score = sum(SAVE_WEIGHTS[key] * value for key, value in breakdown.items())
    return score, breakdown


def travel_cost(seconds):
    """Normalised 0..1 travel cost. Unreachable worksites never reach this function."""
    return clamp(seconds / TRAVEL_HORIZON_S)


def responder_risk(worksite, active_incident_types=()):
    """Risk of sending this crew here, as opposed to the value of arriving.

    Two contributions: the hazard waiting at the worksite, and how far the crew must
    cover the last leg on foot from the nearest junction, which is unassessed ground.
    The approach distance is a property of the worksite, not of the route, so it is
    read from the worksite and never attached to the frozen Route contract.
    """
    risk = 0.6 * hazard_severity(worksite, active_incident_types)
    approach_m = (worksite.get("metadata") or {}).get("access_distance_m", 0.0)
    risk += 0.4 * clamp(approach_m / 200.0)
    return clamp(risk)


def capability_mismatch(resource, worksite, active_incident_types=()):
    """0.0 when the unit is equipped for the task; equivalent units all score 0."""
    tags = set(resource.get("capability_tags") or [])
    if not tags:
        return 0.0
    types = set(active_incident_types)
    required = set()
    if "trapped_victims" in types or (worksite.get("structural_summary") or {}).get("severity") in {"major", "severe"}:
        required.add("technical_rescue")
    if "medical_need" in types:
        required.add("medical")
    if "fire_or_secondary_hazard" in types:
        required.add("hazmat")
    if not required:
        return 0.0
    return clamp(len(required - tags) / len(required))


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
