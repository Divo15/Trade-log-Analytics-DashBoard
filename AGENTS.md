# Codex project instructions

## Python strategy generation

Whenever a user asks to create, modify, optimize, or review a Python trading
strategy for this dashboard, read these files before changing or generating
strategy code:

1. [`integration/DASHBOARD_STRATEGY_GENERATION_PROMPT.md`](integration/DASHBOARD_STRATEGY_GENERATION_PROMPT.md)
2. [`integration/STRATEGY_SCRIPT_CONTRACT.md`](integration/STRATEGY_SCRIPT_CONTRACT.md)
3. [`integration/EQUITY_SNAPSHOT_CONTRACT.md`](integration/EQUITY_SNAPSHOT_CONTRACT.md)
4. [`integration/SWEEP_SUMMARY_CONTRACT.md`](integration/SWEEP_SUMMARY_CONTRACT.md)

Treat the user's stated trading rules and supplied engine reference as the
source of truth. Do not invent missing entry, exit, sizing, stop, target,
re-entry, hedge, fill, cost, or data rules. If a required rule or engine API is
missing, identify it clearly before claiming the strategy is complete.

Generated dashboard strategies must follow Strategy Contract v2, including an
import-safe `run_strategy(context)`, explicit `RUN_MODE`, market data read only
from `context.market_data`, and authoritative raw closed-trade output. For a
sweep, include every exact requested parameter combination and make every key
affect or validate a real strategy setting.

Before delivery, run the repository validation workflow described in the
generation prompt against a representative dataset when one is available.
Report any validation, data, engine, fill-model, dependency, or performance
limitation honestly.

## SENSEX AlgoTest reconciliation rules

For every future SENSEX backtest, sweep, candidate rerun, ranking, or
AlgoTest comparison, treat
`D:\Backend\static\admin\images\SENSEX_ALGOTEST_EXECUTION_RECONCILIATION.md`
as required context. Read it before changing execution logic or reporting final
strategy performance.

Use the reconciled AlgoTest-style execution assumptions unless the user
explicitly asks for the older fast-screening sweep behavior:

1. Scheduled first entry uses the previous completed candle reference.
2. Closest-premium strike selection is based on the execution reference candle.
3. Same-timestamp combined re-entry uses the current candle/current ATM
   reference.
4. Short-option leg SL trigger uses candle high.
5. Leg SL fill is the stop threshold price, not candle close.
6. Combined SL/target exits close remaining open legs at the current candle
   close.
7. Combined SL/target may re-enter on the same timestamp when re-entry is
   available.
8. Net AlgoTest comparison uses 0.5% slippage: short entry adjusted down and
   short exit adjusted up.
9. Platform-matched exit time is preferred; use 15:14 or the matched available
   exit bar instead of blindly assuming 15:15.
10. Compare only common 0DTE dates available in both local data and AlgoTest
    exports.

Treat broad sweep results as screening output only. Final strategy selection
should be based on an AlgoTest-equivalent rerun of shortlisted combinations
with yearly P&L, year-on-year drawdown, net slippage-adjusted metrics, and
the corrected execution assumptions above.
