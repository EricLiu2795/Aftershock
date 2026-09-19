/** Materialize published drone observations in the existing session database. */
import { existsSync, realpathSync, statSync } from "node:fs";
import { resolve, sep, extname } from "node:path";
import { fileURLToPath } from "node:url";
import type { Storage } from "./storage.js";

const packRoot = fileURLToPath(new URL("../../../data/scenarios/caraballeda/", import.meta.url));
type Asset = { asset_id: string; site_id: string; path: string; file_status: string;
  placeholder: boolean; [key: string]: unknown };
type Report = { input_id: string; imagery_reference?: string | null; metadata?: { verification_assets?: Asset[] }; [key: string]: unknown };
type Published = { event_id: string; event_type: string; occurred_at: string;
  payload: { drone_input: Report; observations: { observation_id: string }[] } };
type Mission = { mission_id: string; site_id: string; worksite_id: string; resource: { resource_id: string }; events: Published[] };

export function setupDroneReports(storage: Storage) {
  storage.db.exec(`
    CREATE TABLE IF NOT EXISTS drone_reports (
      session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
      report_id TEXT NOT NULL, mission_id TEXT NOT NULL, resource_id TEXT NOT NULL,
      site_id TEXT NOT NULL, worksite_id TEXT NOT NULL, published_at TEXT NOT NULL,
      payload_json TEXT NOT NULL, PRIMARY KEY(session_id, report_id));
    CREATE TABLE IF NOT EXISTS drone_observations (
      session_id TEXT NOT NULL, observation_id TEXT NOT NULL, report_id TEXT NOT NULL,
      payload_json TEXT NOT NULL, PRIMARY KEY(session_id, observation_id),
      FOREIGN KEY(session_id, report_id) REFERENCES drone_reports(session_id, report_id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS drone_verification_assets (
      session_id TEXT NOT NULL, report_id TEXT NOT NULL, asset_id TEXT NOT NULL,
      payload_json TEXT NOT NULL, PRIMARY KEY(session_id, report_id, asset_id),
      FOREIGN KEY(session_id, report_id) REFERENCES drone_reports(session_id, report_id) ON DELETE CASCADE);
  `);
}

/** Caller must run inside the same transaction that saves the mission revision. */
export function persistDroneReports(storage: Storage, session: string, record: { missions: unknown[] }) {
  for (const m of record.missions as Mission[]) {
    for (const event of m.events) {
      if (event.event_type !== "DRONE_REPORT_READY") continue;
      const { drone_input: report, observations } = event.payload;
      if (storage.one("SELECT 1 FROM drone_reports WHERE session_id=? AND report_id=?", session, report.input_id)) continue;
      storage.run("INSERT INTO drone_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)", session, report.input_id,
        m.mission_id, m.resource.resource_id, m.site_id, m.worksite_id, event.occurred_at, JSON.stringify(report));
      for (const observation of observations) storage.run("INSERT INTO drone_observations VALUES (?, ?, ?, ?)",
        session, observation.observation_id, report.input_id, JSON.stringify(observation));
      for (const asset of report.metadata?.verification_assets ?? []) storage.run(
        "INSERT INTO drone_verification_assets VALUES (?, ?, ?, ?)", session, report.input_id, asset.asset_id, JSON.stringify(asset));
    }
  }
}

export function mediaFile(asset: Asset, root = packRoot): { path: string; mime: string } | undefined {
  if (asset.file_status !== "present" || asset.placeholder || !asset.path.startsWith("assets/drone/")) return;
  const allowed = resolve(root, "assets/drone");
  const path = resolve(root, asset.path);
  if (!path.startsWith(allowed + sep) || !existsSync(path)) return;
  const real = realpathSync(path);
  if (!real.startsWith(realpathSync(allowed) + sep) || !statSync(real).isFile()) return;
  const mime = ({ ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp" } as Record<string, string>)[extname(real).toLowerCase()];
  if (mime) return { path: real, mime };
}

export function findRevealedMedia(storage: Storage, session: string, assetId: string) {
  const rows = storage.all<{ payload_json: string }>(
    "SELECT payload_json FROM drone_verification_assets WHERE session_id=? AND asset_id=?", session, assetId);
  for (const row of rows) {
    const file = mediaFile(JSON.parse(row.payload_json) as Asset);
    if (file) return file;
  }
}

export function droneReports(storage: Storage, session: string) {
  return storage.all<{ report_id: string; mission_id: string; resource_id: string; site_id: string; worksite_id: string;
    published_at: string; payload_json: string }>("SELECT * FROM drone_reports WHERE session_id=? ORDER BY published_at, report_id", session)
    .map(({ payload_json, ...row }) => ({ ...row, report: JSON.parse(payload_json),
      observations: storage.all<{ payload_json: string }>(
        "SELECT payload_json FROM drone_observations WHERE session_id=? AND report_id=? ORDER BY observation_id", session, row.report_id)
        .map(o => JSON.parse(o.payload_json)),
      assets: storage.all<{ payload_json: string }>(
        "SELECT payload_json FROM drone_verification_assets WHERE session_id=? AND report_id=? ORDER BY asset_id", session, row.report_id)
        .map(a => {
          const asset = JSON.parse(a.payload_json) as Asset;
          const available = Boolean(mediaFile(asset));
          return { ...asset, available, image_url: available
            ? `/api/demo-sessions/${encodeURIComponent(session)}/search/assets/${encodeURIComponent(asset.asset_id)}` : null };
        }),
    }));
}
