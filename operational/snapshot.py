"""Read-only access to Agent 1's canonical OperationalBeliefState.

Search does not persist belief. The only owner is:

    operational_belief_sessions.snapshot_json

written by `python -m operational.runtime_bridge` through
`applySessionObservations`. This reader never creates
`operational_belief_snapshots` or any second belief store.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable, Mapping

AGENT1_SNAPSHOT_TABLE = "operational_belief_sessions"
AGENT1_SNAPSHOT_COLUMNS = (
    "session_id",
    "revision",
    "record_json",
    "snapshot_json",
    "updated_at",
)

SnapshotLoader = Callable[[str], Mapping | None]


def get_operational_snapshot(
    session_id: str | None,
    *,
    loader: SnapshotLoader | None = None,
    sqlite_path: str | Path | None = None,
) -> dict | None:
    """Return the canonical OperationalBeliefState snapshot for a session, or None.

    Lookup order:
    1. Injected `loader` (tests)
    2. SQLite `operational_belief_sessions.snapshot_json`

    Empty state_version 0 with no worksites is treated as no snapshot so Search
    stays uninitialized until the first meaningful operational state.
    """
    if not session_id:
        return None
    if loader is not None:
        snapshot = loader(session_id)
        return _usable(dict(snapshot) if snapshot is not None else None)
    if sqlite_path:
        return _usable(_load_sqlite_snapshot(session_id, sqlite_path))
    return None


def _usable(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    version = snapshot.get("state_version")
    worksites = snapshot.get("worksites") or []
    if (not version or version == "0") and not worksites:
        return None
    return snapshot


def _load_sqlite_snapshot(session_id: str, sqlite_path: str | Path) -> dict | None:
    path = Path(sqlite_path)
    if not path.exists():
        return None
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (AGENT1_SNAPSHOT_TABLE,),
        ).fetchone()
        if row is None:
            return None
        record = connection.execute(
            f"SELECT snapshot_json FROM {AGENT1_SNAPSHOT_TABLE} WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if record is None or record[0] is None:
            return None
        value = json.loads(record[0])
        return value if isinstance(value, dict) else None
    except sqlite3.Error:
        return None
    finally:
        connection.close()
