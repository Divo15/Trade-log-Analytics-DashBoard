"""Exact numeric batch accelerator for the R6 dashboard strategy.

This module deliberately does not replace ``run_strategy``.  It prepares the
same market observations used by the oracle strategy and evaluates independent
parameter mappings in a Numba kernel.  The result is a compact provisional
metrics matrix; selected mappings must still be rerun through the oracle before
their results are treated as authoritative.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from strategies import nifty_current_week_0dte_trend_following_r6_sweep_dashboard as oracle


try:
    import numba
    from numba import njit, prange
except ImportError:  # Keep the authoritative strategy usable without Numba.
    numba = None
    prange = range

    def njit(*args, **kwargs):
        def decorate(function):
            return function

        return decorate


NUMBA_AVAILABLE = numba is not None
DEFAULT_BATCH_SIZE = 25_000

PARAMETER_COLUMNS = (
    "premium_index",
    "add_on_straddle_decay_pct",
    "stop_loss_pct",
    "profit_booking_pct",
    "trailing_activation_pct",
    "r6_reversal_threshold_pct",
    "trailing_gap_pct",
    "max_lots_per_day",
    "max_reentries_per_trade",
    "capital",
    "margin_per_lot",
    "lot_size",
    "slippage",
    "fees_per_leg",
    "entry_seconds",
    "square_off_seconds",
)

METRIC_COLUMNS = (
    "status",
    "gross_pnl",
    "fees",
    "net_pnl",
    "completed_trade_count",
    "final_realized_pnl",
    "final_unrealized_pnl",
    "minimum_observed_equity",
    "maximum_observed_drawdown",
)

STATUS_OK = 0
STATUS_MISSING_NEW_ENTRY_SELECTION = 1
STATUS_MISSING_OPEN_POSITION_QUOTE = 2
STATUS_OPEN_POSITION_AT_END_OF_DAY = 3
STATUS_PENDING_REENTRY_CAPACITY = 4
STATUS_INVALID_PARAMETERS = 5

M_STATUS = 0
M_GROSS_PNL = 1
M_FEES = 2
M_NET_PNL = 3
M_TRADE_COUNT = 4
M_FINAL_REALIZED = 5
M_FINAL_UNREALIZED = 6
M_MIN_EQUITY = 7
M_MAX_DRAWDOWN = 8
METRIC_COUNT = len(METRIC_COLUMNS)

P_PREMIUM_INDEX = 0
P_ADD_DECAY = 1
P_STOP_LOSS = 2
P_PROFIT_BOOKING = 3
P_TRAILING_ACTIVATION = 4
P_R6_THRESHOLD = 5
P_TRAILING_GAP = 6
P_MAX_LOTS = 7
P_MAX_REENTRIES = 8
P_CAPITAL = 9
P_MARGIN_PER_LOT = 10
P_LOT_SIZE = 11
P_SLIPPAGE = 12
P_FEES_PER_LEG = 13
P_ENTRY_SECONDS = 14
P_SQUARE_OFF_SECONDS = 15

NANOSECONDS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True)
class R6BatchData:
    """Immutable numeric market representation accepted by the kernel."""

    day_offsets: np.ndarray
    event_timestamps_ns: np.ndarray
    event_seconds: np.ndarray
    straddle: np.ndarray
    current_future: np.ndarray
    d2_future: np.ndarray
    r6_future: np.ndarray
    r6_available: np.ndarray
    quote_offsets: np.ndarray
    quote_strikes: np.ndarray
    quote_ce: np.ndarray
    quote_pe: np.ndarray
    selection_strikes: np.ndarray
    selection_raw: np.ndarray
    premium_values: np.ndarray
    max_day_events: int


def _clock_seconds(value: Any) -> int:
    parsed = oracle._parse_time(value)
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def _resolved_settings(config: Mapping[str, Any], supplied: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(supplied) - set(oracle.DEFAULTS)
    if unknown:
        raise KeyError(f"Unsupported strategy parameters: {sorted(unknown)}")
    settings = {**oracle.DEFAULTS, **dict(supplied)}
    execution = dict(config.get("execution") or {})
    if "slippage" in execution:
        settings["slippage"] = float(execution["slippage"])
    if "fees_per_leg" in execution:
        settings["fees_per_leg"] = float(execution["fees_per_leg"])
    if "lot_size" in execution:
        settings["lot_size"] = int(execution["lot_size"])
    if str(execution.get("timezone", "Asia/Kolkata")) != "Asia/Kolkata":
        raise ValueError("This strategy requires execution.timezone='Asia/Kolkata'")
    oracle._validate_parameters(settings)
    return settings


def encode_parameter_sets(
    config: Mapping[str, Any],
    parameter_sets: Sequence[Mapping[str, Any]],
    premium_values: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Normalize mappings into the fixed numeric layout used by the kernel."""

    if not parameter_sets:
        raise ValueError("parameter_sets must not be empty")
    settings = [_resolved_settings(config, item) for item in parameter_sets]
    if premium_values is None:
        premium_values = np.asarray(
            sorted({float(item["strike_premium_pct_of_straddle"]) for item in settings}),
            dtype=np.float64,
        )
    else:
        premium_values = np.asarray(premium_values, dtype=np.float64)
    premium_index = {float(value): index for index, value in enumerate(premium_values)}
    matrix = np.empty((len(settings), len(PARAMETER_COLUMNS)), dtype=np.float64)
    for index, item in enumerate(settings):
        premium = float(item["strike_premium_pct_of_straddle"])
        if premium not in premium_index:
            raise ValueError(f"Premium {premium!r} was not prepared for this batch")
        matrix[index] = (
            premium_index[premium],
            float(item["add_on_straddle_decay_pct"]),
            float(item["stop_loss_pct"]),
            float(item["profit_booking_pct"]),
            float(item["trailing_activation_pct"]),
            float(item["r6_reversal_threshold_pct"]),
            float(item["trailing_gap_pct"]),
            int(item["max_lots_per_day"]),
            int(item["max_reentries_per_trade"]),
            float(item["capital"]),
            float(item["margin_per_lot"]),
            int(item["lot_size"]),
            float(item["slippage"]),
            float(item["fees_per_leg"]),
            _clock_seconds(item["entry_time"]),
            _clock_seconds(item["square_off_time"]),
        )
    return np.ascontiguousarray(matrix), premium_values


def prepare_batch_data(context: Any, parameter_sets: Sequence[Mapping[str, Any]]) -> tuple[R6BatchData, np.ndarray]:
    """Load the oracle's observations once and build immutable numeric arrays."""

    config = oracle._require_mapping(context.config, "context.config")
    instrument = oracle._require_mapping(config.get("instrument"), "instrument", allow_missing=True)
    period = oracle._require_mapping(config.get("period"), "period", allow_missing=True)
    if instrument and str(instrument.get("symbol", "NIFTY")).upper() != "NIFTY":
        raise ValueError("This strategy requires instrument.symbol='NIFTY'")

    parameter_matrix, premium_values = encode_parameter_sets(config, parameter_sets)
    market_data = Path(context.market_data)
    loader = getattr(context, "market_data_loader", None)
    start_date = str(period["start_date"]) if period.get("start_date") else None
    end_date = str(period["end_date"]) if period.get("end_date") else None
    summary = oracle._load_summary(market_data, start_date, end_date, loader)
    partitioned_chain = oracle._partition_chain_cache(market_data, loader)

    day_offsets = [0]
    event_timestamps_ns: list[int] = []
    event_seconds: list[int] = []
    straddles: list[float] = []
    current_future: list[float] = []
    d2_future: list[float] = []
    r6_future: list[float] = []
    r6_available: list[int] = []
    quote_offsets = [0]
    quote_strikes: list[int] = []
    quote_ce: list[float] = []
    quote_pe: list[float] = []
    selection_strikes = [
        [[], []] for _ in range(len(premium_values))
    ]
    selection_raw = [
        [[], []] for _ in range(len(premium_values))
    ]
    max_day_events = 0

    for day, raw_day_summary in summary.groupby("trade_date", sort=True):
        if not (raw_day_summary["DTE"] == 0).any():
            continue
        day_summary = raw_day_summary.sort_values("ts").reset_index(drop=True)
        summary_lookup = oracle._summary_lookup(day_summary)
        quote_lookup = oracle._load_chain_lookup(
            market_data, day, loader, partitioned_chain
        )
        day_count = 0
        for _, row in day_summary.loc[day_summary["DTE"] == 0].iterrows():
            timestamp = pd.Timestamp(row["ts"])
            quotes = oracle._quotes_at(quote_lookup, timestamp)
            if quotes is None:
                continue
            current = oracle._summary_row_at_or_before(summary_lookup, timestamp)
            d2 = oracle._summary_row_at_or_before(
                summary_lookup, timestamp - pd.Timedelta(minutes=3)
            )
            r6 = oracle._summary_row_at_or_before(
                summary_lookup, timestamp - pd.Timedelta(minutes=5)
            )
            event_timestamps_ns.append(int(timestamp.value))
            event_seconds.append(
                timestamp.hour * 3600 + timestamp.minute * 60 + timestamp.second
            )
            straddles.append(float(row["straddle_future"]))
            current_future.append(float(current["future_close"]) if current is not None else np.nan)
            d2_future.append(float(d2["future_close"]) if d2 is not None else np.nan)
            r6_future.append(float(r6["future_close"]) if r6 is not None else np.nan)
            r6_available.append(1 if r6 is not None else 0)

            quote_strikes.extend(int(value) for value in quotes.strikes)
            quote_ce.extend(float(value) for value in quotes.ce_close)
            quote_pe.extend(float(value) for value in quotes.pe_close)
            quote_offsets.append(len(quote_strikes))

            for premium_index, premium in enumerate(premium_values):
                for direction_index, direction in enumerate(("call", "put")):
                    try:
                        _, strike, raw = oracle._select_short_leg(
                            quotes,
                            direction,
                            int(row["future_atm"]),
                            float(row["straddle_future"]),
                            float(premium),
                        )
                    except ValueError:
                        strike, raw = -1, np.nan
                    selection_strikes[premium_index][direction_index].append(strike)
                    selection_raw[premium_index][direction_index].append(raw)
            day_count += 1

        if day_count:
            day_offsets.append(len(event_timestamps_ns))
            max_day_events = max(max_day_events, day_count)

    event_count = len(event_timestamps_ns)
    if event_count == 0:
        raise ValueError("The selected period contains no DTE=0 rows with option quotes")

    selection_strike_array = np.empty(
        (len(premium_values), 2, event_count), dtype=np.int64
    )
    selection_raw_array = np.empty(
        (len(premium_values), 2, event_count), dtype=np.float64
    )
    for premium_index in range(len(premium_values)):
        for direction_index in range(2):
            selection_strike_array[premium_index, direction_index] = np.asarray(
                selection_strikes[premium_index][direction_index], dtype=np.int64
            )
            selection_raw_array[premium_index, direction_index] = np.asarray(
                selection_raw[premium_index][direction_index], dtype=np.float64
            )

    arrays = R6BatchData(
        day_offsets=np.ascontiguousarray(day_offsets, dtype=np.int64),
        event_timestamps_ns=np.ascontiguousarray(event_timestamps_ns, dtype=np.int64),
        event_seconds=np.ascontiguousarray(event_seconds, dtype=np.int32),
        straddle=np.ascontiguousarray(straddles, dtype=np.float64),
        current_future=np.ascontiguousarray(current_future, dtype=np.float64),
        d2_future=np.ascontiguousarray(d2_future, dtype=np.float64),
        r6_future=np.ascontiguousarray(r6_future, dtype=np.float64),
        r6_available=np.ascontiguousarray(r6_available, dtype=np.int8),
        quote_offsets=np.ascontiguousarray(quote_offsets, dtype=np.int64),
        quote_strikes=np.ascontiguousarray(quote_strikes, dtype=np.int64),
        quote_ce=np.ascontiguousarray(quote_ce, dtype=np.float64),
        quote_pe=np.ascontiguousarray(quote_pe, dtype=np.float64),
        selection_strikes=np.ascontiguousarray(selection_strike_array),
        selection_raw=np.ascontiguousarray(selection_raw_array),
        premium_values=np.ascontiguousarray(premium_values, dtype=np.float64),
        max_day_events=max_day_events,
    )
    return arrays, parameter_matrix


@njit(cache=True, inline="always")
def _direction_code(current: float, d2: float, r6: float, r6_available: int, threshold_pct: float) -> int:
    if np.isnan(current) or np.isnan(d2) or r6_available == 0:
        return -1
    if current > d2:
        direction = 1  # put / PE
    elif current < d2:
        direction = 0  # call / CE
    else:
        return -1
    if not np.isnan(r6):
        move = current - r6
        threshold = abs(r6) * threshold_pct
        if direction == 1 and move <= -threshold:
            return 0
        if direction == 0 and move >= threshold:
            return 1
    return direction


@njit(cache=True, inline="always")
def _quote_price(
    event_index: int,
    strike: int,
    option_side: int,
    quote_offsets: np.ndarray,
    quote_strikes: np.ndarray,
    quote_ce: np.ndarray,
    quote_pe: np.ndarray,
) -> float:
    start = quote_offsets[event_index]
    end = quote_offsets[event_index + 1]
    for quote_index in range(start, end):
        if quote_strikes[quote_index] == strike:
            value = quote_ce[quote_index] if option_side == 0 else quote_pe[quote_index]
            if not np.isnan(value) and value > 0.0:
                return value
            return np.nan
    return np.nan


@njit(cache=True, inline="always")
def _failure_result(status: int) -> np.ndarray:
    result = np.empty(METRIC_COUNT, dtype=np.float64)
    for index in range(METRIC_COUNT):
        result[index] = np.nan
    result[M_STATUS] = status
    return result


@njit(cache=True)
def _simulate_one(
    parameters: np.ndarray,
    day_offsets: np.ndarray,
    event_timestamps_ns: np.ndarray,
    event_seconds: np.ndarray,
    straddle: np.ndarray,
    current_future: np.ndarray,
    d2_future: np.ndarray,
    r6_future: np.ndarray,
    r6_available: np.ndarray,
    quote_offsets: np.ndarray,
    quote_strikes: np.ndarray,
    quote_ce: np.ndarray,
    quote_pe: np.ndarray,
    selection_strikes: np.ndarray,
    selection_raw: np.ndarray,
    max_day_events: int,
) -> np.ndarray:
    premium_index = int(parameters[P_PREMIUM_INDEX])
    add_decay = parameters[P_ADD_DECAY]
    stop_loss = parameters[P_STOP_LOSS]
    profit_booking = parameters[P_PROFIT_BOOKING]
    trailing_activation = parameters[P_TRAILING_ACTIVATION]
    r6_threshold = parameters[P_R6_THRESHOLD]
    trailing_gap = parameters[P_TRAILING_GAP]
    max_lots = int(parameters[P_MAX_LOTS])
    max_reentries = int(parameters[P_MAX_REENTRIES])
    capital = parameters[P_CAPITAL]
    margin_per_lot = parameters[P_MARGIN_PER_LOT]
    lot_size = int(parameters[P_LOT_SIZE])
    slippage = parameters[P_SLIPPAGE]
    fees_per_leg = parameters[P_FEES_PER_LEG]
    entry_seconds = int(parameters[P_ENTRY_SECONDS])
    square_off_seconds = int(parameters[P_SQUARE_OFF_SECONDS])

    if (
        premium_index < 0
        or premium_index >= selection_strikes.shape[0]
        or max_lots < 1
        or max_reentries < 0
        or lot_size < 1
        or margin_per_lot <= 0.0
        or capital <= 0.0
    ):
        return _failure_result(STATUS_INVALID_PARAMETERS)

    open_slot_id = np.empty(max_lots, dtype=np.int64)
    open_reentry = np.empty(max_lots, dtype=np.int64)
    open_side = np.empty(max_lots, dtype=np.int8)
    open_strike = np.empty(max_lots, dtype=np.int64)
    open_entry_raw = np.empty(max_lots, dtype=np.float64)
    open_entry_fill = np.empty(max_lots, dtype=np.float64)
    open_best_raw = np.empty(max_lots, dtype=np.float64)
    open_trailing = np.empty(max_lots, dtype=np.int8)

    pending_slot_id = np.empty(max_day_events, dtype=np.int64)
    pending_reentry = np.empty(max_day_events, dtype=np.int64)
    pending_earliest = np.empty(max_day_events, dtype=np.int64)
    ready_slot_id = np.empty(max_day_events, dtype=np.int64)
    ready_reentry = np.empty(max_day_events, dtype=np.int64)

    gross_points = 0.0
    realized_points = 0.0
    total_fees = 0.0
    trade_count = 0
    next_slot_id = 1
    minimum_equity = 0.0
    peak_equity = 0.0
    maximum_drawdown = 0.0

    for day_index in range(day_offsets.shape[0] - 1):
        day_start = day_offsets[day_index]
        day_end = day_offsets[day_index + 1]
        first_event = -1
        for event_index in range(day_start, day_end):
            if event_seconds[event_index] >= entry_seconds:
                first_event = event_index
                break
        if first_event < 0:
            continue

        final_event = day_end - 1
        for event_index in range(first_event, day_end):
            if event_seconds[event_index] >= square_off_seconds:
                final_event = event_index
                break

        open_count = 0
        pending_count = 0
        last_entry_straddle = np.nan
        starting_equity = realized_points * lot_size
        if starting_equity < minimum_equity:
            minimum_equity = starting_equity
        if starting_equity > peak_equity:
            peak_equity = starting_equity
        drawdown = starting_equity - peak_equity
        if drawdown < maximum_drawdown:
            maximum_drawdown = drawdown

        for event_index in range(first_event, day_end):
            write_index = 0
            original_open_count = open_count
            for slot_index in range(original_open_count):
                raw = _quote_price(
                    event_index,
                    open_strike[slot_index],
                    open_side[slot_index],
                    quote_offsets,
                    quote_strikes,
                    quote_ce,
                    quote_pe,
                )
                if np.isnan(raw):
                    return _failure_result(STATUS_MISSING_OPEN_POSITION_QUOTE)

                best_raw = open_best_raw[slot_index]
                if raw < best_raw:
                    best_raw = raw
                trailing_active = open_trailing[slot_index]
                if raw <= open_entry_raw[slot_index] * (1.0 - trailing_activation):
                    trailing_active = 1

                exit_code = 0
                if raw * (1.0 + slippage) >= open_entry_fill[slot_index] * (1.0 + stop_loss):
                    exit_code = 1
                elif raw * (1.0 + slippage) <= open_entry_fill[slot_index] * (1.0 - profit_booking):
                    exit_code = 2
                elif trailing_active == 1 and raw >= best_raw * (1.0 + trailing_gap):
                    exit_code = 3
                elif event_index >= final_event:
                    exit_code = 4

                if exit_code:
                    points = open_entry_fill[slot_index] - raw * (1.0 + slippage)
                    gross_points += points
                    realized_points += points - fees_per_leg / lot_size
                    total_fees += fees_per_leg
                    trade_count += 1
                    if exit_code != 4 and open_reentry[slot_index] < max_reentries:
                        if pending_count >= max_day_events:
                            return _failure_result(STATUS_PENDING_REENTRY_CAPACITY)
                        pending_slot_id[pending_count] = open_slot_id[slot_index]
                        pending_reentry[pending_count] = open_reentry[slot_index] + 1
                        pending_earliest[pending_count] = (
                            event_timestamps_ns[event_index] + NANOSECONDS_PER_MINUTE
                        )
                        pending_count += 1
                else:
                    open_slot_id[write_index] = open_slot_id[slot_index]
                    open_reentry[write_index] = open_reentry[slot_index]
                    open_side[write_index] = open_side[slot_index]
                    open_strike[write_index] = open_strike[slot_index]
                    open_entry_raw[write_index] = open_entry_raw[slot_index]
                    open_entry_fill[write_index] = open_entry_fill[slot_index]
                    open_best_raw[write_index] = best_raw
                    open_trailing[write_index] = trailing_active
                    write_index += 1
            open_count = write_index

            ready_count = 0
            future_count = 0
            for pending_index in range(pending_count):
                if pending_earliest[pending_index] <= event_timestamps_ns[event_index]:
                    ready_slot_id[ready_count] = pending_slot_id[pending_index]
                    ready_reentry[ready_count] = pending_reentry[pending_index]
                    ready_count += 1
                else:
                    pending_slot_id[future_count] = pending_slot_id[pending_index]
                    pending_reentry[future_count] = pending_reentry[pending_index]
                    pending_earliest[future_count] = pending_earliest[pending_index]
                    future_count += 1
            pending_count = future_count

            for ready_index in range(ready_count):
                if event_index >= final_event or open_count >= max_lots:
                    continue
                if (open_count + 1) * margin_per_lot > capital:
                    continue
                direction = _direction_code(
                    current_future[event_index],
                    d2_future[event_index],
                    r6_future[event_index],
                    r6_available[event_index],
                    r6_threshold,
                )
                if direction < 0:
                    if pending_count >= max_day_events:
                        return _failure_result(STATUS_PENDING_REENTRY_CAPACITY)
                    pending_slot_id[pending_count] = ready_slot_id[ready_index]
                    pending_reentry[pending_count] = ready_reentry[ready_index]
                    pending_earliest[pending_count] = event_timestamps_ns[event_index]
                    pending_count += 1
                    continue
                strike = selection_strikes[premium_index, direction, event_index]
                entry_raw = selection_raw[premium_index, direction, event_index]
                if strike < 0 or np.isnan(entry_raw):
                    if pending_count >= max_day_events:
                        return _failure_result(STATUS_PENDING_REENTRY_CAPACITY)
                    pending_slot_id[pending_count] = ready_slot_id[ready_index]
                    pending_reentry[pending_count] = ready_reentry[ready_index]
                    pending_earliest[pending_count] = event_timestamps_ns[event_index]
                    pending_count += 1
                    continue
                open_slot_id[open_count] = ready_slot_id[ready_index]
                open_reentry[open_count] = ready_reentry[ready_index]
                open_side[open_count] = direction
                open_strike[open_count] = strike
                open_entry_raw[open_count] = entry_raw
                open_entry_fill[open_count] = entry_raw * (1.0 - slippage)
                open_best_raw[open_count] = entry_raw
                open_trailing[open_count] = 0
                open_count += 1

            current_straddle = straddle[event_index]
            should_open = False
            if np.isnan(last_entry_straddle) and event_index < final_event:
                should_open = True
            elif (
                not np.isnan(last_entry_straddle)
                and event_index < final_event
                and current_straddle <= last_entry_straddle * (1.0 - add_decay)
            ):
                should_open = True

            if (
                should_open
                and open_count < max_lots
                and (open_count + 1) * margin_per_lot <= capital
            ):
                direction = _direction_code(
                    current_future[event_index],
                    d2_future[event_index],
                    r6_future[event_index],
                    r6_available[event_index],
                    r6_threshold,
                )
                if direction >= 0:
                    strike = selection_strikes[premium_index, direction, event_index]
                    entry_raw = selection_raw[premium_index, direction, event_index]
                    if strike < 0 or np.isnan(entry_raw):
                        return _failure_result(STATUS_MISSING_NEW_ENTRY_SELECTION)
                    open_slot_id[open_count] = next_slot_id
                    open_reentry[open_count] = 0
                    open_side[open_count] = direction
                    open_strike[open_count] = strike
                    open_entry_raw[open_count] = entry_raw
                    open_entry_fill[open_count] = entry_raw * (1.0 - slippage)
                    open_best_raw[open_count] = entry_raw
                    open_trailing[open_count] = 0
                    open_count += 1
                    next_slot_id += 1
                    last_entry_straddle = current_straddle

            unrealized_points = 0.0
            for slot_index in range(open_count):
                raw = _quote_price(
                    event_index,
                    open_strike[slot_index],
                    open_side[slot_index],
                    quote_offsets,
                    quote_strikes,
                    quote_ce,
                    quote_pe,
                )
                if np.isnan(raw):
                    return _failure_result(STATUS_MISSING_OPEN_POSITION_QUOTE)
                unrealized_points += open_entry_fill[slot_index] - raw * (1.0 + slippage)
            equity = (realized_points + unrealized_points) * lot_size
            if equity < minimum_equity:
                minimum_equity = equity
            if equity > peak_equity:
                peak_equity = equity
            drawdown = equity - peak_equity
            if drawdown < maximum_drawdown:
                maximum_drawdown = drawdown

        if open_count:
            return _failure_result(STATUS_OPEN_POSITION_AT_END_OF_DAY)

    gross_pnl = gross_points * lot_size
    net_pnl = realized_points * lot_size
    result = np.empty(METRIC_COUNT, dtype=np.float64)
    result[M_STATUS] = STATUS_OK
    result[M_GROSS_PNL] = gross_pnl
    result[M_FEES] = total_fees
    result[M_NET_PNL] = net_pnl
    result[M_TRADE_COUNT] = trade_count
    result[M_FINAL_REALIZED] = net_pnl
    result[M_FINAL_UNREALIZED] = 0.0
    result[M_MIN_EQUITY] = minimum_equity
    result[M_MAX_DRAWDOWN] = maximum_drawdown
    return result


@njit(cache=True)
def _batch_kernel_serial(
    parameter_matrix: np.ndarray,
    day_offsets: np.ndarray,
    event_timestamps_ns: np.ndarray,
    event_seconds: np.ndarray,
    straddle: np.ndarray,
    current_future: np.ndarray,
    d2_future: np.ndarray,
    r6_future: np.ndarray,
    r6_available: np.ndarray,
    quote_offsets: np.ndarray,
    quote_strikes: np.ndarray,
    quote_ce: np.ndarray,
    quote_pe: np.ndarray,
    selection_strikes: np.ndarray,
    selection_raw: np.ndarray,
    max_day_events: int,
) -> np.ndarray:
    output = np.empty((parameter_matrix.shape[0], METRIC_COUNT), dtype=np.float64)
    for combination_index in range(parameter_matrix.shape[0]):
        output[combination_index] = _simulate_one(
            parameter_matrix[combination_index], day_offsets, event_timestamps_ns,
            event_seconds, straddle, current_future, d2_future, r6_future,
            r6_available, quote_offsets, quote_strikes, quote_ce, quote_pe, selection_strikes,
            selection_raw, max_day_events,
        )
    return output


@njit(cache=True, parallel=True)
def _batch_kernel_parallel(
    parameter_matrix: np.ndarray,
    day_offsets: np.ndarray,
    event_timestamps_ns: np.ndarray,
    event_seconds: np.ndarray,
    straddle: np.ndarray,
    current_future: np.ndarray,
    d2_future: np.ndarray,
    r6_future: np.ndarray,
    r6_available: np.ndarray,
    quote_offsets: np.ndarray,
    quote_strikes: np.ndarray,
    quote_ce: np.ndarray,
    quote_pe: np.ndarray,
    selection_strikes: np.ndarray,
    selection_raw: np.ndarray,
    max_day_events: int,
) -> np.ndarray:
    output = np.empty((parameter_matrix.shape[0], METRIC_COUNT), dtype=np.float64)
    for combination_index in prange(parameter_matrix.shape[0]):
        output[combination_index] = _simulate_one(
            parameter_matrix[combination_index], day_offsets, event_timestamps_ns,
            event_seconds, straddle, current_future, d2_future, r6_future,
            r6_available, quote_offsets, quote_strikes, quote_ce, quote_pe, selection_strikes,
            selection_raw, max_day_events,
        )
    return output


def run_batch_kernel(
    data: R6BatchData,
    parameter_matrix: np.ndarray,
    *,
    workers: int | None = None,
    parallel: bool = True,
) -> np.ndarray:
    """Evaluate one numeric chunk without creating trades or dashboard files."""

    if not NUMBA_AVAILABLE:
        raise RuntimeError(
            "Numba is required for the R6 batch accelerator; install the project's strategy dependencies"
        )
    parameters = np.ascontiguousarray(parameter_matrix, dtype=np.float64)
    if parameters.ndim != 2 or parameters.shape[1] != len(PARAMETER_COLUMNS):
        raise ValueError(
            f"parameter_matrix must have shape (n, {len(PARAMETER_COLUMNS)})"
        )
    previous_threads = numba.get_num_threads()
    requested = previous_threads if workers is None else int(workers)
    maximum = min(int(numba.config.NUMBA_NUM_THREADS), os.cpu_count() or 1)
    selected_threads = max(1, min(requested, maximum))
    try:
        numba.set_num_threads(selected_threads)
        kernel = _batch_kernel_parallel if parallel and selected_threads > 1 else _batch_kernel_serial
        return kernel(
            parameters,
            data.day_offsets,
            data.event_timestamps_ns,
            data.event_seconds,
            data.straddle,
            data.current_future,
            data.d2_future,
            data.r6_future,
            data.r6_available,
            data.quote_offsets,
            data.quote_strikes,
            data.quote_ce,
            data.quote_pe,
            data.selection_strikes,
            data.selection_raw,
            data.max_day_events,
        )
    finally:
        numba.set_num_threads(previous_threads)


def oracle_metrics(result: Mapping[str, Any]) -> np.ndarray:
    """Reduce an authoritative oracle result to the kernel's metric layout."""

    rows = list(result["completed_trades"])
    gross = sum(
        (float(row["entry_price"]) - float(row["exit_price"]))
        * float(row["quantity"])
        * float(row.get("multiplier", 1))
        for row in rows
    )
    fees = sum(float(row.get("fees", 0)) for row in rows)
    snapshots = list(result.get("equity_snapshots") or [])
    minimum_equity = 0.0
    peak = 0.0
    maximum_drawdown = 0.0
    for snapshot in snapshots:
        equity = float(snapshot["realized_pnl"]) + float(snapshot["unrealized_pnl"])
        minimum_equity = min(minimum_equity, equity)
        peak = max(peak, equity)
        maximum_drawdown = min(maximum_drawdown, equity - peak)
    final_realized = float(snapshots[-1]["realized_pnl"]) if snapshots else gross - fees
    final_unrealized = float(snapshots[-1]["unrealized_pnl"]) if snapshots else 0.0
    return np.asarray(
        [
            STATUS_OK,
            gross,
            fees,
            gross - fees,
            len(rows),
            final_realized,
            final_unrealized,
            minimum_equity,
            maximum_drawdown,
        ],
        dtype=np.float64,
    )
