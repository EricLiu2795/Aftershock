import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mediaFile } from "../src/drone-reports.js";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import type { BeliefSnapshot } from "../src/operational-bridge.js";
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
    expect(result[0].assets[0]).toMatchObject({ asset_id: "va-w2-drone-oblique", file_status: "available", available: true,
      image_url: `/api/demo-sessions/${s.id}/search/assets/va-w2-drone-oblique` });
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
    const published = await command(s, "tick");
    expect(published.statusCode).toBe(200);
    const result = (await reports(s)).json().reports[0];
    expect(result.report.victim_indicator).toEqual({ type: "none", count_min: 0, count_max: 0 });
    expect(result.observations).toHaveLength(3);
    expect(result.observations.every((o: any) => o.verification_status === "unverified")).toBe(true);
    const stored = JSON.parse(runtime.storage.one<{ record_json: string }>(
      "SELECT record_json FROM drone_search_sessions WHERE session_id=?", s.id)!.record_json);
    expect(stored).not.toHaveProperty("operational_runtime");
    expect(stored).not.toHaveProperty("operational_feedback");
    const feedback = published.json().operational_feedback;
    const trapped = feedback.observations.filter((o: any) => o.incident_type === "trapped_victims");
    expect(trapped).toHaveLength(1);
    expect(trapped[0]).toMatchObject({ source_type: "drone", verification_status: "verified",
      victim_indication: { type: "none", count_min: 0, count_max: 0 } });
    expect(trapped[0].location.resolved_site_id).toBeNull();
    expect(JSON.stringify(trapped[0])).not.toContain("intended_referent");
    expect(feedback.seam).toBe("applySessionObservations");
    expect(feedback.changed).toBe(true);
    const replay = (await command(s, "tick")).json().operational_feedback;
    expect(replay.changed).toBe(false);
    expect(replay.state_version).toBe(feedback.state_version);
    expect((await reports({ ...s, cookie: other.cookie })).statusCode).toBe(403);
    expect((await reports({ ...s, cookie: "" })).statusCode).toBe(401);
    expect((await reports(other)).json().reports).toEqual([]);
    const hidden = await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/assets/va-w1-drone-oblique`, headers: { cookie: s.cookie } });
    expect(hidden.statusCode).toBe(404);
    const revealed = await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/assets/va-w7-drone-topdown`, headers: { cookie: s.cookie } });
    expect(revealed.statusCode).toBe(200);
    expect(revealed.headers["content-type"]).toMatch(/image\/png/);
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

const W7 = "osm:node:13976280529";

describe("W7 live drone feedback on Agent 1 belief sessions", () => {
  it("invalidates the E11 rumor from the same persisted session after authored W7 verification", async () => {
    const s = await session();
    const rumor = await runtime.app.inject({
      method: "POST", url: `/api/demo-sessions/${s.id}/belief-state/observations`,
      headers: { cookie: s.cookie }, payload: { observations: [e11()] },
    });
    expect(rumor.statusCode, rumor.body).toBe(200);
    const afterRumor = rumor.json<BeliefSnapshot>();
    const trapped = incidentsOf(afterRumor, W7, "trapped_victims");
    expect(trapped).toHaveLength(1);
    expect(trapped[0]?.status).toBe("active");
    expect(["unverified", "potential"]).toContain(trapped[0]?.verification_status);
    const rumorWorksite = worksiteOf(afterRumor, W7);
    expect(rumorWorksite?.operational_status).toBe("pending");
    expect(rumorWorksite?.active_incident_ids).toEqual([trapped[0]?.incident_id]);
    const rumorVersion = afterRumor.state_version;

    expect((await command(s, "initialize", { state: state("W7") })).statusCode).toBe(200);
    elapse(s.id, 600);
    const published = await command(s, "tick");
    expect(published.statusCode, published.body).toBe(200);
    const feedback = published.json().operational_feedback;
    expect(feedback.seam).toBe("applySessionObservations");
    const drone = feedback.observations.find((o: any) => o.incident_type === "trapped_victims");
    expect(drone).toMatchObject({ source_type: "drone", verification_status: "verified",
      victim_indication: { type: "none", count_min: 0, count_max: 0 } });
    expect(drone.location.resolved_site_id).toBeNull();
    expect(JSON.stringify(drone)).not.toContain("intended_referent");

    const afterDrone = await belief(s);
    expect(Number(afterDrone.state_version)).toBeGreaterThan(Number(rumorVersion));
    expect(afterDrone.state_version).toBe(feedback.state_version);
    const updated = incidentsOf(afterDrone, W7, "trapped_victims")[0];
    expect(updated?.incident_id).toBe(trapped[0]?.incident_id);
    expect(updated?.status).toBe("invalidated");
    expect(updated?.verification_status).not.toBe("corroborated");
    expect(updated?.observation_ids).toEqual(expect.arrayContaining(["obs-e11", drone.observation_id]));
    expect(updated?.evidence_ids?.length).toBeGreaterThanOrEqual(2);
    const worksite = worksiteOf(afterDrone, W7);
    expect(worksite?.operational_status).toBe("resolved");
    expect(worksite?.active_incident_ids).toEqual([]);
    expect(worksite?.metadata?.invalidated_incident_ids).toEqual(expect.arrayContaining([updated?.incident_id]));

    const stored = JSON.parse(runtime.storage.one<{ record_json: string }>(
      "SELECT record_json FROM drone_search_sessions WHERE session_id=?", s.id)!.record_json);
    expect(stored).not.toHaveProperty("operational_runtime");
    expect(runtime.storage.one("SELECT 1 FROM operational_belief_sessions WHERE session_id=?", s.id)).toBeTruthy();

    const replay = await command(s, "tick");
    expect(replay.statusCode).toBe(200);
    expect(replay.json().operational_feedback.changed).toBe(false);
    const afterReplay = await belief(s);
    expect(afterReplay.state_version).toBe(afterDrone.state_version);
    const again = incidentsOf(afterReplay, W7, "trapped_victims")[0];
    expect(again?.observation_ids).toEqual(updated?.observation_ids);
    expect(again?.evidence_ids).toEqual(updated?.evidence_ids);
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
      expect(mediaFile({ ...asset, file_status: "available" }, root)?.mime).toBe("image/png");
      expect(mediaFile({ ...asset, placeholder: true }, root)).toBeUndefined();
      expect(mediaFile({ ...asset, file_status: "placeholder" }, root)).toBeUndefined();
      expect(mediaFile({ ...asset, path: "assets/drone/../../private.png" }, root)).toBeUndefined();
      expect(mediaFile({ ...asset, path: "assets/drone/link.png" }, root)).toBeUndefined();
      expect(mediaFile({ ...asset, path: "assets/drone/missing.png" }, root)).toBeUndefined();
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
});

async function belief(s: { id: string; cookie: string }) {
  const response = await runtime.app.inject({
    url: `/api/demo-sessions/${s.id}/belief-state`, headers: { cookie: s.cookie },
  });
  expect(response.statusCode, response.body).toBe(200);
  return response.json<BeliefSnapshot>();
}

function e11() {
  const event = load("data/scenarios/caraballeda/scenario_events.json").events.find((item: any) => item.event_id === "E11");
  return {
    schema_version: "2.1",
    observation_id: "obs-e11",
    timestamp: event.raw_input.timestamp,
    source_type: "social",
    source_id: event.source_id,
    location: {
      latitude: null, longitude: null, raw_text: event.raw_input.text, label: null,
      accuracy: "unknown", resolved_site_id: null,
    },
    incident_type: "trapped_victims",
    victim_indication: { type: "possible_multiple", count_min: null, count_max: null },
    structural_hazard: "unknown",
    access_status: "unknown",
    verification_status: "unverified",
    raw_source_reference: { reference_type: "test", value: "obs-e11" },
    metadata: { extracted_by: "test", ingestion: { source_metadata: { author_id: event.raw_input.author_id } } },
  };
}

function incidentsOf(snapshot: BeliefSnapshot, siteId: string, incidentType: string) {
  return (snapshot.incidents as Array<{
    incident_id: string; site_id: string; incident_type: string; status: string;
    verification_status: string; observation_ids: string[]; evidence_ids: string[];
  }>).filter((item) => item.site_id === siteId && item.incident_type === incidentType);
}

function worksiteOf(snapshot: BeliefSnapshot, siteId: string) {
  return (snapshot.worksites as Array<{
    site_id: string; operational_status: string; active_incident_ids: string[];
    metadata?: { invalidated_incident_ids?: string[] };
  }>).find((item) => item.site_id === siteId);
}
