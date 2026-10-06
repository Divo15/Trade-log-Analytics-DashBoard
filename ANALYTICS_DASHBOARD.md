# Analytics Dashboard

## Purpose

The Analytics Dashboard evaluates SENSEX strategy sweep files, filters invalid or fragile combinations, ranks eligible strategies, and creates a clean Top-20 shortlist.

Main workflow:

`Parquet sweep -> eligibility filters -> entry robustness -> ranking -> Top 100 -> structured Top 20`

## Input data

The source is a Parquet file containing strategy parameters, entry times, completed trade counts, net P&L, yearly P&L, and drawdown fields.

Important strategy fields include `premium_price`, `leg_sl_pct`, `leg_tp_pct`, `combined_max_loss_rs`, `combined_target_rs`, `combined_max_loss_reentries`, `combined_target_reentries`, `leg_sl_reentries`, `leg_tp_reentries`, `entry_start`, and `exit_time`.

Important result fields include `status`, `completed_trade_count`, `net_pnl`, `pnl_2025`, `pnl_2026`, `mtm_dd_2025`, `mtm_dd_2026`, and `mtm_drawdown`.

## Eligibility filters

A base combination must satisfy:

```text
status = succeeded
completed_trade_count > 0
net_pnl > 0
entry_start <= 10:00
```

The 10:00 restriction applies to the base strategy. Later entry rows are retained internally so that a 10:00 strategy can still be tested for later-time robustness.

## Entry-time robustness filter

The base strategy P&L is called `P`. All other strategy settings remain unchanged while the entry time is moved.

Earlier offsets are:

```text
-2, -4, -6, -8, -10, -12, -14, -16, -18, -20 minutes
```

Later offsets are:

```text
+2, +4, +6, +8, +10, +12, +14, +16, +18, +20 minutes
```

Each tested result must individually be at least `0.70 × P`.

A strategy passes if all 10 earlier variants pass OR all 10 later variants pass. Results are not averaged. Both sides are not required.

Structured labels are:

```text
before = all 10 earlier variants passed
after  = all 10 later variants passed
both   = all 20 variants passed
```

Entry robustness is a mandatory filter only. It contributes 0% to the ranking score.

## Current ranking system

| Component | Weight | Direction |
|---|---:|---|
| 2025 P&L | 30% | Higher is better |
| 2026 P&L | 30% | Higher is better |
| Low drawdown | 40% | Lower is better |
| Entry robustness score | 0% | Filter only |
| Re-entry burden | 0% | Not ranked |
| Consecutive losses | 0% | Not ranked |
| Worst-year P&L | 0% | Not ranked |
| Maximum-loss score | 0% | Not ranked |

The final score is:

```text
final_score =
    0.30 × pnl_2025_score
  + 0.30 × pnl_2026_score
  + 0.40 × drawdown_score
```

Scores are percentile-based among eligible combinations. Higher yearly P&L receives a higher score. Lower drawdown receives a higher score.

The primary sort is `final_score` descending. Tie-breakers are `net_pnl` descending and drawdown ascending.

## Drawdown calculation

When yearly drawdown fields exist, ranking drawdown is:

```text
max(abs(mtm_dd_2025), abs(mtm_dd_2026))
```

This makes the score sensitive to the worst observed year-on-year drawdown.

## Output files

Typical outputs are:

```text
/kaggle/working/sensex_8lac_ranked.parquet
/kaggle/working/sensex_8lac_top100.csv
/kaggle/working/sensex_8lac_structured_top20.csv
```

The ranked Parquet contains the complete ranked result and calculation fields. The Top-100 CSV contains the first 100 combinations. The structured Top-20 CSV is the clean shortlist.

## Structured Top-20 fields

The clean file preserves rank order and keeps one strategy combination per row. Required fields, when available, are:

```text
rank
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
entry_robust_side
net_pnl
pnl_2025
pnl_2026
mtm_dd_2025
mtm_dd_2026
```

The structured file excludes internal fields such as `entry_ts`, `final_score`, `pnl_score`, `pnl_2025_score`, `pnl_2026_score`, `drawdown_score`, `robustness_score`, `recent_pnl_score`, `streak_score`, `reentry_score`, `risk_score`, `prior_robust_ratio`, `after_robust_ratio`, `robustness_ratio`, `recent_year_pnl`, `worst_year_pnl`, `ranking_drawdown`, and `reentry_burden`.

Display the clean table with `display(structured_top20)`. Avoid `to_string()` for the main display because wide tables may wrap and make one row appear split.

## Interpretation cautions

P&L is limited to the dates present in the input Parquet and is not automatically annualized.

Before comparing results with AlgoTest or using a combination live, verify that both systems use the same entry reference, strike selection, slippage, costs, stop fill, target fill, re-entry timing, and exit time.

The sweep is screening output. Shortlisted combinations should receive an execution-matched rerun before final approval.

## Review checklist

1. Confirm the input Parquet path.
2. Confirm yearly P&L and drawdown fields exist.
3. Confirm the base entry is no later than 10:00.
4. Confirm all required 2-minute robustness variants exist.
5. Confirm one complete robustness side passes the 70% rule.
6. Confirm the ranking is 30% 2025 P&L, 30% 2026 P&L, and 40% drawdown.
7. Confirm the structured Top 20 contains only approved fields.
8. Validate shortlisted combinations with an execution-matched rerun.
