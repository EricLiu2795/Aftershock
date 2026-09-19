import { execFile, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const children = new Set<ChildProcess>();

export function pythonBin(): string {
  return (
    process.env.PYTHON_BIN ||
    (existsSync(new URL("../../../.venv/bin/python", import.meta.url))
      ? fileURLToPath(new URL("../../../.venv/bin/python", import.meta.url))
      : "python3")
  );
}

export function repoRoot(): string {
  return fileURLToPath(new URL("../../../", import.meta.url));
}

export function runPythonModule(
  args: string[],
  options: { input?: string; timeout?: number; maxBuffer?: number } = {},
): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = execFile(
      pythonBin(),
      args,
      {
        cwd: repoRoot(),
        timeout: options.timeout,
        maxBuffer: options.maxBuffer,
      },
      (error, stdout) => {
        children.delete(child);
        if (error)
          return reject(
            error instanceof Error ? error : new Error(String(error)),
          );
        resolve(stdout);
      },
    );
    children.add(child);
    child.stdin?.on("error", () => {
      /* execFile callback handles process failure */
    });
    child.stdin?.end(options.input ?? "");
  });
}

export function stopPythonChildren(): void {
  for (const child of children) {
    if (child.exitCode === null && !child.killed) child.kill("SIGTERM");
  }
  children.clear();
}
