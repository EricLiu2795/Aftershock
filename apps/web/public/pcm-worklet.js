// Audio runs only after the provider is ready. The stop acknowledgement is an
// ordered barrier: every buffered frame is posted before 'drained'.
class RadioPCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.active = false;
    this.samples = [];
    this.chunkSize = Math.round(sampleRate / 10);
    this.port.onmessage = ({ data }) => {
      if (data === "start") this.active = true;
      if (data === "stop") {
        this.active = false;
        this.flush();
        this.port.postMessage({ type: "drained" });
      }
    };
  }
  flush() {
    if (!this.samples.length) return;
    const buffer = new ArrayBuffer(this.samples.length * 2);
    const view = new DataView(buffer);
    let energy = 0;
    this.samples.forEach((value, index) => {
      const clamped = Math.max(-1, Math.min(1, value));
      energy += clamped * clamped;
      view.setInt16(
        index * 2,
        Math.round(clamped * (clamped < 0 ? 32768 : 32767)),
        true,
      );
    });
    this.port.postMessage(
      { type: "audio", buffer, level: Math.sqrt(energy / this.samples.length) },
      [buffer],
    );
    this.samples = [];
  }
  process(inputs) {
    if (!this.active || !inputs[0]?.length) return true;
    const channels = inputs[0];
    for (let i = 0; i < channels[0].length; i++) {
      let sample = 0;
      for (const channel of channels) sample += channel[i] / channels.length;
      this.samples.push(sample);
      if (this.samples.length >= this.chunkSize) this.flush();
    }
    return true;
  }
}
registerProcessor("radio-pcm", RadioPCM);
