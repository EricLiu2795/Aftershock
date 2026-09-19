import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mediaFile } from "../src/drone-reports.js";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
let runtime: ApiRuntime;
const load = (path: string) => JSON.parse(readFileSync(new URL(`../../../${path}`, import.meta.url), "utf8"));
beforeEach(async () => {
  process.env.NODE_ENV = "test";
  runtime = await buildApi(loadConfig({ databasePath: ":memory:", presenterApiKey: "scan-test", startWorker: false,
    llmApiKey: undefined, llmModel: undefined, sessionSigningSecret: "test-signing-secret-that-is-long-enough" }));
});
afterEach(async () => { await runtime.app.close(); });
async function session() {
  const response = await runtime.app.inject({ method: "POST", url: "/api/demo-sessions", headers: { "x-presenter-key": "scan-test" },
    payload: { scenario_id: "caraballeda", name: "Scan test", allowed_responder_ids: ["alpha"] } });
  return { id: response.json().session_id as string, cookie: response.cookies.map(c => `${c.name}=${c.value}`).join("; ") };
}
function state(key: string) {
  const s = load("contracts/examples/operational_belief_state.json");
  const manifest = load("data/scenarios/caraballeda/scenario.json");
  const site = load("data/scenarios/caraballeda/active_sites.json").active_worksites.find((w: any) => w.worksite_key === key).site;
  const work = s.worksites[0];
  Object.assign(work, { worksite_id: `scan-${key}`, site_id: site.site_id, active_incident_ids: [], evidence_ids: [], assigned_resource_ids: [],
    metadata: { search_features: { potential_impact: .8, uncertainty: .8, criticality: .5, evidence_conflict: .5 } } });
  return { ...s, state_version: "scan-test", scenario_time: manifest.start_time, updated_at: manifest.start_time,
    sites: [site], worksites: [work], incidents: [], active_routes: [], pending_reassignment_proposals: [],
    resources: manifest.resources.filter((r: any) => r.resource_id === "drone-2"),
    movement_states: manifest.initial_movement_states.filter((m: any) => m.resource_id === "drone-2") };
}
async function command(s: { id: string; cookie: string }, cmd: string, payload = {}) {
  return runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/search/${cmd}`, headers: { cookie: s.cookie }, payload });
}
async function reports(s: { id: string; cookie: string }) {
  return runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/reports`, headers: { cookie: s.cookie } });
}
function elapse(sessionId: string, seconds: number) {
  const row = runtime.storage.one<{ record_json: string }>("SELECT record_json FROM drone_search_sessions WHERE session_id=?", sessionId)!;
  const record = JSON.parse(row.record_json);
  record.last_wall_at = new Date(Date.now() - seconds * 1000).toISOString();
  runtime.storage.run("UPDATE drone_search_sessions SET record_json=? WHERE session_id=?", JSON.stringify(record), sessionId);
}
describe("demo scan feedback database integration", () => {
  it("automatically stores canonical reports and image metadata only after scanning, exactly once", async () => {
    const s = await session();
    expect((await command(s, "initialize", { state: state("W2") })).statusCode).toBe(200);
    expect((await reports(s)).json().reports).toEqual([]);
    expect((await command(s, "tick")).statusCode).toBe(200);
    expect((await reports(s)).json().reports).toEqual([]);
    elapse(s.id, 600);
    expect((await command(s, "tick")).statusCode).toBe(200);
    const result = (await reports(s)).json().reports;
    expect(result).toHaveLength(1);
    expect(result[0].report.victim_indicator).toEqual({ type: "possible_multiple", count_min: null, count_max: null });
    expect(result[0].report.imagery_reference).toBe("imagery://drone-1/frame-140145");
    expect(result[0].observations).toHaveLength(3);
    expect(result[0].assets).toHaveLength(1);
    expect(result[0].assets[0]).toMatchObject({ asset_id: "va-w2-drone-oblique", file_status: "placeholder", available: false, image_url: null });
    expect((await command(s, "tick")).statusCode).toBe(200);
    expect((await reports(s)).json().reports).toEqual(result);
    expect(runtime.storage.one<{ n: number }>("SELECT count(*) AS n FROM drone_observations WHERE session_id=?", s.id)?.n).toBe(3);
    expect(runtime.storage.deleteSession(s.id)).toBe(true);
    expect(runtime.storage.one<{ n: number }>("SELECT count(*) AS n FROM drone_reports WHERE session_id=?", s.id)?.n).toBe(0);
  });
  it("preserves W7 negative findings and keeps reports private to the session", async () => {
    const s = await session(); const other = await session();
    expect((await command(s, "initialize", { state: state("W7") })).statusCode).toBe(200);
    elapse(s.id, 600);
    expect((await command(s, "tick")).statusCode).toBe(200);
    const result = (await reports(s)).json().reports[0];
    expect(result.report.victim_indicator).toEqual({ type: "none", count_min: 0, count_max: 0 });
    expect(result.observations).toHaveLength(3);
    expect(result.observations.every((o: any) => o.verification_status === "unverified")).toBe(true);
    expect((await reports({ ...s, cookie: other.cookie })).statusCode).toBe(403);
    expect((await reports({ ...s, cookie: "" })).statusCode).toBe(401);
    expect((await reports(other)).json().reports).toEqual([]);
    for (const asset of ["va-w7-drone-topdown", "va-w1-drone-oblique"]) {
      const response = await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/assets/${asset}`, headers: { cookie: s.cookie } });
      expect(response.statusCode).toBe(404);
    }
  });
  it("waits for a matching site result instead of inventing data", async () => {
    const s = await session();
    expect((await command(s, "initialize", { state: state("W1") })).statusCode).toBe(200);
    elapse(s.id, 600);
    const result = await command(s, "tick");
    expect(result.statusCode).toBe(200);
    expect(result.json().missions[0].phase).toBe("reporting");
    expect((await reports(s)).json().reports).toEqual([]);
  });
});


describe("revealed media path handling", () => {
  it("resolves a present asset but refuses placeholders, traversal and symlinks outside the pack", () => {
    const root = mkdtempSync(join(tmpdir(), "aftershock-media-"));
    try {
      mkdirSync(join(root, "assets/drone"), { recursive: true });
      writeFileSync(join(root, "assets/drone/frame.png"), "test fixture");
      writeFileSync(join(root, "private.png"), "outside media directory");
      symlinkSync(join(root, "private.png"), join(root, "assets/drone/link.png"));
      const asset = { asset_id: "a", site_id: "site", path: "assets/drone/frame.png", file_status: "present", placeholder: false };
      expect(mediaFile(asset, root)?.mime).toBe("image/png");
      expect(mediaFile({ ...asset, placeholder: true }, root)).toBeUndefined();
      expect(mediaFile({ ...asset, file_status: "placeholder" }, root)).toBeUndefined();
      expect(mediaFile({ ...asset, path: "assets/drone/../../private.png" }, root)).toBeUndefined();
      expect(mediaFile({ ...asset, path: "assets/drone/link.png" }, root)).toBeUndefined();
      expect(mediaFile({ ...asset, path: "assets/drone/missing.png" }, root)).toBeUndefined();
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
});
