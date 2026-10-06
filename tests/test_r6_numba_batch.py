"""Regression tests for the provisional R6 numeric batch accelerator."""

from pathlib import Path
import tempfile
import unittest

import numpy as np

from strategies import nifty_current_week_0dte_trend_following_r6_numba_batch as batch
from strategies import nifty_current_week_0dte_trend_following_r6_sweep_dashboard as strategy
from tools.run_r6_numba_batch import run_checkpoints
from tools.verify_r6_numba_batch import (
    _batch_rows,
    _context,
    _oracle_rows,
    _assert_metrics,
    synthetic_market,
)


@unittest.skipUnless(batch.NUMBA_AVAILABLE, "Numba strategy dependency is unavailable")
class R6NumbaBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary, chain = synthetic_market()
        cls.chains = {
            day: group.copy() for day, group in chain.groupby(chain["ts"].dt.date)
        }

    def test_declared_grid_matches_authoritative_strategy(self):
        parameters = list(strategy.SWEEP_PARAMETER_SETS)
        expected = _oracle_rows(self.summary, self.chains, parameters)
        actual = _batch_rows(self.summary, self.chains, parameters, workers=2)
        _assert_metrics(expected, actual, "declared synthetic grid")

    def test_checkpoint_chunks_are_numeric_and_resumable(self):
        parameters = list(strategy.SWEEP_PARAMETER_SETS[:9])
        from tools.verify_r6_numba_batch import _patched_market

        with tempfile.TemporaryDirectory() as folder, _patched_market(self.summary, self.chains):
            output = Path(folder)
            first = run_checkpoints(_context(), parameters, output, chunk_size=4, workers=2)
            mtimes = {
                name: (output / name).stat().st_mtime_ns for name in first["checkpoints"]
            }
            second = run_checkpoints(_context(), parameters, output, chunk_size=4, workers=2)
            self.assertEqual(first, second)
            self.assertEqual(
                mtimes,
                {name: (output / name).stat().st_mtime_ns for name in second["checkpoints"]},
            )
            for name in second["checkpoints"]:
                with np.load(output / name, allow_pickle=False) as saved:
                    self.assertEqual(saved["parameters"].shape[1], len(batch.PARAMETER_COLUMNS))
                    self.assertEqual(saved["metrics"].shape[1], len(batch.METRIC_COLUMNS))
                    self.assertTrue(np.all(saved["metrics"][:, batch.M_STATUS] == batch.STATUS_OK))


if __name__ == "__main__":
    unittest.main()

