"""Scoring, allocation, traces, canonical contracts, and pure input invariants."""
from copy import deepcopy
import json
import unittest
from search_planner import SearchConfig,plan_search,score_search,score_drone
from search_planner.common import validate
from search_planner.demo import sample_state
from search_planner.flight import distance,interpolate,speed_mps


class SearchPlannerTests(unittest.TestCase):
    def setUp(self):self.state=sample_state()

    def test_exact_user_example_and_trace(self):
        features={'potential_impact':.85,'uncertainty':.8,'criticality':.6,'evidence_conflict':.53}
        result=score_search(features)
        self.assertEqual(result['impact_uncertainty_term'],.68)
        self.assertEqual(result['criticality_contribution'],.15)
        self.assertEqual(result['evidence_conflict_contribution'],.0795)
        self.assertEqual(result['total'],.9095)
        utility=score_drone(result['total'],360,600)
        self.assertEqual(utility['verification_eta'],.6)
        self.assertEqual(utility['eta_penalty'],.18)
        self.assertEqual(utility['final_utility'],.7295)

    def test_components_reconcile_and_reasons_are_fixed(self):
        plan=plan_search(self.state);e=plan['explanations'][0];b=e['score_breakdown']
        self.assertAlmostEqual(b['impact_uncertainty_term']+b['criticality_contribution']+b['evidence_conflict_contribution'],b['search_priority'])
        self.assertAlmostEqual(b['search_priority']-b['eta_penalty'],b['drone_utility'])
        self.assertEqual(e['reason_codes'],['HIGH_UNCERTAINTY','HIGH_POTENTIAL_IMPACT','CONFLICTING_EVIDENCE','DRONE_SELECTED'])
        self.assertEqual(e['score'],plan['decisions'][0]['drone_utility'])
        for decision in plan['decisions']:validate('search.schema.json',decision)
        for task in plan['tasks']:validate('search_task.schema.json',task)
        for assignment in plan['assignments']:validate('assignment.schema.json',assignment)
        for route in plan['routes']:validate('route.schema.json',route)

    def test_repeat_order_independence_and_no_mutation(self):
        before=deepcopy(self.state);a=plan_search(self.state);b=plan_search(self.state)
        self.assertEqual(a,b);self.assertEqual(before,self.state)
        for key in ['sites','worksites','resources','movement_states']:self.state[key].reverse()
        self.assertEqual(a,plan_search(self.state))

    def test_one_drone_one_site_and_deterministic_tie(self):
        self.state['worksites'][1]['metadata']=deepcopy(self.state['worksites'][0]['metadata'])
        self.state['sites'][1]['centroid']=deepcopy(self.state['sites'][0]['centroid'])
        drone=deepcopy(next(r for r in self.state['resources'] if r['resource_type']=='drone_unit'))
        drone['resource_id']='drone-1';drone['movement_state_id']='movement-drone-1'
        movement=deepcopy(next(m for m in self.state['movement_states'] if m['resource_id']=='drone-2'));movement['resource_id']='drone-1'
        self.state['resources'].append(drone);self.state['movement_states'].append(movement)
        plan=plan_search(self.state)
        pairs={(a['resource_id'],a['worksite_id']) for a in plan['assignments']}
        self.assertEqual(pairs,{('drone-1','search-demo-W3'),('drone-2','worksite-d3')})

    def test_capacity_explanation(self):
        plan=plan_search(self.state)
        self.assertEqual(len(plan['assignments']),1)
        self.assertIsNone(plan['decisions'][1]['selected_drone_id'])
        self.assertIn('DRONES_ALLOCATED',plan['explanations'][1]['reason_codes'])

    def test_no_available_drone_and_no_tasks(self):
        for r in self.state['resources']:
            if r['resource_type']=='drone_unit':r['status']='returning'
        plan=plan_search(self.state)
        self.assertEqual(plan['tasks'],[])
        self.assertTrue(all(e['reason_codes'][-1]=='NO_AVAILABLE_DRONE' for e in plan['explanations']))

    def test_missing_zero_stale_and_future_drone(self):
        for problem in ['missing','zero','stale','future']:
            with self.subTest(problem=problem):
                state=deepcopy(self.state);drone=next(r for r in state['resources'] if r['resource_type']=='drone_unit')
                movement=next(m for m in state['movement_states'] if m['resource_id']==drone['resource_id'])
                if problem=='missing':state['movement_states'].remove(movement)
                elif problem=='zero':drone['speed']['value']=0
                else:movement['last_updated_at']='2026-09-19T13:00:00Z' if problem=='stale' else '2026-09-19T15:00:00Z'
                self.assertEqual(plan_search(state)['assignments'],[])

    def test_busy_target_and_resolved_worksite(self):
        drone=next(r for r in self.state['resources'] if r['resource_type']=='drone_unit')
        drone.update(status='verifying',current_worksite_id='worksite-d3')
        self.state['worksites'][1]['operational_status']='resolved'
        plan=plan_search(self.state)
        self.assertEqual(plan['decisions'],[])
        self.assertIn('VERIFICATION_IN_PROGRESS',[x['reason_code'] for x in plan['exclusions']])

    def test_missing_site_and_duplicate_identity(self):
        self.state['sites']=[]
        self.assertEqual(plan_search(self.state)['decisions'],[])
        state=sample_state();state['resources'].append(deepcopy(state['resources'][0]))
        with self.assertRaises(ValueError):plan_search(state)

    def test_threshold_and_invalid_features(self):
        plan=plan_search(self.state,SearchConfig(minimum_utility=2))
        self.assertEqual(plan['assignments'],[])
        self.assertIn('UTILITY_BELOW_THRESHOLD',plan['explanations'][0]['reason_codes'])
        for invalid in [-.1,1.01,float('nan'),True,'high']:
            state=sample_state();state['worksites'][0]['metadata']['search_features']['uncertainty']=invalid
            with self.assertRaises((ValueError,TypeError)):plan_search(state)

    def test_eta_clipped_speed_units_and_no_information_gap(self):
        self.assertEqual(score_drone(1,1200,600)['verification_eta'],1)
        for value,unit in [(10,'m_per_s'),(36,'km_per_h'),(1000/600,'minutes_per_km')]:
            self.assertAlmostEqual(speed_mps({'speed':{'value':value,'unit':unit}}),10)
        for w in self.state['worksites']:
            w['metadata']['search_features'].update(uncertainty=0,evidence_conflict=0)
        self.assertEqual(plan_search(self.state)['decisions'],[])

    def test_feature_fallback_and_task_types(self):
        for w in self.state['worksites']:w.pop('metadata')
        w=self.state['worksites'][0];w['access_summary']['status']='unknown'
        plan=plan_search(self.state)
        d=next(d for d in plan['decisions'] if d['worksite_id']==w['worksite_id'])
        self.assertEqual(d['recommended_task'],'check_access')
        self.assertEqual(d['score_breakdown']['evidence_conflict'],0)

    def test_geographic_distance_and_antimeridian(self):
        a={'latitude':0.,'longitude':179.};b={'latitude':0.,'longitude':-179.}
        self.assertAlmostEqual(distance(a,b),222390.160467,places=3)
        middle=interpolate(a,b,.5)
        self.assertAlmostEqual(abs(middle['longitude']),180)
        self.assertEqual(interpolate(a,b,1),b)


if __name__=='__main__':unittest.main()
