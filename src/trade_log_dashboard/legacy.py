"""Compatibility for the standalone protected-straddle backtester.

The adapter changes only data access and result recording. Trading decisions still
come from the uploaded module's ``ProtectedStraddleBacktester`` implementation.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import MethodType
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd


REQUIRED_PARTS = ("StrategyConfig", "DataLoader", "ProtectedStraddleBacktester")
MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")


def supports_protected_straddle(module) -> bool:
    """Return true only for the known class-based standalone strategy shape."""
    return all(callable(getattr(module, name, None)) for name in REQUIRED_PARTS)


class ProjectDatasetLoader:
    """Expose the project's Parquet layout in the two frames the strategy expects."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.root = Path(cfg.data_dir)
        self.summary_path = self.root / "nifty_summary.parquet"
        self.chain_glob = self.root / "nifty_chain" / "*.parquet"
        if not self.summary_path.is_file() or not any(self.chain_glob.parent.glob("*.parquet")):
            raise ValueError(
                "This standalone strategy needs nifty_summary.parquet and a nifty_chain folder "
                "from one of the project datasets."
            )
        self._spot = None
        self._chain = None
        self._daily = None

    @staticmethod
    def _sql_path(path: Path) -> str:
        return str(path.resolve()).replace("\\", "/")

    def _load_summary(self):
        if self._spot is not None:
            return
        start = pd.Timestamp(self.cfg.start_date).date()
        end = pd.Timestamp(self.cfg.end_date).date()
        with duckdb.connect() as connection:
            frame = connection.execute(
                """
                WITH parsed AS (
                    SELECT TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS timestamp,
                           CAST(future_close AS DOUBLE) AS close,
                           CAST(DTE AS BIGINT) AS dte
                    FROM READ_PARQUET(?)
                )
                SELECT timestamp, close, dte
                FROM parsed
                WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
                  AND timestamp IS NOT NULL AND close IS NOT NULL AND dte IS NOT NULL
                ORDER BY timestamp
                """,
                [self._sql_path(self.summary_path), start, end],
            ).df()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        self._spot = frame[["timestamp", "close"]].copy()
        if frame.empty:
            self._daily = frame[["timestamp", "dte"]].copy()
        else:
            frame["trade_date"] = frame["timestamp"].dt.date
            self._daily = frame.groupby("trade_date", as_index=False).first()[
                ["trade_date", "timestamp", "dte"]
            ]

    def load_spot(self):
        self._load_summary()
        return self._spot.copy()

    def load_chain(self):
        if self._chain is not None:
            return self._chain.copy()
        self._load_summary()
        if self._daily.empty:
            self._chain = pd.DataFrame(
                columns=["timestamp", "strike", "option_type", "close", "expiry"]
            )
            return self._chain.copy()

        daily = self._daily.copy()
        daily["is_cycle"] = daily["dte"].diff().fillna(999) > int(self.cfg.dte_jump_reset)
        cycle_dates = daily.loc[daily["is_cycle"], ["trade_date"]]
        start = pd.Timestamp(self.cfg.start_date).date()
        end = pd.Timestamp(self.cfg.end_date).date()
        with duckdb.connect() as connection:
            connection.register("cycle_dates", cycle_dates)
            frame = connection.execute(
                """
                WITH parsed AS (
                    SELECT TRY_STRPTIME(datetime, '%d/%m/%Y %H:%M:%S') AS timestamp,
                           CAST(strike AS DOUBLE) AS strike,
                           CAST(ce_close AS DOUBLE) AS ce_close,
                           CAST(pe_close AS DOUBLE) AS pe_close,
                           CAST(DTE AS BIGINT) AS dte
                    FROM READ_PARQUET(?)
                ), selected AS (
                    SELECT p.* FROM parsed p
                    JOIN cycle_dates c ON CAST(p.timestamp AS DATE) = c.trade_date
                    WHERE CAST(p.timestamp AS DATE) BETWEEN ? AND ?
                )
                SELECT timestamp, strike, 'CE' AS option_type, ce_close AS close, dte
                FROM selected WHERE ce_close IS NOT NULL AND ce_close > 0
                UNION ALL
                SELECT timestamp, strike, 'PE' AS option_type, pe_close AS close, dte
                FROM selected WHERE pe_close IS NOT NULL AND pe_close > 0
                ORDER BY timestamp, strike, option_type
                """,
                [self._sql_path(self.chain_glob), start, end],
            ).df()

        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        frame["expiry"] = frame["timestamp"].dt.normalize() + pd.to_timedelta(frame.pop("dte"), unit="D")

        # The engine needs one row on non-entry days to observe the daily DTE fall
        # and the next weekly reset. Full option prices are loaded only on cycle days.
        non_cycles = daily.loc[~daily["is_cycle"], ["timestamp", "dte"]].copy()
        if not non_cycles.empty:
            placeholders = pd.DataFrame({
                "timestamp": non_cycles["timestamp"],
                "strike": 0.0,
                "option_type": "CE",
                "close": 0.01,
                "expiry": non_cycles["timestamp"].dt.normalize()
                    + pd.to_timedelta(non_cycles["dte"], unit="D"),
            })
            frame = pd.concat([frame, placeholders], ignore_index=True)
        self._chain = frame.sort_values(
            ["timestamp", "expiry", "strike", "option_type"]
        ).reset_index(drop=True)
        return self._chain.copy()


def _aware(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(MARKET_TIMEZONE)
    return timestamp.to_pydatetime()


def _install_equity_recorder(engine, lot_size):
    """Record the engine's own MTM checks without changing its exit decisions."""
    original_lifecycle = engine._run_leg_lifecycle
    snapshots = {}
    state = {"realized": 0.0}

    def put(timestamp, realized, unrealized):
        snapshots[_aware(timestamp)] = {
            "timestamp": _aware(timestamp).isoformat(),
            "realized_pnl": float(realized),
            "unrealized_pnl": float(unrealized),
        }

    def tracked(self, chain_day, timestamps, entry_ts, legs, net_credit, stop_loss_mult):
        if not snapshots:
            put(pd.Timestamp(entry_ts) - pd.Timedelta(seconds=1), 0.0, 0.0)
        put(entry_ts, state["realized"], 0.0)
        original_mtm = self._mark_to_market

        def capture(chain, timestamp, open_legs):
            pnl = original_mtm(chain, timestamp, open_legs)
            if pnl is not None:
                put(timestamp, state["realized"], pnl * lot_size)
            return pnl

        self._mark_to_market = capture
        try:
            exit_ts, reason, closed_legs = original_lifecycle(
                chain_day, timestamps, entry_ts, legs, net_credit, stop_loss_mult
            )
        finally:
            self._mark_to_market = original_mtm
        points = sum(leg.pnl_points() for leg in closed_legs)
        state["realized"] += points * lot_size
        put(exit_ts, state["realized"], 0.0)
        return exit_ts, reason, closed_legs

    engine._run_leg_lifecycle = MethodType(tracked, engine)
    return snapshots


def _completed_legs(engine, cfg, run_id):
    rows = []
    for trade_number, trade in enumerate(engine.trades, 1):
        batch = f"protected-straddle-{trade_number}"
        for leg_number, leg in enumerate(trade.legs, 1):
            rows.append({
                "run_id": run_id,
                "trade_id": f"{batch}:{leg_number}",
                "batch_id": batch,
                "leg_id": f"{leg.option_type}:{leg.strike:g}:{leg_number}",
                "strategy": "Protected Short Straddle",
                "symbol": f"NIFTY {leg.option_type} {leg.strike:g}",
                "side": "SHORT" if str(leg.side).lower() == "short" else "LONG",
                "entry_time": _aware(trade.entry_time),
                "exit_time": _aware(trade.exit_time),
                "quantity": 1,
                "entry_price": float(leg.entry_price),
                "exit_price": float(leg.exit_price),
                "multiplier": int(cfg.lot_size),
                "fees": 0,
            })
    return rows


def run_protected_straddle(module, context):
    """Execute the standalone engine against the dashboard-selected dataset."""
    period = context.config.get("period") or {}
    if not period.get("start_date") or not period.get("end_date"):
        raise ValueError("The selected dataset does not provide an automatic date range.")

    cfg = module.StrategyConfig()
    for name, value in (context.config.get("parameters") or {}).items():
        if name in {"data_dir", "start_date", "end_date"}:
            raise ValueError(f"Sweep parameter {name!r} is controlled by the selected dataset.")
        if not hasattr(cfg, name):
            raise ValueError(f"Sweep parameter {name!r} is not defined by StrategyConfig.")
        setattr(cfg, name, value)
    cfg.data_dir = str(context.market_data)
    cfg.start_date = period["start_date"]
    # The uploaded engine compares to midnight with <=. Use the last instant of
    # the detected final day so that day is included without changing engine code.
    cfg.end_date = (pd.Timestamp(period["end_date"]) + timedelta(days=1)
                    - pd.Timedelta(nanoseconds=1)).isoformat()
    loader = ProjectDatasetLoader(cfg)
    engine = module.ProtectedStraddleBacktester(cfg, loader)
    snapshots = _install_equity_recorder(engine, int(cfg.lot_size))
    engine.run()
    completed = _completed_legs(engine, cfg, context.run_id)
    ordered_snapshots = [snapshots[key] for key in sorted(snapshots)]
    return {
        "completed_trades": completed,
        "completed_trade_count": len(completed),
        "equity_snapshots": ordered_snapshots or None,
        "adapter": "standalone-protected-straddle",
    }
