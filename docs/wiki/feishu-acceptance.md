# 真实飞书验收清单

自动化测试不能完全证明 Feishu/Lark 客户端体验。涉及卡片 UX、topic、系统提示、命令卡片的版本，发布前需要真实飞书 smoke。

## V4.0.16 加载态去重与真实工具耗时

- 初始加载卡的 Header 只显示 `Hermes Agent`，正文显示动画“正在加载上下文…”，两处不能重复。
- 工具开始后，subtitle 显示当前动作；没有模型正文时，不保留加载占位。
- 工具完成行显示真实耗时；上游显式 duration 优先，兼容兜底只能使用可配对的 started/completed 事件，terminal-only 不得伪造。
- 查询摘要、参数、最终答案、工具时间线结构与 footer 保持既有行为。

2026-07-22 发布候选证据：本机真实 Hermes `progress_callback` 源码确认 `tool.completed` 通过 `kwargs.duration` 提供耗时；按该 callback 结构执行候选 hook/session/render smoke，得到 `✓ web_search · 1.75s`，查询摘要和参数保留，工具开始后无空正文加载占位。renderer/session/server 与全量自动化通过。本补丁不重复宣称飞书客户端视觉复验；公开版安装后的本机 runtime/hook/sidecar 来源另行复核。

## V4.0.15 工具事件视觉与加载动画

- 首个模型/工具事件前，同一张卡显示“正在加载上下文…”及可见 spinner；不能发送额外原生灰色消息或第二张卡。
- 运行中工具的首行显示语义状态、工具名与耗时，次行显示参数摘要；完成、失败、取消、等待的颜色和详情保持可区分。
- spinner 通过同一 card message PATCH 推进；正文、工具终态或消息终态到达即停止，最终只留下单张完成卡。
- topic/reply anchor、工具失败详情、终态 drain 和 create/PATCH 失败回退按既有规则保持。

2026-07-22 验收结果：通过正式 patcher 接入真实 Hermes `v2026.7.20`，使用配置模型 `deepseek-v4-flash` 在飞书中观察到初始“正在加载上下文…”spinner、同卡切换到运行中终端工具、再收束为单张完成卡；候选 sidecar 共处理 10/10 个相关事件，2 次发送、35 次更新全部成功，发送/更新失败和事件拒绝均为 0。Hermes 升级覆盖 hook 的用户侧防护由临时 fixture 完整闭环验证；不把它写成飞书客户端视觉验证。

## V4.0.14 长任务 heartbeat 热修

- 对同一个真实用户消息 reply anchor 连续观察或等价重放 6 分钟、9 分钟 `Working` heartbeat：只能创建一张独立卡，后续为同卡更新。
- heartbeat 卡 Header 保持“运行中”，不能出现“已完成”副标题；最终 `message.completed` 到达后才转为完成态。
- 同一 chat 的两个不同原始消息锚点必须生成不同 independent card identity，不能互相覆盖。
- 受控 unknown delivery 后的下一次 heartbeat 仍回到同一独立生命周期；既有通用警告不得重复原始通知正文。

2026-07-20 发布边界：真实 Feishu 已在公开版 `v4.0.13` 复现两张矛盾状态卡并确认根因；候选修复通过等价 6/9 分钟事件重放、最终完成和 unknown delivery 回归。本次不再次等待真实长任务窗口，不把自动化重放写成客户端视觉复验。

## V4.0.13 全命令反馈卡片

- `/status`、`/usage`、`/commands`、`/reasoning` 与一个 unknown command：每条反馈使用独立命令卡，无灰色原生文本；`/commands` 长 Markdown 不缺段、不破坏代码块。
- 同一命令产生两条以上反馈时只 create 一次，后续更新 same card；topic 内 reply anchor 不掉出原话题。
- 手动 `/compress`：先显示蓝色“正在压缩上下文”，完成后原位显示 Hermes 的 messages/tokens 统计；no-op 和 aborted warning 也更新原卡。
- `/model`、裸 `/resume`、`/new` confirmation：继续使用已有交互卡，选择/确认后原卡更新，不出现第二张结果卡。
- `/learn` 或 `/blueprint`：即时确认使用命令卡，后续 Agent reasoning/answer 使用普通流式卡，不串到命令卡。
- `/update`：重启前反馈使用命令卡；重启后状态允许由独立 `system.notice` 卡继续，不要求跨进程 PATCH 内存中的旧卡。
- 受控让 command card create/PATCH 失败：对应 Hermes 原始反馈必须通过 native fail-open 可见，不能静默丢失。
- 媒体/附件命令继续投递原生文件；文本卡片不能吞掉附件。

## V4.0.12 上下文压缩与字号（发布候选验收）

- 使用官方 installer/patcher 更新真实 Hermes；确认 `doctor` 的 `status_callback=true`，不得手工编辑 `gateway/run.py`。
- 真实长会话触发 Hermes 原生 `Compacting context`：压缩开始时同一张 primary card 的 Header 显示“正在压缩上下文”，后续 answer/tool 清除阶段并继续更新原卡；完成后仅剩一张终态卡，无灰色原生压缩提示。
- 以压缩为首个可见事件时，确认 `create_session=true` 只创建一张卡，私聊与 topic reply anchor 不变。
- 不接受静默 watchdog 或百分比作为验收证据；必须观察真实 callback 和后续事件。
- 桌面端与移动端分别检查 `body`、`reasoning`、`tool`、`notice`、`footer` 的 scalar 和 `pc`/`mobile` 映射，覆盖长中文、代码块、表格、深色模式与 streaming-to-terminal；不得出现 `hfc_*` 可见文本或跨 bot 字号污染。
- 卡片物理 width/height 不在验收范围，由 Feishu/Lark 客户端控制。

2026-07-18 发布候选验收边界：已通过官方 setup/patcher 将候选 runtime 装入真实 Hermes，`doctor` 确认 `status_callback` capability 与 install state 一致；真实 Feishu 字号演示卡完成 create + update，五类 alias 均进入 Card JSON。手动 `/compress` 不经过 `_status_callback_sync`，因此不作为自动压缩 callback 证据；按发布决定不再执行自动压缩长会话 smoke，也不宣称桌面/移动端视觉验收完成。Issue #136 的 selected-env live/noop 分支由真实子进程集成测试覆盖；报告者的 Linux/systemd 环境仍邀请发布后复验。发布后进一步确认 annotated tag `v4.0.12` 指向 `00a48a7`，四个 assets/checksums 与公共 tagged installer 均通过；这些发布验证不扩大上述真实客户端验收结论。

## V4.0.11 system.notice 可靠投递（发布候选验收）

- 私聊触发一条独立 `system.notice`，确认 create 请求携带稳定 `delivery_uuid`，正常路径只有一张卡和 0 条灰色原文。
- topic 内触发 notice，确认 reply 保留原 `reply_to_message_id` / `thread_id`，卡片与任何降级提示都留在 topic。
- 受控测试让前两次 Feishu create/reply 返回 503、第三次成功：三次使用同一 UUID，最终只创建一张卡，`feishu_send_retries=2`。
- 受控永久 400 返回 `not_sent`：只出现一次原始通知文本，`notice_native_fallbacks` 加一。
- 受控 503 耗尽或连接结果不明返回 `unknown`：只尝试一次 `⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。`，不重复原始通知文本，`notice_uncertain_warnings` 加一；飞书完全不可用时不要求该提示必达。
- 检查 `/health`：`feishu_send_retries`、`feishu_send_unknown_outcomes`、`notice_native_fallbacks`、`notice_uncertain_warnings` 与脱敏 `last_send_error` 符合分支，且没有原始 chat/message id、UUID、通知正文、URL、token 或 secret。

2026-07-18 发布验收结果：真实 Hermes `v2026.7.7.2` 通过项目 CLI 重载当前工作树的 sidecar，并使用 Gateway 官方 CLI 重启 Gateway；`doctor` 显示 runtime/import/install state 一致。通过 loopback `/events` 向已认证 Feishu 会话执行私聊 create 与 topic reply，两条事件均返回 `delivered/applied`，sidecar 指标增加 2 次接收、2 次应用和 2 次成功发送，失败、重试及 unknown 均未增加；card-safe diagnostics 未包含验收正文或 `delivery_uuid`。受控 503/400/unknown 与 hook 原生回退分支由自动化集成测试覆盖；本次直接 `/events` smoke 不宣称已完成客户端原生灰字去重视觉验收或真实 Feishu 故障注入。发布后进一步确认 annotated tag 指向 `2a806d3`、四个 assets/checksums 全部通过，公共 `v4.0.11` tag 可安装到独立 Python 3.12 `site-packages`，临时 Hermes fixture 的 hook install state 完整一致。

## V4.0.10 事件传输安全边界

- 使用官方 `install` 把真实 Hermes Gateway venv 升级到 4.0.10；不得手工编辑 `gateway/run.py`。
- `doctor --json` 必须显示 runtime import、install state 与 recovery state 一致。
- 默认 loopback 配置发起一条唯一普通消息，确认 hook 事件能创建、更新并完成一张卡；sidecar 的 `events_rejected`、`event_auth_rejections` 和 Feishu send/update failures 保持为 0。
- 通过 Feishu API 结构检查完成卡只有一张，匹配答案不存在 app text duplicate；不在记录中暴露 chat/message/user id。
- 非回环默认拒绝、显式 opt-in 后强制 event proof、错误/过期/重放 proof 的 401 由自动化矩阵验证；真实 smoke 不临时把生产 sidecar 暴露到非回环地址。

2026-07-17 发布验收结果：真实 Hermes `v2026.7.7.2` 通过官方安装路径将 Gateway venv 从 4.0.9 升级到 4.0.10，`doctor` 的 runtime/import/install/recovery state 一致。已认证 Feishu user 发起唯一 transport smoke，sidecar 收到并应用 3/3 个事件，1 次发送和 2 次更新全部成功，事件/鉴权拒绝与投递失败均为 0；客户端为 1 张完成 interactive card、0 条匹配原生 app text duplicate。发布后进一步验证 annotated tag、Release、四个 assets/checksums 与 public tagged installer fixture；真实 Gateway/sidecar 强制切换到 `v4.0.10@e464316` 的 `site-packages` 后再次完成 3/3 事件 smoke，目标卡完成态 1、运行态 0、原生重复 0。Gateway 与 sidecar 在 smoke 前后保持运行。

## 准备

- 本机 Hermes Gateway 正常运行。
- sidecar 已启动：`python -m hermes_feishu_card.cli status --config ~/.hermes_feishu_card/config.yaml`
- `doctor --explain` 通过，且 `Runtime import` 指向当前版本。
- 飞书 bot 已在目标会话可用。
- 不在仓库、issue 或日志中暴露 App Secret、tenant token、真实 chat id。

## V4.0.9 WebSocket live handler 稳定性

- 使用官方 `restore` / `install` 路径把真实 Hermes Gateway 更新到 V4.0.9，不手工编辑 `gateway/run.py`；`doctor --explain` 必须显示 runtime/import/install state 一致。
- 启动后先发送一条普通消息并确认收到流式完成卡，再保持 WebSocket 空闲超过 Issue #130 报告的 3–6 分钟故障窗口。
- 空闲窗口后再次发送普通消息，确认仍能收到回复；期间 Gateway 不得出现 Lark `ConnectionClosedOK` 后的 restart loop，进程 identity 和 restart count 保持稳定。
- 发送 `/model` 或 `/new` 并完成一次卡片交互，确认 `p2.card.action.trigger` callback 仍可用，且没有 callback timeout 或灰色 fallback。
- 自动化门禁在 Python 3.11 上使用 `lark-oapi==1.6.8`、`websockets==15.0.1`，断言 startup hook 前后 live `EventDispatcherHandler` identity 不变，并且卡片 callback 已通过 `_ws_thread_loop.call_soon_threadsafe(...)` 更新。

2026-07-16 验收结果：真实 Hermes v2026.7.7.2 通过官方 `restore` / `install` 路径加载 V4.0.9，`doctor --explain` 的 runtime/import/install state 一致。私聊先后完成 pre-idle、超过故障窗口的 420 秒空闲、post-idle 与额外 liveness 消息；Gateway/sidecar PID 全程不变，sidecar 10/10 个事件全部应用，3 次发送与 7 次更新成功，发送/更新失败均为 0。`/model` 卡片成功打开，Provider 选择回调原位进入模型列表，随后由 Bailey 手动切换模型并确认成功，没有 callback timeout。完整自动化为 `1330 passed, 4 skipped`，精确 SDK smoke 另行通过；四个发布资产 checksums 和公共 tagged installer fixture smoke 均通过。

## V4.0.8 cron 原生附件投递

- 使用 no-agent 一次性 cron，让脚本返回一段安全测试正文和一个 `MEDIA:` 本地文本文件。
- 验收飞书同时出现一张完成卡和一条独立文件消息；卡片正文不再额外生成灰色原生文本。
- 从飞书下载文件并与源文件逐字节比较；检查 sidecar 的 cron send 成功且 `cron_fallbacks=0`。
- 检查真实 Hermes scheduler 的 HFC cron hook 位于 `media_files` 安全过滤之后，`doctor --explain` 显示 runtime/import/install state 完整一致。

2026-07-16 发布候选验收结果：真实 Hermes 0.18.2 从 V4.0.7 旧 hook 通过官方 restore/install 路径迁移到 V4.0.8；一次性 cron 在测试群产生完成卡和独立文件消息，下载文件与源文件字节一致，sidecar send 成功且无 fallback。Hermes 上游 `cron run` 在成功的一次性任务自动删除后仍会显示已知的 `Ran now: failed` 状态误报，不影响三方证据一致的验收结论。感谢 @zyq2552899783-lgtm 报告 Issue #127。

## V4.0.6 Hermes 0.18.x 完成态与 background 通知

- 私聊 completion：发送一个带工具调用的普通任务，确认卡片从运行态进入完成态，`diagnostics.last_terminal_event` 有记录，且没有重复灰色最终回复。
- 私聊 `/background`：启动一个短后台任务，确认启动确认与 running/final 通知使用卡片；同一任务保持稳定卡片 identity，完成后无额外灰色正文。
- 测试群聊：@bot 执行普通任务和 `/background`，确认完成态、群聊路由和原生重复抑制与私聊一致。
- topic/thread：在测试群的话题回复中执行 `/background`，确认 running/final 更新留在原 thread，不外溢到主群。
- installer：真实 Hermes 0.18.2 runtime 版本、`COMPLETE` / `QUEUED_COMPLETE` marker、`doctor --explain` 均通过；`--accept-hermes-upgrade` 的源码替换路径只在临时 sandbox 验证，不手工编辑真实 `gateway/run.py`。

2026-07-15 发布候选验收结果：runtime/hook 安装、`1315 passed, 3 skipped`、#118 sandbox 与干净 wheel import 通过；私聊 completion、私聊 `/background`、测试群聊 @bot completion、群话题 `/background` 全部通过。background 启动与终态原位更新同一张卡片，终态无“生成中”，没有灰色原生启动/答案；topic 回复留在原 thread，sidecar 发送/更新失败均为 0。

## V4.0.0 实时双轨卡片

使用刻意准备、适合公开展示的任务文案验证以下状态；截图只保留真实飞书卡片区域，不包含群名、头像、真实 chat/open id、无关会话或桌面内容。

- 运行中：Header title 保留用户配置；Hermes 每次发送非空 `progress_callback.preview` 时，subtitle 根据工具名原地更新为动作摘要，不直接暴露完整命令、URL query 或私有路径；正文独立累积公开 `thinking.delta`。
- 等待用户：clarify/approval 问题只在 Header 出现一次，正文保留必要说明和原生按钮/选项；点击后继续 PATCH 同一张卡。群聊必须由任务发起者点击，其他成员点击应被拒绝且不消耗交互。
- 失败：Header 保留最后一条有效工具预览，正文明确显示 Hermes 的失败原因；footer 只有失败状态。
- 已完成：飞书原生回复引用作为唯一 Header 显示用户原指令，Card JSON 不再叠加配置标题；正文只显示最终答案，完成态 footer 才显示时长、模型、token 和 context 数据。没有有效 reply anchor 的兼容路径仍使用配置标题 fallback。
- 回归：工具 timeline、附件顺序、topic 锚点、command/operations 卡片、原 footer 布局和原生灰色消息抑制保持不变。

推荐展示提示词：

```text
请查询广州未来两小时的天气变化，并给我一份简洁的通勤建议。请先核对天气数据，再整理结论。
```

```text
请把广州周末出行建议整理到演示文件中。覆盖现有演示内容前，请先让我确认。
```

```text
请读取演示天气数据并生成摘要；如果数据源不可用，请明确报告失败原因。
```

## V3.10.0 `/resume` 与 footer

- 私聊：发送裸 `/resume`，确认只出现一张原生下拉卡、当前会话有标记；选择其他会话后先显示恢复中，再在原卡显示结果，无灰色文本列表和 callback timeout。
- 当前会话：再次打开 picker 并选择当前项，确认 original Hermes handler 返回 already-on 结果，不执行第二套 switch 逻辑。
- 过期/无效：使用过期 state 或无效 option，确认原卡显示失效提示且不切换会话。
- 群聊：发起者可选择；另一位用户点击被拒绝且 state 保留，随后发起者仍可完成。
- topic：picker 与结果留在原 topic；reply anchor/thread metadata 不丢失。
- fallback：session DB 空、adapter/card 不可用、群聊发起者 `open_id` 无法验证时，Hermes 原生文本列表仍可用。
- footer：常见模型名只有文本颜色变化；divider、字段顺序、分隔符、字号和普通卡 footer/layout 不变，未知/特殊字符模型名无 markup 注入。

## V3.9.1 可靠性热修

- 完成答案：构造 completed event 带较长 suffix 的任务，确认卡片保留完整正文且没有灰色重复 reply。
- 打断任务：旧任务仍在更新时发起新任务，确认旧卡收束为中断终态，迟到更新不再恢复运行态。
- 模型选择：打开 `/model`，选择模型后 callback 不出现超时 toast；先显示切换中，随后原卡显示成功或失败终态，不额外重复发送结果卡。
- 安装恢复：在临时 Hermes sandbox 验证 marker-only 安全恢复；不要在真实运行目录手工编辑 `gateway/run.py`。
- 回归：普通流式卡 footer/layout 不变。

## V3.9.0 运维卡（部分通过）

以下项目必须在真实飞书完成后才可标记通过；当前自动化证据不替代这些 smoke。

- 私聊：`/hfc doctor` 打开运维卡，执行重新检测、两步安全修复、重启确认；确认普通流式卡 footer/layout 快照不变。
- 群聊（group）：发起者能够完成 repair/restart；第二位操作者确认时被拒绝；再次由发起者确认后完成，并检查没有泄漏 chat id、token 或 transport secret。
- topic：在话题内打开运维卡后，普通 topic 流仍更新原卡，运维卡不改写普通 footer/layout。
- cron：cron 投递和普通定时完成卡不被运维操作阻断。
- profile route mismatch：以 main/child profile 或错误 `HERMES_FEISHU_CARD_PROFILE_ID` / endpoint 配置复现 mismatch，确认 `status`/`doctor` 仅显示脱敏 route chain，并修正后恢复。

2026-07-11 已通过的私聊基线：

- `/hfc doctor` 只生成一张运维卡，没有灰色原生未知命令。
- 中文诊断摘要与详情可见，footer 保持不变。
- 连续两次重新检测均快速返回，后台 successor 按钮仍可点击，最终 PATCH 同一张卡片。
- 本轮回调可靠性复测中，“查看诊断”和连续两次“重新检测”均在 156–201 ms 内 ACK；没有新增“目标回调服务超时未响应”，过渡态与终态继续 PATCH 原卡。
- 临时 Hermes sandbox 中两步安全修复成功；卡片实际重启 Gateway，先显示进行态，随后同卡显示完成态。
- 普通流式卡从生成中到完成态保持一张卡，完成 footer/layout 不变，没有灰色重复答案。
- 本轮 sidecar 发送与更新均成功，Gateway 日志没有新的 operations forward timeout。
- no-agent 一次性 cron 的结果正文进入普通完成卡；sidecar 的 event receive/apply/card-send 指标均成功且没有 native fallback。
- Hermes 上游 `cron run` 会在成功的一次性任务自动删除后再次读取 `last_status`，因此本次终端显示 `Ran now: failed`。这属于上游 CLI 状态误报；以飞书卡片、sidecar metrics 和保存的 cron 输出三方一致判定 cron 卡片验收通过。
- 临时设置错误 `HERMES_FEISHU_CARD_PROFILE_ID` 后，`doctor --explain` 显示 `profile_unknown` 与缺失 route，不暴露 chat id、token 或 secret；移除临时环境后恢复默认 profile，持久配置未变。

仍待真实验收：群聊发起者与换操作者拒绝、topic。existing-container Docker 见 release-readiness 单独门禁。

真实验收状态：**部分通过**。

## 普通会话

提示词：

```text
查一下广州明天天气
```

验收：

- 首张卡片出现。
- 正文持续更新。
- 工具调用进入“思考与工具”。
- 完成后只有一张最终卡片，没有额外灰色最终答案。

## Feishu topic / thread

在飞书会话中创建或打开话题，在话题回复框里发送：

```text
请验证当前 Hermes 飞书卡片插件是否已经支持话题内卡片连续更新。不要直接回答，先说明你会从哪些证据判断；然后依次检查本地版本、CHANGELOG、测试用例和运行状态；每次工具调用前先给一句阶段性判断。
```

验收：

- 右侧话题面板中出现卡片。
- 后续工具和答案持续更新同一张卡片。
- `思考与工具` 折叠区可展开，并显示工具 timeline。
- 完成态仍在话题面板内。
- 没有重复外溢的灰色 `system.notice`。

## 系统提示 suppression

提示词：

```text
V3.8.9 notice suppress smoke: please run terminal command date, then reply exactly topic smoke ok
```

验收：

- 卡片完成并回复 `topic smoke ok`。
- 如果触发 `Codex gpt-5.5 caps context...` 等上下文提示，不应额外出现在卡片外灰色消息里。
- Gateway 日志允许出现 `system notice native fallback suppressed`，表示已识别并抑制原生 fallback。

## Slash command cards

发送：

```text
/new
```

验收：

- 出现独立确认卡片。
- 点击“允许一次”或“始终允许”后有状态反馈。
- 允许后的 reset 结果以卡片反馈，不退回灰色文本。

发送：

```text
/model
```

验收：

- 出现模型选择卡片，第一层 Provider 数量、名称和当前项与本机 Hermes CLI `/model` 一致。
- 进入 DeepSeek 等已知 Provider，确认第二层模型数量和模型名称与 CLI 一致，不出现其他 Provider 的模型。
- 点击“返回”后恢复 Provider 列表；再次进入 Provider 后选择模型。
- 结果卡片显示模型已更新。
- 再问“现在是什么模型”，模型应与选择一致。
- 全程没有 callback timeout toast，也没有灰色重复消息。

## 长内容和 Markdown

提示词：

```text
生成一个包含 20 行、4 列的 Markdown 表格，并在后面附一个 80 行 Python 代码块。要求保持表格和代码块结构完整。
```

验收：

- 长表格没有被飞书渲染成 raw markdown。
- code fence 完整，没有半截围栏。
- 卡片完成后没有重复灰色全文。

## 记录方式

验收完成后可记录到 release notes 或 issue comment：

```text
真实飞书验收：
- 普通会话：通过
- 话题回复：通过
- system.notice suppression：通过
- /new：通过
- /model：通过
- 长 Markdown：通过
```

截图入库前需要遮挡私人头像、姓名、chat id、群名和不适合公开的上下文。
