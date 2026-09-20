import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import type { FeatureCollection } from "geojson";
import type { IntelligenceReport } from "../../../contracts/intelligence";
import { reportGeometry, containsPoint } from "../src/dashboard/report-geometry";
const raw=JSON.parse(readFileSync(new URL("../../../data/graph_exports/caraballeda.geojson",import.meta.url),"utf8"));
const geography:FeatureCollection={...raw,features:raw.features.map((f:any)=>({...f,properties:{id:f.id,kind:f.properties.site ? "site":"road",name:f.properties.osm_tags?.name ?? ""}}))};
function report(id:string, name:string, lon:number, lat:number):IntelligenceReport {
  return {id,source:"social",author:"fixture",timestamp:"2026-09-19T14:08:00Z",text:name,mode:"replay",state:"extracted",model:"fixture",sourceUrl:null,
    locations:[{method:"exact_name",site:{id,name,longitude:lon,latitude:lat,facility:null,height:6,heightSource:"display_default"}}],
    observations:[{schema_version:"2.1",observation_id:id,timestamp:"2026-09-19T14:08:00Z",source_type:"social",location:{latitude:null,longitude:null,raw_text:name},incident_type:"trapped_victims",victim_indication:{type:"possible_multiple"},structural_hazard:"unknown",access_status:"unknown",verification_status:"unverified"}]};
}
it("starts empty and colors both the actual W2 footprint and containing school footprint",()=>{
  expect(reportGeometry(geography,[]).buildings).toEqual({});
  expect(reportGeometry(geography,[]).roads).toEqual([]);
  const a=report("osm:way:1533952352","Residencias Caribe B",-66.83892125,10.61471635);
  const b=report("osm:node:13976280529","Colegio Tanaguarenas",-66.8354097,10.6135029);
  const result=reportGeometry(geography,[a,b]);
  expect(Object.keys(result.buildings).sort()).toEqual(["osm:way:1533952352","osm:way:303729130"].sort());
  expect(Object.values(result.buildings).every(s=>s.status==="unverified")).toBe(true);
  expect(result.represented.size).toBe(2); // Neither needs a dot hiding the building.
  expect(result.roads).toEqual([]);
  a.source="radio";
  expect(reportGeometry(geography,[a]).buildings[a.locations[0]!.site.id]?.status).toBe("unverified");
});
it("never colors roads from report text; road condition comes from RoadState",()=>{
  // A report naming a street used to redden every segment carrying that name --
  // "Avenida Principal de Caribe" is 41 segments and 5.4 km, against the 366 m the
  // scenario actually blocks. Road condition is now sourced only from canonical
  // RoadState, produced per segment by the drone road sensor.
  const r=report("s","Avenida La Playa",-66.83892125,10.61471635);
  r.observations[0]!.incident_type="access_blocked";r.observations[0]!.access_status="blocked";r.locations=[];
  expect(reportGeometry(geography,[r]).roads).toEqual([]);
  const near=report("s","Building access blocked",0,0);near.observations[0]!.incident_type="access_blocked";
  const g:FeatureCollection={type:"FeatureCollection",features:[{type:"Feature",properties:{id:"near",kind:"road",name:"Calle Larga"},geometry:{type:"LineString",coordinates:[[-.001,.0005],[.001,.0005]]}},{type:"Feature",properties:{id:"far",kind:"road",name:"Calle Lejana"},geometry:{type:"LineString",coordinates:[[-.001,.01],[.001,.01]]}}]};
  expect(reportGeometry(g,[near]).roads).toEqual([]);
  // A drone building scan still never colors the street outside.
  const scan=report("osm:way:1533952352","Residencias Caribe B",-66.83892125,10.61471635);
  scan.source="drone";scan.observations[0]!.access_status="limited";scan.observations[0]!.incident_type="access_blocked";
  expect(reportGeometry(geography,[scan]).roads).toEqual([]);
  // Buildings are still coloured from reports.
  expect(Object.keys(reportGeometry(geography,[scan]).buildings).length).toBeGreaterThan(0);
});
it("does not attach point sites to polygon holes or ambiguous overlapping footprints",()=>{
  expect(containsPoint([0,0],{type:"Polygon",coordinates:[[[-2,-2],[2,-2],[2,2],[-2,2],[-2,-2]],[[-1,-1],[1,-1],[1,1],[-1,1],[-1,-1]]]})).toBe(false);
  const g:FeatureCollection={type:"FeatureCollection",features:[1,2].map(i=>({type:"Feature",properties:{id:String(i)},geometry:{type:"Polygon",coordinates:[[[-1,-1],[1,-1],[1,1],[-1,1],[-1,-1]]]}}))};
  expect(reportGeometry(g,[report("point","Point",0,0)]).buildings).toEqual({});
});

it("turns a scanned footprint red for trapped people and green when the building is clear",()=>{
  const social=report("osm:way:1533952352","Residencias Caribe B",-66.83892125,10.61471635);
  const drone={...social,id:"drone-w2",source:"drone",
    inspection:{people:"present" as const,result:"confirmed" as const,damage:"major_damage",source:"scan",images:[]},
    observations:[{...social.observations[0]!,source_type:"drone",incident_type:"trapped_victims",victim_indication:{type:"confirmed_multiple"}}]};
  expect(reportGeometry(geography,[social,drone]).buildings["osm:way:1533952352"]?.status).toBe("people_indicated");
  const school=report("osm:node:13976280529","Colegio Tanaguarenas",-66.8354097,10.6135029);
  const clear={...school,id:"drone-w7",source:"drone",
    inspection:{people:"none" as const,result:"safe" as const,damage:"none",source:"scan",images:[]},
    observations:[{...school.observations[0]!,source_type:"drone",incident_type:"trapped_victims",victim_indication:{type:"none"}}]};
  expect(reportGeometry(geography,[school,clear]).buildings["osm:way:303729130"]?.status).toBe("none_indicated");
});
it("highlights every building-only replay target without road highlights",()=>{
  const pack=JSON.parse(readFileSync(new URL("../../../data/scenarios/caraballeda/active_sites.json",import.meta.url),"utf8"));
  const reports=pack.active_worksites.map((w:any)=>report(w.site.site_id,w.display_name,w.site.centroid.longitude,w.site.centroid.latitude));
  const result=reportGeometry(geography,reports);
  expect(Object.keys(result.buildings)).toHaveLength(pack.active_worksites.length);
  expect(result.represented.size).toBe(pack.active_worksites.length);
  expect(result.roads).toEqual([]);
  expect(Object.values(result.buildings).every(s=>s.status==="unverified")).toBe(true);
});
it("turns a rescued building green, but keeps clear or unverified buildings distinct",()=>{
  const siteId="osm:way:1533952352";
  const social=report(siteId,"Residencias Caribe B",-66.83892125,10.61471635);
  const drone:IntelligenceReport={...social,id:"scan-w2",source:"drone",inspection:{people:"present",result:"confirmed",damage:"major_damage",source:"scan",images:[]}};
  expect(reportGeometry(geography,[social,drone]).buildings[siteId]?.status).toBe("people_indicated");
  expect(reportGeometry(geography,[social,drone],[siteId]).buildings[siteId]).toEqual({status:"rescued",color:"#29ce9e"});
  expect(reportGeometry(geography,[social],[siteId]).buildings[siteId]?.status).toBe("unverified");
  drone.inspection!.people="none";
  expect(reportGeometry(geography,[social,drone],[siteId]).buildings[siteId]?.status).toBe("none_indicated");
  // Reset/reopened worksite drops the resolved override.
  drone.inspection!.people="present";
  expect(reportGeometry(geography,[drone],[]).buildings[siteId]?.status).toBe("people_indicated");
});
