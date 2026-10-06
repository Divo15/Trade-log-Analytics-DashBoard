# SENSEX Structured Top-20 Output Specification

Use this specification whenever the user asks for the SENSEX structured Top-20 combinations.

## Input

Read the ranked Top-100 CSV, normally:

```text
/kaggle/working/sensex_8lac_top100.csv
```

## Output

Write:

```text
/kaggle/working/sensex_8lac_structured_top20.csv
```

Display the result as a proper notebook table using `display(dataframe)`, not `to_string()`, so each combination remains one row.

## Required columns and order

### Ranking

```text
rank
```

### Strategy parameters

```text
premium_price
leg_sl_pct
leg_tp_pct
combined_max_loss_rs
combined_target_rs
combined_max_loss_reentries
combined_target_reentries
leg_sl_reentries
leg_tp_reentries
entry_start
exit_time
status
completed_trade_count
```

### Entry robustness

```text
entry_robust_side
```

Convert the values as follows:

```text
prior_side   -> before
after_side   -> after
both_sides   -> both
earlier_side -> before
later_side   -> after
```

### Performance

```text
net_pnl
pnl_2025
pnl_2026
mtm_dd_2025
mtm_dd_2026
```

## Exclude these fields

Do not include internal or ranking fields:

```text
entry_ts
final_score
pnl_score
drawdown_score
robustness_score
recent_pnl_score
streak_score
reentry_score
risk_score
prior_robust_ratio
after_robust_ratio
robustness_ratio
recent_year_pnl
worst_year_pnl
ranking_drawdown
reentry_burden
```

## Output rules

1. Preserve the existing `rank` order.
2. Select only the first 20 rows.
3. Keep one strategy combination per row.
4. Keep year-on-year P&L and year-on-year drawdown.
5. Include `entry_robust_side` as `before`, `after`, or `both`.
6. Save the CSV without the Pandas index.
