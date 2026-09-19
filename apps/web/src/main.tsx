import { useEffect, useRef, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import QRCode from "qrcode";
import { api, message } from "./api";
import {
  browserController,
  type RadioState,
  type TransmissionController,
} from "./radio/transmission-controller";
import {
  subscribeFeed,
  type Snapshot,
  type Transmission,
} from "./feed/event-client";
import "./style.css";
import { Dashboard } from "./dashboard/Dashboard";
interface Session {
  session_id: string;
  name?: string;
  session_name?: string;
  join_code?: string;
  join_expires_at?: string;
  responder_id?: string;
  allowed_responder_ids?: string[];
}
const modeLabel: Record<string, string> = {
  live_microphone: "Simulated live microphone",
  audio_replay: "Prerecorded audio replay",
  cached_transcript: "Cached transcript",
  cached_observation: "Cached observation",
};
const human = (value: string) => value.replaceAll("_", " ");
function savedSession(key: string): Session | null {
  try {
    return JSON.parse(sessionStorage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}
function useSession(key: string) {
  const [session, set] = useState<Session | null>(() => savedSession(key));
  return [
    session,
    (value: Session | null) => {
      set(value);
      if (value) sessionStorage.setItem(key, JSON.stringify(value));
      else sessionStorage.removeItem(key);
    },
  ] as const;
}
function useFeed(session?: string) {
  const [snapshot, set] = useState<Snapshot>();
  const [status, setStatus] = useState("Connecting to command…");
  useEffect(() => {
    set(undefined);
    if (session) return subscribeFeed(session, set, setStatus);
  }, [session]);
  return { snapshot, status };
}
function App() {
  const radio = location.pathname === "/radio";
  if (!radio && location.pathname !== "/radio-command") return <Dashboard />;
  return (
    <>
      <header>
        <a className="brand" href="/command">
          <span className="brandmark">A</span> AFTERSHOCK{" "}
          <span className="muted">/ RADIO</span>
        </a>
        <nav>
          <a aria-current={!radio ? "page" : undefined} href="/command">
            Command
          </a>
          <a aria-current={radio ? "page" : undefined} href="/radio">
            Field radio
          </a>
        </nav>
        <span className="simulation">SIMULATION</span>
      </header>
      {radio ? <RadioPage /> : <CommandPage />}
      <footer>
        Simulated reports · Unverified evidence · No dispatch or resource
        assignment
      </footer>
    </>
  );
}
function CommandPage() {
  const [session, setSession] = useSession("aftershock:presenter");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [qr, setQr] = useState("");
  const { snapshot, status } = useFeed(session?.session_id);
  const joinUrl = session?.join_code
    ? `${location.origin}/radio#code=${session.join_code}&responder=${encodeURIComponent(session.allowed_responder_ids?.[0] ?? "")}`
    : "";
  useEffect(() => {
    let active = true;
    setQr("");
    if (joinUrl)
      void QRCode.toDataURL(joinUrl, { width: 180, margin: 2 }).then(
        (value) => {
          if (active) setQr(value);
        },
      );
    return () => {
      active = false;
    };
  }, [joinUrl]);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError("");
    try {
      const result = await api<Session>(
        "/api/demo-sessions",
        {
          name: data.get("name"),
          allowed_responder_ids: String(data.get("responders"))
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean),
        },
        { headers: { "x-presenter-key": String(data.get("key")) } },
      );
      form.reset();
      setSession(result);
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  async function replay(capture_mode: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/api/demo-sessions/${session!.session_id}/replay`, {
        fixture_name: "riverside_report",
        capture_mode,
      });
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (
      !confirm(
        "Delete this session and all its reports? This cannot be undone.",
      )
    )
      return;
    setBusy(true);
    try {
      await api(
        `/api/demo-sessions/${session!.session_id}`,
        { confirm_session_id: session!.session_id },
        { method: "DELETE" },
      );
      setSession(null);
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main>
      <div className="page-heading">
        <div>
          <p className="eyebrow">COMMAND CHANNEL / 01</p>
          <h1>Every report. In context.</h1>
          <p className="muted">
            Listen to the field. Inspect the source. Preserve uncertainty.
          </p>
        </div>
        <span className="connection">
          ● {session ? status : "Session not started"}
        </span>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!session ? (
        <section className="panel setup">
          <p className="eyebrow">START A SIMULATION</p>
          <h2>Create a command session</h2>
          <form onSubmit={create}>
            <label>
              Session name
              <input
                name="name"
                defaultValue="Riverside radio rehearsal"
                required
                maxLength={100}
              />
            </label>
            <label>
              Allowed responder IDs
              <input name="responders" defaultValue="rescue-alpha" required />
              <small>Separate multiple responders with commas.</small>
            </label>
            <label>
              Presenter key
              <input name="key" type="password" autoComplete="off" required />
            </label>
            <button disabled={busy}>
              {busy ? "Creating…" : "Create session"}
            </button>
          </form>
          <p className="muted">
            The presenter key authenticates session creation and is not saved by
            this application.
          </p>
        </section>
      ) : (
        <div className="command-grid">
          <aside>
            <section className="panel">
              <p className="eyebrow">ACTIVE SESSION</p>
              <h2>{session.name}</h2>
              <p className="muted">Simulated Riverside Apartments</p>
              {qr && (
                <img
                  className="qr"
                  src={qr}
                  alt="Scan to join this radio session"
                />
              )}
              <p className="join-code">{session.join_code}</p>
              <p>
                <a href={joinUrl}>Open field radio ↗</a>
              </p>
              <small>
                Join code expires{" "}
                {new Date(session.join_expires_at!).toLocaleTimeString()}.
                Responder: {session.allowed_responder_ids?.join(", ")}
              </small>
            </section>
            <section className="panel">
              <p className="eyebrow">EXPLICIT FALLBACKS</p>
              <h2>Keep the demo moving</h2>
              <p className="muted">
                Riverside fixture only. Each replay is clearly labeled in the
                feed.
              </p>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void replay("cached_transcript")}
              >
                Replay cached transcript
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void replay("cached_observation")}
              >
                Replay cached observation
              </button>
              <p>
                <small>
                  For the audio fallback, join field radio and select a human
                  recording.
                </small>
              </p>
            </section>
            <button
              className="danger"
              disabled={busy}
              onClick={() => void remove()}
            >
              Delete session & reports
            </button>
            <button className="secondary" onClick={() => setSession(null)}>
              Open session setup
            </button>
          </aside>
          <section>
            <div className="feed-heading">
              <h2>Transmission feed</h2>
              <span>{snapshot?.transmissions.length ?? 0} reports</span>
            </div>
            {!snapshot?.transmissions.length ? (
              <div className="empty">
                <span className="signal">◉</span>
                <h2>Standing by for the field</h2>
                <p>
                  Join this session on a phone and transmit a report,
                  <br />
                  or select a labeled replay.
                </p>
              </div>
            ) : (
              snapshot.transmissions.map((item) => (
                <Report key={item.transmission_id} item={item} />
              ))
            )}
          </section>
        </div>
      )}
    </main>
  );
}
function Report({ item }: { item: Transmission }) {
  const [retryState, setRetryState] = useState("");
  async function retryExtraction() {
    setRetryState("Retrying…");
    try {
      await api(
        `/api/radio/transmissions/${item.transmission_id}/retry-extraction`,
        {},
      );
      setRetryState("Retry requested");
    } catch (error) {
      setRetryState(message(error));
    }
  }
  return (
    <article className="panel report">
      <div className="report-top">
        <strong>{item.responder_id}</strong>
        <span
          className={`badge ${item.processing_state === "failed" ? "failed" : ""}`}
        >
          {human(item.processing_state)}
        </span>
      </div>
      <p className="meta">
        {item.channel} · {new Date(item.created_at).toLocaleTimeString()} ·{" "}
        {modeLabel[item.capture_mode]}
      </p>
      <blockquote>
        {item.transcript ?? "Awaiting a completed transmission…"}
      </blockquote>
      {item.extraction_result && (
        <>
          <p className="eyebrow">EXTRACTED FACTS · UNVERIFIED</p>
          <p className="meta">{item.extraction_result.model_reference}</p>
          {item.extraction_result.result_status === "no_actionable_content" && (
            <p>No actionable content.</p>
          )}
          {item.extraction_result.observations.map((fact, i) => (
            <div className="fact" key={i}>
              <strong>{human(fact.incident_type)}</strong>
              <span>{fact.location_text ?? "Location unknown"}</span>
              <p>
                {human(fact.victim_indication.type)} · count{" "}
                {fact.victim_indication.count_min ?? "?"}–
                {fact.victim_indication.count_max ?? "?"} ·{" "}
                {human(fact.structural_hazard)} · access{" "}
                {human(fact.access_status)}
              </p>
              <small>{fact.supporting_text.join(" · ")}</small>
            </div>
          ))}
        </>
      )}
      {item.location_resolutions?.map((result) => (
        <p className="resolution" key={result.observation_id}>
          Location: {result.resolution_status}
          {result.resolved_site_id
            ? ` · ${result.resolved_site_id}`
            : " · no site assigned"}
        </p>
      ))}
      {item.processing_state === "failed" && (
        <>
          <button
            className="secondary"
            onClick={() => void retryExtraction()}
            disabled={retryState === "Retrying…"}
          >
            Retry extraction
          </button>
          <p role="status">{retryState}</p>
        </>
      )}
      <details>
        <summary>Inspect source & structured payload</summary>
        <pre>{JSON.stringify(item, null, 2)}</pre>
      </details>
    </article>
  );
}
function RadioPage() {
  const [session, setSession] = useSession("aftershock:participant");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [join] = useState(() => new URLSearchParams(location.hash.slice(1)));
  useEffect(() => {
    if (location.hash) history.replaceState(null, "", "/radio");
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setError("");
    setBusy(true);
    try {
      setSession(
        await api<Session>("/api/demo-sessions/join", {
          join_code: data.get("code"),
          responder_id: data.get("responder"),
        }),
      );
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="radio-page">
      <p className="eyebrow">FIELD RADIO / COMMAND-1</p>
      <h1>{session ? session.session_name : "Connect to command."}</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!session ? (
        <section className="panel">
          <h2>Join a simulation</h2>
          <form onSubmit={submit}>
            <label>
              Join code
              <input
                name="code"
                required
                defaultValue={join.get("code") ?? ""}
                autoCapitalize="characters"
                autoComplete="off"
              />
            </label>
            <label>
              Responder ID
              <input
                name="responder"
                required
                defaultValue={join.get("responder") ?? "rescue-alpha"}
                autoCapitalize="none"
              />
            </label>
            <button disabled={busy}>
              {busy ? "Joining…" : "Join command session"}
            </button>
          </form>
        </section>
      ) : (
        <RadioControls
          key={session.session_id}
          session={session}
          leave={() => setSession(null)}
        />
      )}
    </main>
  );
}
function RadioControls({
  session,
  leave,
}: {
  session: Session;
  leave(): void;
}) {
  const [state, setState] = useState<RadioState>({
    phase: "ready",
    caption: "",
    detail: "Ready to connect",
    level: 0,
    elapsed: 0,
    pending: false,
  });
  const controller = useRef<TransmissionController | null>(null);
  const [file, setFile] = useState<File>();
  const { snapshot, status: feedStatus } = useFeed(session.session_id);
  const [audioUrl, setAudioUrl] = useState("");
  useEffect(() => {
    if (!file) {
      setAudioUrl("");
      return;
    }
    const url = URL.createObjectURL(file);
    setAudioUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  useEffect(() => {
    const instance = browserController(setState, session.session_id);
    controller.current = instance;
    const hidden = () => {
      if (document.hidden) instance.interrupt();
    };
    const leaving = () => instance.interrupt();
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("pagehide", leaving);
    return () => {
      instance.dispose();
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("pagehide", leaving);
    };
  }, [session.session_id]);
  const canStart = !state.pending && ["ready", "error"].includes(state.phase);
  const active = ["connecting", "recording"].includes(state.phase);
  return (
    <>
      <div className="identity">
        <span>● {session.responder_id}</span>
        <span>
          {modeLabel[state.captureMode ?? "live_microphone"]?.toUpperCase()}
        </span>
      </div>
      <p className="meta" role="status">
        {feedStatus}
      </p>
      <section className="panel capture">
        <p className="eyebrow">{state.phase.toUpperCase()}</p>
        <h2 role="status" aria-live="polite">
          {state.detail}
        </h2>
        <div
          className="meter"
          role="meter"
          aria-label="Microphone level"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(Math.min(1, state.level * 5) * 100)}
        >
          <span style={{ width: `${Math.min(1, state.level * 5) * 100}%` }} />
        </div>
        <p className="timer">
          00:{String(state.elapsed).padStart(2, "0")} <small>/ 01:00</small>
        </p>
        <button
          className={`ptt ${state.phase === "recording" ? "recording" : ""}`}
          disabled={!canStart && !active}
          onPointerDown={(event) => {
            if (event.button !== 0 || !canStart) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            void controller.current?.start();
          }}
          onPointerUp={() => {
            void controller.current?.stop();
          }}
          onPointerCancel={() => controller.current?.interrupt()}
          onLostPointerCapture={() => {
            if (
              ["connecting", "recording"].includes(
                controller.current?.state.phase ?? "",
              )
            )
              controller.current?.interrupt();
          }}
          onKeyDown={(event) => {
            if ([" ", "Enter"].includes(event.key)) {
              event.preventDefault();
              if (!event.repeat && canStart) void controller.current?.start();
            }
          }}
          onKeyUp={(event) => {
            if ([" ", "Enter"].includes(event.key)) {
              event.preventDefault();
              void controller.current?.stop();
            }
          }}
          onContextMenu={(event) => event.preventDefault()}
        >
          {state.phase === "recording"
            ? "● Transmitting"
            : state.phase === "connecting"
              ? "Connecting…"
              : "Hold to transmit"}
        </button>
        <button
          className="secondary"
          disabled={!canStart && !active}
          onClick={() => {
            if (active) void controller.current?.stop();
            else void controller.current?.start();
          }}
        >
          {active ? "Tap to stop" : "Tap to start instead"}
        </button>
        <p className="muted">
          Wait for “Speak now.” Release to send. Maximum 60 seconds.
        </p>
        {state.pending && state.phase === "error" && (
          <button onClick={() => void controller.current?.retry()}>
            Retry / recover transmission
          </button>
        )}
        <div className="caption">
          <p className="eyebrow">
            {state.phase === "error"
              ? "UNSENT / RECOVERABLE TEXT"
              : "TRANSCRIPT"}
          </p>
          <p>{state.caption || "Your words will appear here."}</p>
        </div>
      </section>
      <details className="panel">
        <summary>Prerecorded human audio fallback</summary>
        <p>
          Choose a recording up to 60 seconds. It will be streamed through
          speech recognition and labeled “audio replay.”
        </p>
        <input
          aria-label="Human audio recording"
          type="file"
          accept="audio/*"
          onChange={(event) => setFile(event.target.files?.[0])}
        />
        {file && <audio controls src={audioUrl} />}
        <button
          disabled={!file || !canStart}
          onClick={() => void controller.current?.start(file)}
        >
          Stream audio replay
        </button>
      </details>
      {snapshot?.transmissions
        .filter((item) => item.responder_id === session.responder_id)
        .slice(0, 3)
        .map((item) => (
          <Report key={item.transmission_id} item={item} />
        ))}
      <button className="secondary" disabled={!canStart} onClick={leave}>
        Leave radio session
      </button>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
