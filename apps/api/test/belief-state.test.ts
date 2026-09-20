import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import type { BeliefSnapshot } from "../src/operational-bridge.js";

const presenterKey = "belief-test-key";
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

describe("runtime operational belief bridge", () => {
  it("A: first W1 E01-like observation increments state and creates one worksite", async () => {
    const session = await createSession();
    const empty = await belief(session);
    expect(empty.state_version).toBe("0");
    expect(empty.incidents).toEqual([]);
    expect(empty.worksites).toEqual([]);
    expect(empty.sites).toEqual([]);
    expect(empty.resources).toEqual([]);
    expect(empty.road_states).toEqual([]);
    expect(empty).not.toHaveProperty("pending_reassignment_proposals");
    expect(empty).not.toHaveProperty("active_routes");
    expect(empty).not.toHaveProperty("movement_states");

    const applied = await applyObservations(session, [e01()]);
    expect(applied.statusCode, applied.body).toBe(200);
    const snapshot = applied.json<BeliefSnapshot>();
    expect(Number(snapshot.state_version)).toBeGreaterThan(Number(empty.state_version));
    expect(snapshot.worksites).toHaveLength(1);
    expect(worksiteSiteIds(snapshot)).toEqual([W1]);
    expect(incidentSiteIds(snapshot)).toEqual([W1]);
    expect(
      snapshot.incidents.every(
        (item) => (item as { incident_type: string }).incident_type === "trapped_victims",
      ),
    ).toBe(true);

    const read = await belief(session);
    expect(read.state_version).toBe(snapshot.state_version);
    expect(worksiteSiteIds(read)).toEqual([W1]);
  });

  it("B: independent E02-like radio corroborates the same W1 worksite", async () => {
    const session = await createSession();
    const first = (await applyObservations(session, [e01()])).json<BeliefSnapshot>();
    const secondResponse = await applyObservations(session, [e02()]);
    expect(secondResponse.statusCode, secondResponse.body).toBe(200);
    const second = secondResponse.json<BeliefSnapshot>();

    expect(Number(second.state_version)).toBeGreaterThan(Number(first.state_version));
    expect(second.worksites).toHaveLength(1);
    expect(worksiteSiteIds(second)).toEqual([W1]);
    const trapped = incidentsOf(second, "trapped_victims");
    expect(trapped).toHaveLength(1);
    expect(trapped[0]?.site_id).toBe(W1);
    expect(trapped[0]?.verification_status).toBe("corroborated");
    expect(trapped[0]?.metadata).toMatchObject({ evidence_conflict: false });
    expect(new Set(trapped[0]?.observation_ids)).toEqual(new Set(["obs-e01", "obs-e02"]));
  });

  it("C: demo sessions do not share belief state", async () => {
    const left = await createSession();
    const right = await createSession();
    await applyObservations(left, [e01()]);
    const leftSnap = await belief(left);
    const rightSnap = await belief(right);
    expect(worksiteSiteIds(leftSnap)).toEqual([W1]);
    expect(rightSnap.state_version).toBe("0");
    expect(rightSnap.worksites).toEqual([]);
    expect(rightSnap.incidents).toEqual([]);

    await applyObservations(right, [e01()]);
    const rightAfter = await belief(right);
    expect(worksiteSiteIds(rightAfter)).toEqual([W1]);
    expect(rightAfter.state_version).toBe("1");
    expect((await belief(left)).state_version).toBe(leftSnap.state_version);
  });

  it("D: reprocessing the same observation does not duplicate evidence", async () => {
    const session = await createSession();
    const first = (await applyObservations(session, [e01()])).json<BeliefSnapshot>();
    const repeated = (await applyObservations(session, [e01()])).json<BeliefSnapshot>();
    expect(repeated.state_version).toBe(first.state_version);
    expect(repeated.worksites).toHaveLength(1);
    const trapped = incidentsOf(repeated, "trapped_victims");
    expect(trapped[0]?.observation_ids).toEqual(["obs-e01"]);
    expect(trapped[0]?.evidence_ids).toEqual(incidentsOf(first, "trapped_victims")[0]?.evidence_ids);
  });

  it("wires radio extraction into the operational bridge and survives worker retry", async () => {
    const session = await createSession();
    const joined = await join(session);
    const first = await transmit(
      session,
      joined.cookie,
      "Command, partial collapse at Residencias Caribe on Avenida Principal de Caribe. Multiple voices heard.",
    );
    const afterFirst = await belief(session);
    expect(Number(afterFirst.state_version)).toBeGreaterThan(0);
    expect(worksiteSiteIds(afterFirst)).toEqual([W1]);
    const observationIds = runtime.storage
      .all<{ observation_id: string }>(
        "SELECT observation_id FROM observations WHERE transmission_id = ? ORDER BY observation_id",
        first.transmissionId,
      )
      .map((row) => row.observation_id);
    expect(observationIds.length).toBeGreaterThan(0);
    expect(observationIds.every((id) => id.startsWith("obs-radio-"))).toBe(true);

    runtime.storage.run(
      "UPDATE extraction_jobs SET status = 'queued', attempts = 0, lease_until = NULL, error_code = NULL, extraction_result_json = NULL WHERE transmission_id = ?",
      first.transmissionId,
    );
    runtime.storage.run(
      "UPDATE transmissions SET status = 'retrying' WHERE id = ?",
      first.transmissionId,
    );
    expect(await runtime.worker.runOnce()).toBe(true);
    const afterRetry = await belief(session);
    expect(afterRetry.state_version).toBe(afterFirst.state_version);
    expect(worksiteSiteIds(afterRetry)).toEqual([W1]);
    const retriedIds = runtime.storage
      .all<{ observation_id: string }>(
        "SELECT observation_id FROM observations WHERE transmission_id = ? ORDER BY observation_id",
        first.transmissionId,
      )
      .map((row) => row.observation_id);
    expect(retriedIds).toEqual(observationIds);
    const trapped = incidentsOf(afterRetry, "trapped_victims")[0];
    expect(new Set(trapped?.observation_ids)).toEqual(
      new Set(incidentsOf(afterFirst, "trapped_victims")[0]?.observation_ids),
    );
  });

  it("rejects cross-session belief reads and does not let the JS gazetteer override Caribe ambiguity", async () => {
    const session = await createSession();
    const other = await createSession();
    expect(
      (
        await runtime.app.inject({
          url: `/api/demo-sessions/${session.id}/belief-state`,
          headers: { cookie: other.cookie },
        })
      ).statusCode,
    ).toBe(403);

    const ambiguous = await applyObservations(session, [
      observation({
        observation_id: "obs-plain-caribe",
        timestamp: "2026-09-19T14:00:20Z",
        source_type: "social",
        source_id: "replay",
        raw_text: "Residencias Caribe",
        author_id: "plain-author",
        resolved_site_id: "osm:way:797152586",
      }),
    ]);
    expect(ambiguous.statusCode, ambiguous.body).toBe(200);
    const snapshot = ambiguous.json<BeliefSnapshot>();
    expect(snapshot.worksites).toEqual([]);
    expect(snapshot.incidents).toEqual([]);
    expect(snapshot.unresolved_observations).toHaveLength(1);
  });
});

async function createSession() {
  const response = await runtime.app.inject({
    method: "POST",
    url: "/api/demo-sessions",
    headers: { "x-presenter-key": presenterKey },
    payload: {
      scenario_id: "caraballeda",
      name: "Belief bridge test",
      allowed_responder_ids: ["rescue-alpha", "rescue-bravo"],
    },
  });
  expect(response.statusCode).toBe(201);
  return {
    id: response.json().session_id as string,
    cookie: response.cookies.map((item) => `${item.name}=${item.value}`).join("; "),
    joinCode: response.json().join_code as string,
  };
}

async function join(session: { joinCode: string }, responderId = "rescue-alpha") {
  const response = await runtime.app.inject({
    method: "POST",
    url: "/api/demo-sessions/join",
    payload: { join_code: session.joinCode, responder_id: responderId },
  });
  expect(response.statusCode).toBe(200);
  return {
    cookie: response.cookies.map((item) => `${item.name}=${item.value}`).join("; "),
  };
}

async function belief(session: { id: string; cookie: string }) {
  const response = await runtime.app.inject({
    url: `/api/demo-sessions/${session.id}/belief-state`,
    headers: { cookie: session.cookie },
  });
  expect(response.statusCode, response.body).toBe(200);
  return response.json<BeliefSnapshot>();
}

async function applyObservations(
  session: { id: string; cookie: string },
  observations: Record<string, unknown>[],
) {
  return runtime.app.inject({
    method: "POST",
    url: `/api/demo-sessions/${session.id}/belief-state/observations`,
    headers: { cookie: session.cookie },
    payload: { observations },
  });
}

async function transmit(
  session: { id: string; cookie: string },
  participantCookie: string,
  transcript: string,
) {
  const reserved = await runtime.app.inject({
    method: "POST",
    url: "/api/radio/transmissions",
    headers: { cookie: participantCookie },
    payload: { capture_mode: "cached_transcript", channel: "command-1" },
  });
  expect(reserved.statusCode).toBe(201);
  const transmissionId = reserved.json().transmission_id as string;
  const finalized = await runtime.app.inject({
    method: "POST",
    url: `/api/radio/transmissions/${transmissionId}/finalize`,
    headers: { cookie: participantCookie },
    payload: {
      schema_version: "2.1",
      transmission_id: transmissionId,
      transcript,
      capture_started_at: "2026-09-19T14:00:38.000Z",
      capture_ended_at: "2026-09-19T14:00:46.000Z",
      segments: [
        {
          sequence: 0,
          text: transcript,
          started_at_ms: 0,
          ended_at_ms: 8000,
        },
      ],
    },
  });
  expect(finalized.statusCode).toBe(202);
  expect(await runtime.worker.runOnce()).toBe(true);
  return { transmissionId, session };
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
  resolved_site_id?: string | null;
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
      resolved_site_id: input.resolved_site_id ?? null,
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
    raw_source_reference: {
      reference_type: "test",
      value: input.observation_id,
    },
    metadata,
  };
}

function worksiteSiteIds(snapshot: BeliefSnapshot) {
  return snapshot.worksites.map((item) => (item as { site_id: string }).site_id);
}

function incidentSiteIds(snapshot: BeliefSnapshot) {
  return snapshot.incidents.map((item) => (item as { site_id: string }).site_id);
}

function incidentsOf(snapshot: BeliefSnapshot, incidentType: string) {
  return (snapshot.incidents as Array<{
    incident_type: string;
    site_id: string;
    verification_status: string;
    observation_ids: string[];
    evidence_ids: string[];
    metadata?: { evidence_conflict?: boolean };
  }>).filter((item) => item.incident_type === incidentType);
}
