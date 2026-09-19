import type { Storage } from "./storage.js";

/**
 * Agent 1 owns OperationalBeliefState persistence.
 * This module does not create a belief table or compete with that schema.
 *
 * Plug-in options:
 * 1. `registerOperationalReset(handler)` — explicit session wipe.
 * 2. If Agent 1 later creates `operational_belief_states(session_id, …)`
 *    in the shared SQLite file, reset deletes that session's rows.
 */
const handlers: Array<(storage: Storage, sessionId: string) => void> = [];

const KNOWN_BELIEF_TABLES = ["operational_belief_states"] as const;

export const OPERATIONAL_BASELINE_VERSION = "0";

export function tableExists(storage: Storage, name: string): boolean {
  return Boolean(
    storage.one(
      "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
      name,
    ),
  );
}

export function registerOperationalReset(
  handler: (storage: Storage, sessionId: string) => void,
): void {
  handlers.push(handler);
}

export function clearOperationalResetHandlers(): void {
  handlers.length = 0;
}

export function resetOperationalState(
  storage: Storage,
  sessionId: string,
): {
  session_id: string;
  state_version: string;
  handlers: number;
  tables_cleared: string[];
} {
  const tables_cleared: string[] = [];
  for (const table of KNOWN_BELIEF_TABLES) {
    if (!tableExists(storage, table)) continue;
    storage.run(`DELETE FROM ${table} WHERE session_id = ?`, sessionId);
    tables_cleared.push(table);
  }
  for (const handler of handlers) handler(storage, sessionId);
  return {
    session_id: sessionId,
    state_version: OPERATIONAL_BASELINE_VERSION,
    handlers: handlers.length,
    tables_cleared,
  };
}
