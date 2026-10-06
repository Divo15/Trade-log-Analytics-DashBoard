from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import duckdb

from trade_log_dashboard.sweep_schema import rank_sweep_parquet


MAPPING = {
    "status": "status",
    "net_pnl": "net_pnl",
    "pnl_2025": "pnl_2025",
    "pnl_2026": "pnl_2026",
    "entry_start": "entry_start",
    "mtm_drawdown": "mtm_drawdown",
}


class SweepRankingTests(unittest.TestCase):
    def test_one_year_sweep_uses_available_yearly_pnl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "one_year.parquet")
            connection = duckdb.connect()
            connection.execute("""
                CREATE TABLE sweep (
                    entry_start VARCHAR, premium_price DOUBLE, status VARCHAR,
                    net_pnl DOUBLE, pnl_2025 DOUBLE, mtm_drawdown DOUBLE
                )
            """)
            connection.executemany(
                "INSERT INTO sweep VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("09:30:00", 100, "succeeded", 100, 100, 20),
                    ("09:40:00", 100, "succeeded", 80, 80, 20),
                    ("09:50:00", 100, "succeeded", 75, 75, 20),
                ],
            )
            connection.execute("COPY sweep TO ? (FORMAT PARQUET)", [str(path)])
            connection.close()

            mapping = {key: value for key, value in MAPPING.items() if key != "pnl_2026"}
            result = rank_sweep_parquet(path, mapping, {}, require_robustness=False)
            self.assertEqual(result["eligible_count"], 3)
            self.assertEqual(
                result["ranking"],
                [
                    {"column": "pnl_2025", "label": "Yearly P&L", "weight": 60.0, "direction": "higher"},
                    {"column": "__ranking_drawdown", "label": "Ranking drawdown", "weight": 40.0, "direction": "lower"},
                ],
            )

    def test_nearby_times_use_complete_available_side_and_reject_failed_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "sweep.parquet")
            connection = duckdb.connect()
            connection.execute("""
                CREATE TABLE sweep (
                    entry_start VARCHAR, premium_price DOUBLE, status VARCHAR,
                    net_pnl DOUBLE, pnl_2025 DOUBLE, pnl_2026 DOUBLE,
                    mtm_drawdown DOUBLE
                )
            """)
            connection.executemany(
                "INSERT INTO sweep VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("09:30:00", 100, "succeeded", 75, 30, 45, 10),
                    ("09:40:00", 100, "succeeded", 100, 40, 60, 10),
                    ("09:50:00", 100, "succeeded", 80, 35, 45, 10),
                    ("10:00:00", 100, "succeeded", 65, 25, 40, 10),
                    ("09:30:00", 200, "failed", 100, 40, 60, 10),
                    ("09:40:00", 200, "succeeded", 100, 40, 60, 10),
                    ("09:50:00", 200, "succeeded", 90, 35, 55, 10),
                ],
            )
            connection.execute("COPY sweep TO ? (FORMAT PARQUET)", [str(path)])
            connection.close()

            result = rank_sweep_parquet(path, MAPPING, {}, require_robustness=False)
            rows = {
                (row["parameters"]["premium_price"], row["entry_start"]): row
                for row in result["rows"]
            }
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[(100, "09:40:00")]["entry_robustness"], "before")
            self.assertEqual(rows[(100, "09:40:00")]["before_variant_count"], 1)
            self.assertEqual(rows[(100, "09:40:00")]["after_variant_count"], 2)
            self.assertEqual(rows[(200, "09:40:00")]["entry_robustness"], "after")
            self.assertEqual(rows[(200, "09:50:00")]["entry_robustness"], "not_confirmed")

            strict = rank_sweep_parquet(path, MAPPING, {}, require_robustness=True)
            self.assertEqual(strict["eligible_count"], 5)
            self.assertNotIn(
                (200, "09:50:00"),
                {(row["parameters"]["premium_price"], row["entry_start"])
                 for row in strict["rows"]},
            )

            empty = rank_sweep_parquet(
                path, MAPPING, {"minimum_pnl": 1000}, require_robustness=True
            )
            self.assertEqual(empty["eligible_count"], 0)
            self.assertEqual(empty["diagnostics"]["base_count"], 0)


if __name__ == "__main__":
    unittest.main()
