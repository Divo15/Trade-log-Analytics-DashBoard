"""Versioned schema and validation for raw completed-trade records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


SCHEMA_VERSION = "1"

CSV_COLUMNS = (
    "schema_version",
    "run_id",
    "trade_id",
    "batch_id",
    "leg_id",
    "strategy",
    "symbol",
    "side",
    "entry_time",
    "exit_time",
    "quantity",
    "entry_price",
    "exit_price",
    "multiplier",
    "fees",
)

REQUIRED_INPUT_FIELDS = {
    "run_id",
    "trade_id",
    "strategy",
    "symbol",
    "side",
    "entry_time",
    "exit_time",
    "quantity",
    "entry_price",
    "exit_price",
}

FORBIDDEN_RESULT_FIELDS = {
    "pnl",
    "net_pnl",
    "gross_pnl",
    "return",
    "drawdown",
}


class SchemaError(ValueError):
    """Raised when a trade record violates the versioned contract."""


def _required_text(name: str, value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise SchemaError(f"{name} is required")
    return text


def _optional_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _positive_decimal(name: str, value: Any, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise SchemaError(f"{name} must be numeric")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise SchemaError(f"{name} must be numeric") from exc
    if not number.is_finite():
        raise SchemaError(f"{name} must be finite")
    minimum_ok = number >= 0 if allow_zero else number > 0
    if not minimum_ok:
        comparison = "zero or greater" if allow_zero else "greater than zero"
        raise SchemaError(f"{name} must be {comparison}")
    return number


def _timestamp(name: str, value: Any, assume_timezone: tzinfo | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise SchemaError(f"{name} must be an ISO 8601 timestamp") from exc
    else:
        raise SchemaError(f"{name} must be a datetime or ISO 8601 string")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if assume_timezone is None:
            raise SchemaError(
                f"{name} has no timezone; pass assume_timezone explicitly or export an offset"
            )
        parsed = parsed.replace(tzinfo=assume_timezone)
    return parsed


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


@dataclass(frozen=True)
class TradeRecord:
    """One actually completed trade or closed strategy leg."""

    schema_version: str
    run_id: str
    trade_id: str
    batch_id: str
    leg_id: str
    strategy: str
    symbol: str
    side: str
    entry_time: datetime
    exit_time: datetime
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    multiplier: Decimal
    fees: Decimal

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        assume_timezone: tzinfo | None = None,
    ) -> "TradeRecord":
        supplied = set(value)
        forbidden = sorted(supplied & FORBIDDEN_RESULT_FIELDS)
        if forbidden:
            raise SchemaError(
                "Derived result fields are forbidden in the raw trade log: " + ", ".join(forbidden)
            )

        unknown = sorted(supplied - set(CSV_COLUMNS))
        if unknown:
            raise SchemaError("Unknown trade-log fields: " + ", ".join(unknown))

        missing = sorted(REQUIRED_INPUT_FIELDS - supplied)
        if missing:
            raise SchemaError("Missing required fields: " + ", ".join(missing))

        schema_version = str(value.get("schema_version", SCHEMA_VERSION)).strip()
        if schema_version != SCHEMA_VERSION:
            raise SchemaError(
                f"Unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION!r}"
            )

        side = _required_text("side", value.get("side")).upper()
        if side not in {"LONG", "SHORT"}:
            raise SchemaError("side must be LONG or SHORT")

        entry_time = _timestamp("entry_time", value.get("entry_time"), assume_timezone)
        exit_time = _timestamp("exit_time", value.get("exit_time"), assume_timezone)
        if exit_time < entry_time:
            raise SchemaError("exit_time cannot be earlier than entry_time")

        return cls(
            schema_version=SCHEMA_VERSION,
            run_id=_required_text("run_id", value.get("run_id")),
            trade_id=_required_text("trade_id", value.get("trade_id")),
            batch_id=_optional_text(value.get("batch_id")),
            leg_id=_optional_text(value.get("leg_id")),
            strategy=_required_text("strategy", value.get("strategy")),
            symbol=_required_text("symbol", value.get("symbol")),
            side=side,
            entry_time=entry_time,
            exit_time=exit_time,
            quantity=_positive_decimal("quantity", value.get("quantity")),
            entry_price=_positive_decimal("entry_price", value.get("entry_price")),
            exit_price=_positive_decimal("exit_price", value.get("exit_price")),
            multiplier=_positive_decimal("multiplier", value.get("multiplier", 1)),
            fees=_positive_decimal("fees", value.get("fees", 0), allow_zero=True),
        )

    def to_csv_row(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "trade_id": self.trade_id,
            "batch_id": self.batch_id,
            "leg_id": self.leg_id,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "side": self.side,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "quantity": _decimal_text(self.quantity),
            "entry_price": _decimal_text(self.entry_price),
            "exit_price": _decimal_text(self.exit_price),
            "multiplier": _decimal_text(self.multiplier),
            "fees": _decimal_text(self.fees),
        }
