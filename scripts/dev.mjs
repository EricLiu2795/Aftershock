import { spawn } from "node:child_process";

// Run from any directory while loading the same root .env as dev:api.
// First-demo processes: API (Node) and Vite web. Search/ingestion Python is
// spawned per request by the API and reaped on API shutdown. Save Planner is
// not part of this startup path.
const cwd = new URL("../", import.meta.url);
const windows = process.platform === "win32";
const children = [
  spawn(
    process.execPath,
    ["--import", "tsx", "--env-file-if-exists=.env", "apps/api/src/index.ts"],
    { cwd, stdio: "inherit", detached: !windows },
  ),
  spawn(windows ? "npm.cmd" : "npm", ["run", "dev:web"], {
    cwd,
    stdio: "inherit",
    detached: !windows,
  }),
];

let stopping = false;

function signal(child, name) {
  if (!child.pid || child.exitCode !== null || child.signalCode) return;
  try {
    if (!windows) process.kill(-child.pid, name);
    else child.kill(name);
  } catch {
    try {
      child.kill(name);
    } catch {
      /* already exited */
    }
  }
}

function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) signal(child, "SIGTERM");
  const deadline = Date.now() + 5_000;
  const timer = setInterval(() => {
    const live = children.filter(
      (child) => child.exitCode === null && !child.signalCode,
    );
    if (!live.length || Date.now() >= deadline) {
      for (const child of live) signal(child, "SIGKILL");
      clearInterval(timer);
      process.exit(code);
    }
  }, 100);
}

for (const child of children) {
  child.on("error", (error) => {
    console.error(error.message);
    stop(1);
  });
  child.on("exit", (code, signalName) => {
    if (!stopping) stop(code ?? (signalName ? 1 : 0));
  });
}
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
