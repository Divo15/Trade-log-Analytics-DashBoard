from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from trade_log_dashboard.storage import load_storage


class StorageTests(unittest.TestCase):
    def test_creates_separate_writable_user_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout = load_storage(user_root=root / "user", project_root=root / "project")

            self.assertTrue(layout.settings_file.is_file())
            self.assertTrue(layout.logs_root.is_dir())
            self.assertTrue(layout.results_root.is_dir())
            self.assertTrue(layout.cache_root.is_dir())
            self.assertEqual(layout.dataset_root, (root / "user" / "datasets").resolve())
            saved = json.loads(layout.settings_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["dataset_root"], str(layout.dataset_root))

    def test_existing_results_are_copied_without_overwriting_user_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "project" / "history" / "run-one"
            legacy.mkdir(parents=True)
            (legacy / "metadata.json").write_text('{"source":"legacy"}', encoding="utf-8")
            layout = load_storage(user_root=root / "user", project_root=root / "project")
            copied = layout.results_root / "run-one" / "metadata.json"
            self.assertEqual(json.loads(copied.read_text(encoding="utf-8")), {"source": "legacy"})

            copied.write_text('{"source":"user"}', encoding="utf-8")
            load_storage(user_root=root / "user", project_root=root / "project")
            self.assertEqual(json.loads(copied.read_text(encoding="utf-8")), {"source": "user"})


if __name__ == "__main__":
    unittest.main()
