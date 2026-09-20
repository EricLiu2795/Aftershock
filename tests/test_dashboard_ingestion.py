import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ingestion.dashboard import collect

# The building fixture carries one actionable social post per Caraballeda worksite W1..W8.
REPLAY_REPORT_COUNT = 8

NOISE_REPLAY = [
    {
        "schema_version": "2.1",
        "input_id": "replay-1",
        "timestamp": "2026-09-19T14:00:00Z",
        "platform": "replay",
        "external_id": "post-001",
        "text": "My parents may be trapped near Riverside Apartments.",
        "language": "en",
    },
    {
        "schema_version": "2.1",
        "input_id": "replay-4",
        "timestamp": "2026-09-19T14:03:00Z",
        "platform": "replay",
        "external_id": "post-004",
        "text": "Finally finished my favorite puzzle. What should I cook tonight?",
        "language": "en",
    },
    {
        "schema_version": "2.1",
        "input_id": "replay-5",
        "timestamp": "2026-09-19T14:04:00Z",
        "platform": "replay",
        "external_id": "post-001",
        "text": "My parents may be trapped near Riverside Apartments.",
        "language": "en",
    },
]


class DashboardIngestionTests(unittest.TestCase):
    def test_replay_preserves_provenance_dedup_and_zero_observations(self):
        reports = collect()
        self.assertEqual(REPLAY_REPORT_COUNT, len(reports))
        self.assertEqual(REPLAY_REPORT_COUNT, sum(len(report['observations']) for report in reports))
        self.assertEqual(0, sum(not report['observations'] for report in reports))
        self.assertTrue(all(report['mode'] == 'social_replay' for report in reports))
        self.assertEqual(
            sorted(f'simulated-witness-W{index}' for index in range(1, REPLAY_REPORT_COUNT + 1)),
            sorted(report['author'] for report in reports),
        )
        self.assertEqual([], collect(known_ids=[report['id'] for report in reports]))
        for report in reports:
            for obs in report['observations']:
                self.assertEqual('2.1', obs['schema_version'])
                self.assertEqual('unverified', obs['verification_status'])
                self.assertIsNone(obs['location']['latitude'])

    def test_noise_posts_are_filtered_and_repeated_identity_collapses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.json"
            path.write_text(json.dumps(NOISE_REPLAY), encoding="utf-8")
            with mock.patch.dict(os.environ, {"INGESTION_REPLAY_FILE": str(path)}):
                reports = collect()
        # replay-1 and replay-5 share one external identity, and the chatter post extracts nothing.
        self.assertEqual(2, len(reports))
        self.assertEqual(1, sum(not report['observations'] for report in reports))
        self.assertEqual(1, sum(len(report['observations']) for report in reports))
