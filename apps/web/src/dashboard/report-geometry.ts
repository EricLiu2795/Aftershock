import type { FeatureCollection, Position, Polygon, MultiPolygon } from "geojson";
import type { IntelligenceReport } from "../../../../contracts/intelligence";
import { siteScanStatuses, type ScanStatus } from "./site-scan-status";
const normalize = (s: string) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
function ringContains(p: Position, ring: Position[]) {
  let inside=false;
  for(let i=0,j=ring.length-1;i<ring.length;j=i++) {
    const a=ring[i]!,b=ring[j]!;
    if ((a[1]!>p[1]!) !== (b[1]!>p[1]!) && p[0]! < (b[0]!-a[0]!)*(p[1]!-a[1]!)/(b[1]!-a[1]!)+a[0]!) inside=!inside;
  }
  return inside;
}
export function containsPoint(p: Position, geometry: Polygon | MultiPolygon) {
  const polygons=geometry.type==="Polygon" ? [geometry.coordinates] : geometry.coordinates;
  return polygons.some(rings => !!rings[0] && ringContains(p,rings[0]) && !rings.slice(1).some(r=>ringContains(p,r)));
}
export function reportGeometry(geography: FeatureCollection, reports: IntelligenceReport[], resolvedSites: string[] = []) {
  const polygons=geography.features.filter(f=>f.geometry.type==="Polygon" || f.geometry.type==="MultiPolygon");
  const ids=new Set(polygons.map(f=>String(f.properties?.id)));
  const statuses=siteScanStatuses(reports, [], resolvedSites);
  const buildings: Record<string, ScanStatus>={}, represented=new Set<string>(), roads=new Set<string>();
  for(const report of [...reports].sort((a,b)=>a.timestamp.localeCompare(b.timestamp))) {
    if(!report.observations.length)continue;
    for(const {site} of report.locations) {
      const matches=ids.has(site.id) ? [site.id] : polygons.filter(f=>containsPoint([site.longitude,site.latitude],f.geometry as Polygon|MultiPolygon)).map(f=>String(f.properties?.id));
      // Only a unique containing footprint can represent a point site.
      if(matches.length===1 && statuses[site.id]) {
        buildings[matches[0]!]=statuses[site.id]!; represented.add(site.id);
      }
    }
  }
  // Road condition is not inferred from report text. A report naming "Avenida
  // Principal de Caribe" used to redden every segment carrying that name -- 41
  // segments, 5.4 km, against the 366 m actually blocked -- reading as a far
  // larger closure than the scenario describes.
  //
  // Roads are coloured only from canonical RoadState, which the drone road sensor
  // emits per segment when a flight sweeps within its radius (see
  // search_planner/road_scan.py and data/scenarios/caraballeda/drone_road_conditions.json).
  // Reports still colour buildings above; they no longer touch roads.
  return {buildings,represented,roads:[] as string[],siteStatuses:statuses};
}
