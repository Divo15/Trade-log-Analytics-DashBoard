import importlib.util
import json
from pathlib import Path
import sys
import unittest


STRATEGY_PATH = (
    Path(__file__).resolve().parents[1]
    / "strategies"
    / "nifty_6DTE_weekly.py"
)


def load_strategy():
    spec = importlib.util.spec_from_file_location("nifty_6dte_strategy_test", STRATEGY_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SixDteStrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategy = load_strategy()

    def test_contract_and_twenty_combination_grid(self):
        strategy = self.strategy
        self.assertEqual(strategy.STRATEGY_CONTRACT_VERSION, "2")
        self.assertEqual(strategy.RUN_MODE, "sweep")
        self.assertEqual(len(strategy.SWEEP_PARAMETER_SETS), 20)
        fingerprints = {
            json.dumps(parameters, sort_keys=True)
            for parameters in strategy.SWEEP_PARAMETER_SETS
        }
        self.assertEqual(len(fingerprints), 20)
        self.assertEqual(strategy.PARAMETER_DEFAULTS["entry_exact_dte"], 6)
        self.assertEqual(strategy.PARAMETER_DEFAULTS["otm_multiple"], 0.0)
        self.assertEqual(strategy.PARAMETER_DEFAULTS["fixed_wing_points"], 300)
        self.assertEqual(
            set(strategy.SWEEP_PARAMETER_SETS[0]),
            {"take_profit_pct", "stop_loss_multiple"},
        )


if __name__ == "__main__":
    unittest.main()
