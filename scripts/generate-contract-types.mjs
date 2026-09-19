import { compileFromFile } from "json-schema-to-typescript";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
const schemas = [
  "radio_transmission",
  "radio_finalize_request",
  "radio_finalize_response",
  "radio_session_snapshot",
  "dashboard_event",
  "radio_extraction_result",
  "raw_radio_input",
  "observation",
  "location_resolution",
];
await mkdir(`${root}contracts/generated`, { recursive: true });
for (const schema of schemas) {
  const output = await compileFromFile(
    `${root}contracts/${schema}.schema.json`,
    {
      bannerComment:
        "/* Generated from JSON Schema. Run npm run contracts:generate. Do not edit. */",
      additionalProperties: false,
      maxItems: -1,
      ignoreMinAndMaxItems: true,
      format: true,
    },
  );
  const path = `${root}contracts/generated/${schema}.d.ts`;
  if (process.argv.includes("--check")) {
    if ((await readFile(path, "utf8").catch(() => "")) !== output)
      throw new Error(`Generated contract is stale: ${schema}`);
  } else await writeFile(path, output);
}
