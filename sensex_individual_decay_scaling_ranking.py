from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

try:
    from IPython.display import display
except ImportError:
    def display(value):
        print(value.to_string(index=False))


INPUT_DIR = Path("/kaggle/input/datasets/joyal126457/unique")

RANKED_PARQUET = Path(
    "/kaggle/working/sensex_individual_decay_scaling_unique_ranked.parquet"
)
TOP100_CSV = Path(
    "/kaggle/working/sensex_individual_decay_scaling_unique_top100.csv"
)
TOP20_CSV = Path(
    "/kaggle/working/sensex_individual_decay_scaling_unique_structured_top20.csv"
)

ROBUSTNESS_LIMIT = 0.70
LATEST_ENTRY_MINUTES = 600  # no entries after 10:00

# This Parquet has entry times on a 10-minute grid.
EARLIER_OFFSETS = [-10, -20, -30, -40, -50]
LATER_OFFSETS = [10, 20, 30, 40, 50]

STRATEGY_FIELDS = [
    "monitor_premium",
    "individual_decay_trigger_pct",
    "lot1_entry_premium",
    "lot1_profit_to_lot2_pct",
    "lot2_entry_premium",
    "lot2_profit_to_lot3_pct",
    "lot3_entry_premium",
    "campaign_sl_rs",
    "campaign_sl_reversal",
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


def find_input_file():
    files = []

    if INPUT_DIR.exists():
        files = sorted(INPUT_DIR.rglob("sensex_individual_decay_scaling_sweep*.parquet"))

    if not files:
        files = sorted(
            Path("/kaggle/input").rglob(
                "sensex_individual_decay_scaling_sweep*.parquet"
            )
        )

    if not files:
        raise FileNotFoundError(
            "No sensex_individual_decay_scaling_sweep Parquet was found."
        )

    return str(files[0])


def percentile_score(series, higher=True):
    values = pd.to_numeric(series, errors="coerce")

    if values.notna().sum() <= 1:
        return pd.Series(100.0, index=series.index)

    if higher:
        return (values.rank(method="average", pct=True) * 100).fillna(0)

    return ((-values).rank(method="average", pct=True) * 100).fillna(0)


def load_qualified_rows(input_file):
    con = duckdb.connect()
    con.execute("SET threads TO 2")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET temp_directory='/kaggle/working/duckdb_temp'")

    base_fields = STRATEGY_FIELDS + ["monitor_start", "net_pnl"]
    base_select = ",\n            ".join(qi(column) for column in base_fields)

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE lookup AS
        SELECT
            {base_select},
            try_strptime(
                CAST(monitor_start AS VARCHAR), '%H:%M:%S'
            ) AS entry_ts
        FROM read_parquet(?)
        WHERE lower(CAST(status AS VARCHAR)) = 'succeeded'
          AND COALESCE(campaign_count, 0) > 0
          AND net_pnl > 0
        """,
        [input_file],
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
            f"prior_pnl_{minutes}" if offset < 0 else f"after_pnl_{minutes}"
        )
        selects.append(f"{name}.net_pnl AS {qi(output_name)}")

    prior_sql = [f"v_m{x}.net_pnl" for x in [10, 20, 30, 40, 50]]
    after_sql = [f"v_p{x}.net_pnl" for x in [10, 20, 30, 40, 50]]

    prior_present = " AND ".join(f"{x} IS NOT NULL" for x in prior_sql)
    after_present = " AND ".join(f"{x} IS NOT NULL" for x in after_sql)

    query = f"""
    WITH candidates AS (
        SELECT *
        FROM lookup
    ),
    matched AS (
        SELECT
            b.*,
            {', '.join(selects)},
            CASE WHEN {prior_present}
                 THEN LEAST({', '.join(prior_sql)}) / NULLIF(b.net_pnl, 0)
                 ELSE NULL END AS prior_robust_ratio,
            CASE WHEN {after_present}
                 THEN LEAST({', '.join(after_sql)}) / NULLIF(b.net_pnl, 0)
                 ELSE NULL END AS after_robust_ratio
        FROM candidates b
        {' '.join(joins)}
    ),
    qualified AS (
        SELECT *
        FROM matched
        WHERE prior_robust_ratio >= {ROBUSTNESS_LIMIT}
           OR after_robust_ratio >= {ROBUSTNESS_LIMIT}
    )
    SELECT original.*,
           qualified.prior_pnl_10,
           qualified.prior_pnl_20,
           qualified.prior_pnl_30,
           qualified.prior_pnl_40,
           qualified.prior_pnl_50,
           qualified.after_pnl_10,
           qualified.after_pnl_20,
           qualified.after_pnl_30,
           qualified.after_pnl_40,
           qualified.after_pnl_50,
           qualified.prior_robust_ratio,
           qualified.after_robust_ratio
    FROM read_parquet(?) original
    INNER JOIN qualified
      ON {join_condition('original', 'qualified')}
     AND original.monitor_start
         IS NOT DISTINCT FROM qualified.monitor_start
    WHERE lower(CAST(original.status AS VARCHAR)) = 'succeeded'
      AND COALESCE(original.campaign_count, 0) > 0
      AND original.net_pnl > 0
      AND (
            CAST(split_part(
                CAST(original.monitor_start AS VARCHAR), ':', 1
            ) AS INTEGER) * 60
            + CAST(split_part(
                CAST(original.monitor_start AS VARCHAR), ':', 2
            ) AS INTEGER)
      ) <= {LATEST_ENTRY_MINUTES}
    """

    # The final query contains one read_parquet(?) placeholder.
    data = con.execute(query, [input_file]).fetchdf()
    con.close()

    if data.empty:
        raise ValueError(
            "No strategy passed the complete one-sided 70% robustness rule."
        )

    return data


def rank_strategies(data):
    prior = data[
        [f"prior_pnl_{x}" for x in [10, 20, 30, 40, 50]]
    ].apply(pd.to_numeric, errors="coerce")

    after = data[
        [f"after_pnl_{x}" for x in [10, 20, 30, 40, 50]]
    ].apply(pd.to_numeric, errors="coerce")

    base = pd.to_numeric(data["net_pnl"], errors="coerce")

    prior_ratio = prior.min(axis=1) / base
    after_ratio = after.min(axis=1) / base

    prior_pass = (~prior.isna().any(axis=1)) & (
        prior_ratio >= ROBUSTNESS_LIMIT
    )
    after_pass = (~after.isna().any(axis=1)) & (
        after_ratio >= ROBUSTNESS_LIMIT
    )

    data["entry_robust_side"] = np.select(
        [prior_pass & after_pass, prior_pass, after_pass],
        ["both_sides", "prior_side", "after_side"],
        default="neither_side",
    )

    # 2024 is excluded from ranking.
    data["pnl_2025_score"] = percentile_score(data["pnl_2025"])
    data["pnl_2026_score"] = percentile_score(data["pnl_2026"])

    dd_columns = [
        column
        for column in ["mtm_dd_2025", "mtm_dd_2026"]
        if column in data.columns
    ]

    if dd_columns:
        data["ranking_drawdown"] = (
            data[dd_columns]
            .apply(pd.to_numeric, errors="coerce")
            .abs()
            .max(axis=1)
        )
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
        + 0.450 * data["drawdown_score"]
    )

    data = data[data["entry_robust_side"] != "neither_side"].copy()
    data = data.sort_values(
        ["final_score", "net_pnl", "ranking_drawdown"],
        ascending=[False, False, True],
    ).reset_index(drop=True)

    data.insert(0, "rank", np.arange(1, len(data) + 1))
    return data


def structure_top20(data):
    structured = data.copy()
    structured["entry_robust_side"] = structured[
        "entry_robust_side"
    ].replace(
        {
            "prior_side": "before",
            "after_side": "after",
            "both_sides": "both",
        }
    )

    columns = [
        "rank",
        "monitor_start",
        "monitor_premium",
        "individual_decay_trigger_pct",
        "lot1_entry_premium",
        "lot1_profit_to_lot2_pct",
        "lot2_entry_premium",
        "lot2_profit_to_lot3_pct",
        "lot3_entry_premium",
        "campaign_sl_rs",
        "campaign_sl_reversal",
        "exit_time",
        "status",
        "campaign_count",
        "lot2_activation_count",
        "lot3_activation_count",
        "campaign_sl_count",
        "reversal_count",
        "max_campaign_loss",
        "win_rate",
        "max_consecutive_losses",
        "entry_robust_side",
        "net_pnl",
        "pnl_2025",
        "pnl_2026",
        "mtm_dd_2025",
        "mtm_dd_2026",
    ]

    for column in columns:
        if column not in structured.columns:
            structured[column] = pd.NA

    top20 = structured[columns].head(20).copy()
    top20.to_csv(TOP20_CSV, index=False)
    return top20


def main():
    input_file = find_input_file()

    print("Input file:")
    print(input_file)
    print()
    print(
        "Calculating robustness using "
        "-10,-20,-30,-40,-50 and +10,+20,+30,+40,+50 minutes..."
    )

    qualified = load_qualified_rows(input_file)
    ranked = rank_strategies(qualified)
    top20 = structure_top20(ranked)

    ranked.to_parquet(RANKED_PARQUET, index=False)
    ranked.head(100).to_csv(TOP100_CSV, index=False)

    print()
    print("Eligible rows:", len(ranked))
    print("Ranked Parquet:", RANKED_PARQUET)
    print("Top 100 CSV:", TOP100_CSV)
    print("Structured Top 20 CSV:", TOP20_CSV)
    print()
    display(top20)


if __name__ == "__main__":
    main()
