"""Malformed optional Compose metadata must not interrupt status refreshes."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("devstacks_compose_config", ROOT / "devstacks.5s.py")
DEVSTACKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEVSTACKS)


class ComposeConfigTests(unittest.TestCase):
    def container(self, compose="healthy", name="healthy-1"):
        return {"name": name, "image": "example:latest", "status": "Up 1 hour",
                "ports": [], "compose": compose}

    def test_non_list_compose_values_do_not_claim_labels(self):
        for invalid in (42, True, 1.5, "healthy-extra", {"healthy": True}):
            with self.subTest(compose=invalid):
                project = {"name": "other", "compose": invalid}
                self.assertFalse(DEVSTACKS.owns(project, self.container()))
                self.assertIsNone(DEVSTACKS.container_owner([project], self.container()))

    def test_invalid_compose_metadata_preserves_prefix_fallback(self):
        for invalid in (42, True, 1.5, "unrelated", {"unrelated": True}):
            with self.subTest(compose=invalid):
                project = {"name": "healthy", "compose": invalid}
                for container in (self.container(), self.container(compose="")):
                    self.assertTrue(DEVSTACKS.owns(project, container))
                    self.assertIs(DEVSTACKS.container_owner([project], container), project)

    def test_supported_and_missing_compose_metadata_keep_existing_ownership(self):
        container = self.container()
        for value in ([], None, False, 0, "", {}, [None, 42, {}, ["healthy"]]):
            with self.subTest(compose=value):
                project = {"name": "other", "compose": value}
                self.assertFalse(DEVSTACKS.owns(project, container))
                self.assertIsNone(DEVSTACKS.container_owner([project], container))
        self.assertIsNone(DEVSTACKS.container_owner([{"name": "other"}], container))
        explicit = {"name": "owner", "compose": ["healthy"]}
        prefix = {"name": "healthy", "compose": []}
        duplicate = {"name": "duplicate", "compose": ["healthy"]}
        self.assertIs(DEVSTACKS.container_owner([prefix, explicit, duplicate], container), explicit)
        mixed = {"name": "mixed", "compose": [None, 42, {}, ["healthy"], "healthy"]}
        self.assertTrue(DEVSTACKS.owns(mixed, container))
        self.assertIs(DEVSTACKS.container_owner([prefix, mixed], container), mixed)

    def test_invalid_compose_metadata_keeps_healthy_peer_and_recovery_menu(self):
        for invalid in (42, True, 1.5, "healthy-extra", {"healthy": True}):
            with self.subTest(compose=invalid), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "projects.json"
                config.write_text(json.dumps({"version": 2, "projects": [
                    {"name": "other", "port": 8099, "compose": invalid},
                    {"name": "healthy", "port": 8100, "compose": ["healthy"]},
                ]}), encoding="utf-8")
                output = io.StringIO()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(DEVSTACKS, "CONFIG", str(config)))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "_read_action_errors", return_value={}))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "resolve_mise", return_value=None))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "resolve_docker", return_value="/mock/docker"))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "processes", side_effect=[None, [
                        {"name": "api", "status": "Running", "is_ready": "Ready"}
                    ]]))
                    stack.enter_context(mock.patch.object(DEVSTACKS, "containers", return_value=[self.container()]))
                    run = stack.enter_context(mock.patch.object(DEVSTACKS.subprocess, "run"))
                    preflight = stack.enter_context(mock.patch.object(DEVSTACKS, "_mise_preflight"))
                    stack.enter_context(contextlib.redirect_stdout(output))
                    DEVSTACKS.main([])
                rendered = output.getvalue()
                self.assertIn("other — stopped |", rendered)
                self.assertIn("healthy — 1/1 healthy · 1 container", rendered)
                self.assertEqual(rendered.count("--▣ healthy-1"), 1)
                self.assertIn("Refresh |", rendered)
                self.assertIn("Edit projects", rendered)
                run.assert_not_called()
                preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
