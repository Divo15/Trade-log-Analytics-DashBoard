import importlib.util
from pathlib import Path
import sys
import unittest
from datetime import time
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "strategies"))
import nifty_6DTE_weekly_hard_sl_tp_two_reentries as strategy
spec = importlib.util.spec_from_file_location("event_reference", ROOT / "tests/fixtures/weekly_event_reference.py")
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class EventEquivalenceTests(unittest.TestCase):
    def test_exit_rules_and_snapshots_match_scalar_engine(self):
        rng = np.random.default_rng(123)
        reasons = set()
        for case in range(240):
            count = 1 if case == 0 else 30
            timestamps = pd.date_range("2023-01-06 15:00", periods=count, freq="min")
            path = pd.DataFrame({"ts": timestamps, "datetime": timestamps.strftime("%d/%m/%Y %H:%M:%S"),
                "DTE": np.maximum(0, 2 - np.arange(count) // 10), "future_close": rng.normal(18000, 200, count)})
            for name, price in [("CE_18000", 100), ("PE_18000", 100), ("CE_18200", 20), ("PE_17800", 20)]:
                path[name] = np.maximum(1, rng.normal(price, price * .6, count))
            settings = dict(short_call=18000, short_put=18000, long_call=18200, long_put=17800,
                entry_credit_points=160., take_profit_pct=[.2, .5, 5][case % 3], stop_loss_multiple=[.2, 1., 5][case % 3],
                force_exit_dte=0 if case % 4 == 0 else None, square_off_time=time(15, 15),
                roll_trigger_pct=.5 if case % 5 == 0 else None, no_roll_last_dte=0,
                use_leg_stop=case % 2 == 0, profit_lock_trigger_pct=.1 if case % 3 == 0 else None,
                profit_lock_exit_pct=.05, max_hold_minutes=5 if case % 7 == 0 else None,
                realized_before_points=17., lot_size=65)
            before, after = {}, {}
            expected = reference.evaluate_event(path, **settings, snapshot_sink=before)
            actual = strategy.evaluate_event(path, **settings, snapshot_sink=after)
            pd.testing.assert_series_equal(expected[0], actual[0], check_exact=True)
            self.assertEqual(expected[1:], actual[1:])
            self.assertEqual(before, after)
            reasons.add(actual[2])
        self.assertTrue({"take_profit", "stop_loss", "leg_stop", "expiry_exit"} <= reasons)
