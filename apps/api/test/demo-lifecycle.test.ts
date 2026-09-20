import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import { DEMO_OMITTED, DEMO_STEPS } from "../src/scenario-pack.js";
import type { BeliefSnapshot } from "../src/operational-bridge.js";
import type { IntelligenceSnapshot } from "../../../contracts/intelligence.js";

const presenterKey = "lifecycle-test";
const W1 = "osm:way:1533952351";
const W7 = "osm:node:13976280529";
const packEvents = JSON.parse(
  readFileSync(new URL("../../../data/scenarios/caraballeda/scenario_events.json", import.meta.url), "utf8"),
) as { events: Array<{ event_id: string; raw_input: { text?: string } }> };
const E01_TEXT = packEvents.events.find((event) => event.event_id === "E01")!.raw_input.text!;
const CANONICAL_SITES = (
  JSON.parse(
    readFileSync(new URL("../../../data/scenarios/caraballeda/active_sites.json", import.meta.url), "utf8"),
  ) as { active_worksites: Array<{ site: { site_id: string } }> }
).active_worksites.map((entry) => entry.site.site_id);
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

    runtime.storage.run("INSERT INTO drone_road_assessments VALUES (?, ?, ?, ?, ?)", left.id, "det-1", "road-1", "2026-09-19T14:00:00Z", "{}");
    runtime.storage.run("INSERT INTO drone_road_states VALUES (?, ?, ?)", left.id, "road-1", "{}");

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
    expect(runtime.storage.one("SELECT 1 FROM drone_road_assessments WHERE session_id=?", left.id)).toBeFalsy();
    expect(runtime.storage.one("SELECT 1 FROM drone_road_states WHERE session_id=?", left.id)).toBeFalsy();
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
    expect(first.reports.find((item) => item.text === E01_TEXT)?.locations[0]?.site.id).toBe(W1);
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
    const targets = search.json().state.worksites.map((w: { site_id: string }) => w.site_id);
    expect(targets).toContain(W1);
    expect(search.json().missions.length).toBeGreaterThan(0);
    expect(search.json().missions.every((m: { site_id: string }) => targets.includes(m.site_id))).toBe(true);
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
    // JSON pipes must stay UTF-8 on Windows, including repeated Python round trips.
    for (const tick of [search, sameTick]) {
      expect(tick.json().state.sites.find((site: { site_id: string }) => site.site_id === W7)
        .metadata.osm_tags["addr:street"]).toBe("Av. José María España");
    }

    const rumor = incidentsOf(afterPack, "trapped_victims").find(
      (item) => item.site_id === "osm:node:13976280529",
    );
    expect(rumor?.verification_status).not.toBe("corroborated");
    expect(runtime.storage.one("SELECT 1 FROM operational_belief_sessions WHERE session_id=?", s.id)).toBeTruthy();
  });
});

describe("unified Start Demo ingestion", () => {
  it("produces the whole stream from one call without duplicating W1 or W7 evidence", async () => {
    const s = await session();
    const response = await start(s, "immediate");
    expect(response.statusCode, response.body).toBe(202);
    expect(response.json()).toMatchObject({
      schema_version: "2.1",
      session_id: s.id,
      started: true,
      already_running: false,
      story: "caraballeda-demo",
      play: "immediate",
    });
    expect(response.json().events).toEqual(DEMO_STEPS);
    expect(response.json().omitted).toEqual(DEMO_OMITTED);
    expect(response.json().social_backfill).toMatchObject({ imported: 6, released: 6, pending: 0 });
    await drainWorker();

    const snapshot = await intelligence(s);
    const social = snapshot.reports.filter((item) => item.source === "social");
    expect(social).toHaveLength(8);
    expect(snapshot.reports.filter((item) => item.source === "radio")).toHaveLength(1);
    expect(new Set(social.map((item) => item.author)).size).toBe(8);
    expect(social.filter((item) => item.mode === "scenario_pack")).toHaveLength(2);
    expect(social.some((item) => item.author === "simulated-witness-W1")).toBe(false);
    expect(social.some((item) => item.author === "simulated-witness-W7")).toBe(false);
    expect(snapshot.pendingReports).toBe(0);

    const after = await belief(s);
    expect(worksiteSiteIds(after).sort()).toEqual([...CANONICAL_SITES].sort());
    expect(JSON.stringify(after)).not.toContain("simulated-witness-W1");
    expect(JSON.stringify(after)).not.toContain("simulated-witness-W7");
    const w1 = incidentsOf(after, "trapped_victims").filter((item) => item.site_id === W1);
    expect(w1).toHaveLength(1);
    expect(w1[0]?.verification_status).toBe("corroborated");
    const w7 = incidentsOf(after, "trapped_victims").filter((item) => item.site_id === W7);
    expect(w7).toHaveLength(1);
    expect(w7[0]?.verification_status).not.toBe("corroborated");

    // The developer import stays available and still cannot re-report W1 or W7.
    const manual = await runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/ingest`,
      headers: { cookie: s.cookie }, payload: { source: "replay", provider: "fixture" } });
    expect(manual.statusCode, manual.body).toBe(200);
    expect(manual.json()).toMatchObject({ imported: 0, pending: 0 });
    expect((await intelligence(s)).reports).toHaveLength(9);
  });

  it("staggers the extra building reports behind the pack narrative and promotes them unattended", async () => {
    await runtime.app.close();
    runtime = await buildApi(loadConfig({ databasePath: ":memory:", presenterApiKey: presenterKey, startWorker: false,
      llmApiKey: undefined, llmModel: undefined, replayReleaseIntervalS: 2,
      sessionSigningSecret: "test-signing-secret-that-is-long-enough" }));
    const s = await session();
    const response = await start(s, "timed");
    expect(response.statusCode, response.body).toBe(202);
    expect(response.json().social_backfill).toMatchObject({ imported: 6, released: 0, pending: 6, interval_s: 2 });
    expect(runtime.storage.all("SELECT 1 FROM external_reports WHERE session_id=? AND release_offset_s > 0", s.id)).toHaveLength(6);

    const opening = await waitFor(async () => {
      const item = await intelligence(s);
      return item.reports.length ? item : undefined;
    });
    expect(opening.reports.some((item) => item.text === E01_TEXT)).toBe(true);
    expect(opening.pendingReports).toBe(6);

    const complete = await waitFor(async () => {
      const item = await belief(s);
      return item.worksites.length === CANONICAL_SITES.length ? item : undefined;
    }, 20_000);
    expect(worksiteSiteIds(complete).sort()).toEqual([...CANONICAL_SITES].sort());
    expect((await intelligence(s)).pendingReports).toBe(0);
  }, 30_000);
});

async function waitFor<T>(read: () => Promise<T | undefined>, timeoutMs = 5_000): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await read();
    if (value !== undefined) return value;
    if (Date.now() > deadline) throw new Error("timed out waiting for the demo stream");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

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
