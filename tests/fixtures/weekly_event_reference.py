# Frozen pre-optimization event evaluator for equivalence tests only.
from __future__ import annotations
import pandas as pd
from datetime import timedelta
SLIPPAGE = 0.005
def structure_stop_debit(entry_credit_points: float, stop_loss_multiple: float) -> float:
    return entry_credit_points * (1 + stop_loss_multiple)


def short_leg_stop_price(entry_short_price: float, stop_loss_multiple: float) -> float:
    return entry_short_price * (1 + stop_loss_multiple)


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
    realized_before_points: float,
    lot_size: int,
    snapshot_sink: dict[pd.Timestamp, dict[str, Any]],
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

    def close_at(row: pd.Series, reason: str) -> tuple[pd.Series, float, str]:
        pnl_points = float(row["open_pnl_points"])
        exit_ts = pd.Timestamp(row["ts"])
        if not snapshot_sink:
            baseline = entry_ts - pd.Timedelta(seconds=1)
            snapshot_sink[baseline] = {
                "timestamp": baseline,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
            }
        observed = path[path["ts"] <= exit_ts]
        for _, mark in observed.iterrows():
            timestamp = pd.Timestamp(mark["ts"])
            snapshot_sink[timestamp] = {
                "timestamp": timestamp,
                "realized_pnl": realized_before_points * lot_size,
                "unrealized_pnl": float(mark["open_pnl_points"]) * lot_size,
            }
        snapshot_sink[exit_ts] = {
            "timestamp": exit_ts,
            "realized_pnl": (realized_before_points + pnl_points) * lot_size,
            "unrealized_pnl": 0.0,
        }
        return row, pnl_points, reason

    for _, row in path.iloc[1:].iterrows():
        if should_max_hold_exit(row, entry_ts, max_hold_minutes):
            return close_at(row, "max_hold_exit")
        if should_force_exit(row, force_exit_dte, square_off_time):
            return close_at(row, "time_exit")
        if float(row["open_pnl_points"]) >= tp_threshold:
            return close_at(row, "take_profit")
        if (
            profit_lock_trigger_pct is not None
            and float(row["open_pnl_points"]) >= entry_credit_points * profit_lock_trigger_pct
        ):
            profit_lock_armed = True
        if (
            profit_lock_armed
            and float(row["open_pnl_points"]) <= entry_credit_points * profit_lock_exit_pct
        ):
            return close_at(row, "profit_lock")
        if use_leg_stop:
            call_mark = float(row[f"CE_{short_call}"]) * (1 + SLIPPAGE)
            put_mark = float(row[f"PE_{short_put}"]) * (1 + SLIPPAGE)
            if call_mark >= call_leg_sl or put_mark >= put_leg_sl:
                return close_at(row, "leg_stop")
        elif float(row["exit_debit_points"]) >= sl_debit:
            return close_at(row, "stop_loss")
        if roll_trigger_pct is not None and int(row["DTE"]) > no_roll_last_dte:
            center_strike = (short_call + short_put) / 2.0
            if abs(float(row["future_close"]) - center_strike) >= wing_distance * roll_trigger_pct:
                return close_at(row, "roll")

    final_row = path.iloc[-1]
    return close_at(final_row, "expiry_exit")

