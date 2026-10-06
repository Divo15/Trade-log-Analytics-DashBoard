# Layered-Decay SENSEX Ranking Strategy

## Purpose

This system evaluates the layered-decay SENSEX sweep and selects combinations that balance yearly P&L with lower drawdown.

## Eligibility filters

A base combination must satisfy:

```text
status = succeeded
campaign_count > 0
net_pnl > 0
monitor_start <= 10:00
```

The time limit applies to the base strategy. Later timestamps remain available for robustness testing.

## Entry-time robustness

The original strategy P&L is called `P`. All other strategy settings remain unchanged while `monitor_start` is shifted.

Earlier variants:

```text
-5, -10, -15, -20, -25 minutes
```

Later variants:

```text
+5, +10, +15, +20, +25 minutes
```

Every tested variant must individually produce at least `70% of P`.

The strategy passes if:

```text
all five earlier variants pass
OR
all five later variants pass
```

The results are not averaged. If one timestamp fails, that complete side fails. Both sides are not required.

Robustness labels are:

```text
before = all five earlier variants passed
after  = all five later variants passed
both   = all ten variants passed
```

Entry robustness is a hard filter only and contributes 0% to the ranking score.

## Ranking weights

Only combinations that pass all filters are ranked.

| Component | Weight | Direction |
|---|---:|---|
| 2025 P&L | 27.5% | Higher is better |
| 2026 P&L | 27.5% | Higher is better |
| Low drawdown | 45% | Lower is better |
| Win-to-loss ratio | 0% | Not ranked |
| Entry robustness score | 0% | Filter only |
| Re-entry burden | 0% | Not ranked |
| Consecutive losses | 0% | Not ranked |
| Worst-year P&L | 0% | Not ranked |
| Maximum-loss score | 0% | Not ranked |
| **Total** | **100%** | |

## Final score

```text
final_score =
    0.275 × pnl_2025_score
  + 0.275 × pnl_2026_score
  + 0.45  × drawdown_score
```

The P&L and drawdown scores are percentile scores among eligible combinations.

Higher yearly P&L receives a higher score. Lower drawdown receives a higher score.

## Yearly P&L scoring

The script scores the available yearly results separately:

```text
pnl_2025_score = percentile rank of pnl_2025
pnl_2026_score = percentile rank of pnl_2026
```

Equal weighting is used for the two available years so that one year does not dominate the result.

## Drawdown scoring

The ranking drawdown is the worst absolute yearly drawdown:

```text
ranking_drawdown = max(
    abs(mtm_dd_2024),
    abs(mtm_dd_2025),
    abs(mtm_dd_2026)
)
```

If only a subset of yearly drawdown columns exists, the maximum is calculated from the available columns. Lower drawdown receives a higher score.

## Final sorting

The ranked result is sorted by:

```text
1. final_score descending
2. net_pnl descending
3. ranking_drawdown ascending
```

## Structured Top-20 fields

The clean Top-20 output keeps one combination per row and contains these fields when available:

```text
rank
monitor_start
strike_selection_type
strike_selection_value
signal_type
signal_value_pct
pullback_value_pct
lot2_activation_profit_pct
lot3_activation_profit_pct
campaign_sl_rs
opposite_entry_mode
portfolio_sl_rs
exit_time
status
campaign_count
entry_robust_side
net_pnl
pnl_2024
pnl_2025
pnl_2026
mtm_dd_2024
mtm_dd_2025
mtm_dd_2026
```

Internal score fields and robustness calculation fields are excluded from the clean structured output.

## Output files

```text
/kaggle/working/sensex_layered_decay_ranked.parquet
/kaggle/working/sensex_layered_decay_top100.csv
/kaggle/working/sensex_layered_decay_structured_top20.csv
```

## Interpretation cautions

This is a screening and ranking system, not a guarantee of future profitability.

Before using a shortlisted combination, review yearly P&L, yearly drawdown, the robustness side that passed, execution assumptions, slippage, costs, stop fills, target fills, re-entry behavior, and exit timing.
