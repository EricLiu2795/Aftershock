import { defineConfig } from "vite";
export default defineConfig({
  envDir: "../..",
  server: {
    proxy: {
      "/api": process.env.API_PROXY_TARGET || "http://127.0.0.1:3001",
      "/health": process.env.API_PROXY_TARGET || "http://127.0.0.1:3001",
    },
  },
});
