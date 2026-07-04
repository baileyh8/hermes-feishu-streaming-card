# Hermes Feishu Streaming Card Plugin

[中文](user-guide.md) | [Project home](../README.en.md)

<p align="center">
  <a href="https://github.com/baileyh8/hermes-feishu-streaming-card/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/baileyh8/hermes-feishu-streaming-card?style=for-the-badge&logo=github&label=Stars&color=2f80ed"></a>
  <a href="https://github.com/baileyh8/hermes-feishu-streaming-card/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/baileyh8/hermes-feishu-streaming-card?style=for-the-badge&logo=githubactions&label=Release&color=22c55e"></a>
  <a href="https://github.com/baileyh8/hermes-feishu-streaming-card/actions/workflows/tests.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/baileyh8/hermes-feishu-streaming-card/tests.yml?branch=main&style=for-the-badge&label=Tests&logo=githubactions"></a>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="Feishu/Lark" src="https://img.shields.io/badge/Feishu%20%2F%20Lark-Streaming%20Cards-00D6B4?style=for-the-badge">
  <img alt="Sidecar only" src="https://img.shields.io/badge/Runtime-Sidecar--only-7C3AED?style=for-the-badge">
  <a href="../LICENSE"><img alt="License" src="https://img.shields.io/github/license/baileyh8/hermes-feishu-streaming-card?style=for-the-badge&color=64748b"></a>
</p>

![Hermes Feishu Streaming Card cover](assets/readme-cover.png)

Hermes Feishu Streaming Card turns Hermes Agent Gateway replies in Feishu/Lark into one continuously updated interactive card. Reasoning, tool calls, final answers, approvals, choices, and runtime stats stay in one readable card instead of spilling into scattered native text messages.

It targets the real pain points of using Hermes inside Feishu: missing or out-of-order streaming text, long tables/code blocks rendered as raw Markdown, invisible tool progress, manual approval replies, sidecar troubleshooting, multi-bot/profile routing, and uncertain hook compatibility after Hermes upgrades.

![Hermes Feishu card slash command interaction, command result feedback, and tool timeline showcase](assets/feishu-card-showcase-v385.png)

Since V3.8.2, the final answer stays in the primary content area while pre-tool answer blocks follow a "show in main body -> archive when the next block arrives" rhythm. Reasoning and tools use different text sizes and visual weight inside the collapsible auxiliary timeline, and the footer no longer repeats the same tool-call summary.

## Project Highlights

- **Streaming card UX**: `thinking.delta`, `answer.delta`, `tool.updated`, and terminal events update one Feishu card.
- **In-card interactions**: Hermes approval and clarify choices prefer Feishu buttons. Since V3.8.5, Feishu/Lark WebSocket long-connection deployments also render independent slash-command confirmations, pickers, and execution results such as `/new`, `/reset`, and `/model` as native interactive cards, falling back to Hermes native text only when cards are unavailable.
- **Runtime notices stay tidy**: Since V3.8.8, native Hermes `Working` heartbeats, context/compression notices, automatic session resets, skill loading, and self-improvement reviews prefer cards or compact notice cards instead of scattered gray native text.
- **Consistent topic replies**: Since V3.8.9, Feishu/Lark topic reply streams and system notices resolve back to the same card, avoiding frozen topic timelines and duplicate gray native notices.
- **Long content protection**: Markdown tables and fenced code blocks split on structure boundaries instead of raw character cuts.
- **Multi-bot / multi-profile**: bot registry, chat bindings, profile-aware session keys, titles, and routing diagnostics.
- **sidecar-only runtime**: Hermes hook stays fail-open while Feishu delivery, session state, retries, and health checks live in the sidecar.
- **Install and release friendly**: one-line installers, Release packages, `doctor`, `start/status/stop`, and safe restore/uninstall flows.

## Pain Points Solved

| Pain point | Project capability |
|---|---|
| Feishu only shows a final wall of text | Reasoning, answer, tool status, and runtime footer stream into one card |
| Tool-heavy runs lose text, reorder chunks, or spill native gray messages | per-message ordering, PATCH coalescing, terminal priority, and native resend suppression |
| Hermes emits separate gray `Working`, context, skill loading, or review notices | `system.notice` cardification: session notices enter the auxiliary timeline; task-external notices use compact standalone cards |
| Topic replies show the first card but the timeline stops updating, while notices also appear outside the card | Topic events resolve by `reply_to_message_id`, keeping updates on the original card and suppressing duplicate native notice text |
| Approval, choice prompts, or slash-command confirmations require manual text replies | Agent-turn choices stay in the active card; independent slash commands use standalone command cards, with numbered text fallback when cards are unavailable |
| Long tables/code blocks render as raw Markdown | Markdown-aware table/code splitting with repeated headers and complete fences |
| Multi-bot, group, and profile routing is hard to inspect | `bindings.chats`, profile-aware sessions, and `/health.routing` diagnostics |
| Hook or sidecar failures are hard to debug | `doctor`, runtime import checks, `/health` metrics, fail-closed installer, restore/uninstall |

## V3.8.9 Feishu Topic Card Continuity Patch

V3.8.9 fixes Feishu/Lark topic reply flows where the first card appears, but later `answer.delta`, `thinking.delta`, `tool.updated`, or `system.notice` events can miss the same card because Hermes switches to a different internal streaming `message_id`. In that state, system notices can also appear both inside the card timeline and as separate gray native messages.

- **One card keeps updating inside the topic**: the sidecar maps later topic events back to the active card session through `reply_to_message_id`.
- **No duplicate gray notices after cardification**: when a session-scoped `system.notice` is accepted into the card, the sidecar returns `applied: true`; if card delivery briefly times out, recognized Hermes system notices are still suppressed instead of being resent as gray native text.
- **Hermes v0.18.x Relay metadata support**: the hook runtime preserves Relay `source.message_id` as the original topic reply anchor.

![Feishu topic reply card continuity and reasoning/tool timeline showcase](assets/feishu-topic-card-showcase-v389.png)

The screenshot above shows a real Feishu topic verification: the topic reply pane keeps the card updating, the reasoning/tool timeline, final answer, and footer stats stay on the same card. Context and self-improvement notices enter cards or compact standalone cards instead of spilling out as extra gray native messages.

Full release notes: [docs/release-notes-v3.8.9.md](release-notes-v3.8.9.md).

## V3.8.8 Hermes Native System Notice Cardification

V3.8.8 folds native Hermes gray runtime notices into the Feishu/Lark card experience. `Working` heartbeats, context-window/compression notices, automatic session resets, skill loading, and self-improvement review messages are classified as `system.notice`. If the active task card can still update, the notice goes into the auxiliary "Reasoning and Tools" timeline; otherwise it is sent as a compact standalone notice card.

- **Fewer scattered gray notices**: context, compression, reset, skill-loading, and review messages prefer card delivery.
- **Heartbeats update one entry**: `Working — iteration ...` notices reuse the same `notice_id`, avoiding repeated timeline spam.
- **Still fail-open**: if the sidecar is unavailable, the notice is unknown, or card delivery fails, Hermes native text fallback still runs.

Full release notes: [docs/release-notes-v3.8.8.md](release-notes-v3.8.8.md).

## V3.8.7 Newer Hermes First-Event Compatibility Patch

V3.8.7 fixes issue #75: some newer Hermes streams can start directly with `answer.delta`, `thinking.delta`, `tool.updated`, or `message.completed` instead of sending `message.started` first. Older sidecar versions had no session yet, counted those events as `events_ignored`, and never sent the initial Feishu/Lark card. These message events can now create the card session and send the first card.

- **No hard dependency on `message.started`**: answer, thinking, tool, and completed first events can all start a card.
- **Existing paths remain compatible**: normal `message.started`, interaction, cron completion, and terminal diagnostics behavior is preserved.
- **Stacks with V3.8.6**: Docker/source-stripped Hermes roots without `VERSION` still use Gateway anchor fallback.

Full release notes: [docs/release-notes-v3.8.7.md](release-notes-v3.8.7.md).

## V3.8.6 Docker / Hermes v0.18.0 Compatibility Patch

V3.8.6 fixes the issue #70 Docker install path. Upstream Hermes v0.18.0 / `v2026.7.1` can be deployed without a top-level `VERSION` file, and container images often omit local `.git` metadata too. `doctor --explain`, `install`, and `setup` now continue by reading verified `gateway/run.py` anchors; when those anchors match, the installer uses `gateway_run_013_plus` instead of failing with `Hermes VERSION missing, unknown, or invalid`.

- **Hermes v0.18.0**: `v2026.7.1`, `0.18.0`, and `v0.18.0` are in the compatibility matrix and keep using `gateway_run_013_plus`.
- **Docker no-VERSION fallback**: diagnostics report `version_source: gateway anchors`, `version: unknown`, and the inferred `hook_strategy`.
- **Explicit bad versions still fail closed**: only missing metadata falls back to anchors; invalid `VERSION` contents still block install.

Full release notes: [docs/release-notes-v3.8.6.md](release-notes-v3.8.6.md).

## V3.8.5 Command Result Card Feedback Patch

V3.8.5 completes the V3.8.4 always-allowed/no-confirm path. When Hermes directly executes `/new`, `/reset`, `/clear`, `/undo`, `/stop`, or direct `/model <model>`, the command result now replies as a Feishu/Lark interactive card instead of gray native text. `/model` switch feedback stays in a green card, while `/update` remains Hermes' background upgrade command and does not render an interaction card.

- **Direct command results become cards**: the patcher passes the current `event` into hook runtime, so Feishu adapter `send()` can recognize standalone slash-command results.
- **Cleaner interactive updates**: button and dropdown clicks rely on the Feishu callback response to update the original card, without an extra unsupported interactive `message.update` call.
- **Upgrade compatible**: rerunning `install` rewrites the V3.8.4 command-card hook block into the V3.8.5 `event=event` form.

Full release notes: [docs/release-notes-v3.8.5.md](release-notes-v3.8.5.md). The previous WebSocket-native command-card hotfix is documented in [docs/release-notes-v3.8.4.md](release-notes-v3.8.4.md).

## V3.8.4 Feishu WebSocket Command Cards Hotfix

V3.8.4 fixes the V3.8.3 local/private sidecar gap where slash commands still fell back to gray native text. Confirmations for `/new`, `/reset`, and `/undo` now reuse Hermes' Feishu adapter WebSocket card-action channel to send native interactive cards; `/model` uses the same native card path for model selection. Active Agent streaming cards still own approval, clarify, conversation choices, and reasoning/tool timelines; slash commands do not get merged into an unrelated Agent card.

- **WebSocket-native confirmation cards**: the plugin dynamically installs Feishu adapter `send_slash_confirm(...)`, and button clicks route through `_on_card_action_trigger` into `tools.slash_confirm.resolve(...)`.
- **WebSocket-native model picker**: when Hermes asks the Feishu adapter for `send_model_picker(...)`, the plugin installs a Feishu-only picker and writes the callback result back to the same command card.
- **No duplicate choice cards**: when WebSocket-native command cards are available, the sidecar pre-interaction is skipped so `/new` does not show both a sidecar choice card and a native button card.
- **No `/update` interaction card**: `/update` remains Hermes's background upgrade command and does not render interactive buttons.
- **Safe fallback**: if native Feishu cards, the sidecar, polling, or completion updates fail, Hermes native text behavior remains available.

Full release notes: [docs/release-notes-v3.8.4.md](release-notes-v3.8.4.md). The previous standalone command-card baseline is documented in [docs/release-notes-v3.8.3.md](release-notes-v3.8.3.md).

## V3.8.2 Card Timeline Readability Patch

V3.8.2 focuses on the real Feishu reading experience inside the auxiliary timeline. Natural-language pre-tool answer blocks stay in the main body until the next pre-tool answer or terminal event arrives, then move into "Reasoning and Tools"; terminal cards strip already archived intermediate prefaces so the primary content keeps only the final answer.

- **Delayed pre-tool answer folding**: a pre-analysis block no longer flashes away immediately; it is archived only when the next block or terminal state arrives.
- **Cleaner terminal cards**: completed cards remove archived preface text when the final answer already contains it.
- **Clearer timeline hierarchy**: reasoning entries use small primary text, while tool details use smaller, lighter quoted text so long commands do not dominate the answer.
- **Raw thinking stays hidden**: low-level `thinking.delta` remains internal stream state; the timeline only shows user-readable pre-tool answer text.

Full release notes: [docs/release-notes-v3.8.2.md](release-notes-v3.8.2.md).

## V3.8.1 High-Frequency Streaming and In-Feishu Diagnostics Patch

V3.8.1 fixes issue #74. With Hermes Agent 0.17.0+, thinking models, long context, and token-by-token high-frequency deltas, the hook now coalesces `thinking.delta` / `answer.delta` inside the Hermes Gateway process before sending fewer events to the sidecar. This reduces object allocation, lock contention, and HTTP scheduling pressure on the stream-reader thread, avoiding `Stream stale for 180s` failures caused by the plugin path.

- **Gateway-side delta coalescing**: adds `HERMES_FEISHU_CARD_DELTA_COALESCE_MS`, `HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS`, and `HERMES_FEISHU_CARD_DELTA_COALESCE_MAX_PENDING`; defaults work without extra config.
- **Flush before terminal state**: `message.completed` / `message.failed` flush pending deltas for the same message before rendering the terminal card.
- **Read-only commands inside Feishu**: `/hfc help`, `/hfc status`, `/hfc doctor`, and `/hfc monitor` reply with diagnostic cards and do not perform write actions.
- **Safer diagnostics**: `/messages/{message_id}/summary` and `/hfc` cards expose hashed chat/message/thread context instead of raw ids.

Full release notes: [docs/release-notes-v3.8.1.md](release-notes-v3.8.1.md).

## V3.8.0 Card UX and Streaming Stability Upgrade

V3.8.0 focuses on the real Feishu reading experience. The final answer remains in the main content area, reasoning / tool timeline data moves into the auxiliary area, and terminal rendering drains pending updates before writing the final card.

- **Clearer primary answer**: long reasoning and tool lists no longer push the final response out of the main reading area.
- **Less duplication**: when the auxiliary timeline is visible, the footer does not render another "N tool calls" summary.
- **More stable terminal state**: pending card updates are drained before the terminal card render so stale intermediate snapshots do not win the last PATCH.
- **More accurate diagnostics**: `doctor` runs the Hermes runtime import check from the Hermes project root, avoiding false positives from the current repository path.
- **Docker examples updated**: V3.8.0 refreshed the Docker install examples while still using `install-docker.sh` inside existing Hermes containers.

Full release notes: [docs/release-notes-v3.8.0.md](release-notes-v3.8.0.md).

## V3.6.6 Interrupt Dedupe and Install Diagnostics Patch

V3.6.6 fixes issues #67 and #68. `message.completed` now uses the sidecar `applied` response to decide whether the card path really took ownership, and terminal events no longer wait for slow Feishu PATCH calls before ACKing Hermes. This prevents interrupted or backlogged sessions from producing both a streaming card and a native gray text reply. `doctor --explain` / `install` also detect a wrong `--hermes-dir` by reading the `Project:` path from `hermes -V` and suggesting the correct Hermes root.

Full release notes: [docs/release-notes-v3.6.6.md](release-notes-v3.6.6.md).

## V3.7.0 Docker Deployment Adapter

V3.7.0 adds issue #70: existing Hermes Docker container upgrade and install support.

Full release notes: [docs/release-notes-v3.7.0.md](release-notes-v3.7.0.md).

## V3.6.5 Streaming Terminal Stability Patch

V3.6.5 fixes issues #64 and #65. In Feishu thread scenarios, `message.started` now uses the same reply anchor as streaming callbacks for the card session `message_id`, preventing `events_applied=0`. For burst-output models such as DeepSeek, completed events can now backfill the final answer from `message.completed` / `agent_result.final_response` even when no `thinking.delta` or `answer.delta` events were emitted.

Full release notes: [docs/release-notes-v3.6.5.md](release-notes-v3.6.5.md).

## V3.6.4 Thread Reply and Cron Delivery Patch

V3.6.4 fixes issues #61 and #62. When a user sends a message from a Feishu thread, the initial streaming card is now sent back into the same thread through the Feishu reply API and subsequent updates keep patching that card. Cron jobs configured with `deliver: "feishu:oc_xxx"` now parse the chat id from `deliver`, so scheduled jobs can render as cards instead of falling back to plain text.

Full release notes: [docs/release-notes-v3.6.4.md](release-notes-v3.6.4.md).

## V3.6.3 Hermes v0.17 Compatibility and Interaction Patch

V3.6.3 fixes issues #56-#59. For Hermes v0.17.0+ / `v2026.6.19+`, where the real streaming implementation can move into `_run_agent_inner`, the patcher now injects `tool.updated`, `answer.delta`, `thinking.delta`, clarify, and approval hooks into `_run_agent_inner` before falling back to `_run_agent`.

localhost/private sidecars keep numbered text fallback for sidecar-owned choices, while V3.8.5 uses the Feishu/Lark WebSocket long-connection card-action path for native slash/model command cards. Those standalone commands do not require a public HTTP callback, and direct command execution results also stay in cards.

This release also ignores non-Feishu platforms such as Telegram at runtime event construction, and correctly resolves Windows profile paths like `C:\Users\...\AppData\Local\hermes\profiles\thinking`.

Full release notes: [docs/release-notes-v3.6.3.md](release-notes-v3.6.3.md).

## V3.6.2 Runtime Install Patch

V3.6.2 fixes issue #53: `setup` / `install` now detects the Python interpreter that Hermes Gateway actually runs from, such as `~/.hermes/hermes-agent/venv/bin/python`, and installs the same `hermes-feishu-streaming-card` release into that runtime venv before patching `gateway/run.py`.

`doctor --explain` and `doctor --json` now include a `runtime_import` check for `hermes_feishu_card.hook_runtime`. Hook import/emit failures also remain fail-open but are no longer fully silent; Hermes stderr gets a `[hermes-feishu-card] hook failed: ...` diagnostic warning.

Full release notes: [docs/release-notes-v3.6.2.md](release-notes-v3.6.2.md).

## V3.6.1 Compatibility Patch

V3.6.1 fixes issue #47: Hermes `VERSION` values without a leading `v`, such as `0.15.1`, are no longer reported as unsupported by `doctor --explain`. Hermes `0.15.x` / `v0.15.x` continues to use `gateway_run_013_plus` when the required `gateway/run.py` anchors are present.

Full release notes: [docs/release-notes-v3.6.1.md](release-notes-v3.6.1.md).

## V3.6.0 Operations Upgrade

V3.6.0 focuses on real deployment operations after the streaming-card baseline is already in place: diagnosing hook/sidecar/Hermes state, repairing safe installer drift, keeping media/file delivery visible, and making multi-profile routing easier to verify.

- **Readable and machine-readable diagnostics**: `doctor --explain` summarizes the next action for humans, while `doctor --json` reports config, sidecar, Hermes, streaming, install state, and recommendations.
- **Safe installer repair**: `repair --hermes-dir ... --yes` and `setup --repair` rebuild only verifiable manifest/backup state and refuse unverifiable user edits.
- **Media/file safety**: structured Hermes `attachments`, `files`, `media_files`, and image/audio/video objects are summarized in cards while native Hermes media/file delivery remains unsuppressed.
- **Multi-profile operations**: `smoke-feishu-card --profile-id`, `bots test --profile-id`, CLI `status`, and `/health.routing.profiles` expose profile-scoped routing state.
- **Compatibility matrix**: tests cover Hermes `v2026.4.23`, `v2026.5.7`, `v2026.5.16+`, `v2026.5.29`, `v2026.6.19+`, `v2026.7.1+`, `0.13.x`, `0.14.x`, `0.15.x`, `0.17.x`, `0.18.x`, and semver `VERSION` values with or without a `v` prefix.

Full release notes: [docs/release-notes-v3.6.0.md](release-notes-v3.6.0.md).

## V3.5.x Runtime Baseline

- **End-to-end ordering for one card**: runtime sends, sidecar updates, and terminal Feishu PATCH calls are ordered/coalesced by message id, reducing thinking/answer truncation under backlog.
- **Faster streaming updates**: sidecar events ACK quickly while card updates are coalesced; terminal events prioritize the final card update in the background.
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
| `HFC_VERSION` | `latest` | Version to install, such as `v3.8.9`, `v3.6.6`, or `main` |
| `HERMES_DIR` | `~/.hermes/hermes-agent` | Hermes Agent Gateway directory |
| `HFC_CONFIG` | `~/.hermes/config.yaml` | sidecar config path |
| `HFC_ENV_FILE` | `.env` next to `HFC_CONFIG` | Feishu credential file |
| `HFC_SKIP_START` | `0` | Set to `1` to install the hook without starting sidecar |
| `HFC_NO_PROMPT` | `0` | Set to `1` for non-interactive automation |

High-frequency streaming knobs usually do not need manual tuning:

| Variable | Default | Description |
|---|---|---|
| `HERMES_FEISHU_CARD_DELTA_COALESCE_MS` | `250` | Maximum Gateway-runtime wait before flushing coalesced deltas; set `0` to disable |
| `HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS` | `600` | Flush immediately once pending deltas reach this character count |
| `HERMES_FEISHU_CARD_DELTA_COALESCE_MAX_PENDING` | `128` | Maximum pending delta sessions kept in Gateway runtime |

## Docker Containers

Use `install-docker.sh` inside an existing Hermes container. It defaults to
`/opt/hermes` for Hermes and `/opt/data/config.yaml` for sidecar config. The
script selects Hermes venv Python and does not fall back to system Python unless
`HFC_PYTHON` is set.

Example:

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export HFC_VERSION=v3.8.9
bash install-docker.sh
```

GitHub Releases also include `hermes-feishu-card-<version>-macos.tar.gz`, `hermes-feishu-card-<version>-linux.tar.gz`, and `hermes-feishu-card-<version>-windows.zip`. Download one, extract it, and run `install.sh` or `install.ps1`. See [README-install.md](../README-install.md) for package details.

## Manual Install

```bash
git clone https://github.com/baileyh8/hermes-feishu-streaming-card.git
cd hermes-feishu-streaming-card && pip install -e ".[test]"
export FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx
python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --yes
```

`setup` generates config, validates Hermes (older Hermes from `v2026.4.23` through `v2026.4.x`, plus Hermes `0.13.0+`, `0.14.0`, `0.15.x`, `0.17.x`, `0.18.x` / `v2026.5.16+` / `v2026.6.19+` / `v2026.7.1+` anchors), installs the package into the Hermes Gateway runtime venv Python, installs the hook, starts the sidecar, and checks health — all in one pass. Hermes semantic `VERSION` values may include or omit the `v` prefix. Since V3.8.6, Docker/source-stripped installs without `VERSION` or `.git` metadata can fall back to verified `gateway/run.py` anchors.

## Core Features

- **Multi-profile in-process** (new in V3.3.0): one sidecar serves multiple Hermes profiles with `profile_id:message_id` composite keys for session isolation and per-profile credentials/bot routing
- **Multi-bot routing & group chat**: register bots in `bots.items`, map `bindings.chats` to `chat_id`, fallback/default bot for unmatched sessions
- **Profile / bot card titles**: global, profile, and bot card titles are supported, with bot-level titles taking precedence
- **Streaming thinking**: renders `thinking.delta`, filters `<think>`/`</think>` and DeepSeek `<thinking>`/`</thinking>` tags
- **Progressive answer**: streams `answer.delta` into one card, replaces thinking on completion
- **Approval/choice interactions**: Hermes approval, clarify choices, and independent slash commands prefer Feishu button cards; when unavailable they fall back to numbered/text choices and Hermes' native text reply path
- **Cron final cards and reply context**: cron jobs can deliver final cards, and reply cards preserve the needed context
- **Attachment summaries and native media delivery**: cards show attachment summaries while the hook keeps Hermes native media/file delivery paths unsuppressed
- **Tool call tracking**: `tool.updated` shows cumulative call count and status
- **Runtime footer**: duration, model, tokens, context %. Non-terminal cards show a rotating braille spinner
- **Table limit protection** (new in V3.3.0): auto-truncates tables exceeding Feishu's 5-table limit with a notice appended
- **Platform check fix** (new in V3.3.0): non-Feishu platforms no longer swallowed by the complete hook
- **Fault isolation**: sidecar unavailable → Hermes hook fail-open, native text continues working
- **Safe installer**: fail-closed, checks version and code structure before writing. `restore`/`uninstall` refuse on modified files

## Upgrading

Upgrading from V3.2.x/V3.3.0/V3.4.x/V3.5.x/V3.6.x/V3.7.x/V3.8.0/V3.8.1/V3.8.2/V3.8.3/V3.8.4/V3.8.5/V3.8.6/V3.8.7/V3.8.8 to V3.8.9 is backward-compatible. **Single-profile configs need no changes.** If Hermes uses its own venv, rerun `setup` or `install` after upgrading so the package also lands in the Hermes runtime Python and the hook is refreshed. V3.8.9 keeps V3.8.8's native Hermes system notice cardification, V3.8.7's newer-Hermes first-event compatibility, and V3.8.6's Docker/Hermes v0.18.0 compatibility, then fixes topic reply card continuity and duplicate native notice fallback; run `doctor --explain` once after upgrading and verify both a normal Feishu chat and a topic reply with a normal prompt, `/new`, `/model`, or a long-running/context-notice task.

```bash
# 1. Stop sidecar
python3 -m hermes_feishu_card.cli stop --config ~/.hermes_feishu_card/config.yaml

# 2. Update code
cd /path/to/hermes-feishu-streaming-card
git checkout v3.8.9 && pip install -e ".[test]" --upgrade

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
| `stop --config ...` | Stop sidecar (validates PID/token against `/health` `process_pid/process_token_hash`) |
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

The Hermes hook converts `message.started` / `thinking.delta` / `answer.delta` / `tool.updated` / `message.completed` / `message.failed` into `SidecarEvent` and forwards to the sidecar. The sidecar owns full session state and the Feishu CardKit boundary — independently testable, restartable, and diagnosable. Historical code is archived under `legacy/` (`installer_v2.py`, `gateway_run_patch.py`, `patch_feishu.py`, etc.) — not the active runtime. Current development uses `hermes_feishu_card/`. See [docs/migration.en.md](migration.en.md).

## FAQ

- **No thinking / not streaming**: check Hermes `streaming.enabled: true` + `streaming.transport: edit`, confirm model exposes reasoning deltas. Don't blindly enable `show_reasoning`.
- **No real Feishu cards**: without credentials, the sidecar uses a no-op client. In multi-profile mode, check each profile's `feishu` config.
- **Hook installed but cards never arrive**: run `doctor --explain` and check `Runtime import`. If Hermes runtime cannot import `hook_runtime`, rerun `setup` or `install --hermes-dir ... --yes`.
- **Duplicate cards**: inspect `/health` metrics (`events_received`, `feishu_send_successes`). V3.3.0 per-message lock + `profile_id:message_id` keys ensure one card per message.
- **Multi-profile route is unclear**: run `status --config ...` and inspect `routing.last_route`, `profile.<id>.events`, and `profile.<id>.last_profile_source`, then verify directly with `smoke-feishu-card --profile-id ...` or `bots test --profile-id ...`.
- **Gray native text**: after sidecar accepts `message.completed`, Hermes hook suppresses native text; fail-open on sidecar unavailable. V3.3.0 fixes non-Feishu platforms being swallowed.
- **`doctor` unsupported**: Hermes ≥ `v2026.4.23` (reads `VERSION` or Git tag `v2026.4.23+`), `gateway/run.py` must exist.
- **No cards after upgrading Hermes 0.13.0+/0.14.0/0.15.x/0.17.x/0.18.x**: run `doctor --config ... --hermes-dir ...` to inspect `hook_strategy`, `compatibility`, and anchors, then re-run `install --hermes-dir ... --yes` if needed.
- **Restore fails**: file modified → `restore`/`uninstall` refuse to overwrite. Run `doctor --explain` to inspect manifest/backup/run.py state; if it reports an automatic repair path, run `repair --hermes-dir ... --yes`, otherwise back up and manually diff.
- **Footer tokens wrong**: abnormal values filtered; if still wrong, inspect Hermes `tokens`/`context` metadata.
- **Table limit exceeded**: V3.3.0 auto-truncates >5 tables with a notice. Reduce Markdown tables.

## Version History

| Version | Date | Highlights |
|---------|------|-----------|
| [v3.8.9](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.9) | 2026-07 | Feishu/Lark topic card continuity: later stream events and `system.notice` resolve by `reply_to_message_id`, keeping the topic timeline updating and avoiding duplicate gray notices |
| [v3.8.8](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.8) | 2026-07 | Native Hermes system notice cardification: Working heartbeats, context/compression notices, session resets, skill loading, and self-improvement reviews enter cards or compact notice cards |
| [v3.8.7](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.7) | 2026-07 | issue #75: first `answer.delta` / `thinking.delta` / `tool.updated` / `message.completed` events create a card when newer Hermes omits `message.started` |
| [v3.8.6](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.6) | 2026-07 | issue #70 Docker/source-stripped Hermes fallback from missing `VERSION` to Gateway anchors, plus Hermes v0.18.0 / `v2026.7.1` compatibility |
| [v3.8.5](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.5) | 2026-07 | Keeps always-allowed `/new` and similar direct command results in Feishu/Lark cards, and removes unsupported interactive direct updates |
| [v3.8.4](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.4) | 2026-07 | Feishu/Lark WebSocket-native slash/model command cards, fixing the V3.8.3 local sidecar gray text fallback gap |
| [v3.8.3](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.3) | 2026-07 | Standalone slash-command cards for `/new`/`/reset`/`/undo` confirmations, `/model` picker cards, `/update` non-interactive boundary, and text fallback |
| [v3.8.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.2) | 2026-07 | Card timeline readability patch with delayed pre-tool answer archival, terminal body de-duplication, separate reasoning/tool hierarchy, and fresh collapsed/expanded screenshots |
| [v3.8.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.1) | 2026-07 | Fixes issue #74 with Gateway-side high-frequency delta coalescing, terminal pre-flush, read-only `/hfc` diagnostics, and hashed diagnostic context |
| [v3.8.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.8.0) | 2026-07 | Separates the primary answer from the auxiliary timeline, removes duplicate tool summaries, drains terminal updates, fixes runtime import diagnostics, and updates Docker examples |
| [v3.7.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.7.0) | 2026-06 | Adds issue #70 Docker container install/update support with `/opt/hermes` and `/opt/data` defaults |
| [v3.6.6](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.6) | 2026-06 | Fixes issues #67/#68 by preventing interrupt/slow-PATCH card + native double replies and by suggesting the real Hermes `Project:` path from `hermes -V` |
| [v3.6.5](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.5) | 2026-06 | Fixes issues #64/#65 with Feishu thread `message_id` normalization and DeepSeek completed-only final-answer backfill |
| [v3.6.4](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.4) | 2026-06 | Fixes issues #61/#62 with Feishu thread card replies and cron `deliver: "feishu:oc_xxx"` card routing |
| [v3.6.3](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.3) | 2026-06 | Fixes issues #56-#59 with Hermes v0.17 `_run_agent_inner` hooks, localhost interaction text fallback, Telegram isolation, and Windows profile paths |
| [v3.6.2](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.6.2) | 2026-06 | Fixes issue #53 with Hermes runtime venv installs, doctor runtime import checks, and hook failure warnings |
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

Full changelog: [CHANGELOG.md](../CHANGELOG.md).

## Testing

```bash
python3 -m pytest -q    # run the full automated regression suite
```

Coverage: real Hermes Gateway E2E, real Feishu app card verification, 16k long-card stress test, `doctor → install → restore` loop, multi-profile routing, DeepSeek tag filtering.

## Documentation

- Maintainer wiki: [docs/wiki](wiki/README.md)
- Architecture: [中文](architecture.md) / [English](architecture.en.md)
- Event protocol: [中文](event-protocol.md) / [English](event-protocol.en.md)
- Installer safety: [中文](installer-safety.md) / [English](installer-safety.en.md)
- Migration: [中文](migration.md) / [English](migration.en.md)
- E2E verification: [中文](e2e-verification.md) / [English](e2e-verification.en.md)
- Release readiness: [中文](release-readiness.md) / [English](release-readiness.en.md)
- Testing: [中文](testing.md) / [English](testing.en.md)

## License

MIT License. See [LICENSE](../LICENSE).

## Contributors

Thanks to these contributors for improving the project:

- [gischuck](https://github.com/gischuck) — [PR #12](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/12) Accept-Encoding fix (V3.2.1 brotli compatibility)
- [gischuck](https://github.com/gischuck) — [PR #76](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/76) reasoning/tool timeline UX proposal and implementation exploration (V3.8.x)
- [fengs2021](https://github.com/fengs2021) — [PR #17](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/17) lock optimization and update interval improvement (V3.3.0)

## Security

Do not commit App Secret, tenant token, or real chat_id. Screenshots demonstrate card rendering only. Production credentials belong in local config or environment variables.
