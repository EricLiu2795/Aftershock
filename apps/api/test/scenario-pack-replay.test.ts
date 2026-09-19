import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buildApi, type ApiRuntime } from "../src/server.js";
import { loadConfig } from "../src/config.js";
import {
  CARABALLEDA_DEMO,
  DEMO_OMITTED,
  DEMO_STEPS,
  SITE_ID_SHORTCUTS,
  assertObservablePayload,
  packEvent,
  packRadioRaw,
  runtimeSourceId,
  stablePackRadioInputId,
} from "../src/scenario-pack.js";
import type { IntelligenceSnapshot } from "../../../contracts/intelligence.js";
import type { BeliefSnapshot } from "../src/operational-bridge.js";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const packEvents = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL("../../../data/scenarios/caraballeda/scenario_events.json", import.meta.url),
    ),
    "utf8",
  ),
) as { events: Array<{ event_id: string; raw_input: { text?: string; transcript?: string } }> };
const byId = Object.fromEntries(packEvents.events.map((event) => [event.event_id, event]));

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

async function session(responders = ["rescue-alpha"]) {
  const response = await runtime.app.inject({
    method: "POST",
    url: "/api/demo-sessions",
    headers: { "x-presenter-key": "dashboard-test" },
    payload: {
      scenario_id: "caraballeda",
      name: "Pack replay test",
      allowed_responder_ids: responders,
    },
  });
  return {
    id: response.json().session_id as string,
    cookie: response.cookies
      .map((item) => `${item.name}=${item.value}`)
      .join("; "),
  };
}

async function drainWorker() {
  for (let i = 0; i < 10; i += 1) {
    if (!(await runtime.worker.runOnce())) break;
  }
}

describe("caraballeda pack live replay", () => {
  it("keeps E01/E02 provenance independent and omits hidden pack fields", () => {
    const e01 = packEvent("E01");
    const e02 = packEvent("E02");
    expect(runtimeSourceId(e01)).toBe("demo-author-e01");
    expect(runtimeSourceId(e02)).toBe("rescue-alpha");
    expect(runtimeSourceId(e01)).not.toBe(runtimeSourceId(e02));
    const raw = packRadioRaw("session-test", "tx-test", "input-test", "E02");
    expect(raw.transcript).toBe(byId.E02.raw_input.transcript);
    expect(raw.speaker_id).toBe("rescue-alpha");
    expect(() => assertObservablePayload(raw)).not.toThrow();
    expect(JSON.stringify(raw)).not.toContain("intended_referent");
    for (const siteId of SITE_ID_SHORTCUTS) {
      expect(JSON.stringify(raw)).not.toContain(siteId);
    }
    expect(DEMO_STEPS.map((step) => step.event_id)).toEqual(["E01", "E02", "E11"]);
    expect(DEMO_OMITTED).toEqual([
      { event_id: "E12", source_type: "drone", reason: "drone_verification" },
    ]);
  });

  it("emits pack observable text in deterministic order without intended_referent", async () => {
    const s = await session();
    const headers = { cookie: s.cookie };
    const replayed = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/replay`,
      headers,
      payload: { fixture_name: CARABALLEDA_DEMO, play: "immediate" },
    });
    expect(replayed.statusCode, replayed.body).toBe(202);
    expect(replayed.json().events.map((item: { event_id: string }) => item.event_id)).toEqual([
      "E01",
      "E02",
      "E11",
    ]);
    await drainWorker();
    const stored = [
      ...runtime.storage
        .all<{ payload_json: string }>(
          "SELECT payload_json FROM external_reports WHERE session_id = ?",
          s.id,
        )
        .map((row) => row.payload_json),
      ...runtime.storage
        .all<{ payload_json: string }>(
          "SELECT payload_json FROM raw_inputs r JOIN transmissions t ON t.id = r.transmission_id WHERE t.session_id = ?",
          s.id,
        )
        .map((row) => row.payload_json),
    ].join("\n");
    expect(stored).not.toContain("intended_referent");
    expect(stored).not.toContain("hidden_truth_linkage");
    expect(stored).not.toContain("scenario_ground_truth");
    for (const siteId of SITE_ID_SHORTCUTS) {
      expect(stored).not.toContain(siteId);
    }
    const snapshot = (
      await runtime.app.inject({
        url: `/api/demo-sessions/${s.id}/intelligence`,
        headers,
      })
    ).json<IntelligenceSnapshot>();
    expect(JSON.stringify(snapshot)).not.toContain("intended_referent");
    expect(JSON.stringify(snapshot)).not.toContain("hidden_truth_linkage");
    expect(JSON.stringify(snapshot)).not.toContain("scenario_ground_truth");
    const social = snapshot.reports.filter((item) => item.source === "social");
    const radio = snapshot.reports.filter((item) => item.source === "radio");
    expect(social).toHaveLength(2);
    expect(radio).toHaveLength(1);
    expect(social.map((item) => item.text)).toEqual([
      byId.E11.raw_input.text,
      byId.E01.raw_input.text,
    ]);
    expect(radio[0]?.text).toBe(byId.E02.raw_input.transcript);
    expect(radio[0]?.author).toBe("rescue-alpha");
    const e01 = social.find((item) => item.text === byId.E01.raw_input.text)!;
    const e11 = social.find((item) => item.text === byId.E11.raw_input.text)!;
    expect(e01.author).toBe("demo-author-e01");
    expect(e11.author).toBe("demo-author-e11");
    expect(e01.author).not.toBe(radio[0]?.author);
    expect(e01.observations.every((item) => item.source_id === "demo-author-e01")).toBe(true);
    expect(radio[0]?.observations.every((item) => item.source_id === "rescue-alpha")).toBe(true);
    expect(radio[0]?.observations.every((item) => item.location.resolved_site_id === null)).toBe(
      true,
    );
    expect(e01.locations).toHaveLength(0);
    expect(radio[0]?.locations).toHaveLength(0);
    expect(e11.locations[0]?.method).toBe("exact_name");
    expect(e11.locations[0]?.site.name).toBe("Colegio Tanaguarenas");
    expect(snapshot.reports.some((item) => item.source === "drone")).toBe(false);
    expect(e01.timestamp < radio[0]!.timestamp).toBe(true);
    expect(radio[0]!.timestamp < e11.timestamp).toBe(true);
  });

  it("replays with stable radio input ids and social dedup", async () => {
    const s = await session();
    const headers = { cookie: s.cookie };
    const play = () =>
      runtime.app.inject({
        method: "POST",
        url: `/api/demo-sessions/${s.id}/replay`,
        headers,
        payload: { fixture_name: CARABALLEDA_DEMO, play: "immediate" },
      });
    expect((await play()).statusCode).toBe(202);
    await drainWorker();
    const first = (
      await runtime.app.inject({
        url: `/api/demo-sessions/${s.id}/intelligence`,
        headers,
      })
    ).json<IntelligenceSnapshot>();
    expect((await play()).statusCode).toBe(202);
    await drainWorker();
    const second = (
      await runtime.app.inject({
        url: `/api/demo-sessions/${s.id}/intelligence`,
        headers,
      })
    ).json<IntelligenceSnapshot>();
    expect(second.reports).toHaveLength(first.reports.length);
    expect(second.reports.map((item) => item.id).sort()).toEqual(
      first.reports.map((item) => item.id).sort(),
    );
    const inputId = stablePackRadioInputId(s.id, "E02");
    expect(
      runtime.storage.one("SELECT 1 FROM raw_inputs WHERE input_id = ?", inputId),
    ).toBeTruthy();
    const radioRows = runtime.storage.all(
      "SELECT id FROM transmissions WHERE session_id = ?",
      s.id,
    );
    expect(radioRows).toHaveLength(1);
  });

  it("rejects a second timed pack replay while one is running", async () => {
    const s = await session();
    const headers = { cookie: s.cookie };
    const first = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/replay`,
      headers,
      payload: { fixture_name: CARABALLEDA_DEMO, play: "timed" },
    });
    expect(first.statusCode).toBe(202);
    const second = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/replay`,
      headers,
      payload: { fixture_name: CARABALLEDA_DEMO, play: "timed" },
    });
    expect(second.statusCode).toBe(409);
  });

  it("E01 then E02 resolve to the same canonical W1 and corroborate one concern", async () => {
    const W1 = "osm:way:1533952351";
    const s = await session();
    const headers = { cookie: s.cookie };
    const e01 = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/ingest`,
      headers,
      payload: {
        source: CARABALLEDA_DEMO,
        provider: "fixture",
        event_ids: ["E01"],
        story: CARABALLEDA_DEMO,
      },
    });
    expect(e01.statusCode, e01.body).toBe(200);
    const afterE01 = (
      await runtime.app.inject({
        url: `/api/demo-sessions/${s.id}/belief-state`,
        headers,
      })
    ).json<BeliefSnapshot>();
    expect(worksiteSiteIds(afterE01)).toEqual([W1]);
    const trappedAfterE01 = incidentsOf(afterE01, "trapped_victims");
    expect(trappedAfterE01).toHaveLength(1);
    expect(trappedAfterE01[0]?.site_id).toBe(W1);
    expect(trappedAfterE01[0]?.verification_status).toBe("unverified");

    const replayed = await runtime.app.inject({
      method: "POST",
      url: `/api/demo-sessions/${s.id}/replay`,
      headers,
      payload: { fixture_name: CARABALLEDA_DEMO, play: "immediate" },
    });
    expect(replayed.statusCode, replayed.body).toBe(202);
    await drainWorker();

    const snapshot = (
      await runtime.app.inject({
        url: `/api/demo-sessions/${s.id}/intelligence`,
        headers,
      })
    ).json<IntelligenceSnapshot>();
    const socialE01 = snapshot.reports.find(
      (item) => item.source === "social" && item.text === byId.E01.raw_input.text,
    );
    const radioE02 = snapshot.reports.find((item) => item.source === "radio");
    expect(socialE01?.observations.every((item) =>
      (item.location.raw_text ?? "").includes("Avenida Principal de Caribe"),
    )).toBe(true);
    expect(radioE02?.observations.every((item) =>
      (item.location.raw_text ?? "").includes("Avenida Principal de Caribe"),
    )).toBe(true);

    const belief = (
      await runtime.app.inject({
        url: `/api/demo-sessions/${s.id}/belief-state`,
        headers,
      })
    ).json<BeliefSnapshot>();
    expect(worksiteSiteIds(belief)).toContain(W1);
    const trapped = incidentsOf(belief, "trapped_victims").filter(
      (item) => item.site_id === W1,
    );
    expect(trapped).toHaveLength(1);
    expect(trapped[0]?.verification_status).toBe("corroborated");
    expect(Number(belief.state_version)).toBeGreaterThan(Number(afterE01.state_version));
  });
});

function worksiteSiteIds(snapshot: BeliefSnapshot) {
  return snapshot.worksites.map((item) => (item as { site_id: string }).site_id);
}

function incidentsOf(snapshot: BeliefSnapshot, incidentType: string) {
  return (
    snapshot.incidents as Array<{
      incident_type: string;
      site_id: string;
      verification_status: string;
    }>
  ).filter((item) => item.incident_type === incidentType);
}
