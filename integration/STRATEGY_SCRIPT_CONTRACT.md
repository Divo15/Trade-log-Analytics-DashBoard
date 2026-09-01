# Strategy Script Contract v1

## Purpose

This contract standardizes the boundary around a generated Python trading
strategy. It does not standardize or constrain the trading strategy itself.

The stable rule is:

> Standardize the plumbing, not the strategy.

## Strategy freedom

The contract must not prescribe or alter:

- indicators or feature engineering;
- entry, exit, or re-entry conditions;
- long, short, multi-leg, or multi-symbol behavior;
- stop-loss, target, trailing, or time-based exit logic;
- sizing, scaling, hedging, or portfolio rules;
- intraday, positional, event-driven, or bar-driven design;
- optimization parameters or internal strategy state; or
- the number or profitability of trades.

These decisions belong exclusively to the submitted strategy.

## Stable module interface

Every submitted module must expose exactly one public execution entry point:

```python
STRATEGY_CONTRACT_VERSION = "1"


def run_strategy(context):
    """Run the strategy and return the authoritative completed-trade result."""
    ...
```

The platform calls `run_strategy(context)` once per execution. Importing the
module must not start a backtest, contact a service, or write output.

Helper functions, classes, indicators, and strategy-specific data structures
are unrestricted and may be defined in the same module.

## Platform-supplied context

The runner supplies one context object with these logical fields:

| Field | Ownership | Meaning |
| --- | --- | --- |
| `run_id` | Platform | Unique identifier for this execution |
| `market_data` | Platform | Read-only market data selected for this run |
| `config` | Platform/user | Read-only run and strategy configuration |

The concrete context type will be provided by the runner implementation. The
script must consume the supplied market data instead of uploading, downloading,
or opening a hard-coded data path. If a framework needs to mutate a dataframe,
the script must make a strategy-local copy first.

The platform may add fields in a backward-compatible way. A v1 strategy must
not depend on undocumented context attributes.

## Configuration ownership

`context.config` separates execution assumptions from strategy parameters:

```python
{
    "instrument": {...},
    "period": {...},
    "execution": {...},
    "parameters": {...},
}
```

- `instrument` identifies symbols, contracts, and timeframe.
- `period` identifies the selected historical range.
- `execution` contains platform-controlled assumptions such as capital, fees,
  slippage, fill behavior, and contract multipliers.
- `parameters` contains arbitrary strategy-specific values.

Generated code may validate and consume these values. It must not silently
replace platform-controlled execution assumptions. Strategy-specific defaults
are permitted only when the corresponding parameter is absent and the default
is visible in the source.

## Successful return value

`run_strategy(context)` returns a mapping with this shape:

```python
{
    "completed_trades": completed_trades,
    "completed_trade_count": len(completed_trades),
    "trade_mapper": map_completed_trade,
    "metadata": {
        "strategy_name": "Descriptive strategy name",
        "engine": "Supported engine name",
    },
}
```

### `completed_trades`

This must be the supported backtesting engine's authoritative collection of
actual completed trades, closed positions, or closed legs. The script must not
infer, reconstruct, fabricate, or supplement this collection.

### `completed_trade_count`

This must be the authoritative engine count for the same collection. The
trusted runner passes it to `export_trade_log(..., expected_count=...)`. A count
mismatch fails the run.

### `trade_mapper`

This callable receives one actual engine trade and its zero-based index and
maps raw execution fields to the existing canonical trade-log schema. It must
not calculate P&L, returns, drawdown, Sharpe, or other analytics. If the
supported engine adapter is installed by the platform, this value may be
`None`, and the trusted runner uses that adapter instead.

### `metadata`

Metadata identifies the strategy and supported engine. It is descriptive and
must not be used as evidence that execution succeeded.

## No-trade runs

A successfully completed engine run may legitimately return zero completed
trades. It must return the real empty authoritative collection and count zero.
The platform records the execution as `no_trades`; it does not fabricate a row
or open an analytics dashboard that requires completed trades.

## Errors and execution status

The strategy script does not declare its own success status. A normal return
means the engine finished and supplied the required result structure. Any
failure must propagate as an exception.

The script must not catch an error merely to print it and then return a fake or
partial success result. It may add context and re-raise the exception.

The trusted platform owns these statuses:

```text
queued -> running -> succeeded
                  -> no_trades
                  -> failed
                  -> timed_out
                  -> rejected
```

Printed output is diagnostic only. The return value, engine count, canonical
export, manifest, and validator determine success.

## Output ownership

Submitted strategy code must not write `trades.csv`, its manifest, dashboard
JSON, or analytics files. After `run_strategy` returns, trusted platform code:

1. obtains the authoritative completed-trade collection;
2. selects the trusted engine adapter or returned mapper;
3. calls `trade_log_exporter.export_trade_log()`;
4. verifies count, schema, checksum, and manifest; and
5. calls `trade_log_dashboard.analyze_trade_log()`.

This keeps dashboard calculations independent of generated code.

## Execution-safety boundary

The eventual runner may restrict filesystem access, network access, packages,
CPU, memory, and runtime. These are execution-safety controls, not trading-rule
constraints. They must not be presented as limitations on indicators, entries,
exits, sizing, or risk logic.

## Acceptance checklist

A script is contract-compliant when:

- it declares `STRATEGY_CONTRACT_VERSION = "1"`;
- importing it has no execution side effects;
- it exposes callable `run_strategy(context)`;
- it consumes platform-supplied data and configuration;
- it preserves all requested strategy behavior;
- it returns the authoritative completed-trade collection and count;
- its mapper contains raw execution fields only;
- it lets failures propagate; and
- it does not write or calculate dashboard outputs.
