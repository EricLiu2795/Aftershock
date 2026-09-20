"""Canonical scan coverage shared by dispatch, retargeting and the target read model."""
ACTIVE_PHASES = frozenset({'en_route', 'verifying', 'reporting'})


def inspected_sites(record):
    result = set(record.get('inspected_site_ids', []))
    missions = list(record.get('missions', [])) + [h['mission'] for h in record.get('history', [])]
    for mission in missions:
        if any(e['event_type'] in {'DRONE_VERIFICATION_COMPLETED', 'DRONE_REPORT_READY'} for e in mission.get('events', [])):
            result.add(mission['site_id'])
    return result


def covered_sites(record):
    """Inspected sites plus the site every live mission already owns."""
    return inspected_sites(record) | {m['site_id'] for m in record.get('missions', [])}


def dispatch_uninspected(record):
    """Assign eligible idle drones to uninspected canonical sites using existing Search.

    Shared by the canonical belief path and the standalone tracking harness so a
    canonical Site is never inspected twice and a finished scan can chain onwards.
    """
    from copy import deepcopy
    from .planner import plan_search
    from .runtime import start_mission, apply_missions, advance_mission
    out=deepcopy(record)
    out['inspected_site_ids']=sorted(inspected_sites(out))
    covered=covered_sites(out)
    view=deepcopy(out['state'])
    view['worksites']=[w for w in view['worksites'] if w['site_id'] not in covered]
    previous = {m['resource']['resource_id']: m for m in out['missions']}
    # Mission phase, not the caller's snapshot, decides whether a drone is still committed.
    for resource in view['resources']:
        old = previous.get(resource['resource_id'])
        if old and old['phase'] in ACTIVE_PHASES:
            resource['status'] = old['phase']
        elif old and old['phase'] == 'returning' and old['assignment']['status'] == 'completed':
            resource['status'] = 'idle'
            view['active_routes'] = [route for route in view['active_routes'] if route['resource_id'] != resource['resource_id']]
    for movement in view['movement_states']:
        resource=next((r for r in view['resources'] if r['resource_id']==movement['resource_id']),None)
        if resource is None:continue
        old = previous.get(movement['resource_id'])
        if old and old['phase']=='returning' and resource['status']=='idle':
            movement.update(movement_mode='stationary', speed_mps=0, heading_deg=None)
        if resource['status']=='idle' and movement['movement_mode']=='stationary':
            movement['last_updated_at']=view['scenario_time']
    plan=plan_search(view)
    out['latest_search_plan']=plan
    new=[start_mission(plan,a['resource_id'],view,
         home_position=previous.get(a['resource_id'],{}).get('home_position')) for a in plan['assignments']]
    if new:
        replaced={m['resource']['resource_id'] for m in new}
        for old in out['missions']:
            if old['resource']['resource_id'] in replaced:
                out['history'].append({'reason':'NEXT_SEARCH_TASK','mission':old})
        out['missions']=[m for m in out['missions'] if m['resource']['resource_id'] not in replaced]+new
    # A completed scan stays at the site just long enough to score its next task.
    # Only unmatched drones resume the ordinary return-to-base lifecycle.
    out['missions']=[advance_mission(m, out['state']['scenario_time'])
        if m['phase']=='idle' and m['return_route'] is None and m['drone_report'] is not None
        else m for m in out['missions']]
    out['state']=apply_missions(out['state'],out['missions'],out['state']['state_version'])
    return out


def publish_priorities(record):
    """Persist numbered ranks from actual planner candidates, not report arrival order.

    Active targets keep their last dispatch score while busy drones are excluded from
    new assignments. Completed scan IDs are removed from the numbered queue.
    """
    from .planner import score_worksite
    inspected = inspected_sites(record)
    record['inspected_site_ids'] = sorted(inspected)
    previous = {p['site_id']:p for p in record.get('target_priorities', [])}
    explanations = {e['target_worksite_id']:e for e in record.get('latest_search_plan', {}).get('explanations', [])}
    sites = {s['site_id']:s for s in record['state']['sites']}
    active = {m['site_id'] for m in record['missions'] if m['phase'] in ACTIVE_PHASES}
    rows = []
    for work in record['state']['worksites']:
        sid = work['site_id']
        if sid in inspected or sid not in sites: continue
        explanation = explanations.get(work['worksite_id'])
        candidates = explanation['candidates'] if explanation else []
        score = max((c['final_utility'] for c in candidates), default=previous.get(sid, {}).get('score', score_worksite(work)[2]['total']))
        rows.append({'site_id':sid, 'worksite_id':work['worksite_id'], 'display_name':work['display_name'],
                     'position':sites[sid]['centroid'], 'score':score, 'assigned':sid in active,
                     'people_count':work.get('metadata',{}).get('building_priority',{}).get('people_count')})
    rows.sort(key=lambda p:(-p['score'],p['worksite_id']))
    record['target_priorities'] = [dict(p, rank=i+1) for i,p in enumerate(rows)]
    return record
