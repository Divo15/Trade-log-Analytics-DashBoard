import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from trade_log_dashboard.research import ResearchManager, check_candidate, connection_status, validate_reply, MODELS
from trade_log_dashboard.storage import load_storage

SOURCE = '''STRATEGY_CONTRACT_VERSION = "2"
RUN_MODE = "single"
SWEEP_PARAMETER_SETS = ()
def run_strategy(context):
    return {"completed_trades": [], "completed_trade_count": 0}
'''


def worker(**extra):
    return dict(summary="Measured task result", limitations=[], evidence=[], outcome="complete",
                strategy_file="", passed=False, **extra)


def lead(action):
    return dict(summary="Research decision", limitations=[], evidence=[], action=action,
                assignment="Investigate the specified full trade journey", journey="Flat → short → exit → stay flat")


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = load_storage(user_root=self.root / "user", project_root=self.root)
        self.runner = Mock()
        self.runner.has_running_job.return_value = False
        self.manager = ResearchManager(self.storage, self.runner)
        self.dataset = dict(id="test", label="Test market", symbol="NIFTY", start_date="2025-01-01", end_date="2026-01-01")
        self.auth = patch("trade_log_dashboard.research.connection_status", return_value={"ready": True})
        self.resolve = patch("trade_log_dashboard.research.resolve_dataset", return_value=(self.root, self.dataset))
        self.auth.start(); self.resolve.start()

    def tearDown(self):
        self.manager.close()
        self.auth.stop(); self.resolve.stop()
        self.temp.cleanup()

    def start(self, maximum=12):
        return self.manager.start(dict(objective="Discover selling strategies from measured market behaviour", dataset_id="test", max_calls=maximum))

    def wait(self, identifier):
        deadline = time.monotonic() + 5
        while self.manager.has_running_job() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.manager.has_running_job())
        return self.manager.status(identifier)

    def test_routing_review_and_explicit_handoff(self):
        calls = []
        def execute(state, role, folder, control):
            calls.append((role, state["tasks"][-1]["model"]))
            if role == "lead":
                return lead("candidate" if state["review_passed"] else "strategy")
            result = worker()
            if role == "strategy":
                (folder / "candidate.py").write_text(SOURCE)
                result["strategy_file"] = "candidate.py"
            if role == "review":
                result["passed"] = True
            return result
        with patch.object(self.manager, "_call", side_effect=execute):
            identifier = self.start()
            state = self.wait(identifier)
        self.assertEqual(state["status"], "candidate_ready")
        self.assertEqual([role for role, _ in calls], ["data", "lead", "strategy", "review", "lead"])
        for role, model in calls:
            self.assertEqual(model, MODELS["lead" if role == "lead" else "worker"])
        result = self.manager.handoff(identifier)
        self.assertEqual(result["source"], SOURCE)
        self.runner.start.assert_not_called()
        candidate = self.manager.artifact(identifier, "3", "candidate.py")
        candidate.write_text(SOURCE + "\n# changed after review\n")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            self.manager.handoff(identifier)

    def test_lead_cannot_skip_review(self):
        with patch.object(self.manager, "_call", side_effect=lambda state, role, folder, control: worker() if role == "data" else lead("candidate")):
            identifier = self.start()
            state = self.wait(identifier)
        self.assertEqual(state["status"], "failed")
        self.assertIn("without a reviewed candidate", state["error"])
        with self.assertRaises(ValueError):
            self.manager.handoff(identifier)

    def test_budget_stops_loop_and_cannot_resume(self):
        with patch.object(self.manager, "_call", side_effect=lambda state, role, folder, control: lead("experiment") if role == "lead" else worker()):
            identifier = self.start(maximum=4)
            state = self.wait(identifier)
        self.assertEqual(state["status"], "budget_reached")
        self.assertEqual(state["calls_used"], 4)
        with self.assertRaises(ValueError):
            self.manager.resume(identifier)

    def test_user_messages_survive_inflight_completion(self):
        waiting, release = threading.Event(), threading.Event()
        def execute(state, role, folder, control):
            if role == "data":
                waiting.set(); release.wait(3)
                return worker()
            return lead("reject")
        with patch.object(self.manager, "_call", side_effect=execute):
            identifier = self.start()
            self.assertTrue(waiting.wait(2))
            self.manager.message(identifier, "No expiry-day entries")
            release.set()
            state = self.wait(identifier)
        self.assertTrue(any(row["text"] == "No expiry-day entries" for row in state["messages"]))

    def test_stop_and_resume_keep_completed_work(self):
        waiting = threading.Event()
        def execute(state, role, folder, control):
            if role == "data":
                waiting.set(); control["stop"].wait(3)
                raise InterruptedError("Stopped")
            return lead("reject")
        with patch.object(self.manager, "_call", side_effect=execute):
            identifier = self.start()
            self.assertTrue(waiting.wait(2))
            self.manager.stop(identifier)
            self.assertEqual(self.wait(identifier)["status"], "stopped")
        with patch.object(self.manager, "_call", side_effect=lambda state, role, folder, control: worker() if role == "data" else lead("reject")):
            self.manager.resume(identifier)
            state = self.wait(identifier)
        self.assertEqual(state["status"], "no_candidate")
        self.assertEqual(state["calls_used"], 3)
        self.assertEqual(state["tasks"][0]["status"], "stopped")

    def test_artifact_escape_rejected(self):
        task = self.root / "task"; task.mkdir()
        (self.root / "secret.txt").write_text("private")
        with self.assertRaises(ValueError):
            self.manager._safe_artifact(task, "../secret.txt")
        with self.assertRaises(KeyError):
            self.manager.status("../outside")

    def test_reply_validation_rejects_wrong_action_and_types(self):
        value = lead("candidate")
        value["action"] = "execute_trade"
        with self.assertRaises(ValueError): validate_reply(value, "lead")
        value = worker(); value["passed"] = "true"
        with self.assertRaises(ValueError): validate_reply(value, "review")

    def test_sweep_candidate_rejected_without_execution(self):
        path = self.root / "candidate.py"
        path.write_text(SOURCE.replace('RUN_MODE = "single"', 'RUN_MODE = "sweep"'))
        with self.assertRaises(ValueError): check_candidate(path)

    def test_worker_failure_is_not_success(self):
        with patch.object(self.manager, "_call", side_effect=ValueError("Model unavailable")):
            state = self.wait(self.start())
        self.assertEqual(state["status"], "failed")
        self.assertIsNone(state["candidate"])

    def test_backtest_blocks_research(self):
        self.runner.has_running_job.return_value = True
        with self.assertRaisesRegex(ValueError, "current research/backtest"):
            self.start()

    def test_api_key_login_is_not_accepted(self):
        # Use the imported function, not the manager's patched module reference.
        with patch("trade_log_dashboard.research.codex_command", return_value=["codex"]), patch("trade_log_dashboard.research.subprocess.run") as run:
            run.return_value = Mock(returncode=0, stdout="Logged in using an API key", stderr="")
            self.assertFalse(connection_status()["ready"])
            run.return_value = Mock(returncode=0, stdout="", stderr="Logged in using ChatGPT")
            self.assertTrue(connection_status()["ready"])

    def test_restart_marks_incomplete_task_interrupted(self):
        with patch.object(self.manager, "_launch"):
            identifier = self.start()
        state = self.manager.status(identifier)
        state["tasks"].append({"status": "running"})
        self.manager._save(state)
        restored = ResearchManager(self.storage, self.runner)
        self.assertEqual(restored.status(identifier)["status"], "interrupted")
        self.assertEqual(restored.status(identifier)["tasks"][0]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
