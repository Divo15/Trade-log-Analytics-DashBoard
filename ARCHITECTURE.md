# Local Backtest and Analytics Architecture

## System boundary

The default workflow runs a local Python server and opens its interface in a web browser.
The terminal owns the server lifetime; Ctrl+C shuts down the server and worker.
The interface submits strategy files, configuration and either a data ZIP or a
local data path. A single-job manager handles polling, cancellation, a 30-minute
deadline and temporary artifacts. The worker calls `run_strategy(context)`,
validates and exports actual completed trades, then independently computes
analytics. Single runs retain the 30-minute deadline; sweep jobs can run for up
to seven days. Sweeps execute all declared parameter sets sequentially,
calculate a compact summary for each variation, discard its temporary trade and
equity artifacts, continue after an individual variation fails, and expose
comparison rows without combining alternative P&L. After explicit user selection,
the worker reruns that exact parameter mapping, retains its validated artifacts,
and rejects the result if core metrics do not reproduce. Optional engine equity
snapshots are reconciled and analysed too.
The server is loopback-only; run mutations require a custom header and enforce
same-origin/Host checks. Subprocess execution is not a security sandbox.
Settings, rotating logs, cached metadata, and saved results use per-user writable
application-data directories. The selected dataset parent can live anywhere and is
stored atomically in settings. The catalog fingerprints every summary and chain
file by relative path, size, and modification time, so unchanged datasets avoid
Parquet scans while changed datasets refresh independently. A process lock prevents
duplicate scans from concurrent UI requests.
The earlier Colab workflow below remains an optional integration. Its execution
boundary applies to that workflow, not to the new local runner.

```text
Google Colab
    -> notebook-selected upload or Google Drive market data
    -> Strategy Contract v2 module: run_strategy(context)
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
- explicit market-data selection, with a notebook Drive-path override taking
  precedence over an explicitly user-supplied strategy default;
- creation of `run_id`, `market_data`, and immutable configuration;
- one call to `run_strategy(context)`;
- authoritative completed-trade count verification;
- canonical CSV export and validation;
- checksum manifest creation; and
- download of the CSV and manifest.

The strategy module owns only strategy behavior and the engine call. It may
passively declare an exact user-supplied Colab Drive path for notebook use, but
must not mount Drive, inspect that path, load data during import, install
packages, write output, or calculate dashboard analytics. Runtime market data
is consumed only through `context.market_data`.

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
