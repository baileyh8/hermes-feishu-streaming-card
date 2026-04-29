# Hermes Feishu Streaming Card Plugin V3.2.0

[中文](README.md) | [English](README.en.md)

![Hermes Feishu Streaming Card cover](docs/assets/readme-cover.png)

Hermes Feishu Streaming Card adds stable streaming card messages to the Feishu/Lark platform adapter in Hermes Agent Gateway. V3.2.0 uses a **sidecar-only** architecture: Hermes receives only a minimal hook, while Feishu CardKit rendering, session state, update throttling, retries, health metrics, and fault isolation live in an independent sidecar process.

The current release has completed the real Feishu E2E main flow: each new user message creates a new card, thinking and final answers update progressively in that same card, tool calls are tracked in real time, the completed card shows duration/model/token/context metadata, and Hermes no longer emits duplicate gray native text messages after the card is delivered.

The Feishu CardKit HTTP client is implemented and covered by a mock Feishu server, real Feishu smoke tests, real Hermes Gateway E2E testing, and long-card stress testing.

Real card screenshot:

![Real Feishu streaming card screenshot](docs/assets/feishu-weather-card.png)

## Core Features

- Streaming thinking: accumulates `thinking.delta` content and filters `<think>` / `</think>` tags.
- Progressive answer updates: streams `answer.delta` into one card and replaces thinking content with the final answer on completion.
- Tool call tracking: supports `tool.updated`, real-time tool counts/status, and final total counts.
- Final-state convergence: handles `message.completed` / `message.failed`; normal card states stay simple: thinking or completed.
- Runtime footer: shows duration, model, input tokens, output tokens, context length, and context percentage by default.
- Stable long-text rendering: splits card body into safe Markdown blocks; real stress testing covered 16k Chinese characters in one Feishu card.
- Fault isolation: when the sidecar is unavailable, the Hermes hook fails open and Hermes native text continues to work.
- Safe installer: fails closed, checks Hermes version/code shape/backup/manifest before writing.
- Recovery path: `restore` and `uninstall` refuse to overwrite user-modified Hermes files.

## When To Use

Use this plugin if you want Hermes Agent replies inside Feishu to appear like modern AI chat cards instead of plain streaming text.

It is designed for users who want visible tool progress, clean chat history, stable Markdown/table/list rendering, token/context stats, and minimal intrusion into Hermes Gateway.

## V3.2 Multi-bot And Group Chat

V3.2 adds multi-bot routing and formal group chat support: one sidecar manages multiple Feishu bots and routes cards by `chat_id/open_chat_id` to the bound bot. Unbound chats use the fallback/default bot. This plugin does not decide group trigger rules; Hermes still decides when to respond, and the plugin only renders cards for events Hermes already emits.

Example config:

```yaml
feishu:
  app_id: "cli_default"
  app_secret: "..."

bots:
  default: default
  items:
    sales:
      name: "Sales Group Bot"
      app_id: "cli_sales"
      app_secret: "..."

bindings:
  fallback_bot: default
  chats:
    oc_sales_group: sales
```

Common commands:

```bash
python3 -m hermes_feishu_card.cli bots list --config ~/.hermes_feishu_card/config.yaml
python3 -m hermes_feishu_card.cli bots bind-chat <chat_id> <bot_id> --config ~/.hermes_feishu_card/config.yaml
python3 -m hermes_feishu_card.cli bots unbind-chat <chat_id> --config ~/.hermes_feishu_card/config.yaml
```

Troubleshooting:

- Wrong bot replied: check `bindings.chats`
- Group card not sent: verify bot is in the group, has permissions, Hermes triggered, and `/health.routing` looks healthy
- Unknown bot binding: run `doctor` or `bots list`

## Requirements

- Python `3.9+`; Python `3.12` is recommended.
- Hermes Agent `v2026.4.23+`.
- macOS/Linux or another POSIX-like environment for sidecar process management and pidfiles.
- A Feishu/Lark custom app with permissions to send and update message cards.
- Python dependencies:
  - `aiohttp>=3.9`
  - `PyYAML>=6.0`

The installer checks `VERSION=v2026.4.23+` or Git tag `v2026.4.23+` in the Hermes directory, plus the `gateway/run.py` structure. If the check fails, it does not write Hermes files.

## Installation

For ordinary users, use the integrated `setup` installer. It creates a default config, validates credentials, checks Hermes compatibility, installs the hook, starts the sidecar, and verifies health.

```bash
git clone https://github.com/baileyh8/hermes-feishu-streaming-card.git
cd hermes-feishu-streaming-card
python3 -m pip install -e ".[test]"
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --yes
```

By default, `setup` writes:

```text
~/.hermes_feishu_card/config.yaml
```

Use a custom config path when needed:

```bash
python3 -m hermes_feishu_card.cli setup \
  --hermes-dir ~/.hermes/hermes-agent \
  --config ~/.hermes_feishu_card/config.yaml \
  --yes
```

`setup` performs these steps:

1. Create a default config if it does not exist.
2. Verify Feishu credentials from environment variables or config.
3. Check the Hermes directory, version, and `gateway/run.py` structure.
4. Back up the original Hermes file and install the minimal hook.
5. Start the sidecar.
6. Call `/health` to confirm the sidecar is running.

If `FEISHU_APP_ID` or `FEISHU_APP_SECRET` is missing, `setup` stops before installing the hook. It only leaves the generated config file behind, preventing false-success installations that cannot send real Feishu cards.

Install the hook without starting the sidecar:

```bash
python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --skip-start --yes
```

### Advanced Troubleshooting Commands

Step-by-step commands remain available for diagnostics:

```bash
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --skip-hermes
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli start --config config.yaml.example
python3 -m hermes_feishu_card.cli status --config config.yaml.example
```

`doctor` prints `version_source`, `version`, `minimum_supported_version`, `run_py_exists`, and the rejection reason. Confirm `doctor: ok` before manual installation.

Stop, restore, or uninstall:

```bash
python3 -m hermes_feishu_card.cli stop --config config.yaml.example
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli uninstall --hermes-dir ~/.hermes/hermes-agent --yes
```

`restore` and `uninstall` use the installer backup and manifest. They refuse to overwrite files when the Hermes file, backup, or manifest has changed unexpectedly.

## Configuration

Copy `config.yaml.example` to a safe local path before adding credentials. Do not commit a real App Secret.

```yaml
server:
  host: 127.0.0.1
  port: 8765

feishu:
  app_id: ""
  app_secret: ""
  base_url: https://open.feishu.cn/open-apis
  timeout_seconds: 30

card:
  title: Hermes Agent
  max_wait_ms: 800
  max_chars: 240
  footer_fields:
    - duration
    - model
    - input_tokens
    - output_tokens
    - context
```

`card.title` controls the Feishu card header title. `footer_fields` controls footer fields and order. Supported values are `duration`, `model`, `input_tokens`, `output_tokens`, and `context`.

Default footer format:

```text
1m32s · MiniMax M2.7 · ↑1.1m · ↓2.2k · ctx 182k/204k 89%
```

Supported environment variables:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `HERMES_FEISHU_CARD_HOST`
- `HERMES_FEISHU_CARD_PORT`
- `HERMES_FEISHU_CARD_ENABLED`
- `HERMES_FEISHU_CARD_EVENT_URL`
- `HERMES_FEISHU_CARD_TIMEOUT_MS`

## Feishu App Setup

Real card delivery requires a Feishu/Lark custom app. Prefer local config or environment variables:

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
```

Run a real Feishu smoke test:

```bash
FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx \
python3 -m hermes_feishu_card.cli smoke-feishu-card --config config.yaml.example --chat-id oc_xxx
```

This command sends a test card and updates it once. It redacts App Secret, tenant token, and Authorization headers in output.

## Hermes Gateway Streaming And Thinking Configuration

This plugin renders events that Hermes already produces. It does not invent model thinking content. To see streaming thinking and progressive answers in the card, Hermes Gateway and the current model/provider must emit streaming events.

Check three things:

1. Hermes Gateway platform streaming is enabled: `streaming.enabled: true`, with `streaming.transport: edit`.
2. Feishu is not disabled by a platform override: avoid `display.platforms.feishu.streaming: false`; set it to `true` when you want to force Feishu streaming on.
3. The current model/provider supports and exposes reasoning/thinking deltas. If the model only returns a final answer, the card can only show the final answer.

In Hermes `config.yaml`, confirm the following. The common path is `~/.hermes/config.yaml`; if your config lives inside the Hermes installation directory, the installer also checks `<hermes-dir>/config.yaml`, `<hermes-dir>/config.yml`, `<hermes-dir>/configs/config.yaml`, and `<hermes-dir>/configs/config.yml`.

```yaml
streaming:
  enabled: true
  transport: edit
  # Optional. Hermes defaults are fine; these are the values used by the
  # locally verified real acceptance instance.
  edit_interval: 0.8
  buffer_threshold: 20
  cursor: ""
```

If your Hermes config previously disabled Feishu streaming with a platform override, explicitly enable it:

```yaml
display:
  platforms:
    feishu:
      streaming: true
```

Do not treat `display.show_reasoning` or `display.platforms.feishu.show_reasoning` as required for this plugin. In current Hermes source, those settings control Hermes' native final reasoning display and may prepend a `💭 Reasoning` code block to the final text, which can interfere with the card-only streaming experience. Enable them only when you intentionally want Hermes' native reasoning block in the final response.

`agent.reasoning_effort` is also optional and model/provider-dependent. It can affect whether some models produce reasoning, but it is not the Gateway card streaming switch.

How to read symptoms:

- The card is created, stays at “thinking”, then completes: the model or Hermes probably did not emit thinking deltas.
- Answer text streams, but no thinking appears: streaming works, but the model is not exposing thinking.
- The card updates only once at the end: check `streaming.enabled`, `streaming.transport`, and `display.platforms.feishu.streaming`.
- No Feishu card appears: check Feishu credentials, sidecar status, and Hermes hook installation first.

`setup` and `doctor --hermes-dir` provide conservative Hermes config guidance. If common config files contain `streaming.enabled: false`, `streaming.transport: off`, or `display.platforms.feishu.streaming: false`, they print a warning. If Gateway streaming config cannot be detected, they print a note. This does not block installation because Hermes config schemas vary across versions.

## Architecture

```text
Hermes Gateway
  └─ minimal hook in gateway/run.py
       └─ hermes_feishu_card.hook_runtime
            └─ HTTP POST /events
                 └─ sidecar server
                      ├─ CardSession state machine
                      ├─ render_card() card rendering
                      ├─ FeishuClient tenant token / send / update
                      ├─ throttling, retry, locks, diagnostics
                      └─ /health metrics
```

The Hermes hook only converts the message lifecycle into `SidecarEvent` events:

- `message.started`
- `thinking.delta`
- `answer.delta`
- `tool.updated`
- `message.completed`
- `message.failed`

The sidecar owns complete session state and the Feishu CardKit boundary. This keeps Hermes code intrusion minimal while allowing card logic to be tested, restarted, and diagnosed independently.

Historical implementations are archived under `legacy/` for migration reference only and are not the active runtime. New development, tests, and installation entry points are under `hermes_feishu_card/`. See [docs/migration.en.md](docs/migration.en.md).

## Diagnostics

`/health` and `status` expose process-local metrics:

- `events_received`
- `events_applied`
- `events_ignored`
- `events_rejected`
- `feishu_send_successes`
- `feishu_send_failures`
- `feishu_update_successes`
- `feishu_update_failures`
- `feishu_update_retries`

`stop` validates the PID/token in the pidfile against `process_pid/process_token` from `/health` before stopping a process, preventing stale pidfiles or PID reuse from killing unrelated services.

Card creation is not retried automatically, avoiding duplicate cards when the response is ambiguous. Updates for known message IDs use a limited retry.

## Troubleshooting

### `doctor` says Hermes is unsupported

Confirm the Hermes version is at least `v2026.4.23` and that the target directory contains `gateway/run.py`. The installer reads `VERSION` or Git tags; inspect `version_source`, `version`, and `reason` if detection fails.

### The sidecar starts but no real card appears

Check `FEISHU_APP_ID` and `FEISHU_APP_SECRET`. Without credentials, advanced sidecar starts use a no-op client that accepts events but does not send real Feishu cards.

### The card has no thinking content or does not stream

Check Hermes `config.yaml` for `streaming.enabled: true` and `streaming.transport: edit`. If `display.platforms.feishu.streaming: false` is present, remove that override or set it to `true`. Then confirm that the current model/provider actually exposes reasoning/thinking deltas. Do not blindly enable `show_reasoning` for card thinking; it may only append a final reasoning code block to Hermes' native response. The plugin config file `~/.hermes_feishu_card/config.yaml` only controls card title, footer, throttling, and rendering options. It does not control whether Hermes Gateway emits `thinking.delta` or `answer.delta`.

### Duplicate cards appear

Check `feishu_send_successes`, `events_received`, and `events_rejected` in `/health`. V3.1.0 uses a per-message lock and message_id mapping, so one Hermes message should create one Feishu card.

### Gray native text appears

Check whether the sidecar received and applied `message.completed`. After the sidecar accepts the completion event, the Hermes hook suppresses duplicate native text. If the sidecar is unavailable, the hook fails open and Hermes native text continues.

### Footer token numbers look wrong

V3.1.0 filters obviously abnormal token totals. If the footer still looks wrong, inspect the `tokens` and `context` metadata passed by Hermes Gateway.

### Restore fails

`restore` refuses to overwrite files when Hermes files or backups changed after installation. Back up the current Hermes directory, then inspect `gateway/run.py`, the backup, and the manifest before restoring manually.

## Testing

Full local test suite:

```bash
python3 -m pytest -q
```

Focused checks:

```bash
python3 -m pytest tests/unit -q
python3 -m pytest tests/integration -q
python3 -m pytest tests/unit/test_docs.py -q
python3 -m pytest tests/integration/test_feishu_client_http.py -q
```

Current V3.2.0 acceptance status:

- Full automated test suite: `396 passed`
- GitHub Actions: Python 3.9 / 3.12 matrix passed
- Installer/restore tests cover backups, manifest, duplicate install, modified-file refusal, uninstall, and restore idempotency
- Real Hermes Gateway E2E verified card creation, streaming updates, tool counts, completion state, and footer metadata
- Real Feishu app verified in-card updates with no duplicate gray native messages
- Real long-card stress test updated one Feishu card to 16k Chinese characters
- Fresh Hermes `v2026.4.23`: `doctor -> install -> doctor -> restore -> doctor` loop completed
- Ordinary-user `setup --hermes-dir ... --yes` covers config creation, hook install, sidecar startup, and health check

## Documentation

- Architecture: [中文](docs/architecture.md) / [English](docs/architecture.en.md)
- Event protocol: [中文](docs/event-protocol.md) / [English](docs/event-protocol.en.md)
- Installer safety: [中文](docs/installer-safety.md) / [English](docs/installer-safety.en.md)
- Migration: [中文](docs/migration.md) / [English](docs/migration.en.md)
- E2E verification: [中文](docs/e2e-verification.md) / [English](docs/e2e-verification.en.md)
- Release readiness: [中文](docs/release-readiness.md) / [English](docs/release-readiness.en.md)
- Testing: [中文](docs/testing.md) / [English](docs/testing.en.md)

## Security

Do not commit App Secret, tenant token, real chat_id, or private conversation content. The README images are only public demonstrations of the V3.1.0 card experience. Production credentials should always live in local config, environment variables, or a dedicated secret manager.
