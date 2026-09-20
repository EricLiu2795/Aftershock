from copy import deepcopy
import unittest
from search_planner.demo import sample_state
from search_planner.building_priority import enrich_worksite
from search_planner.planner import plan_search
from search_planner.runtime import start_mission
from search_planner.verification import DemoVerification

class BuildingPriorityTests(unittest.TestCase):
    def observations(self, people=None, damage='unknown'):
        return [{'observation_id':'one','victim_indication':{'type':'possible_multiple','count_min':people,'count_max':people},'structural_hazard':damage}]

    def test_counts_not_summed_unknown_not_zero(self):
        w=sample_state()['worksites'][0]
        o=self.observations(20,'major_damage')
        enrich_worksite(w,o+o)
        self.assertEqual(w['metadata']['building_priority']['people_count'],20)
        enrich_worksite(w,self.observations())
        self.assertIsNone(w['metadata']['building_priority']['people_count'])
        self.assertEqual(w['structural_summary']['severity'],'unknown')

    def test_people_damage_priority_wins_regardless_input_order(self):
        state=sample_state()
        a,b=state['worksites'][:2]
        state['sites'][1]['centroid']=deepcopy(state['sites'][0]['centroid'])
        enrich_worksite(a,self.observations(1,'minor_damage'))
        enrich_worksite(b,self.observations(40,'severe_collapse'))
        plan=plan_search(state)
        self.assertEqual(plan['assignments'][0]['worksite_id'],b['worksite_id'])
        e=plan['explanations'][0]; f=e['building_factors']
        self.assertAlmostEqual(sum(f[k] for k in ['people_contribution','damage_contribution','uncertainty_contribution']),e['score_breakdown']['search_priority'])
        self.assertTrue(all(c['flight_distance_m']>=0 for c in e['candidates']))
        state['worksites'].reverse()
        self.assertEqual(plan,plan_search(state))

    def test_nearer_target_wins_equal_factors(self):
        state=sample_state()
        for w in state['worksites']:enrich_worksite(w,self.observations(20,'major_damage'))
        plan=plan_search(state)
        pairs=[c for e in plan['explanations'] for c in e['candidates']]
        nearest=min(pairs,key=lambda c:c['flight_distance_m'])
        self.assertEqual(plan['assignments'][0]['worksite_id'],nearest['worksite_id'])

    def test_database_result_waits_for_scan_and_preserves_image(self):
        state=sample_state(); plan=plan_search(state)
        mission=start_mission(plan,plan['assignments'][0]['resource_id'],state)
        report={'schema_version':'2.1','input_id':'stored','timestamp':state['scenario_time'],'drone_id':'old',
            'location':{**mission['outbound_route']['path'][-1],'raw_text':'building','resolved_site_id':mission['site_id']},
            'structural_damage':'major_damage','victim_indicator':{'type':'possible_multiple'},
            'metadata':{'verification_assets':[{'asset_id':'same-building-image'}]}}
        provider=DemoVerification(stored_inspections=[{'site_id':mission['site_id'],'report_id':'stored','report':report}])
        self.assertIsNone(provider.advance(mission,mission['launched_at'])['drone_report'])
        done=provider.advance(mission,'2026-09-20T15:00:00Z')
        self.assertEqual(done['drone_report']['metadata']['database_source_report_id'],'stored')
        self.assertEqual(done['drone_report']['metadata']['verification_assets'],report['metadata']['verification_assets'])

    def test_missing_result_is_unknown_and_can_continue(self):
        state=sample_state();plan=plan_search(state)
        mission=start_mission(plan,plan['assignments'][0]['resource_id'],state)
        done=DemoVerification(complete_missing=True).advance(mission,'2026-09-20T15:00:00Z',defer_return=True)
        self.assertEqual(done['phase'],'idle')
        self.assertEqual(done['drone_report']['structural_damage'],'unknown')
        self.assertEqual(done['drone_report']['metadata']['inspection_source'],'inspection_unavailable')
