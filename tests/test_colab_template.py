from __future__ import annotations

import json
from pathlib import Path
import ast
import unittest


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "integration" / "COLAB_BACKTEST_TEMPLATE.ipynb"
CONTRACT = ROOT / "integration" / "STRATEGY_SCRIPT_CONTRACT.md"
PROMPT = ROOT / "integration" / "CHATGPT_STRATEGY_PROMPT.md"


class ColabTemplateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        cls.source = "\n".join(
            "".join(cell.get("source", [])) for cell in cls.notebook["cells"]
        )
        cls.code_source = "\n".join(
            "".join(cell.get("source", []))
            for cell in cls.notebook["cells"]
            if cell["cell_type"] == "code"
        )
        cls.contract = CONTRACT.read_text(encoding="utf-8")
        cls.prompt = PROMPT.read_text(encoding="utf-8")

        python_code_source = "\n".join(
            line
            for line in cls.code_source.splitlines()
            if not line.lstrip().startswith("%")
        )
        tree = ast.parse(python_code_source)
        path_helpers = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {
                "require_colab_drive_path",
                "select_drive_market_data_path",
            }
        ]
        namespace: dict[str, object] = {}
        exec(compile(ast.Module(path_helpers, type_ignores=[]), str(NOTEBOOK), "exec"), namespace)
        cls.require_colab_drive_path = staticmethod(namespace["require_colab_drive_path"])
        cls.select_drive_market_data_path = staticmethod(
            namespace["select_drive_market_data_path"]
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
            'STRATEGY_CONTRACT_VERSION", None) != "2"',
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
            "market_data=market_data",
            "validate_trade_log_csv",
            "files.download(str(receipt.output_path))",
            "files.download(str(receipt.manifest_path))",
            "strategy_default_path",
            "notebook_path or strategy_default_path",
        )
        for text in required:
            self.assertIn(text, self.source)

    def test_contains_no_desktop_specific_path_or_analytics_call(self) -> None:
        self.assertNotIn("C:\\\\", self.source)
        self.assertNotIn("D:\\\\", self.source)
        self.assertNotIn("analyze_trade_log", self.source)

    def test_contract_v2_required_interface(self) -> None:
        required = (
            '# Strategy Script Contract v2',
            'STRATEGY_CONTRACT_VERSION = "2"',
            'RUN_MODE = "single"',
            'SWEEP_PARAMETER_SETS = ()',
            'DEFAULT_MARKET_DATA_PATH = None',
            'run_strategy(context)',
        )
        for text in required:
            self.assertIn(text, self.contract)

    def test_exact_user_supplied_colab_paths_are_unchanged(self) -> None:
        for supplied in (
            "/content/drive/MyDrive/market data/NIFTY",
            "/content/drive/Shareddrives/Trading/data.parquet",
        ):
            with self.subTest(path=supplied):
                self.assertEqual(self.require_colab_drive_path(supplied, "test"), supplied)

    def test_notebook_override_precedes_strategy_default(self) -> None:
        override = "/content/drive/MyDrive/override/data"
        default = "/content/drive/MyDrive/default/data"
        self.assertEqual(self.select_drive_market_data_path(override, default), override)

    def test_absent_or_none_default_requires_override(self) -> None:
        override = "/content/drive/MyDrive/selected/data"
        self.assertEqual(self.select_drive_market_data_path(override, None), override)
        with self.assertRaisesRegex(
            ValueError, "Set MARKET_DATA_PATH or declare DEFAULT_MARKET_DATA_PATH"
        ):
            self.select_drive_market_data_path("", None)

    def test_embedded_desktop_and_arbitrary_server_paths_are_rejected(self) -> None:
        invalid_paths = (
            r"C:\\Users\\name\\data",
            r"D:\\market-data",
            "/Users/name/Desktop/data",
            "/home/name/data",
            "/srv/market-data",
        )
        for supplied in invalid_paths:
            with self.subTest(path=supplied), self.assertRaises(ValueError):
                self.select_drive_market_data_path("", supplied)

    def test_strategy_import_precedes_drive_mount_and_filesystem_validation(self) -> None:
        import_position = self.source.index("strategy = importlib.import_module")
        mount_position = self.source.index('drive.mount("/content/drive")')
        exists_position = self.source.index("if not market_data.exists()")
        self.assertLess(import_position, mount_position)
        self.assertLess(import_position, exists_position)
        for text in (
            "Importing the module must not",
            "Google Drive",
            "inspect or access the filesystem",
            "load market data",
        ):
            self.assertIn(text, self.prompt)

    def test_upload_branch_does_not_mount_drive(self) -> None:
        python_code_source = "\n".join(
            line
            for line in self.code_source.splitlines()
            if not line.lstrip().startswith("%")
        )
        tree = ast.parse(python_code_source)
        data_source_if = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == "DATA_SOURCE == 'drive'"
        )
        upload_branch = ast.unparse(ast.Module(data_source_if.orelse, type_ignores=[]))
        self.assertIn("files.upload()", upload_branch)
        self.assertNotIn("drive.mount", upload_branch)

    def test_context_receives_resolved_market_data_path(self) -> None:
        self.assertIn("market_data=market_data", self.source)

    def test_single_and_sweep_export_calls_are_unchanged(self) -> None:
        self.assertEqual(self.source.count("receipt = export_trade_log("), 1)
        self.assertEqual(self.source.count("receipt = export_sweep_summary("), 1)


if __name__ == "__main__":
    unittest.main()
