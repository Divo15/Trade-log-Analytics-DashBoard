"""Shared runtime cache tests independent of trading rules."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch
from types import SimpleNamespace
import pandas as pd
from trade_log_dashboard.market_data import MarketDataLoader


class SharedCacheTests(unittest.TestCase):
    def test_worker_shares_loader_across_combinations(self):
        from trade_log_dashboard.worker import _run_sweep
        with tempfile.TemporaryDirectory() as folder, MarketDataLoader() as loader:
            read = Mock(return_value=pd.DataFrame({"price": [1.]}))
            seen = []
            def execute(context):
                seen.append(context.market_data_loader)
                context.market_data_loader.read_frame([], "same-data", read)
                return {}
            module = SimpleNamespace(SWEEP_PARAMETER_SETS=({"target": 1}, {"target": 2}), run_strategy=execute)
            context = SimpleNamespace(run_id="test", market_data=Path(folder), config={}, market_data_loader=loader)
            with patch("trade_log_dashboard.worker._execute_result", return_value={"status": "empty"}):
                result = _run_sweep(module, context, Path(folder), None)
            self.assertEqual(result["sweep"]["failed_count"], 0)
            self.assertEqual(seen, [loader, loader])
            self.assertEqual(read.call_count, 1)

    def test_memory_isolation_eviction_and_changed_input(self):
        frame = pd.DataFrame({"price": [1., 2.]})
        size = int(frame.memory_usage(index=True, deep=True).sum())
        with MarketDataLoader(memory_limit=size) as loader:
            callback = Mock(side_effect=lambda: frame * 2)
            first = loader.prepare_frame("double", [frame], callback)
            first.iloc[0, 0] = 99
            self.assertEqual(loader.prepare_frame("double", [frame], callback).iloc[0, 0], 2)
            self.assertEqual(callback.call_count, 1)
            frame.iloc[0, 0] = 3
            self.assertEqual(loader.prepare_frame("double", [frame], callback).iloc[0, 0], 6)
            self.assertEqual(callback.call_count, 2)
            self.assertLessEqual(loader.memory_bytes, size)
            self.assertEqual(len(loader._memory), 1)

    def test_file_cache_invalidation_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.csv"
            source.write_text("price\n1\n")
            with MarketDataLoader(directory=folder) as loader:
                cache_folder = Path(loader._temporary.name)
                read = Mock(side_effect=lambda: pd.read_csv(source))
                first = loader.read_frame([source], "csv-v1", read)
                first.iloc[0, 0] = 99
                self.assertEqual(loader.read_frame([source], "csv-v1", read).iloc[0, 0], 1)
                self.assertEqual(read.call_count, 1)
                self.assertGreater(loader.memory_bytes, 0)
                source.write_text("price\n200\n")
                self.assertEqual(loader.read_frame([source], "csv-v1", read).iloc[0, 0], 200)
                self.assertEqual(read.call_count, 2)
            self.assertFalse(cache_folder.exists())

    def test_time_slices_and_operation_keys_are_separate(self):
        frame = pd.DataFrame({"price": [1., 2.]})
        with MarketDataLoader() as loader:
            whole = loader.prepare_frame("identity", [frame], lambda: frame)
            later = frame.iloc[1:]
            result = loader.prepare_frame("identity", [later], lambda: later)
            self.assertEqual(len(whole), 2)
            pd.testing.assert_frame_equal(result, later)
            changed = loader.prepare_frame("double", [frame], lambda: frame * 2)
            self.assertEqual(changed.iloc[0, 0], 2)

    def test_disk_budget_and_callback_errors(self):
        with MarketDataLoader(disk_limit=1) as loader:
            frame = pd.DataFrame({"price": [1.]})
            loader.read_frame([], "large", lambda: frame)
            self.assertEqual(loader.disk_bytes, 0)
            with self.assertRaisesRegex(ValueError, "bad source"):
                loader.read_frame([], "bad", Mock(side_effect=ValueError("bad source")))

    def test_partitioned_dataset_reuse_budget_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.parquet"
            source.write_bytes(b"source")
            with MarketDataLoader(directory=root, disk_limit=100) as loader:
                cache_folder = Path(loader._temporary.name)
                build = Mock(side_effect=lambda destination: (
                    (destination / "trade_date=2026-01-01").mkdir(parents=True),
                    (destination / "trade_date=2026-01-01" / "part.parquet").write_bytes(b"data"),
                ))
                first = loader.partitioned_dataset([source], "by-date", build)
                second = loader.partitioned_dataset([source], "by-date", build)
                self.assertEqual(first, second)
                self.assertEqual(build.call_count, 1)
                self.assertEqual(loader.stats["partition_misses"], 1)
                self.assertEqual(loader.stats["partition_hits"], 1)
                self.assertLessEqual(loader.disk_bytes, loader.disk_limit)
                loader.read_frame([], "extra", lambda: pd.DataFrame({"value": [1] * 100}))
                self.assertLessEqual(loader.disk_bytes, loader.disk_limit)
            self.assertFalse(cache_folder.exists())

    def test_oversized_partitioned_dataset_is_not_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.parquet"
            source.write_bytes(b"source")
            with MarketDataLoader(directory=root, disk_limit=3) as loader:
                def build(destination):
                    destination.mkdir()
                    (destination / "part.parquet").write_bytes(b"too large")
                self.assertIsNone(loader.partitioned_dataset([source], "large", build))
                self.assertEqual(loader.disk_bytes, 0)

    def test_shared_object_reuse_invalidation_budget_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.bin"
            source.write_bytes(b"one")
            with MarketDataLoader(directory=root, disk_limit=1024) as loader:
                cache_folder = Path(loader._temporary.name)
                build = Mock(return_value=("immutable", 1, 2, 3))
                first = loader.shared_object([source], "index-v1", build)
                second = loader.shared_object([source], "index-v1", build)
                self.assertEqual(first, second)
                self.assertEqual(build.call_count, 1)
                self.assertEqual(loader.stats["shared_misses"], 1)
                self.assertEqual(loader.stats["shared_hits"], 1)
                self.assertLessEqual(loader.disk_bytes, loader.disk_limit)

                source.write_bytes(b"changed source")
                loader.shared_object([source], "index-v1", build)
                self.assertEqual(build.call_count, 2)
                self.assertEqual(loader.stats["shared_misses"], 2)
            self.assertFalse(cache_folder.exists())

        with MarketDataLoader(disk_limit=1) as loader:
            build = Mock(return_value=("too large",) * 100)
            loader.shared_object([], "oversized", build)
            loader.shared_object([], "oversized", build)
            self.assertEqual(build.call_count, 2)
            self.assertEqual(loader.disk_bytes, 0)
