/** Hidden simulation oracle: what a drone would find inside each building once it scans it.
 *
 * Rows are seeded from the Caraballeda pack once and upserted idempotently, joined by canonical
 * OSM site ID. They exist to answer a completed scan — simulated people count, simulated
 * structural result, verification response, photo asset — and never to enrich an unscanned
 * Search Worksite. The photo is served only once this session's scan found people (red).
 */
import { readFileSync } from "node:fs";
import { mediaFile } from "./drone-reports.js";
import type { Storage } from "./storage.js";

const packRoot = new URL("../../../data/scenarios/caraballeda/", import.meta.url);
const read = <T>(name: string): T => JSON.parse(readFileSync(new URL(name, packRoot), "utf8")) as T;

export type BuildingRow = {
  scenario_id: string; pack_scenario_id: string; site_id: string; worksite_key: string; ground_truth_id: string;
  display_name: string; latitude: number; longitude: number;
  people_count: number; people_trapped: number; structural_state: string; access_state: string; drone_verifiable: number;
  photo_asset_id: string | null; photo_path: string | null; photo_caption: string | null; simulated: number; updated_at: string;
};

type Truth = { scenario_id: string; worksites: { ground_truth_id: string; worksite_key: string; site_id: string;
  victims: { count: number; trapped: boolean }; structural_state: string; access_state: string; drone_verifiable: boolean }[] };
type Active = { active_worksites: { worksite_key: string; display_name: string;
  site: { site_id: string; centroid: { latitude: number; longitude: number } } }[] };
type Assets = { assets: { asset_id: string; site_id: string; path: string; caption?: string; usable_for_verification: boolean;
  placeholder: boolean; file_status: string }[] };

/** Session scenario key used by `sessions.scenario_id`; the pack itself is versioned separately. */
export const SCENARIO = "caraballeda";

export function setupScenarioBuildings(storage: Storage) {
  storage.db.exec(`
    CREATE TABLE IF NOT EXISTS scenario_buildings (
      scenario_id TEXT NOT NULL, pack_scenario_id TEXT NOT NULL, site_id TEXT NOT NULL, worksite_key TEXT NOT NULL,
      ground_truth_id TEXT NOT NULL, display_name TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
      people_count INTEGER NOT NULL CHECK(people_count >= 0), people_trapped INTEGER NOT NULL CHECK(people_trapped IN (0, 1)),
      structural_state TEXT NOT NULL, access_state TEXT NOT NULL, drone_verifiable INTEGER NOT NULL,
      photo_asset_id TEXT, photo_path TEXT, photo_caption TEXT, simulated INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
      PRIMARY KEY(scenario_id, site_id));`);
  seedScenarioBuildings(storage);
}

/** Idempotent: re-running keeps one row per canonical building and refreshes pack values. */
export function seedScenarioBuildings(storage: Storage) {
  const truth = read<Truth>("scenario_ground_truth.json");
  const active = read<Active>("active_sites.json");
  const assets = read<Assets>("verification_assets.json").assets;
  const byId = new Map(active.active_worksites.map(w => [w.site.site_id, w]));
  const now = new Date().toISOString();
  storage.transaction(() => {
    for (const entry of truth.worksites) {
      const site = byId.get(entry.site_id);
      if (!site || site.worksite_key !== entry.worksite_key) throw new Error(`Scenario building ${entry.site_id} has no matching canonical active site`);
      const count = entry.victims.count;
      if (!Number.isInteger(count) || count < 0) throw new Error(`Invalid people count for ${entry.site_id}`);
      // The building photo is the site's own drone render; contextual photos of other incidents never qualify.
      const photo = assets.find(a => a.site_id === entry.site_id && a.path.startsWith("assets/drone/") && a.usable_for_verification && !a.placeholder && a.file_status === "available");
      storage.run(`INSERT INTO scenario_buildings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(scenario_id, site_id) DO UPDATE SET pack_scenario_id=excluded.pack_scenario_id, worksite_key=excluded.worksite_key,
          ground_truth_id=excluded.ground_truth_id, display_name=excluded.display_name, latitude=excluded.latitude, longitude=excluded.longitude,
          people_count=excluded.people_count, people_trapped=excluded.people_trapped, structural_state=excluded.structural_state,
          access_state=excluded.access_state, drone_verifiable=excluded.drone_verifiable, photo_asset_id=excluded.photo_asset_id,
          photo_path=excluded.photo_path, photo_caption=excluded.photo_caption, updated_at=excluded.updated_at`,
        SCENARIO, truth.scenario_id, entry.site_id, entry.worksite_key, entry.ground_truth_id, site.display_name,
        site.site.centroid.latitude, site.site.centroid.longitude, count, count > 0 ? 1 : 0, entry.structural_state, entry.access_state,
        entry.drone_verifiable ? 1 : 0, photo?.asset_id ?? null, photo?.path ?? null, photo?.caption ?? null, now);
    }
  });
}

export function scenarioBuildings(storage: Storage, scenarioId: string | undefined): BuildingRow[] {
  const key = scenarioId === "caraballeda-v0" ? SCENARIO : scenarioId ?? SCENARIO;
  return storage.all<BuildingRow>("SELECT * FROM scenario_buildings WHERE scenario_id=? ORDER BY worksite_key", key);
}

export function scenarioBuilding(storage: Storage, scenarioId: string | undefined, siteId: string): BuildingRow | undefined {
  return scenarioBuildings(storage, scenarioId).find(b => b.site_id === siteId);
}

/** True once a drone report in this session found people at the building. Green/unknown scans never reveal. */
export function scanFoundPeople(storage: Storage, session: string, siteId: string) {
  return storage.all<{ payload_json: string }>(
    "SELECT payload_json FROM drone_reports WHERE session_id=? AND site_id=?", session, siteId)
    .some(row => {
      const type = (JSON.parse(row.payload_json) as { victim_indicator?: { type?: string } }).victim_indicator?.type;
      return !!type && !["none", "unknown"].includes(type);
    });
}

/** Photo for a red building only; the row's photo is hidden until this session's scan finds people. */
export function buildingPhoto(storage: Storage, session: string, scenarioId: string | undefined, siteId: string) {
  const row = scenarioBuilding(storage, scenarioId, siteId);
  if (!row?.photo_path || !row.photo_asset_id || !scanFoundPeople(storage, session, siteId)) return;
  return mediaFile({ asset_id: row.photo_asset_id, site_id: siteId, path: row.photo_path, file_status: "available", placeholder: false });
}

/** Developer read model over the oracle: counts are simulated scenario inputs, not verified
 *  casualties, and no primary demo surface reads them as operational truth. */
export function publicBuilding(row: BuildingRow, session: string, revealed: boolean) {
  return { site_id: row.site_id, worksite_key: row.worksite_key, display_name: row.display_name,
    position: { latitude: row.latitude, longitude: row.longitude }, people_count: row.people_count, people_trapped: row.people_trapped === 1,
    structural_state: row.structural_state, access_state: row.access_state, simulated: true,
    photo: row.photo_asset_id ? { asset_id: row.photo_asset_id, caption: row.photo_caption, revealed,
      image_url: revealed ? `/api/demo-sessions/${encodeURIComponent(session)}/search/buildings/${encodeURIComponent(row.site_id)}/photo` : null } : null };
}
