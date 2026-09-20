/** Staggered release of imported replay reports so information arrives like a real feed.
 *
 * All replay reports are stored at import time, but each gets a release offset: the first
 * report is visible immediately, then two more per interval. Elapsed time is scaled by the
 * session's drone simulation speed so the information flow keeps pace with the drones.
 * Unreleased reports are invisible to the intelligence feed and therefore to drone intake.
 * An interval of 0 disables staggering (everything is released at once).
 */
import type { Storage } from "./storage.js";

export type ReleaseRow = { imported_at: string | null; release_offset_s: number | null; payload_json?: string };

/** Offsets in seconds by arrival index: 0, then pairs at 1×, 2×, 3×… the interval.
 *  `startIndex` shifts the series so a caller that already published an opening beat
 *  (Start Demo plays the pack narrative first) does not release a second report at once. */
export function releaseOffsets(count: number, intervalS: number, startIndex = 0): number[] {
  return Array.from({ length: count }, (_, i) => {
    const index = i + startIndex;
    return intervalS <= 0 || index === 0 ? 0 : Math.ceil(index / 2) * intervalS;
  });
}

export function releasedReports(storage: Storage, session: string, nowMs = Date.now()) {
  const rows = storage.all<ReleaseRow>("SELECT imported_at, release_offset_s, payload_json FROM external_reports WHERE session_id=?", session);
  const speed = simulationSpeed(storage, session);
  return rows.filter(r => isReleased(r, nowMs, speed)).length;
}

export function isReleased(row: ReleaseRow, nowMs: number, speed: number) {
  if (row.release_offset_s == null || row.release_offset_s <= 0 || !row.imported_at) return true;
  // One-click demo arrivals use wall time so changing vehicle speed cannot dump the feed.
  // Legacy/manual replay keeps its existing simulation-scaled batches.
  const wallClock = row.payload_json && JSON.parse(row.payload_json).release_clock === "wall";
  const elapsed = Math.max(0, nowMs - Date.parse(row.imported_at)) / 1000 * (wallClock ? 1 : Math.max(1, speed));
  return elapsed >= row.release_offset_s;
}

/** Current drone simulation speed for the session (1 when Search has not been prepared). */
export function simulationSpeed(storage: Storage, session: string): number {
  try {
    const row = storage.one<{ record_json: string }>("SELECT record_json FROM drone_search_sessions WHERE session_id=?", session);
    const speed = row ? (JSON.parse(row.record_json) as { simulation_speed?: number }).simulation_speed : undefined;
    return typeof speed === "number" && speed > 0 ? speed : 1;
  } catch { return 1; }
}

export function pendingReplay(storage: Storage, session: string, nowMs = Date.now()) {
  const rows = storage.all<ReleaseRow>("SELECT imported_at, release_offset_s, payload_json FROM external_reports WHERE session_id=?", session);
  const speed = simulationSpeed(storage, session);
  return rows.filter(r => !isReleased(r, nowMs, speed)).length;
}

/** Operator shortcut: make the earliest pending batch visible now. Returns how many were released. */
export function releaseNextBatch(storage: Storage, session: string, nowMs = Date.now()) {
  const rows = storage.all<ReleaseRow & { report_id: string }>(
    "SELECT report_id, imported_at, release_offset_s, payload_json FROM external_reports WHERE session_id=?", session);
  const speed = simulationSpeed(storage, session);
  const pending = rows.filter(r => !isReleased(r, nowMs, speed));
  if (!pending.length) return 0;
  const next = Math.min(...pending.map(r => r.release_offset_s!));
  const batch = pending.filter(r => r.release_offset_s === next);
  storage.transaction(() => {
    for (const r of batch) storage.run("UPDATE external_reports SET release_offset_s=0 WHERE session_id=? AND report_id=?", session, r.report_id);
    // Keep later batches on the same relative cadence from this moment.
    for (const r of pending) if (r.release_offset_s! > next) storage.run(
      "UPDATE external_reports SET imported_at=?, release_offset_s=? WHERE session_id=? AND report_id=?",
      new Date(nowMs).toISOString(), r.release_offset_s! - next, session, r.report_id);
  });
  return batch.length;
}
