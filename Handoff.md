# Trade-log Analytics Dashboard — Detailed Handoff

Last updated: 8 September 2026  
Workspace: `C:\Users\HELLO!\Documents\ChatGPT\Analytics DashBoard`  
Remote: `https://github.com/Divo15/Trade-log-Analytics-DashBoard.git`  
Branch: `codex/strategy-contract-v2-market-data-path`  
HEAD: `712d61a Add Colab market data path contract v2`

## 1. Current state

This is a **local web app that really executes trusted Python backtest
scripts**. The browser is the interface, a Python HTTP server is the backend,
and a separate Python worker imports and runs the strategy against the selected
local dataset.

The UI is not displaying hard-coded or strategy-supplied analytics. The worker
exports authoritative completed trades to a canonical schema. The dashboard
then independently derives P&L and report metrics from those raw executions.
When the strategy returns engine-recorded equity snapshots, the dashboard also
calculates observed intraday and unrealised drawdown.

Implemented features:

- local Python execution;
- project dataset selection with detected date coverage;
- market-data ZIP and absolute local-path inputs;
- single and parameter-sweep modes;
- progress, cancellation, timeouts and per-combination failure isolation;
- weighted best-combination recommendation;
- automatic rerun and analytics for the recommended result;
- manual selection of another sweep result;
- persistent history for only the recommended winner;
- canonical trade and optional equity export/validation;
- CSV-only analytics;
- performance, risk, consistency, monthly and batch reports;
- two NIFTY sweep strategies; and
- 61 passing automated tests.

No server was running when this handoff was written. A request to
`http://127.0.0.1:8790/api/health` was refused.

## 2. Source-control warning

The working tree is heavily modified and contains many untracked files. Most
of the local-runner, sweep, equity, dataset and UI implementation is **not
committed or pushed**. The remote branch ends at `712d61a`; the local working
directory is materially newer.

Do not run `git reset --hard`, `git clean`, or overwrite this checkout. Preserve
`data`, `history`, `strategies` and the untracked implementation files.

Changed tracked areas include the product/design/architecture documentation,
README, Colab template, contract, dependencies, analytics, server, exporter,
static UI and tests. Important untracked additions include:

- `LIMITATIONS.md` and this handoff;
- `integration/EQUITY_SNAPSHOT_CONTRACT.md`;
- `src/trade_log_dashboard/datasets.py`;
- `src/trade_log_dashboard/equity.py`;
- `src/trade_log_dashboard/legacy.py`;
- `src/trade_log_dashboard/runner.py`;
- `src/trade_log_dashboard/worker.py`;
- `src/trade_log_dashboard/static/runner.js`;
- `src/trade_log_exporter/equity.py`;
- `strategies/`;
- launch/setup scripts; and
- new local-runner, equity and strategy tests.

Review and split the changes into coherent commits before pushing.

## 3. Architecture

### Browser

- `static/index.html` defines the runner, sweep comparison, Best history, CSV
  upload and analytics views.
- `static/runner.js` handles dataset discovery, submissions, polling,
  cancellation, sweep rendering, selected-row reruns and history navigation.
- `static/app.js` renders analytics and charts.
- `static/app.css` implements the design system documented in `DESIGN.md`.

All static files are under `src/trade_log_dashboard/static/`.

### Local server

`src/trade_log_dashboard/server.py` uses `ThreadingHTTPServer` and accepts only
`127.0.0.1` or `localhost`. Main endpoints:

- `GET /api/health` and `GET /api/datasets`
- `POST /api/backtests`
- `GET /api/backtests/{id}`
- `POST /api/backtests/{id}/cancel`
- `POST /api/backtests/{id}/iterations/{index}/run`
- `GET /api/backtests/{id}/{artifact}`
- `GET /api/history` and `GET /api/history/{id}`
- `GET /api/history/{id}/{artifact}`
- `POST /api/analyze`

Runner endpoints check localhost Host/Origin and require
`X-Local-Runner: 1`. This is local request protection, not a Python sandbox.

### Runner and Python execution

`src/trade_log_dashboard/runner.py` validates inputs, creates a temporary job
folder and launches:

```text
sys.executable -u -m trade_log_dashboard.worker <job-folder>
```

The worker therefore uses the same project Python interpreter that started the
server. Output and errors are captured in `run.log`.

`src/trade_log_dashboard/worker.py` imports the selected module and calls its
real backtest entry point. Uploaded code runs with the current Windows user's
permissions. The subprocess provides lifecycle isolation and cancellation; it
does not make untrusted code safe.

Only one job can run at a time. Single runs time out after 30 minutes. Sweeps
time out after seven days and execute combinations sequentially. The latest
five working jobs are retained only for the server session.

### Independent P&L

`src/trade_log_dashboard/analytics.py` validates `trades.csv` and calculates:

```text
LONG:  (exit_price - entry_price) × quantity × multiplier
SHORT: (entry_price - exit_price) × quantity × multiplier
net P&L = gross P&L - fees
```

Legs with the same `batch_id` are grouped as one strategy batch. From those
batches the dashboard derives equity, realised drawdown, win rate, average win,
average loss, profit factor, streaks, daily/monthly results and concentration.

The calculation is independent of a P&L column from the strategy. Its accuracy
still depends on the strategy returning truthful entry/exit prices, direction,
size, multiplier and fees.

### Intraday and unrealised risk

`src/trade_log_exporter/equity.py` validates and exports optional engine
snapshots. `src/trade_log_dashboard/equity.py` calculates:

```text
observed equity = cumulative realised P&L + current unrealised P&L
drawdown = equity - previous high-water mark (including zero)
```

Snapshots must come from real engine valuation events. Missing observations
cannot be reconstructed safely from the final trade CSV.

## 4. Setup and launch

Dependencies are installed in the project-owned `.venv`, not globally.

```powershell
.\setup_environment.cmd
```

The setup installs the package plus DuckDB, pandas, NumPy, Matplotlib, PyArrow,
SciPy, scikit-learn, seaborn, Plotly, statsmodels and openpyxl.

Open the native desktop window:

```powershell
.\start_dashboard.cmd
```

The desktop launcher asks Windows for an available loopback port, waits for the
backend health check, opens the interface, and closes the backend with the window.
For browser-based development without opening a window:

```powershell
.\start_dashboard.cmd -NoBrowser
```

The development command prints the assigned local address. Pass `-Port 8790`
only when a stable development port is specifically useful.

Inside a restricted Codex sandbox, `.venv\Scripts\python.exe` may report
“Access is denied” until execution is approved outside the sandbox. That is an
environment permission issue, not a missing interpreter.

## 5. Dataset inventory

The dataset parent is configurable from **Storage**. On this development machine
it remains `data\db`. Per-user settings, logs, cached coverage, and saved results
live under `%LOCALAPPDATA%\TradeLogAnalytics` and survive application updates.

| UI choice | ID | Folder | Coverage | Days | Chain files |
| --- | --- | --- | --- | ---: | ---: |
| Weekly · current expiry | `weekly` | `nifty current week` | 2 Jan 2023–5 May 2026 | 760 | 23 |
| Next weekly · next expiry | `next-weekly` | `next week` | 2 Jan 2023–5 May 2026 | 759 | 15 |
| Monthly | `monthly` | `nifty monthly` | 2 Jan 2023–5 May 2026 | 761 | 51 |
| Following weekly · two expiries ahead | `next2week` | `next2week` | 2 Jan 2023–5 May 2026 | 656 | 14 |

Each usable folder has `nifty_summary.parquet` and a `nifty_chain` folder of
Parquet files. Coverage is the timestamp overlap between both sources.

The UI should show coverage only after selection and automatically use the full
available range. Users do not enter dates.

The prior dataset copy is retained at `data\db.previous-20260907`. Do not delete
it until the replacement has been reviewed and backed up.

Universal strategy-dataset declarations were deferred because team scripts may
not contain metadata. Current purpose-built strategies validate their required
DTE lifecycle at runtime and fail clearly if the selected dataset is wrong.

## 6. Strategy compatibility

### Preferred contract

```python
STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "single"  # or "sweep"
SWEEP_PARAMETER_SETS = ()

def run_strategy(context):
    return {
        "completed_trades": completed_trades,
        "completed_trade_count": len(completed_trades),
        "trade_mapper": map_trade,
        "equity_snapshots": optional_engine_snapshots,
        "metadata": {
            "strategy_name": "Example",
            "engine": "Engine name",
        },
    }
```

Versions `1` and `2` are accepted. Version 2 documents optional equity
snapshots. `context.market_data` is the selected data path.
`context.config["period"]` contains detected project-dataset coverage and
`context.config["parameters"]` contains the active parameter mapping.

For a sweep, declare a nonempty sequence:

```python
RUN_MODE = "sweep"
SWEEP_PARAMETER_SETS = (
    {"take_profit_pct": 0.20, "stop_loss_multiple": 0.50},
    {"take_profit_pct": 0.20, "stop_loss_multiple": 0.75},
)
```

For a normal strategy, use `RUN_MODE = "single"` and an empty sequence. It runs
once and opens normal analytics.

### Legacy adapter

`src/trade_log_dashboard/legacy.py` recognizes the known standalone format
containing `StrategyConfig`, `DataLoader` and
`ProtectedStraddleBacktester`. Other arbitrary backtesting frameworks need a
`run_strategy(context)` wrapper or a dedicated adapter.

## 7. Single-run workflow

1. User chooses one main Python file and optional sibling modules.
2. User chooses one project dataset, ZIP, or absolute local path.
3. Server copies code and writes `request.json` to a temporary job folder.
4. Runner starts the project Python interpreter in a subprocess.
5. Worker imports and executes the strategy.
6. Strategy returns authoritative completed trades and optional snapshots.
7. Worker exports and validates `trades.csv`.
8. Dashboard derives analytics from those raw executions.
9. If snapshots exist, worker exports `equity.csv` and calculates observed
   intraday/unrealised risk.
10. UI displays the report and downloadable artifacts.

A valid zero-trade run becomes an explicit empty result; it does not fabricate
analytics.

## 8. Sweep workflow

1. `RUN_MODE = "sweep"` activates optimization.
2. Worker validates that `SWEEP_PARAMETER_SETS` is nonempty, unique and
   JSON-storable. There is no fixed combination-count limit.
3. Every mapping runs independently against the same selected dataset.
4. One variation failure is recorded without stopping later variations.
5. Successful completed trades are exported and independently analysed.
6. Only parameters and compact metrics are retained per iteration; large
   trade/equity artifacts are deleted.
7. Profitable completed combinations are ranked.
8. The recommended row is rerun as a normal single backtest to create full
   trades, equity and analytics.
9. P&L, drawdown, win rate and batch count must reproduce the sweep row.
10. The recommended analytics open automatically and are saved to Best history.
11. A user may return and select another completed row. That row is rerun and
    opened, but is not added to persistent Best history.

Sweep progress is written atomically to `progress.json`. The implementation
uses unique temporary names and retries replacement to handle intermittent
Windows file locks.

## 9. Best-combination selection

Implemented in `src/trade_log_dashboard/worker.py`:

```python
SELECTION_WEIGHTS = {
    "net_pnl": 0.35,
    "drawdown": 0.35,
    "average_win_loss_ratio": 0.15,
    "win_rate": 0.10,
    "max_consecutive_losses": 0.05,
}
```

Only completed combinations with positive net P&L are eligible. Metrics are
converted to relative percentile scores within that sweep. Higher P&L,
win/loss ratio and win rate are better; smaller drawdown magnitude and fewer
consecutive losses are better.

Ties are broken by higher score, higher P&L, lower drawdown and then earlier
combination index.

This is an in-sample heuristic. It is not walk-forward validation and does not
prove that the recommended parameters will perform out of sample.

## 10. Included strategies

### `strategies/nifty_6DTE_weekly.py`

- Weekly-expiry hedged short straddle.
- Entry at DTE 6; target, stop or DTE 0 at 15:15 exit.
- Four legs and one 65-unit lot.
- 0.5% adverse fill slippage, zero default fees, no re-entry.
- Take profits: 0.20, 0.30, 0.40, 0.50, 0.60.
- Stop multiples: 0.50, 0.75, 1.00, 1.25.
- 20 combinations.
- Use **Weekly · current expiry**.
- Validates DTE 6 through DTE 0.

### `strategies/nifty_7DTE_straddle_sweep.py`

- Monthly-expiry protected short straddle.
- Entry at DTE 7; target, stop or DTE 0 at 15:15 exit.
- Four legs and one 65-unit lot.
- 0.5% adverse fill slippage, zero default fees, no re-entry.
- 4 targets × 4 stops × 4 wing distances × 3 entry times.
- 192 combinations.
- Use **Monthly**.
- Validates DTE 7 through DTE 0.

Both return completed legs and minute/bar mark-to-market snapshots from their
embedded engine. Full-data sweeps can take hours because execution is
sequential and every mapping processes market data.

## 11. Best history

Persistent winner folders are stored under `history\<sweep-run-id>`. Only the
automatically recommended winner is saved. Potential artifacts:

- `metadata.json` and `analysis.json`
- `trades.csv` and its manifest
- `equity.csv`
- `run.log`

One record exists:

- strategy: NIFTY weekly 6-DTE hedged short straddle sweep;
- dataset: Weekly · current expiry;
- parameters: take profit 0.20 and stop multiple 0.50;
- score: 62.5;
- net P&L: about ₹2,418.98;
- observed intraday drawdown: about -₹2,157.77; and
- completed batches: 1.

This proves persistence works. One batch is not meaningful strategy evidence.

## 12. Pending Impeccable UI request

The latest UI request was to improve the Sweep comparison using the
Impeccable workflow. It is **not implemented** in this handoff. A partial HTML
change was reverted, so current markup and JavaScript remain aligned.

Current problems:

- one separate column is created for every sweep parameter;
- selection/rank/context disappear during horizontal scrolling;
- row-level actions repeat the global action;
- the scoring explanation is long and visually flat;
- the selected row resembles a warning/loss state; and
- the right-side action is clipped at common laptop widths.

Recommended implementation:

1. Combine parameters into one readable key/value cell.
2. Keep selection, rank and parameter context sticky.
3. Group average win/loss and quality metrics while preserving every required
   metric.
4. Use row selection plus one “View selected analytics” action.
5. Add Best overall, Highest P&L, Lowest drawdown and Highest win-rate sorting.
6. Replace the long note with recommended/profitable/completed/unavailable
   summary information.
7. Use a neutral or positive selection state with a Recommended badge.
8. Verify desktop, tablet, mobile, keyboard and reduced-motion behavior.

Follow `C:\Users\HELLO!\.agents\skills\impeccable\SKILL.md`, `PRODUCT.md` and
`DESIGN.md`.

## 13. Verification

Run on 8 September 2026:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result:

```text
Ran 61 tests in 25.044s
OK
```

Tests cover canonical exports, schema integrity, P&L/drawdown calculations,
equity validation, real dataset plumbing, price-sensitive results, local NIFTY
execution, the legacy adapter, sweeps over 100 combinations, failure isolation,
ranking weights, deterministic reruns, recommended-only persistence, both
included strategies and the Colab contract/template.

After starting the server, verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8790/api/health
```

Expected: `{"status":"ok"}`.

## 14. Material limitations

See `LIMITATIONS.md` for the maintained list. Key boundaries:

- uploaded Python is trusted and unsandboxed;
- one running job at a time;
- 30-minute single and seven-day sweep timeouts;
- combinations execute sequentially with no fixed count limit;
- latest five working jobs are temporary;
- sweep tables disappear after server restart;
- only recommended winners have persistent history;
- no resume, checkpointing or parallel sweep workers;
- no universal strategy/dataset declaration;
- no walk-forward or holdout optimization;
- calculations trust the raw execution facts supplied by the engine;
- fee completeness and mark authenticity cannot be independently proven;
- realised drawdown is daily without equity snapshots;
- snapshot gaps may hide worse losses;
- no dependable CAGR/Calmar without capital and cash-flow history;
- Sharpe uses traded-day currency P&L;
- currency display is fixed to INR;
- only the latest 100 batches reach the browser;
- no public-hosting security, TLS, accounts or multi-user isolation; and
- the sweep comparison still needs its requested redesign.

## 15. Documentation inconsistency

Some Colab-era text still says the website does not execute Python. That is
obsolete for the local app. The repository now has two paths:

- Colab runs a strategy and exports canonical files; and
- the local app runs a trusted strategy directly.

Reconcile `README.md`, `ARCHITECTURE.md`,
`integration/STRATEGY_SCRIPT_CONTRACT.md` and `LIMITATIONS.md` so both paths
are explicit. Preserve the canonical trade boundary and do not claim arbitrary
strategy compatibility.

## 16. Recommended continuation order

1. Start port 8790 and confirm health.
2. Complete the Impeccable sweep-table redesign.
3. Run all 61 tests again.
4. Exercise a small sweep in the browser: progress, auto winner, manual
   override and Best history.
5. Review `git diff` and create coherent commits.
6. Reconcile the documentation.
7. Push the intended branch.
8. Add holdout/walk-forward evaluation before treating optimization as a
   production selection process.

## 17. Read these files first

1. `PRODUCT.md`
2. `DESIGN.md`
3. `LIMITATIONS.md`
4. `src/trade_log_dashboard/runner.py`
5. `src/trade_log_dashboard/worker.py`
6. `src/trade_log_dashboard/datasets.py`
7. `src/trade_log_dashboard/analytics.py`
8. `src/trade_log_dashboard/equity.py`
9. `src/trade_log_dashboard/static/runner.js`
10. `integration/STRATEGY_SCRIPT_CONTRACT.md`
11. `tests/test_local_runner.py`
12. `tests/test_equity.py`

This handoff describes the local working tree, not only the older remote
branch.
