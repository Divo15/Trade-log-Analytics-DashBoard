from __future__ import annotations

import json
from pathlib import Path
import ast
import unittest


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "integration" / "COLAB_BACKTEST_TEMPLATE.ipynb"


class ColabTemplateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        cls.source = "\n".join(
            "".join(cell.get("source", [])) for cell in cls.notebook["cells"]
        )

    def test_is_valid_notebook_with_no_saved_outputs(self) -> None:
        self.assertEqual(self.notebook["nbformat"], 4)
        self.assertGreaterEqual(len(self.notebook["cells"]), 8)
        for cell in self.notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])

    def test_python_cells_have_valid_syntax(self) -> None:
        for index, cell in enumerate(self.notebook["cells"]):
            if cell["cell_type"] != "code":
                continue
            source = "".join(cell["source"])
            python_source = "\n".join(
                line for line in source.splitlines() if not line.lstrip().startswith("%")
            )
            with self.subTest(cell=index):
                ast.parse(python_source)

    def test_preserves_contract_and_authoritative_count(self) -> None:
        required = (
            "STRATEGY_CONTRACT_VERSION",
            "RUN_MODE",
            "run_strategy(context)",
            "completed_trades",
            "completed_trade_count",
            "expected_count=int(result[\"completed_trade_count\"])",
            "export_sweep_summary",
        )
        for text in required:
            self.assertIn(text, self.source)

    def test_supports_drive_upload_validation_and_download(self) -> None:
        required = (
            "drive.mount",
            "files.upload()",
            "context.market_data",
            "validate_trade_log_csv",
            "files.download(str(receipt.output_path))",
            "files.download(str(receipt.manifest_path))",
        )
        for text in required:
            self.assertIn(text, self.source)

    def test_contains_no_desktop_specific_path_or_analytics_call(self) -> None:
        self.assertNotIn("C:\\\\", self.source)
        self.assertNotIn("D:\\\\", self.source)
        self.assertNotIn("analyze_trade_log", self.source)


if __name__ == "__main__":
    unittest.main()
