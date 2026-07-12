# Release Readiness

[中文](release-readiness.md) | [English](release-readiness.en.md)

Current release candidate: `4.0.0`. It adds a tool-preview runtime Header, public interim output in the body, and natural waiting/failed/completed transitions while reusing the existing Hermes hook, update queue, interaction security, and native reply boundary. V3.10.0 was released on 2026-07-11.

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
- Feishu card button interactions are covered through local mock acceptance for `interaction.requested`, `/card/actions`, and `/interactions/{interaction_id}`; localhost/private sidecars use WebSocket-native callbacks in default `auto` mode, while explicit `card.interaction_mode: text` retains numbered-text fallback.
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
- Setup resolves profile/event URL by explicit argument, process environment, selected env file, then default; only `doctor` shows the complete redacted identity/profile/event-endpoint route chain, `status` summarizes runtime routing/profile events, and `/health` reports actual routing-health fields.
- Install/setup can automatically repair known-safe state; `--no-repair` opts out, and unverifiable user edits remain refused. Cleanup history and metrics are bounded and hashed.
- Operations-card WebSocket callbacks ACK immediately, authenticated actions enter a bounded background queue with finite retry, and every authenticated state PATCHes the original card without making recheck/repair/restart wait for Feishu PATCH completion.
- Automated release gate: `1172 passed, 3 skipped` on Python 3.9 and Python 3.12. Operations semaphore/publish-lock state is initialized only inside the active event loop, preserving the declared Python 3.9 support.
- Real Feishu private-chat acceptance passed on 2026-07-11: `/hfc doctor` produced no gray native unknown-command reply; localized details and two consecutive rechecks (including the background successor) ACKed in 156–201 ms without a target-callback timeout toast and updated the same card; sandboxed two-step safe repair, card-triggered Gateway restart, and the normal completed-card footer passed with zero sidecar send/update failures.
- V3.9.1 regression coverage includes completed-answer boundaries, interrupted terminal ordering, asynchronous model-picker callbacks, loopback no-proxy behavior, marker-only recovery, and refusal of unknown edits.
- V3.9.1 automated release gate: `1198 passed, 3 skipped` on both Python 3.9 and Python 3.12, followed by `git diff --check`.
- V3.10.0 bare `/resume` picker tests cover the original Hermes handler, group initiator, topic metadata, expired/invalid state, fail-open behavior, and immediate ACK.
- V3.10.0 footer tests prove only the escaped model label changes color; element ids, field order, separators, text size, and non-completed states remain unchanged.
- V4.0.0 combines Hermes tool names and `tool.updated.detail` into deterministic non-completed Header action summaries and streams public `thinking.delta` independently in the body; final `answer.delta` remains the primary body content.

## Required Pre-release Checks

```bash
python3 -m pytest -q
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

Real Feishu integration must use local config or environment variables for `FEISHU_APP_ID` and `FEISHU_APP_SECRET`. Do not commit App Secret, tenant token, real chat_id, or sensitive screenshots. Public screenshots must be checked for secrets and private conversation content before being added to the repository.

## V3.9.0 Manual Acceptance Progress

- Existing-container Docker: fresh install, pinned upgrade, known-safe corrupt-marker auto-repair, user-edit refusal, main/child profile endpoint mapping, and final `doctor`. **Pending acceptance**.
- Real Feishu private chat: `/hfc doctor`, localized details, recheck, a second click from the background successor, same-card PATCH, sandboxed two-step safe repair, card-triggered Gateway restart, and the normal footer snapshot. **Passed on 2026-07-11**.
- Real Feishu cron: a no-agent one-shot result reached a normal completed card; sidecar event receive/apply/card-send metrics succeeded with no fallback. **Passed on 2026-07-11**.
- Profile route mismatch: a temporary invalid `HERMES_FEISHU_CARD_PROFILE_ID` produced a redacted `profile_unknown` route chain, and removing the temporary environment restored the default profile without changing persistent config. **Passed on 2026-07-11**.
- V3.10.0 real Feishu `/resume`: private chat, group initiator, topic placement, and same-card PATCH passed; changed-operator rejection remains automation-backed because the test group had one human participant.

Acceptance also exposed an upstream Hermes `cron run` status-reporting bug: a successful finite one-shot can print `Ran now: failed` because Hermes re-reads `last_status` after the completed job record has already been deleted. This does not indicate a card-delivery failure; the acceptance decision uses the matching Feishu card, sidecar metrics, and saved cron output. The plugin deliberately does not add another source patch for Hermes `tools/cronjob_tools.py` just to mask this upstream CLI issue.

## V3.9.1 Release Gates

- Python 3.9 / 3.12 full automation: **passed (`1198 passed, 3 skipped`)**.
- `git diff --check`: **passed**.
- Real Feishu focus: model-picker callbacks, interrupted terminal cards, and completed-answer preservation follow the [Feishu acceptance checklist](wiki/feishu-acceptance.md); public evidence remains redacted.
- Release assets: verify macOS, Linux, Windows, and checksums after tagging.

## V3.10.0 Release Gates

- Focused interaction/installer/render matrix: **passed (`416 passed`)**.
- Python 3.9 / 3.12 full automation: **passed (`1216 passed, 3 skipped`)**.
- Real Feishu: private chat, group initiator, topic same-thread update, and footer passed; changed-operator rejection is covered by automation.
- Verify macOS, Linux, Windows, and checksums assets after tagging.
- `v3.10.0`: **released on 2026-07-11** with all four assets verified.

## V4.0.0 Release Gates

- Session/render/status focused tests: **passed (`139 passed`)**.
- Server/hook/model-picker hot-path matrix: **passed (`341 passed`)**.
- Private/group real-Feishu four-state acceptance: **passed on 2026-07-12**. Running, waiting, failed, and completed states updated one card in place; runtime action summaries remained independent from public interim output; non-completed footers contained status only; completed cards kept the native reply quote without a duplicate Card JSON Header; no gray native duplicate or callback timeout appeared.
- Real Feishu `/model`: **passed on 2026-07-12**. Provider and model data came directly from the same upstream Hermes CLI picker list; provider navigation, Back, model switching, and same-card result updates all succeeded.
- All four public screenshots: **passed privacy and visual review**, retaining only redacted real-Feishu card regions.
- Full automation: **passed (`1252 passed, 3 skipped`)**; `git diff --check` passed.
- Local release-package smoke: **passed**. The sdist and wheel built successfully, a clean Python 3.12 venv imported version `4.0.0`, and the Hermes `v2026.7.7.2` doctor confirmed runtime import, streaming, and install state.
- Verify macOS, Linux, Windows, and checksums assets after tagging.

The `v3.9.0` release-assets workflow publishes four assets: the macOS tarball, Linux tarball, Windows zip, and checksums file: `hermes-feishu-card-v3.9.0-macos.tar.gz`, `hermes-feishu-card-v3.9.0-linux.tar.gz`, `hermes-feishu-card-v3.9.0-windows.zip`, and `hermes-feishu-card-v3.9.0-checksums.txt`.

## Current Boundaries

Automated tests do not access real Feishu and do not start a real Hermes Gateway. Real integration remains a local/manual acceptance flow. After successful testing, record only redacted results; never commit credentials, real chat_id, or sensitive screenshots.
