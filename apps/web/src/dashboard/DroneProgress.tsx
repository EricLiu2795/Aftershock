import { Plane, CircleHelp } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { etaLabel, projectMission, visibleDrones, type TrackingFrame } from "./drone-tracking";
export function DroneProgress({ frame, children }: { frame?: TrackingFrame; children?: ReactNode }) {
  const [clock, setClock] = useState(() => performance.now());
  useEffect(() => { const timer = setInterval(() => setClock(performance.now()), 250); return () => clearInterval(timer); }, []);
  return <section className="dashboard-panel drone-progress" aria-label="Individual drone progress">
    <div className="panel-heading"><div><Plane size={17} /><h2>Drone progress</h2></div><CircleHelp size={15} className="subtle-icon" /></div>
    <div className="drone-progress-body">
    {(frame?.snapshot.initialized ? visibleDrones(frame) : []).map(m => {
      const v = projectMission(m, frame!, clock);
      const route = m.phase === "returning" ? m.return_route : m.outbound_route;
      const duration = m.phase === "verifying" ? m.config.verification_duration_s : route?.estimated_travel_time_s ?? 0;
      const progress = m.phase === "idle" ? 100 : v.eta === null || !duration ? 0 : Math.max(0, Math.min(100, 100 * (1 - v.eta / duration)));
      return <article key={m.resource.resource_id}>
        <header><strong>{v.name}</strong><span>{v.phase}{v.stale ? " · Delayed" : ""}</span></header>
        <p>{m.phase === "ready" ? "Available for a search task" : m.phase === "idle" ? "Returned to starting location" : v.target}</p>
        <progress max="100" value={progress} aria-label={`${v.name} ${v.phase} progress`}/>
        <small>{m.phase === "ready" ? "Ready to fly" : m.phase === "idle" ? "Mission complete" : v.eta === null ? "Waiting for sensor evidence" : `${m.phase === "verifying" ? "Scan remaining" : "ETA"} ${etaLabel(v.eta)}`}</small>
      </article>;
    })}
    {children}
    </div>
  </section>;
}
