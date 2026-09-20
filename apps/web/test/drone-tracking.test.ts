import { describe, expect, it } from "vitest";
import { interpolateFlight, projectMission, STALE_AFTER_MS, visibleDrones, type TrackedMission, type TrackingFrame } from "../src/dashboard/drone-tracking";
import { siteScanStatuses } from "../src/dashboard/site-scan-status";
import type { IntelligenceReport } from "../../../contracts/intelligence";
const start = "2026-09-19T14:00:00Z";
const m: TrackedMission = { mission_id:"m", resource:{resource_id:"d"}, worksite_id:"w", phase:"en_route", launched_at:start,last_updated_at:start,
  outbound_route:{route_id:"r",path:[{latitude:10,longitude:20},{latitude:10,longitude:20.01}],route_status:"active",estimated_travel_time_s:100,created_at:start},
  return_route:null,movement:{position:{latitude:10,longitude:20},route_id:"r",route_progress:0,heading_deg:90,last_updated_at:start},
  config:{verification_duration_s:30,reporting_duration_s:2},report_received_at:null };
const frame: TrackingFrame = {receivedAt:0,snapshot:{initialized:true,server_now:start,missions:[m],state:{scenario_time:start,worksites:[]}}};
describe("drone display projections", () => {
  it("moves between authoritative updates, holds when stale and respects reduced motion", () => {
    const projection=projectMission(m,frame,2000);
    expect(projection.position.longitude).toBeGreaterThan(20);
    expect(projection.eta).toBe(98);
    expect(projectMission(m,frame,10000).position).toEqual(projectMission(m,frame,STALE_AFTER_MS).position);
    expect(projectMission(m,frame,10000).stale).toBe(true);
    expect(projectMission(m,frame,2000,true).position).toEqual(m.movement.position);
    expect(m.movement.position.longitude).toBe(20);
  });
  it("holds a scanning drone and never changes its phase before the backend", () => {
    const scan={...m,phase:"verifying",movement:{...m.movement,position:m.outbound_route.path[1]!}};
    const result=projectMission(scan,frame,2000);
    expect(result.phase).toBe("Scanning");expect(result.position).toEqual(scan.movement.position);
  });
  it("handles dateline flight and starts new routes from the updated position", () => {
    const p=interpolateFlight({latitude:0,longitude:179},{latitude:0,longitude:-179},.5);
    expect(Math.abs(p.longitude)).toBeCloseTo(180);
    const next={...m,movement:{...m.movement,position:{latitude:10,longitude:20.005}}};
    expect(projectMission(next,frame,0).position).toEqual(next.movement.position);
  });
  it("displays ready drones from canonical resource and movement state", () => {
    const f=structuredClone(frame);f.snapshot.missions=[];
    f.snapshot.state!.resources=[{resource_id:"d",resource_type:"drone_unit"}];
    f.snapshot.state!.movement_states=[{...m.movement,resource_id:"d"}];
    expect(visibleDrones(f)[0]?.phase).toBe("ready");
  });
});
function report(source:string,type:string,at:string): IntelligenceReport {
  return {id:source+at,source,timestamp:at,author:"test",text:"test",mode:"test",state:"extracted",sourceUrl:null,model:null,
    locations:[{site:{id:"s",name:"Site",latitude:10,longitude:20,facility:null,height:1,heightSource:"osm"},method:"canonical_site"}],
    observations:[{schema_version:"2.1",observation_id:"o",timestamp:at,source_type:source,source_id:"test",location:{latitude:10,longitude:20,resolved_site_id:"s"},incident_type:"trapped_victims",victim_indication:{type},structural_hazard:"unknown",access_status:"unknown",verification_status:"unverified"} as IntelligenceReport["observations"][number]]};
}
it("uses yellow before scans, red for positive findings, green only for explicit negative findings", () => {
  expect(siteScanStatuses([], ["s"]).s?.status).toBe("unverified");
  expect(siteScanStatuses([report("radio","possible_multiple",start)]).s?.status).toBe("unverified");
  expect(siteScanStatuses([report("drone","possible_multiple",start)]).s?.status).toBe("people_indicated");
  expect(siteScanStatuses([report("drone","none",start)]).s?.status).toBe("none_indicated");
  expect(siteScanStatuses([report("drone","unknown",start)]).s?.status).toBe("unverified");
  expect(siteScanStatuses([report("drone","none",start),report("radio","possible_multiple","2026-09-19T14:01:00Z")]).s?.status).toBe("none_indicated");
});

it("starts with no highlights and highlights located social reports yellow", () => {
  expect(siteScanStatuses([])).toEqual({});
  expect(siteScanStatuses([report("social","possible_multiple",start)]).s?.status).toBe("unverified");
  const unresolved=report("social","possible_multiple",start); unresolved.locations=[];
  expect(siteScanStatuses([unresolved])).toEqual({});
});

it("colors by people findings independently of damage", () => {
  const r=report("drone","none",start);
  r.inspection={result:"confirmed",damage:"major_damage",source:"test",images:[]};
  expect(siteScanStatuses([r]).s?.color).toBe("#29ce9e");
  r.observations[0]!.victim_indication.type="confirmed_multiple";
  expect(siteScanStatuses([r]).s?.color).toBe("#fa626b");
  r.observations[0]!.victim_indication.type="unknown";
  expect(siteScanStatuses([r]).s?.status).toBe("unverified");
});

it("uses drone inspection people even without a trapped_victims observation", () => {
  const r=report("drone","none",start);
  r.observations[0]!.incident_type="structural_damage";
  r.inspection={people:"none",result:"safe",damage:"none",source:"test",images:[]};
  expect(siteScanStatuses([r]).s?.status).toBe("none_indicated");
  r.inspection={people:"present",result:"confirmed",damage:"major_damage",source:"test",images:[]};
  expect(siteScanStatuses([r]).s?.status).toBe("people_indicated");
});
