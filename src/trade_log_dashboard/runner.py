"""One local backtest at a time, with polling, cancellation and downloadable outputs."""
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4
import zipfile
from .datasets import resolve_dataset

MAX_ARCHIVE_BYTES = 2 * 1024**3
SINGLE_RUN_TIMEOUT = 1800
SWEEP_RUN_TIMEOUT = 7 * 24 * 60 * 60
HISTORY_ROOT = Path(__file__).resolve().parents[2] / "history"
HISTORY_ARTIFACTS = {
    "trades.csv", "trades.csv.manifest.json", "equity.csv", "run.log"
}


def _declared_run_mode(source):
    """Read a literal RUN_MODE without importing or executing uploaded code."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return "single"
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "RUN_MODE" for target in targets):
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    return "single"
                return value if value in {"single", "sweep"} else "single"
    return "single"


def extract_market_data(archive, destination):
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > 20000 or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
            raise ValueError("Data ZIP exceeds 20,000 files or 2 GiB expanded. Use a local data folder instead.")
        for member in members:
            name = member.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or ":" in name or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Data ZIP contains an unsafe path or symbolic link.")
            target = destination.joinpath(*path.parts).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Data ZIP path escapes its destination.")
        bundle.extractall(destination)
    # Accept the common ZIP containing one wrapper folder.
    root = destination
    while True:
        items = list(root.iterdir())
        if len(items) == 1 and items[0].is_dir():
            root = items[0]
        else:
            return root


class LocalRunner:
    def __init__(self, history_root=None, dataset_root=None, dataset_cache_path=None):
        self.lock = threading.Lock()
        self.jobs = {}
        self.storage = tempfile.TemporaryDirectory(prefix="local-backtests-")
        self.history_root = Path(history_root or HISTORY_ROOT).resolve()
        self.history_root.mkdir(parents=True, exist_ok=True)
        self.dataset_root = Path(dataset_root).resolve() if dataset_root else None
        self.dataset_cache_path = Path(dataset_cache_path).resolve() if dataset_cache_path else None

    def has_running_job(self):
        with self.lock:
            return any(job["status"] == "running" for job in self.jobs.values())

    def set_dataset_root(self, dataset_root, dataset_cache_path=None, persist=None):
        with self.lock:
            if any(job["status"] == "running" for job in self.jobs.values()):
                raise ValueError("Wait for the active backtest to finish before changing the dataset folder.")
            if persist:
                persist()
            self.dataset_root = Path(dataset_root).resolve()
            self.dataset_cache_path = Path(dataset_cache_path).resolve() if dataset_cache_path else None

    def _launch(self, folder, timeout):
        env = os.environ.copy()
        paths = [str(Path(__file__).resolve().parents[1])] + [
            str(Path(path).resolve()) for path in sys.path if path and Path(path).exists()
        ]
        env["PYTHONPATH"] = os.pathsep.join(paths)
        env["PYTHONIOENCODING"] = "utf-8"
        command = [sys.executable, "-u", "-m", "trade_log_dashboard.worker", str(folder)]
        with (folder / "run.log").open("wb") as log:
            process = subprocess.Popen(
                command,
                cwd=folder,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=os.name != "nt",
            )
        return dict(
            folder=folder,
            process=process,
            status="running",
            started=time.monotonic(),
            timeout=timeout,
        )

    def start(self, fields, files):
        with self.lock:
            if any(j["status"] == "running" for j in self.jobs.values()):
                raise ValueError("A backtest is already running. Wait for it or cancel it first.")
            if len(self.jobs) >= 5:
                oldest = next(iter(self.jobs))
                shutil.rmtree(self.jobs.pop(oldest)["folder"])
            identifier = uuid4().hex
            folder = Path(self.storage.name, identifier)
            (folder / "code").mkdir(parents=True)
            try:
                entrypoint = fields.get("entrypoint", "")
                strategy_files = [part for part in files if part[0] == "strategy"]
                if len(strategy_files) != 1:
                    raise ValueError("Choose exactly one Python strategy file.")
                names = set()
                for _, name, content in strategy_files:
                    if Path(name).name != name or not name.endswith(".py") or not name.isascii() or "/" in name or "\\" in name or ":" in name:
                        raise ValueError("Strategy files must have simple .py filenames, without folders.")
                    if name in names or len(content) > 2 * 1024**2:
                        raise ValueError("Duplicate Python filename or Python file larger than 2 MiB.")
                    names.add(name)
                    (folder / "code" / name).write_bytes(content)
                if entrypoint not in names:
                    raise ValueError("Select the strategy entry point from the uploaded Python files.")
                mode = _declared_run_mode((folder / "code" / entrypoint).read_text(encoding="utf-8"))
                config = json.loads(fields.get("config", "{}"))
                if not isinstance(config, dict):
                    raise ValueError("Run configuration must be a JSON object.")
                archives = [part for part in files if part[0] == "market_data"]
                local_path = fields.get("market_path", "").strip()
                dataset_id = fields.get("dataset_id", "").strip()
                if sum(map(bool, (archives, local_path, dataset_id))) != 1:
                    raise ValueError("Choose one project dataset, data ZIP or local market-data path.")
                dataset_label = None
                if dataset_id:
                    market, dataset = resolve_dataset(
                        dataset_id, self.dataset_root, self.dataset_cache_path
                    )
                    dataset_label = dataset["label"]
                    config["period"] = {
                        "start_date": dataset["start_date"],
                        "end_date": dataset["end_date"],
                    }
                    instrument = config.get("instrument")
                    if instrument is None:
                        instrument = {}
                    if not isinstance(instrument, dict):
                        raise ValueError("Run configuration instrument must be a JSON object.")
                    config["instrument"] = {**instrument, "symbol": dataset["symbol"]}
                elif archives:
                    if len(archives) != 1 or not archives[0][1].lower().endswith(".zip"):
                        raise ValueError("Market-data upload must be one ZIP file.")
                    archive = folder / "market.zip"
                    archive.write_bytes(archives[0][2])
                    market = folder / "market"
                    market.mkdir()
                    market = extract_market_data(archive, market)
                else:
                    market = Path(local_path).expanduser()
                    if not market.is_absolute() or not market.exists():
                        raise ValueError("Local market-data path must be an existing absolute file or folder path.")
                request = dict(entrypoint=entrypoint, market_data=str(market), config=config, run_id=f"local-{identifier}", dataset_id=dataset_id, dataset_label=dataset_label, dataset=dataset if dataset_id else None)
                (folder / "request.json").write_text(json.dumps(request), encoding="utf-8")
                timeout = SWEEP_RUN_TIMEOUT if mode == "sweep" else SINGLE_RUN_TIMEOUT
                self.jobs[identifier] = self._launch(folder, timeout)
                threading.Thread(target=self._watch, args=(identifier,), daemon=True).start()
                return identifier
            except Exception:
                shutil.rmtree(folder)
                raise

    def start_iteration(self, identifier, index, *, save_history=False):
        """Rerun one optimizer row and retain full artifacts for that selection."""
        with self.lock:
            if any(job["status"] == "running" for job in self.jobs.values()):
                raise ValueError("A backtest is already running. Wait for it or cancel it first.")
            parent = self.jobs[identifier]
            if parent["status"] != "succeeded" or not str(index).isdigit():
                raise ValueError("The optimizer run or selected combination is unavailable.")
            summary_path = parent["folder"] / "iterations" / str(int(index)) / "summary.json"
            if not summary_path.is_file():
                raise ValueError("The selected combination is unavailable.")
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if summary.get("status") != "succeeded" or not summary.get("metrics"):
                raise ValueError("Choose a combination that completed with trades.")
            sweep = parent.get("result", {}).get("sweep", {})
            ranked_summary = next(
                (item for item in sweep.get("iterations", []) if item.get("index") == int(index)),
                summary,
            )

            new_identifier = uuid4().hex
            folder = Path(self.storage.name, new_identifier)
            (folder / "code").mkdir(parents=True)
            shutil.copytree(parent["folder"] / "code", folder / "code", dirs_exist_ok=True)
            request = json.loads((parent["folder"] / "request.json").read_text(encoding="utf-8"))
            request["run_id"] = f"local-{new_identifier}"
            request["run_mode_override"] = "single"
            request["optimizer_parent_id"] = identifier
            request["optimizer_iteration"] = int(index)
            request["optimizer_expected_metrics"] = summary["metrics"]
            request.setdefault("config", {})["parameters"] = summary["parameters"]
            (folder / "request.json").write_text(json.dumps(request), encoding="utf-8")

            if len(self.jobs) >= 5:
                oldest = next(key for key in self.jobs if key != identifier)
                shutil.rmtree(self.jobs.pop(oldest)["folder"])
            self.jobs[new_identifier] = self._launch(folder, SINGLE_RUN_TIMEOUT)
            self.jobs[new_identifier]["save_history"] = bool(save_history)
            self.jobs[new_identifier]["history_parent_id"] = f"{identifier}s{int(index)}"
            self.jobs[new_identifier]["history_summary"] = ranked_summary
            threading.Thread(target=self._watch, args=(new_identifier,), daemon=True).start()
            return new_identifier

    def _history_folder(self, identifier):
        if not identifier or not identifier.isascii() or not identifier.isalnum():
            raise KeyError(identifier)
        folder = (self.history_root / identifier).resolve()
        if not folder.is_relative_to(self.history_root):
            raise KeyError(identifier)
        return folder

    def _save_history(self, identifier, job):
        result = job.get("result", {})
        analysis = result.get("analysis")
        if not isinstance(analysis, dict):
            raise ValueError("Completed combination has no analytics to save.")
        history_id = job["history_parent_id"]
        summary = job["history_summary"]
        temporary = self.history_root / f".{history_id}-{uuid4().hex}.tmp"
        target = self._history_folder(history_id)
        temporary.mkdir(parents=True)
        try:
            artifacts = []
            for name in sorted(HISTORY_ARTIFACTS):
                source = job["folder"] / name
                if source.is_file():
                    shutil.copy2(source, temporary / name)
                    artifacts.append(name)
            (temporary / "analysis.json").write_text(
                json.dumps(analysis, allow_nan=False), encoding="utf-8"
            )
            metadata = {
                "id": history_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "strategy": analysis.get("overview", {}).get("strategy", "Strategy"),
                "dataset": analysis.get("dataset"),
                "parameters": summary.get("parameters", {}),
                "rank": summary.get("rank"),
                "selection_score": summary.get("metrics", {}).get("selection_score"),
                "metrics": summary.get("metrics", {}),
                "artifacts": artifacts,
            }
            (temporary / "metadata.json").write_text(
                json.dumps(metadata, allow_nan=False), encoding="utf-8"
            )
            if target.exists():
                shutil.rmtree(target)
            temporary.replace(target)
            job["history_id"] = history_id
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def history(self):
        records = []
        with self.lock:
            for folder in self.history_root.iterdir():
                metadata_path = folder / "metadata.json"
                if not folder.is_dir() or not metadata_path.is_file():
                    continue
                try:
                    records.append(json.loads(metadata_path.read_text(encoding="utf-8")))
                except (OSError, ValueError, TypeError):
                    continue
        records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return records

    def history_item(self, identifier):
        with self.lock:
            folder = self._history_folder(identifier)
            metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
            metadata["analysis"] = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
            return metadata

    def history_artifact(self, identifier, name):
        if name not in HISTORY_ARTIFACTS:
            raise KeyError(name)
        with self.lock:
            path = self._history_folder(identifier) / name
            if not path.is_file():
                raise KeyError(name)
            return path

    def _stop(self, job):
        process = job["process"]
        if process.poll() is None:
            if os.name == "nt":
                stopped = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)
                if stopped.returncode and process.poll() is None:
                    # Restricted Windows environments may deny tree enumeration.
                    # The handle of our own worker can still terminate that process.
                    process.kill()
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)

    def _watch(self, identifier):
        job = self.jobs[identifier]
        try:
            job["process"].wait(timeout=job["timeout"])
        except subprocess.TimeoutExpired:
            self._stop(job)
            if job["timeout"] == SWEEP_RUN_TIMEOUT:
                job["error"] = "Sweep exceeded the 7-day limit. Reduce the combinations or dataset and retry."
            else:
                job["error"] = "Backtest exceeded the 30-minute limit. Reduce the date range and retry."
        with self.lock:
            job["finished"] = time.monotonic()
            if job["status"] == "cancelled":
                return
            result_path = job["folder"] / "result.json"
            try:
                result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
                job["status"] = result.get("status", "failed") if not job.get("error") else "failed"
                job["error"] = job.get("error") or result.get("error", "Strategy process exited without a result.")
                job["result"] = result
                if job["status"] == "succeeded" and job.get("save_history"):
                    try:
                        self._save_history(identifier, job)
                    except Exception as exc:
                        job["history_error"] = f"Saved-history result could not be saved: {exc}"
            except Exception as exc:
                job.update(status="failed", error=f"Could not read backtest result: {exc}")

    def status(self, identifier):
        with self.lock:
            job = self.jobs[identifier]
            with (job["folder"] / "run.log").open("rb") as handle:
                handle.seek(max(0, handle.seek(0, 2) - 12000))
                log = handle.read().decode("utf-8", errors="replace")
            elapsed = int(job.get("finished", time.monotonic()) - job["started"])
            progress = None
            progress_path = job["folder"] / "progress.json"
            if progress_path.is_file():
                try:
                    progress = json.loads(progress_path.read_text(encoding="utf-8"))
                    completed = progress.get("completed", 0)
                    total = progress.get("total", 0)
                    progress_elapsed = progress.get("elapsed_seconds", elapsed)
                    progress["percentage"] = (
                        round(completed / total * 100, 1) if total else 0.0
                    )
                    progress["average_per_second"] = (
                        completed / progress_elapsed if completed and progress_elapsed > 0 else None
                    )
                    if progress.get("mode") == "sweep":
                        durations = progress.get("completed_combination_seconds") or []
                        average = sum(durations) / len(durations) if durations else None
                        current_elapsed = progress.get("combination_elapsed_seconds", 0.0)
                        current_completed = progress.get("combination_completed", 0)
                        current_total = progress.get("combination_total", 0)
                        current_remaining = (
                            current_elapsed / current_completed * (current_total - current_completed)
                            if current_completed and current_total >= current_completed else None
                        )
                        future_count = max(0, total - completed - (1 if progress.get("current") else 0))
                        if average is not None:
                            active_remaining = (
                                current_remaining if current_remaining is not None
                                else max(0.0, average - current_elapsed)
                            ) if progress.get("current") else 0.0
                            overall_remaining = active_remaining + average * future_count
                        elif current_remaining is not None:
                            projected_duration = current_elapsed + current_remaining
                            overall_remaining = current_remaining + projected_duration * future_count
                        else:
                            overall_remaining = None
                        progress["average_combination_seconds"] = average
                        progress["current_estimated_remaining_seconds"] = current_remaining
                        progress["current_percentage"] = (
                            round(current_completed / current_total * 100, 1)
                            if current_total else None
                        )
                        progress["estimated_remaining_seconds"] = (
                            max(0, round(overall_remaining))
                            if overall_remaining is not None else None
                        )
                    else:
                        progress["estimated_remaining_seconds"] = (
                            max(0, round(progress_elapsed / completed * (total - completed)))
                            if completed and total >= completed else None
                        )
                except (OSError, ValueError, TypeError):
                    progress = None
            return dict(id=identifier, status=job["status"], elapsed_seconds=elapsed, progress=progress,
                        log=log, error=job.get("error") if job["status"] == "failed" else None,
                        history_id=job.get("history_id"), history_error=job.get("history_error"),
                        result=job.get("result") if job["status"] in {"succeeded", "empty"} else None)

    def cancel(self, identifier):
        with self.lock:
            job = self.jobs[identifier]
            if job["status"] == "running":
                self._stop(job)
                job["status"] = "cancelled"
                job["finished"] = time.monotonic()

    def artifact(self, identifier, name):
        if name not in {"trades.csv", "trades.csv.manifest.json", "equity.csv", "run.log"}:
            raise KeyError(name)
        job = self.jobs[identifier]
        if job["status"] not in {"succeeded", "empty", "failed", "cancelled"}:
            raise KeyError(name)
        path = job["folder"] / name
        if not path.is_file():
            raise KeyError(name)
        return path

    def iteration(self, identifier, index):
        job = self.jobs[identifier]
        if job["status"] != "succeeded" or not str(index).isdigit():
            raise KeyError(index)
        path = job["folder"] / "iterations" / str(int(index)) / "summary.json"
        if not path.is_file():
            raise KeyError(index)
        return json.loads(path.read_text(encoding="utf-8"))

    def iteration_artifact(self, identifier, index, name):
        if name not in {"trades.csv", "trades.csv.manifest.json", "equity.csv"} or not str(index).isdigit():
            raise KeyError(name)
        job = self.jobs[identifier]
        if job["status"] != "succeeded":
            raise KeyError(name)
        path = job["folder"] / "iterations" / str(int(index)) / name
        if not path.is_file():
            raise KeyError(name)
        return path

    def close(self):
        for job in self.jobs.values():
            self._stop(job)
        self.storage.cleanup()
