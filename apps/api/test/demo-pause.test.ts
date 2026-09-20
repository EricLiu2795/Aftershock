import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import { demoClock, setDemoPaused } from "../src/demo-clock.js";
import { releasedReports } from "../src/replay-release.js";

let runtime: ApiRuntime;
let wall: number;
beforeEach(async () => {
  process.env.NODE_ENV = "test";
  runtime = await buildApi(loadConfig({ databasePath: ":memory:", presenterApiKey: "pause-test", startWorker: false,
    llmApiKey: undefined, llmModel: undefined, sessionSigningSecret: "test-signing-secret-that-is-long-enough" }));
  wall = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => wall);
});
afterEach(async () => { vi.restoreAllMocks(); await runtime.app.close(); });

async function session() {
  const r = await runtime.app.inject({ method: "POST", url: "/api/demo-sessions", headers: { "x-presenter-key": "pause-test" },
    payload: { scenario_id: "caraballeda", name: "Pause test", allowed_responder_ids: ["alpha"] } });
  expect(r.statusCode, r.body).toBe(201);
  return { id: r.json().session_id as string, cookie: r.cookies.map(c => `${c.name}=${c.value}`).join("; ") };
}
type Session = Awaited<ReturnType<typeof session>>;
function post(s: Session, path: string, payload: object = {}) {
  return runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/${path}`, headers: { cookie: s.cookie }, payload });
}
async function get(s: Session, path: string) {
  const r = await runtime.app.inject({ url: `/api/demo-sessions/${s.id}/${path}`, headers: { cookie: s.cookie } });
  expect(r.statusCode, r.body).toBe(200); return r.json();
}
async function initialize(s: Session) {
  expect((await post(s, "start", { play: "immediate" })).statusCode).toBe(202);
  while (await runtime.worker.runOnce()) { /* finish opening radio */ }
  for (const path of ["search/tick", "save/tick"]) expect((await post(s, path)).statusCode).toBe(200);
}

it("authenticates pause, isolates sessions, is idempotent, and resets paused state", async () => {
  const a = await session(), b = await session();
  expect((await runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${a.id}/playback`, payload: { paused: true } })).statusCode).toBe(401);
  expect((await post({ ...a, cookie: b.cookie }, "playback", { paused: true })).statusCode).toBe(403);
  expect((await post(a, "playback", { paused: "yes" })).statusCode).toBe(400);
  const paused = (await post(a, "playback", { paused: true })).json();
  expect(paused.paused).toBe(true);
  expect((await post(a, "playback", { paused: true })).json()).toEqual(paused);
  expect((await get(b, "playback")).paused).toBe(false);
  expect((await post(a, "start")).statusCode).toBe(409);
  expect((await post(a, "search/speed", { simulation_speed: 10 })).statusCode).toBe(409);
  wall += 60000;
  expect((await post(a, "reset")).statusCode).toBe(200);
  expect((await get(a, "playback")).paused).toBe(false);
  expect(demoClock(runtime.storage, a.id).nowMs).toBe(wall);
});

it("freezes report-release time and resumes without skipping the remaining delay", async () => {
  const s = await session();
  runtime.storage.run("INSERT INTO external_reports(session_id,report_id,payload_json,imported_at,release_offset_s) VALUES(?,?,?,?,?)",
    s.id, "delayed", JSON.stringify({ release_clock: "wall" }), new Date(wall).toISOString(), 5);
  wall += 2000;
  await post(s, "playback", { paused: true });
  const frozen = demoClock(runtime.storage, s.id).nowMs;
  wall += 120000;
  expect(demoClock(runtime.storage, s.id).nowMs).toBe(frozen);
  expect(releasedReports(runtime.storage, s.id)).toBe(0);
  await post(s, "playback", { paused: false });
  expect(demoClock(runtime.storage, s.id).nowMs).toBe(frozen);
  wall += 2999; expect(releasedReports(runtime.storage, s.id)).toBe(0);
  wall += 1; expect(releasedReports(runtime.storage, s.id)).toBe(1);
});

it("freezes real Search and Save state and resumes without a movement jump", async () => {
  const s = await session(); await initialize(s);
  await post(s, "search/speed", { simulation_speed: 5 });
  wall += 1000;
  const search = (await post(s, "search/tick")).json();
  const save = (await post(s, "save/tick")).json();
  expect(search.missions.length).toBeGreaterThan(0);
  expect(save.resources.some((r: any) => r.current_worksite_id)).toBe(true);
  await post(s, "playback", { paused: true });
  wall += 120000;
  const heldSearch = (await post(s, "search/tick")).json();
  const heldSave = (await post(s, "save/tick")).json();
  expect(heldSearch.paused).toBe(true); expect(heldSave.paused).toBe(true);
  expect(heldSearch.missions).toEqual(search.missions);
  expect(heldSave.resources).toEqual(save.resources);
  await post(s, "playback", { paused: false });
  const resumedSearch = (await post(s, "search/tick")).json();
  const resumedSave = (await post(s, "save/tick")).json();
  expect(resumedSearch.simulation_speed).toBe(5);
  expect(resumedSearch.missions.map((m: any) => m.movement.position)).toEqual(search.missions.map((m: any) => m.movement.position));
  expect(resumedSave.resources.map((r: any) => r.position)).toEqual(save.resources.map((r: any) => r.position));
  wall += 1000;
  expect((await post(s, "search/tick")).json().missions[0].movement.position).not.toEqual(search.missions[0].movement.position);
}, 30000);

it("discards in-flight Search and Save results if pause wins before persistence", async () => {
  const s = await session(); await initialize(s);
  for (const [path, table] of [["search/tick", "drone_search_sessions"], ["save/tick", "save_planner_sessions"]]) {
    await post(s, "playback", { paused: false });
    const before = runtime.storage.one<{ record_json: string }>(`SELECT record_json FROM ${table} WHERE session_id=?`, s.id)!;
    wall += 1000;
    const original = runtime.storage.transaction.bind(runtime.storage);
    let armed = true;
    const transaction = vi.spyOn(runtime.storage, "transaction").mockImplementation(work => {
      if (armed) { armed = false; setDemoPaused(runtime.storage, s.id, true); }
      return original(work);
    });
    const result = await post(s, path!);
    transaction.mockRestore();
    expect(result.statusCode, result.body).toBe(200);
    expect(result.json().paused).toBe(true);
    expect(runtime.storage.one(`SELECT record_json FROM ${table} WHERE session_id=?`, s.id)).toEqual(before);
  }
}, 30000);

it("holds pack social/radio timers during pause and allows ending a paused session", async () => {
  const s = await session();
  expect((await post(s, "start", { play: "timed" })).statusCode).toBe(202);
  await post(s, "playback", { paused: true });
  const count = releasedReports(runtime.storage, s.id);
  wall += 60000;
  await new Promise(r => setTimeout(r, 400));
  expect(releasedReports(runtime.storage, s.id)).toBe(count);
  expect(runtime.storage.all("SELECT id FROM transmissions WHERE session_id=?", s.id)).toHaveLength(0);
  expect(await runtime.worker.runOnce()).toBe(false);
  await post(s, "playback", { paused: false });
  wall += 11000;
  await vi.waitFor(() => expect(runtime.storage.all("SELECT id FROM transmissions WHERE session_id=?", s.id)).toHaveLength(1), { timeout: 6000 });
  await post(s, "playback", { paused: true });
  expect((await post(s, "end")).statusCode).toBe(200);
  expect((await post(s, "playback", { paused: false })).statusCode).toBe(410);
}, 15000);
