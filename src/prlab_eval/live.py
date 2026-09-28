"""Serve the comparison canvas as a local web page that follows reports/ live.

The Cursor canvas (viewer/review-tool-comparison.canvas.tsx) only renders inside
Cursor. This serves viewer/index.html, a browser copy of the same layout, and
feeds it the same data: ``canvas.build_payload()`` over the latest run per tool.

    python3 -m prlab_eval.live            # http://127.0.0.1:8765
    python3 -m prlab_eval.live --port 9000 --open

Routes:
    /              the page
    /api/payload   the canvas payload, rebuilt from reports/ on every request
    /api/events    server-sent events; "reports" fires when a report changes

Standard library only. Binds to 127.0.0.1 unless --host says otherwise.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from prlab_eval.canvas import CanvasError, build_payload
from prlab_eval.cases import ROOT
from prlab_eval.report import REPORT_DIR

PAGE = ROOT / "viewer" / "index.html"
# Files the generator writes into reports/ itself; watching them would loop.
IGNORED = {"comparison.json"}
REPORT_SUFFIXES = {".json", ".md", ".html"}


def snapshot(reports: Path) -> dict[str, tuple[int, int]]:
    """Path -> (mtime_ns, size) for every report file, superseded/ included."""
    state: dict[str, tuple[int, int]] = {}
    if not reports.is_dir():
        return state
    for path in reports.rglob("*"):
        if path.is_file() and path.suffix in REPORT_SUFFIXES and path.name not in IGNORED:
            stat = path.stat()
            state[str(path.relative_to(reports))] = (stat.st_mtime_ns, stat.st_size)
    return state


def changed(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    """Paths added, removed or modified between two snapshots, sorted."""
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


class ReportWatcher:
    """Polls reports/ and bumps a version number whenever a report file changes."""

    def __init__(self, reports: Path, interval: float = 1.0) -> None:
        self.reports = reports
        self.interval = interval
        self.version = 0
        self.last_changed: list[str] = []
        self._state = snapshot(reports)
        self._cond = threading.Condition()
        self._stop = threading.Event()

    def poll_once(self) -> bool:
        current = snapshot(self.reports)
        diff = changed(self._state, current)
        if not diff:
            return False
        with self._cond:
            self._state = current
            self.version += 1
            self.last_changed = diff
            self._cond.notify_all()
        return True

    def run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def wait(self, seen: int, timeout: float) -> int:
        with self._cond:
            self._cond.wait_for(lambda: self.version != seen or self._stop.is_set(), timeout)
            return self.version

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()


def payload_json(reports: Path) -> tuple[int, bytes]:
    try:
        body = {"ok": True, "payload": build_payload(reports)}
        status = HTTPStatus.OK
    except CanvasError as exc:
        body = {"ok": False, "error": str(exc)}
        status = HTTPStatus.OK  # the page shows the message; not a server fault
    except Exception as exc:  # a half-written report must not kill the page
        body = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        status = HTTPStatus.OK
    return status, json.dumps(body, ensure_ascii=False).encode()


def make_handler(reports: Path, watcher: ReportWatcher, page: Path = PAGE, heartbeat: float = 15.0):
    class Handler(BaseHTTPRequestHandler):
        server_version = "prlab-live"

        def log_message(self, format: str, *args) -> None:  # keep the terminal quiet
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0]
            if route in ("/", "/index.html"):
                self._send(HTTPStatus.OK, page.read_bytes(), "text/html; charset=utf-8")
            elif route == "/api/payload":
                status, body = payload_json(reports)
                self._send(status, body, "application/json; charset=utf-8")
            elif route == "/api/events":
                self._events()
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

        def _events(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            seen = watcher.version
            try:
                self.wfile.write(f"event: hello\ndata: {seen}\n\n".encode())
                self.wfile.flush()
                while not watcher._stop.is_set():
                    version = watcher.wait(seen, heartbeat)
                    if version != seen:
                        seen = version
                        data = json.dumps({"version": version, "changed": watcher.last_changed[:20]})
                        self.wfile.write(f"event: reports\ndata: {data}\n\n".encode())
                    else:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def serve(host: str, port: int, reports: Path, interval: float = 1.0, open_browser: bool = False) -> None:
    watcher = ReportWatcher(reports, interval)
    threading.Thread(target=watcher.run, daemon=True).start()
    server = ThreadingHTTPServer((host, port), make_handler(reports, watcher))
    server.daemon_threads = True
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"prlab live canvas: {url}  (watching {reports})", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live local view of the comparison canvas")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reports", type=Path, default=REPORT_DIR, help="reports folder to follow")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between folder scans")
    parser.add_argument("--open", action="store_true", help="open the page in a browser")
    args = parser.parse_args(argv)
    serve(args.host, args.port, args.reports, args.interval, args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
