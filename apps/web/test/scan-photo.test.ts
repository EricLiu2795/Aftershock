import { expect, it } from "vitest";
import type { IntelligenceReport } from "../../../contracts/intelligence";
import { redScanPhotos } from "../src/dashboard/ScanPhotoOverlay";
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
