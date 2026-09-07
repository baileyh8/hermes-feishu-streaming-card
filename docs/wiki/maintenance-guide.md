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
- 运行时包装手动 `/compress`，先创建运行卡，再以 original handler 返回值更新同一卡。
- 用 task-local command context 承载 all slash command feedback；首次 create、后续 PATCH，失败逐条回原生文本。
- 处理新版 Hermes 缺少 `message.started` 的首事件场景。
- 在任何 event/sequence/session 或原生抑制发生前查询 per-chat delivery policy，并对本轮固定决策。
- 通过既有 startup adapter 和 legacy first-hook fallback 启动认证 runtime hello/heartbeat；不得增加 import hook。

高风险点：

- 字段名必须贴合 Hermes 变量：`source`、`event`、`response`、`agent_result`、`event_message_id` 等。
- Feishu topic 场景必须保留 `source.message_id` 和 `reply_to_message_id`；首回复建 thread 的 `reply_in_thread` 意图还必须进入 `CardSession`，并通过普通交互与 `RuntimeInteractionDeliveryReservation` 的所有新卡发送路径继续传递。create API 不接受 `receive_id_type=thread_id`：无 reply anchor 时实际发送必须回落父 `chat_id`，但 native-handoff 的 logical topic route/UUID identity 仍保持稳定。
- `message.started` 必须从真实入站 `event.message_id` 绑定 `turn_id`；同一 `source` 的 stream/tool/terminal 回调复用这个 immutable identity。显式 `turn_id` 是 canonical turn hard fence，不能被 `message_id` 或 `reply_to_message_id` alias 覆盖；无法在 `source` 绑定私有属性时保持 legacy fail-open。
- stable tool lifecycle 与 legacy progress callback 不能同时投递同一调用；wrapper 检测必须读取 agent 当前实际 callback，显式 fallback 标记仅用于卡片路径未接受时恢复原生进度。
- 已识别 `system.notice` 必须按 sidecar 结果分流：已有卡片异步更新的 `accepted` 与独立卡首次投递的 `delivered` 抑制原生文本，`not_sent` 回退原始通知文本，`unknown` 只尝试固定通用提示且不重复原始通知文本；`accepted` 必须同时带有 `applied=true`，不可解析响应一律视为 `unknown`。
- 上下文压缩只从 `_status_callback_sync` 的固定 `Compacting context` 标记产生 `context-compaction`；不得用静默 watchdog、普通 compression 文本或虚构百分比推断。
- cron completion hook 必须位于 `extract_media` / `media_files` 过滤之后：`native_delivery=required` 时清空原生正文但继续文件上传，不能在媒体提取前提前返回。
- 不得恢复固定 command allowlist；built-in、alias、plugin/quick、unknown feedback 都必须经过统一 command context。V4.2.0 的飞书私聊裸 `/update` 必须优先进入专用维护流；群聊、非飞书、别名和带参数更新仍使用 Hermes 原 handler。
- `/update` 确认必须在 ACK 前创建 durable job，再持久化 HFC drain lease，并用 Hermes 原生 external drain marker 关闭 turn/cron/API 准入；维护进程必须看到 schema v2 的 `_active_work_count()` 聚合计数、external drain 生效与连续 heartbeat，不能把缺失计数解释为零。V4.2.1 要求 startup adapter 在启动 runtime control 前登记 live runner，确保首个 heartbeat 即携带完整聚合证据。V4.2.2 要求 native action 快速 ACK 后由 sidecar 异步 PATCH 同一个 message：取消必须写入 terminal card 且绝不调度 updater；确认必须先尝试发布 locking/准备态，再启动维护任务。V4.2.3 要求 WebSocket hook 将 `update_evidence_fingerprint` 原样转发给 sidecar；缺失或不匹配证据仍必须 fail-closed。V4.2.4 要求 `message.started` 优先使用真实入站 message ID，并且新 turn 不得通过 reply alias 复用旧 session；同一轮后续 stream/tool 事件仍可通过 alias 关联当前卡片。
- updater 前显式 fetch 并展示当前 `origin/main` 快照，不能把 fork 的 `upstream/main` 摘要当作 apply 目标；确认语义必须透明说明官方 updater 会再次 fetch 最新 `origin/main`。若 HEAD 相对快照漂移，必须先恢复 HFC/hook/services，再以失败终态报告。Gateway 任务数与聚合计数能力必须来自同一次 `_active_work_count()` 采样，且 runtime 必须证明实际 `HERMES_HOME` 与 checkout 的 marker 目录一致。完成态还必须证明新 sidecar PID、新 runtime identity、fresh heartbeat、目标 Python identity、版本、`site-packages` 导入和 owned hook。
- command context 只能接管非空文本；Agent turn、专用交互卡和媒体路径保持原边界。只有 create/PATCH 成功才抑制对应原生文本。
- 已连接 Lark WebSocket 的 live `EventDispatcherHandler` identity 不得被重建或替换；只可通过 `_ws_thread_loop.call_soon_threadsafe(...)` 更新现有 `p2.card.action.trigger` processor callback，不兼容内部结构必须 fail-open。
- `_hfc_original_handle_resume_command` 必须保留为唯一恢复执行路径；不要在 HFC 重写 session ownership、continuation 或 `switch_session` 规则。
- 群聊/topic picker 只有在发起者 `open_id` 可验证时才显示；不可验证时 fail-open。私聊不额外比较操作者。
- slash-confirm 必须在回调线程先原子 claim pending state，再提交到 Gateway loop；submit 返回 false 或抛错时必须回退执行，不能空 ACK 后丢失点击。
- schema-2 form submit 的按钮名携带 callback token；Gateway 转发前要求非空 chat 和可验证操作者，sidecar 再要求 token/chat 完全匹配。
- `interaction.requested` 对 `/events` 只 POST 一次；响应丢失后只允许只读查询 interaction 状态，禁止重放事件。
- 非回环 sidecar 的 `/card/actions`、`/interactions/{id}` 与 `/messages/{id}/summary` 必须使用独立 `hfc-sidecar-request-v1` proof；签名绑定 HTTP method、规范 path 与 raw body，不能复用 `/events` proof，也不能只把 callback token 当作网络认证。
- Gateway poll 到达 deadline 时只 best-effort 发送一次新的 `interaction.failed`，不得重放原始 `interaction.requested`；发送失败保持 fail-open，不循环重试。
- policy cache 必须有界、短 TTL、线程安全且按 profile/chat/endpoint 隔离；认证、timeout、reload 或响应异常全部回到 Hermes 原生路径。terminal 必须清理 turn 决策、pending delta 和 native-media suppression。

### `hermes_feishu_card/server.py`

职责：

- 管理 `CardSession`。
- 根据 `turn_id`（若存在）、`message_id`、`reply_to_message_id` 和 profile/bot 信息路由到卡片。
- 合并高频 delta，安排 Feishu PATCH。
- 处理 terminal drain、终态优先更新、metrics 和 `/health`。
- 在创建 `CardSession`、alias、动画或 Feishu client state 前再次检查 delivery policy，并接收认证 runtime events 维护 readiness。

高风险点：

- 显式 `turn_id` 必须作为 canonical turn hard fence，直接决定 session ownership、ordering 和 native handoff，绝不查 reply alias；只有缺少 `turn_id` 的 legacy topic 后续事件使用不同内部 `message_id` 时，才查 `reply_to_message_id` anchor。
- terminal 事件前要 flush pending delta，避免尾部文本丢失。
- 卡片已完成时不能让 Hermes 原生 resend 泄漏成灰色消息。
- 初始 create/reply 只能在 Feishu API 边界用稳定 `delivery_uuid` 重试，最多 3 次；不重试 `/events`，也不把这套策略套到 PATCH。
- `feishu_send_retries`、`feishu_send_unknown_outcomes`、`notice_native_fallbacks`、`notice_uncertain_warnings`、`notice_update_failures`、`last_send_error` 与 `last_update_error` 必须保持脱敏；更新失败只可附加白名单校验后的 `status_code` / `api_code`，不得记录 UUID、响应正文、URL 或原始标识符。
- 无凭据的 Noop 模式必须在 `/health` 中标记 `degraded` / `noop_mode`，发送计入 `feishu_noop_attempts` 和 failure；不得生成假 message id 或计入 success。
- 首轮加载和运行中工具动画必须复用 session 的 `FlushController` 更新同一卡，并保持有界；正文/工具终态到达、更新失败、session reset 或应用清理时必须停止，不能与 terminal drain 竞争或制造独立消息。
- 群聊 `/hfc status` 只做路由诊断和 binding 提示；@机器人触发、白名单和群消息准入属于 Hermes Gateway。
- 真实 Card JSON 上限由共享 serializer 最终裁决：5 张 table、200 tagged element、28,000 UTF-8 byte。terminal native handoff 必须幂等，不能发送半截卡后再重复原生答案。
- pending interaction 期间，非 interaction lifecycle 的 card PATCH 与动画必须冻结，避免全量替换清空用户尚未提交的多选和输入。
- form submit 不接受 interaction ID 或空 token 作为凭据，也不接受缺失或不匹配的 callback chat。
- `interaction.requested` 在已有 session card 时会发送新的当前状态卡并迁移后续 message id；必须使用 interaction-specific delivery key，发送失败恢复 session，动画任务也必须从旧 message id 切到新卡。
- interaction deadline 由 sidecar 接收时刻与 `timeout_seconds` 计算为绝对截止时间 `expires_at`；action、result poll 与周期清理都在现有 session lock 下先做幂等过期转换。过期状态只能是 failed，晚到直连按钮或 form submit 不能把它改回 completed，原卡必须刷新为“交互已过期”。
- card action 是认证的 out-of-band 回调：它生成的内部 `interaction.completed` 可以执行 identity/stale 校验，但不得推进 Hermes `/events` transport 的 `last_sequence`。batch 下一条 `interaction.requested` 必须仍按严格单调序列接受；callback 响应卡要在同一 session lock 内快照，不能混入随后到达的下一题。
- cleanup 只把尚未到期的 pending interaction 视为活跃；周期循环先转换/刷新过期 interaction，再执行普通 retention cleanup，避免永久保留或删掉仍显示可点击按钮的旧卡。

### `hermes_feishu_card/install/patcher.py`

职责：

- 唯一允许修改 Hermes `gateway/run.py` 的代码。
- AST 定位 Gateway 函数并插入 marker-wrapped hook blocks。
- 创建 manifest、backup，支持 restore/uninstall/repair。

高风险点：

- patch 必须幂等、可移除、可检测 corrupt markers。
- Base media/local delivery filter 的 exact matcher 只接受旧版单位置参数调用，或唯一 `session_key=session_key` 关键字调用；extra/wrong/unpacked keyword 与位置参数漂移必须 fail-closed，并保持 apply/remove/restore 逐字往返。
- Hermes source-stripped Docker 目录缺少 `VERSION`，或版本 metadata 可读但格式不可解析时，只能在 gateway anchors 可验证时兜底。
- 新 hook block 必须有 patcher 单测和 remove/restore 覆盖。
- Hermes 0.20 将同步 delivery-ledger 写入包装为 `await asyncio.to_thread(...)`；只可在已验证的 ledger anchor 内解包这一精确结构，未 `await`、其他 wrapper 或全局 call 解包必须继续拒绝。
- `_status_callback_sync` 是 optional `status_callback` capability；缺失时保持其他安装路径可用并由 doctor 报 partial compatibility。
- Hermes `79445a496c` facade 拆分的路由、Base helper 契约与 V4 ownership 边界见 [拆分适配说明](hermes-decomposed-patcher.md)。源码布局不得被用作 native hook 能力证明。

### `hermes_feishu_card/install/recovery.py` and operations execution

职责：

- `plan_recovery(...)` 只根据当前 Hermes detection、manifest、backup 和 marker 证据生成可脱敏展示的 recovery plan。
- `execute_recovery(...)` 在 mutation 前重新规划并比较 fingerprint，只执行可验证的修复；证据变化、用户编辑或无法确认的状态必须拒绝。
- `server.py` 的 operations-card executor 只消费带确认的 plan，保留私聊/群聊 ownership 边界和 CLI fallback。

高风险点：

- 不把 recovery plan、state-dir transport secret、真实 chat id 或安装路径未经脱敏地放进 card、`/health` 或日志。
- 自动 repair 只适用于 known-safe state；`--no-repair` 必须保持有效，用户编辑不能被覆盖。
- `integrity.mode=safe` 还必须验证 Git root/ancestry/current blobs、provenance、anchors、可逆 patch 和 mutation 前 fingerprint；runtime heartbeat 本身不构成 mutation 权限。修复只设置 restart required，不能自动重启 Gateway。
- 调整 planner/executor 时运行 `tests/unit/test_recovery.py`、`tests/unit/test_operations.py`、`tests/integration/test_server.py`；涉及安装器时再加 `tests/integration/test_cli_install.py`。
- ownership manifest 与 recovery plan 的受管相对路径统一写成 POSIX 表示。兼容旧 Windows manifest 时只可把 `\\` 规范化为 `/` 后做精确等值比较；绝对路径、父目录跳转或多余后缀仍须拒绝。

### `hermes_feishu_card/process.py` and sidecar lifecycle

职责：

- 按 `service.manager` 的 `auto` / `systemd-user` / `systemd-system` / `detached` 明确选择进程所有者。
- `auto` 仅在 Linux user manager 可用时使用独立 transient user service，其他环境使用 detached。
- 用 PID、process token 和 manager/unit identity 管理 status、migration 与 stop。

高风险点：

- `start_new_session=True` 不能脱离 systemd cgroup，不能作为 Linux Gateway 重启隔离方案。
- systemd 可重启 sidecar 并改变 PID；status/stop 必须以 token 和记录的 unit 为稳定身份，不能只比较旧 PID。
- Hermes 升级可能替换 `gateway/run.py` 而保留 HFC backup/manifest；CLI `status` / `start` 必须只读识别 verified `stale_unpatched`，仅对可执行的 `accept_hermes_upgrade` plan 给出显式恢复命令。用户改动、损坏或证据不足必须 fail-closed，不得自动重写 Hermes 或自动重启 Gateway。
- runner 必须真正读取 `setup` / `start` 显式传入的 `--env-file`。配置优先级保持 YAML < 同目录 `.env` < 显式 env file < process env；禁止为了修复 systemd 环境而隐式读取全局 `~/.hermes/.env`。
- 升级迁移只能停止 PID/token/health 三者一致的旧进程，未知进程保持 fail-closed。
- `auto` 不得探测 system bus、调用 sudo/pkexec、写 `/etc` 或静默 fallback 到 system manager；`systemd-system` 只能显式使用 transient unit。
- guided `setup` 在 `service.manager=auto|systemd-user`、user manager 可用且 `loginctl ... Linger=yes` 时默认进入 persistent systemd user 路径；不可用时必须显式警告重启风险并给出精确 `enable` 命令，`--transient` 是显式 opt-out。不得自动 enable linger、调用 sudo 或进入 system manager。随后以 exact Hermes venv Python、absolute config/env/Hermes root 渲染 unit；unit 与 `persistent-service.json` 都必须为 owner-only regular file，并以 `unit_sha256` 互证。
- persistent enable 前先用现有 token/pidfile owner 安全停止 transient/detached sidecar；无 ownership 的同名 active unit、停服失败、manifest/unit 不完整或 drift 均拒绝。enable/health 失败后只有 `disable --now` 成功才可删除 ownership evidence。
- `start` 对 exact active persistent unit 只返回 already running；`stop` 不绕过 persistent owner，必须走 `disable`。独立 `start` 仍为 transient，安装器不自动执行 `loginctl enable-linger`。
- 调整 lifecycle 时运行 `tests/unit/test_process.py`、`tests/integration/test_cli_process.py` 和 `tests/unit/test_install_scripts.py`。
- Windows venv launcher 与实际 runner PID 不一致时，只允许 `win32 + detached + exact token + pidfile PID == runner parent PID` 的一次重绑，并在原子写后重新读取精确记录；其他平台、manager 或不完整证据保持 fail-closed。

### Hermes Feishu SDK 能力门禁

- Hermes adapter 出现 `extra_ua_tags` 调用时，Gateway venv 的 `lark_oapi.ws.Client` 必须支持同名参数；不能只看 Gateway 进程是否存活。
- `doctor` 保持只读并报告 `feishu_sdk`；`setup/install` 仅在 adapter 确实需要该能力且当前 SDK 不兼容时安装 `lark-oapi==1.6.8`，随后以构造签名复检。
- Windows Defender/venv 冷启动可能超过 8 秒；SDK 能力与已安装 HFC import 两个隔离子进程探针均使用 30 秒上限，超时仍按失败处理。PowerShell installer 必须显式检查 native `pip` / `setup` 的 `$LASTEXITCODE`，失败时不得继续打印完成。
- 修改门禁时运行 `tests/integration/test_cli.py`、`tests/integration/test_cli_install.py` 和 `tests/unit/test_diagnostics.py`。

## 常见改动对应测试

| 改动 | 先跑 | 发布前还要跑 |
|---|---|---|
| runtime event 抽取、topic、notice | `python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_server.py -q` | `python -m pytest -q` |
| Lark WebSocket handler / command-card callback | `python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_feishu_sdk_compat.py -q` | Python 3.11 + `lark-oapi==1.6.8` + `websockets==15.0.1` CI、真实 Feishu 稳定性 smoke、`python -m pytest -q` |
| `/resume` / `/model` 原生 picker | `python -m pytest tests/unit/test_hook_runtime.py tests/unit/test_patcher.py tests/integration/test_cli_install.py -q` | 真实 Feishu 私聊、群聊、topic smoke + `python -m pytest -q` |
| 群聊路由诊断 / 工具详情 | `python -m pytest tests/unit/test_bots.py tests/unit/test_session.py tests/unit/test_render.py tests/integration/test_server.py -q` | `python -m pytest -q` |
| patcher / install hook | `python -m pytest tests/unit/test_patcher.py tests/integration/test_cli_install.py -q` | `python -m pytest -q` |
| renderer / timeline / Markdown | `python -m pytest tests/unit/test_render.py tests/unit/test_session.py -q` | `python -m pytest -q` |
| delivery policy / native bypass | `python -m pytest tests/unit/test_delivery_policy.py tests/unit/test_hook_runtime.py tests/integration/test_server.py -q` | 真实 card → native → card + `python -m pytest -q` |
| runtime integrity / strict repair | `python -m pytest tests/unit/test_runtime_control.py tests/unit/test_integrity.py tests/unit/test_integrity_coordinator.py tests/integration/test_cli_integrity.py -q` | upgrade simulation + `python -m pytest -q` |
| service manager / Docker | `python -m pytest tests/unit/test_process.py tests/integration/test_cli_process.py tests/unit/test_install_scripts.py -q` | Linux + ordinary Docker smoke + `python -m pytest -q` |
| CLI / doctor / install scripts | `python -m pytest tests/integration/test_cli.py tests/unit/test_install_scripts.py -q` | `python -m pytest -q` |
| README / release notes / TODO | `python -m pytest tests/unit/test_docs.py -q` | `git diff --check` |
| version bump | `python -m pytest tests/unit/test_package_metadata.py tests/unit/test_docs.py -q` | `python -m pytest -q` |

## 维护原则

- 先加失败测试，再修复复杂 bug。
- 不直接改 Hermes 本体；通过 patcher 和 install 命令验证。
- 保持 hook fail-open，但对已识别且已接管的 Feishu 卡片消息要抑制重复原生文本。
- 真实 Feishu 凭据、chat id、token 不进入仓库。
- 截图入库前脱敏；优先展示项目能力，不展示私人内容。
# Hermes update maintenance

Use `hermes-feishu-card maintenance status` before testing the private
`/update` flow. `setup` provisions the independent runtime from the exact HFC
install spec; an explicit wheel can be staged with:

```bash
hermes-feishu-card maintenance provision \
  --hermes-dir ~/.hermes/hermes-agent \
  --wheel /absolute/path/to/hermes_feishu_streaming_card-X.Y.Z-py3-none-any.whl
```

The job journal and cached wheel live under the private HFC state directory.
Never hand-edit a job, copy secrets into it, or bypass a failed evidence check.
If a job stops, inspect it with `maintenance status`; only resume the exact
existing job file. Recovery deliberately uses the official Hermes updater and
HFC patcher and does not implement a custom Git rollback.

Hermes and maintenance venvs commonly expose `bin/python` as a symlink. Keep
that lexical venv path when launching isolated commands and validating
`site-packages`; resolving it to the backing interpreter silently discards the
venv package boundary and can make `/update` fail before mutation.

The native read-only update check and the explicit target fetch may each take
up to five minutes on a slow remote. They must still fail closed on timeout;
do not replace the bound `origin/main` snapshot with stale local metadata.

When the root `VERSION` file is absent, version detection reads only a literal
top-level `hermes_cli.__version__` assignment without importing Hermes before
falling back to Git tags. This keeps 0.20+ doctor and update results from
reporting an older nearest tag.
