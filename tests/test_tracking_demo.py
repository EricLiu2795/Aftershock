from copy import deepcopy
import unittest
from search_planner.tracking_demo import prepare_tracking, dispatch_tracking, sync_intake
from search_planner.reassignment import step
from search_planner.verification import DemoVerification
from search_planner.common import validate

class TrackingDemoTests(unittest.TestCase):
    def test_ready_then_scanning_and_completed_results(self):
        record=prepare_tracking('2026-09-19T15:00:00Z','session-a')
        self.assertFalse(record['missions'])
        self.assertEqual(len(record['state']['resources']),2)
        self.assertEqual(record,prepare_tracking('2026-09-19T15:00:00Z','session-a'))
        other=prepare_tracking('2026-09-19T15:00:00Z','session-b')
        self.assertNotEqual(record['state']['movement_states'],other['state']['movement_states'])
        record=dispatch_tracking(record)
        self.assertEqual(len(record['missions']),2)
        for mission in record['missions']:self.assertEqual(mission['phase'],'en_route')
        result=step(record,'2026-09-19T15:10:00Z',verification=DemoVerification())
        for m in result['missions']:self.assertEqual(m['phase'],'idle')
        result=dispatch_tracking(result)
        self.assertEqual(len(result['missions']),2)
        self.assertTrue(all(m['phase']=='idle' for m in result['missions']))
        validate('drone_tracking.schema.json',dict(result,initialized=True,server_now=result['last_wall_at']))

    def test_completed_scan_chains_from_site_and_preserves_home(self):
        record=prepare_tracking('2026-09-19T15:00:00Z','chain')
        record['state']['resources']=record['state']['resources'][:1]
        rid=record['state']['resources'][0]['resource_id']
        record['state']['movement_states']=[m for m in record['state']['movement_states'] if m['resource_id']==rid]
        record=dispatch_tracking(record)
        first=deepcopy(record['missions'][0])
        record=step(record,'2026-09-19T15:10:00Z',verification=DemoVerification())
        record=dispatch_tracking(record)
        second=record['missions'][0]
        self.assertNotEqual(first['site_id'],second['site_id'])
        self.assertEqual(second['outbound_route']['path'][0],first['outbound_route']['path'][-1])
        self.assertEqual(second['home_position'],first['home_position'])
        self.assertEqual(second['phase'],'en_route')
        self.assertFalse(any(e['event_type']=='DRONE_RETURN_STARTED' for e in record['history'][0]['mission']['events']))
        record=dispatch_tracking(step(record,'2026-09-19T15:20:00Z',verification=DemoVerification()))
        self.assertEqual(record['missions'][0]['phase'],'idle')
        self.assertEqual(record['missions'][0]['movement']['position'],first['home_position'])

    def test_returning_drone_can_take_target_from_current_position(self):
        from search_planner.common import stamp, time
        from datetime import timedelta
        record=prepare_tracking('2026-09-19T15:00:00Z','return-chain')
        record['state']['resources']=record['state']['resources'][:1]
        rid=record['state']['resources'][0]['resource_id']
        record['state']['movement_states']=[m for m in record['state']['movement_states'] if m['resource_id']==rid]
        record=dispatch_tracking(record)
        record['scanning_enabled']=False
        # Advance to one second after publication using the ordinary return lifecycle.
        mission=DemoVerification().advance(record['missions'][0],'2026-09-20T15:00:00Z')
        published=next(e['occurred_at'] for e in mission['events'] if e['event_type']=='DRONE_REPORT_READY')
        delta=time(published)-time(record['state']['scenario_time'])+timedelta(seconds=1)
        record=step(record,stamp(time(record['last_wall_at'])+delta),verification=DemoVerification())
        old=deepcopy(record['missions'][0])
        self.assertEqual(old['phase'],'returning')
        result=dispatch_tracking(record)
        new=result['missions'][0]
        self.assertNotEqual(new['site_id'],old['site_id'])
        self.assertEqual(new['outbound_route']['path'][0],old['movement']['position'])
        self.assertEqual(new['home_position'],old['home_position'])

    def test_coordinator_state_not_modified_by_demo_intake(self):
        record=prepare_tracking('2026-09-19T15:00:00Z','a');record['tracking_demo']=False
        self.assertEqual(sync_intake(record,[{'invalid':'unused'}]),record)

    def test_waiting_before_dispatch_keeps_idle_positions_usable(self):
        record=prepare_tracking('2026-09-19T15:00:00Z','a')
        record=step(record,'2026-09-19T15:10:00Z',verification=DemoVerification())
        self.assertEqual(len(dispatch_tracking(record)['missions']),2)

if __name__=='__main__':unittest.main()
