/* Live feed from the Python Save planner (`python -m saveplanner serve`).
 *
 * Routes, ETAs and the blocked-road set are all computed server side against the
 * OpenStreetMap graph in `data/graph_exports/caraballeda.json`. The map only draws
 * what the planner decided; it never routes anything itself. Vite proxies
 * `/saveplanner/*` to the planner, so this uses one browser origin like the rest
 * of the dashboard.
 */
export interface SavePosition {
  latitude: number;
  longitude: number;
}
export interface SaveWorksite {
  worksite_id: string;
  site_id: string;
  sector: string;
  display_name: string;
  site_display_name: string | null;
  position: SavePosition;
  evidence_status: string;
  operational_status: string;
  incident_types: string[];
  criticality: number;
  eligible: boolean;
  save_priority: number | null;
  score_breakdown: Record<string, number> | null;
  candidate_assignments: {
    resource_id: string;
    assignment_utility: number;
    travel_cost: number;
    responder_risk: number;
    capability_mismatch: number;
  }[];
  recommended_resource_id: string | null;
  reasons: string[];
  assigned_resource_ids: string[];
  simulated: boolean;
}
export interface SaveResource {
  resource_id: string;
  display_name: string;
  status: string;
  capability_tags: string[];
  current_worksite_id: string | null;
  position: SavePosition;
  heading_deg: number | null;
  movement_mode: string;
  route_progress: number;
  route_id: string | null;
  distance_remaining_m: number;
  eta_s: number | null;
}
export interface SaveRoute {
  route_id: string;
  resource_id: string;
  worksite_id: string;
  path: SavePosition[];
  road_edge_ids: string[];
  estimated_travel_time_s: number;
  total_distance_m: number;
}
export interface SaveProposal {
  proposal_id: string;
  resource_id: string;
  resource_name: string;
  current_sector: string;
  proposed_sector: string;
  reassignment_gain: number;
  threshold: number;
  breakdown: {
    proposed_utility: number;
    current_utility: number;
    progress_loss: number;
    additional_travel: number;
    switch_cost: number;
  };
  status: string;
  seconds_left: number;
}
export interface SaveTrace {
  at: string;
  state_version: string;
  headline: string;
  detail: string;
}
export interface SaveState {
  state_version: string;
  scenario: string;
  scenario_time: string;
  elapsed_s: number;
  sim_speed: number;
  running: boolean;
  worksites: SaveWorksite[];
  resources: SaveResource[];
  routes: SaveRoute[];
  blocked_edge_ids: string[];
  proposal: SaveProposal | null;
  trace: SaveTrace[];
}

const BASE = "/saveplanner";

/** Stable per-unit colours shared by the map, the route lines and the unit table. */
export const UNIT_COLOURS: readonly string[] = [
  "#53def6",
  "#c08bff",
  "#5bd6a4",
  "#f6bc5a",
];

export function unitColour(
  resourceId: string,
  resources: SaveResource[],
): string {
  const index = resources.findIndex((item) => item.resource_id === resourceId);
  const slot = (index < 0 ? 0 : index) % UNIT_COLOURS.length;
  return UNIT_COLOURS[slot] ?? "#53def6";
}

/** Subscribe to planner state. Falls back to polling if the SSE stream drops. */
export function subscribeSavePlanner(
  changed: (state: SaveState) => void,
  status: (value: "live" | "connecting" | "offline") => void,
) {
  let stopped = false;
  let source: EventSource | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;

  const connect = () => {
    if (stopped) return;
    status("connecting");
    source = new EventSource(`${BASE}/stream`);
    source.onopen = () => status("live");
    source.onmessage = (event) => {
      if (stopped) return;
      try {
        changed(JSON.parse(event.data) as SaveState);
        status("live");
      } catch {
        /* a truncated frame is replaced by the next tick */
      }
    };
    source.onerror = () => {
      source?.close();
      source = undefined;
      if (stopped) return;
      status("offline");
      retry = setTimeout(connect, 2000);
    };
  };

  connect();
  return () => {
    stopped = true;
    clearTimeout(retry);
    source?.close();
  };
}

async function post(path: string, body: unknown) {
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`Save planner rejected the request`);
  return response.json();
}

/** Block or reopen one road. The planner reroutes every affected unit immediately. */
export const setRoadBlocked = (edgeId: string, blocked: boolean) =>
  post("/road", { edge_id: edgeId, blocked, source: "commander" });

/** Answer the 30-second human reassignment gate. */
export const decideProposal = (approve: boolean) =>
  post("/proposal", { approve });

export const setSimulation = (body: {
  running?: boolean;
  sim_speed?: number;
}) => post("/control", body);
