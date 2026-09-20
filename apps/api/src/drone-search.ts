import { setupRoadScans, persistRoadScans, roadScanSnapshot } from "./drone-road-scans.js";
import { intelligenceSnapshot } from "./intelligence.js";
import { scanIntake } from "./drone-intake.js";
import { execFile } from "node:child_process";
import { createReadStream } from "node:fs";
import { storedInspectionInputs, setupDroneReports, persistDroneReports, droneReports, findRevealedMedia } from "./drone-reports.js";
import { setupScenarioBuildings, scenarioBuildings, buildingPhoto, publicBuilding, scanFoundPeople } from "./scenario-buildings.js";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";
import { getOperationalSnapshot } from "./operational-snapshot.js";
import {
  applySessionObservations,
  applySessionRoadStates,
  readBeliefSnapshot,
  type BeliefSnapshot,
  type RoadStateUpdate,
} from "./operational-bridge.js";
import type { Storage } from "./storage.js";

type Authorize = (request: FastifyRequest, reply: FastifyReply) => { id: string; session_id: string } | undefined;
type RecordData = { proposals: unknown[]; missions: unknown[]; last_wall_at: string;
  operational_observations?: Record<string, unknown>[]; road_state_updates?: RoadStateUpdate[];
  [key: string]: unknown };

function plannerRecord(record: RecordData) {
  const { operational_observations: _ignored, operational_runtime: _runtime, operational_feedback: _feedback,
    road_state_updates: _roads, ...visible } = record;
  void _ignored; void _runtime; void _feedback; void _roads;
  return visible;
}

function compactFeedback(
  snapshot: BeliefSnapshot,
  observations: Record<string, unknown>[],
  roadStates: RoadStateUpdate[],
  previousVersion: string,
) {
  return {
    observations,
    road_state_updates: roadStates,
    state_version: snapshot.state_version,
    previous_state_version: previousVersion,
    changed: snapshot.state_version !== previousVersion,
    seam: "applySessionObservations",
    road_seam: "applySessionRoadState",
  };
}

function bridge(input: unknown): Promise<RecordData> {
  return new Promise((resolve, reject) => {
    const python = process.env.PYTHON_BIN || (existsSync(new URL("../../../.venv/bin/python", import.meta.url))
      ? fileURLToPath(new URL("../../../.venv/bin/python", import.meta.url)) : "python3");
    const child = execFile(python, ["-X", "utf8", "-m", "search_planner.approval_bridge"], {
      cwd: fileURLToPath(new URL("../../../", import.meta.url)), timeout: 10_000, maxBuffer: 64 * 1024 * 1024,
    }, (error, stdout) => {
      if (error) return reject(new Error("Search runtime failed. Check PYTHON_BIN and Python dependencies."));
      try {
        const value = JSON.parse(stdout) as { record?: RecordData; error?: string };
        if (!value.record) return reject(Object.assign(new Error(value.error || "Invalid Search result"), { statusCode: 400 }));
        resolve(value.record);
      } catch (error) { reject(error); }
    });
    child.stdin?.on("error", () => { /* execFile callback handles process failure */ });
    child.stdin?.end(JSON.stringify(input));
  });
}

export function registerDroneSearch(app: FastifyInstance, storage: Storage, authorize: Authorize) {
  setupDroneReports(storage);
  setupScenarioBuildings(storage);
  setupRoadScans(storage);
  storage.db.exec(`CREATE TABLE IF NOT EXISTS drone_search_sessions (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL, record_json TEXT NOT NULL)`);
  // Optimistic revision checks serialize across requests AND server processes. A retry
  // resamples server time in Python, so queueing never extends the approval deadline.
  async function execute(sessionId: string, command: string, body: Record<string, unknown>, operatorId: string) {
    const scenario = storage.one<{ scenario_id: string }>("SELECT scenario_id FROM sessions WHERE id=?", sessionId);
    for (let attempt = 0; attempt < 5; attempt++) {
      const operationalSnapshot = getOperationalSnapshot(storage, sessionId);
      const row = storage.one<{ revision: number; record_json: string }>(
        "SELECT revision, record_json FROM drone_search_sessions WHERE session_id = ?", sessionId);
      if (!row && command === "tick" && !operationalSnapshot)
        return { initialized: false, server_now: new Date().toISOString(), proposals: [], missions: [] };
      const record = await bridge({
        command,
        scenario_id: scenario?.scenario_id,
        session_id: sessionId,
        sqlite_path: storage.path !== ":memory:" ? storage.path : undefined,
        operational_snapshot: operationalSnapshot,
        stored_inspections: storedInspectionInputs(storage, sessionId),
        scenario_buildings: scenarioBuildings(storage, scenario?.scenario_id),
        intake: scanIntake(intelligenceSnapshot(storage, sessionId).reports),
        record: row ? JSON.parse(row.record_json) : null,
        state: body.state, report: body.report, simulation_speed: body.simulation_speed,
        decision: command === "decide" ? { proposal_id: body.proposal_id, decision: body.decision, operator_id: operatorId } : undefined });
      if ((record as { uninitialized?: boolean }).uninitialized)
        return { initialized: false, server_now: record.last_wall_at, proposals: [], missions: [] };
      const observations = Array.isArray(record.operational_observations) ? record.operational_observations : [];
      const roadStates = Array.isArray(record.road_state_updates) ? record.road_state_updates : [];
      const stored = plannerRecord(record);
      const saved = storage.transaction(() => {
        const current = storage.one<{ revision: number }>("SELECT revision FROM drone_search_sessions WHERE session_id = ?", sessionId);
        if (current?.revision !== row?.revision) return false;
        storage.run(`INSERT INTO drone_search_sessions(session_id, revision, record_json) VALUES (?, ?, ?)
          ON CONFLICT(session_id) DO UPDATE SET revision=excluded.revision, record_json=excluded.record_json`,
          sessionId, (row?.revision ?? 0) + 1, JSON.stringify(stored));
        persistDroneReports(storage, sessionId, stored);
        persistRoadScans(storage, sessionId, record);
        return true;
      });
      if (saved) {
        const scenarioId = scenario?.scenario_id ?? "";
        const before = readBeliefSnapshot(storage, sessionId, scenarioId);
        // Building findings and road detections both become canonical here; Search, Save
        // and the map then read the one belief state, never these audit rows.
        let snapshot = observations.length
          ? await applySessionObservations(storage, sessionId, scenarioId, observations)
          : before;
        if (roadStates.length)
          snapshot = await applySessionRoadStates(storage, sessionId, scenarioId, roadStates);
        return {
          initialized: true,
          server_now: record.last_wall_at,
          ...stored,
          operational_feedback: compactFeedback(snapshot, observations, roadStates, before.state_version),
        };
      }
    }
    throw Object.assign(new Error("Search changed concurrently; retry"), { statusCode: 409 });
  }
  app.get("/api/demo-sessions/:id/search/roads", async (request, reply) => {
    const actor = authorize(request, reply);
    if (!actor) return;
    const { id } = request.params as { id: string };
    if (id !== actor.session_id) return reply.code(403).send({ error: { code: "FORBIDDEN", message: "Wrong session" } });
    const scenarioId = storage.one<{ scenario_id: string }>("SELECT scenario_id FROM sessions WHERE id=?", id)?.scenario_id ?? "";
    return roadScanSnapshot(storage, id, readBeliefSnapshot(storage, id, scenarioId));
  });
  app.get("/api/demo-sessions/:id/search/buildings", async (request, reply) => {
    const actor = authorize(request, reply);
    if (!actor) return;
    const { id } = request.params as { id: string };
    if (id !== actor.session_id) return reply.code(403).send({ error: { code: "FORBIDDEN", message: "Wrong session" } });
    const scenario = storage.one<{ scenario_id: string }>("SELECT scenario_id FROM sessions WHERE id=?", id);
    return { buildings: scenarioBuildings(storage, scenario?.scenario_id).map(row => publicBuilding(row, id, scanFoundPeople(storage, id, row.site_id))) };
  });
  app.get("/api/demo-sessions/:id/search/buildings/:siteId/photo", async (request, reply) => {
    const actor = authorize(request, reply);
    if (!actor) return;
    const { id, siteId } = request.params as { id: string; siteId: string };
    if (id !== actor.session_id) return reply.code(403).send({ error: { code: "FORBIDDEN", message: "Wrong session" } });
    const scenario = storage.one<{ scenario_id: string }>("SELECT scenario_id FROM sessions WHERE id=?", id);
    const media = buildingPhoto(storage, id, scenario?.scenario_id, siteId);
    if (!media) return reply.code(404).send({ error: { code: "MEDIA_UNAVAILABLE", message: "Photo is shown only after a scan finds people at this building" } });
    return reply.header("Cache-Control", "private, no-store").header("X-Content-Type-Options", "nosniff")
      .type(media.mime).send(createReadStream(media.path));
  });
  app.get("/api/demo-sessions/:id/search/reports", async (request, reply) => {
    const actor = authorize(request, reply);
    if (!actor) return;
    const { id } = request.params as { id: string };
    if (id !== actor.session_id) return reply.code(403).send({ error: { code: "FORBIDDEN", message: "Wrong session" } });
    const scenarioId = storage.one<{ scenario_id: string }>("SELECT scenario_id FROM sessions WHERE id=?", id)?.scenario_id ?? "";
    const snapshot = readBeliefSnapshot(storage, id, scenarioId);
    return { reports: droneReports(storage, id), operational_feedback: {
      state_version: snapshot.state_version, observations: [], road_state_updates: [],
      seam: "applySessionObservations", road_seam: "applySessionRoadState",
    } };
  });
  app.get("/api/demo-sessions/:id/search/assets/:assetId", async (request, reply) => {
    const actor = authorize(request, reply);
    if (!actor) return;
    const { id, assetId } = request.params as { id: string; assetId: string };
    if (id !== actor.session_id) return reply.code(403).send({ error: { code: "FORBIDDEN", message: "Wrong session" } });
    const media = findRevealedMedia(storage, id, assetId);
    if (!media) return reply.code(404).send({ error: { code: "MEDIA_UNAVAILABLE", message: "Image is hidden, missing, or a placeholder" } });
    return reply.header("Cache-Control", "private, no-store").header("X-Content-Type-Options", "nosniff")
      .type(media.mime).send(createReadStream(media.path));
  });
  app.post("/api/demo-sessions/:id/search/:command", { bodyLimit: 32 * 1024 * 1024 }, async (request, reply) => {
    const actor = authorize(request, reply);
    if (!actor) return;
    const { id, command } = request.params as { id: string; command: string };
    if (id !== actor.session_id) return reply.code(403).send({ error: { code: "FORBIDDEN", message: "Wrong session" } });
    if (!["initialize", "update", "tick", "decide", "report", "prepare", "dispatch", "speed"].includes(command))
      return reply.code(404).send({ error: { code: "NOT_FOUND", message: "Unknown Search command" } });
    const body = request.body as Record<string, unknown> | undefined;
    if (!body || Array.isArray(body) || typeof body !== "object")
      return reply.code(400).send({ error: { code: "INVALID_INPUT", message: "JSON object required" } });
    try { return await execute(id, command, body, actor.id); }
    catch (error) {
      const e = error as Error & { statusCode?: number };
      return reply.code(e.statusCode ?? 503).send({ error: { code: "SEARCH_ERROR", message: e.message } });
    }
  });
}
