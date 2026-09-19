from copy import deepcopy
from datetime import timedelta
import unittest
from search_planner.common import stamp, time, validate
from search_planner.demo import sample_state
from search_planner.reassignment import initialize, step
from search_planner.runtime import advance_mission


class ReassignmentTests(unittest.TestCase):
    def setUp(self):
        self.state = sample_state()
        for r in self.state['resources']:
            if r['resource_type'] == 'drone_unit':
                r['speed'] = {'value': .1, 'unit': 'm_per_s'}
        self.wall = time('2026-09-19T15:00:00Z')
        self.initial = initialize(self.state, stamp(self.wall))
        self.changed = deepcopy(self.state)
        self.changed['state_version'] = 'changed-2'
        self.changed['worksites'][1]['metadata']['search_features'] = dict(
            potential_impact=1, uncertainty=1, criticality=1, evidence_conflict=1)
        self.pending = step(self.initial, self.at(1), state=self.changed)
        self.proposal = self.pending['proposals'][0]['proposal']

    def at(self, seconds):
        return stamp(self.wall + timedelta(seconds=seconds))

    def decide(self, seconds, choice='approve', record=None):
        return step(record or self.pending, self.at(seconds), decision={
            'proposal_id': self.proposal['proposal_id'], 'decision': choice, 'operator_id': 'human-1'})

    def test_pending_does_not_stop_or_redirect(self):
        current = self.pending['missions'][0]
        next_state = step(self.pending, self.at(20))
        expected = advance_mission(current, stamp(time(current['last_updated_at']) + timedelta(seconds=19)))
        self.assertEqual(next_state['missions'][0], expected)
        self.assertEqual(next_state['proposals'][0]['proposal']['status'], 'pending')
        self.assertEqual(self.proposal['expires_at'], self.at(31))
        self.assertNotEqual(expected['movement']['position'], current['movement']['position'])

    def test_no_response_expires_at_exact_deadline_and_keeps_route(self):
        for seconds in (31, 40, 100):
            with self.subTest(seconds=seconds):
                result = step(self.pending, self.at(seconds))
                self.assertEqual(result['proposals'][0]['proposal']['status'], 'expired')
                self.assertEqual(result['missions'][0]['mission_id'], self.pending['missions'][0]['mission_id'])
                self.assertFalse(result['history'])
                self.assertNotIn('human_decision', result['proposals'][0]['proposal'])

    def test_late_approval_and_retry_cannot_switch(self):
        for seconds in (31, 31.001, 60):
            self.assertEqual(self.decide(seconds)['proposals'][0]['proposal']['status'], 'expired')
        rejected = self.decide(2, 'reject')
        retried = self.decide(3, record=rejected)
        self.assertEqual(retried['proposals'][0]['proposal']['status'], 'rejected')
        self.assertFalse(retried['history'])

    def test_approve_before_deadline_reroutes_from_current_position_preserves_home(self):
        result = self.decide(30.999)
        self.assertEqual(result['proposals'][0]['proposal']['status'], 'approved')
        old = self.pending['missions'][0]
        moved = advance_mission(old, stamp(time(old['last_updated_at']) + timedelta(seconds=29.999)))
        new = result['missions'][0]
        self.assertEqual(new['outbound_route']['path'][0], moved['movement']['position'])
        self.assertEqual(new['home_position'], old['home_position'])
        self.assertEqual(new['worksite_id'], self.proposal['proposed_worksite_id'])
        self.assertEqual(len(result['history']), 1)
        retry = self.decide(31, record=result)
        self.assertEqual(len(retry['history']), 1)
        self.assertEqual(retry['missions'][0]['mission_id'], new['mission_id'])
        validate('operational_belief_state.schema.json', result['state'])

    def test_changed_evidence_invalidates_even_with_yes(self):
        state = deepcopy(self.pending['state'])
        state['worksites'][1]['metadata']['search_features']['uncertainty'] = .99
        result = step(self.pending, self.at(2), state=state, decision={
            'proposal_id': self.proposal['proposal_id'], 'decision': 'approve'})
        self.assertEqual(result['proposals'][0]['proposal']['status'], 'invalidated')
        self.assertFalse(result['history'])

    def test_reject_cooldown_prevents_repeated_prompts(self):
        result = self.decide(2, 'reject')
        result = step(result, self.at(61))
        self.assertEqual(len(result['proposals']), 1)
        result = step(result, self.at(62))
        self.assertEqual(len(result['proposals']), 2)

    def test_arrival_invalidates_pending_and_keeps_verification(self):
        state = sample_state()
        initial = initialize(state, self.at(0))
        changed = deepcopy(state)
        changed['worksites'][1]['metadata']['search_features'] = self.changed['worksites'][1]['metadata']['search_features']
        pending = step(initial, self.at(1), state=changed)
        self.assertTrue(pending['proposals'])
        result = step(pending, self.at(15), decision={
            'proposal_id': pending['proposals'][0]['proposal']['proposal_id'], 'decision': 'approve'})
        self.assertEqual(result['proposals'][0]['proposal']['status'], 'invalidated')
        self.assertEqual(result['missions'][0]['phase'], 'verifying')

    def test_no_material_gain_no_proposal_and_input_immutable(self):
        original = deepcopy(self.initial)
        self.assertFalse(step(self.initial, self.at(2))['proposals'])
        self.assertEqual(self.initial, original)
        self.assertEqual(step(self.initial, self.at(1), state=self.changed), self.pending)

    def test_unknown_choices_and_old_mission_reports_fail_closed(self):
        with self.assertRaises(ValueError): self.decide(2, 'yes')
        with self.assertRaises(ValueError):
            step(self.pending, self.at(2), decision={'proposal_id': 'fake', 'decision': 'approve'})
        result = self.decide(2)
        with self.assertRaises(ValueError):
            step(result, self.at(3), report={'mission_id': self.pending['missions'][0]['mission_id'], 'report': {}})
        with self.assertRaises(ValueError): step(self.pending, self.at(0))


if __name__ == '__main__': unittest.main()
