import { execFile } from "node:child_process";
import { createReadStream } from "node:fs";
import { setupDroneReports, persistDroneReports, droneReports, findRevealedMedia } from "./drone-reports.js";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";
import type { Storage } from "./storage.js";
import {
  applySessionObservations,
  readBeliefSnapshot,
  type BeliefSnapshot,
} from "./operational-bridge.js";

type Authorize = (request: FastifyRequest, reply: FastifyReply) => { id: string; session_id: string } | undefined;
type RecordData = { proposals: unknown[]; missions: unknown[]; last_wall_at: string;
  operational_observations?: Record<string, unknown>[]; [key: string]: unknown };

function plannerRecord(record: RecordData) {
  const { operational_observations: _ignored, operational_runtime: _runtime, operational_feedback: _feedback, ...visible } = record;
  void _ignored; void _runtime; void _feedback;
  return visible;
}

function compactFeedback(
  snapshot: BeliefSnapshot,
  observations: Record<string, unknown>[],
  previousVersion: string,
) {
  return {
    observations,
    state_version: snapshot.state_version,
    previous_state_version: previousVersion,
    changed: snapshot.state_version !== previousVersion,
    seam: "applySessionObservations",
  };
}

function bridge(input: unknown): Promise<RecordData> {
  return new Promise((resolve, reject) => {
    const python = process.env.PYTHON_BIN || (existsSync(new URL("../../../.venv/bin/python", import.meta.url))
      ? fileURLToPath(new URL("../../../.venv/bin/python", import.meta.url)) : "python3");
    const child = execFile(python, ["-m", "search_planner.approval_bridge"], {
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
  storage.db.exec(`CREATE TABLE IF NOT EXISTS drone_search_sessions (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL, record_json TEXT NOT NULL)`);
  // Optimistic revision checks serialize across requests AND server processes. A retry
  // resamples server time in Python, so queueing never extends the approval deadline.
  async function execute(sessionId: string, command: string, body: Record<string, unknown>, operatorId: string) {
    const scenarioRow = storage.one<{ scenario_id: string }>("SELECT scenario_id FROM sessions WHERE id=?", sessionId);
    for (let attempt = 0; attempt < 5; attempt++) {
      const row = storage.one<{ revision: number; record_json: string }>(
        "SELECT revision, record_json FROM drone_search_sessions WHERE session_id = ?", sessionId);
      if (!row && command === "tick") return { initialized: false, server_now: new Date().toISOString(), proposals: [], missions: [] };
      const record = await bridge({ command, scenario_id: scenarioRow?.scenario_id, record: row ? JSON.parse(row.record_json) : null,
        state: body.state, report: body.report,
        decision: command === "decide" ? { proposal_id: body.proposal_id, decision: body.decision, operator_id: operatorId } : undefined });
      const observations = Array.isArray(record.operational_observations) ? record.operational_observations : [];
      const stored = plannerRecord(record);
      const saved = storage.transaction(() => {
        const current = storage.one<{ revision: number }>("SELECT revision FROM drone_search_sessions WHERE session_id = ?", sessionId);
        if (current?.revision !== row?.revision) return false;
        storage.run(`INSERT INTO drone_search_sessions(session_id, revision, record_json) VALUES (?, ?, ?)
          ON CONFLICT(session_id) DO UPDATE SET revision=excluded.revision, record_json=excluded.record_json`,
          sessionId, (row?.revision ?? 0) + 1, JSON.stringify(stored));
        persistDroneReports(storage, sessionId, stored);
        return true;
      });
      if (saved) {
        const scenarioId = scenarioRow?.scenario_id ?? "";
        const before = readBeliefSnapshot(storage, sessionId, scenarioId);
        const snapshot = observations.length
          ? await applySessionObservations(storage, sessionId, scenarioId, observations)
          : before;
        return {
          initialized: true,
          server_now: record.last_wall_at,
          ...stored,
          operational_feedback: compactFeedback(snapshot, observations, before.state_version),
        };
      }
    }
    throw Object.assign(new Error("Search changed concurrently; retry"), { statusCode: 409 });
  }
  app.get("/api/demo-sessions/:id/search/reports", async (request, reply) => {
    const actor = authorize(request, reply);
    if (!actor) return;
    const { id } = request.params as { id: string };
    if (id !== actor.session_id) return reply.code(403).send({ error: { code: "FORBIDDEN", message: "Wrong session" } });
    const scenarioId = storage.one<{ scenario_id: string }>("SELECT scenario_id FROM sessions WHERE id=?", id)?.scenario_id ?? "";
    const snapshot = readBeliefSnapshot(storage, id, scenarioId);
    return { reports: droneReports(storage, id), operational_feedback: {
      state_version: snapshot.state_version, observations: [], seam: "applySessionObservations",
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
    if (!["initialize", "update", "tick", "decide", "report"].includes(command))
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
