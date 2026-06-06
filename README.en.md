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

Hermes Feishu Streaming Card turns Hermes Agent Gateway replies in Feishu/Lark into one continuously updated interactive card. Reasoning, tool calls, final answers, approvals, choices, and runtime stats stay in one readable card instead of spilling into scattered native text messages.

It targets the real pain points of using Hermes inside Feishu: missing or out-of-order streaming text, long tables/code blocks rendered as raw Markdown, invisible tool progress, manual approval replies, sidecar troubleshooting, multi-bot/profile routing, and uncertain hook compatibility after Hermes upgrades.

![Real Feishu streaming card screenshot](docs/assets/feishu-weather-card.png)

## Project Highlights

- **Streaming card UX**: `thinking.delta`, `answer.delta`, `tool.updated`, and terminal events update one Feishu card.
- **In-card interactions**: Hermes approval and clarify choices become Feishu buttons; clicks continue the original task.
- **Long content protection**: Markdown tables and fenced code blocks split on structure boundaries instead of raw character cuts.
- **Multi-bot / multi-profile**: bot registry, chat bindings, profile-aware session keys, titles, and routing diagnostics.
- **sidecar-only runtime**: Hermes hook stays fail-open while Feishu delivery, session state, retries, and health checks live in the sidecar.
- **Install and release friendly**: one-line installers, Release packages, `doctor`, `start/status/stop`, and safe restore/uninstall flows.

## Pain Points Solved

| Pain point | Project capability |
|---|---|
| Feishu only shows a final wall of text | Reasoning, answer, tool status, and runtime footer stream into one card |
| Tool-heavy runs lose text, reorder chunks, or spill native gray messages | per-message ordering, PATCH coalescing, terminal priority, and native resend suppression |
| Approval or choice prompts require manual text replies | Feishu buttons record the choice and continue the Hermes task |
| Long tables/code blocks render as raw Markdown | Markdown-aware table/code splitting with repeated headers and complete fences |
| Multi-bot, group, and profile routing is hard to inspect | `bindings.chats`, profile-aware sessions, and `/health.routing` diagnostics |
| Hook or sidecar failures are hard to debug | `doctor`, `/health` metrics, fail-closed installer, restore/uninstall |

## V3.6.1 Compatibility Patch

V3.6.1 fixes issue #47: Hermes `VERSION` values without a leading `v`, such as `0.15.1`, are no longer reported as unsupported by `doctor --explain`. Hermes `0.15.x` / `v0.15.x` continues to use `gateway_run_013_plus` when the required `gateway/run.py` anchors are present.

Full release notes: [docs/release-notes-v3.6.1.md](docs/release-notes-v3.6.1.md).

## V3.6.0 Operations Upgrade

V3.6.0 focuses on real deployment operations after the streaming-card baseline is already in place: diagnosing hook/sidecar/Hermes state, repairing safe installer drift, keeping media/file delivery visible, and making multi-profile routing easier to verify.

- **Readable and machine-readable diagnostics**: `doctor --explain` summarizes the next action for humans, while `doctor --json` reports config, sidecar, Hermes, streaming, install state, and recommendations.
- **Safe installer repair**: `repair --hermes-dir ... --yes` and `setup --repair` rebuild only verifiable manifest/backup state and refuse unverifiable user edits.
- **Media/file safety**: structured Hermes `attachments`, `files`, `media_files`, and image/audio/video objects are summarized in cards while native Hermes media/file delivery remains unsuppressed.
- **Multi-profile operations**: `smoke-feishu-card --profile-id`, `bots test --profile-id`, CLI `status`, and `/health.routing.profiles` expose profile-scoped routing state.
- **Compatibility matrix**: tests cover Hermes `v2026.4.23`, `v2026.5.7`, `v2026.5.16+`, `v2026.5.29`, `0.13.x`, `0.14.x`, `0.15.x`, and semver `VERSION` values with or without a `v` prefix.

Full release notes: [docs/release-notes-v3.6.0.md](docs/release-notes-v3.6.0.md).

## V3.5.x Runtime Baseline

- **End-to-end ordering for one card**: runtime sends, sidecar updates, and terminal Feishu PATCH calls are ordered/coalesced by message id, reducing thinking/answer truncation under backlog.
- **Faster streaming updates**: non-terminal events ACK quickly and card updates are coalesced; terminal events remain awaited so completed cards land before the task finishes.
- **Feishu JSON 2.0 buttons fixed**: interaction buttons now use direct `button` elements with `behaviors.callback`, avoiding PATCH failures in active cards.
- **Queued follow-up native text suppression**: queued completions emit `message.completed` into the card path and suppress native resend once the Feishu card is delivered.
- **`.env` credential fallback**: `load_config()` reads `.env` next to the selected config file, so manual sidecar restarts do not silently enter no-op mode when credentials live beside Hermes config.
- **Chinese README homepage reorganized**: the homepage leads with user scenarios, V3.5.x value, installation, troubleshooting, and release history.
- **V3.6.0 operations polish**: profile-targeted smoke checks, routing profile diagnostics, structured attachment summaries, and safe repair build on the V3.5.x baseline.

## V3.5.0 Feature Highlights

- **Feishu button interaction loop**: Hermes approval and choice prompts are rendered inside the same streaming card; clicking a button records the choice in the sidecar, lets the Hermes hook continue, and updates the original card.
- **issue #41 fixed**: multi-reply and newer Hermes streaming flows keep using card updates, so final answers no longer fall back to native text after the first reply.
- **PR #42 handled**: cron card routing now prefers `job['deliver']` and scheduler-resolved Feishu targets, so jobs migrated from Discord/Telegram are not skipped because of stale `origin.platform`.
- **Long table/code protection improved**: a single Markdown table or fenced code block longer than `MAIN_CONTENT_CHUNK_CHARS` is split into multiple still-valid Markdown blocks instead of raw fragments.
- **thinking truncation fixed**: Hermes interim assistant callbacks are treated as complete thinking blocks via `thinking.delta(mode=append_block)`, preventing missing characters or glued sentences.
- **Hermes `v0.14.0` / `v2026.5.16+` support remains verified**: the installer selects `gateway_run_013_plus`; `v2026.4.x` remains on `legacy_gateway_run`.
- **issue #39 fixed**: whitespace-only `message.completed.answer` values no longer clear answers already streamed through `answer.delta`, preventing DeepSeek V4 Pro tool-call flows from ending with an empty Feishu card.
- **issue #31 fixed**: PATCH updates for the same Feishu card are serialized per session, preventing older snapshots from landing after newer content and making thinking/answer text flicker, truncate, or roll back.
- **safer concurrent event sequences**: runtime sequence allocation is locked so concurrent Hermes callbacks cannot produce duplicate sequence ids for valid `thinking.delta` / `answer.delta` chunks.
- **issue #25 fixed**: Hermes v2026.5.7 started hooks now treat `event_message_id` as the explicit `message_id`, preventing `message.started` and `message.completed` fallback card ids from diverging.
- **Hermes 0.13.0+/0.14.0 and later**: the installer selects the `gateway_run_013_plus` hook strategy from the Hermes version and code anchors.
- **older Hermes remains supported**: Hermes `v2026.4.23` through `0.12.x` continues to use the `legacy_gateway_run` strategy; no plugin downgrade is required.
- **doctor reports compatibility**: `doctor` prints `hook_strategy`, `compatibility`, and anchor detection results so you can confirm the selected path.
- **Reinstall the hook after upgrading**: run `install --hermes-dir ... --yes` after upgrading so Hermes uses the matching hook implementation.
- **issue #23 fixed**: multi Hermes profile + multi Feishu bot deployments explicitly identify profile ids and route to the matching bot.
- **Multi-profile / multi-bot polish**: per-bot/profile title, cron final cards, attachment summaries + native media delivery, and reply card context.

## One-Line Install

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.ps1 | iex
```

The installer installs or upgrades the package, reads or prompts for Feishu credentials, writes `~/.hermes/.env`, and runs the integrated setup command:

```bash
python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --config ~/.hermes/config.yaml --yes
```

After installation:

```bash
python3 -m hermes_feishu_card.cli status --config ~/.hermes/config.yaml
```

Common environment variables:

| Variable | Default | Description |
|---|---|---|
| `HFC_VERSION` | `latest` | Version to install, such as `v3.6.1` or `main` |
| `HERMES_DIR` | `~/.hermes/hermes-agent` | Hermes Agent Gateway directory |
| `HFC_CONFIG` | `~/.hermes/config.yaml` | sidecar config path |
| `HFC_ENV_FILE` | `.env` next to `HFC_CONFIG` | Feishu credential file |
| `HFC_SKIP_START` | `0` | Set to `1` to install the hook without starting sidecar |
| `HFC_NO_PROMPT` | `0` | Set to `1` for non-interactive automation |

GitHub Releases also include `hermes-feishu-card-<version>-macos.tar.gz`, `hermes-feishu-card-<version>-linux.tar.gz`, and `hermes-feishu-card-<version>-windows.zip`. Download one, extract it, and run `install.sh` or `install.ps1`. See [README-install.md](README-install.md) for package details.

## Manual Install

```bash
git clone https://github.com/baileyh8/hermes-feishu-streaming-card.git
cd hermes-feishu-streaming-card && pip install -e ".[test]"
export FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx
python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --yes
```

`setup` generates config, validates Hermes (older Hermes from `v2026.4.23` through `v2026.4.x`, plus Hermes `0.13.0+`, `0.14.0`, `0.15.x` / `v2026.5.16+` anchors), installs the hook, starts the sidecar, and checks health — all in one pass. Hermes semantic `VERSION` values may include or omit the `v` prefix.

## Core Features

- **Multi-profile in-process** (new in V3.3.0): one sidecar serves multiple Hermes profiles with `profile_id:message_id` composite keys for session isolation and per-profile credentials/bot routing
- **Multi-bot routing & group chat**: register bots in `bots.items`, map `bindings.chats` to `chat_id`, fallback/default bot for unmatched sessions
- **Profile / bot card titles**: global, profile, and bot card titles are supported, with bot-level titles taking precedence
- **Streaming thinking**: renders `thinking.delta`, filters `<think>`/`</think>` and DeepSeek `<thinking>`/`</thinking>` tags
- **Progressive answer**: streams `answer.delta` into one card, replaces thinking on completion
- **Approval/choice buttons**: Hermes approval and clarify choices can be rendered as buttons in the same Feishu card, and clicks continue the original Hermes task
- **Cron final cards and reply context**: cron jobs can deliver final cards, and reply cards preserve the needed context
- **Attachment summaries and native media delivery**: cards show attachment summaries while the hook keeps Hermes native media/file delivery paths unsuppressed
- **Tool call tracking**: `tool.updated` shows cumulative call count and status
- **Runtime footer**: duration, model, tokens, context %. Non-terminal cards show a rotating braille spinner
- **Table limit protection** (new in V3.3.0): auto-truncates tables exceeding Feishu's 5-table limit with a notice appended
- **Platform check fix** (new in V3.3.0): non-Feishu platforms no longer swallowed by the complete hook
- **Fault isolation**: sidecar unavailable → Hermes hook fail-open, native text continues working
- **Safe installer**: fail-closed, checks version and code structure before writing. `restore`/`uninstall` refuse on modified files

## Upgrading

Upgrading from V3.2.x/V3.3.0/V3.4.x/V3.5.x/V3.6.0 to V3.6.1 is backward-compatible. **Single-profile configs need no changes.** If you are only upgrading from V3.6.0 to V3.6.1, update the package and rerun `doctor`; reinstall the Hermes hook only if the reported hook strategy should change.

```bash
# 1. Stop sidecar
python3 -m hermes_feishu_card.cli stop --config ~/.hermes_feishu_card/config.yaml

# 2. Update code
cd /path/to/hermes-feishu-streaming-card
git checkout v3.6.1 && pip install -e ".[test]" --upgrade

# 3. Diagnose Hermes hook strategy and anchors
python3 -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml --hermes-dir ~/.hermes/hermes-agent

# 4. Reinstall hook when hook_strategy should change
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes

# 5. Start sidecar
python3 -m hermes_feishu_card.cli start --config ~/.hermes_feishu_card/config.yaml
```

**Using multi-profile**: add a `profiles` section to `config.yaml` (see multi-profile config example below) with per-profile `feishu.app_id`/`app_secret`. Environment variables `FEISHU_APP_ID`/`FEISHU_APP_SECRET` are ignored in multi-profile mode.

**Rolling back to V3.2**: stop sidecar, `git checkout v3.2.1`, reinstall `pip`, restore backed-up `config.yaml`, re-run `install` + `start`.

## Configuration

Copy `config.yaml.example` locally. Never commit real credentials. Three common setups:

**Single Profile (minimal)** — quickest way to start:

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

**Single Profile + Multi-bot** — register bots, route by chat_id:

```yaml
server:
  host: 127.0.0.1
  port: 8765
feishu:
  app_id: ""
  app_secret: ""          # fallback only
bots:
  default: default
  items:
    sales:
      app_id: "cli_sales_xxx"
      app_secret: "xxx"
    support:
      app_id: "cli_support_yyy"
      app_secret: "yyy"
bindings:
  fallback_bot: default
  chats:
    oc_5cc6a25d8815790fa890dd0226005e83: sales
  group_rules:
    enabled: false
card:
  title: Hermes Agent
  footer_fields: [duration, model, input_tokens, output_tokens, context]
```

**Multi-profile** (new in V3.3.0) — one sidecar, multiple Hermes instances, isolated per profile:

```yaml
server:
  host: 127.0.0.1
  port: 8765
profiles:
  engineering:
    feishu:
      app_id: "cli_eng_xxx"
      app_secret: "xxx"
    bots:
      default: default
      items:
        default:
          app_id: "cli_eng_xxx"
          app_secret: "xxx"
    bindings:
      fallback_bot: default
      chats: {}
  sales:
    feishu:
      app_id: "cli_sales_xxx"
      app_secret: "xxx"
    bots:
      default: default
      items:
        default:
          app_id: "cli_sales_xxx"
          app_secret: "xxx"
    bindings:
      fallback_bot: default
      chats: {}
  group_rules:
    enabled: false
card:
  title: Hermes Agent
  footer_fields: [duration, model, input_tokens, output_tokens, context]
```

In multi-profile mode, `FEISHU_APP_ID`/`FEISHU_APP_SECRET` env vars are ignored. `footer_fields` accepts: `duration`, `model`, `input_tokens`, `output_tokens`, `context`.

## Feishu App Setup

```bash
export FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx
# Real Feishu smoke test:
python3 -m hermes_feishu_card.cli smoke-feishu-card --config config.yaml.example --chat-id oc_xxx
```

## Hermes Gateway Streaming And Thinking

Ensure Hermes `config.yaml` has `streaming.enabled: true` and `streaming.transport: edit`. Avoid `display.platforms.feishu.streaming: false`. Do not treat `display.show_reasoning` as required — it may prepend a reasoning code block to the final text, interfering with the card streaming experience. If the model only returns final answers (no thinking deltas), the card shows the final answer directly.

## CLI Commands

| Command | Description |
|---------|-------------|
| `setup --hermes-dir ... --yes` | One-shot install (config, check, hook, sidecar, health) |
| `doctor --config ... --hermes-dir ...` | Diagnostics: `version_source`, `version`, `minimum_supported_version`, `run_py_exists`, `hook_strategy`, `compatibility`, anchors, `reason`; supports `--explain` / `--json` |
| `install --hermes-dir ... --yes` | Install hook into Hermes |
| `repair --hermes-dir ... --yes` | Repair verifiable hook manifest/backup state without overwriting user edits |
| `restore --hermes-dir ... --yes` | Restore original Hermes files |
| `uninstall --hermes-dir ... --yes` | Uninstall and restore |
| `start --config ...` | Start sidecar |
| `stop --config ...` | Stop sidecar (validates PID/token against `/health` `process_pid/process_token`) |
| `status --config ...` | Sidecar status, routing, profile diagnostics, and metrics |
| `smoke-feishu-card --profile-id ... --chat-id ...` | Send a real Feishu smoke card for a specific profile |
| `bots list|show|add|remove --config ...` | Manage bot registry |
| `bots test --profile-id ... --chat-id ...` | Run a real Feishu bot smoke for a specific profile/bot |
| `bots bind-chat|unbind-chat --config ...` | Manage chat bindings |

## Architecture

```text
Hermes Gateway
  └─ minimal hook in gateway/run.py
       └─ hermes_feishu_card.hook_runtime
            └─ HTTP POST /events ——→  sidecar server
                                      ├─ CardSession state machine
                                      ├─ render_card() card rendering
                                      ├─ FeishuClient tenant token / send / update
                                      ├─ throttling, retry, locks, diagnostics
                                      └─ /health metrics
```

The Hermes hook converts `message.started` / `thinking.delta` / `answer.delta` / `tool.updated` / `message.completed` / `message.failed` into `SidecarEvent` and forwards to the sidecar. The sidecar owns full session state and the Feishu CardKit boundary — independently testable, restartable, and diagnosable. Historical code is archived under `legacy/` (`installer_v2.py`, `gateway_run_patch.py`, `patch_feishu.py`, etc.) — not the active runtime. Current development uses `hermes_feishu_card/`. See [docs/migration.en.md](docs/migration.en.md).

## FAQ

- **No thinking / not streaming**: check Hermes `streaming.enabled: true` + `streaming.transport: edit`, confirm model exposes reasoning deltas. Don't blindly enable `show_reasoning`.
- **No real Feishu cards**: without credentials, the sidecar uses a no-op client. In multi-profile mode, check each profile's `feishu` config.
- **Duplicate cards**: inspect `/health` metrics (`events_received`, `feishu_send_successes`). V3.3.0 per-message lock + `profile_id:message_id` keys ensure one card per message.
- **Multi-profile route is unclear**: run `status --config ...` and inspect `routing.last_route`, `profile.<id>.events`, and `profile.<id>.last_profile_source`, then verify directly with `smoke-feishu-card --profile-id ...` or `bots test --profile-id ...`.
- **Gray native text**: after sidecar accepts `message.completed`, Hermes hook suppresses native text; fail-open on sidecar unavailable. V3.3.0 fixes non-Feishu platforms being swallowed.
- **`doctor` unsupported**: Hermes ≥ `v2026.4.23` (reads `VERSION` or Git tag `v2026.4.23+`), `gateway/run.py` must exist.
- **No cards after upgrading Hermes 0.13.0+/0.14.0/0.15.x**: run `doctor --config ... --hermes-dir ...` to inspect `hook_strategy`, `compatibility`, and anchors, then re-run `install --hermes-dir ... --yes` if needed.
- **Restore fails**: file modified → `restore`/`uninstall` refuse to overwrite. Run `doctor --explain` to inspect manifest/backup/run.py state; if it reports an automatic repair path, run `repair --hermes-dir ... --yes`, otherwise back up and manually diff.
- **Footer tokens wrong**: abnormal values filtered; if still wrong, inspect Hermes `tokens`/`context` metadata.
- **Table limit exceeded**: V3.3.0 auto-truncates >5 tables with a notice. Reduce Markdown tables.

## Version History

| Version | Date | Highlights |
|---------|------|-----------|
| [v3.6.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.1) | 2026-06 | Fixes issue #47, supports Hermes `0.15.x` and no-`v` VERSION values so `doctor --explain` no longer reports them unsupported |
| [v3.6.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.0) | 2026-06 | `doctor --json/--explain`, safe `repair`, structured media/file summaries, profile-targeted smoke checks, routing profile diagnostics, Hermes compatibility matrix |
| [v3.5.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.5.2) | 2026-06 | Cross-platform one-line installers, Release packages, safer macOS `.env` parsing, uv/PEP 668 Python install handling, Windows installer CI parser validation |
| [v3.5.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.5.1) | 2026-06 | Streaming update ordering/coalescing, Feishu JSON 2.0 button fix, queued follow-up suppression, `.env` credential fallback, Chinese README refresh |
| [v3.5.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.5.0) | 2026-06 | Feishu button interaction loop, issue #41, PR #42, long table/code splitting, thinking truncation fix |
| [v3.4.3](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.4.3) | 2026-05 | Fixes issue #39, adds structure-aware Markdown splitting, and verifies Hermes v0.14.0/v2026.5.16+ support |
| [v3.4.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.4.2) | 2026-05 | Fixes issue #31, preventing concurrent PATCH and sequence races from rolling back or dropping streaming card text |
| [v3.4.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.4.1) | 2026-05 | Fixes issue #25, keeping Hermes v2026.5.7 fallback message ids lifecycle-stable |
| [v3.4.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.4.0) | 2026-05 | Hermes 0.13.0+ compatibility, older Hermes strategy preserved, issue #23, multi-profile/multi-bot, attachments and reply context |
| [v3.3.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.3.0) | 2026-05 | Multi-profile, DeepSeek compat, table protection, footer spinner, platform fix |
| [v3.2.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.2.1) | 2026-04 | Accept-Encoding fix |
| [v3.2.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.2.0) | 2026-04 | Multi-bot routing, group chat bindings, Bot CLI, routing diagnostics |
| [v3.1.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.1.0) | 2026-04 | Sidecar architecture, streaming cards, health endpoint, install wizard |
| [v3.0.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.0.0) | 2026-04 | Initial sidecar-only release (migrated from V2.x monolith hook) |

Full changelog: [CHANGELOG.md](CHANGELOG.md).

## Testing

```bash
python3 -m pytest -q    # run the full automated regression suite
```

Coverage: real Hermes Gateway E2E, real Feishu app card verification, 16k long-card stress test, `doctor → install → restore` loop, multi-profile routing, DeepSeek tag filtering.

## Documentation

- Architecture: [中文](docs/architecture.md) / [English](docs/architecture.en.md)
- Event protocol: [中文](docs/event-protocol.md) / [English](docs/event-protocol.en.md)
- Installer safety: [中文](docs/installer-safety.md) / [English](docs/installer-safety.en.md)
- Migration: [中文](docs/migration.md) / [English](docs/migration.en.md)
- E2E verification: [中文](docs/e2e-verification.md) / [English](docs/e2e-verification.en.md)
- Release readiness: [中文](docs/release-readiness.md) / [English](docs/release-readiness.en.md)
- Testing: [中文](docs/testing.md) / [English](docs/testing.en.md)

## License

MIT License. See [LICENSE](LICENSE).

## Contributors

Thanks to these contributors for improving the project:

- [gischuck](https://github.com/gischuck) — [PR #12](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/12) Accept-Encoding fix (V3.2.1 brotli compatibility)
- [fengs2021](https://github.com/fengs2021) — [PR #17](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/17) lock optimization and update interval improvement (V3.3.0)

## Security

Do not commit App Secret, tenant token, or real chat_id. Screenshots demonstrate card rendering only. Production credentials belong in local config or environment variables.
