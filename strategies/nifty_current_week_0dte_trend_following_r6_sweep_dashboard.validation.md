# R6 sweep dashboard integration

Source: `D:\Backend\static\admin\images\nifty_current_week_0dte_trend_following_r6_sweep.py`.
The source file is unchanged. Upload the sibling `_dashboard.py` copy to the
updated local dashboard and select **Weekly · current expiry**.

## Preserved strategy

- Same 144 Cartesian-product parameter combinations, in the same order.
- Maximum four simultaneous active slots, one lot per position, 65 units per lot
  (exported as quantity 1 with multiplier 65).
- Same D2/R6 direction calculation, strike selection, add-on triggers, re-entry,
  stops, profit booking, trailing exits, square-off and fill assumptions.
- Same capital, margin, execution overrides and default zero fees.
- No additional backend changes are required for this script on the updated
  local checkout. This does not install the backend updates on another computer.

## Integration changes

- Optional `context.report_progress` reports completed/total expiry days, enabling
  dashboard progress and ETA within each combination. No per-row console output.
  ETA begins after a completed expiry day; summary loading has an explicit phase.
- Uses the worker-owned bounded cache for the prepared summary and daily option
  chain frames. The first combination reads and prepares each day; later
  combinations reuse the same invalidation-aware frames.
- Builds a worker-temporary Hive-style `trade_date=YYYY-MM-DD` cache with typed
  `trade_date` and `ts` columns. Daily reads target one partition and avoid
  repeatedly scanning the source glob or parsing timestamp strings.
- Counts the partitioned dataset against the existing 2 GiB cache limit and
  deletes it automatically when the worker exits. If it exceeds the limit, the
  strategy falls back to the original source query.
- Deduct configured fees at each close in the cumulative observed equity ledger.
  The former script omitted these fees in intermediate snapshots.
- Validate final observed equity instead of overwriting its realized and
  unrealized values using the final trade result.
- Reject missing marks for open positions and positions left unclosed; do not
  invent quotes, silently value missing positions at zero, or force reconciliation.
- Describe the actual swept premium percentage in metadata instead of always 30%.

## Reproducible bounded validation

Date-partition verification on the local **Weekly · current expiry** dataset:

- 22,171,560 source rows matched the partition-cache row signature exactly.
- 760 typed date partitions were created.
- The temporary cache was 76.4 MiB from 342 MiB of source Parquet files.
- Initial cache construction took 6.00 seconds on this machine.
- Synthetic direct-partition reads, disk-budget enforcement, reuse and cleanup
  passed. Python syntax and `git diff --check` also passed.

The full pandas worker suite could not be rerun after this change because Windows
Application Control blocked NumPy's `_sfc64` DLL at process startup. This is an
environment launch failure; the DuckDB cache verification does not load NumPy.

Completed validation (`outputs/r6_compatibility_check_v2`):

- All 144 synthetic combinations returned exactly the same closed trades as
  the supplied original (22 or 26 legs per variation).
- Default parameters on 5 and 12 January 2023 matched all 34 real-data legs.
- Nonzero fees reconciled at every observed snapshot across both sessions.
- Missing open-position marks were rejected explicitly.
- All 144 variations passed the real dashboard worker's export and analytics
  checks on the synthetic Parquet sample.
- Reports: `parity.json` and `worker/validation.json` in that output directory.

These checks cover bounded samples, not the complete historical dataset.

Run from the repository using its Python environment:

```powershell
.\.venv\Scripts\python.exe tools/verify_r6_dashboard_integration.py `
  --baseline 'D:\Backend\static\admin\images\nifty_current_week_0dte_trend_following_r6_sweep.py' `
  --output outputs/r6_new_validation `
  --market-data 'data/db/nifty current week'
```

Use a new output directory. The checker compares all 144 mappings on two
synthetic expiry days, checks per-snapshot fee accounting and missing-quote
failure, compares two complete real expiry days when `--market-data` is supplied,
then executes all 144 combinations through the actual dashboard worker on the
synthetic Parquet dataset. It does not perform a full historical sweep.

The strategy's loading order and pandas trading engine remain unchanged. All
144 simulations still run; only deterministic prepared frames are reused. Missing
contract quotes may now expose a data problem which the previous snapshot
overwrite concealed.
