from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from trade_log_exporter import CSV_COLUMNS, TradeLogError, export_trade_log, validate_trade_log_csv


IST = timezone(timedelta(hours=5, minutes=30))


def valid_trade(**overrides):
    trade = {
        "run_id": "run-001",
        "trade_id": "trade-001",
        "batch_id": "batch-001",
        "leg_id": "call",
        "strategy": "Straddle",
        "symbol": "NIFTY_CE",
        "side": "SHORT",
        "entry_time": datetime(2026, 8, 1, 9, 20, tzinfo=IST),
        "exit_time": datetime(2026, 8, 1, 15, 15, tzinfo=IST),
        "quantity": 50,
        "entry_price": "124.50",
        "exit_price": "82.20",
        "multiplier": 1,
        "fees": "40.00",
    }
    trade.update(overrides)
    return trade


class ExporterTests(unittest.TestCase):
    def test_exports_exact_schema_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output" / "trades.csv"
            receipt = export_trade_log([valid_trade()], output, expected_count=1)

            self.assertTrue(output.is_file())
            self.assertEqual(receipt.row_count, 1)
            with output.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(tuple(reader.fieldnames or ()), CSV_COLUMNS)
                row = next(reader)
            self.assertEqual(row["schema_version"], "1")
            self.assertEqual(row["side"], "SHORT")
            self.assertNotIn("pnl", row)

            manifest = json.loads(receipt.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["row_count"], 1)
            self.assertEqual(manifest["sha256"], receipt.sha256)
            self.assertEqual(validate_trade_log_csv(output).row_count, 1)

    def test_refuses_derived_pnl(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TradeLogError, "Derived result fields are forbidden"):
                export_trade_log(
                    [valid_trade(pnl=1000)],
                    Path(directory) / "trades.csv",
                    expected_count=1,
                )

    def test_refuses_count_mismatch_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trades.csv"
            with self.assertRaisesRegex(TradeLogError, "count"):
                export_trade_log([valid_trade()], output, expected_count=2)
            self.assertFalse(output.exists())

    def test_refuses_duplicate_trade_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TradeLogError, "unique"):
                export_trade_log(
                    [valid_trade(), valid_trade(symbol="NIFTY_PE")],
                    Path(directory) / "trades.csv",
                    expected_count=2,
                )

    def test_refuses_naive_timestamps_unless_timezone_is_explicit(self):
        naive = valid_trade(
            entry_time=datetime(2026, 8, 1, 9, 20),
            exit_time=datetime(2026, 8, 1, 15, 15),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trades.csv"
            with self.assertRaisesRegex(TradeLogError, "no timezone"):
                export_trade_log([naive], output, expected_count=1)
            receipt = export_trade_log(
                [naive],
                output,
                expected_count=1,
                assume_timezone=IST,
            )
            self.assertEqual(receipt.row_count, 1)

    def test_failed_export_does_not_replace_last_valid_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trades.csv"
            first = export_trade_log([valid_trade()], output, expected_count=1)
            original_bytes = output.read_bytes()

            with self.assertRaises(TradeLogError):
                export_trade_log(
                    [valid_trade(side="UNKNOWN")],
                    output,
                    expected_count=1,
                )

            self.assertEqual(output.read_bytes(), original_bytes)
            self.assertEqual(validate_trade_log_csv(output).sha256, first.sha256)


if __name__ == "__main__":
    unittest.main()
