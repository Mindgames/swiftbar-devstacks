# Dev Stacks — a SwiftBar plugin

Start, stop, and monitor [Process Compose](https://github.com/F1bonacc1/process-compose)
development stacks and their Docker containers from the macOS menu bar.

Project controls run through the toolchain pinned by each repository. The
plugin does not select or install Node, Python, pnpm, uv, or Process Compose.
It uses `mise exec --locked -C <project-dir> -- <command>` for every managed
action.

Process status remains a direct read from the configured Process Compose REST
API. SwiftBar does not start a shell or run mise during its five-second status
refresh. Docker discovery and Docker container controls remain independent of
mise.

## Requirements

- macOS with [SwiftBar](https://github.com/swiftbar/SwiftBar)
- system `python3`; the plugin uses no third-party Python packages
- mise installed at `~/.local/bin/mise`, or an absolute `mise.bin` path in the
  machine-owned configuration
- each controlled repository contains a trusted `mise.toml`, a current
  `mise.lock`, installed locked tools, and repository-owned lifecycle commands
- Docker is optional; the plugin uses `DEVSTACKS_DOCKER_BIN`, the current
  non-interactive `PATH`, or the Docker Desktop application binary, in that
  order. Container sections are skipped when Docker is unavailable.

The plugin never runs `mise install`, changes a lockfile, or downloads tools.
Bootstrap and trust are explicit operator steps in the target repository.

## Install

```bash
git clone https://github.com/Mindgames/swiftbar-devstacks.git
cd swiftbar-devstacks
./install.sh
```

`install.sh` links the plugin into the SwiftBar plugin directory and seeds
`~/.config/devstacks/projects.json` only when that file does not exist.

## Configuration schema

Configuration version 2 is an object with global mise settings and project
entries. Lifecycle commands are argument arrays. They are not shell strings.

```json
{
  "version": 2,
  "mise": {
    "bin": "/absolute/path/to/mise"
  },
  "docker": {
    "bin": "/absolute/path/to/docker"
  },
  "projects": [
    {
      "name": "myapp",
      "dir": "/Users/you/Projects/myapp/myapp-ops",
      "port": 8099,
      "toolchain": "mise",
      "commands": {
        "up": ["make", "start"],
        "restart": ["make", "restart-stack"],
        "down": ["make", "stop"]
      },
      "compose": ["myapp", "myapp-native-observability"],
      "links": [
        {"label": "App", "url": "http://localhost:3001"}
      ]
    }
  ]
}
```

| Key | Required | Meaning |
| --- | --- | --- |
| `version` | yes | Must be `2` for managed controls. |
| `mise.bin` | no | Absolute mise executable. Default: `~/.local/bin/mise`. |
| `docker.bin` | no | Absolute Docker executable. Default: environment, current `PATH`, then Docker Desktop. |
| `name` | yes | Stable display name and action identifier. |
| `dir` | yes | Absolute repository directory containing `mise.toml` and `mise.lock`. |
| `port` | yes | Loopback Process Compose REST port. |
| `toolchain` | yes | Must be `mise` to enable controls. |
| `commands.up` | yes | Repository-owned stack start command as an argument array. |
| `commands.restart` | yes | Repository-owned stack restart command as an argument array. |
| `commands.down` | yes | Repository-owned stack stop command as an argument array. |
| `compose` | no | Docker Compose project labels owned by this project. |
| `links` | no | Menu links with `label` and `url`. |

Do not add per-project `path` entries. They are a second tool-version authority,
and version 2 disables controls when one is present.

See [`projects.example.json`](projects.example.json) for a complete generic
example.

## Repository onboarding and rollout

Migrate one project atomically:

1. Commit and verify the repository's `mise.toml`, `mise.lock`, Process Compose
   configuration, and lifecycle wrappers.
2. Bootstrap and trust the repository outside SwiftBar. Confirm
   `mise which --locked -C <dir> process-compose` succeeds.
3. Back up the machine-owned `projects.json` file.
4. Convert only that project to the version 2 fields. Remove its old `up` shell
   string and runtime-specific `path` entries.
5. Refresh SwiftBar and accept start, process restart, process stop/start, logs,
   TUI, stack restart, stack stop, and recovery from a failed start.
6. Confirm the Process Compose executable and version match the repository lock.
7. Keep the backup until the project has passed acceptance.

During the bounded transition, an old list-style version 1 configuration still
shows REST and Docker status. It shows a migration warning and disables all
project controls. In a version 2 file, an entry without `toolchain: "mise"` is
also status-only. This avoids a silent fallback to global tools while other
repositories finish migration.

## Rollback

1. Stop the affected stack through its repository-owned command.
2. Restore the backed-up machine configuration and the previously accepted
   plugin revision together.
3. Refresh SwiftBar.
4. Keep the repository toolchain files; rollback must not reintroduce global
   runtime selection into a migrated repository.

Do not restore only the old configuration after installing version 3 controls.
The old config remains status-only by design.

## Error diagnosis

Controls fail closed. The menu reports a specific error when the project is not
migrated, its directory or mise files are missing, mise is unavailable, the lock
cannot resolve Process Compose, or an action exits unsuccessfully.

1. Confirm the configured `dir` is the intended repository.
2. Run `mise which --locked -C <dir> process-compose` in a terminal.
3. Run the repository's documented bootstrap command if a locked tool is not
   installed. SwiftBar will not install it.
4. Refresh SwiftBar after correcting the repository or machine configuration.
5. Inspect `~/.config/devstacks/action-errors.json` for the last bounded action
   failure. It contains no command output and is mode `0600`.

For isolated source acceptance, `DEVSTACKS_CONFIG` and
`DEVSTACKS_ACTION_ERRORS` can point one plugin invocation at temporary files.
SwiftBar does not set these variables during normal use.

A stopped or starting stack is not itself an error. Red means a process failed,
restarted, or was skipped, or a container health check is failing.

## Actions and isolation

- Stack start, restart, and stop use only the configured lifecycle arrays.
- Process start, stop, restart, logs, and TUI use the target repository's locked
  Process Compose executable and configured REST port.
- Arguments are passed as arrays. Repository paths and process names are not
  interpolated into a project shell command.
- Network and subprocess reads are bounded. Status refresh does not run mise.
- Docker uses its resolved executable and remains available for unmigrated or
  stopped projects.

## Menu bar icon style

`ICON_STYLE` near the top of the plugin selects the icon:

| Value | Appearance | Colour |
| --- | --- | --- |
| `stack` (default) | Layered stack drawn by the plugin | yes |
| `text` | Small square glyph | yes |
| `emoji` | Coloured dot | yes |
| `symbol` | SF Symbol through `sfcolor` | depends on SwiftBar/macOS |

The default is a 32-by-32 pixel alpha mask written at 144 DPI. macOS renders it
as a crisp 16-point menu-bar image without replacing its state colour.

## Refresh interval

The `5s` in `devstacks.5s.py` is SwiftBar's refresh interval. Rename both the
file and symlink to change it.

## License

MIT — see [LICENSE](LICENSE).
