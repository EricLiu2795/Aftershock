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

    def test_coordinator_state_not_modified_by_demo_intake(self):
        record=prepare_tracking('2026-09-19T15:00:00Z','a');record['tracking_demo']=False
        self.assertEqual(sync_intake(record,[{'invalid':'unused'}]),record)

    def test_waiting_before_dispatch_keeps_idle_positions_usable(self):
        record=prepare_tracking('2026-09-19T15:00:00Z','a')
        record=step(record,'2026-09-19T15:10:00Z',verification=DemoVerification())
        self.assertEqual(len(dispatch_tracking(record)['missions']),2)

if __name__=='__main__':unittest.main()
