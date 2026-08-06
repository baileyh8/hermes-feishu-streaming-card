# 事件流和卡片生命周期

## 总览

```text
Hermes Gateway
  -> patched gateway/run.py hook block
  -> hermes_feishu_card.hook_runtime
  -> signed sidecar /delivery/policy preflight
  -> sidecar /events
  -> signed sidecar /runtime/events hello/heartbeat
  -> CardSession / reply index / route lookup
  -> Feishu/Lark send or update card
```

Hermes 进程内的 hook 只负责提取和转发。sidecar 负责会话状态、卡片渲染、Feishu API、重试、诊断和 metrics。

## V4.1 投递与完整性控制流

新 turn 在任何 sequence、pending delta、session 或原生抑制发生前，通过 `hfc-policy-v1` 查询 per-chat 决策。card decision 在 turn 内固定；native decision 让 original Hermes 路径继续。sidecar 收到 `/events` 后在创建 CardSession、reply alias、动画或 Feishu client state 前再查一次。未知 profile、配置 reload 失败、proof 无效/过期/重放、timeout 或 malformed response 全部走 native fail-open。

normal answer/tool、approval/clarify、cron、system notice、command feedback 与 picker 使用同一决策；`/hfc help/status/doctor/monitor` 和 smoke card 是显式管理面，始终保持卡片。terminal 会清理本轮 policy cache、pending delta 和 native-media suppression；durable record 可读时，重复 terminal 复用已记录 disposition。durable record 缺失时，只有当前进程仍持有 generation、obligation、content、delivery plan、route 与 target scope 全部相同的完整 descriptor，才允许恢复同一个 fence；其余缺失、冲突、损坏或不可读状态返回 503 让 hook fail-open，绝不临时生成新的权威 descriptor，也不能只凭内存 session 声称已经投递。

实际 card JSON 由共享 serializer 检查 5 table、200 tagged element 与 28,000 UTF-8 byte。非终态超限用小型 waiting card 继续收集；terminal 超限先持久化不含正文和原始路由标识的 handoff delivery record，再立即返回 `applied:false, disposition:native` 与短期 descriptor。有旧卡时，简短 handoff notice 只作为当前进程内 best-effort 异步 PATCH，不阻塞 descriptor；进程在 PATCH 中途退出时，旧卡可能停在 waiting 状态，但完整原答案仍由 Hermes 原生投递，无卡时不额外发卡。

ACK-capable handoff 只在默认 profile 且受管的 Hermes 0.19 `gateway/run.py` 与 `gateway/platforms/base.py` 精确结构同时可验证时启用；Hermes 0.19 startup recovery 不 sweep secondary profile 的独立 ledger，因此 secondary profile 保持普通 native fail-open。run hook 只暂存 terminal；Base 在计算真实 obligation、完成媒体提取并选定真实 Feishu adapter 后，才把 obligation hash、完整答案 hash、当前 delivery-plan fingerprint 和 canonical `create` / `thread-create` route 绑定到 sidecar descriptor。新协议只广告 `native-ack-v2`、`stable-feishu-uuid-v2`、`exact-base-delivery-v1` 并接受 `hfc-native-handoff-v2` descriptor，避免新 Gateway 把旧 sidecar 的 v1 descriptor 当成 exact 授权；缺少任一能力、descriptor 版本或 exact binding 不匹配，都保持 legacy native fail-open，不能宣称受 ACK 保护。

异常恢复的总原则是：状态丢失或损坏时优先避免丢失答案，同时让 exact 与普通 fail-open 的边界对操作者可见。

每个逻辑分片使用稳定 Feishu UUID，adapter 内部重试与 Hermes ledger recovery 都复用相同 UUID。`hfc-native-handoff-recovery-v2` 只按 obligation、content、plan、route、target 五项精确查询，并始终重放 ledger 中保存的原始 content；terminal POST 已在 sidecar 提交但响应丢失/损坏时，Gateway 也立即用这五项查询找回 descriptor。若两次本机响应都丢失，本轮可先使用同五项派生的 provisional UUID seed，并在 ledger 标记 delivered 后异步重查完整 descriptor；provisional seed 本身不构成 ACK 权限。可见 recovered marker 或随机兜底文本只能走普通原生发送，不能借用旧 descriptor。只有全部 required chunks 成功且 Hermes ledger 已持久化 `delivered` 后才发送签名 ACK；ACK 失败不能回滚 ledger。若进程在平台成功与 ledger transition 之间退出，一小时窗口内由 ledger recovery 与稳定 UUID 提供 bounded idempotency；窗口外 exact descriptor 失效、sidecar 记录为 uncertain，Hermes 仍可用带可见 `RECOVERED_MARKER` 的有界 native recovery 避免答案丢失。状态丢失或损坏时同样优先保留这条可见 fail-open，而不虚构 exact 成功；该普通随机 UUID 路径不属于 exact 契约，因此不承诺永久 exactly-once。任何空 body、非 object 或缺少显式 `ok:true, applied:true` 的 terminal/cron/command 响应都必须 fail-open，不能抑制 Hermes 原生答案。附件/媒体、Cron 与 direct command 仍沿用 Hermes 原生 best-effort/fail-open 契约，不纳入这条 exact Base ledger ACK 保证。

Gateway runtime 以独立 `hfc-runtime-v1` 签名域发送 `runtime.hello` / `runtime.heartbeat`。V4.2 的 payload schema v2 额外携带当前活跃任务计数，用于 `/update` 维护 drain；v1 仍可维持普通 readiness，但不能授权自动维护。sidecar readiness 根据本机 monotonic receipt、generation 和 strict integrity 状态计算；heartbeat 只证明 runtime 活性，不授权写源码。on-disk plan 已是 `installed` 时，等待首次 heartbeat 的 waiting/missing 与 Gateway 正常重启期间的 `runtime_heartbeat_stale` 都只保持 degraded readiness，不触发 repair，也不写 restart/manual-review fence；新 matching hello 可一次恢复 ready。safe repair 仍需 Git/manifest/backup/blob/anchor/fingerprint 证据，成功后只设置 `gateway.restart_required`，不自动重启 Gateway。

飞书私聊裸 `/update` 被确认后，sidecar 在返回 ACK 前创建 durable job，再持久化有 owner 和过期时间的 HFC drain lease，并通过 Hermes 自带 `gateway.drain_control` 写入同一实例的 external drain marker。Gateway 自身停止新 turn、cron 与 API admission；HFC schema v2 heartbeat 从一次 `_active_work_count()` 调用同时产生聚合计数和“计数完整”证据，并验证实际 `HERMES_HOME` 与 checkout marker 目录一致。维护进程只在 external drain 已生效、lease 仍有效、sidecar 与 Gateway 计数都为零且连续两个 heartbeat sequence 前进时继续。随后停止 Gateway、清理 native marker、停止 sidecar、恢复 owned hook并运行官方 updater。确认卡明确授权 updater 在执行时再次 fetch 最新 `origin/main`；若实际 HEAD 不再等于确认时展示的快照，仍先重装 HFC、恢复 hook/服务并验证新 runtime，再以 mismatch 失败终态结束。恢复完成必须看到新的 sidecar PID、新的 runtime identity 与 fresh heartbeat，旧进程或旧 heartbeat 不能冒充成功。

restart/manual-review fence 会原子写入私有 state dir，修复前 runtime 只保存 domain-separated hash；V4.1.1 fence 还以脱敏 hash 绑定 Hermes target/integrity plan，并由跨进程锁与 snapshot CAS 保护。重启 sidecar 不会清除 fence，旧 runtime 的 hello/heartbeat 与新 runtime 的 heartbeat也不能绕过。bound non-empty hash 的 `integrity acknowledge-review` 只清除 manual-review 位，restart fence 与 hash 继续等待不同 runtime id 且 generation/package 匹配的 `runtime.hello`。V4.1.0 unbound empty-hash fence 只在精确已知形态、两次 plan/pidfile/health 检查均稳定时允许显式迁移；其他 unbound fence拒绝。随后必须人工重启 sidecar 与 Gateway，并以新 hello 恢复 ready。

setup/install 通过 Hermes runtime venv 安装，以 `python -I` 验证 package 来自该 venv `site-packages`；若 `/health` 的 package version 或 Python identity 与目标不一致，才受管重启 sidecar。已验证 canonical Hermes root 会显式传入 runner，不能被 selected env 重定向。V4.1.1 detached 子进程在读取配置和监听前先核对父进程写入的精确 PID/token 管理记录；写入失败由子进程自行退出。受管进程以 loopback process-token 请求自停，管理端不向数字 PID/PGID 发 TERM/KILL。具体 non-loopback 地址会配套同地址族的 loopback 管理监听，wildcard 不重复绑定。legacy `0644` pidfile 迁移限定在 owned `0700` state dir 内，并将目录与已打开 fd identity 绑定后收紧权限；pidfile-less、缺少自停接口或自停超时的进程不自动接管/kill，要求人工停止旧服务后重跑。

## 初始卡片可靠投递

sidecar 为 Feishu create/reply 初始卡片生成同一条逻辑投递稳定、不同 bot/route 隔离的 `delivery_uuid`。仅 429、502、503、504、连接异常和超时会在 Feishu API 边界内重试，最多 3 次；不重试 `/events`，PATCH 更新继续沿用原有独立策略。

初始投递对 hook 只暴露三种结果：`delivered` 表示拿到 message id；`not_sent` 表示确定未发送；`unknown` 表示请求可能已被飞书接收但客户端无法确认。异常、日志和 `/health` 不记录 UUID、原始响应正文、chat/message id、URL 或凭据。

已有卡片的 `system.notice` 更新走异步 PATCH。sidecar 在事件已经写入 session 且更新任务已排队时返回 `delivery.outcome=accepted`，表示“已接管并排队”，不伪装成 `delivered`；hook 据此抑制原生灰色提示。独立 notice 的首次 create/reply 仍必须返回 `delivered`、`not_sent` 或 `unknown`。

`/health.metrics` 使用 `feishu_send_retries` 统计额外 Feishu 尝试，`feishu_send_unknown_outcomes` 统计不确定结果，`notice_native_fallbacks` / `notice_uncertain_warnings` 分别统计 hook 被要求执行原文回退和通用提示的次数；`notice_update_failures` 统计 accepted notice 的异步更新任务在内部重试后仍失败的次数。`last_update_error` 只保留异常类型以及经过白名单校验的 `status_code` / `api_code`，不得包含响应正文、token 或凭据。

## 普通消息生命周期

1. `message.started`
   - 创建或定位 session。
   - 发送首张 Feishu/Lark interactive card。
2. `thinking.delta` / `answer.delta`
   - 更新思考或正文。
   - 高频 delta 在 runtime 或 sidecar 层合并。
3. `tool.updated`
   - 更新工具调用 timeline。
   - 尽量附带参数摘要、耗时和失败原因；长详情保持紧凑折叠。
   - Hermes 提供稳定 `call_id` callback 时，以 agent 上实际安装的 start/complete wrapper 为准并抑制 legacy progress path；只有稳定卡片事件未被接受时，显式 fallback 才允许旧路径 fail-open，避免一次调用显示两项。
   - terminal 事件前 flush pending delta。
4. `message.completed`
   - 渲染终态卡片。
   - 标记 session completed。
   - 抑制 Hermes 原生重复答复。
   - 若运行中收到后续消息并使原卡片离开会话底部，原卡片完成更新后以稳定 UUID 发送一条原生完成提醒；普通完成不提醒，提醒失败不回滚卡片终态。

## 工具事件视觉与运行动画

- `message.started` 后、首个模型或工具事件到达前，正文显示“正在加载上下文…”，并保留“思考与工具 · 0 次工具调用”折叠入口。
- 工具事件使用紧凑两级结构：首行由状态图标、工具名和耗时组成；参数、结果或失败原因放在第二行灰色小字中。成功、运行中、失败、取消和等待分别使用语义色，不再使用整块 Markdown 引用背景。
- 初始加载和运行中工具复用该 session 的 `FlushController` PATCH 同一卡片，每 0.8 秒推进一次 spinner，最多 15 次（约 12 秒）；收到可见正文、工具终态或 session 终态后停止。
- 动画更新失败即停止，不重建卡片、不切换 message id，也不绕过既有更新重试、终态 drain 和 topic/reply anchor 规则。

## V4.0.0 实时双轨卡片

V4 将工具执行预览和 Hermes 的公开阶段性输出分别放入 Header 与正文，两条数据流独立更新：

```text
progress_callback.preview
  -> tool.updated.detail
  -> deterministic tool action summary
  -> CardSession.latest_tool_preview
  -> 非完成态 Header subtitle（title 保留用户配置）

_interim_assistant_cb
  -> thinking.delta
  -> CardSession.thinking_text
  -> answer.delta 开始前的正文

message.completed
  -> 普通聊天使用飞书原生 reply Header
  -> 移除重复的 Card JSON Header
  -> 完成态 footer 统计
```

状态规则：

- 运行中：Header title 保留用户配置；subtitle 将 Hermes 最新一条非空工具预览与工具名整理为确定性的动作摘要。正文继续累积公开 `thinking.delta`，完整命令留在 timeline。
- 等待用户：待处理的 `interaction.requested.prompt` 临时覆盖 Header；正文只保留说明和交互控件，不重复问题。每次请求都会把完整当前状态提升为一张新的最新卡片，旧卡保留为此前快照；交互完成后，最新卡恢复此前工具预览。
- 失败：保留失败前最后一条工具预览，正文显示 Hermes 的失败原因。
- 已完成：移除运行时预览；普通聊天只保留飞书原生回复引用作为 Header，不再显示配置标题或在卡片内复制引用。没有有效 reply anchor 的兼容路径仍使用配置标题 fallback。
- 一旦 `answer.delta` 开始，最终回答成为正文主内容，之前的公开阶段性输出不再与答案并排显示。
- 空的工具预览不清除上一条非空预览；Header 文本进入 Card JSON 前会单行化、限制为 120 个字符并脱敏。
- 运行中、等待用户和失败态 footer 只显示状态；时长、模型、token 和 context 数据只在完成态出现。

## 新版 Hermes 首事件兼容

部分 Hermes 版本可能不先发送 `message.started`。如果首个事件是：

- `answer.delta`
- `thinking.delta`
- `tool.updated`
- `message.completed`

sidecar 仍应创建 session 并发送初始卡片，不能把整条流计入 `events_ignored`。

## 上下文压缩运行阶段

新版 Hermes 在 `_status_callback_sync` 里产生 `Compacting context` 状态。patcher 在 Hermes 自身过滤前插入可移除、fail-open 的 hook；`hook_runtime` 只匹配这个固定标记并发出 `system.notice`，其中 `notice_kind=context-compaction`、`phase=started`、`create_session=true`。

- 已有 session：更新同一张卡，Header 临时显示“正在压缩上下文”。
- 压缩是首个可见事件：仅该精确事件可创建 primary card，并保留原 reply/topic 锚点；后续 answer/tool 继续更新同一张卡。
- thinking、answer、tool 或 terminal 到来时立即清除运行阶段；不写入最终答案、footer 或诊断正文。
- 缺少 callback anchor 时 `status_callback=false`，doctor 报 partial compatibility，但其他可验证能力仍可安装。
- 不使用静默 watchdog，不从等待时长猜测压缩，也不展示虚构百分比。

## 卡片文字字号

`card.text_sizes` 只接受 `body`、`reasoning`、`tool`、`notice`、`footer` 五个角色。每个角色可用 scalar，也可使用 `default`、`pc`、`mobile` 映射；映射只为实际渲染的角色生成 `hfc_<role>` alias。

允许值为：`heading-0`、`heading-1`、`heading-2`、`heading-3`、`heading-4`、`heading`、`normal`、`notation`、`xxxx-large`、`xxx-large`、`xx-large`、`x-large`、`large`、`medium`、`small`、`x-small`。平台示例中的 `normal_v2` 是自定义 alias，本项目不接受。未配置时不输出 `config.style`，保持既有 Card JSON；物理 width/height 由 Feishu/Lark 客户端控制。

## Feishu topic / thread 锚点

话题场景里，用户原消息、topic thread 和 Hermes 内部 stream id 可能不是同一个值。

关键规则：

- 首张卡片通常锚定用户 topic message id。
- hook runtime 从真实入站 `event.message_id` 绑定可选 `turn_id`，同一轮后续事件继续携带这个稳定值；`message_id` 仍可表示 Hermes 内部 streaming/reply identity。
- `reply_to_message_id` 只决定飞书回复锚点，不决定 session ownership。
- sidecar 对显式 `turn_id` 启用 canonical turn hard fence：session、sequence、policy 与 native handoff 都使用 `turn_id`，绝不查询 reply alias。
- 旧 hook 缺少 `turn_id` 时继续走兼容路径：sidecar 可用 `reply_to_message_id` 查已有 active card，找到后继续 PATCH 原卡片。
- hook runtime 不把 `source.message_id` 当 canonical turn identity；它只保留 Feishu reply anchor 语义。

这条规则解决：

- 话题右侧面板卡片出现但 timeline 不更新。
- 主会话卡片更新、topic 卡片停住。
- `system.notice` 同时进入卡片 timeline 又在外面出现灰色消息。

## Cron 话题线程投递

从 Feishu/Lark 话题线程创建的 cron job 也必须保留 origin 的 `thread_id`。`build_cron_event` 的目标优先级为：scheduler 已解析的 Feishu target、Feishu origin、显式环境 fallback；没有 thread id 时继续按 `chat_id` 投递。

cron scheduler 必须先完成 `BasePlatformAdapter.extract_media(...)` 与 `media_files` 安全过滤，再让 HFC 接管完成卡。卡片成功且 `native_delivery=required` 时，只清空 `cleaned_delivery_content` 并继续 Hermes 原生附件上传；无媒体时才直接结束。这样正文只在卡片出现一次，真实文件仍沿用 Hermes 的平台上传和 topic 路由。

只有 `origin.platform == feishu` 时才读取 origin thread id。Telegram 等非 Feishu origin 的 thread id 不得进入 Feishu 事件，避免跨平台路由数据泄漏。

## `system.notice`

Hermes 原生运行提示会被归一为 `system.notice`：

- `Working — ...`
- context window / auto-compaction 提示
- automatic session reset
- skill loading
- self-improvement review
- context compression
- background process running / finished output
- background task complete / failed output

处理规则：

1. 如果当前 session 可用，notice 进入辅助 timeline。
2. 如果没有当前 session，发送独立小卡片。
3. `Working` heartbeat 始终是非终态；主 session 缺失时，chat + 原始用户消息锚点 + notice kind 组成稳定的 independent message id。连续 heartbeat 更新同一卡，不同任务锚点保持隔离，最终 `message.completed` 通过 reply-anchor alias 收束该卡。
4. 同一 background process 使用稳定的 `notice_id` 和独立 message id；running 更新与 finished 终态复用同一张卡。exit code `0` 显示成功，非零显示失败，未知 exit code 显示警告。
5. Gateway 启动时会在 recovered watcher drain 前安装 adapter wrapper；contextless / recovered watcher 优先沿用 Hermes `metadata.thread_id`，避免独立通知掉出原 topic。
6. 已有卡片的异步 PATCH 返回 `accepted` 后抑制原生灰色文本；独立卡首次投递的 `delivered` 同样抑制原生文本。`not_sent` 才回退原始通知文本；`unknown` 或不可解析响应只尝试发送 `⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。`，不重复原始通知文本。飞书本身完全不可用时，不保证通用提示最终可见。
7. 只有严格匹配 Hermes 固定 envelope 和 production process/task id 的后台通知才会被接管；未知或不完整文本保持 Hermes 原生路径，避免吞掉普通回复。

## 全 slash command 反馈卡片

`all slash command feedback` 使用统一的命令上下文，不混入正在运行的 Agent 卡片，也不再维护固定命令 allowlist：

1. Feishu/Lark inbound event 只要是 slash command，built-in、alias、plugin/quick 和 unknown-command 提示都建立有期限的 task-local context。
2. Hermes 发出第一条非空文本反馈时 reply 创建 interactive card；同一命令后续反馈串行 PATCH the same card。
3. `/help`、`/commands`、`/debug` 等长反馈按 Markdown 结构拆成多个 element；topic/thread metadata 与原 user message reply anchor 保持不变。
4. create/PATCH 成功才抑制该条原生灰色文本；失败把未修改的 Hermes feedback fail-open 回 original adapter `send`。
5. 手动 `/compress` 运行时包装 original handler：先创建“正在压缩上下文”卡，再以成功、no-op、fallback 或 aborted 原文更新同一卡。

专用交互路径仍优先：

- `/model`、裸 `/resume` 和 `/new`、`/reset`、`/undo` 等 destructive confirmation 继续使用原有按钮、下拉框和同卡结果更新；成功时不会再创建第二张命令卡。
- `/hfc help/status/doctor/monitor` 继续使用 sidecar 运维卡；只有专用路径失败返回文本时才进入统一反馈卡。
- `/learn`、`/blueprint`、`/steer`、`/queue`、`/moa` 等转入 Agent turn 的命令只把即时确认、usage 或错误当作命令反馈；正常 reasoning/answer 仍由普通流式卡承载。
- 飞书私聊裸 `/update` 由 V4.2.0 专用维护确认卡接管，并由独立 runtime 跨重启 PATCH 原卡；群聊、非飞书、别名和带参数更新仍使用 Hermes 原路径。
- 文件、图片、音频等附件继续使用 Hermes 原生媒体发送路径。

### V4.0.9 WebSocket live handler 边界

Hermes 建立 Feishu/Lark WebSocket 时，SDK 持有一个包含消息、卡片、reaction、bot 生命周期等 processor 的 live `EventDispatcherHandler`。HFC startup hook 不得调用 `_build_event_handler()` 重建它，也不得替换 adapter 或 WS client 上的 handler identity。

HFC 只更新现有 `p2.card.action.trigger` processor 的 callback，使 `/new`、`/model`、`/resume` 等命令卡片继续进入运行时包装后的 handler。WebSocket 模式必须通过 `_ws_thread_loop.call_soon_threadsafe(...)` 在 SDK 线程执行；如果当前 Hermes/Lark SDK 内部结构不兼容，则保持 fail-open，不改写 live handler。这样消息、reaction、bot 生命周期、drive、meeting 等其他 processor 和 WebSocket receive loop 始终使用连接建立时的同一 handler 对象。

### 裸 `/resume` 原生选择器

V3.10.0 的 command-card adapter hook 会在运行时包装 runner 的 `_handle_resume_command`，不增加新的 patcher block：

1. 只有无参数、Feishu/Lark、session DB 可用且存在可见命名会话时才发送 `resume_picker` / `select_static` 卡片。
2. 会话先经过 Hermes `_resume_row_visible(...)`；卡片 state 只保存允许的 session id、原事件、original handler 和 5 分钟 expiry。
3. topic metadata 与 reply anchor 原样传给 Feishu send helper。
4. 点击后校验 chat、expiry、允许 id 和操作者。私聊不额外比较操作者；群聊/topic 必须匹配发起者 `open_id`。
5. callback 即时返回“会话恢复中”，后台复制原事件为 `/resume <session_id>` 并调用 original Hermes handler；成功后更新原卡，更新失败最多发送一张 fallback 卡。
6. 任一前置条件或卡片发送失败都 fail-open 到原生 `/resume` 文本流程；带参数 `/resume` 从不进入 picker。

## Agent clarify / approval 交互

Agent 任务内的 `interaction.requested` 会渲染为当前 streaming card 里的按钮。若本轮已经存在卡片，sidecar 会发送一张新的完整当前状态卡并把后续更新切换到新 message id；因此多轮选择始终出现在聊天底部，旧卡不会被追加按钮或再次改写。

HTTP callback 可达时，Feishu/Lark 直接 POST 到 sidecar `/card/actions`。在 WebSocket 长连接或本地/private sidecar 场景中，按钮点击会先到 Hermes Feishu adapter 的原生 card-action channel，再由 hook runtime 接管 `interaction.select` 并转发到 sidecar `/card/actions`。

关键边界：

- sidecar 仍负责校验 `interaction_id` 和 callback token。
- callback payload 带 `open_chat_id` 时，sidecar 还会确认 chat id 与 active session 匹配。
- 新卡使用独立 interaction delivery key；发送失败会恢复请求前 session，原卡仍保持权威，Hermes 可按同一事件安全重试。
- 成功后 sidecar 记录 `interaction.completed`，Hermes hook 轮询 `/interactions/{interaction_id}` 后继续执行。
- sidecar 拒绝、超时或没有返回 card 时，hook 返回空 Feishu callback response，避免崩溃或落入未知原生 handler。

## 群聊边界

群聊准入由 Hermes Gateway 控制，包括 @机器人触发、用户白名单和群消息是否进入 Agent。sidecar 不替代这层判断。

sidecar 负责：

- 根据 `bindings.chats` 选择 bot 或 fallback/default 路由。
- 在群内 `/hfc status` 中提示是否已绑定当前 chat。
- 读取 `bindings.group_rules` 的 enabled/require_mention/计数用于安全诊断，不展示真实 chat/user id。
- 说明群内所有 slash command 先经过 Hermes 准入；built-in、alias、plugin/quick 和 unknown command 的非空文本反馈都进入独立命令卡片。群内 `/update` 仍是 Hermes 原生后台升级命令，V4.2.0 专用维护确认卡仅接管私聊裸命令。

## 运维卡与恢复边界

`/hfc doctor` 可以发出独立运维卡，用于查看诊断、重新检测、两步安全修复和 Gateway 重启确认；它不进入普通 Agent streaming card，也不改变普通卡的 layout 或 footer。

- 私聊 repair/restart 允许后续确认者继续操作，不比较操作者。
- 群聊 repair/restart 只有创建运维卡的发起者可以完成确认；其他操作者会被拒绝并保留重新检测路径。
- command transport 使用 state-dir transport root 自动创建私有 secret；不从 config、env 或卡片 payload 暴露 secret。
- 修复执行前重新校验 recovery plan。已知安全的 manifest/backup 状态可自动 repair；无法验证的用户编辑仍拒绝覆盖。卡片不可用、超时或未投递时，使用 CLI `doctor`、`repair`、`install`、`status` 和 `start/stop` fallback。
- lifecycle cleanup 会回收终态 session、孤立锁和关闭 controller，并保留有界、hash 化的 cleanup history 和 metrics。

profile 路由由 setup 的显式参数、进程环境变量、选定 env file、默认值依次决定；`status`、`doctor` 和 `/health` 只输出脱敏 route-chain/profile diagnostics，用于识别 profile 或 endpoint mismatch。PR #84 / @Zanetach 提供这一 profile env/status routing 基础。

## `/health` 观测指标

排查时先看：

- `events_received`
- `events_applied`
- `events_ignored`
- `feishu_send_successes`
- `feishu_update_successes`
- `feishu_update_failures`
- `last_update_error`
- `last_route_error`
- `reply_index.entries`
- `cleanup_history`
- `cleanup_*` metrics

如果 Feishu UI 出现灰色重复文本，同时 `/health` 显示卡片成功更新，应优先查 hook runtime 的 native fallback suppression。
# Private `/update` maintenance flow

An exact bare `/update` from a verified Feishu private chat is handled as a
maintenance operation, not as an ordinary model turn. The hook submits an
authenticated `/commands` request, the sidecar performs read-only inspection,
and the confirmation card carries an initiator-, chat-, profile-, and
evidence-bound token that expires after 120 seconds.

After confirmation, the sidecar rechecks the evidence, persists a private job
journal, writes the native drain marker only after a heartbeat proves the
running `HERMES_HOME` matches the checkout, and launches the independent
maintenance runtime. One `_active_work_count()` call produces both the
turn/cron/API count and the proof that the aggregate API was available. The
confirmation authorizes the official updater to fetch the latest `origin/main`
at execution time. The runtime waits for active card sessions, restores
HFC-owned hooks, runs only
`hermes update --yes`, reinstalls the cached exact HFC wheel, reapplies hooks,
starts services, and verifies version, import origin, health, and hook state. A
remote advance after confirmation is reported as target mismatch only after
those services have been restored.
Once the sidecar stops, the maintenance process updates the original Feishu
card directly. Group, non-Feishu, alias, and parameterized update commands
remain on Hermes' native path.
