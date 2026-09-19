"""Direct great-circle flight, independent of roads. Explicit simulation time only."""
from copy import deepcopy
import math
from .common import number, validate, version

EARTH_M=6371008.8


def coordinate(p):
    # Reuse the existing coordinate contract, including geographic bounds.
    validate('movement.schema.json',{'schema_version':version(),'resource_id':'coordinate-check',
             'movement_mode':'stationary','position':p,'route_progress':0,'speed_mps':0,
             'last_updated_at':'2000-01-01T00:00:00Z'})
    return p


def vector(p):
    lat,lon=math.radians(p['latitude']),math.radians(p['longitude'])
    return (math.cos(lat)*math.cos(lon),math.cos(lat)*math.sin(lon),math.sin(lat))


def distance(a,b):
    coordinate(a);coordinate(b)
    lat1,lat2=math.radians(a['latitude']),math.radians(b['latitude'])
    dlat=lat2-lat1;dlon=math.radians(b['longitude']-a['longitude'])
    h=math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2*EARTH_M*math.asin(min(1,math.sqrt(max(0,h))))


def speed_mps(resource):
    speed=resource.get('speed')
    if not speed: raise ValueError('MISSING_SPEED')
    value=number(speed['value'],'speed')
    if value<=0: raise ValueError('NONPOSITIVE_SPEED')
    unit=speed['unit']
    return value if unit=='m_per_s' else (value/3.6 if unit=='km_per_h' else 1000/(60*value))


def flight_seconds(a,b,speed):
    number(speed,'speed')
    if speed<=0: raise ValueError('speed must be positive')
    # Round up to microseconds, matching timestamp resolution at lifecycle boundaries.
    return math.ceil(distance(a,b)/speed*1_000_000)/1_000_000


def interpolate(a,b,fraction):
    number(fraction,'fraction',0,1)
    if fraction==0: return deepcopy(a)
    if fraction==1: return deepcopy(b)
    u,v=vector(a),vector(b)
    angle=math.acos(max(-1,min(1,sum(x*y for x,y in zip(u,v)))))
    if angle<1e-10:return deepcopy(a)
    if abs(math.pi-angle)<1e-8:raise ValueError('Antipodal flight needs an explicit waypoint')
    left=math.sin((1-fraction)*angle)/math.sin(angle);right=math.sin(fraction*angle)/math.sin(angle)
    x,y,z=(left*i+right*j for i,j in zip(u,v))
    return {'latitude':math.degrees(math.atan2(z,math.hypot(x,y))),
            'longitude':math.degrees(math.atan2(y,x))}


def heading(a,b):
    if distance(a,b)<1e-6:return None
    lat1,lat2=math.radians(a['latitude']),math.radians(b['latitude'])
    dlon=math.radians(b['longitude']-a['longitude'])
    return math.degrees(math.atan2(math.sin(dlon)*math.cos(lat2),
        math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(dlon)))%360


def make_route(route_id,resource_id,worksite_id,origin,target,speed,state_version,created_at):
    # Test the midpoint too, to fail early on ambiguous antipodal flights.
    interpolate(origin,target,.5)
    result={'schema_version':version(),'route_id':route_id,'resource_id':resource_id,
            'worksite_id':worksite_id,'route_type':'flight','route_status':'planned',
            'path':[deepcopy(origin),deepcopy(target)],'start_position':deepcopy(origin),
            'end_position':deepcopy(target),'total_distance_m':distance(origin,target),
            'estimated_travel_time_s':flight_seconds(origin,target,speed),
            'generated_from_state_version':state_version,'created_at':created_at}
    validate('route.schema.json',result)
    return result
