import { api } from "../api";

export interface SavePosition {
  latitude: number;
  longitude: number;
}
export interface SaveWorksite {
  worksite_id: string;
  site_id: string;
  /** Scenario worksite key, W1-W8. Presentation only, never an identity. */
  key: string;
  display_name: string | null;
  position: SavePosition | null;
  evidence_status: string;
  operational_status: string;
  eligible: boolean;
  recommended_task: string | null;
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
  current_worksite_id: string;
  current_label: string;
  proposed_worksite_id: string;
  proposed_label: string;
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
  initialized: boolean;
  waiting: boolean;
  session_id?: string;
  state_version: string;
  last_consumed_operational_state_version?: string | null;
  scenario: string;
  scenario_event: string | null;
  scenario_time: string;
  elapsed_s: number;
  sim_speed: number;
  running: boolean;
  worksites: SaveWorksite[];
  resources: SaveResource[];
  routes: SaveRoute[];
  blocked_edge_ids: string[];
  limited_edge_ids: string[];
  proposal: SaveProposal | null;
  trace: SaveTrace[];
  sync_action?: string | null;
}

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

export function tickSave(sessionId: string) {
  return api<SaveState>(`/api/demo-sessions/${sessionId}/save/tick`, {});
}

export function decideProposal(
  sessionId: string,
  proposalId: string,
  approve: boolean,
) {
  return api<SaveState>(
    `/api/demo-sessions/${sessionId}/save/proposals/${encodeURIComponent(proposalId)}/decision`,
    { approve },
  );
}

export function setRoadStatus(
  sessionId: string,
  edgeId: string,
  status: "open" | "limited" | "blocked",
  travelCostMultiplier = 1,
) {
  return api<{ save: SaveState }>(
    `/api/demo-sessions/${sessionId}/roads/${encodeURIComponent(edgeId)}`,
    { status, travel_cost_multiplier: travelCostMultiplier },
  );
}
