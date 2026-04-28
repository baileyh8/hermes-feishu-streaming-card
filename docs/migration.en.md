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

`restore` only handles install state that the current manifest can verify. If it reports `run.py changed since install`, `backup changed since install`, or `install state incomplete`, stop and inspect Hermes `gateway/run.py` manually.

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

The installer creates a backup and manifest, then installs a minimal hook that calls `hermes_feishu_card.hook_runtime`. Feishu CardKit, session state, health metrics, and retry counts live inside the sidecar process.

7. Start and inspect the sidecar:

```bash
python3 -m hermes_feishu_card.cli start --config config.yaml.example
python3 -m hermes_feishu_card.cli status --config config.yaml.example
```

`status` should show `status: running`, `active_sessions`, and metrics. Without Feishu credentials, advanced starts use a no-op client. With credentials, the sidecar reads them only from local config or environment variables.

## Rollback

To roll back:

```bash
python3 -m hermes_feishu_card.cli stop --config config.yaml.example
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

If `restore` refuses to overwrite, do not force-delete the hook. Compare Hermes `gateway/run.py`, the installer backup, the manifest, and any external backup before manual recovery.

## Verification Checklist

- `doctor --config ... --hermes-dir ...` prints `hermes: supported`.
- `install --hermes-dir ... --yes` prints `install ok`.
- `start --config ...` prints `start ok` or `start: already running`.
- `status --config ...` prints `/health` metrics.
- Hermes native text still works when the sidecar is unavailable.
- `gateway/run.py` does not contain both legacy/dual and sidecar-only hooks.
