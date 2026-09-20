/** Session-scoped road sensor AUDIT, committed atomically with the drone simulation tick.
 *  Authoritative road state lives only in operational_belief_sessions; these rows record
 *  what a drone reported, never what the world is. Nothing plans or renders from them. */
import type { Storage } from "./storage.js";
type RoadState = { road_id: string; status: "open" | "limited" | "blocked"; updated_at: string; [key: string]: unknown };
type Detection = { detection_id: string; mission_id: string; resource_id: string; condition_effective_at: string;
  assessment: { road_state: RoadState }; [key: string]: unknown };
export const RADIUS_M = 500;
export function setupRoadScans(storage: Storage) {
  storage.db.exec(`CREATE TABLE IF NOT EXISTS drone_road_assessments (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    detection_id TEXT NOT NULL, road_id TEXT NOT NULL, observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL, PRIMARY KEY(session_id, detection_id));
    CREATE INDEX IF NOT EXISTS drone_road_by_edge ON drone_road_assessments(session_id, road_id);
    CREATE TABLE IF NOT EXISTS drone_road_states (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    road_id TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(session_id, road_id));`);
}
export function persistRoadScans(storage: Storage, sessionId: string, record: Record<string, unknown>) {
  for (const d of (record.road_scans ?? []) as Detection[]) {
    const road = d.assessment.road_state;
    storage.run("INSERT OR IGNORE INTO drone_road_assessments VALUES (?, ?, ?, ?, ?)",
      sessionId, d.detection_id, road.road_id, road.updated_at, JSON.stringify(d));
  }
  for (const road of (record.road_state_updates ?? []) as RoadState[]) {
    storage.run(`INSERT INTO drone_road_states VALUES (?, ?, ?)
      ON CONFLICT(session_id,road_id) DO UPDATE SET payload_json=excluded.payload_json`,
      sessionId, road.road_id, JSON.stringify(road));
  }
}
export function roadScanSnapshot(storage: Storage, sessionId: string,
  canonical: { state_version: string; road_states: unknown[] }) {
  return { detection_radius_m: RADIUS_M,
    road_state_source: "operational_belief_state",
    canonical_state_version: canonical.state_version,
    road_states: canonical.road_states,
    detected_road_states: storage.all<{ payload_json: string }>(
      "SELECT payload_json FROM drone_road_states WHERE session_id=? ORDER BY road_id", sessionId).map(r => JSON.parse(r.payload_json)),
    detections: storage.all<{ payload_json: string }>(
      "SELECT payload_json FROM drone_road_assessments WHERE session_id=? ORDER BY observed_at,detection_id", sessionId).map(r => JSON.parse(r.payload_json)) };
}
