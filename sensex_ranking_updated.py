"""Rank the SENSEX yearly sweep with entry robustness and re-entry preference."""
from pathlib import Path
import os
import duckdb

INPUT = Path("/kaggle/input/datasets/joyal126457/new-structure")
OUTPUT = Path("/kaggle/working/sensex_8lac_ranked.parquet")
TOP100 = Path("/kaggle/working/sensex_8lac_top100.csv")
TOP20 = Path("/kaggle/working/sensex_8lac_structured_top20.csv")
DB = Path("/kaggle/working/sensex_8lac_sort.duckdb")

METRICS = {
    "entry_start", "status", "completed_trade_count", "net_pnl", "pnl",
    "total_pnl", "net_profit", "total_net_pnl", "gross_pnl", "max_loss",
    "win_rate", "max_drawdown", "mtm_drawdown", "max_consecutive_losses",
    "combination_index", "overall_roi_pct",
}

def q(name):
    return '"' + name.replace('"', '""') + '"'

def sql_path(path):
    return str(path).replace("'", "''")

def find_source():
    files = sorted(
        INPUT.rglob("sensex_algotest_execution_sweep*.parquet")
    ) if INPUT.is_dir() else [INPUT]
    if not files and INPUT.is_dir():
        files = sorted(INPUT.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No Parquet file found below {INPUT}")
    yearly = [p for p in files if "yearly" in p.name.lower()]
    return (yearly or files)[0]

def main():
    source = find_source()
    for path in (OUTPUT, TOP100, TOP20):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

    con = duckdb.connect(str(DB))
    con.execute("SET preserve_insertion_order=false")
    con.execute(f"SET threads={max(1, min(8, os.cpu_count() or 1))}")
    src = sql_path(source)
    columns = {r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{src}')"
    ).fetchall()}
    required = {"entry_start", "status", "net_pnl"}
    missing = required - columns
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    # Group only by strategy settings. Do not include yearly results, audit
    # metrics, or derived ranking fields in the grouping key.
    result_columns = {
        column for column in columns
        if column.startswith(("pnl_", "roi_", "mtm_dd_"))
    } | {
        "recent_year_pnl", "worst_year_pnl", "worst_year_drawdown",
        "drawdown_risk", "max_loss_risk", "reentry_burden",
        "prior_min_pnl", "prior_count", "after_min_pnl", "after_count",
        "prior_robust_ratio", "after_robust_ratio", "robustness_ratio",
        "entry_robust_side", "recent_score", "worst_year_score",
        "pnl_score", "robustness_score", "drawdown_score",
        "max_loss_score", "streak_score", "reentry_score", "risk_score",
        "final_score", "generated_combination_index", "exit_time",
    }
    params = sorted(columns - METRICS - result_columns)
    if not params:
        raise ValueError("Could not identify strategy parameter columns")
    group = ", ".join(q(x) for x in params)
    reentries = [x for x in (
        "leg_sl_reentries", "combined_max_loss_reentries",
        "combined_target_reentries"
    ) if x in columns]
    reentry_expr = " + ".join(f"COALESCE({q(x)}, 0)" for x in reentries) or "0"
    years = sorted(x for x in columns if x.startswith("pnl_") and x[4:].isdigit())
    if not years:
        raise ValueError("Yearly pnl_YYYY columns are required")
    recent = "(" + " + ".join(f"COALESCE({q(x)}, 0)" for x in years[-2:]) + f") / {len(years[-2:])}.0"
    worst = "LEAST(" + ", ".join(f"COALESCE({q(x)}, 0)" for x in years) + ")"
    year_filter = " AND ".join(f"{q(x)} > 0" for x in years)
    dd = [x for x in columns if x.startswith("mtm_dd_") and x[7:].isdigit()]
    worst_dd = ("GREATEST(" + ", ".join(f"ABS(COALESCE({q(x)}, 0))" for x in dd) + ")") if dd else "ABS(max_drawdown)"
    if "max_drawdown" in columns:
        drawdown_expr = "max_drawdown"
    elif "mtm_drawdown" in columns:
        drawdown_expr = "mtm_drawdown"
    elif dd:
        drawdown_expr = "-" + worst_dd
    else:
        raise ValueError("Missing drawdown data: expected max_drawdown, mtm_drawdown, or mtm_dd_YYYY")

    max_loss_expr = q("max_loss") if "max_loss" in columns else "0"
    streak_expr = q("max_consecutive_losses") if "max_consecutive_losses" in columns else "0"

    con.execute(f"""
    CREATE OR REPLACE TABLE scored_base AS
    WITH p AS (
      SELECT *, CAST(split_part(entry_start, ':', 1) AS INTEGER) * 60
                       + CAST(split_part(entry_start, ':', 2) AS INTEGER) AS entry_minutes
      FROM read_parquet('{src}')
    ), w AS (
      SELECT *,
        MIN(net_pnl) OVER (PARTITION BY {group} ORDER BY entry_minutes RANGE BETWEEN 20 PRECEDING AND 2 PRECEDING) AS prior_min,
        COUNT(*) OVER (PARTITION BY {group} ORDER BY entry_minutes RANGE BETWEEN 20 PRECEDING AND 2 PRECEDING) AS prior_count,
        MIN(net_pnl) OVER (PARTITION BY {group} ORDER BY entry_minutes RANGE BETWEEN 2 FOLLOWING AND 20 FOLLOWING) AS after_min,
        COUNT(*) OVER (PARTITION BY {group} ORDER BY entry_minutes RANGE BETWEEN 2 FOLLOWING AND 20 FOLLOWING) AS after_count
      FROM p
    ), e AS (
      SELECT *,
        {recent} AS recent_year_pnl,
        {worst} AS worst_year_pnl,
        {worst_dd} AS worst_year_drawdown,
        ABS({drawdown_expr}) AS drawdown_risk,
        ABS({max_loss_expr}) AS max_loss_risk,
        ({reentry_expr}) AS reentry_burden,
        prior_min / NULLIF(net_pnl, 0) AS prior_robust_ratio,
        after_min / NULLIF(net_pnl, 0) AS after_robust_ratio,
        GREATEST(COALESCE(prior_min / NULLIF(net_pnl, 0), 0), COALESCE(after_min / NULLIF(net_pnl, 0), 0)) AS robustness_ratio,
        CASE
          WHEN prior_count = 10 AND prior_min >= net_pnl * 0.70 AND after_count = 10 AND after_min >= net_pnl * 0.70 THEN 'both_sides'
          WHEN prior_count = 10 AND prior_min >= net_pnl * 0.70 THEN 'prior_side'
          WHEN after_count = 10 AND after_min >= net_pnl * 0.70 THEN 'after_side'
          ELSE 'neither_side'
        END AS entry_robust_side
      FROM w
    )
    SELECT * FROM e
    WHERE status = 'succeeded' AND net_pnl > 0
      AND {drawdown_expr} IS NOT NULL
      AND entry_robust_side <> 'neither_side'
      AND (
        CAST(split_part(entry_start, ':', 1) AS INTEGER) * 60
        + CAST(split_part(entry_start, ':', 2) AS INTEGER)
      ) <= 600
    """)

    con.execute(f"""
    COPY (
      WITH r AS (
        SELECT *,
          100 * PERCENT_RANK() OVER (ORDER BY recent_year_pnl) AS recent_score,
          100 * PERCENT_RANK() OVER (ORDER BY worst_year_pnl) AS worst_year_score,
          100 * PERCENT_RANK() OVER (ORDER BY net_pnl) AS pnl_score,
          100 * PERCENT_RANK() OVER (ORDER BY pnl_2025) AS pnl_2025_score,
          100 * PERCENT_RANK() OVER (ORDER BY pnl_2026) AS pnl_2026_score,
          100 * PERCENT_RANK() OVER (ORDER BY robustness_ratio) AS robustness_score,
          100 * (1 - PERCENT_RANK() OVER (ORDER BY drawdown_risk)) AS drawdown_score,
          100 * (1 - PERCENT_RANK() OVER (ORDER BY max_loss_risk)) AS max_loss_score,
          100 * (1 - PERCENT_RANK() OVER (ORDER BY {streak_expr})) AS streak_score,
          100 * (1 - PERCENT_RANK() OVER (ORDER BY reentry_burden)) AS reentry_score
        FROM scored_base
      ), s AS (
        SELECT *,
          0.60 * drawdown_score + 0.24 * max_loss_score + 0.16 * streak_score AS risk_score,
          0.30 * pnl_2025_score
          + 0.30 * pnl_2026_score
          + 0.40 * drawdown_score AS final_score
        FROM r
      )
      SELECT * FROM s
      ORDER BY final_score DESC,
               net_pnl DESC,
               drawdown_risk ASC,
               reentry_burden ASC,
               {streak_expr} ASC
    ) TO '{sql_path(OUTPUT)}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    con.execute(f"COPY (SELECT * FROM read_parquet('{sql_path(OUTPUT)}') LIMIT 100) TO '{sql_path(TOP100)}' (FORMAT CSV, HEADER TRUE)")
    con.execute(f"COPY (SELECT * FROM read_parquet('{sql_path(OUTPUT)}') LIMIT 20) TO '{sql_path(TOP20)}' (FORMAT CSV, HEADER TRUE)")
    print(f"Input: {source}")
    print(f"Ranked output: {OUTPUT}")
    print(f"Top 100: {TOP100}")
    print(f"Top 20: {TOP20}")
    con.close()

if __name__ == "__main__":
    main()
