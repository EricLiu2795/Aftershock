import { expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { IntelligenceReport } from "../../../contracts/intelligence";
import { redScanPhotos, ScanPhotoOverlay } from "../src/dashboard/ScanPhotoOverlay";
function scan(id: string, site: string, people: "present" | "none", images = 1, at = "2026-09-19T14:10:00Z"): IntelligenceReport {
  return { id, source: "drone", author: "drone-1", timestamp: at, text: "scan", mode: "simulated_scan", state: "extracted", sourceUrl: null, model: null,
    locations: [{ site: { id: site, name: site, latitude: 10, longitude: -66, facility: null, height: 6, heightSource: "display_default" }, method: "canonical_site" }],
    observations: [], inspection: { people, people_count: people === "present" ? 3 : 0, result: "confirmed", damage: "major_damage", source: "test",
      images: Array.from({ length: images }, (_, i) => ({ image_url: `/img/${id}/${i}`, caption: "test" })) } };
}
it("shows one photo per red building and never for green or photo-less scans", () => {
  const cards = redScanPhotos([scan("a", "w1", "present"), scan("b", "w7", "none"), scan("c", "w2", "present", 0),
    scan("d", "w1", "present", 1, "2026-09-19T14:20:00Z"), { ...scan("e", "w5", "present"), source: "social" }]);
  expect(cards.map(c => c.id)).toEqual(["d"]);
});
it("starts with a compact text popup and loads photos only on demand", () => {
  const markup = renderToStaticMarkup(createElement(ScanPhotoOverlay, {
    map: { project: () => ({ x: 100, y: 100 }) } as any,
    reports: [scan("a", "Mariola", "present")],
    onReport: () => {},
  }));
  expect(markup).toContain("Mariola");
  expect(markup).toContain("3 people trapped");
  expect(markup).toContain("Simulated finding");
  expect(markup).toContain('aria-expanded="false"');
  expect(markup).toContain("Show photo");
  expect(markup).not.toContain("<img");
  expect(markup).not.toContain("/img/a/0");
});
it("labels a resolved rescue without continuing to claim people are trapped", () => {
  const markup = renderToStaticMarkup(createElement(ScanPhotoOverlay, {
    map: { project: () => ({ x: 100, y: 100 }) } as any,
    reports: [scan("a", "Mariola", "present")],
    resolvedSites: ["Mariola"],
    onReport: () => {},
  }));
  expect(markup).toContain("Rescue complete");
  expect(markup).toContain("scan-photo-card rescued");
  expect(markup).not.toContain("people trapped");
  expect(markup).not.toContain("<img");
});
