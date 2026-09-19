# Caraballeda command dashboard

`/command` combines the radio branch with main's Python ingestion and real OSM export. The map uses Mapbox GL JS when `VITE_MAPBOX_ACCESS_TOKEN` is set in the root `.env`. The token must be a public browser token, with URL restrictions appropriate to the app. It is included in the browser build. Keep provider secrets (`ELEVENLABS_API_KEY`, `LLM_API_KEY`, `OPENAI_API_KEY`) server-only.

Without a Mapbox token, MapLibre loads OpenFreeMap's dark basemap and Mapterhorn terrain. Internet access is needed for either online basemap. A local geometry mode loads only `/api/geography/features` and retains roads, buildings, report markers and selection; it has no satellite imagery or terrain elevation. The map can be tilted, rotated, zoomed, reset north, switched between 2D and 3D, and inspected by clicking buildings. OSM attribution is retained.

## Starting a session

1. Run `npm ci`, create `.venv` with Python, install `requirements-dev.txt`, then `npm run dev`.
2. Open `/command`, choose **Connect radio**, and supply your configured presenter key. With no environment override the development key is `development-presenter-key`.
3. Optionally keep **Start with labeled radio and social replay reports** checked. This uses fixture extraction and makes no paid AI requests.
4. Open **Session** to see the radio join code and QR link. The original `/radio` capture flow is preserved. Microphone transcription still needs ElevenLabs credentials and phones need a reachable HTTPS origin.
5. Filter or search incoming reports. Selecting a report exposes its original text, extracted observations, provenance, and location. Sources & replay can import more external information or repeat the radio fixture.

The unified feed refreshes every three seconds and shows connection failure explicitly. Reports are stored per session. The existing radio SSE endpoint and radio-only `/radio-command` screen remain available. Newly emitted radio contracts and checked-in fixtures use v2.1, matching main.

## Locations and uncertainty

The map displays 8,443 OSM sites and 1,123 road segments from `data/graph_exports/caraballeda.geojson`. Buildings use an OSM height when present, otherwise levels × 3 m, otherwise a 6 m display default. These estimates are identified in the legend and building inspector. OSM tags do not automatically become earthquake damage claims.

The new Caraballeda radio fixture names **Residencias Las Anclas**, which has a unique exact OSM name match. Exact matching normalizes case, punctuation and accents; it does not perform fuzzy geocoding. The existing fictional social posts retain their original names. Vague or unmatched locations remain in **Needs location** and do not acquire invented map coordinates.

A presenter can select an actionable report, search for a named site or click a building, then explicitly choose **Place selected report here**. Manual placements are stored separately from canonical source observations, preserve the original text/location, and do not verify a damage claim. A later placement replaces the earlier manual site. This is a display association, not incident fusion or a building damage assessment.

The map marker colors distinguish victim/secondary-hazard claims from damage/access claims. They are not priority scores. There are no rescue-status tables, responder positions, drone tracking, assignment plans, estimated arrival times, or dispatch controls.

## External ingestion

The Node API invokes `python -m ingestion.dashboard` through `execFile` with fixed enum arguments, a three-minute timeout, a bounded output buffer, and no shell. It detects `.venv/bin/python`, or uses `PYTHON_BIN`, or falls back to `python3`. The bridge uses the existing adapters, `IngestionPipeline`, normalizer, and canonical schema validator.

The default **Social replay / Offline fixture extraction** needs no external credentials. Eight source rows become seven report cards and seven observations: one duplicate external ID is omitted, one irrelevant post remains visible with **Show filtered**, and one post has two observations. Reimport skips durable report IDs before invoking extraction. Near-duplicate posts remain separate.

Optional **OpenAI extraction** requires server-side `OPENAI_API_KEY` and `INGESTION_AI_MODEL`. The radio `LLM_*` settings are separate. Live GDACS/ReliefWeb imports require OpenAI extraction; ReliefWeb additionally requires `RELIEFWEB_APPNAME`. Configure `INGESTION_COUNTRY`, `INGESTION_SINCE`, `INGESTION_UNTIL`, and optionally `GDACS_EVENT_ID`. The dashboard bridge defaults its live country filter to Venezuela. Each import reads one bounded page (up to 10 provider records). Failed imports report an error and never silently become fixture data. Live source access/extraction was not exercised as part of dashboard verification.

Social replay is not a connection to X, Instagram, or another social account. The merged module contains fictional posts, GDACS alerts, and ReliefWeb reports; another live social platform would need its own adapter and credentials.

## API and deployment

- `GET /api/geography` — site gazetteer and coverage counts.
- `GET /api/geography/features` — compact OSM rendering GeoJSON.
- `GET /api/demo-sessions/:id/intelligence` — authenticated unified read model.
- `POST /api/demo-sessions/:id/ingest` — presenter-only source/provider import.
- `POST /api/demo-sessions/:id/locate` — presenter-only report/site association.

Session access is checked on every read/write. Import and placement do not dispatch anyone. Session deletion/expiry removes the session's external reports and placements along with its radio data.

Docker includes Python, ingestion fixtures and the geography export. Compose forwards external provider settings and the browser Mapbox build argument. The browser token is a build-time setting, so rebuild the web image when it changes. The Docker deployment changes have not been run on a target host.

Verification: radio regression tests; integration tests for unified ingestion, deduplication, filtered reports, exact-name matching, manual placement, authentication and session isolation; Python contracts/ingestion/graph tests; TypeScript typecheck; generated-contract check; production build; browser checks of Mapbox satellite, local geometry, report details, manual placement, and desktop/mobile layouts.
