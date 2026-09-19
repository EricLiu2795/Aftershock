"""Explicit opt-in tracking demo: real scenario sites, unknown initial findings."""
import json
from .common import validate, version
from .verification import PACK


def tracking_state():
    manifest = json.loads((PACK/'scenario.json').read_text())
    active = json.loads((PACK/'active_sites.json').read_text())['active_worksites']
    targets = [w for w in active if w['worksite_key'] in {'W2', 'W7'}]
    now = manifest['start_time']
    works = []
    for target in targets:
        site = target['site']
        works.append({'schema_version':version(), 'worksite_id':'tracking-'+target['worksite_key'],
          'site_id':site['site_id'], 'display_name':target['worksite_key']+' · '+site['display_name'],
          'evidence_status':'potential', 'operational_status':'unassessed', 'active_incident_ids':[], 'evidence_ids':[],
          'victim_summary':{'indication':{'type':'unknown','count_min':None,'count_max':None}},
          'structural_summary':{'severity':'unknown'}, 'access_summary':{'status':'unknown'},
          'criticality':site.get('criticality',0), 'assigned_resource_ids':[], 'created_at':now, 'updated_at':now,
          'metadata':{'simulation':True,'purpose':'Explicit drone tracking demonstration; no initial casualty claim'}})
    state = {'schema_version':version(), 'state_version':'tracking-demo-1', 'scenario_time':now, 'updated_at':now,
      'sites':[w['site'] for w in targets], 'worksites':works, 'incidents':[],
      'resources':[r for r in manifest['resources'] if r['resource_type']=='drone_unit'],
      'movement_states':[m for m in manifest['initial_movement_states'] if m['resource_id'] in {'drone-1','drone-2'}],
      'road_states':[], 'active_routes':[], 'pending_reassignment_proposals':[]}
    validate('operational_belief_state.schema.json',state)
    return state


def prepare_tracking(wall_now, session_id):
    """Seed reproducible arbitrary starting points; no mission has launched yet."""
    from hashlib import sha256
    from .common import stamp
    state = tracking_state()
    for movement in state['movement_states']:
        seed = int(sha256((session_id+movement['resource_id']).encode()).hexdigest()[:12],16)
        movement['position'] = {'latitude':10.619 + (seed % 1000) / 1000000,
                                'longitude':-66.848 + ((seed // 1000) % 1000) / 1000000}
    return {'state':state, 'missions':[], 'proposals':[], 'history':[], 'cooldowns':{},
            'last_wall_at':stamp(wall_now), 'tracking_demo':True, 'scanning_enabled':False}


def dispatch_tracking(record):
    """Assign eligible idle drones to unscanned canonical sites using existing Search."""
    from copy import deepcopy
    from .planner import plan_search
    from .runtime import start_mission, apply_missions, advance_mission
    out=deepcopy(record)
    out['scanning_enabled']=True
    covered={m['site_id'] for m in out['missions']} | {h['mission']['site_id'] for h in out['history'] if h.get('reason')=='NEXT_SEARCH_TASK'}
    view=deepcopy(out['state'])
    view['worksites']=[w for w in view['worksites'] if w['site_id'] not in covered]
    previous = {m['resource']['resource_id']: m for m in out['missions']}
    for resource in view['resources']:
        old = previous.get(resource['resource_id'])
        if old and old['phase'] == 'returning' and old['assignment']['status'] == 'completed':
            resource['status'] = 'idle'
            view['active_routes'] = [route for route in view['active_routes'] if route['resource_id'] != resource['resource_id']]
    for movement in view['movement_states']:
        resource=next(r for r in view['resources'] if r['resource_id']==movement['resource_id'])
        old = previous.get(movement['resource_id'])
        if old and old['phase']=='returning' and resource['status']=='idle':
            movement.update(movement_mode='stationary', speed_mps=0, heading_deg=None)
        if resource['status']=='idle' and movement['movement_mode']=='stationary':
            movement['last_updated_at']=view['scenario_time']
    plan=plan_search(view)
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


def sync_intake(record, intake):
    """Demo-only queue adapter; does not fuse Incidents or overwrite coordinator state."""
    if not record.get('tracking_demo'):return record
    from copy import deepcopy
    out=deepcopy(record); state=out['state']; now=state['scenario_time']
    sites={s['site_id']:s for s in state['sites']}
    works={w['site_id']:w for w in state['worksites']}
    for item in intake:
        site=item['site'];validate('site.schema.json',site)
        if site['site_id'] in works:continue
        observations=item['observations']
        for observation in observations:validate('observation.schema.json',observation)
        if not any(o['incident_type']=='trapped_victims' and o['victim_indication']['type'] not in {'none'} for o in observations):continue
        work={'schema_version':version(),'worksite_id':'scan:'+site['site_id'],'site_id':site['site_id'],
              'display_name':site['display_name'],'evidence_status':'unverified','operational_status':'unassessed',
              'active_incident_ids':[],'evidence_ids':[],'victim_summary':{'indication':{'type':'unknown'}},
              'structural_summary':{'severity':'unknown'},'access_summary':{'status':'unknown'},
              'criticality':site.get('criticality',0),'assigned_resource_ids':[],'created_at':now,'updated_at':now,
              'metadata':{'scan_queue_only':True,'source_report_id':item['report_id'],'source_observation_ids':[o['observation_id'] for o in observations]}}
        sites[site['site_id']]=site;works[site['site_id']]=work
    state['sites']=list(sites.values());state['worksites']=list(works.values())
    validate('operational_belief_state.schema.json',state)
    return out
