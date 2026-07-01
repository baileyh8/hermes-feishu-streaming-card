# Release Readiness

[中文](release-readiness.md) | [English](release-readiness.en.md)

Current package version: `3.8.2`. This release keeps the sidecar-only mainline, builds on the V3.8.0 card UX upgrade and V3.8.1 high-frequency delta coalescing, fixes pre-tool answer archival timing and terminal body de-duplication, and improves the "Reasoning and Tools" timeline hierarchy.

## Ready

- Hermes `v2026.4.23+` detection and fail-closed installation.
- Minimal Hermes hook, backup, manifest, restore, and uninstall.
- Sidecar `/events`, `/health`, and process `start/status/stop`.
- Feishu CardKit HTTP client, covered by mock Feishu server and real Feishu test app for tenant token, send, and update flows.
- Manual `smoke-feishu-card` command.
- E2E preview artifacts and generator.
- Real long-card stress test: one Feishu card updated to 16k Chinese characters.
- Real Hermes `v2026.4.23` `restore -> install` loop verification.
- Hermes `0.13.0+` / `0.14.0` / `0.15.x` / `0.17.x` / `v2026.5.16+` / `v2026.6.19+` use the `gateway_run_013_plus` hook strategy, while older `v2026.4.x` keeps `legacy_gateway_run`.
- Feishu card button interactions are covered through local mock acceptance for `interaction.requested`, `/card/actions`, and `/interactions/{interaction_id}`; localhost/private sidecar text fallback is covered through `card.interaction_mode: text`.
- Feishu thread messages can carry optional `thread_id`; with a reply anchor, the sidecar uses the Feishu reply API to create the initial card in the original thread, and later updates keep PATCHing the same card.
- Cron delivery can extract chat ids from `deliver: "feishu:oc_xxx"`, avoiding plain-text fallback for scheduled Feishu deliveries.
- Long Markdown tables and fenced code blocks over `MAIN_CONTENT_CHUNK_CHARS` are split as complete repeated structures to avoid raw Markdown rendering.
- Thinking/interim assistant messages use complete `append_block` chunks to avoid delta accumulation truncation or missing text.
- Runtime event sends, sidecar updates, and terminal PATCH calls are ordered/coalesced for the same message id.
- Gateway runtime coalesces high-frequency `thinking.delta` / `answer.delta` events inside the Hermes process, covering V3.8.1 issue #74 and reducing stream-reader thread pressure.
- Terminal events flush pending deltas for the same message before final card rendering.
- Feishu-side `/hfc help/status/doctor/monitor` commands return read-only diagnostic cards with hashed context ids.
- Pre-tool answers stay in the primary body first, then archive into the auxiliary timeline when the next answer or terminal event arrives; terminal cards strip already archived intermediate prefaces.
- Auxiliary timeline reasoning and tool details use separate text sizes and visual weight, while raw `thinking.delta` stays out of the user-visible timeline.
- Terminal events ACK Hermes quickly while slow Feishu PATCH calls complete in the background, preventing duplicate native replies after interrupts or update backlogs.
- `load_config()` reads a `.env` file next to the selected config file while preserving real process environment variables as the highest-precedence source.
- `install.sh` imports only Feishu/sidecar variables from `.env`, avoiding execution of unrelated values such as paths with spaces.
- `install.sh` retries pip with `--break-system-packages` when uv/PEP 668 reports an externally managed Python environment.
- Windows sidecar process `stop/status` avoids POSIX process-group signals and uses Windows-specific PID/`taskkill` handling.
- `doctor --json` / `doctor --explain` report config, sidecar, Hermes, streaming, install_state, and recommendations.
- `doctor --explain` / `install` suggest the Hermes CLI `Project:` directory as the correct `--hermes-dir` when `gateway/run.py` is missing and `hermes -V` is available.
- `setup` / `install` detect the Hermes runtime venv Python and install the same plugin release there; `doctor` reports `runtime_import`.
- `install-docker.sh` supports installer/update workflows inside existing Hermes Docker containers with defaults `HERMES_DIR=/opt/hermes`, `HFC_CONFIG=/opt/data/config.yaml`, and `HFC_ENV_FILE=/opt/data/.env`.
- `docker-compose.example.yml` documents bind mounts and one-shot `bash install-docker.sh` execution for container topologies.
- Hook import/emit failures remain fail-open but write `[hermes-feishu-card] hook failed: ...` diagnostic warnings to Hermes stderr.
- `repair --hermes-dir ... --yes` and `setup --repair` repair verifiable manifest/backup state and refuse unverifiable user edits.
- Structured attachment, media, and file objects keep card summaries while preserving Hermes native media/file delivery paths.
- `smoke-feishu-card --profile-id`, `bots test --profile-id`, CLI `status`, and `/health.routing.profiles` support profile-scoped troubleshooting.
- Hermes key release matrix covers `v2026.4.23`, `v2026.5.7`, `v2026.5.16+`, `v2026.5.29`, `v2026.6.19+`, `0.13.x`, `0.14.x`, `0.15.x`, `0.17.x`, and semantic versions with or without a `v` prefix.
- GitHub Actions Python 3.9 / 3.12 test matrix for PRs and pushes, plus Windows parser validation for `install.ps1`.
- Release assets workflow packages macOS/Linux/Windows installers and checksums for tags.

## Required Pre-release Checks

```bash
python3 -m pytest -q
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

Real Feishu integration must use local config or environment variables for `FEISHU_APP_ID` and `FEISHU_APP_SECRET`. Do not commit App Secret, tenant token, real chat_id, or sensitive screenshots. Public screenshots must be checked for secrets and private conversation content before being added to the repository.

## Current Boundaries

Automated tests do not access real Feishu and do not start a real Hermes Gateway. Real integration remains a local/manual acceptance flow. After successful testing, record only redacted results; never commit credentials, real chat_id, or sensitive screenshots.
