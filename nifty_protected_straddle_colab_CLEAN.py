from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from datetime import time
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from trade_log_exporter import export_trade_log, validate_trade_log_csv


STRATEGY_CONTRACT_VERSION = "1"
RUN_MODE = "single"
SWEEP_PARAMETER_SETS = ()


SLIPPAGE = 0.005
def structure_stop_debit(entry_credit_points: float, stop_loss_multiple: float) -> float:
    return entry_credit_points * (1 + stop_loss_multiple)


def short_leg_stop_price(entry_short_price: float, stop_loss_multiple: float) -> float:
    return entry_short_price * (1 + stop_loss_multiple)


ENGINE_DEFAULTS: dict[str, Any] = {
    'base': None,
    'start_date': '2023-01-02',
    'end_date': '2026-05-05',
    'cycle_reset_dte_jump': 10.0,
    'lot_size': 65,
    'capital_per_trade': 300000.0,
    'wing_multiple': 1.5,
    'fixed_wing_points': None,
    'target_max_loss_points': None,
    'target_max_loss_rupees': None,
    'max_wing_points': None,
    'sd_wing_multiple': None,
    'sd_lookback_days': 20,
    'otm_multiple': 0.0,
    'take_profit_pct': 0.7,
    'reentry_take_profit_pct': None,
    'stop_loss_multiple': 1.5,
    'reentry_stop_loss_multiple': None,
    'profit_lock_trigger_pct': None,
    'profit_lock_exit_pct': 0.0,
    'full_exit_on_side_take_profit': False,
    'full_exit_on_leg_stop': False,
    'calm_leg_stop_atm_reentry': False,
    'calm_leg_stop_reentry_otm_multiple': 0.0,
    'use_leg_stop': False,
    'double_winning_leg_on_stop': False,
    'double_winning_leg_on_leg_stop': False,
    'replace_losing_leg_on_trigger': False,
    'replace_losing_leg_trigger_pct': 0.7,
    'replace_losing_leg_otm_multiple': 0.1,
    'roll_losing_side_on_leg_stop': False,
    'resell_winning_side_on_leg_stop': False,
    'winner_resell_otm_multiple': 0.1,
    'winner_resell_min_premium_points': None,
    'winner_resell_min_dte': None,
    'double_winner_min_premium_points': None,
    'double_winner_add_fraction': 1.0,
    'double_winner_min_dte': None,
    'double_winner_max_distance_multiple': None,
    'double_winner_trigger_pct': None,
    'entry_day_offset': 0,
    'max_entry_wait_days': None,
    'wait_for_valid_entry': False,
    'entry_time': None,
    'square_off_time': None,
    'min_straddle_pct': None,
    'reentry_min_straddle_pct': None,
    'third_entry_min_straddle_pct': None,
    'max_straddle_pct': None,
    'min_entry_credit_points': None,
    'max_entry_loss_points': None,
    'reentry_min_entry_credit_points': None,
    'reentry_max_entry_credit_points': None,
    'reentry_otm_multiple': None,
    'directional_reentry_otm_multiple': None,
    'strong_directional_reentry_otm_multiple': None,
    'reentry_calm_only': False,
    'third_entry_min_entry_credit_points': None,
    'third_entry_max_entry_credit_points': None,
    'third_entry_take_profit_pct': None,
    'third_entry_stop_loss_multiple': None,
    'reentry_on_expiry': False,
    'reentry_on_leg_stop': False,
    'leg_stop_atm_reentry_to_expiry': False,
    'leg_stop_atm_reentry_min_dte': 3,
    'leg_stop_reentry_otm_multiple': 0.0,
    'force_exit_dte': None,
    'max_hold_minutes': None,
    'main_trade_force_exit_dte': None,
    'acceptable_premium_exit_dte': None,
    'rich_straddle_pct': None,
    'rich_entry_credit_points': None,
    'roll_trigger_pct': None,
    'max_rolls': 0,
    'no_roll_last_dte': 5,
    'regime_lookback_days': 0,
    'max_regime_trend_pct': None,
    'max_regime_range_pct': None,
    'max_gap_pct': None,
    'directional_mode_enabled': False,
    'directional_trend_trigger_pct': None,
    'directional_range_trigger_pct': None,
    'directional_gap_trigger_pct': None,
    'directional_consistency_trigger_pct': None,
    'directional_acceleration_trigger_pct': None,
    'directional_otm_multiple': None,
    'directional_min_straddle_pct': None,
    'directional_min_entry_credit_points': None,
    'directional_take_profit_pct': None,
    'directional_stop_loss_multiple': None,
    'directional_reentry_recenter_distance_multiple': None,
    'directional_force_exit_dte': None,
    'directional_wing_multiple': None,
    'strong_directional_mode_enabled': False,
    'strong_directional_trend_trigger_pct': None,
    'strong_directional_range_trigger_pct': None,
    'strong_directional_gap_trigger_pct': None,
    'strong_directional_consistency_trigger_pct': None,
    'strong_directional_acceleration_trigger_pct': None,
    'strong_directional_otm_multiple': None,
    'strong_directional_wing_multiple': None,
    'strong_directional_min_straddle_pct': None,
    'strong_directional_min_entry_credit_points': None,
    'strong_directional_take_profit_pct': None,
    'strong_directional_stop_loss_multiple': None,
    'strong_directional_force_exit_dte': None,
    'min_premium_to_range_ratio': None,
    'max_premium_to_range_ratio': None,
    'max_reentries': 0,
    'reentry_on_structure_stop_only': False,
    'reentry_on_tp_or_sl_only': False,
    'reentry_after_first_on_tp_or_sl_only': False,
    'reentry_min_dte': None,
    'reentry_exact_dte': None,
    'reentry_entry_time': None,
    'reentry_recenter_distance_multiple': None,
}


def engine_defaults() -> SimpleNamespace:
    return SimpleNamespace(**ENGINE_DEFAULTS)


def parse_time_string(value: str | None) -> time | None:
    if value in (None, ""):
        return None
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return time(parts[0], parts[1], 0)
    if len(parts) == 3:
        return time(parts[0], parts[1], parts[2])
    raise ValueError(f"Invalid time value: {value}")


def row_time(row: pd.Series) -> time:
    ts_value = row["ts"] if "ts" in row else pd.to_datetime(row["datetime"], format="%d/%m/%Y %H:%M:%S")
    return pd.Timestamp(ts_value).time()


def should_force_exit(row: pd.Series, force_exit_dte: int | None, square_off_time: time | None) -> bool:
    if force_exit_dte is None:
        return False
    dte = int(row["DTE"])
    if dte < force_exit_dte:
        return True
    if dte > force_exit_dte:
        return False
    if square_off_time is None:
        return True
    return row_time(row) >= square_off_time


def should_max_hold_exit(row: pd.Series, entry_ts: pd.Timestamp, max_hold_minutes: float | None) -> bool:
    if max_hold_minutes is None:
        return False
    row_ts = row["ts"] if "ts" in row else pd.to_datetime(row["datetime"], format="%d/%m/%Y %H:%M:%S")
    return pd.Timestamp(row_ts) >= entry_ts + timedelta(minutes=max_hold_minutes)


def load_summary(base: Path, start_date: str, end_date: str) -> pd.DataFrame:
    con = duckdb.connect()
    con.execute("PRAGMA disable_progress_bar")
    summary_path = (base / "nifty_summary.parquet").as_posix()
    summary = con.execute(
        """
        select *
        from read_parquet(?)
        where cast(strptime(datetime, '%d/%m/%Y %H:%M:%S') as date)
              between CAST(? AS DATE) and CAST(? AS DATE)
        order by strptime(datetime, '%d/%m/%Y %H:%M:%S')
        """, [summary_path, start_date, end_date]
    ).fetchdf()
    summary["ts"] = pd.to_datetime(summary["datetime"], format="%d/%m/%Y %H:%M:%S")
    return summary


def add_cycle_id(summary: pd.DataFrame, cycle_reset_dte_jump: float) -> pd.DataFrame:
    summary = summary.copy()
    summary["trade_date"] = summary["ts"].dt.date
    daily = summary.groupby("trade_date", as_index=False)["DTE"].max()
    daily["new_cycle"] = daily["DTE"].diff().fillna(0) > cycle_reset_dte_jump
    daily["cycle_id"] = daily["new_cycle"].cumsum()
    return summary.merge(daily[["trade_date", "cycle_id"]], on="trade_date", how="left")


def snap_strike(value: float, strikes: list[int]) -> int:
    return min(strikes, key=lambda strike: abs(strike - value))


def realized_sd_move_points(
    summary: pd.DataFrame,
    entry_ts: pd.Timestamp,
    spot: float,
    dte: int,
    lookback_days: int,
) -> float | None:
    daily = summary.loc[:, ["trade_date", "ts", "future_close"]].copy()
    daily = daily.sort_values("ts").groupby("trade_date", as_index=False).last()
    prior_days = daily[daily["trade_date"] < entry_ts.date()].tail(lookback_days + 1).copy()
    if len(prior_days) < max(3, lookback_days // 2):
        return None
    returns = prior_days["future_close"].pct_change().dropna()
    if len(returns) < 2:
        return None
    daily_std = float(returns.std())
    if pd.isna(daily_std) or daily_std <= 0:
        return None
    return spot * daily_std * (max(dte, 1) ** 0.5)


def load_cycle_chain(
    con: duckdb.DuckDBPyConnection,
    chain_glob: str,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
) -> pd.DataFrame:
    start_text = start_ts.strftime("%d/%m/%Y %H:%M:%S")
    end_text = end_ts.strftime("%d/%m/%Y %H:%M:%S")
    return con.execute(
        """
        select datetime, strike, ce_open, ce_close, pe_open, pe_close
        from read_parquet(?)
        where strptime(datetime, '%d/%m/%Y %H:%M:%S')
              between strptime(?, '%d/%m/%Y %H:%M:%S')
                  and strptime(?, '%d/%m/%Y %H:%M:%S')
        order by strptime(datetime, '%d/%m/%Y %H:%M:%S'), strike
        """, [chain_glob, start_text, end_text]
    ).fetchdf()


def option_series(chain: pd.DataFrame, strike: int, side: str) -> pd.DataFrame:
    prefix = side.lower()
    columns = [f"{prefix}_open", f"{prefix}_close"]
    df = chain.loc[chain["strike"] == strike, ["datetime", *columns]].dropna().copy()
    df["ts"] = pd.to_datetime(df["datetime"], format="%d/%m/%Y %H:%M:%S")
    df = df.sort_values("ts").drop_duplicates("ts", keep="first")
    return df.rename(columns={
        f"{prefix}_open": f"{side}_{strike}_open",
        f"{prefix}_close": f"{side}_{strike}",
    })


def build_joined_path(
    chain: pd.DataFrame,
    summary_slice: pd.DataFrame,
    short_call: int,
    short_put: int,
    long_call: int,
    long_put: int,
) -> pd.DataFrame:
    joined = summary_slice[["datetime", "DTE", "future_close", "future_atm", "straddle_future"]].copy()
    joined["ts"] = pd.to_datetime(joined["datetime"], format="%d/%m/%Y %H:%M:%S")
    joined = joined.sort_values("ts").drop_duplicates("ts", keep="first").reset_index(drop=True)

    for strike, side in (
        (short_call, "CE"),
        (short_put, "PE"),
        (long_call, "CE"),
        (long_put, "PE"),
    ):
        series = option_series(chain, strike, side)
        if series.empty:
            return pd.DataFrame()
        close_col = f"{side}_{strike}"
        open_col = f"{close_col}_open"
        # Use only the quote recorded for this candle.  Matching a later quote
        # to an earlier signal is look-ahead bias and can change stop/target
        # decisions when the option chain is sparse.
        joined = joined.merge(
            series[["ts", open_col, close_col]], on="ts", how="inner", validate="one_to_one"
        )
        joined = joined[joined[open_col].notna() & joined[close_col].notna()].copy()
        if joined.empty:
            return joined

    return joined.reset_index(drop=True)


def evaluate_event(
    joined: pd.DataFrame,
    short_call: int,
    short_put: int,
    long_call: int,
    long_put: int,
    entry_credit_points: float,
    take_profit_pct: float,
    stop_loss_multiple: float,
    force_exit_dte: int | None,
    square_off_time: time | None,
    roll_trigger_pct: float | None,
    no_roll_last_dte: int,
    use_leg_stop: bool,
    profit_lock_trigger_pct: float | None,
    profit_lock_exit_pct: float,
    max_hold_minutes: float | None,
) -> tuple[pd.Series, float, str]:
    path = joined.copy()
    open_pnl = []
    entry_ts = path.iloc[0]["ts"] if "ts" in path.columns else pd.to_datetime(path.iloc[0]["datetime"], format="%d/%m/%Y %H:%M:%S")
    entry_ts = pd.Timestamp(entry_ts)
    wing_distance = min(long_call - short_call, short_put - long_put)
    short_call_entry = float(path.iloc[0][f"CE_{short_call}"]) * (1 - SLIPPAGE)
    short_put_entry = float(path.iloc[0][f"PE_{short_put}"]) * (1 - SLIPPAGE)

    for _, row in path.iterrows():
        exit_debit = (
            float(row[f"CE_{short_call}"]) * (1 + SLIPPAGE)
            + float(row[f"PE_{short_put}"]) * (1 + SLIPPAGE)
            - float(row[f"CE_{long_call}"]) * (1 - SLIPPAGE)
            - float(row[f"PE_{long_put}"]) * (1 - SLIPPAGE)
        )
        open_pnl.append(entry_credit_points - exit_debit)

    path["open_pnl_points"] = open_pnl
    path["exit_debit_points"] = entry_credit_points - path["open_pnl_points"]
    tp_threshold = entry_credit_points * take_profit_pct
    sl_debit = structure_stop_debit(entry_credit_points, stop_loss_multiple)
    call_leg_sl = short_leg_stop_price(short_call_entry, stop_loss_multiple)
    put_leg_sl = short_leg_stop_price(short_put_entry, stop_loss_multiple)
    profit_lock_armed = False

    for _, row in path.iloc[1:].iterrows():
        if should_max_hold_exit(row, entry_ts, max_hold_minutes):
            return row, float(row["open_pnl_points"]), "max_hold_exit"
        if should_force_exit(row, force_exit_dte, square_off_time):
            return row, float(row["open_pnl_points"]), "time_exit"
        if float(row["open_pnl_points"]) >= tp_threshold:
            return row, float(row["open_pnl_points"]), "take_profit"
        if (
            profit_lock_trigger_pct is not None
            and float(row["open_pnl_points"]) >= entry_credit_points * profit_lock_trigger_pct
        ):
            profit_lock_armed = True
        if (
            profit_lock_armed
            and float(row["open_pnl_points"]) <= entry_credit_points * profit_lock_exit_pct
        ):
            return row, float(row["open_pnl_points"]), "profit_lock"
        if use_leg_stop:
            call_mark = float(row[f"CE_{short_call}"]) * (1 + SLIPPAGE)
            put_mark = float(row[f"PE_{short_put}"]) * (1 + SLIPPAGE)
            if call_mark >= call_leg_sl or put_mark >= put_leg_sl:
                return row, float(row["open_pnl_points"]), "leg_stop"
        elif float(row["exit_debit_points"]) >= sl_debit:
            return row, float(row["open_pnl_points"]), "stop_loss"
        if roll_trigger_pct is not None and int(row["DTE"]) > no_roll_last_dte:
            center_strike = (short_call + short_put) / 2.0
            if abs(float(row["future_close"]) - center_strike) >= wing_distance * roll_trigger_pct:
                return row, float(row["open_pnl_points"]), "roll"

    final_row = path.iloc[-1]
    return final_row, float(final_row["open_pnl_points"]), "expiry_exit"


def regime_allows_entry(
    summary: pd.DataFrame,
    cycle_summary: pd.DataFrame,
    entry_ts: pd.Timestamp,
    args: SimpleNamespace,
) -> tuple[bool, dict[str, float | None]]:
    metrics: dict[str, float | None] = {
        "regime_trend_pct": None,
        "regime_range_pct": None,
        "entry_gap_pct": None,
        "regime_consistency_pct": None,
        "regime_acceleration_pct": None,
        "premium_to_range_ratio": None,
    }
    if args.regime_lookback_days <= 0:
        return True, metrics

    daily = summary.loc[:, ["trade_date", "ts", "future_close"]].copy()
    daily = daily.sort_values("ts").groupby("trade_date", as_index=False).last()
    current_date = entry_ts.date()
    prior_days = daily[daily["trade_date"] < current_date].tail(args.regime_lookback_days)
    if len(prior_days) < args.regime_lookback_days:
        return False, metrics

    start_spot = float(prior_days.iloc[0]["future_close"])
    end_spot = float(prior_days.iloc[-1]["future_close"])
    avg_spot = float(prior_days["future_close"].mean())
    hi_spot = float(prior_days["future_close"].max())
    lo_spot = float(prior_days["future_close"].min())
    deltas = prior_days["future_close"].diff().dropna()
    if not deltas.empty:
        up_days = int((deltas > 0).sum())
        down_days = int((deltas < 0).sum())
        dominant_days = max(up_days, down_days)
        metrics["regime_consistency_pct"] = (dominant_days / len(deltas)) * 100
    if len(prior_days) >= 3:
        recent_two_start = float(prior_days.iloc[-3]["future_close"])
        recent_two_end = float(prior_days.iloc[-1]["future_close"])
        full_move = abs(end_spot - start_spot)
        recent_two_move = abs(recent_two_end - recent_two_start)
        if full_move > 0:
            metrics["regime_acceleration_pct"] = (recent_two_move / full_move) * 100
    metrics["regime_trend_pct"] = abs(end_spot / start_spot - 1.0) * 100 if start_spot else None
    metrics["regime_range_pct"] = ((hi_spot - lo_spot) / avg_spot) * 100 if avg_spot else None

    entry_day = cycle_summary[cycle_summary["trade_date"] == current_date]
    if not entry_day.empty:
        entry_spot = float(entry_day.iloc[0]["future_close"])
        prev_close = float(prior_days.iloc[-1]["future_close"])
        metrics["entry_gap_pct"] = abs(entry_spot / prev_close - 1.0) * 100 if prev_close else None

    if args.max_regime_trend_pct is not None and metrics["regime_trend_pct"] is not None:
        if metrics["regime_trend_pct"] > args.max_regime_trend_pct:
            return False, metrics
    if args.max_regime_range_pct is not None and metrics["regime_range_pct"] is not None:
        if metrics["regime_range_pct"] > args.max_regime_range_pct:
            return False, metrics
    if args.max_gap_pct is not None and metrics["entry_gap_pct"] is not None:
        if metrics["entry_gap_pct"] > args.max_gap_pct:
            return False, metrics

    return True, metrics


def premium_filter_allows_entry(
    entry_straddle_pct: float,
    regime_metrics: dict[str, float | None],
    args: SimpleNamespace,
) -> tuple[bool, dict[str, float | None]]:
    metrics = {"premium_to_range_ratio": None}
    range_pct = regime_metrics.get("regime_range_pct")
    if args.min_premium_to_range_ratio is None and args.max_premium_to_range_ratio is None:
        return True, metrics
    if range_pct is None or range_pct <= 0:
        return False, metrics

    ratio = entry_straddle_pct / range_pct
    metrics["premium_to_range_ratio"] = ratio

    if args.min_premium_to_range_ratio is not None and ratio < args.min_premium_to_range_ratio:
        return False, metrics
    if args.max_premium_to_range_ratio is not None and ratio > args.max_premium_to_range_ratio:
        return False, metrics
    return True, metrics


def select_cycle_entry(
    summary: pd.DataFrame,
    cycle_summary: pd.DataFrame,
    args: SimpleNamespace,
) -> tuple[pd.DataFrame | None, pd.Series | None, dict[str, float | None], dict[str, float | None], float | None]:
    unique_dates = list(dict.fromkeys(cycle_summary["trade_date"].tolist()))
    if args.entry_day_offset >= len(unique_dates):
        return None, None, {}, {}, None

    entry_dates = [unique_dates[args.entry_day_offset]]
    if args.wait_for_valid_entry:
        max_index = len(unique_dates)
        if args.max_entry_wait_days is not None:
            max_index = min(max_index, args.entry_day_offset + args.max_entry_wait_days + 1)
        entry_dates = unique_dates[args.entry_day_offset:max_index]

    for entry_date in entry_dates:
        candidate_summary = cycle_summary[cycle_summary["trade_date"] >= entry_date].copy()
        if candidate_summary.empty:
            continue

        if args.entry_time is not None:
            same_day_after_time = (
                (candidate_summary["trade_date"] == entry_date)
                & (candidate_summary["ts"].dt.time >= args.entry_time)
            )
            if not same_day_after_time.any():
                continue
            first_valid_ts = candidate_summary.loc[same_day_after_time, "ts"].min()
            candidate_summary = candidate_summary[candidate_summary["ts"] >= first_valid_ts].copy()
            if candidate_summary.empty:
                continue

        entry_row = candidate_summary.iloc[0]
        regime_ok, regime_metrics = regime_allows_entry(summary, candidate_summary, entry_row["ts"], args)
        if not regime_ok:
            continue

        entry_straddle_pct = float(entry_row["straddle_future"] / entry_row["future_close"]) * 100
        premium_ok, premium_metrics = premium_filter_allows_entry(entry_straddle_pct, regime_metrics, args)
        if not premium_ok:
            continue
        if args.min_straddle_pct is not None and entry_straddle_pct < args.min_straddle_pct:
            continue
        if args.max_straddle_pct is not None and entry_straddle_pct > args.max_straddle_pct:
            continue

        return candidate_summary, entry_row, regime_metrics, premium_metrics, entry_straddle_pct

    return None, None, {}, {}, None


def run_backtest(args: SimpleNamespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = Path(args.base)
    summary = add_cycle_id(load_summary(base, args.start_date, args.end_date), args.cycle_reset_dte_jump)
    con = duckdb.connect()
    con.execute("PRAGMA disable_progress_bar")
    chain_glob = (base / "nifty_chain" / "*.parquet").as_posix()
    trades = []
    segment_rows = []
    leg_rows = []

    for cycle_id, cycle in summary.groupby("cycle_id", sort=True):
        cycle_summary = cycle.loc[:, ["datetime", "ts", "future_close", "future_atm", "straddle_future", "DTE"]].copy()
        cycle_summary["trade_date"] = cycle_summary["ts"].dt.date
        cycle_summary, entry_row, regime_metrics, premium_metrics, entry_straddle_pct = select_cycle_entry(
            summary,
            cycle_summary,
            args,
        )
        if cycle_summary is None or entry_row is None or entry_straddle_pct is None:
            continue
        expiry_row = cycle.iloc[-1]
        cycle_chain = load_cycle_chain(con, chain_glob, entry_row["ts"], expiry_row["ts"])
        if cycle_chain.empty:
            continue

        remaining_summary = cycle_summary.copy()
        total_pnl_points = 0.0
        reentry_count = 0
        roll_count = 0
        first_entry_ts = None
        first_entry_spot = None
        last_exit_ts = None
        last_exit_reason = None
        last_atm = None
        last_short_call = None
        last_short_put = None
        last_long_call = None
        last_long_put = None
        last_credit = None
        last_max_loss = None
        last_entry_anchor_straddle = None
        last_trade_directional_mode = False

        while not remaining_summary.empty:
            current_entry_row = remaining_summary.iloc[0]
            current_force_exit_dte = args.force_exit_dte
            current_min_straddle_pct = args.min_straddle_pct
            current_min_entry_credit_points = args.min_entry_credit_points
            current_max_entry_credit_points = None
            current_otm_multiple = args.otm_multiple
            current_wing_multiple = args.wing_multiple
            current_take_profit_pct = args.take_profit_pct
            current_stop_loss_multiple = args.stop_loss_multiple
            current_reentry_recenter_distance_multiple = args.reentry_recenter_distance_multiple
            current_directional_mode = False
            current_strong_directional_mode = False
            if reentry_count == 0 and args.main_trade_force_exit_dte is not None:
                current_force_exit_dte = args.main_trade_force_exit_dte
            if args.directional_mode_enabled:
                regime_trend = regime_metrics.get("regime_trend_pct")
                regime_range = regime_metrics.get("regime_range_pct")
                entry_gap = regime_metrics.get("entry_gap_pct")
                regime_consistency = regime_metrics.get("regime_consistency_pct")
                regime_acceleration = regime_metrics.get("regime_acceleration_pct")
                if args.directional_trend_trigger_pct is not None and regime_trend is not None:
                    current_directional_mode = current_directional_mode or regime_trend >= args.directional_trend_trigger_pct
                if args.directional_range_trigger_pct is not None and regime_range is not None:
                    current_directional_mode = current_directional_mode or regime_range >= args.directional_range_trigger_pct
                if args.directional_gap_trigger_pct is not None and entry_gap is not None:
                    current_directional_mode = current_directional_mode or entry_gap >= args.directional_gap_trigger_pct
                if args.directional_consistency_trigger_pct is not None and regime_consistency is not None:
                    current_directional_mode = current_directional_mode or regime_consistency >= args.directional_consistency_trigger_pct
                if args.directional_acceleration_trigger_pct is not None and regime_acceleration is not None:
                    current_directional_mode = current_directional_mode or regime_acceleration >= args.directional_acceleration_trigger_pct
                if args.strong_directional_mode_enabled:
                    if args.strong_directional_trend_trigger_pct is not None and regime_trend is not None:
                        current_strong_directional_mode = current_strong_directional_mode or regime_trend >= args.strong_directional_trend_trigger_pct
                    if args.strong_directional_range_trigger_pct is not None and regime_range is not None:
                        current_strong_directional_mode = current_strong_directional_mode or regime_range >= args.strong_directional_range_trigger_pct
                    if args.strong_directional_gap_trigger_pct is not None and entry_gap is not None:
                        current_strong_directional_mode = current_strong_directional_mode or entry_gap >= args.strong_directional_gap_trigger_pct
                    if args.strong_directional_consistency_trigger_pct is not None and regime_consistency is not None:
                        current_strong_directional_mode = current_strong_directional_mode or regime_consistency >= args.strong_directional_consistency_trigger_pct
                    if args.strong_directional_acceleration_trigger_pct is not None and regime_acceleration is not None:
                        current_strong_directional_mode = current_strong_directional_mode or regime_acceleration >= args.strong_directional_acceleration_trigger_pct
                if current_directional_mode:
                    if args.directional_otm_multiple is not None:
                        current_otm_multiple = args.directional_otm_multiple
                    if args.directional_min_straddle_pct is not None:
                        current_min_straddle_pct = args.directional_min_straddle_pct
                    if args.directional_min_entry_credit_points is not None:
                        current_min_entry_credit_points = args.directional_min_entry_credit_points
                    if args.directional_take_profit_pct is not None:
                        current_take_profit_pct = args.directional_take_profit_pct
                    if args.directional_stop_loss_multiple is not None:
                        current_stop_loss_multiple = args.directional_stop_loss_multiple
                    if args.directional_reentry_recenter_distance_multiple is not None:
                        current_reentry_recenter_distance_multiple = args.directional_reentry_recenter_distance_multiple
                    if args.directional_force_exit_dte is not None:
                        current_force_exit_dte = args.directional_force_exit_dte
                    if args.directional_wing_multiple is not None:
                        current_wing_multiple = args.directional_wing_multiple
                if current_strong_directional_mode:
                    current_directional_mode = True
                    if args.strong_directional_otm_multiple is not None:
                        current_otm_multiple = args.strong_directional_otm_multiple
                    if args.strong_directional_min_straddle_pct is not None:
                        current_min_straddle_pct = args.strong_directional_min_straddle_pct
                    if args.strong_directional_min_entry_credit_points is not None:
                        current_min_entry_credit_points = args.strong_directional_min_entry_credit_points
                    if args.strong_directional_take_profit_pct is not None:
                        current_take_profit_pct = args.strong_directional_take_profit_pct
                    if args.strong_directional_stop_loss_multiple is not None:
                        current_stop_loss_multiple = args.strong_directional_stop_loss_multiple
                    if args.strong_directional_force_exit_dte is not None:
                        current_force_exit_dte = args.strong_directional_force_exit_dte
                    if args.strong_directional_wing_multiple is not None:
                        current_wing_multiple = args.strong_directional_wing_multiple
            if reentry_count > 0 and args.reentry_min_straddle_pct is not None:
                current_min_straddle_pct = args.reentry_min_straddle_pct
            if reentry_count > 0 and args.reentry_min_entry_credit_points is not None:
                current_min_entry_credit_points = args.reentry_min_entry_credit_points
            if reentry_count > 0 and args.reentry_max_entry_credit_points is not None:
                current_max_entry_credit_points = args.reentry_max_entry_credit_points
            if reentry_count > 0 and args.reentry_take_profit_pct is not None:
                current_take_profit_pct = args.reentry_take_profit_pct
            if reentry_count > 0 and args.reentry_stop_loss_multiple is not None:
                current_stop_loss_multiple = args.reentry_stop_loss_multiple
            if reentry_count > 0 and args.reentry_otm_multiple is not None:
                current_otm_multiple = args.reentry_otm_multiple
            if reentry_count > 0 and current_directional_mode and args.directional_reentry_otm_multiple is not None:
                current_otm_multiple = args.directional_reentry_otm_multiple
            if (
                reentry_count > 0
                and current_strong_directional_mode
                and args.strong_directional_reentry_otm_multiple is not None
            ):
                current_otm_multiple = args.strong_directional_reentry_otm_multiple
            if reentry_count >= 2 and args.third_entry_min_straddle_pct is not None:
                current_min_straddle_pct = args.third_entry_min_straddle_pct
            if reentry_count >= 2 and args.third_entry_min_entry_credit_points is not None:
                current_min_entry_credit_points = args.third_entry_min_entry_credit_points
            if reentry_count >= 2 and args.third_entry_max_entry_credit_points is not None:
                current_max_entry_credit_points = args.third_entry_max_entry_credit_points
            if reentry_count >= 2 and args.third_entry_take_profit_pct is not None:
                current_take_profit_pct = args.third_entry_take_profit_pct
            if reentry_count >= 2 and args.third_entry_stop_loss_multiple is not None:
                current_stop_loss_multiple = args.third_entry_stop_loss_multiple
            if reentry_count > 0 and args.leg_stop_atm_reentry_to_expiry:
                current_otm_multiple = args.leg_stop_reentry_otm_multiple
                current_min_straddle_pct = None
                current_min_entry_credit_points = None
            if (
                reentry_count > 0
                and args.calm_leg_stop_atm_reentry
                and last_exit_reason == "leg_stop_full_exit"
                and not last_trade_directional_mode
            ):
                current_otm_multiple = args.calm_leg_stop_reentry_otm_multiple
                current_min_straddle_pct = None
                current_min_entry_credit_points = None
            valid_times = set(remaining_summary["datetime"])
            filtered_chain = cycle_chain[cycle_chain["datetime"].isin(valid_times)]
            strikes = sorted(int(x) for x in filtered_chain["strike"].dropna().unique())
            if not strikes:
                break

            atm = int(current_entry_row["future_atm"])
            otm_move = float(current_entry_row["straddle_future"]) * current_otm_multiple
            short_call = snap_strike(atm + otm_move, strikes)
            short_put = snap_strike(atm - otm_move, strikes)
            target_max_loss_points = args.target_max_loss_points
            if args.target_max_loss_rupees is not None:
                target_max_loss_points = args.target_max_loss_rupees / args.lot_size

            if target_max_loss_points is not None:
                entry_chain = filtered_chain[filtered_chain["datetime"] == current_entry_row["datetime"]]
                entry_prices = entry_chain.set_index("strike")
                best_wing = None
                best_under = None
                best_over = None
                def entry_option_price(strike: int, col: str) -> float:
                    value = entry_prices.loc[strike, col]
                    if isinstance(value, pd.Series):
                        value = value.dropna()
                        if value.empty:
                            return float("nan")
                        value = value.iloc[0]
                    return float(value)

                for candidate_call in strikes:
                    if candidate_call <= short_call:
                        continue
                    wing_distance = candidate_call - short_call
                    candidate_put = snap_strike(short_put - wing_distance, strikes)
                    if candidate_put >= short_put:
                        continue
                    if (
                        short_call not in entry_prices.index
                        or short_put not in entry_prices.index
                        or candidate_call not in entry_prices.index
                        or candidate_put not in entry_prices.index
                    ):
                        continue
                    call_credit = (
                        entry_option_price(short_call, "ce_close") * (1 - SLIPPAGE)
                        - entry_option_price(candidate_call, "ce_close") * (1 + SLIPPAGE)
                    )
                    put_credit = (
                        entry_option_price(short_put, "pe_close") * (1 - SLIPPAGE)
                        - entry_option_price(candidate_put, "pe_close") * (1 + SLIPPAGE)
                    )
                    candidate_credit = call_credit + put_credit
                    if pd.isna(candidate_credit):
                        continue
                    if candidate_credit <= 0:
                        continue
                    spread_width = max(candidate_call - short_call, short_put - candidate_put)
                    candidate_max_loss = spread_width - candidate_credit
                    row_choice = (candidate_max_loss, candidate_call, candidate_put)
                    if candidate_max_loss <= target_max_loss_points:
                        if best_under is None or candidate_max_loss > best_under[0]:
                            best_under = row_choice
                    elif best_over is None or candidate_max_loss < best_over[0]:
                        best_over = row_choice
                best_wing = best_under if best_under is not None else best_over
                if best_wing is None:
                    break
                _, long_call, long_put = best_wing
            else:
                if args.sd_wing_multiple is not None:
                    sd_move = realized_sd_move_points(
                        summary,
                        current_entry_row["ts"],
                        float(current_entry_row["future_close"]),
                        int(current_entry_row["DTE"]),
                        args.sd_lookback_days,
                    )
                    if sd_move is None:
                        break
                    wing_move = sd_move * args.sd_wing_multiple
                elif args.fixed_wing_points is not None:
                    wing_move = args.fixed_wing_points
                else:
                    wing_move = float(current_entry_row["straddle_future"]) * current_wing_multiple
                    if args.max_wing_points is not None:
                        wing_move = min(wing_move, args.max_wing_points)
                if args.sd_wing_multiple is not None and args.max_wing_points is not None:
                    wing_move = min(wing_move, args.max_wing_points)
                long_call = snap_strike(short_call + wing_move, strikes)
                long_put = snap_strike(short_put - wing_move, strikes)
            if short_call < atm or short_put > atm:
                break
            if short_call < short_put:
                break
            if long_call <= short_call or long_put >= short_put:
                break

            # The signal is evaluated from the current completed candle.  Fill
            # only on the next candle's option open, never on the signal candle.
            execution_summary = remaining_summary.iloc[1:].copy()
            if execution_summary.empty:
                break
            joined = build_joined_path(
                filtered_chain,
                execution_summary[["datetime", "DTE", "future_close", "future_atm", "straddle_future"]].copy(),
                short_call,
                short_put,
                long_call,
                long_put,
            )
            if joined.empty:
                break

            entry = joined.iloc[0]
            entry_credit_points = (
                float(entry[f"CE_{short_call}_open"]) * (1 - SLIPPAGE)
                + float(entry[f"PE_{short_put}_open"]) * (1 - SLIPPAGE)
                - float(entry[f"CE_{long_call}_open"]) * (1 + SLIPPAGE)
                - float(entry[f"PE_{long_put}_open"]) * (1 + SLIPPAGE)
            )
            entry_straddle_pct_current = float(current_entry_row["straddle_future"] / current_entry_row["future_close"]) * 100
            if entry_credit_points <= 0:
                break
            if (
                current_min_straddle_pct is not None
                and entry_straddle_pct_current < current_min_straddle_pct
            ):
                break
            if (
                current_min_entry_credit_points is not None
                and entry_credit_points < current_min_entry_credit_points
            ):
                break
            # Optional future-scaled credit floors. These are checked at the
            # actual entry decision, so rejected sleeves never enter the trade
            # log or affect subsequent re-entry state.
            entry_dte = int(entry["DTE"])
            min_credit_pct_of_future = None
            if entry_dte == 2:
                min_credit_pct_of_future = getattr(args, "dte2_min_credit_pct_of_future", None)
            elif entry_dte == 3:
                min_credit_pct_of_future = getattr(args, "dte3_min_credit_pct_of_future", None)
                if reentry_count > 0:
                    min_credit_pct_of_future = getattr(
                        args,
                        "reentry_dte3_min_credit_pct_of_future",
                        min_credit_pct_of_future,
                    )
            elif entry_dte in (4, 5):
                min_credit_pct_of_future = getattr(args, "dte45_min_credit_pct_of_future", None)
            if min_credit_pct_of_future is not None:
                credit_pct_of_future = entry_credit_points / float(entry["future_close"]) * 100.0
                if credit_pct_of_future < min_credit_pct_of_future:
                    break
            if (
                current_max_entry_credit_points is not None
                and entry_credit_points > current_max_entry_credit_points
            ):
                break
            spread_width = max(long_call - short_call, short_put - long_put)
            max_loss_points = spread_width - entry_credit_points
            if (
                args.max_entry_loss_points is not None
                and max_loss_points > args.max_entry_loss_points
            ):
                break
            if first_entry_ts is None:
                first_entry_ts = pd.to_datetime(entry["datetime"], format="%d/%m/%Y %H:%M:%S")
                first_entry_spot = float(current_entry_row["future_close"])
            if reentry_count == 0 and args.acceptable_premium_exit_dte is not None:
                rich_by_straddle = (
                    args.rich_straddle_pct is not None
                    and entry_straddle_pct_current >= args.rich_straddle_pct
                )
                rich_by_credit = (
                    args.rich_entry_credit_points is not None
                    and entry_credit_points >= args.rich_entry_credit_points
                )
                if not (rich_by_straddle or rich_by_credit):
                    current_force_exit_dte = args.acceptable_premium_exit_dte

            exit_row_data, pnl_points, exit_reason = evaluate_event(
                joined,
                short_call,
                short_put,
                long_call,
                long_put,
                entry_credit_points,
                current_take_profit_pct,
                current_stop_loss_multiple,
                current_force_exit_dte,
                args.square_off_time,
                args.roll_trigger_pct,
                args.no_roll_last_dte,
                args.use_leg_stop,
                args.profit_lock_trigger_pct,
                args.profit_lock_exit_pct,
                args.max_hold_minutes,
            )

            segment_entry_ts = pd.to_datetime(entry["datetime"], format="%d/%m/%Y %H:%M:%S")
            segment_exit_ts = pd.to_datetime(exit_row_data["datetime"], format="%d/%m/%Y %H:%M:%S")
            segment_id = f"{int(cycle_id)}-{reentry_count}"
            management_mode = "wide_range" if current_directional_mode else "normal"
            segment_rows.append(
                {
                    "cycle_id": int(cycle_id),
                    "segment_id": segment_id,
                    "segment_index": reentry_count,
                    "is_reentry": reentry_count > 0,
                    "entry_ts": segment_entry_ts,
                    "entry_date": segment_entry_ts.date(),
                    "entry_time": segment_entry_ts.time(),
                    "exit_ts": segment_exit_ts,
                    "exit_date": segment_exit_ts.date(),
                    "exit_time": segment_exit_ts.time(),
                    "entry_dte": int(entry["DTE"]),
                    "exit_dte": int(exit_row_data["DTE"]),
                    "entry_spot": float(entry["future_close"]),
                    "exit_spot": float(exit_row_data["future_close"]),
                    "atm_strike": atm,
                    "short_call": short_call,
                    "short_put": short_put,
                    "long_call": long_call,
                    "long_put": long_put,
                    "wing_multiple": current_wing_multiple,
                    "otm_multiple": current_otm_multiple,
                    "entry_credit_points": entry_credit_points,
                    "max_loss_points": max_loss_points,
                    "take_profit_pct": current_take_profit_pct,
                    "stop_loss_multiple": current_stop_loss_multiple,
                    "profit_lock_trigger_pct": args.profit_lock_trigger_pct,
                    "profit_lock_exit_pct": args.profit_lock_exit_pct,
                    "force_exit_dte": current_force_exit_dte,
                    "reentry_recenter_distance_multiple": current_reentry_recenter_distance_multiple,
                    "management_mode": management_mode,
                    "regime_range_trigger_pct": args.directional_range_trigger_pct if args.directional_mode_enabled else None,
                    "regime_trend_pct": regime_metrics["regime_trend_pct"],
                    "regime_range_pct": regime_metrics["regime_range_pct"],
                    "entry_gap_pct": regime_metrics["entry_gap_pct"],
                    "exit_reason": exit_reason,
                    "pnl_points": pnl_points,
                    "pnl_rupees": pnl_points * args.lot_size,
                    "return_on_capital": (pnl_points * args.lot_size) / args.capital_per_trade,
                }
            )
            leg_specs = [
                ("short_call", "CE", short_call, "SELL", "BUY"),
                ("short_put", "PE", short_put, "SELL", "BUY"),
                ("long_call", "CE", long_call, "BUY", "SELL"),
                ("long_put", "PE", long_put, "BUY", "SELL"),
            ]
            for leg_name, option_type, strike, entry_action, exit_action in leg_specs:
                price_col = f"{option_type}_{strike}"
                entry_raw = float(entry[f"{price_col}_open"])
                exit_raw = float(exit_row_data[price_col])
                if entry_action == "SELL":
                    entry_fill = entry_raw * (1 - SLIPPAGE)
                    exit_fill = exit_raw * (1 + SLIPPAGE)
                    leg_pnl_points = entry_fill - exit_fill
                else:
                    entry_fill = entry_raw * (1 + SLIPPAGE)
                    exit_fill = exit_raw * (1 - SLIPPAGE)
                    leg_pnl_points = exit_fill - entry_fill
                leg_rows.append(
                    {
                        "cycle_id": int(cycle_id),
                        "segment_id": segment_id,
                        "segment_index": reentry_count,
                        "is_reentry": reentry_count > 0,
                        "leg": leg_name,
                        "option_type": option_type,
                        "strike": strike,
                        "entry_action": entry_action,
                        "exit_action": exit_action,
                        "entry_ts": segment_entry_ts,
                        "entry_date": segment_entry_ts.date(),
                        "entry_time": segment_entry_ts.time(),
                        "exit_ts": segment_exit_ts,
                        "exit_date": segment_exit_ts.date(),
                        "exit_time": segment_exit_ts.time(),
                        "entry_dte": int(entry["DTE"]),
                        "exit_dte": int(exit_row_data["DTE"]),
                        "entry_raw_price": entry_raw,
                        "entry_fill_price": entry_fill,
                        "exit_raw_price": exit_raw,
                        "exit_fill_price": exit_fill,
                        "slippage_pct": SLIPPAGE,
                        "quantity_lots": 1,
                        "lot_size": args.lot_size,
                        "leg_pnl_points": leg_pnl_points,
                        "leg_pnl_rupees": leg_pnl_points * args.lot_size,
                        "management_mode": management_mode,
                        "exit_reason": exit_reason,
                    }
                )
            total_pnl_points += pnl_points
            last_exit_ts = pd.to_datetime(exit_row_data["datetime"], format="%d/%m/%Y %H:%M:%S")
            last_exit_reason = exit_reason
            last_atm = atm
            last_short_call = short_call
            last_short_put = short_put
            last_long_call = long_call
            last_long_put = long_put
            last_credit = entry_credit_points
            last_max_loss = max_loss_points
            last_entry_anchor_straddle = float(current_entry_row["straddle_future"])
            last_trade_directional_mode = current_directional_mode

            if exit_reason == "roll" and roll_count < args.max_rolls:
                future_rows = remaining_summary[remaining_summary["ts"] > last_exit_ts].copy()
                if future_rows.empty:
                    break
                remaining_summary = future_rows
                roll_count += 1
                continue

            reentry_allowed = True
            if args.use_leg_stop:
                allowed_exit_reasons = {"side_take_profit"}
                if args.reentry_on_leg_stop:
                    allowed_exit_reasons.add("leg_stop_side_exit")
                    allowed_exit_reasons.add("leg_stop_full_exit")
                if args.reentry_on_expiry:
                    allowed_exit_reasons.add("expiry_exit")
                reentry_allowed = exit_reason in allowed_exit_reasons
                if args.leg_stop_atm_reentry_to_expiry:
                    reentry_allowed = (
                        reentry_count == 0
                        and exit_reason in {"leg_stop_side_exit", "leg_stop_full_exit"}
                        and int(exit_row_data["DTE"]) > args.leg_stop_atm_reentry_min_dte
                    )

            if args.reentry_after_first_on_tp_or_sl_only:
                if reentry_count == 0:
                    reentry_allowed = exit_reason == "stop_loss"
                else:
                    reentry_allowed = exit_reason in {"take_profit", "stop_loss"}
            elif args.reentry_on_structure_stop_only:
                reentry_allowed = exit_reason == "stop_loss"
            if args.reentry_on_tp_or_sl_only:
                reentry_allowed = exit_reason in {"take_profit", "stop_loss"}
            if (
                reentry_allowed
                and args.reentry_exact_dte is not None
                and int(exit_row_data["DTE"]) < args.reentry_exact_dte
            ):
                reentry_allowed = False
            if (
                reentry_allowed
                and args.reentry_min_dte is not None
                and int(exit_row_data["DTE"]) < args.reentry_min_dte
            ):
                reentry_allowed = False
            if reentry_allowed and args.reentry_calm_only and last_trade_directional_mode:
                reentry_allowed = False

            if reentry_count >= args.max_reentries or not reentry_allowed:
                break

            future_rows = remaining_summary[remaining_summary["ts"] > last_exit_ts].copy()
            if future_rows.empty:
                break
            if args.reentry_entry_time is not None and (
                not args.reentry_after_first_on_tp_or_sl_only or reentry_count > 0
            ):
                future_rows = future_rows[future_rows["ts"].dt.time >= args.reentry_entry_time].copy()
                if future_rows.empty:
                    break
            if args.reentry_min_dte is not None and (
                not args.reentry_after_first_on_tp_or_sl_only or reentry_count > 0
            ):
                dte_candidates = future_rows[future_rows["DTE"].astype(int) >= args.reentry_min_dte].copy()
                if dte_candidates.empty:
                    break
                next_reentry_ts = dte_candidates.iloc[0]["ts"]
                future_rows = future_rows[future_rows["ts"] >= next_reentry_ts].copy()
            if args.reentry_exact_dte is not None and (
                not args.reentry_after_first_on_tp_or_sl_only or reentry_count > 0
            ):
                dte_candidates = future_rows[future_rows["DTE"].astype(int) == args.reentry_exact_dte].copy()
                if dte_candidates.empty:
                    break
                next_reentry_ts = dte_candidates.iloc[0]["ts"]
                future_rows = future_rows[future_rows["ts"] >= next_reentry_ts].copy()
            if (
                current_reentry_recenter_distance_multiple is not None
                and last_atm is not None
                and last_entry_anchor_straddle is not None
            ):
                recenter_distance = last_entry_anchor_straddle * current_reentry_recenter_distance_multiple
                reentry_candidates = future_rows[
                    (future_rows["future_close"].astype(float) - float(last_atm)).abs() <= recenter_distance
                ].copy()
                if reentry_candidates.empty:
                    break
                next_reentry_ts = reentry_candidates.iloc[0]["ts"]
                future_rows = future_rows[future_rows["ts"] >= next_reentry_ts].copy()

            remaining_summary = future_rows
            reentry_count += 1

        if first_entry_ts is None:
            continue

        trades.append(
            {
                "cycle_id": int(cycle_id),
                "entry_ts": first_entry_ts,
                "exit_ts": last_exit_ts,
                "entry_spot": first_entry_spot,
                "expiry_spot": float(expiry_row["future_close"]),
                "atm_strike": last_atm,
                "short_call": last_short_call,
                "short_put": last_short_put,
                "long_call": last_long_call,
                "long_put": last_long_put,
                "wing_multiple": args.wing_multiple,
                "otm_multiple": args.otm_multiple,
                "entry_straddle_pct": entry_straddle_pct,
                "entry_credit_points": last_credit,
                "max_loss_points": last_max_loss,
                "exit_reason": last_exit_reason,
                "rolls": roll_count,
                "reentries": reentry_count,
                "regime_trend_pct": regime_metrics["regime_trend_pct"],
                "regime_range_pct": regime_metrics["regime_range_pct"],
                "entry_gap_pct": regime_metrics["entry_gap_pct"],
                "premium_to_range_ratio": premium_metrics["premium_to_range_ratio"],
                "pnl_points": total_pnl_points,
                "pnl_rupees": total_pnl_points * args.lot_size,
                "return_on_capital": (total_pnl_points * args.lot_size) / args.capital_per_trade,
            }
        )

    trades_df = pd.DataFrame(trades)
    segments_df = pd.DataFrame(segment_rows)
    legs_df = pd.DataFrame(leg_rows)
    if trades_df.empty:
        return trades_df, trades_df, segments_df, legs_df

    monthly = trades_df.assign(month=trades_df["entry_ts"].dt.to_period("M").astype(str))
    monthly = monthly.groupby("month", as_index=False)["pnl_rupees"].sum()
    monthly["return_pct"] = monthly["pnl_rupees"] / args.capital_per_trade * 100
    return trades_df, monthly, segments_df, legs_df


@dataclass(frozen=True)
class StrategyContext:
    run_id: str
    market_data: Path
    config: Mapping[str, Any]


STRATEGY_NAME = "NIFTY weekly protected short straddle"

PARAMETER_DEFAULTS: dict[str, Any] = {
    "cycle_reset_dte_jump": 2.0,
    "entry_day_offset": 1,
    "wing_multiple": 2.3,
    "take_profit_pct": 5.0,
    "stop_loss_multiple": 8.0,
    "min_entry_credit_points": 130.0,
    "profit_lock_trigger_pct": 0.3,
    "profit_lock_exit_pct": 0.1,
    "force_exit_dte": 0,
    "max_hold_minutes": 1.0,
    "max_reentries": 1,
    "reentry_min_dte": 2,
    "reentry_on_structure_stop_only": True,
    "reentry_recenter_distance_multiple": 0.35,
    "regime_lookback_days": 3,
    "directional_mode_enabled": True,
    "directional_range_trigger_pct": 1.6,
    "directional_stop_loss_multiple": 8.0,
    "directional_reentry_recenter_distance_multiple": 0.50,
}


def require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def require_number(
    mapping: Mapping[str, Any],
    key: str,
    *,
    positive: bool = False,
) -> float:
    if key not in mapping:
        raise KeyError(f"execution is missing required key {key!r}")
    value = float(mapping[key])
    if positive and value <= 0:
        raise ValueError(f"execution.{key} must be greater than zero")
    return value


def timestamp_with_timezone(value: Any, timezone: ZoneInfo) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(timezone)
    return timestamp.tz_convert(timezone)


def build_trade_mapper(run_id: str, timezone: ZoneInfo, fees_per_leg: float):
    def map_completed_trade(trade: Any, index: int) -> dict[str, Any]:
        row = pd.Series(trade) if isinstance(trade, Mapping) else trade
        if not isinstance(row, pd.Series):
            raise TypeError("completed trade must be an engine leg row or mapping")

        entry_action = str(row["entry_action"]).upper()
        if entry_action == "SELL":
            side = "SHORT"
        elif entry_action == "BUY":
            side = "LONG"
        else:
            raise ValueError(f"unsupported engine entry_action: {entry_action!r}")

        segment_id = str(row["segment_id"])
        leg_name = str(row["leg"])
        option_type = str(row["option_type"])
        strike = str(row["strike"])

        return {
            "schema_version": "1",
            "run_id": run_id,
            "trade_id": f"{segment_id}:{leg_name}:{index}",
            "batch_id": segment_id,
            "leg_id": f"{leg_name}:{option_type}:{strike}",
            "strategy": STRATEGY_NAME,
            "symbol": f"NIFTY {option_type} {strike}",
            "side": side,
            "entry_time": timestamp_with_timezone(row["entry_ts"], timezone),
            "exit_time": timestamp_with_timezone(row["exit_ts"], timezone),
            "quantity": row["quantity_lots"],
            "entry_price": row["entry_fill_price"],
            "exit_price": row["exit_fill_price"],
            "multiplier": row["lot_size"],
            "fees": fees_per_leg,
        }

    return map_completed_trade


def run_strategy(context: StrategyContext) -> dict[str, Any]:
    run_id = str(context.run_id).strip()
    if not run_id:
        raise ValueError("context.run_id must be a non-empty string")

    market_data_root = Path(context.market_data)
    summary_file = market_data_root / "nifty_summary.parquet"
    chain_folder = market_data_root / "nifty_chain"
    if not summary_file.is_file():
        raise FileNotFoundError(f"Missing required file: {summary_file}")
    if not chain_folder.is_dir() or not list(chain_folder.glob("*.parquet")):
        raise FileNotFoundError(f"Missing NIFTY chain Parquet data: {chain_folder}")

    config = require_mapping(context.config, "context.config")
    instrument = require_mapping(config.get("instrument"), "instrument")
    period = require_mapping(config.get("period"), "period")
    execution = require_mapping(config.get("execution"), "execution")
    supplied_parameters = require_mapping(config.get("parameters", {}), "parameters")

    if str(instrument.get("symbol", "")).upper() != "NIFTY":
        raise ValueError("this strategy requires instrument.symbol='NIFTY'")

    capital_per_trade = float(execution.get("capital_per_trade", ENGINE_DEFAULTS["capital_per_trade"]))
    if capital_per_trade <= 0:
        raise ValueError("execution.capital_per_trade must be greater than zero")
    lot_size_value = float(execution.get("lot_size", ENGINE_DEFAULTS["lot_size"]))
    if lot_size_value <= 0:
        raise ValueError("execution.lot_size must be greater than zero")
    if not lot_size_value.is_integer():
        raise ValueError("execution.lot_size must be an integer")
    lot_size = int(lot_size_value)
    slippage = require_number(execution, "slippage")
    fees_per_leg = require_number(execution, "fees_per_leg")
    timezone = ZoneInfo(str(execution["timezone"]))

    if slippage != SLIPPAGE:
        raise ValueError(f"this engine requires slippage={SLIPPAGE}")
    if fees_per_leg != 0.0:
        raise ValueError("this engine does not model fees; fees_per_leg must be 0")

    unknown_parameters = set(supplied_parameters) - set(PARAMETER_DEFAULTS)
    if unknown_parameters:
        raise KeyError(f"unsupported strategy parameters: {sorted(unknown_parameters)}")
    parameters = {**PARAMETER_DEFAULTS, **dict(supplied_parameters)}

    args = engine_defaults()
    args.base = str(market_data_root)
    args.start_date = str(period["start_date"])
    args.end_date = str(period["end_date"])
    args.capital_per_trade = capital_per_trade
    args.lot_size = lot_size
    for name, value in parameters.items():
        setattr(args, name, value)

    _, _, _, completed_legs = run_backtest(args)
    if not isinstance(completed_legs, pd.DataFrame):
        raise TypeError("engine did not return its authoritative completed-leg DataFrame")

    authoritative_count = len(completed_legs)
    completed_trade_rows = completed_legs.to_dict(orient="records")
    if len(completed_trade_rows) != authoritative_count:
        raise ValueError("completed-trade normalization changed the engine count")

    return {
        "completed_trades": completed_trade_rows,
        "completed_trade_count": authoritative_count,
        "trade_mapper": build_trade_mapper(run_id, timezone, fees_per_leg),
        "metadata": {
            "strategy_name": STRATEGY_NAME,
            "engine": "embedded protected_straddle_backtest custom engine",
        },
    }


def main() -> None:
    from google.colab import drive, files

    drive.mount("/content/drive")
    market_data_root = Path("/content/drive/MyDrive/db/next week")

    context = StrategyContext(
        run_id=f"colab-{uuid4().hex}",
        market_data=market_data_root,
        config=MappingProxyType(
            {
                "instrument": MappingProxyType(
                    {"symbol": "NIFTY", "timeframe": "5m"}
                ),
                "period": MappingProxyType(
                    {"start_date": "2023-01-02", "end_date": "2026-05-05"}
                ),
                "execution": MappingProxyType(
                    {
                        "capital_per_trade": 300000.0,
                        "lot_size": 65,
                        "slippage": 0.005,
                        "fees_per_leg": 0.0,
                        "timezone": "Asia/Kolkata",
                    }
                ),
                "parameters": MappingProxyType({}),
            }
        ),
    )

    result = run_strategy(context)
    output_path = Path("/content/output/trades.csv")
    receipt = export_trade_log(
        result["completed_trades"],
        output_path,
        mapper=result["trade_mapper"],
        expected_count=result["completed_trade_count"],
    )
    validation = validate_trade_log_csv(output_path)
    if validation.row_count != result["completed_trade_count"]:
        raise RuntimeError("validated row count differs from engine count")

    print(
        {
            "status": "no_trades" if receipt.row_count == 0 else "succeeded",
            "run_mode": RUN_MODE,
            "completed_trade_count": result["completed_trade_count"],
            "exported_row_count": receipt.row_count,
            "schema_version": receipt.schema_version,
            "validation": "passed",
            "output": str(receipt.output_path),
        }
    )
    files.download(str(receipt.output_path))
    files.download(str(receipt.manifest_path))


if __name__ == "__main__":
    main()
