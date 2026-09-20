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
function nearRoad(p: Position, line: Position[], radius=100) {
  const sx=111195*Math.cos(p[1]!*Math.PI/180),sy=111195;
  for(let i=1;i<line.length;i++) {
    const a=line[i-1]!,b=line[i]!;
    const ax=(a[0]!-p[0]!)*sx,ay=(a[1]!-p[1]!)*sy,dx=(b[0]!-a[0]!)*sx,dy=(b[1]!-a[1]!)*sy;
    const len=dx*dx+dy*dy,t=len ? Math.max(0,Math.min(1,-(ax*dx+ay*dy)/len)) : 0;
    if(Math.hypot(ax+t*dx,ay+t*dy)<=radius)return true;
  }
  return false;
}
export function reportGeometry(geography: FeatureCollection, reports: IntelligenceReport[]) {
  const polygons=geography.features.filter(f=>f.geometry.type==="Polygon" || f.geometry.type==="MultiPolygon");
  const ids=new Set(polygons.map(f=>String(f.properties?.id)));
  const statuses=siteScanStatuses(reports);
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
    // Building scans (drone) describe the building's access, not a road. Roads are only
    // colored when a non-drone report names that road explicitly; nearby roads never
    // inherit a building's color.
    if(report.source==="drone")continue;
    const relevant=report.observations.filter(o=>o.incident_type==="access_blocked" || o.access_status!=="unknown" || /\b(road|street|avenida|calle)\b/i.test(o.location.raw_text ?? ""));
    if(!relevant.length)continue;
    const text=normalize([report.text,...relevant.map(o=>o.location.raw_text ?? "")].join(" "));
    const candidates=geography.features.filter(f=>f.properties?.kind==="road" && f.geometry.type==="LineString");
    for(const road of candidates) {
      const name=normalize(String(road.properties?.name ?? ""));
      if(name.length>=5 && (` ${text} `).includes(` ${name} `))roads.add(String(road.properties?.id));
    }
  }
  return {buildings,represented,roads:[...roads],siteStatuses:statuses};
}
