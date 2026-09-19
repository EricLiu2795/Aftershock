import { loadConfig } from "./config.js";
import { buildApi } from "./server.js";

const config = loadConfig();
const { app } = await buildApi(config);

try {
  await app.listen({ host: config.host, port: config.port });
} catch (error) {
  app.log.error(error);
  process.exitCode = 1;
}
