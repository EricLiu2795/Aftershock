import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import {
  clearOperationalResetHandlers,
  registerOperationalReset,
  resetOperationalState,
} from "../src/operational-reset.js";
import {
  FIRST_DEMO_RADIO_EVENT,
  FIRST_DEMO_SOCIAL_EVENT,
  loadCaraballedaPackEvents,
} from "../src/demo-lifecycle.js";
import { clearPackReplayApplicator, packReplayStatus, resetPackReplayClock, setPackReplayClock } from "../src/pack-replay.js";
import type { IntelligenceSnapshot } from "../../../contracts/intelligence.js";

let runtime: ApiRuntime;

beforeEach(async () => {
  process.env.NODE_ENV = "test";
  clearOperationalResetHandlers();
  clearPackReplayApplicator();
  runtime = await buildApi(
    loadConfig({
      databasePath: ":memory:",
      presenterApiKey: "demo-test",
      startWorker: false,
      llmApiKey: undefined,
      llmModel: undefined,
      sessionSigningSecret: "test-signing-secret-that-is-long-enough",
    }),
  );
});

afterEach(async () => {
  clearOperationalResetHandlers();
  clearPackReplayApplicator();
  resetPackReplayClock();
  await runtime.app.close();
});

async function session(name = "Demo test") {
  const response = await runtime.app.inject({
    method: "POST",
    url: "/api/demo-sessions",
    headers: { "x-presenter-key": "demo-test" },
    payload: {
      scenario_id: "caraballeda",
      name,
      allowed_responder_ids: ["alpha"],
    },
  });
  return {
    id: response.json().session_id as string,
    cookie: response.cookies.map((item) => `${item.name}=${item.value}`).join("; "),
  };
}

function headers(s: { cookie: string }) {
  return { cookie: s.cookie };
}

async function start(s: { id: string; cookie: string }) {
  return runtime.app.inject({
    method: "POST",
    url: `/api/demo-sessions/${s.id}/start`,
    headers: headers(s),
    payload: {},
  });
}

async function reset(s: { id: string; cookie: string }) {
  return runtime.app.inject({
    method: "POST",
    url: `/api/demo-sessions/${s.id}/reset`,
    headers: headers(s),
    payload: {},
  });
}

async function intelligence(s: { id: string; cookie: string }) {
  return (
    await runtime.app.inject({
      url: `/api/demo-sessions/${s.id}/intelligence`,
      headers: headers(s),
    })
  ).json<IntelligenceSnapshot>();
}

async function searchTick(s: { id: string; cookie: string }) {
  return runtime.app.inject({
    method: "POST",
    url: `/api/demo-sessions/${s.id}/search/tick`,
    headers: headers(s),
    payload: {},
  });
}

function searchState() {
  return JSON.parse(
    readFileSync(
      new URL("../../../data/search_demo/input_state.json", import.meta.url),
      "utf8",
    ),
  );
}

async function seedSearch(s: { id: string; cookie: string }) {
  return runtime.app.inject({
    method: "POST",
    url: `/api/demo-sessions/${s.id}/search/initialize`,
    headers: headers(s),
    payload: { state: searchState() },
  });
}

function counts(sessionId: string) {
  const observationCount =
    runtime.storage.one<{ n: number }>(
      `SELECT COUNT(*) AS n FROM observations o
       JOIN transmissions t ON t.id = o.transmission_id
       WHERE t.session_id = ?`,
      sessionId,
    )?.n ?? 0;
  return {
    reports: runtime.storage.all(
      "SELECT * FROM transmissions WHERE session_id = ?",
      sessionId,
    ).length +
      runtime.storage.all(
        "SELECT * FROM external_reports WHERE session_id = ?",
        sessionId,
      ).length,
    observations: observationCount,
    search: runtime.storage.one(
      "SELECT 1 FROM drone_search_sessions WHERE session_id = ?",
      sessionId,
    )
      ? 1
      : 0,
    droneReports:
      runtime.storage.one<{ n: number }>(
        "SELECT COUNT(*) AS n FROM drone_reports WHERE session_id = ?",
        sessionId,
      )?.n ?? 0,
    replayEvents: runtime.storage.listReplayEvents(sessionId),
  };
}

describe("demo reset and start lifecycle", () => {
  it("A: start, create state, reset empties reports, observations, Search, and drone missions", async () => {
    const s = await session();
    const started = await start(s);
    expect(started.statusCode, started.body).toBe(200);
    expect(started.json().started).toBe(true);
    expect(started.json().replay.event_ids).toEqual([
      FIRST_DEMO_RADIO_EVENT,
      FIRST_DEMO_SOCIAL_EVENT,
    ]);
    await runtime.worker.runOnce();
    const seeded = await seedSearch(s);
    expect(seeded.statusCode, seeded.body).toBe(200);
    expect((await searchTick(s)).json().initialized).toBe(true);
    expect((await intelligence(s)).reports.length).toBe(8);
    expect(counts(s.id).observations).toBeGreaterThan(0);
    expect(counts(s.id).search).toBe(1);

    const cleared = await reset(s);
    expect(cleared.statusCode).toBe(200);
    expect(cleared.json().reset).toBe(true);
    expect(cleared.json().operational.state_version).toBe("0");
    expect((await intelligence(s)).reports).toEqual([]);
    expect(counts(s.id)).toMatchObject({
      reports: 0,
      observations: 0,
      search: 0,
      droneReports: 0,
      replayEvents: [],
    });
    expect((await searchTick(s)).json()).toMatchObject({
      initialized: false,
      missions: [],
      proposals: [],
    });
    const snapshot = await runtime.app.inject({
      url: `/api/demo-sessions/${s.id}/snapshot`,
      headers: headers(s),
    });
    expect(snapshot.statusCode).toBe(200);
    expect(snapshot.json().transmissions).toEqual([]);
  });

  it("B: reset twice succeeds and yields the same clean result", async () => {
    const s = await session();
    expect((await start(s)).statusCode).toBe(200);
    await runtime.worker.runOnce();
    expect((await seedSearch(s)).statusCode).toBe(200);
    const first = await reset(s);
    const second = await reset(s);
    expect(first.statusCode).toBe(200);
    expect(second.statusCode).toBe(200);
    expect(counts(s.id)).toEqual(counts(s.id));
    expect(counts(s.id)).toMatchObject({
      reports: 0,
      observations: 0,
      search: 0,
      droneReports: 0,
      replayEvents: [],
    });
    expect((await intelligence(s)).reports).toHaveLength(0);
    expect((await searchTick(s)).json().initialized).toBe(false);
  });

  it("C: resetting session A does not delete session B", async () => {
    const a = await session("Session A");
    const b = await session("Session B");
    expect((await start(a)).statusCode).toBe(200);
    expect((await start(b)).statusCode).toBe(200);
    await runtime.worker.runOnce();
    await runtime.worker.runOnce();
    expect((await seedSearch(a)).statusCode).toBe(200);
    expect((await seedSearch(b)).statusCode).toBe(200);
    expect((await reset(a)).statusCode).toBe(200);
    expect((await intelligence(a)).reports).toHaveLength(0);
    expect(counts(a.id).search).toBe(0);
    expect((await intelligence(b)).reports).toHaveLength(8);
    expect(counts(b.id).search).toBe(1);
    expect((await searchTick(b)).json().initialized).toBe(true);
    expect(
      (
        await runtime.app.inject({
          method: "POST",
          url: `/api/demo-sessions/${b.id}/reset`,
          headers: headers(a),
          payload: {},
        })
      ).statusCode,
    ).toBe(403);
  });

  it("D: after reset, start produces one copy of each initial event", async () => {
    const s = await session();
    expect((await start(s)).statusCode).toBe(200);
    await runtime.worker.runOnce();
    expect((await intelligence(s)).reports).toHaveLength(8);
    expect((await start(s)).json().already_running).toBe(false);
    await runtime.worker.runOnce();
    expect((await intelligence(s)).reports).toHaveLength(8);
    expect(runtime.storage.listReplayEvents(s.id)).toEqual([
      FIRST_DEMO_RADIO_EVENT,
      FIRST_DEMO_SOCIAL_EVENT,
    ]);
    expect((await reset(s)).statusCode).toBe(200);
    const restarted = await start(s);
    expect(restarted.statusCode).toBe(200);
    expect(restarted.json().started).toBe(true);
    expect(restarted.json().replay.event_ids).toEqual([
      FIRST_DEMO_RADIO_EVENT,
      FIRST_DEMO_SOCIAL_EVENT,
    ]);
    await runtime.worker.runOnce();
    const reports = (await intelligence(s)).reports;
    expect(reports).toHaveLength(8);
    expect(reports.filter((item) => item.source === "radio")).toHaveLength(1);
    expect(reports.filter((item) => item.source === "social")).toHaveLength(7);
  });

  it("E: OperationalBeliefState reset hook clears Agent 1 persistence when present", async () => {
    const s = await session();
    runtime.storage.db.exec(`
      CREATE TABLE operational_belief_states (
        session_id TEXT PRIMARY KEY,
        state_version TEXT NOT NULL,
        belief_json TEXT NOT NULL
      );
    `);
    runtime.storage.run(
      "INSERT INTO operational_belief_states(session_id, state_version, belief_json) VALUES (?, ?, ?)",
      s.id,
      "3",
      "{\"state_version\":\"3\"}",
    );
    const seen: string[] = [];
    registerOperationalReset((_storage, sessionId) => {
      seen.push(sessionId);
    });
    expect((await start(s)).statusCode).toBe(200);
    const other = await session("Other");
    runtime.storage.run(
      "INSERT INTO operational_belief_states(session_id, state_version, belief_json) VALUES (?, ?, ?)",
      other.id,
      "9",
      "{\"state_version\":\"9\"}",
    );
    const result = await reset(s);
    expect(result.statusCode).toBe(200);
    expect(result.json().operational.tables_cleared).toEqual([
      "operational_belief_states",
    ]);
    expect(result.json().operational.handlers).toBe(1);
    expect(result.json().operational.state_version).toBe("0");
    expect(seen).toEqual([s.id]);
    expect(
      runtime.storage.one(
        "SELECT 1 FROM operational_belief_states WHERE session_id = ?",
        s.id,
      ),
    ).toBeUndefined();
    expect(
      runtime.storage.one<{ state_version: string }>(
        "SELECT state_version FROM operational_belief_states WHERE session_id = ?",
        other.id,
      )?.state_version,
    ).toBe("9");
    expect(
      resetOperationalState(runtime.storage, s.id).tables_cleared,
    ).toEqual(["operational_belief_states"]);
  });

  it("does not seed Search with the Los Angeles sample on start", async () => {
    const s = await session();
    expect((await start(s)).statusCode).toBe(200);
    expect((await searchTick(s)).json().initialized).toBe(false);
    expect(counts(s.id).search).toBe(0);
  });

  it("keeps presenter cookies after reset so Start can reuse the session", async () => {
    const s = await session();
    expect((await reset(s)).statusCode).toBe(200);
    expect((await start(s)).statusCode).toBe(200);
    expect((await intelligence(s)).reports.length).toBeGreaterThan(0);
  });

  it("exposes an Agent 2 pack-replay hook that records each pack event once", async () => {
    const timers: Array<{ due: number; callback: () => void; id: NodeJS.Timeout }> =
      [];
    let now = 0;
    let seq = 1;
    setPackReplayClock({
      now: () => now,
      setTimeout(callback, ms) {
        const id = seq++ as unknown as NodeJS.Timeout;
        timers.push({ due: now + ms, callback, id });
        return Object.assign(id, { unref() {} }) as NodeJS.Timeout;
      },
      clearTimeout(id) {
        const index = timers.findIndex((timer) => timer.id === id);
        if (index >= 0) timers.splice(index, 1);
      },
    });
    const applied: string[] = [];
    const s = await session();
    const expected = loadCaraballedaPackEvents().map((event) => event.event_id);
    const first = await runtime.demo.startCaraballedaPackReplay(
      s.id,
      async (_sessionId, event) => {
        applied.push(event.event_id);
      },
    );
    expect(first.started).toBe(true);
    now = 300_000;
    for (const timer of [...timers]) timer.callback();
    for (let i = 0; i < 50 && packReplayStatus(s.id).status === "running"; i++) {
      await Promise.resolve();
    }
    expect(packReplayStatus(s.id).status).toBe("completed");
    expect(applied).toEqual(expected);
    expect(new Set(applied).size).toBe(expected.length);

    applied.length = 0;
    timers.length = 0;
    const again = await runtime.demo.startCaraballedaPackReplay(
      s.id,
      async (_sessionId, event) => {
        applied.push(event.event_id);
      },
    );
    now = 300_000;
    for (const timer of [...timers]) timer.callback();
    for (let i = 0; i < 50 && packReplayStatus(s.id).status === "running"; i++) {
      await Promise.resolve();
    }
    expect(again.started).toBe(true);
    expect(applied).toEqual([]);

    runtime.demo.reset(s.id);
    applied.length = 0;
    timers.length = 0;
    const restarted = await runtime.demo.startCaraballedaPackReplay(
      s.id,
      async (_sessionId, event) => {
        applied.push(event.event_id);
      },
    );
    now = 300_000;
    for (const timer of [...timers]) timer.callback();
    for (let i = 0; i < 50 && packReplayStatus(s.id).status === "running"; i++) {
      await Promise.resolve();
    }
    expect(restarted.started).toBe(true);
    expect(applied).toEqual(expected);
    resetPackReplayClock();
  });
});
