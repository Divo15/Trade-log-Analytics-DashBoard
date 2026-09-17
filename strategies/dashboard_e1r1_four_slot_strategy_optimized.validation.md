# Timestamp optimization validation — 10 September 2026

Source: active job `dcde77993a5747adbe2bf78e3153a2db`, upload
`dashboard_e1r1_four_slot_strategy.py`, SHA256
`2E9E0269E3CCD63DA3F9062F71C18D8524B4DC9E0E301C893AE6B2CA18E5E7B7`.

The optimized copy changes only timestamp preparation. Standard
`DD/MM/YYYY HH:MM:SS` columns use bulk parsing; mixed, unusual, empty,
or invalid columns retain the original scalar parser. Filtering order,
duplicate/missing quote handling, simulation rules, and snapshot recording
remain unchanged. No dependencies were installed or active jobs replaced.

Validation command: `.venv\Scripts\python.exe tools/verify_e1r1_timestamp_optimization.py`.

Timestamp checks passed for standard strings, timezone offsets, mixed strings
and numbers, datetime objects, invalid/missing values, empty columns, and
duplicate indexes, including dtype equality or matching exception types.

Bounded real-data comparison used `nifty current week/nifty_chain/part_0000.parquet`,
with expiry dates 3 April, 3 July, and 2 September 2025: 46,125 chain rows and
1,128 summary rows. Both executions returned exactly equal complete result
mappings, including 36 completed legs and 1,130 snapshots.

The latest repeated check took 11.609 seconds on the scalar path and 0.471
seconds on the optimized path. An earlier run of the same bounded check took
45.076 and 0.570 seconds respectively. These times include preparation and simulation using mocked Parquet
reads returning the same in-memory sample frames, and exclude disk loading.
This was one comparison while another backtest remained active. It is not a
full-run benchmark or an ETA. Full-data execution has not been validated.

The dashboard adapter removes the non-canonical descriptive `exit_reason` field
from exported trade rows and carries realized P&L forward between daily snapshot
segments. Neither change affects entries, exits, fills, quantities, or trade P&L.

After this timestamp-only equivalence check, the confirmed stop-loss reason
mismatch was corrected separately: `close_slot` now tests `COMBINED_SL`, matching
its caller, so the stated one re-entry after a combined stop-loss is reachable.
That intentional strategy correction changes results and is outside the earlier
timestamp-equivalence claim.
