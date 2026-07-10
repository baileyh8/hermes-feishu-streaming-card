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

Hermes 飞书流式卡片插件把 Hermes Agent Gateway 的飞书/Lark 回复变成一张持续更新的交互式卡片。思考过程、工具调用、最终答案、授权确认、选项选择、系统提示和运行统计会收束在卡片内，而不是散落成多条灰色原生消息。

它面向真实飞书使用场景：流式内容漏字/乱序、长表格和代码块变成 raw markdown、工具过程不可见、approval/clarify 需要手工回复、话题里卡片不更新、多 bot / 多 profile 难排查，以及 Hermes 升级后 hook 兼容不确定。

![Hermes 飞书卡片命令交互、结果反馈与工具 timeline 展示](docs/assets/feishu-card-showcase-v385.png)

## 你能看到什么

- **一张持续更新的飞书卡片**：`thinking.delta`、`answer.delta`、`tool.updated`、`message.completed` 会合并到同一张卡片。
- **主答案和过程分区**：最终答案留在正文区，pre-tool answer、工具调用、系统 notice 进入“思考与工具” timeline。
- **卡片内交互**：approval / clarify choices 渲染为按钮；`/new`、`/reset`、`/undo`、`/model` 等独立命令使用原生 interactive card。
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
export HFC_VERSION=v3.9.0
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
| `start --config ...` / `status --config ...` / `stop --config ...` | sidecar 进程管理和 `/health` 检查 |
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
| [v3.9.0](docs/release-notes-v3.9.0.md) | 运维与可靠性基础：profile env/status 路由（PR #84，贡献者 @Zanetach）、安全修复/重启运维卡与 CLI fallback；普通 footer/layout 不变 |
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
- [Zanetach](https://github.com/Zanetach) - [PR #84](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/84) profile 环境变量与 `status` 路由链路能力（V3.9.0）

## 安全说明

不要把 App Secret、tenant token、真实 chat_id、未脱敏截图提交到仓库。效果图仅用于展示卡片体验，生产凭据应保存在本机配置或环境变量中。

## License

MIT License，详见 [LICENSE](LICENSE)。
