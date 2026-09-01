# Google Colab Backtest and Analytics Architecture

## System boundary

Python strategies and backtesting engines run in the user's Google Colab
runtime. The analytics website never executes uploaded Python.

```text
Google Colab
    -> notebook-selected upload or Google Drive market data
    -> Strategy Contract v1 module: run_strategy(context)
    -> supported backtesting engine
    -> authoritative completed trades
    -> trade_log_exporter.export_trade_log()
    -> validated trades.csv + checksum manifest
    -> browser download

For RUN_MODE = sweep
    -> one validated temporary canonical trade log per parameter iteration
    -> trusted export_sweep_summary() calculations
    -> validated sweep_results.csv + checksum manifest
    -> browser download

Analytics website
    -> canonical trades.csv upload
    -> schema/checksum validation
    -> storage and run record
    -> trade_log_dashboard.analyze_trade_log()
    -> dashboard JSON and charts
```

## Strategy-preservation boundary

The contract standardizes plumbing, not trading logic. Moving execution to
Colab must never change, simplify, replace, omit, reinterpret, or tune the
user's indicators, signals, entries, exits, sizing, stops, targets, re-entry,
multi-leg behavior, session rules, or state.

If the supported engine or Colab runtime cannot implement the intended
strategy exactly, generation or execution fails explicitly. It must not
substitute another strategy.

## Colab responsibilities

The reusable notebook owns:

- package installation in setup cells;
- optional Google Drive mounting and file uploads;
- explicit market-data selection;
- creation of `run_id`, `market_data`, and immutable configuration;
- one call to `run_strategy(context)`;
- authoritative completed-trade count verification;
- canonical CSV export and validation;
- checksum manifest creation; and
- download of the CSV and manifest.

The strategy module owns only strategy behavior and the engine call. It must
not mount Drive, install packages, embed paths, write output, or calculate
dashboard analytics.

## Website responsibilities

The website starts at the artifact boundary. It owns:

- canonical CSV upload;
- validation before acceptance;
- storage and run metadata;
- independent analytics calculations; and
- dashboard presentation.

The website does not need Modal, a Python strategy runner, sandboxed backtest
jobs, market-data delivery, a Python editor, or an engine adapter registry.

The current local server processes one uploaded CSV in a temporary directory.
Persistent hosted storage and user accounts remain deployment work.

## Existing trusted components

`src/trade_log_exporter/schema.py` defines schema v1 and exact column order.
It accepts raw execution facts and rejects derived fields such as P&L, returns,
and drawdown.

`src/trade_log_exporter/core.py` verifies counts, validates records, rejects
duplicate trade IDs and mixed run IDs, writes atomically, and creates a
row-count/checksum manifest.

`src/trade_log_dashboard/analytics.py` validates the CSV again and calculates
P&L, equity, drawdown, win rate, profit factor, traded-day Sharpe, streaks,
concentration, monthly results, symbols, and closed batches with DuckDB.

`src/trade_log_dashboard/server.py` and the static frontend accept a CSV and
render the calculated analytics. No Python strategy is accepted or executed.

## Deliberately outside the website

- Python strategy upload or execution;
- backtesting-engine execution;
- server-side market-data storage for backtests;
- Modal or another Python sandbox service;
- execution queues, timeouts, or job workers; and
- reconstruction of trades from arbitrary strategy output.

These are replaced by the standard Colab workflow in
`integration/COLAB_BACKTEST_TEMPLATE.ipynb`.
