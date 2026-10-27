# 维护指南

## 改动前先判断范围

这个项目的风险主要来自三类边界：

- Hermes Gateway 内部变量和事件结构会变。
- Feishu/Lark 卡片 API 与 WebSocket 交互路径有多条 fallback。
- sidecar 执行与授权状态仍是进程内内存；卡片展示可以从有界私有检查点恢复，不能据此恢复执行或旧审批。详见 [卡片重启恢复](card-restart-recovery.md)。

小文档改动可以直接做；涉及 `hook_runtime.py`、`server.py`、`patcher.py`、安装器或 release 流程时，先读根目录 [AGENTS.md](../../AGENTS.md) 的边界与验证要求。技术栈、模块拆分和测试环境见[开发规则](development-rules.md)；新增功能、配置和交互设计见[功能准入规则](feature-rules.md)。

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
- 官方 PluginRuntime 的所有事件还必须保留同一 ingress 的 `profile_id` / `profile_source`，包括 patch delta、状态提示、交互准入、工具观察和结束事件。事件 data 由统一身份绑定函数构造，调用方数据不得覆盖已验证的 profile；不能只在 started 写入身份，否则后续事件会创建另一份 session，留下未收尾卡片。
- patch delta 使用协议支持的 `phase=update`。最终 `message.completed` / `message.failed` 的 HTTP 确认沿用运行时的 10 秒上限，并受调用者更短预算约束；不能再被普通 observer 超时截短。带有已知 runtime admission 协议的 patch `interaction.requested` 也使用该有界预算，因为它等待交互卡送达确认；其他事件保留原 observer 上限，未知结果仍不冒充成功。
- Hybrid 最终投递回执必须穿过 `TurnRunner.run_sync` 的正常与空响应结果重组分支，再由外层 Gateway 一次性消费；只验证 finalizer 和消费 helper 不足以证明真实链路。回归应执行固定上游源码的两个原始 Return AST，核对除私有回执外所有原字段保持一致。排队续问在递归前直接投递上一轮结果，必须在该实际出口独立消费同一回执并保留正文、历史与后续回调。响应含 `MEDIA:` 附件标签、Gateway 后续追加附件或改写响应时，保留原生投递，不能让早期卡片回执吞掉新增内容。
- Hybrid 原生工具提示在 callback 入队前和 sender 每次出队后检查当前卡片所有权，保留独立日志。只有同一 canonical turn、profile、chat/thread、Gateway session 与 generation 的 `CARD_ACTIVE` 才抑制；未知、未接管、其他平台及异常继续原生路径。后台 sender 启动早于接管，不能仅在方法入口检查一次。接受证明仅存于同一个 `TurnContext`，绑定同一 runtime 和完整路由，终结后用于丢弃该上下文的进度尾队列；换轮次、换 runtime 或 runtime 关闭不得复用。取消任务时也检查该证明，避免最后一次队列清空或编辑重新产生原生提示。
- stable tool lifecycle 与 legacy progress callback 不能同时投递同一调用；wrapper 检测必须读取 agent 当前实际 callback，显式 fallback 标记仅用于卡片路径未接受时恢复原生进度。
- 已识别 `system.notice` 必须按 sidecar 结果分流：已有卡片异步更新的 `accepted` 与独立卡首次投递的 `delivered` 抑制原生文本，`not_sent` 回退原始通知文本，`unknown` 只尝试固定通用提示且不重复原始通知文本；`accepted` 必须同时带有 `applied=true`，不可解析响应一律视为 `unknown`。
- canonical turn 的 thread/task carrier、有限 registry 与弱 Agent 引用由 `canonical_turn.py` 单独持有；hook 入口为直接兼容导出，PluginRuntime 直接读取同一模块，不反向导入 hook adapter。不可为了 footer 新建第二份身份或授权状态。
- 上下文压缩只从 `_status_callback_sync` 的固定 `Compacting context` 标记产生 `context-compaction`；不得用静默 watchdog、普通 compression 文本或虚构百分比推断。
- cron completion hook 必须位于 `extract_media` / `media_files` 过滤之后：`native_delivery=required` 时清空原生正文但继续文件上传，不能在媒体提取前提前返回。
- 不得恢复固定 command allowlist；built-in、alias、plugin/quick、unknown feedback 都必须经过统一 command context。V4.2.0 的飞书私聊裸 `/update` 必须优先进入专用维护流；群聊、非飞书、别名和带参数更新仍使用 Hermes 原 handler。
- `/update` 确认必须在 ACK 前创建 durable job，再持久化 HFC drain lease，并用 Hermes 原生 external drain marker 关闭 turn/cron/API 准入；维护进程必须看到 schema v3 的已绑定 Gateway owner 与 schema v2 引入的 `_active_work_count()` 聚合计数、external drain 生效与连续 heartbeat，不能把缺失计数解释为零。V4.2.1 要求 startup adapter 在启动 runtime control 前登记 live runner，确保首个 heartbeat 即携带完整聚合证据。V4.2.2 要求 native action 快速 ACK 后由 sidecar 异步 PATCH 同一个 message：取消必须写入 terminal card 且绝不调度 updater；确认必须先尝试发布 locking/准备态，再启动维护任务。V4.2.3 要求 WebSocket hook 将 `update_evidence_fingerprint` 原样转发给 sidecar；缺失或不匹配证据仍必须 fail-closed。V4.2.4 要求 `message.started` 优先使用真实入站 message ID，并且新 turn 不得通过 reply alias 复用旧 session；同一轮后续 stream/tool 事件仍可通过 alias 关联当前卡片。
- updater 前显式 fetch 并展示当前 `origin/main` 快照，不能把 fork 的 `upstream/main` 摘要当作 apply 目标；确认语义必须透明说明官方 updater 会再次 fetch 最新 `origin/main`。若 HEAD 相对快照漂移，必须先恢复 HFC/hook/services，再以失败终态报告。Gateway 任务数与聚合计数能力必须来自同一次 `_active_work_count()` 采样，且 runtime 必须证明实际 `HERMES_HOME` 与 checkout 的 marker 目录一致。完成态还必须证明新 sidecar PID、新 runtime identity、fresh heartbeat、目标 Python identity、版本、`site-packages` 导入和 owned hook。
- command context 只能接管非空文本；Agent turn、专用交互卡和媒体路径保持原边界。只有 create/PATCH 成功才抑制对应原生文本。
- 已连接 Lark WebSocket 的 live `EventDispatcherHandler` identity 不得被重建或替换；只可通过 `_ws_thread_loop.call_soon_threadsafe(...)` 更新现有 `p2.card.action.trigger` processor callback，不兼容内部结构必须 fail-open。
- `_hfc_original_handle_resume_command` 必须保留为唯一恢复执行路径；不要在 HFC 重写 session ownership、continuation 或 `switch_session` 规则。
- 群聊/topic picker 只有在发起者 `open_id` 可验证时才显示；不可验证时 fail-open。私聊不额外比较操作者。
- slash-confirm 必须在回调线程先原子 claim pending state，再提交到 Gateway loop；submit 返回 false 或抛错时必须回退执行，不能空 ACK 后丢失点击。
- slash-confirm 回调接管须核对同步、异步两个实际方法，不能仅信任可继承或重绑定后残留的 installed 标记。旧 dispatcher 进入异步 fallback 时，也必须更新原确认卡；更新和补发 reply anchor 只使用发送时保存的 IM message ID，不能使用点击 token。不能靠注册通用 `/card` 命令来绕过原交互的身份和一次性确认校验。
- schema-2 form submit 的按钮名携带 callback token；Gateway 转发前要求非空 chat 和可验证操作者，sidecar 再要求 token/chat 完全匹配。
- `interaction.requested` 对 `/events` 只 POST 一次；响应丢失后只允许只读查询 interaction 状态，禁止重放事件。官方插件通过独立签名的 `GET /interactions/{id}/delivery` 核对原 interaction id 和完整 admission descriptor 的摘要，查询最多 3 秒，不刷新等待心跳或触发过期处理；摘要只在平台送达且当前 reservation 提交后绑定到该 interaction 对象，后续选择结果更新保留，新建同 id 对象不得继承。缺失或不匹配的摘要不能证明已送达，不能据此抑制原生交互。
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

- 重定向必须从已校验的 agent turn binding 取回原 conversation ID，不能退化为 source.thread_id；server 的 conversation/profile/chat 边界保持不变。
- 显式 `turn_id` 必须作为 canonical turn hard fence，直接决定 session ownership、ordering 和 native handoff，绝不查 reply alias；只有缺少 `turn_id` 的 legacy topic 后续事件使用不同内部 `message_id` 时，才查 `reply_to_message_id` anchor。
- 群聊新轮清理必须同时匹配可验证的 `sender_open_id`；缺少发送者时保留旧卡。hook 的首事件 fallback 也必须携带发送者和 `chat_type`。原生插件通道使用 Gateway session key 的哈希 `execution_scope` 隔离；缺少可匹配范围时不猜测接管。redirect 只清理明确指定的来源 turn，不能结束同群其他成员的卡片。参见 Issue #348。
- terminal 事件前要 flush pending delta，避免尾部文本丢失。
- decomposed Hermes 的 stale-generation 分支只在返回值明确 `interrupted is True`、原 source/turn 身份一致且不是 queued 子轮结果时补发原任务终态；stale 本身不证明失败。安装器须精确验证该接点并保持逐字可逆。
- Base 的受控 `/stop` 取消可能早于工具结果返回。仅在精确处理 task 的原 owner、会话和路由一致且该 task 确已取消时补发终态；`/new`、`/reset` 与未知取消不猜测。显示投递复用 adapter 的受管后台任务，限制数量和期限，不阻塞原 pending-message drain，也不恢复执行或授权。
- 接管终局后若 PATCH 与终局重试全部失败，sidecar 必须用同一原卡/事件的稳定 UUID 补发完整终局卡，保持原 topic、bot 和内容；不能再让 Gateway 原生发送同一答案。补发使用原 session 对象，不能读取复用 key 后的新轮内容。重复终局不能重复补发；补发不确定或失败须体现在 `last_terminal_delivery` 与 `terminal_delivery_state`，不能宣称已送达。
- legacy completion 与 native `on_session_end` 共用明确结果字段解释；`completed=false` 和已知迭代/预算退出不得显示成功。未知退出码、非布尔字段和正文内容不作为失败推断依据。
- 卡片已完成时不能让 Hermes 原生 resend 泄漏成灰色消息。
- 普通卡片初始 create/reply 只能在 Feishu API 边界用稳定 `delivery_uuid` 重试，最多 3 次；这不是通用 `/events` 重放或 PATCH 策略。terminal 事件另有保留原事件身份、包含锁等待的 75 秒传输预算；HTTP 4xx 不重试。`interaction.requested` 仍只 POST 一次。见[卡片连接恢复](card-restart-recovery.md)。
- `feishu_send_retries`、`feishu_send_unknown_outcomes`、`notice_native_fallbacks`、`notice_uncertain_warnings`、`notice_update_failures`、`last_send_error` 与 `last_update_error` 必须保持脱敏；更新失败只可附加白名单校验后的 `status_code` / `api_code`，不得记录 UUID、响应正文、URL 或原始标识符。
- 无凭据的 Noop 模式必须在 `/health` 中标记 `degraded` / `noop_mode`，发送计入 `feishu_noop_attempts` 和 failure；不得生成假 message id 或计入 success。
- 首轮加载和运行中工具动画必须复用 session 的 `FlushController` 更新同一卡，并保持有界；正文/工具终态到达、更新失败、session reset 或应用清理时必须停止，不能与 terminal drain 竞争或制造独立消息。
- 群聊 `/hfc status` 只做路由诊断和 binding 提示；@机器人触发、白名单和群消息准入属于 Hermes Gateway。
- 真实 Card JSON 上限由共享 serializer 最终裁决：5 张 table、200 tagged element、28,000 UTF-8 byte。terminal native handoff 必须幂等，不能发送半截卡后再重复原生答案。
- pending interaction 期间，非 interaction lifecycle 的 card PATCH 与动画必须冻结，避免全量替换清空用户尚未提交的多选和输入。
- form submit 不接受 interaction ID 或空 token 作为凭据，也不接受缺失或不匹配的 callback chat。
- `interaction.requested` 的 legacy callback 卡不直接替代已有 schema 2.0 stream owner。选择后仅记录显示边界；实际后续输出才以稳定 continuation key 新建同路由 schema 2.0 卡，确认送达后切换 owner。失败/不确定时保留旧 owner 和内容；首张仅有 legacy 卡时，回退、终局与展示恢复继续同方言并保留无 token 回执。见[交互续答](interaction-continuation.md)。
- interaction deadline 由 sidecar 接收时刻与 `timeout_seconds` 计算为绝对截止时间 `expires_at`；action、result poll 与周期清理都在现有 session lock 下先做幂等过期转换。未声明暂停能力的过期状态为 failed；声明 pause_on_timeout 且没有 native runtime admission 的同步 Gateway 审批可转 paused，并撤销旧 token。晚到按钮/form 均不能批准过期操作。恢复按钮要求 Gateway 在最近 15 秒内仍轮询，旋转 token 并重新展示完整范围，只恢复审阅窗口；后续明确选择才解析原 request_id。不得延长 native admission 证明或把重启后旧执行当作仍在等待。
- card action 是认证的 out-of-band 回调：它生成的内部 `interaction.completed` 可以执行 identity/stale 校验，但不得推进 Hermes `/events` transport 的 `last_sequence`。batch 下一条 `interaction.requested` 必须仍按严格单调序列接受；callback 响应卡要在同一 session lock 内快照，不能混入随后到达的下一题。
- cleanup 只把尚未到期的 pending interaction 视为活跃；周期循环先转换/刷新过期 interaction，再执行普通 retention cleanup，避免永久保留或删掉仍显示可点击按钮的旧卡。
- 展示检查点最多保存一个有界、脱敏、无 token 的实际辅助回执；重启验证原 profile/bot/client 后按原方言更新，不恢复执行或授权。只有完整回执 PATCH 明确成功才精简 owner 重复范围；终态已补发或送达不确定时禁止回填旧 owner。
- 重启临时文本的撤回遵循 [通知生命周期](notice-lifecycle.md)：精确 profile/bot/chat/thread、发送前登记代次快照、成功投递后调度、DELETE 成功后释放记录。不能用空 thread 通配其他话题、话题活动清 home、或静默替换失败的待撤回项。新增发卡入口必须传入当前 profile，更新入口通过真实消息 owner 取身份。

### `hermes_feishu_card/install/native_hooks.py` 临时源码快照

只读快照在能力探测结束或构建异常后必须回收。删除前只通过绑定的目录描述符恢复本目录写权限，不改文件权限、不沿符号链接进入外部目录；身份变化时保留未知路径并报告清理未完成。正常关闭、异常、重复关闭、目录重绑定、符号链接和硬链接保护见 `tests/unit/test_native_snapshot_cleanup.py`。与 `tests/unit/test_native_hook_capabilities.py`、安装检测和 CLI 安装一起验证。收尾应同时检查任务目录与实际系统临时目录，不能只看 `TMPDIR`。

native 来源探测在快照前后分别用可信 Git 验证 HEAD 与 clean state。HEAD 与 status 各有 5 秒有界预算，不重试、不缓存来源、不省略末尾复核；超时继续拒绝能力。公开 refusal 保持兼容，诊断中的 `stage=git_head kind=timeout` 才说明读取超时，不能从 `source_commit_mismatch` 单独推断实际提交漂移。

### `hermes_feishu_card/install/patcher.py`

V5.0.7 的 `install/completion_contracts.py` 校验 v0.22.5 完成包装器的参数、原始投递调用、后置 usage-limit offer 和原返回值。patcher 继续负责继承/MRO 归属、唯一接点与逐字恢复；不接受任意同形方法名。回调绑定移到当前轮最终配置后，保留静默回合的精确清空分支与审批回调；缓存 Agent 不得复用上一轮 sink。


Cron 的交付位置定位在 `install/cron_contracts.py`，通用正文/docstring 位置在 `install/ast_contracts.py`；不得把媒体 hook 移到 extract/filter 之前，或用文件拆分改变旧源码接受范围。

Gateway stale-result 的 AST 来源绑定判断在 `install/gateway_contracts.py`。handler 发现、marker、生成与写回仍由 patcher 管理；不得借提取放宽唯一 guard、同块赋值、原 source/pinned tuple 及禁止重赋值的条件。

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
- Recovery/Integrity/CLI 的 secure filesystem primitive 共同归 `install/posix_transaction.py`：目录身份、bound read/stage/replace/unlink 和 cleanup 故障必须注入实际 owner。planner fingerprint、Git/provenance、锁、pre/post validate、逆序回滚仍在原入口；不能把 CLI portable fallback 或原不存在目录的策略用于强恢复。
- 变更共同 I/O 时同时验证 `tests/unit/test_recovery.py`、`tests/unit/test_integrity.py`、`tests/integration/test_cli_integrity.py` 和 `tests/integration/test_cli_install.py`，重点保留 parent ABA/swap、同 inode 编辑、stage 替换、失败 rollback 与原 bytes/mode；最后执行 full/package/platform 门禁。实现与独立验收分开。
- 固定版本 Hybrid 安装的 `terminal_disposition` 已扩展投递回执与原生进度边界。检查和恢复旧清单时，只接受 canonical backup 经精确历史 renderer 生成、与受管文件逐字节相等的版本；不能通过忽略缺失 marker 来迁就旧安装。旧 revision 的检查明确要求先官方 restore 再 install，不得将旧 hook 报为新版安装完成。新安装使用完整新 fragment 集合，旧版普通安装到新版检查/恢复必须单独回归。
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
- CLI 与插件 binding 共用 `install/runtime_python.py` 的隔离 PM 探测。存在 committed venv 时优先选择它并保留 venv launcher 路径；探测失败、相对路径或已删除的 generation 必须拒绝，不能落回旧 `.venv`。只有没有 PM 模块或 PM 明确返回无选择时保留 legacy 行为；插件仍验证 prefix/site-packages/可执行目标，显式 Hermes home 传给 PM 探测。
- Hermes PM 重建会删除旧环境；独立 sidecar 不能假定长驻解释器路径永远存在。可用 `service.python_executable` 绑定独立 HFC venv；setup/start/enable 均遵守此选择，先验证 Hermes hook，再验证侧车的版本与普通 site-packages 来源。配置缺省保持原行为；指定环境失效必须拒绝启动，不静默切回 PM。
- 旧侧车仍响应 health 不代表运行文件可用。升级后的恢复顺序是检查当前 PM 与进程来源，备份并停止侧车，通过官方 install 修复已验证的升级状态；仅 installed 且侧车停止时 acknowledge-review，再启动侧车与 Gateway。不得清除未知围栏或手工改 hook。
- PM 迁移、schema 1 flat wheel、旧 runtime 重绑、VCS 安装边界、实际服务管理器/KeepAlive 与旧 fence target CAS 的完整顺序见 [PM 恢复](pm-recovery.md)。当前 509 management CLI 负责本候选的 schema 1 flat 重绑；v5.0.8 命令基线保留为历史，v5.0.7 CLI 没有 `rebind-legacy`。
- `auto` 不得探测 system bus、调用 sudo/pkexec、写 `/etc` 或静默 fallback 到 system manager；`systemd-system` 只能显式使用 transient unit。
- macOS `enable` 不可用时只提供平台解释。自建登录 LaunchAgent 可用 `RunAtLoad=true` 一次运行会分离子进程的 `start`，不可推荐 `KeepAlive=true`。无 verified pidfile 必须先确认进程归属；只有证实自建 LaunchAgent 直接运行 runner 才提供对应 `launchctl` 停止路径，不据 pidfile 缺失推断 launchd。
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

## 稳定性回归准入

修复前后的失败证据、真实流程覆盖和发版阻断条件见 [稳定性测试规则](stability-test-policy.md)。新兼容契约必须验证错误参数、错误 adapter、错误控制流被拒绝，并实际执行补丁后的投递顺序；失去上游完成证据的旧轮不得被测试固化为成功。

V4.4.6 集中修复的发布范围及现场边界见 [发布说明](../release-notes-v4.4.6.md)；#293/#295 仍为独立功能需求。

## v4.5.1 回归边界

CardKit 创建和所有更新必须经过同一短 ID 映射：字符串不同的超长 ID 也可能触发服务端 300301。诊断只保留哈希及数值摘要。

独立心跳通知可以过期撤回；会话正文、已结束卡片和被新消息接管的槽位不能撤回。审批续期必须证明原可暂停等待方仍存活，不能复活失效任务或扩展 native admission。重启拒收文本不是执行心跳，不能显示持续生成。

## 排队完成与临时通知（V4.5.2）

排队完成必须传递与 `first_response` 同源的最终执行结果；新布局使用 `_delivery_result`，旧布局回退 `result`。不能从响应文本猜测成功。心跳撤回 hook 只能出现在唯一已知异步通知函数的成功新发送路径；编辑成功不重新安排，未知或歧义来源不注入。补丁须验证逐字恢复并执行 send/edit/failure 回归，不以 marker 存在替代语义验证。

## 统一稳定性修复

临时提示撤回需携带 profile/chat 路由，在 sidecar 按当前 bot binding 解析；多 profile 不猜默认机器人。结构化 reasoning 使用单一、逐轮绑定的 callback，不从答案正文推断，不同时注册第二条 observer。卡片重启检查点与原执行分离，终局重试的总预算包含锁等待和请求；记录与重试日志必须脱敏。

## V4.6.1 回调与通知边界

工具 hook 必须位于同一回调方法的必经赋值之后，不能选后置静默分支或嵌套函数；未知条件控制流不作为成功安装证据。实际执行普通与静默分支验证工具事件，而非仅检查 marker。

原生状态撤回保留 task-local profile/chat/thread；跨聊天或无效身份拒绝。仅命中已知临时模板才撤回，“⏳”本身不代表可以删除，答案、队列确认与失败解释必须保留。独立审批卡去重只影响会话展示，不移除专用回调卡的操作和决定结果。

撤回日志仅允许已验证的状态/错误码和哈希标识，不能写入原始异常正文。默认 INFO 限于插件命名空间，不开启第三方 HTTP access 日志；已有宿主日志配置优先。中断及引导当前任务确认使用真实 event 路由，不能依赖 adapter 上残留的 profile。

## V4.6.2 共享维护证明

native plugin 的 runtime-control lease 明确标记 `gateway_admission_dependent`：它观察 Gateway 接纳的轮次，上报自己的活动计数，不独立声明 Gateway 准入与 HOME 身份。只有同一 worker 中有效的 Gateway 聚合 owner 存在时，才借用该 owner 的 drain/home 证明。native 活动、计数不完整、未知 owner、Gateway 释放、HOME 不符及 epoch 变化仍保守拒绝停服。不得用常量 true 或忽略所有缺失 provider 解除门禁。

终态工具区使用 `card.hide_completed_tool_activity`，默认 false 保持旧行为；true 仅在 completed/failed 隐藏正文工具行及旧摘要回退，不改变折叠记录、计数、正文或审批布局。

## V4.6.3 正文思考与测量边界

`stream_thinking_to_body` 默认 true。false 仅关闭运行态正文对原始 thinking 的回退，render-only 预览遵循 show_reasoning/max_reasoning_chars 且始终进入面板，不改持久timeline、终态内容保留与审批。容量检查与实际渲染必须使用同一配置；大面板与大答案仍走共享serializer门禁。

工具按ordinal排序，运行工具及各自前驱可见，终态duration_ms保存在ToolState并兼容旧检查点。failure只接收有效测量，不能用Unknown、零占位、非有限数覆盖已知值；生成的排队接续hook实际执行须保留旧turn及新turn身份。通知撤回不在本版，#331调用方仍需完整传递bot/profile。

## V4.6.4 交互、阅读与通知边界

- 真实生成的嵌套 clarify/approval callback 可能只有 `ctx`，不得假定 `locals()` 包含 `self`。从绑定 TurnRunner 或已记忆 Gateway 恢复时，验证 ctx/source、profile resolver 与同一 live adapter；未知归属 fail-open。只在当前 WS loop 更新原 processor callback，重连不能复用过期刷新标记。
- 交互后按需创建续答；分段只改变显示投影，不能清空 canonical 历史、工具、附件和统计。独立检查 legacy-only owner 的失败、终局、容量回退和重启，检查点不能含 callback token 或恢复旧授权。
- 阅读预设可选，未设置保持旧默认；全局/profile/bot 每层先预设后显式字段。`hide_completed_tool_activity` 显式 true/false 语义不变，`card-config` 仅说明配置而非证明进程已加载。
- 重启通知只登记有来源证明的 exact profile/bot/chat/thread。后续成功投递/更新只清理之前快照，删除失败保留记录；空 thread 不通配。答案、交互回执和来源不明的原生 home 通知不清理。
- 提交前使用[preflight](../testing.md)核对环境和所选差异基线；实施状态及真实验收分别见[实施状态](../superpowers/plans/2026-09-20-v4.6.x-experience.md)和[V4.6.4验收](feishu-acceptance-v4.6.4.md)。

## V4.6.5 故障、通知和升级边界

模型重试耗尽的同步状态 callback 只能在另一个仍运行的 Gateway loop 上等待有界 HTTP 明确 ACK；空/旧/拒绝响应不抑制原生提示，同 loop 不阻塞。未知文案不通过宽泛关键词接管。通知持久化和已知 native producer 的归属见[通知归属](notice-ownership.md)。

工具去重必须使用明确 call_id，不能把重复工具名猜成同一次调用。默认时间线行为不变，分组裁剪只改变显示。带 call_id 的新展示检查点回退旧版可能被跳过，不得因此恢复执行。

`stale_reapplied` 只允许显式接受上游升级：旧 backup 必须匹配 manifest，严格移除已知模板后逐字等于当前 Git blob，检查 resolved HEAD 与事务 fingerprint；中途漂移、未知 marker 和无 Git 证明拒绝。保留 staged index 与无关定制，不手改 installed gateway 或伪造 manifest。

## V4.6.6 收尾回归

审批精简必须有独立完整回执的显式平台 PATCH 确认；不得仅以 callback 返回体作为投递证明。展示指纹、旧段清理队列均不入检查点。通知计时提前唤醒不得取消 in-flight DELETE；原生来源只存在于精确 producer 或同一 Base delivery invocation。总条目窗口优先保留运行工具、前一步和失败条目，仍需遵守卡片预算。

## V4.6.10 Hermes 0.21.x and reopened cards

Use the outbound `_delivery_adapter_for` resolver when present; None/errors are authoritative and must not fall through to another bot. Exact optional guarded marker release is accepted only between ledger recording and sending, once. Queued/idle source identity is independent of platform reply anchors. Same-key terminal reopen requires a fresh create UUID; failed replacement sends retain only terminal display ownership, not old approval/execution state. Verify current stable and main using their hash-bound fixtures, not version-string guesses.

## V4.6.11 显示边界

- `thinking_body_tail_chars` 只限制未结束、无答案时的正文思考；0 保留原行为。每次收缩必须验证完整 Card JSON，不能截断答案/工具/审批范围，也不能承诺任意自定义面板都能容纳。
- `CardRenderResult.primary_text` 记录实际选中的正文；legacy owner 重组时必须复用该值，不能重新注入未裁剪思考。
- `process_activity.py` 只提取已回报的 allowlisted 字段，先脱敏再裁剪；不读取进程注册表、不轮询/执行进程、不虚构 ETA。patch/native 共用提取逻辑。
- 每个 `CardSession` 的 `_process_activity` 至多 32 项，必须列入 checkpoint `_EXCLUDED`，避免改变旧检查点字段契约或恢复进程关联。重复工具终态不得覆盖后续观测到的进程退出状态。

## V4.6.12 卡片宽度

`card.width_mode` 的全局/profile/bot 合并必须保留显式 default 重置。JSON 2.0 的普通卡、预算兜底、修复和维护发布复用同一配置；JSON 1.0 不注入该字段。长思考预算搜索的每个 probe 必须保留 width_mode，legacy owner 继续保留原 JSON 1.0 方言与审批正文。维护子进程按原 profile/bot 重新加载配置；客户端最终控制可见尺寸。


## 控制凭据文件（V4.6.13）

受管启动只把 `--token-file` 路径传给 runner，token 存在私有 state 目录的 `sidecar-control.token`（POSIX 0600）。文件需保留供 systemd 自动重启；不可安全读写时必须失败，不回退 argv。升级后通过原服务管理入口重启 sidecar；不要手工删除仍被服务使用的文件。同用户访问与 Windows ACL 边界见 [发布说明](../release-notes-v4.6.13.md)。


## Task presentation and preview (V4.7.0)

`presentation.py` only derives display state; `render_options.py` is the shared runtime/preview boundary. `preview.py` must stay offline and export allowlisted configuration plus synthetic examples. The task preset uses the existing animation/flush lifecycle for at most one extra silence PATCH; pending input, terminal state, owner changes and cleanup must stop it. Run `python tools/preflight.py --suite focused --module appearance --module quota --module runtime` for this boundary, then full regression for runtime changes. See [task cards](../task-cards.md).


## V4.7.2 update, identity and SDK boundaries

Persistent sidecar ownership makes card-driven `/update` unavailable before confirmation and is rechecked before Gateway stop. Use terminal maintenance through the existing service owner. If sidecar stop fails before hook mutation, only an unchanged HEAD and verified installed hook permit requesting Gateway restart; this is not a health-success claim.

Orphaned integrity fences can be explicitly rebound with `integrity acknowledge-review --rebind-target OLD_TARGET_SHA256 --yes` and explicit config/Hermes/state paths. The exact old identity and CAS snapshot are required; the current installed plan and stopped sidecar are checked twice. This is operator-authorized target rebinding, not proof that only APFS device numbering changed. Existing automatic/same-target behavior stays strict and known restart evidence is retained.

`feishu_ws_compat.py` repairs only the hash-verified lark-oapi 1.6.8 CARD-drop method body, on the live client's WS loop. It keeps SDK fragmentation and response encoding, changes no SDK files or global class, and leaves unknown/new implementations untouched. Test actual protobuf frames, split payloads, error ACKs, reconnect and native EVENT dispatch. HFC replaces the adapter callback: a log added only to the original Hermes callback cannot prove that the platform sent nothing.


## 心跳缺失时的完整性复查

完整性监视仍每轮等待 15 秒。只有完整检测得到 `installed` 且 readiness 仅为心跳等待、缺失或过期时，才延后下一次完整源码检测至本轮结束后 300 秒；期间仍检查最新 readiness，不将缺心跳改成 ready。源码变化最多延迟到下一次完整检查（另加检测时间）；离开这三种心跳状态或重启 sidecar 会清除该观察结果。检查失败及可执行修复计划不缓存，任何修复仍使用本轮新证据和原执行器校验。

`/health` 和状态查询读取最后一次已完成检查的原子快照，不等待后台检测/修复锁。快照中的诊断和计数可能落后于正在运行的检查，不能作为新的安装证明或修复授权。单次源码扫描的 CPU 成本未改变；此处减少重复扫描并解除健康接口的锁等待。

## V4.7.5 私聊按钮与目录边界

私聊命令卡的准入证明来自原请求的 task-local context，并复制到 pending state；回调只能由同一聊天和同一用户消费。不得信任 action.value 的 chat_type，不得把「不是 group」当成私聊证据。表单/运维按钮只有真实平台缓存 raw_type=p2p 才进入原生私聊准入，sidecar 继续验证 token/权限。未知聊天沿用保守路径；群聊规则不放宽。

#384 使用 `instance_binding.py` 统一显式 source/HOME 绑定。setup 将 `--hermes-home` 保存为专用 HFC env 值；runner、PM 探测、认证 runtime target 和维护任务使用同一 HOME。默认 source-parent 身份保持 v1，分离 HOME 用 v2 哈希；禁止只放宽 drain 布尔值却继续比较 source-only 身份。split 只接受 Hermes 进程级 HOME API，不接受 task-local fallback。

维护任务保存 HOME，重读配置发现漂移时在任何 Gateway 命令前拒绝；排空、停止、重启、失败清理均使用所保存的绑定。旧任务无字段时保留原语义。分离 HOME 与 `service.shared_source=true` 的卡片升级保持拒绝：此项目没有跨实例排空权，共享源码的所有 sidecar（包括默认 HOME）均须声明共享。不要把支持 readiness 写成自动协调所有实例。身份原因保留在 integrity 脱敏白名单；现存 fence 不自动清除。


## V4.7.7 表格内容保留

原生表格的 1,200 字符限制只决定是否转换，不能截断最终答案；任一表头或单元格超限时整表回退原 Markdown。Markdown 分段若无法安全拆开表头或行，保留整表交给最终 serializer/容量门禁，禁止以折叠提示替代原文。测试必须验证实际正文、task 展示、混合顺序和超限完整交接；session 中保留原文不能替代投递内容完整性。


## V3 插件启用诊断

官方 enable 命令失败与配置所有权拒绝分别呈现：preimage 后配置漂移、官方命令修改无关配置保留各自固定错误原因，便于定位。未知异常仍只显示 `official plugin enable failed`，不转发任意异常文本。原始异常通过 cause 保留供受控排查，配置恢复、prepared manifest 和源码写入门禁不变。

Hermes 对已启用的插件可只更新 `allow_tool_override: false`，不排序启用列表，也不补写缺失的 `plugins.disabled`。安装器只在 preimage 已启用 HFC 时接受这个精确分支，同时保留原列表规范化分支；两种期望均在运行官方命令前从 preimage 派生。其他插件条目和非插件配置仍须完全一致。命令异常或非零退出时，只对从 preimage 精确派生并核验的官方 postimage 逐字回滚；未知、损坏或并发编辑的配置保留原字节和 preimage，拒绝继续源码写入。`install/plugin_config_bytes.py` 在执行官方 enable 前，用受检 fixed、v0.22.2 PM、旧 pinned main 及 #451 的新 writer family 的真实 helper 和 serializer，在 0700 私有 HOME 中规划精确 postimage bytes；配置输入为 0600，结束即清理。成功出口、异常及非零出口都只能认领这些预期字节，YAML mapping 相等、注释/CRLF 等格式变化或新读出的 hash 均不能证明所有权。writer 源码、调用分支或间接 serializer 未知时拒绝。规划不启动 PM resolver 或插件；仅在受检 config import 的可选 setup 提示元数据路径临时拒绝 provider metadata，真实官方 CLI 不替换。真实 CLI 与规划的逐字对照覆盖六个固定源码族（七个 revision）及 fresh、already-enabled、canonical-disabled 分支；该证据不覆盖上游命令自身读写之间已经丢失的并发编辑。读取当前 hash 只证明读取结果，不能据此认领配置。不能通过忽略整个 `plugins` 区域或预先改写用户配置规避检查。


## 官方插件的本轮 footer 指标

V5.0.4 的 legacy `output_rate` 由 observe-only 插件供数，legacy 仍是唯一事件 producer。`install/legacy_observer.py` 管理 checkout 下 `.hermes_feishu_card_observer.json`（协议 `hfc-legacy-observer-v1`），绑定精确 HOME、runtime Python identity 和官方 config preimage/journal；它与原 v1/v2/v4 源码 manifest 分开版本化，不改写旧 manifest 语义。旧安装无此记录时重新 install 可迁移；重复 install 验证现有记录，不重复 enable 或覆盖 preimage。不要手工创建记录或修改 Hermes config。

配置写入走 `install/plugin.py` 的官方 enable，源码写入仍由既有 patcher/事务拥有。新增 enable/marker 在源码事务之前完成；已核验归属的普通失败按逆序回滚配置和 PM registration；未知配置保留并拒绝继续。doctor/repair 验证 HFC 受管值和完整证据，允许其它配置与插件的无关编辑；旧记录用隔离临时目录重放受检 writer，真实用户状态保持只读，详见 [PM 恢复](pm-recovery.md)。restore/uninstall 在无漂移时逐字恢复 preimage，有无关编辑时从当前字节仅撤销已证明的 HFC delta，再恢复源码；源码恢复失败时保留记录供重试。Windows 的显式 install/restore/uninstall 使用 portable 独占 handle，绑定原 bytes、摘要、目录身份与 reparse 状态；清理也经同一 handle 校验后删除，不能回退为任意路径 unlink。restore 的源码写入、backup/manifest 删除是一个 family 事务：删除失败会先恢复已删证据，再把源码回滚到首阶段返回的 controlled post snapshots；只回滚仍匹配本事务写入的 source，不重新 snapshot 认领当前文件。检查期间发现并发字节修改、inode 替换或目录 swap 时保留现场并转 manual review；初写失败不进入第二次盲目回滚。POSIX 快照复核与替换并非原子 CAS，执行前须停止配置写入器并避免同时编辑。源回滚失败则保留完整证据，不能留下已还原源码但缺 backup/manifest 的组合。自动 recovery 与 `integrity migrate-safe` 仍只支持 POSIX dirfd，并在取锁或读状态前拒绝。降级旧 HFC 前先用当前版本 restore，旧版本不知道此 companion ownership。

`plugins.isolation: host` 不提供进程内指标，也不改变隔离设置。插件启动只读取记录；不补装、不修复、不发送 hello/heartbeat，不新增 sidecar runtime owner。具体计量、容量与失败显示规则见[观测耗时](observed-timing.md)。

固定 `v0.22.2`（`d923a5eb487a17c0a68801bba9ef0f56c81040a8`）的必跑 CI 使用独立 `baseline_0222` checkout，以普通 wheel 的独立 Python 子进程执行真实 `hermes plugins enable`，再用公开 discovery/hook API 加载观察器；不强制 observe 模式。新装、旧 V4 迁移、重复安装、doctor、restore/uninstall 均经过真实源码事务，核对配置原始字节和所有受管源码恢复。运行时探针拒绝网络连接并记录任何 HFC secret/hello/heartbeat/listener/transport 启动尝试。PM 仅注入现成 uv/Python 和隔离的最小 virtual project；admission、resolver、配置发布仍执行固定上游源码。这不是完整 Hermes dependency closure、PM worker transport、Gateway/model 或真人客户端验收。配置使用该固定版本当前 schema；上游命令附带的未知配置迁移仍按漂移拒绝。缺少 fixture、普通 wheel 或解释器时，必跑任务失败；常规平台未提供这些输入时显式 skip。

Doctor JSON 的 `install_state.legacy_observer` 只读报告 `active`、`disabled`（含 reason）或 `not_installed`；active 仅表示安装准入证据有效，不证明进程已加载插件。仅确认 marker 缺失时保留 Hybrid 路径；metadata 不可检查时返回 disabled。共享配置事务的异常回滚与 Windows 私有原子写兼容同时适用于 legacy 和 v3。

`post_api_request` 只接收已绑定 turn/session/platform 的归一化 usage、模型名及 request id；不保存原始 response 或 provider URL。每轮最多记录 256 个 request id，重放不重复累计，缺失任一次 usage 或超出上限时省略本轮 token 合计。上下文使用量只取最新独立请求的实际 prompt token；该请求 usage 缺失时清空旧值，不把上一请求当作最新值。容量通过既有 canonical turn carrier 的弱引用读取当前 `context_compressor.context_length`；同线程/task、当前 turn 不匹配或容量未知时不显示虚构的零值。耗时从本轮 pre-LLM 观察开始计算，不把工具等待时间称为模型推理时间。

新增 publish 参数由官方安装器生成，旧安装只通过已知模板逐字重建验证后 restore，再重新安装；不得手改已安装 Hermes。指标只服务显示，不授予执行或交互所有权。交互卡明确 ACK 后，即使用户已先完成选择，也保留同一 pending identity 的卡片交付所有权，避免再次发送原生问题。

交互卡已明确接管后，原 `wait_for_response` 的异常继续沿 Hermes 调用路径传播，不得被准入的 fail-open 捕获后重发原生题目。未接管或未知接点仍保留原生路径；取消、超时和未知错误不得伪造用户答案。


交互送达回执不确定时，只发送一次准入 POST，再通过认证的 `GET /interactions/{id}/delivery` 查询完整身份指纹。这个只读端点不更新等待者心跳或过期状态；不可用通用 result poll 替代。指纹仅在 Feishu 送达且原 reservation commit 成功后写入同一个 InteractionState；新建同 ID 对象不得继承证明，检查点不恢复它。查询响应上限 64 KiB，整次查询最多 3 秒，包含响应头和分块读取；不另建常驻线程或后台重试。没有匹配证明时仍走原生回退。

实际 PluginManager 注册清单必须包含 `post_api_request`，与插件真实注册的完整集合严格相等。安装能力验证不得只检查处理函数存在，或从被测常量生成预期集合而漏掉注册接线。历史恢复只识别已知完整 revision 组合，不能任意混搭旧 publish、clarify 和 terminal 模板。


## Hermes 0.22.x 与 PM 依赖登记

以下兼容性与依赖登记修复包含在 V5.0.1；发行和真实环境验收边界见[发行说明](../release-notes-v5.0.1.md)。

Issue [#428](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/428) 的两个固定源码修订 `297552a9d646119871391d202509822cd86330df`、`7bda2a3368024f7ae1c70cef16583e7c2c891674` 把 post-turn hooks 和最终响应投递移入 `GatewayTurnPersistenceMixin`。安装器同时核对 handler 的 import/继承关系、MRO 前置 mixin 无遮蔽、调用参数与 `agent:end`；计时仍从原 handler 传入，completion 只写在 persistence owner。缺少来源、重绑定、接点重复或契约漂移均拒绝安装。旧 V4 manifest 增加这组源码目标时，先验证原备份与 ownership，再要求 `--accept-hermes-upgrade`；成功后 restore 恢复新源码的原始字节。

审批 hook 位于 `_render_approval_sync` 的 context binding 后，保留原 `_approval_notify_sync` 的 generation check 和 observer registration。等待期间及解析前再核对原 generation，通过 Hermes 的原 request ID/queue resolver 解析一次。`PendingControls` 仍只是原队列的视图；重复或已撤销的 request 不能批准下一项。原生 fallback 与异常退出仍由 Hermes 处理，不持久化执行 handle。

Issue [#431](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/431) 的故障来自直接 pip 安装只覆盖当前 PM venv。PM 代次从声明重建时，这种临时安装会消失。`install/pm_dependency.py` 现在在所选 Hermes HOME 的 `plugins/hfc-runtime-dependency/` 保存精确普通 wheel、仅含依赖的 virtual `pyproject.toml` 与 `.hfc-owner.json`。它通过官方 `hermes_cli.plugins_admission.admit_plugin_set_change` 登记 `plugins.enabled`，保留其他 enabled/disabled 与配置项；不直接编辑 PM facts 或 lock。该目录无 runtime manifest 和 `register()`，实际 producer 仍是 wheel 的唯一 `hermes_agent.plugins` entry point。`tool.uv.sources` 使用目录内 wheel path，PM 复制 workspace 后继续使用相同包字节。

已经能够 import 同版本也必须完成登记。登记后重新读取 committed venv，并核对 `site-packages`、wheel 来源摘要及每个包资源字节。显式 install spec 不会换成同版本 PyPI 包；wheel 缓存不依赖原下载临时目录。POSIX 路径使用绑定目录描述符的 rename，Windows 沿用显式 lstat/目录身份复检的 portable 安装边界；未知 PM API、明确 disabled、未归属目录、用户修改及不确定文件状态均拒绝覆盖。跨 profile 的共享依赖冲突交给 PM 原 resolver 拒绝，不自动移除另一 profile 的依赖。

普通安装失败会撤回本次新增 selection，并恢复本次替换前的 owned dependency source。PM 是否已提交不能确认时，保留 source 与诊断材料，不把超时当成未安装。`restore` 只恢复 hooks，保留依赖；`uninstall` 在 hooks 恢复后通过同一 admission API 移除 HFC 自己新增的 selection 和未修改的 owned source，不认领用户预先登记的依赖。旧版非 PM 的 pip 安装路径保持独立。

验证分层：离线 fixture 覆盖漂移拒绝、旧布局、V4 扩展迁移及逐字 restore；两个固定 SHA 另跑真实源码安装矩阵和原审批 queue/controls。PM 集成测试在私有 HOME 的最小 project 中执行固定上游的真实 dependency engine，注入现成 uv/Python 仅用于供应工具；A→B 两代均通过独立子进程从普通 wheel 导入，并检查唯一 entry point。portable 分支在本机做故障注入，尚未执行 Windows 主机验收。它没有运行完整 Hermes dependency closure、PM worker transport、生产 updater、实际 Gateway/model 或真实飞书客户端。全量 preflight 仍使用固定 `v2026.8.3` Git fixture；不能用其通过替代上述 pinned 检查。


### Session Authority completion compatibility

跨 Task completion 接纳 Hermes `5fc490e2b34123212d73ef316086f939567a725a`，以及 #451 核验的 `cf4f3a7396814ffcaef69d9af09f7cfdade3ddbe`、`f83b4df3ce1ab2ec62f1e0e90f22e0a1db21cb68`、`4d04303818b61bf3aa09278af48356315515965b` 和 `4e58c721c8cf9b51c46636ea84ea6a5cb540880a`。`session_ingress.py` / `session_authority.py` 必须匹配同一组完整源 SHA、实际 loaded code 与完整受检 Base 提取后 seam；不能混用不同来源的单文件摘要。新来源允许 Hermes 合法重建的 source，但仍核对完整路由与原 admission/owner/generation/epoch；旧来源保留对象身份要求。未知来源、loaded topology 或缺少 seam 保留原生回复，detect 单独报告 admission completion 子能力；不能从文件存在、版本字符串或整体 partial 推断支持。`admission_completion.py` 借原 row/marker maps，测试路由为 runtime/delivery；`install/admission_extraction.py` 归 install。普通旧版同 Task Base 路径保持既有支持面。

固定 v0.22.5 的 automation producer 将身份编码为 JSON 字符串；internal event 不带 `_claimed_owner`。此路径仅在原生 execute-admission frame 的 event、ref、持久 row、automation principal/owner/request、route、执行 generation 和 runtime epoch 一致时借用原 claim；未知 list/tuple ID 在首次卡片准入前保留 native，不进行可能碰撞的拼接。source 的 dataclass 字段仍逐项校验，不能借旧消息锚点补当前身份。

终局发送的排队与 HTTP 共用有界期限，取得发送锁后再次严格检查当前 owner 和 generation。sidecar 明确接管该请求后，用冻结的请求及单次回执只抑制对应的原生正文；回执不会授权新 owner 或后续事件，未确认及绑定变化继续走原生路径。`admission completion prepare rejected` 使用固定且限频的来源、route、claim、profile、generation 和 epoch 等类别，不记录 ID、HOME、payload 或正文；这些类别是排查线索，不单独证明现场根因。

在 multiplex 模式中，直接 source/locals profile 或标准 `.hermes/profiles/<name>` 仍按现有规则识别；未知自定义 HOME 不能仅凭 `fallback_default` 证明 default bot，admission 在首次卡片准入前保留 native 并报告 `profile-default-unverified`。单独进程的 env profile 不覆盖 multiplex 路由。#451 报告者的现场含本地 profile 路由及限流改动，仍须在其实际部署中独立复测。

当前 admission 首次决定卡片策略、且同一 identity 尚未 pin 时，hook 的既有 policy 入口对全部 `SUPPORTED_RUNTIME_EVENTS` 检查 readiness；notice 或 interaction 作为首事件也受此门禁，未知 admission 来源保留原生路径。admission 开始前已 pin 的普通上下文、官方 plugin 的独立 owner 不在本次追改范围；已接管路径继续遵守原投递 owner 与终局失败规则。此能力检测和固定源码测试不代表所有未知版本或 plugin 的零 card 证明，也不代表真实 Gateway/model 验收。


V5.0.7 另保留 `pm-0225` 官方配置 writer 来源族（`bf0203a794a07aac3ff764972ab9087baa4e4f4e`），不替换 `pm-0224`。配置规划、官方 enable 字节、异常回滚与源码漂移拒绝一起验收。v0.22.5 PM 忽略无依赖的 metadata-only member；跨代回归使用真实可安装的无外部依赖小包触发下一代，继续检查 HFC wheel 来源和恢复。测试夹具的代次变化不能代替实际 Gateway/model 验收。


#459/#458 的迁移恢复候选、wheel store 归属和验收边界见 [PM 迁移恢复](pm-recovery.md)。它保留严格默认，通过显式核验后重绑，不要求删除保护记录。
