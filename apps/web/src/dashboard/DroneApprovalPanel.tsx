import { DroneProgress } from "./DroneProgress";
import { useEffect, useRef, useState } from "react";
import { api, message } from "../api";
import "./drone-approval.css";
import type { TrackingFrame, TrackingSnapshot } from "./drone-tracking";
import { searchStatus, type BeliefSnapshot } from "./demo-state";

type Proposal = { proposal_id: string; resource_id: string; current_worksite_id: string;
  proposed_worksite_id: string; current_utility: number; proposed_utility: number; switch_cost: number;
  reassignment_gain: number; threshold: number; status: string; expires_at: string };
type TargetScore = {target_worksite_id: string; selected_resource_id: string | null;
  building_factors?: {people_count: number | null; damage_level: string; people_contribution: number; damage_contribution: number; uncertainty_contribution: number};
  candidates: {drone_id: string; flight_distance_m: number; final_utility: number; eta_penalty: number; selected: boolean}[]};
type Snapshot = TrackingSnapshot & { paused?: boolean; latest_search_plan?: {explanations: TargetScore[]}; proposals: { proposal: Proposal; score_breakdown: unknown }[] };

export function DroneApprovalPanel({ sessionId, belief, paused = false, ended = false, onTracking, onConnect }: { sessionId?: string; belief?: BeliefSnapshot; paused?: boolean; ended?: boolean; onTracking(frame: TrackingFrame): void; onConnect(): void }) {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [clock, setClock] = useState(0);
  const [sync, setSync] = useState({ server: 0, local: 0, received: 0, observed: 0 });
  const [freezeAt, setFreezeAt] = useState<number>();
  const latest = useRef(0);
  useEffect(() => {
    if (paused || ended) setFreezeAt(at => at ?? performance.now());
  }, [paused, ended]);
  function accept(next: Snapshot, started: number) {
    const server = Date.parse(next.server_now);
    if (server < latest.current) return;
    latest.current = server;
    const observedAt = performance.now();
    if (!paused && !ended && !next.paused) setFreezeAt(undefined);
    else setFreezeAt(at => at ?? observedAt);
    // scenario_time is calculated near request start. Anchoring to `started`
    // accounts for request latency immediately and keeps position and ETA
    // continuous when this response replaces the preceding snapshot.
    onTracking({ snapshot: next, receivedAt: started, observedAt, sessionId, pausedAt: (paused || next.paused) ? observedAt : undefined });
    setSnapshot(next); setSync({ server, local: started, received: started, observed: observedAt });
  }
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    setError(""); latest.current = 0;
    async function refresh() {
      if (!sessionId || paused || ended) return;
      const started = performance.now();
      try {
        // Tick alone initializes and dispatches from canonical belief; never push Search into the
        // standalone harness from the integrated path.
        const next = await api<Snapshot>(`/api/demo-sessions/${sessionId}/search/tick`, {});
        if (!stopped) {
          setError("");
          // Conservative display: subtract the full request latency, never grant extra time.
          accept(next, started);
        }
      } catch (e) { if (!stopped) setError(message(e)); }
      if (!stopped) timer = setTimeout(() => void refresh(), 1000);
    }
    void refresh();
    const clockTimer = setInterval(() => setClock(performance.now()), 100);
    return () => { stopped = true; clearTimeout(timer); clearInterval(clockTimer); };
  }, [sessionId, paused, ended]);
  async function decide(proposal: Proposal, decision: "approve" | "reject") {
    if (paused || ended) return;
    setBusy(true);
    try {
      const started = performance.now();
      const next = await api<Snapshot>(`/api/demo-sessions/${sessionId}/search/decide`, { proposal_id: proposal.proposal_id, decision });
      accept(next, started); setError("");
    } catch (e) { setError(message(e)); }
    finally { setBusy(false); }
  }
  async function changeSpeed(speed: number) {
    if (!sessionId || busy || paused || ended) return;
    setBusy(true);
    try {
      const started = performance.now();
      accept(await api<Snapshot>(`/api/demo-sessions/${sessionId}/search/speed`, { simulation_speed: speed }), started);
      setError("");
    } catch (e) { setError(message(e)); }
    finally { setBusy(false); }
  }
  async function call(path: string) {
    if (!sessionId || busy || paused || ended) return;
    setBusy(true);
    try {
      const started = performance.now();
      accept(await api<Snapshot>(`/api/demo-sessions/${sessionId}/search/${path}`, {}), started);
      setError("");
    } catch (e) { setError(message(e)); }
    finally { setBusy(false); }
  }
  if (!sessionId) return <section className="dashboard-panel drone-approval" aria-label="Drone setup">
    <h2>Drone tracking</h2>
    <p>Start a command session, then Start Caraballeda Demo. Search launches its own drones from canonical belief.</p>
    <button className="primary-button" onClick={onConnect}>Set up drones</button>
    <p>No radio connection is required for the simulated drone demo.</p>
  </section>;
  const now = sync.server + Math.max(0, (freezeAt ?? clock) - sync.local);
  const search = searchStatus({ sessionId, error, tick: snapshot, worksites: belief?.worksites });
  return <DroneProgress frame={snapshot ? { snapshot, receivedAt: sync.received, observedAt: sync.observed, sessionId, pausedAt: freezeAt ?? ((paused || ended || snapshot.paused) ? sync.observed : undefined) } : undefined}>
    <div className="drone-controls">
    <p className="search-status" role="status"><span className={`search-phase ${search.phase}`}>{search.label}</span>{search.detail}</p>
    <label className="simulation-speed">Simulation speed <select aria-label="Simulation speed" value={snapshot?.simulation_speed ?? 1} disabled={busy || paused || ended || !snapshot?.initialized} onChange={e => void changeSpeed(Number(e.target.value))}>
      <option value={1}>1×</option><option value={5}>5×</option><option value={10}>10×</option>
    </select></label>
    {error && <><p role="alert">{error}</p><p>The drone service is unavailable. If you just updated the code, restart the backend and refresh this page.</p><button className="quiet-button" onClick={onConnect}>Session settings</button></>}
    {!!snapshot?.latest_search_plan?.explanations.length && <details>
      <summary>Target priority ({snapshot.latest_search_plan.explanations.length})</summary>
      <ol>{snapshot.latest_search_plan.explanations.map(target => <li key={target.target_worksite_id}>
        <strong>{snapshot.state?.worksites.find(w => w.worksite_id === target.target_worksite_id)?.display_name ?? target.target_worksite_id}</strong>
        {target.building_factors && <p>People: {target.building_factors.people_count ?? "Unknown"} · Damage: {target.building_factors.damage_level.replaceAll("_", " ")}<br />
          People +{target.building_factors.people_contribution.toFixed(3)} · Damage +{target.building_factors.damage_contribution.toFixed(3)} · Verification +{target.building_factors.uncertainty_contribution.toFixed(3)}</p>}
        {target.candidates.map(c => <p key={c.drone_id}>{c.drone_id}: {Math.round(c.flight_distance_m)} m · Travel −{c.eta_penalty.toFixed(3)} · Score {c.final_utility.toFixed(3)}{c.selected ? " · Selected" : ""}</p>)}
      </li>)}</ol>
    </details>}
    {snapshot?.proposals.filter(({ proposal }) => proposal.status === "pending" && Date.parse(proposal.expires_at) > now).map(({ proposal: p, score_breakdown }) => {
      const remaining = Math.max(0, Math.ceil((Date.parse(p.expires_at) - now) / 1000));
      const pending = p.status === "pending" && remaining > 0;
      return <article key={p.proposal_id} className={pending ? "drone-request pending" : "drone-request"}>
        <h3>{pending ? "Approval requested" : p.status === "approved" ? "Task switch approved" : "Current task retained"} · {p.resource_id}</h3>
        <p>{p.current_worksite_id} → {p.proposed_worksite_id}</p>
        <dl><dt>New task utility</dt><dd>{p.proposed_utility.toFixed(4)}</dd>
          <dt>Current task utility</dt><dd>− {p.current_utility.toFixed(4)}</dd>
          <dt>Switch cost</dt><dd>− {p.switch_cost.toFixed(4)}</dd>
          <dt>Net gain / threshold</dt><dd>{p.reassignment_gain.toFixed(4)} / {p.threshold}</dd></dl>
        {pending ? <>
          <p role="status">{remaining}s remaining. Drone continues its current task.</p>
          <div className="drone-actions">
            <button className="primary-button" disabled={busy || paused || ended || !!error} onClick={() => void decide(p, "approve")}>Approve task switch</button>
            <button className="quiet-button" disabled={busy || paused || ended || !!error} onClick={() => void decide(p, "reject")}>Keep current task</button>
          </div>
        </> : <p>{p.status === "expired" || p.status === "pending" ? "Timed out — no approval. Current task continues." : p.status === "invalidated" ? "Conditions changed; this request is no longer valid." : p.status === "rejected" ? "Operator kept the current task." : "Approved by operator."}</p>}
        <details><summary>Exact score components</summary><pre>{JSON.stringify(score_breakdown, null, 2)}</pre></details>
      </article>;
    })}
    <details className="drone-developer">
      <summary>Developer tools · manual Search harness</summary>
      <p>Start Caraballeda Demo needs none of these. Search initializes and dispatches itself from canonical belief.</p>
      <div className="drone-actions">
        <button className="quiet-button" disabled={busy || paused || ended} onClick={() => void call("prepare")}>Prepare standalone harness</button>
        <button className="quiet-button" disabled={busy || paused || ended} onClick={() => void call("dispatch")}>Start scanning</button>
      </div>
    </details>
    </div>
  </DroneProgress>;
}
