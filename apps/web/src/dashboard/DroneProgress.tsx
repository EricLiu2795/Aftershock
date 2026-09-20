import { Plane, CircleHelp } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { droneTaskProgress, etaLabel, presentMissionPosition, projectMission, projectedSimulationTime, visibleDrones, type PresentedFlight, type TrackingFrame } from "./drone-tracking";
export function DroneProgress({ frame, children }: { frame?: TrackingFrame; children?: ReactNode }) {
  const [clock, setClock] = useState(() => performance.now());
  const presented = useRef(new Map<string, PresentedFlight>());
  useEffect(() => { const timer = setInterval(() => setClock(performance.now()), 250); return () => clearInterval(timer); }, []);
  useEffect(() => { presented.current.clear(); }, [frame?.sessionId]);
  useEffect(() => {
    if (!frame?.snapshot.initialized) presented.current.clear();
  }, [frame?.snapshot.initialized]);
  return <section className="dashboard-panel drone-progress" aria-label="Individual drone progress" tabIndex={0}>
    <div className="panel-heading"><div><Plane size={17} /><h2>Drone progress</h2></div><CircleHelp size={15} className="subtle-icon" /></div>
    <div className="drone-progress-body">
    {(frame?.snapshot.initialized ? visibleDrones(frame) : []).map(m => {
      const v = projectMission(m, frame!, clock);
      const result = presentMissionPosition(m, v.position, clock, frame!.observedAt ?? clock, frame!.snapshot.simulation_speed ?? 1,
        frame!.pausedAt !== undefined, presented.current.get(m.resource.resource_id));
      presented.current.set(m.resource.resource_id, result.state);
      const progress = result.phase ? result.progress : droneTaskProgress(m, projectedSimulationTime(frame!, clock));
      const percent = Math.round(progress * 100);
      const phase = result.phase === "en_route" ? "En route" : result.phase === "returning" ? "Returning" : v.phase;
      const eta = result.eta ?? v.eta;
      return <article key={m.resource.resource_id}>
        <header><strong>{v.name}</strong><span>{phase}{v.stale ? " · Delayed" : ""}</span></header>
        <p>{m.phase === "ready" ? "Available for a search task" : m.phase === "idle" ? "Returned to starting location" : v.target}</p>
        <div className="drone-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label={`${v.name} ${phase} progress`}>
          <span style={{ width: `${percent}%` }} />
        </div>
        <small>{m.phase === "ready" ? "Ready to fly" : m.phase === "idle" ? "Mission complete" : eta === null ? "Waiting for sensor evidence" : `${phase === "Scanning" ? "Scan remaining" : "ETA"} ${etaLabel(eta)}`}</small>
      </article>;
    })}
    {children}
    </div>
  </section>;
}
