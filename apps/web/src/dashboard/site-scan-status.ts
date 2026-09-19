import type { IntelligenceReport } from "../../../../contracts/intelligence";
export type ScanStatus = { status: "unverified" | "people_indicated" | "none_indicated"; color: string };
const yellow: ScanStatus = { status: "unverified", color: "#f6bc5a" };
/** Latest located source controls the display; colors never rewrite operational truth. */
export function siteScanStatuses(reports: IntelligenceReport[], queuedSites: string[] = []): Record<string, ScanStatus> {
  const statuses: Record<string, ScanStatus> = Object.fromEntries(queuedSites.map(id => [id, yellow]));
  for (const report of [...reports].sort((a,b) => a.timestamp.localeCompare(b.timestamp) || a.id.localeCompare(b.id))) {
    if (!report.observations.length) continue;
    for (const { site } of report.locations) {
      const victims = report.observations.filter(o => o.incident_type === "trapped_victims" && (!o.location.resolved_site_id || o.location.resolved_site_id === site.id)).map(o => o.victim_indication.type);
      statuses[site.id] = report.source !== "drone" || !victims.length ? yellow
        : victims.some(v => !["none","unknown"].includes(v)) ? { status:"people_indicated",color:"#fa626b" }
        : victims.every(v => v === "none") ? { status:"none_indicated",color:"#29ce9e" } : yellow;
    }
  }
  return statuses;
}
