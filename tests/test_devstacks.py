import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("devstacks_plugin", ROOT / "devstacks.5s.py")
DEVSTACKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEVSTACKS)


class DevStacksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dev stacks ; ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository space ; $(not-a-command)"
        self.repository.mkdir()
        (self.repository / "mise.toml").write_text(
            '[tools]\n"aqua:F1bonacc1/process-compose" = "1.122.0"\n',
            encoding="utf-8",
        )
        (self.repository / "mise.lock").write_text(
            '[tools."aqua:F1bonacc1/process-compose"."1.122.0"]\n',
            encoding="utf-8",
        )

        self.log = self.root / "mise calls.jsonl"
        self.tool = self.root / "locked process-compose"
        self.tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.tool.chmod(0o755)
        self.mise = self.root / "mise stub"
        self.mise.write_text(
            """#!/bin/sh
printf 'CALL\\n' >> "$MISE_STUB_LOG"
for value in "$@"; do printf '%s\\n' "$value" >> "$MISE_STUB_LOG"; done
if [ "${1:-}" = "which" ]; then
  status="${MISE_STUB_WHICH_STATUS:-0}"
  if [ "$status" != "0" ]; then
    printf '%s\\n' "${MISE_STUB_ERROR:-locked tool is unavailable}" >&2
    exit "$status"
  fi
  printf '%s\\n' "$MISE_STUB_TOOL"
  exit 0
fi
if [ "${1:-}" = "config" ]; then
  printf '%s\\n' "$MISE_STUB_CONFIG_JSON"
  exit "${MISE_STUB_CONFIG_STATUS:-0}"
fi
exit "${MISE_STUB_EXEC_STATUS:-0}"
""",
            encoding="utf-8",
        )
        self.mise.chmod(0o755)

        self.project = {
            "name": "project ; $name",
            "dir": str(self.repository),
            "port": 8099,
            "toolchain": "mise",
            "commands": {
                "up": ["repository-lifecycle", "up", "argument with space"],
                "restart": ["repository-lifecycle", "restart", "--safe"],
                "down": ["repository-lifecycle", "down"],
            },
            "compose": ["project-compose"],
        }
        self.config = self.root / "projects.json"
        self.errors = self.root / "action-errors.json"
        self.write_config(self.project)

        self.original_config = DEVSTACKS.CONFIG
        self.original_errors = DEVSTACKS.ACTION_ERRORS
        DEVSTACKS.CONFIG = str(self.config)
        DEVSTACKS.ACTION_ERRORS = str(self.errors)
        self.addCleanup(setattr, DEVSTACKS, "CONFIG", self.original_config)
        self.addCleanup(setattr, DEVSTACKS, "ACTION_ERRORS", self.original_errors)

        self.environment = mock.patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin:/bin",
                "MISE_STUB_LOG": str(self.log),
                "MISE_STUB_CONFIG_JSON": json.dumps(
                    [{"path": str(self.repository / "mise.toml"), "tools": ["process-compose"]}]
                ),
                "MISE_STUB_TOOL": str(self.tool),
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write_config(self, project, mise=None):
        payload = {
            "version": 2,
            "mise": mise if mise is not None else {"bin": str(self.mise)},
            "projects": [project],
        }
        self.config.write_text(json.dumps(payload), encoding="utf-8")

    def calls(self):
        if not self.log.exists():
            return []
        chunks = self.log.read_text(encoding="utf-8").split("CALL\n")[1:]
        return [chunk.rstrip("\n").splitlines() for chunk in chunks]

    def invoke(self, operation, arguments=(), interactive=False):
        self.log.unlink(missing_ok=True)
        result = DEVSTACKS.run_action(
            self.project["name"], operation, arguments, str(self.config)
        )
        self.assertEqual(result, 0)
        calls = self.calls()
        self.assertEqual(
            calls[0],
            ["config", "ls", "--json", "-C", str(self.repository)],
        )
        self.assertEqual(
            calls[1],
            [
                "which", "--locked", "-C", str(self.repository),
                "process-compose",
            ],
        )
        self.assertEqual(calls[2][:5], ["exec", "--locked", "-C", str(self.repository), "--"])
        return calls[2][5:]

    def test_every_supported_action_uses_locked_repository_mise_context(self):
        process_name = "-worker ; $(touch never) 'quoted'"
        lifecycle = self.project["commands"]
        process_prefix = [
            "process-compose", "--address", "127.0.0.1", "--port", "8099",
        ]
        cases = [
            ("up", (), lifecycle["up"], False),
            ("restart", (), lifecycle["restart"], False),
            ("down", (), lifecycle["down"], False),
            ("process-start", (process_name,), process_prefix + ["process", "start", "--", process_name], False),
            ("process-stop", (process_name,), process_prefix + ["process", "stop", "--", process_name], False),
            ("process-restart", (process_name,), process_prefix + ["process", "restart", "--", process_name], False),
            ("logs", (process_name,), process_prefix + ["process", "logs", "--follow", "--tail", "200", "--", process_name], True),
            ("tui", (), process_prefix + ["attach"], True),
        ]
        for operation, arguments, expected, interactive in cases:
            with self.subTest(operation=operation):
                self.assertEqual(self.invoke(operation, arguments, interactive), expected)

    def test_swiftbar_parameters_preserve_shell_significant_values_as_arguments(self):
        process_name = "-worker ; $(touch never) 'quoted'"
        rendered = DEVSTACKS.swiftbar_action(
            self.project["name"], "process-restart", process_name, terminal=True
        )
        fields = dict(token.split("=", 1) for token in shlex.split(rendered))
        self.assertEqual(fields["shell"], "/usr/bin/python3")
        self.assertEqual(fields["param0"], str(ROOT / "devstacks.5s.py"))
        self.assertEqual(fields["param2"], self.project["name"])
        self.assertEqual(fields["param3"], "process-restart")
        self.assertEqual(fields["param4"], process_name)
        self.assertEqual(fields["terminal"], "true")

    def test_refresh_keeps_rest_status_when_mise_is_missing_without_running_preflight(self):
        self.write_config(self.project, {"bin": str(self.root / "missing mise")})
        process = {
            "name": "api",
            "status": "Running",
            "is_ready": "Ready",
            "restarts": 0,
        }
        output = io.StringIO()
        with (
            mock.patch.object(DEVSTACKS, "processes", return_value=[process]),
            mock.patch.object(DEVSTACKS, "containers", return_value=[]),
            mock.patch.object(DEVSTACKS, "_mise_preflight", side_effect=AssertionError),
            contextlib.redirect_stdout(output),
        ):
            DEVSTACKS.main([])
        rendered = output.getvalue()
        self.assertIn("1/1 processes healthy", rendered)
        self.assertIn("Controls disabled: mise executable is missing", rendered)
        self.assertNotIn("Start stack | shell=", rendered)

    def test_fail_closed_conditions_are_actionable(self):
        cases = []

        missing_directory = dict(self.project, dir=str(self.root / "missing repository"))
        cases.append((missing_directory, str(self.mise), "project directory is missing"))

        missing_config = dict(self.project)
        missing_config_dir = self.root / "missing config"
        missing_config_dir.mkdir()
        (missing_config_dir / "mise.lock").write_text("lock", encoding="utf-8")
        missing_config["dir"] = str(missing_config_dir)
        cases.append((missing_config, str(self.mise), "mise.toml is missing"))

        missing_lock = dict(self.project)
        missing_lock_dir = self.root / "missing lock"
        missing_lock_dir.mkdir()
        (missing_lock_dir / "mise.toml").write_text("[tools]\n", encoding="utf-8")
        missing_lock["dir"] = str(missing_lock_dir)
        cases.append((missing_lock, str(self.mise), "mise.lock is missing"))

        legacy_path = dict(self.project, path=["/runtime/override"])
        cases.append((legacy_path, str(self.mise), "remove the obsolete per-project path override"))

        for project, mise, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(DEVSTACKS.project_static_error(project, 2, mise), expected)

        self.assertEqual(
            DEVSTACKS.project_static_error(self.project, 2, None),
            "mise executable is missing",
        )
        self.assertEqual(
            DEVSTACKS.project_static_error({"name": "legacy"}, 2, str(self.mise)),
            "project directory must be absolute",
        )

        status_only = dict(self.project)
        status_only.pop("toolchain")
        self.assertIn(
            "status-only",
            DEVSTACKS.project_static_error(status_only, 2, str(self.mise)),
        )

    def test_unavailable_locked_tool_records_visible_bounded_error(self):
        with mock.patch.dict(
            os.environ,
            {"MISE_STUB_WHICH_STATUS": "42", "MISE_STUB_ERROR": "stale lock: process-compose unavailable"},
            clear=False,
        ):
            result = DEVSTACKS.run_action(self.project["name"], "up", config_path=str(self.config))
        self.assertEqual(result, 2)
        payload = json.loads(self.errors.read_text(encoding="utf-8"))
        self.assertIn("stale lock", payload[self.project["name"]]["message"])
        self.assertEqual(stat.S_IMODE(self.errors.stat().st_mode), 0o600)

        output = io.StringIO()
        with (
            mock.patch.object(DEVSTACKS, "processes", return_value=None),
            mock.patch.object(DEVSTACKS, "containers", return_value=[]),
            contextlib.redirect_stdout(output),
        ):
            DEVSTACKS.main([])
        self.assertIn("Last action error: stale lock: process-compose unavailable", output.getvalue())

    def test_mise_environment_restricts_config_to_the_repository_manifest(self):
        poisoned = {
            "MISE_CONFIG_FILE": str(self.root / "other.toml"),
            "MISE_ENV": "production",
            "MISE_IGNORED_CONFIG_PATHS": str(self.repository / "mise.toml"),
            "MISE_GLOBAL_CONFIG_FILE": str(self.root / "global.toml"),
            "MISE_SYSTEM_CONFIG_FILE": str(self.root / "system.toml"),
            "__MISE_SESSION": "ambient",
        }
        with mock.patch.dict(os.environ, poisoned, clear=False):
            environment = DEVSTACKS._mise_environment(self.project)

        manifest = str((self.repository / "mise.toml").resolve())
        self.assertEqual(environment["MISE_GLOBAL_CONFIG_FILE"], manifest)
        self.assertEqual(environment["MISE_SYSTEM_CONFIG_FILE"], manifest)
        self.assertEqual(environment["MISE_CEILING_PATHS"], str(self.repository.resolve()))
        self.assertEqual(environment["MISE_OVERRIDE_CONFIG_FILENAMES"], "mise.toml")
        self.assertEqual(
            environment["MISE_OVERRIDE_TOOL_VERSIONS_FILENAMES"],
            ".devstacks-disabled",
        )
        self.assertEqual(environment["MISE_LOCKED"], "1")
        self.assertEqual(environment["MISE_EXEC_AUTO_INSTALL"], "0")
        for name in poisoned:
            if name not in ("MISE_GLOBAL_CONFIG_FILE", "MISE_SYSTEM_CONFIG_FILE"):
                self.assertNotIn(name, environment)

    def test_mise_preflight_rejects_any_additional_config(self):
        leaked = json.dumps(
            [
                {"path": str(self.repository / "mise.toml"), "tools": ["process-compose"]},
                {"path": str(self.root / "ambient.toml"), "tools": ["node"]},
            ]
        )
        with mock.patch.dict(os.environ, {"MISE_STUB_CONFIG_JSON": leaked}, clear=False):
            result = DEVSTACKS.run_action(
                self.project["name"], "up", config_path=str(self.config)
            )
        self.assertEqual(result, 2)
        payload = json.loads(self.errors.read_text(encoding="utf-8"))
        self.assertIn("only the repository manifest", payload[self.project["name"]]["message"])
        self.assertEqual(len(self.calls()), 1)

    def test_version_one_is_status_only(self):
        self.config.write_text(json.dumps([self.project]), encoding="utf-8")
        version, settings, projects, error = DEVSTACKS.load_config(str(self.config))
        self.assertEqual(version, 1)
        self.assertEqual(settings, {"mise": {}, "docker": {}})
        self.assertEqual(projects, [self.project])
        self.assertIn("status-only", error)

    def test_unreadable_config_still_renders_a_recoverable_menu(self):
        missing = self.root / "missing-projects.json"
        version, settings, projects, error = DEVSTACKS.load_config(str(missing))
        self.assertEqual(version, 0)
        self.assertEqual(settings, {"mise": {}, "docker": {}})
        self.assertEqual(projects, [])
        self.assertIn("Cannot read", error)

        output = io.StringIO()
        with (
            mock.patch.object(DEVSTACKS, "CONFIG", str(missing)),
            mock.patch.object(DEVSTACKS, "containers", return_value=None),
            contextlib.redirect_stdout(output),
        ):
            DEVSTACKS.main([])
        rendered = output.getvalue()
        self.assertIn("Configuration: Cannot read", rendered)
        self.assertIn("Edit projects", rendered)

    def test_falsy_non_object_tool_settings_are_rejected(self):
        for key in ("mise", "docker"):
            for invalid in ([], "", False, 0):
                with self.subTest(key=key, invalid=invalid):
                    payload = {
                        "version": 2,
                        "mise": {"bin": str(self.mise)},
                        "docker": {},
                        "projects": [self.project],
                    }
                    payload[key] = invalid
                    self.config.write_text(json.dumps(payload), encoding="utf-8")
                    _, _, _, error = DEVSTACKS.load_config(str(self.config))
                    self.assertEqual(
                        error,
                        f"Configuration {'Docker' if key == 'docker' else 'mise'} settings must be an object",
                    )

    def test_docker_actions_are_independent_of_mise(self):
        output = io.StringIO()
        container = {
            "name": "container ; name",
            "image": "example:latest",
            "status": "Up 2 minutes",
            "ports": [],
            "compose": "project-compose",
        }
        with contextlib.redirect_stdout(output):
            DEVSTACKS.render_container(container, 2, str(self.tool))
        rendered = output.getvalue().splitlines()
        actions = {}
        for line in rendered:
            if " | shell=" not in line:
                continue
            label, fields_text = line.split(" | ", 1)
            fields = dict(token.split("=", 1) for token in shlex.split(fields_text))
            actions[label.lstrip("-")] = shlex.split(fields["param1"])
        self.assertEqual(
            actions["Logs (Terminal)"],
            [str(self.tool), "logs", "-f", "--tail", "200", container["name"]],
        )
        self.assertEqual(actions["Restart"], [str(self.tool), "restart", container["name"]])
        self.assertEqual(actions["Stop"], [str(self.tool), "stop", container["name"]])
        self.assertNotIn("mise", output.getvalue())

    def test_docker_resolution_is_explicit_and_does_not_add_package_manager_paths(self):
        self.assertEqual(
            DEVSTACKS.resolve_docker({"bin": str(self.tool)}),
            str(self.tool.resolve()),
        )
        self.assertIsNone(DEVSTACKS.resolve_docker({"bin": "relative/docker"}))
        self.assertIsNone(DEVSTACKS.resolve_docker({"bin": 42}))

    def test_failed_explicit_docker_resolution_never_falls_back(self):
        with mock.patch.object(DEVSTACKS, "resolve_docker", side_effect=AssertionError):
            self.assertIsNone(DEVSTACKS.containers(None))

    def test_interactive_exit_failure_is_persisted_for_the_menu(self):
        with mock.patch.dict(os.environ, {"MISE_STUB_EXEC_STATUS": "23"}, clear=False):
            result = DEVSTACKS.run_action(
                self.project["name"], "logs", ("api",), str(self.config)
            )
        self.assertEqual(result, 23)
        payload = json.loads(self.errors.read_text(encoding="utf-8"))
        self.assertIn("terminal action exited with status 23", payload[self.project["name"]]["message"])

    def test_interactive_interrupt_is_persisted_without_a_traceback(self):
        config_result = mock.Mock(
            returncode=0,
            stdout=json.dumps([{"path": str(self.repository / "mise.toml")}]),
            stderr="",
        )
        which_result = mock.Mock(returncode=0, stdout=str(self.tool), stderr="")
        with mock.patch.object(
            DEVSTACKS.subprocess,
            "run",
            side_effect=[config_result, which_result, KeyboardInterrupt],
        ):
            result = DEVSTACKS.run_action(
                self.project["name"], "tui", config_path=str(self.config)
            )
        self.assertEqual(result, 130)
        payload = json.loads(self.errors.read_text(encoding="utf-8"))
        self.assertEqual(
            payload[self.project["name"]]["message"],
            "terminal action interrupted",
        )

    def test_action_error_updates_are_serialized_across_processes(self):
        source = """
import importlib.util
import sys
import time

spec = importlib.util.spec_from_file_location("devstacks_child", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.ACTION_ERRORS = sys.argv[2]
original_read = module._read_action_errors
def slow_read(path=None):
    payload = original_read(path)
    time.sleep(0.1)
    return payload
module._read_action_errors = slow_read
module._record_action_error(sys.argv[3], "concurrent failure")
"""
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", source, str(ROOT / "devstacks.5s.py"), str(self.errors), f"project-{index}"]
            )
            for index in range(4)
        ]
        self.assertTrue(all(process.wait(timeout=10) == 0 for process in processes))
        payload = json.loads(self.errors.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {f"project-{index}" for index in range(4)})
        self.assertEqual(stat.S_IMODE(Path(f"{self.errors}.lock").stat().st_mode), 0o600)

    def test_process_status_and_controls_use_the_same_ipv4_loopback(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"data": []}'
        with mock.patch.object(DEVSTACKS.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertEqual(DEVSTACKS.processes(self.project["port"]), [])
        urlopen.assert_called_once_with(
            f"http://127.0.0.1:{self.project['port']}/processes",
            timeout=1.5,
        )
        self.assertIn("127.0.0.1", DEVSTACKS.process_compose_command(self.project, "attach"))

    def test_action_status_survives_an_unwritable_error_store(self):
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"MISE_STUB_EXEC_STATUS": "23"}, clear=False),
            mock.patch.object(
                DEVSTACKS,
                "_write_action_errors",
                side_effect=OSError("disk full"),
            ),
            contextlib.redirect_stderr(stderr),
        ):
            result = DEVSTACKS.run_action(
                self.project["name"], "up", config_path=str(self.config)
            )
        self.assertEqual(result, 23)
        self.assertIn("cannot persist action error: OSError", stderr.getvalue())

    def test_plugin_has_no_package_manager_path_fallback(self):
        source = (ROOT / "devstacks.5s.py").read_text(encoding="utf-8")
        ast.parse(source, filename="devstacks.5s.py", feature_version=(3, 8))
        self.assertNotIn("/opt/homebrew", source)
        self.assertNotIn("corepack", source)
        self.assertNotIn("PNPM_HOME", source)
        self.assertNotIn(".removeprefix(", source)
        self.assertNotIn("execvpe", source)

    def test_example_uses_only_version_two_mise_contract(self):
        payload = json.loads((ROOT / "projects.example.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["docker"], {})
        for project in payload["projects"]:
            self.assertEqual(project["toolchain"], "mise")
            self.assertNotIn("path", project)
            self.assertEqual(set(project["commands"]), {"up", "restart", "down"})
            self.assertTrue(all(isinstance(value, list) for value in project["commands"].values()))


if __name__ == "__main__":
    unittest.main()
