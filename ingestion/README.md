# Aftershock information ingestion

The ingestion layer stops at schema-valid StructuredObservation.

```text
External APIs / replay -> Adapters -> NormalizedRawInput
    -> AI Information Normalizer -> StructuredObservation
    -> JSON Schema Validation -> StructuredObservation[] handoff -> STOP
```

AI extracts meaning. LocationResolver establishes physical identity.
IncidentFusion determines what operational state changes. Those downstream
components, planners, assignment, and the frontend are outside this module.

## Run

Use Python 3.10+ from the repository root. The only direct third-party runtime
dependency is the existing `jsonschema` library (including its `referencing`
dependency). HTTP, logging, config, replay, and CLI use the standard library.

```sh
python -m pip install -r requirements-dev.txt
python -m ingestion.demo
```

The default is entirely offline: `SocialReplayAdapter` plus an explicitly mocked
`FixtureExtractionModel`. The eight fictional Caraballeda posts yield seven valid
observations: one repeated external ID is skipped, one irrelevant post yields
zero, and one post yields two. A near-duplicate with another external ID survives.
Observations are JSONL on stdout; diagnostic logs are on stderr.

**Offline replay** does not require OpenAI credentials. The default command
above uses local posts plus `FixtureExtractionModel`.

**Live OpenAI extraction** requires `OPENAI_API_KEY` and `INGESTION_AI_MODEL`
(a model that supports Responses API Structured Outputs). Copy `.env.example`
to a gitignored `.env` and paste local values:

```
OPENAI_API_KEY=<paste-key-locally>
INGESTION_AI_MODEL=<configured-model>
```

The demo CLI loads `.env` without overriding variables already set in the
process environment. Library callers (`Config.from_env()`) read the process
environment only. Do not commit `.env` or put a real key in source.

```sh
python -m ingestion.demo --source replay --provider openai
python -m ingestion.demo --source replay --provider openai --smoke
python -m ingestion.demo --source gdacs --provider openai
python -m ingestion.demo --source reliefweb --provider openai
```

`--source replay --provider openai` runs live extraction on the local replay
posts. `--smoke` uses a very small representative set (trapped-victim claim,
structural damage, blocked access, irrelevant text, Spanish, and a
multi-observation report). `--smoke --provider fixture` is an offline dry run
of that set. Live ReliefWeb also requires `RELIEFWEB_APPNAME`.

Live failures do not silently substitute mock results. Run
`python -m ingestion.demo --source replay --provider fixture` to explicitly
return to the offline demo. CLI exit codes: `0` success (including legitimately
empty output), `1` fetch/normalization failures, `2` invalid configuration,
missing live credentials, or replay file. Other inputs continue after one fails.

This layer normalizes reported information into schema-valid observations. It
does not prove that a report is true.

## Configuration

| Variable | Default / meaning |
| --- | --- |
| `INGESTION_MODE` | `replay`; `live` selects GDACS by default |
| `INGESTION_AI_PROVIDER` | `fixture`; `openai` enables live AI |
| `OPENAI_API_KEY` | Required only for live AI; placeholder in `.env.example` |
| `INGESTION_AI_MODEL` | Required only for live AI; no hard-coded model |
| `INGESTION_DEBUG` | Optional; `1`/`true` logs NormalizedRawInput and validated observations, never secrets |
| `INGESTION_AI_BASE_URL` | `https://api.openai.com/v1` |
| `INGESTION_HTTP_TIMEOUT` / `INGESTION_AI_TIMEOUT` | 15 / 45 seconds |
| `INGESTION_COUNTRY` | Optional country name |
| `INGESTION_SINCE` / `INGESTION_UNTIL` | Optional provider time bounds; defaults to last seven days |
| `GDACS_API_URL` | GDACS GeoJSON SEARCH endpoint |
| `GDACS_EVENT_TYPES` | `EQ`; GDACS semicolon-separated event types |
| `GDACS_EVENT_ID` | Optional event filter within the fetched page |
| `RELIEFWEB_API_URL` | `https://api.reliefweb.int/v2/reports` |
| `RELIEFWEB_APPNAME` | Pre-approved application name required for ReliefWeb |
| `RELIEFWEB_QUERY` | `earthquake` |
| `INGESTION_REPLAY_FILE` | `data/social_replay_caraballeda.json` |
| `INGESTION_FIXTURE_FILE` | `data/ingestion_ai_responses.json` |

CLI flags override source/provider selection. Use `YYYY-MM-DD` for GDACS dates
and timezone-bearing ISO timestamps for ReliefWeb date bounds. Source constructors
also accept page/offset and limit; the demo reads a single bounded live page.

## API boundaries

`GDACSAdapter` uses the [GDACS GeoJSON SEARCH API](https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v2.pdf)
with event type, date range, and pagination parameters. Country and individual
event ID filters apply locally to the returned page. Original geometry, country,
alert level, event/episode IDs, raw record, and report URL are preserved. Alert
levels never become Aftershock scores. Credit: Global Disaster Awareness and
Coordination System, GDACS. A magnitude-only alert may correctly yield zero
observations: earthquake occurrence alone is not an IncidentType in the contract.

`ReliefWebAdapter` POSTs a read-only query to the
[ReliefWeb v2 reports API](https://apidoc.reliefweb.int/parameters). It requests
title/body, publication date, organizations, URL, country, and language metadata.
Search, country, dates, and pagination are configurable. ReliefWeb requires a
pre-approved `appname`; obtain one using its documentation. HTML tags are removed
from report text. Reports are not assumed to identify a single building.

`SocialReplayAdapter.fetch()` advances a timestamp-ordered cursor by `batch_size`
(one by default). Replay records validate against the existing `RawSocialInput`
contract; `NormalizedRawInput` is an internal dataclass. Original payloads remain
on that object for debugging but are excluded from model requests and normal logs.

HTTP requires status 200, has timeouts and a 4 MB response cap. Empty/malformed responses, transport
errors, and rate limiting return no records and set `adapter.last_error`.
Malformed individual records are logged and skipped. `Retry-After` is preserved
on a `FetchError` for callers; this CLI does not sleep/retry/rate-limit-loop.

## Extraction and validation

`InformationExtractionModel` is an injectable interface. The OpenAI implementation
uses the [Responses API structured output format](https://developers.openai.com/api/docs/guides/structured-outputs)
with a schema **derived at runtime** from `contracts/observation.schema.json` and
its local `$refs`. There is no application-owned StructuredObservation schema or
dataclass. A transport envelope contains an array of canonical observations.

The canonical contract permits optional/open metadata that strict provider schemas
cannot directly express. The provider schema selects canonical fact fields and
requires the selected fields, with null counts and raw location where unknown.
Provenance is added by deterministic code, not authored by the model. Full
Draft 2020-12 validation runs locally on every final observation; `$refs` resolve
from a local registry without network access. Schemas are cached per process:
restart after a contract change.

The canonical version changed concurrently from 2.0 to 2.1 during implementation.
The replay fixture now uses 2.1; output version is read from `common.schema.json`.
This ingestion task did not edit `/contracts` or the contract test suite.

Policy enforced at this boundary:

- AI coordinates are always null. GDACS epicenters and report country geography
  are preserved as source metadata, never substituted for a rescue location.
- AI site/worksite IDs, commands, priorities, metadata, and credibility scores are
  rejected by the derived closed output schema. Source IDs, timestamps, version,
  and stable per-input observation IDs are checked against the supplied envelope.
- `raw_text` must be a literal excerpt from the source/title/raw location. Counts
  must at least appear explicitly as digits or English number words zero–twenty;
  unknown counts remain null. Ranges must be ordered. These lexical checks are
  conservative and do not prove semantic correctness (e.g. an address number is
  not necessarily a victim count). Other languages/spelled counts may require a
  null fallback until supported. No numeric facts are synthesized by code.
  Absence of victim information is `victim_indication.type = unknown`, not
  `none`. Use `none` only when the source explicitly states there are no victims.
- These external feeds start as `unverified`. Organization, platform, author,
  source URL, and available upstream metadata survive for downstream trust rules.
  This module does not implement credibility assessment or incident fusion.
- Invalid JSON/schema/policy output gets one repair attempt with validation
  feedback, then raises `NormalizationError`. No partial result from an invalid
  multi-observation batch escapes. Provider failures raise a sanitized error.

The prompt treats external text as untrusted data and instructs the model to
extract only supported meaning and preserve unknowns. Structural validation and
lexical checks do not guarantee factual extraction quality; evaluate live model
outputs against real reports before relying on them.

## Use as a module

```python
from ingestion.adapters import SocialReplayAdapter
from ingestion.normalizer import AIInformationNormalizer, IngestionPipeline
from ingestion.providers import FixtureExtractionModel
from ingestion.validation import validate_structured_observation

source = SocialReplayAdapter('data/social_replay_caraballeda.json')
model = FixtureExtractionModel('data/ingestion_ai_responses.json')
pipeline = IngestionPipeline(AIInformationNormalizer(model))
while inputs := source.fetch():
    for raw in inputs:
        for observation in pipeline.ingest(raw):
            validate_structured_observation(observation)
            # Validated downstream handoff here.
```

Swap the source for GDACS/ReliefWeb and the model for `OpenAIExtractionModel`;
the normalizer and handoff remain the same. `normalize(raw)` is also callable
directly; use `IngestionPipeline.ingest(raw)` when input deduplication is desired.

Deduplication uses `(source_type, source_id, external_id)`, with a hash of the
exact source envelope when an ID is absent. It is in memory for one pipeline
instance. Only successful normalization (including zero observations) consumes an
ID. Near-duplicates and cross-source reports remain distinct. Replay's cursor
advances on fetch, so callers must retain failed inputs or restart replay to
retry them. There is no durable queue, transactional handoff, semantic merge, or
cross-process exactly-once guarantee. Revisions under the same external ID are
currently skipped; GDACS episode IDs distinguish different episodes.

## Tests and remaining work

```sh
python -m unittest discover -s tests -p test_ingestion.py -v
python -m unittest discover -s tests -p test_contracts.py -v
python -m unittest discover -s tests -p 'test_*.py' -v
```

Tests mock HTTP and AI responses and never require network or credentials.
`python -m ingestion.demo --source replay --provider openai --smoke` is the live
OpenAI sanity check; CI does not run it. `data/ingestion_ai_responses.json` is
hand-authored mock extraction, not a live model recording or a keyword
classifier; unknown IDs/content fail explicitly.
`tests/fixtures/gdacs_response.json` and `reliefweb_response.json` are synthetic
provider-shaped data. `gdacs_live_2023_response.json` retains one public feature
from the successful live query below, in a minimal FeatureCollection wrapper.
`data/ingestion_ai_responses.json` is hand-authored mock extraction, not a live
model recording or a keyword classifier; unknown IDs/content fail explicitly.

Live verification during final review on 2026-09-19: GDACS SEARCH with `eventlist=EQ`,
`fromdate=2023-02-06`, `todate=2023-02-08`, `pagesize=1`, `pagenumber=1` returned
HTTP 200 and one feature (event 1357591, episode 1487359), which parsed successfully
into a NormalizedRawInput. The earlier recent-events request returned an empty
body; this does not guarantee every query will succeed.

Replay is fully offline and demo-ready. Live OpenAI extraction is production/demo
ready once `OPENAI_API_KEY` and `INGESTION_AI_MODEL` are set locally; unit tests
still mock the provider. GDACS is live-capable with the successful verification
above. ReliefWeb is live-capable but still requires an approved appname.
Other follow-up work is live extraction evaluation, caller-managed
polling/backoff and persistence if needed, and an optional Ushahidi adapter.
No Ushahidi API integration is included.
