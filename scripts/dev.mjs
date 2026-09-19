import { spawn } from "node:child_process";
// Run from any directory while loading the same root .env as dev:api.
const cwd = new URL("../", import.meta.url);
const children = [
  spawn(
    process.execPath,
    ["--import", "tsx", "--env-file-if-exists=.env", "apps/api/src/index.ts"],
    { cwd, stdio: "inherit" },
  ),
  spawn(process.platform === "win32" ? "npm.cmd" : "npm", ["run", "dev:web"], {
    cwd,
    stdio: "inherit",
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
