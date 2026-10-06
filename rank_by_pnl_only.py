"""Rank SENSEX sweep combinations by total P&L only.

Example:
    !pip install -q duckdb
    %run /kaggle/working/rank_by_pnl_only.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_INPUT = "/kaggle/input/datasets/joyal126457/oijpojpj"
DEFAULT_OUTPUT = "/kaggle/working/sensex_pnl_ranked.parquet"
DEFAULT_TOP_CSV = "/kaggle/working/sensex_pnl_top100.csv"
DEFAULT_DB = "/kaggle/working/sensex_pnl_sort.duckdb"


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

    yearly_files = [file for file in files if "yearly" in file.name.lower()]
    if yearly_files:
        files = yearly_files
    if len(files) > 1:
        print(f"Multiple Parquet files found; using {files[0]}", flush=True)
    return files[0]


def rank_by_pnl(
    input_path: str | Path = DEFAULT_INPUT,
    output_path: str | Path = DEFAULT_OUTPUT,
    top_csv: str | Path = DEFAULT_TOP_CSV,
    database_path: str | Path = DEFAULT_DB,
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

    con = duckdb.connect(str(database))
    try:
        columns = {
            row[0] for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{source_sql}')"
            ).fetchall()
        }
        required = {"net_pnl", "status", "completed_trade_count"}
        missing = sorted(required - columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        print(f"Input Parquet: {source}", flush=True)
        print(
            "Ranking rule: highest net_pnl first. "
            "Only succeeded runs with completed trades are kept.",
            flush=True,
        )

        con.execute(f"""
            COPY (
                SELECT
                    ROW_NUMBER() OVER (ORDER BY net_pnl DESC) AS rank,
                    *
                FROM read_parquet('{source_sql}')
                WHERE status = 'succeeded'
                  AND completed_trade_count > 0
                  AND net_pnl IS NOT NULL
                ORDER BY net_pnl DESC
            ) TO '{output_sql}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """)

        con.execute(f"""
            COPY (
                SELECT *
                FROM read_parquet('{output_sql}')
                ORDER BY net_pnl DESC
                LIMIT 100
            ) TO '{csv_sql}' (FORMAT CSV, HEADER TRUE)
        """)

        preview = con.execute(f"""
            SELECT *
            FROM read_parquet('{output_sql}')
            ORDER BY net_pnl DESC
            LIMIT 20
        """).fetchdf()

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

    if argv is None and running_inside_notebook():
        argv = []

    args, unknown_args = parser.parse_known_args(argv)
    if unknown_args:
        print(f"Ignoring notebook-injected arguments: {unknown_args}", flush=True)

    rank_by_pnl(args.input, args.output, args.top_csv, args.database)


if __name__ == "__main__":
    main()
