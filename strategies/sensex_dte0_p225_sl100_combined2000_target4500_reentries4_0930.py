"""SENSEX DTE-0 absolute-premium short-straddle exhaustive sweep.

Rules preserved from the supplied image and reference implementation:
* DTE=0 current-expiry data from the dashboard-selected dataset.
* Entry time sweeps 09:30-11:00 inclusive in five-minute increments.
* Short CE and PE are selected independently closest to the configured absolute
  premium from Rs 200-Rs 400, on the ATM-or-outward side of the ATM strike.
* Individual-leg stop loss sweeps 20%-100%; a stopped leg does not re-enter.
* Combined maximum loss sweeps Rs 500-Rs 2,000 and combined profit sweeps
  Rs 1,000-Rs 5,000. Cycle P&L includes realised stopped-leg P&L.
* Overall re-entry sweeps zero through four and occurs on the next market bar
  after a combined exit.
* 0.5% slippage is applied to entries and exits.
* Remaining open legs close at the first available 15:15-or-later bar, falling
  back to the last available bar when the dataset has no such timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
import os
from pathlib import Path
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

try:
    import numba
    from numba import njit, prange
except ImportError:  # The original Python runner remains available without Numba.
    numba = None
    njit = None
    prange = range


STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "single"
DEFAULT_MARKET_DATA_PATH = None

PREMIUM_PRICES = tuple(range(200, 401, 25))
LEG_SL_PCTS = tuple(range(20, 101, 5))
COMBINED_MAX_LOSSES_RS = tuple(range(500, 2001, 250))
COMBINED_TARGETS_RS = tuple(range(1000, 5001, 500))
REENTRIES = (0, 1, 2, 3, 4)
ENTRY_TIMES = tuple(
    f"{minutes // 60:02d}:{minutes % 60:02d}:00"
    for minutes in range(9 * 60 + 30, 11 * 60 + 1, 5)
)

SWEEP_PARAMETER_NAMES = {
    "premium_price",
    "leg_sl_pct",
    "combined_max_loss_rs",
    "combined_target_rs",
    "overall_reentries",
    "entry_start",
}


class _SweepGrid(Sequence):
    """Materialize one Cartesian-product mapping at a time during iteration."""

    _dimensions = (
        PREMIUM_PRICES,
        LEG_SL_PCTS,
        COMBINED_MAX_LOSSES_RS,
        COMBINED_TARGETS_RS,
        REENTRIES,
        ENTRY_TIMES,
    )

    def __len__(self) -> int:
        result = 1
        for values in self._dimensions:
            result *= len(values)
        return result

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[position] for position in range(*index.indices(len(self))))
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        selected = []
        for values in reversed(self._dimensions):
            index, offset = divmod(index, len(values))
            selected.append(values[offset])
        premium, leg_sl, max_loss, target, reentries, entry_start = reversed(selected)
        return {
            "premium_price": float(premium),
            "leg_sl_pct": float(leg_sl),
            "combined_max_loss_rs": float(max_loss),
            "combined_target_rs": float(target),
            "overall_reentries": int(reentries),
            "entry_start": str(entry_start),
        }


SWEEP_PARAMETER_SETS = ()
SWEEP_COMBINATION_COUNT = 0


ALLOWED_PARAMETERS = {"lot_size"}

FIXED_PARAMETERS = {
    "premium_price": 225.0,
    "leg_sl_pct": 100.0,
    "combined_max_loss_rs": 2_000.0,
    "combined_target_rs": 4_500.0,
    "overall_reentries": 4,
    "entry_start": "09:30:00",
}

STRATEGY_NAME = "sensex-dte0-absolute-premium-short-straddle"
ENTRY_START = "09:30:00"
EXIT_TIME = "15:15:00"
LEG_SL_PCT = 30.0
COMBINED_MAX_LOSS_RS = 1_000.0
COMBINED_TARGET_RS = 4_000.0
OVERALL_REENTRIES = 3
SLIPPAGE = 0.01
MARGIN_BASIS_RS = 300_000
DEFAULT_LOT_SIZE_BY_SYMBOL = {
    "NIFTY": 65,
    "SENSEX": 20,
}
TIMEZONE = ZoneInfo("Asia/Kolkata")


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


class _DayProgress:
    """Report progress by trading day without printing from dashboard runs."""

    def __init__(self, total: int, reporter=None):
        self.total = total
        self.reporter = reporter
        self.completed = 0
        self.started = perf_counter()
        self.last_fallback_refresh = 0.0
        if reporter is not None:
            self.bar = None
            reporter(0, total, "trading day", "Running backtest")
            return
        try:
            from tqdm.auto import tqdm
        except ImportError:
            self.bar = None
        else:
            self.bar = tqdm(
                total=total,
                desc="SENSEX Backtest",
                unit=" trading day",
                dynamic_ncols=True,
                mininterval=1.0,
                smoothing=0.1,
            )

    def update(self) -> None:
        self.completed += 1
        if self.reporter is not None:
            self.reporter(self.completed, self.total, "trading day", "Running backtest")
            return
        elapsed = perf_counter() - self.started
        speed = self.completed / elapsed if elapsed > 0 else 0.0
        remaining = (self.total - self.completed) / speed if speed > 0 else 0.0
        if self.bar is not None:
            finish = datetime.now().astimezone() + timedelta(seconds=remaining)
            self.bar.set_postfix_str(
                f"ETA {_format_duration(remaining)}; finish {finish.strftime('%I:%M:%S %p').lstrip('0')}",
                refresh=False,
            )
            self.bar.update(1)
            return
        now = perf_counter()
        if self.completed != self.total and now - self.last_fallback_refresh < 1.0:
            return
        self.last_fallback_refresh = now
        percent = self.completed / self.total * 100 if self.total else 100.0
        print(
            f"SENSEX Backtest: {percent:5.1f}% | "
            f"Completed: {self.completed}/{self.total} trading days | "
            f"Elapsed: {_format_duration(elapsed)} | "
            f"Speed: {speed:.2f} days/sec | "
            f"ETA Remaining: {_format_duration(remaining)}",
            end="\n" if self.completed == self.total else "\r",
            flush=True,
        )

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close()


def _parse_datetime(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, dayfirst=True)
    if pd.isna(parsed):
        raise ValueError(f"Invalid market-data timestamp: {value!r}")
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(TIMEZONE)
    return timestamp.tz_convert(TIMEZONE)


def _parse_datetimes(values: pd.Series) -> pd.Series:
    if values.empty or not (
        pd.api.types.is_object_dtype(values.dtype)
        or isinstance(values.dtype, pd.StringDtype)
    ):
        return values.map(_parse_datetime)
    try:
        standard = values.str.fullmatch(
            r"[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}",
            na=False,
        ).fillna(False).astype(bool)
    except AttributeError:
        return values.map(_parse_datetime)
    if not standard.all():
        return values.map(_parse_datetime)
    parsed = pd.to_datetime(
        values, format="%d/%m/%Y %H:%M:%S", errors="coerce"
    ).dt.tz_localize(TIMEZONE)
    if parsed.isna().any():
        return values.map(_parse_datetime)
    return parsed


def _instrument_symbol(config: dict[str, Any]) -> str:
    instrument = config.get("instrument") or {}
    if isinstance(instrument, dict):
        symbol = instrument.get("symbol") or instrument.get("underlying")
        if symbol:
            return str(symbol).upper()
    return "SENSEX"


def _lot_size(config: dict[str, Any], symbol: str) -> int:
    parameters = dict(config.get("parameters") or {})
    execution = dict(config.get("execution") or {})
    value = parameters.get("lot_size", execution.get("lot_size"))
    if value is not None:
        lot_size = int(value)
        if lot_size <= 0:
            raise ValueError("lot_size must be positive")
        return lot_size
    return DEFAULT_LOT_SIZE_BY_SYMBOL.get(symbol.upper(), 1)


def _period(config: dict[str, Any]) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    period = dict(config.get("period") or {})
    start = period.get("start_date") or period.get("start")
    end = period.get("end_date") or period.get("end")
    start_ts = pd.Timestamp(start).date() if start else None
    end_ts = pd.Timestamp(end).date() if end else None
    return start_ts, end_ts


def _resolve_data_paths(selected: Path, symbol: str) -> tuple[Path, Path]:
    selected = Path(selected)
    prefix = symbol.lower()
    summary_names = (
        f"{prefix}_summary.parquet",
        "nifty_summary.parquet",
        "sensex_summary.parquet",
        "summary.parquet",
    )
    chain_names = (f"{prefix}_chain", "nifty_chain", "sensex_chain", "chain")

    if selected.is_file():
        summary = selected if "summary" in selected.stem.lower() else None
        if summary is None:
            summary = next((selected.parent / name for name in summary_names if (selected.parent / name).exists()), None)
        chain = selected if selected.suffix.lower() == ".parquet" and "summary" not in selected.stem.lower() else None
        if chain is None:
            chain = next((selected.parent / name for name in chain_names if (selected.parent / name).exists()), None)
    elif selected.is_dir():
        if selected.name.lower() in set(chain_names):
            chain = selected
            summary = next((selected.parent / name for name in summary_names if (selected.parent / name).exists()), None)
        else:
            summary = next((selected / name for name in summary_names if (selected / name).exists()), None)
            chain = next((selected / name for name in chain_names if (selected / name).exists()), None)
    else:
        raise FileNotFoundError(f"Selected market data does not exist: {selected}")

    if summary is None or chain is None:
        raise ValueError(
            f"Selected market data must provide summary and chain data; "
            f"resolved summary={summary!r}, chain={chain!r}"
        )
    return Path(summary), Path(chain)


def _add_time_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["dt"] = _parse_datetimes(out["datetime"])
    out["date"] = out["dt"].dt.date
    out["time"] = out["dt"].dt.strftime("%H:%M:%S")
    return out


def _parquet_sources(path: Path) -> list[Path]:
    return [path] if path.is_file() else sorted(path.rglob("*.parquet"))


def _read_frame(loader, sources: list[Path], key: tuple[Any, ...], build):
    if callable(getattr(loader, "read_frame", None)):
        return loader.read_frame(sources, key, build)
    return build()


def _market_sources(selected: Path, symbol: str) -> list[Path]:
    summary_path, chain_path = _resolve_data_paths(selected, symbol)
    return _parquet_sources(summary_path) + _parquet_sources(chain_path)


def _load_market_data(
    selected: Path,
    symbol: str,
    loader=None,
    start_date=None,
    end_date=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_path, chain_path = _resolve_data_paths(selected, symbol)
    summary_columns = [
        "datetime", "DTE", "future_close", "future_atm", "straddle_future",
        "synth_atm", "straddle_synth",
    ]
    chain_columns = ["datetime", "strike", "ce_close", "pe_close", "DTE"]
    summary = _read_frame(
        loader,
        _parquet_sources(summary_path),
        ("fixed-094559-summary-v1", str(summary_path), tuple(summary_columns)),
        lambda: pd.read_parquet(summary_path, columns=summary_columns),
    )
    chain = _read_frame(
        loader,
        _parquet_sources(chain_path),
        ("fixed-094559-chain-v1", str(chain_path), tuple(chain_columns)),
        lambda: pd.read_parquet(chain_path, columns=chain_columns),
    )

    summary = _add_time_columns(summary)
    summary = summary[summary["DTE"].eq(0)].copy()
    if start_date is not None:
        summary = summary[summary["date"] >= start_date].copy()
    if end_date is not None:
        summary = summary[summary["date"] <= end_date].copy()
    summary["entry_atm"] = summary["synth_atm"].where(
        summary["synth_atm"].notna(), summary["future_atm"]
    )
    summary["entry_straddle"] = summary["straddle_synth"].where(
        summary["straddle_synth"].notna(), summary["straddle_future"]
    )
    summary = summary[
        summary["future_close"].notna()
        & summary["entry_atm"].notna()
        & summary["entry_straddle"].notna()
    ].copy()
    if summary.empty:
        raise ValueError("The selected dataset has no valid DTE=0 summary rows")
    if summary.duplicated("dt").any():
        raise ValueError("Summary data contains duplicate timestamps")
    summary["entry_atm"] = summary["entry_atm"].astype(int)
    summary = summary.sort_values("dt").reset_index(drop=True)

    chain = _add_time_columns(chain)
    chain = chain[chain["DTE"].eq(0)].copy()
    wanted = set(summary["dt"])
    chain = chain[chain["dt"].isin(wanted)].copy()
    chain["strike"] = chain["strike"].astype(int)
    if chain.duplicated(["dt", "strike"]).any():
        chain = chain.groupby(["dt", "date", "time", "strike"], as_index=False)[
            ["ce_close", "pe_close"]
        ].max()
    if chain.empty:
        raise ValueError("The selected dataset has no matching DTE=0 chain rows")
    return summary, chain


def _load_days(
    selected: Path,
    symbol: str,
    loader=None,
    start_date=None,
    end_date=None,
) -> list[dict[str, Any]]:
    sources = _market_sources(selected, symbol)

    def build() -> list[dict[str, Any]]:
        summary, chain = _load_market_data(
            selected,
            symbol,
            loader,
            start_date=start_date,
            end_date=end_date,
        )
        return _build_days(summary, chain, tuple(float(value) for value in PREMIUM_PRICES))

    shared_object = getattr(loader, "shared_object", None)
    if callable(shared_object):
        return shared_object(
            sources,
            (
                "sensex-absolute-premium-days-v3",
                symbol,
                str(start_date),
                str(end_date),
                ENTRY_START,
                EXIT_TIME,
                PREMIUM_PRICES,
            ),
            build,
        )
    return build()


def _clock_seconds(value: str) -> int:
    hour, minute, second = (int(part) for part in str(value).split(":")[:3])
    return hour * 3600 + minute * 60 + second


def _precompute_option_selections(
    strikes: np.ndarray,
    ce: np.ndarray,
    pe: np.ndarray,
    atm: np.ndarray,
    premium_prices: tuple[float, ...],
) -> dict[float, dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]]:
    """Precompute the reference selection rule for every grid premium and bar."""
    selections = {}
    for premium_price in premium_prices:
        sides = {}
        for option_type, quotes in (("CE", ce), ("PE", pe)):
            columns = np.full(len(atm), -1, dtype=np.int32)
            selected_strikes = np.full(len(atm), -1, dtype=np.int32)
            selected_quotes = np.full(len(atm), np.nan, dtype=np.float64)
            for index in range(len(atm)):
                if option_type == "CE":
                    valid = (
                        (strikes >= atm[index])
                        & np.isfinite(quotes[index])
                        & (quotes[index] > 0)
                    )
                else:
                    valid = (
                        (strikes <= atm[index])
                        & np.isfinite(quotes[index])
                        & (quotes[index] > 0)
                    )
                candidates = np.flatnonzero(valid)
                if not len(candidates):
                    continue
                score = (
                    np.abs(quotes[index, candidates] - premium_price)
                    + np.abs(strikes[candidates] - atm[index]) * 1e-9
                )
                column = int(candidates[int(np.argmin(score))])
                columns[index] = column
                selected_strikes[index] = int(strikes[column])
                selected_quotes[index] = float(quotes[index, column])
            columns.flags.writeable = False
            selected_strikes.flags.writeable = False
            selected_quotes.flags.writeable = False
            sides[option_type] = (columns, selected_strikes, selected_quotes)
        selections[float(premium_price)] = sides
    return selections


def _build_days(
    summary: pd.DataFrame,
    chain: pd.DataFrame,
    premium_prices: tuple[float, ...],
) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    start_seconds = _clock_seconds(ENTRY_START)
    exit_seconds = _clock_seconds(EXIT_TIME)
    for date_value, summary_day in summary.groupby("date", sort=True):
        summary_day = summary_day.sort_values("dt").reset_index(drop=True)
        chain_day = chain[chain["date"].eq(date_value)]
        times = summary_day["time"].tolist()
        timestamps = summary_day["dt"].tolist()
        seconds = np.array([_clock_seconds(t) for t in times], dtype=int)
        start_i = next((i for i, value in enumerate(seconds) if value >= start_seconds), None)
        if start_i is None:
            continue
        exit_i = next((i for i, value in enumerate(seconds) if value >= exit_seconds), len(seconds) - 1)
        if exit_i < start_i:
            continue
        strikes = np.sort(chain_day["strike"].unique().astype(int))
        if not len(strikes):
            continue
        ce = chain_day.pivot_table(
            index="time", columns="strike", values="ce_close", aggfunc="max"
        ).reindex(index=times, columns=strikes).to_numpy(float)
        pe = chain_day.pivot_table(
            index="time", columns="strike", values="pe_close", aggfunc="max"
        ).reindex(index=times, columns=strikes).to_numpy(float)
        ce_ff = pd.DataFrame(ce).ffill().to_numpy()
        pe_ff = pd.DataFrame(pe).ffill().to_numpy()
        atm = summary_day["entry_atm"].to_numpy(int)
        straddle = summary_day["entry_straddle"].to_numpy(float)
        selections = _precompute_option_selections(
            strikes, ce, pe, atm, premium_prices
        )
        days.append({
            "date": date_value,
            "timestamps": timestamps,
            "times": times,
            "seconds": seconds,
            "start_i": start_i,
            "exit_i": exit_i,
            "atm": atm,
            "straddle": straddle,
            "strikes": strikes,
            "ce": ce,
            "pe": pe,
            "ce_ff": ce_ff,
            "pe_ff": pe_ff,
            "selections": selections,
        })
    if not days:
        raise ValueError("No tradable DTE=0 expiry days were found")
    return days


def _leg_quote(day: dict[str, Any], option_type: str, column: int, index: int) -> float:
    values = day["ce"] if option_type == "CE" else day["pe"]
    value = values[index, column]
    return float(value) if np.isfinite(value) and value > 0 else float("nan")


def _leg_mark(day: dict[str, Any], option_type: str, column: int, index: int) -> float:
    values = day["ce_ff"] if option_type == "CE" else day["pe_ff"]
    value = values[index, column]
    return float(value) if np.isfinite(value) and value > 0 else float("nan")


def _select_leg(
    day: dict[str, Any],
    option_type: str,
    index: int,
    premium_price: float,
) -> dict[str, Any] | None:
    prepared = day["selections"].get(float(premium_price), {}).get(option_type)
    if prepared is not None:
        columns, strikes, quotes = prepared
        column = int(columns[index])
        if column < 0:
            return None
        return {
            "option_type": option_type,
            "strike": int(strikes[index]),
            "column": column,
            "entry_quote": float(quotes[index]),
        }

    # Preserve exact behavior for an explicitly supplied non-grid value.
    target = float(premium_price)
    atm = int(day["atm"][index])
    strikes = day["strikes"]
    quotes = day["ce"][index] if option_type == "CE" else day["pe"][index]
    if option_type == "CE":
        mask = (strikes >= atm) & np.isfinite(quotes) & (quotes > 0)
    else:
        mask = (strikes <= atm) & np.isfinite(quotes) & (quotes > 0)
    indexes = np.where(mask)[0]
    if not len(indexes):
        return None
    score = np.abs(quotes[indexes] - target) + np.abs(strikes[indexes] - atm) * 1e-9
    column = int(indexes[int(np.argmin(score))])
    return {
        "option_type": option_type,
        "strike": int(strikes[column]),
        "column": column,
        "entry_quote": float(quotes[column]),
    }


def _entry_fill(quote: float) -> float:
    return quote * (1.0 - SLIPPAGE)


def _exit_fill(quote: float) -> float:
    return quote * (1.0 + SLIPPAGE)


def _leg_unrealized(leg: dict[str, Any], day: dict[str, Any], index: int, lot_size: int) -> float:
    if leg["status"] != "active":
        return 0.0
    mark = _leg_mark(day, leg["option_type"], leg["column"], index)
    if not np.isfinite(mark):
        return 0.0
    return (_entry_fill(float(leg["entry_quote"])) - _exit_fill(mark)) * lot_size


def _trade_row(
    day: dict[str, Any],
    leg: dict[str, Any],
    cycle_no: int,
    exit_i: int,
    reason: str,
    run_id: str,
    lot_size: int,
    symbol: str,
) -> dict[str, Any]:
    mark = _leg_mark(day, leg["option_type"], leg["column"], exit_i)
    if not np.isfinite(mark):
        raise ValueError("Open leg has no usable exit quote at the closing timestamp")
    batch = f"{day['date'].isoformat()}-cycle{cycle_no}"
    entry_seq = int(leg["entry_seq"])
    option_type = str(leg["option_type"])
    return {
        "run_id": run_id,
        "trade_id": f"{batch}-{option_type}-{entry_seq}",
        "batch_id": batch,
        "leg_id": f"{option_type}-{entry_seq}",
        "strategy": STRATEGY_NAME,
        "symbol": f"{symbol}_DTE0_{option_type}_{int(leg['strike'])}",
        "side": "SHORT",
        "entry_time": day["timestamps"][int(leg["entry_i"])].isoformat(),
        "exit_time": day["timestamps"][exit_i].isoformat(),
        "quantity": lot_size,
        "entry_price": _entry_fill(float(leg["entry_quote"])),
        "exit_price": _exit_fill(mark),
        "multiplier": 1,
        "fees": 0.0,
    }


def _run_day(
    day: dict[str, Any],
    run_id: str,
    lot_size: int,
    symbol: str,
    starting_realized: float,
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
    completed: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    realized = float(starting_realized)
    entry_seq = 0
    cycle_no = 0
    active_cycle: dict[str, Any] | None = None
    pending_new_cycle = True

    baseline_time = day["timestamps"][0] - timedelta(seconds=1)
    snapshots.append({
        "timestamp": baseline_time.isoformat(),
        "realized_pnl": realized,
        "unrealized_pnl": 0.0,
    })

    def active_legs() -> list[dict[str, Any]]:
        if active_cycle is None:
            return []
        return [leg for leg in active_cycle["legs"].values() if leg["status"] == "active"]

    def cycle_realized() -> float:
        if active_cycle is None:
            return 0.0
        return realized - float(active_cycle["start_realized"])

    def unrealized(index: int) -> float:
        return float(sum(_leg_unrealized(leg, day, index, lot_size) for leg in active_legs()))

    premium_price = float(settings["premium_price"])
    entry_start = str(settings["entry_start"])
    entry_start_seconds = _clock_seconds(entry_start)
    run_start_i = next((i for i, value in enumerate(day["seconds"]) if value >= entry_start_seconds), None)
    if run_start_i is None or run_start_i >= int(day["exit_i"]):
        return completed, snapshots, realized
    leg_sl_pct = float(settings["leg_sl_pct"])
    combined_max_loss_rs = float(settings["combined_max_loss_rs"])
    combined_target_rs = float(settings["combined_target_rs"])
    overall_reentries = int(settings["overall_reentries"])
    exit_i = int(day["exit_i"])

    def open_leg(option_type: str, index: int, reentry_count: int = 0) -> dict[str, Any] | None:
        nonlocal entry_seq
        selected = _select_leg(day, option_type, index, premium_price)
        if selected is None:
            return None
        entry_seq += 1
        selected.update({
            "status": "active",
            "entry_i": index,
            "entry_seq": entry_seq,
            "reentry_count": reentry_count,
        })
        return selected

    def open_cycle(index: int) -> bool:
        nonlocal active_cycle, cycle_no, pending_new_cycle
        ce = open_leg("CE", index)
        pe = open_leg("PE", index)
        if ce is None or pe is None:
            return False
        cycle_no += 1
        active_cycle = {
            "cycle_no": cycle_no,
            "start_realized": realized,
            "legs": {"CE": ce, "PE": pe},
        }
        pending_new_cycle = False
        return True

    def close_leg(leg: dict[str, Any], index: int, reason: str) -> None:
        nonlocal realized
        pnl = _leg_unrealized(leg, day, index, lot_size)
        realized += pnl
        leg["status"] = "closed"
        completed.append(
            _trade_row(
                day,
                leg,
                int(active_cycle["cycle_no"]),
                index,
                reason,
                run_id,
                lot_size,
                symbol,
            )
        )

    def close_cycle(index: int, reason: str) -> None:
        nonlocal active_cycle, pending_new_cycle
        for leg in list(active_legs()):
            close_leg(leg, index, reason)
        active_cycle = None
        pending_new_cycle = cycle_no <= overall_reentries

    for index in range(len(day["times"])):
        if index > exit_i:
            break
        if index < int(run_start_i):
            continue

        if active_cycle is None and pending_new_cycle and index < exit_i:
            open_cycle(index)

        if active_cycle is not None:
            for option_type, leg in list(active_cycle["legs"].items()):
                if leg["status"] != "active":
                    continue
                mark = _leg_mark(day, option_type, int(leg["column"]), index)
                if not np.isfinite(mark):
                    continue
                if mark >= float(leg["entry_quote"]) * (1.0 + leg_sl_pct / 100.0):
                    # Leg SL is final for that leg. No individual leg re-entry.
                    close_leg(leg, index, "LEG_SL")

            if active_cycle is not None:
                cycle_open_pnl = unrealized(index)
                cycle_total_pnl = cycle_realized() + cycle_open_pnl
                if cycle_total_pnl <= -combined_max_loss_rs:
                    close_cycle(index, "COMBINED_MAX_LOSS")
                elif cycle_total_pnl >= combined_target_rs:
                    close_cycle(index, "COMBINED_TARGET")

        if index == exit_i and active_cycle is not None:
            close_cycle(index, "EOD")

        snapshots.append({
            "timestamp": day["timestamps"][index].isoformat(),
            "realized_pnl": float(realized),
            "unrealized_pnl": unrealized(index),
        })

    return completed, snapshots, realized


def _dedupe_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(snapshots, key=lambda item: item["timestamp"])
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for snapshot in ordered:
        if snapshot["timestamp"] in seen:
            continue
        deduped.append(snapshot)
        seen.add(snapshot["timestamp"])
    if any(a["timestamp"] >= b["timestamp"] for a, b in zip(deduped, deduped[1:])):
        raise RuntimeError("Equity snapshot timestamps are not strictly increasing")
    return deduped


def run_strategy(context):
    """Run the fixed 225-premium configuration using the selected dataset."""
    config = dict(context.config or {})
    parameters = dict(config.get("parameters") or {})
    unknown = set(parameters) - ALLOWED_PARAMETERS
    if unknown:
        raise ValueError(f"Unsupported strategy parameters: {sorted(unknown)}")
    settings = dict(FIXED_PARAMETERS)
    if settings["premium_price"] <= 0:
        raise ValueError("premium_price must be positive")
    if settings["leg_sl_pct"] <= 0:
        raise ValueError("leg_sl_pct must be positive")
    if settings["combined_max_loss_rs"] <= 0:
        raise ValueError("combined_max_loss_rs must be positive")
    if settings["combined_target_rs"] <= 0:
        raise ValueError("combined_target_rs must be positive")
    if settings["overall_reentries"] < 0:
        raise ValueError("overall_reentries cannot be negative")
    if settings["entry_start"] not in ENTRY_TIMES:
        raise ValueError("entry_start must be a five-minute grid time from 09:30:00 to 11:00:00")

    symbol = _instrument_symbol(config)
    lot_size = _lot_size(config, symbol)
    start_date, end_date = _period(config)
    market_data_loader = getattr(context, "market_data_loader", None)
    days = _load_days(
        Path(context.market_data),
        symbol,
        market_data_loader,
        start_date=start_date,
        end_date=end_date,
    )

    completed: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    realized = 0.0
    progress = _DayProgress(len(days), getattr(context, "report_progress", None))
    try:
        for day in days:
            day_trades, day_snapshots, realized = _run_day(
                day,
                str(context.run_id),
                lot_size,
                symbol,
                realized,
                settings,
            )
            completed.extend(day_trades)
            snapshots.extend(day_snapshots)
            progress.update()
    finally:
        progress.close()

    equity_snapshots = _dedupe_snapshots(snapshots)
    if equity_snapshots and abs(float(equity_snapshots[-1]["unrealized_pnl"])) > 0.01:
        raise RuntimeError("Final unrealized P&L is not zero; open legs remain")

    return {
        "completed_trades": completed,
        "completed_trade_count": len(completed),
        "equity_snapshots": equity_snapshots or None,
        "metadata": {
            "strategy_name": STRATEGY_NAME,
            "engine": "dashboard-compatible deterministic bar engine",
            "data_rule": "context.market_data, current-expiry DTE=0",
            "sweep_combinations": SWEEP_COMBINATION_COUNT,
            "entry_time": settings["entry_start"],
            "exit_time": EXIT_TIME,
            "strike_rule": (
                f"short CE/PE closest to Rs {settings['premium_price']:g} option premium"
            ),
            "leg_stop_loss": f"{settings['leg_sl_pct']:g}% per leg",
            "leg_sl_reentries": 0,
            "combined_max_loss_rs": settings["combined_max_loss_rs"],
            "combined_target_rs": settings["combined_target_rs"],
            "overall_reentries": settings["overall_reentries"],
            "slippage": "0.5% at entry and exit",
            "lot_size": lot_size,
            "margin_basis_rs": MARGIN_BASIS_RS,
            "drawdown_note": "equity snapshots are quote-marked observations; dashboard calculates analytics",
        },
    }


def run_databricks_sweep(
    spark,
    summary_table="workspace.default.sensex_summary",
    chain_table="workspace.default.sensex_chain",
    start_date=None,
    end_date=None,
    lot_size=20,
    max_combinations=None,
):
    """Run the exhaustive sweep directly against Databricks Unity Catalog tables.

    The table schemas must contain the same columns as the dashboard parquet
    inputs.  The option-chain table is required because strike selection and
    stop/target evaluation use intraday CE/PE quotes.
    """
    summary_columns = [
        "datetime", "DTE", "future_close", "future_atm", "straddle_future",
        "synth_atm", "straddle_synth",
    ]
    chain_columns = ["datetime", "strike", "ce_close", "pe_close", "DTE"]

    summary = spark.table(summary_table).select(*summary_columns).toPandas()
    chain = spark.table(chain_table).select(*chain_columns).toPandas()
    if summary.empty or chain.empty:
        raise ValueError("The Databricks summary or chain table is empty")

    summary = _add_time_columns(summary)
    summary = summary[summary["DTE"].eq(0)].copy()
    chain = _add_time_columns(chain)
    chain = chain[chain["DTE"].eq(0)].copy()

    if start_date is not None:
        start_date = pd.Timestamp(start_date).date()
        summary = summary[summary["date"] >= start_date].copy()
    if end_date is not None:
        end_date = pd.Timestamp(end_date).date()
        summary = summary[summary["date"] <= end_date].copy()

    summary["entry_atm"] = summary["synth_atm"].where(
        summary["synth_atm"].notna(), summary["future_atm"]
    )
    summary["entry_straddle"] = summary["straddle_synth"].where(
        summary["straddle_synth"].notna(), summary["straddle_future"]
    )
    summary = summary[
        summary["future_close"].notna()
        & summary["entry_atm"].notna()
        & summary["entry_straddle"].notna()
    ].copy()
    if summary.empty:
        raise ValueError("No valid DTE=0 summary rows remain after filtering")
    wanted = set(summary["dt"])
    chain = chain[chain["dt"].isin(wanted)].copy()
    if chain.empty:
        raise ValueError("No matching DTE=0 chain rows remain after filtering")

    days = _build_days(summary, chain, tuple(float(x) for x in PREMIUM_PRICES))
    parameter_sets = SWEEP_PARAMETER_SETS
    if max_combinations is not None:
        parameter_sets = parameter_sets[: int(max_combinations)]
    results = []
    total = len(parameter_sets)
    for run_number, parameters in enumerate(parameter_sets, start=1):
        settings = {
            "premium_price": float(parameters["premium_price"]),
            "entry_start": str(parameters["entry_start"]),
            "leg_sl_pct": float(parameters["leg_sl_pct"]),
            "combined_max_loss_rs": float(parameters["combined_max_loss_rs"]),
            "combined_target_rs": float(parameters["combined_target_rs"]),
            "overall_reentries": int(parameters["overall_reentries"]),
        }
        completed = []
        realized = 0.0
        for day in days:
            day_trades, _, realized = _run_day(
                day, f"databricks-{run_number}", int(lot_size), "SENSEX",
                realized, settings,
            )
            completed.extend(day_trades)

        trade_pnl = [
            (float(row["entry_price"]) - float(row["exit_price"]))
            * float(row["quantity"]) * float(row.get("multiplier", 1.0))
            - float(row.get("fees", 0.0))
            for row in completed
        ]
        results.append({
            **parameters,
            "status": "succeeded" if completed else "no_trades",
            "completed_trade_count": len(completed),
            "net_pnl": float(sum(trade_pnl)),
            "max_loss": float(min(trade_pnl)) if trade_pnl else None,
            "win_rate": float(sum(p > 0 for p in trade_pnl) / len(trade_pnl)) if trade_pnl else None,
        })
        if run_number == 1 or run_number % 100 == 0 or run_number == total:
            print(f"Completed {run_number}/{total} combinations")

    return pd.DataFrame(results)


def _fast_parameter_matrix(parameter_sets) -> np.ndarray:
    matrix = np.empty((len(parameter_sets), 6), dtype=np.float64)
    premium_index = {float(value): index for index, value in enumerate(PREMIUM_PRICES)}
    for row_index, parameters in enumerate(parameter_sets):
        premium = float(parameters["premium_price"])
        if premium not in premium_index:
            raise ValueError(f"Unsupported premium_price: {premium}")
        matrix[row_index] = (
            premium_index[premium],
            float(parameters["leg_sl_pct"]),
            float(parameters["combined_max_loss_rs"]),
            float(parameters["combined_target_rs"]),
            float(parameters["overall_reentries"]),
            float(_clock_seconds(parameters["entry_start"])),
        )
    return np.ascontiguousarray(matrix)


def _fast_numeric_data(days: list[dict[str, Any]]) -> dict[str, Any]:
    """Flatten prepared days into compact arrays shared by every sweep row."""
    premium_count = len(PREMIUM_PRICES)
    event_count = sum(len(day["times"]) for day in days)
    max_strikes = max(len(day["strikes"]) for day in days)
    day_offsets = [0]
    exit_indices = []
    seconds = np.empty(event_count, dtype=np.int32)
    selected_columns = np.full((premium_count, 2, event_count), -1, dtype=np.int32)
    quotes = np.full((2, event_count, max_strikes), np.nan, dtype=np.float64)
    quotes_ff = np.full((2, event_count, max_strikes), np.nan, dtype=np.float64)

    offset = 0
    for day in days:
        count = len(day["times"])
        seconds[offset:offset + count] = day["seconds"]
        width = len(day["strikes"])
        quotes[0, offset:offset + count, :width] = day["ce"]
        quotes[1, offset:offset + count, :width] = day["pe"]
        quotes_ff[0, offset:offset + count, :width] = day["ce_ff"]
        quotes_ff[1, offset:offset + count, :width] = day["pe_ff"]
        for premium_index, premium in enumerate(PREMIUM_PRICES):
            for side_index, side in enumerate(("CE", "PE")):
                columns, _, _ = day["selections"][float(premium)][side]
                selected_columns[premium_index, side_index, offset:offset + count] = columns
        offset += count
        day_offsets.append(offset)
        exit_indices.append(int(day["exit_i"]))

    return {
        "day_offsets": np.asarray(day_offsets, dtype=np.int64),
        "exit_indices": np.asarray(exit_indices, dtype=np.int64),
        "seconds": np.ascontiguousarray(seconds),
        "selected_columns": np.ascontiguousarray(selected_columns),
        "quotes": np.ascontiguousarray(quotes),
        "quotes_ff": np.ascontiguousarray(quotes_ff),
    }


if njit is not None:
    @njit(cache=True, parallel=True)
    def _fast_kernel(parameters, day_offsets, exit_indices, seconds, selected_columns, quotes, quotes_ff, lot_size, slippage):
        results = np.full((parameters.shape[0], 7), np.nan, dtype=np.float64)
        for parameter_index in prange(parameters.shape[0]):
            premium_index = int(parameters[parameter_index, 0])
            leg_sl = parameters[parameter_index, 1] / 100.0
            max_loss = parameters[parameter_index, 2]
            target = parameters[parameter_index, 3]
            reentries = int(parameters[parameter_index, 4])
            entry_seconds = int(parameters[parameter_index, 5])
            realized = 0.0
            minimum_trade = 0.0
            trade_count = 0
            win_count = 0
            loss_streak = 0
            maximum_consecutive_losses = 0
            peak_equity = 0.0
            maximum_drawdown = 0.0

            for day_index in range(day_offsets.shape[0] - 1):
                day_start = int(day_offsets[day_index])
                day_end = int(day_offsets[day_index + 1])
                exit_index = day_start + int(exit_indices[day_index])
                cycle_active = False
                ce_active = False
                pe_active = False
                pending = True
                cycle_number = 0
                cycle_start_realized = 0.0
                ce_entry = np.nan
                pe_entry = np.nan
                ce_column = -1
                pe_column = -1

                for event_index in range(day_start, exit_index + 1):
                    # Do not open or evaluate a cycle before the requested
                    # entry time.  This must remain inside the numeric kernel
                    # because entry_start is one of the sweep dimensions.
                    if seconds[event_index] < entry_seconds:
                        continue

                    if not cycle_active and pending and event_index < exit_index:
                        ce_column = selected_columns[premium_index, 0, event_index]
                        pe_column = selected_columns[premium_index, 1, event_index]
                        ce_entry = np.nan
                        pe_entry = np.nan
                        if ce_column >= 0:
                            ce_entry = quotes[0, event_index, ce_column]
                        if pe_column >= 0:
                            pe_entry = quotes[1, event_index, pe_column]
                        if np.isfinite(ce_entry) and ce_entry > 0.0 and np.isfinite(pe_entry) and pe_entry > 0.0:
                            ce_active = True
                            pe_active = True
                            cycle_active = True
                            pending = False
                            cycle_number += 1
                            cycle_start_realized = realized

                    if ce_active:
                        ce_mark = quotes_ff[0, event_index, ce_column]
                        if np.isfinite(ce_mark) and ce_mark >= ce_entry * (1.0 + leg_sl):
                            pnl = (ce_entry * (1.0 - slippage) - ce_mark * (1.0 + slippage)) * lot_size
                            realized += pnl
                            ce_active = False
                            trade_count += 1
                            if pnl > 0.0:
                                win_count += 1
                                loss_streak = 0
                            else:
                                loss_streak += 1
                                if loss_streak > maximum_consecutive_losses:
                                    maximum_consecutive_losses = loss_streak
                            if trade_count == 1 or pnl < minimum_trade:
                                minimum_trade = pnl

                    if pe_active:
                        pe_mark = quotes_ff[1, event_index, pe_column]
                        if np.isfinite(pe_mark) and pe_mark >= pe_entry * (1.0 + leg_sl):
                            pnl = (pe_entry * (1.0 - slippage) - pe_mark * (1.0 + slippage)) * lot_size
                            realized += pnl
                            pe_active = False
                            trade_count += 1
                            if pnl > 0.0:
                                win_count += 1
                                loss_streak = 0
                            else:
                                loss_streak += 1
                                if loss_streak > maximum_consecutive_losses:
                                    maximum_consecutive_losses = loss_streak
                            if trade_count == 1 or pnl < minimum_trade:
                                minimum_trade = pnl

                    open_pnl = 0.0
                    if ce_active and np.isfinite(quotes_ff[0, event_index, ce_column]):
                        open_pnl += (ce_entry * (1.0 - slippage) - quotes_ff[0, event_index, ce_column] * (1.0 + slippage)) * lot_size
                    if pe_active and np.isfinite(quotes_ff[1, event_index, pe_column]):
                        open_pnl += (pe_entry * (1.0 - slippage) - quotes_ff[1, event_index, pe_column] * (1.0 + slippage)) * lot_size
                    cycle_total = realized - cycle_start_realized + open_pnl
                    equity = realized + open_pnl
                    if equity > peak_equity:
                        peak_equity = equity
                    if equity - peak_equity < maximum_drawdown:
                        maximum_drawdown = equity - peak_equity
                    close_cycle = cycle_total <= -max_loss or cycle_total >= target
                    if event_index == exit_index:
                        close_cycle = True

                    # A cycle remains active after one or both legs have
                    # already been stopped.  The original day engine still
                    # closes that cycle at the combined limit or EOD and may
                    # then permit a re-entry.  Do not use leg activity as a
                    # proxy for cycle activity here.
                    if close_cycle and cycle_active:
                        if ce_active:
                            ce_mark = quotes_ff[0, event_index, ce_column]
                            if np.isfinite(ce_mark):
                                pnl = (ce_entry * (1.0 - slippage) - ce_mark * (1.0 + slippage)) * lot_size
                                realized += pnl
                                trade_count += 1
                                if pnl > 0.0:
                                    win_count += 1
                                    loss_streak = 0
                                else:
                                    loss_streak += 1
                                    if loss_streak > maximum_consecutive_losses:
                                        maximum_consecutive_losses = loss_streak
                                if trade_count == 1 or pnl < minimum_trade:
                                    minimum_trade = pnl
                            ce_active = False
                        if pe_active:
                            pe_mark = quotes_ff[1, event_index, pe_column]
                            if np.isfinite(pe_mark):
                                pnl = (pe_entry * (1.0 - slippage) - pe_mark * (1.0 + slippage)) * lot_size
                                realized += pnl
                                trade_count += 1
                                if pnl > 0.0:
                                    win_count += 1
                                    loss_streak = 0
                                else:
                                    loss_streak += 1
                                    if loss_streak > maximum_consecutive_losses:
                                        maximum_consecutive_losses = loss_streak
                                if trade_count == 1 or pnl < minimum_trade:
                                    minimum_trade = pnl
                            pe_active = False
                        cycle_active = False
                        pending = event_index < exit_index and cycle_number <= reentries

            results[parameter_index, 0] = 1.0 if trade_count else 0.0
            results[parameter_index, 1] = trade_count
            results[parameter_index, 2] = realized
            results[parameter_index, 3] = minimum_trade if trade_count else np.nan
            results[parameter_index, 4] = win_count / trade_count if trade_count else np.nan
            results[parameter_index, 5] = maximum_drawdown
            results[parameter_index, 6] = maximum_consecutive_losses
        return results


def run_databricks_fast_sweep(
    spark,
    summary_table="workspace.default.sensex_summary",
    chain_table="workspace.default.sensex_chain",
    start_date=None,
    end_date=None,
    lot_size=20,
    batch_size=25_000,
    workers=None,
    max_combinations=None,
):
    """Run the same sweep rules with one prepared market pass and Numba batches.

    This returns compact screening metrics. Selected winners should be rerun
    through ``run_databricks_sweep`` for authoritative trade rows.
    """
    if numba is None:
        raise RuntimeError("Numba is required for the fast Databricks sweep")
    summary_columns = [
        "datetime", "DTE", "future_close", "future_atm", "straddle_future",
        "synth_atm", "straddle_synth",
    ]
    chain_columns = ["datetime", "strike", "ce_close", "pe_close", "DTE"]
    summary_query = spark.table(summary_table).select(*summary_columns).where("DTE = 0")
    chain_query = spark.table(chain_table).select(*chain_columns).where("DTE = 0")
    if start_date is not None:
        start = pd.Timestamp(start_date).date().isoformat()
        summary_query = summary_query.where(
            f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) >= DATE '{start}'"
        )
        chain_query = chain_query.where(
            f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) >= DATE '{start}'"
        )
    if end_date is not None:
        end = pd.Timestamp(end_date).date().isoformat()
        summary_query = summary_query.where(
            f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) <= DATE '{end}'"
        )
        chain_query = chain_query.where(
            f"to_date(to_timestamp(datetime, 'dd/MM/yyyy HH:mm:ss')) <= DATE '{end}'"
        )
    summary = summary_query.toPandas()
    chain = chain_query.toPandas()
    summary = _add_time_columns(summary)
    chain = _add_time_columns(chain)
    summary["entry_atm"] = summary["synth_atm"].where(summary["synth_atm"].notna(), summary["future_atm"])
    summary["entry_straddle"] = summary["straddle_synth"].where(summary["straddle_synth"].notna(), summary["straddle_future"])
    summary = summary[summary["future_close"].notna() & summary["entry_atm"].notna() & summary["entry_straddle"].notna()].copy()
    if summary.empty or chain.empty:
        raise ValueError("The selected period contains no usable DTE=0 summary and chain rows")
    chain = chain[chain["dt"].isin(set(summary["dt"]))].copy()
    days = _build_days(summary, chain, tuple(float(value) for value in PREMIUM_PRICES))
    numeric = _fast_numeric_data(days)
    parameter_sets = list(SWEEP_PARAMETER_SETS)
    if max_combinations is not None:
        parameter_sets = parameter_sets[: int(max_combinations)]
    matrix = _fast_parameter_matrix(parameter_sets)
    previous_threads = numba.get_num_threads()
    selected_threads = previous_threads if workers is None else max(1, int(workers))
    numba.set_num_threads(selected_threads)
    try:
        rows = []
        for start_index in range(0, len(matrix), int(batch_size)):
            end_index = min(len(matrix), start_index + int(batch_size))
            metrics = _fast_kernel(
                matrix[start_index:end_index],
                numeric["day_offsets"], numeric["exit_indices"], numeric["seconds"],
                numeric["selected_columns"], numeric["quotes"], numeric["quotes_ff"],
                int(lot_size), float(SLIPPAGE),
            )
            for parameter, metric in zip(parameter_sets[start_index:end_index], metrics):
                rows.append({
                    **parameter,
                    "status": "succeeded" if metric[0] else "no_trades",
                    "completed_trade_count": int(metric[1]),
                    "net_pnl": float(metric[2]),
                    "max_loss": float(metric[3]) if np.isfinite(metric[3]) else None,
                    "win_rate": float(metric[4]) if np.isfinite(metric[4]) else None,
                    "max_drawdown": float(metric[5]),
                    "max_consecutive_losses": int(metric[6]),
                })
            print(f"Completed {end_index}/{len(matrix)} combinations")
    finally:
        numba.set_num_threads(previous_threads)
    return pd.DataFrame(rows)
