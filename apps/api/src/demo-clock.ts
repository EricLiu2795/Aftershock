import type { Storage } from "./storage.js";

/** Demo elapsed time only; authentication, session expiry and audit time stay real. */
export function demoClock(storage: Storage, sessionId: string, wallNow = Date.now()) {
  const row = storage.one<{ demo_paused_at: number | null; demo_pause_ms: number; demo_pause_revision: number; ended_at: string | null; expires_at: string }>(
    "SELECT demo_paused_at, demo_pause_ms, demo_pause_revision, ended_at, expires_at FROM sessions WHERE id=?", sessionId);
  return {
    paused: row?.demo_paused_at != null,
    revision: row?.demo_pause_revision ?? 0,
    nowMs: (row?.demo_paused_at ?? wallNow) - (row?.demo_pause_ms ?? 0),
    closed: !row || !!row.ended_at || Date.parse(row.expires_at) <= wallNow,
  };
}

export function setDemoPaused(storage: Storage, sessionId: string, paused: boolean, wallNow = Date.now()) {
  return storage.transaction(() => {
    const current = demoClock(storage, sessionId, wallNow);
    if (current.closed) throw Object.assign(new Error("The session has ended"), { statusCode: 410 });
    if (current.paused !== paused) {
      if (paused) storage.run("UPDATE sessions SET demo_paused_at=?, demo_pause_revision=demo_pause_revision+1 WHERE id=?", wallNow, sessionId);
      else storage.run("UPDATE sessions SET demo_pause_ms=demo_pause_ms+MAX(0, ?-demo_paused_at), demo_paused_at=NULL, demo_pause_revision=demo_pause_revision+1 WHERE id=?", wallNow, sessionId);
    }
    return demoClock(storage, sessionId, wallNow);
  });
}
