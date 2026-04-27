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

官方 Hermes `v2026.4.23` Git tag 源码已用于人工安装/恢复 smoke；该上游标签没有顶层 `VERSION` 文件，因此安装器会在 `VERSION` 缺失时回退读取 Git tag。真实 Hermes Gateway 进程运行 smoke 仍需在有可用 Hermes 本机安装时执行。

## Hermes hook runtime tests

```bash
python3 -m pytest tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py -q
```

这些测试会验证安装后的 Hermes hook 能把 `SidecarEvent` 发送到 mock sidecar，并在发送失败时保持 fail-open。它们只使用 fixture 和 mock sidecar，不访问真实飞书。

## Sidecar process tests

```bash
python3 -m pytest tests/integration/test_cli_process.py -q
```

该测试会启动真实本机 sidecar 进程，检查 `/health`、`status`、事件接收和 `stop` 清理。测试使用临时 pidfile 目录和 no-op Feishu client，不访问真实飞书。

## Feishu HTTP client tests

```bash
python3 -m pytest tests/unit/test_feishu_client.py tests/integration/test_feishu_client_http.py -q
```

这些测试使用 mock Feishu server 验证 tenant token、发送 interactive card、更新卡片消息和错误处理，不访问真实飞书，也不需要真实 App Secret。

手动真实飞书 smoke：

```bash
FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx \
python3 -m hermes_feishu_card.cli smoke-feishu-card --config config.yaml.example --chat-id oc_xxx
```

该命令会真实发送并更新一张测试卡片。只有在本机环境提供凭据和目标 `chat_id` 时才运行；不要把 App Secret、tenant token 或真实 chat_id 写入仓库。

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
