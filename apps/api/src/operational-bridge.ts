import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Storage } from "./storage.js";

const SCHEMA_VERSION = "2.1" as const;
const CARABALLEDA_START = "2026-09-19T14:00:00Z";

export interface BeliefSnapshot {
  schema_version: "2.1";
  session_id: string;
  scenario_id: string;
  state_version: string;
  scenario_time: string | null;
  updated_at: string | null;
  incidents: unknown[];
  worksites: unknown[];
  unresolved_observations: unknown[];
}

interface BridgeResult {
  record: Record<string, unknown>;
  snapshot: Omit<BeliefSnapshot, "session_id" | "scenario_id">;
  error?: string;
}

export function setupBeliefState(storage: Storage): void {
  storage.db.exec(`
    CREATE TABLE IF NOT EXISTS operational_belief_sessions (
      session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
      revision INTEGER NOT NULL,
      record_json TEXT NOT NULL,
      snapshot_json TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
  `);
}

export function emptyBeliefSnapshot(
  sessionId: string,
  scenarioId: string,
): BeliefSnapshot {
  const scenarioTime = caraballedaScenario(scenarioId)
    ? CARABALLEDA_START
    : new Date().toISOString();
  return {
    schema_version: SCHEMA_VERSION,
    session_id: sessionId,
    scenario_id: scenarioId,
    state_version: "0",
    scenario_time: scenarioTime,
    updated_at: scenarioTime,
    incidents: [],
    worksites: [],
    unresolved_observations: [],
  };
}

export function readBeliefSnapshot(
  storage: Storage,
  sessionId: string,
  scenarioId: string,
): BeliefSnapshot {
  const row = storage.one<{ snapshot_json: string }>(
    "SELECT snapshot_json FROM operational_belief_sessions WHERE session_id = ?",
    sessionId,
  );
  if (!row) return emptyBeliefSnapshot(sessionId, scenarioId);
  const snapshot = JSON.parse(row.snapshot_json) as BeliefSnapshot;
  return {
    ...emptyBeliefSnapshot(sessionId, scenarioId),
    ...snapshot,
    schema_version: SCHEMA_VERSION,
    session_id: sessionId,
    scenario_id: scenarioId,
  };
}

export function resetOperationalState(
  storage: Storage,
  sessionId: string,
): BeliefSnapshot {
  const scenarioId = storage.one<{ scenario_id: string }>(
    "SELECT scenario_id FROM sessions WHERE id = ?",
    sessionId,
  )?.scenario_id;
  storage.run(
    "DELETE FROM operational_belief_sessions WHERE session_id = ?",
    sessionId,
  );
  return emptyBeliefSnapshot(sessionId, scenarioId ?? "");
}

export async function applySessionObservations(
  storage: Storage,
  sessionId: string,
  scenarioId: string,
  observations: Record<string, unknown>[],
): Promise<BeliefSnapshot> {
  const prepared = observations.map((observation) =>
    prepareObservation(observation),
  );
  if (!prepared.length) return readBeliefSnapshot(storage, sessionId, scenarioId);
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const row = storage.one<{ revision: number; record_json: string }>(
      "SELECT revision, record_json FROM operational_belief_sessions WHERE session_id = ?",
      sessionId,
    );
    const result = await invokeBridge({
      command: "apply_many",
      scenario_id: scenarioId,
      record: row ? JSON.parse(row.record_json) : null,
      observations: prepared,
    });
    const now = new Date().toISOString();
    const snapshot = attachSession(result.snapshot, sessionId, scenarioId);
    const saved = storage.transaction(() => {
      const current = storage.one<{ revision: number }>(
        "SELECT revision FROM operational_belief_sessions WHERE session_id = ?",
        sessionId,
      );
      if (current?.revision !== row?.revision) return false;
      storage.run(
        `INSERT INTO operational_belief_sessions
         (session_id, revision, record_json, snapshot_json, updated_at)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(session_id) DO UPDATE SET
           revision = excluded.revision,
           record_json = excluded.record_json,
           snapshot_json = excluded.snapshot_json,
           updated_at = excluded.updated_at`,
        sessionId,
        (row?.revision ?? 0) + 1,
        JSON.stringify(result.record),
        JSON.stringify(snapshot),
        now,
      );
      return true;
    });
    if (saved) return snapshot;
  }
  throw Object.assign(new Error("Operational state changed concurrently; retry"), {
    statusCode: 409,
  });
}

export function prepareObservation(
  observation: Record<string, unknown>,
  extras: { author?: string | null } = {},
): Record<string, unknown> {
  const item = structuredClone(observation);
  const location =
    item.location && typeof item.location === "object" && !Array.isArray(item.location)
      ? { ...(item.location as Record<string, unknown>) }
      : {};
  location.resolved_site_id = null;
  item.location = location;
  const metadata =
    item.metadata && typeof item.metadata === "object" && !Array.isArray(item.metadata)
      ? { ...(item.metadata as Record<string, unknown>) }
      : {};
  delete metadata.trusted_site_id;
  delete metadata.canonical_site_id;
  delete metadata.intended_referent;
  if (extras.author) {
    const ingestion =
      metadata.ingestion &&
      typeof metadata.ingestion === "object" &&
      !Array.isArray(metadata.ingestion)
        ? { ...(metadata.ingestion as Record<string, unknown>) }
        : {};
    const sourceMetadata =
      ingestion.source_metadata &&
      typeof ingestion.source_metadata === "object" &&
      !Array.isArray(ingestion.source_metadata)
        ? { ...(ingestion.source_metadata as Record<string, unknown>) }
        : {};
    if (!sourceMetadata.author_id) sourceMetadata.author_id = extras.author;
    ingestion.source_metadata = sourceMetadata;
    metadata.ingestion = ingestion;
  }
  item.metadata = metadata;
  return item;
}

function attachSession(
  snapshot: Omit<BeliefSnapshot, "session_id" | "scenario_id">,
  sessionId: string,
  scenarioId: string,
): BeliefSnapshot {
  return {
    schema_version: SCHEMA_VERSION,
    session_id: sessionId,
    scenario_id: scenarioId,
    state_version: String(snapshot.state_version ?? "0"),
    scenario_time: snapshot.scenario_time ?? null,
    updated_at: snapshot.updated_at ?? null,
    incidents: Array.isArray(snapshot.incidents) ? snapshot.incidents : [],
    worksites: Array.isArray(snapshot.worksites) ? snapshot.worksites : [],
    unresolved_observations: Array.isArray(snapshot.unresolved_observations)
      ? snapshot.unresolved_observations
      : [],
  };
}

function invokeBridge(input: unknown): Promise<BridgeResult> {
  return new Promise((resolve, reject) => {
    const python =
      process.env.PYTHON_BIN ||
      (existsSync(new URL("../../../.venv/bin/python", import.meta.url))
        ? fileURLToPath(new URL("../../../.venv/bin/python", import.meta.url))
        : "python3");
    const child = execFile(
      python,
      ["-m", "operational.runtime_bridge"],
      {
        cwd: fileURLToPath(new URL("../../../", import.meta.url)),
        timeout: 15_000,
        maxBuffer: 16 * 1024 * 1024,
      },
      (error, stdout) => {
        if (error) {
          return reject(
            new Error(
              "Operational runtime failed. Check PYTHON_BIN and Python dependencies.",
            ),
          );
        }
        try {
          const value = JSON.parse(stdout) as BridgeResult;
          if (value.error || !value.record || !value.snapshot) {
            return reject(
              Object.assign(new Error(value.error || "Invalid operational result"), {
                statusCode: 400,
              }),
            );
          }
          resolve(value);
        } catch (parseError) {
          reject(parseError);
        }
      },
    );
    child.stdin?.on("error", () => {
      /* execFile callback handles process failure */
    });
    child.stdin?.end(JSON.stringify(input));
  });
}

function caraballedaScenario(scenarioId: string): boolean {
  return scenarioId === "caraballeda" || scenarioId === "caraballeda-v0";
}
