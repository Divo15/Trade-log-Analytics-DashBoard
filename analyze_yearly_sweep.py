from pathlib import Path
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(tempfile.gettempdir(), "codex-duckdb"))
import duckdb

SOURCE = Path(r"D:\Backend\static\admin\images\sensex_full_sweep_with_yearly.parquet")
OUTPUT = Path(r"C:\Users\HELLO!\Documents\ChatGPT\Analytics DashBoard\sensex_yearly_ranked.parquet")
DB = Path(r"C:\Users\HELLO!\Documents\ChatGPT\Analytics DashBoard\sensex_yearly_sort.duckdb")

con = duckdb.connect(str(DB))
con.execute("SET memory_limit='8GB'")
con.execute(f"SET threads={max(1, min(8, os.cpu_count() or 1))}")
con.execute("SET preserve_insertion_order=false")
p = str(SOURCE).replace("'", "''")
o = str(OUTPUT).replace("'", "''")

con.execute(f"""
CREATE OR REPLACE TABLE bracketed AS
WITH prepared AS (
  SELECT *, row_number() OVER () - 1 AS combination_index,
         CAST(split_part(entry_start, ':', 1) AS INTEGER) * 60
         + CAST(split_part(entry_start, ':', 2) AS INTEGER) AS entry_minutes
  FROM read_parquet('{p}')
)
SELECT *,
  min(net_pnl) OVER (
    PARTITION BY premium_price, leg_sl_pct, combined_max_loss_rs,
                 combined_target_rs, combined_max_loss_reentries,
                 combined_target_reentries
    ORDER BY entry_minutes RANGE BETWEEN 30 PRECEDING AND 30 FOLLOWING
  ) AS bracket_min_pnl,
  count(*) OVER (
    PARTITION BY premium_price, leg_sl_pct, combined_max_loss_rs,
                 combined_target_rs, combined_max_loss_reentries,
                 combined_target_reentries
    ORDER BY entry_minutes RANGE BETWEEN 30 PRECEDING AND 30 FOLLOWING
  ) AS bracket_count
  , min(net_pnl) OVER (
    PARTITION BY premium_price, leg_sl_pct, combined_max_loss_rs,
                 combined_target_rs, combined_max_loss_reentries,
                 combined_target_reentries
    ORDER BY entry_minutes RANGE BETWEEN 25 PRECEDING AND 5 PRECEDING
  ) AS prior_bracket_min_pnl
  , count(*) OVER (
    PARTITION BY premium_price, leg_sl_pct, combined_max_loss_rs,
                 combined_target_rs, combined_max_loss_reentries,
                 combined_target_reentries
    ORDER BY entry_minutes RANGE BETWEEN 25 PRECEDING AND 5 PRECEDING
  ) AS prior_bracket_count
  , min(net_pnl) OVER (
    PARTITION BY premium_price, leg_sl_pct, combined_max_loss_rs,
                 combined_target_rs, combined_max_loss_reentries,
                 combined_target_reentries
    ORDER BY entry_minutes RANGE BETWEEN 5 FOLLOWING AND 25 FOLLOWING
  ) AS after_bracket_min_pnl
  , count(*) OVER (
    PARTITION BY premium_price, leg_sl_pct, combined_max_loss_rs,
                 combined_target_rs, combined_max_loss_reentries,
                 combined_target_reentries
    ORDER BY entry_minutes RANGE BETWEEN 5 FOLLOWING AND 25 FOLLOWING
  ) AS after_bracket_count
FROM prepared
""")

con.execute("""
CREATE OR REPLACE TABLE candidates AS
SELECT *,
       CAST((least(660, entry_minutes + 30) - greatest(570, entry_minutes - 30)) / 5 + 1 AS BIGINT) AS expected_bracket_count,
       bracket_min_pnl / net_pnl AS bracket_pnl_ratio,
       prior_bracket_min_pnl / net_pnl AS prior_robust_ratio,
       after_bracket_min_pnl / net_pnl AS after_robust_ratio,
       abs(max_drawdown) AS drawdown_risk,
       abs(max_loss) AS max_loss_risk,
       net_pnl / 300000.0 * 100.0 AS overall_roi_pct
FROM bracketed
WHERE status='succeeded' AND completed_trade_count > 0 AND net_pnl > 0
  AND win_rate IS NOT NULL AND max_drawdown IS NOT NULL AND max_loss IS NOT NULL
  AND max_consecutive_losses IS NOT NULL AND pnl_2025 IS NOT NULL AND pnl_2026 IS NOT NULL
""")

con.execute("""
CREATE OR REPLACE TEMP TABLE q AS
SELECT
 quantile_cont(net_pnl,[.25,.5,.75]) q_pnl,
 quantile_cont(win_rate,[.25,.5,.75]) q_win,
 quantile_cont(completed_trade_count,[.25,.5,.75]) q_trades,
 quantile_cont(drawdown_risk,[.25,.5,.75]) q_dd,
 quantile_cont(max_loss_risk,[.25,.5,.75]) q_loss,
 quantile_cont(max_consecutive_losses,[.25,.5,.75]) q_streak,
 quantile_cont(pnl_2025,[.25,.5,.75]) q_2025,
 quantile_cont(pnl_2026,[.25,.5,.75]) q_2026
FROM candidates
""")

if OUTPUT.exists():
    OUTPUT.unlink()

con.execute(f"""
COPY (
WITH scored AS (
 SELECT c.*,
  CASE WHEN net_pnl<=q_pnl[1] THEN -2 WHEN net_pnl<=q_pnl[2] THEN -1 WHEN net_pnl<=q_pnl[3] THEN 1 ELSE 2 END pnl_score,
  CASE WHEN win_rate<=q_win[1] THEN -2 WHEN win_rate<=q_win[2] THEN -1 WHEN win_rate<=q_win[3] THEN 1 ELSE 2 END win_score,
  CASE WHEN completed_trade_count<=q_trades[1] THEN -2 WHEN completed_trade_count<=q_trades[2] THEN -1 WHEN completed_trade_count<=q_trades[3] THEN 1 ELSE 2 END trade_score,
  CASE WHEN drawdown_risk<=q_dd[1] THEN 2 WHEN drawdown_risk<=q_dd[2] THEN 1 WHEN drawdown_risk<=q_dd[3] THEN -1 ELSE -2 END dd_score,
  CASE WHEN max_loss_risk<=q_loss[1] THEN 2 WHEN max_loss_risk<=q_loss[2] THEN 1 WHEN max_loss_risk<=q_loss[3] THEN -1 ELSE -2 END loss_score,
  CASE WHEN max_consecutive_losses<=q_streak[1] THEN 2 WHEN max_consecutive_losses<=q_streak[2] THEN 1 WHEN max_consecutive_losses<=q_streak[3] THEN -1 ELSE -2 END streak_score,
  CASE WHEN pnl_2025<=q_2025[1] THEN -2 WHEN pnl_2025<=q_2025[2] THEN -1 WHEN pnl_2025<=q_2025[3] THEN 1 ELSE 2 END score_2025,
  CASE WHEN pnl_2026<=q_2026[1] THEN -2 WHEN pnl_2026<=q_2026[2] THEN -1 WHEN pnl_2026<=q_2026[3] THEN 1 ELSE 2 END score_2026
 FROM candidates c CROSS JOIN q
), totals AS (
 SELECT *,
   pnl_score+win_score+trade_score+dd_score+loss_score+streak_score AS base_quartile_score,
   score_2025+score_2026 AS recent_year_score,
   pnl_score+win_score+trade_score+dd_score+loss_score+streak_score+score_2025+score_2026 AS total_score
 FROM scored
   WHERE (
     (prior_bracket_count = 5 AND prior_bracket_min_pnl >= net_pnl*0.70)
     OR
     (after_bracket_count = 5 AND after_bracket_min_pnl >= net_pnl*0.70)
   )
   AND overall_roi_pct >= 15.0
   AND pnl_2025 > 0 AND pnl_2026 > 0
)
SELECT * FROM totals
ORDER BY total_score DESC, recent_year_score DESC, pnl_2026 DESC, pnl_2025 DESC,
         net_pnl DESC, drawdown_risk ASC, max_loss_risk ASC, win_rate DESC,
         max_consecutive_losses ASC, combination_index ASC
) TO '{o}' (FORMAT PARQUET, COMPRESSION ZSTD)
""")

print("qualified", con.execute(f"SELECT count(*) FROM read_parquet('{o}')").fetchone()[0])
print(con.execute(f"""
SELECT *
FROM read_parquet('{o}')
ORDER BY total_score DESC, recent_year_score DESC, pnl_2026 DESC, pnl_2025 DESC,
         net_pnl DESC, drawdown_risk ASC, max_loss_risk ASC, win_rate DESC,
         max_consecutive_losses ASC, combination_index ASC
LIMIT 20
""").fetchdf().to_string(index=False))
con.close()
