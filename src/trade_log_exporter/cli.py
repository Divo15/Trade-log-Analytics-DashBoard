"""Small command-line interface for schema discovery and CSV validation."""

from __future__ import annotations

import argparse

from .core import TradeLogError, validate_trade_log_csv
from .schema import CSV_COLUMNS, SCHEMA_VERSION
from .sweep import SWEEP_CSV_COLUMNS, SWEEP_SCHEMA_VERSION, validate_sweep_summary_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trade-log")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the canonical CSV header")
    commands.add_parser("sweep-schema", help="print the sweep-summary CSV header")
    validate = commands.add_parser("validate", help="validate a canonical trade-log CSV")
    validate.add_argument("csv_path")
    validate_sweep = commands.add_parser(
        "validate-sweep", help="validate a sweep-summary CSV"
    )
    validate_sweep.add_argument("csv_path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "schema":
        print(",".join(CSV_COLUMNS))
        return 0
    if args.command == "sweep-schema":
        print(",".join(SWEEP_CSV_COLUMNS))
        return 0
    try:
        if args.command == "validate-sweep":
            receipt = validate_sweep_summary_csv(args.csv_path)
            schema_version = SWEEP_SCHEMA_VERSION
        else:
            receipt = validate_trade_log_csv(args.csv_path)
            schema_version = SCHEMA_VERSION
    except TradeLogError as exc:
        print(f"Validation failed: {exc}")
        return 1
    print(f"Schema version: {schema_version}")
    print(f"Rows: {receipt.row_count}")
    print(f"SHA-256: {receipt.sha256}")
    print("Validation: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
