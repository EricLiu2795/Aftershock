# Radio demo runbook

## 1. Setup and configuration

Requires Node 22.9+ (SQLite is experimental in Node 22), npm, and a durable writable filesystem. Use one backend instance.

```sh
npm ci
npm run contracts:check
npm run typecheck
npm test
npm run build
# Create .env only if you do not already have one:
cp .env.example .env
```

Keep `ELEVENLABS_API_KEY`, `LLM_API_KEY`, `SESSION_SIGNING_SECRET`, and `PRESENTER_API_KEY` out of Git and client bundles. The presenter enters the presenter key into the local command login form; it is not saved by the application. Permanent provider keys never reach the browser. Set a restricted ElevenLabs key with speech/token access and an appropriate quota.

`LLM_API_KEY` plus `LLM_MODEL` selects external extraction through `${LLM_API_BASE_URL}/chat/completions`; without both, `deterministic-radio-v1` is used and shown in the report. Cached observations use `cached-riverside-observations-v1` regardless of external configuration.

For development, use `APP_ORIGIN=http://localhost:5173` and `COOKIE_SECURE=false`. Start from the repository root:

```sh
npm run dev:api
# Separate terminal:
npm run dev:web
```

The API command loads the root `.env`. Open http://localhost:5173/command. Vite forwards `/api` and `/health` to port 3001.

## 2. Presenter and phone flow

1. Enter the presenter key and allowed responder IDs on `/command`; create a fresh session.
2. Scan its QR or open the join link on `/radio`. The join code expires after 15 minutes by default. Create a new session for a fresh code.
3. Confirm the responder ID and join. Credentials are scoped HttpOnly cookies; session metadata is stored in the tab for refresh recovery.
4. Hold **Hold to transmit**, or use **Tap to start instead**. Grant microphone permission from the user gesture. Wait for **Speak now** before speaking.
5. Say: “Command, partial collapse near Riverside Apartments. Multiple voices heard. East access may be blocked.” Release or tap stop.
6. The mic stops, buffered audio drains, then the client waits for provider-committed text. New capture stays disabled while finalizing or processing.
7. Inspect the original transcript on command, followed by unverified observations and separate simulated location results. Multiple voices have unknown counts; east access remains possibly blocked.
8. Expand the payload to inspect source metadata. No dispatch, independent corroboration, or verified casualties are implied.

Capture ends automatically at 60 seconds. Early release cancels setup. Hiding the page, navigation, track loss, or network interruption cancels active capture. Available partial text is an unsent draft, never silently submitted. Use **Retry / recover transmission** after an uncertain acknowledgement; the client checks the existing ID and reuses the identical finalized body if needed.

## 3. Explicit fallback ladder

Never substitute a success fixture silently.

1. **Prerecorded human audio:** on field radio, expand the fallback control, select an approved audio file (maximum 20 MB / 60 seconds), preview it, then choose **Stream audio replay**. Decoded audio runs through the same PCM/provider path. The report is labeled `audio_replay`. No human recording is bundled yet.
2. **Cached transcript:** on command, choose **Replay cached transcript**. The Riverside transcript goes through normal configured extraction and resolution.
3. **Cached observation:** choose **Replay cached observation**. The checked-in semantic result bypasses the external model, is validated, receives fresh provenance, and goes through resolution and display.

Replay controls require presenter authorization. `REPLAY_ENABLED=false` disables cached replay. Only the named Riverside fixture is supported. Participant audio replay uses the same authorization and token limits as live capture.

## 4. Recovery and failure rehearsal

- **Microphone denied:** allow microphone access in browser site settings, then retry. Verify no mic indicator remains after failure.
- **Early release / screen lock:** no final report; any reserved attempt is cancelled. A late permission result is immediately stopped.
- **No speech / provider error:** show the error; do not promote partial captions into a final transcript. Select a fallback explicitly.
- **Lost reservation response:** the client retries the same idempotency key; one reservation remains.
- **Lost finalize response:** retry/recover queries the existing transmission and resubmits only the identical stored body if still reserved.
- **External model outage:** preserve the transcript; automatic attempts are bounded. A failed report exposes **Retry extraction**, reusing its existing input and job.
- **Unknown location:** visible unresolved result; no invented site/coordinates.
- **Dashboard disconnect:** fetch a snapshot, reconnect at its cursor, and replace the feed by transmission identity; polling backs up SSE.
- **Backend restart:** reuse the persistent database. Accepted jobs and expired leases recover; no duplicate raw input or observations.
- **Session expiry:** API access ends; worker cleanup removes expired session data. Existing SSE connections are closed. Provider tokens already issued cannot be revoked by session deletion.
- **Reset/delete:** deleting requires the session ID confirmation in the API and a confirmation dialog in the UI. Opening setup permits a fresh isolated session without deleting the old one.

## 5. API reference

All mutation routes require the configured browser origin in production. Presenter/participant cookies authorize scope; a session ID alone never grants access.

| Route | Purpose |
| --- | --- |
| `POST /api/demo-sessions` | Create session with `x-presenter-key`, name and allowed responder IDs |
| `POST /api/demo-sessions/join` | Exchange join code + responder ID for a participant cookie |
| `POST /api/radio/transmissions` | Reserve; optional `Idempotency-Key` safely recovers lost acknowledgement |
| `POST /api/radio/transmissions/:id/token` | Single-use speech token for owning participant |
| `POST /api/radio/transmissions/:id/finalize` | Durable 202 acknowledgement; identical retry idempotent; conflicting body 409 |
| `POST /api/radio/transmissions/:id/cancel` | Cancel a reserved attempt |
| `POST /api/radio/transmissions/:id/retry-extraction` | Presenter/owner retries a failed job without another input |
| `GET /api/radio/transmissions/:id` | Inspect/recover transmission status |
| `GET /api/demo-sessions/:id/snapshot` | Consistent feed + event cursor |
| `GET /api/demo-sessions/:id/events?cursor=…` | Authorized durable SSE events |
| `POST /api/demo-sessions/:id/replay` | Named presenter fallback; `fixture_name: riverside_report` and capture mode |
| `DELETE /api/demo-sessions/:id` | Delete with `confirm_session_id` matching the path |

Fixtures include example finalize requests. Defaults: 10,000 transcript characters, 64 KiB JSON body, six speech tokens per participant/minute and 30 per session/minute. Tokens are returned with `Cache-Control: no-store`.

## 6. HTTPS deployment

A phone's localhost is the phone. Use a real hostname reachable from both devices.

The repository supplies a Docker API image and Caddy web image. On a Docker host with DNS pointed to it, configure `.env` with `DOMAIN`, strong independent signing/presenter secrets, and provider configuration, then run:

```sh
docker compose up --build -d
```

Compose sets `APP_ORIGIN=https://${DOMAIN}`, secure cookies, and SQLite under `/data`. Keep the named volumes persistent. Caddy serves the built web app and proxies API/SSE with buffering disabled, obtaining TLS certificates through ports 80/443. Do not expose the API port separately. This deployment recipe still requires a target-host smoke test.

## 7. Readiness gate

Run the [rehearsal checklist](radio-demo-rehearsal-checklist.md) on the intended phone/browser and network. Ten successive live transmissions must preserve first/last words, produce one input each, stop the mic, and show exactly one result or explicit failure. Rehearse every fallback and capture connection, partial, release, commit, acceptance, extraction, and display times. The desired latency targets are measurements, not guarantees.

Provider protocol reference: [ElevenLabs realtime speech-to-text](https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime). The adapter uses manual commit and native-rate PCM with single-use tokens.
