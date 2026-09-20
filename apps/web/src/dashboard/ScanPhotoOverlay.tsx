import { useEffect, useState } from "react";
import type { Map as LibreMap } from "maplibre-gl";
import type { IntelligenceReport } from "../../../../contracts/intelligence";
import "./drone-tracking.css";

/** Drone scan reports that found people and carry a building photo. Green/unknown scans never show imagery. */
export function redScanPhotos(reports: IntelligenceReport[]) {
  const seen = new Set<string>();
  return [...reports]
    .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
    .filter(r => r.source === "drone" && r.inspection?.people === "present" && r.inspection.images.length && r.locations.length)
    .filter(r => { const id = r.locations[0]!.site.id; if (seen.has(id)) return false; seen.add(id); return true; });
}

export function ScanPhotoOverlay({ map, reports, onReport, resolvedSites = [] }: { map: LibreMap; reports: IntelligenceReport[]; onReport(id: string): void; resolvedSites?: string[] }) {
  const [, setTick] = useState(0);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string>();
  useEffect(() => {
    const render = () => setTick(t => t + 1);
    map.on("move", render); map.on("resize", render);
    return () => { map.off("move", render); map.off("resize", render); };
  }, [map]);
  const cards = redScanPhotos(reports).filter(r => !dismissed.has(r.id));
  if (!cards.length) return null;
  return <div className="drone-map-overlay" aria-label="Drone scan photos">
    {cards.map(report => {
      const site = report.locations[0]!.site;
      const image = report.inspection!.images[0]!;
      const count = report.inspection!.people_count;
      const pixel = map.project([site.longitude, site.latitude]);
      const showPhoto = expanded === report.id;
      const rescued = resolvedSites.includes(site.id);
      return <figure key={report.id} className={`scan-photo-card${showPhoto ? " expanded" : ""}${rescued ? " rescued" : ""}`} style={{ left: pixel.x, top: pixel.y }}>
        {showPhoto && <img src={image.image_url} alt={image.caption?.startsWith("Real satellite") ? `Post-earthquake satellite image around ${site.name}` : `Simulated drone photo of ${site.name}`} onClick={() => onReport(report.id)} />}
        <button type="button" className="scan-photo-close" aria-label={`Dismiss ${site.name} popup`} onClick={() => setDismissed(prev => new Set([...prev, report.id]))}>×</button>
        <figcaption>
          <strong>{site.name}</strong>
          {rescued ? "Rescue complete" : count != null && count > 0 ? `${count} ${count === 1 ? "person" : "people"} trapped` : "People trapped"}
          <br /><small>Simulated finding</small>
          <button type="button" className="scan-photo-toggle" aria-expanded={showPhoto} aria-label={`${showPhoto ? "Hide" : "Show"} photo of ${site.name}`} onClick={() => setExpanded(showPhoto ? undefined : report.id)}>{showPhoto ? "Hide photo" : "Show photo"}</button>
          {showPhoto && <small>{image.caption?.startsWith("Real satellite") ? image.caption : "Simulated drone scan · not verified"}</small>}
        </figcaption>
      </figure>;
    })}
  </div>;
}
