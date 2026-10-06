from pathlib import Path
import duckdb
import pandas as pd
import numpy as np


INPUT_DIR = Path(
    "/kaggle/input/datasets/joyal126457/123456"
)

RANKED_PARQUET = Path(
    "/kaggle/working/sensex_layered_decay_ranked.parquet"
)

TOP100_CSV = Path(
    "/kaggle/working/sensex_layered_decay_top100.csv"
)

TOP20_CSV = Path(
    "/kaggle/working/sensex_layered_decay_structured_top20.csv"
)

ROBUSTNESS_LIMIT = 0.70

EARLIER_OFFSETS = [-5, -10, -15, -20, -25]
LATER_OFFSETS = [5, 10, 15, 20, 25]

STRATEGY_FIELDS = [
    "strike_selection_type",
    "strike_selection_value",
    "signal_type",
    "signal_value_pct",
    "pullback_value_pct",
    "lot2_activation_profit_pct",
    "lot3_activation_profit_pct",
    "campaign_sl_rs",
    "opposite_entry_mode",
    "portfolio_sl_rs",
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


def find_input():
    files = sorted(
        INPUT_DIR.rglob("sensex_layered_decay_campaign_sweep*.parquet")
    ) if INPUT_DIR.exists() else []

    if not files:
        files = sorted(
            Path("/kaggle/input").rglob(
                "sensex_layered_decay_campaign_sweep*.parquet"
            )
        )

    if not files:
        raise FileNotFoundError(
            "Layered-decay Parquet was not found under /kaggle/input."
        )

    return str(files[0])


def pct_score(series, higher=True):
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
    base_select = ",\n            ".join(qi(x) for x in base_fields)

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE lookup AS
        SELECT
            {base_select},
            try_strptime(CAST(monitor_start AS VARCHAR), '%H:%M:%S') AS entry_ts
        FROM read_parquet(?)
        WHERE lower(CAST(status AS VARCHAR)) = 'succeeded'
          AND COALESCE(campaign_count, 0) > 0
          AND net_pnl > 0
          AND (
              CAST(split_part(monitor_start, ':', 1) AS INTEGER) * 60
              + CAST(split_part(monitor_start, ':', 2) AS INTEGER)
          ) <= 625
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
            f"prior_pnl_{minutes}"
            if offset < 0
            else f"after_pnl_{minutes}"
        )
        selects.append(
            f"{name}.net_pnl AS {qi(output_name)}"
        )

    earlier_sql = [f"v_m{x}.net_pnl" for x in [5, 10, 15, 20, 25]]
    later_sql = [f"v_p{x}.net_pnl" for x in [5, 10, 15, 20, 25]]

    earlier_present = " AND ".join(
        f"{x} IS NOT NULL" for x in earlier_sql
    )
    later_present = " AND ".join(
        f"{x} IS NOT NULL" for x in later_sql
    )

    query = f"""
    WITH candidates AS (
        SELECT *
        FROM lookup
        WHERE (
            CAST(split_part(monitor_start, ':', 1) AS INTEGER) * 60
            + CAST(split_part(monitor_start, ':', 2) AS INTEGER)
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
           qualified.prior_pnl_5, qualified.prior_pnl_10,
           qualified.prior_pnl_15, qualified.prior_pnl_20,
           qualified.prior_pnl_25, qualified.after_pnl_5,
           qualified.after_pnl_10, qualified.after_pnl_15,
           qualified.after_pnl_20, qualified.after_pnl_25,
           qualified.prior_robust_ratio,
           qualified.after_robust_ratio
    FROM read_parquet(?) original
    INNER JOIN qualified
      ON {join_condition('original', 'qualified')}
     AND original.monitor_start IS NOT DISTINCT FROM qualified.monitor_start
    WHERE lower(CAST(original.status AS VARCHAR)) = 'succeeded'
      AND COALESCE(original.campaign_count, 0) > 0
      AND original.net_pnl > 0
      AND (
          CAST(split_part(original.monitor_start, ':', 1) AS INTEGER) * 60
          + CAST(split_part(original.monitor_start, ':', 2) AS INTEGER)
      ) <= 600
    """

    # The final query contains one read_parquet(?) placeholder.
    data = con.execute(query, [input_file]).fetchdf()
    con.close()

    if data.empty:
        raise ValueError(
            "No layered-decay strategy passed the 70% one-sided robustness rule."
        )

    return data


def rank(data):
    prior = data[[f"prior_pnl_{x}" for x in [5, 10, 15, 20, 25]]].apply(pd.to_numeric, errors="coerce")
    after = data[[f"after_pnl_{x}" for x in [5, 10, 15, 20, 25]]].apply(pd.to_numeric, errors="coerce")
    base = pd.to_numeric(data["net_pnl"], errors="coerce")

    prior_ratio = prior.min(axis=1) / base
    after_ratio = after.min(axis=1) / base
    prior_pass = (~prior.isna().any(axis=1)) & (prior_ratio >= ROBUSTNESS_LIMIT)
    after_pass = (~after.isna().any(axis=1)) & (after_ratio >= ROBUSTNESS_LIMIT)

    data["entry_robust_side"] = np.select(
        [prior_pass & after_pass, prior_pass, after_pass],
        ["both_sides", "prior_side", "after_side"],
        default="neither_side",
    )

    data["pnl_2025_score"] = pct_score(data["pnl_2025"])
    data["pnl_2026_score"] = pct_score(data["pnl_2026"])

    # Exclude 2024 from ranking. Drawdown is calculated only from 2025/2026.
    dd_columns = [x for x in ["mtm_dd_2025", "mtm_dd_2026"] if x in data.columns]
    if dd_columns:
        data["ranking_drawdown"] = data[dd_columns].apply(pd.to_numeric, errors="coerce").abs().max(axis=1)
    else:
        data["ranking_drawdown"] = pd.to_numeric(data["mtm_drawdown"], errors="coerce").abs()

    data["drawdown_score"] = pct_score(data["ranking_drawdown"], higher=False)

    win_rate = pd.to_numeric(data["win_rate"], errors="coerce")
    win_rate = win_rate.where(win_rate <= 1, win_rate / 100.0)
    data["win_loss_ratio"] = (win_rate / (1 - win_rate)).replace([np.inf, -np.inf], np.nan)
    data["win_loss_score"] = pct_score(data["win_loss_ratio"])

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
        "rank", "monitor_start", "strike_selection_type",
        "strike_selection_value", "signal_type", "signal_value_pct",
        "pullback_value_pct", "lot2_activation_profit_pct",
        "lot3_activation_profit_pct", "campaign_sl_rs",
        "opposite_entry_mode", "portfolio_sl_rs", "exit_time",
        "status", "campaign_count", "entry_robust_side", "net_pnl",
        "pnl_2025", "pnl_2026", "mtm_dd_2025", "mtm_dd_2026",
    ]

    for column in columns:
        if column not in data.columns:
            data[column] = pd.NA

    top20 = data[columns].head(20).copy()
    top20.to_csv(TOP20_CSV, index=False)
    return top20


def main():
    input_file = find_input()
    print("Input:", input_file)

    data = load_qualified_rows(input_file)
    ranked = rank(data)

    ranked.to_parquet(RANKED_PARQUET, index=False)
    ranked.head(100).to_csv(TOP100_CSV, index=False)
    top20 = structure(ranked)

    print("Eligible rows:", len(ranked))
    print("Ranked output:", RANKED_PARQUET)
    print("Top 100:", TOP100_CSV)
    print("Top 20:", TOP20_CSV)
    print()
    display(top20)


if __name__ == "__main__":
    main()
