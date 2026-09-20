import { describe, expect, it } from "vitest";
import {
  metresBetween,
  pointAlongPath,
  projectGroundUnit,
  projectGroundUnits,
  STALE_AFTER_MS,
  type SaveFrame,
} from "../src/dashboard/save-tracking";
import type { SaveResource, SaveRoute, SaveState } from "../src/saveplanner/client";

const start = "2026-09-19T14:00:00Z";

/** An L-shaped road: east along a street, then north around a corner. */
const path = [
  { latitude: 10.61, longitude: -66.85 },
  { latitude: 10.61, longitude: -66.848 },
  { latitude: 10.612, longitude: -66.848 },
];
const legOne = metresBetween(path[0]!, path[1]!);
const legTwo = metresBetween(path[1]!, path[2]!);
const total = legOne + legTwo;

const route: SaveRoute = {
  route_id: "r1",
  resource_id: "rescue-bravo",
  worksite_id: "w1",
  path,
  road_edge_ids: ["osm:way:1:segment:0-1"],
  estimated_travel_time_s: total / 6.94,
  total_distance_m: total,
};

function unit(overrides: Partial<SaveResource> = {}): SaveResource {
  return {
    resource_id: "rescue-bravo",
    display_name: "Rescue Team Bravo",
    status: "en_route",
    capability_tags: ["ground_rescue"],
    current_worksite_id: "w1",
    position: path[0]!,
    heading_deg: 90,
    movement_mode: "ground_route",
    route_progress: 0,
    route_id: "r1",
    distance_remaining_m: total,
    eta_s: total / 6.94,
    ...overrides,
  };
}

function frame(resource: SaveResource, simSpeed = 1): SaveFrame {
  const state = {
    initialized: true,
    waiting: false,
    state_version: "s1",
    scenario: "caraballeda-v0",
    scenario_event: "E07",
    scenario_time: start,
    elapsed_s: 0,
    sim_speed: simSpeed,
    running: true,
    worksites: [],
    resources: [resource],
    routes: [route],
    blocked_edge_ids: [],
    limited_edge_ids: [],
    proposal: null,
    trace: [],
  } as unknown as SaveState;
  return { state, receivedAt: 0 };
}

describe("pointAlongPath", () => {
  it("walks whole segments instead of cutting the corner", () => {
    // Just past the corner: latitude must have started climbing, and longitude
    // must stay at the corner rather than drifting toward the destination.
    const past = pointAlongPath(path, legOne + legTwo / 2);
    expect(past.position.longitude).toBeCloseTo(-66.848, 6);
    expect(past.position.latitude).toBeGreaterThan(10.61);
    expect(past.position.latitude).toBeLessThan(10.612);
  });

  it("returns the endpoints for zero and over-long distances", () => {
    expect(pointAlongPath(path, 0).position).toEqual(path[0]);
    expect(pointAlongPath(path, total * 10).position).toEqual(path[2]);
  });

  it("stays on the road at every sampled distance", () => {
    for (let step = 0; step <= 20; step += 1) {
      const { position } = pointAlongPath(path, (total * step) / 20);
      const onLegOne =
        Math.abs(position.latitude - 10.61) < 1e-9 &&
        position.longitude >= -66.85 - 1e-9 &&
        position.longitude <= -66.848 + 1e-9;
      const onLegTwo =
        Math.abs(position.longitude + 66.848) < 1e-9 &&
        position.latitude >= 10.61 - 1e-9 &&
        position.latitude <= 10.612 + 1e-9;
      expect(onLegOne || onLegTwo).toBe(true);
    }
  });
});

describe("ground unit display projection", () => {
  it("advances along the route between authoritative updates", () => {
    const resource = unit();
    const projected = projectGroundUnit(resource, frame(resource), 2000);
    expect(projected.position.longitude).toBeGreaterThan(-66.85);
    expect(projected.routeProgress).toBeGreaterThan(0);
    // The snapshot itself is never mutated.
    expect(resource.position).toEqual(path[0]);
  });

  it("scales with the server's simulation speed", () => {
    const resource = unit();
    const slow = projectGroundUnit(resource, frame(resource, 1), 1000);
    const fast = projectGroundUnit(resource, frame(resource, 10), 1000);
    expect(fast.routeProgress).toBeGreaterThan(slow.routeProgress);
  });

  it("freezes once the snapshot goes stale", () => {
    const resource = unit();
    const held = projectGroundUnit(resource, frame(resource), STALE_AFTER_MS);
    const later = projectGroundUnit(resource, frame(resource), 30_000);
    expect(later.position).toEqual(held.position);
    expect(later.stale).toBe(true);
    expect(held.stale).toBe(false);
  });

  it("uses authoritative positions under reduced motion", () => {
    const resource = unit();
    const projected = projectGroundUnit(resource, frame(resource), 3000, true);
    expect(projected.position).toEqual(resource.position);
  });

  it("does not move a stationary unit", () => {
    const resource = unit({ movement_mode: "stationary", eta_s: null });
    const projected = projectGroundUnit(resource, frame(resource), 3000);
    expect(projected.position).toEqual(resource.position);
    expect(projected.moving).toBe(false);
  });

  it("holds position when the route is missing from the snapshot", () => {
    const resource = unit({ route_id: "gone" });
    const projected = projectGroundUnit(resource, frame(resource), 3000);
    expect(projected.position).toEqual(resource.position);
  });

  it("never projects past the end of the route", () => {
    const resource = unit({ distance_remaining_m: 5, eta_s: 1 });
    const projected = projectGroundUnit(resource, frame(resource, 10), 3000);
    expect(projected.routeProgress).toBeLessThanOrEqual(1);
    expect(projected.position).toEqual(path[2]);
  });

  it("counts down the ETA it displays", () => {
    const resource = unit();
    const projected = projectGroundUnit(resource, frame(resource), 2000);
    expect(projected.etaSeconds).toBeCloseTo(resource.eta_s! - 2, 5);
  });

  it("returns nothing before the session initialises", () => {
    expect(projectGroundUnits(undefined, 0)).toEqual([]);
  });
});
