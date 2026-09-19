import { readFileSync } from "node:fs";
import type { ExtractionResult, SemanticObservation } from "./types.js";

const FIXTURES = [
  {
    dir: "caraballeda_e02",
    model: "cached-caraballeda-e02-observations-v1",
    transcript:
      "Command, Rescue Team Alpha: partial collapse at Residencias Caribe, Avenida Principal de Caribe. Hearing at least 4 voices from the north side. East approach is limited by debris.",
  },
  {
    dir: "caraballeda",
    model: "cached-caraballeda-observations-v1",
    transcript:
      "Command, partial collapse at Residencias Las Anclas. Multiple voices heard. East access may be blocked.",
  },
  {
    dir: "simulated_riverside",
    model: "cached-riverside-observations-v1",
    transcript:
      "Command, partial collapse near Riverside Apartments. Multiple voices heard. East access may be blocked.",
  },
] as const;

// Only the named, checked-in fixtures may use the cached observation
// path. Fresh source IDs/timestamps are still supplied by the ingestion worker.
export function cachedRiversideObservation(
  transcript: string,
): ExtractionResult {
  const match = FIXTURES.find((item) => item.transcript === transcript);
  if (!match) throw new Error("CACHED_FIXTURE_MISMATCH");
  const base = new URL(`../../../fixtures/radio/${match.dir}/`, import.meta.url);
  const raw = JSON.parse(readFileSync(new URL("raw_input.json", base), "utf8")) as {
    transcript: string;
  };
  if (transcript !== raw.transcript) throw new Error("CACHED_FIXTURE_MISMATCH");
  const fixture = JSON.parse(
    readFileSync(new URL("extraction_result.json", base), "utf8"),
  ) as { observations: SemanticObservation[] };
  return {
    result: "observations",
    observations: fixture.observations,
    adapter: "cached",
    model: match.model,
    prompt_version: "radio-semantic-v1",
  };
}
