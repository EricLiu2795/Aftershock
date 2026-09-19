import { afterEach, expect, it, vi } from "vitest";
import { ElevenLabsSpeech } from "../src/radio/elevenlabs-adapter";
class Socket {
  static OPEN = 1;
  static instance: Socket;
  readyState = 1;
  bufferedAmount = 0;
  onmessage?: (event: { data: string }) => void;
  onclose?: () => void;
  onerror?: () => void;
  sent: unknown[] = [];
  closed = false;
  constructor(public url: string) {
    Socket.instance = this;
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
  emit(message_type: string, text = "") {
    this.onmessage?.({ data: JSON.stringify({ message_type, text }) });
  }
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
function setup() {
  vi.stubGlobal("WebSocket", Socket);
  const callbacks = { partial: vi.fn(), committed: vi.fn(), failed: vi.fn() };
  const speech = new ElevenLabsSpeech(callbacks);
  return { speech, callbacks };
}
it("waits for provider readiness and committed text; ignores timestamp duplicates", async () => {
  const { speech, callbacks } = setup();
  let ready = false;
  const connect = speech.connect("single-use-test-token", 44100).then(() => {
    ready = true;
  });
  expect(ready).toBe(false);
  Socket.instance.emit("session_started");
  await connect;
  expect(new URL(Socket.instance.url).searchParams.get("audio_format")).toBe(
    "pcm_44100",
  );
  speech.send(new ArrayBuffer(8820), 44100);
  let committed = false;
  const commit = speech.commit(44100).then(() => {
    committed = true;
  });
  Socket.instance.emit("partial_transcript", "help");
  await Promise.resolve();
  expect(committed).toBe(false);
  Socket.instance.emit("committed_transcript", "help");
  await commit;
  Socket.instance.emit("committed_transcript_with_timestamps", "help");
  expect(callbacks.committed).toHaveBeenCalledTimes(1);
  expect(Socket.instance.closed).toBe(false);
  expect(Socket.instance.sent.at(-1)).toMatchObject({
    commit: true,
    sample_rate: 44100,
  });
  speech.close();
  expect(Socket.instance.closed).toBe(true);
});
it("does not treat a socket close as a final transcript", async () => {
  const { speech } = setup();
  const connect = speech.connect("token", 48000);
  Socket.instance.emit("session_started");
  await connect;
  const commit = speech.commit(48000);
  Socket.instance.onclose?.();
  await expect(commit).rejects.toThrow("interrupted");
});
it("closes on a final commit timeout and retains partial text only as draft", async () => {
  vi.useFakeTimers();
  const { speech, callbacks } = setup();
  const connect = speech.connect("token", 48000);
  Socket.instance.emit("session_started");
  await connect;
  const commit = speech.commit(48000);
  const assertion = expect(commit).rejects.toThrow("unsent draft");
  await vi.advanceTimersByTimeAsync(10_000);
  await assertion;
  expect(Socket.instance.closed).toBe(true);
  expect(callbacks.committed).not.toHaveBeenCalled();
});
