import type { FastifyInstance, FastifyRequest, FastifyReply } from "fastify";
import { getOperationalSnapshot } from "./operational-snapshot.js";
import { applySessionRoadState } from "./operational-bridge.js";
import { NdjsonPythonWorker } from "./python-worker.js";
import type { Storage } from "./storage.js";

type Authorize = (
  request: FastifyRequest,
  reply: FastifyReply,
) => { id: string; session_id: string } | undefined;

type SaveRecord = {
  initialized?: boolean;
  waiting?: boolean;
  uninitialized?: boolean;
  last_wall_at: string;
  view?: Record<string, unknown>;
  execution?: Record<string, unknown> | null;
  [key: string]: unknown;
};

function waitingView(sessionId: string, scenarioId: string) {
  return {
    initialized: false,
    waiting: true,
    session_id: sessionId,
    scenario_id: scenarioId,
    state_version: "0",
    last_consumed_operational_state_version: null,
    worksites: [],
    resources: [],
    assignments: [],
    routes: [],
    blocked_edge_ids: [],
    limited_edge_ids: [],
    proposal: null,
    trace: [],
    sync_action: "uninitialized",
  };
}

let worker: NdjsonPythonWorker | undefined;

function saveWorker() {
  if (!worker) worker = new NdjsonPythonWorker("saveplanner.live_bridge", ["--ndjson"], 45_000);
  return worker;
}

export function stopSavePlanner(): void {
  worker?.stop();
  worker = undefined;
}

async function bridge(input: unknown): Promise<SaveRecord> {
  const value = await saveWorker().request(input);
  if (value.error) {
    throw Object.assign(new Error(String(value.error)), { statusCode: 400 });
  }
  const record = (value.record ?? value) as SaveRecord;
  if (!record || typeof record !== "object") {
    throw Object.assign(new Error("Invalid Save result"), { statusCode: 400 });
  }
  return record;
}

export function registerSavePlanner(
  app: FastifyInstance,
  storage: Storage,
  authorize: Authorize,
) {
  storage.db.exec(`CREATE TABLE IF NOT EXISTS save_planner_sessions (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    record_json TEXT NOT NULL)`);

  async function execute(
    sessionId: string,
    command: string,
    body: Record<string, unknown> = {},
  ) {
    const scenario = storage.one<{ scenario_id: string }>(
      "SELECT scenario_id FROM sessions WHERE id=?",
      sessionId,
    );
    const scenarioId = scenario?.scenario_id ?? "";
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const operationalSnapshot = getOperationalSnapshot(storage, sessionId);
      const row = storage.one<{ revision: number; record_json: string }>(
        "SELECT revision, record_json FROM save_planner_sessions WHERE session_id = ?",
        sessionId,
      );
      if (!row && !operationalSnapshot) {
        return waitingView(sessionId, scenarioId);
      }
      const record = await bridge({
        command,
        scenario_id: scenarioId,
        session_id: sessionId,
        operational_snapshot: operationalSnapshot
          ? {
              ...operationalSnapshot,
              scenario_id: scenarioId,
            }
          : null,
        record: row ? JSON.parse(row.record_json) : null,
        approve: body.approve,
        proposal_id: body.proposal_id,
        simulation_speed: body.simulation_speed,
      });
      if (record.uninitialized || record.waiting) {
        return {
          ...waitingView(sessionId, scenarioId),
          ...(record.view || {}),
          server_now: record.last_wall_at,
        };
      }
      const saved = storage.transaction(() => {
        const current = storage.one<{ revision: number }>(
          "SELECT revision FROM save_planner_sessions WHERE session_id = ?",
          sessionId,
        );
        if (current?.revision !== row?.revision) return false;
        storage.run(
          `INSERT INTO save_planner_sessions(session_id, revision, record_json)
           VALUES (?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET
             revision=excluded.revision, record_json=excluded.record_json`,
          sessionId,
          (row?.revision ?? 0) + 1,
          JSON.stringify(record),
        );
        return true;
      });
      if (saved) {
        return {
          initialized: true,
          waiting: false,
          session_id: sessionId,
          scenario_id: scenarioId,
          server_now: record.last_wall_at,
          execution_revision: record.execution_revision,
          ...(record.view || {}),
          sync_action: record.sync_action,
          last_consumed_operational_state_version:
            record.last_consumed_operational_state_version,
          operational_state_version:
            record.last_consumed_operational_state_version,
        };
      }
    }
    throw Object.assign(new Error("Save changed concurrently; retry"), {
      statusCode: 409,
    });
  }

  function guard(
    request: FastifyRequest,
    reply: FastifyReply,
  ): { id: string; session_id: string } | undefined {
    const actor = authorize(request, reply);
    if (!actor) return;
    const { id } = request.params as { id: string };
    if (id !== actor.session_id) {
      reply.code(403).send({
        error: { code: "FORBIDDEN", message: "Wrong session" },
      });
      return;
    }
    return actor;
  }

  app.get("/api/demo-sessions/:id/save", async (request, reply) => {
    const actor = guard(request, reply);
    if (!actor) return;
    try {
      return await execute(actor.session_id, "status");
    } catch (error) {
      const failure = error as Error & { statusCode?: number };
      return reply.code(failure.statusCode ?? 503).send({
        error: { code: "SAVE_ERROR", message: failure.message },
      });
    }
  });

  app.post("/api/demo-sessions/:id/save/tick", async (request, reply) => {
    const actor = guard(request, reply);
    if (!actor) return;
    const body = (request.body as Record<string, unknown> | undefined) ?? {};
    try {
      return await execute(actor.session_id, "tick", body);
    } catch (error) {
      const failure = error as Error & { statusCode?: number };
      return reply.code(failure.statusCode ?? 503).send({
        error: { code: "SAVE_ERROR", message: failure.message },
      });
    }
  });

  app.post(
    "/api/demo-sessions/:id/save/proposals/:proposalId/decision",
    async (request, reply) => {
      const actor = guard(request, reply);
      if (!actor) return;
      const { proposalId } = request.params as { proposalId: string };
      const body = (request.body as Record<string, unknown> | undefined) ?? {};
      const decision = body.decision;
      const approve =
        body.approve === true ||
        decision === "approve" ||
        decision === true;
      if (
        body.approve === undefined &&
        decision !== "approve" &&
        decision !== "reject" &&
        decision !== true &&
        decision !== false
      ) {
        return reply.code(400).send({
          error: {
            code: "INVALID_INPUT",
            message: "Provide approve or decision",
          },
        });
      }
      try {
        return await execute(actor.session_id, "decide", {
          proposal_id: proposalId,
          approve,
        });
      } catch (error) {
        const failure = error as Error & { statusCode?: number };
        return reply.code(failure.statusCode ?? 503).send({
          error: { code: "SAVE_ERROR", message: failure.message },
        });
      }
    },
  );

  app.post("/api/demo-sessions/:id/roads/:roadId", async (request, reply) => {
    const actor = guard(request, reply);
    if (!actor) return;
    const { roadId } = request.params as { roadId: string };
    const body = (request.body as Record<string, unknown> | undefined) ?? {};
    const status = typeof body.status === "string" ? body.status : "";
    const scenarioId =
      storage.one<{ scenario_id: string }>(
        "SELECT scenario_id FROM sessions WHERE id=?",
        actor.session_id,
      )?.scenario_id ?? "";
    try {
      const belief = await applySessionRoadState(
        storage,
        actor.session_id,
        scenarioId,
        decodeURIComponent(roadId),
        status,
        {
          travel_cost_multiplier:
            typeof body.travel_cost_multiplier === "number"
              ? body.travel_cost_multiplier
              : undefined,
          reason: typeof body.reason === "string" ? body.reason : undefined,
        },
      );
      const save = await execute(actor.session_id, "tick");
      return { belief, save };
    } catch (error) {
      const failure = error as Error & { statusCode?: number };
      return reply.code(failure.statusCode ?? 503).send({
        error: {
          code: failure.statusCode === 400 ? "INVALID_INPUT" : "SAVE_ERROR",
          message: failure.message,
        },
      });
    }
  });
}
