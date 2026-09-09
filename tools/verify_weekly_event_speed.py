"""Compare all 60 combinations against the frozen scalar evaluator."""
import sys
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from trade_log_dashboard.market_data import MarketDataLoader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_weekly_event_equivalence import reference
from verify_weekly_cache import run, strategy

if __name__ == "__main__":
    old_seconds = new_seconds = 0.
    with MarketDataLoader() as old_loader, MarketDataLoader() as new_loader:
        for index, parameters in enumerate(strategy.SWEEP_PARAMETER_SETS):
            with patch.object(strategy, "evaluate_event", reference.evaluate_event):
                expected, old_time = run(parameters, old_loader)
            actual, new_time = run(parameters, new_loader)
            for left, right in zip(expected[:4], actual[:4]):
                pd.testing.assert_frame_equal(left, right, check_exact=True)
            assert expected[4] == actual[4], "Equity mismatch"
            assert len(actual[3]) > 0
            old_seconds += old_time
            new_seconds += new_time
            if (index + 1) % 10 == 0:
                print(f"Verified {index + 1}/60", flush=True)
    print(f"PASS: identical trades and equity; old={old_seconds:.2f}s new={new_seconds:.2f}s speedup={old_seconds/new_seconds:.2f}x")
