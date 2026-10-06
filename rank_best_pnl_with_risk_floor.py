"""Rank combinations by best P&L with drawdown and SL filters.

Default rules:
    - Input path: /kaggle/input/datasets/joyal126457/23123as
    - Capital: 300000
    - Max drawdown allowed: 2% of capital
    - Max leg SL allowed: 23%
    - Ranking: highest net_pnl first

Example:
    !pip install -q duckdb
    %run /kaggle/working/rank_best_pnl_with_risk_floor.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_INPUT = "/kaggle/input/datasets/joyal126457/23123as"
DEFAULT_OUTPUT = "/kaggle/working/best_pnl_risk_filtered.parquet"
DEFAULT_TOP_CSV = "/kaggle/working/best_pnl_risk_filtered_top100.csv"
DEFAULT_DB = "/kaggle/working/best_pnl_risk_filtered.duckdb"


def sql_string(value: str | Path) -> str:
    return str(value).replace("'", "''")


def running_inside_notebook() -> bool:
    executable = Path(sys.argv[0]).name.lower() if sys.argv else ""
    return (
        "ipykernel_launcher" in executable
        or "colab_kernel_launcher" in executable
        or any(arg.endswith(".json") and "/jupyter/" in arg for arg in sys.argv[1:])
    )


def find_input_parquet(input_path: str | Path) -> Path:
    path = Path(input_path)
    if path.is_file():
        return path
    if not path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {path}")

    files = sorted(path.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No Parquet file found below: {path}")

    if len(files) > 1:
        print(f"Multiple Parquet files found; using {files[0]}", flush=True)
    return files[0]


def detect_yearly_pnl_columns(columns: set[str]) -> list[str]:
    years = sorted(
        column.removeprefix("pnl_")
        for column in columns
        if column.startswith("pnl_") and column.removeprefix("pnl_").isdigit()
    )
    return [f"pnl_{year}" for year in years]


def choose_pnl_expr(columns: set[str], yearly_pnl_cols: list[str]) -> str:
    for column in ("net_pnl", "pnl", "total_pnl", "net_profit", "total_net_pnl", "gross_pnl"):
        if column in columns:
            return column
    if yearly_pnl_cols:
        return "(" + " + ".join(f"COALESCE({column}, 0)" for column in yearly_pnl_cols) + ")"
    raise ValueError("No P&L column found. Expected net_pnl, pnl, total_pnl, or pnl_YYYY.")


def choose_drawdown_expr(columns: set[str]) -> str:
    if "max_drawdown" in columns:
        return "ABS(max_drawdown)"
    if "mtm_drawdown" in columns:
        return "ABS(mtm_drawdown)"

    dd_cols = [
        column for column in sorted(columns)
        if column.startswith("mtm_dd_") and column.removeprefix("mtm_dd_").isdigit()
    ]
    if dd_cols:
        return "GREATEST(" + ", ".join(f"ABS(COALESCE({column}, 0))" for column in dd_cols) + ")"

    raise ValueError("No drawdown column found. Expected max_drawdown, mtm_drawdown, or mtm_dd_YYYY.")


def rank_best_pnl(
    input_path: str | Path = DEFAULT_INPUT,
    output_path: str | Path = DEFAULT_OUTPUT,
    top_csv: str | Path = DEFAULT_TOP_CSV,
    database_path: str | Path = DEFAULT_DB,
    capital: float = 300000.0,
    max_drawdown_pct: float = 2.0,
    max_leg_sl_pct: float = 23.0,
) -> Path:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("Install DuckDB first: pip install duckdb") from exc

    source = find_input_parquet(input_path)
    output = Path(output_path)
    csv_output = Path(top_csv)
    database = Path(database_path)

    for folder in (output.parent, csv_output.parent, database.parent):
        folder.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    if csv_output.exists():
        csv_output.unlink()

    source_sql = sql_string(source)
    output_sql = sql_string(output)
    csv_sql = sql_string(csv_output)
    max_drawdown_value = capital * max_drawdown_pct / 100.0

    con = duckdb.connect(str(database))
    try:
        columns = {
            row[0] for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{source_sql}')"
            ).fetchall()
        }
        missing = sorted({"leg_sl_pct"} - columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        yearly_pnl_cols = detect_yearly_pnl_columns(columns)
        pnl_expr = choose_pnl_expr(columns, yearly_pnl_cols)
        drawdown_expr = choose_drawdown_expr(columns)

        print(f"Input Parquet: {source}", flush=True)
        print(f"Capital: {capital:,.2f}", flush=True)
        print(f"Max drawdown allowed: {max_drawdown_value:,.2f} ({max_drawdown_pct}%)", flush=True)
        print(f"Max leg SL allowed: {max_leg_sl_pct}%", flush=True)
        print("Ranking rule: highest P&L first after filters.", flush=True)

        con.execute(f"""
            COPY (
                WITH prepared AS (
                    SELECT *,
                        {pnl_expr} AS ranking_pnl,
                        {drawdown_expr} AS drawdown_value
                    FROM read_parquet('{source_sql}')
                ), filtered AS (
                    SELECT *,
                        ranking_pnl / {float(capital)} * 100.0 AS roi_pct
                    FROM prepared
                    WHERE ranking_pnl IS NOT NULL
                      AND ranking_pnl > 0
                      AND drawdown_value <= {float(max_drawdown_value)}
                      AND leg_sl_pct <= {float(max_leg_sl_pct)}
                )
                SELECT
                    ROW_NUMBER() OVER (
                        ORDER BY ranking_pnl DESC, drawdown_value ASC, leg_sl_pct ASC
                    ) AS rank,
                    *
                FROM filtered
                ORDER BY ranking_pnl DESC, drawdown_value ASC, leg_sl_pct ASC
            ) TO '{output_sql}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """)

        con.execute(f"""
            COPY (
                SELECT *
                FROM read_parquet('{output_sql}')
                ORDER BY ranking_pnl DESC, drawdown_value ASC, leg_sl_pct ASC
                LIMIT 100
            ) TO '{csv_sql}' (FORMAT CSV, HEADER TRUE)
        """)

        count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{output_sql}')").fetchone()[0]
        preview = con.execute(f"""
            SELECT *
            FROM read_parquet('{output_sql}')
            ORDER BY ranking_pnl DESC, drawdown_value ASC, leg_sl_pct ASC
            LIMIT 20
        """).fetchdf()

        print(f"Filtered rows: {count:,}", flush=True)
        print(f"Saved ranked Parquet: {output}", flush=True)
        print(f"Saved top-100 CSV: {csv_output}", flush=True)
        display(preview)
        return output
    finally:
        con.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--top-csv", default=DEFAULT_TOP_CSV)
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--capital", type=float, default=300000.0)
    parser.add_argument("--max-drawdown-pct", type=float, default=2.0)
    parser.add_argument("--max-leg-sl-pct", type=float, default=23.0)

    if argv is None and running_inside_notebook():
        argv = []

    args, unknown_args = parser.parse_known_args(argv)
    if unknown_args:
        print(f"Ignoring notebook-injected arguments: {unknown_args}", flush=True)

    rank_best_pnl(
        input_path=args.input,
        output_path=args.output,
        top_csv=args.top_csv,
        database_path=args.database,
        capital=args.capital,
        max_drawdown_pct=args.max_drawdown_pct,
        max_leg_sl_pct=args.max_leg_sl_pct,
    )


if __name__ == "__main__":
    main()
