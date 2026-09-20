import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import type { BeliefSnapshot } from "../src/operational-bridge.js";
import type { IntelligenceSnapshot } from "../../../contracts/intelligence.js";

const presenterKey = "lifecycle-test";
const W1 = "osm:way:1533952351";
let runtime: ApiRuntime;

beforeEach(async () => {
  process.env.NODE_ENV = "test";
  runtime = await buildApi(
    loadConfig({
      databasePath: ":memory:",
      presenterApiKey: presenterKey,
      startWorker: false,
      llmApiKey: undefined,
      llmModel: undefined,
      sessionSigningSecret: "test-signing-secret-that-is-long-enough",
    }),
  );
});

afterEach(async () => {
  await runtime.app.close();
});

describe("demo reset and start lifecycle", () => {
  it("reset clears belief, search, drone, and pack replay for only that session", async () => {
    const left = await session();
    const right = await session();
    expect((await start(left, "immediate")).statusCode).toBe(202);
    expect((await start(right, "immediate")).statusCode).toBe(202);
    await drainWorker();
    expect((await belief(left)).worksites.length).toBeGreaterThan(0);
    expect((await belief(right)).worksites.length).toBeGreaterThan(0);

    const first = await reset(left);
    expect(first.statusCode, first.body).toBe(200);
    expect(first.json().reset).toBe(true);
    expect(first.json().belief.state_version).toBe("0");
    expect(first.json().belief.worksites).toEqual([]);
    expect(first.json().search_initialized).toBe(false);
    expect(first.json().save_initialized).toBe(false);
    expect((await belief(left)).state_version).toBe("0");
    expect((await intelligence(left)).reports).toEqual([]);
    expect(runtime.storage.one("SELECT 1 FROM operational_belief_sessions WHERE session_id=?", left.id)).toBeFalsy();
    expect(runtime.storage.one("SELECT 1 FROM drone_search_sessions WHERE session_id=?", left.id)).toBeFalsy();
    expect(runtime.storage.one("SELECT 1 FROM save_planner_sessions WHERE session_id=?", left.id)).toBeFalsy();
    expect(runtime.storage.one("SELECT 1 FROM transmissions WHERE session_id=?", left.id)).toBeFalsy();
    expect(runtime.storage.one("SELECT 1 FROM external_reports WHERE session_id=?", left.id)).toBeFalsy();
    expect(runtime.storage.one("SELECT 1 FROM sessions WHERE id=?", left.id)).toBeTruthy();
    expect(runtime.storage.one("SELECT 1 FROM participants WHERE session_id=?", left.id)).toBeTruthy();

    const again = await reset(left);
    expect(again.statusCode).toBe(200);
    expect(again.json().belief.state_version).toBe("0");
    expect((await belief(right)).worksites.length).toBeGreaterThan(0);
    expect((await intelligence(right)).reports.length).toBeGreaterThan(0);
  });

  it("start after reset plays caraballeda-demo and a second run works", async () => {
    const s = await session();
    expect((await start(s, "immediate")).statusCode).toBe(202);
    await drainWorker();
    const first = await intelligence(s);
    expect(first.reports.some((item) => item.source === "social" && item.text.includes("Residencias Caribe"))).toBe(true);
    expect(first.reports.some((item) => item.source === "radio")).toBe(true);
    expect(first.reports.some((item) => item.source === "drone")).toBe(false);
    const afterFirst = await belief(s);
    expect(worksiteSiteIds(afterFirst)).toContain(W1);
    const trapped = incidentsOf(afterFirst, "trapped_victims").filter((item) => item.site_id === W1);
    expect(trapped[0]?.verification_status).toBe("corroborated");

    expect((await reset(s)).statusCode).toBe(200);
    expect((await belief(s)).state_version).toBe("0");
    expect((await start(s, "immediate")).statusCode).toBe(202);
    await drainWorker();
    const second = await belief(s);
    expect(worksiteSiteIds(second)).toContain(W1);
    expect(incidentsOf(second, "trapped_victims").filter((item) => item.site_id === W1)[0]?.verification_status).toBe("corroborated");
    expect(Number(second.state_version)).toBeGreaterThan(0);
  });

  it("crosses API pack replay, operational_belief_sessions, Search, and drone feedback", async () => {
    const s = await session();
    expect((await reset(s)).statusCode).toBe(200);
    expect((await start(s, "immediate")).statusCode).toBe(202);
    await drainWorker();
    const afterPack = await belief(s);
    expect(worksiteSiteIds(afterPack)).toContain(W1);
    expect(incidentsOf(afterPack, "trapped_victims").find((item) => item.site_id === W1)?.verification_status).toBe("corroborated");

    const search = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/search/tick`,
      headers: { cookie: s.cookie },
      payload: {},
    });
    expect(search.statusCode, search.body).toBe(200);
    expect(search.json().initialized).toBe(true);
    expect(search.json().sync_action).toBe("initialize");
    expect(search.json().missions.some((m: { site_id: string }) => m.site_id === W1)).toBe(true);
    expect(JSON.parse(runtime.storage.one<{ record_json: string }>(
      "SELECT record_json FROM drone_search_sessions WHERE session_id=?", s.id)!.record_json)
      .operational_runtime).toBeUndefined();

    const sameTick = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/search/tick`,
      headers: { cookie: s.cookie },
      payload: {},
    });
    expect(sameTick.json().sync_action).toBe("noop");

    const rumor = incidentsOf(afterPack, "trapped_victims").find(
      (item) => item.site_id === "osm:node:13976280529",
    );
    expect(rumor?.verification_status).not.toBe("corroborated");
    expect(runtime.storage.one("SELECT 1 FROM operational_belief_sessions WHERE session_id=?", s.id)).toBeTruthy();
  });
});

async function session() {
  const response = await runtime.app.inject({
    method: "POST",
    url: "/api/demo-sessions",
    headers: { "x-presenter-key": presenterKey },
    payload: {
      scenario_id: "caraballeda",
      name: "Lifecycle test",
      allowed_responder_ids: ["rescue-alpha"],
    },
  });
  expect(response.statusCode).toBe(201);
  return {
    id: response.json().session_id as string,
    cookie: response.cookies.map((item) => `${item.name}=${item.value}`).join("; "),
  };
}

async function drainWorker() {
  for (let i = 0; i < 10; i += 1) {
    if (!(await runtime.worker.runOnce())) break;
  }
}

function reset(s: { id: string; cookie: string }) {
  return runtime.app.inject({
    method: "POST",
    url: `/api/demo-sessions/${s.id}/reset`,
    headers: { cookie: s.cookie },
    payload: {},
  });
}

function start(s: { id: string; cookie: string }, play: "immediate" | "timed") {
  return runtime.app.inject({
    method: "POST",
    url: `/api/demo-sessions/${s.id}/start`,
    headers: { cookie: s.cookie },
    payload: { play },
  });
}

async function belief(s: { id: string; cookie: string }) {
  const response = await runtime.app.inject({
    url: `/api/demo-sessions/${s.id}/belief-state`,
    headers: { cookie: s.cookie },
  });
  expect(response.statusCode, response.body).toBe(200);
  return response.json<BeliefSnapshot>();
}

async function intelligence(s: { id: string; cookie: string }) {
  const response = await runtime.app.inject({
    url: `/api/demo-sessions/${s.id}/intelligence`,
    headers: { cookie: s.cookie },
  });
  expect(response.statusCode, response.body).toBe(200);
  return response.json<IntelligenceSnapshot>();
}

function worksiteSiteIds(snapshot: BeliefSnapshot) {
  return snapshot.worksites.map((item) => (item as { site_id: string }).site_id);
}

function incidentsOf(snapshot: BeliefSnapshot, incidentType: string) {
  return (
    snapshot.incidents as Array<{
      incident_type: string;
      site_id: string;
      verification_status: string;
    }>
  ).filter((item) => item.incident_type === incidentType);
}
