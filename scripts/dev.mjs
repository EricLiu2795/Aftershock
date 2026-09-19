import { spawn } from "node:child_process";
// Run from any directory while loading the same root .env as dev:api.
const cwd = new URL("../", import.meta.url);
// Node >= 20.12 refuses to spawn Windows .cmd shims (npm.cmd) without a shell and
// fails with EINVAL. Static arguments only, so there is nothing to interpolate.
const windows = process.platform === "win32";
const children = [
  spawn(
    process.execPath,
    ["--import", "tsx", "--env-file-if-exists=.env", "apps/api/src/index.ts"],
    { cwd, stdio: "inherit" },
  ),
  spawn(windows ? "npm.cmd" : "npm", ["run", "dev:web"], {
    cwd,
    stdio: "inherit",
    shell: windows,
  }),
];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill("SIGTERM");
  process.exitCode = code;
}
for (const child of children) {
  child.on("error", (error) => {
    console.error(error.message);
    stop(1);
  });
  child.on("exit", (code) => stop(code ?? 0));
}
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
