# Upgrade from V4.x to V5

[中文](upgrade-v4-to-v5.md) · [Back to README](../README.en.md)

This guide targets existing HFC installations and pins `v5.0.5`. Run it after that version appears in [GitHub Releases](https://github.com/baileyh8/hermes-feishu-streaming-card/releases). Updating HFC neither updates Hermes nor requires a new Feishu bot. Compatibility depends on actual source, capabilities and installation provenance; arbitrary old or manually modified installations are not guaranteed an in-place upgrade.

## Identify the current installation

Record HFC/Hermes versions, the Hermes source directory, actual `HERMES_HOME`, HFC configuration, `.env` and service managers for Gateway, Desktop and sidecar. HFC may use `~/.hermes/config.yaml` or a separate `~/.hermes_feishu_card/config.yaml`; the paths below are examples.

Use the CLI from the current installation. If `hermes-feishu-card` is missing or resolves to another environment, use the absolute CLI path recorded by your installer rather than an unrelated system Python.

```bash
hermes-feishu-card status --config /path/to/config.yaml
hermes-feishu-card doctor --config /path/to/config.yaml --hermes-dir /path/to/hermes-agent --explain
```

Back up the selected configuration, credentials, version record and installer manifests/backups. Keep credential backups private and outside Git. Preserve Hermes sessions, user data and container volumes.

## Stop old services and install V5

Wait for tasks to finish. Stop Gateway and Desktop through their existing managers, then stop the sidecar. Use the existing systemd setup on Linux, prevent a macOS LaunchAgent from respawning a second instance, and use the original container orchestration for Docker. Do not run two Gateways consuming the same bot's events.

For a transient sidecar:

```bash
hermes-feishu-card stop --config /path/to/config.yaml --env-file /path/to/.env
```

### macOS / Linux

Replace all three paths with your existing installation paths. For split HOME, also set the actual `HERMES_HOME`; it need not be the source directory's parent.

```bash
export HERMES_DIR="$HOME/.hermes/hermes-agent"
export HFC_CONFIG="$HOME/.hermes/config.yaml"
export HFC_ENV_FILE="$HOME/.hermes/.env"
curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/v5.0.5/install.sh | bash -s -- --version v5.0.5 --config "$HFC_CONFIG" --env-file "$HFC_ENV_FILE"
```

Keep the same `--profile-id` when upgrading a profile-specific installation. Do not switch configuration or credentials files just to upgrade.

### Windows PowerShell

Reuse the existing paths and set `$env:HERMES_HOME` first if the installation uses split HOME. Download the script and invoke it with explicit arguments:

```powershell
$env:HERMES_DIR = Join-Path $HOME ".hermes/hermes-agent"
$HfcConfig = Join-Path $HOME ".hermes/config.yaml"
$HfcEnvFile = Join-Path $HOME ".hermes/.env"
$HfcInstaller = Join-Path $env:TEMP "hfc-install-v5.0.5.ps1"
Invoke-WebRequest "https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/v5.0.5/install.ps1" -OutFile $HfcInstaller
& $HfcInstaller -Version v5.0.5 -Config $HfcConfig -EnvFile $HfcEnvFile
```

Retain `-ProfileId` where applicable. No machine-wide execution-policy change is required; follow your existing local administration procedure if policy blocks execution.

### Docker / NAS

Pin `HFC_VERSION=v5.0.5` in your existing image build or container installation flow. Keep `HERMES_DIR`, `HFC_CONFIG`, `HFC_ENV_FILE`, HOME and persistent volumes unchanged. Run `install-docker.sh` as described in the [installation guide](../README-install.md), then rebuild/restart through the existing orchestrator. Do not copy host paths into a container or delete volumes to remove an old installation. The [Compose example](../docker-compose.example.yml) is a reference topology, not an official image or a replacement for your configuration.

### When installation is refused

If diagnostics require restoring an old hook revision, follow the official `restore` instruction from `doctor --explain`, then reinstall. Stop on unknown source drift, unverifiable backups, conflicting source/HOME ownership or unsupported capabilities. Do not edit installed `gateway/run.py`, delete manifests/backups or bypass checks. See [installer safety](installer-safety.en.md) and [migration](migration.en.md).

## Configuration changes

| Setting | Upgrade behavior |
| --- | --- |
| App credentials and chat/profile/bot routing | Reuse existing values; do not overwrite them with a complete example |
| `classic`, `focused`, `detailed` | Not automatically changed to `task` |
| Existing `task` preset | V5 expands the process panel by default; set `timeline_expanded: false` to keep it collapsed |
| Explicit display fields | Still override presets at the same scope; old switches may override a newly selected preset |
| Native tables | Remain opt-in; `native_tables: false` by default |
| Existing messages | Not immediately redrawn when configuration changes; verify a new task after restart |

To opt into the task layout with a collapsed process panel, merge into the existing global `card` section:

```yaml
card:
  reading_preset: task
  timeline_expanded: false
```

Inspect effective values and sources before adjusting explicit profile/bot overrides:

```bash
hermes-feishu-card card-config --config /path/to/config.yaml
hermes-feishu-card card-config --config /path/to/config.yaml --minimal-yaml
hermes-feishu-card card-config --config /path/to/config.yaml --preview-dir ./hfc-v5-preview
```

These commands do not write configuration or restart services. Minimal YAML contains reading fields for the selected scope, not a replacement for the complete configuration. Use a new output directory for previews. See [reading presets](wiki/reading-presets.md).

## Start and verify

Restore the original service managers and restart Gateway/Desktop as directed by the installer. Do not start an additional sidecar if setup already started it. Use the same configuration:

```bash
hermes-feishu-card status --config /path/to/config.yaml
hermes-feishu-card doctor --config /path/to/config.yaml --hermes-dir /path/to/hermes-agent --explain
hermes-feishu-card card-config --config /path/to/config.yaml --running
```

Verify the loaded V5 version, readiness and expected loaded/next-load differences. If runtime identity cannot be verified, `--running` reports unavailable rather than presenting file values as loaded state.

Send a normal message in a test conversation and check streaming, the final answer and footer. Then check a familiar low-risk approval or clarification. A continuation link appears only after successful delivery with a verified destination; its absence alone does not prove lost content. Do not select “Always allow” merely to shorten verification.

## Roll back

Stop new tasks and shut down the relevant processes through the original managers. Use the current V5 official `restore` procedure to restore managed Hermes files. Reinstall the V4.x tag recorded before the upgrade with the same directories, configuration and credentials. Restore configuration from your private backup if needed, not from the repository example. Preserve sessions and user data. Restart and repeat status, doctor and real-message checks.

`restore` restores managed files; it does not downgrade the Python package, restore every configuration or roll back Hermes itself. If Hermes source also changed, follow the actual compatibility diagnostics instead of forcing old backups onto it. See [installer safety](installer-safety.en.md).
