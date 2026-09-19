import type { IntelligenceReport } from "../../../contracts/intelligence.js";
/** Queue adapter for already located reports. No new name resolution or incident fusion. */
export function scanIntake(reports: IntelligenceReport[]) {
  return reports.filter(r => r.source !== "drone" && r.observations.length).flatMap(r => r.locations.map(({ site }) => ({
    report_id: r.id, observations: r.observations,
    site: { schema_version: "2.1", site_id: site.id, display_name: site.name,
      site_type: site.facility ? "facility" : "building", canonical_source: "osm",
      canonical_source_id: site.id.replace(/^osm:/, "").replace(":", "/"),
      centroid: { latitude: site.latitude, longitude: site.longitude }, criticality: 0,
      ...(site.facility ? { facility_type: site.facility } : {}) },
  })));
}
