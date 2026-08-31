"""Small command-line interface for schema discovery and CSV validation."""

from __future__ import annotations

import argparse

from .core import TradeLogError, validate_trade_log_csv
from .schema import CSV_COLUMNS, SCHEMA_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trade-log")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the canonical CSV header")
    validate = commands.add_parser("validate", help="validate a canonical trade-log CSV")
    validate.add_argument("csv_path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "schema":
        print(",".join(CSV_COLUMNS))
        return 0
    try:
        receipt = validate_trade_log_csv(args.csv_path)
    except TradeLogError as exc:
        print(f"Validation failed: {exc}")
        return 1
    print(f"Schema version: {SCHEMA_VERSION}")
    print(f"Rows: {receipt.row_count}")
    print(f"SHA-256: {receipt.sha256}")
    print("Validation: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
