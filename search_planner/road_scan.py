"""Simulated 500 m swept road sensor. Never reads hidden truth or interpretation hints."""
from copy import deepcopy
from datetime import timedelta
import json
import math
from pathlib import Path
from .common import identity, stamp, time, validate, version
from .flight import interpolate

RADIUS_M = 500
ROOT = Path(__file__).resolve().parents[1]


def _point_segment(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length = dx*dx+dy*dy
    t = max(0, min(1, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)) if length else 0
    return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)


def segment_distance(a, b, c, d):
    def cross(p, q, r):
        return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    if cross(a,b,c)*cross(a,b,d)<0 and cross(c,d,a)*cross(c,d,b)<0:
        return 0
    return min(_point_segment(a,c,d),_point_segment(b,c,d),_point_segment(c,a,b),_point_segment(d,a,b))


def in_range(a, b, road):
    # Local metric projection, appropriate to the bounded Caraballeda scenario.
    lat = math.radians((a['latitude']+b['latitude'])/2)
    def xy(lon, latitude):
        return (math.radians(lon-a['longitude'])*6371008.8*math.cos(lat),
                math.radians(latitude-a['latitude'])*6371008.8)
    origin=(0,0); end=xy(b['longitude'],b['latitude'])
    points=[xy(*p) for p in road]
    return any(segment_distance(origin,end,c,d)<=RADIUS_M for c,d in zip(points,points[1:]))


def scan_roads(previous, result, *, roads=None, fixture=None):
    """Scan only elapsed flight intervals, including flights completed between ticks."""
    if previous is None:
        return result
    if roads is None:
        features=json.loads((ROOT/'data/graph_exports/caraballeda.geojson').read_text())['features']
        roads={f['id']:f['geometry']['coordinates'] for f in features if f['geometry']['type']=='LineString'}
    if fixture is None:
        fixture=json.loads((ROOT/'data/scenarios/caraballeda/drone_road_conditions.json').read_text())
    if fixture.get('simulation') is not True:
        raise ValueError('Road scan fixture must be explicitly simulated')
    for condition in [fixture['baseline'], *fixture['changes']]:
        if condition['status'] not in {'open','limited','blocked'}:
            raise ValueError('Invalid road scan status')
        if condition.get('road_id') and condition['road_id'] not in roads:
            raise ValueError('Road scan fixture references an unknown edge')
    out=deepcopy(result)
    history=out.setdefault('road_scans',[])
    seen={d['detection_id'] for d in history}
    prior={m['mission_id']:m for m in previous['missions']}
    now=time(result['state']['scenario_time'])
    for m in result['missions']:
        old=prior.get(m['mission_id'])
        if old is None: continue
        for route in [m['outbound_route'],m.get('return_route')]:
            if not route or len(route['path'])!=2: continue
            start=time(route['created_at']); seconds=route['estimated_travel_time_s']
            if seconds<=0: continue
            begin=max(time(old['last_updated_at']),start)
            end=min(now,start+timedelta(seconds=seconds))
            if end<=begin: continue
            for road_id,geometry in sorted(roads.items()):
                changes=[fixture['baseline']]+[c for c in fixture['changes'] if c['road_id']==road_id]
                changes.sort(key=lambda c:time(c['effective_at']))
                for i,condition in enumerate(changes):
                    lo=max(begin,time(condition['effective_at']))
                    hi=min(end,time(changes[i+1]['effective_at'])) if i+1<len(changes) else end
                    if hi<=lo: continue
                    detection_id=identity('drone-road',[m['mission_id'],road_id,condition['effective_at']])
                    if detection_id in seen: continue
                    a=interpolate(*route['path'],(lo-start).total_seconds()/seconds)
                    b=interpolate(*route['path'],(hi-start).total_seconds()/seconds)
                    if not in_range(a,b,geometry): continue
                    at=stamp(hi); evidence_id=identity('road-evidence',detection_id)
                    observation={'schema_version':version(),'observation_id':identity('road-observation',detection_id),
                        'timestamp':at,'source_type':'drone','source_id':m['resource']['resource_id'],
                        'location':{'latitude':None,'longitude':None,'accuracy':'street','label':road_id},
                        'incident_type':'access_blocked','victim_indication':{'type':'unknown'},
                        'structural_hazard':'unknown','access_status':condition['status'],'verification_status':'unverified',
                        'metadata':{'simulation':True,'road_id':road_id,'detection_radius_m':RADIUS_M}}
                    validate('observation.schema.json',observation)
                    assessment={'schema_version':version(),'assessment_id':detection_id,
                        'road_state':{'schema_version':version(),'road_id':road_id,'status':condition['status'],
                            'travel_cost_multiplier':condition['travel_cost_multiplier'],'updated_at':at,
                            'evidence_ids':[evidence_id],'reason':'Simulated 500 m drone road scan'},
                        'passability':deepcopy(condition['passability']),'verification_status':'unverified',
                        'evidence':[{'schema_version':version(),'evidence_id':evidence_id,
                            'observation_id':observation['observation_id'],
                            'source_type':'drone','source_id':m['resource']['resource_id'],'timestamp':at,
                            'summary':'Simulated road sensor result within 500 m of traveled flight path',
                            'verification_status':'unverified'}]}
                    validate('road_assessment.schema.json',assessment)
                    history.append({'detection_id':detection_id,'mission_id':m['mission_id'],
                        'resource_id':m['resource']['resource_id'],'radius_m':RADIUS_M,'simulation':True,
                        'condition_effective_at':condition['effective_at'],
                        'disruption_id':condition.get('id'),'source_event_id':condition.get('source_event_id'),
                        'observation':observation,'assessment':assessment})
                    seen.add(detection_id)
    states={s['road_id']:s for s in out['state']['road_states']}
    for detection in sorted(history,key=lambda d:(time(d['assessment']['road_state']['updated_at']),time(d['condition_effective_at']),d['detection_id'])):
        state=detection['assessment']['road_state']; old=states.get(state['road_id'])
        if old is None or time(state['updated_at'])>=time(old['updated_at']):states[state['road_id']]=deepcopy(state)
    updated_states=sorted(states.values(),key=lambda s:s['road_id'])
    if updated_states != sorted(out['state']['road_states'],key=lambda s:s['road_id']):
        # Search owns the version only for the self-contained tracking demo. When Agent 1's
        # belief snapshot drives Search, its state_version must stay comparable for sync.
        if out.get('tracking_demo'):
            out['state']['state_version']=identity('road-scan-state',[out['state']['state_version'],updated_states])
        out['state']['updated_at']=stamp(now)
    out['state']['road_states']=updated_states
    out['road_detection_radius_m']=RADIUS_M
    return out
