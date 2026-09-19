import { droneReports } from "./drone-reports.js";
import { existsSync } from "node:fs";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import type {
  ExternalReport,
  IntelligenceReport,
  IntelligenceSnapshot,
} from "../../../contracts/intelligence.js";
import type { StructuredObservation } from "../../../contracts/generated/observation.js";
import { Storage } from "./storage.js";
import { matchSite, geography } from "./geography.js";

export function intelligenceSnapshot(
  storage: Storage,
  session: string,
): IntelligenceSnapshot {
  const scenario = storage.one<{ scenario_id: string }>(
    "SELECT scenario_id FROM sessions WHERE id = ?",
    session,
  )!.scenario_id;
  const radio = storage.all<{
    id: string;
    capture_mode: string;
    status: string;
    created_at: string;
    responder_id: string;
    payload_json: string | null;
    model: string | null;
  }>(
    `SELECT t.id, t.capture_mode, t.status, t.created_at, p.responder_id, r.payload_json, j.model
     FROM transmissions t JOIN participants p ON p.id = t.participant_id
     LEFT JOIN raw_inputs r ON r.transmission_id = t.id LEFT JOIN extraction_jobs j ON j.transmission_id = t.id
     WHERE t.session_id = ? AND t.status != 'cancelled' ORDER BY t.created_at DESC`,
    session,
  );
  const reports: IntelligenceReport[] = radio.map((row) => ({
    id: row.id,
    source: "radio",
    author: row.responder_id,
    timestamp: row.created_at,
    text: row.payload_json
      ? JSON.parse(row.payload_json).transcript
      : "Transmission in progress…",
    mode: row.capture_mode,
    state: row.status,
    model: row.model,
    sourceUrl: null,
    locations: [],
    observations: storage
      .all<{ payload_json: string }>(
        "SELECT payload_json FROM observations WHERE transmission_id = ?",
        row.id,
      )
      .map((item) => JSON.parse(item.payload_json) as StructuredObservation),
  }));
  for (const row of storage.all<{ payload_json: string }>(
    "SELECT payload_json FROM external_reports WHERE session_id = ?",
    session,
  )) {
    const report = JSON.parse(row.payload_json) as ExternalReport;
    reports.push({
      ...report,
      state: report.observations.length ? "extracted" : "filtered",
      locations: [],
    });
  }
  for (const row of droneReports(storage, session)) {
    const report = row.report;
    const v = report.victim_indicator;
    const count = v?.count_min == null ? "Count unknown" : v.count_min === v.count_max
      ? `Reported count: ${v.count_min}` : `Reported count: ${v.count_min}–${v.count_max ?? "unknown"}`;
    const site = geography().byId.get(row.site_id);
    reports.push({ id: row.report_id, source: "drone", author: row.resource_id, timestamp: row.published_at,
      text: `Drone scan: ${(v?.type ?? "unknown").replaceAll("_", " ")}. ${count}. Structure: ${(report.structural_damage ?? "unknown").replaceAll("_", " ")}. Access: ${report.access_status ?? "unknown"}. Simulated sensor finding; not independently verified.`,
      mode: "simulated_scan", state: "extracted", observations: row.observations,
      locations: site ? [{ site, method: "canonical_site" }] : [], sourceUrl: null, model: "deterministic_drone_json" });
  }
  const placements = storage.all<{
    report_id: string;
    site_id: string;
    located_at: string;
  }>("SELECT * FROM report_locations WHERE session_id = ?", session);
  for (const report of reports) {
    if (report.source === "drone") continue;
    const placement = placements.find((item) => item.report_id === report.id);
    if (placement) {
      const site = geography().byId.get(placement.site_id);
      if (site)
        report.locations = [
          { site, method: "manual", locatedAt: placement.located_at },
        ];
    } else if (scenario === "caraballeda") {
      const seen = new Set<string>();
      for (const observation of report.observations) {
        const site = matchSite(observation.location.raw_text);
        if (site && !seen.has(site.id)) {
          seen.add(site.id);
          report.locations.push({ site, method: "exact_name" });
        }
      }
    }
  }
  return {
    reports: reports.sort((a, b) => b.timestamp.localeCompare(a.timestamp)),
    scenario,
    updatedAt: new Date().toISOString(),
  };
}

export function importExternal(
  source: string,
  provider: string,
  knownIds: string[],
): Promise<ExternalReport[]> {
  return new Promise((resolve, reject) => {
    const child = execFile(
      process.env.PYTHON_BIN ||
        (existsSync(new URL("../../../.venv/bin/python", import.meta.url))
          ? fileURLToPath(new URL("../../../.venv/bin/python", import.meta.url))
          : "python3"),
      ["-m", "ingestion.dashboard", "--source", source, "--provider", provider],
      {
        cwd: fileURLToPath(new URL("../../../", import.meta.url)),
        timeout: 180_000,
        maxBuffer: 4 * 1024 * 1024,
      },
      (error, stdout) => {
        if (error)
          return reject(
            new Error(
              "External ingestion failed. Check Python dependencies and source/provider configuration.",
            ),
          );
        try {
          const value = JSON.parse(stdout) as { reports: ExternalReport[] };
          if (!Array.isArray(value.reports))
            throw new Error("Invalid ingestion output");
          resolve(value.reports);
        } catch {
          reject(new Error("External ingestion returned invalid data."));
        }
      },
    );
    child.stdin?.end(JSON.stringify({ known_ids: knownIds }));
  });
}
