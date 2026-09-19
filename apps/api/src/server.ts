import {
  createHash,
  createHmac,
  randomBytes,
  randomUUID,
  timingSafeEqual,
} from "node:crypto";
import Fastify, {
  type FastifyInstance,
  type FastifyReply,
  type FastifyRequest,
} from "fastify";
import cookie from "@fastify/cookie";
import cors from "@fastify/cors";
import type {
  AppConfig,
  CaptureMode,
  FinalizeBody,
  RawRadioInput,
} from "./types.js";
import { SCHEMA_VERSION } from "./types.js";
import { Storage } from "./storage.js";
import { ExtractionService } from "./extraction.js";
import { ExtractionWorker } from "./worker.js";

import { geography } from "./geography.js";
import { intelligenceSnapshot, importExternal } from "./intelligence.js";

const PRESENTER_COOKIE = "aftershock_presenter";
const PARTICIPANT_COOKIE = "aftershock_participant";
const REPLAY_TRANSCRIPT =
  "Command, partial collapse near Riverside Apartments. Multiple voices heard. East access may be blocked.";

interface Actor {
  id: string;
  session_id: string;
  responder_id: string;
  role: "presenter" | "participant";
  session_expires_at: string;
}

interface Transmission {
  id: string;
  session_id: string;
  participant_id: string;
  capture_mode: CaptureMode;
  channel: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  finalized_at: string | null;
  payload_hash: string | null;
  input_id: string | null;
  error_code: string | null;
}

export interface ApiRuntime {
  app: FastifyInstance;
  storage: Storage;
  worker: ExtractionWorker;
}

export async function buildApi(config: AppConfig): Promise<ApiRuntime> {
  const app = Fastify({
    logger: process.env.NODE_ENV !== "test",
    bodyLimit: config.maxRequestBytes,
    trustProxy: true,
  });
  const storage = new Storage(config.databasePath);
  const extraction = new ExtractionService(config);
  const worker = new ExtractionWorker(storage, extraction, config);

  await app.register(cookie);
  await app.register(cors, {
    origin: config.appOrigin,
    credentials: true,
    methods: ["GET", "POST", "DELETE", "OPTIONS"],
  });

  app.addHook("onRequest", async (request, reply) => {
    if (!["POST", "PUT", "PATCH", "DELETE"].includes(request.method)) return;
    const origin = request.headers.origin;
    if (
      (process.env.NODE_ENV === "production" && !origin) ||
      (origin && origin !== config.appOrigin)
    ) {
      return reply
        .code(403)
        .send(errorBody("ORIGIN_FORBIDDEN", "Request origin is not allowed"));
    }
  });

  app.setErrorHandler((error, _request, reply) => {
    const fastifyError = error as { validation?: unknown; code?: string };
    if (fastifyError.validation) {
      return reply
        .code(400)
        .send(errorBody("INVALID_REQUEST", "Request payload is invalid"));
    }
    if (fastifyError.code === "FST_ERR_CTP_BODY_TOO_LARGE") {
      return reply
        .code(413)
        .send(errorBody("PAYLOAD_TOO_LARGE", "Request payload exceeds limit"));
    }
    app.log.error({ err: error }, "request failed");
    return reply
      .code(500)
      .send(errorBody("INTERNAL_ERROR", "The request could not be completed"));
  });

  app.get("/health", async () => ({ ok: true }));

  app.get("/api/geography", async () => geography().info);
  app.get("/api/geography/features", async (_request, reply) => {
    reply.header("cache-control", "public, max-age=3600");
    return geography().geojson;
  });
  const imports = new Set<string>();
  app.get("/api/demo-sessions/:id/intelligence", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config);
    if (!actor) return;
    if (actor.session_id !== pathId(request))
      return reply
        .code(403)
        .send(errorBody("SESSION_FORBIDDEN", "Session is not accessible"));
    reply.header("cache-control", "no-store");
    return intelligenceSnapshot(storage, actor.session_id);
  });
  app.post("/api/demo-sessions/:id/ingest", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config, "presenter");
    if (!actor) return;
    if (actor.session_id !== pathId(request))
      return reply
        .code(403)
        .send(errorBody("SESSION_FORBIDDEN", "Session is not accessible"));
    const body = objectBody(request.body);
    const source = optionalString(body.source) ?? "replay";
    const provider = optionalString(body.provider) ?? "fixture";
    if (
      !["replay", "gdacs", "reliefweb"].includes(source) ||
      !["fixture", "openai"].includes(provider) ||
      (source !== "replay" && provider !== "openai")
    )
      return reply
        .code(400)
        .send(
          errorBody(
            "INVALID_SOURCE",
            "Select a supported source and extraction provider",
          ),
        );
    if (source === "replay" && !config.replayEnabled)
      return reply
        .code(404)
        .send(errorBody("REPLAY_DISABLED", "Replay is disabled"));
    if (imports.has(actor.session_id))
      return reply
        .code(409)
        .send(errorBody("INGESTION_BUSY", "An import is already running"));
    imports.add(actor.session_id);
    try {
      const known = storage
        .all<{ report_id: string }>(
          "SELECT report_id FROM external_reports WHERE session_id = ?",
          actor.session_id,
        )
        .map((row) => row.report_id);
      const reports = await importExternal(source, provider, known);
      if (
        !storage.one(
          "SELECT 1 FROM sessions WHERE id = ? AND expires_at > ?",
          actor.session_id,
          new Date().toISOString(),
        )
      )
        return reply
          .code(410)
          .send(errorBody("SESSION_EXPIRED", "Session ended during import"));
      let count = 0;
      storage.transaction(() => {
        for (const report of reports) {
          if (
            storage.one(
              "SELECT 1 FROM external_reports WHERE session_id = ? AND report_id = ?",
              actor.session_id,
              report.id,
            )
          )
            continue;
          storage.run(
            "INSERT INTO external_reports(session_id, report_id, payload_json) VALUES (?, ?, ?)",
            actor.session_id,
            report.id,
            JSON.stringify(report),
          );
          count++;
        }
      });
      return { imported: count, source, provider };
    } catch {
      return reply
        .code(503)
        .send(
          errorBody(
            "INGESTION_UNAVAILABLE",
            "Import failed. Check PYTHON_BIN, Python dependencies, and source/provider credentials.",
          ),
        );
    } finally {
      imports.delete(actor.session_id);
    }
  });
  app.post("/api/demo-sessions/:id/locate", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config, "presenter");
    if (!actor) return;
    if (actor.session_id !== pathId(request))
      return reply
        .code(403)
        .send(errorBody("SESSION_FORBIDDEN", "Session is not accessible"));
    const body = objectBody(request.body);
    const reportId = optionalString(body.report_id);
    const siteId = optionalString(body.site_id);
    const report = intelligenceSnapshot(storage, actor.session_id).reports.find(
      (item) => item.id === reportId,
    );
    if (
      !report ||
      !report.observations.length ||
      !siteId ||
      !geography().byId.has(siteId)
    )
      return reply
        .code(400)
        .send(
          errorBody(
            "INVALID_LOCATION",
            "Select an actionable report and a known map site",
          ),
        );
    storage.run(
      "INSERT INTO report_locations(session_id, report_id, site_id, located_at) VALUES (?, ?, ?, ?) ON CONFLICT(session_id, report_id) DO UPDATE SET site_id = excluded.site_id, located_at = excluded.located_at",
      actor.session_id,
      reportId!,
      siteId,
      new Date().toISOString(),
    );
    return { located: true };
  });

  app.post("/api/demo-sessions", async (request, reply) => {
    if (
      !secretEquals(request.headers["x-presenter-key"], config.presenterApiKey)
    ) {
      return reply
        .code(401)
        .send(
          errorBody("PRESENTER_AUTH_REQUIRED", "Presenter key is required"),
        );
    }
    const body = objectBody(request.body);
    const scenarioId =
      optionalString(body.scenario_id) ?? "simulated-riverside-apartments";
    const name = optionalString(body.name) ?? "Aftershock radio demo";
    const responders = stringArray(body.allowed_responder_ids);
    if (!responders.length || responders.length > 25) {
      return reply
        .code(400)
        .send(
          errorBody(
            "INVALID_RESPONDERS",
            "Provide 1 to 25 allowed responder IDs",
          ),
        );
    }
    const sessionId = randomUUID();
    const participantId = randomUUID();
    const joinCode = generateJoinCode();
    const presenterToken = opaqueToken();
    const now = new Date();
    const expiresAt = new Date(
      now.getTime() + config.sessionTtlHours * 3_600_000,
    ).toISOString();
    const joinExpiresAt = new Date(
      now.getTime() + config.joinCodeTtlMinutes * 60_000,
    ).toISOString();

    storage.transaction(() => {
      storage.run(
        `INSERT INTO sessions
         (id, scenario_id, name, join_code_hash, allowed_responders_json,
          presenter_auth_hash, created_at, expires_at, join_expires_at)
         VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        sessionId,
        scenarioId,
        name,
        authHash(joinCode, config),
        JSON.stringify(responders),
        authHash(presenterToken, config),
        now.toISOString(),
        expiresAt,
        joinExpiresAt,
      );
      storage.run(
        `INSERT INTO participants
         (id, session_id, responder_id, auth_hash, role, created_at, expires_at)
         VALUES(?, ?, ?, ?, 'presenter', ?, ?)`,
        participantId,
        sessionId,
        "presenter",
        authHash(presenterToken, config),
        now.toISOString(),
        expiresAt,
      );
    });
    setAuthCookie(reply, PRESENTER_COOKIE, presenterToken, expiresAt, config);
    return reply.code(201).send({
      schema_version: SCHEMA_VERSION,
      session_id: sessionId,
      scenario_id: scenarioId,
      name,
      join_code: joinCode,
      join_expires_at: joinExpiresAt,
      expires_at: expiresAt,
      allowed_responder_ids: responders,
    });
  });

  app.post("/api/demo-sessions/join", async (request, reply) => {
    const body = objectBody(request.body);
    const joinCode = requiredString(body.join_code)?.toUpperCase();
    const responderId = requiredString(body.responder_id);
    if (!joinCode || !responderId) {
      return reply
        .code(400)
        .send(
          errorBody("INVALID_JOIN", "Join code and responder ID are required"),
        );
    }
    const session = storage.one<{
      id: string;
      scenario_id: string;
      name: string;
      allowed_responders_json: string;
      expires_at: string;
      join_expires_at: string;
    }>(
      "SELECT * FROM sessions WHERE join_code_hash = ?",
      authHash(joinCode, config),
    );
    if (!session || Date.parse(session.join_expires_at) <= Date.now()) {
      return reply
        .code(401)
        .send(
          errorBody("JOIN_CODE_INVALID", "Join code is invalid or expired"),
        );
    }
    if (Date.parse(session.expires_at) <= Date.now()) {
      return reply
        .code(401)
        .send(errorBody("SESSION_EXPIRED", "Session has expired"));
    }
    const allowed = JSON.parse(session.allowed_responders_json) as string[];
    if (!allowed.includes(responderId)) {
      return reply
        .code(403)
        .send(errorBody("RESPONDER_FORBIDDEN", "Responder is not allowed"));
    }
    const token = opaqueToken();
    const participantId = randomUUID();
    storage.run(
      `INSERT INTO participants
       (id, session_id, responder_id, auth_hash, role, created_at, expires_at)
       VALUES(?, ?, ?, ?, 'participant', ?, ?)`,
      participantId,
      session.id,
      responderId,
      authHash(token, config),
      new Date().toISOString(),
      session.expires_at,
    );
    setAuthCookie(reply, PARTICIPANT_COOKIE, token, session.expires_at, config);
    return reply.send({
      schema_version: SCHEMA_VERSION,
      session_id: session.id,
      scenario_id: session.scenario_id,
      session_name: session.name,
      participant_id: participantId,
      responder_id: responderId,
      expires_at: session.expires_at,
    });
  });

  app.post("/api/radio/transmissions", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config, "participant");
    if (!actor) return;
    const body = objectBody(request.body);
    const captureMode = optionalString(body.capture_mode) ?? "live_microphone";
    if (
      !["live_microphone", "audio_replay", "cached_transcript"].includes(
        captureMode,
      )
    ) {
      return reply
        .code(400)
        .send(errorBody("INVALID_CAPTURE_MODE", "Capture mode is invalid"));
    }
    const reservationKey = request.headers["idempotency-key"];
    if (
      reservationKey !== undefined &&
      (typeof reservationKey !== "string" ||
        !/^[a-zA-Z0-9-]{16,100}$/.test(reservationKey))
    ) {
      return reply
        .code(400)
        .send(
          errorBody(
            "INVALID_RESERVATION_KEY",
            "Reservation key must be 16 to 100 alphanumeric characters",
          ),
        );
    }
    if (reservationKey) {
      const existing = storage.one<Transmission>(
        "SELECT * FROM transmissions WHERE participant_id = ? AND reservation_key = ?",
        actor.id,
        reservationKey,
      );
      if (existing) {
        if (
          existing.capture_mode !== captureMode ||
          existing.channel !== (optionalString(body.channel) ?? "command-1")
        ) {
          return reply
            .code(409)
            .send(
              errorBody(
                "RESERVATION_CONFLICT",
                "Reservation key was used with different settings",
              ),
            );
        }
        return reply.code(201).send(transmissionView(storage, existing));
      }
    }
    const active = storage.one<{ count: number }>(
      `SELECT COUNT(*) AS count FROM transmissions
       WHERE participant_id = ? AND status IN ('reserved', 'accepted', 'extracting', 'retrying')`,
      actor.id,
    );
    if ((active?.count ?? 0) > 0) {
      return reply
        .code(409)
        .send(
          errorBody("TRANSMISSION_ACTIVE", "A transmission is already active"),
        );
    }
    const transmissionId = randomUUID();
    const now = new Date().toISOString();
    storage.run(
      `INSERT INTO transmissions
       (id, session_id, participant_id, capture_mode, channel, status, created_at, updated_at, reservation_key)
       VALUES(?, ?, ?, ?, ?, 'reserved', ?, ?, ?)`,
      transmissionId,
      actor.session_id,
      actor.id,
      captureMode,
      optionalString(body.channel) ?? "command-1",
      now,
      now,
      reservationKey ?? null,
    );
    return reply.code(201).send({
      schema_version: SCHEMA_VERSION,
      transmission_id: transmissionId,
      session_id: actor.session_id,
      responder_id: actor.responder_id,
      channel: optionalString(body.channel) ?? "command-1",
      capture_mode: captureMode,
      processing_state: "reserved",
      created_at: now,
      updated_at: now,
    });
  });

  app.post("/api/radio/transmissions/:id/token", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config, "participant");
    if (!actor) return;
    const transmission = ownedTransmission(request, reply, storage, actor);
    if (!transmission) return;
    if (transmission.status !== "reserved") {
      return reply
        .code(409)
        .send(
          errorBody(
            "TRANSMISSION_NOT_RESERVED",
            "Transmission is not reservable",
          ),
        );
    }
    if (!config.elevenLabsApiKey) {
      return reply
        .code(503)
        .send(
          errorBody(
            "STT_NOT_CONFIGURED",
            "Speech transcription is not configured",
          ),
        );
    }
    const minuteAgo = new Date(Date.now() - 60_000).toISOString();
    const participantCount = storage.one<{ count: number }>(
      "SELECT COUNT(*) AS count FROM token_issuances WHERE participant_id = ? AND issued_at >= ?",
      actor.id,
      minuteAgo,
    )?.count;
    const sessionCount = storage.one<{ count: number }>(
      "SELECT COUNT(*) AS count FROM token_issuances WHERE session_id = ? AND issued_at >= ?",
      actor.session_id,
      minuteAgo,
    )?.count;
    if (
      (participantCount ?? 0) >= config.tokenRequestsPerMinute ||
      (sessionCount ?? 0) >= config.sessionTokenRequestsPerMinute
    ) {
      return reply
        .code(429)
        .send(errorBody("TOKEN_RATE_LIMITED", "Token request limit exceeded"));
    }
    // Charge an issuance attempt before yielding to the provider so concurrent
    // requests and provider failures cannot bypass the application budget.
    storage.run(
      `INSERT INTO token_issuances(id, participant_id, session_id, transmission_id, issued_at)
       VALUES(?, ?, ?, ?, ?)`,
      randomUUID(),
      actor.id,
      actor.session_id,
      transmission.id,
      new Date().toISOString(),
    );
    let providerResponse: Response;
    try {
      providerResponse = await fetch(
        `${config.elevenLabsApiBaseUrl.replace(/\/$/, "")}/v1/single-use-token/realtime_scribe`,
        {
          method: "POST",
          headers: { "xi-api-key": config.elevenLabsApiKey },
          signal: AbortSignal.timeout(10_000),
        },
      );
    } catch {
      return reply
        .code(502)
        .send(
          errorBody("STT_TOKEN_FAILED", "Speech token could not be issued"),
        );
    }
    if (!providerResponse.ok) {
      request.log.warn(
        { status: providerResponse.status, transmissionId: transmission.id },
        "ElevenLabs token request failed",
      );
      return reply
        .code(502)
        .send(
          errorBody("STT_TOKEN_FAILED", "Speech token could not be issued"),
        );
    }
    const tokenBody = (await providerResponse.json()) as { token?: string };
    if (!tokenBody.token) {
      return reply
        .code(502)
        .send(
          errorBody("STT_TOKEN_INVALID", "Speech provider returned no token"),
        );
    }
    const stillReserved = storage.one<{ status: string }>(
      "SELECT status FROM transmissions WHERE id = ?",
      transmission.id,
    );
    if (
      stillReserved?.status !== "reserved" ||
      Date.parse(actor.session_expires_at) <= Date.now()
    ) {
      return reply
        .code(409)
        .send(
          errorBody(
            "TRANSMISSION_NOT_RESERVED",
            "Capture was cancelled or expired while connecting",
          ),
        );
    }
    reply.header("cache-control", "no-store");
    return reply.send({
      schema_version: SCHEMA_VERSION,
      token: tokenBody.token,
      provider: "elevenlabs",
      model: "scribe_v2_realtime",
    });
  });

  app.post("/api/radio/transmissions/:id/finalize", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config, "participant");
    if (!actor) return;
    const transmission = ownedTransmission(request, reply, storage, actor);
    if (!transmission) return;
    const validation = validateFinalizeBody(
      request.body,
      transmission.id,
      config,
    );
    if ("error" in validation)
      return reply.code(validation.status).send(validation.error);
    return finalizeTransmission(
      storage,
      extraction,
      transmission,
      actor,
      validation.body,
      reply,
    );
  });

  app.post("/api/radio/transmissions/:id/cancel", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config, "participant");
    if (!actor) return;
    const transmission = ownedTransmission(request, reply, storage, actor);
    if (!transmission) return;
    if (transmission.status === "cancelled") {
      return reply.send(transmissionView(storage, transmission));
    }
    if (transmission.status !== "reserved") {
      return reply
        .code(409)
        .send(
          errorBody(
            "TRANSMISSION_FINALIZED",
            "Finalized transmission cannot be cancelled",
          ),
        );
    }
    storage.transaction(() => {
      storage.run(
        "UPDATE transmissions SET status = 'cancelled', updated_at = ? WHERE id = ?",
        new Date().toISOString(),
        transmission.id,
      );
    });
    return reply.send(
      transmissionView(storage, {
        ...transmission,
        status: "cancelled",
        updated_at: new Date().toISOString(),
      }),
    );
  });

  app.post(
    "/api/radio/transmissions/:id/retry-extraction",
    async (request, reply) => {
      const actor = requireActor(request, reply, storage, config);
      if (!actor) return;
      const transmission = accessibleTransmission(
        request,
        reply,
        storage,
        actor,
      );
      if (!transmission) return;
      if (
        ["accepted", "extracting", "retrying"].includes(transmission.status)
      ) {
        return reply.code(202).send(acknowledgement(transmission));
      }
      if (transmission.status !== "failed" || !transmission.input_id) {
        return reply
          .code(409)
          .send(
            errorBody(
              "EXTRACTION_NOT_FAILED",
              "Only a failed extraction can be retried",
            ),
          );
      }
      storage.transaction(() => {
        const now = new Date().toISOString();
        storage.run(
          "UPDATE extraction_jobs SET status = 'queued', attempts = 0, lease_until = NULL, error_code = NULL, extraction_result_json = NULL, updated_at = ? WHERE transmission_id = ?",
          now,
          transmission.id,
        );
        storage.run(
          "UPDATE transmissions SET status = 'retrying', error_code = NULL, updated_at = ? WHERE id = ?",
          now,
          transmission.id,
        );
        storage.appendEvent(
          actor.session_id,
          transmission.id,
          "radio.extraction_retrying",
          {
            extraction_id: storage.one<{ id: string }>(
              "SELECT id FROM extraction_jobs WHERE transmission_id = ?",
              transmission.id,
            )!.id,
            attempt: 1,
            max_attempts: config.extractionMaxAttempts,
            processing_state: "retrying",
            failure: {
              code: transmission.error_code ?? "EXTRACTION_FAILED",
              message: "Retry requested after extraction failure",
              retryable: true,
            },
          },
        );
      });
      return reply
        .code(202)
        .send(acknowledgement({ ...transmission, status: "retrying" }));
    },
  );

  app.get("/api/radio/transmissions/:id", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config);
    if (!actor) return;
    const transmission = accessibleTransmission(request, reply, storage, actor);
    if (!transmission) return;
    return transmissionView(storage, transmission);
  });

  app.get("/api/demo-sessions/:id/snapshot", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config);
    if (!actor) return;
    const sessionId = pathId(request);
    if (actor.session_id !== sessionId) {
      return reply
        .code(403)
        .send(errorBody("SESSION_FORBIDDEN", "Session is not accessible"));
    }
    return storage.transaction(() => {
      const cursor =
        storage.one<{ cursor: number }>(
          "SELECT COALESCE(MAX(sequence), 0) AS cursor FROM events WHERE session_id = ?",
          sessionId,
        )?.cursor ?? 0;
      const transmissions = storage
        .all<Transmission>(
          "SELECT * FROM transmissions WHERE session_id = ? ORDER BY created_at DESC",
          sessionId,
        )
        .map((item) => transmissionView(storage, item));
      return {
        schema_version: SCHEMA_VERSION,
        session_id: sessionId,
        scenario_id: storage.one<{ scenario_id: string }>(
          "SELECT scenario_id FROM sessions WHERE id = ?",
          sessionId,
        )!.scenario_id,
        session_status:
          Date.parse(actor.session_expires_at) > Date.now()
            ? "active"
            : "expired",
        snapshot_at: new Date().toISOString(),
        event_cursor: cursor > 0 ? String(cursor) : null,
        transmissions,
      };
    });
  });

  app.delete("/api/demo-sessions/:id", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config, "presenter");
    if (!actor) return;
    const sessionId = pathId(request);
    if (actor.session_id !== sessionId) {
      return reply
        .code(403)
        .send(errorBody("SESSION_FORBIDDEN", "Session is not accessible"));
    }
    const body = objectBody(request.body);
    if (body.confirm_session_id !== sessionId) {
      return reply
        .code(400)
        .send(
          errorBody(
            "SESSION_DELETE_CONFIRMATION_REQUIRED",
            "confirm_session_id must match the session being deleted",
          ),
        );
    }
    storage.deleteSession(sessionId);
    clearAuthCookies(reply, config);
    return reply.code(204).send();
  });

  app.get("/api/demo-sessions/:id/events", async (request, reply) => {
    const actor = requireActor(request, reply, storage, config);
    if (!actor) return;
    const sessionId = pathId(request);
    if (actor.session_id !== sessionId) {
      return reply
        .code(403)
        .send(errorBody("SESSION_FORBIDDEN", "Session is not accessible"));
    }
    const query = request.query as { cursor?: string };
    let cursor = Number.parseInt(
      query.cursor ?? String(request.headers["last-event-id"] ?? "0"),
      10,
    );
    if (!Number.isFinite(cursor) || cursor < 0) cursor = 0;
    reply.hijack();
    reply.raw.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    });
    let closed = false;
    let streamTimer: NodeJS.Timeout | undefined;
    reply.raw.on("close", () => {
      closed = true;
      if (streamTimer) clearTimeout(streamTimer);
    });
    let lastHeartbeat = 0;
    const stream = () => {
      if (closed || reply.raw.destroyed) return;
      if (
        Date.parse(actor.session_expires_at) <= Date.now() ||
        !storage.one("SELECT 1 FROM sessions WHERE id = ?", sessionId)
      ) {
        closed = true;
        reply.raw.end();
        return;
      }
      // Bound memory for a slow or disconnected consumer; it reconnects from a snapshot.
      if (reply.raw.writableLength > 1_000_000) {
        reply.raw.destroy();
        return;
      }
      const events = storage.listEvents(sessionId, cursor, 250);
      for (const event of events) {
        cursor = event.sequence;
        reply.raw.write(
          `id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify({
            schema_version: SCHEMA_VERSION,
            event_id: event.event_id,
            session_id: event.session_id,
            transmission_id: event.transmission_id,
            occurred_at: event.occurred_at,
            event_type: event.type,
            payload: event.payload,
          })}\n\n`,
        );
      }
      if (!events.length && Date.now() - lastHeartbeat >= 15_000) {
        reply.raw.write(`: heartbeat ${Date.now()}\n\n`);
        lastHeartbeat = Date.now();
      }
      streamTimer = setTimeout(stream, 500);
      streamTimer.unref();
    };
    stream();
  });

  app.post("/api/demo-sessions/:id/replay", async (request, reply) => {
    if (!config.replayEnabled) {
      return reply
        .code(404)
        .send(errorBody("REPLAY_DISABLED", "Replay is disabled"));
    }
    const actor = requireActor(request, reply, storage, config, "presenter");
    if (!actor) return;
    const sessionId = pathId(request);
    if (actor.session_id !== sessionId) {
      return reply
        .code(403)
        .send(errorBody("SESSION_FORBIDDEN", "Session is not accessible"));
    }
    const body = objectBody(request.body);
    const fixture = optionalString(body.fixture_name) ?? "riverside_report";
    if (!["riverside_report", "caraballeda_report"].includes(fixture)) {
      return reply
        .code(404)
        .send(errorBody("REPLAY_NOT_FOUND", "Named replay does not exist"));
    }
    const mode = optionalString(body.capture_mode) ?? "cached_transcript";
    if (!["cached_transcript", "cached_observation"].includes(mode)) {
      return reply
        .code(400)
        .send(
          errorBody(
            "INVALID_REPLAY_MODE",
            "Use cached_transcript or cached_observation",
          ),
        );
    }
    const transmission: Transmission = {
      id: randomUUID(),
      session_id: sessionId,
      participant_id: actor.id,
      capture_mode: mode as CaptureMode,
      channel: "command-1",
      status: "reserved",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      finalized_at: null,
      payload_hash: null,
      input_id: null,
      error_code: null,
    };
    storage.run(
      `INSERT INTO transmissions
       (id, session_id, participant_id, capture_mode, channel, status, created_at, updated_at)
       VALUES(?, ?, ?, ?, ?, 'reserved', ?, ?)`,
      transmission.id,
      sessionId,
      actor.id,
      transmission.capture_mode,
      transmission.channel,
      transmission.created_at,
      transmission.updated_at,
    );
    const finalized = await finalizeTransmission(
      storage,
      extraction,
      transmission,
      actor,
      {
        schema_version: SCHEMA_VERSION,
        transmission_id: transmission.id,
        transcript:
          fixture === "caraballeda_report"
            ? "Command, partial collapse at Residencias Las Anclas. Multiple voices heard. East access may be blocked."
            : REPLAY_TRANSCRIPT,
        capture_started_at: transmission.created_at,
        capture_ended_at: transmission.created_at,
        segments: [
          {
            sequence: 0,
            text:
              fixture === "caraballeda_report"
                ? "Command, partial collapse at Residencias Las Anclas. Multiple voices heard. East access may be blocked."
                : REPLAY_TRANSCRIPT,
            started_at_ms: 0,
            ended_at_ms: 1,
          },
        ],
      },
      reply,
    );
    return finalized;
  });

  app.addHook("onClose", async () => {
    worker.stop();
    storage.close();
  });

  if (config.startWorker) worker.start();
  return { app, storage, worker };
}

async function finalizeTransmission(
  storage: Storage,
  extraction: ExtractionService,
  transmission: Transmission,
  actor: Actor,
  body: FinalizeBody,
  reply: FastifyReply,
): Promise<unknown> {
  const payloadHash = sha256(canonicalJson(body));
  if (transmission.payload_hash) {
    if (transmission.payload_hash !== payloadHash) {
      return reply
        .code(409)
        .send(
          errorBody(
            "FINALIZE_CONFLICT",
            "Transmission was finalized differently",
          ),
        );
    }
    return reply.code(202).send(acknowledgement(transmission));
  }
  if (transmission.status !== "reserved") {
    return reply
      .code(409)
      .send(
        errorBody(
          "TRANSMISSION_NOT_RESERVED",
          "Transmission cannot be finalized",
        ),
      );
  }
  const inputId = randomUUID();
  const jobId = randomUUID();
  const now = new Date().toISOString();
  const raw: RawRadioInput = {
    schema_version: SCHEMA_VERSION,
    input_id: inputId,
    timestamp: now,
    audio_reference: null,
    transcript: body.transcript.trim(),
    channel: transmission.channel,
    speaker_id: actor.responder_id,
    metadata: {
      capture_mode: transmission.capture_mode,
      transmission_id: transmission.id,
      session_id: transmission.session_id,
      stt_provider:
        transmission.capture_mode === "live_microphone" ? "elevenlabs" : null,
      stt_model:
        transmission.capture_mode === "live_microphone"
          ? "scribe_v2_realtime"
          : null,
      language: "en",
      provider_session_reference: body.provider_session_reference ?? null,
      capture_started_at: body.capture_started_at,
      capture_ended_at: body.capture_ended_at,
      segments: body.segments,
    },
  };
  try {
    extraction.validateRawInput(raw);
  } catch {
    return reply
      .code(422)
      .send(
        errorBody("RAW_INPUT_INVALID", "Finalized input violates its contract"),
      );
  }

  const accepted = storage.transaction(() => {
    const current = storage.one<Transmission>(
      "SELECT * FROM transmissions WHERE id = ?",
      transmission.id,
    );
    if (!current) throw new Error("TRANSMISSION_MISSING");
    if (current.payload_hash) return current;
    if (current.status !== "reserved")
      throw new Error("TRANSMISSION_NOT_RESERVED");
    storage.run(
      `UPDATE transmissions
       SET status = 'accepted', finalized_at = ?, updated_at = ?, payload_hash = ?, input_id = ?
       WHERE id = ?`,
      now,
      now,
      payloadHash,
      inputId,
      transmission.id,
    );
    storage.run(
      "INSERT INTO raw_inputs(input_id, transmission_id, payload_json, created_at) VALUES(?, ?, ?, ?)",
      inputId,
      transmission.id,
      JSON.stringify(raw),
      now,
    );
    storage.run(
      `INSERT INTO extraction_jobs
       (id, transmission_id, session_id, input_id, status, created_at, updated_at)
       VALUES(?, ?, ?, ?, 'queued', ?, ?)`,
      jobId,
      transmission.id,
      transmission.session_id,
      inputId,
      now,
      now,
    );
    storage.appendEvent(
      transmission.session_id,
      transmission.id,
      "radio.accepted",
      {
        input_id: inputId,
        processing_state: "accepted",
      },
    );
    return {
      ...transmission,
      status: "accepted",
      finalized_at: now,
      updated_at: now,
      payload_hash: payloadHash,
      input_id: inputId,
    };
  });
  if (accepted.payload_hash !== payloadHash) {
    return reply
      .code(409)
      .send(
        errorBody(
          "FINALIZE_CONFLICT",
          "Transmission was finalized differently",
        ),
      );
  }
  return reply.code(202).send(acknowledgement(accepted));
}

function requireActor(
  request: FastifyRequest,
  reply: FastifyReply,
  storage: Storage,
  config: AppConfig,
  requiredRole?: Actor["role"],
): Actor | undefined {
  const requestedId = (request.params as { id?: string } | undefined)?.id;
  const requestedSession = request.url.startsWith("/api/demo-sessions/")
    ? requestedId
    : requestedId
      ? storage.one<{ session_id: string }>(
          "SELECT session_id FROM transmissions WHERE id = ?",
          requestedId,
        )?.session_id
      : undefined;
  const candidateTokens = [
    request.cookies[PARTICIPANT_COOKIE],
    request.cookies[PRESENTER_COOKIE],
  ].filter((value): value is string => Boolean(value));
  let actor: Actor | undefined;
  let authenticatedWithWrongRole = false;
  for (const token of candidateTokens) {
    const candidate = storage.one<Actor>(
      `SELECT p.id, p.session_id, p.responder_id, p.role, s.expires_at AS session_expires_at
       FROM participants p JOIN sessions s ON s.id = p.session_id
       WHERE p.auth_hash = ? AND p.expires_at > ? AND s.expires_at > ?`,
      authHash(token, config),
      new Date().toISOString(),
      new Date().toISOString(),
    );
    if (!candidate) continue;
    if (requestedSession && candidate.session_id !== requestedSession) {
      authenticatedWithWrongRole = true;
      continue;
    }
    if (requiredRole && candidate.role !== requiredRole) {
      authenticatedWithWrongRole = true;
      continue;
    }
    actor = candidate;
    break;
  }
  if (!actor) {
    if (authenticatedWithWrongRole) {
      reply
        .code(403)
        .send(
          errorBody(
            "ROLE_FORBIDDEN",
            requiredRole
              ? `${requiredRole} authorization is required`
              : "Session is not accessible",
          ),
        );
      return undefined;
    }
    reply
      .code(401)
      .send(errorBody("AUTH_REQUIRED", "A valid session cookie is required"));
    return undefined;
  }
  return actor;
}

function ownedTransmission(
  request: FastifyRequest,
  reply: FastifyReply,
  storage: Storage,
  actor: Actor,
): Transmission | undefined {
  const transmission = storage.one<Transmission>(
    "SELECT * FROM transmissions WHERE id = ?",
    pathId(request),
  );
  if (!transmission) {
    reply
      .code(404)
      .send(errorBody("TRANSMISSION_NOT_FOUND", "Transmission was not found"));
    return undefined;
  }
  if (
    transmission.participant_id !== actor.id ||
    transmission.session_id !== actor.session_id
  ) {
    reply
      .code(403)
      .send(
        errorBody(
          "TRANSMISSION_FORBIDDEN",
          "Transmission is not owned by this participant",
        ),
      );
    return undefined;
  }
  return transmission;
}

function accessibleTransmission(
  request: FastifyRequest,
  reply: FastifyReply,
  storage: Storage,
  actor: Actor,
): Transmission | undefined {
  const transmission = storage.one<Transmission>(
    "SELECT * FROM transmissions WHERE id = ?",
    pathId(request),
  );
  if (!transmission) {
    reply
      .code(404)
      .send(errorBody("TRANSMISSION_NOT_FOUND", "Transmission was not found"));
    return undefined;
  }
  if (
    transmission.session_id !== actor.session_id ||
    (actor.role !== "presenter" && transmission.participant_id !== actor.id)
  ) {
    reply
      .code(403)
      .send(
        errorBody("TRANSMISSION_FORBIDDEN", "Transmission is not accessible"),
      );
    return undefined;
  }
  return transmission;
}

function transmissionView(
  storage: Storage,
  transmission: Transmission,
): Record<string, unknown> {
  const rawRow = transmission.input_id
    ? storage.one<{ payload_json: string }>(
        "SELECT payload_json FROM raw_inputs WHERE input_id = ?",
        transmission.input_id,
      )
    : undefined;
  const raw = rawRow
    ? (JSON.parse(rawRow.payload_json) as RawRadioInput)
    : undefined;
  const metadata = raw?.metadata ?? {};
  const responder = storage.one<{ responder_id: string }>(
    "SELECT responder_id FROM participants WHERE id = ?",
    transmission.participant_id,
  );
  const extractionRow = storage.one<{ extraction_result_json: string | null }>(
    "SELECT extraction_result_json FROM extraction_jobs WHERE transmission_id = ?",
    transmission.id,
  );
  const extractionResult = extractionRow?.extraction_result_json
    ? (JSON.parse(extractionRow.extraction_result_json) as Record<
        string,
        unknown
      >)
    : undefined;
  const locationResolutions = storage
    .all<{ payload_json: string }>(
      "SELECT payload_json FROM resolutions WHERE transmission_id = ? ORDER BY created_at",
      transmission.id,
    )
    .map((row) => JSON.parse(row.payload_json));
  const result: Record<string, unknown> = {
    schema_version: SCHEMA_VERSION,
    transmission_id: transmission.id,
    session_id: transmission.session_id,
    responder_id: responder?.responder_id ?? "unknown",
    channel: transmission.channel ?? "command-1",
    capture_mode: transmission.capture_mode,
    processing_state: transmission.status,
    created_at: transmission.created_at,
    updated_at: transmission.updated_at ?? transmission.created_at,
  };
  if (raw && transmission.finalized_at && transmission.input_id) {
    result.accepted_at = transmission.finalized_at;
    result.input_id = transmission.input_id;
    result.transcript = raw.transcript;
    result.capture_started_at = metadata.capture_started_at;
    result.capture_ended_at = metadata.capture_ended_at;
    result.segments = metadata.segments;
    if (typeof metadata.provider_session_reference === "string") {
      result.provider_session_reference = metadata.provider_session_reference;
    }
  }
  if (extractionResult) {
    result.extraction_result = extractionResult;
    if (transmission.status === "failed" && extractionResult.failure) {
      result.failure = extractionResult.failure;
    }
  }
  if (["resolved", "unresolved"].includes(transmission.status)) {
    result.location_resolutions = locationResolutions;
  }
  return result;
}

function validateFinalizeBody(
  input: unknown,
  transmissionId: string,
  config: AppConfig,
):
  | { body: FinalizeBody }
  | { status: 400 | 422; error: ReturnType<typeof errorBody> } {
  let body: Record<string, unknown>;
  try {
    body = objectBody(input);
  } catch {
    return {
      status: 400,
      error: errorBody("INVALID_FINALIZE", "Finalize body must be an object"),
    };
  }
  const allowed = new Set([
    "schema_version",
    "transmission_id",
    "transcript",
    "provider_session_reference",
    "capture_started_at",
    "capture_ended_at",
    "segments",
  ]);
  if (Object.keys(body).some((key) => !allowed.has(key))) {
    return {
      status: 400,
      error: errorBody("INVALID_FINALIZE", "Finalize body has unknown fields"),
    };
  }
  if (
    body.schema_version !== SCHEMA_VERSION ||
    body.transmission_id !== transmissionId
  ) {
    return {
      status: 400,
      error: errorBody(
        "INVALID_FINALIZE",
        "Schema or transmission ID is invalid",
      ),
    };
  }
  const transcript = requiredString(body.transcript);
  if (!transcript) {
    return {
      status: 422,
      error: errorBody("EMPTY_TRANSCRIPT", "No speech was detected"),
    };
  }
  if (transcript.length > config.maxTranscriptCharacters) {
    return {
      status: 422,
      error: errorBody("TRANSCRIPT_TOO_LONG", "Transcript exceeds limit"),
    };
  }
  if (
    body.provider_session_reference !== undefined &&
    !requiredString(body.provider_session_reference)
  ) {
    return {
      status: 400,
      error: errorBody(
        "INVALID_PROVIDER_REFERENCE",
        "Provider reference must be non-empty",
      ),
    };
  }
  for (const field of ["capture_started_at", "capture_ended_at"] as const) {
    const value = body[field];
    if (!validDateString(value)) {
      return {
        status: 400,
        error: errorBody(
          "INVALID_TIMESTAMP",
          `${field} must be an ISO timestamp`,
        ),
      };
    }
  }
  if (
    Date.parse(body.capture_ended_at as string) <
    Date.parse(body.capture_started_at as string)
  ) {
    return {
      status: 400,
      error: errorBody("INVALID_TIMESTAMP", "Capture end precedes start"),
    };
  }
  if (
    !Array.isArray(body.segments) ||
    body.segments.length < 1 ||
    body.segments.length > 1_000
  ) {
    return {
      status: 400,
      error: errorBody("INVALID_SEGMENTS", "Segments must be a bounded array"),
    };
  }
  const segments: FinalizeBody["segments"] = [];
  let previous = -1;
  for (const value of body.segments) {
    if (!value || typeof value !== "object") {
      return {
        status: 400,
        error: errorBody("INVALID_SEGMENTS", "Segment is invalid"),
      };
    }
    const segment = value as Record<string, unknown>;
    const segmentKeys = new Set([
      "sequence",
      "text",
      "started_at_ms",
      "ended_at_ms",
      "provider_segment_id",
    ]);
    if (
      Object.keys(segment).some((key) => !segmentKeys.has(key)) ||
      !Number.isInteger(segment.sequence) ||
      (segment.sequence as number) <= previous ||
      !requiredString(segment.text) ||
      !Number.isInteger(segment.started_at_ms) ||
      (segment.started_at_ms as number) < 0 ||
      !Number.isInteger(segment.ended_at_ms) ||
      (segment.ended_at_ms as number) < (segment.started_at_ms as number) ||
      (segment.provider_segment_id !== undefined &&
        !requiredString(segment.provider_segment_id))
    ) {
      return {
        status: 400,
        error: errorBody(
          "INVALID_SEGMENTS",
          "Segments must be ordered and valid",
        ),
      };
    }
    previous = segment.sequence as number;
    segments.push({
      sequence: previous,
      text: (segment.text as string).trim(),
      started_at_ms: segment.started_at_ms as number,
      ended_at_ms: segment.ended_at_ms as number,
      ...(segment.provider_segment_id === undefined
        ? {}
        : {
            provider_segment_id: (segment.provider_segment_id as string).trim(),
          }),
    });
  }
  return {
    body: {
      schema_version: SCHEMA_VERSION,
      transmission_id: transmissionId,
      transcript,
      ...(body.provider_session_reference === undefined
        ? {}
        : {
            provider_session_reference: (
              body.provider_session_reference as string
            ).trim(),
          }),
      capture_started_at: body.capture_started_at as string,
      capture_ended_at: body.capture_ended_at as string,
      segments,
    },
  };
}

function acknowledgement(transmission: Transmission): Record<string, unknown> {
  return {
    schema_version: SCHEMA_VERSION,
    input_id: transmission.input_id,
    transmission_id: transmission.id,
    processing_state: transmission.status,
    accepted_at: transmission.finalized_at,
  };
}

function setAuthCookie(
  reply: FastifyReply,
  name: string,
  token: string,
  expiresAt: string,
  config: AppConfig,
): void {
  reply.setCookie(name, token, {
    path: "/",
    httpOnly: true,
    secure: config.cookieSecure,
    sameSite: "lax",
    expires: new Date(expiresAt),
  });
}

function clearAuthCookies(reply: FastifyReply, config: AppConfig): void {
  for (const name of [PRESENTER_COOKIE, PARTICIPANT_COOKIE]) {
    reply.clearCookie(name, {
      path: "/",
      httpOnly: true,
      secure: config.cookieSecure,
      sameSite: "lax",
    });
  }
}

function pathId(request: FastifyRequest): string {
  return (request.params as { id: string }).id;
}

function objectBody(input: unknown): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)) return {};
  return input as Record<string, unknown>;
}

function requiredString(input: unknown): string | undefined {
  if (typeof input !== "string") return undefined;
  const value = input.trim();
  return value.length ? value : undefined;
}

function optionalString(input: unknown): string | undefined {
  return input === undefined ? undefined : requiredString(input);
}

function stringArray(input: unknown): string[] {
  if (!Array.isArray(input)) return [];
  return [
    ...new Set(
      input
        .map(requiredString)
        .filter((value): value is string => Boolean(value)),
    ),
  ];
}

function validDateString(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function opaqueToken(): string {
  return randomBytes(32).toString("base64url");
}

function generateJoinCode(): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  return Array.from(
    randomBytes(7),
    (byte) => alphabet[byte % alphabet.length],
  ).join("");
}

function authHash(value: string, config: AppConfig): string {
  return createHmac("sha256", config.sessionSigningSecret)
    .update(value)
    .digest("hex");
}

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .filter(([, child]) => child !== undefined)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, child]) => `${JSON.stringify(key)}:${canonicalJson(child)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function secretEquals(
  input: string | string[] | undefined,
  expected: string,
): boolean {
  if (typeof input !== "string") return false;
  const actualBuffer = Buffer.from(input);
  const expectedBuffer = Buffer.from(expected);
  return (
    actualBuffer.length === expectedBuffer.length &&
    timingSafeEqual(actualBuffer, expectedBuffer)
  );
}

function errorBody(
  code: string,
  message: string,
): {
  schema_version: "2.1";
  error: { code: string; message: string };
} {
  return { schema_version: SCHEMA_VERSION, error: { code, message } };
}
