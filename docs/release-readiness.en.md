# Release Readiness

[中文](release-readiness.md) | [English](release-readiness.en.md)

Current package version: `3.9.0`. Building on the sidecar-only, V3.8.2 timeline, group diagnostics, topic/cron routing, Hermes compatibility, and WebSocket interaction work, this release adds an operations/reliability foundation: profile env/status route chains (PR #84, contributed by @Zanetach), bounded operations cards, safe repair, restart confirmation, and lifecycle cleanup. Normal streaming-card footer/layout is unchanged.

## Ready

- Hermes `v2026.4.23+` detection and fail-closed installation.
- Minimal Hermes hook, backup, manifest, restore, and uninstall.
- Sidecar `/events`, `/health`, and process `start/status/stop`.
- Feishu CardKit HTTP client, covered by mock Feishu server and real Feishu test app for tenant token, send, and update flows.
- Manual `smoke-feishu-card` command.
- E2E preview artifacts and generator.
- Real long-card stress test: one Feishu card updated to 16k Chinese characters.
- Real Hermes `v2026.4.23` `restore -> install` loop verification.
- Hermes `0.13.0+` / `0.14.0` / `0.15.x` / `0.17.x` / `0.18.x` / `v2026.5.16+` / `v2026.6.19+` / `v2026.7.1+` / `v2026.7.7.2` use the `gateway_run_013_plus` hook strategy, while older `v2026.4.x` keeps `legacy_gateway_run`.
- Feishu card button interactions are covered through local mock acceptance for `interaction.requested`, `/card/actions`, and `/interactions/{interaction_id}`; localhost/private sidecar text fallback is covered through `card.interaction_mode: text`.
- Feishu thread messages can carry optional `thread_id`; with a reply anchor, the sidecar uses the Feishu reply API to create the initial card in the original thread, and later updates keep PATCHing the same card.
- Cron delivery can extract chat ids from `deliver: "feishu:oc_xxx"` and can resolve `deliver: origin`, `deliver: all`, and `origin,all` through Feishu origins or scheduler targets, avoiding plain-text fallback for scheduled Feishu deliveries; `deliver: local` remains no delivery.
- Long Markdown tables and fenced code blocks over `MAIN_CONTENT_CHUNK_CHARS` are split as complete repeated structures to avoid raw Markdown rendering.
- Thinking/interim assistant messages use complete `append_block` chunks to avoid delta accumulation truncation or missing text.
- Runtime event sends, sidecar updates, and terminal PATCH calls are ordered/coalesced for the same message id.
- Newer Hermes streams that begin with `answer.delta`, `thinking.delta`, `tool.updated`, or `message.completed` without `message.started` still create the initial Feishu/Lark card.
- Native Hermes `Working` heartbeats, context-window/compression notices, automatic session resets, skill loading, and self-improvement reviews are normalized as `system.notice`; session notices prefer the active card timeline, while task-external notices use compact standalone cards.
- In Feishu/Lark topic replies, later `answer.delta`, `thinking.delta`, `tool.updated`, and `system.notice` events resolve through `reply_to_message_id` back to the same card even when Hermes uses a different internal streaming `message_id`, preventing frozen topic timelines and duplicate gray native notices.
- In Feishu/Lark topic groups that reuse the same `message_id` across consecutive turns, completed or failed old sessions are cleared and a fresh card is created; duplicate `message.started` events during an active turn still stay ignored to avoid accidental second cards.
- Gateway runtime coalesces high-frequency `thinking.delta` / `answer.delta` events inside the Hermes process, covering V3.8.1 issue #74 and reducing stream-reader thread pressure.
- Terminal events flush pending deltas for the same message before final card rendering.
- Feishu-side `/hfc help/status/doctor/monitor` commands return read-only diagnostic cards with hashed context ids.
- Accepted `/hfc` diagnostic commands ACK Hermes Gateway quickly and send the real Feishu/Lark card in the background, preventing `/hfc status` from double-sending a card plus the gray native `Unknown command /hfc` reply.
- Generic attachment summaries in completed cards no longer trigger native final-reply fallback; real `MEDIA:`, local file paths, and Hermes media/file locals still keep the native file/media delivery path available.
- Group `/hfc status` reports chat binding state, fallback/default routing, the suggested `bots bind-chat` command, and group slash-command behavior boundaries while real @robot and allowlist admission remains owned by Hermes Gateway.
- Pre-tool answers stay in the primary body first, then archive into the auxiliary timeline when the next answer or terminal event arrives; terminal cards strip already archived intermediate prefaces.
- Auxiliary timeline reasoning and tool details use separate text sizes and visual weight, while raw `thinking.delta` stays out of the user-visible timeline.
- Tool details can show argument summaries, duration, and failure reasons while keeping timeline rendering compact.
- Independent slash-command confirmations support Feishu command cards: `/new`, `/reset`, `/undo`, and high-cost `/model <model>` prompts render as standalone command cards when available.
- Feishu/Lark WebSocket long-connection deployments dynamically gain native `send_slash_confirm(...)` and `send_model_picker(...)` card support; button clicks route through `_on_card_action_trigger` back into Hermes' original handlers.
- When WebSocket-native cards are available, the sidecar `interaction.requested` pre-card is skipped so the same slash command does not show both a sidecar choice card and a native button card.
- No-argument `/model` selection can use a Feishu-only `send_model_picker(...)` card, call Hermes's callback, and update the same command card with the result.
- `/update` remains Hermes' background upgrade command and does not render an interactive command card; Hermes native text fallback remains available when the sidecar or final command-card update fails.
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
- Docker/source-stripped Hermes roots without `VERSION` and `.git` metadata can fall back to `gateway/run.py` anchors in `doctor`, `install`, and `setup`; diagnostics report `version_source: gateway anchors`. If version metadata exists but is unparseable, verifiable anchors allow diagnostics to report `VERSION + gateway anchors` or `git tag + gateway anchors` and continue.
- Hook import/emit failures remain fail-open but write `[hermes-feishu-card] hook failed: ...` diagnostic warnings to Hermes stderr.
- `repair --hermes-dir ... --yes` and `setup --repair` repair verifiable manifest/backup state and refuse unverifiable user edits.
- Structured attachment, media, and file objects keep card summaries while preserving Hermes native media/file delivery paths.
- `smoke-feishu-card --profile-id`, `bots test --profile-id`, CLI `status`, and `/health.routing.profiles` support profile-scoped troubleshooting.
- Hermes key release matrix covers `v2026.4.23`, `v2026.5.7`, `v2026.5.16+`, `v2026.5.29`, `v2026.6.19+`, `v2026.7.1+`, `v2026.7.7.2`, `0.13.x`, `0.14.x`, `0.15.x`, `0.17.x`, `0.18.x`, semantic versions with or without a `v` prefix, and descriptive version metadata.
- GitHub Actions Python 3.9 / 3.12 test matrix for PRs and pushes, plus Windows parser validation for `install.ps1`.
- Release assets workflow packages macOS/Linux/Windows installers and checksums for tags.
- V3.9.0 operations cards support diagnosis, recheck, two-step safe repair, and restart confirmation; private chats do not compare operators, while group repair/restart confirmation stays with the initiator. Use CLI fallback when the card is unavailable.
- The state-dir transport root automatically creates a private-permission transport secret. No secret configuration is required, and diagnostics/cards never output it.
- Setup resolves profile/event URL by explicit argument, process environment, selected env file, then default; `status`, `doctor`, and `/health` emit redacted route-chain/profile diagnostics.
- Install/setup can automatically repair known-safe state; `--no-repair` opts out, and unverifiable user edits remain refused. Cleanup history and metrics are bounded and hashed.
- Task 7 automated release gate: `1061 passed, 3 skipped`.

## Required Pre-release Checks

```bash
python3 -m pytest -q
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

Real Feishu integration must use local config or environment variables for `FEISHU_APP_ID` and `FEISHU_APP_SECRET`. Do not commit App Secret, tenant token, real chat_id, or sensitive screenshots. Public screenshots must be checked for secrets and private conversation content before being added to the repository.

## V3.9.0 Manual Acceptance Pending

- Existing-container Docker: fresh install, pinned upgrade, known-safe corrupt-marker auto-repair, user-edit refusal, main/child profile endpoint mapping, and final `doctor`. **Pending acceptance**.
- Real Feishu: private/group repair/restart, changed-operator rejection in groups, recheck, normal footer snapshot, topic, cron, and profile route mismatch. **Pending acceptance**.

After the approved tag, the release-assets workflow is expected to produce four assets (not created by this task): the macOS tarball, Linux tarball, Windows zip, and checksums file: `hermes-feishu-card-v3.9.0-macos.tar.gz`, `hermes-feishu-card-v3.9.0-linux.tar.gz`, `hermes-feishu-card-v3.9.0-windows.zip`, and `hermes-feishu-card-v3.9.0-checksums.txt`.

## Current Boundaries

Automated tests do not access real Feishu and do not start a real Hermes Gateway. Real integration remains a local/manual acceptance flow. After successful testing, record only redacted results; never commit credentials, real chat_id, or sensitive screenshots.
