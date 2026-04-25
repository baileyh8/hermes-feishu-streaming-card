# Hermes 飞书流式卡片 sidecar-only 插件

本项目为 Hermes Agent 提供飞书/Lark 流式卡片能力。当前主线是 **sidecar-only**：Hermes 只安装一个最小事件转发 hook，流式卡片渲染、会话状态和飞书 CardKit 调用都运行在独立的 `hermes_feishu_card/` sidecar 中。

旧目录和脚本仍保留用于追溯历史实现，但它们不是 active runtime。`adapter/`、`sidecar/`、`patch/`、`installer.py`、`installer_sidecar.py`、`installer_v2.py`、`gateway_run_patch.py`、`patch_feishu.py` 等 legacy/dual/patch 代码不属于新主线；新开发、测试和安装入口以 `hermes_feishu_card/` 为准。

## 支持范围

- 默认支持 Hermes Agent `v2026.4.23` / `v0.11.0` 及以上。
- 安装器会在写入前检查 Hermes 版本、`gateway/run.py` 结构和可插入位置。
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
