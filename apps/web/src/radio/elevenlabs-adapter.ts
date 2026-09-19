export interface SpeechCallbacks {
  partial(text: string): void;
  committed(text: string): void;
  failed(error: Error): void;
}
export class ElevenLabsSpeech {
  private socket?: WebSocket;
  private closed = false;
  private readyReject?: (error: Error) => void;
  private commitResolve?: () => void;
  private commitReject?: (error: Error) => void;
  constructor(private callbacks: SpeechCallbacks) {}
  async connect(token: string, sampleRate: number): Promise<void> {
    if (![8000, 16000, 22050, 24000, 44100, 48000].includes(sampleRate))
      throw new Error("Unsupported microphone sample rate");
    const params = new URLSearchParams({
      token,
      model_id: "scribe_v2_realtime",
      audio_format: `pcm_${sampleRate}`,
      language_code: "en",
      commit_strategy: "manual",
      include_timestamps: "false",
    });
    const socket = (this.socket = new WebSocket(
      `wss://api.elevenlabs.io/v1/speech-to-text/realtime?${params}`,
    ));
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () => this.fail(new Error("Speech connection timed out")),
        10_000,
      );
      this.readyReject = (error) => {
        clearTimeout(timer);
        reject(error);
      };
      socket.onmessage = ({ data }) => {
        if (this.closed) return;
        let event: { message_type: string; text?: string; error?: string };
        try {
          event = JSON.parse(String(data));
        } catch {
          this.fail(new Error("Invalid speech response"));
          return;
        }
        if (event.message_type === "session_started") {
          clearTimeout(timer);
          this.readyReject = undefined;
          resolve();
        } else if (event.message_type === "partial_transcript")
          this.callbacks.partial(event.text ?? "");
        else if (event.message_type === "committed_transcript") {
          this.callbacks.committed(event.text ?? "");
          this.commitResolve?.();
        } else if (
          event.error ||
          /error|quota|rate_limited|queue_overflow|resource_exhausted|time_limit/.test(
            event.message_type,
          )
        ) {
          this.fail(new Error(`Speech service failed: ${event.message_type}`));
        }
      };
      socket.onerror = () => this.fail(new Error("Speech connection failed"));
      socket.onclose = () => {
        if (!this.closed)
          this.fail(
            new Error("Speech connection interrupted; report was not sent"),
          );
      };
    });
  }
  send(buffer: ArrayBuffer, sampleRate: number, commit = false) {
    if (this.closed || this.socket?.readyState !== WebSocket.OPEN)
      throw new Error("Speech connection is not ready");
    if (this.socket.bufferedAmount > 2_000_000)
      throw new Error("Audio connection cannot keep up");
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    this.socket.send(
      JSON.stringify({
        message_type: "input_audio_chunk",
        audio_base_64: btoa(binary),
        sample_rate: sampleRate,
        commit,
      }),
    );
  }
  async commit(sampleRate: number) {
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () =>
          this.fail(
            new Error(
              "Final transcript timed out; available text remains an unsent draft",
            ),
          ),
        10_000,
      );
      this.commitResolve = () => {
        clearTimeout(timer);
        this.commitResolve = undefined;
        this.commitReject = undefined;
        resolve();
      };
      this.commitReject = (error) => {
        clearTimeout(timer);
        reject(error);
      };
      // A short silence chunk marks the boundary after all drained microphone PCM.
      try {
        this.send(
          new ArrayBuffer(Math.round(sampleRate / 10) * 2),
          sampleRate,
          true,
        );
      } catch (error) {
        this.fail(error instanceof Error ? error : new Error("Commit failed"));
      }
    });
  }
  private fail(error: Error) {
    if (this.closed) return;
    this.readyReject?.(error);
    this.commitReject?.(error);
    this.close();
    this.callbacks.failed(error);
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    const error = new Error("Capture cancelled");
    this.readyReject?.(error);
    this.commitReject?.(error);
    this.readyReject = undefined;
    this.commitReject = undefined;
    this.commitResolve = undefined;
    this.socket?.close();
  }
}
