import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const repoRoot = fileURLToPath(new URL("../../../", import.meta.url));

export function pythonBin(): string {
  return (
    process.env.PYTHON_BIN ||
    (existsSync(new URL("../../../.venv/bin/python", import.meta.url))
      ? fileURLToPath(new URL("../../../.venv/bin/python", import.meta.url))
      : "python3")
  );
}

type Pending = {
  resolve: (value: Record<string, unknown>) => void;
  reject: (error: Error) => void;
};

/**
 * Long-lived Python stdin/stdout worker. Save keeps the road graph loaded
 * instead of spawning a process per Dashboard tick.
 */
export class NdjsonPythonWorker {
  private child: ChildProcessWithoutNullStreams | undefined;
  private queue: Pending[] = [];
  private starting: Promise<void> | undefined;
  private dead = false;

  constructor(
    private readonly moduleName: string,
    private readonly args: string[] = ["--ndjson"],
    private readonly timeoutMs = 30_000,
  ) {}

  async request(payload: unknown): Promise<Record<string, unknown>> {
    await this.ensure();
    const child = this.child;
    if (!child?.stdin.writable) {
      throw new Error(`${this.moduleName} worker is not running`);
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const index = this.queue.findIndex((item) => item.resolve === wrappedResolve);
        if (index >= 0) this.queue.splice(index, 1);
        reject(new Error(`${this.moduleName} timed out`));
      }, this.timeoutMs);
      const wrappedResolve = (value: Record<string, unknown>) => {
        clearTimeout(timer);
        resolve(value);
      };
      const wrappedReject = (error: Error) => {
        clearTimeout(timer);
        reject(error);
      };
      this.queue.push({ resolve: wrappedResolve, reject: wrappedReject });
      try {
        child.stdin.write(`${JSON.stringify(payload)}\n`);
      } catch (error) {
        this.queue.pop();
        clearTimeout(timer);
        reject(error as Error);
      }
    });
  }

  stop(): void {
    this.dead = true;
    const error = new Error(`${this.moduleName} worker stopped`);
    for (const pending of this.queue) pending.reject(error);
    this.queue = [];
    this.child?.kill("SIGTERM");
    this.child = undefined;
  }

  private async ensure(): Promise<void> {
    if (this.child && !this.dead) return;
    if (this.starting) return this.starting;
    this.starting = this.spawn();
    try {
      await this.starting;
    } finally {
      this.starting = undefined;
    }
  }

  private async spawn(): Promise<void> {
    this.dead = false;
    const child = spawn(pythonBin(), ["-m", this.moduleName, ...this.args], {
      cwd: repoRoot,
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child = child;
    child.stderr.on("data", (chunk) => {
      const text = String(chunk).trim();
      if (text) console.error(`[${this.moduleName}] ${text}`);
    });
    const lines = createInterface({ input: child.stdout });
    lines.on("line", (line) => {
      const pending = this.queue.shift();
      if (!pending) return;
      try {
        const value = JSON.parse(line) as Record<string, unknown>;
        pending.resolve(value);
      } catch (error) {
        pending.reject(error as Error);
      }
    });
    child.on("exit", () => {
      this.dead = true;
      this.child = undefined;
      const error = new Error(`${this.moduleName} worker exited`);
      for (const pending of this.queue) pending.reject(error);
      this.queue = [];
    });
    await new Promise<void>((resolve, reject) => {
      child.once("spawn", () => resolve());
      child.once("error", reject);
    });
  }
}
