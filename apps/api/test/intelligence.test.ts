import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import { geography, matchSite } from "../src/geography.js";
import type { IntelligenceSnapshot } from "../../../contracts/intelligence.js";
let runtime: ApiRuntime;
beforeEach(async () => {
  process.env.NODE_ENV = "test";
  runtime = await buildApi(
    loadConfig({
      databasePath: ":memory:",
      presenterApiKey: "dashboard-test",
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
    expect(imported.json().imported).toBe(7);
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
    expect(snapshot.reports).toHaveLength(8);
    const radio = snapshot.reports.find((item) => item.source === "radio")!;
    expect(radio.state).toBe("resolved");
    expect(radio.locations[0]?.site.name).toBe("Residencias Las Anclas");
    expect(
      radio.observations.every((item) => item.schema_version === "2.1"),
    ).toBe(true);
    expect(
      snapshot.reports.filter((item) => item.state === "filtered"),
    ).toHaveLength(1);
    expect(
      snapshot.reports
        .filter((item) => item.source === "social")
        .every((item) => !item.locations.length),
    ).toBe(true);
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
