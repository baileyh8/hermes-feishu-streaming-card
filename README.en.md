# Hermes Feishu Streaming Card Plugin

[中文](README.md) | [English](README.en.md)

<p align="center">
  <a href="https://github.com/baileyh8/hermes-feishu-streaming-card/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/baileyh8/hermes-feishu-streaming-card?style=for-the-badge&logo=github&label=Stars&color=2f80ed"></a>
  <a href="https://github.com/baileyh8/hermes-feishu-streaming-card/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/baileyh8/hermes-feishu-streaming-card?style=for-the-badge&logo=githubactions&label=Release&color=22c55e"></a>
  <a href="https://github.com/baileyh8/hermes-feishu-streaming-card/actions/workflows/tests.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/baileyh8/hermes-feishu-streaming-card/tests.yml?branch=main&style=for-the-badge&label=Tests&logo=githubactions"></a>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="Feishu/Lark" src="https://img.shields.io/badge/Feishu%20%2F%20Lark-Streaming%20Cards-00D6B4?style=for-the-badge">
  <img alt="Sidecar only" src="https://img.shields.io/badge/Runtime-Sidecar--only-7C3AED?style=for-the-badge">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/baileyh8/hermes-feishu-streaming-card?style=for-the-badge&color=64748b"></a>
</p>

![Hermes Feishu Streaming Card cover](docs/assets/readme-cover.png)

Hermes Feishu Streaming Card turns Hermes Agent Gateway replies in Feishu/Lark into one continuously updated interactive card. Reasoning, tool calls, final answers, approvals, choices, system notices, and runtime stats stay inside cards instead of spilling into scattered native gray text messages.

It targets the real pain points of running Hermes inside Feishu: missing or out-of-order streaming text, long tables/code blocks rendered as raw Markdown, invisible tool progress, manual approval replies, frozen topic timelines, multi-bot/profile troubleshooting, and uncertain hook compatibility after Hermes upgrades.

![Hermes Feishu card command interaction, command result feedback, and tool timeline showcase](docs/assets/feishu-card-showcase-v385.png)

## What You Get

- **One continuously updated Feishu card**: `thinking.delta`, `answer.delta`, `tool.updated`, and `message.completed` merge into one card.
- **Primary answer and process timeline**: the final answer stays in the main content area while pre-tool answers, tool calls, and system notices move into the "Reasoning and Tools" timeline.
- **In-card interactions**: approval and clarify choices render as buttons; standalone commands such as `/new`, `/reset`, `/undo`, and `/model` use native interactive cards.
- **Consistent topic replies**: later topic stream events resolve by `reply_to_message_id` and keep updating the original card; system notices no longer spill out as duplicates.
- **Long content protection**: long Markdown tables and fenced code blocks split on structure boundaries instead of raw character cuts.
- **Diagnostics and recovery**: `doctor`, `/hfc status`, `/health` metrics, runtime import checks, and safe repair/restore/uninstall cover common failures.

## Problems Solved

| Problem | What the plugin does |
|---|---|
| Feishu only shows a final wall of text | Streams reasoning, answer, tool status, and footer stats into one card |
| Hermes emits separate `Working`, compression, skill loading, or review messages | Classifies them as `system.notice` and routes them into the active card or a compact notice card |
| Topic replies show the first card but the timeline stops updating | Anchors topic events with `source.message_id` / `reply_to_message_id` |
| Approval, choices, and model switching require manual numbered replies | Uses Feishu buttons or dropdowns first, then falls back to text when cards are unavailable |
| Hermes upgrades make hook compatibility unclear | `doctor --explain` reports `version_source`, `hook_strategy`, `compatibility`, anchors, and recommendations |

## Quick Install

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.ps1 | iex
```

The installer installs or upgrades the plugin, reads or prompts for Feishu credentials, writes a local `.env`, and runs the integrated setup command:

```bash
python3 -m hermes_feishu_card.cli setup \
  --hermes-dir ~/.hermes/hermes-agent \
  --config ~/.hermes/config.yaml \
  --yes
```

Check the sidecar after install:

```bash
python3 -m hermes_feishu_card.cli status --config ~/.hermes/config.yaml
```

For Release packages, Docker, PEP 668, uv, and installer details, see [README-install.md](README-install.md) and the [full user guide](docs/user-guide.en.md).

## Minimal Config

Copy `config.yaml.example` locally and never commit real credentials.

```yaml
server:
  host: 127.0.0.1
  port: 8765
feishu:
  app_id: ""
  app_secret: ""
card:
  title: Hermes Agent
  footer_fields: [duration, model, input_tokens, output_tokens, context]
```

Feishu credentials can also live in a `.env` next to the config:

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_CONNECTION_MODE=websocket
FEISHU_HOME_CHANNEL=oc_xxx
```

Multi-bot routing, group chat bindings, multi-profile config, profile-aware routing, footer fields, and no-op client behavior are covered in the [full user guide](docs/user-guide.en.md#configuration).

## Hermes Streaming Config

Confirm `streaming.enabled` is `true`, and let Hermes use edit transport.

Make sure Hermes `config.yaml` enables streaming edits:

```yaml
streaming:
  enabled: true
  transport: edit
```

Do not set `display.platforms.feishu.streaming: false`. Do not treat `display.show_reasoning` as required for this plugin; it can append reasoning blocks to the final answer and disrupt the streaming card experience. The plugin consumes Hermes `thinking.delta` / `answer.delta` directly.

The compatibility matrix covers older Hermes starting at `v2026.4.23` and Hermes 0.13.0+/0.14.0/0.15.x/0.17.x/0.18.x. `doctor` prefers `VERSION` or Git tag `v2026.4.23+` when deciding support. After upgrading Hermes or this plugin, rerun `setup` or `install --hermes-dir ... --yes`.

## Docker Container Install

For an existing Hermes container:

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export HFC_VERSION=v3.8.9
bash install-docker.sh
```

Defaults:

| Variable | Default |
|---|---|
| `HERMES_DIR` | `/opt/hermes` |
| `HFC_CONFIG` | `/opt/data/config.yaml` |
| `HFC_ENV_FILE` | `/opt/data/.env` |
| `HFC_VERSION` | `latest` |

`docker-compose.example.yml` is an integration example, not an official image. Since V3.8.6, Docker/source-stripped Hermes roots without `VERSION` or `.git` can fall back to Gateway anchors and still choose `gateway_run_013_plus`.

## Common Commands

| Command | Purpose |
|---|---|
| `setup --hermes-dir ... --yes` | Configure, diagnose, install hook, and start sidecar |
| `doctor --config ... --hermes-dir ... --explain` | Diagnose Hermes version, runtime import, hook strategy, anchors, and recommendations |
| `install --hermes-dir ... --yes` | Install the plugin into Hermes runtime venv and patch Hermes |
| `repair --hermes-dir ... --yes` | Repair verifiable hook manifest/backup state |
| `restore --hermes-dir ... --yes` | Restore the original Hermes file |
| `start --config ...` / `status --config ...` / `stop --config ...` | Manage the sidecar process and `/health` checks |
| `smoke-feishu-card --profile-id ... --chat-id ...` | Send a real Feishu card smoke test |
| `bots list|show|add|remove|test` | Manage and test multi-bot routing |

High-frequency stream tuning usually needs no change. For DeepSeek burst, token-by-token, or long-context pressure:

| Variable | Default | Purpose |
|---|---:|---|
| `HERMES_FEISHU_CARD_DELTA_COALESCE_MS` | `250` | Max Gateway-side delta coalescing wait |
| `HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS` | `600` | Flush pending delta when this character budget is reached |
| `HERMES_FEISHU_CARD_DELTA_COALESCE_MAX_PENDING` | `128` | Pending delta session cap |

## Latest Releases

![Feishu topic reply card continuity and reasoning/tool timeline showcase](docs/assets/feishu-topic-card-showcase-v389.png)

| Version | Highlights |
|---|---|
| [v3.8.9](docs/release-notes-v3.8.9.md) | Feishu/Lark topic card continuity; `system.notice` no longer duplicates outside the card |
| [v3.8.8](docs/release-notes-v3.8.8.md) | Cardifies native Hermes notices: Working, context compression, skill loading, and self-improvement review |
| [v3.8.7](docs/release-notes-v3.8.7.md) | Newer Hermes streams can create cards even when `message.started` is missing |
| [v3.8.6](docs/release-notes-v3.8.6.md) | Docker/source-stripped Hermes can fall back from missing `VERSION` to Gateway anchors; Hermes v0.18.0 support |
| [v3.8.5](docs/release-notes-v3.8.5.md) | Direct command results for `/new`, `/model`, and similar commands stay in cards |

Full history: [CHANGELOG.md](CHANGELOG.md). Longer historical notes remain in the [full user guide](docs/user-guide.en.md#version-history).

## Architecture At A Glance

```text
Hermes Gateway
  -> minimal hook in gateway/run.py
     -> hermes_feishu_card.hook_runtime
        -> HTTP POST /events
           -> sidecar server
              -> CardSession state
              -> Feishu CardKit send/update
              -> retry / coalescing / metrics / /health
```

This is a sidecar-only design: the Hermes hook stays fail-open, while Feishu delivery, card updates, session state, retries, and diagnostics live in the sidecar. Historical V2 code is archived under `legacy/` and is not the active runtime.

## Documentation

- Full user guide: [中文](docs/user-guide.md) / [English](docs/user-guide.en.md)
- Installer package guide: [README-install.md](README-install.md)
- Architecture: [中文](docs/architecture.md) / [English](docs/architecture.en.md)
- Event protocol: [中文](docs/event-protocol.md) / [English](docs/event-protocol.en.md)
- Installer safety: [中文](docs/installer-safety.md) / [English](docs/installer-safety.en.md)
- Migration: [中文](docs/migration.md) / [English](docs/migration.en.md)
- E2E verification: [中文](docs/e2e-verification.md) / [English](docs/e2e-verification.en.md)
- Release readiness: [中文](docs/release-readiness.md) / [English](docs/release-readiness.en.md)
- Testing: [中文](docs/testing.md) / [English](docs/testing.en.md)
- Maintainer wiki: [docs/wiki](docs/wiki/README.md)

## Contributors

- [gischuck](https://github.com/gischuck) - [PR #12](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/12) Accept-Encoding fix
- [gischuck](https://github.com/gischuck) - [PR #76](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/76) reasoning/tool timeline UX proposal and implementation exploration
- [fengs2021](https://github.com/fengs2021) - [PR #17](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/17) lock optimization and update interval improvement

## Security

Do not commit App Secret, tenant token, real chat_id, or unredacted screenshots. Screenshots demonstrate card rendering only. Production credentials belong in local config or environment variables.

## License

MIT License. See [LICENSE](LICENSE).
