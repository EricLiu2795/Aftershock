import { ElevenLabsSpeech } from "./elevenlabs-adapter";
export interface Capture {
  start(token: () => Promise<string>, file?: File): Promise<void>;
  finish(): Promise<void>;
  close(): void;
}
export class BrowserCapture implements Capture {
  private context?: AudioContext;
  private stream?: MediaStream;
  private source?: AudioNode;
  private node?: AudioWorkletNode;
  private speech: ElevenLabsSpeech;
  private cancelled = false;
  private drained?: () => void;
  private drainReject?: (error: Error) => void;
  constructor(callbacks: {
    partial(text: string): void;
    committed(text: string): void;
    failed(error: Error): void;
    level(value: number): void;
    ended(): void;
  }) {
    this.callbacks = callbacks;
    this.speech = new ElevenLabsSpeech(callbacks);
  }
  private callbacks;
  async start(token: () => Promise<string>, file?: File) {
    if (!window.isSecureContext)
      throw new Error("Microphone access requires HTTPS or localhost");
    // Create/resume synchronously inside the user gesture, before network awaits.
    const context = (this.context = new AudioContext());
    const resumed = context.resume();
    if (!file) {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      if (this.cancelled) {
        stream.getTracks().forEach((track) => track.stop());
        throw new Error("Capture cancelled");
      }
      this.stream = stream;
      stream.getAudioTracks().forEach((track) => {
        track.onended = () => {
          if (!this.cancelled)
            this.callbacks.failed(new Error("Microphone interrupted"));
        };
      });
    }
    await resumed;
    this.check();
    await context.audioWorklet.addModule("/pcm-worklet.js");
    this.check();
    let replay: AudioBuffer | undefined;
    if (file) {
      if (file.size > 20_000_000)
        throw new Error("Audio replay must be smaller than 20 MB");
      replay = await context.decodeAudioData(await file.arrayBuffer());
      this.check();
      if (replay.duration > 60)
        throw new Error("Audio replay must be 60 seconds or shorter");
    }
    const credential = await token();
    this.check();
    await this.speech.connect(credential, context.sampleRate);
    this.check();
    const node = (this.node = new AudioWorkletNode(context, "radio-pcm"));
    node.port.onmessage = ({ data }) => {
      if (this.cancelled) return;
      if (data.type === "drained") this.drained?.();
      if (data.type === "audio") {
        try {
          this.speech.send(data.buffer, context.sampleRate);
          this.callbacks.level(data.level);
        } catch (error) {
          this.callbacks.failed(
            error instanceof Error ? error : new Error("Audio send failed"),
          );
        }
      }
    };
    node.onprocessorerror = () =>
      this.callbacks.failed(new Error("Audio processor failed"));
    const muted = context.createGain();
    muted.gain.value = 0;
    node.connect(muted).connect(context.destination);
    node.port.postMessage("start");
    if (replay) {
      const source = context.createBufferSource();
      source.buffer = replay;
      this.source = source;
      source.connect(node);
      source.onended = () => {
        if (!this.cancelled) this.callbacks.ended();
      };
      source.start();
    } else {
      this.source = context.createMediaStreamSource(this.stream!);
      this.source.connect(node);
    }
  }
  private check() {
    if (this.cancelled) throw new Error("Capture cancelled");
  }
  async finish() {
    this.check();
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error("Audio drain timed out")),
        2000,
      );
      this.drained = () => {
        clearTimeout(timer);
        resolve();
      };
      this.drainReject = (error) => {
        clearTimeout(timer);
        reject(error);
      };
      this.node!.port.postMessage("stop");
    });
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source?.disconnect();
    await this.speech.commit(this.context!.sampleRate);
    this.close();
  }
  close() {
    this.cancelled = true;
    this.drainReject?.(new Error("Capture cancelled"));
    this.speech.close();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source?.disconnect();
    this.node?.disconnect();
    if (this.context && this.context.state !== "closed")
      void this.context.close().catch(() => {});
  }
}
