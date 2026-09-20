import { expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { IntelligenceReport } from "../../../contracts/intelligence";
import { scannedBuildingReports, ScanPhotoOverlay } from "../src/dashboard/ScanPhotoOverlay";
function scan(id: string, site: string, people: "present" | "none", images = 1, at = "2026-09-19T14:10:00Z"): IntelligenceReport {
  return { id, source: "drone", author: "drone-1", timestamp: at, text: "scan", mode: "simulated_scan", state: "extracted", sourceUrl: null, model: null,
    locations: [{ site: { id: site, name: site, latitude: 10, longitude: -66, facility: null, height: 6, heightSource: "display_default" }, method: "canonical_site" }],
    observations: [], inspection: { people, people_count: people === "present" ? 3 : 0, result: "confirmed", damage: "major_damage", source: "test",
      images: Array.from({ length: images }, (_, i) => ({ image_url: `/img/${id}/${i}`, caption: "test" })) } };
}
it("keeps one completed-scan marker per building, using the newest report", () => {
  const cards = scannedBuildingReports([scan("a", "w1", "present"), scan("b", "w7", "none"), scan("c", "w2", "present", 0),
    scan("d", "w1", "present", 1, "2026-09-19T14:20:00Z"), { ...scan("e", "w5", "present"), source: "social" }]);
  expect(cards.map(c => c.id)).toEqual(["d", "b", "c"]);
});
it("starts with a small scan-result dot and hides its detail label", () => {
  const markup = renderToStaticMarkup(createElement(ScanPhotoOverlay, {
    map: { project: () => ({ x: 100, y: 100 }) } as any,
    reports: [scan("a", "Mariola", "present")],
    onReport: () => {},
  }));
  expect(markup).toContain("scan-result-dot people");
  expect(markup).toContain('aria-expanded="false"');
  expect(markup).not.toContain("<strong>Mariola</strong>");
  expect(markup).not.toContain("3 people trapped");
  expect(markup).not.toContain("Simulated drone finding");
  expect(markup).not.toContain("Show photo");
  expect(markup).not.toContain("<img");
  expect(markup).not.toContain("/img/a/0");
});
it("uses a green marker for a clear scan and still keeps its label hidden", () => {
  const markup = renderToStaticMarkup(createElement(ScanPhotoOverlay, {
    map: { project: () => ({ x: 100, y: 100 }) } as any,
    reports: [scan("a", "Mariola", "none")],
    onReport: () => {},
  }));
  expect(markup).toContain("scan-result-dot clear");
  expect(markup).not.toContain("No people found");
  expect(markup).not.toContain("<img");
});
