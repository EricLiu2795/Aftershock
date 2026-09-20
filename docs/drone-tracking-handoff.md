# Live drone tracking handoff

Branch: `feature/live-drone-tracking`. Built against main commit `2020286`.

## Dashboard flow

Create/open a Caraballeda command session. The existing Search polling prepares two idle drones at deterministic arbitrary locations inside the scenario area. Locations are seeded by session/resource ID, so reloads preserve them. Nothing flies until **Start scanning** is clicked. W2/W7 are explicitly simulated initial search cues on real canonical sites. They are not fabricated radio observations or hidden casualty truth.

The map fits the ready drones and sites once. It shows teal drone icons, labels, dashed outbound/return paths, scanning rings, phase-specific ETA, and a per-drone progress panel. Marker focus buttons remain usable when a drone is outside the current view. Existing satellite, terrain and local-geography modes remain available.

Movement comes from persisted MovementState and FLIGHT routes. The frontend projects direct great-circle flight between roughly one-second server updates, at a maximum 30 display frames/sec. Display projection is capped after 3.5 seconds without a new snapshot and labeled delayed. Reduced-motion mode uses authoritative positions and disables scan pulses. The frontend never chooses tasks, changes mission phases, emits reports, or recomputes priorities. Active-target changes still use the existing real-time 30-second approval gate, with continued movement while pending.

## Source queue and ownership

`drone-intake.ts` reads only already-located radio/social reports from the existing intelligence read model. A trapped-person claim adds an unverified site to a **demo scan queue**, without guessing coordinates or creating/fusing Incidents. Duplicate site IDs are ignored. Unresolved reports stay in the existing location workflow. Once scanning is enabled, idle drones get new eligible queue targets through the existing deterministic Search Planner; active missions still require human approval to change targets. Completed sites are not repeatedly dispatched.

The queue adapter is scoped to records with `tracking_demo: true`. A coordinator `update` switches that flag off; subsequent report polling cannot overwrite coordinator-owned Worksites. `initialize`/`update` continue to accept the existing OperationalBeliefState schema. If the dashboard already prepared idle state, use `update` rather than `initialize` to supply the coordinator snapshot. Before initialization, `initialize` remains supported.

The full teammate `operational/` fusion implementation exists on `origin/feature/operational-state-integration`, not on this main baseline. It was inspected but not merged or duplicated. This change does not modify canonical Resource, Movement, Route, Worksite, Observation, Mission or decision schemas.

## Evidence and building colors

Published drone reports join the existing `/intelligence` feed as `source: drone`. Canonical site placement is represented by an additive `canonical_site` location-method value. The existing Incoming information selection and Report details panel render source text, condition fields and original structured observations. A Drone source filter is added. There is **no standalone Description tab on this baseline**; that UI was not rebuilt. This uses the existing details interaction instead.

Yellow: unverified queue/source claim, unknown scan, or newer non-drone evidence.
Red: latest drone trapped-person indication is positive (including possible_multiple; this is not a confirmed count).
Green: latest drone finding explicitly says none. Unknown/missing results are never green.

Colors are display semantics, not an overwrite of fused Worksite truth or a safety-clearance claim. Building footprints use the existing geography feature ID. Point-only canonical facilities use their existing point marker; no nearby building is guessed. Old findings remain visible as evidence. No image pixels are analyzed and no new image/detail viewer is implemented.

## Contracts and extension points

- `contracts/drone_tracking.schema.json`: additive Search telemetry snapshot using existing canonical state and mission references. The API may include other approval/control fields.
- `contracts/intelligence.d.ts`: existing read model, with additive `canonical_site` placement support; drone source already fits its source string field.
- Existing `drone_observation_input.schema.json`, `observation.schema.json`, `movement.schema.json`, `route.schema.json`, and `drone_mission.schema.json` remain unchanged.
- `POST /api/demo-sessions/{id}/search/prepare`: idempotent idle demo preparation, presenter only.
- `POST /api/demo-sessions/{id}/search/dispatch`: enables initial/idle Search dispatch; does not interrupt an active mission.
- Existing `tick`, `update`, `decide`, `report` endpoints keep their roles. Use returned state, never frontend projected pixels, for backend decisions.
- Published reports remain queryable at `GET /api/demo-sessions/{id}/search/reports` and in the existing intelligence feed. Report persistence includes archived missions so completing and starting a task in the same tick cannot lose evidence.

Remaining integration: the coordinator should publish fused Worksites through `update` and consume canonical drone observations. Actual image files are still placeholders in the scenario pack; adding a Description tab or image viewer belongs to the frontend owner.

## Validation

Tests cover stable idle positions, delayed initial dispatch, flight interpolation, stale-update freeze, reduced motion, color transitions, schema compatibility, full scan-to-evidence publication, and queued social reports. Existing approval, ingestion and radio tests are retained. Browser visual inspection was blocked by an unavailable administrator-policy check; verify layout locally at `/command` before the demo.

Simulation speed: POST `search/speed` with `simulation_speed` set to 1, 5, or 10. The server advances at the previous rate before saving the new rate. Snapshots persist the rate; UI interpolation uses it. Approval deadlines and cooldowns remain real-time.

Completed scans are offered the next eligible target through the existing Search scoring before returning. The next route starts at the completed site (or current position if already returning), preserves the original home, and archives the prior report. Active scans still require the existing approval flow to interrupt. Drones return when no eligible unclaimed target remains. Missing reports still wait for sensor evidence.

## Traveling road detection (500 m)

`search_planner/road_scan.py` scans elapsed outbound and return flight segments against
real exported OSM edge polylines. It does not scan while stationary or read hidden truth.
The radius is horizontal map distance, using a local metric projection for Caraballeda.
A segment is detectable when any part lies within 500 m of the traveled path; this does
not claim that the entire road was imaged. Long ticks are clipped to actual flight times
and fixture activation times so accelerated playback cannot skip a crossing or reveal
a future incident. Detection timestamps are the end of the processed flight interval.

`data/scenarios/caraballeda/drone_road_conditions.json` is explicitly simulated sensor
input: open baseline, then R1/E10 on `osm:way:376529129:segment:1-12` at 14:03:25Z
(blocked overall/vehicle, limited on foot). The fixture supports open, limited and
blocked. It is not inferred from OSM or real infrared imagery. Unvisited roads are not
written as open. There is no road color UI in this change.

Each detection embeds canonical StructuredObservation and RoadAssessment objects with
unverified evidence and simulated provenance. One mission/edge/condition-version is
stored once; a changed condition or new mission can produce another assessment.
`state.road_states` and `state_version` update for downstream planners. Existing states
with newer timestamps are preserved.

The API commits detections and latest states with the mission revision in the same
session database (`data/aftershock.sqlite` by default): `drone_road_assessments` preserves
history and `drone_road_states` holds current values. The static country map database is
not modified. GET `/api/demo-sessions/:id/search/roads` returns `detection_radius_m`,
`road_states`, and `detections`, with the same presenter/session authorization as Search.
Detection payloads also appear in Search snapshots as `road_scans`; the additive DTO is
in `contracts/drone_tracking.schema.json`. Consumers can pass each `assessment` to
`GraphStore.road_assessment` when they need a labeled graph export. No ground routing
algorithm is implemented by this sensor adapter.

New command sessions start with no replay reports and no preset W2/W7 targets. Drones are ready but the queue is populated only from located incoming reports. Import social replay is explicit. Map highlights derive only from located reports; base facility/selection dots and the building legend entry are hidden.

Social replay highlighting: post-008 and post-009 are authored simulated reports for Residencias Caribe B (W2) and Colegio Tanaguarenas (W7). The API resolves exact explicit aliases from active_sites.json against canonical map IDs; duplicate names remain ambiguous. Import is still manual; these reports create targets only after import. Original unresolved replay reports are retained.

Report geometry display: polygon sites color their canonical footprint; point sites color
only a uniquely containing polygon (holes excluded). This is a display association,
not a change to canonical identity. W7's point is inside osm:way:303729130. Report details
show seven-decimal stored WGS84 coordinates, explicitly labeled centroid or OSM point;
unresolved reports have no invented coordinates. Road-related reports draw exact named
road matches red, or roads within 100 m of their resolved location. Named roads with
no finer location are shown along the matching road. Red is reported road context,
not a new confirmed RoadState. Report dots are omitted when a footprint is represented.

The default social replay now contains eight explicitly simulated building-only reports, one for each W1–W8 canonical scenario site. Each gives a specific scenario building name and uncertain victim indication, with no road claim. The API imports the batch transactionally, then the dashboard resolves and highlights those buildings. Mixed road/unresolved/irrelevant cases live in tests/fixtures/social_replay_mixed.json for ingestion regression tests. Existing sessions retain previously imported data; use a new session for the building-only replay.

### Database-backed building priority and inspection

After import, `scanIntake` supplies located observations already persisted in SQLite.
`sync_intake` groups canonical site IDs and deduplicates observation IDs. It preserves
victim/damage data instead of creating an all-unknown queue. Repeated reports do not
sum people counts: ranking uses the largest explicitly reported count bound, or an
explicit qualitative policy weight when counts are unknown. Counts are estimates of
reported people, not building occupancy or verified casualties.

`storedInspectionInputs` reads the existing `drone_reports` table by canonical site.
Current-session results take precedence. Only explicitly authored simulated results
(`provenance=simulated`, with a scenario event ID) may be reused across sessions of the
same scenario; live/private records never cross sessions. Latest matching stored data
contributes reported severity and counts. No new building catalogue or duplicate
inspection-source table is created. Existing report/asset persistence remains idempotent.

For tracking queue worksites, the deterministic formula is:
- people component: 0.55 × n/(n+10) when a count is known; otherwise 0.55 × qualitative weight
  (unknown .5, individual .3, multiple .6; these weights are not invented people counts).
- damage component: 0.35 × severity weight (none 0, minor .3, reported .4,
  major .7, severe/secondary hazard 1, unknown .5).
- verification allowance: .10, keeping all uninspected targets eligible.
- drone score: base priority × (1 − .30 × min(verification ETA / 600 seconds, 1)).
  ETA includes direct flight time and scan duration. Distances are great-circle metres.

Highest scoring drone/target pairs are greedily assigned once each, with canonical
IDs as stable tie-breakers. Scores refresh using current positions between missions.
The same formula is used for human-approved task-switch comparisons. The existing
30-second real-time approval gate remains. Non-tracking coordinator worksites retain
the original Search formula unless they carry `metadata.building_priority`.

`latest_search_plan.explanations` exposes `building_factors` plus every available
drone's distance, ETA penalty, utility and selection. The compact Target priority
expander under Drone progress is available before Start scanning. Additive fields use
the existing Worksite metadata and Search explanation contracts.

After scan completion, the provider replays a matching stored simulated inspection,
preserving its source report ID and asset metadata, or uses the existing authored
scenario event. Images are served by the authenticated session asset endpoint only
after report publication. Confirmed damage appears red with its stored image in Report
details. Explicit no-damage AND no-people findings show “Safe — reported damage not
verified” (a simulation label, not structural clearance). Unknown findings never mean
Safe. Tracking-only scans with no result publish an explicit unavailable outcome with
unknown values and no image, leave the building yellow, and continue to the next target;
they do not fabricate an inspection. Non-tracking missions still wait for actual reports.

The checked local database initially covered only W2 and W7; six of the eight replay
buildings had no inspection, and positive numeric people counts were unavailable.
More results can use the existing drone report intake/schema; do not manufacture them.

### Scenario-driven interactive demo (current behavior)

The user explicitly requested use of the scenario building/person data. For **tracking
sessions only**, imported social/radio buildings now join `active_sites.json` and
`scenario_ground_truth.json` by canonical `site_id` (and matching worksite key).
Only imported targets are added; a new empty session still has no targets or highlights.
`metadata.building_priority.people_count` uses the scenario's exact simulated trapped
count, not the upper bound of its range. Damage comes from the same matched entry.
The source and scenario ID are recorded. Normal coordinator planning does not read
these simulator facts. The scenario files themselves are unchanged.

This supersedes the prior tracking-only unavailable-outcome behavior for the eight
scenario sites. After flight and scan duration, a scenario-backed structured drone
result is persisted through the existing report/observation/asset tables. It contains
the matching victim count and damage, explicitly marked simulated. Only existing
same-site `assets/drone/` images are attached; context photos are not drone imagery.
W1/W2/W5 have positive counts (6/3/2); the other five have zero trapped people. These
are simulated victim counts, not total population or real casualties.

Map colors now mean **people found** (red), **no people found** (green), or **not yet
verified** (yellow). Damage remains visible separately. A green building can still
have severe structural damage; it is not a structural safety clearance. Scan findings
take precedence over later duplicate source rumors for display. The highlighted
footprint layer stays on after a scan and is repainted red/green (it no longer drops
back to the base color), and the dashboard refreshes the feed as soon as
`inspected_site_ids` changes. Roads never inherit a building's color: only roads
explicitly named in a non-drone report are drawn; drone building scans and proximity to
a reported building do not color streets.

### Building database and photos

`apps/api/src/scenario-buildings.ts` owns the `scenario_buildings` SQLite table
(session database, `data/aftershock.sqlite` by default). One row per canonical
scenario building: `site_id`, `worksite_key`, `display_name`, centroid, `people_count`,
`people_trapped`, `structural_state`, `access_state`, `drone_verifiable`, and the
building's own drone photo (`photo_asset_id`, `photo_path`, `photo_caption`). Rows are
seeded idempotently from the pack on startup and marked `simulated`. The API passes these
rows to the planner as `scenario_buildings`; `buildings_from_rows` converts them and
`sync_intake` / `DemoVerification` use them instead of reading scenario JSON (JSON is only
the fallback when no rows are supplied). Counts are simulated scenario inputs, not
verified casualties.

- `GET /api/demo-sessions/:id/search/buildings` lists rows with `photo.revealed`.
- `GET /api/demo-sessions/:id/search/buildings/:siteId/photo` serves the building photo
  only after a drone report in **this session** found people there (red). Green or
  unknown scans return 404. Intelligence `inspection.images` follows the same rule and
  `inspection.people_count` carries the reported count.

Only two scenario buildings are safe (0 people): W6 Residencias Las Perlas and W7 Colegio
Tanaguarenas. The other six have simulated trapped-people counts (W1 6, W2 3, W3 2, W4 4,
W5 2, W8 1) and each has its own AI-generated simulated drone render under `assets/drone/`.
The map shows a dismissable photo card anchored to a red building when its scan report
arrives; clicking it opens the report. Numbered priority badges are no longer drawn on the
map (`target_priorities` still exists in the snapshot for the panel).

### Staggered social replay

`apps/api/src/replay-release.ts`. Import stores all replay reports at once but marks each
with `imported_at` and `release_offset_s` (new nullable columns on `external_reports`).
The earliest-stamped report is released immediately (one yellow target), then two more per
`REPLAY_RELEASE_INTERVAL_S` (default 120 s; 0 disables staggering, which the tests use).
Elapsed time is multiplied by the session's drone `simulation_speed`, so at 10× the feed
arrives ten times faster. Unreleased reports are hidden from `/intelligence`, and therefore
from drone intake, until their time comes. Clicking **Import social replay** again while
reports are pending releases the next batch early; the snapshot exposes `pendingReports`
and the button label shows how many are still incoming. GDACS/ReliefWeb imports are not
staggered.

`target_priorities` is an additive array on the tracking snapshot. Each row carries
canonical IDs, display name, position, score, people count, assigned flag, and unique
one-based `rank`. Before dispatch it derives from the same available-drone candidates
used by the planner. Active targets retain their dispatch score while remaining
candidates are rescored when drones become available. Ranks are listed in the drone panel;
they are intentionally not drawn on top of buildings.

`inspected_site_ids` persists canonical coverage in the existing session record,
rebuilt from current and archived scan-completion events. Dispatch, proposal creation,
and proposal approval exclude these IDs. Reimporting reports or refreshing does not
reset coverage. A new session starts fresh. Cancelled flights that never completed a
scan remain eligible. The normal 30-second approval gate for interrupting a flight is
unchanged. Use a new session to test the new scenario outcomes rather than revisiting
already inspected buildings from an older session.
