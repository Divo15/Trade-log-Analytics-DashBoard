"""Prove R6 Numba metrics against the authoritative strategy."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies import nifty_current_week_0dte_trend_following_r6_numba_batch as batch
from strategies import nifty_current_week_0dte_trend_following_r6_sweep_dashboard as strategy


def synthetic_market() -> tuple[pd.DataFrame, pd.DataFrame]:
    summary, chain = [], []
    premiums = [100, 100, 100, 100, 100, 100, 88, 76, 64, 90, 102, 70, 50, 30, 65, 25, 18]
    for day, sign in (("2023-01-05", 1), ("2023-01-12", -1)):
        timestamps = list(pd.date_range(f"{day} 09:13", periods=16, freq="min"))
        timestamps.append(pd.Timestamp(f"{day} 15:15"))
        for index, timestamp in enumerate(timestamps):
            label = timestamp.strftime("%d/%m/%Y %H:%M:%S")
            summary.append(
                {
                    "datetime": label,
                    "DTE": 0,
                    "future_close": 18_000 + sign * index * 10,
                    "future_atm": 18_000,
                    "straddle_future": 2 * premiums[index],
                }
            )
            for strike in range(17_800, 18_201, 50):
                chain.append(
                    {
                        "datetime": label,
                        "strike": strike,
                        "ce_close": premiums[index] * (0.30 if strike > 18_000 else 0.60),
                        "pe_close": premiums[index] * (0.30 if strike < 18_000 else 0.60),
                    }
                )
    summary_frame = pd.DataFrame(summary)
    chain_frame = pd.DataFrame(chain)
    for frame in (summary_frame, chain_frame):
        frame["ts"] = pd.to_datetime(frame["datetime"], format=strategy.DATETIME_FORMAT)
    summary_frame["trade_date"] = summary_frame["ts"].dt.date
    return summary_frame, chain_frame


def _context(parameters=None, execution=None) -> SimpleNamespace:
    return SimpleNamespace(
        run_id="r6-numba-parity",
        market_data=Path("unused"),
        config={"parameters": parameters or {}, "execution": execution or {}},
    )


def _patched_market(summary: pd.DataFrame, chains_by_day: dict):
    stack = ExitStack()
    stack.enter_context(patch.object(strategy, "_load_summary", return_value=summary.copy()))
    stack.enter_context(patch.object(strategy, "_partition_chain_cache", return_value=None))
    stack.enter_context(
        patch.object(
            strategy,
            "_load_chain_for_day",
            side_effect=lambda path, day, loader=None, partitioned=None: chains_by_day[
                pd.Timestamp(day).date()
            ].copy(),
        )
    )
    return stack


def _oracle_rows(summary, chains_by_day, parameter_sets):
    rows = np.empty((len(parameter_sets), len(batch.METRIC_COLUMNS)), dtype=np.float64)
    with _patched_market(summary, chains_by_day):
        for index, parameters in enumerate(parameter_sets):
            result = strategy.run_strategy(_context(parameters))
            rows[index] = batch.oracle_metrics(result)
    return rows


def _batch_rows(summary, chains_by_day, parameter_sets, workers):
    with _patched_market(summary, chains_by_day):
        data, matrix = batch.prepare_batch_data(_context(), parameter_sets)
    serial = batch.run_batch_kernel(data, matrix, workers=1, parallel=False)
    parallel = batch.run_batch_kernel(data, matrix, workers=workers, parallel=True)
    np.testing.assert_array_equal(serial, parallel)
    return serial


def _assert_metrics(expected, actual, label):
    np.testing.assert_array_equal(actual[:, batch.M_STATUS], 0, err_msg=label)
    np.testing.assert_array_equal(
        actual[:, batch.M_TRADE_COUNT], expected[:, batch.M_TRADE_COUNT], err_msg=label
    )
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-8, err_msg=label)


def random_parameter_sets(count: int, seed: int = 20260921):
    rng = np.random.default_rng(seed)
    premium_values = np.asarray([0.20, 0.225, 0.25, 0.275, 0.30, 0.325, 0.35, 0.375, 0.40])
    result = []
    for _ in range(count):
        result.append(
            {
                "strike_premium_pct_of_straddle": float(rng.choice(premium_values)),
                "add_on_straddle_decay_pct": float(rng.uniform(0.04, 0.24)),
                "stop_loss_pct": float(rng.uniform(0.10, 0.70)),
                "profit_booking_pct": float(rng.uniform(0.15, 0.85)),
                "trailing_activation_pct": float(rng.uniform(0.08, 0.60)),
                "trailing_gap_pct": float(rng.uniform(0.05, 0.30)),
                "r6_reversal_threshold_pct": float(rng.uniform(0.0002, 0.0030)),
                "fees_per_leg": float(rng.choice([0.0, 1.25, 3.25, 8.75])),
            }
        )
    return result


def _parse_benchmark_counts(value: str) -> list[int]:
    counts = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        count = int(item)
        if count <= 0:
            raise argparse.ArgumentTypeError("benchmark counts must be positive")
        counts.append(count)
    return counts


def _status_counts(metrics: np.ndarray) -> dict[str, int]:
    values, counts = np.unique(metrics[:, batch.M_STATUS].astype(np.int64), return_counts=True)
    return {str(int(value)): int(count) for value, count in zip(values, counts)}


def _benchmark_batch(summary, chains_by_day, counts: list[int], workers: int):
    if not counts:
        return []
    largest = max(counts)
    parameter_sets = random_parameter_sets(largest, seed=20260922)
    with _patched_market(summary, chains_by_day):
        data, matrix = batch.prepare_batch_data(_context(), parameter_sets)
    rows = []
    for count in counts:
        chunk = np.ascontiguousarray(matrix[:count])
        started = perf_counter()
        metrics = batch.run_batch_kernel(data, chunk, workers=workers, parallel=True)
        seconds = perf_counter() - started
        rows.append(
            {
                "combinations": int(count),
                "seconds": seconds,
                "combinations_per_second": count / seconds if seconds > 0 else None,
                "status_counts": _status_counts(metrics),
            }
        )
    return rows


def _scale_guidance(benchmarks: list[dict], target_combinations: int):
    if not benchmarks or target_combinations <= 0:
        return None
    best = max(
        (row for row in benchmarks if row.get("combinations_per_second")),
        key=lambda row: row["combinations_per_second"],
        default=None,
    )
    if not best:
        return None
    combinations_per_second = float(best["combinations_per_second"])
    estimated_seconds = target_combinations / combinations_per_second
    return {
        "target_combinations": int(target_combinations),
        "basis_combinations": int(best["combinations"]),
        "basis_combinations_per_second": combinations_per_second,
        "estimated_seconds": estimated_seconds,
        "estimated_hours": estimated_seconds / 3600.0,
        "guidance": (
            "local_multicore_is_plausible"
            if estimated_seconds <= 12 * 3600
            else "consider_larger_multicore_vm_after_reviewing_memory_and_cost"
        ),
    }


def _real_sample(path: Path, days: int):
    summary = strategy._load_summary(path, None, None)
    candidates = sorted(summary.loc[summary["DTE"] == 0, "trade_date"].unique())
    selected_days = []
    chains = {}
    rejected = {}
    for day in candidates:
        day_key = pd.Timestamp(day).date()
        day_summary = summary.loc[summary["trade_date"] == day].copy()
        day_chain = strategy._load_chain_for_day(path, day)
        try:
            _oracle_rows(day_summary, {day_key: day_chain}, [{}])
        except ValueError as exc:
            rejected[str(day)] = str(exc)
            continue
        selected_days.append(day)
        chains[day_key] = day_chain
        if len(selected_days) == days:
            break
    if len(selected_days) < days:
        raise RuntimeError(
            f"Only {len(selected_days)} real expiry days passed every oracle mapping; "
            f"requested {days}. Rejected: {rejected}"
        )
    selected_summary = summary.loc[summary["trade_date"].isin(selected_days)].copy()
    return selected_summary, chains, [str(day) for day in selected_days], rejected


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-data", type=Path)
    parser.add_argument("--real-days", type=int, default=10)
    parser.add_argument("--random-count", type=int, default=2048)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--output", "--report", dest="output", type=Path)
    parser.add_argument(
        "--benchmark-counts",
        type=_parse_benchmark_counts,
        default=[],
        help="Comma-separated post-parity batch sizes, for example 10000,25000,50000.",
    )
    parser.add_argument(
        "--target-combinations",
        type=int,
        default=0,
        help="Optional total sweep size used to estimate local versus VM runtime.",
    )
    args = parser.parse_args(argv)
    if not batch.NUMBA_AVAILABLE:
        raise RuntimeError("Numba is unavailable")

    report = {}
    synthetic_summary, synthetic_chain = synthetic_market()
    synthetic_chains = {
        day: group.copy()
        for day, group in synthetic_chain.groupby(synthetic_chain["ts"].dt.date)
    }

    declared = list(strategy.SWEEP_PARAMETER_SETS)
    started = perf_counter()
    expected = _oracle_rows(synthetic_summary, synthetic_chains, declared)
    actual = _batch_rows(synthetic_summary, synthetic_chains, declared, args.workers)
    _assert_metrics(expected, actual, "declared synthetic grid")
    report["declared_synthetic"] = {
        "combinations": len(declared),
        "seconds": perf_counter() - started,
    }

    random_sets = random_parameter_sets(args.random_count)
    started = perf_counter()
    expected = _oracle_rows(synthetic_summary, synthetic_chains, random_sets)
    actual = _batch_rows(synthetic_summary, synthetic_chains, random_sets, args.workers)
    _assert_metrics(expected, actual, "random synthetic combinations")
    report["random_synthetic"] = {
        "combinations": len(random_sets),
        "seconds": perf_counter() - started,
    }

    if args.market_data:
        started = perf_counter()
        real_summary, real_chains, selected_days, rejected_days = _real_sample(
            args.market_data, args.real_days
        )
        expected = _oracle_rows(real_summary, real_chains, declared)
        actual = _batch_rows(real_summary, real_chains, declared, args.workers)
        _assert_metrics(expected, actual, "real expiry-day sample")
        report["real_sample"] = {
            "days": selected_days,
            "rejected_days": rejected_days,
            "combinations": len(declared),
            "seconds": perf_counter() - started,
        }
        benchmark_summary = real_summary
        benchmark_chains = real_chains
    else:
        benchmark_summary = synthetic_summary
        benchmark_chains = synthetic_chains

    if args.benchmark_counts:
        started = perf_counter()
        benchmarks = _benchmark_batch(
            benchmark_summary, benchmark_chains, args.benchmark_counts, args.workers
        )
        report["batch_benchmark"] = {
            "source": "real_sample" if args.market_data else "synthetic_sample",
            "rows": benchmarks,
            "seconds": perf_counter() - started,
        }
        guidance = _scale_guidance(benchmarks, args.target_combinations)
        if guidance is not None:
            report["scale_guidance"] = guidance

    report["parallel_matches_serial_exactly"] = True
    report["metric_columns"] = list(batch.METRIC_COLUMNS)
    encoded = json.dumps(report, indent=2)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
