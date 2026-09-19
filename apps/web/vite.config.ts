import { defineConfig } from "vite";
export default defineConfig({
  envDir: "../..",
  server: {
    proxy: {
      "/api": process.env.API_PROXY_TARGET || "http://127.0.0.1:3001",
      "/health": process.env.API_PROXY_TARGET || "http://127.0.0.1:3001",
      // Save planner (python -m saveplanner serve). Kept on one browser origin like
      // the rest of the dashboard; the planner exposes its routes under /api.
      "/saveplanner": {
        target: process.env.SAVE_PLANNER_TARGET || "http://127.0.0.1:8765",
        rewrite: (path: string) => path.replace(/^\/saveplanner/, "/api"),
      },
    },
  },
});
