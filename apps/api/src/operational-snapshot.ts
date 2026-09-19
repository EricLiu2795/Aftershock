import type { Storage } from "./storage.js";

/**
 * Agent 1 integration seam.
 *
 * Search does not persist OperationalBeliefState. Agent 1 writes one canonical
 * snapshot per demo session; this reader is the only Search access path.
 *
 * Expected table (created and written by Agent 1):
 *   operational_belief_snapshots(
 *     session_id TEXT PRIMARY KEY,
 *     state_version TEXT NOT NULL,
 *     state_json TEXT NOT NULL,
 *     updated_at TEXT NOT NULL
 *   )
 *
 * Snapshot JSON must include at least state_version, worksites, and incidents.
 * Search consumes Worksites as-is and never reconstructs them from reports.
 */
export const AGENT1_SNAPSHOT_TABLE = "operational_belief_snapshots";

export function getOperationalSnapshot(
  storage: Storage,
  sessionId: string,
): Record<string, unknown> | null {
  const table = storage.one<{ name: string }>(
    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
    AGENT1_SNAPSHOT_TABLE,
  );
  if (!table) return null;
  const row = storage.one<{ state_json: string }>(
    `SELECT state_json FROM ${AGENT1_SNAPSHOT_TABLE} WHERE session_id = ?`,
    sessionId,
  );
  if (!row?.state_json) return null;
  try {
    const value = JSON.parse(row.state_json) as unknown;
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    return value as Record<string, unknown>;
  } catch {
    return null;
  }
}
