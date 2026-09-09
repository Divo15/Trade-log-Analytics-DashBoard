from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trade_log_dashboard import datasets


class DatasetCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for _, folder in datasets.DATASETS.values():
            dataset = self.root / folder
            (dataset / "nifty_chain").mkdir(parents=True)
            (dataset / "nifty_summary.parquet").write_bytes(b"summary")
            (dataset / "nifty_chain" / "part.parquet").write_bytes(b"chain")

    def test_unchanged_files_use_the_persistent_coverage_cache(self) -> None:
        with patch.object(datasets, "DATA_ROOT", self.root), patch.object(
            datasets, "_coverage", return_value=("2023-01-02", "2026-05-05", 760)
        ) as calculate:
            first = datasets.catalog()
            second = datasets.catalog()

        self.assertEqual(calculate.call_count, len(datasets.DATASETS))
        self.assertEqual(first, second)
        cache = json.loads((self.root / datasets.CACHE_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(cache["version"], datasets.CACHE_VERSION)
        self.assertEqual(set(cache["datasets"]), set(datasets.DATASETS))

    def test_only_changed_dataset_is_recalculated(self) -> None:
        with patch.object(datasets, "DATA_ROOT", self.root), patch.object(
            datasets, "_coverage", return_value=("2023-01-02", "2026-05-05", 760)
        ):
            datasets.catalog()

        changed = self.root / datasets.DATASETS["weekly"][1] / "nifty_chain" / "part.parquet"
        changed.write_bytes(b"changed chain")
        with patch.object(datasets, "DATA_ROOT", self.root), patch.object(
            datasets, "_coverage", return_value=("2023-01-03", "2026-05-05", 759)
        ) as calculate:
            catalog = datasets.catalog()

        calculate.assert_called_once()
        weekly = next(row for row in catalog if row["id"] == "weekly")
        self.assertEqual((weekly["start_date"], weekly["trading_days"]), ("2023-01-03", 759))

    def test_corrupt_cache_is_rebuilt(self) -> None:
        (self.root / datasets.CACHE_FILENAME).write_text("not json", encoding="utf-8")
        with patch.object(datasets, "DATA_ROOT", self.root), patch.object(
            datasets, "_coverage", return_value=("2023-01-02", "2026-05-05", 760)
        ) as calculate:
            catalog = datasets.catalog()

        self.assertTrue(all(row["available"] for row in catalog))
        self.assertEqual(calculate.call_count, len(datasets.DATASETS))
        json.loads((self.root / datasets.CACHE_FILENAME).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
