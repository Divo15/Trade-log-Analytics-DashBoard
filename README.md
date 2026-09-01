# Trade-log Analytics Dashboard

This repository contains the deterministic trade-log exporter and a local web dashboard that independently calculates backtest analytics with DuckDB.

## Run the dashboard

Install the repository, then start the local application:

```bash
python -m pip install -e .
trade-dashboard
```

The browser opens at `http://127.0.0.1:8765`. Upload a canonical `trades.csv`; the file is processed locally and is not retained after analysis.

The first dashboard version supports one backtest at a time. It calculates gross and net P&L, drawdown, win rate, profit factor, traded-day Sharpe, streaks, daily equity, monthly performance, and closed-batch results. When `fees` are zero it explicitly states that brokerage and statutory charges are excluded.

## What the package does

`trade-log-exporter` copies actual completed trades from a Python backtesting engine into one canonical CSV format. It validates raw execution fields, refuses derived P&L fields, verifies row counts, writes atomically, and creates a checksum manifest.

The package does not discover trades inside arbitrary scripts. Each backtesting framework needs a small adapter that maps its completed-trade objects to the canonical fields.

## Install during development

From this repository:

```bash
python -m pip install -e .
```

For a separate strategy project, install this repository by local path:

```bash
python -m pip install -e "C:\path\to\Trade-log-Analytics-DashBoard"
```

## Connect a backtest

```python
from trade_log_exporter import export_trade_log


def map_engine_trade(trade, index):
    return {
        "run_id": "run-2026-08-31-001",
        "trade_id": str(trade.id),
        "batch_id": str(getattr(trade, "batch_id", "")),
        "leg_id": str(getattr(trade, "leg_id", "")),
        "strategy": "My strategy",
        "symbol": trade.symbol,
        "side": trade.side,
        "entry_time": trade.entry_time,
        "exit_time": trade.exit_time,
        "quantity": trade.quantity,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "multiplier": trade.multiplier,
        "fees": trade.fees,
    }


results = run_backtest()
completed = results.closed_trades  # Replace with the real engine API.

receipt = export_trade_log(
    completed,
    "output/trades.csv",
    mapper=map_engine_trade,
    expected_count=len(completed),
)

print(f"Completed trades: {len(completed)}")
print(f"Exported rows: {receipt.row_count}")
print(f"Trade log: {receipt.output_path}")
print("Validation: passed")
```

The example in [`examples/custom_engine_integration.py`](examples/custom_engine_integration.py) is executable. The mapper is the only engine-specific portion.

## Canonical schema v1

```text
schema_version,run_id,trade_id,batch_id,leg_id,strategy,symbol,side,entry_time,exit_time,quantity,entry_price,exit_price,multiplier,fees
```

There is no P&L column. The future dashboard will calculate P&L independently from direction, quantity, execution prices, multiplier, and fees.

## Validate an exported file

```bash
trade-log validate output/trades.csv
```

Print the required header:

```bash
trade-log schema
```

## Connect Codex or Claude Code

Open the actual strategy repository in the coding agent and use
[`integration/INSTALL_PROMPT.md`](integration/INSTALL_PROMPT.md). It tells the
agent to inspect the real engine API before making changes.

Then copy the relevant durable contract into the strategy repository:

- `integration/AGENTS.md.snippet` → append to that project's `AGENTS.md`
- `integration/CLAUDE.md.snippet` → append to that project's `CLAUDE.md`

These files preserve the contract for future coding-agent sessions. The executable exporter remains responsible for validation; agent instructions are not a substitute for runtime checks.

## Generate a pasted strategy module

The future pasted-script runner uses the strategy-neutral boundary documented in
[`integration/STRATEGY_SCRIPT_CONTRACT.md`](integration/STRATEGY_SCRIPT_CONTRACT.md).
Use [`integration/CHATGPT_STRATEGY_PROMPT.md`](integration/CHATGPT_STRATEGY_PROMPT.md)
to generate a compliant module after supplying the real supported-engine API and
the requested strategy. The contract standardizes data, configuration,
completed-trade handoff, and error behavior without prescribing trading logic.

The current implementation still accepts a canonical CSV; see
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the inspected system boundary and the
runner components that remain to be built.

## Test

```bash
python -m unittest discover -s tests -v
python examples/custom_engine_integration.py
trade-log validate output/trades.csv
```
