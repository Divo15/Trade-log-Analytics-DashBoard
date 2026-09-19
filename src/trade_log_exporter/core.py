"""Atomic export, validation, and callback-friendly recording."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import tzinfo
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Iterable, Mapping, TypeVar

from .schema import CSV_COLUMNS, SCHEMA_VERSION, SchemaError, TradeRecord


RawTrade = TypeVar("RawTrade")
TradeMapper = Callable[[RawTrade, int], Mapping[str, Any] | TradeRecord]


class TradeLogError(RuntimeError):
    """Raised when export or verification cannot complete safely."""


@dataclass(frozen=True)
class ExportReceipt:
    output_path: Path
    manifest_path: Path
    row_count: int
    sha256: str
    schema_version: str = SCHEMA_VERSION


def _materialize_completed_trades(completed_trades: Iterable[RawTrade]) -> list[RawTrade]:
    """Materialize row-oriented engine results without iterating table columns.

    Pandas DataFrames and compatible tabular results expose ``iterrows`` but
    iterate over column labels by default. Treat their rows as the authoritative
    completed-trade items so an engine can hand its closed-trade table directly
    to the exporter without silently mapping column names as trades.
    """
    iterrows = getattr(completed_trades, "iterrows", None)
    if callable(iterrows):
        return [row for _, row in iterrows()]
    return list(completed_trades)


def _to_record(
    raw: Mapping[str, Any] | TradeRecord,
    *,
    assume_timezone: tzinfo | None,
) -> TradeRecord:
    if isinstance(raw, TradeRecord):
        return raw
    try:
        return TradeRecord.from_mapping(raw, assume_timezone=assume_timezone)
    except SchemaError as exc:
        raise TradeLogError(str(exc)) from exc


def _write_atomic_csv(records: list[TradeRecord], output_path: Path) -> None:
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
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="raise")
            writer.writeheader()
            writer.writerows(record.to_csv_row() for record in records)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(receipt: ExportReceipt) -> None:
    payload = {
        "schema_version": receipt.schema_version,
        "row_count": receipt.row_count,
        "sha256": receipt.sha256,
        "csv_file": receipt.output_path.name,
    }
    receipt.manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prepare_trade_records(
    completed_trades: Iterable[RawTrade],
    *,
    mapper: TradeMapper[RawTrade] | None = None,
    expected_count: int | None = None,
    assume_timezone: tzinfo | None = None,
) -> list[TradeRecord]:
    """Validate completed engine output without writing a trade-log file.

    Sweep workers use this to calculate comparison metrics in memory.  The
    exact schema, count, run-id and uniqueness checks are shared with export.
    """
    raw_trades = _materialize_completed_trades(completed_trades)
    if expected_count is not None and len(raw_trades) != expected_count:
        raise TradeLogError(
            f"Engine completed-trade count ({expected_count}) does not match "
            f"received trade count ({len(raw_trades)})"
        )

    records: list[TradeRecord] = []
    for index, raw in enumerate(raw_trades):
        try:
            mapped = mapper(raw, index) if mapper is not None else raw
            if mapper is None and callable(getattr(mapped, "to_dict", None)):
                mapped = mapped.to_dict()
            if not isinstance(mapped, (Mapping, TradeRecord)):
                raise TradeLogError("The mapper must return a mapping or TradeRecord")
            records.append(_to_record(mapped, assume_timezone=assume_timezone))
        except Exception as exc:
            if isinstance(exc, TradeLogError):
                raise TradeLogError(f"Trade {index + 1}: {exc}") from exc
            raise TradeLogError(f"Trade {index + 1}: adapter failed: {exc}") from exc

    trade_ids = [record.trade_id for record in records]
    if len(trade_ids) != len(set(trade_ids)):
        raise TradeLogError("trade_id values must be unique within one export")
    if len({record.run_id for record in records}) > 1:
        raise TradeLogError("All rows in one CSV must have the same run_id")
    return records


def export_trade_log(
    completed_trades: Iterable[RawTrade],
    output_path: str | Path = "output/trades.csv",
    *,
    mapper: TradeMapper[RawTrade] | None = None,
    expected_count: int | None = None,
    assume_timezone: tzinfo | None = None,
) -> ExportReceipt:
    """Export actual completed trades using a caller-supplied engine adapter.

    ``mapper`` receives each engine trade and its zero-based index. It must return
    either a canonical mapping or ``TradeRecord``. If the iterable already contains
    canonical mappings, mapper may be omitted.
    """
    records = prepare_trade_records(
        completed_trades, mapper=mapper, expected_count=expected_count,
        assume_timezone=assume_timezone,
    )

    destination = Path(output_path).resolve()
    _write_atomic_csv(records, destination)
    receipt = ExportReceipt(
        output_path=destination,
        manifest_path=Path(str(destination) + ".manifest.json"),
        row_count=len(records),
        sha256=_sha256(destination),
    )
    _write_manifest(receipt)
    return receipt


def validate_trade_log_csv(
    csv_path: str | Path,
    *,
    assume_timezone: tzinfo | None = None,
) -> ExportReceipt:
    """Validate an existing canonical CSV and its exact column order."""
    path = Path(csv_path).resolve()
    if not path.is_file():
        raise TradeLogError(f"CSV does not exist: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
            raise TradeLogError(
                "CSV columns do not match schema v1 exactly. Expected: " + ",".join(CSV_COLUMNS)
            )
        rows = list(reader)

    records = [
        _to_record(row, assume_timezone=assume_timezone)
        for row in rows
    ]
    trade_ids = [record.trade_id for record in records]
    if len(trade_ids) != len(set(trade_ids)):
        raise TradeLogError("trade_id values must be unique within one CSV")
    if len({record.run_id for record in records}) > 1:
        raise TradeLogError("All rows in one CSV must have the same run_id")

    return ExportReceipt(
        output_path=path,
        manifest_path=Path(str(path) + ".manifest.json"),
        row_count=len(records),
        sha256=_sha256(path),
    )


class TradeRecorder:
    """Collect canonical closed-trade mappings from a custom engine callback."""

    def __init__(self) -> None:
        self._records: list[Mapping[str, Any] | TradeRecord] = []

    def record(self, trade: Mapping[str, Any] | TradeRecord) -> None:
        self._records.append(trade)

    @property
    def count(self) -> int:
        return len(self._records)

    def export(
        self,
        output_path: str | Path = "output/trades.csv",
        *,
        assume_timezone: tzinfo | None = None,
    ) -> ExportReceipt:
        return export_trade_log(
            self._records,
            output_path,
            expected_count=self.count,
            assume_timezone=assume_timezone,
        )
