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

![Hermes Feishu Streaming Card cover](docs/assets/readme-cover-v5.png)

**See what Hermes is doing, confirm the next step, and read the complete answer—all in Feishu.**

HFC turns Hermes Agent Gateway replies in Feishu/Lark into continuously updated interactive cards. Ordinary replies stay in one card. Approvals and clarification offer clickable choices, with subsequent output continuing in order while earlier content stays available.

Use it when connecting [Hermes Agent](https://github.com/NousResearch/hermes-agent) to Feishu or Lark. Hermes runs the model, tools and tasks; HFC handles card presentation, interaction and delivery.

[What’s new in V5](#whats-new-in-v5) · [Upgrade from V4.x](#upgrade-from-v4x-to-v5) · [Install](#quick-install) · [Configure](#configuration) · [Recent releases](#recent-releases) · [User guide](docs/user-guide.en.md) · [Contributors](#contributors)

**V5: follow the task, continue the conversation, keep the complete result.** This page describes v5.0.1; installable versions are listed in [GitHub Releases](https://github.com/baileyh8/hermes-feishu-streaming-card/releases). See the [release notes](docs/release-notes-v5.0.1.en.md).

## What's new in V5

V5 organizes the journey from a running task through a human decision to the final answer. Ordinary replies stay in one card. Interactions retain a decision receipt, and the earlier segment folds only after a continuation is delivered.

| Change | What you see |
| --- | --- |
| **Task sections** | Separate state, answer, process history and metrics; long answers retain their content and process details remain available |
| **Interaction continuity** | Approvals, single/multiple choices and text input use the original Hermes flow; earlier cards offer “View continuation” when a verified destination is available |
| **Clear outcomes** | Denied and timed-out tools keep distinct states; expired cards retain the question and choices but remove actionable controls |
| **Per-turn metrics** | Available model, duration, input/output tokens and context usage appear in the footer; missing values are not invented |
| **Inspectable configuration** | Compare loaded and next-load values, produce minimal YAML and preview changes offline |

Installation, recovery, event handling and rendering now have separate owners. Hermes still executes models and tools and owns authorization. Confirmed card delivery suppresses redundant native text; attachments and unconfirmed delivery retain necessary fallbacks.

To use the V5 task layout, merge these fields into the existing global `card` section and restart the sidecar. Do not replace the entire configuration. Explicit profile/bot values may still override the global scope.

```yaml
card:
  reading_preset: task
  timeline_expanded: false
```

This combination starts with the process panel collapsed and keeps it available after completion. The `task` preset itself expands the panel by default; the second line explicitly selects collapse. Upgrading does not switch `classic`, `focused` or `detailed` to `task`.

V5.0.1 supports inherited completion and approval-renderer contracts for the Hermes 0.22.x revisions pinned at `297552a9` and `7bda2a33`. PM-managed installs register the exact HFC wheel as a durable dependency for later generations. See the [maintenance guide](docs/wiki/maintenance-guide.md#hermes-022x-与-pm-依赖登记) for ownership, uninstall and validation limits.

## Why HFC

| What matters | What you get |
| --- | --- |
| **Know whether work is progressing** | Live tool activity, process history and streamed answers, with distinct running, waiting, failed and completed states |
| **Less typing and message clutter** | Approval and clarification buttons or forms, consolidated notices and fewer duplicate messages |
| **Readable long answers** | Separate answer and process areas, Markdown, code and tables, plus optional reading presets |
| **Continuity across interactions** | Ordered continuation cards after actual new output, retaining earlier content and decision receipts |
| **Fit your setup** | DMs, groups, topics, multiple bots/profiles and native-message delivery for selected chats |
| **Troubleshoot with evidence** | Status checks, compatibility diagnostics, guarded repair and restore commands |

`/model` uses the same Provider/model list as Hermes CLI, with **Provider → Model** selection; `/resume` offers a session picker. The footer can show model, duration, tokens and context usage when Hermes supplies those fields.

## See it in action

The cover is AI-generated V5 artwork, not a screenshot. The actual flow is: streamed task → your decision → decision receipt → continuation → complete result and per-turn metrics.

V5 desktop acceptance verified the timed-out state, expired choices and an actual continuation-link click. Earlier desktop interactions and phone display were recorded separately. Appearance depends on client, theme and configuration; see the [Feishu acceptance checklist](docs/wiki/feishu-acceptance.md).

| V5: continuation after approval | V5: timeout and expired choices |
| --- | --- |
| [![V5 continuation and turn footer](docs/assets/feishu-v5-continuation-redacted.png)](docs/assets/feishu-v5-continuation-redacted.png) | [![V5 timeout and expired interaction](docs/assets/feishu-v5-expiry-redacted.png)](docs/assets/feishu-v5-expiry-redacted.png) |

**Single-choice, multiple-choice and text-input receipts**

The three cards retain the single answer `A`, multiple answers `A, C` and free text `手机输入验收123`. These are submitted receipts, marked “选择已记录” (selection recorded). Pending interactions use buttons or forms; submission leaves a receipt while subsequent output continues in the reply card.

[![Submitted single-choice, multiple-choice and text-input receipts](docs/assets/feishu-v5-clarify-receipts-redacted.png)](docs/assets/feishu-v5-clarify-receipts-redacted.png)

**Command confirmation buttons**

The `/reload-mcp` confirmation offers “允许一次” (allow once), “始终允许” (always allow) and “取消” (cancel). This image comes from mobile command acceptance on an earlier candidate and shows the buttons before a decision. Model-tool approval options depend on the actual permission scope.

<img src="docs/assets/feishu-command-approval-redacted.png" alt="Mobile command confirmation: allow once, always allow and cancel" width="420">

These interface images derive from real acceptance screenshots, with generative privacy redaction and presentation layout adjustments. Local glyphs and spacing may differ from the originals. Original screenshots remain the acceptance evidence. Click to enlarge. See [image provenance](docs/assets/v5-visuals.md).

<details>
<summary>View real V4.x interface references (historical versions)</summary>

| Running | Waiting |
| --- | --- |
| ![V4 running](docs/assets/feishu-v4-runtime-running.png) | ![V4 waiting](docs/assets/feishu-v4-runtime-waiting.png) |

| Failed | Completed |
| --- | --- |
| ![V4 failed](docs/assets/feishu-v4-runtime-failed.png) | ![V4 completed](docs/assets/feishu-v4-runtime-completed.png) |

![Historical commands and tool history](docs/assets/feishu-card-showcase-v385.png)

These are real historical screenshots, not a claim that every detail matches V5.

</details>

## Upgrade from V4.x to V5

**Keep your existing bot.** Reuse the actual Hermes directory, configuration, credentials and profile. Example paths are not substitutes for your split-HOME, Docker or multi-instance layout.

1. Record current HFC/Hermes versions and service managers. Back up configuration, `.env` and installer recovery material. After tasks finish, stop Gateway, Desktop and sidecar through their existing managers.
2. Pin the installer to `v5.0.1` and supply the same configuration and credentials paths. Run it only after that tag is published. The installer checks the provenance and compatibility of the old installation.
3. If diagnostics require restoring old hooks first, use the official `restore` command they provide, then reinstall. Stop on unknown source drift; never edit installed `gateway/run.py` or delete manifests/backups to bypass checks.
4. Opt into the task layout with the two fields above if desired. Keep credentials, routing, native-table settings and other reading preferences from your existing configuration.
5. Restore the original service managers and restart Gateway/Desktop as directed. Check `status`, `doctor --explain` and `card-config --running`, then send a real message to verify the card and footer.

macOS / Linux example—replace these paths with those used by your current installation:

```bash
export HERMES_DIR="$HOME/.hermes/hermes-agent"
export HFC_CONFIG="$HOME/.hermes/config.yaml"
export HFC_ENV_FILE="$HOME/.hermes/.env"
curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/v5.0.1/install.sh | bash -s -- --version v5.0.1 --config "$HFC_CONFIG" --env-file "$HFC_ENV_FILE"
```

See the **[V4.x → V5 upgrade guide](docs/upgrade-v4-to-v5.en.md)** for Windows, Docker, split HOME, verification and rollback. Updating HFC is separate from updating Hermes; Feishu `/update` is not the plugin-version upgrade command.

## Quick install

**Before you start:** install Hermes Agent, have Python 3.9+, and connect a Feishu/Lark bot to Hermes using its App ID / App Secret. See the [installation guide](README-install.md) for setup, permissions and environment details.

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.sh | bash
```

**Windows PowerShell**

```powershell
irm https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.ps1 | iex
```

The script resolves the latest stable Release, installs into the Hermes environment, reads or prompts for credentials, writes a local `.env`, and configures hooks and the sidecar. With the package already installed, you can run setup directly (replace paths for your environment):

```bash
python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --config ~/.hermes/config.yaml --yes
python3 -m hermes_feishu_card.cli status --config ~/.hermes/config.yaml
python3 -m hermes_feishu_card.cli doctor --config ~/.hermes/config.yaml --hermes-dir ~/.hermes/hermes-agent --explain
```

Start or restart Hermes Gateway as directed by setup, then message the bot and confirm that a running card updates to the final answer. Service status alone does not verify delivery.

- **Linux:** setup enables a persistent service when the systemd user manager and linger are ready. Otherwise it warns and starts transiently. Use `--transient` to explicitly choose a temporary service.
- **macOS:** transient by default; `enable` is unsupported. For login startup, manage your own LaunchAgent with `RunAtLoad=true` running `start` once, never `KeepAlive=true`. See [installer safety](docs/installer-safety.en.md).
- **Docker:** inside an existing Hermes container, run the repository's installer:

```bash
export FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx HFC_VERSION=v5.0.1
bash install-docker.sh
```

Defaults: `HERMES_DIR=/opt/hermes`, `HFC_CONFIG=/opt/data/config.yaml`, `HFC_ENV_FILE=/opt/data/.env`. See [container installation](README-install.md) and the [Compose example](docker-compose.example.yml), which is not an official image.

## Configuration

See the [card configuration map](docs/user-guide.en.md#what-can-i-change-on-the-card) for visible regions and limits. Start with a preset; explicit fields override it, so avoid copying the entire example.

`setup` prepares the configuration. For manual changes, use [config.yaml.example](config.yaml.example) as a reference and edit the file selected with `--config`; do not overwrite an existing Hermes config.

### 1. Enable Hermes streaming

Set `streaming.enabled` in the Hermes config and use edit transport:

```yaml
streaming:
  enabled: true
  transport: edit
```

Do not set `display.platforms.feishu.streaming: false`. `display.show_reasoning` is not required by this plugin; HFC handles streaming reasoning and answers directly.

### 2. Configure credentials and cards

Minimal example (prefer `.env` for credentials):

```yaml
server:
  host: 127.0.0.1
  port: 8765
feishu:
  app_id: ""
  app_secret: ""
card:
  title: Hermes Agent
  width_mode: default  # default | compact | fill
  table_overflow_mode: compact
  native_tables: false
  footer_fields: [duration, model, input_tokens, output_tokens, context]
bindings:
  native_chats: []
integrity:
  mode: safe
service:
  manager: auto
```

`card.width_mode` supports `default`, `compact`, and `fill` (adaptive chat-window width) for JSON 2.0 cards. The default preserves existing layout; profile/bot overrides are supported, and explicit `default` resets an inherited mode. JSON 1.0 approval cards are unchanged; clients control final dimensions. See [card width configuration](docs/user-guide.en.md#card-width).

`card.native_tables` (default `false`) renders body Markdown tables as the card's native `table` element, so column widths follow the content and `row_height: auto` wraps a long cell instead of clipping it. Tables that cannot be represented natively, and cards that would exceed the safe budget because of the richer payload, fall back to the Markdown layout. See [body tables](docs/user-guide.en.md#body-tables).

Place `.env` beside the config:

```dotenv
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_CONNECTION_MODE=websocket
FEISHU_HOME_CHANNEL=oc_xxx
```

Precedence: YAML < adjacent `.env` < explicit `--env-file` < process environment. `setup` / `start --env-file ...` load the selected file without falling back to a global `.env`. Missing credentials report degraded / noop and cannot deliver cards.

### 3. Choose a reading style (optional)

```yaml
card:
  reading_preset: task
  timeline_expanded: false
```

| Preset | Reading experience |
| --- | --- |
| Omitted / `classic` | Existing behavior with a collapsed process panel |
| `focused` | Emphasize the answer; reasoning stays in the panel and successful tool rows are reduced after normal completion |
| `detailed` | Expand reasoning and tools to follow the task |
| `task` (since 4.7.0) | Separate state and activity with answer-first reading; V5 expands process details and retains completed history |

4.7.0 adds an optional `task` layout and an offline preview of nine states. See [task cards](docs/task-cards.md#english-quick-reference) for setup, configuration sources and rollback. Subscription quota appears only on GPT turns attributed to `openai-codex`; unrelated or unknown routes omit it.

Explicit fields at the same level override presets. Copying all existing display switches may override your preset; inspect effective values with `hermes-feishu-card card-config --config <config-path>` and restart the sidecar to apply changes. See [reading presets](docs/wiki/reading-presets.md).

In V5, add `--running` to verify loaded defaults, or use `--minimal-yaml` to print a compact reading-field replacement for the selected scope. Preview a redacted local report with [`doctor --support-report`](docs/wiki/local-support-report.md). These commands do not edit configuration or restart services.

| Change | Setting / reference |
| --- | --- |
| Keep live thinking out of the answer area | `card.stream_thinking_to_body: false` |
| Show only the latest live-thinking text | `card.thinking_body_tail_chars: 2400` (default `0`: unlimited) |
| Reduce successful tool rows after completion | `card.hide_completed_tool_activity: true`, retaining unsuccessful tools |
| Text sizes, footer and subscription quota | `card.text_sizes`, `card.footer_fields`; optional `subscription_usage`, `output_rate` |
| Native replies in selected chats | Exact matches in `bindings.native_chats`; configure within each profile when using multiple profiles |
| Multiple bots, groups and profiles | [Configuration and routing](docs/user-guide.en.md) |
| Optional CardKit entity streaming | [Settings and permissions](docs/wiki/cardkit-streaming.md) |

Existing configs without `integrity` retain `notify` behavior; automatic repair is not silently enabled. See [safety controls and troubleshooting](docs/wiki/v4.1-safety-controls.md).

## Everyday use and troubleshooting

| Entry point | Purpose |
| --- | --- |
| Feishu `/hfc status` | Current conversation and group binding status |
| Feishu `/hfc doctor` | Diagnostics and available repair actions |
| CLI `status` / `doctor --explain` | Process, loaded version and Hermes compatibility |
| CLI `card-config` | Effective reading configuration and its sources |
| CLI `repair` / `restore` | Repair verified state or restore original managed files |
| CLI `start --config ...` / `stop --config ...`, `enable` / `disable` | Transient sidecar and Linux persistent service management respectively |

Prefix CLI commands with `hermes-feishu-card` and use your actual config path. After a Hermes upgrade, run `doctor --explain` and follow its recovery guidance; never manually patch installed `gateway/run.py`. Compatibility is determined by source and capability checks, not only a version number.

HFC uses a **sidecar-only** architecture: Hermes executes tasks, the installer manages necessary hooks, and a separate sidecar manages card state and delivery. See the [user guide](docs/user-guide.en.md), [architecture](docs/architecture.en.md), [maintainer wiki](docs/wiki/README.md) and [testing guide](docs/testing.en.md).

<details>
<summary>More technical documentation / 更多技术文档</summary>

- Architecture / 架构：[中文](docs/architecture.md) · [English](docs/architecture.en.md)
- Event protocol / 事件协议：[中文](docs/event-protocol.md) · [English](docs/event-protocol.en.md)
- Installer safety / 安装安全：[中文](docs/installer-safety.md) · [English](docs/installer-safety.en.md)
- Migration / 迁移：[中文](docs/migration.md) · [English](docs/migration.en.md)
- E2E verification / 端到端验收：[中文](docs/e2e-verification.md) · [English](docs/e2e-verification.en.md)
- Release readiness / 发布检查：[中文](docs/release-readiness.md) · [English](docs/release-readiness.en.md)
- Testing / 测试：[中文](docs/testing.md) · [English](docs/testing.en.md)

</details>

## Recent releases

| Version | Highlights |
|---|---|
| [v5.0.1](docs/release-notes-v5.0.1.en.md) | Hermes 0.22.x completion/approval compatibility and persistent PM dependencies |
| [v5.0.0](docs/release-notes-v5.0.0.en.md) | Task sections, interaction continuity, turn metrics and installation boundaries |
| [v4.7.7](docs/release-notes-v4.7.7.en.md) | Opt-in native tables; preserve long cells and indivisible Markdown tables |
| [v4.7.6](docs/release-notes-v4.7.6.en.md) | Split HOME binding, multi-instance ownership and diagnostics |
| [v4.7.5](docs/release-notes-v4.7.5.en.md) | PM runtime binding, DM card admission and layout limitations |
| [v4.7.4](docs/release-notes-v4.7.4.en.md) | Independent sidecar runtime, heartbeat ownership and exit cleanup |
| [v4.7.3](docs/release-notes-v4.7.3.en.md) | Bounded heartbeat scans, responsive health snapshots and recovery guidance |
| [v4.7.2](docs/release-notes-v4.7.2.en.md) | Update shutdown protection, SDK callbacks, integrity recovery and configuration map |
| [v4.7.1](docs/release-notes-v4.7.1.en.md) | Slash-confirm callback repair and visible confirmation results |
| [v4.7.0](docs/release-notes-v4.7.0.en.md) | Task layout, offline previews, attributed quota and interaction-state repairs |

See [release history](docs/release-history.en.md) for earlier versions, and [CHANGELOG](CHANGELOG.md) or [GitHub Releases](https://github.com/baileyh8/hermes-feishu-streaming-card/releases) for the full record.

## Contributors

- V5.0.1: [lanx214](https://github.com/lanx214) reported [#428](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/428) and [#431](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/431); [za8209](https://github.com/za8209) supplied macOS reproduction logs and source analysis in #428. These are issue-evidence credits, separate from code authorship. Earlier code and contributor credits remain intact. Thanks to [Lite-G](https://github.com/Lite-G) for the independent compatibility analysis and proposal in [PR #432](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/432); that PR's code is not included in this release.

- #384: Thanks to [lanx214](https://github.com/lanx214), [Lite-G](https://github.com/Lite-G), and [7360403-coder](https://github.com/7360403-coder) for split-HOME, shared-source and NAS container evidence.

- [yulong-ge](https://github.com/yulong-ge) supplied PM-managed runtime installation reproduction and diagnostics in [#382](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/382).

- V4.7.7: [ywarmy](https://github.com/ywarmy) ([PR #391](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/391)), native-table implementation and API acceptance evidence; original commit authorship is preserved.
- V4.7.3: [lanx214](https://github.com/lanx214) (#378), performance evidence; [ywarmy](https://github.com/ywarmy) (#372), field verification and recovery-documentation feedback.
- V4.7.2: [Nevoker](https://github.com/Nevoker) ([PR #373](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/373)); [DaveWang888](https://github.com/DaveWang888) (#375), [ffdxdynotable](https://github.com/ffdxdynotable) (#374), [ywarmy](https://github.com/ywarmy) (#372), [jackwude](https://github.com/jackwude) (#370). Code, failure evidence and configuration-guide proposal; original commit authorship is preserved.

- V4.7.1: [baileyh8](https://github.com/baileyh8) repaired slash-confirm callbacks in [PR #371](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/371), informed by user-supplied logs. No public reporter attribution was provided.

- V4.7.0 continues the reading-experience requests from [jackwude](https://github.com/jackwude) in [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) and [leavrcn](https://github.com/leavrcn) in [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333). Commits in this release are authored by [baileyh8](https://github.com/baileyh8); all historical code, proposal and field-evidence credits remain intact.

- V4.6.13: Thanks to [ffdxdynotable](https://github.com/ffdxdynotable) for [#367](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/367), reporting control-token exposure with Termux evidence and a private-file proposal.

- V4.6.12: Thanks to [cbatbj](https://github.com/cbatbj) / chenbing1 for [PR #351](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/351) contributing scoped card width configuration and regression tests. Original commits and all earlier credits are retained.

- V4.6.11: thanks to [leavrcn](https://github.com/leavrcn) for [#362](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/362), the reproduction and tail-window patch proposal; [mouyong](https://github.com/mouyong) for the background-wait evidence in [#361](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/361); and [Dependabot](https://github.com/apps/dependabot) for [PR #363](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/363). Original code authorship and all prior credits are retained.

Thank you to everyone contributing code, proposals, reproductions and real-environment verification. All historical credits and associated PR / Issue links are retained below.

<details>
<summary>Show all contribution records</summary>

- V4.6.10: [Nevoker](https://github.com/Nevoker) (PR #355), [shichenshuo-star](https://github.com/shichenshuo-star) (PR #358); [leavrcn](https://github.com/leavrcn) (#359), [lanx214](https://github.com/lanx214) (#352), [kite40](https://github.com/kite40) (#353), [ywarmy](https://github.com/ywarmy) and [mslchy](https://github.com/mslchy) (#354), [Love4yzp](https://github.com/Love4yzp) (PR #355 deployment evidence). Original code authors are preserved; issue reporters are credited for reproduction and diagnosis.

- V4.6.9: Thanks to [cainiaozp](https://github.com/cainiaozp) for the concurrent-group reproduction and root-cause evidence in [#348](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/348) and [mouyong](https://github.com/mouyong) for the compact single-select implementation and tests in [PR #349](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/349). The original code author is preserved.

- V4.6.8: Thanks to [coder-zhw](https://github.com/coder-zhw) for the macOS installation, unknown-pidfile and login-startup investigation, implementation and regressions in [PR #347](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/347).
- V4.6.6: Thanks to [mouyong](https://github.com/mouyong) for the notice/reading and approval proposals in [PR #338](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/338) / [PR #339](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/339) and concrete evidence in [#337](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/337) / [#340](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/340). Adapted with delivery proof, bounded cleanup and preserved defaults; original code authorship remains credited. Thanks also to [tidytorch](https://github.com/tidytorch) for [PR #342](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/342): the original authored commit is retained, with a bounded lookup budget and a real lost-response HTTP regression.
- V4.6.4: thanks to [sthnow](https://github.com/sthnow) for cold-start callback and post-interaction ordering evidence in [#335](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/335), and patch author **babypanda** for the eager-hook implementation; adapted code retains `Co-authored-by`. Thanks to [mouyong](https://github.com/mouyong) for continuation/notice design and code in [PR #331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331), and the contributor-preflight request in [#330](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/330). Individual parts are adapted; this does not merge the entire PR. Optional reading presets also respond to [jackwude](https://github.com/jackwude)'s [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) and [leavrcn](https://github.com/leavrcn)'s [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333). All historical credits below are retained.
- V4.6.3: Thanks to [leavrcn](https://github.com/leavrcn) for [#333](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/333), reproduction and configuration proposal; adapted tool order/duration/interruption code from [mouyong](https://github.com/mouyong)'s [PR #331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331), retaining code authorship. Notice retirement and other changes remain separate.
- V4.6.2: Thanks to [jackwude](https://github.com/jackwude) for [#328](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/328) and the [#329](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/329) evidence for 4.6.1; [mouyong](https://github.com/mouyong) proposed `hide_completed_tool_activity` in [PR #331](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/331). Only that configuration feature is adapted here, with an opt-in default and completed/failed coverage; the rest of #331 remains under review.
- V4.6.1: Thanks to [mouyong](https://github.com/mouyong) for [PR #325](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/325) and [#326](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/326); original commits retained.
- V4.6.0: [mouyong](https://github.com/mouyong) supplied the additional [PR #310](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/310) fixes and [#320](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/320) evidence; [zhangzq](https://github.com/zhangzq) supplied structured-reasoning diagnostics in [#319](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/319); [qqqq560204-maker](https://github.com/qqqq560204-maker) isolated custom-profile recall routing in [#323](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/323). Original PR authorship is retained.
- V4.5.1–V4.5.2: [mouyong](https://github.com/mouyong) contributed [PR #310](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/310), field reports and retesting for #282, #304, #305, #307, #311–#314 and #318/#321, including the queued-outcome fix and transient notice recall; [lanx214](https://github.com/lanx214) reported [#316](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/316) and implemented extracted-clarify compatibility in [PR #317](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/317); [qqqq560204-maker](https://github.com/qqqq560204-maker) and [7360403-coder](https://github.com/7360403-coder) supplied the CardKit 300301 evidence in [#306](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/306). Original PR authorship is preserved, with maintainer safety corrections and regression coverage. The reporter withdrew #282; this does not establish a mobile fix.

- V4.4.5–V4.4.6: [tidytorch](https://github.com/tidytorch) (#286/#291), [Jentlezhi](https://github.com/Jentlezhi) (#292), [sp960817](https://github.com/sp960817), [Cyber-Yichen](https://github.com/Cyber-Yichen), [shichenshuo-star](https://github.com/shichenshuo-star), [ywarmy](https://github.com/ywarmy) (#288/#294/#296), [7360403-coder](https://github.com/7360403-coder) (#298), [mouyong](https://github.com/mouyong) (#276/#280/#282/#289/#301). Thanks for code, tests and field evidence; original PR #291/#292 commit authorship is retained.
### V4.4.3
- [mouyong](https://github.com/mouyong): the multiplex production report in [#268](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/268) and the empty-timeline feedback in [#269](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/269). The reporter's real multi-bot environment still needs retesting for #268.
### V4.4.2
- [ywarmy](https://github.com/ywarmy): [#261](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/261), Hermes 0.21 completion-marker report.
- [Ricadre](https://github.com/Ricadre): [#265](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/265), stale integrity migration reproduction.
- [mouyong](https://github.com/mouyong): [#83](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/83), [#263](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/263), [#264](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/264), [#266](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/266), Docker/source-only and multiplex evidence; [#258](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/258), approval readability feedback.
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

</details>

Before contributing, read [AGENTS.md](AGENTS.md) and the [testing guide](docs/testing.en.md). Include Hermes/HFC versions, reproduction steps and redacted logs when reporting issues.

## Security and license

The default listener is `127.0.0.1`. Non-loopback deployment requires explicit opt-in, HMAC authentication and separately configured TLS. Do not commit App Secrets, tokens, real chat identifiers or unredacted screenshots. See [installer safety](docs/installer-safety.en.md).

[MIT License](LICENSE).

<details>
<summary>Optional service and affiliate disclosure</summary>

[ScrapingAnt](https://scrapingant.com/?ref=zwq4ngy) is an optional web scraping service, not a plugin dependency. This is an affiliate link; a qualifying first paid subscription may earn the project a commission.

</details>
