# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.2.0.html).

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
