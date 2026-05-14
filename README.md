# Hermes 飞书流式卡片插件 V3.4.1

[中文](README.md) | [English](README.en.md)

![Hermes Feishu Streaming Card 封面](docs/assets/readme-cover.png)

为 Hermes Agent Gateway 的飞书/Lark 平台提供流式卡片消息。V3.4.1 **sidecar-only** 架构，支持 Hermes 0.13.0+ 兼容、多 Profile 进程内隔离、多 Bot 路由、DeepSeek 思维链兼容和表格超限保护，并修复 Hermes v2026.5.7 fallback message id 生命周期一致性。向后兼容 V3.2 及更早配置。

![飞书流式卡片真实效果截图](docs/assets/feishu-weather-card.png)

## V3.4.1 更新重点

- **修复 issue #25**：Hermes v2026.5.7 started hook 的 `event_message_id` 会作为显式 `message_id` 使用，避免 `message.started` 和 `message.completed` fallback 卡片 ID 不一致。
- **适配 Hermes 0.13.0+ 和后续新版本**：安装器会根据 Hermes 版本和代码 anchor 选择 `gateway_run_013_plus` hook strategy。
- **旧版本 Hermes 继续兼容**：`v2026.4.23` 到 `0.12.x` 使用 `legacy_gateway_run` strategy，不需要用户降级插件。
- **doctor 可见兼容状态**：`doctor` 输出 `hook_strategy`、`compatibility` 和 anchor 检测结果，便于确认当前 Hermes 目录命中了哪个兼容分支。
- **升级后必须重新安装 hook**：升级插件后运行 `install --hermes-dir ... --yes`，让 Hermes 使用对应版本的 hook。
- **修复 issue #23**：多 Hermes profile + 多 Feishu bot 场景下，profile id 会被显式识别并路由到对应 bot。
- **多 profile / multi bot 体验补齐**：支持 per-bot/profile title、cron final cards、attachment summaries + native media delivery，以及 reply card context。

## 快速安装

```bash
git clone https://github.com/baileyh8/hermes-feishu-streaming-card.git
cd hermes-feishu-streaming-card && pip install -e ".[test]"
export FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx
python3 -m hermes_feishu_card.cli setup --hermes-dir ~/.hermes/hermes-agent --yes
```

`setup` 是整合安装器，自动生成配置、检查 Hermes 版本（支持 `v2026.4.23` 到 `0.12.x` 旧版本，以及 Hermes `0.13.0+` 新版 anchor）、安装 hook、启动 sidecar 并做健康检查。分步命令见下方 CLI 命令表。

## 核心功能

- **多 Profile 进程内支持**（V3.3.0 新增）：一个 sidecar 服务多个 Hermes profile，`profile_id:message_id` 复合键隔离 session，每个 profile 独立凭据和 bot 路由
- **多 bot 路由与群聊绑定**：`bots.items` 注册多个飞书机器人，`bindings.chats` 按 `chat_id` 路由到指定 bot，支持 fallback/default bot
- **Profile / Bot 卡片标题**：全局、profile 和 bot 均可设置卡片标题，bot 级标题优先
- **流式思考展示**：`thinking.delta` 累积渲染，自动过滤 `<think>`/`</think>` 及 DeepSeek `<thinking>`/`</thinking>` 标签
- **渐进式答案更新**：`answer.delta` 分段进入同一张卡片，完成后覆盖思考内容
- **Cron 最终卡片与回复上下文**：cron 任务可发送最终卡片，回复卡片保留必要上下文
- **附件摘要与原生媒体投递**：附件摘要进入卡片，媒体内容按飞书原生消息能力投递
- **工具调用跟踪**：`tool.updated` 显示累计调用次数和状态
- **运行统计 footer**：显示耗时、模型、token、上下文占比，非终态卡片 footer 旋转 braille 动画
- **表格超限保护**（V3.3.0 新增）：超过飞书 5 个表格限制自动截断并提示，避免 11310 错误
- **平台判断修复**（V3.3.0 新增）：非飞书平台不再被 complete hook 吞掉响应
- **故障隔离**：sidecar 不可用时 Hermes hook fail-open，原生文本继续运行
- **安全安装**：安装器 fail-closed，检查版本和代码结构后写入，`restore`/`uninstall` 检测改动拒绝覆盖

## 升级

从 V3.2.x/V3.3.0/V3.4.0 升级到 V3.4.1 向后兼容，**单 Profile 配置无需任何修改**。如果升级的是 Hermes 0.13.0+，必须重新执行 `install --hermes-dir ... --yes`，让安装器写入 `gateway_run_013_plus` hook strategy；旧版本 Hermes 会继续使用 `legacy_gateway_run`。

```bash
# 1. 停止 sidecar
python3 -m hermes_feishu_card.cli stop --config ~/.hermes_feishu_card/config.yaml

# 2. 更新代码
cd /path/to/hermes-feishu-streaming-card
git checkout v3.4.1 && pip install -e ".[test]" --upgrade

# 3. 诊断 Hermes hook strategy 与 anchors
python3 -m hermes_feishu_card.cli doctor --config ~/.hermes_feishu_card/config.yaml --hermes-dir ~/.hermes/hermes-agent

# 4. 重新安装 hook（V3.4.1 会按 Hermes 版本选择 hook_strategy）
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes

# 5. 启动 sidecar
python3 -m hermes_feishu_card.cli start --config ~/.hermes_feishu_card/config.yaml
```

**使用多 Profile**：在 `config.yaml` 中新增 `profiles` 字段（参考下方多 Profile 配置示例），每个 profile 独立填写 `feishu.app_id`/`app_secret`。多 Profile 模式下环境变量 `FEISHU_APP_ID`/`FEISHU_APP_SECRET` 不再生效。

**回退 V3.2**：停止 sidecar，`git checkout v3.2.1`，重新 `pip install`，恢复备份的 `config.yaml`，重新 `install` + `start`。

## 配置

复制 `config.yaml.example` 到本地，不要提交真实凭据。三种典型配置：

**单 Profile 最小配置** — 最简单的起步方式：

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

**单 Profile + 多 Bot** — 在 `bots.items` 注册多个 bot，`bindings.chats` 按 chat_id 路由：

```yaml
server:
  host: 127.0.0.1
  port: 8765
feishu:
  app_id: ""
  app_secret: ""          # 仅作 fallback
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

**多 Profile**（V3.3.0 新增）— 一个 sidecar 服务多个 Hermes 实例，按 profile 隔离凭据和 bot：

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

多 Profile 模式下环境变量 `FEISHU_APP_ID`/`FEISHU_APP_SECRET` 不生效。`footer_fields` 支持 `duration`、`model`、`input_tokens`、`output_tokens`、`context`。

## 飞书应用配置

```bash
export FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx
# 真实飞书 smoke 测试：
python3 -m hermes_feishu_card.cli smoke-feishu-card --config config.yaml.example --chat-id oc_xxx
```

## Hermes Gateway 流式配置

确保 Hermes `config.yaml` 中 `streaming.enabled: true` 且 `streaming.transport: edit`。不要设置 `display.platforms.feishu.streaming: false`。不要把 `display.show_reasoning` 当成本插件的必需开关——它可能在最终回复中追加 reasoning 代码块，反而干扰卡片流式体验。若模型只返回最终答案（无 thinking 增量），卡片直接显示最终答案。

## CLI 命令

| 命令 | 说明 |
|------|------|
| `setup --hermes-dir ... --yes` | 一键安装（配置、检查、hook、sidecar、健康检查） |
| `doctor --config ... --hermes-dir ...` | 诊断 Hermes 兼容性，输出 `version_source`、`version`、`minimum_supported_version`、`run_py_exists`、`hook_strategy`、`compatibility`、anchors 和 `reason` |
| `install --hermes-dir ... --yes` | 安装 hook 到 Hermes |
| `restore --hermes-dir ... --yes` | 恢复原始 Hermes 文件 |
| `uninstall --hermes-dir ... --yes` | 卸载并恢复 |
| `start --config ...` | 启动 sidecar |
| `stop --config ...` | 停止 sidecar（校验 PID/token，匹配 `/health` 的 `process_pid/process_token` 后才停止） |
| `status --config ...` | 查看 sidecar 状态和 metrics |
| `bots list|show|add|remove --config ...` | 管理飞书 Bot 注册 |
| `bots bind-chat|unbind-chat --config ...` | 管理群聊绑定 |

## 架构

```text
Hermes Gateway
  └─ minimal hook in gateway/run.py
       └─ hermes_feishu_card.hook_runtime
            └─ HTTP POST /events ——→  sidecar server
                                      ├─ CardSession 状态机
                                      ├─ render_card() 卡片渲染
                                      ├─ FeishuClient tenant token / send / update
                                      ├─ 节流、重试、锁、诊断
                                      └─ /health 指标
```

Hermes hook 将 `message.started` / `thinking.delta` / `answer.delta` / `tool.updated` / `message.completed` / `message.failed` 转为 `SidecarEvent` 发往 sidecar。sidecar 持有完整会话状态和飞书 CardKit 边界，可独立测试、重启、诊断。历史实现集中归档在 `legacy/`（`installer_v2.py`、`gateway_run_patch.py`、`patch_feishu.py` 等），不是 active runtime，当前主线和安装入口以 `hermes_feishu_card/` 为准。迁移说明见 [docs/migration.md](docs/migration.md)。

## 常见问题

- **卡片没有思考/不流式**：检查 Hermes 的 `streaming.enabled: true` 和 `streaming.transport: edit`，确认模型支持 reasoning 增量。不要盲目开启 `show_reasoning`。
- **真实飞书没有卡片**：凭据未配时 sidecar 使用 no-op client，不发送真实卡片。多 Profile 模式检查各 profile 的 `feishu` 配置。
- **重复卡片**：检查 `/health` metrics（`events_received`、`feishu_send_successes`），V3.3.0 per-message lock 和 `profile_id:message_id` 复合键保证一消息一卡片。
- **灰色原生文本**：sidecar 成功接收 `message.completed` 后 Hermes hook 抑制原生文本；不可用时 fail-open 降级。V3.3.0 修复了非飞书平台被吞掉的问题。
- **doctor 不支持**：确认 Hermes ≥ `v2026.4.23`（读取 `VERSION` 或 Git tag `v2026.4.23+`），且目录中存在 `gateway/run.py`。
- **Hermes 0.13.0+ 升级后仍无卡片**：先运行 `doctor --config ... --hermes-dir ...` 查看 `hook_strategy`、`compatibility` 和 anchors，再重新 `install --hermes-dir ... --yes`。
- **恢复失败**：`restore`/`uninstall` 检测到文件改动拒绝覆盖，先备份再人工确认差异。
- **footer token 异常**：过滤明显异常值，若仍异常检查 Hermes 传入的 `tokens`/`context` 元数据。
- **表格超限**：V3.3.0 自动截断超 5 个表格并附加提示，减少 Markdown 表格数量即可。

## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| [v3.4.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.4.1) | 2026-05 | 修复 issue #25，Hermes v2026.5.7 fallback message id 生命周期一致 |
| [v3.4.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.4.0) | 2026-05 | Hermes 0.13.0+ 兼容、旧版本 strategy 保留、issue #23、多 profile/multi bot、附件与回复上下文 |
| [v3.3.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.3.0) | 2026-05 | 多 Profile、DeepSeek 兼容、表格保护、Footer 动画、平台判断修复 |
| [v3.2.1](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.2.1) | 2026-04 | Accept-Encoding 修复 |
| [v3.2.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.2.0) | 2026-04 | 多 Bot 路由、群聊绑定、Bot CLI、路由诊断 |
| [v3.1.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.1.0) | 2026-04 | Sidecar 架构、流式卡片、健康端点、安装向导 |
| [v3.0.0](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v3.0.0) | 2026-04 | Sidecar-only 初始发布（从 V2.x 单体 hook 迁移） |

完整更新日志：[CHANGELOG.md](CHANGELOG.md)。

## 测试

```bash
python3 -m pytest -q    # 运行全量自动化回归
```

验收覆盖：真实 Feishu E2E 主链路验证通过，真实 Hermes Gateway E2E、真实飞书应用卡片验证、16k 长卡压力测试、`doctor → install → restore` 闭环、多 Profile 路由、DeepSeek 标签过滤。Feishu CardKit HTTP client 已实现，并通过 mock Feishu server 和真实飞书 smoke 验证。

## 文档

- 架构说明：[中文](docs/architecture.md) / [English](docs/architecture.en.md)
- 事件协议：[中文](docs/event-protocol.md) / [English](docs/event-protocol.en.md)
- 安装安全：[中文](docs/installer-safety.md) / [English](docs/installer-safety.en.md)
- 迁移说明：[中文](docs/migration.md) / [English](docs/migration.en.md)
- 端到端验证：[中文](docs/e2e-verification.md) / [English](docs/e2e-verification.en.md)
- 发布准备：[中文](docs/release-readiness.md) / [English](docs/release-readiness.en.md)
- 测试说明：[中文](docs/testing.md) / [English](docs/testing.en.md)

## License

MIT License，详见 [LICENSE](LICENSE)。

## 贡献者

感谢以下贡献者对项目的改进：

- [gischuck](https://github.com/gischuck) — [PR #12](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/12) Accept-Encoding 修复（V3.2.1 brotli 兼容）
- [fengs2021](https://github.com/fengs2021) — [PR #17](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/17) 锁架构优化与更新间隔改进（V3.3.0）

## 安全说明

不要把 App Secret、tenant token、真实 chat_id 提交到仓库。效果图仅用于展示卡片效果，生产凭据保存在本机配置或环境变量中。
