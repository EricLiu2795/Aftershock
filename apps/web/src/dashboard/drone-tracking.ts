/** Display-only projections of authoritative mission state. No planner decisions. */
export type Point = { latitude: number; longitude: number };
export type FlightRoute = { route_id: string; path: Point[]; route_status: string; estimated_travel_time_s: number; created_at: string };
export type TrackedMission = { mission_id: string; resource: { resource_id: string; display_name?: string }; worksite_id: string;
  phase: string; launched_at: string; last_updated_at: string; outbound_route: FlightRoute; return_route: FlightRoute | null;
  movement: { position: Point; route_id: string | null; route_progress: number; heading_deg: number | null; last_updated_at: string };
  config: { verification_duration_s: number; reporting_duration_s: number }; report_received_at: string | null };
export type TrackingSnapshot = { simulation_speed?: number; tracking_demo?: boolean; initialized: boolean; server_now: string; missions: TrackedMission[];
  state?: { sites?: { centroid: Point }[]; resources?: { resource_id: string; display_name?: string; resource_type: string }[]; movement_states?: (TrackedMission["movement"] & { resource_id: string })[]; scenario_time: string; worksites: { worksite_id: string; site_id: string; display_name: string }[] } };
export type TrackingFrame = { sessionId?: string; snapshot: TrackingSnapshot; receivedAt: number };
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
export function projectMission(m: TrackedMission, frame: TrackingFrame, clock: number, reducedMotion = false) {
  const age = Math.max(0, clock - frame.receivedAt);
  const stale = age > STALE_AFTER_MS;
  const elapsed = reducedMotion ? 0 : Math.min(age, STALE_AFTER_MS) / 1000 * (frame.snapshot.simulation_speed ?? 1);
  const simTime = Date.parse(frame.snapshot.state?.scenario_time ?? m.last_updated_at) + elapsed * 1000;
  const route = m.phase === "returning" ? m.return_route : m.outbound_route;
  const flying = m.phase === "en_route" || m.phase === "returning";
  let position = m.movement.position;
  let eta: number | null = null;
  if (flying && route && route.route_id === m.movement.route_id && route.route_status === "active") {
    const remaining = Math.max(0, (Date.parse(route.created_at) + route.estimated_travel_time_s * 1000 - Date.parse(m.last_updated_at)) / 1000);
    // Backend currently produces direct great-circle FLIGHT routes. For an unknown
    // multi-waypoint route hold the authoritative position rather than cut corners.
    if (route.path.length === 2 && remaining > 0) position = interpolateFlight(position, route.path[1]!, elapsed / remaining);
    eta = Math.max(0, (Date.parse(route.created_at) + route.estimated_travel_time_s * 1000 - simTime) / 1000);
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
