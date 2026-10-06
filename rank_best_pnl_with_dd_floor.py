"""Rank combinations by best P&L with a drawdown floor.

Default rule:
    capital = 300000
    max allowed drawdown = 2% of capital = 6000

Example:
    !pip install -q duckdb
    %run /kaggle/working/rank_best_pnl_with_dd_floor.py

Custom capital/DD:
    %run /kaggle/working/rank_best_pnl_with_dd_floor.py --capital 300000 --max-dd-pct 2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_INPUT = "/kaggle/input/datasets/joyal126457/23123as"
DEFAULT_OUTPUT = "/kaggle/working/sensex_best_pnl_dd_floor.parquet"
DEFAULT_TOP_CSV = "/kaggle/working/sensex_best_pnl_dd_floor_top100.csv"
DEFAULT_DB = "/kaggle/working/sensex_best_pnl_dd_floor.duckdb"


def sql_string(value: str | Path) -> str:
    return str(value).replace("'", "''")


def sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


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


def detect_yearly_columns(columns: set[str]) -> tuple[list[str], list[str]]:
    years = sorted(
        column.removeprefix("pnl_")
        for column in columns
        if column.startswith("pnl_") and column.removeprefix("pnl_").isdigit()
    )
    pnl_columns = [f"pnl_{year}" for year in years]
    dd_columns = [f"mtm_dd_{year}" for year in years if f"mtm_dd_{year}" in columns]
    return pnl_columns, dd_columns


def choose_pnl_expression(columns: set[str], yearly_pnl_cols: list[str]) -> str:
    for column in ("net_pnl", "pnl", "total_pnl", "net_profit", "total_net_pnl", "gross_pnl"):
        if column in columns:
            return sql_identifier(column)
    if yearly_pnl_cols:
        return "(" + " + ".join(f"COALESCE({sql_identifier(c)}, 0)" for c in yearly_pnl_cols) + ")"
    raise ValueError("No P&L column found. Expected net_pnl or yearly pnl_YYYY columns.")


def choose_drawdown_expression(columns: set[str], yearly_dd_cols: list[str]) -> str:
    for column in ("max_drawdown", "mtm_drawdown", "drawdown", "max_dd"):
        if column in columns:
            return f"ABS({sql_identifier(column)})"
    if yearly_dd_cols:
        return "GREATEST(" + ", ".join(f"ABS(COALESCE({sql_identifier(c)}, 0))" for c in yearly_dd_cols) + ")"
    raise ValueError("No drawdown column found. Expected max_drawdown, mtm_drawdown, or mtm_dd_YYYY columns.")


def rank_best_pnl_with_dd_floor(
    input_path: str | Path = DEFAULT_INPUT,
    output_path: str | Path = DEFAULT_OUTPUT,
    top_csv: str | Path = DEFAULT_TOP_CSV,
    database_path: str | Path = DEFAULT_DB,
    capital: float = 300000.0,
    max_dd_pct: float = 2.0,
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
    max_allowed_dd = capital * max_dd_pct / 100.0

    con = duckdb.connect(str(database))
    try:
        columns = {
            row[0] for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{source_sql}')"
            ).fetchall()
        }
        yearly_pnl_cols, yearly_dd_cols = detect_yearly_columns(columns)
        pnl_expr = choose_pnl_expression(columns, yearly_pnl_cols)
        dd_expr = choose_drawdown_expression(columns, yearly_dd_cols)

        filters = [
            f"{pnl_expr} IS NOT NULL",
            f"{pnl_expr} > 0",
            f"{dd_expr} <= {float(max_allowed_dd)}",
        ]
        if "status" in columns:
            filters.append("status = 'succeeded'")
        if "completed_trade_count" in columns:
            filters.append("completed_trade_count > 0")

        where_sql = " AND ".join(filters)

        print(f"Input Parquet: {source}", flush=True)
        print(f"Capital: {capital:,.2f}", flush=True)
        print(f"Max DD allowed: {max_allowed_dd:,.2f} ({max_dd_pct}% of capital)", flush=True)
        print("Ranking rule: highest P&L after DD floor filter.", flush=True)

        con.execute(f"""
            COPY (
                SELECT
                    ROW_NUMBER() OVER (ORDER BY computed_pnl DESC, computed_drawdown ASC) AS rank,
                    *
                FROM (
                    SELECT *,
                        {pnl_expr} AS computed_pnl,
                        {dd_expr} AS computed_drawdown,
                        {dd_expr} / {float(capital)} * 100.0 AS computed_drawdown_pct
                    FROM read_parquet('{source_sql}')
                    WHERE {where_sql}
                )
                ORDER BY computed_pnl DESC, computed_drawdown ASC
            ) TO '{output_sql}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """)

        con.execute(f"""
            COPY (
                SELECT *
                FROM read_parquet('{output_sql}')
                ORDER BY computed_pnl DESC, computed_drawdown ASC
                LIMIT 100
            ) TO '{csv_sql}' (FORMAT CSV, HEADER TRUE)
        """)

        count = con.execute(f"SELECT COUNT(*) FROM read_parquet('{output_sql}')").fetchone()[0]
        print(f"Rows passing DD floor: {count:,}", flush=True)
        print(f"Saved ranked Parquet: {output}", flush=True)
        print(f"Saved top-100 CSV: {csv_output}", flush=True)

        preview = con.execute(f"""
            SELECT *
            FROM read_parquet('{output_sql}')
            ORDER BY computed_pnl DESC, computed_drawdown ASC
            LIMIT 20
        """).fetchdf()
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
    parser.add_argument("--max-dd-pct", type=float, default=2.0)

    if argv is None and running_inside_notebook():
        argv = []

    args, unknown_args = parser.parse_known_args(argv)
    if unknown_args:
        print(f"Ignoring notebook-injected arguments: {unknown_args}", flush=True)

    rank_best_pnl_with_dd_floor(
        input_path=args.input,
        output_path=args.output,
        top_csv=args.top_csv,
        database_path=args.database,
        capital=args.capital,
        max_dd_pct=args.max_dd_pct,
    )


if __name__ == "__main__":
    main()
