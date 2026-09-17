# Trade-log Analytics Dashboard — Handoff

Updated: 10 September 2026 (Asia/Kolkata).
Workspace: C:\Users\HELLO!\Documents\ChatGPT\Analytics DashBoard

## Current state

This is a local browser dashboard with a Python server and subprocess workers.
The user explicitly reverted desktop-app/installer work. Do not resume packaging
or the earlier UI redesign. Portable storage and coverage caching remain.
At handoff time http://127.0.0.1:8790/api/health refused connection. No server was
started for this documentation request.

The unresolved issue is execution performance. Several uploads were discussed;
earlier assistant replies confused them and gave unsupported time estimates.
Use the actual job request and uploaded source before diagnosing any run.

## Git and local changes

Branch: codex/strategy-contract-v2-market-data-path
HEAD: 70adcf5 Document dashboard strategy generation contract
team remote: https://github.com/Divo15/Trade-log-Analytics-Local.git
origin remote: https://github.com/Divo15/Trade-log-Analytics-DashBoard.git

Team main was pushed through 70adcf5 in the previous session. No remote check was
made today. aea50c1 contains the app/cache/sweep implementation; ddc3f6c documents
teammate setup.

Current changes:
- src/trade_log_dashboard/static/runner.js: uncommitted ETA wording.
- strategies/non.html: untracked user file; purpose not inspected, preserve it.
- This handoff is local (not listed by git ls-files).

Automatic approval review rejected committing/pushing the ETA wording directly
to team main without explicit shared-branch authorization. The user has not
answered that permission request. Do not push as part of unrelated work.

## Setup and storage

Run setup_environment.cmd once; it creates .venv and installs dependencies from
pyproject.toml. A separate requirements.txt is unnecessary. Run
start_dashboard.cmd, optionally with -NoBrowser. tools/start_dashboard.ps1 uses
the project Python, binds 127.0.0.1 and defaults to port 8790 (-Port overrides).
Use hidden windows for background starts and verify /api/health afterward.
Do not restart an active backtest without authorization.

Datasets and .venv are excluded from Git. Teammates must get the dataset separately,
set up their environment and configure their own parent folder through Storage.
The parent contains nifty current week, next week, next2week and nifty monthly.
Recent local runs selected data/db/nifty current week: 2 January 2023 through
5 May 2026, 760 trading dates. These are local Parquet files, not GitHub downloads.

Default persistent root: %LOCALAPPDATA%\TradeLogAnalytics, containing settings,
logs, cache and results. Settings: settings/settings.json. Dataset precedence:
TRADE_LOG_DATA_ROOT, saved path, project data/db, then user-storage datasets.
Coverage metadata persists and refreshes when source files change.

## Skill and strategy contract

Installed skill:
D:\CodexData\.codex\skills\dashboard-strategy-author\SKILL.md
Source: https://github.com/Divo15/codex-backtest-strategy-skill

The skill repository was made PUBLIC at the user's request on 9 September.
Do not confuse its visibility with the private team project repository.
Source and installed checkout updated to 31dbd60, removing the obsolete 500 cap.
Read this skill and referenced contract/checklist for strategy work.

Require STRATEGY_CONTRACT_VERSION='2', run_strategy(context), and RUN_MODE='single'
or 'sweep' with SWEEP_PARAMETER_SETS. Data must come from context.market_data.
Preserve entries, exits, sizing, hedges and fill assumptions. Return raw completed
trades and true engine-observed snapshots; dashboard owns exports and analytics.
Sweeps must be deterministic because recommendations are rerun. User explicitly
requires authorization before running strategies, installing their dependencies
or changing trading logic.

Project docs: integration/DASHBOARD_STRATEGY_GENERATION_PROMPT.md and
integration/STRATEGY_SCRIPT_CONTRACT.md. The Colab workflow is separate. Some
PRODUCT/design-era prose still describes reverted desktop packaging and is stale.

## Architecture and optimization limits

server.py: local endpoints/static assets/storage settings.
runner.py: uploads, subprocess lifecycle, timeouts, status, history and reruns.
worker.py: imports uploaded code and dispatches the supported interface.
market_data.py: optional worker-owned shared cache.
legacy.py: standalone StrategyConfig/DataLoader/ProtectedStraddleBacktester adapter.
analytics.py, equity.py and exporter package: validation and independent reports.
All dashboard modules are under src/trade_log_dashboard.

One active job at a time. Single timeout 30 minutes; sweep timeout seven days.
No fixed combination count limit. Latest five jobs are temporary per server
session. Only recommended winners persist. Sweep rows retain compact metrics;
recommended/manual selections rerun for full artifacts.

The worker supplies one context.market_data_loader per job. Defaults: 128 MiB
prepared-frame memory cache and 2 GiB disk cache. read_frame can reread cached
Parquet; prepare_frame fingerprints inputs and returns copies. This cache is
not persistent across jobs or winner reruns and does not automatically speed up
arbitrary scripts. Callers must use it. legacy.py currently does not use it.

## Verified performance investigation, 9 September

Temporary root was D:\CodexData\Temp\local-backtests-5x1u853n.
Files may disappear after cleanup; check availability.

### 60-combination script

Job bec187fede924dc1b3697bb16ec1ae8c:
nifty_6DTE_weekly_hard_sl_tp_two_reentries.py, 60 combinations, one 65-unit lot,
four legs. Cancelled. Initially mistaken for the teammate's different upload.

### Premium-selection script

Source: D:\Backend\static\admin\images\dashboard_e1r1_premium_selection_sweep.py
Jobs: 12980ce0e03e4bb5912f155a7eaa2633 and a42142616bdf493c974c09957534ca61.
Five combinations (10, 15, 20, 25, 30 percent), LOT_SIZE=65, MAX_ACTIVE_SLOTS=1.
Filters DTE=0; choose Weekly/current expiry. Four-slot wording is misleading.

Observed first job: combination 1/5 still incomplete after ~21 minutes,
~1264 CPU seconds and 4.16 GiB working set. No stack/phase profile was captured.
CPU use alone does not prove useful progress or locate the bottleneck.

Code concerns:
- Loads all Parquet rows on every variation.
- Applies scalar pd.to_datetime through map(_parse_datetime) BEFORE filtering
  DTE=0. This is a plausible major initial bottleneck, not yet measured.
- Scans chain by day, pivots CE/PE matrices, prepares forward fills and selects
  strikes minute by minute; retains prepared days in a list.
- Never calls context.market_data_loader.

Additional correctness concerns visible in reviewed code, not fixed/tested:
- close_slot checks reason 'SL' to schedule reentry, but callers pass
  'COMBINED_SL'; declared stop-loss reentry appears unreachable.
- _run_day resets realized P&L each day; run_strategy concatenates snapshots
  without cumulative offsets. This may fail reconciliation and misstate equity.
Do not claim full compatibility merely because the entry point exists.
No edits were made to this script. Previous 1–1.5-hour/2-hour ETAs were speculative.

### Latest standalone run completed

Job: 684c7edca65741789bd99d24e5f3543c
Upload: backtest_protected_straddle (1).py
Dispatch: standalone legacy.py adapter.
Final API status: succeeded.
Final log:
  Validating and analysing 648 completed legs…
  Worker elapsed: 1266.724s
  Market-data cache: read_hits=0, read_misses=0, prepared_hits=0, prepared_misses=0
  Cache retained: 0 memory bytes, 0 disk bytes

Total worker duration: 21 minutes 7 seconds. One active backtest was observed;
~5 GB RAM was available. No evidence established memory exhaustion or competing
backtest workers.
Uploaded SHA256:
79AB3C60CC589C86F3118F8605BA7F69767B109452DD818D1E8C5431F3D59BEA

Retained previous-day uploads inspected were differently hashed
nifty_6DTE_weekly_hard_sl_tp_two_reentries.py files. This cannot establish whether
the user ran the exact standalone file elsewhere yesterday.

Code concerns:
- _price_at filters the day's full DataFrame for every timestamp/strike/type.
  Every MTM check calls it for each leg.
- run() repeatedly filters the entire chain by date.
- Adapter uses its own DuckDB/Pandas loader; no shared-loader calls.
- No phase timings separate loading, engine execution and analytics. Profile
  before attributing exact runtime or claiming a regression cause.

## Pending ETA UI change

static/runner.js shows 'estimating time remaining after the first combination'
when ETA is null, rather than blank. After completion of a combination, the
existing backend average-runtime estimate appears. No first-combination progress
is measured by this change.
Checked with node --check, git diff --check, and HTTP GET /runner.js while the
server ran. Local and uncommitted. Potential follow-up: suppress estimating text
for cancelled/terminal jobs. Pushing requires the unanswered explicit permission.

## Next actions

1. Confirm actual uploaded source, dataset, config and status before diagnosis.
2. Profile the relevant execution path with bounded phase timings; avoid another
   unbounded full-data run just to obtain basic timing evidence.
3. For authorized execution-only fixes, investigate indexed quote access and
   reuse of immutable preparation. Include all relevant inputs in cache keys;
   never share portfolio/simulation state between combinations.
4. Compare raw trades, times, fills and snapshots on representative data before
   measuring gains. Preserve duplicate/missing-quote behavior in optimized lookups.
5. Address the premium script's correctness issues explicitly, separately from
   performance changes. Never silently alter trading rules.

Caching alone cannot explain/fix an expensive first combination. Do not repeat
unsupported ETAs or assume the user's claim of identical scripts is disproven
by the limited retained uploads.

## Validation reference

An earlier session recorded 72 passing tests in 18.776 seconds. Tests were not
rerun for this documentation-only handoff. For code changes run appropriate tests:
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

Read README.md, legacy.py, market_data.py, worker.py, runner.py and the actual
uploaded script before continuing. Preserve unrelated files; do not reset/clean.
