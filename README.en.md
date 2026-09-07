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

Hermes Feishu Streaming Card turns Hermes Agent Gateway replies in Feishu/Lark into one continuously updated interactive card. Reasoning, tool calls, final answers, approvals, choices, system notices, and runtime stats stay inside cards instead of spilling into scattered native gray text messages.<br><br>It targets the real pain points of running Hermes inside Feishu: missing or out-of-order streaming text, long tables/code blocks rendered as raw Markdown, invisible tool progress, manual approval replies, frozen topic timelines, multi-bot/profile troubleshooting, and uncertain hook compatibility after Hermes upgrades.
![Hermes Feishu card command interaction, command result feedback, and tool timeline showcase](docs/assets/feishu-card-showcase-v385.png)

<h2>Optional web scraping service</h2><p>If an Agent workflow needs to retrieve public webpages affected by anti-bot restrictions, <a href="https://scrapingant.com/?ref=zwq4ngy">ScrapingAnt</a> is an optional web scraping service to consider. Its Web Scraping API includes 10,000 free API credits every month with no credit card required; it is not required by this plugin.</p><blockquote>Disclosure: This is an affiliate link. A qualifying first paid subscription may earn this project a commission.</blockquote>
## V4 Live Agent States

| Running | Waiting for user |
|---|---|
| ![Real Feishu running state with the current tool action in the Header](docs/assets/feishu-v4-runtime-running.png) | ![Real Feishu waiting state with native buttons in the same card](docs/assets/feishu-v4-runtime-waiting.png) |
| Failed | Completed |
| ![Real Feishu failed state retaining the last tool preview](docs/assets/feishu-v4-runtime-failed.png) | ![Real Feishu completed state with only the native reply Header and final result](docs/assets/feishu-v4-runtime-completed.png) |

During execution, the Header follows real Hermes tool actions while public interim output continues streaming in the body. On completion, the native Feishu reply quote becomes the only Header instead of stacking a second `Hermes Agent` card title above it.

## What You Get

- **One continuously updated Feishu card**: `thinking.delta`, `answer.delta`, `tool.updated`, and `message.completed` merge into one card.
- **A live runtime Header**: the title keeps the user-configured card name (`Hermes Agent` by default), while the subtitle turns tool names and `tool.updated.detail` into concise action summaries; full commands remain in the timeline.
- **Primary answer and process timeline**: the final answer stays in the main content area while pre-tool answers, tool calls, and system notices move into the "Reasoning and Tools" timeline.
- **In-card interactions**: approval and clarify choices render as buttons; standalone commands such as `/new`, `/reset`, `/undo`, and `/model` use native interactive cards. V4 `/model` uses the same Provider/model list as Hermes CLI and follows a Provider → Model flow instead of crowding every model into one dropdown.
- **Reliable topic and notice delivery**: topic events resolve by `reply_to_message_id`; initial cards use bounded stable-UUID retries, definite non-delivery falls back to the original notice, and uncertain outcomes use a generic warning without duplicating the original text.
- **Clearer group diagnostics**: `/hfc status` explains group chat binding state, the suggested bind command, and slash-command behavior boundaries.
- **Bounded operations cards**: `/hfc doctor` can present diagnosis, two-step safe repair, and restart confirmation; private chats do not compare operators, while group confirmations stay with the initiator. When operations cards are unavailable, use the CLI; normal streaming-card layout and footer are unchanged.
- **Long content protection**: long Markdown tables and fenced code blocks split on structure boundaries instead of raw character cuts.
- **V4.1 per-chat native delivery**: exact `bindings.native_chats` entries return selected chats to Hermes native messages. Hook and sidecar both enforce the choice, and policy failures fail open instead of swallowing output.
- **V4.1 lossless table overflow**: `card.table_overflow_mode: compact` converts table six onward into field lists and never sends a partial final card above 28,000 bytes. The exact Base path for ordinary Hermes 0.19 final answers adds bounded stable-UUID, delivery-ledger, and signed-ACK recovery; Cron and other non-exact paths remain native fail-open.
- **V4.1 upgrade and service safety**: authenticated `runtime.hello` / `runtime.heartbeat` distinguishes liveness from delivery readiness; strict repair never restarts Gateway automatically, and `service.manager: auto` never enters a system service or invokes sudo.
- **Diagnostics and recovery**: `doctor`, `/hfc status`, `/health` metrics, runtime import checks, Hermes Feishu SDK capability checks, and safe repair/restore/uninstall cover common failures. If the Hermes adapter uses `extra_ua_tags` while its Gateway venv still has an older `lark-oapi`, `doctor` reports `feishu_sdk_incompatible` and `setup/install` installs the verified `lark-oapi==1.6.8`.

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
  table_overflow_mode: compact
  footer_fields: [duration, model, input_tokens, output_tokens, context]
bindings:
  native_chats: []
integrity:
  mode: safe
service:
  manager: auto
```

`native_chats` uses exact matching only; in multi-profile setups place it under the matching `profiles.<id>.bindings`. Existing configs without an `integrity` section load as `notify` and do not silently enable automatic repair. See [V4.1 safety controls and troubleshooting](docs/wiki/v4.1-safety-controls.md) for the complete boundary.

To show remaining Codex subscription quota, add `subscription_usage` to `footer_fields`. The plugin calls Hermes native `fetch_account_usage("openai-codex")` only when explicitly enabled; older Hermes versions, missing login, or network failures silently omit the field without affecting card completion. `card.text_sizes` can configure `body`, `reasoning`, `tool`, `notice`, and `footer`, including `default` / `pc` / `mobile` device mappings; physical card width/height remain controlled by the Feishu/Lark client.

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

The compatibility matrix covers older Hermes starting at `v2026.4.23` and Hermes 0.13.0+/0.14.0/0.15.x/0.17.x/0.18.x/0.19.0 (`v2026.7.20`)/0.20.x. `doctor` prefers `VERSION`, a literal `hermes_cli.__version__`, or a Git tag and can fall back to verified anchors when metadata is missing or unparseable. Automated strategy detection requires installation to verify and manage both `gateway/run.py` and `gateway/platforms/base.py` for Hermes 0.19.0, `v2026.7.20+`, or verified exact-ledger source; Hermes 0.20 awaited `asyncio.to_thread(...)` ledger writes are accepted only at exact anchors. V4.1 `manifest_version: 2` treats run, required Base, and optional Cron backup/write/restore as one transaction. A separate read-only validation against real local source confirmed startup before ledger redelivery, recovery before adapter send, and idempotent restore, but is not a claim of a real Gateway or Feishu E2E run. A Hermes upgrade can replace managed source; `status` / `start` use `HERMES_DIR` from the config-adjacent `.env` to detect stale state and print a safe recovery command. After confirming an intentional upgrade, run the suggested `install --accept-hermes-upgrade --yes`, then `hermes gateway start`; user edits or incomplete evidence remain fail-closed behind `doctor --explain`.

## Docker Container Install

For an existing Hermes container:

```bash
export FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx HFC_VERSION=v4.4.1
bash install-docker.sh
```

Defaults:

| Variable | Default |
|---|---|
| `HERMES_DIR` | `/opt/hermes` |
| `HFC_CONFIG` | `/opt/data/config.yaml` |
| `HFC_ENV_FILE` | `/opt/data/.env` |
| `HFC_VERSION` | `latest` |

`docker-compose.example.yml` is an integration example, not an official image. Since V3.8.6, Docker/source-stripped Hermes roots without `VERSION` or `.git` can fall back to Gateway anchors and still choose `gateway_run_013_plus`. `latest` resolves once to the exact `vX.Y.Z` tag of the latest stable GitHub Release and installs that pinned ref. Lookup, response, or tag-validation failure stops before credential prompting, pip, setup, doctor, or Docker state mutation. An explicit release tag stays pinned and bypasses the Release API; only explicit `--version main` (PowerShell: `-Version main`) opts into the moving development branch.

## Common Commands

| Command | Purpose |
|---|---|
| `setup --hermes-dir ... --yes` | Configure, diagnose, install the hook, and start the sidecar; enables boot persistence when the Linux user manager and linger are ready, otherwise warns and starts transiently; use `--transient` to opt out |
| `doctor --config ... --hermes-dir ... --explain` | Diagnose Hermes version, runtime import, hook strategy, anchors, and recommendations |
| `install --hermes-dir ... --yes` | Install the plugin into Hermes runtime venv and patch Hermes |
| `repair --hermes-dir ... --yes` | Repair verifiable hook manifest/backup state |
| `setup --repair ... --yes` / `--no-repair` | Automatically repair known-safe state, or explicitly opt out |
| `restore --hermes-dir ... --yes` | Restore the original Hermes file |
| `start --config ...` / `status --config ...` / `stop --config ...` | Manage the transient sidecar process and `/health` |
| `enable --config ... --hermes-dir ... --yes` / `disable` | Explicitly manage the HFC-owned persistent systemd user service |
| `smoke-feishu-card --profile-id ... --chat-id ...` | Send a real Feishu card smoke test |
| `bots list|show|add|remove|test` | Manage and test multi-bot routing |

High-frequency stream tuning usually needs no change. For DeepSeek burst, token-by-token, or long-context pressure:

| Variable | Default | Purpose |
|---|---:|---|
| `HERMES_FEISHU_CARD_DELTA_COALESCE_MS` | `250` | Max Gateway-side delta coalescing wait |
| `HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS` | `600` | Flush pending delta when this character budget is reached |
| `HERMES_FEISHU_CARD_DELTA_COALESCE_MAX_PENDING` | `128` | Pending delta session cap |
## Latest Releases
| Version | Highlights |
|---|---|
| [v4.4.1](docs/release-notes-v4.4.1.en.md) | Hermes 0.21 facade-decomposition compatibility, topic follow-ups, single-process profiles, complete approval scope, optional reasoning code blocks, actual provider attribution, and CodeQL updates |
| [v4.4.0](docs/release-notes-v4.4.0.en.md) | Adds a Feishu-native capability center driven by the latest Hermes `COMMAND_REGISTRY`, category/detail navigation, safe quick actions, KPI cards, `/bg`/`/btw`/`/plan` compatibility, real backlog metrics, and extreme-Markdown safe folds |
| [v4.3.8](docs/release-notes-v4.3.8.en.md) | Makes guided setup persistent when capabilities are ready and explicit about transient reboot risk otherwise, fixes the next-prompt sequence race in batch clarify, and honors proxy environment variables for remote Feishu/Lark HTTP while keeping local/private bypass |
| [v4.3.7](docs/release-notes-v4.3.7.en.md) | Supports Hermes 2026-08-25 core session-scoped delivery filters: the installer accepts the exact new `session_key=session_key` call while preserving the legacy call and rejecting every other keyword shape |
| [v4.3.6](docs/release-notes-v4.3.6.en.md) | Replaces invalid unanchored topic creation with `receive_id_type=chat_id` to prevent Feishu `99992402`; approval/clarify cards and completion notifications can optionally `@` mention the requester without changing the schema 2.0 owner card |
| [v4.3.5](docs/release-notes-v4.3.5.en.md) | Supports the Hermes v2026.8.3 Feishu adapter whose `edit_message` method has no `metadata` parameter: the wrapper removes only unsupported internal metadata, preserves metadata-aware/`**kwargs` adapters, and still raises `TypeError` for unrelated unknown keywords |
| [v4.3.4](docs/release-notes-v4.3.4.en.md) | Prevents reverse-DNS stalls while starting the runtime interaction listener and lets a process exit when that listener is not explicitly closed; `doctor --json` now validates V3 Hybrid installs with the V3 inspector instead of reporting Legacy manifest/hash/path failures |
| [v4.3.3](docs/release-notes-v4.3.3.en.md) | Preserves the reply anchor and `reply_in_thread` placement when the first reply creates a thread; completion notifications stay in that thread, while an explicit thread reply without an anchor fails closed instead of posting top-level text |
| [v4.3.2](docs/release-notes-v4.3.2.en.md) | Fixes Issue #227 by keeping schema 2.0 streaming cards and legacy interaction cards on stable rails, preventing `230099/200800`; the Gateway also rejects schema 2.0 raw callback cards to prevent `200673` |
| [v4.3.1](docs/release-notes-v4.3.1.en.md) | Restores clarify/approval streaming after a Feishu WebSocket click on Hermes 0.20, wakes text fallback on the first reply, and fixes v4.3.0 persistent-service identity, systemd working-directory, and tokenless-health reconciliation |
| [v4.3.0](docs/release-notes-v4.3.0.en.md) | Source-proven Hybrid Plugin/patch integration for Hermes `v2026.8.3`, idempotent and byte-restorable V3 install ownership, single-owner runtime interactions, and a linger-verified persistent systemd user service |
| [v4.2.12](docs/release-notes-v4.2.12.en.md) | Makes approval cards follow Hermes capabilities and reject undeclared input, while zero-tool cards retain a stable collapsed timeline whenever reasoning display is enabled |
| [v4.2.11](docs/release-notes-v4.2.11.en.md) | Fixes Issue #202 by freezing each superseded streaming card as a green “moved to the interaction card” history snapshot after replacement delivery; predecessor PATCH failure remains fail-open and only the newest card receives choices and later updates |
| [v4.2.10](docs/release-notes-v4.2.10.en.md) | Authenticates non-loopback sidecar callbacks and result reads with method/path/body-bound HMAC, enforces absolute interaction expiry with late-button/form rejection and same-card refresh, and adds cross-platform CI, CodeQL, Dependabot, and Node 24 Action SHA gates; see [v4.2.9](docs/release-notes-v4.2.9.en.md) for the preceding release |
| [v4.2.8](docs/release-notes-v4.2.8.en.md) | Fixes the installer contract so `install.sh`, `install-docker.sh`, and `install.ps1` persist process-supplied Feishu credentials into the private `.env` instead of using them only for the current process |
| [v4.2.7](docs/release-notes-v4.2.7.en.md) | Fixes Issue #193 Windows cold-import timeouts and legacy backslash manifest paths, integrates PR #180 parent `HERMES_HOME` discovery and PR #181 safe detached-runner PID rebinding, and propagates PowerShell installer failures |
| [v4.2.6](docs/release-notes-v4.2.6.en.md) | Fixes Issue #187 repeated choice-card position, #188 short terminal postscripts replacing answers, #189/PR #190 exact Base compatibility for Hermes 0.20, and bare Feishu `/update` venv-symlink, slow-fetch, and version-reporting failures; see [v4.2.5](docs/release-notes-v4.2.5.en.md) for the preceding audit safety hotfix |
| [v4.2.4](docs/release-notes-v4.2.4.en.md) | Fixes consecutive Feishu/Lark topic replies quoting the same message overwriting the first reply card; every new message opens an independent card while in-turn streaming still resolves through the reply alias |
| [v4.2.3](docs/release-notes-v4.2.3.en.md) | Preserves `update_evidence_fingerprint` when the WebSocket hook forwards `/update` actions, allowing the sidecar to complete evidence-bound confirm/cancel transitions while missing or mismatched evidence remains fail-closed |
| [v4.2.2](docs/release-notes-v4.2.2.en.md) | Fixes `/update` confirmation actions that changed durable state without PATCHing the original card; cancel now renders a terminal state and never starts the updater, while confirm shows preparation before scheduling maintenance |
| [v4.2.1](docs/release-notes-v4.2.1.en.md) | Registers the live Gateway runner before the first runtime heartbeat, so the first bare private-chat `/update` after restart has complete active-work evidence; missing evidence remains fail-closed |
| [v4.2.0](docs/release-notes-v4.2.0.en.md) | A bare `/update` in a Feishu private chat uses a 120-second confirmation and an independent maintenance process to run the official Hermes updater, then restores the same HFC version, hooks, sidecar, and Gateway; group and parameterized commands keep native Hermes behavior |
| [v4.1.4](docs/release-notes-v4.1.4.en.md) | Fixes Issue #171: on Windows, official install/setup can rebuild a missing manifest for a legacy owned hook only after byte-for-byte gateway, cron, and exact Base evidence checks; edits outside owned blocks still fail closed |
| [v4.1.3](docs/release-notes-v4.1.3.en.md) | Fixes the same-target fence-binding convergence gap from Issue #158, includes PR #168's native delta-callback selection, and restores tool/streaming/interaction hooks plus truthful doctor detection after Hermes' `TurnRunner` refactor from Issue #169 |
| [v4.1.0](docs/release-notes-v4.1.0.en.md) | Exact per-chat card/native policy, lossless compaction after five tables, authenticated runtime integrity with strict repair, and four explicit sidecar managers with no privilege escalation from `auto`; follow-up fixes are documented in [v4.1.1](docs/release-notes-v4.1.1.en.md) and [v4.1.2](docs/release-notes-v4.1.2.en.md) |
| [v4.0.21](docs/release-notes-v4.0.21.en.md) | Issue #155 archives answers only at an explicit `answer -> tool` boundary so post-tool final answers stay visible; Issue #147 real Feishu acceptance observed a completion card plus native image with no matching native duplicate or uncertain-delivery warning; UI and configuration remain unchanged |
| [v4.0.20](docs/release-notes-v4.0.20.en.md) | Fixes Issue #153: queued notice updates return `accepted` without false unknown-delivery warnings, while real PATCH failures retain redacted metrics and error codes |
| [v4.0.19](docs/release-notes-v4.0.19.en.md) | Prevents the one-line installer from using `pip --user` inside the Hermes venv and stops immediately on pip failures, avoiding false upgrade success |
| [v4.0.18](docs/release-notes-v4.0.18.en.md) | Checks the real Hermes Feishu SDK constructor capability, diagnoses stale `lark-oapi`, and repairs it during setup/install |
| [v4.0.17](docs/release-notes-v4.0.17.en.md) | Correlates parallel same-name tools by real call ID, counts invocations once, and removes duplicate duration detail |
| [v4.0.16](docs/release-notes-v4.0.16.en.md) | Removes duplicate initial loading text, drops the stale body placeholder once tools start, and restores real tool durations |
| [v4.0.15](docs/release-notes-v4.0.15.en.md) | Fixes Issue #141 with a compact semantic tool timeline and real loading animation; CLI detects Hermes upgrades that removed the hook |
| [v4.0.14](docs/release-notes-v4.0.14.en.md) | Fixes Issue #142 so orphaned long-task heartbeats stay running, update one card per original message anchor, and still complete on the final event |
| [v4.0.13](docs/release-notes-v4.0.13.en.md) | Routes every non-empty Hermes slash-command feedback message through a standalone command card, updates one card for multi-message feedback, keeps manual `/compress` progress/results in place, and falls back to exact native text on failure |
| [v4.0.12](docs/release-notes-v4.0.12.en.md) | Issue #133 adds visible context-compaction phases and configurable body/reasoning/tool/notice/footer text sizes; Issue #136 loads selected-env credentials and exposes degraded Noop delivery |
| [v4.0.11](docs/release-notes-v4.0.11.en.md) | Fixes Issue #135 with stable-UUID bounded initial delivery retries and safe `delivered/not_sent/unknown` notice fallback semantics |
| [v4.0.10](docs/release-notes-v4.0.10.en.md) | Hardens sidecar event transport: non-loopback listeners require explicit opt-in plus HMAC-SHA256 anti-forgery/replay proofs, while loopback installs stay compatible |
| [v4.0.9](docs/release-notes-v4.0.9.en.md) / [v4.0.8](docs/release-notes-v4.0.8.en.md) | Fixes Issue #130's live WebSocket handler identity and Issue #127's native cron attachment delivery |
| [v4.0.7](docs/release-notes-v4.0.7.en.md) | Isolates the Linux/systemd sidecar in a restartable user service, prefers Hermes venv Python during upgrades, and includes PR #124's orphaned self-improvement notice fix |
| [v4.0.6](docs/release-notes-v4.0.6.en.md) | Fixes Hermes 0.18.x terminal/queued completion hooks and terminal background notice cards without gray native output, with explicit fail-closed recovery after Hermes source upgrades |
| [v4.0.5](docs/release-notes-v4.0.5.en.md) | Fixes upgrades that left the Gateway venv loading an older plugin; the installer compares runtime versions, synchronizes when needed, and verifies the installed version and path |
| [v4.0.4](docs/release-notes-v4.0.4.en.md) | Fixes Markdown `MEDIA:` literals, interaction forwarding with an SDK-retained callback, and misleading `5h` labels when Codex exposes one ambiguous limit window |
| [v4.0.3](docs/release-notes-v4.0.3.en.md) | Fixes duplicate gray answer text when the package is upgraded and restarted while a V4.0.0 completion hook remains; suppresses one exact text copy while preserving native media |
| [v4.0.2](docs/release-notes-v4.0.2.en.md) | Allows safe upgrades from verified older owned hooks when manifest and backup evidence match; includes the v4.0.1 media-text deduplication fix |
| [v4.0.0](docs/release-notes-v4.0.0.en.md) | The running Header shows the latest Hermes tool preview while public interim output streams independently in the body; waiting, failed, and completed states preserve established Footer and reply boundaries |
| [v3.10.0](docs/release-notes-v3.10.0.md) | Bare `/resume` uses a native session picker while retaining Hermes' security path; the model footer gains escaped semantic color without changing layout or field order |
| [v3.9.1](docs/release-notes-v3.9.1.md) | Reliability hotfix: preserve completed answers, serialize interrupted terminal cards, make model-picker callbacks asynchronous, and recover verifiable marker-only installer damage; normal streaming-card footer/layout remains unchanged |
| [v3.8.18](docs/release-notes-v3.8.18.md) | Cron cards preserve `thread_id` and return to the originating Feishu topic thread (PR #91, contributed by @colinaaa) |
| [v3.8.17](docs/release-notes-v3.8.17.md) | Cron `deliver=origin/all` routing intents resolve to Feishu targets and send cards |
| [v3.8.16](docs/release-notes-v3.8.16.md) | Topic groups that reuse `message_id` now send a fresh card for the second and later messages |
| [v3.8.15](docs/release-notes-v3.8.15.md) | Input `.docx/files` context stays as card attachment summaries and no longer duplicates the native final reply |
| [v3.8.14](docs/release-notes-v3.8.14.md) | Agent clarify/approval buttons resolve through WebSocket-native `interaction.select` card actions |
| [v3.8.13](docs/release-notes-v3.8.13.md) | Hermes `v2026.7.7.2` / `0.18.2` upgrades can fall back to anchors and repair stale install state |
| [v3.8.12](docs/release-notes-v3.8.12.md) | Completed cards with attachment summaries such as `colors.csv` / `styles.csv` no longer duplicate the final native reply |
| [v3.8.11](docs/release-notes-v3.8.11.md) | `/hfc status` no longer triggers the gray native `Unknown command /hfc` reply after the card is accepted |
| [v3.8.10](docs/release-notes-v3.8.10.md) | Group `/hfc status` binding hints and slash-command boundaries; tool details show arguments, duration, and failures |
| [v3.8.9](docs/release-notes-v3.8.9.md) | Feishu/Lark topic card continuity; `system.notice` no longer duplicates outside the card |
| [v3.8.8](docs/release-notes-v3.8.8.md) | Cardifies native Hermes notices: Working, context compression, skill loading, and self-improvement review |
| [v3.8.7](docs/release-notes-v3.8.7.md) | Newer Hermes streams can create cards even when `message.started` is missing |
Full history: [CHANGELOG.md](CHANGELOG.md). Longer historical notes remain in the [full user guide](docs/user-guide.en.md#version-history); see also the archived [v3.8.6 notes](docs/release-notes-v3.8.6.md).

## Architecture At A Glance

```text
Hermes Gateway
  -> minimal hooks in gateway/run.py
     + required exact hook in gateway/platforms/base.py (Hermes 0.19)
     -> hermes_feishu_card.hook_runtime
        -> HTTP POST /events
           -> sidecar server
              -> CardSession state
              -> Feishu CardKit send/update
              -> retry / coalescing / metrics / /health
```

This remains a sidecar-only design: Hermes keeps only installer-owned, detectable, restorable hooks, while Feishu delivery, card updates, session state, retries, and diagnostics live in the sidecar. Historical V2 code is archived under `legacy/` and is not the active runtime.

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
- V4.1 safety controls and troubleshooting: [docs/wiki/v4.1-safety-controls.md](docs/wiki/v4.1-safety-controls.md)

## Contributors

### V4.4.1

- [liooil](https://github.com/liooil) contributed the Hermes facade-decomposition implementation in [PR #257](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/257); [Clarence-G](https://github.com/Clarence-G) contributed topic follow-up, queue/redirect, and cron delivery work in [PR #251](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/251). Original code commits and authorship are retained.
- [mouyong](https://github.com/mouyong) supplied multiplex-profile, topic, and readability feedback in [#83](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/83), [#252](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/252), [#253](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/253), [#258](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/258), and [#259](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/259); [shiboyumm](https://github.com/shiboyumm) opened the original [#83](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/83) configuration question; [Boer2333](https://github.com/Boer2333) requested provider attribution in [#250](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/250).
- [sp960817](https://github.com/sp960817), [Kevin32623](https://github.com/Kevin32623), and [shichenshuo-star](https://github.com/shichenshuo-star) reported Hermes 0.21 incompatibility in [#254](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/254), [#255](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/255), and [#256](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/256); [hnzwx](https://github.com/hnzwx) and [leavrcn](https://github.com/leavrcn) supplied additional reproduction and compatibility evidence in [#254](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/254). [micah928](https://github.com/micah928) supplied historical no-card evidence in [#73](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/73), whose current environment still needs retesting.
- Dependabot supplied the CodeQL updates in [PR #247](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/247) and [PR #248](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/248).
- Restored historical acknowledgements: [lanx214](https://github.com/lanx214) supplied the Linux reproduction in [Issue #240](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/240) ([V4.3.7](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.3.7)); [Lite-G](https://github.com/Lite-G) reported, reproduced, tested, and implemented the Feishu edit-fallback fix in [PR #235](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/235) ([V4.3.5](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.3.5)); [lyp88997](https://github.com/lyp88997) supplied the toast-only `200673` fix direction and observations of updates across environments ([V4.3.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.3.2)). These contributions belong to earlier releases; this cycle restores their missing historical attribution.

This list preserves code, PR proposals, issue reproductions, and real-environment retesting. GitHub's [Contributors](https://github.com/baileyh8/hermes-feishu-streaming-card/graphs/contributors) graph is commit-based; people who contributed only issue reports, comments, logs, or acceptance evidence may not appear in that graph and are still credited here.

- [gischuck](https://github.com/gischuck) - [PR #12](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/12) Accept-Encoding fix; [PR #76](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/76) reasoning/tool timeline UX proposal and implementation exploration
- [fengs2021](https://github.com/fengs2021) - [PR #17](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/17) lock optimization and update interval improvement
- [colinaaa](https://github.com/colinaaa) - [PR #87](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/87) WebSocket `interaction.select` clarify/approval card interaction support; [PR #88](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/88) fresh cards for second turns when Feishu topic groups reuse `message_id`; [PR #91](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/91) cron `thread_id` routing back to the originating Feishu topic-group thread
- [zayn-0101](https://github.com/zayn-0101) - [PR #77](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/77) cron `deliver=origin/all` routing-intent card delivery fix; [PR #196](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/196) non-blocking slash confirmation; [Cassius0924](https://github.com/Cassius0924) - [PR #199](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/199) multi-select and custom-answer forms
- [Zanetach](https://github.com/Zanetach) - [PR #84](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/84) / @Zanetach: card progress-status routing and `.env` allowlist expansion for profile environment support (V3.9.0)
- [colinaaa](https://github.com/colinaaa) - [PR #93](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/93) reliable terminal cards for interrupted tasks; [PR #97](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/97) completed-answer preservation (V3.9.1)
- [wjiemin49-ux](https://github.com/wjiemin49-ux) - [PR #52](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/52) diagnosis and direction for loopback health checks bypassing proxies (adopted in V3.9.1)
- [colinaaa](https://github.com/colinaaa) - [Issue #94](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/94) requirements, interaction flow, and security boundary for the native bare `/resume` picker (V3.10.0)
- [charles5g](https://github.com/charles5g) / jackmim - [PR #98](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/98) asynchronous model-picker callbacks, original-card status updates, and semantic model-footer color concept; mainline adds HTML escaping and preserves layout (V3.9.1–V3.10.0)
- [tianqiii](https://github.com/tianqiii) - [Issue #107](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/107) requirements, Hermes-native API direction, and display format for the Codex subscription-quota footer (V4.0.2)
- [sthnow](https://github.com/sthnow) - [Issue #110](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/110) reproduction, root-cause analysis, and expected boundary for literal `MEDIA:` text inside Markdown code (V4.0.4)
- [zkyken](https://github.com/zkyken) - [Issue #112](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/112) logs, bound-callback diagnosis, and fix direction for non-functional lark SDK interaction buttons (V4.0.4)
- [ShakuOvO](https://github.com/ShakuOvO) / [blakejia](https://github.com/blakejia) - [Issue #106](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/106) and [#111](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/111) reports, retesting, and screenshots for duplicate gray image-answer text (V4.0.1-V4.0.3); additional thanks to [blakejia](https://github.com/blakejia) for [#115](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/115) runtime-version evidence, complete upgrade steps, and metrics (V4.0.5); thanks to [nasvip](https://github.com/nasvip), [hzy](https://github.com/hzy), and [lRoccoon](https://github.com/lRoccoon) for V4.0.6's Hermes-upgrade reproduction, background notice-card implementation, and production completion-hook diagnosis/fix; V4.0.7 additionally credits [nasvip](https://github.com/nasvip) for [Issue #125](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/125)'s complete systemd/Python-environment evidence and [hzy](https://github.com/hzy) for [PR #124](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/124)'s self-improvement notice implementation and regression coverage; V4.0.8 thanks [zyq2552899783-lgtm](https://github.com/zyq2552899783-lgtm) for reporting [Issue #127](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/127), where cron delivery showed only the attachment filename; V4.0.9 thanks [Jasonsun77](https://github.com/Jasonsun77) for [Issue #130](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/130)'s Linux crash-loop A/B, complete timing, SDK versions, and upstream reconnect evidence
- V3.4–V3.8 historical PRs: thanks to [wzgrx](https://github.com/wzgrx) (PRs #30/#35/#36/#38), [zsfjim](https://github.com/zsfjim) (PR #33), [atop0914](https://github.com/atop0914) (PR #42), [0269chaoup](https://github.com/0269chaoup) (PR #49), [dominofeng-maker](https://github.com/dominofeng-maker) (PR #50), [coder-zhw](https://github.com/coder-zhw) (PR #51), [x-giraffee](https://github.com/x-giraffee) (PR #54), [jackwude](https://github.com/jackwude) (PR #72), and [bestkxt](https://github.com/bestkxt) (PR #85) for version detection, progress events, cron/topic routing, session GC, configuration, Hermes venv, sync utilities, and delivery-strategy proposals; thanks also to [Thomas0x1f](https://github.com/Thomas0x1f) for PR #143's multi-select exploration. Some proposals were reimplemented with stricter boundaries rather than merged verbatim.
- V4.0.10–V4.0.21: thanks to [tianxia3111](https://github.com/tianxia3111) (Issues #133/#153/#155), [nasvip](https://github.com/nasvip) (Issue #136), [ati121](https://github.com/ati121) (Issues #141/#142), and [Cassius0924](https://github.com/Cassius0924) (Issue #147) for compaction, systemd credentials, tool presentation, long-task duplicate cards, notice delivery, and content-integrity evidence.
- V4.1.x: thanks to [shutdown-awa](https://github.com/shutdown-awa) (Issue #157), [Redeemer-w](https://github.com/Redeemer-w) (Issue #159), [Cyber-Yichen](https://github.com/Cyber-Yichen) (PR #156), [wholegale39](https://github.com/wholegale39) (PR #160), [dake6767](https://github.com/dake6767) (PR #168), [foras910521-lab](https://github.com/foras910521-lab) (Issue #169), and [simon881](https://github.com/simon881) (Issue #171) for per-chat exclusion, table truncation, systemd, newer Hermes entry points, answer-delta selection, TurnRunner, and Windows migration proposals or field evidence.
- V4.2.x: thanks to [Cassius0924](https://github.com/Cassius0924) (PRs #177/#199/#205/#206), [mslchy](https://github.com/mslchy) (PRs #180/#181), [ati121](https://github.com/ati121) (Issue #187), [xingdongcai](https://github.com/xingdongcai) (Issue #188), [Cyber-Yichen](https://github.com/Cyber-Yichen) (Issue #189), [createpjf](https://github.com/createpjf) (PR #190), [Crystalxd](https://github.com/Crystalxd) (Issue #192), [simon881](https://github.com/simon881) (Issue #193), [jdysya](https://github.com/jdysya) (Issue #197), [AnyNice](https://github.com/AnyNice) (Issue #198), [Timeral](https://github.com/Timeral) (Issue #202), [chinakids](https://github.com/chinakids) (Issue #208), and [yuqianma](https://github.com/yuqianma) (Issue #183) for implementation, reproductions, and retesting across topic cards, Windows runners, repeated interactions, terminal answer integrity, Hermes 0.20, quote summaries, superseded cards, plugin-style runtime, and persistent startup.
- V4.3.x: thanks to [leavrcn](https://github.com/leavrcn) (Issues #210/#211/#212/#221/#237), [jsuper](https://github.com/jsuper) (Issue #214), [nasvip](https://github.com/nasvip) (Issues #215/#244), [mouyong](https://github.com/mouyong) (Issue #217), [Timeral](https://github.com/Timeral) (Issue #245), [Cassius0924](https://github.com/Cassius0924) (PRs #213/#220/#228), [PureWhiteWu](https://github.com/PureWhiteWu) (PR #242), and [L261173157](https://github.com/L261173157) (Issue #222 / PR #223) for key evidence or proposals around the Hybrid runtime, interaction state, persistent service, upgrade recovery, approval, topic delivery, HTTP proxy handling, and callback retry; thanks to [saulgoodmanngabriel](https://github.com/saulgoodmanngabriel) and [zhangzq](https://github.com/zhangzq) for real Hermes 0.20 / Feishu WebSocket click and streaming-resume evidence in Issue #216; and thanks to [RanHuang](https://github.com/RanHuang) for PR #226, which exposed persistent-service identity, systemd `WorkingDirectory`, and tokenless-health reconciliation gaps.
- Additional thanks to [Akes119](https://github.com/Akes119) (PR #184) and [yaoge103](https://github.com/yaoge103) (PRs #185/#186) for alternative completion-notice and interaction-identity implementations. Those patches were not merged as written because they could duplicate completion delivery or weaken profile/sequence fencing, but the explorations remain part of the public technical record.

## Security
Default `127.0.0.1` uses local-process trust; do not expose an unauthenticated sidecar to the network. Non-loopback starts only with explicit `server.allow_non_loopback: true` and requires state-directory HMAC event authentication, which does not replace TLS. Do not commit App Secret, tenant token, real chat_id, or unredacted screenshots. Production credentials belong in local config or environment variables.

Windows non-loopback startup is rejected when state-directory ACL privacy cannot be verified. Windows loopback remains available under local-process trust without claiming that ACL privacy has been verified.

## License

MIT License. See [LICENSE](LICENSE).
