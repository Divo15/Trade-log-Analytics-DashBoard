# Current limitations

This is a local single-backtest execution and review tool. Python uploads now
run through the local UI; CSV import remains available. These limits remain.

| Area | Current boundary | Useful next improvement |
| --- | --- | --- |
| Input | Exact canonical schema v1, one run per CSV, maximum 25 MiB. Sweep summaries cannot be uploaded to the dashboard. | Dedicated sweep comparison view and explicit import adapters. |
| History | Working runs remain temporary, while one recommended winner per completed sweep is saved persistently with analytics and outputs. Manual overrides, non-sweep runs and standalone CSV reports are not saved. | Retention controls, deletion and optional naming or notes. |
| Batch inspection | Metrics cover the full CSV, but only the latest 100 batches are returned. Search and outcome filters apply to those loaded batches; no pagination. | Paginated batch inspection with server-side filters. |
| Risk | Primary drawdown uses daily realised batch P&L. Optional equity.csv adds observed intraday drawdown including unrealised P&L. Requires engine valuations and a fully closed run; losses between snapshots remain unknown. | Denser engine valuation events and support for runs ending with open positions. |
| Capital | No capital, margin, cash flows or exposure history. Percentage returns, CAGR and Calmar cannot be calculated reliably. | Capital and margin metadata. |
| Sharpe | Uses traded-day currency P&L and sample standard deviation, annualised by √252. Inactive days and the risk-free rate are excluded. | Calendar-aligned capital returns with explicit annualisation settings. |
| Costs | Only supplied fees are subtracted. The dashboard cannot verify their completeness or model missing slippage. | Explicit cost metadata and optional sensitivity analysis. |
| Currency | Display is fixed to INR; schema v1 has no currency or FX data. | A currency field and explicit conversion rules. |
| Time | Analytics use recorded local clock times, without offset normalisation. Mixed timezones can misorder trades and group dates incorrectly. Entire batches are assigned to the last leg's exit date. | An explicit reporting timezone and leg-realisation reporting mode. |
| Strategy identity | A run may contain multiple strategy labels; the report currently displays one label. Rules and parameters are not included in the trade schema. | Run-level strategy metadata and consistency validation. |
| Integrity | Schema validation and a SHA-256 fingerprint verify structure and identify bytes; they do not prove executions are authentic or the backtest is unbiased. | Verified source provenance and backtest diagnostics. |
| Hosting | Local desktop backend without accounts, TLS or rate limits. Per-user settings, logs and recommended-result history are durable on that computer; it is not a public multi-user service. | A production web service with authentication and resource controls if remote hosting is required. |
| Execution | Trusted Python only; subprocesses are not sandboxed. One job at a time, 30-minute timeout. Contract strategies and the class-based `ProtectedStraddleBacktester` format are supported; other standalone engine shapes still need adapters. Dependencies must be installed locally. | Managed execution environments and more explicit engine adapters. |
| Sweeps | Declared parameter sets run sequentially against the same selected dataset. Failed variations are listed and later combinations continue. Only compact metrics are retained for each row; full artifacts are created by rerunning the selected combination. Results must be deterministic. Sweep history remains temporary for the server session. Full-data optimization can take hours or days. | Durable comparisons, resumable execution and parallel workers with explicit memory limits. |
| Selection score | Ranks profitable combinations relatively within one sweep: 35% P&L, 35% lower drawdown, 15% average win/loss ratio, 10% win rate and 5% fewer consecutive losses. It is a heuristic recommendation, not out-of-sample validation. | User-editable constraints, walk-forward validation and holdout ranking. |
| Scale | Validation and analysis load data into memory; charts render every daily/monthly point. No large-data concurrency or performance guarantees. | Benchmarks, resource limits and chart downsampling. |
| Charts | Native SVG charts have limited interaction. Narrow screens scroll charts horizontally to preserve label readability. | Keyboard-accessible daily data exploration and zoom. |
| Comparisons | No benchmark history, cross-run comparison or portfolio aggregation. | Separate benchmark and run-comparison inputs. |

## Fixes included in this pass

- Initial losses now contribute to total and monthly drawdown; monthly highs include the zero starting balance.
- Break-even days are shown separately and no longer count toward the displayed loss percentage.
- Removing the best and worst day from a one-day run removes that day once.
- Unbatched trade IDs cannot merge with identical named batch IDs.
- Traded-day counts now match the batch-based daily series.
- Uploaded CSV validation rejects multiple run IDs, matching the exporter.
- Replacement-upload errors remain visible, submissions cannot overlap, and the same file can be retried.
- Charts preserve aspect ratio, single-day equity has a visible marker, tables support keyboard scrolling, and reduced-motion preferences apply to report navigation.
- Report assumptions and undefined metrics are explained in the interface.
- The standalone protected-straddle script now runs against the selected project dataset without changing its strategy rules or 65-lot setting.
- Its own minute-by-minute mark-to-market checks are captured for observed intraday and unrealised drawdown.
- Declared parameter sweeps have no fixed combination limit, preserve failures per row, show live progress and requested comparison metrics, discard bulky per-row artifacts, automatically rerun the recommended combination, and save only that winner to persistent history.
