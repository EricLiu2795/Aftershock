import { readFileSync } from "node:fs";
import type { ExtractionResult, SemanticObservation } from "./types.js";

// Only the named, checked-in Riverside fixture may use the cached observation
// path. Fresh source IDs/timestamps are still supplied by the ingestion worker.
export function cachedRiversideObservation(
  transcript: string,
): ExtractionResult {
  const base = new URL(
    "../../../fixtures/radio/simulated_riverside/",
    import.meta.url,
  );
  const raw = JSON.parse(
    readFileSync(new URL("raw_input.json", base), "utf8"),
  ) as { transcript: string };
  if (transcript !== raw.transcript) throw new Error("CACHED_FIXTURE_MISMATCH");
  const fixture = JSON.parse(
    readFileSync(new URL("extraction_result.json", base), "utf8"),
  ) as { observations: SemanticObservation[] };
  return {
    result: "observations",
    observations: fixture.observations,
    adapter: "cached",
    model: "cached-riverside-observations-v1",
    prompt_version: "radio-semantic-v1",
  };
}
