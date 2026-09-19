import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
let runtime: ApiRuntime;
beforeEach(async () => {
  process.env.NODE_ENV = "test";
  runtime = await buildApi(loadConfig({ databasePath: ":memory:", presenterApiKey: "search-test",
    startWorker: false, llmApiKey: undefined, llmModel: undefined,
    sessionSigningSecret: "test-signing-secret-that-is-long-enough" }));
});
afterEach(async () => { await runtime.app.close(); });
async function session() {
  const response = await runtime.app.inject({ method: "POST", url: "/api/demo-sessions",
    headers: { "x-presenter-key": "search-test" }, payload: {
      scenario_id: "caraballeda", name: "Search test", allowed_responder_ids: ["alpha"] } });
  return { id: response.json().session_id as string,
    cookie: response.cookies.map(c => `${c.name}=${c.value}`).join("; ") };
}
function state() {
  const value = JSON.parse(readFileSync(new URL("../../../data/search_demo/input_state.json", import.meta.url), "utf8"));
  for (const r of value.resources) if (r.resource_type === "drone_unit") r.speed = { value: .1, unit: "m_per_s" };
  return value;
}
async function command(s: {id: string; cookie: string}, cmd: string, payload = {}) {
  return runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/search/${cmd}`,
    headers: { cookie: s.cookie }, payload });
}
async function pending() {
  const s = await session(); const input = state();
  expect((await command(s, "initialize", { state: input })).statusCode).toBe(200);
  input.state_version = "new-evidence";
  input.worksites[1].metadata.search_features = { potential_impact: 1, uncertainty: 1, criticality: 1, evidence_conflict: 1 };
  const response = await command(s, "update", { state: input });
  expect(response.statusCode).toBe(200);
  expect(response.json().proposals[0].proposal.status).toBe("pending");
  return { s, record: response.json() };
}
describe("drone approval gate", () => {
  it("requires a presenter in the same session and retains an empty waiting state", async () => {
    const s = await session(); const other = await session();
    expect((await command({ ...s, cookie: "" }, "tick")).statusCode).toBe(401);
    expect((await command({ ...s, cookie: other.cookie }, "tick")).statusCode).toBe(403);
    expect((await command(s, "tick")).json().initialized).toBe(false);
  });
  it("initializes from Agent 1's operational snapshot on tick and skips replan for the same version", async () => {
    const s = await session();
    const input = state();
    runtime.storage.db.exec(`CREATE TABLE IF NOT EXISTS operational_belief_snapshots (
      session_id TEXT PRIMARY KEY, state_version TEXT NOT NULL, state_json TEXT NOT NULL, updated_at TEXT NOT NULL)`);
    runtime.storage.run(
      "INSERT INTO operational_belief_snapshots(session_id, state_version, state_json, updated_at) VALUES (?,?,?,?)",
      s.id, input.state_version, JSON.stringify(input), input.updated_at);
    const first = await command(s, "tick");
    expect(first.statusCode).toBe(200);
    expect(first.json().initialized).toBe(true);
    expect(first.json().sync_action).toBe("initialize");
    expect(first.json().missions.length).toBeGreaterThan(0);
    expect(first.json().operational_state_version).toBe(input.state_version);
    const second = await command(s, "tick");
    expect(second.statusCode).toBe(200);
    expect(second.json().initialized).toBe(true);
    expect(second.json().sync_action).toBe("noop");
    expect(second.json().missions.map((m: { mission_id: string }) => m.mission_id))
      .toEqual(first.json().missions.map((m: { mission_id: string }) => m.mission_id));
    input.state_version = "belief-2";
    input.worksites[1].metadata.search_features = { potential_impact: 1, uncertainty: 1, criticality: 1, evidence_conflict: 1 };
    runtime.storage.run(
      "UPDATE operational_belief_snapshots SET state_version=?, state_json=? WHERE session_id=?",
      input.state_version, JSON.stringify(input), s.id);
    const third = await command(s, "tick");
    expect(third.statusCode).toBe(200);
    expect(third.json().sync_action).toBe("update");
    expect(third.json().operational_state_version).toBe("belief-2");
  });
  it("approves once under concurrent clicks and preserves an audit record", async () => {
    const { s, record } = await pending();
    const choice = { proposal_id: record.proposals[0].proposal.proposal_id, decision: "approve" };
    const responses = await Promise.all([command(s, "decide", choice), command(s, "decide", choice)]);
    for (const response of responses) {
      expect(response.statusCode).toBe(200);
      expect(response.json().history).toHaveLength(1);
      expect(response.json().proposals[0].proposal.status).toBe("approved");
      expect(response.json().history[0].mission.assignment.status).toBe("cancelled");
    }
    expect((await command(s, "initialize", { state: state() })).statusCode).toBe(400);
  });
  it("uses the persisted server deadline, ignoring a client's forged time and approval", async () => {
    const { s, record } = await pending();
    const row = runtime.storage.one<{ record_json: string }>("SELECT record_json FROM drone_search_sessions WHERE session_id=?", s.id)!;
    const stored = JSON.parse(row.record_json);
    stored.proposals[0].proposal.expires_at = new Date(Date.now() - 1).toISOString();
    runtime.storage.run("UPDATE drone_search_sessions SET record_json=? WHERE session_id=?", JSON.stringify(stored), s.id);
    const response = await command(s, "decide", { proposal_id: record.proposals[0].proposal.proposal_id,
      decision: "approve", wall_now: "2020-01-01T00:00:00Z", expires_at: "2100-01-01T00:00:00Z" });
    expect(response.statusCode).toBe(200);
    expect(response.json().proposals[0].proposal.status).toBe("expired");
    expect(response.json().missions[0].mission_id).toBe(record.missions[0].mission_id);
    expect(response.json().history).toHaveLength(0);
  });
  it("rejects invalid payloads without overwriting stored missions", async () => {
    const { s, record } = await pending();
    expect((await command(s, "decide", { proposal_id: record.proposals[0].proposal.proposal_id, decision: "yes" })).statusCode).toBe(400);
    expect((await command(s, "update", { state: {} })).statusCode).toBe(400);
    const response = await command(s, "tick");
    expect(response.json().missions[0].mission_id).toBe(record.missions[0].mission_id);
  });
});
