import type { IntelligenceReport } from "../../../../contracts/intelligence";
export type ScanStatus = { status: "unverified" | "people_indicated" | "none_indicated"; color: string };
export const yellow: ScanStatus = { status: "unverified", color: "#f6bc5a" };
export const red: ScanStatus = { status: "people_indicated", color: "#fa626b" };
export const green: ScanStatus = { status: "none_indicated", color: "#29ce9e" };
function droneFinding(report: IntelligenceReport, siteId: string): ScanStatus | undefined {
  if (report.inspection?.people === "present") return red;
  if (report.inspection?.people === "none") return green;
  const victims = report.observations.filter(o =>
    (!o.location.resolved_site_id || o.location.resolved_site_id === siteId)
    && (o.incident_type === "trapped_victims" || report.source === "drone")).map(o => o.victim_indication.type);
  if (!victims.length) return;
  if (victims.some(v => !["none", "unknown"].includes(v))) return red;
  if (victims.every(v => v === "none")) return green;
}
/** Scanned victim findings take precedence over source rumors, independently of damage. */
export function siteScanStatuses(reports: IntelligenceReport[], queuedSites: string[] = []): Record<string, ScanStatus> {
  const statuses: Record<string, ScanStatus> = Object.fromEntries(queuedSites.map(id => [id, yellow]));
  for (const report of [...reports].sort((a,b) => Number(a.source === "drone") - Number(b.source === "drone") || a.timestamp.localeCompare(b.timestamp) || a.id.localeCompare(b.id))) {
    if (!report.observations.length && !report.inspection) continue;
    for (const { site } of report.locations) {
      statuses[site.id] = report.source === "drone" ? droneFinding(report, site.id) ?? yellow : yellow;
    }
  }
  return statuses;
}
