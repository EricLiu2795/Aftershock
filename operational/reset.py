"""Session-scoped OperationalBeliefState reset hook.

Agent 1 owns belief persistence. This module does **not** define a SQLite
schema and must not grow a competing store. The Node demo lifecycle calls
`resetOperationalState(sessionId)`; Python callers use
`reset_operational_state(session_id, store=...)`.

If `store` is omitted, the hook returns an in-memory baseline
OperationalBeliefState (`state_version` `"0"`) without writing anything.
If `store` exposes `clear_session(session_id)` or `delete(session_id)`,
that method is invoked so Agent 1's persistence can plug in later.
"""

from __future__ import annotations

from operational.belief_state import empty_state

BASELINE_STATE_VERSION = "0"
CARABALLEDA_START = "2026-09-19T14:00:00Z"


def reset_operational_state(session_id, store=None, *, scenario_time=CARABALLEDA_START):
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id is required")
    session_id = session_id.strip()
    cleared = False
    if store is not None:
        if hasattr(store, "clear_session"):
            store.clear_session(session_id)
            cleared = True
        elif hasattr(store, "delete"):
            store.delete(session_id)
            cleared = True
        else:
            raise TypeError(
                "store must implement clear_session(session_id) or delete(session_id)"
            )
    baseline = empty_state(scenario_time)
    return {
        "session_id": session_id,
        "cleared": cleared,
        "state_version": BASELINE_STATE_VERSION,
        "belief": baseline.to_belief_state(),
    }
