"""Trusted sweep-summary calculations over authoritative completed trades."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import tzinfo
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping

from .core import TradeLogError, export_trade_log


SWEEP_SCHEMA_VERSION = "1"
SWEEP_CSV_COLUMNS = (
    "schema_version",
    "sweep_id",
    "run_id",
    "strategy",
    "engine",
    "parameters_json",
    "status",
    "start_time",
    "end_time",
    "completed_trade_count",
    "batch_count",
    "traded_days",
    "gross_pnl",
    "fees",
    "net_pnl",
    "max_drawdown",
    "win_rate",
    "max_loss",
    "profit_factor",
    "sharpe_traded_days",
    "yearly_net_pnl_json",
    "trade_log_sha256",
)


@dataclass(frozen=True)
class SweepExportReceipt:
    output_path: Path
    manifest_path: Path
    row_count: int
    sha256: str
    schema_version: str = SWEEP_SCHEMA_VERSION


def _required_text(name: str, value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise TradeLogError(f"{name} is required")
    return text


def _json_object(name: str, value: Any) -> str:
    if not isinstance(value, Mapping):
        raise TradeLogError(f"{name} must be a mapping")
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TradeLogError(f"{name} must contain JSON-compatible finite values") from exc


def _load_json_object(name: str, value: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise TradeLogError(f"{name} must be a valid JSON object") from exc
    if not isinstance(parsed, Mapping):
        raise TradeLogError(f"{name} must be a JSON object")
    _json_object(name, parsed)
    return parsed


def _finite_or_blank(name: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise TradeLogError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TradeLogError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise TradeLogError(f"{name} must be finite")
    return str(number)


def _nonnegative_int(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise TradeLogError(f"{name} must be a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TradeLogError(f"{name} must be a non-negative integer") from exc
    if number < 0 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise TradeLogError(f"{name} must be a non-negative integer")
    return number


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=SWEEP_CSV_COLUMNS, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _yearly_net_pnl(daily: list[Mapping[str, Any]]) -> dict[str, float]:
    yearly: dict[str, float] = {}
    for row in daily:
        day = _required_text("daily.day", row.get("day"))
        year = day[:4]
        if len(year) != 4 or not year.isdigit():
            raise TradeLogError(f"analytics returned invalid daily date: {day!r}")
        yearly[year] = yearly.get(year, 0.0) + float(row["net_pnl"])
    return {year: yearly[year] for year in sorted(yearly)}


def _completed_iteration_row(
    *,
    sweep_id: str,
    run_id: str,
    strategy_name: str,
    engine: str,
    parameters_json: str,
    analytics: Mapping[str, Any],
    completed_trade_count: int,
    trade_log_sha256: str,
) -> dict[str, str]:
    overview = analytics["overview"]
    statistics = analytics["statistics"]
    analytics_run_id = _required_text("analytics.overview.run_id", overview.get("run_id"))
    if analytics_run_id != run_id:
        raise TradeLogError(
            f"iteration run_id {run_id!r} does not match canonical trades {analytics_run_id!r}"
        )
    analytics_strategy = _required_text("analytics.overview.strategy", overview.get("strategy"))
    if analytics_strategy != strategy_name:
        raise TradeLogError(
            f"metadata strategy {strategy_name!r} does not match canonical trades "
            f"{analytics_strategy!r}"
        )
    if int(overview["leg_count"]) != completed_trade_count:
        raise TradeLogError("analytics leg count differs from authoritative completed-trade count")

    worst_batch = float(statistics["worst_batch"])
    return {
        "schema_version": SWEEP_SCHEMA_VERSION,
        "sweep_id": sweep_id,
        "run_id": run_id,
        "strategy": strategy_name,
        "engine": engine,
        "parameters_json": parameters_json,
        "status": "succeeded",
        "start_time": str(overview["start_time"]),
        "end_time": str(overview["end_time"]),
        "completed_trade_count": str(completed_trade_count),
        "batch_count": str(int(overview["batch_count"])),
        "traded_days": str(int(overview["traded_days"])),
        "gross_pnl": _finite_or_blank("gross_pnl", overview["gross_pnl"]),
        "fees": _finite_or_blank("fees", overview["fees"]),
        "net_pnl": _finite_or_blank("net_pnl", overview["net_pnl"]),
        "max_drawdown": _finite_or_blank("max_drawdown", statistics["max_drawdown"]),
        "win_rate": _finite_or_blank("win_rate", statistics["win_rate"]),
        "max_loss": _finite_or_blank("max_loss", min(0.0, worst_batch)),
        "profit_factor": _finite_or_blank("profit_factor", statistics["profit_factor"]),
        "sharpe_traded_days": _finite_or_blank(
            "sharpe_traded_days", statistics["sharpe_traded_days"]
        ),
        "yearly_net_pnl_json": _json_object(
            "yearly_net_pnl", _yearly_net_pnl(analytics["daily"])
        ),
        "trade_log_sha256": trade_log_sha256,
    }


def _no_trade_iteration_row(
    *,
    sweep_id: str,
    run_id: str,
    strategy_name: str,
    engine: str,
    parameters_json: str,
    trade_log_sha256: str,
) -> dict[str, str]:
    row = {column: "" for column in SWEEP_CSV_COLUMNS}
    row.update(
        {
            "schema_version": SWEEP_SCHEMA_VERSION,
            "sweep_id": sweep_id,
            "run_id": run_id,
            "strategy": strategy_name,
            "engine": engine,
            "parameters_json": parameters_json,
            "status": "no_trades",
            "completed_trade_count": "0",
            "batch_count": "0",
            "traded_days": "0",
            "yearly_net_pnl_json": "{}",
            "trade_log_sha256": trade_log_sha256,
        }
    )
    return row


def export_sweep_summary(
    iterations: Iterable[Mapping[str, Any]],
    output_path: str | Path = "output/sweep_results.csv",
    *,
    sweep_id: str,
    assume_timezone: tzinfo | None = None,
) -> SweepExportReceipt:
    """Calculate and export one trusted analytics-summary row per iteration.

    Each iteration must contain ``run_id``, ``parameters``, and the unmodified
    result returned by ``run_strategy(context)`` under ``result``. Metrics are
    calculated from a temporary validated canonical trade log, never accepted
    from generated strategy code.
    """
    normalized_sweep_id = _required_text("sweep_id", sweep_id)
    iteration_values = list(iterations)
    if not iteration_values:
        raise TradeLogError("a sweep requires at least one iteration")

    rows: list[dict[str, str]] = []
    seen_run_ids: set[str] = set()
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        for index, iteration in enumerate(iteration_values):
            if not isinstance(iteration, Mapping):
                raise TradeLogError(f"iteration {index + 1} must be a mapping")
            run_id = _required_text("iteration.run_id", iteration.get("run_id"))
            if run_id in seen_run_ids:
                raise TradeLogError(f"duplicate sweep run_id: {run_id}")
            seen_run_ids.add(run_id)
            parameters_json = _json_object("iteration.parameters", iteration.get("parameters"))
            result = iteration.get("result")
            if not isinstance(result, Mapping):
                raise TradeLogError(f"iteration {run_id!r} result must be a mapping")
            allowed_result_fields = {
                "completed_trades",
                "completed_trade_count",
                "trade_mapper",
                "metadata",
            }
            unknown_result_fields = sorted(set(result) - allowed_result_fields)
            if unknown_result_fields:
                raise TradeLogError(
                    "sweep iteration result contains unsupported fields: "
                    + ", ".join(unknown_result_fields)
                )
            metadata = result.get("metadata")
            if not isinstance(metadata, Mapping):
                raise TradeLogError(f"iteration {run_id!r} metadata must be a mapping")
            unknown_metadata_fields = sorted(set(metadata) - {"strategy_name", "engine"})
            if unknown_metadata_fields:
                raise TradeLogError(
                    "sweep iteration metadata contains unsupported fields: "
                    + ", ".join(unknown_metadata_fields)
                )
            strategy_name = _required_text("metadata.strategy_name", metadata.get("strategy_name"))
            engine = _required_text("metadata.engine", metadata.get("engine"))
            expected_count = _nonnegative_int(
                "completed_trade_count", result.get("completed_trade_count")
            )
            if result.get("completed_trades") is None:
                raise TradeLogError(f"iteration {run_id!r} completed_trades is required")

            trade_log_path = temporary_root / f"iteration-{index:06d}.csv"
            receipt = export_trade_log(
                result.get("completed_trades"),
                trade_log_path,
                mapper=result.get("trade_mapper"),
                expected_count=expected_count,
                assume_timezone=assume_timezone,
            )
            if expected_count == 0:
                row = _no_trade_iteration_row(
                    sweep_id=normalized_sweep_id,
                    run_id=run_id,
                    strategy_name=strategy_name,
                    engine=engine,
                    parameters_json=parameters_json,
                    trade_log_sha256=receipt.sha256,
                )
            else:
                from trade_log_dashboard import analyze_trade_log

                row = _completed_iteration_row(
                    sweep_id=normalized_sweep_id,
                    run_id=run_id,
                    strategy_name=strategy_name,
                    engine=engine,
                    parameters_json=parameters_json,
                    analytics=analyze_trade_log(receipt.output_path),
                    completed_trade_count=expected_count,
                    trade_log_sha256=receipt.sha256,
                )
            rows.append(row)

    destination = Path(output_path).resolve()
    _write_atomic_csv(rows, destination)
    receipt = SweepExportReceipt(
        output_path=destination,
        manifest_path=Path(str(destination) + ".manifest.json"),
        row_count=len(rows),
        sha256=_sha256(destination),
    )
    receipt.manifest_path.write_text(
        json.dumps(
            {
                "artifact_type": "sweep_summary",
                "schema_version": receipt.schema_version,
                "row_count": receipt.row_count,
                "sha256": receipt.sha256,
                "csv_file": receipt.output_path.name,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return receipt


def validate_sweep_summary_csv(csv_path: str | Path) -> SweepExportReceipt:
    """Validate a sweep-summary CSV and return its calculated receipt."""
    path = Path(csv_path).resolve()
    if not path.is_file():
        raise TradeLogError(f"sweep summary does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != SWEEP_CSV_COLUMNS:
            raise TradeLogError("sweep summary columns do not match schema v1 exactly")
        rows = list(reader)
    if not rows:
        raise TradeLogError("sweep summary must contain at least one iteration")

    run_ids: set[str] = set()
    sweep_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if row["schema_version"] != SWEEP_SCHEMA_VERSION:
            raise TradeLogError(f"sweep row {index} has an unsupported schema version")
        sweep_ids.add(_required_text("sweep_id", row["sweep_id"]))
        run_id = _required_text("run_id", row["run_id"])
        if run_id in run_ids:
            raise TradeLogError(f"duplicate sweep run_id: {run_id}")
        run_ids.add(run_id)
        _required_text("strategy", row["strategy"])
        _required_text("engine", row["engine"])
        _load_json_object("parameters_json", row["parameters_json"])
        _load_json_object("yearly_net_pnl_json", row["yearly_net_pnl_json"])
        status = row["status"]
        if status not in {"succeeded", "no_trades"}:
            raise TradeLogError(f"sweep row {index} has invalid status {status!r}")
        count = _nonnegative_int("completed_trade_count", row["completed_trade_count"])
        if status == "no_trades" and count != 0:
            raise TradeLogError("no_trades sweep rows must have completed_trade_count zero")
        if status == "succeeded" and count == 0:
            raise TradeLogError("succeeded sweep rows must contain completed trades")
        _nonnegative_int("batch_count", row["batch_count"])
        _nonnegative_int("traded_days", row["traded_days"])
        for field in (
            "gross_pnl",
            "fees",
            "net_pnl",
            "max_drawdown",
            "win_rate",
            "max_loss",
            "profit_factor",
            "sharpe_traded_days",
        ):
            _finite_or_blank(field, row[field] or None)
        if status == "succeeded":
            _required_text("start_time", row["start_time"])
            _required_text("end_time", row["end_time"])
            for field in (
                "gross_pnl",
                "fees",
                "net_pnl",
                "max_drawdown",
                "win_rate",
                "max_loss",
            ):
                if row[field] == "":
                    raise TradeLogError(f"succeeded sweep rows require {field}")
        checksum = _required_text("trade_log_sha256", row["trade_log_sha256"])
        if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
            raise TradeLogError("trade_log_sha256 must be a lowercase SHA-256 digest")
    if len(sweep_ids) != 1:
        raise TradeLogError("all sweep-summary rows must have the same sweep_id")

    return SweepExportReceipt(
        output_path=path,
        manifest_path=Path(str(path) + ".manifest.json"),
        row_count=len(rows),
        sha256=_sha256(path),
    )
