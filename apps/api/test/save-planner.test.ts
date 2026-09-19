import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import type { BeliefSnapshot } from "../src/operational-bridge.js";

const presenterKey = "save-test-key";
const W1 = "osm:way:1533952351";
const R1 = "osm:way:376529129:segment:1-12";
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

async function session(name = "Save test") {
  const response = await runtime.app.inject({
    method: "POST",
    url: "/api/demo-sessions",
    headers: { "x-presenter-key": presenterKey },
    payload: {
      scenario_id: "caraballeda",
      name,
      allowed_responder_ids: ["alpha"],
    },
  });
  return {
    id: response.json().session_id as string,
    cookie: response.cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; "),
  };
}

function headers(s: { cookie: string }) {
  return { cookie: s.cookie };
}

async function applyObservations(s: { id: string; cookie: string }, observations: unknown[]) {
  return runtime.app.inject({
    method: "POST",
    url: `/api/demo-sessions/${s.id}/belief-state/observations`,
    headers: headers(s),
    payload: { observations },
  });
}

async function saveTick(s: { id: string; cookie: string }) {
  return runtime.app.inject({
    method: "POST",
    url: `/api/demo-sessions/${s.id}/save/tick`,
    headers: headers(s),
    payload: {},
  });
}

function e01() {
  return observation({
    observation_id: "obs-e01",
    timestamp: "2026-09-19T14:00:20Z",
    source_type: "social",
    source_id: "replay",
    raw_text:
      "Residencias Caribe on Avenida Principal de Caribe looks collapsed. People may be trapped. Please help.",
    author_id: "demo-author-e01",
  });
}

function e02() {
  return observation({
    observation_id: "obs-e02",
    timestamp: "2026-09-19T14:00:38Z",
    source_type: "radio",
    source_id: "rescue-alpha",
    raw_text:
      "Command, Rescue Team Alpha: partial collapse at Residencias Caribe, Avenida Principal de Caribe. Hearing at least 4 voices from the north side. East approach is limited by debris.",
    victim_type: "confirmed_multiple",
    count_min: 4,
    structural_hazard: "major_damage",
    speaker_id: "rescue-alpha",
  });
}

function observation(input: {
  observation_id: string;
  timestamp: string;
  source_type: string;
  source_id: string;
  raw_text: string;
  author_id?: string;
  speaker_id?: string;
  victim_type?: string;
  count_min?: number | null;
  structural_hazard?: string;
}) {
  const metadata: Record<string, unknown> = { extracted_by: "test" };
  if (input.author_id) {
    metadata.ingestion = { source_metadata: { author_id: input.author_id } };
  }
  if (input.speaker_id) metadata.speaker_id = input.speaker_id;
  return {
    schema_version: "2.1",
    observation_id: input.observation_id,
    timestamp: input.timestamp,
    source_type: input.source_type,
    source_id: input.source_id,
    location: {
      latitude: null,
      longitude: null,
      raw_text: input.raw_text,
      label: input.raw_text,
      accuracy: "unknown",
      resolved_site_id: null,
    },
    incident_type: "trapped_victims",
    victim_indication: {
      type: input.victim_type ?? "possible_multiple",
      count_min: input.count_min ?? null,
      count_max: null,
    },
    structural_hazard: input.structural_hazard ?? "unknown",
    access_status: "unknown",
    verification_status: "unverified",
    raw_source_reference: { reference_type: "test", value: input.observation_id },
    metadata,
  };
}

describe("live Save on OperationalBeliefState", () => {
  it("waits until canonical belief exists, then assigns the same W1", async () => {
    const s = await session();
    const waiting = await saveTick(s);
    expect(waiting.statusCode, waiting.body).toBe(200);
    expect(waiting.json().waiting).toBe(true);
    expect(waiting.json().assignments ?? []).toEqual([]);

    const applied = await applyObservations(s, [e01(), e02()]);
    expect(applied.statusCode, applied.body).toBe(200);
    const belief = applied.json<BeliefSnapshot>();
    const worksite = belief.worksites[0] as { worksite_id: string; site_id: string };
    expect(worksite.site_id).toBe(W1);

    const save = (await saveTick(s)).json();
    expect(save.initialized).toBe(true);
    expect(save.worksites[0].worksite_id).toBe(worksite.worksite_id);
    expect(save.worksites[0].site_id).toBe(W1);
    expect(save.assignments[0].worksite_id).toBe(worksite.worksite_id);
    expect(save.routes.length).toBeGreaterThan(0);
    expect(save.last_consumed_operational_state_version).toBe(belief.state_version);
    expect(save.operational_state_version).toBe(belief.state_version);
    expect(save.sync_action).toBe("initialize");
    const again = (await saveTick(s)).json();
    expect(again.sync_action).toBe("noop");
    expect(again.operational_state_version).toBe(belief.state_version);
  });

  it("blocks R1 in canonical state and reroutes the same W1 assignment", async () => {
    const s = await session();
    await applyObservations(s, [e01(), e02()]);
    const first = (await saveTick(s)).json();
    const assignment = first.assignments[0];
    const oldRoute = assignment.route_id;

    const blocked = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/roads/${encodeURIComponent(R1)}`,
      headers: headers(s),
      payload: { status: "blocked" },
    });
    expect(blocked.statusCode, blocked.body).toBe(200);
    const body = blocked.json() as {
      belief: BeliefSnapshot;
      save: {
        assignments: { resource_id: string; worksite_id: string; route_id: string }[];
        routes: { road_edge_ids: string[] }[];
        proposal: unknown;
        last_consumed_operational_state_version: string;
      };
    };
    expect(
      (body.belief.road_states as { road_id: string; status: string }[]).some(
        (item) => item.road_id === R1 && item.status === "blocked",
      ),
    ).toBe(true);
    expect(body.belief.state_version).not.toBe(first.last_consumed_operational_state_version);
    expect(body.save.assignments[0].resource_id).toBe(assignment.resource_id);
    expect(body.save.assignments[0].worksite_id).toBe(assignment.worksite_id);
    expect(body.save.assignments[0].route_id).not.toBe(oldRoute);
    expect(body.save.routes.every((route) => !route.road_edge_ids.includes(R1))).toBe(true);
    expect(body.save.proposal).toBeNull();
  });

  it("H: isolates Save execution between sessions", async () => {
    const left = await session("left");
    const right = await session("right");
    await applyObservations(left, [e01(), e02()]);
    const leftSave = (await saveTick(left)).json();
    expect(leftSave.assignments.length).toBeGreaterThan(0);
    const rightWaiting = (await saveTick(right)).json();
    expect(rightWaiting.waiting || rightWaiting.assignments?.length === 0).toBe(true);
    expect(
      runtime.storage.one(
        "SELECT 1 FROM save_planner_sessions WHERE session_id=?",
        right.id,
      ),
    ).toBeFalsy();
  });

  it("I: reset clears Save execution state", async () => {
    const s = await session();
    await applyObservations(s, [e01(), e02()]);
    expect((await saveTick(s)).json().assignments.length).toBeGreaterThan(0);
    const reset = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/reset`,
      headers: headers(s),
      payload: {},
    });
    expect(reset.statusCode).toBe(200);
    expect(reset.json().save_initialized).toBe(false);
    expect(
      runtime.storage.one(
        "SELECT 1 FROM save_planner_sessions WHERE session_id=?",
        s.id,
      ),
    ).toBeFalsy();
    const after = (await saveTick(s)).json();
    expect(after.waiting).toBe(true);
    expect(after.assignments ?? []).toEqual([]);
    expect(after.routes ?? []).toEqual([]);
    expect(after.proposal ?? null).toBeNull();
  });

  it("rejects unauthenticated Save and road updates", async () => {
    const s = await session();
    const tick = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/save/tick`,
      payload: {},
    });
    expect(tick.statusCode).toBe(401);
    const road = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/roads/${encodeURIComponent(R1)}`,
      payload: { status: "blocked" },
    });
    expect(road.statusCode).toBe(401);
  });

  it("accepts the loopback alias of APP_ORIGIN for Dashboard Save ticks", async () => {
    const s = await session();
    const loopback = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/save/tick`,
      headers: { ...headers(s), origin: "http://127.0.0.1:5173" },
      payload: {},
    });
    expect(loopback.statusCode, loopback.body).toBe(200);
    const foreign = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/save/tick`,
      headers: { ...headers(s), origin: "http://evil.example:5173" },
      payload: {},
    });
    expect(foreign.statusCode).toBe(403);
  });
});
