# Hermes 飞书流式卡片 sidecar-only 插件

本项目目标是为 Hermes Agent 提供飞书/Lark 流式卡片能力。当前主线是 **sidecar-only**：Hermes 侧只安装最小 hook，流式卡片渲染、会话状态和飞书 CardKit 边界都放在独立的 `hermes_feishu_card/` sidecar 中。

当前已完成第二阶段最小事件转发：安装后的 Hermes hook 会调用 `hermes_feishu_card.hook_runtime`，把可识别的 Hermes 消息上下文以 `SidecarEvent` JSON 发送到本机 sidecar `/events`。该链路 fail-open，sidecar 不可用时 Hermes 原生文本回复继续运行。

真实 Feishu CardKit 创建/更新仍未完成，当前卡片侧联调使用 fake client 或 mock server。

旧目录和脚本仍保留用于追溯历史实现，但它们不是 active runtime。`adapter/`、`sidecar/`、`patch/`、`installer.py`、`installer_sidecar.py`、`installer_v2.py`、`gateway_run_patch.py`、`patch_feishu.py` 等 legacy/dual/patch 代码不属于新主线；新开发、测试和安装入口以 `hermes_feishu_card/` 为准。

## 支持范围

- 默认支持 Hermes Agent `v2026.4.23` 及以上。
- 安装器实际以 Hermes 目录中的 `VERSION=v2026.4.23+` 和 `gateway/run.py` 代码结构检测为准。
- `v0.11.0` 是项目规划中对应的 Hermes 名称；当前检测实现不按 `v0.11.0` 字符串判断支持范围。
- 检查失败时安装器 fail-closed，不写入 Hermes 文件，不留下半安装状态。
- sidecar 不可用时，Hermes 应继续走原生文本回复降级路径，避免影响 Agent 主流程。

## 快速开始

```bash
python3 -m pip install -e ".[test]"
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --skip-hermes
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
```

当前 CLI 的 `doctor` 命令必须传入 `--config`。本仓库的 `config.yaml.example` 可用于本地 dry-run；正式使用时建议复制到本机 Hermes 配置目录并填写本机配置。

恢复或移除安装：

```bash
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli uninstall --hermes-dir ~/.hermes/hermes-agent --yes
```

`restore` 和 `uninstall` 都会优先使用安装时的备份与 manifest 校验；检测到 Hermes 文件或备份被用户改动时会拒绝覆盖。

## 飞书凭据

飞书/Lark App ID 和 App Secret 只能通过本机配置或环境变量提供，不要写入仓库、README、测试 fixture 或提交历史。

支持的环境变量：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `HERMES_FEISHU_CARD_HOST`
- `HERMES_FEISHU_CARD_PORT`

## 文档

- [架构说明](docs/architecture.md)
- [事件协议](docs/event-protocol.md)
- [安装安全](docs/installer-safety.md)
- [测试说明](docs/testing.md)
