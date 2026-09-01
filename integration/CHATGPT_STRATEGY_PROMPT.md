# Reusable ChatGPT Strategy-Generation Prompt

Use this prompt when asking ChatGPT to generate a Python trading strategy for
the future pasted-script runner. Supply the supported engine reference and the
strategy request; do not ask the model to invent an engine API.

## Prompt

```text
Generate one complete Python trading-strategy module that complies with
Strategy Script Contract v1 below.

STRATEGY REQUEST
<Describe the complete trading strategy here, including indicators, entries,
exits, sizing, stops, targets, re-entry rules, session rules, and parameters.>

SUPPORTED BACKTESTING ENGINE REFERENCE
<Paste the real engine API, a known-good example, or the adapter documentation
here. Do not guess or substitute another engine.>

PLATFORM CONTEXT REFERENCE
The platform calls run_strategy(context) once. The supplied context contains:
- context.run_id: unique string for the run;
- context.market_data: read-only market data selected by the platform; and
- context.config: read-only mapping with instrument, period, execution, and
  parameters sections.

REQUIRED MODULE BOUNDARY
1. Declare STRATEGY_CONTRACT_VERSION = "1".
2. Expose exactly one public execution entry point named
   run_strategy(context).
3. Importing the module must not execute the backtest, access the network, open
   a hard-coded data path, prompt for input, or write output.
4. Use context.market_data as the only market-data input. Make a local copy if
   the engine or indicators need mutation.
5. Read run settings from context.config. Do not silently override
   platform-controlled capital, fees, slippage, fill, multiplier, instrument,
   timeframe, or period settings.
6. Preserve the requested strategy exactly. The contract must not simplify or
   change indicators, signals, entries, exits, re-entry, sizing, stops, targets,
   multi-leg behavior, or strategy state.

COMPLETED-TRADE RETURN
After the supported engine finishes, return:

{
    "completed_trades": <the engine's actual authoritative closed-trade,
                         closed-position, or closed-leg collection>,
    "completed_trade_count": <the authoritative count for that collection>,
    "trade_mapper": <a callable mapping one actual engine trade and its index
                     to the canonical raw-execution fields, or None when the
                     platform's trusted engine adapter applies>,
    "metadata": {
        "strategy_name": "<descriptive name>",
        "engine": "<the supported engine name>"
    }
}

Never infer, reconstruct, fabricate, or supplement completed trades. A real
zero-trade result must return the real empty collection and count zero.

The mapper may emit only these canonical raw-execution fields:
schema_version, run_id, trade_id, batch_id, leg_id, strategy, symbol, side,
entry_time, exit_time, quantity, entry_price, exit_price, multiplier, fees.

Do not calculate or return P&L, net P&L, gross P&L, returns, equity, drawdown,
Sharpe, win rate, profit factor, or dashboard data. Trusted platform code
calculates those independently.

ERROR RULES
- Let engine, data, configuration, and mapping failures propagate as
  exceptions.
- You may add a useful error message and re-raise.
- Do not catch an exception and return partial trades, an empty result, or a
  success flag.
- Do not declare execution status; the platform owns status.

OUTPUT FORMAT
Return only the complete Python source code, without Markdown fences or prose.
Use comments inside the source to identify any assumption that comes directly
from the supplied engine reference. If the engine reference is insufficient to
implement the strategy correctly, do not invent APIs; return Python source that
raises NotImplementedError with a precise message naming the missing engine
interface.
```

## Why this prompt stays flexible

The prompt fixes only the callable boundary, supplied inputs, authoritative
completed-trade handoff, and error behavior. The strategy request remains free
to specify any supported trading logic.

## Before pasting generated code into the runner

Verify that:

- the engine API was taken from the supplied reference;
- `run_strategy` contains the requested strategy unchanged;
- no market data or credentials are embedded in the source;
- the result uses the engine's real completed-trade collection; and
- the mapper contains execution facts, not analytics.
