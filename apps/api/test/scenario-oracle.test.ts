import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import { scanFoundPeople, scenarioBuildings } from "../src/scenario-buildings.js";
import { storedInspectionInputs } from "../src/drone-reports.js";
import type { BeliefSnapshot } from "../src/operational-bridge.js";
import type { IntelligenceSnapshot } from "../../../contracts/intelligence.js";

const presenterKey = "oracle-test";
const load = (path: string) => JSON.parse(readFileSync(new URL(`../../../${path}`, import.meta.url), "utf8"));
const siteOf = (key: string) =>
  load("data/scenarios/caraballeda/active_sites.json").active_worksites.find((w: { worksite_key: string }) => w.worksite_key === key).site;
const W1 = siteOf("W1").site_id;
const W7 = siteOf("W7").site_id;
let runtime: ApiRuntime;

beforeEach(async () => {
  process.env.NODE_ENV = "test";
  runtime = await buildApi(loadConfig({ databasePath: ":memory:", presenterApiKey: presenterKey, startWorker: false,
    llmApiKey: undefined, llmModel: undefined, replayReleaseIntervalS: 0,
    sessionSigningSecret: "test-signing-secret-that-is-long-enough" }));
});
afterEach(async () => {
  await runtime.app.close();
});

async function session() {
  const response = await runtime.app.inject({ method: "POST", url: "/api/demo-sessions", headers: { "x-presenter-key": presenterKey },
    payload: { scenario_id: "caraballeda", name: "Oracle test", allowed_responder_ids: ["rescue-alpha"] } });
  return { id: response.json().session_id as string, cookie: response.cookies.map(c => `${c.name}=${c.value}`).join("; ") };
}

function command(s: { id: string; cookie: string }, cmd: string, payload = {}) {
  return runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/search/${cmd}`, headers: { cookie: s.cookie }, payload });
}

function photo(s: { id: string; cookie: string }, siteId: string) {
  return runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/buildings/${encodeURIComponent(siteId)}/photo`,
    headers: { cookie: s.cookie } });
}

async function feed(s: { id: string; cookie: string }) {
  const response = await runtime.app.inject({ url: `/api/demo-sessions/${s.id}/intelligence`, headers: { cookie: s.cookie } });
  expect(response.statusCode, response.body).toBe(200);
  return response.json<IntelligenceSnapshot>();
}

async function belief(s: { id: string; cookie: string }) {
  const response = await runtime.app.inject({ url: `/api/demo-sessions/${s.id}/belief-state`, headers: { cookie: s.cookie } });
  expect(response.statusCode, response.body).toBe(200);
  return response.json<BeliefSnapshot>();
}

function start(s: { id: string; cookie: string }) {
  return runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/start`, headers: { cookie: s.cookie },
    payload: { play: "immediate" } });
}

async function drainWorker() {
  for (let i = 0; i < 10; i += 1) {
    if (!(await runtime.worker.runOnce())) break;
  }
}

/** Queue two already located targets so one dispatch round scans a red and a green building. */
function locateTargets(sessionId: string, keys = ["W1", "W7"]) {
  for (const key of keys) {
    const site = siteOf(key);
    const observation = load("contracts/examples/structured_social_observation.json");
    observation.observation_id = `oracle-${key}`;
    observation.location = { latitude: site.centroid.latitude, longitude: site.centroid.longitude, resolved_site_id: site.site_id };
    observation.incident_type = "trapped_victims";
    observation.victim_indication = { type: "possible_multiple" };
    runtime.storage.run("INSERT INTO external_reports(session_id, report_id, payload_json) VALUES(?,?,?)", sessionId, `oracle-${key}`,
      JSON.stringify({ id: `oracle-${key}`, source: "social", author: "test", timestamp: "2026-09-19T14:00:00Z",
        text: "Possible trapped people", mode: "replay", model: "fixture", sourceUrl: null, observations: [observation] }));
    runtime.storage.run("INSERT INTO report_locations VALUES(?,?,?,?)", sessionId, `oracle-${key}`, site.site_id, "2026-09-19T14:00:00Z");
  }
}

function elapse(sessionId: string, seconds: number) {
  const row = runtime.storage.one<{ record_json: string }>("SELECT record_json FROM drone_search_sessions WHERE session_id=?", sessionId)!;
  const record = JSON.parse(row.record_json);
  record.last_wall_at = new Date(Date.now() - seconds * 1000).toISOString();
  runtime.storage.run("UPDATE drone_search_sessions SET record_json=? WHERE session_id=?", JSON.stringify(record), sessionId);
}

async function scanTargets(s: { id: string; cookie: string }) {
  expect((await command(s, "prepare")).statusCode).toBe(200);
  locateTargets(s.id);
  expect((await command(s, "dispatch")).statusCode).toBe(200);
  for (let i = 0; i < 4; i++) { elapse(s.id, 600); expect((await command(s, "tick")).statusCode).toBe(200); }
  return (await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/reports`, headers: { cookie: s.cookie } }))
    .json().reports as Array<{ site_id: string; report: { victim_indicator: { type: string; count_min: number | null }; structural_damage: string } }>;
}

describe("scenario building oracle", () => {
  it("withholds hidden building truth from every surface until a drone scans", async () => {
    const s = await session();
    expect((await start(s)).statusCode).toBe(202);
    await drainWorker();
    const oracle = scenarioBuildings(runtime.storage, "caraballeda");
    expect(oracle).toHaveLength(8);
    expect(oracle.find(row => row.site_id === W1)!.people_count).toBe(6);

    const after = await belief(s);
    expect(after.worksites.length).toBeGreaterThan(0);
    const blob = JSON.stringify(after);
    expect(blob).not.toContain("scenario_ground_truth");
    expect(blob).not.toContain("people_trapped");
    expect(blob).not.toContain("gt-W1");
    // W1's reported victim envelope comes from the radio call, never from the oracle's six.
    const trapped = (after.incidents as Array<{ site_id: string; incident_type: string;
      victim_state?: { count_min: number | null; count_max: number | null } }>)
      .find(item => item.site_id === W1 && item.incident_type === "trapped_victims")!;
    expect(trapped.victim_state?.count_min).toBe(4);
    expect(trapped.victim_state?.count_max).toBe(4);

    expect((await feed(s)).reports.some(item => item.inspection)).toBe(false);
    for (const row of oracle) {
      expect(scanFoundPeople(runtime.storage, s.id, row.site_id)).toBe(false);
      expect((await photo(s, row.site_id)).statusCode).toBe(404);
    }
    expect(storedInspectionInputs(runtime.storage, s.id)).toHaveLength(0);
    const listed = await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/buildings`,
      headers: { cookie: s.cookie } });
    expect(listed.json().buildings.every((b: { photo: { revealed: boolean } | null }) => !b.photo || !b.photo.revealed)).toBe(true);
  });

  it("reveals the simulated result on scan: red reports people and a photo, green reports nobody", async () => {
    const s = await session();
    const scans = await scanTargets(s);
    expect(scans.map(item => item.site_id).sort()).toEqual([W1, W7].sort());

    const red = scans.find(item => item.site_id === W1)!;
    expect(red.report.victim_indicator).toMatchObject({ type: "confirmed_multiple", count_min: 6 });
    expect(red.report.structural_damage).toBe("severe_collapse");
    expect((await photo(s, W1)).statusCode).toBe(200);
    expect((await photo(s, W1)).headers["content-type"]).toMatch(/image\//);

    const green = scans.find(item => item.site_id === W7)!;
    expect(green.report.victim_indicator).toMatchObject({ type: "none", count_min: 0 });
    expect((await photo(s, W7)).statusCode).toBe(404);

    const drones = (await feed(s)).reports.filter(item => item.source === "drone");
    expect(drones.find(item => item.locations[0]?.site.id === W1)!.inspection).toMatchObject({ people: "present", people_count: 6 });
    expect(drones.find(item => item.locations[0]?.site.id === W1)!.inspection!.images).toHaveLength(1);
    expect(drones.find(item => item.locations[0]?.site.id === W7)!.inspection).toMatchObject({ people: "none", people_count: 0 });
    expect(drones.find(item => item.locations[0]?.site.id === W7)!.inspection!.images).toEqual([]);
  }, 30000);

  it("keeps one session's scan reveal out of another session", async () => {
    const scanned = await session();
    const other = await session();
    await scanTargets(scanned);
    expect((await photo(scanned, W1)).statusCode).toBe(200);

    expect(scanFoundPeople(runtime.storage, other.id, W1)).toBe(false);
    expect((await photo(other, W1)).statusCode).toBe(404);
    expect((await feed(other)).reports).toEqual([]);
    expect((await belief(other)).state_version).toBe("0");
    const listed = await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${other.id}/search/buildings`,
      headers: { cookie: other.cookie } });
    expect(listed.json().buildings.every((b: { photo: { revealed: boolean } | null }) => !b.photo || !b.photo.revealed)).toBe(true);
  }, 30000);
});
