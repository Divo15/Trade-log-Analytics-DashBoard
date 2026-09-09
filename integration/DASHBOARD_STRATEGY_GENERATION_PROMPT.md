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
2. Declare RUN_MODE = "single" for one backtest. For an optimization, declare
   RUN_MODE = "sweep" and a non-empty SWEEP_PARAMETER_SETS sequence containing
   every exact requested parameter mapping. Build the complete Cartesian product
   when the request supplies value lists. Do not impose an artificial combination
   limit and do not rank or choose a winner inside the strategy.
3. Expose run_strategy(context). It receives context.run_id,
   context.market_data, and context.config.
4. Read market data only from context.market_data. Never embed a local path,
   download data, or look for another dataset folder.
5. Read current strategy parameters from context.config["parameters"] and make
   every declared sweep key affect or validate a genuine strategy setting.
6. Keep the requested strategy behavior unchanged. Do not simplify or invent
   entries, exits, hedges, sizing, stops, targets, re-entries, or fill rules.
7. Importing the module must not run a backtest, inspect files, load data,
   install packages, prompt for input, access the network, or write outputs.
8. Return the engine's authoritative completed closed trades or legs and their
   exact count. Return raw execution fields only: no P&L, drawdown, win rate,
   ranking, or analytics. Return observed equity snapshots only when the engine
   actually recorded them.
9. Let errors propagate. Do not convert an error into a fake empty result or
   partial success.
10. Do not write trades.csv, equity.csv, manifests, analytics, or dashboard
    output. The dashboard worker owns those files.

OUTPUT
Return the complete Python module and then a brief note listing expected data
schema, dependencies, fill assumptions, and any remaining limitations.
```

Before uploading, verify the strategy against
[Strategy Script Contract v2](STRATEGY_SCRIPT_CONTRACT.md).
