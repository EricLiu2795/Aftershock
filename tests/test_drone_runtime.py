from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from search_planner import plan_search,start_mission,advance_mission,apply_missions,SearchConfig
from search_planner.common import time,stamp,validate,version
from search_planner.demo import sample_state,run_demo
from search_planner.__main__ import save,read


class DroneRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.state=sample_state();self.plan=plan_search(self.state)
        self.rid=self.plan['assignments'][0]['resource_id']
        self.mission=start_mission(self.plan,self.rid,self.state)
        self.arrival=time(self.mission['launched_at'])+timedelta(seconds=self.mission['outbound_route']['estimated_travel_time_s'])
        self.verify=self.arrival+timedelta(seconds=self.plan['config']['verification_duration_s'])
        self.report={'schema_version':version(),'input_id':'test-report','timestamp':stamp(self.verify),'drone_id':self.rid,
                     'location':{**self.mission['outbound_route']['path'][-1],'resolved_site_id':self.mission['site_id'],'accuracy':'building'},
                     'victim_indicator':{'type':'possible_individual'},'structural_damage':'unknown','access_status':'unknown'}

    def test_stale_plan_and_mutation_rejected(self):
        state=deepcopy(self.state);state['state_version']='new'
        with self.assertRaises(ValueError):start_mission(self.plan,self.rid,state)
        state=deepcopy(self.state);state['movement_states'][-1]['position']['latitude']+=.0001
        with self.assertRaises(ValueError):start_mission(self.plan,self.rid,state)
        plan=deepcopy(self.plan);plan['decisions'][0]['search_priority']=99
        with self.assertRaises(ValueError):start_mission(plan,self.rid,self.state)

    def test_lifecycle_and_stationary_verification(self):
        self.assertEqual(self.mission['phase'],'en_route')
        m=advance_mission(self.mission,stamp(self.arrival))
        self.assertEqual(m['phase'],'verifying');self.assertEqual(m['movement']['speed_mps'],0)
        self.assertEqual(m['movement']['position'],m['outbound_route']['path'][-1])
        m=advance_mission(m,stamp(self.verify));self.assertEqual(m['phase'],'reporting')
        m=advance_mission(m,stamp(self.verify),self.report)
        publish=self.verify+timedelta(seconds=2)
        m=advance_mission(m,stamp(publish));self.assertEqual(m['phase'],'returning')
        self.assertEqual(m['assignment']['status'],'completed')
        finish=publish+timedelta(seconds=m['return_route']['estimated_travel_time_s'])
        m=advance_mission(m,stamp(finish));self.assertEqual(m['phase'],'idle')
        self.assertEqual(m['movement']['position'],m['home_position'])
        self.assertIsNone(m['resource']['current_assignment_id']);self.assertIsNone(m['resource']['current_worksite_id'])
        self.assertEqual(m['movement']['distance_remaining_m'],0)
        self.assertEqual(m['return_route']['route_status'],'completed')
        self.assertEqual(len(m['events']),6)
        validate('drone_mission.schema.json',m)

    def test_waits_without_fabricated_report(self):
        m=advance_mission(self.mission,stamp(self.verify+timedelta(hours=2)))
        self.assertEqual(m['phase'],'reporting');self.assertIsNone(m['drone_report'])
        self.assertFalse(any(e['event_type']=='DRONE_REPORT_READY' for e in m['events']))
        self.assertIsNone(m['return_route'])

    def test_same_timestamp_idempotent_and_no_input_mutation(self):
        before=deepcopy(self.mission)
        m=advance_mission(self.mission,stamp(self.verify),self.report)
        self.assertEqual(self.mission,before)
        self.assertEqual(m,advance_mission(m,stamp(self.verify),self.report))
        after=advance_mission(m,stamp(self.verify+timedelta(hours=1)))
        again=advance_mission(after,after['last_updated_at'],self.report)
        self.assertEqual(after,again)
        self.assertEqual(sum(e['event_type']=='DRONE_REPORT_READY' for e in after['events']),1)

    def test_large_and_small_steps_agree(self):
        m=advance_mission(self.mission,stamp(self.verify),self.report)
        end=self.verify+timedelta(seconds=60)
        large=advance_mission(m,stamp(end))
        small=m
        for seconds in [1,2,3,6,9,12,30,60]:small=advance_mission(small,stamp(self.verify+timedelta(seconds=seconds)))
        self.assertEqual(large,small)
        restored=json.loads(json.dumps(m));self.assertEqual(large,advance_mission(restored,stamp(end)))

    def test_foreign_early_future_and_wrong_site_reports(self):
        for mode in ['drone','site','future','early','far','unlocated']:
            report=deepcopy(self.report)
            if mode=='drone':report['drone_id']='other'
            elif mode=='site':report['location']['resolved_site_id']='other'
            elif mode=='future':report['timestamp']=stamp(self.verify+timedelta(hours=1))
            elif mode=='early':report['timestamp']=self.mission['launched_at']
            elif mode=='far':report['location']['latitude']=0
            else:report['location']={'latitude':None,'longitude':None}
            with self.subTest(mode=mode),self.assertRaises(ValueError):advance_mission(self.mission,stamp(self.verify),report)
        m=advance_mission(self.mission,stamp(self.verify),self.report)
        other=deepcopy(self.report);other['input_id']='another-report'
        with self.assertRaises(ValueError):advance_mission(m,stamp(self.verify),other)
        with self.assertRaises(ValueError):advance_mission(m,self.mission['launched_at'])

    def test_report_event_has_canonical_observations(self):
        m=advance_mission(self.mission,stamp(self.verify),self.report)
        m=advance_mission(m,stamp(self.verify+timedelta(seconds=2)))
        e=next(e for e in m['events'] if e['event_type']=='DRONE_REPORT_READY')
        self.assertEqual(e['payload']['drone_input'],self.report)
        for o in e['payload']['observations']:
            validate('observation.schema.json',o)
            self.assertEqual(o['verification_status'],'unverified')
        self.assertFalse(any('priority' in k for k in m['movement']))

    def test_apply_deltas_and_no_duplicate_dispatch(self):
        state=apply_missions(self.state,[self.mission],'state-started')
        validate('operational_belief_state.schema.json',state)
        self.assertEqual(state['worksites'],self.state['worksites'])
        self.assertFalse(plan_search(state)['assignments'])
        self.assertEqual(next(r for r in state['resources'] if r['resource_id']==self.rid)['status'],'en_route')
        self.assertTrue(any(r['resource_id']==self.rid for r in state['active_routes']))
        with self.assertRaises(ValueError):apply_missions(state,[self.mission,self.mission],'bad')
        original=deepcopy(state);next(r for r in original['resources'] if r['resource_id']==self.rid)['current_assignment_id']='someone-else'
        with self.assertRaises(ValueError):apply_missions(original,[self.mission],'bad')

    def test_zero_distance_and_home_position(self):
        state=deepcopy(self.state)
        position=next(m for m in state['movement_states'] if m['resource_id']==self.rid)
        position['position']=deepcopy(state['sites'][0]['centroid'])
        plan=plan_search(state);mission=start_mission(plan,self.rid,state)
        self.assertEqual(mission['phase'],'verifying')
        self.assertEqual(mission['movement']['route_progress'],1)
        self.assertEqual(mission['movement']['distance_remaining_m'],0)

    def test_file_checkpoint_and_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint=Path(tmp)/'mission.json';save(checkpoint,self.mission)
            self.assertEqual(read(checkpoint),self.mission)
            plan,frames=run_demo(Path(tmp)/'demo')
            self.assertEqual([f['phase'] for f in frames],['en_route','verifying','reporting','returning','idle'])
            self.assertEqual(plan['decisions'][0]['search_priority'],.9095)
            for f in frames:validate('drone_mission.schema.json',f)


if __name__=='__main__':unittest.main()
