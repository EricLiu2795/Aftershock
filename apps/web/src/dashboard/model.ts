import type { IntelligenceReport } from "../../../../contracts/intelligence";
export const human = (value: string) => value.replaceAll("_", " ");
export function reportTitle(report: IntelligenceReport): string {
  const types = [
    ...new Set(report.observations.map((item) => item.incident_type)),
  ];
  return types.length
    ? types.map(human).join(" · ")
    : report.state === "filtered"
      ? "No actionable information"
      : human(report.state);
}
export function reportTone(
  report: IntelligenceReport,
): "red" | "amber" | "cyan" {
  if (
    report.observations.some((item) =>
      ["trapped_victims", "fire_or_secondary_hazard"].includes(
        item.incident_type,
      ),
    )
  )
    return "red";
  return report.observations.length ? "amber" : "cyan";
}
export function reportLocation(report: IntelligenceReport): string {
  return (
    report.locations.map((item) => item.site.name).join(" · ") ||
    report.observations.find((item) => item.location.raw_text)?.location
      .raw_text ||
    "Location not established"
  );
}
export const time = (value: string) =>
  new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
export const coordinates = (site: { latitude: number; longitude: number }) =>
  `${site.latitude.toFixed(5)}, ${site.longitude.toFixed(5)}`;

/** Persisted radio locations remain highlighted until a future verification layer exists. */
export function radioAlertSiteIds(reports: IntelligenceReport[]): string[] {
  return [
    ...new Set(
      reports
        .filter((report) => report.source === "radio")
        .flatMap((report) => report.locations.map(({ site }) => site.id)),
    ),
  ];
}

export interface ActivityEvent {
  id: string;
  reportId: string;
  at: string;
  tone: "red" | "amber" | "cyan";
  title: string;
  detail: string;
}
/** Session activity derived from the read model: report arrivals/processing and manual placements. */
export function activityEvents(reports: IntelligenceReport[]): ActivityEvent[] {
  const events: ActivityEvent[] = [];
  for (const report of reports) {
    const kind =
      report.source === "radio" ? "Radio transmission" : "External report";
    const outcome = report.observations.length
      ? "processed"
      : report.state === "filtered"
        ? "filtered"
        : report.state === "failed"
          ? "extraction failed"
          : "received";
    events.push({
      id: `${report.id}:report`,
      reportId: report.id,
      at: report.timestamp,
      tone: reportTone(report),
      title: `${kind} ${outcome}`,
      detail: reportLocation(report),
    });
    for (const location of report.locations) {
      if (location.method === "manual" && location.locatedAt)
        events.push({
          id: `${report.id}:${location.site.id}`,
          reportId: report.id,
          at: location.locatedAt,
          tone: "cyan",
          title: "Report placed on map",
          detail: `${location.site.name} · manual placement, claim unverified`,
        });
    }
  }
  return events.sort((a, b) => b.at.localeCompare(a.at));
}
