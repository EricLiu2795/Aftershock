"""Deterministic, explicitly simulated Search demo based on existing contract fixtures."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from .__main__ import read,save
from .common import stamp,time,version
from .planner import SearchConfig,plan_search
from .runtime import start_mission,advance_mission,apply_missions


def sample_state():
    root=Path(__file__).resolve().parents[1]
    state=read(root/'contracts/examples/operational_belief_state.json')
    state['state_version']='search-demo-1';state['scenario_time']='2026-09-19T14:20:00Z';state['updated_at']=state['scenario_time']
    drone=next(r for r in state['resources'] if r['resource_type']=='drone_unit')
    drone.update(status='idle',current_assignment_id=None,current_worksite_id=None)
    drone['speed']={'value':10,'unit':'m_per_s'}
    position=next(m for m in state['movement_states'] if m['resource_id']==drone['resource_id'])
    position.update(position={'latitude':34.05134,'longitude':-118.24371},movement_mode='stationary',
                    route_id=None,speed_mps=0,route_progress=0,last_updated_at=state['scenario_time'])
    work=state['worksites'][0]
    work['metadata']={'simulation':True,'search_features':{'potential_impact':.85,'uncertainty':.8,'criticality':.6,'evidence_conflict':.53}}
    work['evidence_status']='unverified'
    other=deepcopy(work);other.update(worksite_id='search-demo-W3',display_name='Simulated Worksite W3',site_id='search-demo-site-W3',active_incident_ids=[],evidence_ids=[],assigned_resource_ids=[])
    other['metadata']['search_features']={'potential_impact':.5,'uncertainty':.5,'criticality':.2,'evidence_conflict':0.}
    site=deepcopy(state['sites'][0]);site.update(site_id=other['site_id'],display_name='Simulated Site W3',canonical_source='scenario',canonical_source_id='search-demo-W3',centroid={'latitude':34.055,'longitude':-118.24})
    state['sites'].append(site);state['worksites'].append(other)
    return state


def run_demo(output):
    output=Path(output);state=sample_state();plan=plan_search(state)
    chosen=plan['assignments'][0];mission=start_mission(plan,chosen['resource_id'],state)
    arrival=time(mission['launched_at'])+timedelta(seconds=mission['outbound_route']['estimated_travel_time_s'])
    verify_end=arrival+timedelta(seconds=plan['config']['verification_duration_s'])
    frames=[mission]
    frames.append(advance_mission(frames[-1],stamp(arrival)))
    frames.append(advance_mission(frames[-1],stamp(verify_end)))
    site=next(s for s in state['sites'] if s['site_id']==mission['site_id'])
    report={'schema_version':version(),'input_id':'simulated-search-report-1','timestamp':stamp(verify_end),
            'drone_id':chosen['resource_id'],'location':{**site['centroid'],'resolved_site_id':site['site_id'],'accuracy':'building'},
            'victim_indicator':{'type':'possible_multiple','count_min':None,'count_max':None},
            'structural_damage':'unknown','access_status':'unknown','metadata':{'simulation':True,'sensor_type':'infrared'}}
    reporting=advance_mission(frames[-1],stamp(verify_end),report)
    return_start=verify_end+timedelta(seconds=plan['config']['reporting_duration_s'])
    frames.append(advance_mission(reporting,stamp(return_start)))
    finish=return_start+timedelta(seconds=frames[-1]['return_route']['estimated_travel_time_s'])
    frames.append(advance_mission(frames[-1],stamp(finish)))
    for name,data in [('input_state.json',state),('search_plan.json',plan),('drone_report.json',report),
                      ('mission_timeline.json',frames),('completed_mission.json',frames[-1]),
                      ('updated_state.json',apply_missions(state,[frames[-1]],'search-demo-2'))]:save(output/name,data)
    save(output/'README.json',{'simulation':True,'description':'Contract integration fixture using Los Angeles example coordinates; not real Venezuelan field evidence.',
                             'phases':[f['phase'] for f in frames], 'search_priority':plan['decisions'][0]['search_priority']})
    return plan,frames


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',nargs='?',default='data/search_demo');args=p.parse_args()
    plan,frames=run_demo(args.output)
    print('SearchPriority:',plan['decisions'][0]['search_priority'])
    print('Drone lifecycle:',' -> '.join(f['phase'] for f in frames))
