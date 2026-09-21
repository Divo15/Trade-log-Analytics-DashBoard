"""Regression coverage for SENSEX sweep strike-selection caching."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np


STRATEGY_PATH = Path(__file__).resolve().parents[1] / "strategies" / "sensex_fixed_094559_balanced_sweep.py"
SPEC = importlib.util.spec_from_file_location("sensex_cached_selection_test", STRATEGY_PATH)
strategy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(strategy)


class PrecomputedSelectionTests(unittest.TestCase):
    def test_precomputed_selection_matches_original_per_timestamp_scan(self):
        strikes = np.array([100, 110, 120, 130, 140])
        ce = np.array([[22.0, 15.0, 10.0, 6.0, 3.0], [25.0, 18.0, 11.0, 7.0, 4.0]])
        pe = np.array([[3.0, 6.0, 10.0, 15.0, 22.0], [4.0, 7.0, 11.0, 18.0, 25.0]])
        atm = np.array([120, 120])
        straddle = np.array([40.0, 44.0])
        selections = strategy._precompute_option_selections(
            strikes, ce, pe, atm, straddle, (25.0, 40.0)
        )
        cached_day = {"strikes": strikes, "ce": ce, "pe": pe, "atm": atm,
                      "straddle": straddle, "selections": selections}
        uncached_day = {**cached_day, "selections": {}}

        for premium_pct in (25.0, 40.0):
            for index in range(2):
                for side in ("CE", "PE"):
                    self.assertEqual(
                        strategy._select_leg(cached_day, side, index, premium_pct),
                        strategy._select_leg(uncached_day, side, index, premium_pct),
                    )


if __name__ == "__main__":
    unittest.main()
