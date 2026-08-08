# Dev Stacks — a SwiftBar plugin

Start, stop and monitor [process-compose](https://github.com/F1bonacc1/process-compose)
dev stacks and their Docker containers from the macOS menu bar.

One menu item per project. Each project's native processes come from its
process-compose REST API; its containers are matched by Docker Compose project
label. Adding a project is a config edit, not a code edit.

The menu bar icon carries the state at a glance:

| Icon | Meaning |
| --- | --- |
| green | a stack is up and nothing in it has failed |
| red | a process has failed, or a container's healthcheck is failing |
| grey | no stack is running |

Checked in that order, so a real fault always outranks idleness.

Red means *failed*, not *not ready yet*. A process still working through its
readiness probe stays green, because a stack with a ten-second probe delay
would otherwise flash red on every healthy launch — and a colour that cries
wolf is a colour you stop reading. The statuses that count as failure are
`Error`, `Failed`, `Restarting` and `Skipped`.

Idle is judged on stacks alone, never on containers. Leftover containers — an
observability stack that came back with Docker on login, say — leave the icon
grey rather than green, because "all is well" would be a lie when no stack is
actually running. They are still listed in the menu.

Per process you get restart / start / stop and streaming logs in a Terminal
window. Per container: logs, restart, stop, and one-click links to any port the
plugin recognises as a browsable web UI (Grafana, Prometheus, Jaeger, Alloy).
Containers that don't belong to a configured project are grouped under
"Other containers" rather than hidden.

## Requirements

- macOS with [SwiftBar](https://github.com/swiftbar/SwiftBar) (`brew install --cask swiftbar`)
- `python3` (system Python is fine — no third-party packages)
- `process-compose` on your PATH, for the stack controls
- `docker` — optional; container sections are skipped when the daemon is unreachable

## Install

```bash
git clone https://github.com/Mindgames/swiftbar-devstacks.git
cd swiftbar-devstacks
./install.sh
```

`install.sh` symlinks the plugin into your SwiftBar plugin folder, so
`git pull` updates it in place. It also seeds `~/.config/devstacks/projects.json`
from the example on first run.

Then edit your projects:

```bash
open -t ~/.config/devstacks/projects.json
```

(or use the "Edit projects…" item at the bottom of the menu).

## Configuration

`~/.config/devstacks/projects.json` is a list of project objects. This file is
personal — it holds your local paths — and is deliberately kept outside the repo.

| Key | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Display name, and the prefix used to claim unlabeled containers |
| `port` | yes | The project's process-compose REST port (`PC_PORT_NUM`) |
| `dir` | no | Working directory for the start/restart commands, and for "Open folder" |
| `up` | no | Command that brings the stack up (default `make native-up`) |
| `compose` | no | Docker Compose project labels belonging to this project |
| `path` | no | Extra `PATH` directories for this project's commands, `~` allowed |
| `links` | no | `{label, url}` items listed under the "Open" submenu |

### About `path`

SwiftBar hands plugins a minimal `PATH` — no Homebrew, and none of the per-user
bin directories. The plugin prepends a generous default set (including
`~/.local/bin`, where `uv` and `pipx` install) and keeps whatever the login
shell already had.

That is not always enough. If your shell pins a specific toolchain — a
Homebrew versioned formula like `node@22`, a `PNPM_HOME`, a version-manager
shim directory — the menu bar will not see it, and your stack will start with
different tool versions than your terminal gives it. List those directories in
`path` so the two environments match.

The symptom of a missing directory is a process that dies immediately with
**exit code 127** and restart-loops, while everything that depends on it is
reported as `Skipped`.

See [`projects.example.json`](projects.example.json).

## Menu bar icon style

`ICON_STYLE` near the top of the plugin picks how the icon is drawn:

| Value | Looks like | Colour reliability |
| --- | --- | --- |
| `text` (default) | a small square, tinted with `color=` | high — the same parameter every row in the menu uses |
| `emoji` | a coloured dot | total — the glyph carries its own colour |
| `symbol` | an SF Symbol tinted with `sfcolor` | varies by SwiftBar and macOS version |

`symbol` is the nicest looking, but `sfcolor` is not honoured on every
SwiftBar/macOS pairing. Where it is dropped the symbol falls back to a
monochrome template image and *every state renders as the same grey shape* —
which looks exactly like a plugin that has stopped updating. If your icon is
grey no matter what the stacks are doing, that is the cause; switch to `text`
or `emoji`.

## Refresh interval

The `5s` in `devstacks.5s.py` is SwiftBar's refresh interval. To poll less
often, rename the file (and the symlink) to e.g. `devstacks.30s.py`.

## Notes

- SwiftBar runs plugins with a minimal PATH, so the plugin exports a Homebrew-aware
  PATH into every action it fires.
- The Docker binary is resolved at runtime rather than hardcoded, so switching
  between Docker Desktop, OrbStack and Colima doesn't silently empty the
  container list.
- `docker ps` is called with a timeout — a wedged Docker daemon must not freeze
  the menu bar.

## License

MIT — see [LICENSE](LICENSE).
