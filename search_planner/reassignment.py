"""Dynamic greedy Search with an explicit, fail-closed human approval gate.

Clock arguments are injected for tests. The HTTP bridge supplies server UTC time.
Only EN_ROUTE missions can be interrupted; verification/reporting finish normally.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from .common import identity, index, stamp, time, validate, version
from .flight import flight_seconds
from .planner import SearchConfig, extract_features, plan_search, score_drone, score_search
from .runtime import advance_mission, apply_missions, start_mission

APPROVAL_SECONDS = 30
COOLDOWN_SECONDS = 60
GAIN_THRESHOLD = .15
SWITCH_COST = .05


def initialize(state, wall_now):
    validate('operational_belief_state.schema.json', state)
    plan = plan_search(state)
    missions = [start_mission(plan, a['resource_id'], state) for a in plan['assignments']]
    return {'state': deepcopy(state), 'missions': missions, 'proposals': [], 'history': [],
            'cooldowns': {}, 'last_wall_at': stamp(wall_now), 'plan': plan}


def _view(state, mission, target=None):
    """Private planning view; does not release the actual drone before approval."""
    out = deepcopy(state)
    rid = mission['resource']['resource_id']
    drone = deepcopy(mission['resource'])
    drone.update(status='idle', current_assignment_id=None, current_worksite_id=None)
    movement = deepcopy(mission['movement'])
    movement.update(movement_mode='stationary', route_id=None, speed_mps=0)
    # Other drones still reserve their targets, but cannot take this private candidate.
    out['resources'] = [drone] + [r for r in out['resources'] if r['resource_id'] != rid and r['resource_type'] != 'drone_unit']
    out['movement_states'] = [movement] + [m for m in out['movement_states'] if m['resource_id'] != rid]
    out['active_routes'] = [r for r in out['active_routes'] if r['resource_id'] != rid]
    if target is not None:
        out['worksites'] = [w for w in out['worksites'] if w['worksite_id'] == target]
    return out


def _comparison(state, mission, target):
    works = index(state['worksites'], 'worksite_id')
    current = works.get(mission['worksite_id'])
    if current is None or target not in works:
        return None
    view = _view(state, mission, target)
    plan = plan_search(view, SearchConfig(**mission['config']))
    if not plan['assignments']:
        return None
    features, _ = extract_features(current)
    breakdown = score_search(features)
    config = SearchConfig(**mission['config'])
    eta = flight_seconds(mission['movement']['position'], mission['outbound_route']['path'][-1], mission['speed_mps']) + config.verification_duration_s
    old = score_drone(breakdown['total'], eta, config.eta_normalization_s)
    new = plan['explanations'][0]
    utility = new['score']
    gain = float(Decimal(str(utility)) - Decimal(str(old['final_utility'])) - Decimal(str(SWITCH_COST)))
    return {'current': {**breakdown, **old}, 'proposed': new['score_breakdown'],
            'current_utility': old['final_utility'], 'proposed_utility': utility,
            'switch_cost': SWITCH_COST, 'reassignment_gain': gain, 'plan': plan, 'view': view}


def _evidence(state, mission, target):
    works = index(state['worksites'], 'worksite_id')
    sites = index(state['sites'], 'site_id')
    return identity('evidence', [(works.get(wid), sites.get(works.get(wid, {}).get('site_id')))
                                for wid in [mission['worksite_id'], target]])


def step(record, wall_now, *, state=None, decision=None, report=None, verification=None):
    """Advance movement, expire gates, revalidate, then optionally apply human choice.

    Pending/rejected/expired proposals never mutate a mission's objective or route.
    At exactly expires_at approval is too late. Completed proposal retries are inert.
    """
    out = deepcopy(record)
    now = time(wall_now)
    if now < time(out['last_wall_at']):
        raise ValueError('Server clock moved backwards; refusing approval')
    elapsed = now - time(out['last_wall_at'])
    sim = time(out['state']['scenario_time']) + elapsed
    if state is not None:
        validate('operational_belief_state.schema.json', state)
        if time(state['scenario_time']) < time(out['state']['scenario_time']):
            raise ValueError('Stale operational snapshot')
        # Coordinator owns evidence; this controller owns dispatched drone movement.
        out['state'] = deepcopy(state)
        sim = max(sim, time(state['scenario_time']))
    if decision is not None and decision.get('decision') not in {'approve', 'reject'}:
        raise ValueError('Only explicit approve or reject is accepted')
    if report is not None and report.get('mission_id') not in {m['mission_id'] for m in out['missions']}:
        raise ValueError('Report belongs to an inactive mission')
    def advance(m):
        if report is not None and report['mission_id'] == m['mission_id']:
            return advance_mission(m, stamp(sim), report['report'])
        return verification.advance(m, stamp(sim)) if verification else advance_mission(m, stamp(sim))
    out['missions'] = [advance(m) for m in out['missions']]
    # Remove the coordinator's stale ownership only for controller-owned drones.
    owned = {m['resource']['resource_id'] for m in out['missions']}
    for resource in out['state']['resources']:
        if resource['resource_id'] in owned:
            resource['current_assignment_id'] = None
    out['state']['scenario_time'] = stamp(sim)
    out['state'] = apply_missions(out['state'], out['missions'], out['state']['state_version'])
    out['last_wall_at'] = stamp(now)
    missions = {m['resource']['resource_id']: m for m in out['missions']}
    occupied = {m['worksite_id'] for m in out['missions'] if m['phase'] in {'en_route', 'verifying', 'reporting'}}
    occupied |= {r['current_worksite_id'] for r in out['state']['resources']
                 if r['resource_type'] == 'drone_unit' and r.get('current_worksite_id')}
    if decision is not None and not any(p['proposal']['proposal_id'] == decision.get('proposal_id') for p in out['proposals']):
        raise ValueError('Unknown proposal')
    for entry in out['proposals']:
        p = entry['proposal']; rid = p['resource_id']; m = missions.get(rid)
        if p['status'] != 'pending':
            continue
        comparison = None
        if now >= time(p['expires_at']):
            p['status'] = 'expired'
        elif (m is None or m['phase'] != 'en_route' or m['mission_id'] != entry['mission_id']
              or p['proposed_worksite_id'] in occupied
              or _evidence(out['state'], m, p['proposed_worksite_id']) != entry['evidence']):
            p['status'] = 'invalidated'
        else:
            comparison = _comparison(out['state'], m, p['proposed_worksite_id'])
            if comparison is None or comparison['reassignment_gain'] <= GAIN_THRESHOLD:
                p['status'] = 'invalidated'
        if p['status'] == 'pending' and decision and decision['proposal_id'] == p['proposal_id']:
            p['human_decision'] = {'schema_version': version(), 'decision': decision['decision'],
                                   'decided_at': stamp(now), 'operator_id': decision.get('operator_id')}
            p['status'] = 'approved' if decision['decision'] == 'approve' else 'rejected'
            if p['status'] == 'approved':
                replacement = start_mission(comparison['plan'], rid, comparison['view'], m['home_position'])
                archived = deepcopy(m)
                archived['assignment']['status'] = 'cancelled'
                archived['outbound_route']['route_status'] = 'superseded'
                out['history'].append({'ended_at': stamp(sim), 'reason': 'HUMAN_APPROVED_REASSIGNMENT',
                                       'proposal_id': p['proposal_id'], 'mission': archived,
                                       'replacement_mission_id': replacement['mission_id']})
                entry['approved_comparison'] = {k: v for k, v in comparison.items() if k not in {'plan', 'view'}}
                missions[rid] = replacement
                occupied.discard(m['worksite_id']); occupied.add(replacement['worksite_id'])
        if p['status'] != 'pending':
            out['cooldowns'][rid] = stamp(now + timedelta(seconds=COOLDOWN_SECONDS))
        validate('reassignment.schema.json', p)
    pending = {e['proposal']['resource_id'] for e in out['proposals'] if e['proposal']['status'] == 'pending'}
    reserved = {e['proposal']['proposed_worksite_id'] for e in out['proposals'] if e['proposal']['status'] == 'pending'}
    pairs = []
    for rid, m in sorted(missions.items()):
        if m['phase'] != 'en_route' or rid in pending or now < time(out['cooldowns'].get(rid, stamp(now))):
            continue
        for w in out['state']['worksites']:
            wid = w['worksite_id']
            if wid in occupied or wid in reserved:
                continue
            c = _comparison(out['state'], m, wid)
            if c and c['reassignment_gain'] > GAIN_THRESHOLD:
                pairs.append((rid, wid, c))
    for rid, wid, c in sorted(pairs, key=lambda x: (-x[2]['reassignment_gain'], x[1], x[0])):
        if rid in pending or wid in reserved:
            continue
        m = missions[rid]
        p = {'schema_version': version(), 'proposal_id': identity('drone-retarget', [m['mission_id'], wid, stamp(now)]),
             'resource_id': rid, 'current_worksite_id': m['worksite_id'], 'proposed_worksite_id': wid,
             'current_utility': c['current_utility'], 'proposed_utility': c['proposed_utility'],
             'progress_loss': 0., 'additional_travel': 0., 'switch_cost': SWITCH_COST,
             'reassignment_gain': c['reassignment_gain'], 'threshold': GAIN_THRESHOLD,
             'created_at': stamp(now), 'expires_at': stamp(now + timedelta(seconds=APPROVAL_SECONDS)),
             'based_on_state_version': out['state']['state_version'], 'status': 'pending',
             'reasons': ['Higher verification utility after fixed switching penalty', 'Explicit human approval required within 30 seconds; otherwise keep current task']}
        validate('reassignment.schema.json', p)
        out['proposals'].append({'proposal': p, 'mission_id': m['mission_id'],
            'evidence': _evidence(out['state'], m, wid),
            'score_breakdown': {k: v for k, v in c.items() if k not in {'plan', 'view'}}})
        pending.add(rid); reserved.add(wid)
    out['missions'] = list(missions.values())
    for resource in out['state']['resources']:
        if resource['resource_id'] in owned:
            resource['current_assignment_id'] = None
    out['state'] = apply_missions(out['state'], out['missions'], out['state']['state_version'])
    return out
