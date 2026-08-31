# Backtest integration prompt

Paste the text below into Codex or Claude Code while the actual strategy repository is open. Replace `<EXPORTER_REPOSITORY_PATH>` with the local path or private Git URL for this package.

```text
Integrate the standard trade-log exporter into this backtesting project.

Do not change any strategy entry, exit, sizing, data-loading, fill, slippage,
commission, or execution behavior.

First inspect the repository and report:
1. the normal backtest entry point;
2. the backtesting framework or custom engine;
3. the engine's authoritative completed-trades or closed-positions collection;
4. how partial exits and multi-leg positions are represented;
5. the command currently used to run a backtest.

Then:
1. Install `trade-log-exporter` from `<EXPORTER_REPOSITORY_PATH>` in the
   project's environment and record the dependency using this project's
   existing dependency-management convention.
2. Create one engine adapter that maps only actual completed trade objects to
   the canonical schema. Never infer, reconstruct, or fabricate trades.
3. Call `trade_log_exporter.export_trade_log()` unconditionally after every
   successful backtest through the normal entry point.
4. Export to `output/trades.csv` with expected_count set to the authoritative
   engine completed-trade count.
5. Use one row per completed trade or closed leg. Connect multi-leg rows with
   batch_id and identify the leg with leg_id.
6. Do not include P&L, return, drawdown, or any derived result field.
7. Give naive timestamps an explicit timezone from the backtest configuration;
   never guess silently.
8. Ensure a failed backtest or failed export cannot replace the last valid CSV.
9. Append the contents of the supplied AGENTS.md or CLAUDE.md snippet to the
   matching project instruction file.
10. Add tests covering exact columns, row-count matching, field mapping,
    timezone handling, multi-leg behavior when relevant, and failure behavior.

Run one small representative backtest. Then run:

trade-log validate output/trades.csv

Report the engine completed-trade count, exported-row count, output path,
schema version, and validation result. Do not claim completion unless the file
was produced by the actual engine run and all tests passed.
```
