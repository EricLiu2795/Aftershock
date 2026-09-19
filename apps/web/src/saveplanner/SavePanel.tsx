import type { SaveState } from "./client";
import { decideProposal, unitColour } from "./client";
import "./saveplanner.css";

interface Props {
  save?: SaveState;
  status: "live" | "connecting" | "offline";
  onWorksite?(worksiteId: string): void;
}

const duration = (seconds: number | null) => {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  return `${minutes}:${String(total % 60).padStart(2, "0")}`;
};

/** Rescue dispatch: who is going where, and the human gate for objective changes. */
export function SavePanel(props: Props) {
  const { save, status } = props;
  const proposal =
    save?.proposal && save.proposal.status === "pending" ? save.proposal : null;
  const worksites = [...(save?.worksites ?? [])]
    .filter((worksite) => worksite.operational_status !== "resolved")
    .sort((a, b) => (b.save_priority ?? -1) - (a.save_priority ?? -1));

  return (
    <section className="save-panel dashboard-panel">
      <div className="save-panel-head">
        <h2>Rescue dispatch</h2>
        <span className={`save-status ${status}`}>{status}</span>
      </div>

      {status !== "live" && !save ? (
        <div className="save-hint">
          Save planner not running. Start it with{" "}
          <code>python -m saveplanner serve</code>.
        </div>
      ) : (
        <div className="save-hint">
          Click any road on the map to block or reopen it. Routes avoid blocked
          roads and are recomputed immediately.
        </div>
      )}

      {proposal && (
        <div className="save-gate">
          <div className="save-gate-head">Reassignment recommended</div>
          <div className="save-gate-body">
            <div>
              <strong>{proposal.resource_name}</strong>
            </div>
            <div className="save-gate-move">
              <span className="from">{proposal.current_label}</span>
              <span>→</span>
              <span className="to">{proposal.proposed_label}</span>
            </div>
            <table className="save-gate-math">
              <tbody>
                <tr>
                  <td>Proposed utility</td>
                  <td>+{proposal.breakdown.proposed_utility.toFixed(3)}</td>
                </tr>
                <tr>
                  <td>Current utility</td>
                  <td>−{proposal.breakdown.current_utility.toFixed(3)}</td>
                </tr>
                <tr>
                  <td>Switch cost</td>
                  <td>−{proposal.breakdown.switch_cost.toFixed(3)}</td>
                </tr>
                <tr className="total">
                  <td>Reassignment gain</td>
                  <td>+{proposal.reassignment_gain.toFixed(3)}</td>
                </tr>
                <tr>
                  <td>Threshold</td>
                  <td>+{proposal.threshold.toFixed(2)}</td>
                </tr>
              </tbody>
            </table>
            <div className="save-gate-actions">
              <button onClick={() => void decideProposal(false)}>
                Keep {proposal.current_label}
              </button>
              <button
                className="approve"
                onClick={() => void decideProposal(true)}
              >
                Approve
              </button>
            </div>
            <div className="save-gate-timer">
              Expires in {Math.ceil(proposal.seconds_left)}s · units keep moving
              while you decide
            </div>
          </div>
        </div>
      )}

      <div className="save-units">
        {(save?.resources ?? []).map((resource) => {
          const colour = unitColour(resource.resource_id, save!.resources);
          const worksite = save!.worksites.find(
            (item) => item.worksite_id === resource.current_worksite_id,
          );
          return (
            <div
              key={resource.resource_id}
              className="save-unit-row"
              style={{ ["--unit-color" as string]: colour }}
            >
              <span className="swatch" style={{ background: colour }} />
              <span className="who">
                {resource.display_name}{" "}
                <span className="what">
                  {worksite
                    ? `→ ${worksite.key}`
                    : resource.status.replace("_", " ")}
                </span>
              </span>
              <span className="eta">{duration(resource.eta_s)}</span>
              <div className="save-progress">
                <i style={{ width: `${resource.route_progress * 100}%` }} />
              </div>
            </div>
          );
        })}
      </div>

      <div className="save-worksites">
        {worksites.map((worksite) => (
          <div
            key={worksite.worksite_id}
            className={`save-ws${worksite.eligible ? "" : " ineligible"}`}
            onClick={() => props.onWorksite?.(worksite.worksite_id)}
          >
            <span className="sector">{worksite.key}</span>
            <span className="label">{worksite.display_name ?? worksite.site_id}</span>
            <span className="score">
              {worksite.save_priority === null
                ? "—"
                : worksite.save_priority.toFixed(2)}
            </span>
            {worksite.eligible && worksite.candidate_assignments.length === 0 && (
              <span className="flag">No passable route · needs clear-route</span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
