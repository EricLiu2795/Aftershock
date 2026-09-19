# Radio demo rehearsal checklist

Session/date: ____________________  
Operator: ____________________  
API revision: ____________________  
Public HTTPS origin: ____________________  
Phone/browser: ____________________  
Network: ____________________

## Stop/go checks

- [ ] `npm run typecheck` passes.
- [ ] `npm test` passes.
- [ ] `.env` is ignored and readable only by its owner.
- [ ] `.env.example` contains placeholders only.
- [ ] Any previously exposed ElevenLabs key has been revoked.
- [ ] Production signing and presenter secrets are unique and at least 32 characters.
- [ ] `APP_ORIGIN` exactly matches the public HTTPS origin.
- [ ] `COOKIE_SECURE=true` for HTTPS.
- [ ] Persistent `DATABASE_PATH` is known, writable, and backed up as required.
- [ ] ElevenLabs key is restricted to required Scribe/token access and has a demo quota.
- [ ] Active extraction mode is declared: external LLM / deterministic.
- [ ] `/health` returns `{"ok":true}`.
- [ ] A fresh `simulated-riverside-apartments` session and unexpired join code exist.
- [ ] Replay is enabled only if presenter fallbacks are intended.
- [ ] Presenter can fetch snapshot and reconnect SSE from its cursor.

## Phone and dashboard checks

The web client is implemented. Check these on the intended phone over HTTPS; laptop localhost verification does not satisfy the device gate.

- [ ] `/radio` and `/command` load from the same trusted HTTPS origin.
- [ ] Allowed responder joins; disallowed responder is rejected.
- [ ] Microphone prompt follows a user gesture.
- [ ] Denial and recovery instructions work.
- [ ] “Speak now” appears only after provider readiness.
- [ ] Release waits for committed text; partial captions are never submitted as final.
- [ ] Page hide, lock, cancellation, and navigation stop microphone use.
- [ ] Dashboard labels `live_microphone`, `audio_replay`, `cached_transcript`, and `cached_observation` accurately.
- [ ] Original transcript, facts, uncertainty, and resolution are inspectable.
- [ ] Refresh/SSE reconnect creates no duplicate card.

## Ten-transmission gate

For each row record `P` (pass), `F` (explicit failure), or leave blank. A silent drop, duplicate, clipped last word, or lingering microphone is a gate failure.

| # | First/last word kept | One input/result | Correct label | No duplicate | Mic stopped | Terminal state | Notes |
|---|---|---|---|---|---|---|---|
| 1 |  |  |  |  |  |  |  |
| 2 |  |  |  |  |  |  |  |
| 3 |  |  |  |  |  |  |  |
| 4 |  |  |  |  |  |  |  |
| 5 |  |  |  |  |  |  |  |
| 6 |  |  |  |  |  |  |  |
| 7 |  |  |  |  |  |  |  |
| 8 |  |  |  |  |  |  |  |
| 9 |  |  |  |  |  |  |  |
| 10 |  |  |  |  |  |  |  |

- [ ] Immediate release after the last syllable passed.
- [ ] Long pause passed.
- [ ] Presentation noise passed.
- [ ] Wi-Fi path passed.
- [ ] Hotspot path passed.
- [ ] Screen lock/interruption produced an explicit failure, not a partial final.

## Failure and fallback rehearsal

- [ ] Microphone denial produced no report.
- [ ] Release-before-ready cancelled the reserved attempt.
- [ ] Missing STT key returned `503 STT_NOT_CONFIGURED`.
- [ ] Invalid/quota-limited STT key returned a safe error without exposing the key.
- [ ] Empty transcript returned `422 EMPTY_TRANSCRIPT`.
- [ ] Identical finalize retry returned the same `input_id`.
- [ ] Conflicting finalize retry returned `409 FINALIZE_CONFLICT`.
- [ ] LLM outage preserved the accepted transcript and ended visibly.
- [ ] Retryable extraction emitted `radio.extraction_retrying`, not `radio.failed`.
- [ ] Terminal extraction failure emitted exactly one `radio.failed`.
- [ ] Unknown place remained unresolved with no invented coordinates/site.
- [ ] SSE reconnect recovered from the snapshot cursor.
- [ ] Restart recovered durable data/jobs.
- [ ] `cached_transcript` fallback was announced and labeled.
- [ ] `cached_observation` fallback was announced and labeled.
- [ ] `audio_replay` was used only if an actual audio streaming client was present.

## Scope statement rehearsed

- [ ] Data is fictional simulated operational reporting.
- [ ] “Multiple voices” does not establish an exact or verified casualty count.
- [ ] Riverside resolution is a scenario-scoped exact alias with no coordinates.
- [ ] One radio source is not independent corroboration.
- [ ] Radio processing does not assign responders or change operational state.
- [ ] Incident Fusion, Search, Save, maps, and resource simulation are not claimed.

## After rehearsal/event

- [ ] Record session ID, transmission IDs, timing, and failures without recording secrets.
- [ ] Delete the completed session with the presenter-confirmed API, or document approved retention.
- [ ] Stop/expire the development tunnel.
- [ ] Remove local cookie jars.
- [ ] Verify worker cleanup removed expired session data; stop the service only after required cleanup.
- [ ] Revoke temporary credentials and rotate anything exposed.

Gate result: GO / NO-GO  
Approved by: ____________________  
Open follow-ups: ____________________________________________________________

