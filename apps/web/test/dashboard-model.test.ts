import { describe, expect, it } from "vitest";
import type { IntelligenceReport } from "../../../contracts/intelligence";
import {
  activityEvents,
  reportLocation,
  reportTitle,
  reportTone,
} from "../src/dashboard/model";

const site = {
  id: "osm:way:1",
  name: "Residencias Las Anclas",
  latitude: 10.61,
  longitude: -66.84,
  facility: null,
  height: 6,
  heightSource: "display_default" as const,
};
function observation(
  incident_type: string,
  raw_text = "Residencias Las Anclas",
) {
  return {
    incident_type,
    location: { raw_text },
    victim_indication: { type: "unknown" },
    structural_hazard: "unknown",
    access_status: "unknown",
  } as unknown as IntelligenceReport["observations"][number];
}
function report(
  overrides: Partial<IntelligenceReport> = {},
): IntelligenceReport {
  return {
    id: "r1",
    source: "social",
    author: "@resident",
    timestamp: "2026-09-19T14:00:00Z",
    text: "text",
    mode: "social_replay",
    state: "extracted",
    observations: [],
    locations: [],
    sourceUrl: null,
    model: "fixture",
    ...overrides,
  };
}

describe("dashboard read-model helpers", () => {
  it("derives tone, title and location without inventing coordinates", () => {
    const trapped = report({
      observations: [observation("trapped_victims"), observation("road_blocked")],
    });
    expect(reportTone(trapped)).toBe("red");
    expect(reportTitle(trapped)).toBe("trapped victims · road blocked");
    expect(reportLocation(trapped)).toBe("Residencias Las Anclas");
    expect(reportTone(report({ observations: [observation("road_blocked")] }))).toBe("amber");
    const filtered = report({ state: "filtered" });
    expect(reportTone(filtered)).toBe("cyan");
    expect(reportTitle(filtered)).toBe("No actionable information");
    expect(reportLocation(report({ observations: [observation("road_blocked", "")] }))).toBe(
      "Location not established",
    );
    expect(reportLocation(report({ locations: [{ site, method: "exact_name" }] }))).toBe(site.name);
  });

  it("builds a newest-first session feed from reports and manual placements only", () => {
    const events = activityEvents([
      report({
        id: "radio",
        source: "radio",
        timestamp: "2026-09-19T14:05:00Z",
        observations: [observation("structural_damage")],
        locations: [{ site, method: "exact_name" }],
      }),
      report({
        id: "placed",
        timestamp: "2026-09-19T14:01:00Z",
        observations: [observation("road_blocked", "somewhere uphill")],
        locations: [{ site, method: "manual", locatedAt: "2026-09-19T14:30:00Z" }],
      }),
      report({ id: "noise", timestamp: "2026-09-19T14:03:00Z", state: "filtered" }),
      report({ id: "broken", source: "radio", timestamp: "2026-09-19T14:02:00Z", state: "failed" }),
    ]);
    expect(events.map((event) => event.title)).toEqual([
      "Report placed on map",
      "Radio transmission processed",
      "External report filtered",
      "Radio transmission extraction failed",
      "External report processed",
    ]);
    expect(events[0]).toMatchObject({
      reportId: "placed",
      at: "2026-09-19T14:30:00Z",
      tone: "cyan",
    });
    expect(events[0].detail).toContain(site.name);
    expect(events[1].detail).toBe(site.name);
    // Exact-name matches carry no separate timestamp and must not appear as placement events.
    expect(events.filter((event) => event.title === "Report placed on map")).toHaveLength(1);
    expect(new Set(events.map((event) => event.id)).size).toBe(events.length);
  });
});
