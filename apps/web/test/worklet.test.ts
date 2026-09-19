import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { expect, it } from "vitest";
it("downmixes PCM16 and drains the final incomplete chunk before acknowledgement", () => {
  let Processor: any;
  const messages: any[] = [];
  class Base {
    port = {
      onmessage: (_: unknown) => {},
      postMessage: (value: unknown) => messages.push(value),
    };
  }
  runInNewContext(
    readFileSync(new URL("../public/pcm-worklet.js", import.meta.url), "utf8"),
    {
      AudioWorkletProcessor: Base,
      sampleRate: 48000,
      registerProcessor: (_name: string, implementation: unknown) => {
        Processor = implementation;
      },
    },
  );
  const worklet = new Processor();
  worklet.process([[new Float32Array([1, 1])]]);
  expect(messages).toHaveLength(0);
  worklet.port.onmessage({ data: "start" });
  worklet.process([
    [new Float32Array([1, -1, 1]), new Float32Array([1, -1, -1])],
  ]);
  expect(messages).toHaveLength(0); // sub-100ms audio is buffered
  worklet.port.onmessage({ data: "stop" });
  expect(messages.map((item) => item.type)).toEqual(["audio", "drained"]);
  const samples = new DataView(messages[0].buffer);
  expect([0, 2, 4].map((offset) => samples.getInt16(offset, true))).toEqual([
    32767, -32768, 0,
  ]);
  worklet.process([[new Float32Array([1])]]);
  expect(messages).toHaveLength(2);
});
