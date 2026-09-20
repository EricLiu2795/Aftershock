"""Pure deterministic Search planning and numeric explanations. No model calls."""
from copy import deepcopy
from dataclasses import asdict,dataclass
from decimal import Decimal
from .common import fingerprint,identity,index,number,stamp,time,validate,version
from .flight import distance,flight_seconds,make_route,speed_mps


@dataclass(frozen=True)
class SearchConfig:
    verification_duration_s: float = 30.0
    reporting_duration_s: float = 2.0
    eta_normalization_s: float = 600.0
    minimum_utility: float = 0.0
    max_movement_age_s: float = 300.0

    def __post_init__(self):
        for key,value in asdict(self).items():
            number(value,key)
        if self.eta_normalization_s<=0: raise ValueError('eta_normalization_s must be positive')


REASONS={
 'HIGH_UNCERTAINTY':'High uncertainty',
 'HIGH_POTENTIAL_IMPACT':'Potentially high human impact',
 'CONFLICTING_EVIDENCE':'Conflicting evidence',
 'CRITICAL_FACILITY':'Critical facility',
 'VERIFICATION_RECOMMENDED':'Information gaps warrant verification',
 'DRONE_SELECTED':'Selected by deterministic utility ranking',
 'NO_AVAILABLE_DRONE':'No available drone has a usable position and speed',
 'UTILITY_BELOW_THRESHOLD':'All drone utilities are below the dispatch threshold',
 'DRONES_ALLOCATED':'Available drones were allocated to higher-ranked pairs',
}


def extract_features(worksite):
    """Accept upstream normalized metadata.search_features, otherwise documented MVP defaults."""
    victim=worksite['victim_summary']['indication']['type']
    impact={'none':0.,'unknown':.5,'possible_individual':.3,'possible_multiple':.6,
            'confirmed_individual':.5,'confirmed_multiple':.8}[victim]
    evidence={'potential':1.,'unverified':.8,'corroborated':.4,'verified':0.}[worksite['evidence_status']]
    unknowns=sum((victim=='unknown',worksite['structural_summary']['severity']=='unknown',
                  worksite['access_summary']['status']=='unknown'))/3
    values={'potential_impact':impact,'uncertainty':max(evidence,unknowns),
            'criticality':worksite['criticality'],'evidence_conflict':0.}
    sources={'potential_impact':'victim_indication_lookup','uncertainty':'max_evidence_uncertainty_and_unknown_field_fraction',
             'criticality':'worksite.criticality','evidence_conflict':'default_zero_not_inferred'}
    overrides=worksite.get('metadata',{}).get('search_features',{})
    if not isinstance(overrides,dict) or set(overrides)-set(values):
        raise ValueError('metadata.search_features must contain only normalized Search features')
    for key,value in overrides.items():
        values[key]=value;sources[key]='worksite.metadata.search_features.'+key
    for key in values:values[key]=number(values[key],key,0,1)
    return values,sources


def score_search(features):
    f={k:Decimal(str(number(features[k],k,0,1))) for k in
       ['potential_impact','uncertainty','criticality','evidence_conflict']}
    impact=f['potential_impact']*f['uncertainty']
    critical=Decimal('.25')*f['criticality'];conflict=Decimal('.15')*f['evidence_conflict']
    total=impact+critical+conflict
    return {**{k:float(v) for k,v in f.items()},'impact_uncertainty_term':float(impact),
            'criticality_contribution':float(critical),'evidence_conflict_contribution':float(conflict),
            'search_priority':float(total),'total':float(total)}


def score_drone(priority,eta_s,horizon_s):
    number(priority,'priority');number(eta_s,'verification_eta_s');number(horizon_s,'horizon_s')
    if horizon_s<=0:raise ValueError('horizon_s must be positive')
    # The stored normalized feature is the precise feature used in the formula.
    norm=min(eta_s/horizon_s,1.)
    penalty=Decimal('.30')*Decimal(str(norm));utility=Decimal(str(priority))-penalty
    return {'search_priority':priority,'verification_eta_s':eta_s,'verification_eta':norm,
            'eta_penalty':float(penalty),'final_utility':float(utility)}


def score_worksite(worksite):
    features,sources=extract_features(worksite)
    breakdown=score_search(features)
    building=worksite.get('metadata',{}).get('building_priority')
    if building:
        features.update(potential_impact=building['people_score'], uncertainty=1., criticality=0., evidence_conflict=0.)
        sources.update(potential_impact='worksite.metadata.building_priority.people_score',
                       uncertainty='fixed_verification_allowance', criticality='not_used_in_building_formula',
                       evidence_conflict='not_used_in_building_formula')
        breakdown.update(features)
        for key in ('impact_uncertainty_term','criticality_contribution','evidence_conflict_contribution'):
            breakdown.pop(key)
        for key in ('people_contribution','damage_contribution','uncertainty_contribution'):
            breakdown[key]=building[key]
        breakdown['search_priority']=breakdown['total']=building['priority']
    return features,sources,breakdown


def score_pair(priority, eta, horizon, building=False):
    result=score_drone(priority,eta,horizon)
    if building:
        # Bound the travel discount at 30% so every queued building remains eligible.
        result['eta_penalty'] *= priority
        result['final_utility'] = priority-result['eta_penalty']
    return result


def reason_codes(features):
    return [code for key,threshold,code in [
        ('uncertainty',.7,'HIGH_UNCERTAINTY'),('potential_impact',.7,'HIGH_POTENTIAL_IMPACT'),
        ('evidence_conflict',.5,'CONFLICTING_EVIDENCE'),('criticality',.7,'CRITICAL_FACILITY')]
        if features[key]>threshold] or ['VERIFICATION_RECOMMENDED']


def task_type(worksite):
    structural=worksite['structural_summary']['severity']=='unknown'
    access=worksite['access_summary']['status']=='unknown'
    victims=worksite['victim_summary']['indication']['type']=='unknown'
    if access and not structural and not victims:return 'check_access'
    if structural and not access and not victims:return 'assess_damage'
    return 'verify_worksite'


def plan_search(state,config=None):
    config=config or SearchConfig()
    validate('operational_belief_state.schema.json',state)
    fp=fingerprint(state);now=stamp(state['scenario_time'])
    sites=index(state['sites'],'site_id');works=index(state['worksites'],'worksite_id')
    resources=index(state['resources'],'resource_id');movements=index(state['movement_states'],'resource_id')
    exclusions=[];available={}
    active_worksites=set()
    for rid,resource in sorted(resources.items()):
        if resource['resource_type']!='drone_unit':continue
        if resource['status'] in {'en_route','verifying','reporting'} and resource.get('current_worksite_id'):
            active_worksites.add(resource['current_worksite_id'])
        reason=None
        movement=movements.get(rid)
        if resource['status']!='idle' or resource.get('current_assignment_id') or resource.get('current_worksite_id'):
            reason='DRONE_BUSY'
        elif movement is None:reason='MISSING_MOVEMENT_STATE'
        elif movement['movement_mode']!='stationary':reason='DRONE_NOT_STATIONARY'
        elif not 0<=(time(now)-time(movement['last_updated_at'])).total_seconds()<=config.max_movement_age_s:
            reason='STALE_OR_FUTURE_POSITION'
        elif any(r['resource_id']==rid and r['route_status'] in {'active','planned'} for r in state['active_routes']):
            reason='DRONE_HAS_ACTIVE_ROUTE'
        else:
            try:speed=speed_mps(resource)
            except ValueError as exc:reason=str(exc)
        if reason:exclusions.append({'entity_type':'drone','entity_id':rid,'reason_code':reason})
        else:available[rid]=(resource,movement,speed)
    scored={};candidates={}
    for wid,w in sorted(works.items()):
        reason=None
        if w['operational_status']=='resolved':reason='WORKSITE_RESOLVED'
        elif wid in active_worksites:reason='VERIFICATION_IN_PROGRESS'
        elif w['site_id'] not in sites:reason='MISSING_CANONICAL_SITE'
        if reason:
            exclusions.append({'entity_type':'worksite','entity_id':wid,'reason_code':reason});continue
        features,sources,breakdown=score_worksite(w)
        if features['uncertainty']==0 and features['evidence_conflict']==0:
            exclusions.append({'entity_type':'worksite','entity_id':wid,'reason_code':'NO_INFORMATION_GAP'});continue
        building=w.get('metadata',{}).get('building_priority')
        scored[wid]=(features,sources,breakdown)
        pairs=[]
        for rid,(resource,movement,speed) in sorted(available.items()):
            origin=movement['position'];target=sites[w['site_id']]['centroid']
            seconds=flight_seconds(origin,target,speed)
            breakdown=score_pair(scored[wid][2]['total'],seconds+config.verification_duration_s,config.eta_normalization_s,bool(building))
            pairs.append({'drone_id':rid,'worksite_id':wid,'flight_distance_m':distance(origin,target),
                          'flight_time_s':seconds,'verification_duration_s':config.verification_duration_s,
                          **breakdown,'selected':False})
        candidates[wid]=pairs
    ranked=sorted((c for pairs in candidates.values() for c in pairs),key=lambda c:(-c['final_utility'],c['worksite_id'],c['drone_id']))
    chosen={};used=set()
    for pair in ranked:
        rid,wid=pair['drone_id'],pair['worksite_id']
        if pair['final_utility']>=config.minimum_utility and rid not in used and wid not in chosen:
            chosen[wid]=pair;used.add(rid);pair['selected']=True
    plan={'schema_version':version(),'state_version':state['state_version'],'state_fingerprint':fp,
          'created_at':now,'config':asdict(config),'decisions':[],'explanations':[],'tasks':[],
          'assignments':[],'routes':[],'exclusions':exclusions}
    for wid,(features,sources,breakdown) in sorted(scored.items(),key=lambda item:(-item[1][2]['total'],item[0])):
        pair=chosen.get(wid);rid=pair['drone_id'] if pair else None
        decision_id=identity('search-decision',[fp,asdict(config),wid])
        codes=reason_codes(features)
        codes.append('DRONE_SELECTED' if pair else ('NO_AVAILABLE_DRONE' if not candidates[wid] else
                     ('UTILITY_BELOW_THRESHOLD' if max(c['final_utility'] for c in candidates[wid])<config.minimum_utility else 'DRONES_ALLOCATED')))
        sb=deepcopy(features)
        if pair:sb['verification_eta_penalty']=pair['eta_penalty']
        decision={'schema_version':version(),'decision_id':decision_id,'state_version':state['state_version'],
                  'worksite_id':wid,'search_priority':breakdown['total'],'selected_drone_id':rid,
                  'verification_eta':pair['verification_eta'] if pair else None,
                  'drone_utility':pair['final_utility'] if pair else None,'score_breakdown':sb,
                  'recommended_task':task_type(works[wid]),'reasons':[REASONS[code] for code in codes],'created_at':now}
        explanation={'decision_id':decision_id,'decision_type':'SEARCH','target_worksite_id':wid,
                     'selected_resource_id':rid,'score':decision['drone_utility'],'score_breakdown':{
                         **breakdown,'verification_eta':decision['verification_eta'],
                         'verification_eta_s':pair['verification_eta_s'] if pair else None,
                         'eta_penalty':pair['eta_penalty'] if pair else None,'drone_utility':decision['drone_utility']},
                     'feature_sources':sources,'reason_codes':codes,'candidates':candidates[wid]}
        if works[wid].get('metadata',{}).get('building_priority'):
            explanation['building_factors']=deepcopy(works[wid]['metadata']['building_priority'])
            explanation['reason_codes']=['BUILDING_PEOPLE_DAMAGE_DISTANCE', 'PEOPLE_COUNT_UNKNOWN' if explanation['building_factors']['people_count'] is None else 'REPORTED_PEOPLE_COUNT', codes[-1]]
            decision['reasons']=['Ranked by reported people, damage severity and drone travel time']
        validate('search.schema.json',decision)
        plan['decisions'].append(decision);plan['explanations'].append(explanation)
        if pair:
            route_id=identity('search-flight',[decision_id,rid])
            task={'schema_version':version(),'task_id':identity('search-task',decision_id),'worksite_id':wid,
                  'task_type':decision['recommended_task'],'created_at':now}
            assignment={'schema_version':version(),'assignment_id':identity('search-assignment',[decision_id,rid]),
                        'resource_id':rid,'worksite_id':wid,'task_type':task['task_type'],'status':'proposed',
                        'route_id':route_id,'source_decision_id':decision_id}
            resource,movement,speed=available[rid]
            route=make_route(route_id,rid,wid,movement['position'],sites[works[wid]['site_id']]['centroid'],speed,state['state_version'],now)
            validate('search_task.schema.json',task);validate('assignment.schema.json',assignment)
            plan['tasks'].append(task);plan['assignments'].append(assignment);plan['routes'].append(route)
    if any(w.get('metadata',{}).get('building_priority') for w in works.values()):
        plan['explanations'].sort(key=lambda e:(-max((c['final_utility'] for c in e['candidates']), default=e['score_breakdown']['total']), e['target_worksite_id']))
    validate('search_plan.schema.json',plan)
    return plan
