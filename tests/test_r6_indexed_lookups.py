import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

from trade_log_dashboard.market_data import MarketDataLoader


STRATEGY_PATH = (
    Path(__file__).resolve().parents[1]
    / "strategies"
    / "nifty_current_week_0dte_trend_following_r6_sweep_dashboard.py"
)


def load_strategy():
    spec = importlib.util.spec_from_file_location("r6_indexed_lookup_test", STRATEGY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class R6IndexedLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategy = load_strategy()

    def test_quote_lookup_preserves_nearest_timestamp_and_first_duplicate_strike(self):
        strategy = self.strategy
        frame = pd.DataFrame(
            {
                "ts": pd.to_datetime(
                    ["2026-01-01 09:18", "2026-01-01 09:18", "2026-01-01 09:20"]
                ),
                "strike": [25000, 25000, 25100],
                "ce_close": [101.0, 999.0, 80.0],
                "pe_close": [91.0, 999.0, 110.0],
            }
        )

        lookup = strategy._frame_lookup(frame)
        exact = strategy._quotes_at(lookup, pd.Timestamp("2026-01-01 09:18"))
        nearest = strategy._quotes_at(lookup, pd.Timestamp("2026-01-01 09:19:59"))

        self.assertIsNotNone(exact)
        self.assertIs(exact, nearest)
        self.assertEqual(strategy._price_for_leg(exact, 25000, "CE"), 101.0)
        self.assertIsNone(strategy._price_for_leg(exact, 25200, "CE"))
        self.assertIsNone(
            strategy._quotes_at(lookup, pd.Timestamp("2026-01-01 09:22:01"))
        )

    def test_summary_lookup_returns_last_row_at_or_before_timestamp(self):
        strategy = self.strategy
        summary = pd.DataFrame(
            {
                "ts": pd.to_datetime(
                    [
                        "2026-01-01 09:18",
                        "2026-01-01 09:20",
                        "2026-01-01 09:20",
                        "2026-01-01 09:23",
                    ]
                ),
                "future_close": [100.0, 101.0, 102.0, 103.0],
            }
        )
        lookup = strategy._summary_lookup(summary)

        self.assertIsNone(
            strategy._summary_row_at_or_before(lookup, pd.Timestamp("2026-01-01 09:17"))
        )
        row = strategy._summary_row_at_or_before(
            lookup, pd.Timestamp("2026-01-01 09:21")
        )
        self.assertEqual(float(row["future_close"]), 102.0)

    def test_short_leg_selection_keeps_existing_tie_break_rules(self):
        strategy = self.strategy
        timestamp = pd.Timestamp("2026-01-01 09:20")
        frame = pd.DataFrame(
            {
                "ts": [timestamp] * 4,
                "strike": [24900, 25000, 25100, 25200],
                "ce_close": [140.0, 120.0, 100.0, 80.0],
                "pe_close": [80.0, 100.0, 120.0, 140.0],
            }
        )
        quotes = strategy._quotes_at(strategy._frame_lookup(frame), timestamp)

        self.assertEqual(
            strategy._select_short_leg(quotes, "call", 25000, 400.0, 0.25),
            ("CE", 25100, 100.0),
        )
        self.assertEqual(
            strategy._select_short_leg(quotes, "put", 25000, 400.0, 0.25),
            ("PE", 25000, 100.0),
        )

    def test_daily_quote_index_is_reused_from_shared_loader_cache(self):
        strategy = self.strategy
        day = pd.Timestamp("2026-01-01")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            partitioned = root / "partitioned"
            day_folder = partitioned / "trade_date=2026-01-01"
            day_folder.mkdir(parents=True)
            chain = pd.DataFrame(
                {
                    "ts": pd.to_datetime(["2026-01-01 09:18", "2026-01-01 09:18"]),
                    "strike": [25000, 25100],
                    "ce_close": [100.0, 80.0],
                    "pe_close": [90.0, 110.0],
                }
            )
            chain.to_parquet(day_folder / "part.parquet", index=False)
            source_folder = root / "nifty_chain"
            source_folder.mkdir()
            chain.assign(datetime="01/01/2026 09:18:00").to_parquet(
                source_folder / "source.parquet", index=False
            )

            with MarketDataLoader(directory=root) as loader:
                first = strategy._load_chain_lookup(root, day, loader, partitioned)
                second = strategy._load_chain_lookup(root, day, loader, partitioned)

                self.assertEqual(first.timestamps, second.timestamps)
                self.assertEqual(loader.stats["shared_misses"], 1)
                self.assertEqual(loader.stats["shared_hits"], 1)
                self.assertEqual(loader.stats["read_misses"], 1)
                self.assertEqual(loader.stats["read_hits"], 0)


if __name__ == "__main__":
    unittest.main()
