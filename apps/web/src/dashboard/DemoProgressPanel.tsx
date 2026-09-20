import { ListChecks } from "lucide-react";
import type { IntelligenceReport } from "../../../../contracts/intelligence";
import {
  buildingStateCounts,
  demoProgress,
  roadStateCounts,
  type BeliefRoadState,
  type BeliefWorksite,
  type SearchTickView,
} from "./demo-state";
import { green, red, yellow } from "./site-scan-status";
import "./demo-progress.css";

const LIMITED_ROAD = "#f6bc5a";
const BLOCKED_ROAD = "#ff5468";

const swatch = (color: string) => ({ ["--swatch" as string]: color });

/** Server-reported sequence of the integrated demo. Nothing here decides anything. */
export function DemoProgressPanel({
  session,
  reports,
  pendingReports,
  worksites,
  roads,
  stateVersion,
  tick,
  save,
}: {
  session: boolean;
  reports: IntelligenceReport[];
  pendingReports?: number;
  worksites?: BeliefWorksite[];
  roads?: BeliefRoadState[];
  stateVersion?: string;
  tick?: SearchTickView;
  save?: { resources?: { current_worksite_id: string | null }[] };
}) {
  const progress = demoProgress({ session, reports, pendingReports, worksites, roads, tick, save });
  const buildings = buildingStateCounts(reports);
  const counts = roadStateCounts(roads);
  return (
    <section className="dashboard-panel demo-progress-panel" aria-label="Demo progress">
      <div className="panel-heading">
        <div>
          <ListChecks size={16} />
          <h2>Demo progress</h2>
        </div>
        <span className="subtle-label">
          {progress.done}/{progress.total} OBSERVED
        </span>
      </div>
      <ol className="demo-steps">
        {progress.steps.map((step, index) => (
          <li
            key={step.id}
            className={step.state}
            aria-current={step.state === "active" ? "step" : undefined}
          >
            <i />
            <span>
              {index + 1}. {step.label}
            </span>
          </li>
        ))}
      </ol>
      <div className="demo-legend">
        <p>
          <span>BUILDINGS</span>
          <b style={swatch(yellow.color)}>{buildings.reported} reported</b>
          <b style={swatch(red.color)}>{buildings.people} people</b>
          <b style={swatch(green.color)}>{buildings.clear} clear</b>
        </p>
        <p>
          <span>ROADS</span>
          <b style={swatch(LIMITED_ROAD)}>{counts.limited} limited</b>
          <b style={swatch(BLOCKED_ROAD)}>{counts.blocked} blocked</b>
        </p>
        <small>
          Canonical belief state_version {stateVersion ?? "—"} · colours follow drone findings, not a
          safety clearance
        </small>
      </div>
    </section>
  );
}
