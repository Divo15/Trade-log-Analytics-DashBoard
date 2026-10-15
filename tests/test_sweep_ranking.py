from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import duckdb

from trade_log_dashboard.sweep_schema import rank_sweep_parquet, rank_sweep_time_groups


MAPPING = {
    "status": "status",
    "net_pnl": "net_pnl",
    "pnl_2025": "pnl_2025",
    "pnl_2026": "pnl_2026",
    "entry_start": "entry_start",
    "mtm_drawdown": "mtm_drawdown",
}


class SweepRankingTests(unittest.TestCase):
    def test_time_groups_do_not_treat_outcome_counts_as_fixed_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "outcome_columns.parquet")
            connection = duckdb.connect()
            connection.execute("""
                CREATE TABLE sweep (
                    monitor_start VARCHAR, monitor_cutoff VARCHAR,
                    premium_price DOUBLE, status VARCHAR, net_pnl DOUBLE,
                    mtm_drawdown DOUBLE, trade_win_rate DOUBLE,
                    primary_entries BIGINT, primary_sl_exits BIGINT, roi_pct DOUBLE
                )
            """)
            rows = []
            for index in range(12):
                rows.append((
                    "09:18:00", f"{10 + (45 + index * 5) // 60:02d}:{(45 + index * 5) % 60:02d}:00",
                    100, "succeeded", 100 + index, 20 + index,
                    40 + index, 10 + index, index, 1 + index / 10,
                ))
            connection.executemany("INSERT INTO sweep VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            connection.execute("COPY sweep TO ? (FORMAT PARQUET)", [str(path)])
            connection.close()

            mapping = {
                "status": "status", "net_pnl": "net_pnl",
                "entry_start": "monitor_cutoff", "mtm_drawdown": "mtm_drawdown",
            }
            result = rank_sweep_time_groups(path, mapping, {}, group_size=10, top_n=20)

            self.assertEqual(result["eligible_count"], 1)
            self.assertEqual(result["time_group"]["time_column"], "monitor_cutoff")
            self.assertEqual(result["time_group"]["available_time_count"], 12)
            self.assertTrue(result["time_group"]["fixed_parameters_enforced"])
            self.assertTrue(result["time_group"]["distinct_time_values_required"])
            self.assertEqual(result["rows"][0]["parameters"], {
                "monitor_start": "09:18:00", "premium_price": 100,
            })

    def test_time_group_can_include_a_negative_member_when_ten_times_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "soft_time_group.parquet")
            connection = duckdb.connect()
            connection.execute("""
                CREATE TABLE sweep (
                    entry_start VARCHAR, premium_price DOUBLE, status VARCHAR,
                    net_pnl DOUBLE, pnl_2025 DOUBLE, pnl_2026 DOUBLE,
                    mtm_drawdown DOUBLE
                )
            """)
            rows = []
            for index in range(10):
                entry_time = f"09:{30 + index * 2:02d}:00"
                net_pnl = -5 if index == 0 else 50 + index
                rows.append((entry_time, 100, "succeeded", net_pnl, net_pnl, 0, 10 + index))
            connection.executemany("INSERT INTO sweep VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            connection.execute("COPY sweep TO ? (FORMAT PARQUET)", [str(path)])
            connection.close()

            result = rank_sweep_time_groups(path, MAPPING, {}, group_size=10, top_n=20)

            self.assertEqual(result["eligible_count"], 1)
            self.assertFalse(result["time_group"]["requires_positive_members"])
            self.assertEqual(result["rows"][0]["time_member_count"], 10)
            self.assertEqual(result["rows"][0]["group_minimum_pnl"], -5)

    def test_time_groups_keep_parameters_fixed_and_select_ten_entry_times(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "time_groups.parquet")
            connection = duckdb.connect()
            connection.execute("""
                CREATE TABLE sweep (
                    entry_start VARCHAR, premium_price DOUBLE, status VARCHAR,
                    net_pnl DOUBLE, pnl_2025 DOUBLE, pnl_2026 DOUBLE,
                    mtm_drawdown DOUBLE
                )
            """)
            rows = []
            for premium_price, pnl_base, drawdown_base in ((100, 100, 50), (200, 70, 10)):
                for index in range(12):
                    minutes = 30 + index * 5
                    entry_time = f"{9 + minutes // 60:02d}:{minutes % 60:02d}:00"
                    net_pnl = pnl_base + index
                    rows.append((
                        entry_time, premium_price, "succeeded", net_pnl,
                        net_pnl * 0.45, net_pnl * 0.55, drawdown_base + index,
                    ))
            connection.executemany("INSERT INTO sweep VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            connection.execute("COPY sweep TO ? (FORMAT PARQUET)", [str(path)])
            connection.close()

            result = rank_sweep_time_groups(path, MAPPING, {}, group_size=10, top_n=20)

            self.assertTrue(result["time_group"]["enabled"])
            self.assertEqual(result["time_group"]["group_size"], 10)
            self.assertEqual(result["time_group"]["available_time_count"], 12)
            self.assertEqual(result["eligible_count"], 2)
            self.assertEqual(len(result["rows"]), 2)
            winner = result["rows"][0]
            self.assertEqual(winner["parameters"]["premium_price"], 100)
            self.assertEqual(winner["time_member_count"], 10)
            self.assertEqual(len(winner["time_members"]), 10)
            self.assertEqual(len({member["entry_start"] for member in winner["time_members"]}), 10)
            self.assertEqual(
                [member["entry_start"] for member in winner["time_members"]],
                sorted(member["entry_start"] for member in winner["time_members"]),
            )
            self.assertGreater(winner["group_minimum_pnl"], 0)
            self.assertAlmostEqual(winner["group_total_pnl"], sum(
                member["net_pnl"] for member in winner["time_members"]
            ))
            self.assertAlmostEqual(winner["group_combined_drawdown"], sum(
                member["ranking_drawdown"] for member in winner["time_members"]
            ))
            self.assertEqual(winner["group_worst_drawdown"], max(
                member["ranking_drawdown"] for member in winner["time_members"]
            ))
            self.assertEqual(winner["ranking_components"][0]["label"], "Group total P&L")
            self.assertEqual(winner["ranking_components"][1]["label"], "Group combined drawdown")

    def test_time_group_parameter_filter_keeps_the_complete_ten_time_basket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "filtered_time_groups.parquet")
            connection = duckdb.connect()
            connection.execute("""
                CREATE TABLE sweep (
                    entry_start VARCHAR, premium_price DOUBLE, status VARCHAR,
                    net_pnl DOUBLE, pnl_2025 DOUBLE, pnl_2026 DOUBLE,
                    mtm_drawdown DOUBLE
                )
            """)
            rows = []
            for premium_price in (100, 200):
                for index in range(10):
                    entry_time = f"09:{30 + index * 2:02d}:00"
                    rows.append((
                        entry_time, premium_price, "succeeded", 100 + index,
                        50 + index, 50, 20 + index,
                    ))
            connection.executemany("INSERT INTO sweep VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            connection.execute("COPY sweep TO ? (FORMAT PARQUET)", [str(path)])
            connection.close()

            filters = {
                "parameter_filters": [
                    {"column": "premium_price", "mode": "values", "values": ["200"]},
                ],
            }
            result = rank_sweep_time_groups(
                path, MAPPING, filters, group_size=10, top_n=20,
            )

            self.assertEqual(result["eligible_count"], 1)
            self.assertEqual(result["rows"][0]["parameters"]["premium_price"], 200)
            self.assertEqual(result["rows"][0]["time_member_count"], 10)
            self.assertEqual(len(result["rows"][0]["time_members"]), 10)

    def test_time_groups_rank_by_combined_not_worst_member_drawdown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "combined_drawdown.parquet")
            connection = duckdb.connect()
            connection.execute("""
                CREATE TABLE sweep (
                    entry_start VARCHAR, premium_price DOUBLE, status VARCHAR,
                    net_pnl DOUBLE, pnl_2025 DOUBLE, pnl_2026 DOUBLE,
                    mtm_drawdown DOUBLE
                )
            """)
            rows = []
            for premium_price in (100, 200):
                for index in range(10):
                    entry_time = f"09:{30 + index * 2:02d}:00"
                    drawdown = 100 if index == 0 else (1 if premium_price == 100 else 50)
                    rows.append((entry_time, premium_price, "succeeded", 100, 50, 50, drawdown))
            connection.executemany("INSERT INTO sweep VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            connection.execute("COPY sweep TO ? (FORMAT PARQUET)", [str(path)])
            connection.close()

            result = rank_sweep_time_groups(path, MAPPING, {}, group_size=10, top_n=20)

            winner = result["rows"][0]
            self.assertEqual(winner["parameters"]["premium_price"], 100)
            self.assertEqual(winner["group_worst_drawdown"], 100)
            self.assertEqual(result["rows"][1]["group_worst_drawdown"], 100)
            self.assertEqual(winner["group_combined_drawdown"], 109)
            self.assertEqual(result["rows"][1]["group_combined_drawdown"], 550)

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

    def test_nearby_times_pass_with_any_qualifying_variant(self):
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
            self.assertEqual(
                result["ranking"],
                [
                    {
                        "column": "__time_diversification",
                        "label": "Time diversification (weakest-year percentile)",
                        "weight": 70.0,
                        "direction": "higher",
                    },
                    {
                        "column": "__ranking_drawdown",
                        "label": "Ranking drawdown",
                        "weight": 30.0,
                        "direction": "lower",
                    },
                ],
            )
            rows = {
                (row["parameters"]["premium_price"], row["entry_start"]): row
                for row in result["rows"]
            }
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[(100, "09:40:00")]["entry_robustness"], "both")
            self.assertEqual(rows[(100, "09:40:00")]["before_variant_count"], 1)
            self.assertEqual(rows[(100, "09:40:00")]["after_variant_count"], 2)
            self.assertEqual(rows[(200, "09:40:00")]["entry_robustness"], "after")
            self.assertEqual(rows[(200, "09:50:00")]["entry_robustness"], "before")

            strict = rank_sweep_parquet(path, MAPPING, {}, require_robustness=True)
            self.assertEqual(strict["eligible_count"], 6)
            self.assertIn(
                (200, "09:50:00"),
                {(row["parameters"]["premium_price"], row["entry_start"])
                 for row in strict["rows"]},
            )

            empty = rank_sweep_parquet(
                path, MAPPING, {"minimum_pnl": 1000}, require_robustness=True
            )
            self.assertEqual(empty["eligible_count"], 0)
            self.assertEqual(empty["diagnostics"]["base_count"], 0)

    def test_time_diversification_prefers_strongest_weak_year_and_requires_each_year_positive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "three_year.parquet")
            connection = duckdb.connect()
            connection.execute("""
                CREATE TABLE sweep (
                    entry_start VARCHAR, premium_price DOUBLE, status VARCHAR,
                    net_pnl DOUBLE, pnl_2024 DOUBLE, pnl_2025 DOUBLE, pnl_2026 DOUBLE,
                    mtm_drawdown DOUBLE
                )
            """)
            connection.executemany(
                "INSERT INTO sweep VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("09:30:00", 100, "succeeded", 410, 200, 200, 10, 20),
                    ("09:30:00", 200, "succeeded", 360, 120, 120, 120, 20),
                    ("09:30:00", 300, "succeeded", 330, 110, 110, 110, 20),
                    ("09:30:00", 400, "succeeded", 300, 100, 100, 100, 20),
                    ("09:30:00", 500, "succeeded", 390, 200, 200, -10, 20),
                ],
            )
            connection.execute("COPY sweep TO ? (FORMAT PARQUET)", [str(path)])
            connection.close()

            mapping = {
                **MAPPING,
                "pnl_2024": "pnl_2024",
            }
            result = rank_sweep_parquet(path, mapping, {}, require_robustness=False)

            self.assertTrue(result["time_diversification"]["enabled"])
            self.assertEqual(result["time_diversification"]["years"], [2024, 2025, 2026])
            self.assertTrue(result["time_diversification"]["requires_positive_each_year"])
            self.assertEqual(result["eligible_count"], 4)
            self.assertEqual(result["rows"][0]["parameters"]["premium_price"], 200)
            self.assertGreater(result["rows"][0]["time_diversification_score"], 0.5)
            self.assertNotIn(500, [row["parameters"]["premium_price"] for row in result["rows"]])
            self.assertEqual(result["rows"][0]["pnl_2024"], 120)


if __name__ == "__main__":
    unittest.main()
