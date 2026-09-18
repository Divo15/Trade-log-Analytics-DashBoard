# Product

## Register

product

## Users

Trading-strategy users who run trusted Python strategies locally against their
market data, or import a completed trade log for independent analysis.

## Product Purpose

Run a trusted single strategy or parameter sweep from the local UI with a market-data ZIP
or local path, export authoritative executions, and calculate performance
independently. Keep a CSV analysis workflow for existing results. Strategy
execution runs in a separate process with logs, cancellation and a time limit;
it is not a security sandbox. Make every cost and sampling assumption explicit.
The desktop app starts and stops its local Python backend automatically, binds to
an available localhost port, requires no account, and runs one job at a time.
Each teammate selects their dataset parent folder, while settings, logs, cached
metadata, and saved results remain in that operating-system user's writable app-data folder.
Sweep variations execute sequentially and remain separate. The optimizer compares
all declared combinations with a transparent weighted recommendation that emphasizes
high P&L and low drawdown. The recommended combination is rerun and opened
automatically; the user can return to the comparison and override it. Each sweep
row retains compact metrics only. The chosen combination is rerun to create and verify its complete analytics and downloadable
artifacts. Only the automatically recommended winner is added to persistent
history; opening a manual override does not add another history entry. Users can
stop a running sweep and review every completed combination; unfinished work is
discarded and completed rows remain available for a full analytics rerun.

## Brand Personality

Precise, calm, trustworthy. The product should feel like a serious internal risk workstation: information-dense but never intimidating, with restrained color and direct language.

## Anti-references

Avoid retail-trading hype, glowing terminal aesthetics, decorative finance imagery, oversized KPI theatre, and ambiguous green/red signals. Do not imply that historical performance guarantees future results.

## Design Principles

1. Calculations earn trust: expose validation, data coverage, and cost assumptions.
2. Lead with decisions: show performance, risk, consistency, and concentration before raw records.
3. Dense, not crowded: keep related metrics together and progressively reveal detail.
4. Errors must be actionable: identify the exact CSV problem and how to correct it.
5. Preserve the source: analytics never mutate the uploaded trade log.

## Accessibility & Inclusion

Target WCAG 2.2 AA. Do not rely on red or green alone; pair color with labels and signs. Support keyboard navigation, visible focus, reduced motion, responsive layouts, and readable tabular data.
