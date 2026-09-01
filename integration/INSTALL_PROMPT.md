# Google Colab backtest integration prompt

Paste the text below into Codex or Claude Code while the actual strategy
repository is open. Replace `<EXPORTER_REPOSITORY_PATH_OR_URL>` with the local
path or pinned Git URL for this package.

```text
Integrate this backtesting project with the standard Google Colab runner and
canonical trade-log exporter.

Do not change, simplify, replace, omit, reinterpret, or tune any strategy
indicator, signal, entry, exit, sizing, stop, target, re-entry, multi-leg,
session, data, fill, slippage, commission, or execution behavior.

First inspect the repository and report:
1. the normal backtest entry point;
2. the backtesting framework or custom engine;
3. the authoritative completed-trades, closed-positions, or closed-legs
   collection;
4. how partial exits and multi-leg positions are represented;
5. the packages required in Google Colab; and
6. the command currently used to run the backtest.

Then:
1. Install `trade-log-exporter` from
   `<EXPORTER_REPOSITORY_PATH_OR_URL>` and pin the dependency.
2. Keep or create a Strategy Contract v1 module exposing
   `run_strategy(context)`. Require `RUN_MODE = "single"` or `"sweep"` based
   on the user's requested run type, plus an exact non-empty
   `SWEEP_PARAMETER_SETS` for sweep mode or an empty tuple for single mode. Do
   not add execution side effects at import time.
3. Consume market data only from `context.market_data`. The standard notebook
   will set it from a Colab upload or user-selected Google Drive path. Never
   embed a desktop or Drive path in the strategy module.
4. Consume instrument, period, execution assumptions, and strategy parameters
   from `context.config`. Never silently override supplied execution values.
5. Return the engine's authoritative completed-trade collection, its original
   count, and a raw-execution mapper. Never infer, reconstruct, supplement, or
   fabricate trades.
6. Normalize DataFrame-like completed trades to row mappings without changing
   values, preserve the authoritative count, and verify the row count.
7. Keep one row per completed trade or closed leg. Connect multi-leg rows with
   `batch_id` and identify each leg with `leg_id`.
8. Keep P&L, returns, drawdown, Sharpe, win rate, and all dashboard analytics
   out of both the strategy result and canonical CSV.
9. Configure `integration/COLAB_BACKTEST_TEMPLATE.ipynb` with the real strategy
   module, engine files, Colab-installable dependencies, configuration, and
   market-data selection. Package installation belongs in notebook setup cells.
10. Let the notebook call `run_strategy(context)`. For single mode, export
    `output/trades.csv` with `expected_count`. For sweep mode, run each declared
    parameter mapping and call trusted `export_sweep_summary()` to create
    `output/sweep_results.csv`. Validate and download the resulting CSV and
    manifest.
11. Ensure a failed backtest or export cannot be reported as successful.
12. Add tests for strategy preservation, exact columns, count matching,
    DataFrame row normalization, mapping, timezone handling, multi-leg behavior,
    dependency failure, and export failure.

Run one representative backtest in Colab. Report the authoritative completed-
trade count, exported-row count, output path, schema version, and validation
result. Do not claim completion unless the actual engine produced the rows and
the exported CSV validated.

The analytics website is downstream only. It uploads, revalidates, stores, and
analyzes the canonical CSV. It must never execute the strategy or engine.
```
