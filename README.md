# Trade-log Analytics Dashboard

Run trusted Python backtests locally in a browser, then calculate analytics from
their exported executions. The browser interface, strategies, Python environment,
datasets, and backtest execution all run on the teammate's own computer.

## Team quick start

This repository is private. Each teammate must first be invited to it on GitHub,
then clone it through Codex or Git.

The market dataset is deliberately excluded from Git. Give each teammate a copy
of the dataset folder through your approved shared drive, external disk, or other
team file-sharing method. Keep its contents unchanged.

In Codex, a teammate can use this prompt after cloning the repository:

> Read README.md, run the one-time Python environment setup, configure the local
> dataset folder at `PATH_TO_DATASET`, and run the dashboard locally.

Codex can complete the setup and start the local server. The teammate still needs
access to the private repository and a local dataset copy.

For manual setup on Windows, install Python 3.11, 3.12, or 3.13, then run from
the project folder:

```bat
setup_environment.cmd
start_dashboard.cmd
```

`setup_environment.cmd` creates the project `.venv` and installs the dashboard
and shared strategy stack: pandas, NumPy, DuckDB, PyArrow, Matplotlib, SciPy,
scikit-learn, Seaborn, Plotly, Statsmodels, and OpenPyXL. It needs internet only
on the first run. `start_dashboard.cmd` opens the dashboard at
`http://127.0.0.1:8790/`. Keep the server terminal open while using the app;
press Ctrl+C there to stop it.

## Configure market data

In the dashboard, open **Storage** and enter the full path to the parent dataset
folder. It must contain these folders:

```text
PATH_TO_DATASET/
  nifty current week/
  next week/
  next2week/
  nifty monthly/
```

The setting is saved per Windows user. The dataset dropdown then shows the
available expiry datasets. The app uses a persistent coverage cache and refreshes
only datasets whose files have changed.

Settings, logs, cache, and saved best results are stored under
`%LOCALAPPDATA%\TradeLogAnalytics`; market data remains in the folder selected
above.

## Run a backtest

1. Choose a strategy `.py` file.
2. Select **Weekly**, **Next Weekly**, or **Monthly**.
3. Review the available period and optional configuration.
4. Click **Run backtest**.

The selected data folder is passed directly to the strategy as
`context.market_data`. The strategy owns its lot size, capital model, and trading
rules. When a run finishes, the app shows its results and offers the trade CSV
and checksum manifest for download.

The runner calls `run_strategy(context)`, not the script's Colab `main()`.
Contract versions 1 and 2 are accepted with `RUN_MODE = "single"`. Return
`completed_trades`, an integer `completed_trade_count`, and an optional
`trade_mapper`. Optional `equity_snapshots` are exported and shown automatically.
The included compatibility adapter also recognizes standalone scripts containing
`StrategyConfig`, `DataLoader`, and `ProtectedStraddleBacktester`. It supplies the
selected project dataset and detected period, preserves the strategy's own settings,
and records the engine's mark-to-market checks for intraday risk. Other standalone
engine shapes still need an adapter to this interface.

For parameter optimization, declare `RUN_MODE = "sweep"` and a non-empty
`SWEEP_PARAMETER_SETS` sequence of mappings. The local runner executes all declared
variations sequentially against the same selected dataset. One invalid variation
is reported without stopping later combinations. Live progress includes completed
count and an estimated remaining time. The comparison table shows every variation's
parameters, net P&L, maximum drawdown, win rate, maximum consecutive losses,
average profit, average loss, profit factor and trade count. The user selects the
winning combination using their own criteria. Profitable combinations are ranked
with a transparent weighted score: 35% relative P&L, 35% lower drawdown, 15%
average-win/average-loss ratio, 10% win rate and 5% fewer consecutive losses.
The highest score is automatically rerun and its full analytics open without an
extra confirmation click. The comparison table remains available for a manual override. Per-combination trade and equity
files are temporary and are discarded after their metrics are calculated. The
recommended combination is rerun once to create its validated trade log, equity
file and complete analytics, then saved persistently in the per-user results folder. Manual
overrides are displayed but are not added to history. The rerun must reproduce
the sweep P&L, drawdown, win rate, and trade count or it fails with a
deterministic-strategy error. Use **Best history** to reopen saved reports after
restarting the application.

Python runs with your account's permissions: use trusted code. This is process
separation, not a security sandbox. Additional strategy dependencies must be
installed in the Python environment that launches the dashboard; the standard
NIFTY dependencies (pandas, DuckDB and timezone data) are included.

Limits: one running job, 30 minutes per single run, seven days per sweep, 2 MiB per Python file, 256 MiB
encoded upload, and 2 GiB / 20,000 files for extracted data. Use a local data
path for larger datasets. The latest five working runs remain temporary for the
server session. Only each sweep's recommended winner is kept permanently, with
its analytics and downloadable trade/equity outputs.
Active runs reconnect after a browser refresh. No market data is downloaded.

The local `data/db` copy is excluded from Git. The downloaded source archive is
preserved. Python caches and DuckDB scratch directories were excluded from the
copy. Weekly, Next Weekly, Next2Week and Monthly are available in the NIFTY
runner. Stock-options data is also saved, but uses a different layout and is not
offered as a NIFTY expiry dataset. ZIP/path inputs remain supported by the backend
for integrations; the UI presents only saved datasets.

Use **I already have a trades CSV** to analyse existing results without running
Python. The CSV analysis upload limit remains 25 MiB.

The first dashboard version supports one backtest at a time. It calculates gross and net P&L, drawdown, win rate, profit factor, traded-day Sharpe, streaks, daily equity, monthly performance, and closed-batch results. When `fees` are zero it explicitly states that brokerage and statutory charges are excluded.

## What the package does

For intraday and unrealised risk, upload an optional `equity.csv` after the
trade log. It contains actual engine-recorded valuation snapshots; the dashboard
calculates observed mark-to-market drawdown and checks run identity and final
P&L reconciliation. See [engine integration and sampling limits](integration/EQUITY_SNAPSHOT_CONTRACT.md).

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

## Run a backtest in Google Colab

Open [`integration/COLAB_BACKTEST_TEMPLATE.ipynb`](integration/COLAB_BACKTEST_TEMPLATE.ipynb)
in Google Colab. The notebook:

1. installs the pinned exporter and strategy dependencies;
2. accepts strategy/engine files through a Colab upload;
3. imports a Strategy Contract v2 module and selects market data from an
   upload, an explicit notebook Drive-path override, or the module's explicitly
   user-supplied `DEFAULT_MARKET_DATA_PATH`;
4. creates the context and calls `run_strategy(context)`;
5. exports and validates `output/trades.csv`; and
6. downloads the CSV and checksum manifest.

The strategy module never owns CSV export or analytics. The rules preserve the
user's complete intended trading behavior and standardize only the interface,
configuration, authoritative completed-trade handoff, and raw trade schema.
Every generated module declares `RUN_MODE = "single"` or `"sweep"` from the
user's request so the notebook can select the correct trusted output workflow.
It also declares `DEFAULT_MARKET_DATA_PATH = None` unless the user supplied an
exact Colab Drive path. The notebook override takes precedence, and the module
still consumes data only through `context.market_data`.
Single mode creates `trades.csv`. Sweep mode runs the exact declared
`SWEEP_PARAMETER_SETS` and creates `sweep_results.csv`; see
[`integration/SWEEP_SUMMARY_CONTRACT.md`](integration/SWEEP_SUMMARY_CONTRACT.md).

## Connect an existing backtest

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

There is no P&L column. The dashboard calculates P&L independently from
direction, quantity, execution prices, multiplier, and fees.

## Validate an exported file

```bash
trade-log validate output/trades.csv
```

Print the required header:

```bash
trade-log schema
```

Validate or inspect a sweep summary:

```bash
trade-log validate-sweep output/sweep_results.csv
trade-log sweep-schema
```

## Connect Codex or Claude Code

Open the actual strategy repository in the coding agent and use
[`integration/INSTALL_PROMPT.md`](integration/INSTALL_PROMPT.md). It tells the
agent to inspect the real engine API before making changes.

Then copy the relevant durable contract into the strategy repository:

- `integration/AGENTS.md.snippet` → append to that project's `AGENTS.md`
- `integration/CLAUDE.md.snippet` → append to that project's `CLAUDE.md`

These files preserve the contract for future coding-agent sessions. The executable exporter remains responsible for validation; agent instructions are not a substitute for runtime checks.

## Generate a Colab strategy module

The standard Colab notebook uses the strategy-neutral boundary documented in
[`integration/STRATEGY_SCRIPT_CONTRACT.md`](integration/STRATEGY_SCRIPT_CONTRACT.md).
Use [`integration/CHATGPT_STRATEGY_PROMPT.md`](integration/CHATGPT_STRATEGY_PROMPT.md)
to generate a compliant module after supplying the real supported-engine API and
the requested strategy. The contract standardizes data, configuration,
completed-trade handoff, and error behavior without prescribing trading logic.

After Colab downloads the validated CSV, upload it to the dashboard. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the complete boundary.

## Generate a local dashboard strategy

For a strategy that will run in this dashboard, use
[`integration/DASHBOARD_STRATEGY_GENERATION_PROMPT.md`](integration/DASHBOARD_STRATEGY_GENERATION_PROMPT.md).
It requires a self-contained `run_strategy(context)` module, explicit
`RUN_MODE`, declared sweep parameter mappings when needed, and market data read
only from `context.market_data`.

## Test

See [current limitations and improvement backlog](LIMITATIONS.md) for reporting,
data, performance, and hosting boundaries.

```bash
python -m unittest discover -s tests -v
python examples/custom_engine_integration.py
trade-log validate output/trades.csv
```
