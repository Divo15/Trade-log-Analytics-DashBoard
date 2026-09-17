"""Bounded equivalence check; never runs the full selected dataset."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
import json
import warnings

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location(
    "e1r1_optimized", ROOT / "strategies/dashboard_e1r1_four_slot_strategy_optimized.py"
)
strategy = module_from_spec(spec)
spec.loader.exec_module(strategy)


def scalar(values):
    return values.map(strategy._parse_datetime)


def main():
    cases = [
        ["02/01/2023 09:18:59", "05/05/2026 15:29:59"],
        ["02/01/2023 09:18:59", "2023-01-02T04:00:00+00:00"],
        ["02/01/2023 09:18:59", "01/13/2023 09:18:59"],
        [pd.Timestamp("2023-01-02 09:18:59"), pd.Timestamp("2023-01-03")],
        ["02/01/2023 09:18:59", 0],
        [0, 1],
        [],
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        for values in cases:
            for dtype in (None, "object"):
                series = pd.Series(values, dtype=dtype)
                pd.testing.assert_series_equal(scalar(series), strategy._parse_datetimes(series))
        for bad in (None, "bad timestamp", "31/02/2023 09:18:59", ""):
            series = pd.Series(["02/01/2023 09:18:59", bad])
            failures = []
            for parse in (scalar, strategy._parse_datetimes):
                try:
                    parse(series)
                except Exception as exc:
                    failures.append(type(exc))
            assert len(failures) == 2 and failures[0] == failures[1], failures
    duplicate_index = pd.Series(cases[1], index=[7, 7])
    pd.testing.assert_series_equal(scalar(duplicate_index), strategy._parse_datetimes(duplicate_index))

    dataset = ROOT / "data/db/nifty current week"
    # Read only one chain file, retain at most three expiry days.
    first_file = sorted((dataset / "nifty_chain").glob("*.parquet"))[0]
    chain = pd.read_parquet(
        first_file, columns=["datetime", "strike", "ce_close", "pe_close", "DTE"],
        filters=[("DTE", "=", 0)],
    )
    dates = sorted(chain["datetime"].str[:10].unique())[:3]
    assert dates, "Sample file has no expiry dates"
    chain = chain[chain["datetime"].str[:10].isin(dates)].copy()
    summary = pd.read_parquet(dataset / "nifty_summary.parquet", filters=[("DTE", "=", 0)])
    summary = summary[summary["datetime"].str[:10].isin(dates)].copy()
    assert not summary.empty
    context = SimpleNamespace(market_data=dataset, run_id="equivalence", config={"parameters": {}})

    def sample_read(path, columns=None, **kwargs):
        frame = summary if "summary" in Path(path).name else chain
        return frame.loc[:, columns].copy() if columns else frame.copy()

    results, timings = [], []
    for parser in (scalar, strategy._parse_datetimes):
        with patch.object(strategy, "_parse_datetimes", parser), patch.object(
            strategy.pd, "read_parquet", side_effect=sample_read
        ):
            start = perf_counter()
            results.append(strategy.run_strategy(context))
            timings.append(perf_counter() - start)
    assert results[0] == results[1], "Trades, snapshots or metadata changed"
    assert results[0]["completed_trade_count"] > 0, "Sample must exercise trading"
    print(json.dumps({
        "sample_file": first_file.name, "expiry_dates": dates,
        "chain_rows": len(chain), "summary_rows": len(summary),
        "completed_legs": results[0]["completed_trade_count"],
        "snapshots": len(results[0]["equity_snapshots"]),
        "scalar_sample_seconds": round(timings[0], 3),
        "optimized_sample_seconds": round(timings[1], 3),
        "exact_result_equality": True, "timestamp_edge_cases": "passed",
    }, indent=2))


if __name__ == "__main__":
    main()
