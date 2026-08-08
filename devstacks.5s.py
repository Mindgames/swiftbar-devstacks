#!/usr/bin/env python3

# <bitbar.title>Dev Stacks</bitbar.title>
# <bitbar.version>1.9</bitbar.version>
# <bitbar.author>Mathias Asberg</bitbar.author>
# <bitbar.author.github>Mindgames</bitbar.author.github>
# <bitbar.desc>Start, stop and monitor process-compose dev stacks and their containers.</bitbar.desc>
# <bitbar.dependencies>python3,process-compose,docker</bitbar.dependencies>
#
# Reads ~/.config/devstacks/projects.json. Each project's native processes come
# from its process-compose REST API; its containers are matched by Docker
# Compose project label. Adding a project is a config edit, not a code edit.
#
#   [{"name": "lookprep", "dir": "...", "port": 8099,
#     "up": "make native-up",
#     "compose": ["lookprep", "lookprep-native-observability"],
#     "links": [{"label": "App", "url": "http://localhost:3001"}]}]

import base64
import json
import os
import re
import shlex
import struct
import subprocess
import urllib.error
import urllib.request
import zlib

CONFIG = os.path.expanduser("~/.config/devstacks/projects.json")

# SwiftBar runs plugins with a minimal PATH: no Homebrew, and none of the
# per-user bin directories where tools like uv and pipx install themselves.
# A tool missing from here does not fail visibly — the process it belongs to
# dies with exit 127 and restart-loops — so this list is deliberately generous,
# and a project can add its own directories via the "path" config key.
BIN_PATH = ":".join([
    os.path.expanduser("~/.local/bin"),
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
])

GREEN = "#3fb950"
RED = "#f85149"
AMBER = "#d29922"
DIM = "#8b949e"

# How the menu bar icon is drawn:
#
#   "stack"  the layered stack drawn below, tinted and handed to SwiftBar as a
#            base64 PNG via image=. Keeps the stack shape *and* the colour,
#            because a supplied image is not a template image and so is not
#            repainted to match the menu bar.
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
# Drawn rather than shipped as an asset, so the plugin stays a single file with
# no binary blobs, and a colour change is a hex edit rather than a redraw.

ICON_PIXELS = 36
ICON_SUPERSAMPLE = 4

# Three layers in a 36x36 box: a wide base with two narrower slabs above it,
# which is what reads as a stack seen from slightly above.
ICON_LAYERS = [
    (12.0, 4.0, 24.0, 8.0, 1.8),
    (8.0, 11.0, 28.0, 16.0, 2.4),
    (4.0, 19.0, 32.0, 32.0, 4.0),
]

_ICON_MASK = None
_ICON_CACHE = {}


def _in_round_rect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _icon_mask():
    """Alpha coverage for the stack, supersampled so the corners are smooth.

    Computed once and reused: the shape never changes, only its colour does.
    """
    global _ICON_MASK
    if _ICON_MASK is None:
        step = 1.0 / ICON_SUPERSAMPLE
        samples = ICON_SUPERSAMPLE * ICON_SUPERSAMPLE
        _ICON_MASK = [
            [
                sum(
                    1
                    for sy in range(ICON_SUPERSAMPLE)
                    for sx in range(ICON_SUPERSAMPLE)
                    if any(_in_round_rect(px + (sx + 0.5) * step,
                                          py + (sy + 0.5) * step, *layer)
                           for layer in ICON_LAYERS)
                ) * 255 // samples
                for px in range(ICON_PIXELS)
            ]
            for py in range(ICON_PIXELS)
        ]
    return _ICON_MASK


def _png_chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def stack_icon(hex_colour):
    """The stack as a base64 PNG in `hex_colour`, ready for SwiftBar's image=."""
    if hex_colour not in _ICON_CACHE:
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
        raw = bytearray()
        for row in _icon_mask():
            raw.append(0)                                  # PNG filter type 0
            for alpha in row:
                raw += bytes((r, g, b, alpha))
        png = (b"\x89PNG\r\n\x1a\n"
               + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", ICON_PIXELS,
                                                 ICON_PIXELS, 8, 6, 0, 0, 0))
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


def _docker_binary():
    """Docker moves with the runtime — Docker Desktop installs to /usr/local/bin,
    OrbStack and Colima to Homebrew. Resolve rather than hardcode, so switching
    runtimes does not silently empty the container listing."""
    for candidate in ("/usr/local/bin/docker", "/opt/homebrew/bin/docker", "/usr/bin/docker"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "docker"


DOCKER = _docker_binary()


def api(port, path, timeout=1.5):
    try:
        with urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def processes(port):
    payload = api(port, "/processes")
    if payload is None:
        return None
    rows = payload.get("data", payload) if isinstance(payload, dict) else payload
    return rows if isinstance(rows, list) else None


def action(command, cwd=None, terminal=False, extra_path=()):
    """Render SwiftBar params that run `command` through a login shell.

    PATH is *prepended*, never replaced. Replacing it silently drops whatever
    the login shell set up — version-manager shims especially — and the damage
    only shows up later as a child process exiting 127.
    """
    search = ":".join([*extra_path, BIN_PATH])
    full = f'export PATH={shlex.quote(search)}:"$PATH"; '
    if cwd:
        full += f"cd {shlex.quote(cwd)} && "
    full += command
    return (
        f"shell=/bin/bash param0=-lc param1={shlex.quote(full)} "
        f"terminal={'true' if terminal else 'false'} refresh=true"
    )


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


def containers():
    """Running containers as dicts. Docker Desktop can hang while the VM starts
    or stops, so this is bounded — a wedged daemon must not freeze the menu bar."""
    fmt = '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}|{{.Label "com.docker.compose.project"}}'
    try:
        out = subprocess.run([DOCKER, "ps", "--format", fmt],
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


def render_container(container, depth):
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
    print(f"{d}--Logs (Terminal) | {action(f'docker logs -f --tail 200 {name}', terminal=True)}")
    print(f"{d}--Restart | {action(f'docker restart {name}')}")
    print(f"{d}--Stop | {action(f'docker stop {name}')} color={RED}")


def main():
    try:
        with open(CONFIG) as handle:
            projects = json.load(handle)
    except (OSError, ValueError):
        print("dev ⚠")
        print("---")
        print(f"Cannot read {CONFIG} | color={RED}")
        return

    states = [(project, processes(project.get("port"))) for project in projects]
    docker_rows = containers()

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
    print("---")

    # ---- one section per project --------------------------------------------
    for project, rows in states:
        name = project["name"]
        port = project["port"]
        directory = project.get("dir")
        up_command = project.get("up", "make native-up")
        mine = owned.get(name, [])
        extra = [os.path.expanduser(p) for p in (project.get("path") or [])]

        if rows is None:
            suffix = f" · {len(mine)} containers" if mine else ""
            print(f"{name} — stopped{suffix} | {FONT} color={DIM}")
            print(f"--Start stack | {action(up_command, directory, extra_path=extra)} color={GREEN}")
            if directory:
                print(f"--Open folder | shell=/usr/bin/open param0={shlex.quote(directory)} terminal=false")
            for container in sorted(mine, key=lambda c: c["name"]):
                render_container(container, 2)
            print("---")
            continue

        healthy = sum(1 for p in rows if is_healthy(p))
        headline = f"{name} — {healthy}/{len(rows)} healthy"
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

            pc = f"PC_PORT_NUM={port} process-compose process"
            print(f"----Restart | {action(f'{pc} restart {shlex.quote(pname)}', extra_path=extra)}")
            if status.lower() == "running":
                print(f"----Stop | {action(f'{pc} stop {shlex.quote(pname)}', extra_path=extra)} color={RED}")
            else:
                print(f"----Start | {action(f'{pc} start {shlex.quote(pname)}', extra_path=extra)} color={GREEN}")
            print(f"----Logs (Terminal) | "
                  f"{action(f'{pc} logs {shlex.quote(pname)} -f -n 200', terminal=True, extra_path=extra)}")

        # Containers belonging to this project, alongside its processes.
        for container in sorted(mine, key=lambda c: c["name"]):
            render_container(container, 2)

        links = project.get("links") or ([{"label": "Open", "url": project["open"]}]
                                         if project.get("open") else [])
        if links:
            print(f"--Open | {FONT}")
            for link in links:
                print(f"----{link['label']}  —  {link['url']} | href={link['url']}")

        print(f"--Dashboard (TUI) | "
              f"{action(f'PC_PORT_NUM={port} process-compose attach', terminal=True, extra_path=extra)}")
        print(f"--Restart stack | "
              f"{action(f'PC_PORT_NUM={port} process-compose down && {up_command}', directory, extra_path=extra)}")
        print(f"--Stop stack | "
              f"{action(f'PC_PORT_NUM={port} process-compose down', extra_path=extra)} color={RED}")
        print("---")

    # ---- anything not belonging to a configured project ---------------------
    if docker_rows is None:
        print(f"Docker unavailable | {FONT} color={DIM}")
    elif unclaimed:
        print(f"Other containers — {len(unclaimed)} | {FONT} color={DIM}")
        for container in sorted(unclaimed, key=lambda c: c["name"]):
            render_container(container, 2)

    print("---")
    print(f"Refresh | refresh=true color={DIM}")
    print(f"Edit projects… | shell=/usr/bin/open param0=-t param1={shlex.quote(CONFIG)} terminal=false")


if __name__ == "__main__":
    main()
