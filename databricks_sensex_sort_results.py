"""Post-process and rank the completed SENSEX Databricks sweep results.

This module is intentionally separate from the strategy/sweep generator. It
adds the agreed quartile scores and applies the agreed entry-time robustness
rule: all five exact earlier or all five exact later entry times must each
retain at least 70% of the candidate's net P&L. All non-time parameters must
match exactly.
"""


GROUP_PARAMETER_COLUMNS = (
    "premium_price",
    "leg_sl_pct",
    "combined_max_loss_rs",
    "combined_target_rs",
    "leg_sl_reentries",
    "combined_max_loss_reentries",
    "combined_target_reentries",
)

HIGHER_IS_BETTER_COLUMNS = (
    "net_pnl",
    "win_rate",
    "completed_trade_count",
)

LOWER_IS_BETTER_COLUMNS = (
    "drawdown_risk",
    "max_loss_risk",
    "max_consecutive_losses",
)

SCORE_COLUMNS = (
    "net_pnl_score",
    "win_rate_score",
    "completed_trade_count_score",
    "drawdown_risk_score",
    "max_loss_risk_score",
    "max_consecutive_losses_score",
    "reentry_burden_score",
)


def rank_sensex_sweep_results(
    spark,
    input_table="workspace.default.sensex_screening_results_all",
    output_table="workspace.default.sensex_screening_results_ranked",
    bracket_minutes=25,
    minimum_pnl_ratio=0.70,
    quantile_relative_error=0.0,
):
    """Apply robustness filtering, quartile scoring, and deterministic sorting.

    Quartiles are calculated across all profitable, successful combinations.
    The +/- time-bracket condition is then applied before selecting the top row.
    ``quantile_relative_error=0.0`` requests exact Spark quantiles.
    """
    if int(bracket_minutes) != 25:
        raise ValueError("bracket_minutes must be exactly 25 for the five-point rule")
    if not 0.0 <= float(minimum_pnl_ratio) <= 1.0:
        raise ValueError("minimum_pnl_ratio must be between 0 and 1")

    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    source = spark.table(input_table)
    required_columns = {
        "combination_index", "entry_start", "status", "completed_trade_count",
        "net_pnl", "win_rate", "max_drawdown", "max_loss",
        "max_consecutive_losses", *GROUP_PARAMETER_COLUMNS,
    }
    missing = sorted(required_columns - set(source.columns))
    if missing:
        raise ValueError(f"Input table is missing required columns: {missing}")

    time_parts = F.split(F.col("entry_start"), ":")
    with_time = source.withColumn(
        "entry_minutes",
        time_parts.getItem(0).cast("int") * F.lit(60)
        + time_parts.getItem(1).cast("int"),
    )

    bracket_window = (
        Window.partitionBy(*GROUP_PARAMETER_COLUMNS)
        .orderBy(F.col("entry_minutes"))
        .rangeBetween(-25, 25)
    )
    prior_window = (
        Window.partitionBy(*GROUP_PARAMETER_COLUMNS)
        .orderBy(F.col("entry_minutes"))
        .rangeBetween(-25, -5)
    )
    after_window = (
        Window.partitionBy(*GROUP_PARAMETER_COLUMNS)
        .orderBy(F.col("entry_minutes"))
        .rangeBetween(5, 25)
    )

    # Each side is evaluated independently; no side average is used.
    with_bracket = (
        with_time
        .withColumn("bracket_min_pnl", F.min("net_pnl").over(bracket_window))
        .withColumn("bracket_combination_count", F.count(F.lit(1)).over(bracket_window))
        .withColumn("bracket_neighbor_count", F.col("bracket_combination_count") - F.lit(1))
        .withColumn("prior_bracket_min_pnl", F.min("net_pnl").over(prior_window))
        .withColumn("prior_bracket_count", F.count(F.lit(1)).over(prior_window))
        .withColumn("after_bracket_min_pnl", F.min("net_pnl").over(after_window))
        .withColumn("after_bracket_count", F.count(F.lit(1)).over(after_window))
        .withColumn(
            "expected_bracket_combination_count",
            (
                (
                    F.least(F.lit(11 * 60), F.col("entry_minutes") + F.lit(int(bracket_minutes)))
                    - F.greatest(F.lit(9 * 60 + 30), F.col("entry_minutes") - F.lit(int(bracket_minutes)))
                ) / F.lit(5)
                + F.lit(1)
            ).cast("int"),
        )
        .withColumn(
            "required_bracket_pnl",
            F.col("net_pnl") * F.lit(float(minimum_pnl_ratio)),
        )
        .withColumn(
            "bracket_pnl_ratio",
            F.greatest(
                F.col("prior_bracket_min_pnl") / F.col("net_pnl"),
                F.col("after_bracket_min_pnl") / F.col("net_pnl"),
            ),
        )
        .withColumn(
            "prior_robust_ratio",
            F.col("prior_bracket_min_pnl") / F.col("net_pnl"),
        )
        .withColumn(
            "after_robust_ratio",
            F.col("after_bracket_min_pnl") / F.col("net_pnl"),
        )
        .withColumn(
            "entry_robust_side",
            F.when(
                (F.col("prior_bracket_count") == 5)
                & (F.col("prior_bracket_min_pnl") >= F.col("required_bracket_pnl"))
                & (F.col("after_bracket_count") == 5)
                & (F.col("after_bracket_min_pnl") >= F.col("required_bracket_pnl")),
                "both_sides",
            ).when(
                (F.col("prior_bracket_count") == 5)
                & (F.col("prior_bracket_min_pnl") >= F.col("required_bracket_pnl")),
                "prior_side",
            ).when(
                (F.col("after_bracket_count") == 5)
                & (F.col("after_bracket_min_pnl") >= F.col("required_bracket_pnl")),
                "after_side",
            ).otherwise("neither_side"),
        )
        .withColumn(
            "passes_entry_time_robustness",
            (F.col("entry_robust_side") != F.lit("neither_side")),
        )
        .withColumn(
            "reentry_burden",
            F.coalesce(F.col("leg_sl_reentries"), F.lit(0))
            + F.coalesce(F.col("combined_max_loss_reentries"), F.lit(0))
            + F.coalesce(F.col("combined_target_reentries"), F.lit(0)),
        )
    )

    candidates = (
        with_bracket
        .filter(F.col("status") == "succeeded")
        .filter(F.col("completed_trade_count") > 0)
        .filter(F.col("net_pnl") > 0)
        .withColumn("drawdown_risk", F.abs(F.col("max_drawdown")))
        .withColumn("max_loss_risk", F.abs(F.col("max_loss")))
        .dropna(subset=[
            "net_pnl", "win_rate", "completed_trade_count",
            "drawdown_risk", "max_loss_risk", "max_consecutive_losses",
            "bracket_min_pnl", "prior_bracket_min_pnl", "after_bracket_min_pnl",
        ])
    )

    metric_columns = list(HIGHER_IS_BETTER_COLUMNS + LOWER_IS_BETTER_COLUMNS)
    boundaries = candidates.approxQuantile(
        metric_columns,
        [0.25, 0.50, 0.75],
        float(quantile_relative_error),
    )

    def add_quartile_score(frame, metric_name, cuts, higher_is_better):
        if len(cuts) != 3:
            raise ValueError(f"Could not calculate quartiles for {metric_name}")
        q1, q2, q3 = (float(value) for value in cuts)
        quartile_column = f"{metric_name}_quartile"
        quartile = (
            F.when(F.col(metric_name) <= F.lit(q1), F.lit(1))
            .when(F.col(metric_name) <= F.lit(q2), F.lit(2))
            .when(F.col(metric_name) <= F.lit(q3), F.lit(3))
            .otherwise(F.lit(4))
        )
        frame = frame.withColumn(quartile_column, quartile)
        if higher_is_better:
            score = (
                F.when(F.col(quartile_column) == 4, F.lit(2))
                .when(F.col(quartile_column) == 3, F.lit(1))
                .when(F.col(quartile_column) == 2, F.lit(-1))
                .otherwise(F.lit(-2))
            )
        else:
            score = (
                F.when(F.col(quartile_column) == 1, F.lit(2))
                .when(F.col(quartile_column) == 2, F.lit(1))
                .when(F.col(quartile_column) == 3, F.lit(-1))
                .otherwise(F.lit(-2))
            )
        return frame.withColumn(f"{metric_name}_score", score)

    scored = candidates
    for metric_name, cuts in zip(metric_columns, boundaries):
        scored = add_quartile_score(
            scored,
            metric_name,
            cuts,
            higher_is_better=metric_name in HIGHER_IS_BETTER_COLUMNS,
        )

    reentry_cuts = candidates.approxQuantile(
        ["reentry_burden"], [0.25, 0.50, 0.75], float(quantile_relative_error)
    )[0]
    scored = add_quartile_score(
        scored, "reentry_burden", reentry_cuts, higher_is_better=False
    )

    total_score = F.lit(0)
    for score_column in SCORE_COLUMNS:
        total_score = total_score + F.col(score_column)

    qualified = (
        scored
        .withColumn("quartile_score", total_score)
        .filter(F.col("passes_entry_time_robustness"))
    )

    qualified.write.mode("overwrite").format("delta").option(
        "overwriteSchema", "true"
    ).saveAsTable(output_table)

    return spark.table(output_table).orderBy(
        F.desc("quartile_score"),
        F.desc("net_pnl"),
        F.asc("drawdown_risk"),
        F.asc("max_loss_risk"),
        F.desc("win_rate"),
        F.asc("max_consecutive_losses"),
        F.asc("combination_index"),
    )
