import { Plane } from "lucide-react";
import type { TrackingFrame } from "./drone-tracking";
import { DroneApprovalPanel } from "./DroneApprovalPanel";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Activity,
  Radio,
  MessageSquare,
  MapPin,
  ChevronRight,
  Plus,
  Search,
  SlidersHorizontal,
  X,
  ArrowUpRight,
  RefreshCw,
  Clock3,
  Check,
  AlertTriangle,
  Building2,
  LocateFixed,
  Link2,
  Layers3,
  Signal,
  CircleHelp,
} from "lucide-react";
import QRCode from "qrcode";
import {
  api,
  ApiError,
  beaconEndSession,
  endSession,
  isSessionEnded,
  message,
} from "../api";
import type {
  GeographyInfo,
  IntelligenceReport,
  IntelligenceSnapshot,
  MapSite,
} from "../../../../contracts/intelligence";
import { MapView } from "./MapView";
import {
  activityEvents,
  coordinates,
  human,
  reportLocation,
  reportTitle,
  reportTone,
  time,
} from "./model";
import "./dashboard.css";

interface Session {
  session_id: string;
  name: string;
  join_code?: string;
  join_expires_at?: string;
  allowed_responder_ids?: string[];
}
function savedSession(): Session | null {
  try {
    return JSON.parse(sessionStorage.getItem("aftershock:presenter") ?? "null");
  } catch {
    return null;
  }
}
function PanelHeading({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="panel-heading">
      <div>
        {icon}
        <h2>{title}</h2>
      </div>
      {children}
    </div>
  );
}
function SourceIcon({ source }: { source: string }) {
  return source === "drone" ? <Plane size={17} /> : source === "radio" ? <Radio size={17} /> : <MessageSquare size={17} />;
}
function Modal({
  title,
  close,
  children,
}: {
  title: string;
  close(): void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  return (
    <dialog
      aria-label={title}
      className="command-modal"
      ref={dialog}
      onCancel={close}
    >
      <div className="modal-title">
        <h2>{title}</h2>
        <button aria-label="Close dialog" onClick={close}>
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function Dashboard() {
  const [droneTracking, setDroneTracking] = useState<TrackingFrame>();
  const [session, setSession] = useState<Session | null>(savedSession);
  const [sessionEnded, setSessionEnded] = useState(false);
  const [snapshot, setSnapshot] = useState<IntelligenceSnapshot>();
  const [geography, setGeography] = useState<GeographyInfo>();
  const [geoError, setGeoError] = useState("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("Session not started");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [showFiltered, setShowFiltered] = useState(false);
  const [selectedId, setSelectedId] = useState<string>();
  const [selectedSite, setSelectedSite] = useState<MapSite>();
  const [modal, setModal] = useState<"session" | "sources" | null>(null);
  const [qr, setQr] = useState("");
  const [source, setSource] = useState("replay");
  const [provider, setProvider] = useState("fixture");
  const [siteQuery, setSiteQuery] = useState("");
  const [clock, setClock] = useState(new Date());
  const detailsPanel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (detailsPanel.current) detailsPanel.current.scrollTop = 0;
  }, [selectedId]);
  const currentSession = useRef(session?.session_id);
  currentSession.current = session?.session_id;
  const sessionEndedRef = useRef(sessionEnded);
  sessionEndedRef.current = sessionEnded;
  const reports = snapshot?.reports ?? [];
  const selected = reports.find((report) => report.id === selectedId);
  const actionable = reports.filter((report) => report.observations.length);
  const mapped = actionable.filter((report) => report.locations.length);
  const unplaced = actionable.filter((report) => !report.locations.length);
  const radioCount = reports.filter(
    (report) => report.source === "radio",
  ).length;
  const socialCount = reports.filter(
    (report) => report.source === "social",
  ).length;
  const joinUrl = session?.join_code
    ? `${location.origin}/radio#code=${session.join_code}&responder=${encodeURIComponent(session.allowed_responder_ids?.[0] ?? "")}`
    : "";
  function saveSession(value: Session | null) {
    setSession(value);
    setSnapshot(undefined);
    setSelectedId(undefined);
    setSelectedSite(undefined);
    if (value)
      sessionStorage.setItem("aftershock:presenter", JSON.stringify(value));
    else sessionStorage.removeItem("aftershock:presenter");
  }
  const showSessionEnded = useCallback(() => {
    sessionEndedRef.current = true;
    setSessionEnded(true);
    setModal(null);
    setStatus("Session ended");
  }, []);
  const refresh = useCallback(async (id: string) => {
    try {
      const next = await api<IntelligenceSnapshot>(
        `/api/demo-sessions/${id}/intelligence`,
      );
      if (currentSession.current !== id) return;
      setSnapshot(next);
      setStatus("Feed connected");
    } catch (err) {
      if (currentSession.current !== id) return;
      if (isSessionEnded(err)) {
        showSessionEnded();
        return;
      }
      const auth = err instanceof ApiError && [401, 403].includes(err.status);
      setStatus(
        auth ? "Session expired · reconnect" : "Disconnected · retrying",
      );
      if (auth)
        setError(
          "This session is no longer accessible. Open Session to create a new one.",
        );
    }
  }, [showSessionEnded]);
  useEffect(() => {
    let active = true;
    api<GeographyInfo>("/api/geography")
      .then((info) => {
        if (active) setGeography(info);
      })
      .catch(() => {
        if (active)
          setGeoError(
            "Geography unavailable. Check that the API is running, then reload.",
          );
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    const timer = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!session) {
      setStatus("Session not started");
      return;
    }
    if (sessionEnded) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await refresh(session.session_id);
      if (!stopped && !sessionEndedRef.current)
        timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [session?.session_id, sessionEnded, refresh]);
  useEffect(() => {
    if (!session) return;
    const leaving = () => {
      if (!sessionEndedRef.current) beaconEndSession(session.session_id);
    };
    window.addEventListener("pagehide", leaving);
    return () => window.removeEventListener("pagehide", leaving);
  }, [session?.session_id]);
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
  const visible = reports.filter(
    (report) =>
      (filter === "all" ||
        (filter === "unplaced"
          ? report.observations.length && !report.locations.length
          : report.source === filter)) &&
      (showFiltered || report.state !== "filtered") &&
      `${report.text} ${reportLocation(report)} ${report.source}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const sites = useMemo(
    () =>
      siteQuery.trim().length >= 2
        ? (geography?.sites
            .filter((site) =>
              site.name.toLowerCase().includes(siteQuery.toLowerCase()),
            )
            .slice(0, 8) ?? [])
        : [],
    [siteQuery, geography],
  );
  async function run(label: string, work: () => Promise<void>) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (err) {
      if (isSessionEnded(err)) showSessionEnded();
      else setError(message(err));
    } finally {
      setBusy("");
    }
  }
  async function ingest(id: string, src = source, ai = provider) {
    const result = await api<{ imported: number }>(
      `/api/demo-sessions/${id}/ingest`,
      { source: src, provider: ai },
      { signal: AbortSignal.timeout(190000) },
    );
    setNotice(
      `${result.imported} ${src === "replay" ? "replay" : src.toUpperCase()} reports imported${result.imported === 0 ? " · already up to date or no results" : ""}.`,
    );
    await refresh(id);
  }
  async function replay(id: string) {
    await api(`/api/demo-sessions/${id}/replay`, {
      fixture_name: "caraballeda_report",
      capture_mode: "cached_observation",
    });
    await refresh(id);
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    await run("Starting session", async () => {
      const result = await api<Session>(
        "/api/demo-sessions",
        {
          name: data.get("name"),
          scenario_id: "caraballeda",
          allowed_responder_ids: String(data.get("responders"))
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean),
        },
        { headers: { "x-presenter-key": String(data.get("key")) } },
      );
      currentSession.current = result.session_id;
      setSessionEnded(false);
      saveSession(result);
      setModal(null);
      if (data.get("replay")) {
        await replay(result.session_id);
        await ingest(result.session_id, "replay", "fixture");
      }
    });
  }
  function selectReport(id: string) {
    setSelectedId(id);
    setSelectedSite(undefined);
    setSiteQuery("");
  }
  async function locate() {
    if (!session || !selected || !selectedSite) return;
    await run("Saving location", async () => {
      await api(`/api/demo-sessions/${session.session_id}/locate`, {
        report_id: selected.id,
        site_id: selectedSite.id,
      });
      await refresh(session.session_id);
      setSelectedSite(undefined);
      setNotice("Report location saved. The report remains unverified.");
    });
  }
  async function endCurrentSession() {
    if (!session || sessionEndedRef.current) return;
    setBusy("Ending session");
    setError("");
    try {
      await endSession(session.session_id);
      showSessionEnded();
    } catch (err) {
      if (isSessionEnded(err)) showSessionEnded();
      else setError(message(err));
    } finally {
      setBusy("");
    }
  }
  function acknowledgeSessionEnded() {
    saveSession(null);
    setSessionEnded(false);
    setError("");
    setModal("session");
  }
  const locationCount = new Set(
    mapped.flatMap((report) => report.locations.map((item) => item.site.id)),
  ).size;
  const activity = useMemo(() => activityEvents(reports), [reports]);
  function openModal(kind: "session" | "sources") {
    // Messages from earlier actions should not appear as the result of this dialog.
    setError("");
    setNotice("");
    setModal(kind);
  }
  return (
    <div className="command-app">
      <header className="command-header">
        <a href="/command" className="command-brand">
          <span className="command-brand-icon">
            <Activity size={28} />
          </span>
          <div>
            AFTERSHOCK<span>DISASTER INTELLIGENCE</span>
          </div>
        </a>
        <div className="header-divider" />
        <div className="scenario-heading">
          <span>OPERATIONS OVERVIEW</span>
          <strong>Caraballeda earthquake scenario</strong>
        </div>
        <div className="header-right">
          <span className="sim-chip">
            <i /> SIMULATION
          </span>
          <span className="utc-clock">
            {clock.toISOString().slice(11, 19)} <small>UTC</small>
          </span>
          <button className="quiet-button" onClick={() => openModal("session")}>
            <Radio size={15} />
            {session ? "Session" : "Start session"}
          </button>
        </div>
      </header>
      <div className="command-subbar">
        <div>
          <span
            className={`connection-dot ${status === "Feed connected" ? "connected" : ""}`}
          />
          {status}
          <span className="subbar-separator">/</span>
          <span>{session?.name ?? "Caraballeda · La Guaira, Venezuela"}</span>
        </div>
        <button onClick={() => openModal("sources")}>
          <SlidersHorizontal size={14} /> Sources & replay{" "}
          <ChevronRight size={14} />
        </button>
      </div>
      {(error || geoError || notice) && (
        <div
          role={error || geoError ? "alert" : "status"}
          className={`dashboard-banner ${error || geoError ? "banner-error" : ""}`}
        >
          <span>{error || geoError || notice}</span>
          <button
            aria-label="Dismiss message"
            onClick={() => {
              setError("");
              setGeoError("");
              setNotice("");
            }}
          >
            <X size={15} />
          </button>
        </div>
      )}
      <main className="operations-grid">
        <aside className="incoming-panel dashboard-panel">
          <PanelHeading
            icon={<Signal size={17} />}
            title="Incoming information"
          >
            <span className="count-pill">{reports.length}</span>
          </PanelHeading>
          <div className="source-tabs">
            {[
              ["all", "All", reports.length],
              ["radio", "Radio", radioCount],
              ["social", "Social", socialCount],
              ["drone", "Drone", reports.filter(r => r.source === "drone").length],
            ].map(([key, label, count]) => (
              <button
                key={key}
                aria-pressed={filter === key}
                className={filter === key ? "active" : ""}
                onClick={() => setFilter(String(key))}
              >
                {label}
                <span>{count}</span>
              </button>
            ))}
          </div>
          <div className="feed-search">
            <Search size={14} />
            <input
              aria-label="Search reports"
              placeholder="Search reports or locations…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="feed-options">
            <label>
              <input
                type="checkbox"
                checked={showFiltered}
                onChange={(e) => setShowFiltered(e.target.checked)}
              />{" "}
              Show filtered
            </label>
            {filter === "unplaced" ? (
              <button
                className="filter-chip"
                aria-label="Clear needs-location filter"
                onClick={() => setFilter("all")}
              >
                NEEDS LOCATION <X size={10} />
              </button>
            ) : (
              <span>NEWEST FIRST</span>
            )}
          </div>
          <div className="incoming-list">
            {visible.map((report) => (
              <button
                key={report.id}
                onClick={() => selectReport(report.id)}
                className={`incoming-card ${selectedId === report.id ? "selected" : ""} tone-${reportTone(report)}`}
              >
                <div className="incoming-top">
                  <span className={`source-symbol ${report.source}`}>
                    <SourceIcon source={report.source} />
                  </span>
                  <strong>
                    {report.source === "radio"
                      ? "Field radio"
                      : report.source === "social"
                        ? "Social report"
                        : report.source.toUpperCase()}
                  </strong>
                  <time>{time(report.timestamp)}</time>
                </div>
                <div className="incoming-badges">
                  <span
                    className={`state-badge ${report.state === "filtered" ? "filtered" : report.state === "failed" ? "failed" : ""}`}
                  >
                    {report.state === "filtered"
                      ? "FILTERED"
                      : report.state === "failed"
                        ? "FAILED"
                        : report.observations.length
                          ? "PROCESSED"
                          : human(report.state).toUpperCase()}
                  </span>
                  <span className="replay-label">
                    {report.mode === "live_external"
                      ? "EXTERNAL FEED"
                      : report.mode === "live_microphone"
                        ? "MICROPHONE"
                        : "REPLAY"}
                  </span>
                </div>
                <p>“{report.text}”</p>
                <div className="incoming-location">
                  <MapPin size={12} />
                  <span>{reportLocation(report)}</span>
                  {report.locations.length > 0 && <Check size={12} />}
                </div>
              </button>
            ))}
            {!visible.length && (
              <div className="dashboard-empty">
                <Radio size={28} />
                <h3>
                  {reports.length
                    ? "No matching reports"
                    : "Listening starts here"}
                </h3>
                <p>
                  {reports.length
                    ? "Change your search or source filter."
                    : "Connect field radio or load the social replay to bring reports into one view."}
                </p>
                {!session && (
                  <button
                    className="primary-button"
                    onClick={() => openModal("session")}
                  >
                    Start a session <ArrowUpRight size={14} />
                  </button>
                )}
              </div>
            )}
          </div>
          <div className="feed-bottom">
            <button
              disabled={!!busy}
              onClick={() =>
                session
                  ? void run("Importing replay", () =>
                      ingest(session.session_id, "replay", "fixture"),
                    )
                  : openModal("session")
              }
            >
              <Plus size={15} />
              {busy === "Importing replay"
                ? "Importing…"
                : "Import social replay"}
            </button>
            <small>Source claims are unverified</small>
          </div>
        </aside>
        <div className="map-column">
          <div className="overview-stats">
            <div>
              <span>INCOMING REPORTS</span>
              <strong>
                {String(reports.length).padStart(2, "0")}
                <small>Radio + external</small>
              </strong>
            </div>
            <div>
              <span>MAPPED LOCATIONS</span>
              <strong>
                {String(locationCount).padStart(2, "0")}
                <small>Located reports</small>
              </strong>
            </div>
            <button
              className={filter === "unplaced" ? "active" : ""}
              onClick={() =>
                setFilter(filter === "unplaced" ? "all" : "unplaced")
              }
            >
              <span>NEEDS LOCATION</span>
              <strong>
                {String(unplaced.length).padStart(2, "0")}
                <small>
                  Review reports <ChevronRight size={12} />
                </small>
              </strong>
            </button>
          </div>
          <MapView
            droneTracking={droneTracking?.sessionId === session?.session_id ? droneTracking : undefined}
            geography={geography}
            reports={actionable}
            selected={selected}
            selectedSite={selectedSite}
            onReport={selectReport}
            onSite={setSelectedSite}
          />
          <section className="activity-panel dashboard-panel">
            <PanelHeading
              icon={<Activity size={16} />}
              title="Information activity"
            >
              <span className="subtle-label">
                {activity.length
                  ? `${activity.length} EVENTS · SESSION FEED`
                  : "SESSION FEED"}
              </span>
            </PanelHeading>
            <div className="activity-items">
              {activity.map((event) => (
                <button
                  key={event.id}
                  aria-current={event.reportId === selectedId ? "true" : undefined}
                  onClick={() => selectReport(event.reportId)}
                >
                  <span className={`activity-point ${event.tone}`} />
                  <time dateTime={event.at}>{time(event.at)}</time>
                  <div>
                    <strong>{event.title}</strong>
                    <span>{event.detail}</span>
                  </div>
                  <ChevronRight size={14} />
                </button>
              ))}
              {!reports.length && (
                <p className="activity-empty">
                  New reports and processed observations will appear here.
                </p>
              )}
            </div>
          </section>
        </div>
        <aside className="detail-column">
          <section ref={detailsPanel} className="dashboard-panel details-panel">
            <PanelHeading icon={<Layers3 size={17} />} title="Report details">
              <CircleHelp size={15} className="subtle-icon" />
            </PanelHeading>
            {selected ? (
              <div className="report-details">
                <div className="detail-source">
                  <SourceIcon source={selected.source} />
                  <span>{selected.source.toUpperCase()} REPORT</span>
                  <span className="unverified">UNVERIFIED</span>
                </div>
                <h2>{reportTitle(selected)}</h2>
                <p className="detail-location">
                  <MapPin size={14} />
                  {reportLocation(selected)}
                </p>
                <blockquote>“{selected.text}”</blockquote>
                <div className="detail-meta">
                  <span>
                    <Clock3 size={12} />
                    {new Date(selected.timestamp).toLocaleString()}
                  </span>
                  <span>
                    {selected.mode === "live_external"
                      ? "Live external source"
                      : selected.mode === "live_microphone"
                        ? "Simulated field microphone"
                        : selected.mode === "simulated_scan" ? "Simulated drone scan" : "Simulated replay"}{" "}
                    · {selected.model ?? "Processing"}
                  </span>
                  {selected.author && (
                    <span>
                      {selected.source === "radio" ? "Responder" : "Author"} ·{" "}
                      {selected.author}
                    </span>
                  )}
                </div>
                <h3 className="section-label">
                  EXTRACTED OBSERVATIONS{" "}
                  <span>{selected.observations.length}</span>
                </h3>
                {selected.observations.map((observation) => (
                  <div
                    className="observation-fact"
                    key={observation.observation_id}
                  >
                    <strong>
                      <AlertTriangle size={13} />
                      {human(observation.incident_type)}
                    </strong>
                    <dl>
                      <dt>Victim indication</dt>
                      <dd>{human(observation.victim_indication.type)}</dd>
                      <dt>Structure</dt>
                      <dd>{human(observation.structural_hazard)}</dd>
                      <dt>Access</dt>
                      <dd>{human(observation.access_status)}</dd>
                    </dl>
                  </div>
                ))}
                {selected.state === "filtered" && (
                  <p className="detail-hint">
                    No actionable observations were extracted. The original
                    report is preserved.
                  </p>
                )}
                {selected.state === "failed" && (
                  <button
                    className="primary-button"
                    disabled={!!busy}
                    onClick={() =>
                      void run("Retrying extraction", async () => {
                        await api(
                          `/api/radio/transmissions/${selected.id}/retry-extraction`,
                          {},
                        );
                        await refresh(session!.session_id);
                      })
                    }
                  >
                    Retry radio extraction
                  </button>
                )}
                {selected.observations.length > 0 && (
                  <>
                    <h3 className="section-label">MAP LOCATION</h3>
                    {selected.locations.length ? (
                      selected.locations.map((location) => (
                        <div
                          className="location-confirmed"
                          key={location.site.id}
                        >
                          <Check size={14} />
                          <div>
                            {location.site.name}
                            <small>
                              {location.method === "manual"
                                ? "Manually placed"
                                : location.method === "canonical_site" ? "Canonical scan location" : "Exact map-name match"}{" "}
                              · claim remains unverified
                              <br />
                              {coordinates(location.site)}
                            </small>
                          </div>
                        </div>
                      ))
                    ) : (
                      <p className="location-pending">
                        <LocateFixed size={16} />
                        Location unresolved. Select a building on the map or
                        search below.
                      </p>
                    )}
                    <div className="site-search">
                      <Search size={14} />
                      <input
                        aria-label="Search map buildings"
                        value={siteQuery}
                        onChange={(e) => setSiteQuery(e.target.value)}
                        placeholder="Find a building by name…"
                      />
                    </div>
                    {sites.length > 0 && (
                      <div className="site-results">
                        {sites.map((site) => (
                          <button
                            key={site.id}
                            onClick={() => {
                              setSelectedSite(site);
                              setSiteQuery("");
                            }}
                          >
                            {site.name}
                            <ChevronRight size={12} />
                          </button>
                        ))}
                      </div>
                    )}
                    {siteQuery.length >= 2 && !sites.length && (
                      <p className="detail-hint">
                        No named buildings found. Select a footprint on the map.
                      </p>
                    )}
                  </>
                )}
                {selected.sourceUrl &&
                  /^https?:\/\//.test(selected.sourceUrl) && (
                    <a
                      className="source-link"
                      href={selected.sourceUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open original source <ArrowUpRight size={13} />
                    </a>
                  )}
                <details className="source-payload">
                  <summary>Inspect structured source</summary>
                  <pre>{JSON.stringify(selected, null, 2)}</pre>
                </details>
              </div>
            ) : (
              <div className="dashboard-empty detail-placeholder">
                <span className="placeholder-target">
                  <LocateFixed size={32} />
                </span>
                <h3>Every report, in context.</h3>
                <p>
                  Select an incoming report or a marker to inspect its source,
                  extracted information, and location.
                </p>
                <div className="empty-detail-lines">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            )}
          </section>
          <DroneApprovalPanel key={session?.session_id} sessionId={session?.session_id} onTracking={setDroneTracking} onConnect={() => openModal("session")} />
          {selectedSite && (
            <section className="dashboard-panel selected-site-panel">
              <PanelHeading
                icon={<Building2 size={16} />}
                title="Selected building"
              >
                <button
                  className="icon-button"
                  aria-label="Clear selected building"
                  onClick={() => setSelectedSite(undefined)}
                >
                  <X size={15} />
                </button>
              </PanelHeading>
              <div>
                <h3>{selectedSite.name}</h3>
                <p>{coordinates(selectedSite)}</p>
                <small>
                  {selectedSite.heightSource === "osm"
                    ? "OSM height"
                    : "Estimated display height"}{" "}
                  · {selectedSite.height} m
                </small>
                {selected?.observations.length ? (
                  <button
                    className="primary-button"
                    disabled={!!busy}
                    onClick={() => void locate()}
                  >
                    <MapPin size={14} />
                    Place selected report here
                  </button>
                ) : (
                  <p className="detail-hint">
                    Select an actionable report to place it at this building.
                  </p>
                )}
              </div>
            </section>
          )}
          <section className="dashboard-panel coverage-panel">
            <PanelHeading
              icon={<MapPin size={16} />}
              title="Geographic context"
            />
            <div>
              <span>CARABALLEDA / LA GUAIRA</span>
              <h3>Real geography. Clear provenance.</h3>
              <div className="coverage-counts">
                <p>
                  <strong>
                    {geography?.sites.length.toLocaleString() ?? "—"}
                  </strong>
                  mapped sites
                </p>
                <p>
                  <strong>
                    {geography?.roadCount.toLocaleString() ?? "—"}
                  </strong>
                  road segments
                </p>
              </div>
              <p className="detail-hint">
                OpenStreetMap geography. Report markers represent source claims,
                not confirmed damage.
              </p>
            </div>
          </section>
        </aside>
      </main>
      <footer className="command-footer">
        <span>
          <Activity size={13} />
          <strong>AFTERSHOCK</strong>
          <i />
          From information to impact
        </span>
        <span>Real geography · Simulated operational reports</span>
        <span>
          <span className="connection-dot connected" />
          Research & education prototype
        </span>
      </footer>
      {modal === "session" && (
        <Modal
          title={session ? "Radio session" : "Start a command session"}
          close={() => setModal(null)}
        >
          {session ? (
            <>
              <p className="modal-description">{session.name}</p>
              {qr && (
                <img
                  className="session-qr"
                  src={qr}
                  alt="Scan to join field radio"
                />
              )}
              <div className="session-code">
                {session.join_code ?? "No active join code"}
              </div>
              <p className="modal-description">
                {session.join_expires_at
                  ? `Join code expires ${new Date(session.join_expires_at).toLocaleTimeString()}.`
                  : "Create a new session to issue a join code."}
              </p>
              {joinUrl && (
                <a
                  className="primary-button"
                  href={joinUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open field radio <ArrowUpRight size={15} />
                </a>
              )}
              <button
                className="quiet-button full-width"
                disabled={!!busy}
                onClick={() => void endCurrentSession()}
              >
                {busy === "Ending session"
                  ? "Ending session…"
                  : "Set up another session"}
              </button>
              <small className="modal-footnote">
                This ends the shared radio session but keeps its saved reports.
              </small>
            </>
          ) : (
            <form onSubmit={create}>
              <p className="modal-description">
                Bring radio transmissions and external reports into the
                Caraballeda map.
              </p>
              <label>
                Session name
                <input
                  name="name"
                  defaultValue="Caraballeda command"
                  maxLength={100}
                  required
                />
              </label>
              <label>
                Responder IDs
                <input name="responders" defaultValue="rescue-alpha" required />
              </label>
              <label>
                Presenter key
                <input
                  name="key"
                  type="password"
                  autoComplete="off"
                  required
                  placeholder="Your configured presenter key"
                />
              </label>
              <label className="checkbox-label">
                <input name="replay" type="checkbox" defaultChecked />
                Start with labeled radio and social replay reports
              </label>
              <button className="primary-button" disabled={!!busy}>
                {busy || "Create session"}
                <ArrowUpRight size={15} />
              </button>
              {error && (
                <p role="alert" className="form-error">
                  {error}
                </p>
              )}
            </form>
          )}
        </Modal>
      )}
      {modal === "sources" && (
        <Modal title="Sources & replay" close={() => setModal(null)}>
          <p className="modal-description">
            Radio connects from a phone or another browser. Social replay uses
            the existing ingestion pipeline and fictional posts.
          </p>
          <div className="source-option">
            <Radio size={21} />
            <div>
              <strong>Field radio</strong>
              <span>Live microphone + cached replay</span>
            </div>
            <button
              className="quiet-button"
              onClick={() => openModal("session")}
            >
              Connect
              <Link2 size={13} />
            </button>
          </div>
          <button
            className="quiet-button full-width"
            disabled={!!busy || !session}
            onClick={() =>
              void run("Loading radio replay", () =>
                replay(session!.session_id),
              )
            }
          >
            Load Caraballeda radio replay <Plus size={14} />
          </button>
          <hr />
          <label>
            External source
            <select
              value={source}
              onChange={(e) => {
                setSource(e.target.value);
                setProvider(e.target.value === "replay" ? "fixture" : "openai");
              }}
            >
              <option value="replay">
                Social replay · fictional Caraballeda posts
              </option>
              <option value="gdacs">GDACS · live disaster alerts</option>
              <option value="reliefweb">
                ReliefWeb · live situation reports
              </option>
            </select>
          </label>
          <label>
            Extraction
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
            >
              <option value="fixture" disabled={source !== "replay"}>
                Offline fixture extraction
              </option>
              <option value="openai">
                OpenAI · requires server credentials
              </option>
            </select>
          </label>
          {source !== "replay" && (
            <p className="detail-hint">
              Live sources use the server’s country/date filters and OpenAI
              configuration. ReliefWeb also requires an approved app name.
            </p>
          )}
          <button
            className="primary-button"
            disabled={!!busy || !session}
            onClick={() =>
              void run("Importing reports", () => ingest(session!.session_id))
            }
          >
            <RefreshCw size={14} />
            {busy || "Import reports"}
          </button>
          {!session && (
            <p className="detail-hint">Start a session to import reports.</p>
          )}
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          {notice && (
            <p role="status" className="detail-hint">
              {notice}
            </p>
          )}
        </Modal>
      )}
      {sessionEnded && (
        <Modal title="Session ended" close={acknowledgeSessionEnded}>
          <p className="modal-description">
            This shared command and field radio session has ended. Its reports
            and map data remain saved, but no more session operations can be
            performed.
          </p>
          <button
            className="primary-button"
            onClick={acknowledgeSessionEnded}
          >
            Set up another session
          </button>
        </Modal>
      )}
    </div>
  );
}
