"""Dashboard strategy: finalized E1+R1 four-slot NIFTY straddle.

This module is intentionally import-safe.  The dashboard supplies the selected
market-data path through ``context.market_data`` and the run metadata through
``context``; no repository-local data path is used here.

Finalized rules preserved from the research implementation:
* current-week DTE=0 data, first entry at or after 09:18:59;
* first entry immediately at the first eligible bar, then fresh entries when
  the ATM straddle premium falls 15% from the last fresh-entry reference;
* CE and PE shorts are selected independently near 20% of the current ATM
  straddle premium, on the ATM-or-outward side of the ATM strike;
* four maximum simultaneous active slots and a one-minute global entry gap;
* combined-premium SL at +15% and combined straddle profit booking after 95%
  decay (combined premium <= 5% of the entry premium);
* at most one re-entry per slot, at least one minute after that slot's SL;
* 0.5% slippage per leg at entry and exit; remaining positions close EOD.
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
RUN_MODE = "single"
SWEEP_PARAMETER_SETS = ()

LOT_SIZE = 65
SLIPPAGE = 0.005
ENTRY_START = "09:18:59"
ENTRY_FALL_PCT = 15.0
SELECTED_PREMIUM_PCT = 20.0
COMBINED_SL_PCT = 15.0
PROFIT_CAPTURE_PCT = 95.0
MAX_ACTIVE_SLOTS = 4
MAX_SLOT_IDS = 6  # Preserve the tested engine's total fresh-slot guard.
ENTRY_GAP_SECONDS = 60
REENTRY_WAIT_SECONDS = 60
MAX_REENTRIES_PER_SLOT = 1
TIMEZONE = ZoneInfo("Asia/Kolkata")


# --- Progress/ETA tracking added here; no strategy state is read or changed. ---
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
    """One update per completed trading day, using tqdm when it is available."""

    def __init__(self, total: int, reporter=None):
        self.total = total
        self.reporter = reporter
        self.completed = 0
        self.started = perf_counter()
        self.last_fallback_refresh = 0.0
        if self.reporter is not None:
            self.bar = None
        else:
            try:
                from tqdm.auto import tqdm
            except ImportError:
                self.bar = None
            else:
                self.bar = tqdm(
                    total=total,
                    desc="NIFTY Backtest",
                    unit=" trading day",
                    dynamic_ncols=True,
                    mininterval=1.0,
                    smoothing=0.1,
                    bar_format=(
                        "{desc}: {percentage:5.1f}% | Completed: {n_fmt}/{total_fmt} "
                        "trading days | Remaining: {remaining} | Elapsed: {elapsed} | "
                        "Speed: {rate_fmt} | {postfix}"
                    ),
                )
        if self.reporter is not None:
            self.reporter(0, total, "trading day")

    def update(self) -> None:
        self.completed += 1
        if self.reporter is not None:
            self.reporter(self.completed, self.total, "trading day")
            return
        elapsed = perf_counter() - self.started
        speed = self.completed / elapsed if elapsed > 0 else 0.0
        remaining = (self.total - self.completed) / speed if speed > 0 else 0.0
        finish = datetime.now().astimezone() + timedelta(seconds=remaining)
        finish_text = finish.strftime("%I:%M:%S %p").lstrip("0")
        if self.bar is not None:
            self.bar.set_postfix_str(f"Estimated finish: {finish_text}", refresh=False)
            self.bar.update(1)
            return

        # Throttle the dependency-free fallback to avoid slowing notebook runs.
        now = perf_counter()
        if self.completed != self.total and now - self.last_fallback_refresh < 1.0:
            return
        self.last_fallback_refresh = now
        percentage = self.completed / self.total * 100 if self.total else 100.0
        message = (
            f"NIFTY Backtest: {percentage:5.1f}% | "
            f"Completed: {self.completed}/{self.total} trading days | "
            f"Remaining: {self.total - self.completed} | "
            f"Elapsed: {_format_duration(elapsed)} | "
            f"Speed: {speed:.2f} days/sec | "
            f"ETA Remaining: {_format_duration(remaining)} | "
            f"Estimated Finish: {finish_text}"
        )
        print(message, end="\n" if self.completed == self.total else "\r", flush=True)

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close()
# --- End progress/ETA tracking. ---


def _parse_datetime(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, dayfirst=True)
    if pd.isna(parsed):
        raise ValueError(f"Invalid market-data timestamp: {value!r}")
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(TIMEZONE)
    return timestamp.tz_convert(TIMEZONE)


def _resolve_data_paths(selected: Path) -> tuple[Path, Path]:
    """Resolve a selected summary file/dataset directory without hard-coded paths."""
    selected = Path(selected)
    if selected.is_file():
        if "summary" in selected.stem.lower():
            summary = selected
            chain_candidates = (selected.parent / "nifty_chain", selected.parent / "chain")
        else:
            summary_candidates = (
                selected.parent / "nifty_summary.parquet",
                selected.parent / "summary.parquet",
            )
            summary = next((p for p in summary_candidates if p.exists()), None)
            chain_candidates = (selected, selected.parent / "nifty_chain", selected.parent / "chain")
            if summary is None:
                raise ValueError(
                    "context.market_data must identify a summary parquet file or a dataset "
                    "directory containing summary and chain data"
                )
        chain = next((p for p in chain_candidates if p.exists()), None)
    elif selected.is_dir():
        if selected.name.lower() in {"nifty_chain", "chain"}:
            summary_candidates = (
                selected.parent / "nifty_summary.parquet",
                selected.parent / "summary.parquet",
                selected.parent / "nifty_summary",
            )
            summary = next((p for p in summary_candidates if p.exists()), None)
            chain = selected
        else:
            summary_candidates = (
                selected / "nifty_summary.parquet",
                selected / "summary.parquet",
                selected / "nifty_summary",
            )
            summary = next((p for p in summary_candidates if p.exists()), None)
            chain_candidates = (selected / "nifty_chain", selected / "chain")
            chain = next((p for p in chain_candidates if p.exists()), None)
    else:
        raise FileNotFoundError(f"Selected market data does not exist: {selected}")

    if summary is None or chain is None:
        raise ValueError(
            f"Selected market data must provide both summary and chain data; "
            f"resolved summary={summary!r}, chain={chain!r}"
        )
    return Path(summary), Path(chain)


def _parse_datetimes(values: pd.Series) -> pd.Series:
    """Batch the dataset's timestamp format; retain scalar semantics otherwise.

    Do not drop non-expiry rows first: the original loader also validates their
    timestamps. Unusual formats and invalid values still use the original parser.
    """
    if values.empty or not (pd.api.types.is_object_dtype(values.dtype)
            or isinstance(values.dtype, pd.StringDtype)):
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
    fallback = parsed.isna()
    if fallback.any():
        return values.map(_parse_datetime)
    return parsed


def _add_time_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["dt"] = _parse_datetimes(out["datetime"])
    out["date"] = out["dt"].dt.date
    out["time"] = out["dt"].dt.strftime("%H:%M:%S")
    return out


def _load_market_data(selected: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_path, chain_path = _resolve_data_paths(selected)
    summary_columns = [
        "datetime", "DTE", "future_close", "future_atm", "straddle_future",
        "synth_atm", "straddle_synth",
    ]
    chain_columns = ["datetime", "strike", "ce_close", "pe_close", "DTE"]
    summary = pd.read_parquet(summary_path, columns=summary_columns)
    chain = pd.read_parquet(chain_path, columns=chain_columns)

    summary = _add_time_columns(summary)
    summary = summary[summary["DTE"].eq(0)].copy()
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
    chain["strike"] = chain["strike"].astype(int)
    wanted = set(summary["dt"])
    chain = chain[chain["dt"].isin(wanted)].copy()
    if chain.duplicated(["dt", "strike"]).any():
        # The source audit established that duplicate keys only differ by
        # null-vs-non-null values, so max coalesces the usable quote.
        chain = chain.groupby(["dt", "date", "time", "strike"], as_index=False)[
            ["ce_close", "pe_close"]
        ].max()
    if chain.empty:
        raise ValueError("The selected dataset has no matching DTE=0 chain rows")
    return summary, chain


def _build_days(summary: pd.DataFrame, chain: pd.DataFrame, progress_callback=None) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    start_seconds = _clock_seconds(ENTRY_START)
    grouped = summary.groupby("date", sort=True)
    total_dates = grouped.ngroups
    for completed_dates, (date_value, summary_day) in enumerate(grouped, 1):
        summary_day = summary_day.sort_values("dt").reset_index(drop=True)
        chain_day = chain[chain["date"].eq(date_value)]
        times = summary_day["time"].tolist()
        timestamps = summary_day["dt"].tolist()
        seconds = np.array([_clock_seconds(t) for t in times], dtype=int)
        start_i = next((i for i, value in enumerate(seconds) if value >= start_seconds), None)
        if start_i is None:
            if progress_callback is not None:
                progress_callback(completed_dates, total_dates)
            continue
        strikes = np.sort(chain_day["strike"].unique().astype(int))
        ce = chain_day.pivot_table(
            index="time", columns="strike", values="ce_close", aggfunc="max"
        ).reindex(index=times, columns=strikes).to_numpy(float)
        pe = chain_day.pivot_table(
            index="time", columns="strike", values="pe_close", aggfunc="max"
        ).reindex(index=times, columns=strikes).to_numpy(float)
        if not len(strikes):
            if progress_callback is not None:
                progress_callback(completed_dates, total_dates)
            continue

        atm = summary_day["entry_atm"].to_numpy(int)
        straddle = summary_day["entry_straddle"].to_numpy(float)
        ce_strike = np.full(len(times), -1, dtype=int)
        pe_strike = np.full(len(times), -1, dtype=int)
        ce_px = np.full(len(times), np.nan, dtype=float)
        pe_px = np.full(len(times), np.nan, dtype=float)
        for i in range(len(times)):
            target = straddle[i] * SELECTED_PREMIUM_PCT / 100.0
            ce_mask = (strikes >= atm[i]) & np.isfinite(ce[i]) & (ce[i] > 0)
            pe_mask = (strikes <= atm[i]) & np.isfinite(pe[i]) & (pe[i] > 0)
            for mask, prices, out_strikes, out_prices in (
                (ce_mask, ce[i], ce_strike, ce_px),
                (pe_mask, pe[i], pe_strike, pe_px),
            ):
                indexes = np.where(mask)[0]
                if len(indexes):
                    score = np.abs(prices[indexes] - target) + np.abs(strikes[indexes] - atm[i]) * 1e-9
                    selected = int(indexes[int(np.argmin(score))])
                    out_strikes[i] = int(strikes[selected])
                    out_prices[i] = float(prices[selected])

        days.append({
            "date": date_value,
            "timestamps": timestamps,
            "times": times,
            "seconds": seconds,
            "start_i": start_i,
            "atm": atm,
            "straddle": straddle,
            "strikes": strikes,
            "ce": ce,
            "pe": pe,
            "ce_ff": pd.DataFrame(ce).ffill().to_numpy(),
            "pe_ff": pd.DataFrame(pe).ffill().to_numpy(),
            "ce_strike": ce_strike,
            "pe_strike": pe_strike,
            "ce_px": ce_px,
            "pe_px": pe_px,
        })
        if progress_callback is not None:
            progress_callback(completed_dates, total_dates)
    if not days:
        raise ValueError("No tradable DTE=0 expiry days were found after data preparation")
    return days


def _clock_seconds(value: str) -> int:
    hour, minute, second = (int(part) for part in str(value).split(":")[:3])
    return hour * 3600 + minute * 60 + second


def _leg_quote(day: dict[str, Any], side: str, column: int, index: int) -> float:
    value = (day["ce"] if side == "CE" else day["pe"])[index, column]
    return float(value) if np.isfinite(value) and value > 0 else float("nan")


def _select_legs(day: dict[str, Any], index: int) -> list[dict[str, Any]] | None:
    ce_strike, pe_strike = int(day["ce_strike"][index]), int(day["pe_strike"][index])
    if ce_strike < 0 or pe_strike < 0:
        return None
    legs = []
    for side, strike in (("CE", ce_strike), ("PE", pe_strike)):
        column = int(np.searchsorted(day["strikes"], strike))
        if column >= len(day["strikes"]) or day["strikes"][column] != strike:
            return None
        quote = _leg_quote(day, side, column, index)
        if not np.isfinite(quote):
            return None
        legs.append({"side": side, "strike": strike, "column": column, "entry_quote": quote})
    return legs


def _prepare_path(day: dict[str, Any], legs: list[dict[str, Any]], entry_i: int) -> bool:
    entry_sum = 0.0
    for leg in legs:
        quote = _leg_quote(day, leg["side"], leg["column"], entry_i)
        if not np.isfinite(quote):
            return False
        leg["entry_quote"] = quote
        leg["entry_fill"] = quote * (1.0 - SLIPPAGE)
        leg["marks"] = (day["ce_ff"] if leg["side"] == "CE" else day["pe_ff"])[
            :, leg["column"]
        ]
        leg["pnl_path"] = (
            leg["entry_fill"] - leg["marks"] * (1.0 + SLIPPAGE)
        ) * LOT_SIZE
        entry_sum += quote
    legs[0]["entry_sum"] = entry_sum
    legs[1]["entry_sum"] = entry_sum
    return True


def _fill_price(quote: float, is_entry: bool) -> float:
    return quote * (1.0 - SLIPPAGE if is_entry else 1.0 + SLIPPAGE)


def _trade_rows(day: dict[str, Any], slot: dict[str, Any], exit_i: int, reason: str,
                run_id: str) -> list[dict[str, Any]]:
    batch = f"{day['date'].isoformat()}-slot{slot['slot_id']}-cycle{slot['cycle']}"
    rows = []
    for leg in slot["legs"]:
        mark = float(leg["marks"][exit_i])
        if not np.isfinite(mark) or mark <= 0:
            raise ValueError("Open leg has no usable exit quote at the closing timestamp")
        rows.append({
            "run_id": run_id,
            "trade_id": f"{batch}-{leg['side']}",
            "batch_id": batch,
            "leg_id": leg["side"],
            "strategy": "E1-R1-four-slot-combined-premium",
            "symbol": f"NIFTY_DTE0_{leg['side']}_{leg['strike']}",
            "side": "SHORT",
            "entry_time": day["timestamps"][slot["entry_i"]].isoformat(),
            "exit_time": day["timestamps"][exit_i].isoformat(),
            "quantity": LOT_SIZE,
            "entry_price": _fill_price(float(leg["entry_quote"]), True),
            "exit_price": _fill_price(mark, False),
            "multiplier": 1,
            "fees": 0.0,
        })
    return rows


def _run_day(day: dict[str, Any], run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    slots: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    realized = 0.0
    last_reference: float | None = None
    last_open_seconds = -10**12
    last_sl_seconds = -10**12
    zero_time = day["timestamps"][0] - timedelta(seconds=1)
    snapshots.append({"timestamp": zero_time.isoformat(), "realized_pnl": 0.0, "unrealized_pnl": 0.0})

    def active_count() -> int:
        return sum(slot["status"] == "active" for slot in slots)

    def mark_unrealized(index: int) -> float:
        return float(sum(
            sum(float(leg["pnl_path"][index]) for leg in slot["legs"])
            for slot in slots if slot["status"] == "active"
        ))

    def close_slot(slot: dict[str, Any], index: int, reason: str) -> None:
        nonlocal realized, last_sl_seconds
        realized += sum(float(leg["pnl_path"][index]) for leg in slot["legs"])
        completed.extend(_trade_rows(day, slot, index, reason, run_id))
        slot["status"] = "waiting" if reason == "COMBINED_SL" and slot["cycle"] == 1 else "done"
        if reason == "COMBINED_SL":
            last_sl_seconds = day["seconds"][index]
            slot["sl_i"] = index

    def open_slot(index: int, cycle: int, slot: dict[str, Any] | None = None) -> bool:
        nonlocal last_open_seconds, last_reference
        legs = _select_legs(day, index)
        if legs is None or not _prepare_path(day, legs, index):
            return False
        if slot is None:
            slot = {"slot_id": len(slots) + 1}
            slots.append(slot)
        slot.update({"status": "active", "cycle": cycle, "entry_i": index, "legs": legs})
        last_open_seconds = day["seconds"][index]
        if cycle == 1:
            last_reference = float(day["straddle"][index])
        return True

    for index in range(len(day["times"])):
        seconds = day["seconds"][index]
        allowed = seconds - last_open_seconds >= ENTRY_GAP_SECONDS
        fresh = last_reference is None or float(day["straddle"][index]) <= last_reference * (1.0 - ENTRY_FALL_PCT / 100.0)

        if index >= day["start_i"] and fresh and len(slots) < MAX_SLOT_IDS:
            if allowed and active_count() < MAX_ACTIVE_SLOTS:
                if open_slot(index, 1):
                    allowed = False

        for slot in slots:
            if slot["status"] != "waiting":
                continue
            if slot["cycle"] >= 1 + MAX_REENTRIES_PER_SLOT:
                slot["status"] = "done"
                continue
            if seconds - day["seconds"][slot["sl_i"]] < REENTRY_WAIT_SECONDS:
                continue
            if allowed and active_count() < MAX_ACTIVE_SLOTS:
                if open_slot(index, slot["cycle"] + 1, slot):
                    allowed = False

        for slot in list(slots):
            if slot["status"] != "active":
                continue
            entry_sum = float(slot["legs"][0]["entry_sum"])
            combined_mark = sum(float(leg["marks"][index]) for leg in slot["legs"])
            if not np.isfinite(combined_mark):
                continue
            sl_hit = combined_mark >= entry_sum * (1.0 + COMBINED_SL_PCT / 100.0)
            pb_hit = combined_mark <= entry_sum * (1.0 - PROFIT_CAPTURE_PCT / 100.0)
            if sl_hit:
                close_slot(slot, index, "COMBINED_SL")
            elif pb_hit:
                close_slot(slot, index, "STRADDLE_PROFIT")

        if index == len(day["times"]) - 1:
            for slot in slots:
                if slot["status"] == "active":
                    close_slot(slot, index, "EOD")
        snapshots.append({
            "timestamp": day["timestamps"][index].isoformat(),
            "realized_pnl": float(realized),
            "unrealized_pnl": mark_unrealized(index),
        })

    return completed, snapshots


def run_strategy(context):
    """Run the finalized strategy using only the dashboard-selected dataset."""
    parameters = dict(context.config.get("parameters", {}))
    if parameters:
        raise ValueError(
            "This finalized strategy is fixed; unsupported strategy parameters: "
            f"{sorted(parameters)}"
        )
    reporter = getattr(context, "report_progress", None)
    summary, chain = _load_market_data(Path(context.market_data))
    prepare_progress = (
        (lambda completed, total: reporter(
            completed, total, "trading day", "Preparing trading days"
        ))
        if reporter is not None else None
    )
    days = _build_days(summary, chain, progress_callback=prepare_progress)
    completed: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    cumulative_realized = 0.0
    # Trading days are the stable outer work unit. Inner timestamp, strike, and
    # slot loops vary per day and would make ETA noisy while adding overhead.
    backtest_progress = (
        (lambda completed, total, unit: reporter(
            completed, total, unit, "Running backtest"
        ))
        if reporter is not None else None
    )
    progress = _DayProgress(len(days), backtest_progress)
    try:
        for day in days:
            day_trades, day_snapshots = _run_day(day, str(context.run_id))
            completed.extend(day_trades)
            for snapshot in day_snapshots:
                snapshot["realized_pnl"] += cumulative_realized
            if day_snapshots:
                cumulative_realized = day_snapshots[-1]["realized_pnl"]
            snapshots.extend(day_snapshots)
            progress.update()
    finally:
        progress.close()

    snapshots.sort(key=lambda row: row["timestamp"])
    deduped = []
    seen = set()
    for snapshot in snapshots:
        if snapshot["timestamp"] not in seen:
            deduped.append(snapshot)
            seen.add(snapshot["timestamp"])
    if any(a["timestamp"] >= b["timestamp"] for a, b in zip(deduped, deduped[1:])):
        raise RuntimeError("Equity snapshot timestamps are not strictly increasing")

    return {
        "completed_trades": completed,
        "completed_trade_count": len(completed),
        "equity_snapshots": deduped,
        "metadata": {
            "strategy_name": "E1-R1 four-slot combined-premium straddle",
            "engine": "dashboard-compatible deterministic bar engine",
            "data_rule": "context.market_data, current-week DTE=0",
            "entry_rule": "first eligible entry, then 15% fall in ATM straddle premium",
            "strike_rule": "closest valid CE/PE premium to 20% of ATM straddle premium",
            "exit_rule": "combined SL +15%; combined premium decay 95%; EOD close",
            "reentry_rule": "one re-entry per slot after one minute from combined SL",
            "slippage": "0.5% per leg at entry and exit",
            "drawdown_note": "equity snapshots are quote-marked observations; dashboard calculates analytics",
        },
    }
