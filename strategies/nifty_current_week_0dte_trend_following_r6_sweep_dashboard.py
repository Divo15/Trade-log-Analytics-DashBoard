"""NIFTY current-week 0-DTE trend-following short-option sweep strategy.

Rules implemented:

- Dataset: choose the Current Week dataset in the dashboard.
- Trade only DTE 0 rows.
- First entry: first available DTE 0 row at or after 09:18 Asia/Kolkata.
- D2 base direction: compare latest completed NIFTY FUT close with the FUT
  close 3 minutes earlier.
  - Higher: bullish, sell PE.
  - Lower: bearish, sell CE.
- R6 reversal: compare latest FUT close with the FUT close 5 minutes earlier.
  If this last-5-minute move is strongly opposite to D2, flip the direction.
  The default "strong" threshold is 0.10% and is configurable.
- Strike selection: sell the CE/PE premium closest to 30% of the current ATM
  straddle premium. CE candidates are at/above ATM; PE candidates are at/below
  ATM.
- Add lots when the current straddle premium decays 10% from the last entry
  reference. Maximum active lots/slots per day is 4.
- Capital/margin model: INR 12,00,000 total capital, INR 3,00,000 per lot.
- Each stopped or profit-booked trade may re-enter once.
- Optimizer: tests 144 structure combinations across strike selection, add-on
  decay, SL, profit booking, trailing activation, and R6 threshold.
- Exit conditions:
  - Stop loss: option buyback premium rises by stop_loss_pct from entry.
  - Profit booking: option buyback premium decays by profit_booking_pct.
  - Trailing: after trailing_activation_pct decay, trail from best observed
    buyback premium by trailing_gap_pct.
  - EOD: close open positions at or after 15:15, or final available quote.
- Slippage: 0.5% adverse on every fill.
- Fees: zero.

Required dataset schema:

- context.market_data / "nifty_summary.parquet" with columns:
  datetime, future_close, future_atm, straddle_future, DTE.
- context.market_data / "nifty_chain" / "*.parquet" with columns:
  datetime, strike, ce_close, pe_close.

Dependencies: pandas, DuckDB, tzdata/zoneinfo.
The module is import-safe: it does not read data or run a backtest at import.

Dashboard integration: reports completed expiry days, builds a typed date-
partitioned option-chain cache, reuses prepared frames through the dashboard
runtime cache, accounts for configured fees at each close, and validates
observed equity without overwriting it.
Incomplete position marks or unclosed positions fail explicitly; no fills are
invented. Trading rules, defaults and all 144 sweep mappings are unchanged.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from itertools import product
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd


STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "sweep"

STRIKE_PREMIUM_PCTS = (0.25, 0.30, 0.35)
ADD_ON_DECAY_PCTS = (0.075, 0.10, 0.125)
STOP_LOSS_PCTS = (0.20, 0.30)
PROFIT_BOOKING_PCTS = (0.40, 0.55)
TRAILING_ACTIVATION_PCTS = (0.20, 0.35)
R6_REVERSAL_THRESHOLD_PCTS = (0.0005, 0.0010)
SWEEP_PARAMETER_SETS = tuple(
    {
        "strike_premium_pct_of_straddle": strike_pct,
        "add_on_straddle_decay_pct": add_decay,
        "stop_loss_pct": stop_loss,
        "profit_booking_pct": profit_booking,
        "trailing_activation_pct": trailing_activation,
        "r6_reversal_threshold_pct": r6_threshold,
    }
    for (
        strike_pct,
        add_decay,
        stop_loss,
        profit_booking,
        trailing_activation,
        r6_threshold,
    ) in product(
        STRIKE_PREMIUM_PCTS,
        ADD_ON_DECAY_PCTS,
        STOP_LOSS_PCTS,
        PROFIT_BOOKING_PCTS,
        TRAILING_ACTIVATION_PCTS,
        R6_REVERSAL_THRESHOLD_PCTS,
    )
)

STRATEGY_NAME = "NIFTY current-week 0DTE D2/R6 trend-following short option sweep"
TIMEZONE = ZoneInfo("Asia/Kolkata")
DATETIME_FORMAT = "%d/%m/%Y %H:%M:%S"

DEFAULTS: dict[str, Any] = {
    "entry_time": "09:18",
    "square_off_time": "15:15",
    "strike_premium_pct_of_straddle": 0.30,
    "add_on_straddle_decay_pct": 0.10,
    "stop_loss_pct": 0.30,
    "profit_booking_pct": 0.50,
    "trailing_activation_pct": 0.30,
    "trailing_gap_pct": 0.15,
    "r6_reversal_threshold_pct": 0.001,
    "max_lots_per_day": 4,
    "max_reentries_per_trade": 1,
    "capital": 1_200_000.0,
    "margin_per_lot": 300_000.0,
    "lot_size": 65,
    "slippage": 0.005,
    "fees_per_leg": 0.0,
}


@dataclass(frozen=True)
class StrategyContext:
    run_id: str
    market_data: Path
    config: Mapping[str, Any]


@dataclass
class Slot:
    slot_id: int
    batch_id: str
    reentry_index: int
    entry_ts: pd.Timestamp
    option_type: str
    strike: int
    entry_raw: float
    entry_fill: float
    best_buyback_raw: float
    trailing_active: bool


@dataclass(frozen=True)
class PendingReentry:
    slot_id: int
    reentry_index: int
    earliest_ts: pd.Timestamp


def _require_mapping(value: Any, name: str, *, allow_missing: bool = False) -> Mapping[str, Any]:
    if value is None and allow_missing:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _parse_time(value: str | time) -> time:
    if isinstance(value, time):
        return value
    parts = [int(part) for part in str(value).split(":")]
    if len(parts) == 2:
        return time(parts[0], parts[1])
    if len(parts) == 3:
        return time(parts[0], parts[1], parts[2])
    raise ValueError(f"Invalid time value: {value!r}")


def _timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(TIMEZONE)
    return timestamp.tz_convert(TIMEZONE)


def _positive_price(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return pd.notna(number) and number > 0


def _load_summary(
    market_data: Path,
    start_date: str | None,
    end_date: str | None,
    market_data_loader: Any = None,
) -> pd.DataFrame:
    summary_path = market_data / "nifty_summary.parquet"
    if not summary_path.is_file():
        raise FileNotFoundError(f"Missing required summary file: {summary_path}")

    def read_and_prepare() -> pd.DataFrame:
        connection = duckdb.connect(":memory:")
        try:
            if start_date and end_date:
                summary = connection.execute(
                    """
                    SELECT datetime, DTE, future_close, future_atm, straddle_future
                    FROM read_parquet(?)
                    WHERE CAST(STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE)
                          BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                    ORDER BY STRPTIME(datetime, '%d/%m/%Y %H:%M:%S')
                    """,
                    [str(summary_path), start_date, end_date],
                ).fetchdf()
            else:
                summary = connection.execute(
                    """
                    SELECT datetime, DTE, future_close, future_atm, straddle_future
                    FROM read_parquet(?)
                    ORDER BY STRPTIME(datetime, '%d/%m/%Y %H:%M:%S')
                    """,
                    [str(summary_path)],
                ).fetchdf()
        finally:
            connection.close()

        required = {"datetime", "DTE", "future_close", "future_atm", "straddle_future"}
        missing = required - set(summary.columns)
        if missing:
            raise ValueError(f"Summary data is missing required columns: {sorted(missing)}")
        if summary.empty:
            raise ValueError("Selected period contains no summary rows")
        summary = summary.copy()
        summary["ts"] = pd.to_datetime(summary["datetime"], format=DATETIME_FORMAT)
        summary["trade_date"] = summary["ts"].dt.date
        summary["DTE"] = summary["DTE"].astype(int)
        return summary.sort_values("ts").drop_duplicates("ts", keep="first").reset_index(drop=True)

    if callable(getattr(market_data_loader, "read_frame", None)):
        return market_data_loader.read_frame(
            [summary_path],
            ("r6-summary-prepared-v1", start_date, end_date),
            read_and_prepare,
        )
    return read_and_prepare()


def _load_chain_for_day(
    market_data: Path,
    day: Any,
    market_data_loader: Any = None,
    partitioned_chain: Path | None = None,
) -> pd.DataFrame:
    chain_folder = market_data / "nifty_chain"
    chain_glob = chain_folder / "*.parquet"
    chain_paths = sorted(chain_folder.glob("*.parquet")) if chain_folder.is_dir() else []
    if not chain_paths:
        raise FileNotFoundError(f"Missing required option-chain parquet files: {chain_folder}")

    day_iso = pd.Timestamp(day).date().isoformat()
    partition_paths: list[Path] = []
    if partitioned_chain is not None:
        partition_folder = partitioned_chain / f"trade_date={day_iso}"
        partition_paths = sorted(partition_folder.glob("*.parquet"))

    def read_and_prepare() -> pd.DataFrame:
        connection = duckdb.connect(":memory:")
        try:
            if partitioned_chain is not None:
                if not partition_paths:
                    return pd.DataFrame(columns=["datetime", "strike", "ce_close", "pe_close", "ts"])
                chain = connection.execute(
                    """
                    SELECT ts, strike, ce_close, pe_close
                    FROM read_parquet(?)
                    ORDER BY ts, strike
                    """,
                    [str(partition_folder / "*.parquet")],
                ).fetchdf()
            else:
                chain = connection.execute(
                    """
                    SELECT datetime, strike, ce_close, pe_close
                    FROM read_parquet(?)
                    WHERE CAST(STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE) = CAST(? AS DATE)
                    ORDER BY STRPTIME(datetime, '%d/%m/%Y %H:%M:%S'), strike
                    """,
                    [str(chain_glob), str(day)],
                ).fetchdf()
        finally:
            connection.close()
        required = {"ts", "strike", "ce_close", "pe_close"} if partitioned_chain is not None else {
            "datetime", "strike", "ce_close", "pe_close"
        }
        missing = required - set(chain.columns)
        if missing:
            raise ValueError(f"Chain data is missing required columns: {sorted(missing)}")
        if chain.empty:
            return chain
        if partitioned_chain is None:
            chain["ts"] = pd.to_datetime(chain["datetime"], format=DATETIME_FORMAT)
        else:
            chain["ts"] = pd.to_datetime(chain["ts"])
        chain["strike"] = chain["strike"].astype(int)
        return chain

    if callable(getattr(market_data_loader, "read_frame", None)):
        sources = partition_paths if partitioned_chain is not None else chain_paths
        return market_data_loader.read_frame(
            sources,
            ("r6-chain-day-prepared-v2", day_iso, partitioned_chain is not None),
            read_and_prepare,
        )
    return read_and_prepare()


def _partition_chain_cache(
    market_data: Path,
    market_data_loader: Any,
) -> Path | None:
    """Materialize typed Hive date partitions in the worker-owned temp cache."""
    partition = getattr(market_data_loader, "partitioned_dataset", None)
    if not callable(partition):
        return None
    chain_folder = market_data / "nifty_chain"
    chain_paths = sorted(chain_folder.glob("*.parquet")) if chain_folder.is_dir() else []
    if not chain_paths:
        raise FileNotFoundError(f"Missing required option-chain parquet files: {chain_folder}")

    def build(destination: Path) -> None:
        source = str(chain_folder / "*.parquet").replace("'", "''")
        target = destination.as_posix().replace("'", "''")
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                f"""
                COPY (
                    SELECT
                        CAST(STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS DATE) AS trade_date,
                        STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS ts,
                        CAST(strike AS INTEGER) AS strike,
                        ce_close,
                        pe_close
                    FROM read_parquet('{source}')
                ) TO '{target}' (
                    FORMAT PARQUET,
                    PARTITION_BY (trade_date),
                    COMPRESSION ZSTD
                )
                """
            )
        finally:
            connection.close()

    return partition(chain_paths, "r6-chain-by-trade-date-v1", build)


@dataclass(frozen=True)
class QuoteBook:
    """Quotes for one timestamp plus first-row strike access."""

    strikes: np.ndarray
    ce_close: np.ndarray
    pe_close: np.ndarray
    first_index_by_strike: dict[int, int]


@dataclass(frozen=True)
class QuoteLookup:
    """Sorted timestamp and strike indexes prepared once per trading day."""

    timestamps: tuple[pd.Timestamp, ...]
    by_timestamp: dict[pd.Timestamp, QuoteBook]


@dataclass(frozen=True)
class SummaryLookup:
    """Sorted futures rows supporting O(log n) at-or-before lookup."""

    frame: pd.DataFrame
    timestamps: pd.DatetimeIndex


def _frame_lookup(frame: pd.DataFrame) -> QuoteLookup:
    by_timestamp: dict[pd.Timestamp, QuoteBook] = {}
    for ts, group in frame.groupby("ts", sort=True):
        strikes = group["strike"].to_numpy(dtype=np.int64, copy=True)
        ce_close = group["ce_close"].to_numpy(dtype=np.float64, copy=True)
        pe_close = group["pe_close"].to_numpy(dtype=np.float64, copy=True)
        strikes.flags.writeable = False
        ce_close.flags.writeable = False
        pe_close.flags.writeable = False
        first_index_by_strike: dict[int, int] = {}
        for index, strike in enumerate(strikes):
            first_index_by_strike.setdefault(int(strike), index)
        by_timestamp[pd.Timestamp(ts)] = QuoteBook(
            strikes, ce_close, pe_close, first_index_by_strike
        )
    return QuoteLookup(tuple(by_timestamp), by_timestamp)


def _load_chain_lookup(
    market_data: Path,
    day: Any,
    market_data_loader: Any = None,
    partitioned_chain: Path | None = None,
) -> QuoteLookup:
    """Reuse an immutable daily quote index across sweep combinations."""
    day_iso = pd.Timestamp(day).date().isoformat()
    if partitioned_chain is not None:
        source_folder = partitioned_chain / f"trade_date={day_iso}"
        sources = sorted(source_folder.glob("*.parquet"))
    else:
        source_folder = market_data / "nifty_chain"
        sources = sorted(source_folder.glob("*.parquet")) if source_folder.is_dir() else []

    def build() -> QuoteLookup:
        chain = _load_chain_for_day(
            market_data, day, market_data_loader, partitioned_chain
        )
        return _frame_lookup(chain)

    shared_object = getattr(market_data_loader, "shared_object", None)
    if callable(shared_object) and sources:
        return shared_object(
            sources,
            (
                "r6-chain-daily-quote-index-v1",
                str(market_data.resolve()),
                day_iso,
                partitioned_chain is not None,
            ),
            build,
        )
    return build()


def _summary_lookup(day_summary: pd.DataFrame) -> SummaryLookup:
    # _run_day receives this frame in timestamp order. Keeping that stable order
    # preserves the historical "last duplicate wins" behavior.
    frame = day_summary.reset_index(drop=True)
    return SummaryLookup(frame, pd.DatetimeIndex(frame["ts"]))


def _quotes_at(
    lookup: QuoteLookup,
    timestamp: pd.Timestamp,
) -> QuoteBook | None:
    timestamp = pd.Timestamp(timestamp)
    exact = lookup.by_timestamp.get(timestamp)
    if exact is not None:
        return exact
    position = bisect_right(lookup.timestamps, timestamp)
    if position == 0:
        return None
    nearest = lookup.timestamps[position - 1]
    if timestamp - nearest > pd.Timedelta(minutes=2):
        return None
    return lookup.by_timestamp[nearest]


def _summary_row_at_or_before(summary: SummaryLookup, timestamp: pd.Timestamp) -> pd.Series | None:
    position = summary.timestamps.searchsorted(pd.Timestamp(timestamp), side="right")
    if position == 0:
        return None
    return summary.frame.iloc[position - 1]


def _direction_at(
    summary: SummaryLookup,
    timestamp: pd.Timestamp,
    r6_reversal_threshold_pct: float,
) -> str | None:
    current = _summary_row_at_or_before(summary, timestamp)
    d2_reference = _summary_row_at_or_before(summary, timestamp - pd.Timedelta(minutes=3))
    r6_reference = _summary_row_at_or_before(summary, timestamp - pd.Timedelta(minutes=5))
    if current is None or d2_reference is None or r6_reference is None:
        return None

    current_close = float(current["future_close"])
    d2_close = float(d2_reference["future_close"])
    if current_close > d2_close:
        direction = "put"
    elif current_close < d2_close:
        direction = "call"
    else:
        return None

    r6_close = float(r6_reference["future_close"])
    r6_move = current_close - r6_close
    reversal_threshold = abs(r6_close) * float(r6_reversal_threshold_pct)
    if direction == "put" and r6_move <= -reversal_threshold:
        return "call"
    if direction == "call" and r6_move >= reversal_threshold:
        return "put"
    return direction


def _price_for_leg(quotes: QuoteBook, strike: int, option_type: str) -> float | None:
    try:
        index = quotes.first_index_by_strike[int(strike)]
    except KeyError:
        return None
    prices = quotes.ce_close if option_type == "CE" else quotes.pe_close
    value = prices[index]
    return float(value) if _positive_price(value) else None


def _select_short_leg(
    quotes: QuoteBook,
    direction: str,
    atm_strike: int,
    straddle_premium: float,
    premium_pct: float,
) -> tuple[str, int, float]:
    target = float(straddle_premium) * float(premium_pct)
    if target <= 0:
        raise ValueError("ATM straddle premium must be positive at entry")

    if direction == "call":
        option_type = "CE"
        prices = quotes.ce_close
        candidate_indexes = np.flatnonzero(
            (quotes.strikes >= int(atm_strike)) & (prices > 0)
        )
        strike_order = 1
    elif direction == "put":
        option_type = "PE"
        prices = quotes.pe_close
        candidate_indexes = np.flatnonzero(
            (quotes.strikes <= int(atm_strike)) & (prices > 0)
        )
        strike_order = -1
    else:
        raise ValueError(f"Unsupported direction: {direction!r}")

    if not len(candidate_indexes):
        raise ValueError(f"No valid {option_type} candidates for target premium {target:.2f}")
    selected_index = min(
        candidate_indexes,
        key=lambda index: (
            abs(float(prices[index]) - target),
            strike_order * int(quotes.strikes[index]),
        ),
    )
    return (
        option_type,
        int(quotes.strikes[selected_index]),
        float(prices[selected_index]),
    )


def _precompute_entry_selections(
    day_summary: pd.DataFrame,
    chain_lookup: QuoteLookup,
) -> dict[tuple[pd.Timestamp, str, float], tuple[str, int, float]]:
    """Select every configured CE/PE premium once per timestamp.

    These selections depend only on market data and the three sweep premium
    targets. They are immutable and can be shared by all other parameters.
    """
    selections = {}
    for _, row in day_summary.iterrows():
        timestamp = pd.Timestamp(row["ts"])
        quotes = _quotes_at(chain_lookup, timestamp)
        if quotes is None:
            continue
        for direction in ("call", "put"):
            for premium_pct in STRIKE_PREMIUM_PCTS:
                try:
                    selections[(timestamp, direction, float(premium_pct))] = _select_short_leg(
                        quotes, direction, int(row["future_atm"]),
                        float(row["straddle_future"]), float(premium_pct),
                    )
                except ValueError:
                    # Preserve the existing runtime error/re-entry behavior for
                    # timestamps without a valid contract.
                    continue
    return selections


def _cached_entry_selections(
    market_data: Path,
    day: Any,
    day_summary: pd.DataFrame,
    chain_lookup: QuoteLookup,
    market_data_loader: Any = None,
    partitioned_chain: Path | None = None,
) -> dict[tuple[pd.Timestamp, str, float], tuple[str, int, float]]:
    day_iso = pd.Timestamp(day).date().isoformat()
    summary_path = market_data / "nifty_summary.parquet"
    source_folder = (partitioned_chain / f"trade_date={day_iso}") if partitioned_chain else (market_data / "nifty_chain")
    sources = [summary_path, *sorted(source_folder.glob("*.parquet"))]
    shared_object = getattr(market_data_loader, "shared_object", None)
    build = lambda: _precompute_entry_selections(day_summary, chain_lookup)
    if callable(shared_object) and all(path.is_file() for path in sources):
        return shared_object(
            sources,
            ("r6-entry-selections-v1", str(market_data.resolve()), day_iso,
             tuple(STRIKE_PREMIUM_PCTS), partitioned_chain is not None),
            build,
        )
    return build()


def _open_slot(
    *,
    slot_id: int,
    reentry_index: int,
    timestamp: pd.Timestamp,
    quotes: QuoteBook,
    direction: str,
    atm_strike: int,
    straddle_premium: float,
    premium_pct: float,
    slippage: float,
    selection: tuple[str, int, float] | None = None,
) -> Slot:
    option_type, strike, raw_price = selection or _select_short_leg(
        quotes, direction, atm_strike, straddle_premium, premium_pct,
    )
    return Slot(
        slot_id=slot_id,
        batch_id=f"slot-{slot_id}-entry-{reentry_index}",
        reentry_index=reentry_index,
        entry_ts=timestamp,
        option_type=option_type,
        strike=strike,
        entry_raw=raw_price,
        entry_fill=raw_price * (1 - slippage),
        best_buyback_raw=raw_price,
        trailing_active=False,
    )


def _close_slot_row(
    *,
    run_id: str,
    slot: Slot,
    exit_ts: pd.Timestamp,
    exit_raw: float,
    sequence: int,
    lot_size: int,
    slippage: float,
    fees_per_leg: float,
) -> dict[str, Any]:
    exit_fill = exit_raw * (1 + slippage)
    leg_name = slot.option_type.lower()
    return {
        "schema_version": "1",
        "run_id": run_id,
        "trade_id": f"{slot.batch_id}-{leg_name}-{sequence}",
        "batch_id": slot.batch_id,
        "leg_id": f"{leg_name}:{slot.option_type}:{slot.strike}",
        "strategy": STRATEGY_NAME,
        "symbol": f"NIFTY {slot.option_type} {slot.strike}",
        "side": "SHORT",
        "entry_time": _timestamp(slot.entry_ts),
        "exit_time": _timestamp(exit_ts),
        "quantity": 1,
        "entry_price": slot.entry_fill,
        "exit_price": exit_fill,
        "multiplier": lot_size,
        "fees": fees_per_leg,
    }


def _slot_points(slot: Slot, exit_raw: float, slippage: float) -> float:
    return slot.entry_fill - exit_raw * (1 + slippage)


def _trade_leg_pnl(row: Mapping[str, Any]) -> float:
    return (
        (float(row["entry_price"]) - float(row["exit_price"]))
        * float(row["quantity"])
        * float(row.get("multiplier", 1))
        - float(row.get("fees", 0))
    )


def _snapshot(
    snapshots: dict[pd.Timestamp, dict[str, Any]],
    timestamp: pd.Timestamp,
    realized_points: float,
    open_slots: list[Slot],
    quotes: QuoteBook | None,
    lot_size: int,
    slippage: float,
) -> None:
    unrealized_points = 0.0
    if quotes is not None:
        for slot in open_slots:
            raw = _price_for_leg(quotes, slot.strike, slot.option_type)
            if raw is None:
                raise ValueError(
                    f"Cannot value open {slot.option_type} {slot.strike} at {timestamp}: "
                    "missing valid quote; observed equity would be incomplete"
                )
            unrealized_points += _slot_points(slot, raw, slippage)
    snapshots[pd.Timestamp(timestamp)] = {
        "timestamp": _timestamp(timestamp),
        "realized_pnl": realized_points * lot_size,
        "unrealized_pnl": unrealized_points * lot_size,
    }


def _run_day(
    *,
    run_id: str,
    day_summary: pd.DataFrame,
    chain_lookup: QuoteLookup,
    parameters: Mapping[str, Any],
    starting_slot_id: int,
    starting_trade_sequence: int,
    starting_realized_points: float,
    entry_selections: Mapping[tuple[pd.Timestamp, str, float], tuple[str, int, float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, float]:
    entry_time = _parse_time(parameters["entry_time"])
    square_off_time = _parse_time(parameters["square_off_time"])
    premium_pct = float(parameters["strike_premium_pct_of_straddle"])
    add_decay_pct = float(parameters["add_on_straddle_decay_pct"])
    stop_loss_pct = float(parameters["stop_loss_pct"])
    profit_booking_pct = float(parameters["profit_booking_pct"])
    trailing_activation_pct = float(parameters["trailing_activation_pct"])
    trailing_gap_pct = float(parameters["trailing_gap_pct"])
    r6_reversal_threshold_pct = float(parameters["r6_reversal_threshold_pct"])
    max_lots = int(parameters["max_lots_per_day"])
    max_reentries = int(parameters["max_reentries_per_trade"])
    capital = float(parameters["capital"])
    margin_per_lot = float(parameters["margin_per_lot"])
    lot_size = int(parameters["lot_size"])
    slippage = float(parameters["slippage"])
    fees_per_leg = float(parameters["fees_per_leg"])

    rows: list[dict[str, Any]] = []
    snapshots: dict[pd.Timestamp, dict[str, Any]] = {}
    open_slots: list[Slot] = []
    pending_reentries: list[PendingReentry] = []
    next_slot_id = starting_slot_id
    trade_sequence = starting_trade_sequence
    realized_points = starting_realized_points
    last_entry_straddle: float | None = None
    used_reentries_by_slot: dict[int, int] = {}
    summary_lookup = _summary_lookup(day_summary)

    tradable = day_summary[
        (day_summary["DTE"] == 0) & (day_summary["ts"].dt.time >= entry_time)
    ].copy()
    if tradable.empty:
        return rows, [], next_slot_id, trade_sequence, realized_points

    tradable = tradable[
        tradable["ts"].map(lambda ts: _quotes_at(chain_lookup, pd.Timestamp(ts)) is not None)
    ].copy()
    if tradable.empty:
        return rows, [], next_slot_id, trade_sequence, realized_points

    square_off_candidates = tradable[tradable["ts"].dt.time >= square_off_time]
    final_ts = pd.Timestamp(
        square_off_candidates.iloc[0]["ts"] if not square_off_candidates.empty else tradable.iloc[-1]["ts"]
    )
    first_ts = pd.Timestamp(tradable.iloc[0]["ts"]) - pd.Timedelta(seconds=1)
    _snapshot(snapshots, first_ts, realized_points, open_slots, None, lot_size, slippage)

    for _, row in tradable.iterrows():
        timestamp = pd.Timestamp(row["ts"])
        quotes = _quotes_at(chain_lookup, timestamp)
        if quotes is None:
            continue

        survivors: list[Slot] = []
        for slot in open_slots:
            raw = _price_for_leg(quotes, slot.strike, slot.option_type)
            if raw is None:
                survivors.append(slot)
                continue

            slot.best_buyback_raw = min(slot.best_buyback_raw, raw)
            if raw <= slot.entry_raw * (1 - trailing_activation_pct):
                slot.trailing_active = True

            exit_reason = None
            if raw * (1 + slippage) >= slot.entry_fill * (1 + stop_loss_pct):
                exit_reason = "stop_loss"
            elif raw * (1 + slippage) <= slot.entry_fill * (1 - profit_booking_pct):
                exit_reason = "profit_booking"
            elif slot.trailing_active and raw >= slot.best_buyback_raw * (1 + trailing_gap_pct):
                exit_reason = "trailing_exit"
            elif timestamp >= final_ts:
                exit_reason = "eod_exit"

            if exit_reason is None:
                survivors.append(slot)
                continue

            rows.append(
                _close_slot_row(
                    run_id=run_id,
                    slot=slot,
                    exit_ts=timestamp,
                    exit_raw=raw,
                    sequence=trade_sequence,
                    lot_size=lot_size,
                    slippage=slippage,
                    fees_per_leg=fees_per_leg,
                )
            )
            trade_sequence += 1
            # Dashboard accounting: recognize this closed leg's fee exactly once.
            realized_points += _slot_points(slot, raw, slippage) - fees_per_leg / lot_size
            if exit_reason in {"stop_loss", "profit_booking", "trailing_exit"}:
                used_count = used_reentries_by_slot.get(slot.slot_id, slot.reentry_index)
                if used_count < max_reentries:
                    pending_reentries.append(
                        PendingReentry(
                            slot_id=slot.slot_id,
                            reentry_index=used_count + 1,
                            earliest_ts=timestamp + pd.Timedelta(minutes=1),
                        )
                    )
        open_slots = survivors

        ready_reentries = [item for item in pending_reentries if item.earliest_ts <= timestamp]
        pending_reentries = [item for item in pending_reentries if item.earliest_ts > timestamp]
        for reentry in ready_reentries:
            if timestamp >= final_ts or len(open_slots) >= max_lots:
                continue
            if (len(open_slots) + 1) * margin_per_lot > capital:
                continue
            direction = _direction_at(summary_lookup, timestamp, r6_reversal_threshold_pct)
            if direction is None:
                pending_reentries.append(reentry)
                continue
            try:
                slot = _open_slot(
                    slot_id=reentry.slot_id,
                    reentry_index=reentry.reentry_index,
                    timestamp=timestamp,
                    quotes=quotes,
                    direction=direction,
                    atm_strike=int(row["future_atm"]),
                    straddle_premium=float(row["straddle_future"]),
                    premium_pct=premium_pct,
                    slippage=slippage,
                    selection=entry_selections.get((timestamp, direction, premium_pct)),
                )
            except ValueError:
                pending_reentries.append(reentry)
                continue
            open_slots.append(slot)
            used_reentries_by_slot[reentry.slot_id] = reentry.reentry_index

        current_straddle = float(row["straddle_future"])
        should_open = False
        if last_entry_straddle is None and timestamp < final_ts:
            should_open = True
        elif (
            last_entry_straddle is not None
            and timestamp < final_ts
            and current_straddle <= last_entry_straddle * (1 - add_decay_pct)
        ):
            should_open = True

        if (
            should_open
            and len(open_slots) < max_lots
            and (len(open_slots) + 1) * margin_per_lot <= capital
        ):
            direction = _direction_at(summary_lookup, timestamp, r6_reversal_threshold_pct)
            if direction is not None:
                slot = _open_slot(
                    slot_id=next_slot_id,
                    reentry_index=0,
                    timestamp=timestamp,
                    quotes=quotes,
                    direction=direction,
                    atm_strike=int(row["future_atm"]),
                    straddle_premium=current_straddle,
                    premium_pct=premium_pct,
                    slippage=slippage,
                    selection=entry_selections.get((timestamp, direction, premium_pct)),
                )
                open_slots.append(slot)
                used_reentries_by_slot[next_slot_id] = 0
                next_slot_id += 1
                last_entry_straddle = current_straddle

        _snapshot(snapshots, timestamp, realized_points, open_slots, quotes, lot_size, slippage)

    if open_slots:
        raise ValueError(
            f"{len(open_slots)} position(s) remain open after {final_ts}; "
            "cannot report a completed backtest without valid closing quotes"
        )
    ordered_snapshots = [snapshots[key] for key in sorted(snapshots)]
    return rows, ordered_snapshots, next_slot_id, trade_sequence, realized_points


def _validate_parameters(parameters: Mapping[str, Any]) -> None:
    positive = {
        "strike_premium_pct_of_straddle",
        "add_on_straddle_decay_pct",
        "stop_loss_pct",
        "profit_booking_pct",
        "trailing_activation_pct",
        "trailing_gap_pct",
        "capital",
        "margin_per_lot",
        "lot_size",
    }
    for key in positive:
        if float(parameters[key]) <= 0:
            raise ValueError(f"{key} must be greater than zero")
    if int(parameters["max_lots_per_day"]) < 1:
        raise ValueError("max_lots_per_day must be at least 1")
    if int(parameters["max_reentries_per_trade"]) < 0:
        raise ValueError("max_reentries_per_trade must be non-negative")
    if float(parameters["slippage"]) < 0 or float(parameters["fees_per_leg"]) < 0:
        raise ValueError("slippage and fees_per_leg must be non-negative")


def run_strategy(context: StrategyContext) -> dict[str, Any]:
    run_id = str(context.run_id).strip()
    if not run_id:
        raise ValueError("context.run_id must be a non-empty string")

    config = _require_mapping(context.config, "context.config")
    instrument = _require_mapping(config.get("instrument"), "instrument", allow_missing=True)
    period = _require_mapping(config.get("period"), "period", allow_missing=True)
    execution = _require_mapping(config.get("execution"), "execution", allow_missing=True)
    supplied_parameters = _require_mapping(config.get("parameters"), "parameters", allow_missing=True)

    if instrument and str(instrument.get("symbol", "NIFTY")).upper() != "NIFTY":
        raise ValueError("This strategy requires instrument.symbol='NIFTY'")

    unknown_parameters = set(supplied_parameters) - set(DEFAULTS)
    if unknown_parameters:
        raise KeyError(f"Unsupported strategy parameters: {sorted(unknown_parameters)}")
    parameters = {**DEFAULTS, **dict(supplied_parameters)}
    if "slippage" in execution:
        parameters["slippage"] = float(execution["slippage"])
    if "fees_per_leg" in execution:
        parameters["fees_per_leg"] = float(execution["fees_per_leg"])
    if "lot_size" in execution:
        parameters["lot_size"] = int(execution["lot_size"])
    if str(execution.get("timezone", "Asia/Kolkata")) != "Asia/Kolkata":
        raise ValueError("This strategy requires execution.timezone='Asia/Kolkata'")
    _validate_parameters(parameters)

    market_data = Path(context.market_data)
    market_data_loader = getattr(context, "market_data_loader", None)
    start_date = str(period["start_date"]) if period.get("start_date") else None
    end_date = str(period["end_date"]) if period.get("end_date") else None
    # Dashboard progress is optional for direct Python/Jupyter callers.
    report_progress = getattr(context, "report_progress", None)
    if callable(report_progress):
        report_progress(0, 0, "trading day", "Loading summary")
    summary = _load_summary(market_data, start_date, end_date, market_data_loader)
    partitioned_chain = _partition_chain_cache(market_data, market_data_loader)
    trading_days = [
        (day, frame) for day, frame in summary.groupby("trade_date", sort=True)
        if (frame["DTE"] == 0).any()
    ]
    completed_rows: list[dict[str, Any]] = []
    equity_snapshots: list[dict[str, Any]] = []
    next_slot_id = 1
    trade_sequence = 0
    cumulative_realized_points = 0.0

    for day_index, (day, day_summary) in enumerate(trading_days):
        day_summary = day_summary.sort_values("ts").reset_index(drop=True)
        if callable(report_progress):
            report_progress(day_index, len(trading_days), "trading day", "Running backtest")
        lookup = _load_chain_lookup(
            market_data, day, market_data_loader, partitioned_chain
        )
        entry_selections = _cached_entry_selections(
            market_data, day, day_summary, lookup, market_data_loader, partitioned_chain
        )
        day_rows, day_snapshots, next_slot_id, trade_sequence, cumulative_realized_points = _run_day(
            run_id=run_id,
            day_summary=day_summary,
            chain_lookup=lookup,
            parameters=parameters,
            starting_slot_id=next_slot_id,
            starting_trade_sequence=trade_sequence,
            starting_realized_points=cumulative_realized_points,
            entry_selections=entry_selections,
        )
        completed_rows.extend(day_rows)
        equity_snapshots.extend(day_snapshots)
        if callable(report_progress):
            report_progress(day_index + 1, len(trading_days), "trading day", "Running backtest")

    authoritative_count = len(completed_rows)
    if len({row["trade_id"] for row in completed_rows}) != authoritative_count:
        raise ValueError("Generated completed-leg trade IDs are not unique")

    if equity_snapshots:
        equity_snapshots = sorted(equity_snapshots, key=lambda item: pd.Timestamp(item["timestamp"]))
        reconstructed = sum(_trade_leg_pnl(row) for row in completed_rows)
        final = equity_snapshots[-1]
        if abs(final["realized_pnl"] - reconstructed) > 0.01 or abs(final["unrealized_pnl"]) > 0.01:
            raise ValueError(
                "Observed final equity does not reconcile with closed legs: "
                f"realized={final['realized_pnl']}, trades_after_fees={reconstructed}, "
                f"unrealized={final['unrealized_pnl']}"
            )

    return {
        "completed_trades": completed_rows,
        "completed_trade_count": authoritative_count,
        "trade_mapper": None,
        "equity_snapshots": equity_snapshots or None,
        "metadata": {
            "strategy_name": STRATEGY_NAME,
            "engine": "self-contained pandas/DuckDB 0DTE current-week option-chain engine",
            "required_dataset": "Current Week",
            "sweep_combinations": len(SWEEP_PARAMETER_SETS),
            "direction_rule": "D2 latest FUT close vs 3-minute prior close, with R6 5-minute opposite reversal",
            "strike_selection": f"short premium closest to {float(parameters['strike_premium_pct_of_straddle']):.0%} of current straddle premium",
            "capital": float(parameters["capital"]),
            "margin_per_lot": float(parameters["margin_per_lot"]),
            "max_lots_per_day": int(parameters["max_lots_per_day"]),
            "max_reentries_per_trade": int(parameters["max_reentries_per_trade"]),
            "stop_loss_pct": float(parameters["stop_loss_pct"]),
            "profit_booking_pct": float(parameters["profit_booking_pct"]),
            "trailing_activation_pct": float(parameters["trailing_activation_pct"]),
            "trailing_gap_pct": float(parameters["trailing_gap_pct"]),
            "r6_reversal_threshold_pct": float(parameters["r6_reversal_threshold_pct"]),
            "slippage": float(parameters["slippage"]),
            "fees_per_leg": float(parameters["fees_per_leg"]),
        },
    }
