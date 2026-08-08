# Dev Stacks — a SwiftBar plugin

Start, stop and monitor [process-compose](https://github.com/F1bonacc1/process-compose)
dev stacks and their Docker containers from the macOS menu bar.

One menu item per project. Each project's native processes come from its
process-compose REST API; its containers are matched by Docker Compose project
label. Adding a project is a config edit, not a code edit.

The menu bar icon carries the state at a glance:

| Icon | Meaning |
| --- | --- |
| green stack | everything that is up is healthy |
| red stack | a running process is unhealthy, or a container's healthcheck is failing |
| grey slashed stack | nothing running |

A stopped stack is not a problem — it stays green, because you never asked it to
run. Red means something that *is* running has gone wrong.

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
| `links` | no | `{label, url}` items listed under the "Open" submenu |

See [`projects.example.json`](projects.example.json).

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
