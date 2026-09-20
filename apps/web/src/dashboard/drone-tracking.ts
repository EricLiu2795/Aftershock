/** Display-only projections of authoritative mission state. No planner decisions. */
export type Point = { latitude: number; longitude: number };
export type FlightRoute = { route_id: string; path: Point[]; route_status: string; estimated_travel_time_s: number; total_distance_m?: number; created_at: string };
export type TrackedMission = { mission_id: string; resource: { resource_id: string; display_name?: string }; worksite_id: string;
  phase: string; launched_at: string; last_updated_at: string; outbound_route: FlightRoute; return_route: FlightRoute | null;
  movement: { position: Point; route_id: string | null; route_progress: number; heading_deg: number | null; last_updated_at: string };
  config: { verification_duration_s: number; reporting_duration_s: number }; report_received_at: string | null };
export type TargetPriority = {site_id: string; worksite_id: string; display_name: string; position: Point;
  rank: number; score: number; people_count: number | null; assigned: boolean};
export type TrackingSnapshot = { target_priorities?: TargetPriority[]; inspected_site_ids?: string[]; simulation_speed?: number; tracking_demo?: boolean; scanning_enabled?: boolean; initialized: boolean; server_now: string; missions: TrackedMission[];
  state?: { sites?: { centroid: Point }[]; resources?: { resource_id: string; display_name?: string; resource_type: string }[]; movement_states?: (TrackedMission["movement"] & { resource_id: string })[]; scenario_time: string; worksites: { worksite_id: string; site_id: string; display_name: string }[] } };
export type TrackingFrame = { sessionId?: string; snapshot: TrackingSnapshot; receivedAt: number; observedAt?: number; pausedAt?: number };
export type PresentedFlight = { missionId: string; routeId: string | null; origin: Point; target: Point; position: Point;
  progress: number; durationS: number; wallAt: number; phase: "en_route" | "returning" | null };
export const STALE_AFTER_MS = 3500;
const radians = (v: number) => v * Math.PI / 180;
export function interpolateFlight(a: Point, b: Point, fraction: number): Point {
  const f = Math.max(0, Math.min(1, fraction));
  if (f === 0) return a;
  if (f === 1) return b;
  const vector = (p: Point) => [Math.cos(radians(p.latitude)) * Math.cos(radians(p.longitude)), Math.cos(radians(p.latitude)) * Math.sin(radians(p.longitude)), Math.sin(radians(p.latitude))];
  const u = vector(a), v = vector(b);
  const angle = Math.acos(Math.max(-1, Math.min(1, u.reduce((sum, x, i) => sum + x * v[i]!, 0))));
  if (angle < 1e-10 || Math.abs(Math.PI - angle) < 1e-8) return a;
  const p = u.map((x, i) => (Math.sin((1 - f) * angle) * x + Math.sin(f * angle) * v[i]!) / Math.sin(angle));
  return { latitude: Math.atan2(p[2]!, Math.hypot(p[0]!, p[1]!)) * 180 / Math.PI, longitude: Math.atan2(p[1]!, p[0]!) * 180 / Math.PI };
}
export function etaLabel(seconds: number | null) {
  if (seconds === null) return "Awaiting report";
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
const clamp01 = (value: number) => Math.max(0, Math.min(1, value));
function distanceMeters(a: Point, b: Point) {
  const lat1 = radians(a.latitude), lat2 = radians(b.latitude);
  const dlat = lat2 - lat1, dlon = radians(b.longitude - a.longitude);
  const h = Math.sin(dlat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dlon / 2) ** 2;
  return 2 * 6371008.8 * Math.asin(Math.min(1, Math.sqrt(Math.max(0, h))));
}
export function projectedSimulationTime(frame: TrackingFrame, clock: number, reducedMotion = false) {
  const until = frame.pausedAt ?? clock;
  const age = Math.max(0, until - frame.receivedAt);
  const elapsed = reducedMotion ? 0 : age * (frame.snapshot.simulation_speed ?? 1);
  return Date.parse(frame.snapshot.state?.scenario_time ?? frame.snapshot.server_now) + elapsed;
}
/** Progress for the current drone task, expressed as a fraction from 0 to 1. */
export function droneTaskProgress(m: TrackedMission, simTime: number) {
  if (m.phase === "ready") return 0;
  if (m.phase === "idle") return 1;
  const flightProgress = (route: FlightRoute | null) => route && route.estimated_travel_time_s > 0
    ? clamp01((simTime - Date.parse(route.created_at)) / (route.estimated_travel_time_s * 1000)) : 0;
  if (m.phase === "en_route") return flightProgress(m.outbound_route);
  if (m.phase === "returning") return flightProgress(m.return_route);
  const verificationStart = Date.parse(m.launched_at) + m.outbound_route.estimated_travel_time_s * 1000;
  if (m.phase === "verifying") return m.config.verification_duration_s > 0
    ? clamp01((simTime - verificationStart) / (m.config.verification_duration_s * 1000)) : 1;
  if (m.phase === "reporting") {
    if (!m.report_received_at || m.config.reporting_duration_s <= 0) return m.report_received_at ? 1 : 0;
    const reportingStart = Math.max(Date.parse(m.report_received_at), verificationStart + m.config.verification_duration_s * 1000);
    return clamp01((simTime - reportingStart) / (m.config.reporting_duration_s * 1000));
  }
  return 0;
}

/**
 * Present flight routes on a local monotonic clock. A newly observed route starts
 * exactly where the prior route was displayed, so work performed while the API
 * request was in flight cannot make the marker jump forward on first render.
 */
export function presentMissionPosition(
  m: TrackedMission,
  projected: Point,
  wallAt: number,
  observedAt: number,
  simulationSpeed: number,
  paused: boolean,
  previous?: PresentedFlight,
): { position: Point; state: PresentedFlight; progress: number; eta: number | null; phase: "en_route" | "returning" | null; route: FlightRoute | null } {
  const authoritativeRoute = m.phase === "returning" ? m.return_route : m.outbound_route;
  const flying = m.phase === "en_route" || m.phase === "returning";
  const previousRoute = previous?.phase === "returning" ? m.return_route : m.outbound_route;
  const continuing = !flying && previous?.missionId === m.mission_id && previousRoute?.route_id === previous.routeId && previous.progress < 1;
  // At high simulation speeds a short flight may begin and finish inside one
  // API request. It still needs to be presented from the prior visible point.
  const newlyObservedCompletedFlight = !flying && previous !== undefined && previous.missionId !== m.mission_id;
  const route = continuing ? previousRoute : newlyObservedCompletedFlight ? m.outbound_route : authoritativeRoute;
  const phase = continuing ? previous.phase : newlyObservedCompletedFlight ? "en_route" : (flying ? m.phase as "en_route" | "returning" : null);
  if (!phase || !route || route.path.length !== 2 || (!continuing && !newlyObservedCompletedFlight && (route.route_id !== m.movement.route_id || route.route_status !== "active"))) {
    const state = { missionId: m.mission_id, routeId: null, origin: projected, target: projected,
      position: projected, progress: 1, durationS: 0, wallAt, phase: null } satisfies PresentedFlight;
    return { position: projected, state, progress: 1, eta: null, phase: null, route: null };
  }
  const sameRoute = previous?.missionId === m.mission_id && previous.routeId === route.route_id;
  let origin: Point, progress: number, durationS: number;
  if (sameRoute) {
    origin = previous.origin;
    durationS = previous.durationS;
    const elapsedS = paused ? 0 : Math.max(0, wallAt - previous.wallAt) / 1000 * simulationSpeed;
    progress = durationS > 0 ? clamp01(previous.progress + elapsedS / durationS) : 1;
  } else {
    const hasPriorDisplay = previous !== undefined;
    origin = previous?.position ?? route.path[0]!;
    progress = hasPriorDisplay ? 0 : clamp01(m.movement.route_progress);
    const routeDistance = route.total_distance_m ?? distanceMeters(route.path[0]!, route.path[1]!);
    const displayDistance = distanceMeters(origin, route.path[1]!);
    durationS = routeDistance > 0 ? route.estimated_travel_time_s * displayDistance / routeDistance : 0;
  }
  const target = route.path[1]!;
  const position = interpolateFlight(origin, target, progress);
  const state = { missionId: m.mission_id, routeId: route.route_id, origin, target, position,
    progress, durationS, wallAt: sameRoute ? wallAt : observedAt, phase } satisfies PresentedFlight;
  return { position, state, progress, eta: Math.max(0, durationS * (1 - progress)), phase, route };
}
export function projectMission(m: TrackedMission, frame: TrackingFrame, clock: number, reducedMotion = false) {
  // A paused/ended presentation must remain exactly where it was when the
  // state change arrived; do not let the render clock advance it underneath.
  const age = Math.max(0, (frame.pausedAt ?? clock) - frame.receivedAt);
  const stale = frame.pausedAt === undefined && age > STALE_AFTER_MS;
  const simTime = projectedSimulationTime(frame, clock, reducedMotion);
  const route = m.phase === "returning" ? m.return_route : m.outbound_route;
  const flying = m.phase === "en_route" || m.phase === "returning";
  let position = m.movement.position;
  let eta: number | null = null;
  if (flying && route && route.route_id === m.movement.route_id && route.route_status === "active") {
    const routeStart = Date.parse(route.created_at);
    const routeEnd = routeStart + route.estimated_travel_time_s * 1000;
    // Project from the immutable route clock, not from each one-second server
    // snapshot. This gives a fixed 1× visual velocity; simTime already applies
    // the chosen multiplier, so 5× is exactly five times that velocity.
    if (!reducedMotion && route.path.length === 2 && route.estimated_travel_time_s > 0) {
      const progress = (simTime - routeStart) / (route.estimated_travel_time_s * 1000);
      position = interpolateFlight(route.path[0]!, route.path[1]!, progress);
    }
    eta = Math.max(0, (routeEnd - simTime) / 1000);
  } else if (m.phase === "verifying") {
    eta = Math.max(0, (Date.parse(m.launched_at) + (m.outbound_route.estimated_travel_time_s + m.config.verification_duration_s) * 1000 - simTime) / 1000);
  } else if (m.phase === "reporting" && m.report_received_at) {
    const end = Math.max(Date.parse(m.report_received_at), Date.parse(m.launched_at) + (m.outbound_route.estimated_travel_time_s + m.config.verification_duration_s) * 1000);
    eta = Math.max(0, (end + m.config.reporting_duration_s * 1000 - simTime) / 1000);
  }
  const phase = ({ ready: "Ready to fly", en_route: "En route", verifying: "Scanning", reporting: "Reporting", returning: "Returning", idle: "At base" } as Record<string, string>)[m.phase] ?? m.phase;
  return { position, route: flying ? route : null, eta, phase, stale,
    name: m.resource.display_name ?? m.resource.resource_id,
    target: frame.snapshot.state?.worksites.find(w => w.worksite_id === m.worksite_id)?.display_name ?? m.worksite_id };
}

/** Idle markers are view models only, not persisted or submitted as missions. */
export function visibleDrones(frame: TrackingFrame): TrackedMission[] {
  const { snapshot } = frame;
  const busy = new Set(snapshot.missions.map(m => m.resource.resource_id));
  return [...snapshot.missions, ...(snapshot.state?.resources ?? []).filter(r => r.resource_type === "drone_unit" && !busy.has(r.resource_id)).flatMap(r => {
    const movement = snapshot.state?.movement_states?.find(m => m.resource_id === r.resource_id);
    if (!movement) return [];
    return [{ mission_id: `display-idle:${r.resource_id}`, resource: r, worksite_id: "", phase: "ready",
      movement, launched_at: movement.last_updated_at, last_updated_at: movement.last_updated_at,
      outbound_route: { route_id: "", path: [movement.position], route_status: "completed", estimated_travel_time_s: 0, created_at: movement.last_updated_at },
      return_route: null, config: { verification_duration_s: 0, reporting_duration_s: 0 }, report_received_at: null }];
  })];
}
