import io
import ast
import json
from pathlib import Path
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch

from trade_log_dashboard.runner import LocalRunner, MAX_SAVED_HISTORY, SWEEP_RUN_TIMEOUT, extract_market_data
from trade_log_dashboard.worker import QUARTILE_POINTS, SELECTION_METRICS, _rank_sweep

STRATEGY = '''
import csv
STRATEGY_CONTRACT_VERSION = "1"
RUN_MODE = "single"
def run_strategy(context):
    with (context.market_data / "prices.csv").open() as handle:
        prices = list(csv.DictReader(handle))
    trades = []
    for i in range(1, len(prices)):
        previous, current = prices[i-1], prices[i]
        trades.append(dict(run_id=context.run_id, trade_id=str(i), batch_id=str(i),
            leg_id="1", strategy="Price change test", symbol="TEST", side="LONG",
            entry_time=previous["time"], exit_time=current["time"], quantity=1,
            entry_price=previous["price"], exit_price=current["price"], multiplier=1, fees=0))
    return dict(completed_trades=trades, completed_trade_count=len(trades))
if __name__ == "__main__":
    raise RuntimeError("CLI main must not run")
'''

STANDALONE_STRATEGY = '''
from dataclasses import dataclass
from types import SimpleNamespace
import pandas as pd

@dataclass
class StrategyConfig:
    data_dir: str = "ignored"
    lot_size: int = 65
    start_date: str = "2000-01-01"
    end_date: str = "2000-01-02"
    dte_jump_reset: int = 2

class DataLoader:
    def __init__(self, cfg): self.cfg = cfg

class ProtectedStraddleBacktester:
    def __init__(self, cfg, loader):
        self.cfg, self.loader, self.trades = cfg, loader, []
    def _mark_to_market(self, chain, timestamp, legs):
        return 2.0
    def _run_leg_lifecycle(self, chain, timestamps, entry, legs, credit, stop):
        self._mark_to_market(chain, timestamps[-1], legs)
        for leg in legs: leg.exit_price = 90.0
        return timestamps[-1], "take_profit", legs
    def run(self):
        spot = self.loader.load_spot()
        chain = self.loader.load_chain()
        assert self.cfg.lot_size == 65
        assert len(spot) and len(chain)
        entry = pd.Timestamp("2026-01-01 10:00:00")
        exit_time = pd.Timestamp("2026-01-01 10:01:00")
        leg = SimpleNamespace(option_type="CE", strike=25000.0, side="short",
                              entry_price=100.0, exit_price=float("nan"),
                              pnl_points=lambda: 10.0)
        _, reason, legs = self._run_leg_lifecycle(chain, [entry, exit_time], entry,
                                                  [leg], 100.0, 1.0)
        self.trades = [SimpleNamespace(entry_time=entry, exit_time=exit_time,
                        exit_reason=reason, legs=legs)]
        return pd.DataFrame()
'''

SWEEP_STRATEGY = '''
import csv
STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "sweep"
SWEEP_PARAMETER_SETS = (
    {"take_profit": 0.4},
    {"take_profit": 0.6},
    {"take_profit": 0.8},
    {"take_profit": 1.0},
)
def run_strategy(context):
    with (context.market_data / "prices.csv").open() as handle:
        prices = list(csv.DictReader(handle))
    scale = context.config["parameters"]["take_profit"]
    trade = dict(run_id=context.run_id, trade_id="1", batch_id="1", leg_id="1",
        strategy="TP sweep", symbol="TEST", side="LONG",
        entry_time=prices[0]["time"], exit_time=prices[1]["time"], quantity=1,
        entry_price=prices[0]["price"], exit_price=float(prices[0]["price"]) + 10 * scale,
        multiplier=1, fees=0)
    return dict(completed_trades=[trade], completed_trade_count=1)
'''


class LocalRunnerTests(unittest.TestCase):
    def test_selected_dataset_is_the_actual_worker_input(self):
        from trade_log_dashboard.datasets import DATASETS, catalog
        cases = [("weekly", 110, 10), ("next-weekly", 125, 25),
                 ("monthly", 90, -10), ("sensex-weekly", 140, 40)]
        for identifier, price, _ in cases:
            definition = DATASETS[identifier]
            folder = self.market / definition["folder"]
            (folder / definition["chain"]).mkdir(parents=True)
            (folder / definition["summary"]).touch()
            (folder / definition["chain"] / "part.parquet").touch()
            (folder / "prices.csv").write_text(f"time,price\n2026-01-01T10:00:00+00:00,100\n2026-01-01T11:00:00+00:00,{price}\n")
        with patch("trade_log_dashboard.datasets.DATA_ROOT", self.market), \
             patch("trade_log_dashboard.datasets._coverage", return_value=("2026-01-01", "2026-01-02", 2)):
            self.assertFalse(next(row for row in catalog() if row["id"] == "next2week")["available"])
            for identifier, _, expected in cases:
                job = self.runner.start(dict(entrypoint="strategy.py", dataset_id=identifier, config="{}"),
                                        [("strategy", "strategy.py", STRATEGY.encode())])
                request = json.loads((self.runner.jobs[job]["folder"] / "request.json").read_text())
                self.assertEqual(request["config"]["period"], {
                    "start_date": "2026-01-01", "end_date": "2026-01-02"
                })
                self.assertEqual(request["config"]["instrument"]["symbol"], DATASETS[identifier]["symbol"])
                result = self.finish(job)
                self.assertEqual(result["status"], "succeeded", result)
                analysis = result["result"]["analysis"]
                self.assertEqual(analysis["overview"]["net_pnl"], expected)
                self.assertEqual(analysis["dataset"]["id"], identifier)
            with self.assertRaisesRegex(ValueError, "Unknown project dataset"):
                self.runner.start(dict(entrypoint="strategy.py", dataset_id="../weekly", config="{}"),
                                  [("strategy", "strategy.py", STRATEGY.encode())])

    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="runner's-market-")
        self.addCleanup(self.root.cleanup)
        self.market = Path(self.root.name)
        (self.market / "prices.csv").write_text("time,price\n2026-01-01T10:00:00+00:00,100\n2026-01-01T11:00:00+00:00,125\n")
        self.runner = LocalRunner(history_root=self.market / "history")
        self.addCleanup(self.runner.close)

    def start(self, source=STRATEGY):
        return self.runner.start(dict(entrypoint="strategy.py", market_path=str(self.market), config="{}"),
                                 [("strategy", "strategy.py", source.encode())])

    def finish(self, identifier):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            status = self.runner.status(identifier)
            if status["status"] != "running":
                return status
            time.sleep(.05)
        self.fail("Test run did not finish")

    def test_executes_market_data_and_changes_results_when_prices_change(self):
        first = self.finish(self.start())
        self.assertEqual(first["status"], "succeeded", first)
        self.assertEqual(first["result"]["analysis"]["overview"]["net_pnl"], 25)
        self.assertTrue(self.runner.artifact(first["id"], "trades.csv").is_file())
        (self.market / "prices.csv").write_text("time,price\n2026-01-01T10:00:00+00:00,100\n2026-01-01T11:00:00+00:00,75\n")
        second = self.finish(self.start())
        self.assertEqual(second["result"]["analysis"]["overview"]["net_pnl"], -25)

    def test_zero_trades_is_completed_empty_state(self):
        result = self.finish(self.start(STRATEGY.replace("range(1, len(prices))", "range(0)")))
        self.assertEqual(result["status"], "empty", result)

    def test_single_run_reports_work_progress_and_eta(self):
        source = '''
STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "single"
def run_strategy(context):
    context.report_progress(0, 4, "trading day", "Preparing trading days")
    context.report_progress(2, 4, "trading day", "Preparing trading days")
    return {"completed_trades": [], "completed_trade_count": 0}
'''
        result = self.finish(self.start(source))
        progress = result["progress"]
        self.assertEqual(progress["mode"], "single")
        self.assertEqual(progress["completed"], 2)
        self.assertEqual(progress["total"], 4)
        self.assertEqual(progress["unit"], "trading day")
        self.assertEqual(progress["phase"], "Preparing trading days")
        self.assertEqual(progress["percentage"], 50.0)
        self.assertIsNotNone(progress["average_per_second"])
        self.assertIsNotNone(progress["estimated_remaining_seconds"])

    def test_missing_dependency_is_actionable(self):
        result = self.finish(self.start("import nonexistent_backtest_dependency_123"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("Install this strategy dependency", result["error"])

    def test_cancellation_and_single_run_limit(self):
        identifier = self.start("import time\ntime.sleep(60)")
        with self.assertRaisesRegex(ValueError, "already running"):
            self.start()
        self.runner.cancel(identifier)
        cancelled = self.runner.status(identifier)
        self.assertEqual(cancelled["status"], "cancelled")
        with patch("trade_log_dashboard.runner.time.monotonic", return_value=10**9):
            self.assertEqual(
                self.runner.status(identifier)["elapsed_seconds"],
                cancelled["elapsed_seconds"],
            )

    def test_archive_traversal_rejected(self):
        archive = self.market / "bad.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("../escape.csv", "bad")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            extract_market_data(archive, self.market / "extracted")

    def test_zip_wrapper_directory_supported(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as bundle:
            bundle.writestr("dataset/prices.csv", (self.market / "prices.csv").read_text())
        identifier = self.runner.start(dict(entrypoint="strategy.py", config="{}"), [
            ("strategy", "strategy.py", STRATEGY.encode()), ("market_data", "data.zip", content.getvalue())])
        self.assertEqual(self.finish(identifier)["status"], "succeeded")

    def test_existing_nifty_strategy_runs_without_colab(self):
        import duckdb
        connection = duckdb.connect()
        summary = self.market / "nifty_summary.parquet"
        chain = self.market / "nifty_chain"
        chain.mkdir()
        # Valid schema, deliberately empty dataset: exercise the actual user's module.
        connection.execute("CREATE TABLE summary(datetime VARCHAR, DTE DOUBLE, future_close DOUBLE, future_atm DOUBLE, straddle_future DOUBLE)")
        connection.execute("COPY summary TO ? (FORMAT PARQUET)", [str(summary)])
        connection.execute("CREATE TABLE chain(datetime VARCHAR, strike INTEGER, ce_close DOUBLE, pe_close DOUBLE)")
        connection.execute("COPY chain TO ? (FORMAT PARQUET)", [str(chain / "part.parquet")])
        connection.close()
        source = Path(__file__).resolve().parents[1] / "nifty_protected_straddle_colab_CLEAN.py"
        if not source.exists():
            self.skipTest("User's optional NIFTY strategy not present")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        defaults = next(ast.literal_eval(node.value) for node in tree.body
                        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "ENGINE_DEFAULTS")
        self.assertEqual(defaults["lot_size"], 65)
        self.assertEqual(defaults["capital_per_trade"], 300000.0)
        config = dict(instrument=dict(symbol="NIFTY", timeframe="5m"), period=dict(start_date="2023-01-02", end_date="2026-05-05"),
                      execution=dict(slippage=.005, fees_per_leg=0, timezone="Asia/Kolkata"), parameters={})
        identifier = self.runner.start(dict(entrypoint=source.name, market_path=str(self.market), config=json.dumps(config)),
                                     [("strategy", source.name, source.read_bytes())])
        result = self.finish(identifier)
        self.assertEqual(result["status"], "empty", result)

    def test_standalone_protected_straddle_is_adapted(self):
        import duckdb
        connection = duckdb.connect()
        summary = self.market / "nifty_summary.parquet"
        chain = self.market / "nifty_chain"
        chain.mkdir(exist_ok=True)
        connection.execute("CREATE TABLE summary(datetime VARCHAR, DTE BIGINT, future_close DOUBLE)")
        connection.execute("INSERT INTO summary VALUES ('01/01/2026 10:00:00', 3, 25000)")
        connection.execute("COPY summary TO ? (FORMAT PARQUET)", [str(summary)])
        connection.execute("CREATE TABLE options(datetime VARCHAR, strike BIGINT, ce_close DOUBLE, pe_close DOUBLE, DTE BIGINT)")
        connection.execute("INSERT INTO options VALUES ('01/01/2026 10:00:00', 25000, 100, 100, 3)")
        connection.execute("COPY options TO ? (FORMAT PARQUET)", [str(chain / "part.parquet")])
        connection.close()
        fields = dict(entrypoint="standalone.py", market_path=str(self.market),
                      config=json.dumps({"period": {"start_date": "2026-01-01", "end_date": "2026-01-01"}}))
        identifier = self.runner.start(fields, [("strategy", "standalone.py", STANDALONE_STRATEGY.encode())])
        result = self.finish(identifier)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["result"]["analysis"]["overview"]["net_pnl"], 650)
        self.assertIn("intraday", result["result"]["analysis"])
        self.assertTrue(self.runner.artifact(identifier, "equity.csv").is_file())

    def test_sweep_keeps_every_variation_separate_and_openable(self):
        result = self.finish(self.start(SWEEP_STRATEGY))
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["result"]["mode"], "sweep")
        iterations = result["result"]["sweep"]["iterations"]
        self.assertEqual(len(iterations), 4)
        self.assertEqual([row["parameters"]["take_profit"] for row in iterations], [.4, .6, .8, 1.0])
        self.assertEqual([row["metrics"]["net_pnl"] for row in iterations], [4, 6, 8, 10])
        self.assertEqual(
            [row["metrics"]["yearly_net_pnl"] for row in iterations],
            [{"2026": value} for value in [4, 6, 8, 10]],
        )
        self.assertEqual([row["metrics"]["max_consecutive_losses"] for row in iterations], [0, 0, 0, 0])
        self.assertEqual([row["metrics"]["average_profit"] for row in iterations], [4, 6, 8, 10])
        self.assertEqual([row["metrics"]["average_loss"] for row in iterations], [None, None, None, None])
        self.assertEqual(result["result"]["sweep"]["recommended_index"], 3)
        self.assertEqual(result["result"]["sweep"]["recent_year_decay"], .6)
        self.assertEqual(iterations[3]["rank"], 1)
        self.assertGreater(iterations[3]["metrics"]["selection_score"],
                           iterations[0]["metrics"]["selection_score"])
        detail = self.runner.iteration(result["id"], 2)
        self.assertEqual(detail["parameters"], {"take_profit": .8})
        self.assertEqual(detail["metrics"]["net_pnl"], 8)
        iteration_folder = self.runner.jobs[result["id"]]["folder"] / "iterations" / "2"
        self.assertEqual([path.name for path in iteration_folder.iterdir()], ["summary.json"])
        with self.assertRaises(KeyError):
            self.runner.iteration_artifact(result["id"], 2, "trades.csv")

        selected_id = self.runner.start_iteration(result["id"], 2)
        selected = self.finish(selected_id)
        self.assertEqual(selected["status"], "succeeded", selected)
        self.assertEqual(selected["result"]["analysis"]["overview"]["net_pnl"], 8)
        self.assertEqual(selected["result"]["analysis"]["parameters"], {"take_profit": .8})
        self.assertTrue(self.runner.artifact(selected_id, "trades.csv").is_file())
        selected_request = json.loads(
            (self.runner.jobs[selected_id]["folder"] / "request.json").read_text()
        )
        self.assertEqual(selected_request["config"]["parameters"], {"take_profit": .8})
        self.assertEqual(selected_request["optimizer_parent_id"], result["id"])

    def test_sweep_rejects_duplicate_parameter_sets(self):
        source = SWEEP_STRATEGY.replace('{"take_profit": 0.6}', '{"take_profit": 0.4}')
        result = self.finish(self.start(source))
        self.assertEqual(result["status"], "failed")
        self.assertIn("duplicates an earlier parameter set", result["error"])

    def test_any_completed_combination_can_be_saved_and_survives_restart(self):
        sweep = self.finish(self.start(SWEEP_STRATEGY))
        self.assertEqual(sweep["status"], "succeeded", sweep)
        self.assertEqual(self.runner.history(), [])

        third_id = self.runner.start_iteration(sweep["id"], 2, save_history=True)
        third = self.finish(third_id)
        self.assertEqual(third["status"], "succeeded", third)
        self.assertEqual(third["history_id"], f"{sweep['id']}s2")

        best_id = self.runner.start_iteration(sweep["id"], 3, save_history=True)
        best = self.finish(best_id)
        self.assertEqual(best["status"], "succeeded", best)
        self.assertEqual(best["history_id"], f"{sweep['id']}s3")

        records = sorted(self.runner.history(), key=lambda record: record["parameters"]["take_profit"])
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["parameters"], {"take_profit": .8})
        self.assertEqual(records[0]["rank"], 2)
        self.assertEqual(records[0]["metrics"]["net_pnl"], 8)
        self.assertEqual(records[1]["parameters"], {"take_profit": 1.0})
        self.assertEqual(records[1]["rank"], 1)
        self.assertEqual(records[1]["metrics"]["net_pnl"], 10)

        third_history_id = f"{sweep['id']}s2"
        best_history_id = f"{sweep['id']}s3"
        self.assertEqual(self.runner.history_item(third_history_id)["analysis"]["overview"]["net_pnl"], 8)
        self.assertEqual(self.runner.history_item(best_history_id)["analysis"]["overview"]["net_pnl"], 10)
        self.assertTrue(self.runner.history_artifact(third_history_id, "trades.csv").is_file())
        self.assertTrue(self.runner.history_artifact(best_history_id, "trades.csv").is_file())

        reopened = LocalRunner(history_root=self.market / "history")
        self.addCleanup(reopened.close)
        reopened_records = sorted(reopened.history(), key=lambda record: record["parameters"]["take_profit"])
        self.assertEqual([record["id"] for record in reopened_records], [third_history_id, best_history_id])
        self.assertEqual(reopened.history_item(third_history_id)["parameters"], {"take_profit": .8})
        self.assertEqual(reopened.history_item(best_history_id)["parameters"], {"take_profit": 1.0})

    def test_saved_history_keeps_twenty_most_recent_results(self):
        for index in range(MAX_SAVED_HISTORY + 1):
            folder = self.market / f"job-{index}"
            folder.mkdir()
            (folder / "trades.csv").write_text("trades\n", encoding="utf-8")
            job = {
                "folder": folder,
                "history_parent_id": f"saved{index}",
                "history_summary": {
                    "parameters": {"index": index},
                    "rank": index + 1,
                    "metrics": {"net_pnl": index},
                },
                "result": {
                    "analysis": {
                        "overview": {"strategy": "Retention test", "net_pnl": index},
                        "dataset": {"id": "test", "label": "Test"},
                    },
                },
            }
            self.runner._save_history(f"job{index}", job)

        records = self.runner.history()
        self.assertEqual(len(records), MAX_SAVED_HISTORY)
        self.assertEqual(records[0]["id"], f"saved{MAX_SAVED_HISTORY}")
        self.assertEqual(records[-1]["id"], "saved1")
        self.assertFalse((self.market / "history" / "saved0").exists())
        self.assertTrue((self.market / "history" / f"saved{MAX_SAVED_HISTORY}").exists())

    def test_quartile_score_uses_all_selection_metrics(self):
        self.assertEqual(QUARTILE_POINTS, (3, 2, -2, -3))
        self.assertEqual(SELECTION_METRICS, (
            "net_pnl", "recent_year_pnl", "drawdown", "average_win_loss_ratio",
            "win_rate", "max_consecutive_losses",
        ))

        def row(index, pnl, drawdown, ratio, win_rate, streak, yearly=None):
            return {
                "index": index,
                "status": "succeeded",
                "parameters": {"value": index},
                "metrics": {
                    "net_pnl": pnl,
                    "yearly_net_pnl": yearly or {"2025": pnl * .4, "2026": pnl * .6},
                    "max_drawdown": drawdown,
                    "intraday_drawdown": None,
                    "win_rate": win_rate,
                    "profit_factor": ratio,
                    "max_consecutive_losses": streak,
                    "average_profit": ratio * 100,
                    "average_loss": -100,
                    "batch_count": 20,
                },
            }

        rows = [
            row(0, 1000, -500, 2, 60, 3, {"2025": 900, "2026": 100}),
            row(1, 800, -100, 3, 65, 2, {"2025": 100, "2026": 700}),
            row(2, 200, -50, 1, 40, 5, {"2025": 100, "2026": 100}),
            row(3, -100, -10, 5, 90, 1),
        ]
        self.assertEqual(_rank_sweep(rows), 1)
        self.assertEqual(rows[1]["rank"], 1)
        self.assertNotIn("rank", rows[3])

        bands = [
            row(10, 400, -10, 4, 80, 1, {"2026": 400}),
            row(11, 300, -20, 3, 70, 2, {"2026": 300}),
            row(12, 200, -30, 2, 60, 3, {"2026": 200}),
            row(13, 100, -40, 1, 50, 4, {"2026": 100}),
        ]
        self.assertEqual(_rank_sweep(bands), 10)
        for item, points in zip(bands, QUARTILE_POINTS):
            self.assertEqual(
                item["metrics"]["selection_components"],
                {name: points for name in SELECTION_METRICS},
            )
            self.assertEqual(item["metrics"]["selection_score"], points * len(SELECTION_METRICS))

    def test_latest_year_pnl_has_priority_over_older_year_pnl(self):
        def row(index, yearly):
            return {
                "index": index,
                "status": "succeeded",
                "parameters": {"value": index},
                "metrics": {
                    "net_pnl": 100,
                    "yearly_net_pnl": yearly,
                    "max_drawdown": -20,
                    "intraday_drawdown": None,
                    "win_rate": 60,
                    "profit_factor": 2,
                    "max_consecutive_losses": 2,
                    "average_profit": 200,
                    "average_loss": -100,
                    "batch_count": 20,
                },
            }

        rows = [
            row(0, {"2025": 20, "2026": 80}),
            row(1, {"2025": 80, "2026": 20}),
        ]

        self.assertEqual(_rank_sweep(rows), 0)
        self.assertEqual(rows[0]["rank"], 1)
        self.assertGreater(
            rows[0]["metrics"]["selection_components"]["recent_year_pnl"],
            rows[1]["metrics"]["selection_components"]["recent_year_pnl"],
        )

    def test_sweep_runs_more_than_five_hundred_combinations(self):
        source = '''
STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "sweep"
SWEEP_PARAMETER_SETS = tuple({"value": value} for value in range(501))
def run_strategy(context):
    return {"completed_trades": [], "completed_trade_count": 0}
'''
        identifier = self.start(source)
        self.assertEqual(self.runner.jobs[identifier]["timeout"], SWEEP_RUN_TIMEOUT)
        result = self.finish(identifier)
        self.assertEqual(result["status"], "succeeded", result)
        sweep = result["result"]["sweep"]
        self.assertEqual(sweep["iteration_count"], 501)
        self.assertEqual(sweep["no_trade_count"], 501)

    def test_sweep_exposes_current_combination_progress_and_rolling_eta(self):
        source = '''
import time
STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "sweep"
SWEEP_PARAMETER_SETS = ({"value": 1}, {"value": 2})
def run_strategy(context):
    context.report_progress(2, 4, "trading day")
    time.sleep(.5)
    return {"completed_trades": [], "completed_trade_count": 0}
'''
        identifier = self.start(source)
        observed = None
        deadline = time.time() + 3
        while time.time() < deadline:
            progress = self.runner.status(identifier)["progress"]
            if progress and progress.get("combination_completed") == 2:
                observed = progress
                break
            time.sleep(.02)
        self.assertIsNotNone(observed)
        self.assertEqual(observed["mode"], "sweep")
        self.assertEqual(observed["current"], 1)
        self.assertEqual(observed["current_percentage"], 50.0)
        self.assertIsNotNone(observed["current_estimated_remaining_seconds"])
        result = self.finish(identifier)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertIsNotNone(result["progress"]["average_combination_seconds"])
        self.assertEqual(result["progress"]["estimated_remaining_seconds"], 0)

    def test_cancelled_sweep_keeps_completed_combinations_reviewable(self):
        source = SWEEP_STRATEGY.replace(
            "import csv", "import csv\nimport time"
        ).replace(
            'scale = context.config["parameters"]["take_profit"]',
            'scale = context.config["parameters"]["take_profit"]\n    if scale == .6: time.sleep(2)',
        )
        identifier = self.start(source)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            progress = self.runner.status(identifier)["progress"]
            if progress and progress.get("completed") >= 1 and progress.get("current") == 2:
                break
            time.sleep(.02)
        else:
            self.fail("Sweep did not complete its first combination")

        self.runner.cancel(identifier)
        stopped = self.runner.status(identifier)
        self.assertEqual(stopped["status"], "cancelled")
        partial = stopped["partial_sweep"]
        self.assertEqual(partial["iteration_count"], 4)
        self.assertEqual(partial["processed_count"], 1)
        self.assertEqual(partial["completed_count"], 1)
        self.assertTrue(partial["partial"])
        self.assertEqual(partial["recommended_index"], 0)
        self.assertEqual(partial["iterations"][0]["parameters"], {"take_profit": .4})
        self.assertEqual(self.runner.iteration(identifier, 0)["status"], "succeeded")

        self.assertEqual(self.runner.resume(identifier), identifier)
        resumed = self.finish(identifier)
        self.assertEqual(resumed["status"], "succeeded", resumed)
        self.assertEqual(resumed["result"]["sweep"]["processed_count"], 4)
        self.assertEqual(len(resumed["result"]["sweep"]["iterations"]), 4)
        self.assertEqual(resumed["result"]["sweep"]["recommended_index"], 3)

        selected = self.finish(self.runner.start_iteration(identifier, 0))
        self.assertEqual(selected["status"], "succeeded", selected)
        self.assertEqual(selected["result"]["analysis"]["overview"]["net_pnl"], 4)

    def test_failed_sweep_variation_does_not_stop_later_combinations(self):
        source = '''
STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "sweep"
SWEEP_PARAMETER_SETS = ({"value": 1}, {"value": 2}, {"value": 3})
def run_strategy(context):
    if context.config["parameters"]["value"] == 2:
        raise ValueError("invalid combination")
    return {"completed_trades": [], "completed_trade_count": 0}
'''
        result = self.finish(self.start(source))
        self.assertEqual(result["status"], "succeeded", result)
        sweep = result["result"]["sweep"]
        self.assertEqual(sweep["failed_count"], 1)
        self.assertEqual([item["status"] for item in sweep["iterations"]],
                         ["no_trades", "failed", "no_trades"])

    def test_selected_combination_must_reproduce_its_sweep_result(self):
        source = '''
STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "sweep"
SWEEP_PARAMETER_SETS = ({"value": 1},)
def run_strategy(context):
    marker = context.market_data / "selection-marker"
    exit_price = 120 if not marker.exists() else 130
    marker.write_text("seen")
    trade = dict(run_id=context.run_id, trade_id="1", batch_id="1", leg_id="1",
        strategy="Reproduction check", symbol="TEST", side="LONG",
        entry_time="2026-01-01T10:00:00+00:00", exit_time="2026-01-01T11:00:00+00:00",
        quantity=1, entry_price=100, exit_price=exit_price, multiplier=1, fees=0)
    return {"completed_trades": [trade], "completed_trade_count": 1}
'''
        sweep = self.finish(self.start(source))
        selected = self.finish(self.runner.start_iteration(sweep["id"], 0))
        self.assertEqual(selected["status"], "failed", selected)
        self.assertIn("did not match its sweep row", selected["error"])

    def test_standalone_protected_straddle_can_declare_a_sweep(self):
        import duckdb
        connection = duckdb.connect()
        summary = self.market / "nifty_summary.parquet"
        chain = self.market / "nifty_chain"
        chain.mkdir(exist_ok=True)
        connection.execute("CREATE TABLE summary(datetime VARCHAR, DTE BIGINT, future_close DOUBLE)")
        connection.execute("INSERT INTO summary VALUES ('01/01/2026 10:00:00', 3, 25000)")
        connection.execute("COPY summary TO ? (FORMAT PARQUET)", [str(summary)])
        connection.execute("CREATE TABLE options(datetime VARCHAR, strike BIGINT, ce_close DOUBLE, pe_close DOUBLE, DTE BIGINT)")
        connection.execute("INSERT INTO options VALUES ('01/01/2026 10:00:00', 25000, 100, 100, 3)")
        connection.execute("COPY options TO ? (FORMAT PARQUET)", [str(chain / "part.parquet")])
        connection.close()
        source = ("RUN_MODE = 'sweep'\nSWEEP_PARAMETER_SETS = ({'lot_size': 10}, {'lot_size': 20})\n"
                  + STANDALONE_STRATEGY.replace("assert self.cfg.lot_size == 65", "assert self.cfg.lot_size in {10, 20}"))
        fields = dict(entrypoint="standalone.py", market_path=str(self.market),
                      config=json.dumps({"period": {"start_date": "2026-01-01", "end_date": "2026-01-01"}}))
        result = self.finish(self.runner.start(fields, [("strategy", "standalone.py", source.encode())]))
        self.assertEqual(result["status"], "succeeded", result)
        iterations = result["result"]["sweep"]["iterations"]
        self.assertEqual([row["metrics"]["net_pnl"] for row in iterations], [100, 200])
