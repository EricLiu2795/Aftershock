# Implementation status — September 19, 2026

Source plan: [Aftershock radio implementation plan](../Aftershock_Radio_Implementation_Plan.md). The original “no application code” inventory is historical. The radio module now has an API and a React/Vite application.

| Area | Current implementation | Remaining gate |
| --- | --- | --- |
| Contracts and fixtures | Transport schemas, simulated scenario, generated shared TypeScript declarations, Python validation | None for the current API contracts |
| Durable ingestion | SQLite transactions, scoped session cookies, idempotent reservations/finalize, job leases, retention cleanup | Production persistent-volume restart rehearsal |
| Extraction and resolution | Configurable LLM adapter, validation, deterministic fallback, exact simulated aliases, user-requested retry | Opt-in live model evaluation against fixture assertions |
| Phone client | `/radio`, QR/code join, hold or tap capture, PCM AudioWorklet, manual commit, captions, meter/timer, 60-second stop, cancellation, draft/retry recovery | Target-phone first/last-word and ten-transmission test |
| Command client | `/command`, presenter session creation, join QR, report cards, raw payload inspection, SSE/snapshot/poll recovery, reset/delete controls | Two-device HTTPS rehearsal |
| Fallbacks | User-selected human audio file streamed through STT, cached transcript, validated cached semantic fixture with fresh provenance | Supply and check in an approved human recording; rehearse all rungs |
| Deployment | Docker multi-stage build, Caddy TLS/proxy, Compose durable volumes | Host/domain selection and deployment; Docker daemon unavailable during implementation checks |

## Verified locally

- API and web TypeScript checks and production builds.
- 22 passing JavaScript tests (13 API, nine browser lifecycle/protocol/PCM tests) and 15 passing Python contract tests.
- Native Chrome: command session creation, QR/link rendering, cached observation replay reaching resolved state, explicit uncertainty/location display, and participant joining.
- Browser replay/join verification used an isolated temporary database and dummy provider configuration; no paid-provider success is claimed. The local API was then restarted with the existing project `.env` for user testing.

## Deliberate implementation decisions

- Direct documented ElevenLabs WebSocket protocol is isolated behind an adapter instead of introducing a provider SDK dependency.
- PCM is sent at the actual AudioContext sample rate when supported by ElevenLabs (8/16/22.05/24/44.1/48 kHz). Unsupported rates fail explicitly. No untested resampler is used.
- The AudioWorklet drains buffered PCM before the commit frame; a socket close or partial caption is never considered successful finalization.
- Native EventSource reconnect is replaced with a fresh authorized snapshot and cursor; snapshots replace cards by transmission identity. Bounded polling also runs as recovery.
- Session storage retains session metadata and one pending finalized body for refresh/acknowledgement recovery. It contains no provider or presenter credentials. Uncommitted captions remain an unsent in-memory draft after interruption.
- Cached observation replay now reads the checked-in semantic fixture, validates it while wrapping observations, and assigns fresh source IDs. It is labeled `cached-riverside-observations-v1`.

## Still unverified

Do not mark the complete radio MVP ready until the [real-device checklist](radio-demo-rehearsal-checklist.md) passes. No deployed HTTPS URL, live microphone result, human recording fixture, paid-provider smoke result, or ten-transmission result is claimed. External downstream fusion/planning modules are outside this plan.
