# Current Architecture and Strategy-Runner Boundary

## Inspection scope

This document records the repository inspection completed before defining the
strategy-script contract. It describes the current implementation; it does not
claim that pasted-script execution has been implemented.

## Current data flow

```text
Backtesting engine
    -> engine-specific completed-trade adapter
    -> trade_log_exporter.export_trade_log()
    -> canonical trades.csv + checksum manifest
    -> trade_log_dashboard.analyze_trade_log()
    -> dashboard JSON
    -> local browser dashboard
```

The current browser flow still accepts a manually uploaded canonical CSV:

```text
Browser POST /api/analyze (text/csv)
    -> temporary trades.csv
    -> schema validation
    -> DuckDB analytics
    -> JSON response
    -> static dashboard rendering
```

## Components inspected

### Deterministic exporter

`src/trade_log_exporter/schema.py` defines schema v1 and its exact column
order. It accepts raw execution facts and rejects derived result fields such as
P&L, returns, and drawdown.

`src/trade_log_exporter/core.py`:

- accepts an engine's actual completed-trade collection;
- permits one engine-specific mapper;
- verifies the optional authoritative completed-trade count;
- validates every canonical record;
- rejects duplicate trade IDs and mixed run IDs;
- writes the CSV atomically; and
- writes a row-count and SHA-256 manifest.

### Independent analytics

`src/trade_log_dashboard/analytics.py` validates the canonical CSV before using
DuckDB to calculate P&L, daily equity, drawdown, win rate, profit factor,
traded-day Sharpe, streaks, concentration, monthly results, symbol results, and
closed-batch results. Generated strategy code is therefore not trusted to
calculate dashboard analytics.

### Current server and frontend

`src/trade_log_dashboard/server.py` is a local HTTP server. It accepts a raw CSV
body at `/api/analyze`, stores it only in a temporary directory, invokes the
analytics function, and returns JSON.

`src/trade_log_dashboard/static/app.js` implements CSV selection/upload and
renders KPI values, an equity/drawdown SVG, monthly bars, and result tables. It
does not yet contain a Python editor, execution job lifecycle, or sandbox
client.

### Existing integration guidance

`integration/INSTALL_PROMPT.md`, `integration/AGENTS.md.snippet`, and
`integration/CLAUDE.md.snippet` already protect strategy behavior and require
the adapter to use the engine's authoritative completed-trade collection.

## Tests inspected

`tests/test_exporter.py` verifies exact schema and manifest output, rejection of
derived P&L, count mismatches, duplicate IDs, timezone ambiguity, and atomic
failure behavior.

`tests/test_dashboard_analytics.py` verifies that analytics are independently
calculated from raw executions and that an empty trade log cannot produce an
analytics dashboard.

## Confirmed integration boundary

The pasted strategy runner should be added before the existing exporter, not
inside the analytics layer:

```text
Pasted strategy module
    -> platform-supplied context
    -> supported backtesting engine
    -> authoritative completed trades
    -> existing deterministic exporter
    -> existing independent analytics
```

The strategy owns signals and orders. The trusted platform owns run status,
market-data delivery, output paths, export validation, and analytics.

## Deliberately not implemented in this phase

- browser code editor and Run button;
- strategy execution API and job persistence;
- sandbox/container execution;
- concrete market-data context implementation;
- supported-engine adapter registry; and
- automatic handoff from a completed execution to the dashboard.

Those components can now be built against
`integration/STRATEGY_SCRIPT_CONTRACT.md` without changing the canonical trade
schema or analytics calculations.
