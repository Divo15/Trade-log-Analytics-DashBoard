from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from trade_log_dashboard import analyze_trade_log
from trade_log_exporter import export_trade_log


class DashboardAnalyticsTest(unittest.TestCase):
    def test_calculates_metrics_from_raw_executions(self) -> None:
        trades = [
            self.trade("t1", "b1", "LONG", "2026-01-02", 100, 110),
            self.trade("t2", "b1", "SHORT", "2026-01-02", 50, 45),
            self.trade("t3", "b2", "LONG", "2026-01-03", 200, 190),
            self.trade("t4", "b2", "SHORT", "2026-01-03", 80, 90),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "trades.csv")
            export_trade_log(trades, path)
            result = analyze_trade_log(path)

        self.assertEqual(result["validation"]["status"], "passed")
        self.assertEqual(result["overview"]["leg_count"], 4)
        self.assertEqual(result["overview"]["batch_count"], 2)
        self.assertAlmostEqual(result["overview"]["gross_pnl"], -250)
        self.assertAlmostEqual(result["overview"]["net_pnl"], -250)
        self.assertAlmostEqual(result["statistics"]["max_drawdown"], -1000)
        self.assertAlmostEqual(result["statistics"]["win_rate"], 50)
        self.assertEqual(result["statistics"]["longest_win_streak"], 1)
        self.assertEqual(result["statistics"]["longest_loss_streak"], 1)
        self.assertEqual(result["statistics"]["win_days"], 1)
        self.assertEqual(result["statistics"]["loss_days"], 1)
        self.assertAlmostEqual(result["statistics"]["day_risk_reward"], 0.75)
        self.assertEqual(result["statistics"]["longest_underwater_traded_days"], 1)
        self.assertAlmostEqual(result["concentration"]["best_day_share_pct"], 100)
        self.assertAlmostEqual(result["concentration"]["net_without_best_day"], -1000)
        self.assertAlmostEqual(result["monthly"][0]["high"], 750)
        self.assertAlmostEqual(result["monthly"][0]["close"], -250)
        self.assertAlmostEqual(result["monthly"][0]["max_drawdown"], -1000)
        self.assertFalse(result["assumptions"]["fees_included"])

    def test_rejects_empty_trade_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "trades.csv")
            export_trade_log([], path)
            with self.assertRaisesRegex(Exception, "contains no completed trades"):
                analyze_trade_log(path)

    @staticmethod
    def trade(
        trade_id: str,
        batch_id: str,
        side: str,
        day: str,
        entry_price: float,
        exit_price: float,
    ) -> dict[str, object]:
        return {
            "run_id": "run-test",
            "trade_id": trade_id,
            "batch_id": batch_id,
            "leg_id": trade_id,
            "strategy": "Test strategy",
            "symbol": "NIFTY",
            "side": side,
            "entry_time": f"{day}T09:15:00+05:30",
            "exit_time": f"{day}T15:15:00+05:30",
            "quantity": 1,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "multiplier": 50,
            "fees": 0,
        }


if __name__ == "__main__":
    unittest.main()
