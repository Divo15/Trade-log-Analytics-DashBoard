"""SENSEX selection reaches the worker request without importing strategy code."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from trade_log_dashboard.runner import LocalRunner


class SensexSelectionTests(unittest.TestCase):
    def test_runner_uses_sensex_path_period_and_symbol(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dataset = root / "sensex current week"
            (dataset / "sensex_chain").mkdir(parents=True)
            (dataset / "sensex_summary.parquet").touch()
            (dataset / "sensex_chain" / "part.parquet").touch()
            runner = LocalRunner(
                history_root=root / "history",
                dataset_root=root,
                dataset_cache_path=root / "coverage.json",
            )
            process = Mock()
            process.poll.return_value = 0
            def launched(job_folder, timeout):
                return dict(folder=job_folder, process=process, status="running",
                            started=time.monotonic(), timeout=timeout)
            try:
                with patch("trade_log_dashboard.datasets._coverage",
                           return_value=("2024-01-01", "2026-09-01", 600)), \
                     patch.object(runner, "_launch", side_effect=launched), \
                     patch("trade_log_dashboard.runner.threading.Thread"):
                    identifier = runner.start(
                        {
                            "entrypoint": "strategy.py",
                            "dataset_id": "sensex-weekly",
                            "config": json.dumps({
                                "instrument": {"symbol": "NIFTY", "timeframe": "1m"},
                                "parameters": {},
                            }),
                        },
                        [("strategy", "strategy.py", b'RUN_MODE = "single"\n')],
                    )
                request = json.loads(
                    (runner.jobs[identifier]["folder"] / "request.json").read_text(encoding="utf-8")
                )
                self.assertEqual(request["dataset_id"], "sensex-weekly")
                self.assertEqual(request["dataset"]["symbol"], "SENSEX")
                self.assertEqual(request["config"]["instrument"], {
                    "symbol": "SENSEX", "timeframe": "1m",
                })
                self.assertEqual(request["config"]["period"], {
                    "start_date": "2024-01-01", "end_date": "2026-09-01",
                })
                self.assertEqual(Path(request["market_data"]), dataset.resolve())
            finally:
                runner.close()


if __name__ == "__main__":
    unittest.main()
