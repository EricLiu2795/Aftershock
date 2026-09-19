# Drone reassignment and operator approval

Dynamic Search is implemented by `search_planner.reassignment`. Initial dispatch uses the existing deterministic greedy planner. During flight, fresh Worksite evidence is scored again at the drone's current position. This controller proposes changes; only the authenticated presenter can approve a change of objective.

- `gain = proposed DroneUtility − current DroneUtility − 0.05`.
- A proposal requires gain **strictly greater than 0.15**. Both utilities use the existing SearchPriority and normalized ETA formulas. The current utility uses remaining flight time; the new utility uses flight time from the current position. Both include verification duration.
- The fixed 0.05 switch penalty is a conservative MVP policy, configurable in the module constants. Extra distance is already represented in the ETA comparison; `progress_loss` and `additional_travel` are zero rather than double-counted. No LLM explanation is used.
- Eligible pairs are sorted by decreasing gain, then Worksite ID, then drone ID. Each drone and target can have only one pending proposal. Existing active drone targets are excluded.
- **Approval expires after 30 real seconds**, independent of simulation time. At exactly the deadline, approval is rejected. No response has the same operational effect as “Keep current task”; history records `expired`, without inventing a human response.
- Pending approval does not pause, cancel or change the original mission. Rejection, expiry and invalidation leave its route unchanged.
- Approval checks deadline, mission identity, evidence, target occupancy and current gain again. It creates a route from the updated physical position and preserves the original home position. The old assignment is archived as cancelled and its route as superseded. Repeated approval requests cannot dispatch twice.
- A 60-second cooldown follows approval, rejection, expiry or invalidation. Once that cooldown ends, persistent high gain may produce a new request.
- This MVP only interrupts **EN_ROUTE** flights. Arrival invalidates a pending request; VERIFYING and REPORTING finish normally. Missing JSON sensor reports still hold REPORTING (the Caraballeda provider now supplies authored W2/W7 reports automatically). Old mission reports are refused after reassignment.

## Dashboard and API

The existing dashboard displays Drone task approvals at the top of the details column. It polls every second, shows the server deadline, current/new task, exact score components, and explicit **Approve task switch** / **Keep current task** buttons. A disconnected or background browser cannot extend the server deadline. The timeout display is conservative; server time decides acceptance.

Configure `PYTHON_BIN` to a Python 3.11+ interpreter with `pip install -r requirements.txt`. The Fastify API calls the deterministic Python module without a shell. Records persist in the existing SQLite database in `drone_search_sessions`; optimistic revisions prevent concurrent approvals or evidence updates from overwriting each other. The API uses the existing presenter cookie, session checks and origin protection.

All calls are JSON POSTs under `/api/demo-sessions/{session_id}/search/`:

| Command | Body | Purpose |
|---|---|---|
| `initialize` | `{ "state": OperationalBeliefState }` | Once per session: plan and dispatch currently idle drones. |
| `update` | `{ "state": OperationalBeliefState }` | Coordinator publishes new fused Worksite evidence using the existing schema; controller keeps ownership of its drones. |
| `tick` | `{}` | Advance flights and expire/revalidate proposals; return the snapshot. |
| `decide` | `{ "proposal_id": "…", "decision": "approve" }` | Explicit approval; use `reject` to keep current task. Operator and time are supplied by the server. |
| `report` | `{ "report": { "mission_id": "…", "report": DroneObservationInput } }` | Submit infrared JSON to the current mission. |

Responses include `initialized`, `server_now`, `state`, `missions`, `proposals`, and `history`. Each proposal entry wraps the unchanged canonical ReassignmentProposal and separate numeric `score_breakdown`; approved entries also retain the rechecked `approved_comparison`. Use the returned `state.active_routes` and `movement_states` to render flight paths and positions. `missions[].events` contains report-ready observations for upstream fusion.

**Integration boundary:** Dashboard `/search/tick` polls remain unchanged. The backend owns Search synchronization from Agent 1’s canonical `OperationalBeliefState` snapshot:

- `getOperationalSnapshot(sessionId)` reads `operational_belief_snapshots` (Agent 1 writes; Search never persists belief).
- `syncSearchFromOperationalState(snapshot)` initializes once, updates only when `state_version` changes, and otherwise ticks without replanning.

Search consumes snapshot Worksites as-is, including canonical `site_id` / `worksite_id`. It does not reconstruct Worksites from raw reports and does not load `data/search_demo` fixture Worksites on the live path. Explicit `initialize` / `update` with a coordinator-supplied state remain available for tests. The approval UI still displays “Waiting for the Search Planner’s operational snapshot” until a snapshot exists. Drone route overlays remain the map owner's integration point.

Simulation advances by elapsed server time (1×), or forward to a later coordinator timestamp. Absolute timestamps make delayed polling catch up without changing the trajectory. Expiry is evaluated on every command, including the first command after a browser/server restart. No background job is necessary to preserve the original task: no approval means no route mutation. A backwards server clock refuses changes. Keep host clocks synchronized.

## Validation

`python -m unittest discover -s tests` exercises deterministic scoring and movement; `tests/test_drone_reassignment.py` covers exact timeout boundaries, late clicks, no response, rejection, duplicate decisions, evidence invalidation, arrival, cooldown, stale reports and current-position rerouting. API tests verify presenter isolation and bridge persistence.

## Automatic demo scan feedback and database output

The Caraballeda Scenario Pack v0 was imported unchanged from teammate commit
`285a7ac67e4c2062ec25eb3aec2959b874083f78` on `feature/caraballeda-scenario`.
That pack defines image **metadata**, but currently bundles no image files:
all four entries in `verification_assets.json` are `simulated_render` placeholders.
W2's E05 and W7's E12 reveal assets. W1/W5 placeholders remain hidden.

For API sessions named `caraballeda` or `caraballeda-v0`, the Search bridge now
automatically uses `DemoVerification` when advancing missions. No manual `report`
request is needed for W2 or W7. It reads authored `scenario_events.json` raw drone
inputs and media metadata, never `scenario_ground_truth.json`, interpretation hints,
or verification summaries. It does not analyze images or invent counts.

- The matching **canonical site ID** selects the result, irrespective of which
  drone the dynamic planner assigned. The report records the actual drone/mission;
  original event, input ID, timestamp and drone identity remain in metadata.
- A result becomes available at the later of the actual 30-second scan completion
  and its authored event timestamp (E05: T+105s; E12: T+268s).
- The existing 2-second reporting delay then publishes `DRONE_REPORT_READY`.
  Sparse polling produces the same capture/publication timing as frequent ticks.
- An existing manually submitted report takes precedence. A cancelled/replaced
  mission is never advanced to reveal its abandoned target. Unmatched sites still
  wait in REPORTING for external sensor JSON.
- W2 retains `possible_multiple` with null count bounds. W7 retains `none` with
  zero count bounds and explicit negative structural/access observations. These
  remain unverified evidence for the fusion layer, not an automatic truth update.

The same SQLite database used by the API (normally `data/aftershock.sqlite`) now
materializes published output in `drone_reports`, `drone_observations`, and
`drone_verification_assets`. Canonical payloads remain unchanged; separate tables
avoid inventing radio transmission/job IDs required by the existing radio-only
`observations` table. Report rows, observations, media metadata and the mission
revision commit together. Session-scoped keys prevent duplicate rows on retries.
Deletion of a session cascades to these tables.

Authenticated presenters can read:

`GET /api/demo-sessions/{session_id}/search/reports`

Each row includes mission/resource/site/worksite IDs, publication time, raw
`DroneObservationInput`, normalized observations and revealed assets. This is the
integration point for the information-fusion teammate and frontend; Worksite belief
is still updated by fusion, not overwritten with hidden scenario truth.

Assets return `available: false` and `image_url: null` while placeholder/missing.
When a real file or demo render is added at the pack's declared path and marked
`file_status: present`, `placeholder: false`, a subsequent scan stores that metadata
and returns an authenticated image URL. PNG/JPEG/WebP files are served only for
already-revealed assets in the same session, within `assets/drone/`. Files elsewhere,
unrevealed IDs and placeholders are not served. No new image UI or image generation
is included in this change.
