/* Display projection for ground rescue units, the counterpart of drone-tracking.ts.
 *
 * The API ticks Save roughly once a second. Drawing only those positions makes the
 * rescue icons visibly step while the drones, which are projected up to 30 fps,
 * glide. This module closes that gap using the same rules as drone tracking:
 * interpolate from the last authoritative snapshot, scale by the server's
 * simulation speed, freeze after STALE_AFTER_MS, and fall back to authoritative
 * positions under reduced motion.
 *
 * The one real difference: a drone flies a two-point great-circle, so it can be
 * slerped. A rescue unit follows a road polyline, so projection has to walk that
 * polyline segment by segment -- the same traversal the planner does in
 * `SaveController._step`. Interpolating straight to the destination would cut
 * corners across buildings, which is the whole thing the road graph exists to avoid.
 *
 * Nothing here decides anything. Positions computed in this file are display only
 * and are never sent back to the planner.
 */
import type { SavePosition, SaveResource, SaveRoute, SaveState } from "../saveplanner/client";

/** Shared with drone tracking so both icon families go stale identically. */
export const STALE_AFTER_MS = 3500;

/** A snapshot plus the moment it arrived, which is what projection measures from. */
export interface SaveFrame {
  state: SaveState;
  receivedAt: number;
}

const EARTH_RADIUS_M = 6371008.8;
const radians = (value: number) => (value * Math.PI) / 180;

export function metresBetween(a: SavePosition, b: SavePosition): number {
  const dLat = radians(b.latitude - a.latitude);
  const dLon = radians(b.longitude - a.longitude);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(radians(a.latitude)) *
      Math.cos(radians(b.latitude)) *
      Math.sin(dLon / 2) ** 2;
  return EARTH_RADIUS_M * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
}

function bearing(a: SavePosition, b: SavePosition): number {
  const lat1 = radians(a.latitude);
  const lat2 = radians(b.latitude);
  const delta = radians(b.longitude - a.longitude);
  const y = Math.sin(delta) * Math.cos(lat2);
  const x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(delta);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/**
 * Point `distance` metres along a road polyline, measured from its start.
 *
 * Walks whole segments before interpolating inside one, so the result always lies
 * on a real road edge rather than on a shortcut between distant shape points.
 */
export function pointAlongPath(
  path: SavePosition[],
  distance: number,
): { position: SavePosition; heading: number | null } {
  if (!path.length) return { position: { latitude: 0, longitude: 0 }, heading: null };
  const first = path[0]!;
  if (path.length === 1 || distance <= 0) return { position: first, heading: null };

  let remaining = distance;
  for (let index = 0; index < path.length - 1; index += 1) {
    const start = path[index]!;
    const end = path[index + 1]!;
    const length = metresBetween(start, end);
    if (length <= 0) continue;
    if (remaining <= length) {
      const fraction = remaining / length;
      return {
        position: {
          latitude: start.latitude + (end.latitude - start.latitude) * fraction,
          longitude: start.longitude + (end.longitude - start.longitude) * fraction,
        },
        heading: bearing(start, end),
      };
    }
    remaining -= length;
  }
  const last = path[path.length - 1]!;
  const previous = path[path.length - 2]!;
  return { position: last, heading: bearing(previous, last) };
}

/** Metres already covered, derived from the route the planner published. */
function travelled(resource: SaveResource, route: SaveRoute | undefined): number {
  if (!route) return 0;
  return Math.max(0, route.total_distance_m - Math.max(0, resource.distance_remaining_m));
}

/**
 * Ground speed in m/s implied by the snapshot.
 *
 * Taken from the unit's own remaining distance and ETA rather than a hardcoded
 * constant, so a scenario speed change or a slower LIMITED road is reflected
 * without the frontend knowing anything about road conditions.
 */
function speedMps(resource: SaveResource): number {
  if (!resource.eta_s || resource.eta_s <= 0) return 0;
  return Math.max(0, resource.distance_remaining_m) / resource.eta_s;
}

export interface ProjectedUnit {
  resourceId: string;
  name: string;
  position: SavePosition;
  heading: number | null;
  moving: boolean;
  stale: boolean;
  etaSeconds: number | null;
  routeProgress: number;
}

/**
 * Display position for one rescue unit at `clock`.
 *
 * A stationary unit, a unit with no active route, or reduced motion all return the
 * authoritative position untouched.
 */
export function projectGroundUnit(
  resource: SaveResource,
  frame: SaveFrame,
  clock: number,
  reducedMotion = false,
): ProjectedUnit {
  const age = Math.max(0, clock - frame.receivedAt);
  const stale = age > STALE_AFTER_MS;
  const base: ProjectedUnit = {
    resourceId: resource.resource_id,
    name: resource.display_name,
    position: resource.position,
    heading: resource.heading_deg,
    moving: resource.movement_mode === "ground_route",
    stale,
    etaSeconds: resource.eta_s,
    routeProgress: resource.route_progress,
  };
  if (reducedMotion || !base.moving) return base;

  const route = frame.state.routes.find(
    (item) => item.route_id === resource.route_id,
  );
  if (!route || route.path.length < 2) return base;

  // Cap the projected interval so a dropped connection freezes the icon instead of
  // flying it down the road on stale data.
  const elapsed =
    (Math.min(age, STALE_AFTER_MS) / 1000) * (frame.state.sim_speed || 1);
  const speed = speedMps(resource);
  if (speed <= 0) return base;

  const distance = Math.min(
    route.total_distance_m,
    travelled(resource, route) + elapsed * speed,
  );
  const { position, heading } = pointAlongPath(route.path, distance);
  return {
    ...base,
    position,
    heading: heading ?? base.heading,
    etaSeconds:
      resource.eta_s === null ? null : Math.max(0, resource.eta_s - elapsed),
    routeProgress: route.total_distance_m
      ? Math.min(1, distance / route.total_distance_m)
      : base.routeProgress,
  };
}

/** Every ground unit the map should draw, projected to `clock`. */
export function projectGroundUnits(
  frame: SaveFrame | undefined,
  clock: number,
  reducedMotion = false,
): ProjectedUnit[] {
  if (!frame?.state.initialized) return [];
  return frame.state.resources.map((resource) =>
    projectGroundUnit(resource, frame, clock, reducedMotion),
  );
}
