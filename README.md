# Trade-log Analytics Dashboard

This repository currently contains the backtest-side integration package for producing deterministic, versioned trade-log CSV files. The web dashboard is intentionally deferred until the export contract is installed and verified.

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

## Test

```bash
python -m unittest discover -s tests -v
python examples/custom_engine_integration.py
trade-log validate output/trades.csv
```
