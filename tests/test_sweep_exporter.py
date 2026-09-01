from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from trade_log_exporter import (
    SWEEP_CSV_COLUMNS,
    TradeLogError,
    export_sweep_summary,
    validate_sweep_summary_csv,
)


def trade(run_id: str, trade_id: str, day: str, entry: float, exit: float):
    return {
        "run_id": run_id,
        "trade_id": trade_id,
        "batch_id": trade_id,
        "leg_id": trade_id,
        "strategy": "Sweep strategy",
        "symbol": "NIFTY",
        "side": "LONG",
        "entry_time": f"{day}T09:15:00+05:30",
        "exit_time": f"{day}T15:15:00+05:30",
        "quantity": 1,
        "entry_price": entry,
        "exit_price": exit,
        "multiplier": 1,
        "fees": 0,
    }


def iteration(run_id: str, parameters, trades):
    return {
        "run_id": run_id,
        "parameters": parameters,
        "result": {
            "completed_trades": trades,
            "completed_trade_count": len(trades),
            "trade_mapper": None,
            "metadata": {
                "strategy_name": "Sweep strategy",
                "engine": "Test engine",
            },
        },
    }


class SweepExporterTest(unittest.TestCase):
    def test_calculates_one_summary_row_per_iteration(self) -> None:
        iterations = [
            iteration(
                "run-001",
                {"lookback": 10},
                [
                    trade("run-001", "t1", "2025-01-02", 100, 110),
                    trade("run-001", "t2", "2026-01-02", 100, 80),
                ],
            ),
            iteration("run-002", {"lookback": 20}, []),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "sweep_results.csv"
            receipt = export_sweep_summary(iterations, output, sweep_id="sweep-001")
            validation = validate_sweep_summary_csv(output)
            with output.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(tuple(reader.fieldnames or ()), SWEEP_CSV_COLUMNS)
                rows = list(reader)
            manifest = json.loads(receipt.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(receipt.row_count, 2)
        self.assertEqual(validation.row_count, 2)
        self.assertEqual(rows[0]["status"], "succeeded")
        self.assertEqual(float(rows[0]["net_pnl"]), -10)
        self.assertEqual(float(rows[0]["max_drawdown"]), -20)
        self.assertEqual(float(rows[0]["win_rate"]), 50)
        self.assertEqual(float(rows[0]["max_loss"]), -20)
        self.assertEqual(json.loads(rows[0]["yearly_net_pnl_json"]), {"2025": 10, "2026": -20})
        self.assertEqual(json.loads(rows[0]["parameters_json"]), {"lookback": 10})
        self.assertEqual(rows[1]["status"], "no_trades")
        self.assertEqual(rows[1]["completed_trade_count"], "0")
        self.assertEqual(manifest["artifact_type"], "sweep_summary")

    def test_rejects_count_mismatch_before_replacing_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "sweep_results.csv"
            output.write_text("last valid summary\n", encoding="utf-8")
            bad = iteration("run-001", {}, [trade("run-001", "t1", "2025-01-02", 1, 2)])
            bad["result"]["completed_trade_count"] = 2
            with self.assertRaisesRegex(TradeLogError, "count"):
                export_sweep_summary([bad], output, sweep_id="sweep-001")
            self.assertEqual(output.read_text(encoding="utf-8"), "last valid summary\n")

    def test_rejects_metrics_supplied_by_strategy_result(self) -> None:
        supplied = iteration("run-001", {}, [])
        supplied["result"]["win_rate"] = 100
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(TradeLogError, "unsupported fields"):
                export_sweep_summary(
                    [supplied],
                    Path(temporary) / "sweep_results.csv",
                    sweep_id="sweep-001",
                )

    def test_rejects_duplicate_run_ids(self) -> None:
        duplicate = iteration("run-001", {}, [])
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(TradeLogError, "duplicate"):
                export_sweep_summary(
                    [duplicate, duplicate],
                    Path(temporary) / "sweep_results.csv",
                    sweep_id="sweep-001",
                )


if __name__ == "__main__":
    unittest.main()
