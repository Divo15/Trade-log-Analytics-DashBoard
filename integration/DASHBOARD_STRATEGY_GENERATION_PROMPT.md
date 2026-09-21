# Trade-log Analytics Dashboard Strategy-Generation Prompt

Use this prompt when asking Codex or another coding agent to create, modify, or
review a Python strategy that will run in the local Trade-log Analytics
Dashboard. Supply the complete trading rules and a real engine reference. Do
not ask the agent to invent an engine API.

```text
Create, modify, or review one self-contained Python strategy module for the
Trade-log Analytics Dashboard.

STRATEGY REQUEST
<Describe all entries, exits, hedges, sizing, stops, targets, re-entries,
session rules, fills, costs, and parameters.>

SUPPORTED ENGINE REFERENCE
<Paste the real engine API, a known-good strategy, or adapter documentation.>

REQUIRED DASHBOARD CONTRACT
1. Declare STRATEGY_CONTRACT_VERSION = "2".
2. Declare RUN_MODE = "single" for one backtest. For a requested parameter search, declare
   RUN_MODE = "sweep" and a non-empty SWEEP_PARAMETER_SETS sequence containing
   every exact requested parameter mapping. Build the complete Cartesian product
   when the request supplies value lists. Do not impose an artificial combination
   limit and do not rank or choose a winner inside the strategy. A request to
   improve execution speed alone must preserve the existing RUN_MODE and grid.
3. Expose run_strategy(context). It receives context.run_id,
   context.market_data, and context.config.
4. Read market data only from context.market_data. Never embed a local path,
   download data, or look for another dataset folder.
5. Read current strategy parameters from context.config["parameters"] and make
   every declared sweep key affect or validate a genuine strategy setting.
6. Keep the requested strategy behavior unchanged. Do not simplify or invent
   entries, exits, hedges, sizing, stops, targets, re-entries, or fill rules.
   For an optimization of an existing strategy, treat its baseline constants
   as locked unless the request explicitly names them as sweep parameters.
   This includes active-slot limits, maximum slot IDs, lot quantity, capital or
   margin model, entry time, expiry filter, premium-selection rule, stop,
   target, re-entry delay and limit, slippage, fees, and fill model.
7. A sweep changes only the keys declared in SWEEP_PARAMETER_SETS. Give each
   key a name that describes the rule it changes. For example, a parameter
   selecting strikes by premium must not be named as though it changes an
   entry trigger. Do not alter unrelated constants to speed up a sweep.
8. If the strategy describes a capital, margin, or maximum-exposure limit,
   implement that limit or state clearly that it is descriptive only. Distinguish
   a maximum number of simultaneously active positions from a lifetime cap on
   fresh slot IDs; do not substitute one for the other.
9. Use the execution values supplied in context.config when the supported
   engine permits it. If a strategy intentionally requires fixed slippage,
   fees, multiplier, timezone, or fill assumptions, validate conflicting
   dashboard values and fail clearly rather than silently ignoring them.
10. Preserve the exact stop and re-entry state transitions. Use one canonical
   exit-reason value at the producer and every consumer; do not test for an
   alternate string that can make a stated re-entry path unreachable.
11. Importing the module must not run a backtest, inspect files, load data,
   install packages, prompt for input, access the network, or write outputs.
12. Return the engine's authoritative completed closed trades or legs and their
   exact count. Return raw execution fields only: no P&L, drawdown, win rate,
   ranking, or analytics. Return observed equity snapshots only when the engine
   actually recorded them.
13. Let errors propagate. Do not convert an error into a fake empty result or
   partial success.
14. Do not write trades.csv, equity.csv, manifests, analytics, or dashboard
    output. The dashboard worker owns those files.
15. For a sweep derived from a baseline strategy, include a verification step:
    run the sweep combination whose parameters equal the baseline values on a
    bounded representative sample and assert that completed trades and equity
    snapshots exactly match the baseline. Do not claim parity without this
    comparison.
16. Performance work may cache only immutable market-data reads or deterministic
    prepared market-data frames. It must never cache positions, trade state,
    fills, P&L, or equity. Validate cached and uncached results for every sweep
    combination on a bounded representative sample before delivery.
17. Do not describe a loss streak ambiguously. If a report needs losing days,
    calculate daily portfolio P&L by chronological evaluated expiry day; a day
    is losing only when its aggregate P&L is below zero, and a daily streak
    resets on profitable or zero-P&L evaluated days. Keep this distinct from a
    consecutive losing-batch streak.
18. For a new strategy, use its own specified rules; do not impose NIFTY E1-R1
    slots, lot sizes, expiry filters or signals from an unrelated example.
    Distinguish speeding up execution from requesting a parameter search.
    Run the repository's validation command against a representative sample:
    python -m trade_log_dashboard.validate_strategy strategy.py --market-data
    <sample-dataset> --output <new-check-folder> --timeout 120
    Include the signal history and execution days required by the strategy.
    Inspect validation.json and every sweep outcome. Report missing data or
    unavailable validation honestly; do not declare success from import-only
    or signal-direction tests. Final equity must reconcile across all sessions
    and LONG/SHORT legs, including fees and quantities.
19. For a large sweep, generate an optimized execution design in addition to
    the auditable `run_strategy(context)` path. Load market data once through
    `context.market_data`, prepare immutable NumPy arrays, precompute option
    selections for every required timestamp, side, and premium target, and
    process parameter mappings in bounded batches. Where the numeric state
    loop is compatible with Numba, implement it as a small `@numba.njit`
    function that accepts only numeric arrays and scalar settings. Do not put
    Pandas objects, dictionaries, file I/O, progress callbacks, or trade-log
    writing inside that compiled function.
20. Numba is an optimization, not permission to alter trading behavior. The
    generated module must keep the normal Python path for a selected-combination
    rerun and compare representative combinations from the compiled batch path
    against that path. Compare completed trade count, every entry/exit fill,
    final P&L, and final observed equity. If parity is not demonstrated, use
    the normal path and state that the batch path is unavailable.
21. Make batch size configurable through a visible strategy constant, choose a
    bounded default that controls RAM, and release each batch's temporary
    numeric arrays before starting the next one. Never fabricate trade rows or
    metrics from a vectorized approximation. Any batch result shown by the
    dashboard must still be derived from the strategy's actual completed trades
    and observed equity.
22. When parameter combinations are independent, use controlled CPU
    parallelism inside the numeric batch kernel with `@numba.njit(parallel=True)`
    and `numba.prange`, rather than allowing uncontrolled Python workers to
    duplicate the market-data cache. Make the worker count configurable, cap it
    to the available CPU cores, preserve deterministic index ordering, and
    ensure each combination owns its own numeric state and output slot. Do not
    parallelize across shared mutable positions, fills, trade lists, equity
    snapshots, progress files, or dashboard result storage. Compare parallel
    and one-worker outputs before enabling more than one worker.
23. Use pruning or staged search only when the strategy request explicitly
    permits an exploratory search that evaluates fewer than every requested
    combination. Keep it separate from an exhaustive sweep: first run a coarse
    grid or bounded screening period, retain promising parameter regions using
    stated risk limits, then run a finer grid and validate finalists on the
    complete period. Label exploratory results clearly. If the request declares
    an exact Cartesian grid or requires every combination, do not prune, skip,
    sample, or infer untested results.
24. For an explicitly permitted high-volume screening pass, the compiled batch
    kernel may retain only provisional numeric outputs such as net P&L, trade
    count, win/loss totals, and observed drawdown. It must still execute every
    strategy rule. It may skip materializing trade rows and equity snapshots,
    but must not present those provisional values as trusted analytics, save
    them, or make a final recommendation. Rerun shortlisted combinations with
    the normal `run_strategy(context)` path and require full trade/equity
    validation before treating any result as final.

OUTPUT
Return the complete Python module and then a brief note listing expected data
schema, dependencies, fill assumptions, fixed execution assumptions, capital or
exposure behavior, sweep keys, and any remaining limitations. For a derived
sweep, also state the exact baseline combination used for parity verification.
```

Before uploading, verify the strategy against
[Strategy Script Contract v2](STRATEGY_SCRIPT_CONTRACT.md).
