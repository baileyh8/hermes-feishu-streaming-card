# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.2.0.html).

## V3.7.0 — 2026-06-29

### Added
- issue #70: added `install-docker.sh` for existing Hermes Docker containers with `/opt/hermes`, `/opt/data`, and Hermes venv Python assumptions.
- Added `docker-compose.example.yml` as a non-official Compose example for bind/volume layout and non-interactive installer execution.
- Release packages now include Docker install assets.

### Tests
- Added Docker installer script coverage, Compose example checks, release packaging coverage, and docs assertions.

## V3.6.6 — 2026-06-26

### Fixed
- issue #67: terminal Hermes events now ACK before slow Feishu card PATCH calls finish, while the card update continues in the background. This prevents interrupted or backlogged sessions from making Hermes fall back to a duplicate native text reply while the streaming card still updates.
- issue #67: `emit_from_hermes_locals_async()` now reads the sidecar JSON response and only reports delivery when `ok` and `applied` are not false, so stale or unapplied terminal events no longer masquerade as successful card delivery.
- issue #68: when `--hermes-dir` points to a directory without `gateway/run.py`, Hermes detection reads `hermes -V`, extracts the CLI `Project:` path, and surfaces a concrete `Use --hermes-dir ...` recommendation in `doctor --explain` / install diagnostics.

### Tests
- Added regression coverage for slow terminal Feishu PATCH ACK behavior, sidecar `applied` handling in the Hermes async hook, and wrong `--hermes-dir` diagnostics using a mocked Hermes CLI.

## V3.6.5 — 2026-06-23

### Fixed
- issue #64: `gateway_run_013_plus` now emits `message.started` with the same Feishu reply anchor used by streaming callbacks, so thread sessions no longer split across different `message_id` values and increment `events_ignored` with `events_applied=0`.
- issue #65: completed-only / burst-output models such as DeepSeek can now backfill the final answer from `agent_result.final_response` when no `thinking.delta` or `answer.delta` events were emitted before `message.completed`.
- Added sidecar regression coverage proving a card created by `message.started` updates and completes correctly when the only content-bearing event is `message.completed`.

### Docs
- Added V3.6.5 release notes and refreshed install/readiness examples for the new tag.

## V3.6.4 — 2026-06-22

### Fixed
- issue #61: Feishu thread messages now keep the initial streaming card inside the originating thread by carrying `thread_id` through the event protocol and using the Feishu reply API with `reply_in_thread: true` when a reply anchor is available.
- issue #62: cron jobs with `deliver: "feishu:oc_xxx"` now parse the chat id from the `deliver` field, allowing scheduled Feishu deliveries to render as cards instead of falling back to plain text.

### Docs
- Added V3.6.4 release notes and documented the optional event `thread_id` field used for Feishu thread routing.

## V3.6.3 — 2026-06-21

### Fixed
- issue #59: patcher now prefers Hermes v0.17.0+ / `v2026.6.19+` `_run_agent_inner` when injecting streaming callbacks, so tool, answer, thinking, clarify, and approval hooks are no longer skipped when `_run_agent` is only a wrapper.
- issue #57: `card.interaction_mode: auto` now switches localhost/private sidecars to text-choice fallback, and the Hermes hook stops polling for unreachable Feishu Card Action callbacks in that mode.
- issue #56: non-Feishu platforms such as Telegram are ignored before runtime event construction, keeping native Telegram delivery untouched after hook installation.
- issue #58: Windows `HERMES_HOME` profile paths under both `hermes/profiles/<id>` and `.hermes/profiles/<id>` now resolve the correct profile id.
- Extracted PR #52's useful Windows/proxy fixes: local/private sidecar calls bypass system proxies while public sidecar URLs keep default proxy behavior, and sidecar PID stop/status uses a Windows-specific path instead of POSIX process groups.

### Docs
- Added V3.6.3 release notes, README compatibility guidance for Hermes v0.17.0+ / `v2026.6.19+`, and `card.interaction_mode` config documentation.

## V3.6.2 — 2026-06-16

### Fixed
- issue #53: `install` / `setup` now detects the Hermes Gateway runtime venv Python and installs `hermes-feishu-streaming-card` into that interpreter before patching `gateway/run.py`.
- Hermes hook import/emit failures are no longer completely silent; injected hook blocks still fail open, but now write a diagnostic `[hermes-feishu-card] hook failed: ...` warning to Hermes stderr.
- `doctor --json` and `doctor --explain` now report `runtime_import`, including whether Hermes runtime Python can import `hermes_feishu_card.hook_runtime`.

### Docs
- Documented Hermes venv deployment behavior and installer safety expectations in README and installer safety docs.
- Kept `.env` search expansion out of this release scope; it remains a separate follow-up item from the venv runtime installation fix.

## V3.6.1 — 2026-06-06

### Fixed
- issue #47: Hermes semver `VERSION` values without a `v` prefix, such as `0.15.1`, are now parsed correctly instead of being reported as unsupported.
- Hermes `0.15.x` / `v0.15.x` now uses the existing `gateway_run_013_plus` hook strategy when the required `gateway/run.py` anchors are present.

### Tests
- Added release-matrix coverage for `0.13.0`, `0.14.0`, `0.15.1`, and `v0.15.1`.
- Added a `doctor --explain` regression test for Hermes `0.15.1` without the `v` prefix.

## V3.6.0 — 2026-06-04

### Added
- Read-only `doctor --json` and `doctor --explain` diagnostics covering config, sidecar, Hermes version/anchors, streaming settings, install state, and actionable recommendations.
- Safe `repair --hermes-dir ... --yes` and `setup --repair` flows for verifiable hook state recovery without overwriting user edits.
- Structured attachment extraction for Hermes locals such as `attachments`, `files`, `media_files`, image/audio/video file objects, and URL/file dictionaries.
- Profile-scoped operations: `smoke-feishu-card --profile-id`, `bots test --profile-id`, clearer CLI `status` routing output, and `/health.routing.profiles`.
- Hermes compatibility release matrix coverage for `v2026.4.23`, `v2026.5.7`, `v2026.5.16+`, `v2026.5.29`, `0.13.x`, and `0.14.x`.
- `docs/release-notes-v3.6.0.md` and refreshed release-readiness docs for operations-focused publishing.

### Fixed
- Repairable missing manifest/backup states are now detected and explained instead of leaving users with opaque `run.py changed since install` failures.
- Cards retain attachment summaries while the hook keeps Hermes native media/file delivery paths unsuppressed.
- Multi-profile routing diagnostics now show profile-level bot counts, chat bindings, last route, last route error, and event counters.

### Tests
- Added regression coverage for doctor JSON/explain output, repair refusal/recovery paths, structured media/file events, profile-targeted smoke commands, health routing grouping, Hermes release matrix fixtures, release asset dry-run guards, and documentation constraints.

## V3.5.2 — 2026-06-04

### Added
- Cross-platform installers: `install.sh` for macOS/Linux and `install.ps1` for Windows PowerShell.
- One-line install entry points in the Chinese and English README homepages.
- GitHub Release asset packaging workflow for macOS/Linux tarballs, Windows zip packages, and SHA-256 checksums.
- `README-install.md` and `docs/release-notes-v3.5.2.md` for packaged installer usage and release publishing.
- V3.6.0 roadmap documentation focused on repair diagnostics, media/file delivery, multi-profile operations, and release/E2E matrices.

### Fixed
- `install.sh` no longer sources the whole `.env` file. It now reads only Feishu/sidecar-related variables, so unrelated values with spaces such as browser paths do not break macOS installs.
- `install.sh` detects uv/PEP 668 `externally-managed-environment` Python errors and retries pip installation with `--break-system-packages`, keeping the failure mode explicit while allowing one-line installs on uv-managed macOS Python.

### CI
- Added a Windows GitHub Actions job that parses `install.ps1` with PowerShell AST validation.
- Added installer regression tests for safe `.env` parsing and externally managed Python retry behavior.
- Added documentation tests for one-line install commands and Release asset workflow coverage.

## V3.5.1 — 2026-06-01

### Fixed
- Feishu card updates are now ordered end-to-end for the same message id, covering Hermes runtime sends, interaction requests, sidecar state updates, and terminal card patches so thinking/answer text no longer rolls back or truncates under backlog.
- Sidecar non-terminal updates are coalesced and acknowledged quickly while terminal events remain awaited, improving perceived streaming speed and preventing a long update backlog from outliving `message.completed`.
- Feishu JSON 2.0 interaction buttons now use direct `button` elements with `behaviors.callback`, fixing card PATCH failures when approval/choice buttons render inside an active card.
- Queued follow-up completions now emit `message.completed` into the card path and suppress native resend once the Feishu card is delivered, preventing final answers from spilling into gray plain-text messages.
- Runtime delta extraction preserves raw boundary spaces for `thinking.delta` and `answer.delta`, preventing sentence/code spacing loss while streaming.
- `load_config()` reads a `.env` file next to the selected config file before applying real process environment variables, preventing manual sidecar restarts from silently entering no-op mode when Feishu credentials live beside Hermes config.

### Docs
- Reorganized the Chinese README homepage around the V3.5.x value proposition, live user scenarios, installation/upgrade flow, troubleshooting, and version history.

### Tests
- Added regression coverage for ordered runtime sends, interaction event retries on transient sidecar state, Feishu JSON 2.0 button callback payloads, queued follow-up suppression, `.env` config fallback, and update coalescing.

## V3.5.0 — 2026-06-01

### Added
- Feishu card interaction loop for Hermes approval and choice prompts: `interaction.requested` renders buttons in the active card, `/card/actions` records the user's selection, and the Hermes hook polls `/interactions/{interaction_id}` so the original task can continue.
- Patcher support for Hermes `v0.14.0` / `v2026.5.16+` approval and clarify callbacks.

### Fixed
- issue #41: multi-reply/newer Hermes streaming flows keep final answers on the card path instead of falling back to native text after the first reply.
- PR #42: cron card delivery now prioritizes `job['deliver']` and scheduler-resolved Feishu targets over stale `origin.platform` metadata.
- Long single Markdown tables and fenced code blocks are split into valid repeated table/code chunks when they exceed `MAIN_CONTENT_CHUNK_CHARS`, preventing raw Markdown rendering in Feishu.
- Thinking/interim assistant text is emitted as complete `append_block` chunks so sentences are not truncated, glued, or dropped by delta-style accumulation.

### Tests
- Added regression coverage for interaction event parsing, session state, card buttons, Feishu callback resolution, Hermes hook polling, cron deliver precedence, long table/code chunking, and thinking append-block behavior.

## V3.4.3 — 2026-05-27

### Fixed
- issue #39: blank or whitespace-only `message.completed` answers no longer clear an answer that already arrived through `answer.delta`, preventing DeepSeek V4 Pro tool-call flows from ending with an empty Feishu card.
- Long Markdown card content is split at paragraph, table, and fenced-code boundaries instead of raw character offsets, so Feishu does not render split table/code fragments as broken raw Markdown.
- issue #34 follow-up: compatibility tests now cover Hermes `v0.14.0` / `v2026.5.16+` selecting the `gateway_run_013_plus` strategy, while `v2026.4.x` remains on `legacy_gateway_run`.

### Tests
- Added regression coverage for blank completed answers after streamed deltas, Markdown-aware card splitting, Hermes `v0.14.0`, and Hermes `v2026.4.30`.

## V3.4.2 — 2026-05-21

### Fixed
- issue #31: Feishu card PATCH updates are now serialized per session so older card snapshots cannot land after newer content and cause thinking/answer text to flicker or roll back.
- Concurrent Hermes callback events now allocate per-message sequence numbers under a lock, preventing duplicate sequence ids that could make valid `thinking.delta` / `answer.delta` chunks look stale.

### Tests
- Added regression coverage for out-of-order PATCH completion and concurrent runtime sequence allocation.

## V3.4.1 — 2026-05-14

### Fixed
- issue #25: Hermes v2026.5.7 started hooks now treat `event_message_id` as an explicit message id, keeping `message.started` and `message.completed` on the same card lifecycle.
- Fallback preview now reuses the active fallback cache, so `_preview_fallback_message_id` and `_create_active_fallback_message_id` do not drift when `created_at` is missing.

### Tests
- Added regression coverage for Hermes v2026.5.7-style started locals and untokened fallback preview/create lifecycle consistency.

## V3.4.0 — 2026-05-10

### Added
- Hermes 0.13+ compatibility strategy: installer and `doctor` select/report the `gateway_run_013_plus` hook strategy from Hermes version and code anchors.
- Per-bot/profile titles: card titles can be set globally, per profile, or per bot, with bot-level titles taking precedence.
- Cron final card delivery for scheduled Hermes runs.
- Attachment summaries with native media delivery, keeping summaries in cards while media uses Feishu-native delivery.
- Card reply context so reply cards retain the routing/context needed by the sidecar.

### Fixed
- issue #23: multi Hermes profile + multi Feishu bot deployments now preserve explicit profile identity and route to the intended bot.

### Compatibility
- Older Hermes strategy preserved: Hermes `v2026.4.23` through `0.12.x` continues to use `legacy_gateway_run`.
- `doctor` now exposes `hook_strategy`, `compatibility`, and anchor diagnostics to make install decisions auditable before writing hooks.

## [3.3.0] - 2026-05-01

### Fixed
- **#15 - COMPLETE_PATCH platform check**: `_render_complete_hook_block` and `_render_previous_async_complete_hook_block` now gate `return None` behind `source.platform.value == "feishu"`, preventing the complete hook from swallowing responses on QQ/WeChat/DingTalk etc. (`install/patcher.py`)
- **#18b - Tool count accuracy**: `CardSession.tool_count` now returns actual cumulative call count instead of deduplicated unique tool count. Added `_tool_call_count` field that increments on every `tool.updated` event. (`session.py`)
- **#10 - Card table limit**: Markdown tables exceeding Feishu's 5-table-per-card limit are now truncated with a notice appended. Added `count_markdown_tables()` and `MAX_CARD_TABLES` constant. (`text.py`, `render.py`)

### Added
- **#18a - DeepSeek `<thinking>` tag support**: `THINK_TAG_RE` and `THINK_TAGS` now include `<thinking>`/`</thinking>` tags alongside `<think>`/`</think>` for DeepSeek-compatible reasoning content normalization. (`text.py`)
- **#18c - Footer spinner animation**: Non-terminal card footer now shows a rotating braille spinner instead of static "生成中". Frame driven by `time.time()`, no extra API calls. (`render.py`)
- **#16 - Multi-profile support**: A single sidecar process can now serve multiple Hermes profiles with independent Feishu credentials, session isolation, and per-profile bot routing. Backward compatible — single profile behavior unchanged. (`config.py`, `runner.py`, `server.py`)

### Changed
- `_render_footer()`: "生成中" static text replaced with `_spinner_text("生成中")`
- `CardSession`: `_tool_call_count` field tracks actual call count; `tool_count` property reflects cumulative count while `tools` dict retains unique tool states
- `_render_complete_hook_block` / `_render_previous_async_complete_hook_block`: platform check added before `return None`
- `build_feishu_boundary()`: now detects profiles and delegates to `_build_multi_profile_boundary()` when configured
- `_apply_event_locked()`: uses composite `profile_id:message_id` session keys when profiles are active
- `_resolve_route()` / `_client_for_bot()`: profile-aware routing with dict-based factory selection

## [3.2.1] - 2026-04-29

### Fixed
- **HTTP Accept-Encoding header**: Add `Accept-Encoding: gzip, deflate` to Feishu API requests to avoid `ClientPayloadError: Can not decode content-encoding: br` when Feishu returns brotli-compressed responses (aiohttp limitation). Fix in `feishu_client.py` by setting request headers, not relying on server's `content-encoding` auto-decoding.

### Changed
- `feishu_client.py`: HTTP client now explicitly requests gzip/deflate encoding; brotli responses from Feishu are avoided at the server side by this header.

## [3.2.0] - 2026-04-29

### Added
- **Multi-bot registry**: `bots` section in config to define multiple Feishu bots with `app_id`/`app_secret`
- **Chat-to-bot bindings**: `bindings.chats` maps `chat_id` → `bot_id`, with `fallback_bot` for unbound sessions
- **Group rules framework**: `bindings.group_rules` section reserved for future group trigger filtering (V3.2 no-op)
- **Bot management CLI**: `hermes_feishu_card.cli bots` with `list`, `show`, `add`, `remove` commands
- **Sidecar routing diagnostics**: `/health.routing` exposes `bot_count`, `chat_binding_count`, `last_route`, `bots[]` details
- **Optional routing context extraction**: `hook_runtime._event_data()` now extracts `chat_type`, `tenant_key`, `agent_id`, `profile_id` from `message.started` for future features

### Changed
- `runner.py`: Uses `FeishuBoundary` with `BotRegistry.resolve()` to route events to bot-specific `FeishuClient`
- `server.py`: Adds bot lookup via `registry.resolve(RoutingContext(...))` before sending card updates
- `config.py`: Adds `bots`, `bindings`, `group_rules` schema validation with defaults
- `cli.py`: New `bots` command group with management subcommands and `--config` flag
- Package version: `3.1.0` → `3.2.0`

### Fixed
- `runner.py`: Ensure `NoopFeishuClient` path respects absent credentials without breaking
- `cli.py`: Default bot name resolution respects config-defined default item name
- `server.py`: Bot resolution gracefully falls back to `default_bot` when no binding matches

### Docs
- `README.md` / `README.en.md`: New "V3.2 多 bot 与群聊" section with config examples and CLI usage
- `config.yaml.example`: Full `bots` + `bindings` + `group_rules` sample
- Test suite updated to 398 tests (unit + integration coverage for bots, routing, config)

## [3.1.0] - 2026-04-XX

### Added
- Sidecar architecture: standalone aiohttp server for Feishu CardKit HTTP client
- Streaming card updates: `thinking.delta`, `answer.delta`, `tool.updated`, `message.completed/failed`
- Health endpoint (`/health`) with metrics and diagnostics
- Auto-recovery: retry with exponential backoff on transient failures
- Fail-open: Hermes continues with plain text if sidecar unavailable
- Installation wizard with version and structure guardrails
- Uninstall/restore hooks preserving user modifications

### Changed
- `feishu_streaming_card.mode: sidecar` in Hermes config (replaces `enabled: true`)
- Card rendering offloaded from Hermes process to sidecar
- Footer fields configurable via `card.footer_fields` (default: duration/model/tokens/context)

### Fixed
- Long card body splitting into multiple Markdown elements for 16k+ Chinese characters
- `<think>`/`</think>` tags stripped from streaming content
- Duplicate native text message suppression on completion

(Placeholder entries below for future minor/patch releases)

## [3.1.1] - TBD
- Patch notes...

## [3.0.0] - 2026-04-XX
Initial public release of the sidecar architecture. (Previous versions were v2.x monolith hook inside Hermes.)
