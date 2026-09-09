# Observed intraday and unrealised drawdown

The dashboard now accepts an optional `equity.csv` alongside a canonical
`trades.csv`. Upload trades first, then choose **Add equity CSV** in the
Intraday and unrealised risk section.

## Engine integration

Capture snapshots from the actual engine's account/position valuation at every
bar or tick, and after executions. Do not interpolate between trade entry and
exit prices. Do not create a curve from final trade P&L. If the engine cannot
supply valuations, report that limitation instead of generating snapshots.

Each snapshot contains:

| Field | Meaning |
| --- | --- |
| `timestamp` | ISO 8601 timestamp with timezone offset, strictly increasing in absolute time. |
| `realized_pnl` | Cumulative realised P&L since run start, after recorded fees. |
| `unrealized_pnl` | Total marked P&L of all open positions, using the engine's contemporaneous prices and actual size, direction and multiplier. |

Both P&L fields are supplied by the engine, unlike trade-log P&L independently
reconstructed from executions. The dashboard independently calculates
`equity = realized_pnl + unrealized_pnl` and
`drawdown = equity - max(0, all previous observed equity)`.
Worst unrealised P&L is the minimum open-position P&L; it is not itself a
peak-to-trough drawdown. No starting capital, deposits or withdrawals belong in
these fields. Avoid counting fees twice. Use the same currency as the trades.

Start with a zero snapshot at or before the first entry. End after all trades
are closed, with zero unrealised P&L and realised P&L matching the canonical
trade log within 0.01 currency units. Use the same run ID. At least two
snapshots are required. This version supports fully closed backtests.

```python
from trade_log_exporter import export_equity_snapshots

# engine_snapshots must come from the actual backtest's valuation events.
# An adapter may map the engine fields to the three fields defined above.
export_equity_snapshots(
    engine_snapshots,
    "output/equity.csv",
    run_id=context.run_id,
)
```

For Strategy Contract v2, return an optional `equity_snapshots` iterable in the
result mapping. It must be captured during the strategy run. The trusted runner
can then export it:

```python
if result.get("equity_snapshots") is not None:
    equity_path = export_equity_snapshots(
        result["equity_snapshots"], "/content/output/equity.csv",
        run_id=context.run_id,
    )
    files.download(str(equity_path))
```

Install this updated package before using the new export function. The existing
Colab notebook pins an older Git commit; this local change does not update or
publish that remote commit. Until a release is pinned, install a copy of this
updated repository in Colab. The notebook download cell runs the optional
export block when the result contains snapshots, and gives an explicit upgrade
error if the installed exporter is too old. Existing trade-only modules remain compatible.

CSV header:

```text
schema_version,run_id,timestamp,realized_pnl,unrealized_pnl
```

The exporter inserts `schema_version=1` and `run_id`, validates before replacing
an existing export, and writes atomically. The dashboard fingerprints the
uploaded equity bytes and validates run ID, timestamps, baseline, coverage and
final P&L. These checks do not authenticate market marks or guarantee every
engine valuation event was exported.

## Sampling limits

Intraday here means the entire chronological sampled equity path, including
cross-day peaks. It does not reset the high-water mark each session. Sparse
snapshots may miss worse losses between observations; the UI shows snapshot
count and maximum gap explicitly. Hourly or daily input is not tick-level risk.
The existing primary drawdown remains the daily realised batch metric, so it
can be compared with observed mark-to-market drawdown without changing its meaning.
The combined encoded upload has a 25 MiB cap. Sweep reports remain trade-only.
