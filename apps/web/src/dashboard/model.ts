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
