import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Storage } from "./storage.js";
import {
  packReplayStatus,
  registeredPackReplayApplicator,
  startPackReplay,
  stopAllPackReplay,
  stopPackReplay,
  type PackReplayEvent,
} from "./pack-replay.js";
import { resetOperationalState } from "./operational-reset.js";

export const FIRST_DEMO_RADIO_EVENT = "first-demo:radio-caraballeda";
export const FIRST_DEMO_SOCIAL_EVENT = "first-demo:social-replay";

const FIRST_DEMO_EVENTS: PackReplayEvent[] = [
  { event_id: FIRST_DEMO_RADIO_EVENT, scenario_time_s: 0 },
  { event_id: FIRST_DEMO_SOCIAL_EVENT, scenario_time_s: 0 },
];

export interface DemoActor {
  id: string;
  session_id: string;
}

export interface DemoHooks {
  replayCaraballedaRadio(sessionId: string, actor: DemoActor): Promise<void>;
  ingestSocialReplay(sessionId: string): Promise<number>;
}

export class DemoRuntime {
  private readonly generations = new Map<string, number>();

  constructor(
    private readonly storage: Storage,
    private readonly hooks: DemoHooks,
  ) {}

  generation(sessionId: string): number {
    return this.generations.get(sessionId) ?? 0;
  }

  bumpGeneration(sessionId: string): number {
    const next = this.generation(sessionId) + 1;
    this.generations.set(sessionId, next);
    return next;
  }

  ingestStillCurrent(sessionId: string, generation: number): boolean {
    return this.generation(sessionId) === generation;
  }

  reset(sessionId: string): {
    session_id: string;
    reset: true;
    replay: { stopped: boolean };
    operational: ReturnType<typeof resetOperationalState>;
  } {
    this.bumpGeneration(sessionId);
    const replay = stopPackReplay(sessionId);
    let operational: ReturnType<typeof resetOperationalState> | undefined;
    this.storage.resetSessionRuntime(sessionId, () => {
      operational = resetOperationalState(this.storage, sessionId);
    });
    return {
      session_id: sessionId,
      reset: true,
      replay,
      operational: operational ?? resetOperationalState(this.storage, sessionId),
    };
  }

  async start(
    sessionId: string,
    actor: DemoActor,
  ): Promise<{
    session_id: string;
    started: boolean;
    already_running: boolean;
    replay: ReturnType<typeof packReplayStatus> & { event_ids: string[] };
  }> {
    const current = packReplayStatus(sessionId);
    if (current.status === "running") {
      return {
        session_id: sessionId,
        started: false,
        already_running: true,
        replay: { ...current, event_ids: current.applied_event_ids },
      };
    }

    const generation = this.generation(sessionId);
    const apply = async (id: string, event: PackReplayEvent) => {
      if (!this.ingestStillCurrent(id, generation)) return;
      if (this.storage.hasReplayEvent(id, event.event_id)) return;
      if (event.event_id === FIRST_DEMO_RADIO_EVENT) {
        await this.hooks.replayCaraballedaRadio(id, actor);
      } else if (event.event_id === FIRST_DEMO_SOCIAL_EVENT) {
        await this.hooks.ingestSocialReplay(id);
      }
      if (!this.ingestStillCurrent(id, generation)) return;
      this.storage.recordReplayEvent(id, event.event_id);
    };
    const result = await startPackReplay(sessionId, FIRST_DEMO_EVENTS, apply);
    return {
      session_id: sessionId,
      started: result.started,
      already_running: result.already_running,
      replay: { ...packReplayStatus(sessionId), event_ids: result.event_ids },
    };
  }

  /**
   * Agent 2 hook: play `scenario_events.json` from t=0 with the registered
   * (or provided) pack applicator. Refuses a second simultaneous player.
   */
  async startCaraballedaPackReplay(
    sessionId: string,
    apply = registeredPackReplayApplicator(),
  ): Promise<{
    started: boolean;
    already_running: boolean;
    event_ids: string[];
  }> {
    if (!apply) {
      throw Object.assign(new Error("NO_PACK_REPLAY_APPLICATOR"), {
        statusCode: 500,
      });
    }
    const current = packReplayStatus(sessionId);
    if (current.status === "running") {
      return {
        started: false,
        already_running: true,
        event_ids: current.applied_event_ids,
      };
    }
    const generation = this.generation(sessionId);
    return startPackReplay(
      sessionId,
      loadCaraballedaPackEvents(),
      async (id, event) => {
        if (!this.ingestStillCurrent(id, generation)) return;
        if (this.storage.hasReplayEvent(id, event.event_id)) return;
        await apply(id, event);
        if (!this.ingestStillCurrent(id, generation)) return;
        this.storage.recordReplayEvent(id, event.event_id);
      },
    );
  }

  status(sessionId: string) {
    const replay = packReplayStatus(sessionId);
    return {
      session_id: sessionId,
      generation: this.generation(sessionId),
      replay: {
        ...replay,
        event_ids: this.storage.listReplayEvents(sessionId),
      },
      search_initialized: Boolean(
        this.storage.one(
          "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'drone_search_sessions'",
        ) &&
          this.storage.one(
            "SELECT 1 FROM drone_search_sessions WHERE session_id = ?",
            sessionId,
          ),
      ),
    };
  }

  stopAll(): void {
    stopAllPackReplay();
  }
}

export function loadCaraballedaPackEvents(): PackReplayEvent[] {
  const path = fileURLToPath(
    new URL(
      "../../../data/scenarios/caraballeda/scenario_events.json",
      import.meta.url,
    ),
  );
  const pack = JSON.parse(readFileSync(path, "utf8")) as {
    events: Array<{ event_id: string; scenario_time_s: number }>;
  };
  return pack.events.map((event) => ({
    event_id: event.event_id,
    scenario_time_s: event.scenario_time_s,
    payload: event,
  }));
}
