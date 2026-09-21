"""Drawdown from observed equity, with run and endpoint reconciliation."""
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from collections.abc import Mapping

from trade_log_exporter import TradeLogError, TradeRecord
from trade_log_exporter.equity import read_equity_csv, validate_equity_rows


def _analyze_equity(run_id, snapshots, trades, digest):
    if {row["run_id"] for row in trades} != {run_id}:
        raise TradeLogError("Equity run_id does not match the trade log")
    def instant(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    if snapshots[0]["timestamp"] > min(instant(row["entry_time"]) for row in trades):
        raise TradeLogError("Equity baseline must be at or before the first entry")
    if snapshots[-1]["timestamp"] < max(instant(row["exit_time"]) for row in trades):
        raise TradeLogError("Equity snapshots must cover the final trade exit")
    expected = sum((Decimal(row["exit_price"]) - Decimal(row["entry_price"]))
                   * (1 if row["side"] == "LONG" else -1)
                   * Decimal(row["quantity"]) * Decimal(row["multiplier"])
                   - Decimal(row["fees"]) for row in trades)
    if abs(snapshots[-1]["realized_pnl"] - expected) > Decimal("0.01"):
        actual = snapshots[-1]["realized_pnl"]
        raise TradeLogError(
            "Final realized equity P&L does not reconcile with trades and fees "
            f"(tolerance 0.01): expected={expected}, supplied={actual}, "
            f"difference={actual - expected}. Check cumulative realized P&L across "
            "sessions, LONG/SHORT signs, quantities, multipliers, fees, and fill prices. "
            "Do not overwrite the final snapshot to hide a bookkeeping error."
        )
    if snapshots[-1]["unrealized_pnl"] != 0:
        raise TradeLogError("Final unrealized P&L must be zero: this report requires a fully closed run")
    peak = Decimal(0)
    worst = Decimal(0)
    peak_time = snapshots[0]["timestamp"]
    worst_peak = worst_time = None
    series = []
    for row in snapshots:
        equity = row["realized_pnl"] + row["unrealized_pnl"]
        if equity >= peak:
            peak, peak_time = equity, row["timestamp"]
        drawdown = equity - peak
        if drawdown < worst:
            worst, worst_peak, worst_time = drawdown, peak_time, row["timestamp"]
        series.append({"timestamp": row["timestamp"].isoformat(), "equity": float(equity),
                       "realized_pnl": float(row["realized_pnl"]), "unrealized_pnl": float(row["unrealized_pnl"]),
                       "drawdown": float(drawdown)})
    return {"max_drawdown": float(worst),
            "worst_unrealized_pnl": float(min(row["unrealized_pnl"] for row in snapshots)),
            "peak_time": worst_peak.isoformat() if worst_peak else None,
            "trough_time": worst_time.isoformat() if worst_time else None,
            "max_gap_seconds": max((b["timestamp"] - a["timestamp"]).total_seconds() for a, b in zip(snapshots, snapshots[1:])),
            "snapshot_count": len(series), "series": series,
            "sha256": digest}


def analyze_equity(equity_path, trade_path):
    run_id, snapshots = read_equity_csv(equity_path)
    with Path(trade_path).open(encoding="utf-8-sig", newline="") as handle:
        trades = list(csv.DictReader(handle))
    return _analyze_equity(
        run_id, snapshots, trades, hashlib.sha256(Path(equity_path).read_bytes()).hexdigest()
    )


def analyze_equity_snapshots(snapshots, records: list[TradeRecord]):
    """Validate engine snapshots and calculate intraday metrics without files."""
    if not records:
        raise TradeLogError("Equity analysis requires completed trades")
    run_ids = {record.run_id for record in records}
    if len(run_ids) != 1:
        raise TradeLogError("All records must have the same run_id")
    run_id = next(iter(run_ids))
    if callable(getattr(snapshots, "iterrows", None)):
        snapshots = (row.to_dict() for _, row in snapshots.iterrows())
    rows = []
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            raise TradeLogError("Equity snapshots must be mappings")
        rows.append({
            "schema_version": "1", "run_id": run_id,
            "timestamp": snapshot.get("timestamp"),
            "realized_pnl": snapshot.get("realized_pnl"),
            "unrealized_pnl": snapshot.get("unrealized_pnl"),
        })
    checked_run_id, checked_snapshots = validate_equity_rows(rows)
    trades = [record.to_csv_row() for record in records]
    digest = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()
    return _analyze_equity(checked_run_id, checked_snapshots, trades, digest)
