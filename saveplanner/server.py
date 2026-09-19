"""Local HTTP server: static UI, road-graph payload, SSE state stream, control API.

Standard library only. No API key, no account and no outbound network call is needed
to run the demo; the satellite basemap is fetched by the browser from a keyless tile
service, and a Google Maps key can be supplied later without touching this file.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB_ROOT = Path(__file__).with_name("web")
TICK_HZ = 20.0            # unit markers move at the tick rate, so keep it smooth
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class Broadcaster:
    """Fan-out of runtime snapshots to every connected browser."""

    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()

    def subscribe(self):
        channel = queue.Queue(maxsize=4)
        with self.lock:
            self.clients.append(channel)
        return channel

    def unsubscribe(self, channel):
        with self.lock:
            if channel in self.clients:
                self.clients.remove(channel)

    def publish(self, payload):
        message = json.dumps(payload, allow_nan=False)
        with self.lock:
            targets = list(self.clients)
        for channel in targets:
            try:
                channel.put_nowait(message)
            except queue.Full:
                pass                    # a slow tab may drop frames; it resyncs next tick


class Simulation(threading.Thread):
    """Drives the movement tick. Planner work stays event driven inside the runtime."""

    daemon = True

    def __init__(self, runtime, broadcaster, lock):
        super().__init__(name="aftershock-sim")
        self.runtime = runtime
        self.broadcaster = broadcaster
        self.lock = lock
        self.stopping = threading.Event()

    def run(self):
        interval = 1.0 / TICK_HZ
        previous = time.monotonic()
        while not self.stopping.is_set():
            time.sleep(interval)
            now = time.monotonic()
            elapsed, previous = now - previous, now
            with self.lock:
                self.runtime.tick(elapsed)
                snapshot = self.runtime.snapshot()
            self.broadcaster.publish(snapshot)


def road_payload(graph):
    """Every road once, at load. Only the blocked set changes afterwards."""
    return {
        "attribution": "© OpenStreetMap contributors, ODbL 1.0 (extract by Geofabrik)",
        "edges": [{
            "edge_id": edge.edge_id,
            "name": edge.name,
            "vehicle": edge.vehicle,
            "highway": edge.tags.get("highway"),
            "path": [[round(point[1], 6), round(point[0], 6)] for point in edge.path],
        } for edge in graph.edges.values()],
    }


def make_handler(runtime, graph, broadcaster, lock):
    roads = road_payload(graph)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Aftershock/1.0"

        def log_message(self, *args):
            pass                        # keep the console clear for the demo

        def send_json(self, payload, status=200):
            body = json.dumps(payload, allow_nan=False).encode("utf-8")
            self.send_response(status)
            # Permissive by design: this binds to localhost and serves a public
            # demo scenario, so the dashboard can reach it with or without a proxy.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, name):
            target = (WEB_ROOT / name).resolve()
            if not target.is_file() or WEB_ROOT.resolve() not in target.parents:
                self.send_error(404)
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                return self.send_file("index.html")
            if path == "/api/roads":
                return self.send_json(roads)
            if path == "/api/state":
                with lock:
                    return self.send_json(runtime.snapshot())
            if path == "/api/stream":
                return self.stream()
            if path.startswith("/api/"):
                return self.send_error(404)
            return self.send_file(path.lstrip("/"))

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                body = self.read_json()
            except json.JSONDecodeError:
                return self.send_json({"error": "invalid JSON"}, 400)
            try:
                with lock:
                    if path == "/api/road":
                        changed = runtime.set_road_blocked(
                            body["edge_id"], bool(body.get("blocked", True)), body.get("source", "commander"))
                        return self.send_json({"changed": changed, "state_version": runtime.state_version})
                    if path == "/api/worksite":
                        worksite = runtime.update_worksite(
                            body["worksite_id"],
                            {k: v for k, v in body.items()
                             if k not in {"worksite_id", "source"}},
                            body.get("source", "fusion"))
                        return self.send_json({
                            "worksite_id": worksite["worksite_id"],
                            "evidence_status": worksite["evidence_status"],
                            "save_priority": runtime.save_decisions.get(
                                worksite["worksite_id"], {}).get("save_priority"),
                            "state_version": runtime.state_version})
                    if path == "/api/proposal":
                        handled = runtime.decide_proposal(bool(body.get("approve")))
                        return self.send_json({"handled": handled, "state_version": runtime.state_version})
                    if path == "/api/control":
                        if "running" in body:
                            runtime.running = bool(body["running"])
                        if "sim_speed" in body:
                            runtime.sim_speed = max(0.1, min(60.0, float(body["sim_speed"])))
                        return self.send_json({"running": runtime.running, "sim_speed": runtime.sim_speed})
                    if path == "/api/replan":
                        runtime.replan("manual recompute")
                        return self.send_json({"state_version": runtime.state_version})
            except (KeyError, ValueError) as error:
                return self.send_json({"error": str(error)}, 400)
            return self.send_error(404)

        def stream(self):
            channel = broadcaster.subscribe()
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                with lock:
                    first = json.dumps(runtime.snapshot(), allow_nan=False)
                self.wfile.write(f"data: {first}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    message = channel.get()
                    self.wfile.write(f"data: {message}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                broadcaster.unsubscribe(channel)

    return Handler


def serve(runtime, graph, host="127.0.0.1", port=8765):
    """Start the simulation thread and block on the HTTP server."""
    lock = threading.RLock()
    broadcaster = Broadcaster()
    simulation = Simulation(runtime, broadcaster, lock)
    simulation.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(runtime, graph, broadcaster, lock))
    httpd.daemon_threads = True
    print(f"Aftershock Save Planner on http://{host}:{port}")
    print("Click any road on the map to block or reopen it; units reroute automatically.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        simulation.stopping.set()
        httpd.server_close()
