# Migrating From legacy/dual To sidecar-only

[中文](migration.md) | [English](migration.en.md)

This document covers safe migration from historical legacy/dual/patch implementations in this repository to the current `hermes_feishu_card/` sidecar-only mainline. Historical entry points are archived under `legacy/`, including `legacy/adapter/`, old `legacy/sidecar/`, old `legacy/patch/`, `legacy/installer.py`, `legacy/installer_sidecar.py`, `legacy/installer_v2.py`, `legacy/gateway_run_patch.py`, and `legacy/patch_feishu.py`. They are not the active runtime.

## Principles

- Back up first, then diagnose, then install. Any uncertain state should fail closed.
- Do not mix legacy/dual hooks with the sidecar-only hook.
- Do not commit App Secret, tenant token, real chat_id, logs, or screenshots containing private content.
- Do not manually copy old patch fragments into Hermes `gateway/run.py`.
- If Hermes files were changed by users or other tools, inspect the diff before continuing.

## Recommended Flow

1. Stop the current sidecar-only process if it has been started:

```bash
python3 -m hermes_feishu_card.cli stop --config config.yaml.example
```

2. Keep an external backup of the Hermes installation directory. Back up the whole Hermes directory, not just this repository.

3. If the current Hermes directory was installed by this sidecar-only plugin, restore first:

```bash
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

`restore` handles only state verified by the current manifest. V4.1 `manifest_version: 2` treats `gateway/run.py`, required Hermes 0.19 `gateway/platforms/base.py`, and optional Cron as one transaction; an incomplete target or backup never triggers partial restore. On changed source/backup, `install state incomplete`, or `newer installer required`, stop and inspect every managed target rather than fixing run alone.

4. If Hermes previously used historical legacy/dual scripts such as `legacy/installer_v2.py`, `legacy/gateway_run_patch.py`, or `legacy/patch_feishu.py`, restore from the original backup created by those scripts. If no trusted backup exists, reinstall or check out the matching Hermes version before migration.

5. Run read-only diagnostics:

```bash
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
```

Continue only when the output says `hermes: supported` and `version`, `version_source`, `run_py_exists`, and `reason` match expectations.

6. Install the sidecar-only hook:

```bash
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
```

The installer backs up every managed target, writes `manifest_version: 2`, and installs minimal calls into `hermes_feishu_card.hook_runtime`. For Hermes 0.19 / `v2026.7.20+` or verified exact-ledger source, required Base installs, restores, and rolls back with run. Feishu CardKit, session state, health metrics, and retry counts remain in the sidecar.

7. Start and inspect the sidecar:

```bash
python3 -m hermes_feishu_card.cli start --config config.yaml.example
python3 -m hermes_feishu_card.cli status --config config.yaml.example
```

`status` should show `status: running`, `active_sessions`, and metrics. Without Feishu credentials, advanced starts use a no-op client. With credentials, the sidecar reads them only from local config or environment variables.

## Upgrading From V4.1.0 To V4.1.1

V4.1.1 fixes the boundary where disk state is current but a running sidecar still uses an old interpreter/package, as well as an incorrect fence while waiting for the first heartbeat. Continue to use official setup/install and never edit Hermes source manually:

```bash
hermes-feishu-card doctor --config CONFIG --hermes-dir HERMES_DIR --explain
hermes-feishu-card stop --config CONFIG
hermes-feishu-card setup --config CONFIG --hermes-dir HERMES_DIR --yes
```

Setup installs and rechecks V4.1.1 through the detected Hermes runtime venv and uses the package version and Python identity from `/health` to decide whether an old sidecar must restart. A running old sidecar without a pidfile is never silently adopted or killed. Stop that service manually and rerun setup; do not substitute a guessed PID or broad `pkill`.

Only when `doctor --explain` confirms an `installed` on-disk plan, sidecar health is unreachable, no pidfile exists in the state directory, and `manual_review_required` remains may you run:

```bash
hermes-feishu-card integrity acknowledge-review \
  --config CONFIG \
  --hermes-dir HERMES_DIR \
  --yes
```

An empty `pre_repair_runtime_hash` means runtime identity cannot prove a process transition, so operator acknowledgement may clear that otherwise unresolvable fence. A non-empty hash clears only the manual-review bit; the Gateway restart fence remains until a different runtime id sends a generation/package-matching `runtime.hello`. Then manually restart sidecar and Hermes Gateway and confirm ready through `doctor` / `/health`. Dirty targets, unknown manifests/backups, non-private state/fence files, or a remaining pidfile must be resolved first; `acknowledge-review` is not a force-clear command.

A legacy `0644` pidfile can be tightened in place to `0600` only inside a current-user-owned private `0700` state directory with a strictly matching shape and identity. Every other case fails closed.

## Upgrading From V4.1.3 To V4.1.4

V4.1.4 fixes the Windows legacy-install migration gap from Issue #171. If `.hermes_feishu_card_manifest` is missing while an older owned Gateway hook and `.hermes_feishu_card.bak` remain, rerun official `install` / `setup`. The installer rebuilds the manifest and upgrades the hook only when removing owned blocks restores the backup byte-for-byte, the backup is parseable clean source, and Cron/Base evidence independently matches.

Do not hand-create a manifest or call internal `apply_patch()` APIs. Unicode comments, CRLF, and native Windows path separators are not the reproduced root cause. An edit outside owned markers, a mismatched backup, a symlink, or unparseable source still fails closed and requires preserved files plus manual review.

```bash
hermes-feishu-card doctor --config CONFIG --hermes-dir HERMES_DIR --explain
hermes-feishu-card install --hermes-dir HERMES_DIR --yes
```

On Windows PowerShell, install V4.1.4 through the official `install.ps1` / `setup` flow. After `manifest: rebuilt` and `install ok`, restart Hermes Gateway and confirm that doctor reports install state `installed`.

## Upgrading From V4.1.2 To V4.1.3

V4.1.3 fixes the recovery convergence gap reproduced in Issue #158 after a real Hermes upstream update. Once official `install` reinjects the hook, the current integrity-plan fingerprint changes while the old manual-review fence remains bound to the pre-upgrade plan. `integrity acknowledge-review` can now atomically update that plan binding and clear manual review only after two verified-current-installed-plan checks, two stopped-sidecar checks, an unchanged fence CAS snapshot, and an exact match between the old and current Hermes target identity. A different target, remaining pidfile/health, dirty or unverifiable plan, or unknown legacy fence still fails closed. A non-empty restart hash remains until a new matching `runtime.hello` clears the independent restart fence.

This release also includes PR #168's same-name answer-delta callback selection and fixes Issue #169. After Hermes `1a3a9de` moved callbacks into `TurnRunner`, the official patcher restores stable tool, answer, thinking, clarify, approval, and status hooks through `TurnContext`. Doctor follows actual patchability; an unrecognized TurnRunner shape fails installation with `not safely patchable` and must not be bypassed by editing `gateway/run.py` manually.

Do not edit `runtime-integrity-fence.json` or call internal Python functions. When `doctor --explain` reports `integrity_migration_required`, run the displayed `integrity migrate-safe` command. For another verified manual-review fence, stop the sidecar, run the complete `integrity acknowledge-review --config CONFIG --hermes-dir PATH --state-dir STATE --yes` command printed by doctor, then manually restart the sidecar and Hermes Gateway.

## Upgrading From V4.1.1 To V4.1.2

V4.1.2 fixes the race where a brief stale-heartbeat window during a normal Gateway restart could be persisted as a restart fence. Upgrade through the official `setup` path and restart the Gateway once. After the new matching `runtime.hello`, readiness should return directly to `runtime_ready`. If restart-required remains, do not restart repeatedly; use `doctor --explain` to inspect generation/package, control authentication, and any existing fence.

## Upgrading To V4.1.0

V4.1.0 preserves cards as the default and does not silently mutate an old configuration. Upgrade the package and rerun setup/install first, then add only the controls you need:

```yaml
bindings:
  native_chats: []
card:
  table_overflow_mode: compact  # compact | truncate
integrity:
  mode: notify  # old configs remain notify until explicit migration
service:
  manager: auto  # auto | systemd-user | systemd-system | detached
```

`bindings.native_chats` uses exact matching. Manage it with `chats use-native`, `chats use-card`, and `chats list`; multi-profile commands require `--profile-id` and write only that profile. `table_overflow_mode: compact` retains table six onward without data loss, while `truncate` is the explicit legacy behavior. A terminal card JSON above 28,000 bytes returns the complete answer to Hermes native delivery.

An old config without `integrity` loads as `notify`. Only an installation with verified Git provenance, backup, manifest, owned blobs, and anchors can migrate explicitly:

```bash
hermes-feishu-card integrity migrate-safe \
  --config ~/.hermes/config.yaml \
  --hermes-dir ~/.hermes/hermes-agent \
  --yes
```

Success prints `sidecar.restart_required: true` and `gateway.restart_required: false`. Restart the sidecar before signed `runtime.hello` / `runtime.heartbeat` events are evaluated in safe mode. If strict repair later reinstalls the hook, state changes to `gateway.restart_required: true`, but HFC never restarts Gateway automatically. Incomplete evidence, user edits, symlinks, dirty targets, branch rewinds, and source-stripped roots remain fail closed.

`service.manager: auto` chooses only `systemd-user` or `detached`; it never silently enters `systemd-system` or invokes sudo. `systemd-system` is an explicit Linux transient-unit opt-in. Docker remains an ordinary container process with `detached`. Hermes 0.19.0 / `v2026.7.20` uses AST-owned run + Base hooks. A legacy run-only manifest gains the Base backup/patch and migrates to v2 only under strict evidence; runtime monitoring and strict repair handle upgrade replacement without an import-hook bridge.

## Upgrading To V3.4.0

V3.4.0+ selects the hook strategy from the Hermes version and `gateway/run.py` code anchors. Hermes `0.13.0+`, `0.14.0` / `v2026.5.16+` uses `gateway_run_013_plus`; older Hermes from `v2026.4.23` through `v2026.4.x` continues to use `legacy_gateway_run`. After upgrading the plugin, reinstall the hook; restarting the sidecar alone is not enough.

```bash
python3 -m hermes_feishu_card.cli stop --config ~/.hermes_feishu_card/config.yaml
pip install -e ".[test]" --upgrade
python3 -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml --hermes-dir ~/.hermes/hermes-agent
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli start --config ~/.hermes_feishu_card/config.yaml
```

`doctor` output should include `hook_strategy`, `compatibility`, and anchors. If Hermes has been upgraded to `0.13.0+`, `0.14.0`, or `v2026.5.16+`, confirm `hook_strategy: gateway_run_013_plus` before installing; older `v2026.4.x` Hermes should continue to report `legacy_gateway_run`.

For multiple independent Hermes profile processes, set a stable `HERMES_FEISHU_CARD_PROFILE_ID` for each process. This avoids ambiguous automatic profile detection and keeps profile-to-bot routing explicit. A single sidecar serving multiple profiles should still use the `profiles` section for each profile's credentials, bots, and card title.

## Upgrading From V3.1 To V3.2.1

V3.2.1 is **backward compatible** with V3.1 on the sidecar-only architecture. Single-bot configurations continue to work without changes; to use the new multi-bot / group chat binding features, the configuration must be extended.

### Upgrade Steps

1. **Back up current config**

   ```bash
   cp ~/.hermes_feishu_card/config.yaml ~/.hermes_feishu_card/config.yaml.v3.1.backup
   ```

2. **Stop the sidecar (recommended)**

   ```bash
   python3 -m hermes_feishu_card.cli stop --config ~/.hermes_feishu_card/config.yaml
   ```

3. **Update code to V3.2.1**

   ```bash
   cd /path/to/hermes-feishu-streaming-card
   git checkout v3.2.1  # or the latest tag
   python3 -m pip install -e ".[test]" --upgrade
   ```

4. **Update configuration**

   Option A: Use CLI to generate an updated template (preserves existing config, adds V3.2.1 fields)
   ```bash
   python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --config ~/.hermes_feishu_card/config.yaml --yes
   ```
   This supplements the existing `config.yaml` with new top-level fields (`bots`, `bindings`, etc.) without overwriting existing values.

   Option B: Manual merge (see `config.yaml.example` for a complete sample)
   - Add a `bots:` list under `hermes:` (at least one bot; its `app_id`/`app_secret` can be inherited from the original single-bot fields)
   - Add a `bindings:` section with `fallback_bot` and optional `chats:` mappings
   - The old `feishu.app_id` / `feishu.app_secret` are still valid in single-bot mode, but migrating to `bots[0]` is recommended for consistency

5. **Validate configuration**

   ```bash
   python3 -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml
   ```
   Expect `config: valid` and correct detection of `bots` / `bindings` fields.

6. **Restart sidecar**

   ```bash
   python3 -m hermes_feishu_card.cli start --config ~/.hermes_feishu_card/config.yaml
   python3 -m hermes_feishu_card.cli status --config ~/.hermes_feishu_card/config.yaml
   ```

7. **Functional validation**
   - Send a card message in a 1-to-1 or group chat to confirm normal rendering
   - If multi-bot is configured, check `/health.routing` for routing stats
   - Run `cli bots list` to verify the bot registry

### Compatibility Notes

- V3.1 single-bot configs **continue to work** on V3.2.1 without modification (old fields still supported)
- V3.2.1's multi-bot features are optional; if `bindings.chats` is unset, all conversations route to `bindings.fallback_bot`
- Environment variables `FEISHU_APP_ID` / `FEISHU_APP_SECRET` remain effective in V3.2.1, but `config.yaml`'s `bots[]` takes precedence
- To roll back to V3.1: stop the sidecar, restore the backed-up `config.yaml`, and reinstall the V3.1 version

### Important Notes

- Each bot used in multi-bot mode must be created in the Feishu Open Platform with `send_message` and `update_message` permissions
- Group chat bindings require `chat_id` (obtained from the Feishu client or API), not the group name
- After upgrading, it is recommended to run `pytest -q` locally to ensure all tests pass

## Rollback

To roll back:

```bash
python3 -m hermes_feishu_card.cli stop --config config.yaml.example
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

If `restore` refuses to overwrite, do not force-delete a hook. Compare run, required Base, optional Cron, each installer backup, the manifest, and external backups before manual recovery. A future-version manifest must be handled by its matching newer installer.

## Verification Checklist

- `doctor --config ... --hermes-dir ...` prints `hermes: supported`.
- `install --hermes-dir ... --yes` prints `install ok`.
- `start --config ...` prints `start ok` or `start: already running`.
- `status --config ...` prints `/health` metrics.
- Hermes native text still works when the sidecar is unavailable.
- `gateway/run.py` does not contain both legacy/dual and sidecar-only hooks.

## Integrity review acknowledgement boundary

`acknowledge-review` is not a general repair command. Doctor suggests it only after verifying an `installed` recovery state with no recovery actions and an `installed` integrity plan whose reason is `recovery_not_required`. Every other manual-review reason must be repaired and diagnosed again first.

## Explicit orphaned-target rebinding

After a reboot/remount changes a filesystem device number, a fence may refer to an old identity. The stored hash cannot prove that only the device number changed; HFC does not automatically treat it as the same installation.

Verify the intended Hermes root and configuration, stop sidecar through its service owner, and confirm the installed plan is verifiable with doctor. Read (do not edit) `target_identity` in the selected state's `runtime-integrity-fence.json`, then explicitly authorize rebinding:

```bash
hermes-feishu-card integrity acknowledge-review \
  --config <actual-config> --hermes-dir <actual-hermes-root> \
  --state-dir <actual-sidecar-state> \
  --rebind-target <old-64-character-target_identity> --yes
```

This authorizes binding that exact old record to the verified current install; it does not prove that the directory was never replaced. The old identity must match, the installed plan and stopped-sidecar state are verified twice, and the write uses the unchanged record's CAS token. Dirty/unverifiable installs, a running sidecar, wrong identity or races refuse the operation. Known Gateway restart evidence remains. Restart through existing owners and check health afterwards. Ordinary acknowledgement continues to reject target changes.
