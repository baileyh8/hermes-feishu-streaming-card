# Hermes 飞书流式卡片插件

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

![Hermes Feishu Streaming Card 封面](docs/assets/readme-cover.png)

Hermes 飞书流式卡片插件把 Hermes Agent Gateway 的飞书/Lark 回复变成一张持续更新的交互式卡片。思考过程、工具调用、最终答案、授权确认、选项选择、系统提示和运行统计会收束在卡片内，而不是散落成多条灰色原生消息。<br><br>它面向真实飞书使用场景：流式内容漏字/乱序、长表格和代码块变成 raw markdown、工具过程不可见、approval/clarify 需要手工回复、话题里卡片不更新、多 bot / 多 profile 难排查，以及 Hermes 升级后 hook 兼容不确定。

![Hermes 飞书卡片命令交互、结果反馈与工具 timeline 展示](docs/assets/feishu-card-showcase-v385.png)

## V4 实时 Agent 状态

| 运行中 | 等待用户 |
|---|---|
| ![真实飞书运行态：Header 实时显示当前工具动作](docs/assets/feishu-v4-runtime-running.png) | ![真实飞书等待态：原生按钮保持在同一张卡片](docs/assets/feishu-v4-runtime-waiting.png) |
| 失败 | 已完成 |
| ![真实飞书失败态：保留最后工具预览](docs/assets/feishu-v4-runtime-failed.png) | ![真实飞书完成态：仅保留原生回复 Header 与最终结果](docs/assets/feishu-v4-runtime-completed.png) |

运行时 Header 跟随 Hermes 的真实工具动作更新，公开阶段输出继续在正文流式呈现；完成后只保留飞书原生回复引用，不再叠加一层 `Hermes Agent` 卡片标题。

## 你能看到什么

- **一张持续更新的飞书卡片**：`thinking.delta`、`answer.delta`、`tool.updated`、`message.completed` 会合并到同一张卡片。
- **运行态 Header 看见当前动作**：Header title 保留用户自定义标题（默认 `Hermes Agent`），subtitle 将工具名与 `tool.updated.detail` 整理为实时动作摘要；完整命令留在 timeline。
- **主答案和过程分区**：最终答案留在正文区，pre-tool answer、工具调用、系统 notice 进入“思考与工具” timeline。
- **卡片内交互**：approval / clarify choices 渲染为按钮；`/new`、`/reset`、`/undo`、`/model` 等独立命令使用原生 interactive card。V4 的 `/model` 与 Hermes CLI 使用同一 Provider/模型列表，按 Provider → Model 两级选择，不再把全部模型挤进一个下拉框。
- **飞书话题一致体验**：话题里的后续流式事件通过 `reply_to_message_id` 回到原卡片，系统提示不再重复外溢。
- **群聊诊断更清楚**：`/hfc status` 会提示群内 chat binding 状态、绑定命令和 slash command 行为边界。
- **运维卡有明确边界**：`/hfc doctor` 可给出诊断、两步安全修复和重启确认；私聊不比较操作者，群聊只允许发起者确认。运维卡不可用时继续使用 CLI，不改变普通流式卡的 layout 或 footer。
- **长内容保护**：长 Markdown 表格、fenced code block 按结构边界拆分，降低 raw markdown 和半截围栏问题。
- **可诊断、可恢复**：`doctor`、`/hfc status`、`/health` metrics、runtime import 检查、safe repair/restore/uninstall 覆盖常见故障。

## 适用场景

| 你遇到的问题 | 插件做的事 |
|---|---|
| 飞书里只看到最终文本，看不到 Agent 过程 | 把思考、工具、答案、footer 统计放进同一张卡片 |
| 运行中不断出现 `Working`、压缩提示、skill loading、自我改进 review | 识别为 `system.notice`，进入当前卡片或独立小卡片 |
| 话题回复里卡片发出来了，但 timeline 不更新 | 用 `source.message_id` / `reply_to_message_id` 锚定同一张话题卡片 |
| 授权、选择、模型切换要手工回编号 | 优先使用飞书按钮或下拉选择，失败时再退回文本 fallback |
| Hermes 升级后不知道 hook 是否兼容 | `doctor --explain` 展示 `version_source`、`hook_strategy`、`compatibility`、anchors 和建议 |

## 快速安装

macOS / Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/baileyh8/hermes-feishu-streaming-card/main/install.ps1 | iex
```

安装脚本会安装或升级插件、读取/提示飞书凭据、写入本地 `.env`，并调用整合安装器：

```bash
python3 -m hermes_feishu_card.cli setup \
  --hermes-dir ~/.hermes/hermes-agent \
  --config ~/.hermes/config.yaml \
  --yes
```

安装完成后检查 sidecar：

```bash
python3 -m hermes_feishu_card.cli status --config ~/.hermes/config.yaml
```

更完整的安装包、Release 下载、Docker 和 PEP 668/uv 说明见 [README-install.md](README-install.md) 与 [详细使用手册](docs/user-guide.md)。

## 最小配置

复制 `config.yaml.example` 到本地使用，不要提交真实凭据。

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

需要显示 Codex 订阅剩余额度时，把 `subscription_usage` 加入 `footer_fields`。插件仅在显式启用后，通过 Hermes 原生 `fetch_account_usage("openai-codex")` 查询；旧 Hermes、未登录或网络失败时静默隐藏，不影响卡片完成。

飞书凭据也可以放在配置同目录 `.env`：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_CONNECTION_MODE=websocket
FEISHU_HOME_CHANNEL=oc_xxx
```

多 bot、群聊绑定、`bindings.chats`、multi profile、profile-aware routing、footer 字段和 no-op client 说明见 [详细使用手册](docs/user-guide.md#配置)。

## Hermes 流式配置

确认 `streaming.enabled` 为 `true`，并让 Hermes 使用 edit transport。

确保 Hermes `config.yaml` 中启用流式编辑：

```yaml
streaming:
  enabled: true
  transport: edit
```

不要设置 `display.platforms.feishu.streaming: false`。也不要把 `display.show_reasoning` 当成本插件必需开关；它可能把 reasoning 追加到最终回复里，反而干扰卡片流式体验。插件会直接处理 Hermes 的 `thinking.delta` / `answer.delta`。

Hermes `v2026.4.23` 起的旧版和 Hermes 0.13.0+/0.14.0/0.15.x/0.17.x/0.18.x 均有兼容策略；`doctor` 会优先读取 `VERSION` 或 Git tag `v2026.4.23+`，也会在版本 metadata 不完整或不可解析时用 `gateway/run.py` anchors 兜底。升级 Hermes 或插件后建议重新执行 `setup` 或 `install --hermes-dir ... --yes`。

## Docker 容器内安装

已有 Hermes 容器优先使用：

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export HFC_VERSION=v4.0.10
bash install-docker.sh
```

默认路径：

| 变量 | 默认值 |
|---|---|
| `HERMES_DIR` | `/opt/hermes` |
| `HFC_CONFIG` | `/opt/data/config.yaml` |
| `HFC_ENV_FILE` | `/opt/data/.env` |
| `HFC_VERSION` | `latest` |

`docker-compose.example.yml` 是适配示例，不是官方镜像。V3.8.6 起，Docker/source-stripped Hermes 缺少 `VERSION` 和 `.git` 时也会用 Gateway anchors 兜底判断 `gateway_run_013_plus`。

## 常用命令

| 命令 | 说明 |
|---|---|
| `setup --hermes-dir ... --yes` | 一键配置、检测、安装 hook、启动 sidecar |
| `doctor --config ... --hermes-dir ... --explain` | 诊断 Hermes 版本、runtime import、hook strategy、anchors 和建议 |
| `install --hermes-dir ... --yes` | 安装插件到 Hermes runtime venv，并安装 hook |
| `repair --hermes-dir ... --yes` | 修复可验证的 hook manifest/backup 状态 |
| `setup --repair ... --yes` / `--no-repair` | 自动修复已知安全状态，或显式关闭自动修复 |
| `restore --hermes-dir ... --yes` | 恢复原始 Hermes 文件 |
| `start --config ...` / `status --config ...` / `stop --config ...` | sidecar 进程管理和 `/health` 检查；Linux/systemd 使用独立 user service |
| `smoke-feishu-card --profile-id ... --chat-id ...` | 真实飞书卡片 smoke test |
| `bots list|show|add|remove|test` | 多 bot 注册、查看和联调 |

高频流式调优通常不需要改。遇到 DeepSeek burst、token-by-token 或长上下文压力时再看：

| 变量 | 默认值 | 用途 |
|---|---:|---|
| `HERMES_FEISHU_CARD_DELTA_COALESCE_MS` | `250` | Gateway 内 delta 最大合并等待时间 |
| `HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS` | `600` | pending delta 达到字符数后立即 flush |
| `HERMES_FEISHU_CARD_DELTA_COALESCE_MAX_PENDING` | `128` | pending delta session 上限 |

## 最新版本

![飞书话题内卡片连续更新与思考工具 timeline 展示](docs/assets/feishu-topic-card-showcase-v389.png)
| 版本 | 重点 |
|---|---|
| [v4.0.10](docs/release-notes-v4.0.10.md) | 收紧 sidecar 事件传输边界：非回环监听必须显式授权并启用 HMAC-SHA256 防伪与防重放，本机回环安装保持兼容 |
| [v4.0.9](docs/release-notes-v4.0.9.md) | 修复 Issue #130：不再替换已连接 Lark WebSocket 的 live event handler，只在 WS 线程更新卡片回调，避免断连与 Gateway 重启循环 |
| [v4.0.8](docs/release-notes-v4.0.8.md) | 修复 Issue #127：cron 卡片接管正文后继续使用 Hermes 原生链路上传附件，不再只显示文件名 |
| [v4.0.7](docs/release-notes-v4.0.7.md) | Linux/systemd sidecar 使用独立可重启 user service，升级时优先选择 Hermes venv Python；合入 PR #124 修复自我改进通知误占下一轮卡片 |
| [v4.0.6](docs/release-notes-v4.0.6.md) | 修复 Hermes 0.18.x 完成 hook、队列完成 hook，以及无灰色原生输出且可正确收束的 background 通知卡片；新增显式且 fail-closed 的 Hermes 升级恢复 |
| [v4.0.5](docs/release-notes-v4.0.5.md) | 修复升级后 Gateway venv 仍加载旧插件的问题；安装器会比较 runtime 版本、自动同步并在安装后复核版本与路径 |
| [v4.0.4](docs/release-notes-v4.0.4.md) | 修复 Markdown `MEDIA:` 字面量、SDK 预绑定旧 callback 的交互转发，以及 Codex 只返回单个限额窗口时的错误 `5h` 标签 |
| [v4.0.3](docs/release-notes-v4.0.3.md) | 修复仅升级包并重启、但仍保留 V4.0.0 completion hook 时的媒体回答灰色正文重复；匹配正文只抑制一次，原生图片/文件继续发送 |
| [v4.0.2](docs/release-notes-v4.0.2.md) | 修复 manifest 与 backup 均可信时，合法旧 owned hook 仍被拒绝升级的问题；保留 v4.0.1 的媒体正文去重修复 |
| [v4.0.1](docs/release-notes-v4.0.1.md) | 修复 `MEDIA:` 图片/文件完成卡之后重复发送原生正文；原生通道仅投递媒体，卡片隐藏内部本地路径 |
| [v4.0.0](docs/release-notes-v4.0.0.md) | 运行态 Header 实时显示 Hermes 工具 preview，正文独立流式显示公开阶段输出；等待、失败、完成状态自然衔接并保持现有 Footer/引用边界 |
| [v3.10.0](docs/release-notes-v3.10.0.md) | 裸 `/resume` 使用原生会话下拉卡并沿用 Hermes 安全恢复路径；模型 footer 增加转义后的轻量语义色，不改变布局和字段顺序 |
| [v3.9.1](docs/release-notes-v3.9.1.md) | 可靠性热修：完成答案不截断、打断任务终态串行化、模型选择回调异步化，以及可验证的 marker-only 安装损坏恢复；普通流式卡 footer/layout 保持不变 |
| [v3.8.18](docs/release-notes-v3.8.18.md) | cron 卡片携带 `thread_id` 回到飞书话题原线程（PR #91，贡献者 @colinaaa） |
| [v3.8.17](docs/release-notes-v3.8.17.md) | cron `deliver=origin/all` 等路由意图会解析到飞书目标并发送卡片 |
| [v3.8.16](docs/release-notes-v3.8.16.md) | 话题群连续消息复用 `message_id` 时，第二条及后续消息会重新发送卡片 |
| [v3.8.15](docs/release-notes-v3.8.15.md) | 输入 `.docx/files` 上下文只做卡片附件摘要，不再放行重复原生最终 reply |
| [v3.8.14](docs/release-notes-v3.8.14.md) | WebSocket 长连接下 agent clarify/approval 按钮通过 `interaction.select` 原生 card action 闭环 |
| [v3.8.13](docs/release-notes-v3.8.13.md) | Hermes `v2026.7.7.2` / `0.18.2` 升级后可用 anchors 兜底并修复 stale install state |
| [v3.8.12](docs/release-notes-v3.8.12.md) | 修复带 `colors.csv` / `styles.csv` 等附件摘要的完成卡片仍重复发送原生 reply 的问题 |
| [v3.8.11](docs/release-notes-v3.8.11.md) | `/hfc status` 卡片接管后不再同时触发灰色 `Unknown command /hfc` 原生回复 |
| [v3.8.10](docs/release-notes-v3.8.10.md) | 群聊 `/hfc status` 自动提示 chat binding 与 slash command 边界；工具详情显示参数、耗时和失败原因 |
| [v3.8.9](docs/release-notes-v3.8.9.md) | 飞书/Lark 话题内卡片连续更新，`system.notice` 不再重复外溢 |
| [v3.8.8](docs/release-notes-v3.8.8.md) | Hermes 原生系统提示卡片化：Working、上下文压缩、skill loading、自我改进 review |
| [v3.8.7](docs/release-notes-v3.8.7.md) | 新版 Hermes 缺少 `message.started` 时也能从首个 delta/completed 事件创建卡片 |
| [v3.8.6](docs/release-notes-v3.8.6.md) | Docker/source-stripped Hermes 缺 `VERSION` 时用 Gateway anchors 兜底，兼容 Hermes v0.18.0 |
| [v3.8.5](docs/release-notes-v3.8.5.md) | `/new`、`/model` 等直通命令执行结果保持卡片反馈 |
完整版本历史见 [CHANGELOG.md](CHANGELOG.md)，更长的历史说明保留在 [详细使用手册](docs/user-guide.md#版本历史)。

## 架构简图

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

这是 sidecar-only 设计：Hermes hook 保持 fail-open，飞书发送、更新、状态机、重试、诊断都在 sidecar 中运行。历史 V2 实现归档在 `legacy/`，不是 active runtime。

## 文档入口

- 详细使用手册：[中文](docs/user-guide.md) / [English](docs/user-guide.en.md)
- 安装包说明：[README-install.md](README-install.md)
- 架构说明：[中文](docs/architecture.md) / [English](docs/architecture.en.md)
- 事件协议：[中文](docs/event-protocol.md) / [English](docs/event-protocol.en.md)
- 安装安全：[中文](docs/installer-safety.md) / [English](docs/installer-safety.en.md)
- 迁移说明：[中文](docs/migration.md) / [English](docs/migration.en.md)
- 端到端验证：[中文](docs/e2e-verification.md) / [English](docs/e2e-verification.en.md)
- 发布准备：[中文](docs/release-readiness.md) / [English](docs/release-readiness.en.md)
- 测试说明：[中文](docs/testing.md) / [English](docs/testing.en.md)
- 项目维护 Wiki：[docs/wiki](docs/wiki/README.md)

## 贡献者

- [gischuck](https://github.com/gischuck) - [PR #12](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/12) Accept-Encoding 修复
- [gischuck](https://github.com/gischuck) - [PR #76](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/76) 思考与工具 timeline 体验建议与实现探索
- [fengs2021](https://github.com/fengs2021) - [PR #17](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/17) 锁架构优化与更新间隔改进
- [colinaaa](https://github.com/colinaaa) - [PR #87](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/87) WebSocket `interaction.select` clarify/approval 卡片交互支持
- [colinaaa](https://github.com/colinaaa) - [PR #88](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/88) 话题群 `message_id` 复用下第二轮消息新卡片修复
- [colinaaa](https://github.com/colinaaa) - [PR #91](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/91) cron 结果回到飞书话题群原线程的 `thread_id` 路由修复
- [zayn-0101](https://github.com/zayn-0101) - [PR #77](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/77) cron `deliver=origin/all` 路由意图卡片投递修复
- [Zanetach](https://github.com/Zanetach) - [PR #84](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/84) / @Zanetach：卡片 progress-status 路由与 `.env` 白名单扩展的 profile 环境支持（V3.9.0）
- [colinaaa](https://github.com/colinaaa) - [PR #93](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/93) 打断任务后将旧卡片可靠收束为终态；[PR #97](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/97) 保留完整完成答案（V3.9.1）
- [charles5g](https://github.com/charles5g) - [PR #98](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/98) 模型选择回调异步化与原卡片状态更新（V3.9.1）
- [wjiemin49-ux](https://github.com/wjiemin49-ux) - [PR #52](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/52) loopback 健康检查代理问题的诊断与修复方向（V3.9.1 采用）
- [colinaaa](https://github.com/colinaaa) - [Issue #94](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/94) 裸 `/resume` 原生会话选择器的需求、交互流程与安全边界（V3.10.0）
- [charles5g](https://github.com/charles5g) / jackmim - [PR #98](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/98) 模型 footer 语义色创意；主线实现补充 HTML 转义并保持布局不变（V3.10.0）
- [tianqiii](https://github.com/tianqiii) - [Issue #107](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/107) Codex 订阅配额 footer 的需求、Hermes 原生接口方案与展示格式（V4.0.2）
- [sthnow](https://github.com/sthnow) - [Issue #110](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/110) Markdown 代码中的 `MEDIA:` 字面量误解析复现、根因与期望边界（V4.0.4）
- [zkyken](https://github.com/zkyken) - [Issue #112](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/112) lark SDK 预绑定 callback 下交互按钮失效的日志、根因线索与修复方向（V4.0.4）
- [ShakuOvO](https://github.com/ShakuOvO) / [blakejia](https://github.com/blakejia) - [Issue #106](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/106) 与 [#111](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/111) 图片回答灰色正文重复的报告、复测与截图（V4.0.1–V4.0.3）；另感谢 [blakejia](https://github.com/blakejia) 在 [#115](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/115) 提供 Gateway venv 旧版本证据、完整升级步骤与复测指标（V4.0.5）；感谢 [nasvip](https://github.com/nasvip) / [hzy](https://github.com/hzy) / [lRoccoon](https://github.com/lRoccoon) 贡献 V4.0.6 的 Hermes 升级恢复复现、background 通知卡片实现，以及 Hermes 0.18.x completion hook 生产诊断与修复；V4.0.7 继续感谢 [nasvip](https://github.com/nasvip) 的 [Issue #125](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/125) systemd/Python 环境完整证据，以及 [hzy](https://github.com/hzy) 的 [PR #124](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/124) 自我改进通知卡片实现与回归测试；V4.0.8 感谢 [zyq2552899783-lgtm](https://github.com/zyq2552899783-lgtm) 报告 [Issue #127](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/127) 的 cron 附件只显示文件名问题；V4.0.9 感谢 [Jasonsun77](https://github.com/Jasonsun77) 在 [Issue #130](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/130) 提供 Linux crash-loop A/B、完整时间线、SDK 版本与上游 reconnect 关联证据

## 安全说明
默认 `127.0.0.1` 采用本机进程互信；不要把 sidecar 未鉴权暴露到网络。非 loopback 只有显式设置 `server.allow_non_loopback: true` 才能启动，并强制使用私有 state directory 的 HMAC 事件鉴权；它不替代 TLS。不要把 App Secret、tenant token、真实 chat_id、未脱敏截图提交到仓库，生产凭据应保存在本机配置或环境变量中。

## License

MIT License，详见 [LICENSE](LICENSE)。
