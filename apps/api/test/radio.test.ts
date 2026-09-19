import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import Ajv2020, { type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadConfig } from "../src/config.js";
import { buildApi, type ApiRuntime } from "../src/server.js";

const presenterKey = "presenter-test-key";
const scenarioId = "simulated-riverside-apartments";
let runtime: ApiRuntime;
const contractValidators = createContractValidators();

beforeEach(async () => {
  process.env.NODE_ENV = "test";
  runtime = await buildApi(
    loadConfig({
      databasePath: ":memory:",
      sessionSigningSecret: "test-signing-secret-that-is-long-enough",
      presenterApiKey: presenterKey,
      appOrigin: "http://localhost:5173",
      cookieSecure: false,
      startWorker: false,
      llmApiKey: undefined,
      llmModel: undefined,
    }),
  );
});

afterEach(async () => {
  await runtime.app.close();
  vi.unstubAllGlobals();
});

describe("radio backend MVP", () => {
  it("enforces presenter, join, and responder authorization", async () => {
    const unauthorized = await runtime.app.inject({
      method: "POST",
      url: "/api/demo-sessions",
      payload: { allowed_responder_ids: ["rescue-alpha"] },
    });
    expect(unauthorized.statusCode).toBe(401);

    const session = await createSession();
    const denied = await runtime.app.inject({
      method: "POST",
      url: "/api/demo-sessions/join",
      payload: { join_code: session.body.join_code, responder_id: "intruder" },
    });
    expect(denied.statusCode).toBe(403);

    const joined = await joinSession(session.body.join_code);
    expect(joined.response.statusCode).toBe(200);
    expect(joined.body.responder_id).toBe("rescue-alpha");

    const reserveWithoutCookie = await runtime.app.inject({
      method: "POST",
      url: "/api/radio/transmissions",
      payload: { capture_mode: "live_microphone" },
    });
    expect(reserveWithoutCookie.statusCode).toBe(401);
  });

  it("recovers lost reservation acknowledgements with the same idempotency key", async () => {
    const context = await joinedContext();
    const options = {
      method: "POST" as const,
      url: "/api/radio/transmissions",
      headers: {
        cookie: context.participantCookie,
        "idempotency-key": "reservation-test-key-0001",
      },
      payload: { capture_mode: "live_microphone" },
    };
    const first = await runtime.app.inject(options);
    const repeated = await runtime.app.inject(options);
    expect(first.statusCode).toBe(201);
    expect(repeated.json().transmission_id).toBe(first.json().transmission_id);
    expect(
      runtime.storage.one<{ count: number }>(
        "SELECT COUNT(*) AS count FROM transmissions",
      )?.count,
    ).toBe(1);
    const conflict = await runtime.app.inject({
      ...options,
      payload: { capture_mode: "audio_replay" },
    });
    expect(conflict.statusCode).toBe(409);
  });

  it("selects the matching session when presenter and participant cookies coexist", async () => {
    const old = await joinedContext();
    const current = await createSession();
    const response = await runtime.app.inject({
      method: "GET",
      url: `/api/demo-sessions/${current.body.session_id}/snapshot`,
      headers: {
        cookie: `${old.participantCookie}; ${current.presenterCookie}`,
      },
    });
    expect(response.statusCode).toBe(200);
    expect(response.json().session_id).toBe(current.body.session_id);
  });

  it("retries a failed extraction without creating another input or job", async () => {
    const context = await joinedContext();
    const transmission = await reserve(context.participantCookie);
    await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/finalize`,
      headers: { cookie: context.participantCookie },
      payload: finalPayload(transmission.transmission_id),
    });
    runtime.storage.run(
      "UPDATE transmissions SET status = 'failed', error_code = 'TEST_OUTAGE' WHERE id = ?",
      transmission.transmission_id,
    );
    runtime.storage.run(
      "UPDATE extraction_jobs SET status = 'failed', attempts = 2 WHERE transmission_id = ?",
      transmission.transmission_id,
    );
    const options = {
      method: "POST" as const,
      url: `/api/radio/transmissions/${transmission.transmission_id}/retry-extraction`,
      headers: { cookie: context.presenterCookie },
      payload: {},
    };
    expect((await runtime.app.inject(options)).statusCode).toBe(202);
    expect((await runtime.app.inject(options)).statusCode).toBe(202);
    const event = runtime.storage.listEvents(context.sessionId, 0).at(-1)!;
    expect(
      contractValidators.event({
        schema_version: "2.0",
        event_id: event.event_id,
        session_id: event.session_id,
        transmission_id: event.transmission_id,
        occurred_at: event.occurred_at,
        event_type: event.type,
        payload: event.payload,
      }),
      JSON.stringify(contractValidators.event.errors),
    ).toBe(true);
    await runtime.worker.runOnce();
    expect(
      runtime.storage.one<{ count: number }>(
        "SELECT COUNT(*) AS count FROM raw_inputs",
      )?.count,
    ).toBe(1);
    expect(
      runtime.storage.one<{ count: number }>(
        "SELECT COUNT(*) AS count FROM extraction_jobs",
      )?.count,
    ).toBe(1);
    expect(
      runtime.storage.one<{ status: string }>(
        "SELECT status FROM transmissions WHERE id = ?",
        transmission.transmission_id,
      )?.status,
    ).toBe("resolved");
  });

  it("purges expired sessions and their stored reports", async () => {
    const context = await joinedContext();
    await reserve(context.participantCookie);
    runtime.storage.run(
      "UPDATE sessions SET expires_at = '2020-01-01T00:00:00.000Z' WHERE id = ?",
      context.sessionId,
    );
    expect(await runtime.worker.runOnce()).toBe(false);
    expect(
      runtime.storage.one(
        "SELECT id FROM sessions WHERE id = ?",
        context.sessionId,
      ),
    ).toBeUndefined();
    expect(
      runtime.storage.one(
        "SELECT id FROM transmissions WHERE session_id = ?",
        context.sessionId,
      ),
    ).toBeUndefined();
  });

  it("durably finalizes once and rejects a conflicting retry", async () => {
    const context = await joinedContext();
    const transmission = await reserve(context.participantCookie);
    const payload = finalPayload(transmission.transmission_id);

    const first = await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/finalize`,
      headers: { cookie: context.participantCookie },
      payload,
    });
    expect(first.statusCode).toBe(202);
    const firstBody = first.json();
    expect(
      contractValidators.finalize(firstBody),
      JSON.stringify(contractValidators.finalize.errors),
    ).toBe(true);
    expect(firstBody.processing_state).toBe("accepted");

    const duplicate = await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/finalize`,
      headers: { cookie: context.participantCookie },
      payload,
    });
    expect(duplicate.statusCode).toBe(202);
    expect(duplicate.json().input_id).toBe(firstBody.input_id);

    const conflict = await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/finalize`,
      headers: { cookie: context.participantCookie },
      payload: { ...payload, transcript: "A different final transcript" },
    });
    expect(conflict.statusCode).toBe(409);

    expect(
      runtime.storage.one<{ count: number }>(
        "SELECT COUNT(*) AS count FROM raw_inputs",
      )?.count,
    ).toBe(1);
    expect(
      runtime.storage.one<{ count: number }>(
        "SELECT COUNT(*) AS count FROM extraction_jobs",
      )?.count,
    ).toBe(1);
    expect(
      runtime.storage.one<{ count: number }>(
        "SELECT COUNT(*) AS count FROM events WHERE type = 'radio.accepted'",
      )?.count,
    ).toBe(1);
  });

  it("extracts uncertainty and resolves the explicit simulated alias", async () => {
    const context = await joinedContext();
    const transmission = await reserve(context.participantCookie);
    const finalized = await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/finalize`,
      headers: { cookie: context.participantCookie },
      payload: finalPayload(transmission.transmission_id),
    });
    expect(finalized.statusCode).toBe(202);
    expect(await runtime.worker.runOnce()).toBe(true);

    const status = await runtime.app.inject({
      method: "GET",
      url: `/api/radio/transmissions/${transmission.transmission_id}`,
      headers: { cookie: context.participantCookie },
    });
    expect(status.statusCode).toBe(200);
    const body = status.json();
    expect(
      contractValidators.transmission(body),
      JSON.stringify(contractValidators.transmission.errors),
    ).toBe(true);
    expect(body.processing_state).toBe("resolved");
    expect(body.extraction_result.observations.length).toBeGreaterThanOrEqual(
      3,
    );
    const victim = body.extraction_result.observations.find(
      (item: { incident_type: string }) =>
        item.incident_type === "trapped_victims",
    );
    expect(victim.victim_indication).toEqual({
      type: "possible_multiple",
      count_min: null,
      count_max: null,
    });
    expect(victim.structural_hazard).toBe("reported_damage");
    expect(victim.access_status).toBe("possibly_blocked");
    expect(body.location_resolutions).toHaveLength(
      body.extraction_result.observations.length,
    );
    expect(body.location_resolutions[0]).toMatchObject({
      resolution_status: "resolved",
      resolved_site_id: "site-sim-riverside-apartments",
      metadata: {
        simulated: true,
        matched_alias: "Riverside Apartments",
        scenario_id: scenarioId,
      },
    });
    for (const event of runtime.storage.listEvents(context.sessionId, 0)) {
      const envelope = {
        schema_version: "2.0",
        event_id: event.event_id,
        session_id: event.session_id,
        transmission_id: event.transmission_id,
        occurred_at: event.occurred_at,
        event_type: event.type,
        payload: event.payload,
      };
      expect(
        contractValidators.event(envelope),
        JSON.stringify(contractValidators.event.errors),
      ).toBe(true);
    }
  });

  it("preserves unknown upper bounds for at-least reports", async () => {
    const context = await joinedContext();
    const transmission = await reserve(context.participantCookie);
    const payload = finalPayload(transmission.transmission_id);
    payload.transcript =
      "At Riverside Apartments, hearing at least three voices.";
    payload.segments = [
      {
        sequence: 0,
        text: payload.transcript,
        started_at_ms: 0,
        ended_at_ms: 2500,
      },
    ];
    await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/finalize`,
      headers: { cookie: context.participantCookie },
      payload,
    });
    runtime.storage.run(
      "UPDATE extraction_jobs SET status = 'leased', lease_until = ?",
      "2020-01-01T00:00:00.000Z",
    );
    expect(await runtime.worker.runOnce()).toBe(true);
    expect(await runtime.worker.runOnce()).toBe(false);
    const observation = runtime.storage.one<{ payload_json: string }>(
      "SELECT payload_json FROM observations LIMIT 1",
    );
    expect(JSON.parse(observation!.payload_json).victim_indication).toEqual({
      type: "possible_multiple",
      count_min: 3,
      count_max: null,
    });
  });

  it("supports presenter replay and consistent snapshot cursors", async () => {
    const session = await createSession();
    const replay = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${session.body.session_id}/replay`,
      headers: { cookie: session.presenterCookie },
      payload: {
        fixture_name: "riverside_report",
        capture_mode: "cached_observation",
      },
    });
    expect(replay.statusCode).toBe(202);
    await runtime.worker.runOnce();

    const snapshot = await runtime.app.inject({
      method: "GET",
      url: `/api/demo-sessions/${session.body.session_id}/snapshot`,
      headers: { cookie: session.presenterCookie },
    });
    expect(snapshot.statusCode).toBe(200);
    const body = snapshot.json();
    expect(
      contractValidators.snapshot(body),
      JSON.stringify(contractValidators.snapshot.errors),
    ).toBe(true);
    expect(Number(body.event_cursor)).toBeGreaterThan(0);
    expect(body.transmissions).toHaveLength(1);
    expect(body.transmissions[0]).toMatchObject({
      capture_mode: "cached_observation",
      processing_state: "resolved",
      extraction_result: {
        model_reference: "cached-riverside-observations-v1",
      },
    });
  });

  it("emits retry status before terminal extraction failure", async () => {
    await runtime.app.close();
    const providerFetch = vi
      .fn()
      .mockRejectedValue(new Error("provider offline"));
    vi.stubGlobal("fetch", providerFetch);
    runtime = await buildApi(
      loadConfig({
        databasePath: ":memory:",
        sessionSigningSecret: "test-signing-secret-that-is-long-enough",
        presenterApiKey: presenterKey,
        appOrigin: "http://localhost:5173",
        cookieSecure: false,
        startWorker: false,
        llmApiKey: "configured-for-test",
        llmModel: "test-model",
        extractionMaxAttempts: 2,
      }),
    );
    const context = await joinedContext();
    const transmission = await reserve(context.participantCookie);
    await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/finalize`,
      headers: { cookie: context.participantCookie },
      payload: finalPayload(transmission.transmission_id),
    });

    expect(await runtime.worker.runOnce()).toBe(true);
    const firstAttempt = await runtime.app.inject({
      method: "GET",
      url: `/api/radio/transmissions/${transmission.transmission_id}`,
      headers: { cookie: context.participantCookie },
    });
    expect(firstAttempt.json().processing_state).toBe("retrying");
    const retryEvents = runtime.storage.listEvents(context.sessionId, 0);
    expect(
      retryEvents.filter((event) => event.type === "radio.failed"),
    ).toHaveLength(0);
    const retryEvent = retryEvents.find(
      (event) => event.type === "radio.extraction_retrying",
    );
    expect(retryEvent?.payload).toMatchObject({
      attempt: 1,
      max_attempts: 2,
      processing_state: "retrying",
      failure: { retryable: true },
    });
    expect(
      contractValidators.event({
        schema_version: "2.0",
        event_id: retryEvent!.event_id,
        session_id: retryEvent!.session_id,
        transmission_id: retryEvent!.transmission_id,
        occurred_at: retryEvent!.occurred_at,
        event_type: retryEvent!.type,
        payload: retryEvent!.payload,
      }),
      JSON.stringify(contractValidators.event.errors),
    ).toBe(true);

    expect(await runtime.worker.runOnce()).toBe(true);
    const terminalEvents = runtime.storage.listEvents(context.sessionId, 0);
    expect(
      terminalEvents.filter((event) => event.type === "radio.failed"),
    ).toHaveLength(1);
    const terminalEvent = terminalEvents.find(
      (event) => event.type === "radio.failed",
    );
    expect(terminalEvent?.payload).toMatchObject({
      failure: { retryable: false },
    });
    expect(
      contractValidators.event({
        schema_version: "2.0",
        event_id: terminalEvent!.event_id,
        session_id: terminalEvent!.session_id,
        transmission_id: terminalEvent!.transmission_id,
        occurred_at: terminalEvent!.occurred_at,
        event_type: terminalEvent!.type,
        payload: terminalEvent!.payload,
      }),
      JSON.stringify(contractValidators.event.errors),
    ).toBe(true);
  });

  it("requires presenter confirmation before deleting a session and its data", async () => {
    const context = await joinedContext();
    const transmission = await reserve(context.participantCookie);
    await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/finalize`,
      headers: { cookie: context.participantCookie },
      payload: finalPayload(transmission.transmission_id),
    });
    await runtime.worker.runOnce();

    const participantDenied = await runtime.app.inject({
      method: "DELETE",
      url: `/api/demo-sessions/${context.sessionId}`,
      headers: { cookie: context.participantCookie },
      payload: { confirm_session_id: context.sessionId },
    });
    expect(participantDenied.statusCode).toBe(403);

    const unconfirmed = await runtime.app.inject({
      method: "DELETE",
      url: `/api/demo-sessions/${context.sessionId}`,
      headers: { cookie: context.presenterCookie },
      payload: { confirm_session_id: "wrong-session" },
    });
    expect(unconfirmed.statusCode).toBe(400);

    const deleted = await runtime.app.inject({
      method: "DELETE",
      url: `/api/demo-sessions/${context.sessionId}`,
      headers: { cookie: context.presenterCookie },
      payload: { confirm_session_id: context.sessionId },
    });
    expect(deleted.statusCode).toBe(204);
    for (const table of [
      "sessions",
      "participants",
      "transmissions",
      "raw_inputs",
      "extraction_jobs",
      "observations",
      "resolutions",
      "events",
      "token_issuances",
    ]) {
      expect(
        runtime.storage.one<{ count: number }>(
          `SELECT COUNT(*) AS count FROM ${table}`,
        )?.count,
        table,
      ).toBe(0);
    }
  });

  it("does not issue an STT token when server credentials are absent", async () => {
    const context = await joinedContext();
    const transmission = await reserve(context.participantCookie);
    const response = await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/token`,
      headers: { cookie: context.participantCookie },
    });
    expect(response.statusCode).toBe(503);
    expect(response.body).not.toContain("ELEVENLABS");
  });

  it("issues provider tokens server-side and applies the durable rate limit", async () => {
    await runtime.app.close();
    const providerFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ token: "single-use-browser-token" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", providerFetch);
    runtime = await buildApi(
      loadConfig({
        databasePath: ":memory:",
        sessionSigningSecret: "test-signing-secret-that-is-long-enough",
        presenterApiKey: presenterKey,
        appOrigin: "http://localhost:5173",
        cookieSecure: false,
        startWorker: false,
        elevenLabsApiKey: "permanent-server-secret",
        elevenLabsApiBaseUrl: "https://speech.example.test",
        tokenRequestsPerMinute: 1,
      }),
    );
    const context = await joinedContext();
    const transmission = await reserve(context.participantCookie);
    const first = await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/token`,
      headers: { cookie: context.participantCookie },
    });
    expect(first.statusCode).toBe(200);
    expect(first.json().token).toBe("single-use-browser-token");
    expect(first.headers["cache-control"]).toBe("no-store");
    expect(first.body).not.toContain("permanent-server-secret");
    expect(providerFetch).toHaveBeenCalledWith(
      "https://speech.example.test/v1/single-use-token/realtime_scribe",
      expect.objectContaining({
        method: "POST",
        headers: { "xi-api-key": "permanent-server-secret" },
      }),
    );

    const limited = await runtime.app.inject({
      method: "POST",
      url: `/api/radio/transmissions/${transmission.transmission_id}/token`,
      headers: { cookie: context.participantCookie },
    });
    expect(limited.statusCode).toBe(429);
    expect(providerFetch).toHaveBeenCalledTimes(1);
  });
});

async function createSession(): Promise<{
  response: Awaited<ReturnType<typeof runtime.app.inject>>;
  body: Record<string, string>;
  presenterCookie: string;
}> {
  const response = await runtime.app.inject({
    method: "POST",
    url: "/api/demo-sessions",
    headers: { "x-presenter-key": presenterKey },
    payload: {
      name: "Test session",
      allowed_responder_ids: ["rescue-alpha"],
    },
  });
  expect(response.statusCode).toBe(201);
  const body = response.json();
  expect(body.scenario_id).toBe(scenarioId);
  return {
    response,
    body,
    presenterCookie: cookieFrom(response.headers["set-cookie"]),
  };
}

async function joinSession(joinCode: string): Promise<{
  response: Awaited<ReturnType<typeof runtime.app.inject>>;
  body: Record<string, string>;
  participantCookie: string;
}> {
  const response = await runtime.app.inject({
    method: "POST",
    url: "/api/demo-sessions/join",
    payload: { join_code: joinCode, responder_id: "rescue-alpha" },
  });
  return {
    response,
    body: response.json(),
    participantCookie: cookieFrom(response.headers["set-cookie"]),
  };
}

async function joinedContext(): Promise<{
  sessionId: string;
  presenterCookie: string;
  participantCookie: string;
}> {
  const session = await createSession();
  const joined = await joinSession(session.body.join_code);
  return {
    sessionId: session.body.session_id,
    presenterCookie: session.presenterCookie,
    participantCookie: joined.participantCookie,
  };
}

async function reserve(cookie: string): Promise<{ transmission_id: string }> {
  const response = await runtime.app.inject({
    method: "POST",
    url: "/api/radio/transmissions",
    headers: { cookie },
    payload: { capture_mode: "live_microphone", channel: "command-1" },
  });
  expect(response.statusCode).toBe(201);
  return response.json();
}

function finalPayload(transmissionId: string): {
  schema_version: "2.0";
  transmission_id: string;
  transcript: string;
  capture_started_at: string;
  capture_ended_at: string;
  segments: Array<{
    sequence: number;
    text: string;
    started_at_ms: number;
    ended_at_ms: number;
  }>;
} {
  return {
    schema_version: "2.0",
    transmission_id: transmissionId,
    transcript:
      "Command, partial collapse near Riverside Apartments. Multiple voices heard. East access may be blocked.",
    capture_started_at: "2026-09-19T14:02:00.000Z",
    capture_ended_at: "2026-09-19T14:02:06.000Z",
    segments: [
      {
        sequence: 0,
        text: "Command, partial collapse near Riverside Apartments.",
        started_at_ms: 0,
        ended_at_ms: 3000,
      },
      {
        sequence: 1,
        text: "Multiple voices heard. East access may be blocked.",
        started_at_ms: 3000,
        ended_at_ms: 6000,
      },
    ],
  };
}

function cookieFrom(value: string | string[] | undefined): string {
  const header = Array.isArray(value) ? value[0] : value;
  if (!header) throw new Error("Expected set-cookie header");
  return header.split(";", 1)[0]!;
}

function createContractValidators(): {
  finalize: ValidateFunction;
  transmission: ValidateFunction;
  snapshot: ValidateFunction;
  event: ValidateFunction;
} {
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  addFormats(ajv);
  const directory = fileURLToPath(
    new URL("../../../contracts/", import.meta.url),
  );
  for (const name of [
    "common.schema.json",
    "location_resolution.schema.json",
    "radio_finalize_request.schema.json",
    "radio_finalize_response.schema.json",
    "radio_extraction_result.schema.json",
    "radio_transmission.schema.json",
    "radio_session_snapshot.schema.json",
    "dashboard_event.schema.json",
  ]) {
    ajv.addSchema(JSON.parse(readFileSync(`${directory}${name}`, "utf8")));
  }
  const validator = (name: string): ValidateFunction => {
    const result = ajv.getSchema(`https://aftershock.local/contracts/${name}`);
    if (!result) throw new Error(`Missing validator for ${name}`);
    return result;
  };
  return {
    finalize: validator("radio_finalize_response.schema.json"),
    transmission: validator("radio_transmission.schema.json"),
    snapshot: validator("radio_session_snapshot.schema.json"),
    event: validator("dashboard_event.schema.json"),
  };
}
