import { afterEach, describe, expect, it } from "vitest";
import {
  packReplayStatus,
  resetPackReplayClock,
  setPackReplayClock,
  startPackReplay,
  stopAllPackReplay,
  stopPackReplay,
  type ReplayClock,
} from "../src/pack-replay.js";

afterEach(() => {
  stopAllPackReplay();
  resetPackReplayClock();
});

function fakeClock(): ReplayClock & { flush(ms?: number): void } {
  let now = 1_000;
  const timers = new Map<
    NodeJS.Timeout,
    { callback: () => void; due: number }
  >();
  let nextId = 1;
  return {
    now: () => now,
    setTimeout(callback, ms) {
      const id = nextId++ as unknown as NodeJS.Timeout;
      timers.set(id, { callback, due: now + ms });
      return Object.assign(id, { unref() {} }) as NodeJS.Timeout;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    flush(ms = 0) {
      now += ms;
      for (const [id, timer] of [...timers.entries()]) {
        if (timer.due <= now) {
          timers.delete(id);
          timer.callback();
        }
      }
    },
  };
}

describe("pack replay lifecycle hook", () => {
  it("starts from the beginning, ignores a second simultaneous start, and reset stops timers", async () => {
    const clock = fakeClock();
    setPackReplayClock(clock);
    const applied: string[] = [];
    const apply = async (_sessionId: string, event: { event_id: string }) => {
      applied.push(event.event_id);
    };
    const events = [
      { event_id: "E0", scenario_time_s: 0 },
      { event_id: "E1", scenario_time_s: 1 },
    ];
    const first = await startPackReplay("session-a", events, apply);
    const second = await startPackReplay("session-a", events, apply);
    expect(first.started).toBe(true);
    expect(second.already_running).toBe(true);
    expect(applied).toEqual(["E0"]);
    clock.flush(1000);
    await Promise.resolve();
    expect(applied).toEqual(["E0", "E1"]);
    expect(packReplayStatus("session-a").status).toBe("completed");

    applied.length = 0;
    const restarted = await startPackReplay("session-a", events, apply);
    expect(restarted.started).toBe(true);
    expect(applied).toEqual(["E0"]);
    stopPackReplay("session-a");
    clock.flush(1000);
    await Promise.resolve();
    expect(applied).toEqual(["E0"]);
    expect(packReplayStatus("session-a").status).toBe("idle");
  });

  it("isolates playback per session", async () => {
    const applied: string[] = [];
    await startPackReplay(
      "a",
      [{ event_id: "A", scenario_time_s: 0 }],
      async (sessionId, event) => {
        applied.push(`${sessionId}:${event.event_id}`);
      },
    );
    await startPackReplay(
      "b",
      [{ event_id: "B", scenario_time_s: 0 }],
      async (sessionId, event) => {
        applied.push(`${sessionId}:${event.event_id}`);
      },
    );
    expect(applied).toEqual(["a:A", "b:B"]);
    stopPackReplay("a");
    expect(packReplayStatus("a").status).toBe("idle");
    expect(packReplayStatus("b").status).toBe("completed");
  });
});
