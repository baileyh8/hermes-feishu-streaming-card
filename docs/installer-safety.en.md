# Installer Safety

[中文](installer-safety.md) | [English](installer-safety.en.md)

The installer is designed to perform only minimal, verifiable, recoverable writes. Any uncertain version, code structure, backup, or manifest state must fail closed.

## Pre-install Checks

Before installation, the installer verifies:

- The Hermes directory exists and contains the expected `gateway/run.py`.
- Hermes version and structure are in the supported range: `VERSION=v2026.4.23+` or Git tag `v2026.4.23+`.
- `gateway/run.py` contains an insertion point recognized by the current hook.
- Existing install state, backup, and manifest are not contradictory.

If a check fails, Hermes files are not modified.

Run a read-only diagnostic first:

```bash
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
```

Diagnostic output includes Hermes support status, Hermes root, `gateway/run.py`, `run_py_exists`, `version_source`, `version`, `minimum_supported_version`, and `reason`.

## Backup And Manifest

Installation saves a backup of `gateway/run.py` before writing the patched file, then writes a manifest. The manifest records at least:

- Relative `run_py` path.
- Hash of the patched `run.py`.
- Relative backup path.
- Backup hash.

`restore` and `uninstall` use the manifest to verify the current `run.py` and backup are in a state owned by this installer. If user changes or unknown tool changes are detected, the command refuses to overwrite.

## Atomic Writes

The installer writes `run.py`, backup files, and manifest files via temporary file replacement to avoid truncated files. If any install step fails, already-written state should be rolled back or cleaned up.

## Restore And Uninstall

```bash
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli uninstall --hermes-dir ~/.hermes/hermes-agent --yes
```

`restore` restores the Hermes file that existed before installation. `uninstall` removes the hook and install state owned by this plugin. Neither command should overwrite unverifiable user changes.

When migrating from legacy/dual historical installs, read [migration.en.md](migration.en.md). Historical `legacy/installer_v2.py`, `legacy/gateway_run_patch.py`, and `legacy/patch_feishu.py` wrote patches outside the current manifest model and must not be assumed recoverable by current `restore`.

## Degraded Behavior

If the sidecar is unavailable, times out, or returns an error, the Hermes hook lets Hermes continue with native text replies. Card failure is a plugin failure, not an Agent workflow failure.
