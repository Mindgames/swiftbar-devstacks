"""Process failure colours must agree with the menu-bar status."""

import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "devstacks_process_colours", ROOT / "devstacks.5s.py"
)
DEVSTACKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEVSTACKS)


class ProcessColourTests(unittest.TestCase):
    def test_process_colours_distinguish_failures_readiness_and_idle_states(self):
        cases = [
            ("Error", "-", DEVSTACKS.RED),
            ("Failed", "-", DEVSTACKS.RED),
            ("Restarting", "-", DEVSTACKS.RED),
            ("Skipped", "-", DEVSTACKS.RED),
            ("sKiPpEd", "Ready", DEVSTACKS.RED),
            ("Running", "Ready", DEVSTACKS.GREEN),
            ("Running", "-", DEVSTACKS.GREEN),
            ("Running", "", DEVSTACKS.GREEN),
            ("Running", "Not Ready", DEVSTACKS.AMBER),
            ("Pending", "-", DEVSTACKS.DIM),
            ("Completed", "-", DEVSTACKS.DIM),
            ("", "-", DEVSTACKS.DIM),
        ]
        for status, ready, expected in cases:
            with self.subTest(status=status, ready=ready):
                self.assertEqual(
                    DEVSTACKS.proc_colour({"status": status, "is_ready": ready}),
                    expected,
                )

    def test_skipped_process_row_matches_problem_icon_and_recovers_next_refresh(self):
        projects = [
            {"name": "affected", "port": 8099},
            {"name": "healthy", "port": 8100},
        ]
        for status, expected_colour, expected_icon in (
            ("Skipped", DEVSTACKS.RED, "🔴"),
            ("Running", DEVSTACKS.GREEN, "🟢"),
            ("Skipped", DEVSTACKS.RED, "🔴"),
        ):
            with self.subTest(status=status):
                rows = [
                    [{"name": "worker", "status": status, "is_ready": "Ready"}],
                    [{"name": "api", "status": "Running", "is_ready": "Ready"}],
                ]
                output = io.StringIO()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(
                        DEVSTACKS, "load_config", return_value=(
                            1, {"mise": {}, "docker": {}}, projects, None
                        )
                    ))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "resolve_mise", return_value=None))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "resolve_docker", return_value=None))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "_read_action_errors", return_value={}))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "processes", side_effect=rows))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "containers", return_value=[]))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "ICON_STYLE", "emoji"))
                    stack.enter_context(contextlib.redirect_stdout(output))
                    DEVSTACKS.main([])
                lines = output.getvalue().splitlines()
                self.assertEqual(lines[0], expected_icon)
                worker = next(line for line in lines if line.startswith("--● worker"))
                self.assertIn(status, worker)
                self.assertIn("color=" + expected_colour, worker)
                healthy = next(line for line in lines if line.startswith("--● api"))
                self.assertIn("color=" + DEVSTACKS.GREEN, healthy)
                self.assertTrue(any(line.startswith("Refresh |") for line in lines))
                self.assertTrue(any(line.startswith("Edit projects… |") for line in lines))


if __name__ == "__main__":
    unittest.main()
