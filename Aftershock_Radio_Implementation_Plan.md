# Aftershock radio implementation plan

Planning date: September 19, 2026. Implementation update: API and browser application now exist. See [current implementation status](docs/implementation-status.md) for completed work, verified checks, and remaining real-device/deployment gates. The repository findings below describe the original planning baseline.

## 1. Outcome and scope

Build a mobile-friendly radio simulator. A presenter opens `/radio` on a phone, joins the command dashboard's session, selects an authorized responder identity, and speaks. ElevenLabs transcribes the audio. The backend sends the completed transmission to an LLM, validates its extracted facts, and publishes the result to the laptop dashboard and the location-resolution pipeline.

The MVP ends at a reliable, validated handoff to LocationResolver. An integration demo may show resolver results using curated scenario data. Incident Fusion, Search, Save, maps, and resource simulation remain separate modules; do not claim they exist simply because a radio message was processed.

Success means a live phone transmission appears once on the laptop with its original transcript, source, processing status, extracted facts, and resolved or unresolved location. A failed external service must leave an understandable status and an explicit fallback.

This is speech transcription, not language translation. Start with English speech and English transcripts. Spanish transcription and translation into a separate display language are later extensions; preserve the original-language transcript if added.

## 2. Repository findings and constraints

The repository currently contains the architecture specification, JSON Schema Draft 2020-12 contracts, examples, and Python contract tests. It has no frontend application, backend service, or selected application runtime.

Existing contracts to reuse:

- `contracts/raw_radio_input.schema.json`: RawRadioInput.
- `contracts/observation.schema.json`: StructuredObservation.
- `contracts/location_resolution.schema.json`: resolver output.
- `contracts/common.schema.json`: enums, location, victim indication, provenance.
- `tests/test_contracts.py`: schema/example validation.

All shared payloads use `schema_version: "2.0"`. Top-level radio and observation schemas reject unknown properties, so new transport fields belong in explicitly defined envelopes, not arbitrary extra top-level fields.

Two fixture issues must be handled before using examples as extraction expectations:

1. The existing Site D3 example is in Los Angeles, while the specification describes a Caraballeda scenario. Create a separate, internally consistent radio-demo fixture set. Do not present the existing coordinates as Caraballeda geography or invent replacement coordinates.
2. The example transcript says “at least three voices,” but its observation gives `count_max: 3` and an internal D3 label. Extraction tests must preserve an unknown upper bound and must not infer a backend identity from that transcript. Voice indications alone also do not establish verified casualties.

The scenario title and historical claims are outside this radio plan's verification scope. Use clearly labeled simulated operational reports.

## 3. Proposed technical choices

These are implementation defaults, not existing project dependencies.

| Area | Choice | Reason |
| --- | --- | --- |
| Frontend | React + TypeScript + Vite | One responsive phone page and a small command feed |
| Backend | Node.js + TypeScript + Fastify | Token issuance, authenticated ingestion, worker, event stream |
| Speech service | ElevenLabs `scribe_v2_realtime` | Live captions and committed transcript segments |
| Browser STT client | `@elevenlabs/client`, pinned after the spike | Isolate provider APIs behind one adapter |
| Validation | Ajv with Draft 2020-12 and format support | Validate against existing schemas with local reference resolution |
| Storage | SQLite on a persistent volume, one backend instance | Durable transmissions, jobs, and events without another service |
| Dashboard updates | Server-Sent Events (SSE), snapshot/poll fallback | Backend-to-dashboard delivery with reconnect support |
| LLM | Configurable server-side extraction adapter | Provider/model not specified by the user |
| Hosting | One HTTPS origin and a long-running backend | Simplifies cookies, SSE, phone access, and persistence |

If the team already selects a different application stack, retain the contracts and lifecycle below and adapt the runtime. Multi-instance hosting requires shared storage/event delivery; the SQLite MVP deliberately runs one instance.

## 4. End-to-end architecture

```text
Phone /radio                         Laptop /command
  |                                      ^
  | request session + STT token          | SSE / snapshot
  v                                      |
Aftershock API --------------------------+
  | server-only ElevenLabs credential
  v
Single-use token -> phone

Phone microphone -> PCM audio -> ElevenLabs realtime
                                      |
Phone captions <--- partial / committed transcript
  |
  | completed transmission, stable transmission_id
  v
Aftershock API -> RawRadioInput + durable extraction job
                                      |
                                      v
                         LLM semantic extraction
                                      |
                         schema + semantic validation
                                      |
                         StructuredObservation(s)
                                      |
                         LocationResolver handoff
                                      |
                         Incident Fusion integration
```

Audio goes directly from the phone to ElevenLabs. Aftershock receives finalized text and supplies credentials through a restricted token endpoint. A later real-radio receiver can replace the phone capture adapter.

Browser-submitted text is a demo input, not cryptographic proof of provider output. Server-side audio transcription would be needed if trusted audio provenance becomes a requirement.

## 5. Verified ElevenLabs integration and first technical spike

ElevenLabs documents `scribe_v2_realtime`, partial/committed events, a browser client, and server-issued single-use tokens. Keep the permanent API key on the server. See the [JavaScript SDK reference](https://elevenlabs.io/docs/eleven-api/resources/libraries/scribe-stt/javascript-scribe).

The earlier suggestion to simply stop built-in microphone recording and commit needs qualification: ElevenLabs recommends VAD for the built-in microphone integration. Manual commit is appropriate when the application controls audio chunks. See [transcripts and commit strategies](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime/transcripts-and-commit-strategies).

For exact push-to-talk boundaries, choose manual audio capture and manual commit. Before building the rest of the UI, prove that release-to-finalization preserves the last word on the actual presentation phone. This is the highest-risk dependency.

Spike tasks:

1. Obtain microphone permission from a user gesture on an HTTPS page.
2. Capture through Web Audio and an AudioWorklet; inspect actual device sample rate.
3. Downmix to mono and resample to the configured PCM rate with a proper streaming resampler. Do not assume a requested AudioContext rate is honored.
4. Encode signed PCM16 little-endian; send ordered chunks through the SDK's manual audio interface. Start with 100 ms chunks and tune after measurement.
5. Connect with `scribe_v2_realtime`, matching audio format/sample rate, manual commit, and English language selection.
6. Wait for provider readiness before showing “Speak now.” Capture no pre-transmission speech.
7. On release, stop accepting new microphone frames, drain already captured frames in order, and request commit once.
8. Wait for committed text before closing the provider connection. Verify finalization/error semantics against the pinned SDK and API; a socket-close event is not a successful commit.
9. Confirm microphone tracks and audio resources are stopped on completion, cancellation, navigation, and error.
10. Repeat with short speech, pauses, immediate release after the last syllable, and ten consecutive transmissions on iPhone Safari and Android Chrome where available.

The realtime endpoint accepts audio chunks and emits partial and committed transcript messages; both manual and VAD strategies are documented in the [realtime API reference](https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime).

Exit gate: a working phone capture prototype with no clipped ending, no duplicate finalization, and no mic left active. If manual capture cannot pass on the presentation device, use an explicitly labeled start/stop VAD mode with a tested finalization interval, or recorded-audio transcription as the reduced-scope demo. Do not substitute uncommitted captions for a final transcript.

## 6. Phone experience and lifecycle

The command page creates a demo session and shows a join code/QR. The phone joins and receives a scoped participant cookie; the server binds its allowed responder identity. A selected name is a demo identity, not voice biometrics.

Phone layout:

- Session name and responder identity.
- Connection/microphone status in plain language.
- Large “Hold to transmit” control; accessible tap-to-start/tap-to-stop alternative.
- Recording timer, sound-level indicator, and live transcript.
- Last submission status and retry action.
- Clearly separated demo replay control, restricted to presenter permissions.

Client lifecycle:

```text
DISCONNECTED -> READY -> CONNECTING -> RECORDING
                                      |
                                   release
                                      v
                                  FINALIZING
                                      |
                                   SUBMITTING
                                      |
                            ACCEPTED / RETRYABLE_ERROR
```

`READY` means the page can begin setup; it does not imply an already-open provider socket. The UI shows “Connecting” until audio can be transmitted. Releasing before readiness cancels the attempt and cleans up a late connection.

Use pointer capture for hold gestures, keyboard support, and one shared start/stop controller. Handle pointer cancellation, page hiding, phone locking, and navigation. Interrupted capture is marked interrupted; do not silently send incomplete speech. Disable new transmission while finalizing the current one.

Set proposed application limits of 60 seconds per transmission and one concurrent transmission per participant. These are application choices, not ElevenLabs limits. Auto-stop at the limit and show why.

Partials replace the current caption; committed segments append in order. A committed segment is not necessarily a complete radio transmission. Aggregate all segments for one transmission and trigger extraction only after finalization. If both plain and timestamped committed events are enabled, select one as authoritative rather than concatenating both.

Never deduplicate segments solely by text: “help, help” can be legitimate repetition. Use callback ownership, connection generation, segment sequence, and one submission guard.

## 7. Sessions and API surface

| Endpoint | Responsibility |
| --- | --- |
| `POST /api/demo-sessions` | Authenticated presenter creates a scenario-scoped session |
| `POST /api/demo-sessions/join` | Exchange expiring join code for scoped participant cookie |
| `POST /api/radio/transmissions` | Reserve transmission ID and bind participant, session, capture mode |
| `POST /api/radio/transmissions/:id/token` | Authorize owner and issue single-use ElevenLabs token |
| `POST /api/radio/transmissions/:id/finalize` | Validate and durably accept complete transcript once |
| `POST /api/radio/transmissions/:id/cancel` | Mark unused/interrupted attempt cancelled |
| `GET /api/radio/transmissions/:id` | Recover acknowledgement and processing status |
| `GET /api/demo-sessions/:id/snapshot` | Current authorized feed plus event cursor |
| `GET /api/demo-sessions/:id/events` | SSE stream, resumable by event ID |
| `POST /api/demo-sessions/:id/replay` | Presenter selects a named fallback fixture |

Finalize request contract: `schema_version`, `transmission_id`, `transcript`, optional provider session reference, capture start/end times, and ordered segment metadata. Session and responder authority come from server state. Client timestamps are diagnostic, not authoritative receipt time.

Finalize response: HTTP 202 with stable `input_id`, `transmission_id`, and current processing state, after durable storage. An identical retry returns the same acknowledgement. A different transcript for an already-finalized ID returns HTTP 409. Invalid payloads return 400/422; unauthorized sessions return 401/403; rate limits return 429.

Token issuance calls ElevenLabs `POST /v1/single-use-token/realtime_scribe` server-side. Use no-store responses, bounded issuance rates, and a new token for a new provider connection. The [client streaming guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime/client-side-streaming) documents single-use tokens and their expiration; request them just before connecting.

Application defaults to begin testing: 10,000 transcript characters, 64 KiB JSON body, six token requests per participant per minute, and a session-wide budget. Tune from observed demo use. Never expose provider keys in client bundles, URLs, logs, or error responses.

## 8. Contracts and persistence

Add shared schemas for `RadioTransmission`, finalize request/response, extraction result, session snapshot, and dashboard event envelope. Keep cross-module definitions under `/contracts`; generate TypeScript types rather than maintaining divergent interfaces. Add examples and extend the existing example-to-schema test mapping.

Reuse RawRadioInput without breaking its shape:

```json
{
  "schema_version": "2.0",
  "input_id": "raw-radio-example-001",
  "timestamp": "2026-09-19T14:02:10Z",
  "audio_reference": null,
  "transcript": "Command, partial collapse near Riverside Apartments. Multiple voices heard. East access may be blocked.",
  "channel": "command-1",
  "speaker_id": "rescue-alpha",
  "metadata": {
    "capture_mode": "live_microphone",
    "transmission_id": "tx-example-001",
    "session_id": "demo-example",
    "stt_provider": "elevenlabs",
    "stt_model": "scribe_v2_realtime",
    "language": "en"
  }
}
```

The backend assigns IDs, receipt timestamp, identity, and trusted metadata. Leave `audio_reference` null unless an actual stored recording exists. The initial MVP does not retain live audio. Replay audio is a separate checked-in fixture.

Persist sessions, participants, transmissions, raw inputs, extraction jobs, observations, resolver outcomes, and durable dashboard events. Use unique constraints on `(session_id, transmission_id)` and observation IDs.

In one transaction, finalize the transmission, insert RawRadioInput, create the extraction job, and append the accepted event. A small database-backed worker claims jobs with a lease; expired leases recover after restart. Store observations, job completion, and outgoing events transactionally. Consumers deduplicate by event/observation ID. Target at-least-once delivery with idempotent effects, not an unsupported claim of distributed exactly-once execution.

Transport states are distinct from domain evidence and operational states. Examples: `accepted`, `extracting`, `extracted`, `resolving`, `resolved`, `unresolved`, `failed`. Use lowercase canonical evidence values such as `unverified`; uppercase is optional presentation styling.

## 9. LLM extraction behavior

Implement `extractRadioFacts(rawInput) -> extractionResult` behind a server-side adapter. Select a team-approved model with structured output support at implementation time; keep model name, timeout, and credentials configurable. The radio integration does not depend on a particular LLM vendor.

The model sees the transcript as quoted input data, not instructions. Give it only semantic output fields and enums. It has no tools to dispatch responders, mutate state, or look up arbitrary coordinates.

Extraction rules:

- Preserve uncertainty, negation, directions, and location wording.
- “Multiple voices” means possible multiple victims; it does not yield an exact count.
- “At least three” may support a lower bound of three; the upper bound stays null unless stated.
- “East access may be blocked” remains `possibly_blocked`.
- Preserve “partial collapse” in supporting text; conservatively map to `reported_damage` unless a documented scenario mapping supports a stronger enum. `partial_collapse_reported` is not a valid existing enum.
- No `site_id`, worksite ID, coordinates, priority, resource assignment, or verification upgrade may be invented.
- Unknown information remains unknown/null. “No victims seen” is not proof that nobody is trapped.
- Routine chatter can produce zero observations with an explicit `no_actionable_content` result.

One transmission can report several incident types or locations. Define an extraction-result envelope containing an array of semantic observations. The backend wraps each in a valid StructuredObservation. Separate distinct locations; attach only supporting facts to each observation. Multiple observations from one radio input are one source, never independent corroboration.

For the example report, expected facts include location text “Riverside Apartments,” possible multiple victims with unknown counts, reported structural damage, and possibly blocked east access. The backend adds IDs, `source_type: "radio"`, source reference, timestamp, and `verification_status: "unverified"`. Location coordinates and `resolved_site_id` remain null until deterministic resolution.

Validate both schema shape and semantics: count bounds, forbidden identity output, provenance, enum values, and unsupported certainty. Omit evidence-strength scoring until a documented deterministic policy exists; STT confidence and evidence reliability are separate concepts.

Use a bounded retry policy: one repair attempt for malformed semantic output; limited backoff for transient provider failures. Preserve the transcript on failure. Do not repeatedly create jobs from dashboard reconnects. Record prompt/model versions and failure codes for debugging.

## 10. Location and downstream integration

Provide a scenario-scoped resolver interface accepting validated observations. Start with exact normalized aliases and explicit known addresses. Seed “Riverside Apartments” only as a labeled simulated alias in the chosen scenario; do not use LLM-generated location IDs.

Ambiguous or unknown mentions return unresolved with candidate information where available. They remain in the feed and do not drive precise building assignment. An incident location is not automatically the phone's GPS position; GPS collection is unnecessary for this MVP.

Publish validated observations and separate resolution outputs to the downstream boundary. Fusion preserves provenance and deduplicates repeated deliveries. It decides incident creation/update and any operational state-version change. Live captions and token issuance do not increment operational `state_version`.

An extracted radio report alone must not bypass the evidence eligibility gate or active-objective approval rule. The radio module never assigns rescue units.

## 11. Laptop command feed

Show one card per transmission with responder, channel, time, capture mode, original transcript, processing status, extracted facts, and location resolution. Allow expansion to inspect raw and structured payloads. Group multiple extracted observations beneath the same transmission.

The phone shows partial captions immediately. Laptop partial mirroring is optional after the reliable path is complete; the first MVP streams accepted/final reports and processing changes only.

Event envelope: `schema_version`, `event_id`, `session_id`, `transmission_id`, `occurred_at`, event type, and a typed payload. Suggested types: `radio.accepted`, `radio.extraction_started`, `radio.extracted`, `radio.location_resolved`, `radio.location_unresolved`, and `radio.failed`.

Subscribe using an authenticated same-origin cookie. Replay durable events after the last acknowledged cursor, deduplicate event IDs, and request a new snapshot if the cursor is outside retention. Snapshot response and its cursor must be consistent so events are not lost between fetching and subscribing. Use heartbeats and reconnect backoff; fall back to bounded polling if streaming fails.

Render transcript text safely as text. Show “simulated live microphone,” “audio replay,” “cached transcript,” or “cached observation” accurately. Never silently replace an arbitrary failed live report with an unrelated success fixture.

## 12. Reliability and fallback behavior

| Failure | Expected handling |
| --- | --- |
| Microphone denied/unavailable | Explain permission recovery; offer laptop capture or labeled replay |
| Release before connection | Cancel, close any late socket, do not create a report |
| Silence/empty text | Show “No speech detected”; no extraction job |
| Provider token/auth/quota failure | Preserve clear failure code; do not retry credentials indefinitely |
| Connection lost mid-speech | Mark interrupted; allow a new attempt; never label partial text final |
| Final commit timeout | Retain available text as an unsent draft; offer retry or explicitly manual submission |
| Finalize POST acknowledgement lost | Retry same ID/payload or query status; avoid duplicate extraction |
| LLM unavailable | Keep accepted transcript; show failed/pending extraction and retry action |
| Invalid model output | Reject; bounded repair; never send invalid observations to resolver |
| Unknown location | Visible unresolved report, no invented marker |
| Dashboard disconnect | Recover via event cursor/snapshot |
| Backend restart | Recover jobs and feed from persistent storage |

Use configurable starting deadlines: connection 10 seconds, finalization 10 seconds, extraction 20 seconds per attempt. These are proposed UI/worker settings, not vendor guarantees. Cancellation races must be handled even after a deadline fires.

Fallback ladder, selected explicitly by presenter:

1. Prerecorded human audio through ElevenLabs: proves STT and extraction when live capture fails.
2. Cached transcript through live extraction: proves semantic processing when STT fails.
3. Cached validated observation: proves downstream display/integration when both services fail.

Each fixture has a fixed transcript, expected semantic assertions, scenario aliases, and capture-mode label. Reset creates a fresh session so old input IDs and evidence do not contaminate the next demonstration.

## 13. Deployment, access, and configuration

Serve the phone over HTTPS. A phone cannot access the laptop by opening its own `localhost`. Use a reachable deployment or an authorized HTTPS development tunnel for device testing. Configure reverse-proxy SSE buffering/timeouts and persistent database storage explicitly.

Server configuration: `ELEVENLABS_API_KEY`, `LLM_API_KEY`, `LLM_MODEL`, `APP_ORIGIN`, session-signing secret, database path, request limits, job timeouts, and replay mode. Commit an `.env.example` with placeholders only.

Use an authenticated presenter session, expiring join codes, scoped HttpOnly/Secure cookies, origin/CSRF checks for mutation endpoints, and server-enforced session ownership. A session ID alone must not authorize token issuance or access to another session's reports.

Do not log keys, provider token URLs, or raw audio. Restrict transcript logs; keep diagnostic IDs and timing/error metrics. Proposed demo retention is 24 hours with an explicit reset/delete action. Session expiry stops future token issuance and submissions; do not assume it revokes an already issued provider token.

## 14. Proposed file organization

```text
apps/web/src/pages/RadioPage.tsx
apps/web/src/pages/CommandPage.tsx
apps/web/src/radio/capture-controller.ts
apps/web/src/radio/pcm-worklet.ts
apps/web/src/radio/elevenlabs-adapter.ts
apps/web/src/radio/transmission-controller.ts
apps/web/src/feed/event-client.ts
apps/api/src/routes/sessions.ts
apps/api/src/routes/radio.ts
apps/api/src/routes/events.ts
apps/api/src/services/elevenlabs-token.ts
apps/api/src/services/radio-ingestion.ts
apps/api/src/services/extraction.ts
apps/api/src/services/location-handoff.ts
apps/api/src/workers/extraction-worker.ts
apps/api/src/storage/
contracts/                           existing schemas plus transport contracts
fixtures/radio/                      separately labeled scenario fixtures
tests/radio/                         lifecycle, ingestion, extraction, integration
docs/radio-demo-runbook.md
```

Use a root workspace manifest, lockfile, formatting/typecheck commands, and generated contract types. Keep provider-specific audio code out of the page and domain pipeline.

## 15. Implementation sequence and completion gates

| Phase | Tasks | Completion gate | Rough focused effort |
| --- | --- | --- | --- |
| 1. Contracts and fixture design | Define transport envelopes; fix semantic expectations; choose scenario aliases and LLM adapter configuration | Examples validate and scenario inputs are internally consistent | 3–5 hours |
| 2. Phone audio spike | Minimal HTTPS page, token route, PCM capture, manual commit | Real device preserves final words and cleans up mic | 4–8 hours |
| 3. Durable ingestion | Session auth, IDs, finalize endpoint, SQLite transactions, worker skeleton | Retries/restarts produce one stored input and job | 4–6 hours |
| 4. Extraction and handoff | Prompt, validation, semantic tests, resolver adapter | Live transcript produces valid observations or visible failure | 4–7 hours |
| 5. Presentation UI | Phone lifecycle, command cards, SSE recovery | Phone and laptop share one isolated session | 4–6 hours |
| 6. Rehearsal and fallback | Replay ladder, failure injection, deployed device tests, runbook | Ten successive transmissions and a full failure rehearsal pass | 4–6 hours |

Estimated total: 23–38 focused engineering hours, with mobile audio behavior and deployment as the main uncertainties. This estimate excludes implementing the full disaster simulator and map. First milestone is phone-to-transcript on the target device; do not spend time polishing the dashboard before that gate passes.

## 16. Verification plan

Run existing Python contract tests after schema/example changes. Add meaningful tests for new boundaries and lifecycle behavior:

- Finalization drains audio before commit and waits for completion before socket close.
- Cancellation during connection and timeout cleanup do not leave a microphone or late submission active.
- Pauses yielding multiple segments produce one transmission and one extraction job.
- Duplicate submit, lost acknowledgement, job lease expiry, and worker restart do not duplicate observations.
- Unauthorized joins/session access and rate limits reject requests correctly.
- Invalid enums, malformed JSON, invented identity, and impossible count ranges cannot reach the resolver.
- “May be trapped,” negation, multiple places, routine chatter, corrections, and “at least three” preserve meaning.
- Same-source multi-observation output is not counted as independent corroboration.
- SSE reconnect/snapshot races do not miss or duplicate cards.
- Replay modes are visible and do not masquerade as live results.

Use mocked provider events for deterministic tests and a small opt-in paid live smoke test. Evaluate LLM outputs by semantic assertions rather than exact JSON text or field ordering. If a model is nondeterministic, record repeat-run failure rates on the fixture set before demo day.

Real-device rehearsal includes Safari/Chrome, Wi-Fi and hotspot, permission denial, screen lock, quiet room and presentation noise, long pauses, immediate release, and unavailable external services.

Performance targets to measure, not guarantees: useful live captions while speaking; accepted final transcript within 3 seconds of release under normal conditions; extracted report within 8 seconds; laptop feed update within 1 second of durable backend event creation. Record capture, first partial, release, final commit, acceptance, extraction, and display timings separately.

## 17. Presenter runbook

1. Open the deployed command page and create a fresh demo session.
2. Scan the join QR on the phone, select the allowed responder, and allow microphone access.
3. Make one sound check and confirm live transcription and extraction availability.
4. Hold to transmit, wait for “Speak now,” and say: “Command, partial collapse near Riverside Apartments. Multiple voices heard. East access may be blocked.”
5. Release and show the original transcript arriving on the laptop.
6. Expand extracted facts; explain that uncertainty is preserved and the model did not invent a casualty count.
7. Show a separate deterministic location result, or show unresolved status honestly if no alias matches.
8. Hand off to the team's existing fusion/planning demo only when integrated.
9. If a service fails, use the labeled fallback and explain which part is being replayed.

## 18. Definition of done

The radio MVP is complete when the target phone can send ten consecutive transmissions into an isolated laptop session; each produces one durable raw input and validated extraction result or an explicit failure; source text and uncertainty remain inspectable; location resolution is separate; retry/restart paths are idempotent; no permanent credential reaches the browser; microphone capture ends correctly; and the presenter has exercised all three fallback levels.

Deliver application source, schema additions/examples, passing checks, a deployed HTTPS test URL, configuration instructions, a fixture pack, and the demo runbook. Implementation progress is tracked in docs/implementation-status.md; real-device verification and target-host deployment remain completion gates.
