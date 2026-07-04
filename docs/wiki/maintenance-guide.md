# 维护指南

## 改动前先判断范围

这个项目的风险主要来自三类边界：

- Hermes Gateway 内部变量和事件结构会变。
- Feishu/Lark 卡片 API 与 WebSocket 交互路径有多条 fallback。
- sidecar 状态是进程内内存，不能依赖持久化恢复。

小文档改动可以直接做；涉及 `hook_runtime.py`、`server.py`、`patcher.py`、安装器或 release 流程时，先读 `AGENTS.md` 的 hot files 和测试矩阵。

## Hot files

### `hermes_feishu_card/hook_runtime.py`

职责：

- 从 Hermes runtime `locals()` 里抽取事件信息。
- 向 sidecar 发送 `message.*`、`tool.updated`、`system.notice` 等事件。
- monkeypatch Feishu adapter 的 `send`、`edit_message`、slash confirm、model picker。
- 处理新版 Hermes 缺少 `message.started` 的首事件场景。

高风险点：

- 字段名必须贴合 Hermes 变量：`source`、`event`、`response`、`agent_result`、`event_message_id` 等。
- Feishu topic 场景必须保留 `source.message_id` 和 `reply_to_message_id`。
- 已识别 `system.notice` 不能在卡片投递超时后再次退回灰色原生文本。
- `/update` 不进入命令卡片，保持 Hermes 后台升级。

### `hermes_feishu_card/server.py`

职责：

- 管理 `CardSession`。
- 根据 `message_id`、`reply_to_message_id` 和 profile/bot 信息路由到卡片。
- 合并高频 delta，安排 Feishu PATCH。
- 处理 terminal drain、终态优先更新、metrics 和 `/health`。

高风险点：

- topic 后续事件使用不同内部 `message_id` 时，必须先查 reply anchor。
- terminal 事件前要 flush pending delta，避免尾部文本丢失。
- 卡片已完成时不能让 Hermes 原生 resend 泄漏成灰色消息。

### `hermes_feishu_card/install/patcher.py`

职责：

- 唯一允许修改 Hermes `gateway/run.py` 的代码。
- AST 定位 Gateway 函数并插入 marker-wrapped hook blocks。
- 创建 manifest、backup，支持 restore/uninstall/repair。

高风险点：

- patch 必须幂等、可移除、可检测 corrupt markers。
- Hermes source-stripped Docker 目录缺少 `VERSION` 时，只能在 gateway anchors 可验证时兜底。
- 新 hook block 必须有 patcher 单测和 remove/restore 覆盖。

## 常见改动对应测试

| 改动 | 先跑 | 发布前还要跑 |
|---|---|---|
| runtime event 抽取、topic、notice | `python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_server.py -q` | `python -m pytest -q` |
| patcher / install hook | `python -m pytest tests/unit/test_patcher.py tests/integration/test_cli_install.py -q` | `python -m pytest -q` |
| renderer / timeline / Markdown | `python -m pytest tests/unit/test_render.py tests/unit/test_session.py -q` | `python -m pytest -q` |
| CLI / doctor / install scripts | `python -m pytest tests/integration/test_cli.py tests/unit/test_install_scripts.py -q` | `python -m pytest -q` |
| README / release notes / TODO | `python -m pytest tests/unit/test_docs.py -q` | `git diff --check` |
| version bump | `python -m pytest tests/unit/test_package_metadata.py tests/unit/test_docs.py -q` | `python -m pytest -q` |

## 维护原则

- 先加失败测试，再修复复杂 bug。
- 不直接改 Hermes 本体；通过 patcher 和 install 命令验证。
- 保持 hook fail-open，但对已识别且已接管的 Feishu 卡片消息要抑制重复原生文本。
- 真实 Feishu 凭据、chat id、token 不进入仓库。
- 截图入库前脱敏；优先展示项目能力，不展示私人内容。

