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
- 运行时包装 bare `/resume` 并安装 native resume picker；选择结果复用 original Hermes resume handler。
- 处理新版 Hermes 缺少 `message.started` 的首事件场景。

高风险点：

- 字段名必须贴合 Hermes 变量：`source`、`event`、`response`、`agent_result`、`event_message_id` 等。
- Feishu topic 场景必须保留 `source.message_id` 和 `reply_to_message_id`。
- 已识别 `system.notice` 不能在卡片投递超时后再次退回灰色原生文本。
- cron completion hook 必须位于 `extract_media` / `media_files` 过滤之后：`native_delivery=required` 时清空原生正文但继续文件上传，不能在媒体提取前提前返回。
- `/update` 不进入命令卡片，保持 Hermes 后台升级。
- 已连接 Lark WebSocket 的 live `EventDispatcherHandler` identity 不得被重建或替换；只可通过 `_ws_thread_loop.call_soon_threadsafe(...)` 更新现有 `p2.card.action.trigger` processor callback，不兼容内部结构必须 fail-open。
- `_hfc_original_handle_resume_command` 必须保留为唯一恢复执行路径；不要在 HFC 重写 session ownership、continuation 或 `switch_session` 规则。
- 群聊/topic picker 只有在发起者 `open_id` 可验证时才显示；不可验证时 fail-open。私聊不额外比较操作者。

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
- 群聊 `/hfc status` 只做路由诊断和 binding 提示；@机器人触发、白名单和群消息准入属于 Hermes Gateway。

### `hermes_feishu_card/install/patcher.py`

职责：

- 唯一允许修改 Hermes `gateway/run.py` 的代码。
- AST 定位 Gateway 函数并插入 marker-wrapped hook blocks。
- 创建 manifest、backup，支持 restore/uninstall/repair。

高风险点：

- patch 必须幂等、可移除、可检测 corrupt markers。
- Hermes source-stripped Docker 目录缺少 `VERSION`，或版本 metadata 可读但格式不可解析时，只能在 gateway anchors 可验证时兜底。
- 新 hook block 必须有 patcher 单测和 remove/restore 覆盖。

### `hermes_feishu_card/install/recovery.py` and operations execution

职责：

- `plan_recovery(...)` 只根据当前 Hermes detection、manifest、backup 和 marker 证据生成可脱敏展示的 recovery plan。
- `execute_recovery(...)` 在 mutation 前重新规划并比较 fingerprint，只执行可验证的修复；证据变化、用户编辑或无法确认的状态必须拒绝。
- `server.py` 的 operations-card executor 只消费带确认的 plan，保留私聊/群聊 ownership 边界和 CLI fallback。

高风险点：

- 不把 recovery plan、state-dir transport secret、真实 chat id 或安装路径未经脱敏地放进 card、`/health` 或日志。
- 自动 repair 只适用于 known-safe state；`--no-repair` 必须保持有效，用户编辑不能被覆盖。
- 调整 planner/executor 时运行 `tests/unit/test_recovery.py`、`tests/unit/test_operations.py`、`tests/integration/test_server.py`；涉及安装器时再加 `tests/integration/test_cli_install.py`。

### `hermes_feishu_card/process.py` and sidecar lifecycle

职责：

- 在 Linux/systemd user manager 可用时，把 sidecar 放入独立 transient user service，避免与 `hermes-gateway` 共用 cgroup。
- 在其他平台保留 detached-process fallback。
- 用 PID、process token 和 manager/unit identity 管理 status、migration 与 stop。

高风险点：

- `start_new_session=True` 不能脱离 systemd cgroup，不能作为 Linux Gateway 重启隔离方案。
- systemd 可重启 sidecar 并改变 PID；status/stop 必须以 token 和记录的 unit 为稳定身份，不能只比较旧 PID。
- 升级迁移只能停止 PID/token/health 三者一致的旧进程，未知进程保持 fail-closed。
- 调整 lifecycle 时运行 `tests/unit/test_process.py`、`tests/integration/test_cli_process.py` 和 `tests/unit/test_install_scripts.py`。

## 常见改动对应测试

| 改动 | 先跑 | 发布前还要跑 |
|---|---|---|
| runtime event 抽取、topic、notice | `python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_server.py -q` | `python -m pytest -q` |
| Lark WebSocket handler / command-card callback | `python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_feishu_sdk_compat.py -q` | Python 3.11 + `lark-oapi==1.6.8` + `websockets==15.0.1` CI、真实 Feishu 稳定性 smoke、`python -m pytest -q` |
| `/resume` / `/model` 原生 picker | `python -m pytest tests/unit/test_hook_runtime.py tests/unit/test_patcher.py tests/integration/test_cli_install.py -q` | 真实 Feishu 私聊、群聊、topic smoke + `python -m pytest -q` |
| 群聊路由诊断 / 工具详情 | `python -m pytest tests/unit/test_bots.py tests/unit/test_session.py tests/unit/test_render.py tests/integration/test_server.py -q` | `python -m pytest -q` |
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
