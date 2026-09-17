"""Explicit project dataset selection; never substitute another expiry dataset."""
import json
from pathlib import Path
from threading import Lock
from uuid import uuid4

import duckdb

DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "db"
CACHE_VERSION = 2
CACHE_FILENAME = ".dataset-coverage.json"
CACHE_LOCK = Lock()
DATASETS = {
    "weekly": {
        "label": "NIFTY · current weekly expiry", "folder": "nifty current week",
        "symbol": "NIFTY", "summary": "nifty_summary.parquet", "chain": "nifty_chain",
    },
    "next-weekly": {
        "label": "NIFTY · next weekly expiry", "folder": "next week",
        "symbol": "NIFTY", "summary": "nifty_summary.parquet", "chain": "nifty_chain",
    },
    "monthly": {
        "label": "NIFTY · monthly expiry", "folder": "nifty monthly",
        "symbol": "NIFTY", "summary": "nifty_summary.parquet", "chain": "nifty_chain",
    },
    "next2week": {
        "label": "NIFTY · two weekly expiries ahead", "folder": "next2week",
        "symbol": "NIFTY", "summary": "nifty_summary.parquet", "chain": "nifty_chain",
    },
    "sensex-weekly": {
        "label": "SENSEX · current weekly expiry", "folder": "sensex current week",
        "symbol": "SENSEX", "summary": "sensex_summary.parquet", "chain": "sensex_chain",
    },
}


def _coverage(summary_path, chain_glob, summary_signature, chain_signature):
    """Return the time range where both summary and option-chain data exist."""
    del summary_signature, chain_signature
    connection = duckdb.connect(":memory:")
    try:
        summary = connection.execute(
            "SELECT MIN(TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S')), "
            "MAX(TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S')) FROM READ_PARQUET(?)",
            [summary_path],
        ).fetchone()
        chain = connection.execute(
            "SELECT MIN(TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S')), "
            "MAX(TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S')) FROM READ_PARQUET(?)",
            [chain_glob],
        ).fetchone()
        if None in (*summary, *chain):
            raise ValueError("summary or chain has no valid timestamps")
        start, end = max(summary[0], chain[0]), min(summary[1], chain[1])
        if start > end:
            raise ValueError("summary and chain date ranges do not overlap")
        trading_days = connection.execute(
            "SELECT COUNT(DISTINCT CAST(TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE)) "
            "FROM READ_PARQUET(?) WHERE TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') "
            "BETWEEN ? AND ?",
            [summary_path, start, end],
        ).fetchone()[0]
        return start.date().isoformat(), end.date().isoformat(), trading_days
    finally:
        connection.close()


def _cache_path(data_root=None, cache_path=None):
    return Path(cache_path) if cache_path else Path(data_root or DATA_ROOT) / CACHE_FILENAME


def _file_signature(path, root):
    stat = path.stat()
    return {
        "path": path.relative_to(root).as_posix(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _dataset_signature(root, summary, chains):
    return {
        "summary": _file_signature(summary, root),
        "chains": [_file_signature(path, root) for path in sorted(chains)],
    }


def _read_cache(data_root, cache_path=None):
    try:
        payload = json.loads(_cache_path(data_root, cache_path).read_text(encoding="utf-8"))
        if (
            payload.get("version") != CACHE_VERSION
            or payload.get("data_root") != str(Path(data_root).resolve())
            or not isinstance(payload.get("datasets"), dict)
        ):
            return {}
        return payload["datasets"]
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _cached_coverage(cache, identifier, signature):
    record = cache.get(identifier)
    if not isinstance(record, dict) or record.get("signature") != signature:
        return None
    coverage = record.get("coverage")
    if (
        not isinstance(coverage, dict)
        or not isinstance(coverage.get("start_date"), str)
        or not isinstance(coverage.get("end_date"), str)
        or not isinstance(coverage.get("trading_days"), int)
    ):
        return None
    return coverage["start_date"], coverage["end_date"], coverage["trading_days"]


def _write_cache(data_root, datasets, cache_path=None):
    target = _cache_path(data_root, cache_path)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps({
                "version": CACHE_VERSION,
                "data_root": str(Path(data_root).resolve()),
                "datasets": datasets,
            }, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(target)
    except OSError:
        # A read-only dataset remains usable; the next launch will recalculate coverage.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def catalog(data_root=None, cache_path=None):
    data_root = Path(data_root or DATA_ROOT).resolve()
    with CACHE_LOCK:
        cache = _read_cache(data_root, cache_path)
        refreshed_cache = {}
        entries = []
        for identifier, definition in DATASETS.items():
            label = definition["label"]
            folder = definition["folder"]
            root = data_root / folder
            summary = root / definition["summary"]
            chains = list((root / definition["chain"]).glob("*.parquet"))
            available = summary.is_file() and bool(chains)
            reason = "" if available else (
                f'Missing {definition["summary"]} or {definition["chain"]}/*.parquet'
            )
            start_date = end_date = None
            trading_days = 0
            if available:
                try:
                    signature = _dataset_signature(root, summary, chains)
                    coverage = _cached_coverage(cache, identifier, signature)
                    if coverage is None:
                        coverage = _coverage(
                            str(summary),
                            str(root / definition["chain"] / "*.parquet"),
                            signature["summary"],
                            signature["chains"],
                        )
                    start_date, end_date, trading_days = coverage
                    refreshed_cache[identifier] = {
                        "signature": signature,
                        "coverage": {
                            "start_date": start_date,
                            "end_date": end_date,
                            "trading_days": trading_days,
                        },
                    }
                except (duckdb.Error, OSError, ValueError) as exc:
                    available, reason = False, f"Could not determine usable date coverage: {exc}"
            entries.append(dict(id=identifier, label=label, available=available,
                reason=reason, folder=folder, symbol=definition["symbol"],
                summary_file=definition["summary"], chain_folder=definition["chain"],
                start_date=start_date, end_date=end_date, trading_days=trading_days))
        if refreshed_cache != cache:
            _write_cache(data_root, refreshed_cache, cache_path)
        return entries


def resolve_dataset(identifier, data_root=None, cache_path=None):
    data_root = Path(data_root or DATA_ROOT).resolve()
    if identifier not in DATASETS:
        raise ValueError("Unknown project dataset. Choose one from the dataset selector.")
    entry = next(row for row in catalog(data_root, cache_path) if row["id"] == identifier)
    if not entry["available"]:
        raise ValueError(f'{entry["label"]}: {entry["reason"]}. No other dataset will be substituted.')
    return (data_root / DATASETS[identifier]["folder"]).resolve(), entry
