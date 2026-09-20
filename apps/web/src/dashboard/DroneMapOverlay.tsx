import { useEffect, useRef, useState } from "react";
import type { Map as LibreMap } from "maplibre-gl";
import { etaLabel, interpolateFlight, projectMission, visibleDrones, type Point, type TrackingFrame } from "./drone-tracking";
import "./drone-tracking.css";

function DroneIcon() {
  return <svg viewBox="0 0 40 40" width="36" height="36" aria-hidden="true">
    <g fill="none" stroke="currentColor" strokeWidth="2.5">
      <path d="M12 12L28 28M28 12L12 28"/><rect x="15" y="15" width="10" height="10" rx="3" fill="#073431"/>
      <circle cx="10" cy="10" r="6"/><circle cx="30" cy="10" r="6"/><circle cx="10" cy="30" r="6"/><circle cx="30" cy="30" r="6"/>
    </g>
  </svg>;
}
export function DroneMapOverlay({ map, frame }: { map: LibreMap; frame?: TrackingFrame }) {
  const fitted = useRef<string | undefined>(undefined);
  // The server supplies a new authoritative coordinate roughly once per second.  Keep
  // the last rendered coordinate as the next transition's start, rather than placing
  // the icon at the new coordinate immediately.
  const motion = useRef(new Map<string, { frameAt: number; source: Point; target: Point }>());
  useEffect(() => {
    if (!frame?.snapshot.initialized || fitted.current === (frame.sessionId ?? "tracking")) return;
    const points = [...visibleDrones(frame).map(m => m.movement.position), ...(frame.snapshot.state?.sites ?? []).map(s => s.centroid)];
    if (!points.length) return;
    fitted.current = frame.sessionId ?? "tracking";
    map.fitBounds([[Math.min(...points.map(p => p.longitude)), Math.min(...points.map(p => p.latitude))], [Math.max(...points.map(p => p.longitude)), Math.max(...points.map(p => p.latitude))]], { padding: 90, maxZoom: 16, duration: 0, pitch: map.getPitch(), bearing: map.getBearing() });
  }, [frame, map]);
  const [clock, setClock] = useState(() => performance.now());
  const [selected, setSelected] = useState<string>();
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const change = () => setReduced(query.matches);
    change(); query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  useEffect(() => {
    let animation = 0, previous = 0;
    const render = () => setClock(performance.now());
    function tick(now: number) {
      if (!document.hidden && now - previous >= 33) { previous = now; setClock(now); }
      animation = requestAnimationFrame(tick);
    }
    // One clock drives marker interpolation; no network/planning in this loop.
    if (!reduced) animation = requestAnimationFrame(tick);
    const timer = reduced ? setInterval(render, 1000) : undefined;
    map.on("move", render); map.on("resize", render);
    return () => { cancelAnimationFrame(animation); clearInterval(timer); map.off("move", render); map.off("resize", render); };
  }, [map, reduced]);
  if (!frame?.snapshot.initialized || !visibleDrones(frame).length) return null;
  const drones = visibleDrones(frame).map(m => {
    const view = projectMission(m, frame, clock, reduced);
    const id = m.resource.resource_id;
    const previous = motion.current.get(id);
    if (!previous || previous.frameAt !== frame.receivedAt) {
      const elapsed = previous ? Math.min(1, Math.max(0, (clock - previous.frameAt) / 900)) : 1;
      motion.current.set(id, {
        frameAt: frame.receivedAt,
        source: previous ? interpolateFlight(previous.source, previous.target, elapsed) : view.position,
        target: view.position,
      });
    }
    const active = motion.current.get(id)!;
    const progress = reduced ? 1 : Math.min(1, Math.max(0, (clock - active.frameAt) / 900));
    return { m, view: { ...view, position: interpolateFlight(active.source, active.target, progress) } };
  });
  const focus = (id: string) => {
    setSelected(id);
    const drone = drones.find(d => d.m.resource.resource_id === id);
    if (drone) map.easeTo({ center: [drone.view.position.longitude, drone.view.position.latitude], duration: reduced ? 0 : 500 });
  };
  return <div className="drone-map-overlay" aria-label="Live drone tracking">
    <svg className="drone-flight-lines" aria-hidden="true">
      {drones.map(({ m, view }) => view.route && <polyline key={m.resource.resource_id}
        className={m.phase === "returning" ? "return-flight" : "outbound-flight"}
        points={view.route.path.map(p => { const pixel = map.project([p.longitude, p.latitude]); return `${pixel.x},${pixel.y}`; }).join(" ")} />)}
    </svg>
    {drones.map(({ m, view }, i) => {
      const pixel = map.project([view.position.longitude, view.position.latitude]);
      const scanning = m.phase === "verifying";
      return <button key={m.resource.resource_id} type="button" className={`live-drone ${scanning ? "scanning" : ""} ${view.stale ? "stale" : ""} ${selected === m.resource.resource_id ? "selected" : ""}`}
        style={{ left: pixel.x, top: pixel.y }} onClick={() => focus(m.resource.resource_id)}
        aria-label={`${view.name}, ${view.phase}, ${view.target}${view.stale ? ", updates delayed" : ""}`}>
        <span className="drone-symbol"><DroneIcon/>{scanning && <span className="scan-ring"/>}</span>
        <span className={`drone-map-label ${i % 2 ? "label-left" : ""}`}>
          <strong>{view.name}</strong><span>{view.phase}{view.stale ? " · Delayed" : ""}</span>
          <small>{m.phase === "ready" ? "Awaiting dispatch" : m.phase === "idle" ? "Mission complete" : view.eta === null ? "Awaiting report" : `${scanning ? "Scan" : "ETA"} ${etaLabel(view.eta)}`}</small>
        </span>
      </button>;
    })}
  </div>;
}
