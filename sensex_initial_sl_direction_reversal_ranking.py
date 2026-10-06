from pathlib import Path
import duckdb
import pandas as pd
import numpy as np


INPUT_FILE = (
    "/kaggle/input/datasets/joyal126457/qwerty/"
    "sensex_initial_sl_direction_reversal_sweep.parquet"
)

RANKED_PARQUET = "/kaggle/working/sensex_initial_sl_ranked.parquet"
TOP100_CSV = "/kaggle/working/sensex_initial_sl_top100.csv"
TOP20_CSV = "/kaggle/working/sensex_initial_sl_structured_top20.csv"

ROBUSTNESS_LIMIT = 0.70

# This Parquet contains entry times every 10 minutes.
EARLIER_OFFSETS = [-10, -20, -30, -40, -50]
LATER_OFFSETS = [10, 20, 30, 40, 50]

STRATEGY_FIELDS = [
    "initial_premium",
    "individual_leg_sl_pct",
    "post_sl_premium",
    "combined_sl_rs",
    "combined_profit_rs",
    "reversal_wait_minutes",
    "reversal_premium",
    "max_reversal",
    "exit_time",
]


def qi(name):
    return '"' + str(name).replace('"', '""') + '"'


def alias(offset):
    return f"v_m{abs(offset)}" if offset < 0 else f"v_p{offset}"


def join_condition(left, right):
    return " AND ".join(
        f"{left}.{qi(column)} IS NOT DISTINCT FROM {right}.{qi(column)}"
        for column in STRATEGY_FIELDS
    )


def percentile_score(series, higher=True):
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() <= 1:
        return pd.Series(100.0, index=series.index)
    if higher:
        return (values.rank(method="average", pct=True) * 100).fillna(0)
    return ((-values).rank(method="average", pct=True) * 100).fillna(0)


def load_qualified_rows():
    con = duckdb.connect()
    con.execute("SET threads TO 2")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET temp_directory='/kaggle/working/duckdb_temp'")

    base_fields = STRATEGY_FIELDS + ["entry_start", "net_pnl"]
    base_select = ",\n            ".join(qi(x) for x in base_fields)

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE lookup AS
        SELECT
            {base_select},
            try_strptime(CAST(entry_start AS VARCHAR), '%H:%M:%S') AS entry_ts
        FROM read_parquet(?)
        WHERE lower(CAST(status AS VARCHAR)) = 'succeeded'
          AND COALESCE(trading_day_count, 0) > 0
          AND net_pnl > 0
          AND (
              CAST(split_part(entry_start, ':', 1) AS INTEGER) * 60
              + CAST(split_part(entry_start, ':', 2) AS INTEGER)
          ) <= 650
        """,
        [INPUT_FILE],
    )

    joins = []
    selects = []

    for offset in EARLIER_OFFSETS + LATER_OFFSETS:
        name = alias(offset)
        sign = "+" if offset >= 0 else "-"
        minutes = abs(offset)

        joins.append(
            f"""
            LEFT JOIN lookup {name}
              ON {join_condition('b', name)}
             AND {name}.entry_ts =
                 b.entry_ts {sign} INTERVAL '{minutes} minutes'
            """
        )

        output_name = (
            f"prior_pnl_{minutes}"
            if offset < 0
            else f"after_pnl_{minutes}"
        )
        selects.append(f"{name}.net_pnl AS {qi(output_name)}")

    earlier_sql = [f"v_m{x}.net_pnl" for x in [10, 20, 30, 40, 50]]
    later_sql = [f"v_p{x}.net_pnl" for x in [10, 20, 30, 40, 50]]

    earlier_present = " AND ".join(f"{x} IS NOT NULL" for x in earlier_sql)
    later_present = " AND ".join(f"{x} IS NOT NULL" for x in later_sql)

    query = f"""
    WITH candidates AS (
        SELECT *
        FROM lookup
        WHERE (
            CAST(split_part(entry_start, ':', 1) AS INTEGER) * 60
            + CAST(split_part(entry_start, ':', 2) AS INTEGER)
        ) <= 600
    ), matched AS (
        SELECT
            b.*,
            {', '.join(selects)},
            CASE WHEN {earlier_present}
                 THEN LEAST({', '.join(earlier_sql)}) / NULLIF(b.net_pnl, 0)
                 ELSE NULL END AS prior_robust_ratio,
            CASE WHEN {later_present}
                 THEN LEAST({', '.join(later_sql)}) / NULLIF(b.net_pnl, 0)
                 ELSE NULL END AS after_robust_ratio
        FROM candidates b
        {' '.join(joins)}
    ), qualified AS (
        SELECT *
        FROM matched
        WHERE prior_robust_ratio >= {ROBUSTNESS_LIMIT}
           OR after_robust_ratio >= {ROBUSTNESS_LIMIT}
    )
    SELECT original.*,
           qualified.prior_pnl_10, qualified.prior_pnl_20,
           qualified.prior_pnl_30, qualified.prior_pnl_40,
           qualified.prior_pnl_50, qualified.after_pnl_10,
           qualified.after_pnl_20, qualified.after_pnl_30,
           qualified.after_pnl_40, qualified.after_pnl_50,
           qualified.prior_robust_ratio,
           qualified.after_robust_ratio
    FROM read_parquet(?) original
    INNER JOIN qualified
      ON {join_condition('original', 'qualified')}
     AND original.entry_start IS NOT DISTINCT FROM qualified.entry_start
    WHERE lower(CAST(original.status AS VARCHAR)) = 'succeeded'
      AND COALESCE(original.trading_day_count, 0) > 0
      AND original.net_pnl > 0
      AND (
          CAST(split_part(original.entry_start, ':', 1) AS INTEGER) * 60
          + CAST(split_part(original.entry_start, ':', 2) AS INTEGER)
      ) <= 600
    """

    data = con.execute(query, [INPUT_FILE]).fetchdf()
    con.close()

    if data.empty:
        raise ValueError(
            "No strategy passed the 70% one-sided 10-minute robustness rule."
        )

    return data


def rank(data):
    earlier = data[[f"prior_pnl_{x}" for x in [10, 20, 30, 40, 50]]].apply(pd.to_numeric, errors="coerce")
    later = data[[f"after_pnl_{x}" for x in [10, 20, 30, 40, 50]]].apply(pd.to_numeric, errors="coerce")
    base = pd.to_numeric(data["net_pnl"], errors="coerce")

    prior_ratio = earlier.min(axis=1) / base
    after_ratio = later.min(axis=1) / base
    prior_pass = (~earlier.isna().any(axis=1)) & (prior_ratio >= ROBUSTNESS_LIMIT)
    after_pass = (~later.isna().any(axis=1)) & (after_ratio >= ROBUSTNESS_LIMIT)

    data["entry_robust_side"] = np.select(
        [prior_pass & after_pass, prior_pass, after_pass],
        ["both_sides", "prior_side", "after_side"],
        default="neither_side",
    )

    data["pnl_2025_score"] = percentile_score(data["pnl_2025"])
    data["pnl_2026_score"] = percentile_score(data["pnl_2026"])

    # Exclude 2024 completely from ranking.  The drawdown score is based only
    # on the years requested for the current analysis: 2025 and 2026.
    dd_columns = [
        x for x in ["mtm_dd_2025", "mtm_dd_2026"]
        if x in data.columns
    ]

    if dd_columns:
        data["ranking_drawdown"] = data[dd_columns].apply(
            pd.to_numeric,
            errors="coerce",
        ).abs().max(axis=1)
    else:
        data["ranking_drawdown"] = pd.to_numeric(
            data["mtm_drawdown"],
            errors="coerce",
        ).abs()

    data["drawdown_score"] = percentile_score(
        data["ranking_drawdown"],
        higher=False,
    )

    data["final_score"] = (
        0.275 * data["pnl_2025_score"]
        + 0.275 * data["pnl_2026_score"]
        + 0.45 * data["drawdown_score"]
    )

    data = data[data["entry_robust_side"] != "neither_side"].copy()
    data = data.sort_values(
        ["final_score", "net_pnl", "ranking_drawdown"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    data.insert(0, "rank", np.arange(1, len(data) + 1))
    return data


def structure(data):
    data = data.copy()
    data["entry_robust_side"] = data["entry_robust_side"].replace(
        {"prior_side": "before", "after_side": "after", "both_sides": "both"}
    )

    columns = [
        "rank", "entry_start", "initial_premium",
        "individual_leg_sl_pct", "post_sl_premium", "combined_sl_rs",
        "combined_profit_rs", "reversal_wait_minutes",
        "reversal_premium", "max_reversal", "exit_time", "status",
        "trading_day_count", "entry_robust_side", "net_pnl",
        "pnl_2025", "pnl_2026", "mtm_dd_2025", "mtm_dd_2026",
    ]

    for column in columns:
        if column not in data.columns:
            data[column] = pd.NA

    top20 = data[columns].head(20).copy()
    top20.to_csv(TOP20_CSV, index=False)
    return top20


def main():
    print("Input:", INPUT_FILE)
    qualified = load_qualified_rows()
    ranked = rank(qualified)

    ranked.to_parquet(RANKED_PARQUET, index=False)
    ranked.head(100).to_csv(TOP100_CSV, index=False)
    top20 = structure(ranked)

    print("Eligible rows:", len(ranked))
    print("Ranked output:", RANKED_PARQUET)
    print("Top 100:", TOP100_CSV)
    print("Top 20:", TOP20_CSV)
    print(top20.to_string(index=False))


if __name__ == "__main__":
    main()
