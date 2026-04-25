# 测试说明

## 单元测试

```bash
python3 -m pytest tests/unit -q
```

单元测试覆盖配置加载、事件模型、文本清理、卡片渲染、会话状态、安装器检测、manifest 和 patcher 行为。

## 集成测试

```bash
python3 -m pytest tests/integration -q
```

集成测试覆盖 CLI、doctor、sidecar server，以及基于 fixture Hermes 目录的安装、恢复和卸载流程。

## 文档测试

```bash
python3 -m pytest tests/unit/test_docs.py -q
```

文档测试只做低脆弱度守卫：确认 README 保留 sidecar-only 和 `v2026.4.23` 支持范围说明，确认主线文档仍明确 legacy/dual 代码不是 active runtime，并确保事件协议持续声明卡片状态。它不替代人工文档 review。

## Fixture 安装恢复测试

`tests/fixtures/hermes_v2026_4_23/` 是安装器安全测试使用的 Hermes fixture。相关测试会复制 fixture 到临时目录，验证：

- `install` 写入 hook、备份和 manifest。
- `restore` 能恢复原始 `run.py`。
- `uninstall` 能移除本插件拥有的安装状态。
- 用户改动过 `run.py`、备份或 manifest 时拒绝覆盖。

## Doctor

本地检查命令：

```bash
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --skip-hermes
```

当前 CLI 的 `doctor` 需要显式传入 `--config`。`--skip-hermes` 适合仓库内 dry-run；真实安装前应去掉该模式或补充 Hermes 目录检查能力。

## 真实飞书联调

真实飞书/Lark 联调只能通过环境变量或本机配置提供凭据，例如 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`。不要把 App Secret 写入仓库、测试 fixture、日志样例或文档。

联调完成后建议轮换测试应用凭据，并检查本地日志中没有持久化 secret。
