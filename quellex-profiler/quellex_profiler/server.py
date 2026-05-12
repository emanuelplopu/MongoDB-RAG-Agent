"""Stdlib local HTTP server that serves the UI and the /api/report JSON.

No Flask/FastAPI dependency. Uses :class:`http.server.ThreadingHTTPServer`
so multiple static asset requests can proceed concurrently with the initial
profile scan.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from quellex_profiler.schema import ProfilerReport


def _static_bytes(name: str) -> tuple[bytes, str] | None:
    """Return ``(body, content_type)`` for a bundled UI asset or None."""
    try:
        pkg = resources.files("quellex_profiler").joinpath("ui")
        target = pkg.joinpath(name)
        if not target.is_file():
            return None
        data = target.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        return None
    ctype, _ = mimetypes.guess_type(name)
    return data, (ctype or "application/octet-stream")


def make_handler(report_provider):
    """Return a request handler class bound to the given report provider."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            path = self.path.split("?", 1)[0]

            if path in ("/", "/index.html"):
                asset = _static_bytes("index.html")
                if asset is None:
                    self._send(HTTPStatus.NOT_FOUND, b"index.html missing", "text/plain")
                    return
                self._send(HTTPStatus.OK, asset[0], asset[1])
                return

            if path.startswith("/static/"):
                rel = path[len("/static/"):]
                asset = _static_bytes(rel)
                if asset is None:
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                    return
                self._send(HTTPStatus.OK, asset[0], asset[1])
                return

            if path == "/api/report":
                report: ProfilerReport = report_provider()
                body = json.dumps(report.to_dict()).encode("utf-8")
                self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
                return

            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            # Silence default logging to stderr in client demos.
            return

    return Handler


def serve(
    report: ProfilerReport,
    host: str = "127.0.0.1",
    port: int = 17645,
    open_browser: bool = True,
) -> None:
    """Serve the dashboard on ``host:port``. Blocks until Ctrl+C."""
    handler = make_handler(lambda: report)
    server = ThreadingHTTPServer((host, port), handler)

    url = f"http://{host}:{port}/"
    print(f"[quellex-profiler] serving at {url}")
    print("[quellex-profiler] press Ctrl+C to exit")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[quellex-profiler] shutting down")
    finally:
        server.server_close()
