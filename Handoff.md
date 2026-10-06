# SENSEX Backtest, Sweep, Ranking, and Dashboard Handoff

Updated: 25 September 2026  
Workspace: `C:\Users\HELLO!\Documents\ChatGPT\Analytics DashBoard`

## 1. Project purpose

This project evaluates large numbers of SENSEX DTE-0 options strategy
combinations. The workflow is split into four stages:

1. Load and validate historical SENSEX option-chain data.
2. Run a strategy backtest or exhaustive parameter sweep.
3. Persist every combination and its metrics in Parquet, Delta, or DuckDB-compatible output.
4. Apply transparent filters, rank candidates, and create a small structured review table.

The sweep stage must not silently choose a winner. It should calculate and save
all requested combinations first. Selection is a separate analytical step.

The current output is screening output. It must not be described as AlgoTest
equivalent until at least one shortlisted combination has been compared with an
AlgoTest export event by event.

## 2. Required operating rules

Read `AGENTS.md` before changing SENSEX execution, sweep, ranking, or AlgoTest
comparison code. The required reconciliation document is:

```text
D:\Backend\static\admin\images\SENSEX_ALGOTEST_EXECUTION_RECONCILIATION.md
```

The current reconciled assumptions are:

- A scheduled first entry uses the previous completed candle as its reference.
- Closest-premium strike selection uses the execution reference candle.
- A same-timestamp combined re-entry uses the current candle/current ATM.
- A short-option leg stop-loss trigger uses the candle high.
- A leg stop-loss fills at the stop threshold, not at candle close.
- Combined stop-loss and target exits close remaining legs at the current candle close.
- Same-timestamp combined re-entry is allowed when re-entry is available.
- Net AlgoTest comparison uses 0.5% slippage: short entry is adjusted down and short exit is adjusted up.
- Use the platform-matched exit bar, preferably 15:14 when that is the matched bar, instead of blindly assuming 15:15.
- Compare only common 0DTE dates available in both datasets.

Do not claim parity from headline P&L alone. Compare selected strikes,
timestamps, prices, exit reasons, re-entries, daily P&L, total P&L, trade count,
and win rate.

## 3. Repository map

### Backtest and sweep

- `databricks_sensex_exhaustive_sweep.py` — primary Databricks exhaustive sweep implementation.
- `databricks_sensex_exhaustive_sweep_combined_and_leg.py` — related combined-and-leg implementation; use only when explicitly required.
- `nifty_protected_straddle_colab_CLEAN.py` — older/general Colab strategy file, not the primary SENSEX sweep source.
- `strategies/` — dashboard-compatible or standalone strategy modules.

### Ranking and selection

- `sensex_yearly_selection_sorter.py` — main DuckDB-based yearly sorter with dynamic yearly fields, entry-time filtering, ranking, and clean CSV output.
- `kaggle_sensex_sort_results.py` — earlier Kaggle-oriented sorter; check its default path before reuse.
- `databricks_sensex_sort_results.py` — Spark/PySpark ranking implementation for Databricks tables.
- `rank_best_pnl_with_risk_floor.py` — highest-P&L ranking with drawdown and 23% leg-SL limits.
- `rank_best_pnl_with_dd_floor.py` — highest-P&L ranking with a drawdown limit only.
- `rank_by_pnl_only.py` — simple P&L-only ranking.
- `structure_sensex_output.py` — creates a clean top-N table from sorter CSV output.

### Documentation and validation

- `AGENTS.md` — project instructions and SENSEX reconciliation rules.
- `ARCHITECTURE.md` — local dashboard architecture.
- `README.md` — local dashboard setup and usage.
- `LIMITATIONS.md` — known dashboard limitations.
- `integration/STRATEGY_SCRIPT_CONTRACT.md` — Strategy Contract v2.
- `integration/EQUITY_SNAPSHOT_CONTRACT.md` — equity snapshot requirements.
- `integration/SWEEP_SUMMARY_CONTRACT.md` — sweep output requirements.
- `tests/test_sensex_selection.py` — selection tests.
- `tests/test_sensex_precomputed_selection.py` — precomputed-output tests.

Generated examples include `sensex_yearly_ranked.parquet`,
`sensex_yearly_top100.csv`, `sensex_yearly_structured_top20.csv`, and their
`*_verified`/`*_new` variants. These are outputs, not source code.

## 4. End-to-end data flow

```text
Historical option-chain Parquet
        |
        v
Databricks/Spark or local strategy engine
        |
        v
Raw sweep result: one row per parameter combination
        |
        v
Yearly enrichment: pnl_YYYY, roi_YYYY_pct, mtm_dd_YYYY
        |
        v
DuckDB/Spark screening and ranking
        |
        +--> ranked Parquet with analytical fields
        +--> clean top-100 CSV
        +--> clean structured top-20 CSV
        |
        v
Shortlist review and AlgoTest-equivalent rerun
```

Raw results should retain strategy parameters and audit metrics. The clean CSV
is intentionally smaller and excludes internal score fields unless requested.

## 5. Dataset and coverage

The local market data is under:

```text
data\db\sensex current week\
```

Expected layout:

```text
sensex_summary.parquet
sensex_chain\part_YYYY_MM.parquet
```

The local dataset contains monthly chain files for 2024, 2025, and 2026, but
each year may be partial. Always inspect the manifest and filenames before
reporting the number of months.

An older result file, `sensex_full_sweep_with_yearly.parquet`, contained
897,750 rows and was not the full planned Cartesian sweep. It included yearly
fields such as `pnl_2024`, `pnl_2025`, `pnl_2026`, `mtm_dd_2024`,
`mtm_dd_2025`, and `mtm_dd_2026`. Do not call a result from that file the
winner of a newer sweep.

## 6. Planned exhaustive sweep grid

The full requested grid in the primary Databricks sweep is:

| Parameter | Values |
|---|---:|
| Premium target | ₹200–₹400, step ₹25: 9 |
| Leg stop-loss | 12%–40%, step 2%: 15 |
| Combined maximum loss | ₹500–₹2,000, step ₹250: 7 |
| Combined target | ₹1,000–₹5,000, step ₹500: 9 |
| Leg-SL re-entries | 0–4: 5 |
| Combined-SL re-entries | 0–4: 5 |
| Combined-target re-entries | 0–4: 5 |
| Entry time | 09:30–11:00, step 5 minutes: 19 |

Expected Cartesian count:

```text
9 × 15 × 7 × 9 × 5 × 5 × 5 × 19 = 20,199,375 combinations
```

The sweep must stream batches to storage. Do not collect all combinations in
driver memory.

Some older sweep assumptions include lot size 20, exit at the first bar at or
after 15:15, fixed 1% entry/exit slippage, and zero fees. These are not enough
to establish AlgoTest parity; confirm them before using the results.

## 7. Result schema

Common strategy parameters:

```text
premium_price
leg_sl_pct
combined_max_loss_rs
combined_target_rs
leg_sl_reentries
combined_max_loss_reentries
combined_target_reentries
entry_start
combination_index
```

Older files may not contain `leg_sl_reentries`; do not invent it when it was
not part of the executed sweep.

Core metrics:

```text
status
completed_trade_count
net_pnl
overall_roi_pct
max_drawdown
mtm_drawdown
max_loss
max_consecutive_losses
win_rate
```

If `net_pnl` is missing, the sorter can use `pnl`, `total_pnl`, `net_profit`,
`total_net_pnl`, or `gross_pnl`. If no alias exists, it derives total P&L from
the available yearly P&L columns.

If `max_drawdown` is missing and `mtm_drawdown` exists, the sorter uses
`mtm_drawdown`. Otherwise it derives a worst drawdown from yearly
`mtm_dd_YYYY` fields.

Year-on-year fields must contain values, not only headers:

```text
pnl_2024, pnl_2025, pnl_2026
roi_2024_pct, roi_2025_pct, roi_2026_pct
mtm_dd_2024, mtm_dd_2025, mtm_dd_2026
```

The scripts detect years dynamically. A file containing only `pnl_2026` and
`mtm_dd_2026` is supported, but missing years cannot be reconstructed by the
sorter.

## 8. Main sorter

Run in Kaggle:

```python
!pip install -q duckdb
%run /kaggle/working/sensex_yearly_selection_sorter.py
```

Current default input:

```text
/kaggle/input/datasets/joyal126457/23123as
```

Default outputs:

```text
/kaggle/working/sensex_8lac_ranked.parquet
/kaggle/working/sensex_8lac_top100.csv
/kaggle/working/sensex_8lac_structured_top20.csv
/kaggle/working/sensex_8lac_sort.duckdb
```

The sorter searches recursively for Parquet and prefers filenames containing
`yearly`. If multiple files exist, verify the printed selected path. Use a
direct file path when there is ambiguity.

## 9. Hard filters and entry-time robustness

The main sorter requires:

- `status = 'succeeded'`;
- at least one completed trade;
- positive `net_pnl`;
- non-null win rate, drawdown, maximum loss, and consecutive-loss fields;
- a complete earlier or later entry-time side;
- every one of the five variants on that side to retain at least 70% of the
  candidate P&L.

The default ROI basis is ₹300,000 and the default minimum ROI is 15%.

For each candidate, record its total P&L as `P`. The earlier side tests
`entry_start - 5`, `-10`, `-15`, `-20`, and `-25` minutes. The later side tests
`entry_start + 5`, `+10`, `+15`, `+20`, and `+25` minutes. All non-time
parameters remain identical. For either side to pass:

```text
required nearby P&L = net_pnl × 0.70
```

Every one of the five side-specific P&L values must individually meet the
threshold. Do not average the five results. The candidate is removed only when
neither complete side passes.
Entry-time stability is a hard filter only. It is not a score and does not
receive ranking weight.

The output label means:

- `both_sides` — prior and after windows pass;
- `prior_side` — only the prior window passes;
- `after_side` — only the after window passes;
- `neither_side` — neither passes.

`entry_robust_side` is descriptive. A row can show `both_sides` and still have
`passes_strict_screen = False` because that flag also checks ROI and yearly P&L
positivity.

## 10. Current ranking model

After the hard eligibility filters, the main sorter calculates percentile-based
components for:

- recent-year P&L;
- worst-year P&L;
- total P&L;
- bracket P&L ratio;
- inverse drawdown;
- inverse maximum loss;
- inverse consecutive losses;
- lower total re-entry burden.

Risk score:

```text
risk_score = 60% drawdown score
           + 24% maximum-loss score
           + 16% consecutive-loss score
```

Current final score:

```text
final_score = 18% recent-year score
            + 15% bracket robustness score
            + 5% worst-year score
            + 25% risk score
            + 30% total P&L score
            + 7% re-entry score
```

The displayed components total 100%. The effective weights are 15% drawdown,
6% maximum loss, and 4% consecutive losses within the 25% risk score. The
re-entry score rewards a lower sum of
the available leg-SL, combined-SL, and combined-target re-entry counts. It is a
ranking preference, not a hard rejection filter.

Tie-break order:

1. Higher final score.
2. Higher worst-year P&L.
3. Higher bracket P&L ratio.
4. Lower drawdown risk.
5. Lower maximum-loss risk.
6. Higher total P&L.
7. Lower generated combination index.

## 11. Simpler selection variants

Use `rank_by_pnl_only.py` when the requirement is explicitly “sort by P&L” and
no composite score or robustness selection is wanted.

Use `rank_best_pnl_with_risk_floor.py` when the requirement is:

- rank by highest P&L;
- reject drawdown above 2% of ₹300,000;
- reject `leg_sl_pct` above 23%.

The drawdown limit is:

```text
₹300,000 × 2% = ₹6,000
```

The 23% limit is 23% of the option entry premium because it filters the
`leg_sl_pct` parameter. It is not 23% of account capital.

## 12. Structuring output

Run after the sorter has created its CSV:

```python
%run /kaggle/working/structure_sensex_output.py
```

Default paths:

```text
Input:  /kaggle/working/sensex_8lac_top100.csv
Output: /kaggle/working/sensex_8lac_structured_top20.csv
```

The structurer detects and preserves populated yearly P&L/DD columns, derives
`net_pnl` only when absent, derives recent/worst-year P&L when absent, preserves
existing rank order, and prints/saves the top N rows.

If yearly fields are `<NA>`, the wrong Parquet was selected or the source file
never contained yearly values. The structurer cannot reconstruct missing
historical values from a one-year file.

## 13. Recommended Kaggle procedure

1. Attach the intended dataset in Kaggle.
2. Confirm the mounted path in the Input panel.
3. Inspect the Parquet files and schema before sorting:

   ```python
   import pandas as pd
   from pathlib import Path

   root = Path('/kaggle/input/datasets/joyal126457/23123as')
   files = list(root.rglob('*.parquet'))
   print(files)
   sample = pd.read_parquet(files[0])
   print(sample.columns.tolist())
   print(sample.shape)
   ```

4. Confirm `pnl_YYYY` and `mtm_dd_YYYY` columns contain values.
5. Run the sorter and verify the printed input path and row counts.
6. Inspect `net_pnl`, yearly P&L, yearly DD, the five earlier/later side
   results, `entry_robust_side`, and
   `passes_strict_screen`.
7. Run the structurer only after sorter output exists.
8. Review a shortlist using the ranked Parquet, not only the first display.
9. Rerun shortlisted combinations using reconciled execution logic before
   treating any one as the final strategy.

Notebook-injected `-f kernel.json` arguments are ignored through
`parse_known_args`. If code is pasted directly into a notebook cell, paste the
complete function body; an incomplete `def` causes the earlier indentation and
syntax errors.

## 14. Common failures

### Windows path not found in Kaggle

Paths such as `D:/Backend/...` exist only on the local Windows machine. Kaggle
must use its mounted path, for example:

```text
/kaggle/input/datasets/joyal126457/23123as
```

### Missing `max_drawdown`

Some files use `mtm_drawdown`. The sorter supports that fallback. If neither
exists, yearly `mtm_dd_YYYY` fields are required for derivation.

### Missing yearly values

The selected input is not the yearly Parquet, or the yearly enrichment stage
did not run. Point the sorter to the correct file and verify the schema first.

### Zero eligible rows

Check `status`, completed trades, positive P&L, all five timestamps on a side,
and the 70% per-variant P&L requirement.

### `both_sides` but `passes_strict_screen = False`

This is expected when entry-time robustness passes but ROI or yearly P&L fails.
The two columns represent different checks.

### P&L absent in structured output

The clean field is `net_pnl`; yearly fields are `pnl_2024`, `pnl_2025`, and
`pnl_2026` when available. Inspect the sorter CSV header and source schema
instead of adding empty column names.

## 15. AlgoTest parity work still required

Before a production decision:

1. Verify entry timestamp mapping against the AlgoTest candle convention.
2. Load option OHLC high/low fields for leg stop-loss behavior.
3. Implement exact individual-leg re-entry while the opposite leg remains open.
4. Match same-candle re-entry timing.
5. Produce cycle/day-level combined metrics as well as raw leg metrics.
6. Match slippage, fees, lot size, expiry selection, ATM calculation, and
   closest-premium tie-breaking.
7. Compare one parameter combination day by day against an AlgoTest export.
8. Run the broad reconciled sweep only after the single-combination comparison
   agrees.

Debug the first differing execution event. Do not tune ranking to compensate
for an execution-model mismatch.

## 16. Safe handoff checklist

Before changing or rerunning this project:

- Read `AGENTS.md` and the reconciliation document.
- Identify the exact source Parquet and record its schema.
- Record date coverage and common 0DTE dates.
- Record the parameter grid and expected combination count.
- State whether the result is screening or reconciled output.
- Keep raw generation separate from ranking.
- Confirm yearly P&L/DD values are populated before structuring.
- Keep the 70% five-variant entry-time rule as a filter only.
- Do not calculate or rank an entry-stability score.
- Preserve `net_pnl` and yearly P&L values in the review CSV.
- Keep deterministic tie-break ordering.
- Rerun shortlisted combinations under reconciled execution rules.
- Never call a provisional screening winner final without AlgoTest comparison.

## 17. Next owner actions

1. Inspect the current Kaggle input and verify whether it contains 2024, 2025,
   and 2026 values.
2. Run the P&L/risk-floor script to establish a transparent baseline.
3. Run the full yearly sorter and inspect why high-P&L rows fail strict screen.
4. Add a failure-reason field if row-level rejection explanations are needed.
5. Select a shortlist instead of relying on one row.
6. Rerun the shortlist with AlgoTest-matched execution and compare daily logs.
7. Update this handoff with the final input filename, actual date coverage,
   selected parameters, and validation evidence.
