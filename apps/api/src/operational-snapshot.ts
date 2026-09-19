import { readBeliefSnapshot } from "./operational-bridge.js";
import type { Storage } from "./storage.js";

/**
 * Search's only read path into Agent 1 OperationalBeliefState.
 * Does not persist belief and does not use a second snapshot table.
 */
export function getOperationalSnapshot(
  storage: Storage,
  sessionId: string,
): Record<string, unknown> | null {
  const row = storage.one<{ snapshot_json: string }>(
    "SELECT snapshot_json FROM operational_belief_sessions WHERE session_id = ?",
    sessionId,
  );
  if (!row) return null;
  const scenarioId = storage.one<{ scenario_id: string }>(
    "SELECT scenario_id FROM sessions WHERE id = ?",
    sessionId,
  )?.scenario_id;
  if (!scenarioId) return null;
  const snapshot = readBeliefSnapshot(storage, sessionId, scenarioId);
  if (snapshot.state_version === "0" && snapshot.worksites.length === 0) {
    return null;
  }
  return snapshot as unknown as Record<string, unknown>;
}
