# Architecture

[中文](architecture.md) | [English](architecture.en.md)

The target architecture is sidecar-only. Hermes Agent keeps only a minimal hook that forwards message lifecycle events to a local HTTP sidecar. Feishu card creation, updates, final-state rendering, and state accumulation happen inside `hermes_feishu_card/`.

The current implementation includes the installer, backup/restore/uninstall loop, event protocol, sidecar HTTP API, rendering, session state, fail-open forwarding from the Hermes hook to `/events`, Feishu CardKit HTTP delivery, process management, health metrics, and retry diagnostics.

## Components

### Minimal Hermes hook

The installer only modifies Hermes `gateway/run.py` by inserting a marked hook block. The hook calls `hermes_feishu_card.hook_runtime`; extraction and forwarding logic is tested in this package. The hook remains fail-open and does not contain Feishu credentials or card rendering logic.

### HTTP sidecar

`hermes_feishu_card.server` exposes local HTTP endpoints that receive events from the Hermes hook. The sidecar runs independently from the Hermes process, so card delivery failures should not bring down the Agent.

`hermes_feishu_card.cli start/status/stop` manages the local sidecar process. Process state is stored in a user-level pidfile. `status` uses `/health` as the source of truth. `stop` only terminates the process when the PID/token in the pidfile matches `process_pid/process_token` returned by `/health`, avoiding stale pidfiles and PID-reuse hazards. This process model targets POSIX environments such as macOS and Linux.

When Feishu credentials are missing, advanced sidecar starts use a no-op client that accepts events and maintains session state without sending real Feishu cards. When credentials are configured, the runner uses the real Feishu HTTP client. The ordinary-user `setup` command requires credentials before installing the Hermes hook.

`/health` exposes process-local `metrics`, including `events_received`, `events_applied`, `events_ignored`, `events_rejected`, `feishu_send_successes`, `feishu_update_successes`, `feishu_update_failures`, and `feishu_update_retries`. To avoid duplicate cards, `send_card` does not blindly retry card creation. Updates for an existing message_id use a limited retry.

### Session state

`hermes_feishu_card.session` maintains streaming state per message: thinking text, answer text, tool call counts, completion status, and failure status. Events are aggregated by session before rendering creates the Feishu card content.

### Feishu client

`hermes_feishu_card.feishu_client` defines the Feishu/Lark CardKit boundary. Credentials come from local config or environment variables and must not enter the repository. The client obtains a tenant access token, sends an interactive card, and updates the card message incrementally.

## Legacy Boundary

`legacy/adapter/`, `legacy/sidecar/`, `legacy/patch/`, `legacy/installer.py`, `legacy/installer_sidecar.py`, `legacy/installer_v2.py`, `legacy/gateway_run_patch.py`, and `legacy/patch_feishu.py` are historical legacy/dual/patch implementations. They are not the active runtime. The active runtime is `hermes_feishu_card/`, the current CLI, and the current installer safety model.
