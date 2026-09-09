"""Compare cached and uncached weekly simulations on a short real-data period."""
import sys
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import pandas as pd
from trade_log_dashboard.market_data import MarketDataLoader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "strategies"))
import nifty_6DTE_weekly_hard_sl_tp_two_reentries as strategy


def run(parameters, loader=None):
    args = strategy.engine_defaults()
    args.market_data_loader = loader
    for name, value in {**strategy.PARAMETER_DEFAULTS, **parameters}.items():
        if name in {"entry_time", "square_off_time", "reentry_entry_time"}:
            value = strategy.parse_time_string(value)
        setattr(args, name, value)
    args.base = str(Path(__file__).resolve().parents[1] / "data/db/nifty current week")
    args.start_date, args.end_date = "2023-01-06", "2023-01-19"
    started = perf_counter()
    result = strategy.run_backtest(args)
    return result, perf_counter() - started


if __name__ == "__main__":
    combinations = [*strategy.SWEEP_PARAMETER_SETS[:3], *strategy.SWEEP_PARAMETER_SETS[-3:]]
    baseline = [run(parameters) for parameters in combinations]
    with MarketDataLoader(memory_limit=0) as data_loader:
        for parameters in combinations:
            run(parameters, data_loader)
        data_only = [run(parameters, data_loader) for parameters in combinations]
    with MarketDataLoader() as loader:
        for parameters in combinations:
            run(parameters, loader)
        with patch.object(strategy, "_read_cycle_chain", side_effect=AssertionError("Cache miss")), patch.object(strategy, "_build_joined_path", side_effect=AssertionError("Prepared path cache miss")):
            for parameters, (expected, elapsed), (previous, data_elapsed) in zip(combinations, baseline, data_only):
                actual, cached_elapsed = run(parameters, loader)
                for left, right, middle in zip(expected[:4], actual[:4], previous[:4]):
                    pd.testing.assert_frame_equal(left, right)
                    pd.testing.assert_frame_equal(left, middle)
                assert expected[4] == actual[4] == previous[4], "Equity snapshots differ"
                assert len(actual[3]) > 0, "Verification must include actual trades"
                print(f"{parameters}: identical trades and equity; uncached={elapsed:.2f}s data-only={data_elapsed:.2f}s prepared={cached_elapsed:.2f}s", flush=True)
        assert loader.memory_bytes <= loader.memory_limit
        print(f"PASS: shared runtime cache {loader.stats}")
