# LookPrep isolated mise environment integration

- Owner: SwiftBar dev-stacks
- Date: 2026-09-01
- Status: Ready for merge

## Motivation

The merged version 2 plugin isolates mise configuration with `MISE_CONFIG_DIR`
and `MISE_SYSTEM_CONFIG_DIR`. LookPrep's repository-owned lifecycle guard
requires `MISE_DATA_DIR`, `MISE_CACHE_DIR`, `MISE_STATE_DIR`, and
`MISE_CONFIG_DIR` to be all set or all unset. Static tool resolution passes,
but a real LookPrep lifecycle command fails before it can control the stack.

Success means SwiftBar supplies one complete explicit mise directory set,
retains repository-only configuration selection, and can execute LookPrep's
non-mutating lifecycle version check through the same environment used by menu
actions.

## Scope

In scope:

- Complete the plugin-owned mise directory environment.
- Keep configuration discovery isolated while selecting the deterministic
  user data, cache, and state directories that own trusted manifests and
  installed tools.
- Add a regression test for the complete contract.
- Run the exact LookPrep `make mise-version-check` integration probe.

Out of scope:

- Changing LookPrep's lifecycle guard.
- Installing or updating tools.
- Migrating the active machine configuration before acceptance is possible.
- Repairing Docker Desktop or merging the separate Grais toolchain PR.

## Main systems

- SwiftBar Python plugin subprocess environment
- mise configuration and tool-data directory isolation
- LookPrep repository-owned Make lifecycle guard

## Expected outcome

The plugin continues to load only the target repository's `mise.toml`, resolves
the locked Process Compose executable, and runs LookPrep lifecycle commands
without falling back to ambient mise state.

## Stages

### Stage 1: Complete the isolation contract

Goal: supply all mise runtime directories from deterministic owners.

Expected output: `_mise_environment()` sets data, cache, state, config, and
system-config directories together. Configuration remains isolated, while the
deterministic user directories preserve trusted manifests and installed tools.

- [x] Add the missing isolated directory variables in `devstacks.5s.py`.
- [x] Extend `tests/test_devstacks.py` with exact path and environment checks.

### Stage 2: Verify source and integration behavior

Goal: prove the fix at both repository and cross-repository boundaries.

Expected output: unit tests and the exact LookPrep non-mutating lifecycle probe
pass under the plugin-generated environment.

- [x] Run all SwiftBar unit tests with system Python.
- [x] Run `mise config ls`, locked Process Compose resolution, and LookPrep
      `make mise-version-check` through the plugin-generated environment.
- [x] Run `git diff --check` and inspect the focused diff.

### Stage 3: Deliver the focused fix

Goal: publish a reviewable correction without changing machine configuration.

Expected output: one focused commit and PR linked to issue #1.

- [x] Commit and push the verified source and plan.
- [x] Open a PR with the regression and integration evidence.
- [x] Keep the active version 1 machine config unchanged until Docker and Grais
      allow the required manual acceptance.

## Validation

- `python3 -m unittest discover -s tests -v`
- Plugin-generated mise environment selects only the LookPrep repository
  manifest.
- `mise which --locked -C <lookprep-ops> process-compose` resolves version
  `1.122.0` from the repository lock.
- `mise exec --locked -C <lookprep-ops> -- make mise-version-check` passes.
- `git diff --check`

## Follow-ups

- Repair Docker Desktop before active LookPrep stack acceptance.
- Merge and accept the Grais repository toolchain before its configuration
  entry is migrated.
- Migrate `projects.json` atomically with a backup, then complete menu-bar
  start, process, log, TUI, restart, stop, failure, and recovery acceptance.
