import importlib.util
import sys
from pathlib import Path
import unittest

import pandas as pd


def load_strategy():
    path = Path(__file__).resolve().parents[1] / "nifty_protected_straddle_colab_CLEAN.py"
    spec = importlib.util.spec_from_file_location("protected_open_fill_strategy", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ProtectedOpenFillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategy = load_strategy()

    def test_path_uses_exact_candle_quotes_and_preserves_open_prices(self):
        chain = pd.DataFrame([
            {"datetime": "01/04/2025 09:16:59", "strike": 100, "ce_open": 11, "ce_close": 12, "pe_open": 21, "pe_close": 22},
            {"datetime": "01/04/2025 09:16:59", "strike": 110, "ce_open": 7, "ce_close": 8, "pe_open": 27, "pe_close": 28},
            {"datetime": "01/04/2025 09:17:59", "strike": 100, "ce_open": 13, "ce_close": 14, "pe_open": 23, "pe_close": 24},
            {"datetime": "01/04/2025 09:17:59", "strike": 110, "ce_open": 9, "ce_close": 10, "pe_open": 29, "pe_close": 30},
        ])
        summary = pd.DataFrame([
            {"datetime": "01/04/2025 09:15:59", "DTE": 4, "future_close": 100, "future_atm": 100, "straddle_future": 20},
            {"datetime": "01/04/2025 09:16:59", "DTE": 4, "future_close": 101, "future_atm": 100, "straddle_future": 20},
            {"datetime": "01/04/2025 09:17:59", "DTE": 4, "future_close": 102, "future_atm": 100, "straddle_future": 20},
        ])

        path = self.strategy.build_joined_path(chain, summary, 100, 100, 110, 110)

        # There is no option quote at 09:15:59, so it must not borrow 09:16:59.
        self.assertEqual(path["datetime"].tolist(), ["01/04/2025 09:16:59", "01/04/2025 09:17:59"])
        self.assertEqual(path.iloc[0]["CE_100_open"], 11)
        self.assertEqual(path.iloc[0]["CE_100"], 12)
        self.assertEqual(path.iloc[0]["PE_110_open"], 27)


if __name__ == "__main__":
    unittest.main()
