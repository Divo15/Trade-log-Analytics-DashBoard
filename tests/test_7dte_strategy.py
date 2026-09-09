import importlib.util
import json
from pathlib import Path
import sys
import unittest

import pandas as pd


STRATEGY_PATH = Path(__file__).resolve().parents[1] / "strategies" / "nifty_7DTE_straddle_sweep.py"


def load_strategy():
    spec = importlib.util.spec_from_file_location("nifty_7dte_strategy_test", STRATEGY_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SevenDteStrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategy = load_strategy()

    def test_contract_and_sweep_grid(self):
        strategy = self.strategy
        self.assertEqual(strategy.STRATEGY_CONTRACT_VERSION, "2")
        self.assertEqual(strategy.RUN_MODE, "sweep")
        self.assertEqual(len(strategy.SWEEP_PARAMETER_SETS), 192)
        fingerprints = {
            json.dumps(parameters, sort_keys=True)
            for parameters in strategy.SWEEP_PARAMETER_SETS
        }
        self.assertEqual(len(fingerprints), 192)
        self.assertEqual(strategy.PARAMETER_DEFAULTS["entry_exact_dte"], 7)
        self.assertEqual(strategy.PARAMETER_DEFAULTS["otm_multiple"], 0.0)

    def test_option_marks_never_use_a_future_quote(self):
        strategy = self.strategy
        summary = pd.DataFrame({
            "datetime": ["17/04/2025 09:19:59", "17/04/2025 09:20:59"],
            "DTE": [7, 7],
            "future_close": [24000.0, 24000.0],
            "future_atm": [24000, 24000],
            "straddle_future": [300.0, 300.0],
        })
        chain_rows = []
        for strike in (23800, 24000, 24200):
            chain_rows.append({
                "datetime": "17/04/2025 09:20:59",
                "strike": strike,
                "ce_close": 100.0,
                "pe_close": 100.0,
            })
        joined = strategy.build_joined_path(
            pd.DataFrame(chain_rows), summary, 24000, 24000, 24200, 23800
        )
        self.assertEqual(joined.iloc[0]["datetime"], "17/04/2025 09:20:59")


if __name__ == "__main__":
    unittest.main()
