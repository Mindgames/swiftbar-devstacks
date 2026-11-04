"""Use the REST exit code when a completed Process Compose command failed."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "devstacks_completed_exit", ROOT / "devstacks.5s.py"
)
DEVSTACKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEVSTACKS)


class CompletedExitStatusTests(unittest.TestCase):
    def test_nonzero_completed_exit_is_broken_and_red(self):
        for status in ("Completed", "completed", "cOmPlEtEd"):
            for exit_code in (1, 2, 127, -1):
                with self.subTest(status=status, exit_code=exit_code):
                    process = {"status": status, "exit_code": exit_code, "is_ready": "-"}
                    self.assertTrue(DEVSTACKS.is_broken(process))
                    self.assertFalse(DEVSTACKS.is_healthy(process))
                    self.assertEqual(DEVSTACKS.proc_colour(process), DEVSTACKS.RED)

    def test_completed_without_a_failed_exit_keeps_existing_idle_colour(self):
        for exit_code in (0, None, "0", "1", False, True):
            with self.subTest(exit_code=exit_code):
                process = {"status": "Completed", "exit_code": exit_code}
                self.assertFalse(DEVSTACKS.is_broken(process))
                self.assertEqual(DEVSTACKS.proc_colour(process), DEVSTACKS.DIM)
        self.assertFalse(DEVSTACKS.is_broken({"status": "Completed"}))
        self.assertEqual(DEVSTACKS.proc_colour({"status": "Completed"}), DEVSTACKS.DIM)

    def test_completed_honours_additional_success_exit_codes(self):
        for exit_code, success_codes, broken, colour in (
            (130, [130], False, DEVSTACKS.DIM),
            (130, [143], True, DEVSTACKS.RED),
            (143, [130, 143], False, DEVSTACKS.DIM),
            (0, [130], False, DEVSTACKS.DIM),
            (1, [], True, DEVSTACKS.RED),
            (1, None, True, DEVSTACKS.RED),
            (1, [True, "1"], True, DEVSTACKS.RED),
        ):
            with self.subTest(exit_code=exit_code, success_codes=success_codes):
                process = {"status": "Completed", "exit_code": exit_code,
                           "success_exit_codes": success_codes}
                self.assertEqual(DEVSTACKS.is_broken(process), broken)
                self.assertEqual(DEVSTACKS.proc_colour(process), colour)

    def test_historical_exit_code_does_not_override_current_process_state(self):
        for status, ready, broken, colour in (
            ("Running", "Ready", False, DEVSTACKS.GREEN),
            ("Running", "Not Ready", False, DEVSTACKS.AMBER),
            ("Pending", "-", False, DEVSTACKS.DIM),
            ("Launching", "-", False, DEVSTACKS.DIM),
            ("Terminating", "-", False, DEVSTACKS.DIM),
            ("Disabled", "-", False, DEVSTACKS.DIM),
            ("Error", "-", True, DEVSTACKS.RED),
            ("Failed", "-", True, DEVSTACKS.RED),
            ("Restarting", "-", True, DEVSTACKS.RED),
        ):
            with self.subTest(status=status, ready=ready):
                process = {"status": status, "is_ready": ready, "exit_code": 1}
                self.assertEqual(DEVSTACKS.is_broken(process), broken)
                self.assertEqual(DEVSTACKS.proc_colour(process), colour)
        self.assertTrue(DEVSTACKS.is_broken({"status": "Skipped", "exit_code": 0}))

    def test_rest_failure_turns_menu_red_then_recovers_without_actions(self):
        projects = [{"name": "stack", "port": 8099}]
        for exit_code, icon, colour in (
            (1, "🔴", DEVSTACKS.RED),
            (0, "🟢", DEVSTACKS.DIM),
            (2, "🔴", DEVSTACKS.RED),
        ):
            with self.subTest(exit_code=exit_code):
                response = mock.MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps({"data": [
                    {"name": "migrate", "status": "Completed", "exit_code": exit_code,
                     "is_ready": "-", "has_ready_probe": False, "is_running": False},
                    {"name": "api", "status": "Running", "exit_code": 0,
                     "is_ready": "Ready", "has_ready_probe": True, "is_running": True},
                ]}).encode()
                output = io.StringIO()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(DEVSTACKS, "load_config", return_value=(
                        1, {"mise": {}, "docker": {}}, projects, None
                    )))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "resolve_mise", return_value=None))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "resolve_docker", return_value=None))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "_read_action_errors", return_value={}))
                    request = stack.enter_context(mock.patch.object(
                        DEVSTACKS.urllib.request, "urlopen", return_value=response
                    ))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "containers", return_value=[]))
                    run = stack.enter_context(mock.patch.object(DEVSTACKS.subprocess, "run"))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "ICON_STYLE", "emoji"))
                    stack.enter_context(contextlib.redirect_stdout(output))
                    DEVSTACKS.main([])
                lines = output.getvalue().splitlines()
                self.assertEqual(lines[0], icon)
                failed = next(line for line in lines if line.startswith("--● migrate"))
                self.assertIn("Completed", failed)
                self.assertIn("color=" + colour, failed)
                healthy = next(line for line in lines if line.startswith("--● api"))
                self.assertIn("color=" + DEVSTACKS.GREEN, healthy)
                self.assertTrue(any(line.startswith("Refresh |") for line in lines))
                self.assertTrue(any(line.startswith("Edit projects… |") for line in lines))
                request.assert_called_once_with("http://127.0.0.1:8099/processes", timeout=1.5)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
