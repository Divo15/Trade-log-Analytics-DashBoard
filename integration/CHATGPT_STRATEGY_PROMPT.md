# Reusable ChatGPT Colab Strategy-Generation Prompt

Use this prompt when asking ChatGPT to generate a Python trading strategy for
the standard Google Colab runner. Supply the real engine reference and the
complete strategy request; never ask the model to invent an engine API.

## Prompt

```text
Generate one complete Google Colab-compatible Python trading-strategy module
that complies with Strategy Script Contract v2 below.

STRATEGY REQUEST
<Describe the complete intended strategy, including indicators, entries,
exits, sizing, stops, targets, re-entry rules, session rules, and parameters.>

SUPPORTED BACKTESTING ENGINE REFERENCE
<Paste the real engine API, a known-good example, or adapter documentation.
Do not guess or substitute another engine.>

COLAB CONTEXT REFERENCE
The standard Colab notebook calls run_strategy(context) once. It supplies:
- context.run_id: unique string for the run;
- context.market_data: read-only market data selected from a Colab upload or
  Google Drive; and
- context.config: read-only mapping with instrument, period, execution, and
  parameters sections.

REQUIRED MODULE BOUNDARY
1. Declare STRATEGY_CONTRACT_VERSION = "2".
2. Declare RUN_MODE = "single" when the request is one backtest, or
   RUN_MODE = "sweep" when the request is a parameter sweep, grid search, or
   optimization. Choose from the user's request, never from runtime results.
3. Declare SWEEP_PARAMETER_SETS = () for single mode. For sweep mode, declare
   a non-empty sequence containing one exact parameter mapping per requested
   iteration. Do not add, remove, or tune parameter values.
4. Declare DEFAULT_MARKET_DATA_PATH = None unless the user explicitly supplied
   an exact Colab Google Drive path. If supplied, reproduce that string exactly
   without inventing, inferring, normalizing, relocating, or changing it. Only
   `/content/drive/MyDrive/...` and `/content/drive/Shareddrives/...` paths are
   permitted; reject Windows, macOS/Linux desktop, and arbitrary server paths.
5. Expose exactly one public execution entry point named
   run_strategy(context).
6. Importing the module must not execute the backtest, install packages, mount
   Google Drive, inspect or access the filesystem, load market data, access the
   network, prompt for input, or write output.
7. DEFAULT_MARKET_DATA_PATH is a passive declaration for the trusted notebook;
   never open, validate, or otherwise consume it inside the strategy module.
8. Use context.market_data as the only market-data input. Make a local copy if
   the engine or indicators need mutation.
9. Read run settings from context.config. Do not silently override supplied
   capital, fees, slippage, fill, multiplier, instrument, timeframe, or period.
10. Preserve the user's intended strategy exactly. Colab compatibility and
   RUN_MODE selection must not
   simplify, replace, omit, reinterpret, or tune indicators, signals, entries,
   exits, re-entry, sizing, stops, targets, multi-leg behavior, or state.
11. When modifying or sweeping an existing strategy, treat every baseline
    constant as locked unless the user explicitly names it as a sweep parameter.
    This includes active-slot limits, total fresh-slot limits, lot quantity,
    capital or margin behavior, entry time, expiry filter, premium-selection
    rule, stop, target, re-entry delay and limit, slippage, fees, and fill model.
12. A sweep may change only the keys declared in SWEEP_PARAMETER_SETS. Name each
    key after the rule it actually changes; do not use a strike-selection
    parameter to imply an entry-trigger change, and do not alter unrelated
    constants to make a sweep faster.
13. If capital, margin, or exposure is described as a limit, implement it or
    state clearly that it is descriptive only. Keep simultaneous active-slot
    limits distinct from a lifetime cap on fresh slot IDs.
14. Use context.config execution values when the engine supports them. If fixed
    slippage, fees, multiplier, timezone, or fill assumptions are required,
    validate conflicting configuration and fail clearly rather than silently
    ignoring it.
15. Use one canonical exit-reason value throughout each stop and re-entry path.
    A mismatch must never make a stated re-entry rule unreachable.
16. For a sweep derived from a baseline strategy, verify the combination with
    baseline parameter values against the baseline on a bounded representative
    sample. Completed trades and observed equity snapshots must match exactly
    before claiming parity.
17. Performance improvements may cache only immutable market-data reads or
    deterministic prepared frames. Never cache positions, fills, P&L, trades,
    or equity. Verify cached and uncached results match for every sweep
    combination on a bounded representative sample.
18. If reporting losing days or a daily loss streak, aggregate portfolio P&L by
    chronological evaluated expiry day. A losing day is below zero and a daily
    streak resets on profitable or zero-P&L evaluated days. Keep this separate
    from a losing-batch streak.
19. Use only normal Python imports in the module. List any additional
   Colab-installable packages in source comments; installation belongs in the
   notebook setup cell.

COMPLETED-TRADE RETURN
After the supported engine finishes, return:

{
    "completed_trades": <the engine's actual authoritative closed-trade,
                         closed-position, or closed-leg collection>,
    "completed_trade_count": <the authoritative count for that collection>,
    "trade_mapper": <a callable mapping one actual engine trade and its index
                     to canonical raw-execution fields, or None when the
                     notebook's trusted engine adapter applies>,
    "metadata": {
        "strategy_name": "<descriptive name>",
        "engine": "<the supported engine name>"
    }
}

Never infer, reconstruct, fabricate, or supplement completed trades. A real
zero-trade result must return the real empty collection and count zero.

ROW-ORIENTED RETURN REQUIREMENT
`completed_trades` must iterate over actual trade rows. Never return a pandas
DataFrame directly. For a DataFrame or another column-iterating table:

1. Store its authoritative count before conversion.
2. Convert it to a row-oriented list without changing values, for example with
   `to_dict(orient="records")`.
3. Verify the normalized row count equals the authoritative engine count.
4. Return those rows and the original authoritative count.

The mapper may emit only:
schema_version, run_id, trade_id, batch_id, leg_id, strategy, symbol, side,
entry_time, exit_time, quantity, entry_price, exit_price, multiplier, fees.

Do not calculate or return P&L, returns, equity, drawdown, Sharpe, win rate,
profit factor, or dashboard data. For single runs the website calculates them
from `trades.csv`; for sweeps the trusted `export_sweep_summary()` package
calculates them from each iteration's validated canonical trades.

ERROR RULES
- Let engine, data, configuration, dependency, and mapping failures propagate.
- Do not return partial trades, a fake empty result, or a success flag after an
  error.
- If the engine reference or Colab-compatible dependency information is
  insufficient, do not change the strategy or invent an API. Raise
  NotImplementedError with a precise explanation of what is missing.

OUTPUT FORMAT
Return only the complete Python module, without Markdown fences or prose.
```

## Before using the module in Colab

Verify that:

- Strategy Script Contract v2 was applied;
- RUN_MODE correctly identifies the user-requested run type;
- SWEEP_PARAMETER_SETS exactly matches the requested sweep, or is empty for a
  single backtest;
- DEFAULT_MARKET_DATA_PATH is None unless the user explicitly supplied the
  exact permitted Colab Drive path;
- every requested strategy rule is preserved;
- baseline constants are unchanged unless explicitly declared as sweep
  parameters;
- a derived sweep's baseline combination exactly matches the baseline strategy
  on a bounded representative sample;
- cached and uncached sweep results match on that sample;
- active-slot, fresh-slot, capital, margin, quantity, cost, and fill behavior
  are explicit and match the request;
- the engine API came from the supplied reference;
- dependencies are available in the selected Colab runtime;
- no desktop, arbitrary server, or invented path is embedded in the module;
- the result uses the engine's authoritative completed trades;
- tabular trades are normalized to rows; and
- the mapper contains execution facts, not analytics.
