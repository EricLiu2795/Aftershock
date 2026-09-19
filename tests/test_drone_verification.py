from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from search_planner.common import stamp, time, validate
from search_planner.planner import plan_search
from search_planner.runtime import start_mission
from search_planner.reassignment import initialize, step
from search_planner.verification import DemoVerification, PACK

ROOT = Path(__file__).resolve().parents[1]


def scan_state(key='W2', drone='drone-2', start='2026-09-19T14:00:00Z'):
    state = json.loads((ROOT/'contracts/examples/operational_belief_state.json').read_text())
    manifest = json.loads((PACK/'scenario.json').read_text())
    active = json.loads((PACK/'active_sites.json').read_text())
    site = next(w['site'] for w in active['active_worksites'] if w['worksite_key'] == key)
    work = deepcopy(state['worksites'][0])
    work.update(worksite_id='scan-'+key, site_id=site['site_id'], active_incident_ids=[], evidence_ids=[], assigned_resource_ids=[],
                metadata={'search_features':dict(potential_impact=.8, uncertainty=.8, criticality=.5, evidence_conflict=.5)})
    state.update(state_version='scan-test', scenario_time=start, updated_at=start, worksites=[work], sites=[site],
                 resources=[r for r in manifest['resources'] if r['resource_id'] == drone],
                 movement_states=[m for m in manifest['initial_movement_states'] if m['resource_id'] == drone],
                 incidents=[], active_routes=[], pending_reassignment_proposals=[])
    state['movement_states'][0]['last_updated_at'] = start
    validate('operational_belief_state.schema.json', state)
    return state


class VerificationTests(unittest.TestCase):
    def mission(self, key='W2', **kwargs):
        state = scan_state(key, **kwargs)
        plan = plan_search(state)
        return start_mission(plan, state['resources'][0]['resource_id'], state)

    def ready(self, mission, event='2026-09-19T14:01:45Z'):
        return max(time(mission['launched_at']) + timedelta(seconds=mission['outbound_route']['estimated_travel_time_s'] + 30), time(event))

    def test_no_early_reveal_then_publish_after_scan_and_reporting(self):
        m = self.mission(); provider = DemoVerification(); ready = self.ready(m)
        before = provider.advance(m, stamp(ready - timedelta(microseconds=1)))
        self.assertIsNone(before['drone_report'])
        received = provider.advance(before, stamp(ready))
        self.assertEqual(received['phase'], 'reporting')
        self.assertFalse(any(e['event_type']=='DRONE_REPORT_READY' for e in received['events']))
        published = provider.advance(received, stamp(ready + timedelta(seconds=2)))
        self.assertEqual(published['phase'], 'returning')
        report = published['drone_report']
        self.assertEqual(report['victim_indicator'], dict(type='possible_multiple', count_min=None, count_max=None))
        self.assertEqual(report['imagery_reference'], 'imagery://drone-1/frame-140145')
        self.assertEqual(report['drone_id'], 'drone-2')
        self.assertEqual(report['metadata']['scenario_original_drone_id'], 'drone-1')
        self.assertEqual(report['location']['resolved_site_id'], m['site_id'])
        self.assertEqual(report['metadata']['verification_assets'][0]['file_status'], 'placeholder')
        self.assertEqual(len([e for e in published['events'] if e['event_type']=='DRONE_REPORT_READY']), 1)

    def test_late_tick_matches_fine_ticks_and_deduplicates(self):
        m = self.mission(); provider = DemoVerification(); ready = self.ready(m)
        now = stamp(ready + timedelta(seconds=100))
        late = provider.advance(m, now)
        fine = provider.advance(provider.advance(m, stamp(ready)), now)
        self.assertEqual(late, fine)
        self.assertEqual(late, provider.advance(late, now))

    def test_scan_must_finish_even_if_script_event_already_available(self):
        m = self.mission(start='2026-09-19T14:10:00Z'); provider = DemoVerification()
        end = self.ready(m)
        self.assertIsNone(provider.advance(m, stamp(end-timedelta(seconds=1)))['drone_report'])
        self.assertIsNotNone(provider.advance(m, stamp(end))['drone_report'])

    def test_school_negative_finding_preserved_without_confirmation_upgrade(self):
        m = self.mission('W7'); provider = DemoVerification()
        ready = self.ready(m, '2026-09-19T14:04:28Z')
        complete = provider.advance(m, stamp(ready + timedelta(seconds=2)))
        event = next(e for e in complete['events'] if e['event_type']=='DRONE_REPORT_READY')
        observations = event['payload']['observations']
        self.assertEqual(len(observations), 3)
        for observation in observations:
            self.assertEqual(observation['victim_indication'], dict(type='none', count_min=0, count_max=0))
            self.assertEqual(observation['verification_status'], 'unverified')
        self.assertEqual(complete['drone_report']['metadata']['verification_assets'][0]['asset_id'], 'va-w7-drone-topdown')

    def test_missing_site_result_waits_and_never_borrows_another_image(self):
        m = self.mission('W1'); provider = DemoVerification()
        result = provider.advance(m, '2026-09-19T15:00:00Z')
        self.assertEqual(result['phase'], 'reporting')
        self.assertIsNone(result['drone_report'])

    def test_hidden_ground_truth_is_never_read(self):
        read = Path.read_text
        def guarded(path, *args, **kwargs):
            if path.name == 'scenario_ground_truth.json':
                raise AssertionError('Hidden truth must never be read')
            return read(path, *args, **kwargs)
        m = self.mission()
        with patch.object(Path, 'read_text', guarded):
            result = DemoVerification().advance(m, stamp(self.ready(m)+timedelta(seconds=3)))
        self.assertNotIn('hidden_truth_linkage', json.dumps(result))
        self.assertNotIn('expected_downstream_interpretation', json.dumps(result))

    def test_controller_receives_reports_without_manual_submission(self):
        state = scan_state(); record = initialize(state, '2026-09-19T15:00:00Z')
        result = step(record, '2026-09-19T15:10:00Z', verification=DemoVerification())
        self.assertIsNotNone(result['missions'][0]['drone_report'])
        self.assertEqual(result['missions'][0]['phase'], 'idle')

    def test_unrevealed_reserved_assets_are_not_exposed(self):
        m = self.mission(); result = DemoVerification().advance(m, stamp(self.ready(m)+timedelta(seconds=3)))
        blob = json.dumps(result)
        self.assertNotIn('va-w1-', blob); self.assertNotIn('va-w5-', blob)


if __name__ == '__main__': unittest.main()
