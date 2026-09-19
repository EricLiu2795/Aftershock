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
