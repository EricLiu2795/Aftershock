# Aftershock radio

A simulated field-radio client and command feed. Radio audio is transcribed by ElevenLabs; the API durably stores completed text, extracts unverified facts, and resolves explicitly configured scenario aliases. This module does not dispatch responders or implement Incident Fusion, Search, Save, or maps.

## Run locally

Requires Node 22.9+ and npm. Configure `.env` from `.env.example` (keep existing credentials private).

```sh
npm ci
npm run dev
```

Open http://localhost:5173/command. Enter the configured `PRESENTER_API_KEY`, create a session, then open or scan its field-radio link. Local development defaults to `development-presenter-key` if no key is configured. The UI and API use one browser origin through Vite's proxy.

Live transcription needs `ELEVENLABS_API_KEY`. External extraction needs both `LLM_API_KEY` and `LLM_MODEL`; otherwise the UI identifies the deterministic adapter. Cached replay works without provider credentials. The presenter key is entered only for session creation; provider keys remain server-side.

Phones require a reachable HTTPS URL. A phone cannot use the laptop's localhost.

## Verify

```sh
npm run contracts:check
npm run typecheck
npm test
npm run build
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests
```

Generate shared TypeScript declarations after changing JSON schemas with `npm run contracts:generate`. Validation constraints remain enforced by the API; declarations do not replace runtime validation.

## Deployment

`Dockerfile`, `compose.yaml`, and `deploy/Caddyfile` provide a single-host deployment with TLS, a persistent SQLite volume, and unbuffered SSE. Set `DOMAIN`, production signing/presenter secrets, and provider configuration in the deployment environment. Point DNS to the host and allow ports 80/443, then run `docker compose up --build -d`. This deployment definition has not yet been exercised on a target host.

Read [implementation status](docs/implementation-status.md), the [demo runbook](docs/radio-demo-runbook.md), and the [rehearsal checklist](docs/radio-demo-rehearsal-checklist.md) before presenting.
