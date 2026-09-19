import { api, ApiError, message } from "../api";
import { BrowserCapture, type Capture } from "./capture-controller";
import type {
  RadioFinalizeRequest as Finalize,
  Segment,
} from "../../../../contracts/generated/radio_finalize_request";
export type { Finalize, Segment };
export interface RadioState {
  captureMode?: "live_microphone" | "audio_replay";
  phase:
    | "ready"
    | "connecting"
    | "recording"
    | "finalizing"
    | "submitting"
    | "processing"
    | "error";
  caption: string;
  detail: string;
  level: number;
  elapsed: number;
  pending: boolean;
}
interface Pending {
  id: string;
  body?: Finalize;
  reservation?: { key: string; mode: string };
}
interface Dependencies {
  request: typeof api;
  capture(callbacks: ConstructorParameters<typeof BrowserCapture>[0]): Capture;
  save(pending: Pending | null): void;
}
export class TransmissionController {
  state: RadioState = {
    phase: "ready",
    caption: "",
    detail: "Ready to connect",
    level: 0,
    elapsed: 0,
    pending: false,
  };
  private generation = 0;
  private capture?: Capture;
  private pending?: Pending;
  private segments: Segment[] = [];
  private started = 0;
  private timer?: ReturnType<typeof setInterval>;
  private polling?: ReturnType<typeof setTimeout>;
  private disposed = false;
  private retrying = false;
  private starting = false;
  constructor(
    private changed: (state: RadioState) => void,
    private deps: Dependencies,
    restored?: Pending,
  ) {
    this.pending = restored;
    if (restored)
      this.update({
        phase: "error",
        pending: true,
        detail:
          "An unfinished transmission was recovered. Retry to check its status.",
      });
  }
  private update(patch: Partial<RadioState>) {
    this.state = { ...this.state, ...patch };
    if (!this.disposed) this.changed(this.state);
  }
  private remember(pending?: Pending) {
    this.pending = pending;
    this.deps.save(pending ?? null);
    this.update({ pending: Boolean(pending) });
  }
  async start(file?: File) {
    if (
      this.starting ||
      this.pending ||
      !["ready", "error"].includes(this.state.phase) ||
      this.disposed
    )
      return;
    this.starting = true;
    const generation = ++this.generation;
    this.segments = [];
    this.started = Date.now();
    this.update({
      captureMode: file ? "audio_replay" : "live_microphone",
      phase: "connecting",
      caption: "",
      detail: "Connecting — wait before speaking",
      elapsed: 0,
    });
    const current = () => generation === this.generation && !this.disposed;
    const capture = (this.capture = this.deps.capture({
      partial: (text) => {
        if (current())
          this.update({
            caption: [...this.segments.map((s) => s.text), text].join(" "),
          });
      },
      committed: (text) => {
        if (!current() || !text.trim()) return;
        this.segments.push({
          sequence: this.segments.length,
          text: text.trim(),
          started_at_ms: this.segments.at(-1)?.ended_at_ms ?? 0,
          ended_at_ms: Math.max(1, Date.now() - this.started),
        });
        this.update({ caption: this.segments.map((s) => s.text).join(" ") });
      },
      failed: (error) => {
        if (current()) void this.abort(message(error));
      },
      level: (level) => {
        if (current()) this.update({ level });
      },
      ended: () => {
        if (current()) void this.stop();
      },
    }));
    const deadline = setTimeout(() => {
      if (current())
        void this.abort(
          "Connection setup timed out. Check microphone permission and try again.",
        );
    }, 20_000);
    try {
      await capture.start(async () => {
        if (!current()) throw new Error("Capture cancelled");
        this.remember({
          id: "",
          reservation: {
            key: crypto.randomUUID(),
            mode: file ? "audio_replay" : "live_microphone",
          },
        });
        await this.reservePending();
        if (!current()) {
          await this.cancelPending();
          throw new Error("Capture cancelled");
        }
        const result = await this.deps.request<{ token: string }>(
          `/api/radio/transmissions/${this.pending!.id}/token`,
          {},
        );
        if (!current()) throw new Error("Capture cancelled");
        return result.token;
      }, file);
      if (!current()) {
        capture.close();
        return;
      }
      this.started = Date.now();
      this.update({
        phase: "recording",
        detail: file ? "Streaming prerecorded audio" : "Speak now",
      });
      this.timer = setInterval(() => {
        const elapsed = Math.floor((Date.now() - this.started) / 1000);
        this.update({ elapsed });
        if (elapsed >= 60) void this.stop();
      }, 250);
    } catch (error) {
      if (current()) await this.abort(message(error));
    } finally {
      clearTimeout(deadline);
      this.starting = false;
    }
  }
  async stop() {
    if (this.state.phase === "connecting") {
      await this.abort("Released before ready — no report sent");
      return;
    }
    if (this.state.phase !== "recording") return;
    const generation = this.generation;
    clearInterval(this.timer);
    const ended = new Date().toISOString();
    this.update({
      phase: "finalizing",
      detail: "Finishing audio and waiting for final text…",
      level: 0,
    });
    try {
      await this.capture!.finish();
      if (generation !== this.generation) return;
      const transcript = this.segments.map((s) => s.text).join(" ");
      if (!transcript.trim()) {
        await this.abort("No speech detected — no report sent");
        return;
      }
      this.remember({
        id: this.pending!.id,
        body: {
          schema_version: "2.0",
          transmission_id: this.pending!.id,
          transcript,
          capture_started_at: new Date(this.started).toISOString(),
          capture_ended_at: ended,
          segments: this.segments,
        },
      });
      await this.submit();
    } catch (error) {
      if (generation === this.generation) await this.abort(message(error));
    }
  }
  private async submit() {
    if (!this.pending?.body) return;
    this.update({ phase: "submitting", detail: "Saving report…" });
    try {
      await this.deps.request(
        `/api/radio/transmissions/${this.pending.id}/finalize`,
        this.pending.body,
      );
      this.update({
        phase: "processing",
        detail: "Accepted — extracting report",
      });
      this.poll();
    } catch (error) {
      this.update({
        phase: "error",
        detail: `${message(error)}. Retry sends the same report ID.`,
      });
    }
  }
  private poll() {
    clearTimeout(this.polling);
    this.polling = setTimeout(() => {
      void this.retry();
    }, 1500);
  }
  async retry() {
    if (!this.pending || this.retrying || this.disposed) return;
    this.retrying = true;
    try {
      await this.reservePending();
      const result = await this.deps.request<{ processing_state: string }>(
        `/api/radio/transmissions/${this.pending.id}`,
      );
      if (this.disposed) return;
      if (result.processing_state === "reserved") {
        if (this.pending.body) await this.submit();
        else {
          await this.cancelPending();
          this.update({
            phase: "ready",
            detail: "Interrupted attempt cancelled. Ready to try again.",
          });
        }
      } else if (
        ["accepted", "extracting", "retrying", "resolving"].includes(
          result.processing_state,
        )
      ) {
        this.update({
          phase: "processing",
          detail: `Report ${result.processing_state}`,
        });
        this.poll();
      } else {
        this.remember();
        this.update({
          phase: "ready",
          detail: `Last report: ${result.processing_state}`,
        });
      }
    } catch (error) {
      if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        this.remember();
        this.update({
          phase: "error",
          detail:
            "This transmission is no longer accessible. Leave and join a valid session to continue.",
        });
      } else
        this.update({
          phase: "error",
          detail: `${message(error)}. Retry to recover the report.`,
        });
    } finally {
      this.retrying = false;
    }
  }
  private async reservePending() {
    const pending = this.pending;
    if (!pending || pending.id || !pending.reservation) return;
    const result = await this.deps.request<{ transmission_id: string }>(
      "/api/radio/transmissions",
      { capture_mode: pending.reservation.mode, channel: "command-1" },
      { headers: { "idempotency-key": pending.reservation.key } },
    );
    // A concurrent cancellation can have cleared this reservation already.
    if (this.pending?.reservation?.key === pending.reservation.key)
      this.remember({ ...pending, id: result.transmission_id });
  }
  private async cancelPending() {
    if (!this.pending || this.pending.body) return;
    await this.reservePending();
    if (!this.pending) return;
    await this.deps.request(
      `/api/radio/transmissions/${this.pending.id}/cancel`,
      {},
    );
    this.remember();
  }
  async abort(reason = "Capture interrupted — no report sent") {
    ++this.generation;
    clearInterval(this.timer);
    this.capture?.close();
    this.update({ phase: "error", level: 0, detail: reason });
    try {
      await this.cancelPending();
    } catch {
      this.update({
        detail: `${reason}. Retry to clear the reserved attempt.`,
      });
    }
  }
  interrupt() {
    if (["connecting", "recording", "finalizing"].includes(this.state.phase))
      void this.abort();
  }
  dispose() {
    this.disposed = true;
    this.interrupt();
    clearInterval(this.timer);
    clearTimeout(this.polling);
  }
}
export function browserController(
  changed: (state: RadioState) => void,
  session: string,
) {
  const key = `aftershock:pending:${session}`;
  let restored: Pending | undefined;
  try {
    restored = JSON.parse(sessionStorage.getItem(key) ?? "null") ?? undefined;
  } catch {
    /* empty recovery */
  }
  return new TransmissionController(
    changed,
    {
      request: api,
      capture: (callbacks) => new BrowserCapture(callbacks),
      save: (pending) => {
        if (pending) sessionStorage.setItem(key, JSON.stringify(pending));
        else sessionStorage.removeItem(key);
      },
    },
    restored,
  );
}
