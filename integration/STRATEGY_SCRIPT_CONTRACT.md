# Strategy Script Contract v2

## Purpose

Optional engine-recorded equity snapshots are supported for intraday risk;
see [Equity snapshot contract](EQUITY_SNAPSHOT_CONTRACT.md). Existing modules
without snapshots remain compatible.

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

### Optional local runtime market-data loader

The local worker provides `context.market_data_loader`. One loader is shared
across the sequential combinations of a sweep. It caches data and deterministic
preparation only; trading decisions, positions, fills, and equity remain the
strategy's responsibility. Existing strategies need not use it.

```python
loader = getattr(context, "market_data_loader", None)
read = lambda: pandas.read_parquet(summary_path)
summary = (loader.read_frame([summary_path], ("summary-v1",), read)
           if loader is not None else read())
```

`read_frame(paths, key, load)` caches a DataFrame from `load()`. List every
source file in `paths`; the runtime keys by absolute path, size and modification
time. Put the operation version, date filters and all other inputs in `key`.
`prepare_frame(key, frames, prepare)` caches deterministic DataFrame preparation;
the runtime fingerprints the complete input frames, including indexes, and
returns independent copies. Include every other preparation parameter in `key`.
Do not cache callbacks with side effects or simulation state. Use simple scalar
market-data columns; Python objects nested inside cells are outside this API.

The worker bounds prepared frames to 128 MiB and disk frames to 2 GiB, evicting
older entries when needed. Normal completion and exceptions clean up the cache;
forced termination can leave cache files in the temporary job folder until that
folder is removed. A winner rerun receives a fresh cache. No cache is shared
between separate jobs. The worker prints hit/miss counts in the run log.

Generated standalone scripts can opt in using `getattr` as above and fall back
to ordinary loading outside the local runner. Every dashboard strategy must be
self-contained in its uploaded Python file. This interface changes execution
plumbing, never strategy rules.

Every submitted module must expose exactly one public execution entry point:

```python
STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "single"  # or "sweep"
SWEEP_PARAMETER_SETS = ()  # Non-empty sequence of mappings when RUN_MODE == "sweep"
DEFAULT_MARKET_DATA_PATH = None  # Or an exact user-supplied Colab Drive path


def run_strategy(context):
    """Run the strategy and return the authoritative completed-trade result."""
    ...
```

The generated module must declare `RUN_MODE` explicitly. Set it to `"single"`
when the user requests one backtest and `"sweep"` when the user requests a
parameter sweep, grid search, or optimization. The value describes the user's
requested run type; it must not be inferred from completed trades at runtime.
Selecting a mode must never alter the intended trading rules.

For the local Trade-log Analytics Dashboard, generated strategy modules must
read market data only through `context.market_data`. A strategy must never
embed a developer's local dataset path, infer another dataset folder, download
market data, or read an environment-specific path.

For `RUN_MODE = "sweep"`, the generated module must also declare a non-empty
`SWEEP_PARAMETER_SETS` sequence. Each item is one exact strategy-parameter
mapping requested by the user. The notebook calls `run_strategy(context)` once
per item after placing that mapping in `context.config["parameters"]`. For
`RUN_MODE = "single"`, declare `SWEEP_PARAMETER_SETS = ()`.

`DEFAULT_MARKET_DATA_PATH` is optional configuration for the trusted notebook.
It may be set only when the user explicitly supplies the exact Colab Google
Drive path. The generator must preserve that string exactly: it must not
invent, infer, normalize, relocate, or silently change it. Otherwise, declare
`DEFAULT_MARKET_DATA_PATH = None`.

An embedded default is valid only when it is a Colab-mounted Google Drive path,
such as `/content/drive/MyDrive/...` (or a shared-drive path under
`/content/drive/Shareddrives/...`). Windows paths, macOS or Linux desktop
paths, and arbitrary server paths are forbidden. This declaration does not
authorize the module to open or inspect the path.

The standard Colab notebook calls `run_strategy(context)` once per execution.
Importing the module must not start a backtest, mount Drive, inspect the
filesystem, load market data, contact a service, prompt for input, or write
output.

Helper functions, classes, indicators, and strategy-specific data structures
are unrestricted and may be defined in the same module.

## Colab-supplied context

The standard Colab notebook supplies one context object with these logical fields:

| Field | Ownership | Meaning |
| --- | --- | --- |
| `run_id` | Notebook | Unique identifier for this execution |
| `market_data` | User/notebook | Read-only market data selected from a Colab upload or Google Drive |
| `config` | User/notebook | Read-only run and strategy configuration |

The concrete context type is provided by the reusable notebook. For Drive
data, the notebook imports the strategy without filesystem side effects and
then selects the exact path in this order:

1. a non-empty explicit notebook `MARKET_DATA_PATH` override;
2. `strategy.DEFAULT_MARKET_DATA_PATH`; or
3. a clear failure when neither is present.

The notebook validates that a declared default is a permitted Colab Drive
path, mounts Drive, verifies the selected path exists, and passes the resolved
`Path` through `context.market_data`. For uploaded data, the notebook uses the
uploaded file or extracted archive and does not mount Drive.

The strategy must consume market data only through `context.market_data`. It
must not mount Drive, open its declared default directly, prompt for a file, or
download market data. If a framework needs to mutate a dataframe, the script
must make a strategy-local copy first.

The notebook may add fields in a backward-compatible way. A v2 strategy must
not depend on undocumented context attributes.

## Strategy-preservation invariant

Colab compatibility is plumbing only. Generating or adapting a strategy must
not simplify, replace, omit, reinterpret, or tune any user-requested trading
behavior. If the supplied engine cannot express part of the intended strategy,
the generated module must fail explicitly rather than substitute different
logic.

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
- `execution` contains notebook-supplied assumptions such as capital, fees,
  slippage, fill behavior, and contract multipliers.
- `parameters` contains arbitrary strategy-specific values.

Generated code may validate and consume these values. It must not silently
replace user-supplied execution assumptions. Strategy-specific defaults
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

The value handed to the exporter must iterate over trades, not table columns.
If the engine's authoritative collection is a pandas `DataFrame` or another
column-iterating table, capture its authoritative count first and normalize it
to a row-oriented list without changing any values:

```python
authoritative_completed_trades = engine_result.completed_legs
authoritative_count = len(authoritative_completed_trades)
completed_trade_rows = authoritative_completed_trades.to_dict(orient="records")

if len(completed_trade_rows) != authoritative_count:
    raise ValueError("completed-trade row normalization changed the engine count")
```

Returning `completed_trade_rows` with `authoritative_count` is portable across
runners. The local worker also accepts canonical pandas DataFrames directly,
with or without a custom trade mapper. A custom mapper receives each Series row;
without a mapper, canonical columns are converted to mappings without changing
values. Lists and generators of canonical mappings remain supported. This
conversion does not infer or reconstruct trades. Noncanonical engine objects
still need an explicit mapper; the backend does not guess field meanings.

### `completed_trade_count`

This must be the authoritative engine count for the same collection. The
trusted notebook passes it to `export_trade_log(..., expected_count=...)`. A count
mismatch fails the run. The local worker accepts Python and NumPy integer
counts, but rejects booleans, fractional counts and numeric strings. The result
may be any mapping, including a read-only mapping.

### Optional equity snapshots

The local worker accepts lists, generators of mappings, and pandas DataFrames
with `timestamp`, `realized_pnl`, and `unrealized_pnl` columns. If `run_id` or
`schema_version` columns are included, they must match the current run and
equity schema v1. See [the equity contract](EQUITY_SNAPSHOT_CONTRACT.md).
Snapshots must contain genuine observations with cumulative net realised P&L
across the whole run. LONG and SHORT legs, quantities, multipliers and fees
must reconcile with the trade export. Invalid snapshots still fail validation;
the worker reports expected, supplied and difference values for endpoint
mismatches. It does not silently repair day resets or replace an endpoint.
Strategies without observed equity may omit snapshots; trade analytics still
work, while intraday drawdown is unavailable.

### Progress callbacks

Both single and sweep runs accept
`context.report_progress(completed, total, unit="item", phase=None)`.
Reporting is optional; it does not alter trades. Report meaningful work units
such as days or files. A phase label describes the current stage; a preparation
stage estimate is not a guarantee of the entire backtest finish time.

### Validate before a long run

Run a strategy through the actual local worker against a representative sample
dataset using:

```powershell
.venv\Scripts\python.exe -m trade_log_dashboard.validate_strategy strategies\my_strategy.py --market-data data\sample --output outputs\my_strategy_check --timeout 120
```

The sample must have the same schema and contain all history needed by the
strategy, including signal days and execution days. The command does not change
rules, date filters, or sweep parameter sets and does not select a sample for
you. It runs every declared sweep combination with an overall deadline. Use
`--config config.json` when configuration is needed and choose a new output
folder for each check. It does not use or interrupt the dashboard server.

The output contains `validation.json`, logs and available worker artifacts,
including exported trades when equity reconciliation fails. Any failed or
empty variation, or timeout, returns a nonzero exit code. Passing checks the
output contract on the supplied sample; it does not prove trading-rule fidelity
or profitability. Compare against an existing baseline separately when adapting
an existing strategy.

### `trade_mapper`

This callable receives one actual engine trade and its zero-based index and
maps raw execution fields to the existing canonical trade-log schema. It must
not calculate P&L, returns, drawdown, Sharpe, or other analytics. If the
supported engine adapter is installed by the notebook, this value may be
`None`, and the trusted notebook uses that adapter instead.

### `metadata`

Metadata identifies the strategy and supported engine. It is descriptive and
must not be used as evidence that execution succeeded.

## No-trade runs

A successfully completed engine run may legitimately return zero completed
trades. It must return the real empty authoritative collection and count zero.
The notebook reports `no_trades`; it does not fabricate a row or create an
analytics result that requires completed trades.

## Errors and execution status

The strategy script does not declare its own success status. A normal return
means the engine finished and supplied the required result structure. Any
failure must propagate as an exception.

The script must not catch an error merely to print it and then return a fake or
partial success result. It may add context and re-raise the exception.

The trusted notebook may report these outcomes:

```text
running -> succeeded
        -> no_trades
        -> failed
```

Printed output is diagnostic only. The return value, engine count, canonical
export, manifest, and validator determine success.

## Colab output ownership

Submitted strategy code must not write `trades.csv`, its manifest, dashboard
JSON, or analytics files. After `run_strategy` returns, trusted notebook code:

1. reads the declared `RUN_MODE`;
2. obtains the authoritative completed-trade collection for each requested run;
3. selects the trusted engine adapter or returned mapper;
4. calls `trade_log_exporter.export_trade_log()` for `single` or
   `trade_log_exporter.export_sweep_summary()` for `sweep`;
5. verifies count, schema, checksum, and manifest; and
6. downloads the validated CSV and checksum manifest.

The website accepts the resulting CSV, validates it again, and stores it. For a
single run it calculates dashboard analytics independently from `trades.csv`.
For a sweep it displays the metrics that trusted `export_sweep_summary()`
calculated from each iteration's validated canonical trades. The website does
not execute the strategy or backtesting engine.

## Colab compatibility boundary

Generated modules may import dependencies that are installable in the selected
Google Colab runtime. Package installation belongs in notebook setup cells, not
at module import time. Colab runtime limits and package availability are
execution constraints only; they must never be used as a reason to silently
change indicators, entries, exits, sizing, risk rules, or other strategy logic.

## Acceptance checklist

A script is contract-compliant when:

- it declares `STRATEGY_CONTRACT_VERSION = "2"`;
- it declares `RUN_MODE` as exactly `"single"` or `"sweep"` based on the
  user's request;
- it declares an empty `SWEEP_PARAMETER_SETS` for `single` or the exact,
  non-empty requested parameter mappings for `sweep`;
- it declares `DEFAULT_MARKET_DATA_PATH = None` unless the user supplied an
  exact permitted Colab Drive path;
- it preserves an explicitly supplied default path exactly and embeds no
  desktop or arbitrary server market-data path;
- importing it has no execution, Drive, filesystem, network, input, or
  market-data-loading side effects;
- it exposes callable `run_strategy(context)`;
- it consumes notebook-supplied data and configuration;
- it preserves all requested strategy behavior;
- it returns the authoritative completed-trade collection and count;
- any authoritative tabular result is normalized to trade rows, never returned
  as a directly iterated DataFrame;
- its mapper contains raw execution fields only;
- it lets failures propagate; and
- it does not write or calculate dashboard outputs.
