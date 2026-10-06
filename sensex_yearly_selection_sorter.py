"""Yearly-data sorter/selector for the completed SENSEX sweep.

Example:
    !pip install -q duckdb
    %run /kaggle/working/sensex_yearly_selection_sorter.py

The raw sweep and selection stages remain separate. The script applies hard
filters, checks the exact five-point earlier/later entry-time robustness rule,
gives 30% weight to P&L, uses drawdown-heavy risk scoring, and writes
structured ranked output.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_INPUT = "/kaggle/input/datasets/joyal126457/sensex-8lac-hedged-sweep"
DEFAULT_OUTPUT = "/kaggle/working/sensex_8lac_ranked.parquet"
DEFAULT_TOP_CSV = "/kaggle/working/sensex_8lac_top100.csv"
DEFAULT_STRUCTURED_TOP20_CSV = "/kaggle/working/sensex_8lac_structured_top20.csv"
DEFAULT_DB = "/kaggle/working/sensex_8lac_sort.duckdb"
DEFAULT_TEMP = "/kaggle/working/sensex_duckdb_temp"

REQUIRED_COLUMNS = {
    "entry_start", "status", "completed_trade_count", "net_pnl",
    "max_loss", "win_rate", "max_drawdown", "max_consecutive_losses",
}
METRIC_COLUMNS = {
    "combination_index", "entry_start", "status", "completed_trade_count",
    "net_pnl", "pnl", "total_pnl", "net_profit", "total_net_pnl",
    "gross_pnl", "max_loss", "win_rate", "max_drawdown", "mtm_drawdown",
    "max_consecutive_losses", "overall_roi_pct", "pnl_2024", "roi_2024_pct",
    "mtm_dd_2024", "pnl_2025", "roi_2025_pct", "mtm_dd_2025", "pnl_2026",
    "roi_2026_pct", "mtm_dd_2026",
}


def sql_string(value: str | Path) -> str:
    return str(value).replace("'", "''")


def sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def detect_yearly_columns(columns: set[str]) -> tuple[list[str], list[str]]:
    years = sorted(
        column.removeprefix("pnl_")
        for column in columns
        if column.startswith("pnl_") and column.removeprefix("pnl_").isdigit()
    )
    pnl_columns = [f"pnl_{year}" for year in years]
    dd_columns = [f"mtm_dd_{year}" for year in years if f"mtm_dd_{year}" in columns]
    return pnl_columns, dd_columns


def find_input_parquets(input_path: str | Path) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        return [path]
    search_roots = []
    if path.is_dir():
        search_roots.append(path)
    for fallback in (
        Path("/kaggle/input"),
        Path("/kaggle/input/datasets/joyal126457/sensex-8lac-sweep"),
        Path("/kaggle/working"),
    ):
        if fallback.is_dir() and fallback not in search_roots:
            search_roots.append(fallback)
    if not search_roots:
        raise FileNotFoundError(
            f"Input path does not exist and no Kaggle input folder was found: {path}"
        )

    files = []
    for root in search_roots:
        files.extend(sorted(root.rglob("*.parquet")))
    if not files:
        searched = ", ".join(str(root) for root in search_roots)
        raise FileNotFoundError(f"No Parquet file found below: {searched}")
    yearly_files = [file for file in files if "yearly" in file.name.lower()]
    if yearly_files:
        files = yearly_files
    return files


def find_input_parquet(input_path: str | Path) -> Path:
    files = find_input_parquets(input_path)
    if len(files) > 1:
        print(f"Multiple Parquet files found; using {files[0]}", flush=True)
    return files[0]


def sort_sensex_sweep(
    input_path: str | Path = DEFAULT_INPUT,
    output_path: str | Path = DEFAULT_OUTPUT,
    top_csv: str | Path = DEFAULT_TOP_CSV,
    structured_top20_csv: str | Path = DEFAULT_STRUCTURED_TOP20_CSV,
    database_path: str | Path = DEFAULT_DB,
    temp_directory: str | Path = DEFAULT_TEMP,
    memory_limit: str = "12GB",
    min_roi_pct: float = 15.0,
    bracket_minutes: int = 25,
    bracket_min_ratio: float = 0.70,
) -> Path:
    """Filter and rank the sweep with explicit consistency-first weights.

    Score weights: recent-year performance 18%, entry robustness 15%,
    worst-year performance 10%, inverse risk 20%, and total P&L 30%.
    Percentiles are calculated after hard filters.
    """
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("Install DuckDB first: pip install duckdb") from exc
    if int(bracket_minutes) != 25:
        raise ValueError("bracket_minutes must be exactly 25 for the five-point rule")

    source = find_input_parquet(input_path)
    output = Path(output_path)
    csv_output = Path(top_csv)
    structured_csv_output = Path(structured_top20_csv)
    database = Path(database_path)
    temp = Path(temp_directory)
    for folder in (output.parent, csv_output.parent, structured_csv_output.parent, database.parent, temp):
        folder.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    if csv_output.exists():
        csv_output.unlink()
    if structured_csv_output.exists():
        structured_csv_output.unlink()

    source_sql = sql_string(source)
    output_sql = sql_string(output)
    csv_sql = sql_string(csv_output)
    structured_csv_sql = sql_string(structured_csv_output)
    con = duckdb.connect(str(database))
    try:
        con.execute(f"SET memory_limit='{sql_string(memory_limit)}'")
        con.execute(f"SET temp_directory='{sql_string(temp)}'")
        con.execute("SET preserve_insertion_order=false")
        con.execute(f"SET threads={max(1, min(8, os.cpu_count() or 1))}")

        columns = {
            row[0] for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{source_sql}')"
            ).fetchall()
        }
        years, yearly_drawdowns = detect_yearly_columns(columns)
        if not years or not yearly_drawdowns:
            for candidate in find_input_parquets(input_path):
                candidate_sql = sql_string(candidate)
                candidate_columns = {
                    row[0] for row in con.execute(
                        f"DESCRIBE SELECT * FROM read_parquet('{candidate_sql}')"
                    ).fetchall()
                }
                candidate_years, candidate_drawdowns = detect_yearly_columns(candidate_columns)
                if candidate_years and candidate_drawdowns:
                    source = candidate
                    source_sql = candidate_sql
                    columns = candidate_columns
                    years = candidate_years
                    yearly_drawdowns = candidate_drawdowns
                    print(f"Using yearly Parquet with YoY values: {source}", flush=True)
                    break
        if not years:
            raise ValueError(
                "The selected Parquet does not contain any yearly P&L columns "
                "like pnl_2024, pnl_2025, or pnl_2026."
            )
        if not yearly_drawdowns:
            raise ValueError(
                "The selected Parquet does not contain matching yearly drawdown "
                "columns like mtm_dd_2024, mtm_dd_2025, or mtm_dd_2026."
            )
        has_mtm_drawdown = "mtm_drawdown" in columns
        max_drawdown_missing = "max_drawdown" not in columns
        pnl_source = next(
            (
                column for column in (
                    "net_pnl", "pnl", "total_pnl", "net_profit",
                    "total_net_pnl", "gross_pnl",
                )
                if column in columns
            ),
            None,
        )
        net_pnl_missing = "net_pnl" not in columns
        missing_required = REQUIRED_COLUMNS - columns
        if net_pnl_missing:
            missing_required.discard("net_pnl")
            if pnl_source:
                print(
                    f"net_pnl column missing; using {pnl_source} as net_pnl.",
                    flush=True,
                )
            else:
                print(
                    "net_pnl column missing; deriving it from available yearly P&L columns.",
                    flush=True,
                )
        if max_drawdown_missing:
            missing_required.discard("max_drawdown")
            if has_mtm_drawdown:
                print(
                    "max_drawdown column missing; using mtm_drawdown as max_drawdown.",
                    flush=True,
                )
            else:
                print(
                    "max_drawdown column missing; deriving it from yearly DD columns.",
                    flush=True,
                )
        missing = sorted(missing_required)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        row_count = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{source_sql}')"
        ).fetchone()[0]
        print(f"Input rows: {row_count:,}", flush=True)
        print(f"Input Parquet: {source}", flush=True)

        params = sorted(columns - METRIC_COLUMNS)
        if not params:
            raise ValueError("Could not identify strategy parameter columns")
        params_sql = ", ".join(sql_identifier(c) for c in params)
        print(f"Parameters held constant: {params}", flush=True)
        reentry_columns = [
            column for column in (
                "leg_sl_reentries",
                "combined_max_loss_reentries",
                "combined_target_reentries",
            ) if column in columns
        ]
        reentry_burden_expr = " + ".join(
            f"COALESCE({sql_identifier(column)}, 0)" for column in reentry_columns
        ) or "0"

        if years:
            recent_years = years[-2:]
            recent_expr = "(" + " + ".join(f"COALESCE({c}, 0)" for c in recent_years) + f") / {len(recent_years)}.0"
            worst_expr = "LEAST(" + ", ".join(f"COALESCE({c}, 0)" for c in years) + ")"
            year_filters = " AND ".join(f"{c} > 0" for c in years)
        else:
            print("Yearly P&L columns absent; using total P&L as consistency proxy.", flush=True)
            recent_expr = "net_pnl"
            worst_expr = "net_pnl"
            year_filters = "TRUE"
        if yearly_drawdowns:
            worst_year_drawdown_expr = (
                "GREATEST("
                + ", ".join(f"ABS(COALESCE({c}, 0))" for c in yearly_drawdowns)
                + ")"
            )
        else:
            worst_year_drawdown_expr = "ABS(max_drawdown)"
        if max_drawdown_missing and has_mtm_drawdown:
            max_drawdown_projection = "*, mtm_drawdown AS max_drawdown"
        elif max_drawdown_missing:
            max_drawdown_projection = (
                "*, -("
                + worst_year_drawdown_expr
                + ") AS max_drawdown"
            )
        else:
            max_drawdown_projection = "*"
        if net_pnl_missing and pnl_source:
            net_pnl_projection = f", {sql_identifier(pnl_source)} AS net_pnl"
        elif net_pnl_missing:
            net_pnl_projection = (
                ", ("
                + " + ".join(f"COALESCE({column}, 0)" for column in years)
                + ") AS net_pnl"
            )
        else:
            net_pnl_projection = ""

        con.execute(f"""
            CREATE OR REPLACE TABLE bracketed AS
            WITH prepared AS (
                SELECT {max_drawdown_projection}{net_pnl_projection},
                    ROW_NUMBER() OVER () - 1 AS generated_combination_index,
                    CAST(split_part(entry_start, ':', 1) AS INTEGER) * 60
                      + CAST(split_part(entry_start, ':', 2) AS INTEGER) AS entry_minutes
                FROM read_parquet('{source_sql}')
            )
            SELECT *,
                MIN(net_pnl) OVER (
                    PARTITION BY {params_sql} ORDER BY entry_minutes
                    RANGE BETWEEN {int(bracket_minutes)} PRECEDING
                          AND {int(bracket_minutes)} FOLLOWING
                ) AS bracket_min_pnl,
                MAX(net_pnl) OVER (
                    PARTITION BY {params_sql} ORDER BY entry_minutes
                    RANGE BETWEEN {int(bracket_minutes)} PRECEDING
                          AND {int(bracket_minutes)} FOLLOWING
                ) AS bracket_max_pnl,
                COUNT(*) OVER (
                    PARTITION BY {params_sql} ORDER BY entry_minutes
                    RANGE BETWEEN {int(bracket_minutes)} PRECEDING
                          AND {int(bracket_minutes)} FOLLOWING
                ) AS bracket_count,
                MIN(net_pnl) OVER (
                    PARTITION BY {params_sql} ORDER BY entry_minutes
                    RANGE BETWEEN 25 PRECEDING
                          AND 5 PRECEDING
                ) AS prior_bracket_min_pnl,
                COUNT(*) OVER (
                    PARTITION BY {params_sql} ORDER BY entry_minutes
                    RANGE BETWEEN 25 PRECEDING AND 5 PRECEDING
                ) AS prior_bracket_count,
                MIN(net_pnl) OVER (
                    PARTITION BY {params_sql} ORDER BY entry_minutes
                    RANGE BETWEEN 5 FOLLOWING
                          AND 25 FOLLOWING
                ) AS after_bracket_min_pnl,
                COUNT(*) OVER (
                    PARTITION BY {params_sql} ORDER BY entry_minutes
                    RANGE BETWEEN 5 FOLLOWING AND 25 FOLLOWING
                ) AS after_bracket_count
            FROM prepared
        """)

        # Each qualifying side must contain exactly five five-minute variants.
        expected_bracket = (
            "CAST((LEAST(660, entry_minutes + 25) "
            "- GREATEST(570, entry_minutes - 25)) / 5 + 1 AS BIGINT)"
        )
        expected_side_count = 5
        con.execute(f"""
            CREATE OR REPLACE TABLE eligible AS
            SELECT *,
                {recent_expr} AS recent_year_pnl,
                {worst_expr} AS worst_year_pnl,
                {worst_year_drawdown_expr} AS worst_year_drawdown,
                ABS(max_drawdown) AS drawdown_risk,
                ABS(max_loss) AS max_loss_risk,
                GREATEST(
                    COALESCE(prior_bracket_min_pnl / NULLIF(net_pnl, 0), 0),
                    COALESCE(after_bracket_min_pnl / NULLIF(net_pnl, 0), 0)
                ) AS bracket_pnl_ratio,
                prior_bracket_min_pnl / NULLIF(net_pnl, 0) AS prior_robust_ratio,
                after_bracket_min_pnl / NULLIF(net_pnl, 0) AS after_robust_ratio,
                ({reentry_burden_expr}) AS reentry_burden,
                CASE
                    WHEN prior_bracket_count = {expected_side_count}
                     AND prior_bracket_min_pnl >= net_pnl * {float(bracket_min_ratio)}
                     AND after_bracket_count = {expected_side_count}
                     AND after_bracket_min_pnl >= net_pnl * {float(bracket_min_ratio)}
                        THEN 'both_sides'
                    WHEN prior_bracket_count = {expected_side_count}
                     AND prior_bracket_min_pnl >= net_pnl * {float(bracket_min_ratio)}
                        THEN 'prior_side'
                    WHEN after_bracket_count = {expected_side_count}
                     AND after_bracket_min_pnl >= net_pnl * {float(bracket_min_ratio)}
                        THEN 'after_side'
                    ELSE 'neither_side'
                END AS entry_robust_side,
                net_pnl / 300000.0 * 100.0 AS overall_roi_pct,
                {expected_bracket} AS expected_bracket_count,
                (
                    net_pnl / 300000.0 * 100.0 >= {float(min_roi_pct)}
                    AND ({year_filters})
                    AND (
                        (prior_bracket_count = {expected_side_count}
                         AND prior_bracket_min_pnl >= net_pnl * {float(bracket_min_ratio)})
                        OR
                        (after_bracket_count = {expected_side_count}
                         AND after_bracket_min_pnl >= net_pnl * {float(bracket_min_ratio)})
                    )
                ) AS passes_strict_screen
            FROM bracketed
            WHERE status = 'succeeded'
              AND completed_trade_count > 0
              AND net_pnl > 0
              AND (
                    (prior_bracket_count = {expected_side_count}
                     AND prior_bracket_min_pnl >= net_pnl * {float(bracket_min_ratio)})
                    OR
                    (after_bracket_count = {expected_side_count}
                     AND after_bracket_min_pnl >= net_pnl * {float(bracket_min_ratio)})
                  )
              AND win_rate IS NOT NULL
              AND max_drawdown IS NOT NULL
              AND max_loss IS NOT NULL
              AND max_consecutive_losses IS NOT NULL
              AND bracket_min_pnl IS NOT NULL
              AND bracket_max_pnl IS NOT NULL
        """)

        eligible_count = con.execute("SELECT COUNT(*) FROM eligible").fetchone()[0]
        strict_count = con.execute(
            "SELECT COUNT(*) FROM eligible WHERE passes_strict_screen"
        ).fetchone()[0]
        print(f"Eligible rows: {eligible_count:,}", flush=True)
        print(f"Strict-screen rows: {strict_count:,}", flush=True)
        if eligible_count == 0:
            raise ValueError("No successful positive-P&L combinations found")

        con.execute(f"""
            COPY (
                WITH ranked AS (
                    SELECT *,
                        100.0 * PERCENT_RANK() OVER (ORDER BY recent_year_pnl) AS recent_score,
                        100.0 * PERCENT_RANK() OVER (ORDER BY worst_year_pnl) AS worst_year_score,
                        100.0 * PERCENT_RANK() OVER (ORDER BY net_pnl) AS pnl_score,
                        100.0 * PERCENT_RANK() OVER (ORDER BY bracket_pnl_ratio) AS robustness_score,
                        100.0 * (1.0 - PERCENT_RANK() OVER (ORDER BY reentry_burden)) AS reentry_score,
                        100.0 * (1.0 - PERCENT_RANK() OVER (ORDER BY drawdown_risk)) AS drawdown_score,
                        100.0 * (1.0 - PERCENT_RANK() OVER (ORDER BY max_loss_risk)) AS max_loss_score,
                        100.0 * (1.0 - PERCENT_RANK() OVER (ORDER BY max_consecutive_losses)) AS streak_score
                    FROM eligible
                ), scored AS (
                    SELECT *,
                        0.60 * drawdown_score
                          + 0.24 * max_loss_score
                          + 0.16 * streak_score AS risk_score,
                        0.18 * recent_score
                          + 0.15 * robustness_score
                          + 0.05 * worst_year_score
                          + 0.25 * risk_score
                          + 0.30 * pnl_score
                          + 0.07 * reentry_score AS final_score
                    FROM ranked
                )
                SELECT * FROM scored
                ORDER BY final_score DESC, worst_year_pnl DESC,
                         bracket_pnl_ratio DESC, drawdown_risk ASC,
                         max_loss_risk ASC, net_pnl DESC,
                         generated_combination_index ASC
            ) TO '{output_sql}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """)

        ranked_columns = {
            row[0] for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{output_sql}')"
            ).fetchall()
        }
        clean_columns = [
            *params,
            "entry_start",
            "net_pnl",
            "overall_roi_pct",
            "pnl_2024",
            "pnl_2025",
            "pnl_2026",
            "recent_year_pnl",
            "worst_year_pnl",
            "mtm_dd_2024",
            "mtm_dd_2025",
            "mtm_dd_2026",
            "worst_year_drawdown",
            "max_drawdown",
            "max_loss",
            "max_consecutive_losses",
            "win_rate",
            "bracket_pnl_ratio",
            "prior_bracket_count",
            "after_bracket_count",
            "prior_robust_ratio",
            "after_robust_ratio",
            "entry_robust_side",
            "reentry_burden",
            "reentry_score",
            "passes_strict_screen",
        ]
        clean_columns = [
            column for column in dict.fromkeys(clean_columns)
            if column in ranked_columns
        ]
        clean_select = ", ".join(sql_identifier(column) for column in clean_columns)
        structured_order = (
            "final_score DESC, worst_year_pnl DESC, bracket_pnl_ratio DESC, "
            "drawdown_risk ASC, max_loss_risk ASC, net_pnl DESC, "
            "generated_combination_index ASC"
        )
        con.execute(f"""
            COPY (
                SELECT
                    ROW_NUMBER() OVER (ORDER BY {structured_order}) AS rank,
                    {clean_select}
                FROM read_parquet('{output_sql}')
                ORDER BY {structured_order}
                LIMIT 100
            ) TO '{csv_sql}' (FORMAT CSV, HEADER TRUE)
        """)

        con.execute(f"""
            COPY (
                SELECT
                    ROW_NUMBER() OVER (ORDER BY {structured_order}) AS rank,
                    {clean_select}
                FROM read_parquet('{output_sql}')
                ORDER BY {structured_order}
                LIMIT 20
            ) TO '{structured_csv_sql}' (FORMAT CSV, HEADER TRUE)
        """)

        print(f"Saved ranked Parquet: {output}", flush=True)
        print(f"Saved top-100 CSV: {csv_output}", flush=True)
        print(f"Saved structured top-20 CSV: {structured_csv_output}", flush=True)
        preview = con.execute(f"""
            SELECT * FROM read_csv_auto('{structured_csv_sql}')
        """).fetchdf()
        print("Structured top-20 preview:", flush=True)
        print(preview.to_string(index=False), flush=True)
        return output
    finally:
        con.close()


def running_inside_notebook() -> bool:
    executable = Path(sys.argv[0]).name.lower() if sys.argv else ""
    return (
        "ipykernel_launcher" in executable
        or "colab_kernel_launcher" in executable
        or any(arg.endswith(".json") and "/jupyter/" in arg for arg in sys.argv[1:])
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--top-csv", default=DEFAULT_TOP_CSV)
    parser.add_argument("--structured-top20-csv", default=DEFAULT_STRUCTURED_TOP20_CSV)
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--temp-directory", default=DEFAULT_TEMP)
    parser.add_argument("--memory-limit", default="12GB")
    parser.add_argument("--min-roi-pct", type=float, default=15.0)
    parser.add_argument("--bracket-min-ratio", type=float, default=0.70)
    if argv is None and running_inside_notebook():
        argv = []
    args, unknown_args = parser.parse_known_args(argv)
    if unknown_args:
        print(f"Ignoring notebook-injected arguments: {unknown_args}", flush=True)
    sort_sensex_sweep(
        input_path=args.input, output_path=args.output, top_csv=args.top_csv,
        structured_top20_csv=args.structured_top20_csv,
        database_path=args.database, temp_directory=args.temp_directory,
        memory_limit=args.memory_limit, min_roi_pct=args.min_roi_pct,
        bracket_min_ratio=args.bracket_min_ratio,
    )


if __name__ == "__main__":
    main()
