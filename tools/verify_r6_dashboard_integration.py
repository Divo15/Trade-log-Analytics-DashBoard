"""Bounded compatibility/parity check; never runs the complete market dataset."""
import argparse
import ast
import importlib.util
import json
from contextlib import ExitStack
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import pandas as pd

from trade_log_dashboard.validate_strategy import main as validate


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "strategies/nifty_current_week_0dte_trend_following_r6_sweep_dashboard.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sample():
    summary, chain = [], []
    premiums = [100, 100, 100, 100, 100, 100, 88, 76, 64, 90, 102, 70, 50, 30, 65, 25, 18]
    for day, sign in (("2023-01-05", 1), ("2023-01-12", -1)):
        ticks = list(pd.date_range(f"{day} 09:13", periods=16, freq="min"))
        ticks.append(pd.Timestamp(f"{day} 15:15"))
        for i, timestamp in enumerate(ticks):
            label = timestamp.strftime("%d/%m/%Y %H:%M:%S")
            summary.append(dict(datetime=label, DTE=0, future_close=18000 + sign * i * 10,
                                future_atm=18000, straddle_future=2 * premiums[i]))
            for strike in range(17800, 18201, 50):
                chain.append(dict(datetime=label, strike=strike,
                    ce_close=premiums[i] * (0.30 if strike > 18000 else 0.60),
                    pe_close=premiums[i] * (0.30 if strike < 18000 else 0.60)))
    return pd.DataFrame(summary), pd.DataFrame(chain)


def prepared(summary, chain):
    summary, chain = summary.copy(), chain.copy()
    for frame in (summary, chain):
        frame["ts"] = pd.to_datetime(frame.datetime, format="%d/%m/%Y %H:%M:%S")
    summary["trade_date"] = summary.ts.dt.date
    chain["trade_date"] = chain.ts.dt.normalize()
    return summary, chain


def execute(module, summary, chain, parameters, execution=None, market_data_loader=None):
    progress = []
    context = SimpleNamespace(run_id="r6-parity", market_data=Path("unused"),
        config={"parameters": parameters, "execution": execution or {}},
        market_data_loader=market_data_loader,
        report_progress=lambda *args: progress.append(args))
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "_load_summary", return_value=summary.copy()))
        if hasattr(module, "_load_chain_for_days"):
            bulk_chain = chain.copy()
            if "trade_date" not in bulk_chain:
                bulk_chain["trade_date"] = bulk_chain.ts.dt.normalize()
            stack.enter_context(patch.object(module, "_load_chain_for_days", return_value=bulk_chain))
        else:
            stack.enter_context(patch.object(
                module, "_load_chain_for_day",
                side_effect=lambda path, day, loader=None, partitioned=None:
                    chain[chain.ts.dt.date == pd.Timestamp(day).date()].copy(),
            ))
        result = module.run_strategy(context)
    return result, progress


def assert_snapshots(result):
    rows = result["completed_trades"]
    snapshots = result["equity_snapshots"]
    assert snapshots and rows
    assert snapshots[0]["realized_pnl"] == snapshots[0]["unrealized_pnl"] == 0
    assert snapshots[-1]["unrealized_pnl"] == 0
    for snapshot in snapshots:
        expected = sum((r["entry_price"] - r["exit_price"]) * r["quantity"] * r["multiplier"] - r["fees"]
                       for r in rows if r["exit_time"] <= snapshot["timestamp"])
        assert abs(snapshot["realized_pnl"] - expected) < 0.01, (snapshot, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--market-data", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    original = load(args.baseline, "r6_original")
    updated = load(SCRIPT, "r6_dashboard")
    assert original.DEFAULTS == updated.DEFAULTS
    assert original.SWEEP_PARAMETER_SETS == updated.SWEEP_PARAMETER_SETS
    assert len(updated.SWEEP_PARAMETER_SETS) == 144
    # The core selection and fill functions must be identical, not merely similar.
    def functions(path):
        return {n.name: ast.dump(n, include_attributes=False)
                for n in ast.parse(path.read_text(encoding="utf-8-sig")).body
                if isinstance(n, ast.FunctionDef)}
    before, after = functions(args.baseline), functions(SCRIPT)
    for name in ("_direction_at", "_select_short_leg", "_open_slot", "_close_slot_row", "_quotes_at"):
        assert before[name] == after[name], name
    raw_summary, raw_chain = sample()
    summary, chain = prepared(raw_summary, raw_chain)
    comparisons = []
    for index, parameters in enumerate(updated.SWEEP_PARAMETER_SETS):
        old, _ = execute(original, summary, chain, parameters)
        new, progress = execute(updated, summary, chain, parameters)
        assert old["completed_trades"] == new["completed_trades"], index
        assert old["completed_trade_count"] == new["completed_trade_count"]
        assert_snapshots(new)
        assert progress[-1][:3] == (2, 2, "trading day")
        comparisons.append(new["completed_trade_count"])
        if (index + 1) % 24 == 0:
            print(f"Trade parity: {index + 1}/144 combinations matched", flush=True)
    # Fees must agree at every intermediate snapshot, including across sessions.
    old_fees, _ = execute(original, summary, chain, {}, {"fees_per_leg": 3.25})
    fees, _ = execute(updated, summary, chain, {}, {"fees_per_leg": 3.25})
    assert old_fees["completed_trades"] == fees["completed_trades"]
    assert_snapshots(fees)
    # Missing marks cannot be silently valued at zero or concealed in the last row.
    missing = chain.copy()
    missing.loc[missing.ts.dt.time == pd.Timestamp("09:19").time(), ["pe_close", "ce_close"]] = float("nan")
    try:
        execute(updated, summary, missing, {"add_on_straddle_decay_pct": 0.99})
    except ValueError as exc:
        assert "Cannot value open" in str(exc), str(exc)
    else:
        raise AssertionError("Missing marks must fail explicitly")
    report = dict(synthetic_combinations_matched=len(comparisons),
                  completed_leg_counts=sorted(set(comparisons)),
                  nonzero_fees_all_snapshots_reconciled=True, missing_marks_rejected=True,
                  defaults_and_grid_unchanged=True)
    if args.market_data:
        # Only two complete expiry days; selection does not truncate individual days.
        source_summary = original._load_summary(args.market_data, None, None)
        days = sorted(source_summary.loc[source_summary.DTE == 0, "trade_date"].unique())[:2]
        real_summary = source_summary[source_summary.trade_date.isin(days)].copy()
        real_chain = pd.concat([original._load_chain_for_day(args.market_data, day) for day in days])
        old, _ = execute(original, real_summary, real_chain, {})
        new, _ = execute(updated, real_summary, real_chain, {})
        assert old["completed_trades"] == new["completed_trades"]
        assert_snapshots(new)
        report["real_sample"] = dict(days=[str(d) for d in days],
                                     matched_legs=new["completed_trade_count"])
    print(json.dumps(report), flush=True)
    market = args.output / "synthetic_market"
    (market / "nifty_chain").mkdir(parents=True)
    with duckdb.connect() as connection:
        connection.from_df(raw_summary).write_parquet(str(market / "nifty_summary.parquet"))
        connection.from_df(raw_chain).write_parquet(str(market / "nifty_chain/part.parquet"))
    code = validate([str(SCRIPT), "--market-data", str(market), "--output",
                     str(args.output / "worker"), "--timeout", "180"])
    report["worker_validation_exit_code"] = code
    run_log = (args.output / "worker/run.log").read_text(encoding="utf-8")
    cache_line = next(line for line in run_log.splitlines() if line.startswith("Market-data cache: "))
    cache_stats = ast.literal_eval(cache_line.removeprefix("Market-data cache: "))
    assert cache_stats["read_misses"] == 3, cache_stats
    assert cache_stats["read_hits"] == 3 * (len(updated.SWEEP_PARAMETER_SETS) - 1), cache_stats
    assert cache_stats["partition_misses"] == 1, cache_stats
    assert cache_stats["partition_hits"] == len(updated.SWEEP_PARAMETER_SETS) - 1, cache_stats
    report["worker_cache_stats"] = cache_stats
    (args.output / "parity.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
