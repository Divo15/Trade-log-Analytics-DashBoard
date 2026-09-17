import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest

import numpy as np
import pandas as pd

from trade_log_dashboard.validate_strategy import main
from trade_log_dashboard.worker import _validate_result
from trade_log_exporter import export_equity_snapshots, TradeLogError


STRATEGY = '''
import json
from types import MappingProxyType
import numpy as np
import pandas as pd
STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "single"
SWEEP_PARAMETER_SETS = ({"size": 1}, {"size": 3})

def run_strategy(context):
    context.report_progress(0, 2, "trading day", "Preparing data")
    rows = json.loads((context.market_data / "legs.json").read_text())
    scale = context.config.get("parameters", {}).get("size", 1)
    for row in rows:
        row["run_id"] = context.run_id
        row["quantity"] *= scale
    snapshots = []
    realized = 0
    for i, day in enumerate(("2026-01-01", "2026-01-02"), 1):
        snapshots.append(dict(timestamp=day + "T09:00:00+05:30", realized_pnl=realized, unrealized_pnl=0))
        for row in rows:
            if row["exit_time"].startswith(day):
                sign = 1 if row["side"] == "LONG" else -1
                realized += sign * (row["exit_price"] - row["entry_price"]) * row["quantity"] - row["fees"]
        snapshots.append(dict(timestamp=day + "T15:15:00+05:30", realized_pnl=realized, unrealized_pnl=0))
        context.report_progress(i, 2, "trading day", "Running backtest")
    return MappingProxyType(dict(completed_trades=pd.DataFrame(rows),
        completed_trade_count=np.int64(len(rows)), equity_snapshots=pd.DataFrame(snapshots)))
'''


class StrategyCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.market = self.root / "sample"
        self.market.mkdir()
        rows = []
        for day in ("2026-01-01", "2026-01-02"):
            for side, entry, exit_price in (("SHORT", 100, 90), ("LONG", 20, 25)):
                rows.append(dict(trade_id=f"{day}-{side}", batch_id=day,
                    strategy="Hedged directional", symbol=side, side=side,
                    entry_time=day + "T09:18:00+05:30", exit_time=day + "T15:15:00+05:30",
                    quantity=2, entry_price=entry, exit_price=exit_price, fees=1))
        (self.market / "legs.json").write_text(json.dumps(rows))
        self.source = self.root / "strategy.py"

    def validate(self, source=STRATEGY, timeout=20):
        self.source.write_text(source, encoding="utf-8")
        output = self.root / "check"
        code = main([str(self.source), "--market-data", str(self.market),
                     "--output", str(output), "--timeout", str(timeout)])
        return code, json.loads((output / "validation.json").read_text()), output

    def test_worker_accepts_dataframe_trades_equity_and_numpy_count(self):
        code, report, output = self.validate()
        self.assertEqual(code, 0, report)
        analysis = report["job"]["result"]["analysis"]
        self.assertEqual(analysis["overview"]["net_pnl"], 56)
        self.assertEqual(analysis["overview"]["leg_count"], 4)
        self.assertEqual(analysis["intraday"]["series"][-1]["realized_pnl"], 56)
        self.assertTrue((output / "trades.csv").is_file())

    def test_sweep_accepts_phase_progress_and_independent_parameters(self):
        code, report, _ = self.validate(STRATEGY.replace('RUN_MODE = "single"', 'RUN_MODE = "sweep"'))
        self.assertEqual(code, 0, report)
        variations = report["job"]["result"]["sweep"]["iterations"]
        self.assertEqual([row["metrics"]["net_pnl"] for row in variations], [56, 176])

    def test_equity_mismatch_reports_values_and_keeps_exported_trades(self):
        source = STRATEGY.replace("return MappingProxyType", 'snapshots[-1]["realized_pnl"] = 28\n    return MappingProxyType')
        code, report, output = self.validate(source)
        self.assertEqual(code, 1)
        self.assertEqual(report["validation"], "failed")
        error = report["job"]["error"]
        for fragment in ("expected=56", "supplied=28", "difference=-28"):
            self.assertIn(fragment, error)
        self.assertTrue((output / "trades.csv").is_file())

    def test_failed_sweep_is_not_reported_as_passed(self):
        source = STRATEGY.replace('RUN_MODE = "single"', 'RUN_MODE = "sweep"')
        source = source.replace("for row in rows:", 'if scale == 3:\n        raise ValueError("deliberate failure")\n    for row in rows:', 1)
        code, report, _ = self.validate(source)
        self.assertEqual(code, 1)
        self.assertEqual(report["validation"], "failed")

    def test_deadline_cancels_worker(self):
        code, report, _ = self.validate("import time\ntime.sleep(60)", timeout=0.3)
        self.assertEqual(code, 1)
        self.assertEqual(report["validation"], "timeout")
        self.assertEqual(report["job"]["status"], "cancelled")

    def test_count_remains_strict(self):
        for count in (True, np.bool_(True), 1.0, "1", -1):
            with self.subTest(count=count), self.assertRaises(ValueError):
                _validate_result({"completed_trades": [], "completed_trade_count": count})
        self.assertEqual(_validate_result(MappingProxyType(
            {"completed_trades": [], "completed_trade_count": np.int64(0)})), 0)

    def test_equity_dataframe_still_rejects_bad_values_and_run_ids(self):
        rows = [dict(timestamp="2026-01-01T09:00:00+05:30", realized_pnl=0, unrealized_pnl=0),
                dict(timestamp="2026-01-01T15:00:00+05:30", realized_pnl=0, unrealized_pnl=0)]
        for field, value in (("run_id", "wrong"), ("realized_pnl", float("nan")),
                             ("schema_version", "2")):
            changed = [dict(row, **{field: value}) for row in rows]
            with self.subTest(field=field), self.assertRaises(TradeLogError):
                export_equity_snapshots(pd.DataFrame(changed), self.root / "equity.csv", run_id="test")


if __name__ == "__main__":
    unittest.main()
