from copy import deepcopy
import unittest
from search_planner.scenario_buildings import scenario_buildings
from search_planner.tracking_demo import prepare_tracking, sync_intake, dispatch_tracking, publish_priorities
from search_planner.planner import plan_search
from search_planner.verification import DemoVerification
from search_planner.reassignment import step
from search_planner.common import validate

class ScenarioBuildingTests(unittest.TestCase):
    def intake(self):
        rows=[]
        for sid, entry in scenario_buildings().items():
            site={'schema_version':'2.1','site_id':sid,'display_name':entry['display_name'],
                  'site_type':'building','canonical_source':'osm','canonical_source_id':sid,
                  'centroid':entry['centroid'],'criticality':0}
            observation={'schema_version':'2.1','observation_id':'social:'+sid,'timestamp':'2026-09-19T14:00:00Z',
                         'source_type':'social','incident_type':'trapped_victims','location':dict(entry['centroid'],resolved_site_id=sid),
                         'victim_indication':{'type':'possible_multiple'},'structural_hazard':'unknown',
                         'access_status':'unknown','verification_status':'unverified'}
            rows.append({'site':site,'observations':[observation],'report_id':'report:'+sid})
        return rows

    def test_empty_until_import_then_exact_identity_and_counts(self):
        empty=prepare_tracking('2026-09-19T15:00:00Z','scenario')
        self.assertEqual(sync_intake(empty,[])['state']['worksites'],[])
        intake=self.intake()
        record=sync_intake(empty,intake+intake)
        self.assertEqual(len(record['state']['worksites']),8)
        for w in record['state']['worksites']:
            f=w['metadata']['building_priority']
            self.assertEqual(f['people_count'],scenario_buildings()[w['site_id']]['victims']['count'])
            self.assertEqual(f['people_count_basis'],'scenario_simulated_count')
        record['latest_search_plan']=plan_search(record['state'])
        record=publish_priorities(record)
        self.assertEqual([p['rank'] for p in record['target_priorities']],list(range(1,9)))
        validate('drone_tracking.schema.json',dict(record,initialized=True,server_now=record['last_wall_at']))
        self.assertEqual(record['target_priorities'],publish_priorities(deepcopy(record))['target_priorities'])

    def test_database_rows_replace_pack_json_for_counts_and_scan_results(self):
        from search_planner.scenario_buildings import buildings_from_rows
        rows=[{'site_id':sid,'pack_scenario_id':e['scenario_id'],'worksite_key':e['worksite_key'],'ground_truth_id':e['ground_truth_id'],
               'display_name':e['display_name'],'latitude':e['centroid']['latitude'],'longitude':e['centroid']['longitude'],
               'people_count':e['victims']['count'],'people_trapped':int(e['victims']['count']>0),'structural_state':e['structural_state'],
               'access_state':e['access_state'],'drone_verifiable':1,'photo_asset_id':None} for sid,e in scenario_buildings().items()]
        rows[0]['people_count']=9  # the database, not the JSON pack, is authoritative for the demo
        buildings=buildings_from_rows(rows)
        self.assertEqual(buildings[rows[0]['site_id']]['victims']['count'],9)
        self.assertEqual(buildings[rows[0]['site_id']]['victims']['indication'],'confirmed_multiple')
        record=sync_intake(prepare_tracking('2026-09-19T15:00:00Z','db'),self.intake(),buildings=buildings)
        counts={w['site_id']:w['metadata']['building_priority']['people_count'] for w in record['state']['worksites']}
        self.assertEqual(counts[rows[0]['site_id']],9)
        record=dispatch_tracking(record)
        provider=DemoVerification(scenario_outcomes=False,buildings=buildings)
        for minute in (10,20,30,40,50,59):
            record=step(record,f'2026-09-19T15:{minute:02d}:00Z',verification=provider); record=dispatch_tracking(record)
        done={m['site_id']:m for m in record['missions']+[h['mission'] for h in record['history']] if m['drone_report']}
        self.assertEqual(done[rows[0]['site_id']]['drone_report']['victim_indicator']['count_min'],9)
        with self.assertRaises(ValueError): buildings_from_rows(rows+[rows[0]])
        with self.assertRaises(ValueError): buildings_from_rows([dict(rows[0],people_count=-1)])

    def test_scan_gated_scenario_results_and_no_revisits_or_retargeting(self):
        record=sync_intake(prepare_tracking('2026-09-19T15:00:00Z','coverage'),self.intake())
        record=dispatch_tracking(record)
        provider=DemoVerification(scenario_outcomes=True)
        self.assertTrue(all(provider.advance(m,m['launched_at'])['drone_report'] is None for m in record['missions']))
        for minute in (10,20,30,40,50,59):
            record=step(record,f'2026-09-19T15:{minute:02d}:00Z',verification=provider)
            record=dispatch_tracking(record)
            works={w['worksite_id']:w for w in record['state']['worksites']}
            for entry in record['proposals']:
                if entry['proposal']['status']=='pending':
                    self.assertNotIn(works[entry['proposal']['proposed_worksite_id']]['site_id'],record['inspected_site_ids'])
        record=publish_priorities(record)
        missions=record['missions']+[h['mission'] for h in record['history']]
        completed=[m for m in missions if m['drone_report']]
        self.assertEqual(len(completed),8)
        self.assertEqual(len({m['site_id'] for m in completed}),8)
        self.assertEqual(len(record['inspected_site_ids']),8)
        self.assertEqual(record['target_priorities'],[])
        for m in completed:
            self.assertEqual(m['drone_report']['victim_indicator']['count_min'],scenario_buildings()[m['site_id']]['victims']['count'])
        again=dispatch_tracking(sync_intake(record,self.intake()))
        self.assertEqual(len(again['history']),len(record['history']))
        self.assertTrue(all(m['phase']=='idle' for m in again['missions']))
