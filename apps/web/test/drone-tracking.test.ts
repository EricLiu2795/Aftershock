import { describe, expect, it } from "vitest";
import { droneTaskProgress, interpolateFlight, presentMissionPosition, projectMission, projectedSimulationTime, STALE_AFTER_MS, visibleDrones, type TrackedMission, type TrackingFrame } from "../src/dashboard/drone-tracking";
import { siteScanStatuses } from "../src/dashboard/site-scan-status";
import { scannedBuildingReports } from "../src/dashboard/ScanPhotoOverlay";
import type { IntelligenceReport } from "../../../contracts/intelligence";
const start = "2026-09-19T14:00:00Z";
const m: TrackedMission = { mission_id:"m", resource:{resource_id:"d"}, worksite_id:"w", phase:"en_route", launched_at:start,last_updated_at:start,
  outbound_route:{route_id:"r",path:[{latitude:10,longitude:20},{latitude:10,longitude:20.01}],route_status:"active",estimated_travel_time_s:100,created_at:start},
  return_route:null,movement:{position:{latitude:10,longitude:20},route_id:"r",route_progress:0,heading_deg:90,last_updated_at:start},
  config:{verification_duration_s:30,reporting_duration_s:2},report_received_at:null };
const frame: TrackingFrame = {receivedAt:0,snapshot:{initialized:true,server_now:start,missions:[m],state:{scenario_time:start,worksites:[]}}};
describe("drone display projections", () => {
  it("keeps moving during a slow tick, reports staleness and respects reduced motion", () => {
    const projection=projectMission(m,frame,2000);
    expect(projection.position.longitude).toBeGreaterThan(20);
    expect(projection.eta).toBe(98);
    expect(projectMission(m,frame,10000).position.longitude).toBeGreaterThan(projectMission(m,frame,STALE_AFTER_MS).position.longitude);
    expect(projectMission(m,frame,10000).stale).toBe(true);
    expect(projectMission(m,frame,2000,true).position).toEqual(m.movement.position);
    expect(m.movement.position.longitude).toBe(20);
  });
  it.each([1, 5, 10])("uses a constant route velocity at %sx simulation speed", speed => {
    const atOne = projectMission(m, frame, 1000).position.longitude;
    const atSpeed = projectMission(m, { ...frame, snapshot: { ...frame.snapshot, simulation_speed: speed } }, 1000).position.longitude;
    expect(atSpeed - 20).toBeCloseTo((atOne - 20) * speed, 6);
  });
  it("calculates a separate 0-to-1 bar for every drone task", () => {
    expect(droneTaskProgress(m, Date.parse(start) + 50_000)).toBeCloseTo(0.5, 6);
    const scanning = { ...m, phase: "verifying" };
    expect(droneTaskProgress(scanning, Date.parse(start) + 115_000)).toBeCloseTo(0.5, 6);
    const reporting = { ...m, phase: "reporting", report_received_at: "2026-09-19T14:02:10Z" };
    expect(droneTaskProgress(reporting, Date.parse(start) + 131_000)).toBeCloseTo(0.5, 6);
    const returning = { ...m, phase: "returning", movement: { ...m.movement, route_id: "return" }, return_route: { ...m.outbound_route, route_id: "return", created_at: "2026-09-19T14:03:00Z" } };
    expect(droneTaskProgress(returning, Date.parse(start) + 50_000)).toBe(0);
  });
  it("uses the simulation clock for progress at the selected multiplier", () => {
    const fast = { ...frame, snapshot: { ...frame.snapshot, simulation_speed: 5 } };
    const simTime = projectedSimulationTime(fast, 1000);
    expect(droneTaskProgress(m, simTime)).toBeCloseTo(0.05, 6);
  });
  it.each([1, 5, 10])("keeps position and ETA continuous when a delayed %sx tick arrives", speed => {
    const oldFrame = { ...frame, receivedAt: 0, snapshot: { ...frame.snapshot, simulation_speed: speed } };
    const requestStarted = 1000;
    const responseArrived = 1300;
    const before = projectMission(m, oldFrame, responseArrived);
    const nextScenarioTime = new Date(Date.parse(start) + requestStarted * speed).toISOString();
    const nextSnapshot = {
      ...oldFrame.snapshot,
      server_now: nextScenarioTime,
      state: { ...oldFrame.snapshot.state!, scenario_time: nextScenarioTime },
    };
    const next = { ...oldFrame, receivedAt: requestStarted, snapshot: nextSnapshot };
    const after = projectMission(m, next, responseArrived);
    expect(after.position.latitude).toBeCloseTo(before.position.latitude, 10);
    expect(after.position.longitude).toBeCloseTo(before.position.longitude, 10);
    expect(after.eta).toBeCloseTo(before.eta!, 10);
  });
  it("starts a replacement route at the previously displayed position", () => {
    const oldPosition = interpolateFlight(m.outbound_route.path[0]!, m.outbound_route.path[1]!, 0.5);
    const oldState = { missionId: "old", routeId: "r", origin: m.outbound_route.path[0]!, target: m.outbound_route.path[1]!,
      position: oldPosition, progress: 0.5, durationS: 100, wallAt: 1000, phase: "en_route" as const };
    const replacement = { ...m, mission_id: "new", outbound_route: { ...m.outbound_route, route_id: "r2", created_at: "2026-09-19T14:01:00Z", path: [{ latitude: 10, longitude: 20.006 }, { latitude: 10, longitude: 20.02 }] }, movement: { ...m.movement, route_id: "r2", route_progress: 0.4 } };
    const result = presentMissionPosition(replacement, replacement.outbound_route.path[0]!, 2000, 2000, 10, false, oldState);
    expect(result.position).toEqual(oldState.position);
    expect(result.progress).toBe(0);
  });
  it.each([1, 5, 10])("advances a newly presented route steadily at %sx with matching ETA", speed => {
    const oldPosition = interpolateFlight(m.outbound_route.path[0]!, m.outbound_route.path[1]!, 0.5);
    const oldState = { missionId: "old", routeId: "r", origin: m.outbound_route.path[0]!, target: m.outbound_route.path[1]!,
      position: oldPosition, progress: 0.5, durationS: 100, wallAt: 0, phase: "en_route" as const };
    const replacement = { ...m, mission_id: "new", outbound_route: { ...m.outbound_route, route_id: "r2", path: [oldPosition, { latitude: 10, longitude: 20.02 }] }, movement: { ...m.movement, route_id: "r2", route_progress: 0.5 } };
    const first = presentMissionPosition(replacement, oldPosition, 1000, 1000, speed, false, oldState);
    const later = presentMissionPosition(replacement, oldPosition, 2000, 1000, speed, false, first.state);
    expect(later.progress).toBeCloseTo(speed / later.state.durationS, 6);
    expect(later.eta).toBeCloseTo(later.state.durationS - speed, 6);
    expect(later.position.longitude).toBeGreaterThan(oldPosition.longitude);
  });
  it("finishes the displayed flight before showing the backend scanning phase", () => {
    const prior = { missionId: "m", routeId: "r", origin: m.outbound_route.path[0]!, target: m.outbound_route.path[1]!,
      position: interpolateFlight(m.outbound_route.path[0]!, m.outbound_route.path[1]!, 0.9), progress: 0.9,
      durationS: 100, wallAt: 0, phase: "en_route" as const };
    const scanning = { ...m, phase: "verifying", outbound_route: { ...m.outbound_route, route_status: "completed" },
      movement: { ...m.movement, position: m.outbound_route.path[1]!, route_progress: 1 } };
    const result = presentMissionPosition(scanning, scanning.movement.position, 1000, 0, 1, false, prior);
    expect(result.phase).toBe("en_route");
    expect(result.progress).toBeCloseTo(0.91, 6);
    expect(result.position.longitude).toBeLessThan(scanning.movement.position.longitude);
  });
  it("animates a new short route even if the backend already reached scanning", () => {
    const prior = { missionId: "old", routeId: null, origin: m.outbound_route.path[0]!, target: m.outbound_route.path[0]!,
      position: m.outbound_route.path[0]!, progress: 1, durationS: 0, wallAt: 0, phase: null };
    const scanning = { ...m, mission_id: "new", phase: "verifying", outbound_route: { ...m.outbound_route, route_status: "completed" },
      movement: { ...m.movement, position: m.outbound_route.path[1]!, route_progress: 1 } };
    const first = presentMissionPosition(scanning, scanning.movement.position, 3000, 3000, 10, false, prior);
    expect(first.phase).toBe("en_route");
    expect(first.progress).toBe(0);
    expect(first.position).toEqual(prior.position);
  });
  it("holds a scanning drone and never changes its phase before the backend", () => {
    const scan={...m,phase:"verifying",movement:{...m.movement,position:m.outbound_route.path[1]!}};
    const result=projectMission(scan,frame,2000);
    expect(result.phase).toBe("Scanning");expect(result.position).toEqual(scan.movement.position);
  });
  it("handles dateline flight and follows the route clock rather than snapshot positions", () => {
    const p=interpolateFlight({latitude:0,longitude:179},{latitude:0,longitude:-179},.5);
    expect(Math.abs(p.longitude)).toBeCloseTo(180);
    const next={...m,movement:{...m.movement,position:{latitude:10,longitude:20.005}}};
    expect(projectMission(next,frame,0).position).toEqual(m.outbound_route.path[0]);
  });
  it("freezes position and ETA while paused without showing stale", () => {
    const paused = { ...frame, pausedAt: 2000 };
    const atPause = projectMission(m, paused, 2000);
    const later = projectMission(m, paused, 120000);
    expect(later.position).toEqual(atPause.position);
    expect(later.eta).toBe(atPause.eta);
    expect(later.stale).toBe(false);
  });
  it("resumes interpolation only from a fresh frame", () => {
    const resumed = { ...frame, receivedAt: 5000 };
    expect(projectMission(m, resumed, 5000).position).toEqual(m.movement.position);
    expect(projectMission(m, resumed, 6000).position.longitude).toBeGreaterThan(20);
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

it("keeps one click-to-open marker for every scanned building", () => {
  const newest = report("drone", "none", "2026-09-19T14:02:00Z");
  newest.inspection = { people: "none", result: "safe", damage: "none", source: "scan", images: [] };
  const older = report("drone", "possible_multiple", start);
  older.inspection = { people: "present", result: "confirmed", damage: "major_damage", source: "scan", images: [] };
  expect(scannedBuildingReports([older, newest])).toEqual([newest]);
});
