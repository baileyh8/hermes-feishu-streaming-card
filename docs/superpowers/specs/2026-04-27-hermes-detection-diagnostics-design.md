# Hermes Detection Diagnostics Design

## Goal

Improve install-time diagnostics so users can understand why a Hermes directory is accepted or rejected before any file is modified.

## Scope

This change does not alter hook insertion, restore, uninstall, sidecar runtime, or Feishu CardKit behavior. It only enriches detection metadata, CLI output, and docs.

## Behavior

- `install --hermes-dir ... --yes` remains fail-closed. On unsupported Hermes directories, stderr prints a concise diagnostic block.
- `doctor --config ... --hermes-dir ...` performs a read-only Hermes detection and prints the same useful metadata.
- Existing `doctor --skip-hermes` behavior remains available.

## Diagnostic Fields

The CLI should show:

- Hermes root
- `gateway/run.py` path and whether it exists
- version source: `VERSION`, `git tag`, or `unknown`
- detected version
- minimum supported version
- support result and reason

## Safety

Diagnostics must not write to Hermes files, backups, manifests, config, or logs. Unknown version or unknown structure still means unsupported.

## Testing

Automated tests use fixtures and temporary directories. Tests cover supported fixture output, missing `gateway/run.py`, Git tag fallback source, unknown version source, and install failure diagnostics.
