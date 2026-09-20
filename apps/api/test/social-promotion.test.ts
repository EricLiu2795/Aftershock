import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import type { BeliefSnapshot } from "../src/operational-bridge.js";
import type { ExternalReport, IntelligenceSnapshot } from "../../../contracts/intelligence.js";

const presenterKey = "promotion-test";
const W1 = "osm:way:1533952351";
const W2 = "osm:way:1533952352";
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
    payload: { scenario_id: "caraballeda", name: "Promotion test", allowed_responder_ids: ["alpha"] } });
  return { id: response.json().session_id as string, cookie: response.cookies.map(c => `${c.name}=${c.value}`).join("; ") };
}

function report(id: string, rawText: string, author = `witness-${id}`): ExternalReport {
  const timestamp = "2026-09-19T14:00:10Z";
  return {
    id, source: "social", author, timestamp, mode: "social_replay", model: "fixture", sourceUrl: null,
    text: `Voices inside ${rawText}. Nobody can reach them.`,
    observations: [{
      schema_version: "2.1", observation_id: `${id}-0`, timestamp, source_type: "social", source_id: author,
      location: { latitude: null, longitude: null, raw_text: rawText, label: null, accuracy: "unknown", resolved_site_id: null },
      incident_type: "trapped_victims",
      victim_indication: { type: "possible_multiple", count_min: null, count_max: null },
      structural_hazard: "unknown", access_status: "unknown", verification_status: "unverified",
      raw_source_reference: { reference_type: "social", value: id },
      metadata: { ingestion: { source_metadata: { author_id: author } } },
    }],
  } as unknown as ExternalReport;
}

/** Stage a raw report the way an import would, still waiting for its release slot. */
function stagePending(sessionId: string, item: ExternalReport) {
  runtime.storage.run(
    "INSERT INTO external_reports(session_id, report_id, payload_json, imported_at, release_offset_s) VALUES (?, ?, ?, ?, ?)",
    sessionId, item.id, JSON.stringify(item), new Date().toISOString(), 600);
}

function releaseNext(s: { id: string; cookie: string }) {
  return runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/ingest`, headers: { cookie: s.cookie },
    payload: { source: "replay", provider: "fixture" } });
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

function siteIds(snapshot: BeliefSnapshot) {
  return snapshot.worksites.map(item => (item as { site_id: string }).site_id);
}

function unresolved(snapshot: BeliefSnapshot) {
  return snapshot.unresolved_observations as Array<{
    observation_id: string;
    location_resolution: { resolution_status: string; candidate_site_ids: string[] };
  }>;
}

describe("social promotion gate", () => {
  it("keeps an ambiguous report in the feed and out of belief, Search, and Save", async () => {
    const s = await session();
    stagePending(s.id, report("ambiguous-caribe", "Residencias Caribe"));
    expect((await feed(s)).reports).toHaveLength(0);
    expect((await feed(s)).pendingReports).toBe(1);

    const released = await releaseNext(s);
    expect(released.statusCode, released.body).toBe(200);
    expect(released.json()).toMatchObject({ imported: 0, released: 1, pending: 0 });

    const visible = await feed(s);
    expect(visible.reports).toHaveLength(1);
    expect(visible.reports[0]!.state).toBe("extracted");
    // The display gazetteer is ambiguous here too, and never had the power to promote.
    expect(visible.reports[0]!.locations).toEqual([]);

    const after = await belief(s);
    expect(after.worksites).toEqual([]);
    expect(after.sites).toEqual([]);
    expect(after.incidents).toEqual([]);
    expect(unresolved(after)).toHaveLength(1);
    expect(unresolved(after)[0]!.location_resolution.resolution_status).toBe("ambiguous");
    expect(unresolved(after)[0]!.location_resolution.candidate_site_ids).toEqual([W1, W2]);

    const search = await runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/search/tick`,
      headers: { cookie: s.cookie }, payload: {} });
    expect(search.statusCode, search.body).toBe(200);
    expect(search.json().state?.worksites ?? []).toEqual([]);
    expect(search.json().target_priorities ?? []).toEqual([]);
    expect(search.json().missions).toEqual([]);
    const save = await runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/save/tick`,
      headers: { cookie: s.cookie }, payload: {} });
    expect(save.statusCode, save.body).toBe(200);
    expect(save.json().assignments ?? []).toEqual([]);
    expect(save.json().tasks ?? []).toEqual([]);
  });

  it("promotes a released report that resolves to exactly one canonical Site", async () => {
    const s = await session();
    stagePending(s.id, report("unique-caribe-a", "Residencias Caribe A"));
    expect((await releaseNext(s)).json()).toMatchObject({ released: 1, pending: 0 });

    const after = await belief(s);
    expect(siteIds(after)).toEqual([W1]);
    expect(unresolved(after)).toEqual([]);
    const trapped = after.incidents as Array<{ site_id: string; incident_type: string; verification_status: string }>;
    expect(trapped.filter(item => item.site_id === W1 && item.incident_type === "trapped_victims")).toHaveLength(1);
    expect((await feed(s)).reports).toHaveLength(1);
  });

  it("treats a gazetteer-only match as display context, never as promotion", async () => {
    const s = await session();
    // Residencias Las Anclas exists in OSM but is not a canonical Caraballeda worksite.
    stagePending(s.id, report("anclas", "Residencias Las Anclas"));
    expect((await releaseNext(s)).json()).toMatchObject({ released: 1 });

    const visible = await feed(s);
    expect(visible.reports[0]!.locations[0]?.method).toBe("exact_name");
    expect(visible.reports[0]!.locations[0]?.site.name).toBe("Residencias Las Anclas");
    const after = await belief(s);
    expect(after.worksites).toEqual([]);
    expect(unresolved(after)).toHaveLength(1);
    expect(unresolved(after)[0]!.location_resolution.resolution_status).toBe("unresolved");
  });
});

describe("manual location resolution", () => {
  it("promotes an ambiguous report once the operator picks the canonical Site", async () => {
    const s = await session();
    stagePending(s.id, report("ambiguous-caribe", "Residencias Caribe"));
    await releaseNext(s);
    expect((await belief(s)).worksites).toEqual([]);

    const located = await runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/locate`,
      headers: { cookie: s.cookie }, payload: { report_id: "ambiguous-caribe", site_id: W2 } });
    expect(located.statusCode, located.body).toBe(200);
    expect(located.json()).toMatchObject({ located: true, promoted: true });

    const after = await belief(s);
    expect(siteIds(after)).toEqual([W2]);
    const trapped = (after.incidents as Array<{ site_id: string; incident_type: string; verification_status: string }>)
      .filter(item => item.site_id === W2 && item.incident_type === "trapped_victims");
    expect(trapped).toHaveLength(1);
    // One operator decision is still one source: it corroborates nothing on its own.
    expect(trapped[0]!.verification_status).toBe("unverified");
    const visible = await feed(s);
    expect(visible.reports[0]!.locations[0]?.method).toBe("manual");
    expect(visible.reports[0]!.locations[0]?.site.id).toBe(W2);

    const repeated = await runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/locate`,
      headers: { cookie: s.cookie }, payload: { report_id: "ambiguous-caribe", site_id: W2 } });
    expect(repeated.json().state_version).toBe(after.state_version);
  });

  it("leaves the report unpromoted when the chosen Site is outside canonical belief", async () => {
    const s = await session();
    stagePending(s.id, report("ambiguous-caribe", "Residencias Caribe"));
    await releaseNext(s);
    const anclas = (await runtime.app.inject({ url: "/api/geography" })).json()
      .sites.find((site: { name: string }) => site.name === "Residencias Las Anclas");
    const located = await runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/locate`,
      headers: { cookie: s.cookie }, payload: { report_id: "ambiguous-caribe", site_id: anclas.id } });
    expect(located.json()).toMatchObject({ located: true, promoted: false });
    expect((await belief(s)).worksites).toEqual([]);
  });

  it("requires a presenter cookie", async () => {
    const s = await session();
    stagePending(s.id, report("ambiguous-caribe", "Residencias Caribe"));
    await releaseNext(s);
    const anonymous = await runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/locate`,
      payload: { report_id: "ambiguous-caribe", site_id: W1 } });
    expect(anonymous.statusCode).toBe(401);
    expect((await belief(s)).worksites).toEqual([]);
  });
});

describe("session isolation of promoted belief", () => {
  it("keeps one session's promoted report out of another session", async () => {
    const left = await session();
    const right = await session();
    stagePending(left.id, report("unique-caribe-a", "Residencias Caribe A"));
    await releaseNext(left);
    expect(siteIds(await belief(left))).toEqual([W1]);
    expect((await belief(right)).state_version).toBe("0");
    expect((await belief(right)).worksites).toEqual([]);
    expect((await feed(right)).reports).toEqual([]);
  });
});
