#!/usr/bin/env python3

# <bitbar.title>Dev Stacks</bitbar.title>
# <bitbar.version>3.0</bitbar.version>
# <bitbar.author>Mathias Asberg</bitbar.author>
# <bitbar.author.github>Mindgames</bitbar.author.github>
# <bitbar.desc>Start, stop and monitor process-compose dev stacks and their containers.</bitbar.desc>
# <bitbar.dependencies>python3,mise,docker</bitbar.dependencies>
#
# Reads ~/.config/devstacks/projects.json. Each project's native processes come
# from its process-compose REST API; its containers are matched by Docker
# Compose project label. Adding a project is a config edit, not a code edit.
#
#   {"version": 2, "projects": [{"name": "myapp", "dir": "...",
#     "port": 8099, "toolchain": "mise",
#     "commands": {"up": ["make", "start"],
#                  "restart": ["make", "restart-stack"],
#                  "down": ["make", "stop"]}}]}

import base64
import fcntl
import json
import os
import re
import signal
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib

CONFIG = os.path.expanduser(
    os.environ.get("DEVSTACKS_CONFIG", "~/.config/devstacks/projects.json")
)
ACTION_ERRORS = os.path.expanduser(
    os.environ.get("DEVSTACKS_ACTION_ERRORS", "~/.config/devstacks/action-errors.json")
)
CONFIG_VERSION = 2
PLUGIN_PATH = os.path.realpath(__file__)
PYTHON_EXECUTABLE = os.path.realpath(sys.executable)
PROCESS_COMPOSE_ADDRESS = "127.0.0.1"
ACTION_TIMEOUT_SECONDS = 180
ACTION_TERMINATION_GRACE_SECONDS = 3
MISE_CANDIDATES = (
    os.path.expanduser("~/.local/bin/mise"),
)

GREEN = "#3fb950"
RED = "#f85149"
AMBER = "#d29922"
DIM = "#8b949e"

# How the menu bar icon is drawn:
#
#   "stack"  Apple's square.stack.3d.up.fill artwork, tinted and handed to
#            SwiftBar as a base64 PNG via image=. Keeps the stack shape *and*
#            the colour, because a supplied image is not a template image and
#            so is not repainted to match the menu bar.
#   "text"   a glyph tinted with color=. Plainer, but the same colour path.
#   "emoji"  a coloured dot; colour cannot be discarded, since the glyph
#            carries its own.
#   "symbol" an SF Symbol tinted with sfcolor. This is what the plugin shipped
#            with, and on macOS 26 with SwiftBar 2.0.1 the tint is discarded:
#            the symbol renders as a monochrome template image, identical in
#            every state. Kept only for builds where sfcolor is honoured.
ICON_STYLE = "stack"

STATE_COLOUR = {"problem": RED, "idle": DIM, "ok": GREEN}
MENU_GLYPH = "◼︎"
EMOJI_ICON = {"problem": "🔴", "idle": "⚪️", "ok": "🟢"}
SYMBOL_ICON = "square.stack.3d.up.fill"

# ---- the stack icon ---------------------------------------------------------
# Apple's own square.stack.3d.up.fill, captured once as an alpha mask and
# recoloured here for each state.
#
# Baking the colour into a PNG is the entire point. A supplied image is not a
# template image, so macOS leaves it alone; an sfimage is, so macOS repaints it
# to match the menu bar and the tint disappears.
#
# 32x32 pixels written at 144 DPI, which loads as a 16x16 *point* image: menu
# bar sized, and crisp on a retina display. The DPI is not decoration — at the
# default 72 the same pixels load as a 32 point image and tower over the bar.

ICON_PIXELS = 32
ICON_DPI = 144
ICON_MASK = (
    "eNpjYEAGzqWBLAw4gdWp////3w3CISu95D8E7NXFIstR/eU/DPyZIoQuHXjvPzJ4m82M"
    "LKuz9z86uOwElxWa8uc/FrBWESzLnP32P3bwvYWbgcHw4n/c4IkXw5f/+MBfp//4QeNd"
    "vNIftIQX/sMtvVsF6AGLUzhk7wZC/M+Y+ByL7JcqdngI8Xb9RJP9t1gKIuXNBqZUNqNI"
    "n7KEmOzG+/+WD0Sh+3W47PNERrCQybH/pUDudg0wj7XgA1j2ZxcvmC8+D+iztCKgzb/6"
    "+MEiotN//v+3ThXMZiv5CFQ7lZlBfTuQfpXCBFHhKAd11i2g6DkbMNvnNpB91gYpPahv"
    "A+lJY4Jy2co+AfnLZKBc/t5f////niAA5siCSYkFQLd8reUAMpmSXwIV79QEi0sx/V+r"
    "AGaZnQCK3g9htD0LpO/4QRLW5D+pwGTSzA0Oi/hnQJlvQPy5AhyyzFlv/v8vWwpKJlGQ"
    "UO4EhfK/RZCQdbwE5BzlYwh9CKSPGIPFlCYfXQhhKawBir4vAKVyzgagoX/niCH5j6vp"
    "O1BoliiUK7cSqPhjEStMOvIxkH/IAEmD3XmgyA1PMNvoMJD9KBw1fzGlvwaKblFjEJv9"
    "F+iLBk6YBAAUyhJ9"
)

_ICON_CACHE = {}


def _png_chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def stack_icon(hex_colour):
    """The stack symbol as a base64 PNG in `hex_colour`, for SwiftBar's image=."""
    if hex_colour not in _ICON_CACHE:
        mask = zlib.decompress(base64.b64decode(ICON_MASK))
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
        pixel = bytes((r, g, b))

        raw = bytearray()
        for row in range(ICON_PIXELS):
            raw.append(0)                                  # PNG filter type 0
            for alpha in mask[row * ICON_PIXELS:(row + 1) * ICON_PIXELS]:
                raw += pixel + bytes((alpha,))

        per_metre = round(ICON_DPI / 0.0254)
        png = (b"\x89PNG\r\n\x1a\n"
               + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", ICON_PIXELS,
                                                 ICON_PIXELS, 8, 6, 0, 0, 0))
               + _png_chunk(b"pHYs", struct.pack(">IIB", per_metre, per_metre, 1))
               + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
               + _png_chunk(b"IEND", b""))
        _ICON_CACHE[hex_colour] = base64.b64encode(png).decode()
    return _ICON_CACHE[hex_colour]


FONT = "font=Menlo size=12"

PROC_GLYPH = "●"
CONTAINER_GLYPH = "▣"

# Container port -> (label, is a browsable web UI). Keyed on the *container*
# port because that is stable regardless of how it is published on the host.
#
# "Browsable" means the root path actually renders something. Loki is listed as
# false deliberately: it serves an API but 404s at /, so an "Open Loki" item
# would hand you a broken page. Grafana is how you read Loki.
KNOWN_PORTS = {
    3000: ("Grafana", True),
    3100: ("Loki API", False),
    12345: ("Alloy", True),
    9090: ("Prometheus", True),
    16686: ("Jaeger", True),
    8080: ("HTTP", True),
    4317: ("OTLP gRPC", False),
    4318: ("OTLP HTTP", False),
    5432: ("Postgres", False),
    6379: ("Redis", False),
}


def resolve_docker(settings=None):
    """Resolve Docker independently without adding a package-manager PATH."""
    configured = settings.get("bin") if isinstance(settings, dict) else None
    if configured is not None and not isinstance(configured, str):
        return None
    if configured:
        configured = os.path.expanduser(configured)
        if not os.path.isabs(configured):
            return None
    candidates = (
        (configured,)
        if configured
        else (
            os.environ.get("DEVSTACKS_DOCKER_BIN"),
            shutil.which("docker"),
            "/Applications/Docker.app/Contents/Resources/bin/docker",
        )
    )
    for candidate in candidates:
        candidate = os.path.expanduser(candidate) if candidate else None
        if (candidate and os.path.isabs(candidate) and os.path.isfile(candidate)
                and os.access(candidate, os.X_OK)):
            return os.path.realpath(candidate)
    return None


def api(port, path, timeout=1.5):
    try:
        with urllib.request.urlopen(
            f"http://{PROCESS_COMPOSE_ADDRESS}:{port}{path}", timeout=timeout
        ) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def processes(port):
    payload = api(port, "/processes")
    if payload is None:
        return None
    rows = payload.get("data", payload) if isinstance(payload, dict) else payload
    return rows if isinstance(rows, list) else None


def shell_action(command, terminal=False):
    """Render an independent Docker action with an already-resolved binary."""
    return (
        f"shell=/bin/bash param0=-lc param1={shlex.quote(command)} "
        f"terminal={'true' if terminal else 'false'} refresh=true"
    )


def load_config(path=None):
    """Load the versioned config while recognizing the bounded v1 transition."""
    path = path or CONFIG
    default_settings = {"mise": {}, "docker": {}}
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        return 0, default_settings, [], f"Cannot read {path}: {exc.__class__.__name__}"

    if isinstance(payload, list):
        return 1, default_settings, payload, (
            "Configuration v1 is status-only; migrate to version 2"
        )
    if not isinstance(payload, dict):
        return 0, default_settings, [], "Configuration root must be an object"

    version = payload.get("version")
    projects = payload.get("projects")
    mise_settings = payload.get("mise")
    docker_settings = payload.get("docker")
    if mise_settings is None:
        mise_settings = {}
    if docker_settings is None:
        docker_settings = {}
    settings = {"mise": mise_settings, "docker": docker_settings}
    if version != CONFIG_VERSION:
        return version or 0, settings, projects if isinstance(projects, list) else [], (
            f"Unsupported configuration version: {version!r}; expected {CONFIG_VERSION}"
        )
    if not isinstance(projects, list):
        return version, settings, [], "Configuration projects must be a list"
    if not isinstance(mise_settings, dict):
        return version, settings, projects, "Configuration mise settings must be an object"
    if not isinstance(docker_settings, dict):
        return version, settings, projects, "Configuration Docker settings must be an object"
    return version, settings, projects, None


def resolve_mise(settings):
    configured = settings.get("bin") if isinstance(settings, dict) else None
    if configured is not None and not isinstance(configured, str):
        return None
    candidates = (os.path.expanduser(configured),) if configured else MISE_CANDIDATES
    for candidate in candidates:
        if (candidate and os.path.isabs(candidate) and os.path.isfile(candidate)
                and os.access(candidate, os.X_OK)):
            return os.path.realpath(candidate)
    return None


def _valid_command(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(part, str) and part and "\x00" not in part for part in value))


def project_static_error(project, version, mise_bin):
    if version != CONFIG_VERSION:
        return f"configuration version {CONFIG_VERSION} is required for controls"
    if not isinstance(project, dict):
        return "project entry must be an object"
    name = project.get("name")
    directory = project.get("dir")
    port = project.get("port")
    if not isinstance(name, str) or not name.strip():
        return "project name is missing"
    if not isinstance(directory, str) or not os.path.isabs(directory):
        return "project directory must be absolute"
    if not os.path.isdir(directory):
        return "project directory is missing"
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        return "Process Compose port is invalid"
    if project.get("toolchain") != "mise":
        return "project is status-only until toolchain is set to mise"
    if project.get("path"):
        return "remove the obsolete per-project path override"
    if not mise_bin:
        return "mise executable is missing"
    if not os.path.isfile(os.path.join(directory, "mise.toml")):
        return "mise.toml is missing"
    if not os.path.isfile(os.path.join(directory, "mise.lock")):
        return "mise.lock is missing"
    commands = project.get("commands")
    if not isinstance(commands, dict):
        return "lifecycle commands are missing"
    for name in ("up", "restart", "down"):
        if not _valid_command(commands.get(name)):
            return f"lifecycle command {name} must be a non-empty argument list"
    return None


def menu_text(value):
    """Keep configuration and subprocess text inside one SwiftBar label."""
    return re.sub(r"[|\r\n]+", " ", str(value)).strip()


def swiftbar_action(project_name, operation, *arguments, terminal=False):
    params = [PLUGIN_PATH, "--run-action", project_name, operation, *arguments]
    rendered = " ".join(
        f"param{index}={shlex.quote(value)}" for index, value in enumerate(params)
    )
    return (
        f"shell={shlex.quote(PYTHON_EXECUTABLE)} {rendered} "
        f"terminal={'true' if terminal else 'false'} refresh=true"
    )


def process_compose_command(project, operation, *arguments):
    return [
        "process-compose",
        "--address", PROCESS_COMPOSE_ADDRESS,
        "--port", str(project["port"]),
        *operation.split(),
        *arguments,
    ]


def mise_exec_command(mise_bin, project, command):
    return [mise_bin, "exec", "--locked", "-C", project["dir"], "--", *command]


def _read_action_errors(path=None):
    path = path or ACTION_ERRORS
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_action_errors(errors, path=None):
    path = path or ACTION_ERRORS
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".action-errors.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(errors, handle, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _update_action_errors(project_name, entry, path=None):
    path = path or ACTION_ERRORS
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    lock_path = f"{path}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as lock_handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        errors = _read_action_errors(path)
        if entry is None:
            if project_name not in errors:
                return
            del errors[project_name]
        else:
            errors[project_name] = entry
        _write_action_errors(errors, path)


def _record_action_error(project_name, message):
    try:
        _update_action_errors(
            project_name,
            {
                "message": re.sub(r"[\r\n]+", " ", str(message))[:240],
                "recorded_at": int(time.time()),
            },
        )
    except OSError as exc:
        print(
            f"{project_name}: cannot persist action error: {exc.__class__.__name__}",
            file=sys.stderr,
        )


def _clear_action_error(project_name):
    try:
        _update_action_errors(project_name, None)
    except OSError as exc:
        print(
            f"{project_name}: cannot clear action error: {exc.__class__.__name__}",
            file=sys.stderr,
        )


def _mise_environment(project):
    environment = os.environ.copy()
    for name in (
        "MISE_CONFIG_FILE",
        "MISE_CONFIG_ROOT",
        "MISE_DEFAULT_CONFIG_FILENAME",
        "MISE_ENV",
        "MISE_ENV_FILE",
        "MISE_GLOBAL_CONFIG_FILE",
        "MISE_IGNORED_CONFIG_PATHS",
        "MISE_PROJECT_ROOT",
        "MISE_SYSTEM_CONFIG_FILE",
        "MISE_TRUSTED_CONFIG_PATHS",
    ):
        environment.pop(name, None)
    for name in tuple(environment):
        if name.startswith("__MISE_") or re.fullmatch(r"MISE_[A-Z0-9_]+_VERSION", name):
            environment.pop(name, None)

    repository = os.path.realpath(project["dir"])
    isolation_root = os.path.join(
        os.path.dirname(os.path.realpath(ACTION_ERRORS)),
        ".devstacks-mise-isolation",
    )
    user_home = os.path.expanduser("~")
    cache_directory = (
        os.path.join(user_home, "Library", "Caches", "mise")
        if sys.platform == "darwin"
        else os.path.join(user_home, ".cache", "mise")
    )
    environment["MISE_LOCKED"] = "1"
    environment["MISE_DATA_DIR"] = os.path.join(user_home, ".local", "share", "mise")
    environment["MISE_CACHE_DIR"] = cache_directory
    environment["MISE_STATE_DIR"] = os.path.join(user_home, ".local", "state", "mise")
    environment["MISE_CONFIG_DIR"] = os.path.join(isolation_root, "config")
    environment["MISE_SYSTEM_CONFIG_DIR"] = os.path.join(isolation_root, "system")
    environment["MISE_CEILING_PATHS"] = os.path.dirname(repository)
    environment["MISE_OVERRIDE_CONFIG_FILENAMES"] = "mise.toml"
    environment["MISE_OVERRIDE_TOOL_VERSIONS_FILENAMES"] = ".devstacks-disabled"
    environment["MISE_IDIOMATIC_VERSION_FILE"] = "0"
    environment["MISE_LEGACY_VERSION_FILE"] = "0"
    environment["MISE_AUTO_INSTALL"] = "0"
    environment["MISE_EXEC_AUTO_INSTALL"] = "0"
    environment["MISE_NOT_FOUND_AUTO_INSTALL"] = "0"
    return environment


def _mise_error_detail(stderr, fallback):
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    trust_error = next(
        (line for line in lines if "not trusted" in line.lower()),
        None,
    )
    if trust_error:
        return trust_error[:180]
    useful = [
        line for line in lines
        if not line.startswith("mise ERROR Version:")
        and "Run with --verbose" not in line
    ]
    return useful[-1][:180] if useful else fallback


def _mise_preflight(mise_bin, project):
    environment = _mise_environment(project)
    expected_manifest = os.path.realpath(os.path.join(project["dir"], "mise.toml"))
    try:
        config_result = subprocess.run(
            [mise_bin, "config", "ls", "--json", "-C", project["dir"]],
            capture_output=True, text=True, timeout=8, env=environment,
        )
        if config_result.returncode != 0:
            return None, _mise_error_detail(
                config_result.stderr,
                "mise config isolation failed",
            )
        config_payload = json.loads(config_result.stdout)
        if not isinstance(config_payload, list) or not config_payload:
            return None, "mise config isolation did not return a configuration list"
        if any(
            not isinstance(item, dict) or not isinstance(item.get("path"), str)
            for item in config_payload
        ):
            return None, "mise config isolation returned an invalid configuration entry"
        config_paths = {os.path.realpath(item["path"]) for item in config_payload}
        if config_paths != {expected_manifest}:
            return None, "mise config isolation did not select only the repository manifest"
        result = subprocess.run(
            [mise_bin, "which", "--locked", "-C", project["dir"], "process-compose"],
            capture_output=True, text=True, timeout=8, env=environment,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return None, f"mise preflight failed: {exc.__class__.__name__}"
    executable = result.stdout.strip()
    if result.returncode != 0 or not os.path.isabs(executable) or not os.access(executable, os.X_OK):
        return None, _mise_error_detail(
            result.stderr,
            "locked process-compose is unavailable",
        )
    return executable, None


def _process_group_exists(process_group):
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_group(process, grace_seconds):
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass

    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(process_group) and time.monotonic() < deadline:
        process.poll()
        time.sleep(0.05)

    if _process_group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=max(grace_seconds, 1))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_bounded_action(
    invocation,
    environment,
    timeout=ACTION_TIMEOUT_SECONDS,
    termination_grace=ACTION_TERMINATION_GRACE_SECONDS,
):
    process = subprocess.Popen(
        invocation,
        env=environment,
        start_new_session=True,
    )
    try:
        return process.wait(timeout=timeout), None
    except subprocess.TimeoutExpired:
        _terminate_process_group(process, termination_grace)
        return None, f"action timed out after {timeout:g} seconds"


def run_action(project_name, operation, arguments=(), config_path=None):
    version, settings, projects, config_error = load_config(config_path)
    matches = [
        item for item in projects
        if isinstance(item, dict) and item.get("name") == project_name
    ]
    project = matches[0] if len(matches) == 1 else None
    mise_bin = resolve_mise(settings["mise"])
    duplicate_error = "project name is duplicated" if len(matches) > 1 else None
    error = config_error or duplicate_error or ("project is not configured" if project is None else None)
    if error is None:
        error = project_static_error(project, version, mise_bin)
    if error is None:
        _, error = _mise_preflight(mise_bin, project)
    if error:
        _record_action_error(project_name, error)
        print(f"{project_name}: {error}", file=sys.stderr)
        return 2

    commands = project["commands"]
    if operation in ("up", "restart", "down"):
        command = commands[operation]
        interactive = False
    elif operation in ("process-start", "process-stop", "process-restart") and len(arguments) == 1:
        command = process_compose_command(
            project,
            f"process {operation[len('process-'):]}",
            "--",
            arguments[0],
        )
        interactive = False
    elif operation == "logs" and len(arguments) == 1:
        command = process_compose_command(
            project,
            "process logs",
            "--follow",
            "--tail",
            "200",
            "--",
            arguments[0],
        )
        interactive = True
    elif operation == "tui" and not arguments:
        command = process_compose_command(project, "attach")
        interactive = True
    else:
        error = "unsupported or malformed action"
        _record_action_error(project_name, error)
        print(f"{project_name}: {error}", file=sys.stderr)
        return 2

    invocation = mise_exec_command(mise_bin, project, command)
    environment = _mise_environment(project)
    if interactive:
        try:
            result = subprocess.run(invocation, env=environment)
        except KeyboardInterrupt:
            message = "terminal action interrupted"
            _record_action_error(project_name, message)
            print(f"{project_name}: {message}", file=sys.stderr)
            return 130
        except OSError as exc:
            message = f"terminal action failed: {exc.__class__.__name__}"
            _record_action_error(project_name, message)
            print(f"{project_name}: {message}", file=sys.stderr)
            return 1
        if result.returncode != 0:
            _record_action_error(
                project_name, f"terminal action exited with status {result.returncode}"
            )
        else:
            _clear_action_error(project_name)
        return result.returncode
    try:
        returncode, timeout_error = _run_bounded_action(invocation, environment)
    except OSError as exc:
        _record_action_error(project_name, f"action failed: {exc.__class__.__name__}")
        return 1
    if timeout_error:
        _record_action_error(project_name, timeout_error)
        return 1
    if returncode != 0:
        _record_action_error(project_name, f"action exited with status {returncode}")
    else:
        _clear_action_error(project_name)
    return returncode


# A process with no readiness probe reports "-", which means "not measured",
# not "unhealthy". Only an explicit non-ready verdict counts against it.
NO_PROBE = ("", "-", "ready")


def is_healthy(proc):
    status = (proc.get("status") or "").lower()
    ready = (proc.get("is_ready") or "").lower()
    return status == "running" and ready in NO_PROBE


# Statuses that mean something actually went wrong, as opposed to "not there
# yet". "Skipped" belongs here: process-compose skips a process whose
# dependency never became healthy, so it is the visible half of a failure
# upstream.
BROKEN_STATUS = ("error", "failed", "restarting", "skipped")


def is_broken(proc):
    """Has this process failed, as distinct from merely not being ready yet?

    A process that is running but still working through its readiness probe is
    not broken — it is starting. Treating it as broken paints the menu bar red
    for the first ten or fifteen seconds of every healthy launch, which teaches
    you to ignore the colour exactly when it matters.
    """
    return (proc.get("status") or "").lower() in BROKEN_STATUS


def proc_colour(proc):
    status = (proc.get("status") or "").lower()
    if is_healthy(proc):
        return GREEN
    if status == "running":
        return AMBER
    if status in ("restarting", "error", "failed"):
        return RED
    return DIM


def containers(docker_bin):
    """Running containers as dicts. Docker Desktop can hang while the VM starts
    or stops, so this is bounded — a wedged daemon must not freeze the menu bar."""
    fmt = '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}|{{.Label "com.docker.compose.project"}}'
    if not docker_bin:
        return None
    try:
        out = subprocess.run([docker_bin, "ps", "--format", fmt],
                             capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None

    rows = []
    for line in out.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 5:
            continue
        rows.append({
            "name": parts[0], "image": parts[1], "status": parts[2],
            "ports": parse_ports(parts[3]), "compose": parts[4],
        })
    return rows


def parse_ports(spec):
    """Published (host_port, container_port) pairs, deduped.

    Docker prints both IPv4 and IPv6 bindings for the same publish, and may
    collapse consecutive ports into a range (4317-4318->4317-4318/tcp).
    """
    pairs = {}
    for match in re.finditer(r"(?:0\.0\.0\.0|\[::\]):([\d-]+)->([\d-]+)/tcp", spec):
        for host, container in zip(expand(match.group(1)), expand(match.group(2))):
            pairs[host] = container
    return sorted(pairs.items())


def expand(value):
    if "-" in value:
        start, end = value.split("-", 1)
        try:
            return list(range(int(start), int(end) + 1))
        except ValueError:
            return []
    try:
        return [int(value)]
    except ValueError:
        return []


def owns(project, container):
    """Does this project own this container?

    Compose project label first, since that is what Compose actually stamps.
    Falls back to a name prefix so a container started outside the configured
    Compose projects still lands somewhere sensible.
    """
    configured = project.get("compose") or []
    compose = container["compose"]
    if compose and compose in configured:
        return True
    if compose and compose.startswith(project["name"]):
        return True
    if not compose and container["name"].startswith(project["name"]):
        return True
    return False


def render_container(container, depth, docker_bin):
    """Render one container and its actions, nested at `depth` dashes."""
    d = "-" * depth
    status = container["status"]
    colour = GREEN if status.startswith("Up") else AMBER
    if "unhealthy" in status:
        colour = RED

    print(f"{d}{CONTAINER_GLYPH} {container['name']}  {status} | {FONT} color={colour}")
    print(f"{d}--{container['image']} | {FONT} color={DIM}")

    ports = container["ports"]
    for host, cport in [(h, c) for h, c in ports if KNOWN_PORTS.get(c, ("", False))[1]]:
        url = f"http://localhost:{host}"
        print(f"{d}--Open {KNOWN_PORTS[cport][0]}  —  {url} | href={url}")

    other = [(h, c) for h, c in ports if not KNOWN_PORTS.get(c, ("", False))[1]]
    if other:
        shown = ", ".join(
            f"{h}→{c}" + (f" {KNOWN_PORTS[c][0]}" if c in KNOWN_PORTS else "")
            for h, c in other
        )
        print(f"{d}--Ports: {shown} | {FONT} color={DIM}")

    name = shlex.quote(container["name"])
    if not docker_bin:
        return
    docker = shlex.quote(docker_bin)
    print(f"{d}--Logs (Terminal) | {shell_action(f'{docker} logs -f --tail 200 {name}', terminal=True)}")
    print(f"{d}--Restart | {shell_action(f'{docker} restart {name}')}")
    print(f"{d}--Stop | {shell_action(f'{docker} stop {name}')} color={RED}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        if len(argv) >= 3 and argv[0] == "--run-action":
            raise SystemExit(run_action(argv[1], argv[2], tuple(argv[3:])))
        print("Unsupported plugin invocation", file=sys.stderr)
        raise SystemExit(2)

    version, settings, configured_projects, config_error = load_config()
    projects = [
        project for project in configured_projects
        if isinstance(project, dict)
        and isinstance(project.get("name"), str)
        and isinstance(project.get("port"), int)
        and not isinstance(project.get("port"), bool)
        and 1 <= project["port"] <= 65535
    ]
    if len(projects) != len(configured_projects):
        config_error = config_error or "Each project needs a name and a valid REST port"
    names = [project["name"] for project in projects]
    if len(names) != len(set(names)):
        config_error = config_error or "Project names must be unique"
    mise_bin = resolve_mise(settings["mise"])
    docker_bin = resolve_docker(settings["docker"])
    action_errors = _read_action_errors()

    states = [(project, processes(project.get("port"))) for project in projects]
    docker_rows = containers(docker_bin)

    # Assign each container to at most one project; whatever is left over is
    # genuinely unrelated to a configured stack.
    owned = {}
    unclaimed = []
    for container in (docker_rows or []):
        for project in projects:
            if owns(project, container):
                owned.setdefault(project["name"], []).append(container)
                break
        else:
            unclaimed.append(container)

    # ---- menu bar title -----------------------------------------------------
    running = [(p, r) for p, r in states if r is not None]
    total_procs = sum(len(r) for _, r in running)
    healthy_procs = sum(1 for _, r in running for p in r if is_healthy(p))

    # A "problem" is something that has actually failed: a process in a broken
    # status, or a container whose own healthcheck says unhealthy. Neither a
    # stopped stack nor a process still warming up counts — the first was never
    # asked to run, and the second has not failed at anything yet.
    sick_containers = sum(1 for c in (docker_rows or []) if "unhealthy" in c["status"])
    broken_procs = sum(1 for _, r in running for p in r if is_broken(p))
    problem = broken_procs > 0 or sick_containers > 0

    # Red: something is wrong. Grey: no stack is up. Green: a stack is up and
    # well. Checked in that order, so a real fault always outranks idleness.
    #
    # "Idle" is judged on stacks alone, never on containers. Leftover
    # containers from a previous session — an observability stack that came
    # back with Docker on login, say — must not paint the icon green while
    # every actual stack is down: that reads as "all is well" when nothing is
    # running at all.
    #
    # Colour carries the state; the shape changes only for idle, so the icon
    # stays easy to find in a crowded menu bar.
    #
    # Both names below are verified to resolve via NSImage(systemSymbolName:).
    # Do not swap in a variant without checking it the same way — the obvious
    # choice for the degraded state, square.stack.3d.up.trianglebadge
    # .exclamationfill, does not exist, and an unresolved name renders as a
    # blank menu bar item: invisible exactly when something is wrong.
    state = "problem" if problem else ("idle" if not running else "ok")
    if ICON_STYLE == "stack":
        print(f"| image={stack_icon(STATE_COLOUR[state])}")
    elif ICON_STYLE == "symbol":
        print(f"| sfimage={SYMBOL_ICON} sfcolor={STATE_COLOUR[state]}")
    elif ICON_STYLE == "emoji":
        print(EMOJI_ICON[state])
    else:
        print(f"{MENU_GLYPH} | color={STATE_COLOUR[state]} size=14")

    print("---")

    bits = []
    if running:
        bits.append(f"{len(running)} {'stack' if len(running) == 1 else 'stacks'}")
        bits.append(f"{healthy_procs}/{total_procs} processes healthy")
    if docker_rows:
        bits.append(f"{len(docker_rows)} {'container' if len(docker_rows) == 1 else 'containers'}")
    print(f"{' · '.join(bits) if bits else 'Nothing running'} | {FONT} "
          f"color={RED if problem else (GREEN if running else DIM)}")
    if config_error:
        print(f"Configuration: {menu_text(config_error)} | {FONT} color={RED}")
    print("---")

    # ---- one section per project --------------------------------------------
    for project, rows in states:
        name = project["name"]
        display_name = menu_text(name)
        port = project["port"]
        directory = project.get("dir")
        mine = owned.get(name, [])
        control_error = config_error or project_static_error(project, version, mise_bin)
        last_error = action_errors.get(name)

        if rows is None:
            suffix = f" · {len(mine)} containers" if mine else ""
            print(f"{display_name} — stopped{suffix} | {FONT} color={DIM}")
            if control_error:
                print(f"--Controls disabled: {menu_text(control_error)} | {FONT} color={RED}")
            else:
                print(f"--Start stack | {swiftbar_action(name, 'up')} color={GREEN}")
            if isinstance(last_error, dict) and last_error.get("message"):
                print(f"--Last action error: {menu_text(last_error['message'])} | {FONT} color={RED}")
            if directory:
                print(f"--Open folder | shell=/usr/bin/open param0={shlex.quote(directory)} terminal=false")
            for container in sorted(mine, key=lambda c: c["name"]):
                render_container(container, 2, docker_bin)
            print("---")
            continue

        healthy = sum(1 for p in rows if is_healthy(p))
        headline = f"{display_name} — {healthy}/{len(rows)} healthy"
        if mine:
            headline += f" · {len(mine)} {'container' if len(mine) == 1 else 'containers'}"
        print(f"{headline} | {FONT}")

        for proc in sorted(rows, key=lambda p: p.get("name", "")):
            pname = proc.get("name", "?")
            status = proc.get("status", "?")
            ready = proc.get("is_ready") or "-"
            restarts = proc.get("restarts", 0)

            label = f"{PROC_GLYPH} {pname:<16} {status}"
            if ready not in ("-", ""):
                label += f"/{ready}"
            if restarts:
                label += f"  ↻{restarts}"
            print(f"--{label} | {FONT} color={proc_colour(proc)}")

            if not control_error:
                print(f"----Restart | {swiftbar_action(name, 'process-restart', pname)}")
                if status.lower() == "running":
                    print(f"----Stop | {swiftbar_action(name, 'process-stop', pname)} color={RED}")
                else:
                    print(f"----Start | {swiftbar_action(name, 'process-start', pname)} color={GREEN}")
                print(f"----Logs (Terminal) | "
                      f"{swiftbar_action(name, 'logs', pname, terminal=True)}")

        # Containers belonging to this project, alongside its processes.
        for container in sorted(mine, key=lambda c: c["name"]):
            render_container(container, 2, docker_bin)

        links = project.get("links") or ([{"label": "Open", "url": project["open"]}]
                                         if project.get("open") else [])
        if links:
            print(f"--Open | {FONT}")
            for link in links:
                print(f"----{link['label']}  —  {link['url']} | href={link['url']}")

        if control_error:
            print(f"--Controls disabled: {menu_text(control_error)} | {FONT} color={RED}")
        else:
            print(f"--Dashboard (TUI) | {swiftbar_action(name, 'tui', terminal=True)}")
            print(f"--Restart stack | {swiftbar_action(name, 'restart')}")
            print(f"--Stop stack | {swiftbar_action(name, 'down')} color={RED}")
        if isinstance(last_error, dict) and last_error.get("message"):
            print(f"--Last action error: {menu_text(last_error['message'])} | {FONT} color={RED}")
        print("---")

    # ---- anything not belonging to a configured project ---------------------
    if docker_rows is None:
        print(f"Docker unavailable | {FONT} color={DIM}")
    elif unclaimed:
        print(f"Other containers — {len(unclaimed)} | {FONT} color={DIM}")
        for container in sorted(unclaimed, key=lambda c: c["name"]):
            render_container(container, 2, docker_bin)

    print("---")
    print(f"Refresh | refresh=true color={DIM}")
    print(f"Edit projects… | shell=/usr/bin/open param0=-t param1={shlex.quote(CONFIG)} terminal=false")


if __name__ == "__main__":
    main()
