# Trade-log Analytics Dashboard - Project Handoff

Updated: 2026-10-19  
Primary working branch: `alternate-sorting`  
GitHub repository: `Divo15/Trade-log-Analytics-DashBoard`

This document is for the next developer or coding agent. It records product intent,
architecture, operating steps, important implementation distinctions, known
checkout risks, and a safe starting sequence. Paths below are repository-relative;
no developer-specific machine paths are required.

## 1. Project in one paragraph

Trade-log Analytics is a local-first Windows dashboard for running trusted Python
backtests, analyzing existing trade CSVs, ranking large Parquet sweep outputs, and
researching time-diversified groups from daily P&L data. The key current product
goal is to compare groups of otherwise identical strategies that differ only by
entry or cutoff time, then rank groups using combined profitability, drawdown, and
cross-time daily-P&L correlation. The dashboard is a research/screening tool, not
proof that a strategy is live-safe or equivalent to a broker/backtesting platform.

## 2. Product intent and decisions from the user

The workflow evolved from sorting a very large sweep to explicitly evaluating
time diversification. Preserve these decisions when changing ranking code or UI:

- A time-diversified group must have the same fixed strategy parameters for every
  member. Time is the only varying strategy parameter.
- The goal is to rank groups, not just rank the best individual combination.
- P&L and drawdown matter, and correlation is important because the goal is
  diversification across entry/cutoff times.
- Correlation is computed between the member strategies' daily P&L series over
  the same dates. A strategy's P&L on Monday and Tuesday are two observations in
  the same series; they are not two different strategies.
- A group with `n` members has `n * (n - 1) / 2` pairwise correlations. For
  example, 10 time variants create 45 pairs. The group correlation metric is the
  average of the valid pairwise correlations; it is not a sum over dates or
  correlations across unrelated calendar days.
- A high-scoring group is not automatically the best standalone strategy. It is
  a portfolio-style screening result and should be examined across years and
  validated under reconciled execution assumptions before selection.
- Keep UI labels and empty-state explanations explicit about which ranking is
  being run and which columns/filters make a group eligible.

The handwritten project note also records: variable P&L/drawdown ranking does not
include correlation; correlation-based ranking is the separate “Rank +
Correlation” workflow; Storage is for previously saved combinations; changes were
associated with `alternate-sorting`; and internal Python-script execution was
removed. The last statement conflicts with this checkout's README and UI, which
still implement local Python backtests. Confirm which behavior is intended before
removing or restoring the local runner.

## 3. Main user-facing workflows

### Local backtest

The user chooses a Python strategy, a locally configured dataset, and execution
settings, then runs it in a local worker. Results include validated trades and
analytics. Python code runs with the user's permissions: subprocess separation is
not a security sandbox. Only run trusted scripts.

### Analyze an existing trades CSV

The user uploads an existing canonical trade CSV and reviews calculated analytics.
The dashboard derives P&L from raw trade fields; P&L is not an authoritative input
field in canonical schema v1. The schema/exporter validates the execution records
and can produce a checksum manifest.

### Rank a Parquet sweep

The user inspects a Parquet schema, maps available ranking fields, previews
eligibility, sets optional filters and ranking criteria, then ranks the eligible
combinations. Filters can remove rows before ranking; an empty result is not
necessarily a ranking failure. Check status, P&L/drawdown mappings, filter values,
parameter filters, and the rejection report.

The sweep interface also includes a same-fixed-parameters time-group ranking.
`rank_sweep_time_groups` chooses a fixed number of top-scoring profitable members
per parameter family, then ranks groups using average member P&L and the worst
member drawdown (default 60% / 40%). It does not calculate daily-P&L correlation.
Do not describe this as the correlation-aware ranking.

### Rank + Correlation: daily-P&L groups

The daily-P&L workflow is the correlation-aware analysis. It accepts daily P&L
records and groups rows by the selected fixed-parameter columns. It ranks groups
by weighted percentiles for combined P&L (higher is better), combined maximum
drawdown (lower is better), and average pairwise daily Pearson correlation (lower
is better). Default weights are 40% / 30% / 30%; they must total 100%.

The groups this user requested contain 2-10 available time variants and use
5-, 10-, 15-, or 20-minute spacing. The code generates arithmetic sequences of
available times for each parameter family and interval; it does not search every
arbitrary subset of times. It can therefore return groups with fewer than 10
members when a sequence has fewer available variants. A hard cap of 250,000
candidate groups protects the local process.

### Saved history and Storage

Storage configures the local market-data parent folder and is also where users
view persisted saved combinations/results. Local settings, logs, cache, and saved
results are per-user data; they are not part of the repository. `Best history`
reopens supported saved backtest reports. Confirm the exact save/history scope in
`storage.py`, `runner.py`, and the UI before promising persistence for a new result
type.

## 4. Ranking semantics and data assumptions

### Daily P&L correlation implementation

The current implementation is in `src/trade_log_dashboard/time_diversification.py`
(`rank_daily_time_groups`):

1. Inspect the Parquet schema and suggest mappings for date, daily P&L, and time.
2. By default, every remaining column is treated as a fixed strategy parameter.
   The UI lets the user choose these explicitly. Include every field that defines
   the strategy, and remove only identifiers/result-only columns that should not
   split otherwise identical strategies.
3. Convert dates to calendar dates and parse times as `HH:MM`, timestamp-like
   values, numeric `HHMM`, or integer minutes after midnight. Invalid rows are
   discarded. Additive rows with the same parameter values, time, and date are
   summed.
4. Build one daily series per parameter-family/time pair. The date index is
   aligned across the input; absent strategy-time/date rows are filled with zero
   P&L. Missing days therefore affect both the cumulative group P&L and the
   correlation calculation.
5. For each allowed interval and available starting time, build the contiguous
   sequence at that exact interval, up to 10 members. Sequences shorter than two
   are ignored. Require the configured minimum date count (UI default 30); the
   backend accepts values from 2 to 10,000.
6. Compute Pearson correlation for each pair of member daily series and average
   the valid pairs. The result includes `pair_count`; a group with 10 members has
   at most 45 pair values. If no pair meets the minimum-overlap condition, that
   candidate is excluded.
7. Sum member P&L by date to form the group series. Combined maximum drawdown is
   calculated from cumulative group daily P&L, with the initial zero balance
   treated as a peak.
8. Convert each group metric to a percentile score, reverse the drawdown and
   correlation directions, apply user weights, and sort by score. Ties prefer
   higher combined P&L, then lower drawdown, then lower correlation. The endpoint
   supports top-N from 1 to 100; the UI displays Top 20 by default.

Do not silently change the missing-day policy, minimum-overlap rule, group
generation, fixed-parameter key, or score direction. Each can change the winners.
If any of them changes, update tests and explain the effect in the UI and this
handoff.

### Sweep-Parquet time groups are different

`rank_sweep_time_groups` in `sweep_schema.py` is a different calculation:

- It groups by inferred non-result parameter columns and selects exactly the
  configured `group_size` of eligible member rows (default 10 in the API call
  path; verify the caller before changing defaults).
- A member must have succeeded status, positive net P&L, a valid entry time, and
  a mapped drawdown value. Optional filters can reduce the eligible members.
- Each member is scored using P&L and drawdown percentiles; the default weighting
  is 60% P&L and 40% lower drawdown. The best members form the group.
- Groups are ranked by average member P&L and worst member drawdown, by default
  60% / 40%. It does not use daily series or correlation and does not enforce the
  daily workflow's 5/10/15/20-minute spacing rule.
- It requires the exact group size. Unlike daily-P&L groups, it may return no
  result when a parameter family has fewer than the required number of eligible
  time rows. Filters are a common cause of this.

The two views should remain separately named and explained. A time-group preset
that selects sweep members is not a substitute for a correlation-aware daily-P&L
analysis.

## 5. Architecture map

The application uses a Python local HTTP server and a static browser interface.
The server binds to loopback; `start_dashboard.cmd` starts it and normally opens
the browser. Keep its terminal open while using the dashboard; stop with Ctrl+C.

| Area | Repository files | Responsibility |
| --- | --- | --- |
| Local HTTP/API | `src/trade_log_dashboard/server.py` | Routes, upload validation, settings, sweep and correlation requests, saved rankings. |
| Browser shell | `src/trade_log_dashboard/static/index.html`, `app.js`, `app.css` | Navigation, layout, global UI state and styling. |
| Backtest UI | `src/trade_log_dashboard/static/runner.js` | Local runs, progress, CSV analysis, Parquet inspection/ranking, daily-P&L groups. |
| Local execution | `runner.py`, `worker.py`, `validate_strategy.py` | Job lifecycle, subprocess worker, strategy contract and validation. |
| Analytics | `analytics.py`, `equity.py` | Trade/equity metrics and report data. |
| Sweep ranking | `sweep.py`, `sweep_schema.py`, `rejection_report.py` | Sweep outputs, mappings, filters, ranking, robustness and Parquet time groups. |
| Daily correlation | `time_diversification.py` | Daily P&L mappings, time-group candidates, drawdown and Pearson correlation. |
| Local state/data | `storage.py`, `datasets.py`, `market_data.py` | Per-user paths, settings, logs, coverage cache and dataset catalog. |
| Trade export | `src/trade_log_exporter/` | Canonical trade schema, validation, atomic export and checksum manifest. |
| Test suite | `tests/` | Unit, integration and UI checks; see the tests relevant to the area being changed. |

Important API endpoints include:

- `POST /api/sweep-schema`, `/api/sweep-preview`, `/api/sweep-rank`,
  `/api/sweep-export`, and `/api/sweep-save`.
- `POST /api/daily-pnl-schema` and `/api/time-diversification-rank`.
- Backtest job operations under `/api/backtests...` and CSV analysis at
  `/api/analyze`.
- Settings are updated through `/api/settings`.

The server checks local request origin/host and requires a custom header for
state-changing operations. Preserve these checks when changing routes.

## 6. Setup and data

On Windows, use Python 3.11, 3.12, or 3.13. From the repository root:

```bat
git lfs install
git lfs pull
setup_environment.cmd
start_dashboard.cmd
```

Use the browser tab opened by the startup command. In **Storage**, set the
absolute path to the parent dataset folder; do not commit that personal path.
The README documents the expected NIFTY weekly/monthly and SENSEX current-week
folder layout. SENSEX summary/chain data is delivered through Git LFS; local NIFTY
datasets are excluded from Git and must be supplied separately. The dataset
catalog caches file fingerprints so unchanged datasets avoid unnecessary scans.

Per-user application settings, logs, caches, and results use the platform's user
application-data directory (Windows: `%LOCALAPPDATA%\TradeLogAnalytics` unless
overridden with `TRADE_LOG_ANALYTICS_HOME`). Local market data stays in the folder
selected in Storage.

Backtest strategy code follows Strategy Contract v2: import-safe
`run_strategy(context)`, explicit `RUN_MODE`, market data accessed through
`context.market_data`, and authoritative completed-trade output. Sweeps declare
their parameter mappings and run sequentially; failures are retained per row.
The dashboard reruns the selected winner and checks key metrics for deterministic
reproduction. Read `AGENTS.md` and the four integration contracts before generating
or editing strategy code. For SENSEX execution or AlgoTest comparisons, also read
the required reconciliation note referenced by `AGENTS.md`.

## 7. Safety, limits, and interpretation

- Uploaded Python is trusted code running with the user's account permissions;
  there is no security sandbox.
- The runner is local and single-job. README documents one running job, a
  30-minute single-run deadline, a longer sweep deadline, and upload/extraction
  limits; check the current constants before changing or repeating them here.
- Data is not downloaded by the dashboard. Large Parquet inputs should be read
  locally. The current sweep Parquet limit is configured in `server.py`.
- CSV analytics are based on supplied executions and fees. They do not prove
  that fills, costs, or backtests are authentic or unbiased.
- The daily correlation rank is a historical descriptive statistic, not a
  forward-looking guarantee. It is sensitive to sample dates, missing-day-as-zero
  assumptions, filters, parameter mappings, and ranking weights.
- Treat sweep/correlation output as a shortlist. Validate with holdout or
  walk-forward data and reconcile actual execution rules before relying on it.

## 8. Current branch and repository state

Snapshot observed on 2026-10-19:

- Local branch: `alternate-sorting`, local HEAD `91f9e09` (`Baseline time
  diversification implementation`).
- Git reported the local branch one commit ahead and three commits behind its
  configured tracking ref. Here, `origin` points to a local sibling checkout,
  not the GitHub URL. Do not use that tracking status as proof of GitHub state.
- The working tree was dirty. Changes included Research feature removals,
  modifications to server/UI/sweep ranking, and untracked
  `time_diversification.py` plus its test. `Handoff.md` was already modified and
  contains earlier SENSEX research notes and a dashboard-photo note.
- Previous handoff notes record GitHub commit `2cf583e` as the Research-removal
  push. Confirm the current GitHub branch head before claiming that code changes
  are published. The Markdown handoff is being published separately; that does
  not publish the dirty application-code changes.
- Do not run `git reset --hard`, `git clean`, broad checkout, or blind pulls in
  this worktree. Preserve the user's modified files and inspect changes first.
- Do not stage the generated PDF, datasets, runtime folders, caches, or unrelated
  SENSEX outputs when committing documentation.

## 9. Documentation map and known drift

- `README.md` is the main setup and user-operating guide. Prefer it for the
  current local runner workflow.
- `ARCHITECTURE.md` contains older statements that the website does not execute
  Python and begins only at CSV upload. Those statements conflict with the
  current local backtest UI and runner; treat that document as stale until it is
  reconciled with the code.
- `LIMITATIONS.md` records known analytics and operational constraints. Verify
  details against current code before repeating exact limits.
- `AGENTS.md` contains mandatory instructions for future strategy work and
  SENSEX/AlgoTest comparisons.
- `Handoff.md` is an older SENSEX strategy/sweep handoff, not the primary
  dashboard architecture guide. It also has the handwritten-photo dashboard
  notes. Keep its SENSEX execution guidance intact.
- `DASHBOARD_HANDOFF.md` is a shorter dashboard summary. This file is the fuller
  coding-agent context, including unresolved local-versus-GitHub status.

## 10. Safe first steps for the next agent

1. Read `AGENTS.md`, `README.md`, this file, and the relevant integration
   contract. For a SENSEX task, read the execution reconciliation document too.
2. Check `git status`, current branch, and both local and GitHub branch heads.
   Remember this checkout's `origin` is a local sibling path.
3. Inspect the exact files/tests for the task before editing; preserve unrelated
   changes already in the worktree.
4. For time-group bugs, reproduce with a small synthetic Parquet fixture and
   check schema mapping, fixed-parameter columns, time parsing, available
   intervals, candidate counts, minimum overlap, and filters separately.
5. Keep the two time-group workflows separate in logic, UI, docs, and tests:
   sweep grouping is P&L/drawdown-only and exact-size; daily-P&L grouping uses
   date-aligned correlation and supports available group sizes from 2 to 10.
6. Run the focused tests first, then the applicable broader validation from the
   README. Record what was and was not run; do not imply tests passed if skipped.
7. Commit only files explicitly in scope. Never include datasets, local settings,
   saved results, runtime output, or unrelated dirty changes.

## 11. Useful focused tests

- `tests/test_sweep_ranking.py` — sweep schema, filtering, ranking and group
  behavior.
- `tests/test_time_diversification.py` — daily P&L mapping, time parsing,
  parameter grouping, missing-day handling, interval sequences, correlation and
  ranking.
- `tests/test_local_runner.py` — local execution and job lifecycle.
- `tests/test_datasets.py` and `tests/test_storage.py` — dataset catalog and
  per-user persistence.
- `tests/test_dashboard_analytics.py` and `tests/test_equity.py` — trade/equity
  analytics.
- `tests/test_roi.cjs` — browser/UI regression checks.

Use the test runner configured in the current environment. `pyproject.toml`
configures pytest's test discovery, while the README also documents unittest
discovery; verify which dependencies are installed before selecting a command.


