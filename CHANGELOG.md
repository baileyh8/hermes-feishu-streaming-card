# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.2.0.html).

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
