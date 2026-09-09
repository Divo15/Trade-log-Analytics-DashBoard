import tempfile
from pathlib import Path
import unittest

from trade_log_exporter import export_equity_snapshots, export_trade_log, TradeLogError
from trade_log_dashboard.equity import analyze_equity
import test_dashboard_analytics


class EquityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.trades = Path(self.temporary.name, "trades.csv")
        self.equity = Path(self.temporary.name, "equity.csv")
        export_trade_log([test_dashboard_analytics.DashboardAnalyticsTest.trade("t", "b", "LONG", "2026-01-02", 100, 110)], self.trades)
        self.rows = [
            dict(timestamp="2026-01-02T09:00:00+05:30", realized_pnl=0, unrealized_pnl=0),
            dict(timestamp="2026-01-02T10:00:00+05:30", realized_pnl=0, unrealized_pnl=1000),
            dict(timestamp="2026-01-02T11:00:00+05:30", realized_pnl=0, unrealized_pnl=-1500),
            dict(timestamp="2026-01-02T15:15:00+05:30", realized_pnl=500, unrealized_pnl=0),
        ]

    def export(self, run_id="run-test"):
        export_equity_snapshots(self.rows, self.equity, run_id=run_id)

    def test_observes_loss_hidden_by_profitable_exit(self):
        self.export()
        result = analyze_equity(self.equity, self.trades)
        self.assertEqual(result["max_drawdown"], -2500)
        self.assertEqual(result["worst_unrealized_pnl"], -1500)
        self.assertEqual(result["snapshot_count"], 4)
        self.assertEqual(result["max_gap_seconds"], 15300)
        self.assertEqual(result["trough_time"], "2026-01-02T05:30:00+00:00")

    def test_run_mismatch(self):
        self.export("wrong")
        with self.assertRaisesRegex(TradeLogError, "run_id"):
            analyze_equity(self.equity, self.trades)

    def test_rejects_final_pnl_mismatch(self):
        self.rows[-1]["realized_pnl"] = 501
        self.export()
        with self.assertRaisesRegex(TradeLogError, "reconcile"):
            analyze_equity(self.equity, self.trades)

    def test_rejects_missing_final_coverage(self):
        self.rows[-1]["timestamp"] = "2026-01-02T12:00:00+05:30"
        self.export()
        with self.assertRaisesRegex(TradeLogError, "final trade exit"):
            analyze_equity(self.equity, self.trades)

    def test_rejects_open_final_position(self):
        self.rows[-1]["unrealized_pnl"] = 12
        self.export()
        with self.assertRaisesRegex(TradeLogError, "fully closed"):
            analyze_equity(self.equity, self.trades)

    def test_invalid_snapshots_preserve_existing_export(self):
        self.export()
        original = self.equity.read_bytes()
        for field, value in [("unrealized_pnl", "NaN"), ("timestamp", "2026-01-02T10:00:00"),
                             ("timestamp", self.rows[0]["timestamp"])]:
            with self.subTest(field=field, value=value):
                previous = self.rows[1][field]
                self.rows[1][field] = value
                with self.assertRaises(TradeLogError):
                    self.export()
                self.assertEqual(self.equity.read_bytes(), original)
                self.rows[1][field] = previous

    def test_rejects_nonzero_baseline(self):
        self.rows[0]["unrealized_pnl"] = -1
        with self.assertRaisesRegex(TradeLogError, "zero"):
            self.export()

    def test_orders_absolute_times_across_offsets(self):
        self.rows[1]["timestamp"] = "2026-01-02T04:30:00+00:00"
        self.export()
        self.assertEqual(analyze_equity(self.equity, self.trades)["max_drawdown"], -2500)
