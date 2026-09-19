import unittest
from ingestion.dashboard import collect

class DashboardIngestionTests(unittest.TestCase):
    def test_replay_preserves_provenance_dedup_and_zero_observations(self):
        reports = collect()
        self.assertEqual(7, len(reports))
        self.assertEqual(7, sum(len(report['observations']) for report in reports))
        self.assertEqual(1, sum(not report['observations'] for report in reports))
        self.assertTrue(all(report['mode'] == 'social_replay' for report in reports))
        self.assertEqual([], collect(known_ids=[report['id'] for report in reports]))
        for report in reports:
            for obs in report['observations']:
                self.assertEqual('2.1', obs['schema_version'])
                self.assertEqual('unverified', obs['verification_status'])
                self.assertIsNone(obs['location']['latitude'])
