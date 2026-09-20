"""Building-only scoring inputs from persisted observations; never sum duplicate claims."""
from copy import deepcopy
DAMAGE = {'none':0., 'unknown':.5, 'reported_damage':.4, 'minor_damage':.3,
          'major_damage':.7, 'severe_collapse':1., 'active_secondary_hazard':1.}

def enrich_worksite(work, observations, stored=None, scenario=None):
    # Highest reported count/severity is a conservative ranking input, not a fused fact.
    unique = {o['observation_id']:o for o in observations}
    victims = [o['victim_indication'] for o in unique.values()]
    damage = [o['structural_hazard'] for o in unique.values() if o['structural_hazard'] != 'unknown']
    report = stored['report'] if stored else {}
    if report.get('victim_indicator'): victims.append(report['victim_indicator'])
    if report.get('structural_damage') not in (None, 'unknown'): damage.append(report['structural_damage'])
    counts = [v[k] for v in victims for k in ('count_min','count_max') if v.get(k) is not None]
    people = scenario['victims']['count'] if scenario else max(counts) if counts else None
    qualitative = {'none':0.,'unknown':.5,'possible_individual':.3,'confirmed_individual':.3,
                   'possible_multiple':.6,'confirmed_multiple':.6}
    # Unknown is never converted to zero people. Numeric counts remain distinct from policy weights.
    people_score = people/(people+10.) if people is not None else max((qualitative[v['type']] for v in victims), default=.5)
    severity = scenario['structural_state'] if scenario else max(damage, key=lambda d:(DAMAGE[d],d)) if damage else 'unknown'
    factors = {'people_count':people, 'people_count_basis':'scenario_simulated_count' if scenario else 'maximum_reported_bound_not_sum' if counts else 'unknown_qualitative_indication',
               'people_score':people_score, 'damage_level':severity, 'damage_score':DAMAGE[severity],
               'people_contribution':.55*people_score, 'damage_contribution':.35*DAMAGE[severity],
               'uncertainty_contribution':.1, 'observation_ids':sorted(unique),
               'database_report_id':stored['report_id'] if stored else None}
    if scenario:
        factors.update(scenario_worksite_key=scenario['worksite_key'], scenario_id=scenario['scenario_id'],
                       scenario_source='scenario_ground_truth.json', simulation=True)
    factors['priority'] = factors['people_contribution']+factors['damage_contribution']+.1
    work.setdefault('metadata',{})['building_priority']=factors
    work['structural_summary']['severity']={'none':'none','unknown':'unknown','reported_damage':'unknown','minor_damage':'minor','major_damage':'major','severe_collapse':'severe','active_secondary_hazard':'severe'}[severity]
    if victims:
        work['victim_summary']['indication']=deepcopy(max(victims,key=lambda v:(max(v.get('count_max') or 0,v.get('count_min') or 0),qualitative[v['type']])))
