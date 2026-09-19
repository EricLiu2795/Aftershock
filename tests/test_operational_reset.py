"""Agent 1 OperationalBeliefState reset hook — no competing schema."""

import unittest

from operational.belief_state import empty_state
from operational.reset import BASELINE_STATE_VERSION, reset_operational_state


class MemoryStore:
    def __init__(self):
        self.sessions = {"session-a": {"state_version": "4"}, "session-b": {"state_version": "8"}}

    def clear_session(self, session_id):
        self.sessions.pop(session_id, None)


class OperationalResetTests(unittest.TestCase):
    def test_reset_without_store_returns_baseline_belief(self):
        result = reset_operational_state("session-a")
        self.assertEqual(result["session_id"], "session-a")
        self.assertFalse(result["cleared"])
        self.assertEqual(result["state_version"], BASELINE_STATE_VERSION)
        self.assertEqual(result["belief"]["state_version"], "0")
        self.assertEqual(result["belief"]["sites"], [])
        self.assertEqual(result["belief"]["worksites"], [])
        self.assertEqual(empty_state("2026-09-19T14:00:00Z").state_version, "0")

    def test_reset_is_idempotent_and_session_scoped(self):
        store = MemoryStore()
        first = reset_operational_state("session-a", store)
        second = reset_operational_state("session-a", store)
        self.assertTrue(first["cleared"])
        self.assertTrue(second["cleared"])
        self.assertEqual(first["state_version"], second["state_version"])
        self.assertNotIn("session-a", store.sessions)
        self.assertIn("session-b", store.sessions)
        self.assertEqual(store.sessions["session-b"]["state_version"], "8")

    def test_rejects_a_store_without_a_clear_hook(self):
        with self.assertRaises(TypeError):
            reset_operational_state("session-a", store=object())
        with self.assertRaises(ValueError):
            reset_operational_state("")
