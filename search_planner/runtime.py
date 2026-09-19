"""Serializable drone lifecycle driven by an injected simulation timestamp."""
from copy import deepcopy
from datetime import timedelta
from geograph.drone import normalize_drone
from .common import identity,index,stamp,time,validate,version
from .flight import coordinate,distance,heading,interpolate,make_route,speed_mps
from .planner import SearchConfig,plan_search


def start_mission(plan,resource_id,state,home_position=None):
    """Dispatch an idle drone only if the complete current state still matches the plan."""
    validate('search_plan.schema.json',plan)
    config=SearchConfig(**plan['config'])
    if plan != plan_search(state,config):
        raise ValueError('STALE_OR_CHANGED_PLAN: replan from current operational belief')
    assignments=[a for a in plan['assignments'] if a['resource_id']==resource_id]
    if len(assignments)!=1:raise ValueError('Drone has no unique selected assignment')
    assignment=deepcopy(assignments[0]);wid=assignment['worksite_id']
    resource=deepcopy(index(state['resources'],'resource_id')[resource_id])
    movement=deepcopy(index(state['movement_states'],'resource_id')[resource_id])
    route=deepcopy(next(r for r in plan['routes'] if r['route_id']==assignment['route_id']))
    worksite=index(state['worksites'],'worksite_id')[wid]
    home=deepcopy(home_position if home_position is not None else movement['position']);coordinate(home)
    # Fail at dispatch if return geometry would be ambiguous.
    interpolate(route['path'][-1],home,.5)
    assignment.update(status='active',assigned_at=plan['created_at'],started_at=plan['created_at'])
    mission={'schema_version':version(),'mission_id':identity('drone-mission',assignment['assignment_id']),
             'based_on_state_version':state['state_version'],'decision_id':assignment['source_decision_id'],
             'site_id':worksite['site_id'],'worksite_id':wid,'resource':resource,'movement':movement,
             'assignment':assignment,'outbound_route':route,'return_route':None,'home_position':home,
             'speed_mps':speed_mps(resource),'launched_at':plan['created_at'],'last_updated_at':plan['created_at'],
             'config':plan['config'],'phase':'en_route','report_received_at':None,'drone_report':None,'events':[]}
    return advance_mission(mission,plan['created_at'])


def _report_check(mission,report,now,arrival):
    validate('drone_observation_input.schema.json',report)
    if report['drone_id']!=mission['resource']['resource_id']:raise ValueError('Report belongs to another drone')
    if not arrival<=time(report['timestamp'])<=now:raise ValueError('Report timestamp must be after arrival and not in the future')
    loc=report['location'];resolved=loc.get('resolved_site_id')
    if resolved and resolved!=mission['site_id']:raise ValueError('Report belongs to another site')
    if loc['latitude'] is not None and loc['longitude'] is not None:
        point={'latitude':loc['latitude'],'longitude':loc['longitude']}
        if distance(point,mission['outbound_route']['path'][-1])>100:
            raise ValueError('Report coordinates exceed 100 m target tolerance')
    elif resolved!=mission['site_id']:
        raise ValueError('Report needs the canonical site or target coordinates')
    # Validate conversion at receipt, before any report-ready event is committed.
    normalize_drone(report)


def advance_mission(mission,now,report=None, *, defer_return=False):
    """Pure copy-on-write tick. Missing reports hold REPORTING; reports are never invented."""
    validate('drone_mission.schema.json',mission)
    m=deepcopy(mission);current=time(now);now=stamp(current)
    if current<time(m['last_updated_at']):raise ValueError('Simulation time cannot move backwards')
    config=SearchConfig(**m['config']);outbound=m['outbound_route'];speed=m['speed_mps']
    start=time(m['launched_at']);arrival=start+timedelta(seconds=outbound['estimated_travel_time_s'])
    verification_end=arrival+timedelta(seconds=config.verification_duration_s)
    if report is not None:
        if m['drone_report'] is not None:
            if report!=m['drone_report']:raise ValueError('Mission already has a different report')
        else:
            _report_check(m,report,current,arrival)
            m['drone_report']=deepcopy(report);m['report_received_at']=now
    publish=(max(verification_end,time(m['report_received_at']))+timedelta(seconds=config.reporting_duration_s)
             if m['report_received_at'] else None)
    returned=None
    if publish is not None:
        returning=make_route(identity('return-flight',m['mission_id']),m['resource']['resource_id'],m['worksite_id'],
                            outbound['path'][-1],m['home_position'],speed,m['based_on_state_version'],stamp(publish))
        returned=publish+timedelta(seconds=returning['estimated_travel_time_s'])
        m['return_route']=returning if current>=publish and not defer_return else None
    transitions=[('DRONE_DISPATCHED',start),('DRONE_ARRIVED',arrival),('DRONE_VERIFICATION_COMPLETED',verification_end)]
    if publish is not None:
        transitions += [('DRONE_REPORT_READY',publish)]
        if not defer_return:
            transitions += [('DRONE_RETURN_STARTED',publish),('DRONE_RETURNED',returned)]
    existing={e['event_id'] for e in m['events']}
    for event_type,when in transitions:
        event_id=identity('drone-event',[m['mission_id'],event_type,stamp(when)])
        if when<=current and event_id not in existing:
            payload={}
            if event_type=='DRONE_REPORT_READY':
                payload={'drone_input':deepcopy(m['drone_report']),'observations':normalize_drone(m['drone_report'])}
            m['events'].append({'event_id':event_id,'event_type':event_type,'resource_id':m['resource']['resource_id'],
                                'worksite_id':m['worksite_id'],'occurred_at':stamp(when),'payload':payload})
            existing.add(event_id)
    if current<arrival:phase='en_route'
    elif current<verification_end:phase='verifying'
    elif publish is None or current<publish:phase='reporting'
    elif defer_return:phase='idle'
    elif current<returned:phase='returning'
    else:phase='idle'
    m['phase']=phase
    outbound['route_status']='active' if phase=='en_route' else 'completed'
    route=outbound
    if m['return_route'] is not None:
        route=m['return_route'];route['route_status']='active' if phase=='returning' else 'completed'
    flying=phase in {'en_route','returning'}
    route_start=time(route['created_at']);duration=route['estimated_travel_time_s']
    progress=min(1.,max(0.,(current-route_start).total_seconds()/duration)) if duration else 1.
    origin,target=route['path'][0],route['path'][-1]
    position=interpolate(origin,target,progress)
    travel=route['total_distance_m']*progress
    m['movement']={'schema_version':version(),'resource_id':m['resource']['resource_id'],
                  'movement_mode':'flight' if flying else 'stationary','route_id':route['route_id'],
                  'position':position,'segment_index':0,'segment_progress':progress,'route_progress':progress,
                  'speed_mps':speed if flying else 0.,'heading_deg':heading(position,target) if flying else None,
                  'started_at':route['created_at'],'last_updated_at':now,
                  'estimated_arrival_at':stamp(route_start+timedelta(seconds=duration)),
                  'distance_traveled_m':travel,'distance_remaining_m':max(0.,route['total_distance_m']-travel)}
    m['resource']['status']=phase
    active=phase in {'en_route','verifying','reporting'}
    m['resource']['current_assignment_id']=m['assignment']['assignment_id'] if active else None
    m['resource']['current_worksite_id']=m['worksite_id'] if active else None
    if publish is not None and current>=publish:
        m['assignment'].update(status='completed',completed_at=stamp(publish))
    m['last_updated_at']=now
    validate('drone_mission.schema.json',m)
    return m


def apply_missions(state,missions,state_version):
    """Apply resource/movement/active-route deltas. Caller owns global versioning and fusion."""
    validate('operational_belief_state.schema.json',state)
    if not isinstance(state_version,str) or not state_version:raise ValueError('state_version is required')
    out=deepcopy(state);resource_map=index(out['resources'],'resource_id');movement_map=index(out['movement_states'],'resource_id')
    seen=set();times=set()
    for mission in missions:
        validate('drone_mission.schema.json',mission)
        rid=mission['resource']['resource_id']
        if rid in seen:raise ValueError('Multiple missions for one drone')
        seen.add(rid);times.add(mission['last_updated_at'])
        if rid not in resource_map or resource_map[rid]['resource_type']!='drone_unit':raise ValueError('Unknown drone')
        assigned=resource_map[rid].get('current_assignment_id')
        if assigned and assigned!=mission['assignment']['assignment_id']:raise ValueError('Drone has another active assignment')
        if time(mission['last_updated_at'])<time(out['scenario_time']):raise ValueError('Cannot apply an older mission')
        if rid in movement_map and time(mission['last_updated_at'])<time(movement_map[rid]['last_updated_at']):raise ValueError('Cannot replace a newer movement state')
        resource_map[rid]=deepcopy(mission['resource']);movement_map[rid]=deepcopy(mission['movement'])
        out['active_routes']=[r for r in out['active_routes'] if r['resource_id']!=rid]
        for route in [mission['outbound_route'],mission['return_route']]:
            if route and route['route_status']=='active':out['active_routes'].append(deepcopy(route))
    if len(times)>1:raise ValueError('Advance all missions to one simulation time before applying')
    out['resources']=sorted(resource_map.values(),key=lambda x:x['resource_id'])
    out['movement_states']=sorted(movement_map.values(),key=lambda x:x['resource_id'])
    out['active_routes'].sort(key=lambda x:x['route_id']);out['state_version']=state_version
    if times:out['scenario_time']=next(iter(times));out['updated_at']=out['scenario_time']
    validate('operational_belief_state.schema.json',out)
    return out
