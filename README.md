# Aftershock

A disaster information dashboard with a real 3D satellite map, field radio, and the existing social/external ingestion pipeline. Radio audio is transcribed by ElevenLabs; the API stores reports and extracted observations in SQLite. Reports remain unverified. Rescue status, dispatch, resource assignment, and Incident Fusion are not implemented.

The dashboard follows `image.png`: incoming reports on the left, interactive geography in the center, report evidence on the right, and an information activity feed below the map. See [dashboard setup and behavior](docs/dashboard.md).

## Run locally

Requires Node 22.9+, npm, and Python 3.10+ for social ingestion (Python 3.12+ for the complete graph test suite). Configure `.env` from `.env.example` (keep existing credentials private).

```sh
npm ci
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm run dev
```

Open http://localhost:5173/command. Click **Connect radio**, enter the configured `PRESENTER_API_KEY`, and create a session. The optional labeled replay loads one Caraballeda radio report and seven deduplicated social posts. Open **Session** to scan the field-radio QR code. The original radio-only command screen is available at `/radio-command`. Local development defaults to `development-presenter-key` if no key is configured. The UI and API use one browser origin through Vite's proxy.

Set `VITE_MAPBOX_ACCESS_TOKEN` in the root `.env` for Mapbox satellite imagery and terrain. Without a token the map uses MapLibre/OpenFreeMap and Mapterhorn terrain. **Map layers → Use local geography** works with the bundled OSM geometry when external tiles are unavailable. The root `.env` is loaded by Vite; restart the dev server after changing tokens.

Live transcription needs `ELEVENLABS_API_KEY`. External extraction needs both `LLM_API_KEY` and `LLM_MODEL`; otherwise the UI identifies the deterministic adapter. Cached replay works without provider credentials. The presenter key is entered only for session creation; provider keys remain server-side.

Phones require a reachable HTTPS URL. A phone cannot use the laptop's localhost.

## Verify

```sh
npm run contracts:check
npm run typecheck
npm test
npm run build
.venv/bin/pip install -r requirements-graph.txt
.venv/bin/python -m unittest discover -s tests
```

Generate shared TypeScript declarations after changing JSON schemas with `npm run contracts:generate`. Validation constraints remain enforced by the API; declarations do not replace runtime validation.

## Deployment

`Dockerfile`, `compose.yaml`, and `deploy/Caddyfile` provide a single-host deployment with TLS, a persistent SQLite volume, and unbuffered SSE. Set `DOMAIN`, production signing/presenter secrets, and provider configuration in the deployment environment. Point DNS to the host and allow ports 80/443, then run `docker compose up --build -d`. This deployment definition has not yet been exercised on a target host.

Read [implementation status](docs/implementation-status.md), the [demo runbook](docs/radio-demo-runbook.md), and the [rehearsal checklist](docs/radio-demo-rehearsal-checklist.md) before presenting.
