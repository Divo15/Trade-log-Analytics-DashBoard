"""Small local HTTP server for the trade-log dashboard."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import tempfile
from typing import Any
import webbrowser

import duckdb

from trade_log_exporter import TradeLogError

from .analytics import analyze_trade_log


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
STATIC_ROOT = files("trade_log_dashboard").joinpath("static")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "TradeDashboard/0.1"

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        asset_name = "index.html" if self.path in {"/", "/index.html"} else self.path.lstrip("/")
        if asset_name not in {"index.html", "app.css", "app.js"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        asset = STATIC_ROOT.joinpath(asset_name)
        body = asset.read_bytes()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }[Path(asset_name).suffix]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/analyze":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Choose a CSV file first."})
            return
        if length > MAX_UPLOAD_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "CSV exceeds the 25 MB limit."})
            return
        payload = self.rfile.read(length)
        try:
            with tempfile.TemporaryDirectory(prefix="trade-dashboard-") as temporary:
                csv_path = Path(temporary, "trades.csv")
                csv_path.write_bytes(payload)
                result = analyze_trade_log(csv_path)
            self._json(HTTPStatus.OK, result)
        except (TradeLogError, ValueError, duckdb.Error) as exc:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
        except Exception:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "The file could not be analyzed. Check the server terminal for details."},
            )
            raise

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local trade-log analytics dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://{args.host}:{args.port}"
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Trade-log dashboard running at {url}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
