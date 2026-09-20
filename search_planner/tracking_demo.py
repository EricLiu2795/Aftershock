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
    state['sites'] = []
    state['worksites'] = []
    for movement in state['movement_states']:
        seed = int(sha256((session_id+movement['resource_id']).encode()).hexdigest()[:12],16)
        movement['position'] = {'latitude':10.619 + (seed % 1000) / 1000000,
                                'longitude':-66.848 + ((seed // 1000) % 1000) / 1000000}
    return {'state':state, 'missions':[], 'proposals':[], 'history':[], 'cooldowns':{},
            'last_wall_at':stamp(wall_now), 'tracking_demo':True, 'scanning_enabled':False}


def dispatch_tracking(record):
    """Assign eligible idle drones to unscanned canonical sites using existing Search."""
    from copy import deepcopy
    from .coverage import dispatch_uninspected
    out=deepcopy(record)
    out['scanning_enabled']=True
    return dispatch_uninspected(out)


def sync_intake(record, intake, stored_inspections=(), buildings=None):
    """Demo-only queue adapter; does not fuse Incidents or overwrite coordinator state.

    Keep the demo's existing scenario-count priority inputs.
    """
    if not record.get('tracking_demo'):return record
    from copy import deepcopy
    out=deepcopy(record); state=out['state']; now=state['scenario_time']
    sites={s['site_id']:s for s in state['sites']}
    works={w['site_id']:w for w in state['worksites']}
    from .building_priority import enrich_worksite
    from .scenario_buildings import scenario_buildings
    scenario=buildings if buildings is not None else scenario_buildings()
    stored={r['site_id']:r for r in stored_inspections}
    grouped={}
    for item in intake:
        grouped.setdefault(item['site']['site_id'], []).append(item)
    for sid, items in sorted(grouped.items()):
        item=items[0]
        site=item['site'];validate('site.schema.json',site)
        observations=[o for i in items for o in i['observations']]
        if site['site_id'] in works:
            enrich_worksite(works[site['site_id']], observations, stored.get(site['site_id']), scenario.get(site['site_id']))
            continue
        for observation in observations:validate('observation.schema.json',observation)
        if not any(o['incident_type'] in {'trapped_victims','structural_damage'} for o in observations):continue
        work={'schema_version':version(),'worksite_id':'scan:'+site['site_id'],'site_id':site['site_id'],
              'display_name':site['display_name'],'evidence_status':'unverified','operational_status':'unassessed',
              'active_incident_ids':[],'evidence_ids':[],'victim_summary':{'indication':{'type':'unknown'}},
              'structural_summary':{'severity':'unknown'},'access_summary':{'status':'unknown'},
              'criticality':site.get('criticality',0),'assigned_resource_ids':[],'created_at':now,'updated_at':now,
              'metadata':{'scan_queue_only':True,'source_report_id':item['report_id'],'source_observation_ids':[o['observation_id'] for o in observations]}}
        enrich_worksite(work, observations, stored.get(site['site_id']), scenario.get(site['site_id']))
        sites[site['site_id']]=site;works[site['site_id']]=work
    state['sites']=list(sites.values());state['worksites']=list(works.values())
    idle={r['resource_id'] for r in state['resources'] if r['status']=='idle'}
    for movement in state['movement_states']:
        if movement['resource_id'] in idle and movement['movement_mode']=='stationary':
            movement['last_updated_at']=now
    validate('operational_belief_state.schema.json',state)
    return out
