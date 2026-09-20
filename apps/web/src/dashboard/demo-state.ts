/** Presentation projections of server state. No allocation, fusion, routing or thresholds here. */
import type { IntelligenceReport } from "../../../../contracts/intelligence";
import { siteScanStatuses } from "./site-scan-status";

export interface BeliefRoadState {
  schema_version?: string;
  road_id: string;
  status: "open" | "limited" | "blocked";
  travel_cost_multiplier: number;
  evidence_ids?: string[];
  reason?: string | null;
  based_on_state_version?: string | null;
  updated_at: string;
}
export interface BeliefWorksite {
  worksite_id: string;
  site_id?: string;
  display_name?: string;
  evidence_status?: string;
  operational_status?: string;
  assigned_resource_ids?: string[];
}
/** Shape of GET /api/demo-sessions/:id/belief-state. */
export interface BeliefSnapshot {
  schema_version: string;
  session_id: string;
  scenario_id: string;
  state_version: string;
  scenario_time: string | null;
  updated_at: string | null;
  incidents: unknown[];
  worksites: BeliefWorksite[];
  sites: unknown[];
  resources: unknown[];
  road_states: BeliefRoadState[];
  unresolved_observations: unknown[];
}

export interface RoadCounts { limited: number; blocked: number }
export function roadStateCounts(roads?: BeliefRoadState[]): RoadCounts {
  const states = roads ?? [];
  return {
    limited: states.filter((road) => road.status === "limited").length,
    blocked: states.filter((road) => road.status === "blocked").length,
  };
}

export interface BuildingCounts { reported: number; people: number; clear: number }
/** Counts reuse the same site colour derivation the map uses, so panel and map agree. */
export function buildingStateCounts(reports?: IntelligenceReport[]): BuildingCounts {
  const statuses = Object.values(siteScanStatuses(reports ?? []));
  return {
    reported: statuses.filter((item) => item.status === "unverified").length,
    people: statuses.filter((item) => item.status === "people_indicated").length,
    clear: statuses.filter((item) => item.status === "none_indicated").length,
  };
}

export type SearchPhase =
  | "idle"
  | "connecting"
  | "offline"
  | "waiting"
  | "launching"
  | "active"
  | "scanning"
  | "standby";
export interface SearchTickView {
  initialized: boolean;
  tracking_demo?: boolean;
  scanning_enabled?: boolean;
  target_priorities?: { assigned: boolean }[];
  inspected_site_ids?: string[];
  missions: { phase: string }[];
}
export interface SearchStatusView { phase: SearchPhase; label: string; detail: string }

const LIVE_PHASES = new Set(["en_route", "verifying", "reporting", "returning"]);

export function openWorksiteCount(worksites?: BeliefWorksite[]): number {
  return (worksites ?? []).filter((item) => item.operational_status !== "resolved").length;
}

/** Targets Search still owes a scan, from its own plan when published, otherwise from belief. */
export function outstandingTargets(tick: SearchTickView, worksites?: BeliefWorksite[]): number {
  if (tick.target_priorities)
    return tick.target_priorities.filter((target) => !target.assigned).length;
  const scanned = new Set(tick.inspected_site_ids ?? []);
  return (worksites ?? []).filter(
    (item) => item.operational_status !== "resolved" && !scanned.has(item.site_id ?? ""),
  ).length;
}

export function searchStatus(input: {
  sessionId?: string;
  error?: string;
  tick?: SearchTickView;
  worksites?: BeliefWorksite[];
}): SearchStatusView {
  const open = openWorksiteCount(input.worksites);
  const belief = `${open} worksite${open === 1 ? "" : "s"} in canonical belief`;
  if (!input.sessionId)
    return { phase: "idle", label: "Not started", detail: "Start a command session, then Start Caraballeda Demo." };
  if (input.error)
    return { phase: "offline", label: "Offline", detail: "Search state is unavailable from the server." };
  const tick = input.tick;
  if (!tick)
    return { phase: "connecting", label: "Connecting", detail: "Reading Search state from the server." };
  if (!tick.initialized)
    return open
      ? { phase: "launching", label: "Launching", detail: `${belief} · Search initialises on its next tick.` }
      : {
          phase: "waiting",
          label: "Waiting for belief",
          detail: "No actionable worksite yet. Start Caraballeda Demo and Search launches itself.",
        };
  const live = tick.missions.filter((mission) => LIVE_PHASES.has(mission.phase));
  if (live.some((mission) => mission.phase === "verifying"))
    return { phase: "scanning", label: "Scanning", detail: `Drone verifying on target · ${belief}.` };
  if (live.length)
    return {
      phase: "active",
      label: "Flying",
      detail: `${live.length} drone mission${live.length === 1 ? "" : "s"} running · ${belief}.`,
    };
  const remaining = outstandingTargets(tick, input.worksites);
  if (!remaining)
    return { phase: "standby", label: "Standby", detail: `Every known target is scanned · ${belief}.` };
  if (tick.tracking_demo && !tick.scanning_enabled)
    return {
      phase: "standby",
      label: "Standby",
      detail: `Standalone tracking harness · ${remaining} target${remaining === 1 ? "" : "s"} need a manual dispatch.`,
    };
  return {
    phase: "launching",
    label: "Launching",
    detail: `${remaining} target${remaining === 1 ? "" : "s"} outstanding · Search dispatches idle drones on its next tick.`,
  };
}

export type StepState = "done" | "active" | "pending";
export interface DemoStep { id: string; label: string; state: StepState }
export interface DemoProgressInput {
  session?: boolean;
  reports?: IntelligenceReport[];
  pendingReports?: number;
  worksites?: BeliefWorksite[];
  roads?: BeliefRoadState[];
  tick?: SearchTickView;
  save?: { resources?: { current_worksite_id: string | null }[] };
}
export interface DemoProgressView {
  steps: DemoStep[];
  done: number;
  total: number;
  current: DemoStep | null;
}

/** The observable Caraballeda sequence, each step proven only by server-reported state. */
export function demoProgress(input: DemoProgressInput): DemoProgressView {
  const reports = input.reports ?? [];
  const social = reports.filter((report) => report.source === "social" && report.observations.length);
  const radio = reports.filter((report) => report.source === "radio" && report.observations.length);
  const scans = reports.filter((report) => !!report.inspection);
  const roads = roadStateCounts(input.roads);
  const launched = (input.tick?.missions ?? []).length > 0;
  const dispatched = (input.save?.resources ?? []).some((item) => !!item.current_worksite_id);
  const reached: [string, string, boolean][] = [
    ["report", "Social report arrives · building turns yellow", social.length > 0],
    [
      "worksite",
      "Belief opens an uncertain worksite · Search launches a drone",
      (input.worksites ?? []).length > 0 && launched,
    ],
    ["corroborated", "Radio corroborates · Save dispatches a rescue team", radio.length > 0 && dispatched],
    ["backfill", "Later social reports add more yellow targets", social.length > 1 && !(input.pendingReports ?? 0)],
    ["scan", "Drone scans resolve buildings red or green", scans.length > 0],
    ["rumour", "Rumoured site cleared by a negative scan", scans.some((report) => report.inspection?.people === "none")],
    ["limited", "Limited road segments show yellow", roads.limited > 0],
    ["blocked", "Road obstruction reported · blocked to vehicles", roads.blocked > 0],
  ];
  const next = reached.findIndex(([, , done]) => !done);
  const steps: DemoStep[] = reached.map(([id, label, done], index) => ({
    id,
    label,
    state: done ? "done" : input.session && index === next ? "active" : "pending",
  }));
  return {
    steps,
    done: steps.filter((step) => step.state === "done").length,
    total: steps.length,
    current: steps.find((step) => step.state === "active") ?? null,
  };
}
