import { useEffect, useState } from "react";
import type { Map as LibreMap } from "maplibre-gl";
import type { IntelligenceReport } from "../../../../contracts/intelligence";
import "./drone-tracking.css";

/** One dot per completed drone scan. The result stays hidden until its dot is selected. */
export function scannedBuildingReports(reports: IntelligenceReport[]) {
  const seen = new Set<string>();
  return [...reports]
    .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
    .filter(r => r.source === "drone" && r.inspection && r.locations.length)
    .filter(r => { const id = r.locations[0]!.site.id; if (seen.has(id)) return false; seen.add(id); return true; });
}

export function ScanPhotoOverlay({ map, reports, onReport, resolvedSites = [] }: { map: LibreMap; reports: IntelligenceReport[]; onReport(id: string): void; resolvedSites?: string[] }) {
  const [, setTick] = useState(0);
  const [opened, setOpened] = useState<string>();
  useEffect(() => {
    const render = () => setTick(t => t + 1);
    map.on("move", render); map.on("resize", render);
    return () => { map.off("move", render); map.off("resize", render); };
  }, [map]);
  const scans = scannedBuildingReports(reports);
  if (!scans.length) return null;
  return <div className="drone-map-overlay" aria-label="Completed drone scans">
    {scans.map(report => {
      const site = report.locations[0]!.site;
      const image = report.inspection!.images[0];
      const count = report.inspection!.people_count;
      const pixel = map.project([site.longitude, site.latitude]);
      const isOpen = opened === report.id;
      const rescued = resolvedSites.includes(site.id);
      const people = report.inspection!.people;
      return <div key={report.id} className="scan-result" style={{ left: pixel.x, top: pixel.y }}>
        <button type="button" className={`scan-result-dot ${people === "present" ? "people" : people === "none" ? "clear" : "unknown"}`}
          aria-expanded={isOpen} aria-label={`${isOpen ? "Hide" : "Show"} drone scan for ${site.name}`}
          onClick={() => setOpened(isOpen ? undefined : report.id)} />
        {isOpen && <article className={`scan-result-label${rescued ? " rescued" : ""}`}>
          <button type="button" className="scan-result-close" aria-label={`Close ${site.name} scan result`} onClick={() => setOpened(undefined)}>×</button>
          <strong>{site.name}</strong>
          <span>{rescued ? "Rescue complete" : people === "present" ? `${count ?? "Unknown"} ${count === 1 ? "person" : "people"} trapped` : people === "none" ? "No people found" : "Finding unavailable"}</span>
          <small>Simulated drone finding</small>
          {image && <button type="button" className="scan-photo-toggle" onClick={() => onReport(report.id)}>Show photo</button>}
        </article>}
      </div>;
    })}
  </div>;
}
