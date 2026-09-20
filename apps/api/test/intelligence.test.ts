import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import { geography, matchSite } from "../src/geography.js";
import type { IntelligenceSnapshot } from "../../../contracts/intelligence.js";
let runtime: ApiRuntime;
const SOCIAL_REPORT_COUNT = 20;
beforeEach(async () => {
  process.env.NODE_ENV = "test";
  runtime = await buildApi(
    loadConfig({
      databasePath: ":memory:",
      presenterApiKey: "dashboard-test",
      startWorker: false,
      llmApiKey: undefined,
      llmModel: undefined,
      replayReleaseIntervalS: 0,
      sessionSigningSecret: "test-signing-secret-that-is-long-enough",
    }),
  );
});
afterEach(async () => {
  await runtime.app.close();
});
async function session() {
  const response = await runtime.app.inject({
    method: "POST",
    url: "/api/demo-sessions",
    headers: { "x-presenter-key": "dashboard-test" },
    payload: {
      scenario_id: "caraballeda",
      name: "Integration test",
      allowed_responder_ids: ["alpha"],
    },
  });
  return {
    id: response.json().session_id as string,
    cookie: response.cookies
      .map((item) => `${item.name}=${item.value}`)
      .join("; "),
    joinCode: response.json().join_code,
  };
}
describe("unified intelligence", () => {
  it("uses real OSM geometry and exact names, never vague or fictional locations", () => {
    expect(geography().info.sites.length).toBeGreaterThan(8000);
    expect(geography().info.roadCount).toBeGreaterThan(1000);
    expect(matchSite("Residencias Las Anclas")?.longitude).toBeLessThan(-66);
    expect(matchSite("Riverside Apartments")).toBeUndefined();
    expect(matchSite("Residencias Caribe B")?.id).toBe("osm:way:1533952352");
    expect(matchSite("Residencias Caribe")).toBeUndefined();
    expect(matchSite("Residencias Caribe A")?.name).toBe("Residencias Caribe A");
    expect(matchSite("Residencias Caribe B")?.name).toBe("Residencias Caribe B");
    expect(
      matchSite("the apartment building across from the pharmacy"),
    ).toBeUndefined();
    expect(matchSite("La Guaira")).toBeUndefined();
  });
  it("joins radio and social pipeline output, deduplicates imports, preserves filtered reports, and persists manual locations", async () => {
    const s = await session();
    const headers = { cookie: s.cookie };
    const ingest = {
      method: "POST" as const,
      url: `/api/demo-sessions/${s.id}/ingest`,
      headers,
      payload: { source: "replay", provider: "fixture" },
    };
    const imported = await runtime.app.inject(ingest);
    expect(imported.statusCode, imported.body).toBe(200);
    expect(imported.json().imported).toBe(SOCIAL_REPORT_COUNT);
    expect((await runtime.app.inject(ingest)).json().imported).toBe(0);
    const replay = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/replay`,
      headers,
      payload: {
        fixture_name: "caraballeda_report",
        capture_mode: "cached_observation",
      },
    });
    expect(replay.statusCode, replay.body).toBe(202);
    await runtime.worker.runOnce();
    const read = async () =>
      (
        await runtime.app.inject({
          url: `/api/demo-sessions/${s.id}/intelligence`,
          headers,
        })
      ).json<IntelligenceSnapshot>();
    const snapshot = await read();
    expect(snapshot.reports).toHaveLength(SOCIAL_REPORT_COUNT + 1);
    const radio = snapshot.reports.find((item) => item.source === "radio")!;
    expect(radio.state).toBe("resolved");
    expect(radio.locations[0]?.site.name).toBe("Residencias Las Anclas");
    expect(
      radio.observations.every((item) => item.schema_version === "2.1"),
    ).toBe(true);
    expect(
      snapshot.reports.filter((item) => item.state === "filtered"),
    ).toHaveLength(0);
    expect(
      snapshot.reports
        .filter((item) => item.source === "social" && item.locations.length),
    ).toHaveLength(SOCIAL_REPORT_COUNT);
    const post = snapshot.reports.find(
      (item) => item.source === "social" && item.observations.length,
    )!;
    const place = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/locate`,
      headers,
      payload: { report_id: post.id, site_id: radio.locations[0]!.site.id },
    });
    expect(place.statusCode).toBe(200);
    const located = (await read()).reports.find((item) => item.id === post.id)!;
    expect(located.locations[0]?.method).toBe("manual");
    expect(
      located.observations.every(
        (item) => item.verification_status === "unverified",
      ),
    ).toBe(true);
    expect(located.observations[0]?.location.latitude).toBeNull();
    const other = await session();
    expect(
      (
        await runtime.app.inject({
          url: `/api/demo-sessions/${other.id}/intelligence`,
          headers: { cookie: other.cookie },
        })
      ).json().reports,
    ).toHaveLength(0);
    expect(
      (
        await runtime.app.inject({
          url: `/api/demo-sessions/${other.id}/intelligence`,
          headers,
        })
      ).statusCode,
    ).toBe(403);
    runtime.storage.deleteSession(s.id);
    expect(runtime.storage.all("SELECT * FROM external_reports")).toHaveLength(
      0,
    );
    expect(runtime.storage.all("SELECT * FROM report_locations")).toHaveLength(
      0,
    );
  });
  it("requires a presenter for imports and location edits and rejects unsupported data", async () => {
    const s = await session();
    expect(
      (
        await runtime.app.inject({
          url: `/api/demo-sessions/${s.id}/intelligence`,
        })
      ).statusCode,
    ).toBe(401);
    const joined = await runtime.app.inject({
      method: "POST",
      url: "/api/demo-sessions/join",
      payload: { join_code: s.joinCode, responder_id: "alpha" },
    });
    const participant = joined.cookies
      .map((item) => `${item.name}=${item.value}`)
      .join("; ");
    expect(
      (
        await runtime.app.inject({
          method: "POST",
          url: `/api/demo-sessions/${s.id}/ingest`,
          headers: { cookie: participant },
          payload: {},
        })
      ).statusCode,
    ).toBe(403);
    expect(
      (
        await runtime.app.inject({
          method: "POST",
          url: `/api/demo-sessions/${s.id}/locate`,
          headers: { cookie: s.cookie },
          payload: { report_id: "made-up", site_id: "made-up" },
        })
      ).statusCode,
    ).toBe(400);
    expect(
      (
        await runtime.app.inject({
          method: "POST",
          url: `/api/demo-sessions/${s.id}/ingest`,
          headers: { cookie: s.cookie },
          payload: { source: "gdacs", provider: "fixture" },
        })
      ).statusCode,
    ).toBe(400);
  });
});

describe("staggered replay release", () => {
  it("shows one report first, then pairs over time, follows simulation speed, and can be released early", async () => {
    await runtime.app.close();
    runtime = await buildApi(loadConfig({ databasePath: ":memory:", presenterApiKey: "dashboard-test", startWorker: false,
      llmApiKey: undefined, llmModel: undefined, replayReleaseIntervalS: 120, sessionSigningSecret: "test-signing-secret-that-is-long-enough" }));
    const s = await session();
    const headers = { cookie: s.cookie };
    const read = async () => (await runtime.app.inject({ url: `/api/demo-sessions/${s.id}/intelligence`, headers })).json<IntelligenceSnapshot>();
    const ingest = () => runtime.app.inject({ method: "POST", url: `/api/demo-sessions/${s.id}/ingest`, headers, payload: { source: "replay", provider: "fixture" } });
    const first = await ingest();
    expect(first.statusCode, first.body).toBe(200);
    expect(first.json()).toMatchObject({ imported: SOCIAL_REPORT_COUNT, released: 1, pending: SOCIAL_REPORT_COUNT - 1 });
    let snapshot = await read();
    expect(snapshot.reports).toHaveLength(1);
    expect(snapshot.pendingReports).toBe(SOCIAL_REPORT_COUNT - 1);
    // The earliest-stamped report (Residencias Caribe A) arrives first, as a yellow located target.
    expect(snapshot.reports[0]!.locations[0]!.site.name).toBe("Residencias Caribe A");
    // Nothing is stored twice and every scenario report is already in the database.
    expect(runtime.storage.all("SELECT 1 FROM external_reports WHERE session_id=?", s.id)).toHaveLength(SOCIAL_REPORT_COUNT);
    // Two minutes later (real time, 1× speed) the next pair appears.
    runtime.storage.run("UPDATE external_reports SET imported_at=? WHERE session_id=?", new Date(Date.now() - 121_000).toISOString(), s.id);
    snapshot = await read();
    expect(snapshot.reports).toHaveLength(3);
    expect(snapshot.pendingReports).toBe(SOCIAL_REPORT_COUNT - 3);
    // At 10× drone simulation speed the same wall time releases the whole feed faster.
    runtime.storage.run("INSERT INTO drone_search_sessions(session_id, revision, record_json) VALUES (?, 1, ?)", s.id, JSON.stringify({ simulation_speed: 10 }));
    snapshot = await read();
    expect(snapshot.reports).toHaveLength(SOCIAL_REPORT_COUNT);
    runtime.storage.run("DELETE FROM drone_search_sessions WHERE session_id=?", s.id);
    // Clicking Import again releases the next pending batch immediately instead of re-importing.
    const early = await ingest();
    expect(early.json()).toMatchObject({ imported: 0, released: 2, pending: SOCIAL_REPORT_COUNT - 5 });
    expect((await read()).reports).toHaveLength(5);
    expect(runtime.storage.all("SELECT 1 FROM external_reports WHERE session_id=?", s.id)).toHaveLength(SOCIAL_REPORT_COUNT);
  });
});
