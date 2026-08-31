"""Example only: replace FakeTrade fields with fields from the real engine."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from trade_log_exporter import export_trade_log


IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class FakeTrade:
    identifier: str
    symbol: str
    direction: str
    opened_at: datetime
    closed_at: datetime
    size: int
    open_price: float
    close_price: float
    commission: float


def map_engine_trade(trade: FakeTrade, index: int) -> dict[str, object]:
    """This is the only framework-specific part of the integration."""
    return {
        "run_id": "example-run-001",
        "trade_id": trade.identifier,
        "batch_id": "",
        "leg_id": "",
        "strategy": "Example strategy",
        "symbol": trade.symbol,
        "side": trade.direction,
        "entry_time": trade.opened_at,
        "exit_time": trade.closed_at,
        "quantity": trade.size,
        "entry_price": trade.open_price,
        "exit_price": trade.close_price,
        "multiplier": 1,
        "fees": trade.commission,
    }


def run_example_backtest() -> list[FakeTrade]:
    """Stand-in for the engine's real completed-trades collection."""
    return [
        FakeTrade(
            identifier="T001",
            symbol="NIFTY",
            direction="LONG",
            opened_at=datetime(2026, 8, 1, 9, 20, tzinfo=IST),
            closed_at=datetime(2026, 8, 1, 10, 5, tzinfo=IST),
            size=50,
            open_price=24500,
            close_price=24580,
            commission=40,
        )
    ]


if __name__ == "__main__":
    completed_trades = run_example_backtest()
    receipt = export_trade_log(
        completed_trades,
        "output/trades.csv",
        mapper=map_engine_trade,
        expected_count=len(completed_trades),
    )
    print(f"Completed trades: {len(completed_trades)}")
    print(f"Exported rows: {receipt.row_count}")
    print(f"Trade log: {receipt.output_path}")
    print("Validation: passed")
