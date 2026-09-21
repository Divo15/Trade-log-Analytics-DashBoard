"""Dashboard sweep: fixed 09:45:59 balanced short straddle.

Rules encoded from the supplied strategy note:
* DTE=0 current-expiry data from the dashboard-selected dataset.
* First entry at or after 09:45:59.
* Short CE and PE are selected independently closest to 40% of the ATM straddle
  premium, on the ATM-or-outward side of the ATM strike.
* Individual leg SL closes that leg if its quote rises 30% over its entry quote.
* A stopped leg may re-enter up to two times.
* Combined cycle exits when cycle P&L reaches -Rs 1,000 or +Rs 4,000.
* Up to three overall re-entries after a combined cycle exit.
* 0.5% slippage is applied to entries and exits.
* Remaining open legs close at the first available quote at or after 15:15:00.
* Sweep variations keep the same strategy structure and test nearby parameter
  combinations around the supplied baseline.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "sweep"

SWEEP_PARAMETER_SETS = (
    {
        "premium_pct": 40.0,
        "leg_sl_pct": 30.0,
        "combined_max_loss_rs": 1_000.0,
        "combined_target_rs": 4_000.0,
        "leg_sl_reentries": 2,
        "overall_reentries": 3,
    },
    {
        "premium_pct": 35.0,
        "leg_sl_pct": 30.0,
        "combined_max_loss_rs": 1_000.0,
        "combined_target_rs": 4_000.0,
        "leg_sl_reentries": 2,
        "overall_reentries": 3,
    },
    {
        "premium_pct": 45.0,
        "leg_sl_pct": 30.0,
        "combined_max_loss_rs": 1_000.0,
        "combined_target_rs": 4_000.0,
        "leg_sl_reentries": 2,
        "overall_reentries": 3,
    },
     {
            "premium_pct": 45.0,
            "leg_sl_pct": 25.0,
            "combined_max_loss_rs": 1_000.0,
            "combined_target_rs": 4_000.0,
            "leg_sl_reentries": 2,
            "overall_reentries": 3,
    },
    
    {
        "premium_pct": 40.0,
        "leg_sl_pct": 25.0,
        "combined_max_loss_rs": 1_000.0,
        "combined_target_rs": 3_500.0,
        "leg_sl_reentries": 2,
        "overall_reentries": 3,
    },
    {
        "premium_pct": 40.0,
        "leg_sl_pct": 35.0,
        "combined_max_loss_rs": 1_200.0,
        "combined_target_rs": 4_500.0,
        "leg_sl_reentries": 2,
        "overall_reentries": 3,
    },
    {
        "premium_pct": 35.0,
        "leg_sl_pct": 25.0,
        "combined_max_loss_rs": 1_000.0,
        "combined_target_rs": 3_500.0,
        "leg_sl_reentries": 2,
        "overall_reentries": 3,
    },
    {
        "premium_pct": 45.0,
        "leg_sl_pct": 35.0,
        "combined_max_loss_rs": 1_200.0,
        "combined_target_rs": 4_500.0,
        "leg_sl_reentries": 2,
        "overall_reentries": 3,
    },
    {
        "premium_pct": 40.0,
        "leg_sl_pct": 30.0,
        "combined_max_loss_rs": 1_200.0,
        "combined_target_rs": 5_000.0,
        "leg_sl_reentries": 2,
        "overall_reentries": 3,
    },
    {
        "premium_pct": 35.0,
        "leg_sl_pct": 30.0,
        "combined_max_loss_rs": 800.0,
        "combined_target_rs": 3_000.0,
        "leg_sl_reentries": 1,
        "overall_reentries": 3,
    },
    {
        "premium_pct": 45.0,
        "leg_sl_pct": 30.0,
        "combined_max_loss_rs": 1_500.0,
        "combined_target_rs": 5_000.0,
        "leg_sl_reentries": 2,
        "overall_reentries": 2,
    },
)
ALLOWED_PARAMETERS = {
    "premium_pct",
    "leg_sl_pct",
    "combined_max_loss_rs",
    "combined_target_rs",
    "leg_sl_reentries",
    "overall_reentries",
    "lot_size",
}

STRATEGY_NAME = "fixed-094559-balanced-short-straddle"
ENTRY_START = "09:45:59"
EXIT_TIME = "15:15:00"
PREMIUM_PCT_OF_ATM_STRADDLE = 40.0
LEG_SL_PCT = 30.0
MAX_LEG_SL_REENTRIES = 2
COMBINED_MAX_LOSS_RS = 1_000.0
COMBINED_TARGET_RS = 4_000.0
OVERALL_REENTRIES = 3
SLIPPAGE = 0.005
MARGIN_BASIS_RS = 300_000
DEFAULT_LOT_SIZE_BY_SYMBOL = {
    "NIFTY": 65,
    "SENSEX": 20,
}
TIMEZONE = ZoneInfo("Asia/Kolkata")
PRECOMPUTED_PREMIUM_PCTS = tuple(sorted({
    PREMIUM_PCT_OF_ATM_STRADDLE,
    *(float(item["premium_pct"]) for item in SWEEP_PARAMETER_SETS),
}))


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
        return _build_days(summary, chain, PRECOMPUTED_PREMIUM_PCTS)

    shared_object = getattr(loader, "shared_object", None)
    if callable(shared_object):
        return shared_object(
            sources,
            (
                "fixed-094559-days-v2",
                symbol,
                str(start_date),
                str(end_date),
                ENTRY_START,
                EXIT_TIME,
                PRECOMPUTED_PREMIUM_PCTS,
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
    straddle: np.ndarray,
    premium_pcts: tuple[float, ...],
) -> dict[float, dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]]:
    """Select every configured CE/PE contract once for this prepared day."""
    selections = {}
    for premium_pct in premium_pcts:
        side_values = {}
        for option_type, quotes in (("CE", ce), ("PE", pe)):
            columns = np.full(len(atm), -1, dtype=int)
            selected_strikes = np.full(len(atm), -1, dtype=int)
            selected_quotes = np.full(len(atm), np.nan, dtype=float)
            for index in range(len(atm)):
                if option_type == "CE":
                    mask = (strikes >= atm[index]) & np.isfinite(quotes[index]) & (quotes[index] > 0)
                else:
                    mask = (strikes <= atm[index]) & np.isfinite(quotes[index]) & (quotes[index] > 0)
                candidates = np.flatnonzero(mask)
                if not len(candidates):
                    continue
                target = float(straddle[index]) * float(premium_pct) / 100.0
                score = np.abs(quotes[index, candidates] - target) + np.abs(strikes[candidates] - atm[index]) * 1e-9
                column = int(candidates[int(np.argmin(score))])
                columns[index] = column
                selected_strikes[index] = int(strikes[column])
                selected_quotes[index] = float(quotes[index, column])
            columns.flags.writeable = False
            selected_strikes.flags.writeable = False
            selected_quotes.flags.writeable = False
            side_values[option_type] = (columns, selected_strikes, selected_quotes)
        selections[float(premium_pct)] = side_values
    return selections


def _build_days(
    summary: pd.DataFrame,
    chain: pd.DataFrame,
    premium_pcts: tuple[float, ...],
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
            strikes, ce, pe, atm, straddle, premium_pcts
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
    premium_pct: float,
) -> dict[str, Any] | None:
    prepared = day["selections"].get(float(premium_pct), {}).get(option_type)
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

    # Direct single-run callers may use a premium target outside the declared
    # sweep grid. Preserve the original selection behavior for that case.
    target = float(day["straddle"][index]) * premium_pct / 100.0
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

    premium_pct = float(settings["premium_pct"])
    leg_sl_pct = float(settings["leg_sl_pct"])
    combined_max_loss_rs = float(settings["combined_max_loss_rs"])
    combined_target_rs = float(settings["combined_target_rs"])
    max_leg_sl_reentries = int(settings["leg_sl_reentries"])
    overall_reentries = int(settings["overall_reentries"])
    exit_i = int(day["exit_i"])

    def open_leg(option_type: str, index: int, reentry_count: int = 0) -> dict[str, Any] | None:
        nonlocal entry_seq
        selected = _select_leg(day, option_type, index, premium_pct)
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
        pending_new_cycle = cycle_no <= OVERALL_REENTRIES

    for index in range(len(day["times"])):
        if index > exit_i:
            break
        if index < int(day["start_i"]):
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
                    reentry_count = int(leg["reentry_count"])
                    close_leg(leg, index, "LEG_SL")
                    if reentry_count < max_leg_sl_reentries and index < exit_i:
                        replacement = open_leg(option_type, index, reentry_count + 1)
                        if replacement is not None:
                            active_cycle["legs"][option_type] = replacement

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
    """Run the fixed balanced strategy using the dashboard-selected dataset."""
    config = dict(context.config or {})
    parameters = dict(config.get("parameters") or {})
    unknown = set(parameters) - ALLOWED_PARAMETERS
    if unknown:
        raise ValueError(f"Unsupported strategy parameters: {sorted(unknown)}")
    settings = {
        "premium_pct": float(parameters.get("premium_pct", PREMIUM_PCT_OF_ATM_STRADDLE)),
        "leg_sl_pct": float(parameters.get("leg_sl_pct", LEG_SL_PCT)),
        "combined_max_loss_rs": float(parameters.get("combined_max_loss_rs", COMBINED_MAX_LOSS_RS)),
        "combined_target_rs": float(parameters.get("combined_target_rs", COMBINED_TARGET_RS)),
        "leg_sl_reentries": int(parameters.get("leg_sl_reentries", MAX_LEG_SL_REENTRIES)),
        "overall_reentries": int(parameters.get("overall_reentries", OVERALL_REENTRIES)),
    }
    if settings["premium_pct"] <= 0:
        raise ValueError("premium_pct must be positive")
    if settings["leg_sl_pct"] <= 0:
        raise ValueError("leg_sl_pct must be positive")
    if settings["combined_max_loss_rs"] <= 0:
        raise ValueError("combined_max_loss_rs must be positive")
    if settings["combined_target_rs"] <= 0:
        raise ValueError("combined_target_rs must be positive")
    if settings["leg_sl_reentries"] < 0:
        raise ValueError("leg_sl_reentries cannot be negative")
    if settings["overall_reentries"] < 0:
        raise ValueError("overall_reentries cannot be negative")

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
            "entry_time": ENTRY_START,
            "exit_time": EXIT_TIME,
            "strike_rule": (
                f"short CE/PE closest to {settings['premium_pct']:g}% "
                "of ATM straddle premium"
            ),
            "leg_stop_loss": f"{settings['leg_sl_pct']:g}% per leg",
            "leg_sl_reentries": settings["leg_sl_reentries"],
            "combined_max_loss_rs": settings["combined_max_loss_rs"],
            "combined_target_rs": settings["combined_target_rs"],
            "overall_reentries": settings["overall_reentries"],
            "slippage": "0.5% at entry and exit",
            "lot_size": lot_size,
            "margin_basis_rs": MARGIN_BASIS_RS,
            "drawdown_note": "equity snapshots are quote-marked observations; dashboard calculates analytics",
        },
    }
