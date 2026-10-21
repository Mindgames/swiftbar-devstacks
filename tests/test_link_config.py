"""Optional menu links must not interrupt status or recovery controls."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("devstacks_link_config", ROOT / "devstacks.5s.py")
DEVSTACKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEVSTACKS)


class LinkConfigTests(unittest.TestCase):
    def render(self, metadata):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "projects.json"
            config.write_text(json.dumps({"version": 2, "projects": [
                dict({"name": "first", "port": 8099}, **metadata),
                {"name": "healthy-peer", "port": 8100},
            ]}), encoding="utf-8")
            output = io.StringIO()
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(DEVSTACKS, "CONFIG", str(config)))
                stack.enter_context(mock.patch.object(DEVSTACKS, "_read_action_errors", return_value={}))
                stack.enter_context(mock.patch.object(DEVSTACKS, "resolve_mise", return_value=None))
                stack.enter_context(mock.patch.object(DEVSTACKS, "resolve_docker", return_value=None))
                stack.enter_context(mock.patch.object(DEVSTACKS, "processes", return_value=[
                    {"name": "api", "status": "Running", "is_ready": "Ready"}
                ]))
                stack.enter_context(mock.patch.object(DEVSTACKS, "containers", return_value=[]))
                run = stack.enter_context(mock.patch.object(DEVSTACKS.subprocess, "run"))
                preflight = stack.enter_context(mock.patch.object(DEVSTACKS, "_mise_preflight"))
                stack.enter_context(contextlib.redirect_stdout(output))
                try:
                    DEVSTACKS.main([])
                except (TypeError, KeyError) as exc:
                    self.fail(f"Optional links interrupted the menu: {exc!r}")
            rendered = output.getvalue()
            self.assertIn("first — 1/1 healthy", rendered)
            self.assertIn("healthy-peer — 1/1 healthy", rendered)
            self.assertIn("Refresh |", rendered)
            self.assertIn("Edit projects", rendered)
            run.assert_not_called()
            preflight.assert_not_called()
            return rendered

    def test_non_list_links_preserve_healthy_peer_and_recovery_controls(self):
        for links in (42, True, 1.5, "http://localhost:3001", {
            "label": "App", "url": "http://localhost:3001"
        }):
            with self.subTest(links=links):
                rendered = self.render({"links": links})
                self.assertNotIn("\n--Open |", rendered)

    def test_invalid_link_entries_are_skipped(self):
        for link in (None, True, 42, 1.5, [], "http://localhost:3001", {},
                     {"label": "App"}, {"url": "http://localhost:3001"},
                     {"label": None, "url": "http://localhost:3001"},
                     {"label": 42, "url": "http://localhost:3001"},
                     {"label": "App", "url": None},
                     {"label": "App", "url": 42}):
            with self.subTest(link=link):
                rendered = self.render({"links": [link]})
                self.assertNotIn("\n--Open |", rendered)

    def test_mixed_links_keep_valid_entries_in_order(self):
        rendered = self.render({"links": [
            {"label": "App", "url": "http://localhost:3001"},
            None, {"label": "Missing URL"}, 42,
            {"label": "Metrics", "url": "http://localhost:9090", "extra": True},
        ]})
        first = "----App  —  http://localhost:3001 | href=http://localhost:3001"
        second = "----Metrics  —  http://localhost:9090 | href=http://localhost:9090"
        self.assertEqual(rendered.count(first), 1)
        self.assertEqual(rendered.count(second), 1)
        self.assertLess(rendered.index(first), rendered.index(second))
        self.assertEqual(rendered.count("\n--Open |"), 1)
        self.assertNotIn("Missing URL", rendered)

    def test_valid_links_preserve_rendering_and_override_legacy_open(self):
        for label, url in (("App", "http://localhost:3001"),
                           ("Docs", "https://example.com/help?q=local#setup"),
                           ("", "")):
            with self.subTest(label=label, url=url):
                rendered = self.render({"links": [{"label": label, "url": url}],
                                        "open": "http://legacy.invalid"})
                self.assertIn(f"----{label}  —  {url} | href={url}", rendered)
                self.assertNotIn("legacy.invalid", rendered)

    def test_absent_and_empty_links_remain_optional(self):
        for metadata in ({}, {"links": None}, {"links": []}, {"links": ""},
                         {"links": False}, {"links": 0}, {"links": {}}):
            with self.subTest(metadata=metadata):
                self.assertNotIn("\n--Open |", self.render(metadata))

    def test_legacy_open_fallback_is_preserved_for_absent_or_falsy_links(self):
        for metadata in ({}, {"links": None}, {"links": []}, {"links": ""},
                         {"links": False}, {"links": 0}, {"links": {}}):
            with self.subTest(metadata=metadata):
                rendered = self.render(dict(metadata, open="http://localhost:3001"))
                self.assertIn("----Open  —  http://localhost:3001 | href=http://localhost:3001",
                              rendered)

    def test_non_string_legacy_open_is_skipped(self):
        for url in (42, True, 1.5, ["http://localhost:3001"], {"url": "http://localhost:3001"}):
            with self.subTest(url=url):
                self.assertNotIn("\n--Open |", self.render({"open": url}))

    def test_invalid_explicit_links_do_not_select_a_legacy_fallback(self):
        for links in (42, {"label": "App"}, [{"label": "Missing URL"}]):
            with self.subTest(links=links):
                rendered = self.render({"links": links, "open": "http://legacy.invalid"})
                self.assertNotIn("\n--Open |", rendered)
                self.assertNotIn("legacy.invalid", rendered)


if __name__ == "__main__":
    unittest.main()
