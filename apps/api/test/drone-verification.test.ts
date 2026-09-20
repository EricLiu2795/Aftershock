import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mediaFile, storedInspectionInputs, inspectionResult } from "../src/drone-reports.js";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
let runtime: ApiRuntime;
const load = (path: string) => JSON.parse(readFileSync(new URL(`../../../${path}`, import.meta.url), "utf8"));
beforeEach(async () => {
  process.env.NODE_ENV = "test";
  runtime = await buildApi(loadConfig({ databasePath: ":memory:", presenterApiKey: "scan-test", startWorker: false, replayReleaseIntervalS: 0,
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
    expect((await command(s, "tick")).statusCode).toBe(200);
    const result = (await reports(s)).json().reports[0];
    expect(result.report.victim_indicator).toEqual({ type: "none", count_min: 0, count_max: 0 });
    expect(result.observations).toHaveLength(3);
    expect(result.observations.every((o: any) => o.verification_status === "unverified")).toBe(true);
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

function importLocatedTargets(s: {id:string}, keys = ["W2", "W7"]) {
  for (const key of keys) {
    const site=load("data/scenarios/caraballeda/active_sites.json").active_worksites.find((w:any)=>w.worksite_key===key).site;
    const observation=load("contracts/examples/structured_social_observation.json");
    observation.observation_id=`imported-${key}`;
    observation.location={latitude:site.centroid.latitude,longitude:site.centroid.longitude,resolved_site_id:site.site_id};
    observation.incident_type="trapped_victims"; observation.victim_indication={type:"possible_multiple"};
    runtime.storage.run("INSERT INTO external_reports(session_id, report_id, payload_json) VALUES(?,?,?)",s.id,`imported-${key}`,JSON.stringify({id:`imported-${key}`,source:"social",author:"test",timestamp:"2026-09-19T14:00:00Z",text:"Possible trapped people",mode:"replay",model:"fixture",sourceUrl:null,observations:[observation]}));
    runtime.storage.run("INSERT INTO report_locations VALUES(?,?,?,?)",s.id,`imported-${key}`,site.site_id,"2026-09-19T14:00:00Z");
  }
}
describe("tracking dashboard flow", () => {
  it("prepares idle markers, dispatches on demand, and feeds scan evidence into the existing details read model", async () => {
    const s = await session();
    const ready = await command(s, "prepare");
    expect(ready.statusCode).toBe(200);
    expect(ready.json().missions).toHaveLength(0);
    expect(ready.json().state.resources).toHaveLength(2);
    expect((await command(s, "tick")).json().missions).toHaveLength(0);
    expect(ready.json().state.worksites).toHaveLength(0);
    const emptyFeed=await runtime.app.inject({method:"GET",url:`/api/demo-sessions/${s.id}/intelligence`,headers:{cookie:s.cookie}});
    expect(emptyFeed.json().reports).toHaveLength(0);
    importLocatedTargets(s);
    const dispatched = await command(s, "dispatch");
    expect(dispatched.statusCode).toBe(200);
    expect(dispatched.json().missions).toHaveLength(2);
    expect(dispatched.json().missions.every((m: any) => m.phase === "en_route")).toBe(true);
    elapse(s.id, 600);
    expect((await command(s, "tick")).statusCode).toBe(200);
    const feed = await runtime.app.inject({ method:"GET",url:`/api/demo-sessions/${s.id}/intelligence`,headers:{cookie:s.cookie} });
    const drones = feed.json().reports.filter((r: any) => r.source === "drone");
    expect(drones).toHaveLength(2);
    expect(drones.every((r: any) => r.locations[0].method === "canonical_site")).toBe(true);
    expect(drones.every((r: any) => r.text.includes("Structure:") && r.observations.length === 3)).toBe(true);
    const again = await command(s, "dispatch");
    expect(again.json().missions.every((m: any) => m.phase === "idle")).toBe(true);
  });
  it("queues an already located social report without resolving new identities or replacing coordinator work", async () => {
    const s = await session();
    await command(s,"prepare");
    importLocatedTargets(s);
    await command(s,"dispatch");
    const site = load("data/scenarios/caraballeda/active_sites.json").active_worksites.find((w: any) => w.worksite_key === "W1").site;
    const observation = load("contracts/examples/structured_social_observation.json");
    observation.observation_id="queued-observation";
    observation.location={...observation.location, latitude:site.centroid.latitude,longitude:site.centroid.longitude,resolved_site_id:site.site_id};
    observation.incident_type="trapped_victims";observation.victim_indication={type:"possible_multiple"};
    runtime.storage.run("INSERT INTO external_reports(session_id, report_id, payload_json) VALUES(?,?,?)",s.id,"queued-social",JSON.stringify({id:"queued-social",source:"social",author:"test",timestamp:"2026-09-19T14:00:00Z",text:"Possible trapped people",mode:"replay",model:"fixture",sourceUrl:null,observations:[observation]}));
    runtime.storage.run("INSERT INTO report_locations VALUES(?,?,?,?)",s.id,"queued-social",site.site_id,"2026-09-19T14:00:00Z");
    elapse(s.id,600);
    const tick=await command(s,"tick");
    expect(tick.statusCode).toBe(200);
    expect(tick.json().missions.some((m:any)=>m.site_id===site.site_id && m.phase==="en_route")).toBe(true);
    // Completed reports survive when an idle resource begins another task on the same tick.
    expect((await reports(s)).json().reports).toHaveLength(2);
  });
});


describe("traveling drone road scans", () => {
  it("persists swept-road assessments once and isolates session reads", async () => {
    const s = await session();
    expect((await command(s, "initialize", { state: state("W2") })).statusCode).toBe(200);
    elapse(s.id, 600);
    const tick = await command(s, "tick");
    expect(tick.statusCode).toBe(200);
    const url = `/api/demo-sessions/${s.id}/search/roads`;
    const first = await runtime.app.inject({ method:"GET", url, headers:{cookie:s.cookie} });
    expect(first.statusCode).toBe(200);
    expect(first.json().detection_radius_m).toBe(500);
    expect(first.json().detections.length).toBeGreaterThan(0);
    expect(first.json().road_states.length).toBeGreaterThan(0);
    for (const d of first.json().detections) {
      expect(["open", "limited", "blocked"]).toContain(d.assessment.road_state.status);
      expect(d.simulation).toBe(true);
      expect(d.assessment.verification_status).toBe("unverified");
    }
    expect((await command(s, "tick")).statusCode).toBe(200);
    const next = await runtime.app.inject({ method:"GET", url, headers:{cookie:s.cookie} });
    expect(next.json().detections.length).toBe(first.json().detections.length);
    const other=await session();
    expect((await runtime.app.inject({method:"GET",url,headers:{cookie:other.cookie}})).statusCode).toBe(403);
    expect((await runtime.app.inject({method:"GET",url})).statusCode).toBe(401);
  });
});


describe("explicit social replay targets", () => {
  it("keeps a new session empty then resolves imported sites into drone targets", async () => {
    const s=await session();
    const ready=await command(s,"prepare");
    expect(ready.json().state.worksites).toHaveLength(0);
    const imported=await runtime.app.inject({method:"POST",url:`/api/demo-sessions/${s.id}/ingest`,headers:{cookie:s.cookie},payload:{source:"replay",provider:"fixture"}});
    expect(imported.statusCode).toBe(200);
    const feed=await runtime.app.inject({method:"GET",url:`/api/demo-sessions/${s.id}/intelligence`,headers:{cookie:s.cookie}});
    const located=feed.json().reports.filter((r:any)=>r.source==="social" && r.locations.length);
    expect(located).toHaveLength(8);
    expect(new Set(located.map((r:any)=>r.id)).size).toBe(8);
    expect(new Set(located.flatMap((r:any)=>r.locations.map((l:any)=>l.site.id))).size).toBe(8);
    expect(new Set(located.flatMap((r:any)=>r.locations.map((l:any)=>l.site.name))).size).toBe(8);
    const repeated=await runtime.app.inject({method:"POST",url:`/api/demo-sessions/${s.id}/ingest`,headers:{cookie:s.cookie},payload:{source:"replay",provider:"fixture"}});
    expect(repeated.json().imported).toBe(0);
    expect(located.every((r:any)=>r.observations.every((o:any)=>o.incident_type!=="access_blocked" && o.access_status==="unknown"))).toBe(true);
    expect(located.flatMap((r:any)=>r.locations.map((l:any)=>l.site.id)).sort()).toEqual(load("data/scenarios/caraballeda/active_sites.json").active_worksites.map((w:any)=>w.site.site_id).sort());
    const tick=await command(s,"tick");
    expect(tick.statusCode).toBe(200);
    expect(tick.json().state.worksites).toHaveLength(8);
    expect(tick.json().target_priorities.map((p:any)=>p.rank)).toEqual([1,2,3,4,5,6,7,8]);
    expect(new Set(tick.json().target_priorities.map((p:any)=>p.site_id)).size).toBe(8);
    expect(tick.json().state.worksites.map((w:any)=>w.metadata.building_priority.people_count).sort((a:number,b:number)=>a-b)).toEqual([0,0,1,2,2,3,4,6]);
    expect(tick.json().missions).toHaveLength(0);
    const dispatched=await command(s,"dispatch");
    expect(dispatched.statusCode).toBe(200);
    expect(dispatched.json().missions).toHaveLength(2);
  });
});

describe("database building priority and verification", () => {
  it("matches scenario counts after import and reveals the matching image only after scanning", async () => {
    const historical = await session();
    // Populate the existing database through the normal inspection lifecycle.
    await command(historical, "initialize", {state:state("W2")});
    elapse(historical.id,600); await command(historical,"tick");
    const s=await session();
    await command(s,"prepare"); importLocatedTargets(s);
    const preview=await command(s,"tick");
    expect(preview.statusCode).toBe(200);
    expect(preview.json().missions).toHaveLength(0);
    const factors=preview.json().latest_search_plan.explanations.find((e:any)=>e.building_factors?.database_report_id);
    expect(factors.building_factors.damage_level).toBe("major_damage");
    expect(factors.building_factors.people_count).toBe(3);
    expect(factors.building_factors.people_count_basis).toBe("scenario_simulated_count");
    expect(factors.candidates).toHaveLength(2);
    expect((await reports(s)).json().reports).toHaveLength(0);
    const dispatched=await command(s,"dispatch"); expect(dispatched.statusCode).toBe(200);
    elapse(s.id,600); expect((await command(s,"tick")).statusCode).toBe(200);
    const feed=await runtime.app.inject({method:"GET",url:`/api/demo-sessions/${s.id}/intelligence`,headers:{cookie:s.cookie}});
    const drone=feed.json().reports.filter((r:any)=>r.source==="drone");
    expect(drone.find((r:any)=>r.inspection.result==="confirmed").inspection.images).toHaveLength(1);
    const safe=drone.find((r:any)=>r.inspection.result==="safe");
    expect(safe.inspection.images).toHaveLength(0);
    const stored=(await reports(s)).json().reports.find((r:any)=>r.report.structural_damage==="major_damage");
    expect(stored.report.metadata.scenario_ground_truth_id).toBe("gt-W2");
  });

  it("inspects all eight unique scenario buildings once and reports the correct people counts", async () => {
    const s=await session();
    await command(s,"prepare");
    const imported=await runtime.app.inject({method:"POST",url:`/api/demo-sessions/${s.id}/ingest`,headers:{cookie:s.cookie},payload:{source:"replay",provider:"fixture"}});
    expect(imported.statusCode).toBe(200);
    expect((await command(s,"dispatch")).statusCode).toBe(200);
    for(let i=0;i<6;i++) {elapse(s.id,600);const tick=await command(s,"tick");expect(tick.statusCode,tick.body).toBe(200);}
    const result=(await reports(s)).json().reports;
    expect(result).toHaveLength(8);
    expect(new Set(result.map((r:any)=>r.site_id)).size).toBe(8);
    expect(result.every((r:any)=>r.report.metadata.inspection_source==="scenario_simulated_scan")).toBe(true);
    expect(result.map((r:any)=>r.report.victim_indicator.count_min).sort((a:number,b:number)=>a-b)).toEqual([0,0,1,2,2,3,4,6]);
    const finished=await command(s,"tick");
    expect(finished.json().inspected_site_ids).toHaveLength(8);
    expect(finished.json().target_priorities).toHaveLength(0);
    // Reimporting and explicitly dispatching again cannot revisit inspected sites.
    await runtime.app.inject({method:"POST",url:`/api/demo-sessions/${s.id}/ingest`,headers:{cookie:s.cookie},payload:{source:"replay",provider:"fixture"}});
    const again=await command(s,"dispatch");
    expect(again.json().missions.every((m:any)=>m.phase==="idle")).toBe(true);
    expect((await reports(s)).json().reports).toHaveLength(8);
    const feed=await runtime.app.inject({method:"GET",url:`/api/demo-sessions/${s.id}/intelligence`,headers:{cookie:s.cookie}});
    expect(feed.json().reports.filter((r:any)=>r.inspection?.people==="none")).toHaveLength(2);
    expect(feed.json().reports.filter((r:any)=>r.inspection?.people==="present")).toHaveLength(6);
  }, 15000);
});


describe("building database", () => {
  it("stores one row per scenario building with people counts and photos, idempotently", async () => {
    const { seedScenarioBuildings, scenarioBuildings } = await import("../src/scenario-buildings.js");
    seedScenarioBuildings(runtime.storage); seedScenarioBuildings(runtime.storage);
    const rows = scenarioBuildings(runtime.storage, "caraballeda");
    expect(rows).toHaveLength(8);
    expect(rows.map(r => r.people_count).sort((a, b) => a - b)).toEqual([0, 0, 1, 2, 2, 3, 4, 6]);
    expect(rows.filter(r => r.people_trapped === 1).map(r => r.worksite_key).sort()).toEqual(["W1", "W2", "W3", "W4", "W5", "W8"]);
    // Every red building has its own drone photo on disk; contextual photos never qualify.
    for (const r of rows.filter(r => r.people_trapped === 1)) expect(r.photo_path, r.worksite_key).toMatch(/^assets\/drone\//);
    const s = await session();
    const listed = await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/buildings`, headers: { cookie: s.cookie } });
    expect(listed.statusCode).toBe(200);
    expect(listed.json().buildings).toHaveLength(8);
    expect(listed.json().buildings.every((b: any) => b.simulated && typeof b.people_count === "number")).toBe(true);
    expect(listed.json().buildings.every((b: any) => !b.photo || b.photo.revealed === false)).toBe(true);
    expect((await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/buildings` })).statusCode).toBe(401);
  });
  it("reveals a building photo only after this session's scan finds people there", async () => {
    const s = await session();
    await command(s, "prepare");
    const imported = await runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/ingest`, headers: { cookie: s.cookie }, payload: { source: "replay", provider: "fixture" } });
    expect(imported.statusCode).toBe(200);
    const w1 = load("data/scenarios/caraballeda/active_sites.json").active_worksites.find((w: any) => w.worksite_key === "W1").site.site_id;
    const w7 = load("data/scenarios/caraballeda/active_sites.json").active_worksites.find((w: any) => w.worksite_key === "W7").site.site_id;
    const photo = (site: string) => runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/buildings/${encodeURIComponent(site)}/photo`, headers: { cookie: s.cookie } });
    expect((await photo(w1)).statusCode).toBe(404);
    expect((await command(s, "dispatch")).statusCode).toBe(200);
    for (let i = 0; i < 6; i++) { elapse(s.id, 600); expect((await command(s, "tick")).statusCode).toBe(200); }
    expect((await photo(w1)).statusCode).toBe(200);
    expect((await photo(w1)).headers["content-type"]).toMatch(/image\/jpeg/);
    expect((await photo(w7)).statusCode).toBe(404); // green: no people, no photo
    const feed = await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/intelligence`, headers: { cookie: s.cookie } });
    const drones = feed.json().reports.filter((r: any) => r.source === "drone");
    expect(drones.filter((r: any) => r.inspection.people === "present").every((r: any) => r.inspection.images.length >= 1 && r.inspection.people_count > 0)).toBe(true);
    expect(drones.filter((r: any) => r.inspection.people === "none").every((r: any) => r.inspection.images.length === 0 && r.inspection.people_count === 0)).toBe(true);
    const listed = await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${s.id}/search/buildings`, headers: { cookie: s.cookie } });
    expect(listed.json().buildings.filter((b: any) => b.photo?.revealed).map((b: any) => b.worksite_key).sort()).toEqual(["W1", "W2", "W3", "W4", "W5", "W8"]);
    const other = await session();
    expect((await runtime.app.inject({ method: "GET", url: `/api/demo-sessions/${other.id}/search/buildings/${encodeURIComponent(w1)}/photo`, headers: { cookie: other.cookie } })).statusCode).toBe(404);
  }, 20000);
});

describe("stored inspection boundaries", () => {
  it("does not share private reports across sessions and keeps incomplete findings unverified", async () => {
    const source=await session(), target=await session();
    const site=state("W2").sites[0];
    runtime.storage.run("INSERT INTO drone_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",source.id,"private-report","mission","drone",site.site_id,"worksite","2026-09-19T14:00:00Z",JSON.stringify({structural_damage:"major_damage",metadata:{provenance:"live"}}));
    expect(storedInspectionInputs(runtime.storage,target.id)).toHaveLength(0);
    expect(storedInspectionInputs(runtime.storage,source.id)).toHaveLength(1);
    expect(inspectionResult({structural_damage:"unknown",victim_indicator:{type:"none"}})).toBe("unverified");
    expect(inspectionResult({structural_damage:"none",victim_indicator:{type:"possible_multiple"}})).toBe("unverified");
  });
});
