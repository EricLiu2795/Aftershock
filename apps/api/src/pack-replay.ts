/**
 * Agent 2 pack-replay lifecycle.
 *
 * This is not a scenario framework and does not apply Caraballeda pack
 * payloads. Agent 2 registers an applicator and/or calls `startPackReplay`
 * with pack events. This module only:
 * - starts from the beginning
 * - stops old playback and clears timers
 * - refuses a second simultaneous player for the same session
 */
export interface ReplayClock {
  now(): number;
  setTimeout(callback: () => void, ms: number): NodeJS.Timeout;
  clearTimeout(id: NodeJS.Timeout): void;
}

export interface PackReplayEvent {
  event_id: string;
  scenario_time_s: number;
  payload?: unknown;
}

export type PackReplayApply = (
  sessionId: string,
  event: PackReplayEvent,
) => void | Promise<void>;

export type PackReplayStatus = "idle" | "running" | "stopped" | "completed";

interface Playback {
  status: Exclude<PackReplayStatus, "idle">;
  startedAt: string;
  timers: NodeJS.Timeout[];
  applied: string[];
}

const playback = new Map<string, Playback>();
let packApplicator: PackReplayApply | undefined;
let clock: ReplayClock = {
  now: () => Date.now(),
  setTimeout(callback, ms) {
    const timer = setTimeout(callback, ms);
    timer.unref();
    return timer;
  },
  clearTimeout(id) {
    clearTimeout(id);
  },
};

export function setPackReplayClock(next: ReplayClock): void {
  clock = next;
}

export function resetPackReplayClock(): void {
  clock = {
    now: () => Date.now(),
    setTimeout(callback, ms) {
      const timer = setTimeout(callback, ms);
      timer.unref();
      return timer;
    },
    clearTimeout(id) {
      clearTimeout(id);
    },
  };
}

/** Agent 2: register the Caraballeda pack event applicator. */
export function registerPackReplayApplicator(apply: PackReplayApply): void {
  packApplicator = apply;
}

export function clearPackReplayApplicator(): void {
  packApplicator = undefined;
}

export function registeredPackReplayApplicator(): PackReplayApply | undefined {
  return packApplicator;
}

export function stopPackReplay(sessionId: string): { stopped: boolean } {
  const current = playback.get(sessionId);
  if (!current) return { stopped: false };
  for (const timer of current.timers) clock.clearTimeout(timer);
  current.timers = [];
  current.status = "stopped";
  playback.delete(sessionId);
  return { stopped: true };
}

export function stopAllPackReplay(): void {
  for (const sessionId of [...playback.keys()]) stopPackReplay(sessionId);
}

export function packReplayStatus(sessionId: string): {
  status: PackReplayStatus;
  started_at: string | null;
  applied_event_ids: string[];
} {
  const current = playback.get(sessionId);
  return {
    status: current?.status ?? "idle",
    started_at: current?.startedAt ?? null,
    applied_event_ids: current?.applied ?? [],
  };
}

export async function startPackReplay(
  sessionId: string,
  events: PackReplayEvent[],
  apply: PackReplayApply,
): Promise<{
  started: boolean;
  already_running: boolean;
  event_ids: string[];
}> {
  const existing = playback.get(sessionId);
  if (existing?.status === "running") {
    return {
      started: false,
      already_running: true,
      event_ids: existing.applied,
    };
  }
  stopPackReplay(sessionId);
  const startedMs = clock.now();
  const state: Playback = {
    status: "running",
    startedAt: new Date(startedMs).toISOString(),
    timers: [],
    applied: [],
  };
  playback.set(sessionId, state);

  const pending = [...events].sort(
    (a, b) =>
      a.scenario_time_s - b.scenario_time_s ||
      a.event_id.localeCompare(b.event_id),
  );

  const runEvent = async (event: PackReplayEvent) => {
    if (playback.get(sessionId) !== state || state.status !== "running") return;
    await apply(sessionId, event);
    if (playback.get(sessionId) !== state || state.status !== "running") return;
    if (!state.applied.includes(event.event_id))
      state.applied.push(event.event_id);
  };

  const immediate = pending.filter((event) => event.scenario_time_s <= 0);
  const delayed = pending.filter((event) => event.scenario_time_s > 0);
  for (const event of immediate) await runEvent(event);

  if (!delayed.length) {
    if (playback.get(sessionId) === state) state.status = "completed";
    return {
      started: true,
      already_running: false,
      event_ids: state.applied,
    };
  }

  let remaining = delayed.length;
  for (const event of delayed) {
    const timer = clock.setTimeout(() => {
      state.timers = state.timers.filter((item) => item !== timer);
      void runEvent(event).finally(() => {
        remaining -= 1;
        if (
          remaining <= 0 &&
          playback.get(sessionId) === state &&
          state.status === "running"
        ) {
          state.status = "completed";
        }
      });
    }, event.scenario_time_s * 1000);
    state.timers.push(timer);
  }
  return {
    started: true,
    already_running: false,
    event_ids: state.applied,
  };
}
