"""Engine-supplied mark-to-market snapshots; never infer marks from exits."""
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os
import tempfile

from .core import TradeLogError

EQUITY_COLUMNS = ("schema_version", "run_id", "timestamp", "realized_pnl", "unrealized_pnl")


def validate_equity_rows(rows):
    checked = []
    previous = None
    run_id = None
    for index, row in enumerate(rows, 2):
        try:
            if set(row) != set(EQUITY_COLUMNS) or str(row["schema_version"]) != "1":
                raise ValueError("expected equity schema v1 columns")
            current_run = str(row["run_id"]).strip()
            if not current_run or (run_id is not None and current_run != run_id):
                raise ValueError("all snapshots must have the same nonempty run_id")
            timestamp = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError("timestamp requires a timezone offset")
            timestamp = timestamp.astimezone(timezone.utc)
            if previous is not None and timestamp <= previous:
                raise ValueError("timestamps must be unique and strictly increasing")
            realized = Decimal(str(row["realized_pnl"]))
            unrealized = Decimal(str(row["unrealized_pnl"]))
            if not realized.is_finite() or not unrealized.is_finite():
                raise ValueError("P&L must be finite")
            if max(abs(realized), abs(unrealized)) > Decimal("1e15"):
                raise ValueError("P&L exceeds supported magnitude (1e15)")
        except (ValueError, TypeError, InvalidOperation) as exc:
            raise TradeLogError(f"Equity row {index}: {exc}") from exc
        checked.append(dict(timestamp=timestamp, realized_pnl=realized, unrealized_pnl=unrealized))
        previous, run_id = timestamp, current_run
    if len(checked) < 2:
        raise TradeLogError("Equity CSV requires at least two snapshots, including a zero starting baseline")
    if checked[0]["realized_pnl"] != 0 or checked[0]["unrealized_pnl"] != 0:
        raise TradeLogError("First equity snapshot must be zero, before the first trade")
    return run_id, checked


def read_equity_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != EQUITY_COLUMNS:
            raise TradeLogError("Equity CSV header must be: " + ",".join(EQUITY_COLUMNS))
        return validate_equity_rows(reader)


def export_equity_snapshots(snapshots, output_path, *, run_id):
    """Export engine snapshots with cumulative realized net P&L and open P&L."""
    rows = [dict(row, schema_version="1", run_id=run_id) for row in snapshots]
    validate_equity_rows(rows)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=EQUITY_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
