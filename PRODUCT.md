# Product

## Register

product

## Users

An internal trading-strategy team reviewing Python backtests. Users need to upload a deterministic trade log and understand strategy performance without trusting an LLM-generated P&L figure.

## Product Purpose

Turn one validated `trades.csv` file into a professional analytics dashboard. The application independently calculates performance from raw executions, rejects incompatible files, and makes every cost assumption explicit. Version one runs locally, requires no account, and analyzes one backtest at a time.

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
