import { afterEach, describe, expect, it, vi } from "vitest";
import { TransmissionController } from "../src/radio/transmission-controller";
import type { BrowserCapture } from "../src/radio/capture-controller";
const tick = async () => {
  for (let i = 0; i < 15; i++) await Promise.resolve();
};
function setup({ late = false, failFinalize = false } = {}) {
  let callbacks: ConstructorParameters<typeof BrowserCapture>[0];
  let complete: () => void = () => {};
  let status = "reserved";
  const order: string[] = [];
  const request = vi.fn(async (path: string, body?: unknown) => {
    if (path.endsWith("/transmissions")) return { transmission_id: "tx-one" };
    if (path.endsWith("/token")) return { token: "ephemeral" };
    if (path.endsWith("/cancel")) {
      status = "cancelled";
      return {};
    }
    if (path.endsWith("/finalize")) {
      order.push("finalize");
      status = "accepted";
      if (failFinalize) throw new Error("Acknowledgement lost");
      return {};
    }
    return { processing_state: status };
  });
  const close = vi.fn();
  const save = vi.fn();
  const controller = new TransmissionController(() => {}, {
    request: request as never,
    save,
    capture: (cb) => {
      callbacks = cb;
      return {
        start: async (token) => {
          await token();
          if (late)
            await new Promise<void>((resolve) => {
              complete = resolve;
            });
        },
        finish: async () => {
          order.push("drain");
          callbacks.committed("help");
          callbacks.committed("help");
          order.push("commit");
        },
        close,
      };
    },
  });
  return {
    controller,
    request,
    close,
    save,
    order,
    complete: () => complete(),
    setStatus: (value: string) => {
      status = value;
    },
  };
}
afterEach(() => vi.useRealTimers());
describe("radio transmission lifecycle", () => {
  it("drains and commits before one finalize, preserving repeated words", async () => {
    const test = setup();
    await test.controller.start();
    await Promise.all([test.controller.stop(), test.controller.stop()]);
    expect(test.order).toEqual(["drain", "commit", "finalize"]);
    const calls = test.request.mock.calls.filter(([path]) =>
      path.endsWith("/finalize"),
    );
    expect(calls).toHaveLength(1);
    expect(calls[0]?.[1]).toMatchObject({
      transcript: "help help",
      segments: [
        { sequence: 0, text: "help" },
        { sequence: 1, text: "help" },
      ],
    });
    test.controller.dispose();
  });
  it("cancels release during connection and closes a late capture", async () => {
    const test = setup({ late: true });
    const start = test.controller.start();
    await tick();
    await test.controller.stop();
    test.complete();
    await start;
    expect(test.close).toHaveBeenCalled();
    expect(
      test.request.mock.calls.some(([path]) => path.endsWith("/cancel")),
    ).toBe(true);
    expect(
      test.request.mock.calls.some(([path]) => path.endsWith("/finalize")),
    ).toBe(false);
    test.controller.dispose();
  });
  it("recovers a lost acknowledgement by checking the same transmission", async () => {
    const test = setup({ failFinalize: true });
    await test.controller.start();
    await test.controller.stop();
    expect(test.controller.state.phase).toBe("error");
    await test.controller.retry();
    expect(test.controller.state.phase).toBe("processing");
    expect(
      test.request.mock.calls.filter(([path]) => path.endsWith("/finalize")),
    ).toHaveLength(1);
    test.setStatus("resolved");
    await test.controller.retry();
    expect(test.controller.state.pending).toBe(false);
    expect(test.save).toHaveBeenLastCalledWith(null);
    test.controller.dispose();
  });
  it("auto-stops at sixty seconds", async () => {
    vi.useFakeTimers();
    const test = setup();
    await test.controller.start();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(
      test.request.mock.calls.filter(([path]) => path.endsWith("/finalize")),
    ).toHaveLength(1);
    test.controller.dispose();
  });
  it("interrupts on navigation without submitting partial captions", async () => {
    const test = setup();
    await test.controller.start();
    test.controller.dispose();
    await tick();
    expect(test.close).toHaveBeenCalled();
    expect(
      test.request.mock.calls.some(([path]) => path.endsWith("/finalize")),
    ).toBe(false);
  });
});
