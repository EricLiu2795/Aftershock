import { useEffect, useRef, useState } from "react";
import { api, message } from "../api";
import "./drone-approval.css";

type Proposal = { proposal_id: string; resource_id: string; current_worksite_id: string;
  proposed_worksite_id: string; current_utility: number; proposed_utility: number; switch_cost: number;
  reassignment_gain: number; threshold: number; status: string; expires_at: string };
type Snapshot = { initialized: boolean; server_now: string; proposals: { proposal: Proposal; score_breakdown: unknown }[];
  missions: { resource: { resource_id: string }; worksite_id: string; phase: string }[] };

export function DroneApprovalPanel({ sessionId }: { sessionId?: string }) {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [clock, setClock] = useState(0);
  const [sync, setSync] = useState({ server: 0, local: 0 });
  const latest = useRef(0);
  function accept(next: Snapshot, started: number) {
    const server = Date.parse(next.server_now);
    if (server < latest.current) return;
    latest.current = server;
    setSnapshot(next); setSync({ server, local: started });
  }
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    setSnapshot(undefined); setError(""); latest.current = 0;
    async function refresh() {
      if (!sessionId) return;
      const started = performance.now();
      try {
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
  }, [sessionId]);
  async function decide(proposal: Proposal, decision: "approve" | "reject") {
    setBusy(true);
    try {
      const started = performance.now();
      const next = await api<Snapshot>(`/api/demo-sessions/${sessionId}/search/decide`, { proposal_id: proposal.proposal_id, decision });
      accept(next, started); setError("");
    } catch (e) { setError(message(e)); }
    finally { setBusy(false); }
  }
  if (!sessionId) return null;
  const now = sync.server + Math.max(0, clock - sync.local);
  return <section className="dashboard-panel drone-approval" aria-label="Drone task approvals">
    <h2>Drone task approvals</h2>
    <p>Only an explicit approval within 30 seconds changes a task. Otherwise the drone keeps its current task.</p>
    {error && <p role="alert">{error} Approval unavailable; the current task continues.</p>}
    {!snapshot?.initialized && !error && <p>Waiting for the Search Planner’s operational snapshot.</p>}
    {snapshot?.missions.map(m => <p key={m.resource.resource_id}><strong>{m.resource.resource_id}</strong> · {m.worksite_id} · {m.phase.replaceAll("_", " ")}</p>)}
    {snapshot?.proposals.slice(-10).reverse().map(({ proposal: p, score_breakdown }) => {
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
            <button className="primary-button" disabled={busy || !!error} onClick={() => void decide(p, "approve")}>Approve task switch</button>
            <button className="quiet-button" disabled={busy || !!error} onClick={() => void decide(p, "reject")}>Keep current task</button>
          </div>
        </> : <p>{p.status === "expired" || p.status === "pending" ? "Timed out — no approval. Current task continues." : p.status === "invalidated" ? "Conditions changed; this request is no longer valid." : p.status === "rejected" ? "Operator kept the current task." : "Approved by operator."}</p>}
        <details><summary>Exact score components</summary><pre>{JSON.stringify(score_breakdown, null, 2)}</pre></details>
      </article>;
    })}
  </section>;
}
