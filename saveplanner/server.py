"""STANDALONE test harness: serves SaveDemo over HTTP + SSE on :8765.

This is **not** the live Dashboard integration. `npm run dev` uses Fastify
`/api/demo-sessions/:id/save` and `saveplanner.live_bridge`. Nothing in
`controller.py` depends on this file.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TICK_HZ = 20.0            # unit markers move at the tick rate, so keep it smooth


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

    def __init__(self, demo, broadcaster, lock):
        super().__init__(name="aftershock-sim")
        self.demo = demo
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
                self.demo.tick(elapsed)
                snapshot = self.demo.snapshot()
            self.broadcaster.publish(snapshot)


def road_payload(graph):
    """Every road once, at load. Only the blocked set changes afterwards."""
    return {
        "attribution": "© OpenStreetMap contributors, ODbL 1.0 (extract by Geofabrik)",
        "edges": [{
            "edge_id": edge.edge_id,
            "name": edge.name,
            "vehicle": edge.vehicle,
            "status": edge.status,
            "highway": edge.tags.get("highway"),
            "path": [[round(point[1], 6), round(point[0], 6)] for point in edge.path],
        } for edge in graph.edges.values()],
    }


def make_handler(demo, broadcaster, lock):
    roads = road_payload(demo.graph)

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

        def read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/roads":
                return self.send_json(roads)
            if path == "/api/state":
                with lock:
                    return self.send_json(demo.snapshot())
            if path == "/api/stream":
                return self.stream()
            return self.send_error(404)

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                body = self.read_json()
            except json.JSONDecodeError:
                return self.send_json({"error": "invalid JSON"}, 400)
            try:
                with lock:
                    if path == "/api/road":
                        # Three states, per the frozen contract. `blocked: true/false`
                        # is still accepted so the map click toggle keeps working.
                        status = body.get("status")
                        if status is None:
                            status = "blocked" if body.get("blocked", True) else "open"
                        demo.set_road(body["edge_id"], status,
                                      body.get("travel_cost_multiplier", 1.0))
                        return self.send_json({"status": status,
                                               "state_version": demo.belief["state_version"]})
                    if path == "/api/event":
                        event = (demo.jump_to(body["event_id"]) if body.get("event_id")
                                 else demo.next_event())
                        return self.send_json({"scenario_event": event})
                    if path == "/api/proposal":
                        handled, reason = demo.decide(bool(body.get("approve")))
                        return self.send_json({"handled": handled, "reason": reason})
                    if path == "/api/control":
                        if "running" in body:
                            demo.running = bool(body["running"])
                        if "sim_speed" in body:
                            demo.sim_speed = max(0.1, min(60.0, float(body["sim_speed"])))
                        return self.send_json({"running": demo.running, "sim_speed": demo.sim_speed})
                    if path == "/api/replan":
                        demo.controller.plan(demo.belief, "manual recompute")
                        return self.send_json({"state_version": demo.belief["state_version"]})
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
                    first = json.dumps(demo.snapshot(), allow_nan=False)
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


def serve(demo, host="127.0.0.1", port=8765):
    """Start the simulation thread and block on the HTTP server."""
    lock = threading.RLock()
    broadcaster = Broadcaster()
    simulation = Simulation(demo, broadcaster, lock)
    simulation.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(demo, broadcaster, lock))
    httpd.daemon_threads = True
    print(f"Save layer on http://{host}:{port} (dev harness; dashboard proxies /saveplanner)")
    print(f"Scenario {demo.index.scenario_id}: replaying the frozen Caraballeda events.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        simulation.stopping.set()
        httpd.server_close()
