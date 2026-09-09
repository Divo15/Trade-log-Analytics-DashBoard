"""Per-user writable storage and portable dataset configuration."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4


APP_FOLDER = "TradeLogAnalytics"
SETTINGS_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def default_user_root() -> Path:
    override = os.environ.get("TRADE_LOG_ANALYTICS_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_FOLDER
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_FOLDER
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "trade-log-analytics"


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class StorageLayout:
    root: Path
    settings_file: Path
    logs_root: Path
    results_root: Path
    cache_root: Path
    dataset_root: Path

    @property
    def dataset_cache_file(self) -> Path:
        return self.cache_root / "dataset-coverage.json"

    @property
    def application_log(self) -> Path:
        return self.logs_root / "application.log"

    def public_settings(self) -> dict[str, str]:
        return {
            "dataset_root": str(self.dataset_root),
            "settings_file": str(self.settings_file),
            "logs_root": str(self.logs_root),
            "results_root": str(self.results_root),
        }

    def set_dataset_root(self, value: str | Path) -> None:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise ValueError("Choose an absolute dataset folder path.")
        candidate = candidate.resolve()
        if not candidate.is_dir():
            raise ValueError("The selected dataset folder does not exist or is not a folder.")
        self.dataset_root = candidate
        _atomic_json(
            self.settings_file,
            {"version": SETTINGS_VERSION, "dataset_root": str(self.dataset_root)},
        )


def _saved_dataset_root(settings_file: Path) -> Path | None:
    try:
        payload = json.loads(settings_file.read_text(encoding="utf-8"))
        value = payload.get("dataset_root") if payload.get("version") == SETTINGS_VERSION else None
        return Path(value).expanduser().resolve() if isinstance(value, str) and value else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _copy_legacy_results(source: Path, target: Path) -> None:
    if not source.is_dir():
        return
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir() and not destination.exists():
            try:
                shutil.copytree(item, destination)
            except OSError:
                continue


def _verify_writable(folder: Path) -> None:
    probe = folder / f".write-test-{uuid4().hex}"
    try:
        probe.write_bytes(b"")
    except OSError as exc:
        raise RuntimeError(f"The application cannot write to {folder}: {exc}") from exc
    finally:
        probe.unlink(missing_ok=True)


def load_storage(*, user_root: str | Path | None = None, project_root: str | Path | None = None) -> StorageLayout:
    root = Path(user_root).expanduser().resolve() if user_root else default_user_root()
    project = Path(project_root).resolve() if project_root else PROJECT_ROOT
    settings_root = root / "settings"
    logs_root = root / "logs"
    results_root = root / "results"
    cache_root = root / "cache"
    for folder in (settings_root, logs_root, results_root, cache_root):
        folder.mkdir(parents=True, exist_ok=True)
        _verify_writable(folder)

    settings_file = settings_root / "settings.json"
    environment_dataset = os.environ.get("TRADE_LOG_DATA_ROOT")
    saved_dataset = _saved_dataset_root(settings_file)
    legacy_dataset = project / "data" / "db"
    if environment_dataset:
        dataset_root = Path(environment_dataset).expanduser().resolve()
    elif saved_dataset:
        dataset_root = saved_dataset
    elif legacy_dataset.is_dir():
        dataset_root = legacy_dataset
    else:
        dataset_root = root / "datasets"
        dataset_root.mkdir(parents=True, exist_ok=True)

    layout = StorageLayout(root, settings_file, logs_root, results_root, cache_root, dataset_root)
    if not settings_file.exists():
        layout.set_dataset_root(dataset_root)
    _copy_legacy_results(project / "history", results_root)
    legacy_cache = legacy_dataset / ".dataset-coverage.json"
    if legacy_cache.is_file() and not layout.dataset_cache_file.exists():
        try:
            shutil.copy2(legacy_cache, layout.dataset_cache_file)
        except OSError:
            pass
    return layout


def configure_logging(layout: StorageLayout) -> logging.Logger:
    logger = logging.getLogger("trade_log_dashboard")
    target = str(layout.application_log.resolve())
    if not any(getattr(handler, "baseFilename", None) == target for handler in logger.handlers):
        handler = RotatingFileHandler(target, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def close_logging(layout: StorageLayout) -> None:
    logger = logging.getLogger("trade_log_dashboard")
    target = str(layout.application_log.resolve())
    for handler in list(logger.handlers):
        if getattr(handler, "baseFilename", None) == target:
            logger.removeHandler(handler)
            handler.close()
