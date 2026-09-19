"""Read-only access to Agent 1's canonical OperationalBeliefState.

This module is the Search integration seam. It does not persist, reduce, or
reconstruct belief. Agent 1 owns runtime snapshot writes. Search calls
`get_operational_snapshot(session_id)` and consumes Worksites as-is.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable, Mapping

# Agent 1 is expected to persist one canonical snapshot per demo session.
# Search only reads this table; it never creates Worksites from reports.
AGENT1_SNAPSHOT_TABLE = "operational_belief_snapshots"
AGENT1_SNAPSHOT_COLUMNS = ("session_id", "state_version", "state_json", "updated_at")

# Optional Python module Agent 1 may land in parallel:
#   operational.runtime_store.get_snapshot(session_id) -> dict | None
SnapshotLoader = Callable[[str], Mapping | None]


def get_operational_snapshot(
    session_id: str | None,
    *,
    loader: SnapshotLoader | None = None,
    sqlite_path: str | Path | None = None,
) -> dict | None:
    """Return the canonical OperationalBeliefState for a session, or None.

    Lookup order:
    1. Injected `loader` (tests and Agent 1 wiring)
    2. `operational.runtime_store.get_snapshot` if Agent 1 added that module
    3. SQLite table `operational_belief_snapshots` (same API database)

    Never loads `data/search_demo` fixture Worksites.
    Never reconstructs Worksites from raw radio/social reports.
    """
    if not session_id:
        return None
    if loader is not None:
        snapshot = loader(session_id)
        return dict(snapshot) if snapshot is not None else None
    snapshot = _load_runtime_store(session_id)
    if snapshot is not None:
        return snapshot
    if sqlite_path:
        return _load_sqlite_snapshot(session_id, sqlite_path)
    return None


def _load_runtime_store(session_id: str) -> dict | None:
    try:
        from operational.runtime_store import get_snapshot as agent1_get
    except ImportError:
        return None
    snapshot = agent1_get(session_id)
    return dict(snapshot) if snapshot is not None else None


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
            f"SELECT state_json FROM {AGENT1_SNAPSHOT_TABLE} WHERE session_id=?",
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
