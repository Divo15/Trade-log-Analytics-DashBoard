"""Small local HTTP server for the trade-log dashboard."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import logging
from pathlib import Path
import tempfile
from typing import Any
import webbrowser
from email.parser import BytesParser
from email.policy import default
from urllib.parse import urlsplit
import zipfile

import duckdb

from trade_log_exporter import TradeLogError

from .analytics import analyze_trade_log
from .equity import analyze_equity
from .runner import LocalRunner
from .datasets import catalog
from .storage import StorageLayout, close_logging, configure_logging, load_storage
from .sweep_schema import export_ranked_sweep, inspect_sweep_parquet, preview_sweep_filters, rank_sweep_parquet


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
STATIC_ROOT = files("trade_log_dashboard").joinpath("static")
MAX_BACKTEST_BYTES = 256 * 1024 * 1024
MAX_SWEEP_PARQUET_BYTES = 256 * 1024 * 1024
MAX_SWEEP_EXPORT_BYTES = 5 * 1024 * 1024
LOGGER = logging.getLogger("trade_log_dashboard.server")


class DashboardServer(ThreadingHTTPServer):
    """Local-only HTTP server with request threads that cannot delay shutdown."""

    daemon_threads = True

    def __init__(self, server_address, handler_class, *, storage=None, runner=None):
        self.storage: StorageLayout = storage or load_storage()
        configure_logging(self.storage)
        self.runner = runner or LocalRunner(
            history_root=self.storage.results_root,
            dataset_root=self.storage.dataset_root,
            dataset_cache_path=self.storage.dataset_cache_file,
        )
        super().__init__(server_address, handler_class)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "TradeDashboard/0.1"

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        request_path = urlsplit(self.path).path
        if request_path == "/api/datasets":
            self._json(HTTPStatus.OK, {"datasets": catalog(
                self.server.storage.dataset_root,
                self.server.storage.dataset_cache_file,
            )})
            return
        if request_path == "/api/settings":
            if not self._local_request():
                return
            self._json(HTTPStatus.OK, self.server.storage.public_settings())
            return
        if request_path.startswith("/api/history"):
            if not self._local_request():
                return
            parts = request_path.strip("/").split("/")
            try:
                if len(parts) == 2:
                    self._json(HTTPStatus.OK, {"history": self.server.runner.history()})
                elif len(parts) == 3:
                    self._json(HTTPStatus.OK, self.server.runner.history_item(parts[2]))
                elif len(parts) == 4:
                    path = self.server.runner.history_artifact(parts[2], parts[3])
                    body = path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    raise KeyError(request_path)
            except (KeyError, FileNotFoundError, ValueError, json.JSONDecodeError):
                self._json(HTTPStatus.NOT_FOUND, {"error": "Saved result was not found."})
            return
        if request_path.startswith("/api/backtests/"):
            if not self._local_request():
                return
            parts = request_path.strip("/").split("/")
            try:
                if len(parts) == 3:
                    self._json(HTTPStatus.OK, self.server.runner.status(parts[2]))
                elif len(parts) == 5 and parts[3] == "iterations":
                    self._json(HTTPStatus.OK, self.server.runner.iteration(parts[2], parts[4]))
                elif len(parts) == 6 and parts[3] == "iterations":
                    path = self.server.runner.iteration_artifact(parts[2], parts[4], parts[5])
                    body = path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif len(parts) == 4:
                    path = self.server.runner.artifact(parts[2], parts[3])
                    body = path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    raise KeyError(request_path)
            except KeyError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Run or output no longer available. Runs are retained for this server session (latest five)."})
            return
        if request_path == "/api/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if request_path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        asset_name = "index.html" if request_path in {"/", "/index.html"} else request_path.lstrip("/")
        if asset_name not in {"index.html", "app.css", "app.js", "runner.js"}:
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
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/settings":
            self._settings()
            return
        if self.path == "/api/sweep-schema":
            self._sweep_schema()
            return
        if self.path == "/api/sweep-preview":
            self._sweep_preview()
            return
        if self.path == "/api/sweep-rank":
            self._sweep_rank()
            return
        if self.path == "/api/sweep-export":
            self._sweep_export()
            return
        if self.path.startswith("/api/backtests"):
            self._backtest()
            return
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
                equity_text = None
                if self.headers.get_content_type() == "application/json":
                    bundle = json.loads(payload)
                    if not isinstance(bundle, dict) or not isinstance(bundle.get("trades_csv"), str) or not isinstance(bundle.get("equity_csv"), str):
                        raise ValueError("Supply trades_csv and equity_csv as CSV text")
                    csv_path.write_text(bundle["trades_csv"], encoding="utf-8")
                    equity_text = bundle["equity_csv"]
                else:
                    csv_path.write_bytes(payload)
                result = analyze_trade_log(csv_path)
                if equity_text is not None:
                    equity_path = Path(temporary, "equity.csv")
                    equity_path.write_text(equity_text, encoding="utf-8")
                    result["intraday"] = analyze_equity(equity_path, csv_path)
            self._json(HTTPStatus.OK, result)
        except (TradeLogError, ValueError, duckdb.Error) as exc:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
        except Exception:
            LOGGER.exception("CSV analysis failed")
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "The file could not be analyzed. Check the server terminal for details."},
            )
            raise

    def _sweep_schema(self) -> None:
        if not self._local_request():
            return
        if self.headers.get("X-Local-Runner") != "1":
            self._json(HTTPStatus.FORBIDDEN, {"error": "Inspect sweep files from this local dashboard."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Choose a Parquet sweep file first."})
            return
        if length > MAX_SWEEP_PARQUET_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Parquet sweep exceeds the 256 MiB limit."})
            return
        try:
            self.connection.settimeout(180)
            with tempfile.TemporaryDirectory(prefix="sweep-schema-") as temporary:
                parquet_path = Path(temporary, "sweep.parquet")
                remaining = length
                with parquet_path.open("wb") as destination:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Upload was interrupted. Choose the Parquet file and retry.")
                        destination.write(chunk)
                        remaining -= len(chunk)
                result = inspect_sweep_parquet(parquet_path)
                supplied_name = self.headers.get("X-File-Name", "").strip()
                if supplied_name:
                    result["file"]["name"] = Path(supplied_name).name
            self._json(HTTPStatus.OK, result)
        except (ValueError, OSError, duckdb.Error) as exc:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
        except Exception:
            LOGGER.exception("Parquet schema inspection failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "The Parquet schema could not be inspected. Check the server terminal for details."
            })

    def _sweep_preview(self) -> None:
        if not self._local_request():
            return
        if self.headers.get("X-Local-Runner") != "1":
            self._json(HTTPStatus.FORBIDDEN, {"error": "Preview sweep filters from this local dashboard."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            mapping = json.loads(self.headers.get("X-Sweep-Mapping", "{}"))
            filters = json.loads(self.headers.get("X-Sweep-Filters", "{}"))
            if not isinstance(mapping, dict) or not isinstance(filters, dict):
                raise ValueError("Sweep mapping and filters must be objects.")
        except (ValueError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Sweep filter request is invalid."})
            return
        if length <= 0 or length > MAX_SWEEP_PARQUET_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Choose a Parquet sweep up to 256 MiB."})
            return
        try:
            self.connection.settimeout(300)
            with tempfile.TemporaryDirectory(prefix="sweep-preview-") as temporary:
                parquet_path = Path(temporary, "sweep.parquet")
                remaining = length
                with parquet_path.open("wb") as destination:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Upload was interrupted. Choose the Parquet file and retry.")
                        destination.write(chunk)
                        remaining -= len(chunk)
                self._json(HTTPStatus.OK, preview_sweep_filters(parquet_path, mapping, filters))
        except (ValueError, OSError, duckdb.Error) as exc:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
        except Exception:
            LOGGER.exception("Sweep filter preview failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "The sweep filters could not be applied. Check the server terminal for details."
            })

    def _sweep_rank(self) -> None:
        if not self._local_request():
            return
        if self.headers.get("X-Local-Runner") != "1":
            self._json(HTTPStatus.FORBIDDEN, {"error": "Rank sweep files from this local dashboard."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            mapping = json.loads(self.headers.get("X-Sweep-Mapping", "{}"))
            filters = json.loads(self.headers.get("X-Sweep-Filters", "{}"))
            ranking = json.loads(self.headers.get("X-Sweep-Ranking", "null"))
            require_robustness = json.loads(self.headers.get("X-Sweep-Robustness", "false"))
            if not isinstance(mapping, dict) or not isinstance(filters, dict):
                raise ValueError("Sweep mapping and filters must be objects.")
            if ranking is not None and not isinstance(ranking, list):
                raise ValueError("Sweep ranking criteria must be a list.")
            if not isinstance(require_robustness, bool):
                raise ValueError("Entry-time robustness selection must be true or false.")
        except (ValueError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Sweep ranking request is invalid."})
            return
        if length <= 0 or length > MAX_SWEEP_PARQUET_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Choose a Parquet sweep up to 256 MiB."})
            return
        try:
            self.connection.settimeout(600)
            with tempfile.TemporaryDirectory(prefix="sweep-rank-") as temporary:
                parquet_path = Path(temporary, "sweep.parquet")
                remaining = length
                with parquet_path.open("wb") as destination:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Upload was interrupted. Choose the Parquet file and retry.")
                        destination.write(chunk)
                        remaining -= len(chunk)
                result = rank_sweep_parquet(
                    parquet_path, mapping, filters, top_n=20, ranking=ranking,
                    require_robustness=require_robustness,
                )
                result["run_summary"] = {
                    "input_file": Path(self.headers.get("X-Sweep-File-Name", "sweep.parquet")).name,
                    "input_size_bytes": length,
                    "input_rows": result["source_row_count"],
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "filters": filters,
                    "column_mapping": mapping,
                    "ranking": result["ranking"],
                    "entry_robustness": {
                        "threshold_ratio": 0.70,
                        "window_minutes": 20,
                        "required": require_robustness,
                        "mode": "strict_filter" if require_robustness else "annotation_only",
                    },
                }
                self._json(HTTPStatus.OK, result)
        except (ValueError, OSError, duckdb.Error) as exc:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
        except Exception:
            LOGGER.exception("Sweep ranking failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "The sweep could not be ranked. Check the server terminal for details."
            })

    def _sweep_export(self) -> None:
        if not self._local_request():
            return
        if self.headers.get("X-Local-Runner") != "1":
            self._json(HTTPStatus.FORBIDDEN, {"error": "Export ranked sweeps from this local dashboard."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_SWEEP_EXPORT_BYTES:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "The ranked export request is invalid or too large."})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            export_format = payload.get("format")
            rows = payload.get("rows")
            if not isinstance(rows, list):
                raise ValueError("Ranked rows are required for export.")
            body = export_ranked_sweep(rows, export_format)
            extension = {"csv": "csv", "parquet": "parquet", "xlsx": "xlsx"}[export_format]
            content_type = {
                "csv": "text/csv; charset=utf-8",
                "parquet": "application/vnd.apache.parquet",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }[export_format]
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="ranked_strategies.{extension}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, KeyError, TypeError, OSError, duckdb.Error) as exc:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
        except Exception:
            LOGGER.exception("Ranked sweep export failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "The ranked export could not be created. Check the server terminal for details."
            })

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s %s", self.client_address[0], format % args)

    def _settings(self):
        if not self._local_request():
            return
        if self.headers.get("X-Local-Runner") != "1":
            self._json(HTTPStatus.FORBIDDEN, {"error": "Change settings from this local dashboard."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("Dataset settings request is empty or too large.")
            payload = json.loads(self.rfile.read(length))
            dataset_root = payload.get("dataset_root") if isinstance(payload, dict) else None
            if not isinstance(dataset_root, str) or not dataset_root.strip():
                raise ValueError("Choose a dataset folder.")
            selected_root = Path(dataset_root.strip()).expanduser()
            self.server.runner.set_dataset_root(
                selected_root,
                self.server.storage.dataset_cache_file,
                persist=lambda: self.server.storage.set_dataset_root(selected_root),
            )
            self._json(HTTPStatus.OK, self.server.storage.public_settings())
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _local_request(self):
        host = self.headers.get("Host", "")
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        if self.client_address[0] != "127.0.0.1" or host not in expected or (origin and origin != f"http://{host}"):
            self._json(HTTPStatus.FORBIDDEN, {"error": "Local runner requests must come from this dashboard on localhost."})
            return False
        return True

    def _backtest(self):
        if not self._local_request():
            return
        if self.headers.get("X-Local-Runner") != "1":
            self._json(HTTPStatus.FORBIDDEN, {"error": "Start runs using the local dashboard."})
            return
        try:
            parts = self.path.strip("/").split("/")
            if len(parts) == 6 and parts[3] == "iterations" and parts[5] == "save":
                record = self.server.runner.save_combination(parts[2], parts[4])
                self._json(HTTPStatus.OK, record)
                return
            if len(parts) == 4 and parts[-1] == "resume":
                identifier = self.server.runner.resume(parts[2])
                self._json(HTTPStatus.ACCEPTED, {"id": identifier, "status": "running"})
                return
            if len(parts) == 6 and parts[3] == "iterations" and parts[5] == "run":
                save_history = (
                    self.headers.get("X-Save-History") == "1"
                    or self.headers.get("X-Save-Best") == "1"
                )
                identifier = self.server.runner.start_iteration(parts[2], parts[4], save_history=save_history)
                self._json(HTTPStatus.ACCEPTED, {"id": identifier, "status": "running"})
                return
            if len(parts) == 4 and parts[-1] == "cancel":
                self.server.runner.cancel(parts[2])
                self._json(HTTPStatus.OK, {"status": "cancelled"})
                return
            if self.path != "/api/backtests":
                raise KeyError(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BACKTEST_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Backtest upload must be nonempty and at most 256 MiB. Use a local path for larger datasets."})
                return
            if self.headers.get_content_type() != "multipart/form-data":
                raise ValueError("Upload Python files, configuration and market data using the run form.")
            self.connection.settimeout(120)
            payload = self.rfile.read(length)
            if len(payload) != length:
                raise ValueError("Upload was interrupted. Select your files and retry.")
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n" + payload)
            fields, uploads = {}, []
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                body = part.get_payload(decode=True) or b""
                if part.get_filename():
                    uploads.append((name, part.get_filename(), body))
                else:
                    fields[name] = body.decode("utf-8")
            identifier = self.server.runner.start(fields, uploads)
            self._json(HTTPStatus.ACCEPTED, {"id": identifier, "status": "running"})
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Run or endpoint not found."})
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local trade-log analytics dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("The Python runner is local-only. Use --host 127.0.0.1 or localhost.")
    url = f"http://{args.host}:{args.port}"
    server = DashboardServer((args.host, args.port), DashboardHandler)
    actual_port = int(server.server_address[1])
    url = f"http://{args.host}:{actual_port}"
    print(f"Trade-log dashboard running at {url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
        server.runner.close()
        close_logging(server.storage)


if __name__ == "__main__":
    main()
