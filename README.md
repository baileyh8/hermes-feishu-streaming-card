# Hermes 飞书流式卡片 sidecar-only 插件

本项目目标是为 Hermes Agent 提供飞书/Lark 流式卡片能力。当前主线是 **sidecar-only**：Hermes 侧只安装最小 hook，流式卡片渲染、会话状态和飞书 CardKit 边界都放在独立的 `hermes_feishu_card/` sidecar 中。

当前已完成真实 Feishu E2E 主链路：安装后的 Hermes hook 会调用 `hermes_feishu_card.hook_runtime`，把可识别的 Hermes 消息上下文以 `SidecarEvent` JSON 发送到本机 sidecar `/events`。sidecar 负责创建/更新飞书卡片、节流、重试、状态聚合和 footer 渲染；该链路 fail-open，sidecar 不可用时 Hermes 原生文本回复继续运行。

Feishu CardKit HTTP client 已实现并通过 mock server、真实飞书 smoke、真实 Hermes Gateway E2E 和长卡片压力测试验证。飞书凭据只允许通过本机配置或环境变量提供，不应写入仓库。

旧目录和脚本仍保留用于追溯历史实现，但它们不是 active runtime。`adapter/`、`sidecar/`、`patch/`、`installer.py`、`installer_sidecar.py`、`installer_v2.py`、`gateway_run_patch.py`、`patch_feishu.py` 等 legacy/dual/patch 代码不属于新主线；新开发、测试和安装入口以 `hermes_feishu_card/` 为准。

## 支持范围

- 默认支持 Hermes Agent `v2026.4.23` 及以上。
- 安装器实际以 Hermes 目录中的 `VERSION=v2026.4.23+` 或 Git tag `v2026.4.23+`，以及 `gateway/run.py` 代码结构检测为准。
- `v0.11.0` 是项目规划中对应的 Hermes 名称；当前检测实现不按 `v0.11.0` 字符串判断支持范围。
- 检查失败时安装器 fail-closed，不写入 Hermes 文件，不留下半安装状态。
- sidecar 不可用时，Hermes 应继续走原生文本回复降级路径，避免影响 Agent 主流程。

## 快速开始

```bash
python3 -m pip install -e ".[test]"
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --skip-hermes
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent
python3 -m hermes_feishu_card.cli start --config config.yaml.example
python3 -m hermes_feishu_card.cli status --config config.yaml.example
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
```

当前 CLI 的 `doctor` 命令必须传入 `--config`。本仓库的 `config.yaml.example` 可用于本地 dry-run；正式使用时建议复制到本机 Hermes 配置目录并填写本机配置。真实安装前建议运行 `doctor --hermes-dir`，输出会展示 `version_source`、`version`、`minimum_supported_version`、`run_py_exists` 和拒绝原因；`--skip-hermes` 仅适合不检查 Hermes 目录的本地配置 dry-run。

恢复或移除安装：

```bash
python3 -m hermes_feishu_card.cli stop --config config.yaml.example
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli uninstall --hermes-dir ~/.hermes/hermes-agent --yes
```

`restore` 和 `uninstall` 都会优先使用安装时的备份与 manifest 校验；检测到 Hermes 文件或备份被用户改动时会拒绝覆盖。

`start` 会启动本机 sidecar HTTP 进程并写入用户态 pidfile；`status` 通过 `/health` 探活；`stop` 会校验 pidfile 中的 PID/token 与 `/health` 返回的 process_pid/process_token 匹配后才停止本插件管理的 sidecar，避免误杀无关进程。当前进程管理面向 macOS/Linux 这类 POSIX 环境。未配置飞书凭据时，进程内使用 no-op client 接收事件，不会发送真实飞书卡片；配置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 后，runner 会使用真实 Feishu HTTP client。

`/health` 和 `status` 会展示当前 sidecar 进程生命周期内的内存指标，包括事件接收/应用/忽略/拒绝次数、飞书发送/更新成功失败次数，以及飞书卡片更新重试次数。初始创建卡片不自动重试，避免响应丢失时重复发卡；已存在 message_id 的卡片更新会有限重试一次。

卡片 footer 默认显示：

```text
耗时 · 当前模型 · ↑输入 token · ↓输出 token · ctx 当前上下文/最大上下文 百分比
```

示例：

```text
1m32s · MiniMax M2.7 · ↑1.1m · ↓2.2k · ctx 182k/204k 89%
```

可通过配置选择显示字段：

```yaml
card:
  title: Hermes Agent
  footer_fields:
    - duration
    - model
    - input_tokens
    - output_tokens
    - context
```

`title` 控制飞书卡片 header 主标题，默认是 `Hermes Agent`。footer 可用字段为 `duration`、`model`、`input_tokens`、`output_tokens`、`context`。

真实飞书卡片 smoke：

```bash
FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx \
python3 -m hermes_feishu_card.cli smoke-feishu-card --config config.yaml.example --chat-id oc_xxx
```

该命令会向指定会话发送一张测试卡片并更新一次；只在本机环境读取凭据，不会输出 App Secret 或 tenant token。

## 飞书凭据

飞书/Lark App ID 和 App Secret 只能通过本机配置或环境变量提供，不要写入仓库、README、测试 fixture 或提交历史。

支持的环境变量：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `HERMES_FEISHU_CARD_HOST`
- `HERMES_FEISHU_CARD_PORT`
- `HERMES_FEISHU_CARD_ENABLED`
- `HERMES_FEISHU_CARD_EVENT_URL`
- `HERMES_FEISHU_CARD_TIMEOUT_MS`

## 文档

- [架构说明](docs/architecture.md)
- [事件协议](docs/event-protocol.md)
- [安装安全](docs/installer-safety.md)
- [迁移说明](docs/migration.md)
- [端到端验证材料](docs/e2e-verification.md)
- [发布准备说明](docs/release-readiness.md)
- [测试说明](docs/testing.md)

## 当前验证状态

- 自动化全量测试：`348 passed`
- 安装/恢复专项测试：覆盖备份、manifest、重复安装、用户改动拒绝恢复、卸载和恢复幂等
- 真实 Feishu E2E：已验证新卡片创建、流式更新、工具调用计数、完成状态、footer 元数据、无重复灰色原生消息
- 长卡片压力测试：同一张真实飞书卡片更新到 16k 中文字符成功，渲染分段稳定
