# Sweep Summary Contract v1

## Purpose

`sweep_results.csv` is a trusted comparison artifact with one row per requested
parameter iteration. It is not a raw trade log. Generated strategy code must
never supply its P&L or risk metrics.

## Trusted calculation flow

For every iteration, the Colab notebook:

1. creates a unique `run_id` and supplies one declared parameter mapping;
2. calls the unchanged `run_strategy(context)`;
3. passes its authoritative completed trades and count to the canonical trade
   exporter in a temporary workspace;
4. validates that canonical trade log;
5. calculates metrics with the trusted dashboard analytics engine; and
6. writes one sweep-summary row.

A real zero-trade iteration is recorded with status `no_trades` and blank
performance fields. Any failed iteration fails the export rather than creating
fabricated metrics.

## Schema v1

```text
schema_version,sweep_id,run_id,strategy,engine,parameters_json,status,start_time,end_time,completed_trade_count,batch_count,traded_days,gross_pnl,fees,net_pnl,max_drawdown,win_rate,max_loss,profit_factor,sharpe_traded_days,yearly_net_pnl_json,trade_log_sha256
```

- `parameters_json` is the exact user-requested parameter mapping.
- `max_loss` is the worst completed batch result, capped at zero when every
  batch wins.
- `yearly_net_pnl_json` maps each traded calendar year to trusted net P&L.
- `trade_log_sha256` identifies the temporary validated canonical trade log
  used to calculate that row.

The exporter rejects duplicate run IDs, mixed sweep IDs, unsupported result
fields, count mismatches, non-finite metrics, malformed JSON, and invalid
statuses.

## Commands

```bash
trade-log sweep-schema
trade-log validate-sweep output/sweep_results.csv
```
