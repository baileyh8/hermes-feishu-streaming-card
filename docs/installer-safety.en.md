# Installer Safety

[中文](installer-safety.md) | [English](installer-safety.en.md)

The installer performs only minimal, verifiable, recoverable writes. Version-text changes can fall back to supported source anchors, but uncertain structure, backups, manifests, or file-safety checks fail closed. For Hermes 0.19.0, `v2026.7.20+`, or source with the verified exact delivery-ledger structure, `gateway/run.py` and `gateway/platforms/base.py` are inseparable required targets; optional Cron remains capability-detected.

## Pre-install Checks

Before installation, the installer verifies:

- The Hermes directory contains `gateway/run.py`; exact-Base releases must also contain `gateway/platforms/base.py`.
- Version metadata is parseable, or source contains structures recognized by the current hooks. Supported inputs include `VERSION=v2026.4.23+`, Git tag `v2026.4.23+`, `0.18.x` / `0.19.x`, descriptive versions, and unparseable metadata with verified anchors.
- `gateway/run.py` has a supported insertion point. When Base is required, media extraction, obligation, ledger attempting/delivered, and final-send structures must all match exactly.
- Existing install state, backup, and manifest are not contradictory.
- If the Hermes directory contains `venv/bin/python`, `.venv/bin/python`, or the Windows `Scripts/python.exe` equivalent, that runtime Python must be able to import `hermes_feishu_card.hook_runtime`; otherwise setup installs the current plugin release into that venv before patching Hermes.

If a check fails, Hermes files are not modified.

## macOS: a transient sidecar is expected

`enable` / persistent user services require Linux systemd. This project does not manage macOS LaunchAgents (see [#183](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/183)). On macOS, `setup` / `install.sh` normally start a detached sidecar, explain the persistence limitation, and no longer suggest the unsupported `enable` command.

For startup after user login, configure your own user LaunchAgent with `RunAtLoad=true` to run `hermes-feishu-card start` once. **Do not set `KeepAlive=true`**: `start` detaches the sidecar child, writes its HFC-managed pidfile, and exits; repeatedly restarting this short-lived launcher causes a relaunch loop. Use verified absolute paths for the executable, `--config`, `--hermes-dir`, and any `--env-file`. This arrangement does not provide pre-login boot startup or automatic crash recovery.

This differs from a custom LaunchAgent that **runs the runner directly**, which has no HFC-managed pidfile. If the installer reports `a running sidecar cannot be managed safely without a verified pidfile`, identify the actual process owner first; a missing pidfile alone does not prove launchd ownership. For a confirmed direct-runner LaunchAgent, verify its label, stop that job with `launchctl bootout gui/$(id -u)/<label>` (or `launchctl unload /absolute/path/to/job.plist` for the confirmed plist), then rerun the official installer. Otherwise identify and stop the owning service manually. The installer does not guess ownership or kill an unknown process.

Run a read-only diagnostic first:

```bash
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --json
```

Diagnostic output includes support status, Hermes root, run/Base paths and existence, `base_required`, `exact_delivery_contract`, version source/value, minimum version, strategy, compatibility, anchors, and reason. Source-stripped roots with valid anchors but no metadata display `version: unknown (source-stripped metadata)`. Diagnostics also include runtime import and Feishu SDK capability. `--explain` renders runtime import, streaming, manifest/backup/multi-target state, and recommendations; `--json` provides the machine-readable equivalent. Every `doctor` mode is read-only.

## Repair

```bash
python3 -m hermes_feishu_card.cli repair --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli setup --repair --hermes-dir ~/.hermes/hermes-agent --config ~/.hermes_feishu_card/config.yaml --yes
```

`repair` fixes only install state this project can verify. V4.1 requires ownership evidence for run, required Base, and optional Cron to agree: if any Base marker, backup, or manifest evidence exists, repair must not restore run alone and then clear state. Missing backups or manifests may be rebuilt only when every managed target supports the same verified transaction. The narrow marker-only recovery still requires the expected patched hash rebuilt from a verified backup, with differences limited to owned BEGIN/END marker lines.

If an intentional Hermes upgrade replaced unpatched run, required Base, or optional Cron so it differs from a verified old backup, recovery refuses ordinary stale-state handling by default. After confirming an intentional upgrade, opt in explicitly:

```bash
# Recover old state and reinstall from the upgraded source in one command
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --accept-hermes-upgrade --yes

# Or run the two phases separately
python3 -m hermes_feishu_card.cli repair --hermes-dir ~/.hermes/hermes-agent --accept-hermes-upgrade --yes
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
```

`setup` also accepts `--accept-hermes-upgrade`. It never writes an old backup over upgraded source. It clears only verified stale HFC artifacts, then backs up and patches every currently required target. All source must parse with supported anchors, the manifest must validate, and every old backup must remain unchanged and match its hash. Missing or corrupt backups, invalid manifests, symlinks, unreadable files, unknown markers, an incompatible required Base, unsupported source, or remaining owned patches still fail closed.

`status` and `start` resolve `HERMES_DIR` from an explicit `--hermes-dir`, the selected env file, the config-adjacent `.env`, or process environment, then check hook state read-only. When a Hermes upgrade replaced the source but the old backup/manifest still verify, they report `hook.status: upgrade_repair_required` and print the explicit recovery command plus `hermes gateway start`; `start` refuses before launching the sidecar, preventing a silent “healthy sidecar, missing Gateway hook” state. User edits, corruption, unsupported source, or incomplete evidence report `manual_review_required` without offering the `--accept-hermes-upgrade` shortcut.

## V4.1 Runtime Integrity

New installs write `integrity.mode: safe`; an old config without the section remains `notify`. An older installation may run `integrity migrate-safe --config CONFIG --hermes-dir HERMES_DIR --yes` only when provenance verifies. Success prints `sidecar.restart_required: true` and `gateway.restart_required: false`: restart the sidecar to load the mode, but the migration itself does not require a Gateway restart.

After restart, the Gateway runtime sends `runtime.hello` / `runtime.heartbeat` in a separate signing domain. These events prove only the current HFC runtime generation and liveness. They carry no paths, source hashes, chat ids, or secrets and cannot authorize a file write by themselves. `safe` still requires the current Git HEAD to descend from the recorded HEAD, target blobs to equal current HEAD, backup/manifest/anchors/reversible patch evidence to agree, and a fresh fingerprint check immediately before mutation.

When strict repair successfully reinstalls the hook, readiness reports `gateway.restart_required: true`. HFC never restarts or kills Gateway automatically. The operator selects a suitable window, and a later matching `runtime.hello` clears the state. A missing authenticated control secret, source-stripped root, symlink, dirty target, branch rewind, user edit, old manifest, or changing evidence refuses automatic repair.

## Backup And Manifest

V4.1 saves every managed source backup, then writes `manifest_version: 2`. It records at least:

- Relative `run_py` path.
- Hash of the patched `run.py`.
- Relative backup path.
- Backup hash.
- Required Base `base_py`, `base_patched_sha256`, `base_backup`, and `base_backup_sha256`; these four fields are all-or-none.
- Equivalent optional Cron paths and hashes when that target is supported.

`restore` and `uninstall` verify run, required Base, optional Cron, and their backups as one transaction. A v1 manifest cannot prove Base ownership and migrates only through strict repair/install verification. Any target change, missing required backup, or partial field set refuses partial restore or ownership cleanup.

## Atomic Writes

Base (before run), run, optional Cron, backups, and manifest use temporary replacement. If any install or restore step fails, the entire multi-target transaction rolls back, preventing a restored run from leaving Base patched and orphaned.

## Restore And Uninstall

```bash
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli uninstall --hermes-dir ~/.hermes/hermes-agent --yes
```

`restore` restores every managed Hermes file that existed before installation. `uninstall` removes the same owned hooks and state. Both are all-target success or no-op and never overwrite unverifiable user changes.

When migrating from legacy/dual historical installs, read [migration.en.md](migration.en.md). Historical `legacy/installer_v2.py`, `legacy/gateway_run_patch.py`, and `legacy/patch_feishu.py` wrote patches outside the current manifest model and must not be assumed recoverable by current `restore`.

## Degraded Behavior

If the sidecar is unavailable, times out, or returns an error, the Hermes hook lets Hermes continue with native text replies. Card failure is a plugin failure, not an Agent workflow failure.

Hook import or emit exceptions also remain fail-open, but should not be fully silent. From V3.6.2, injected hook blocks write `[hermes-feishu-card] hook failed: ...` to Hermes stderr so runtime venv, import, or sidecar emit problems are diagnosable from Gateway logs.

## Remote version resolution

Installer `latest` means “resolve the latest stable release once” and must become an exact `vX.Y.Z` Git ref. Release API, JSON, or tag-validation failure fails closed before credential prompting, pip, doctor, setup, or Docker state writes. An explicit release tag remains pinned and bypasses the API; only explicit `--version main` selects the moving development branch.
