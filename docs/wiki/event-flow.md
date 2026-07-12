# 事件流和卡片生命周期

## 总览

```text
Hermes Gateway
  -> patched gateway/run.py hook block
  -> hermes_feishu_card.hook_runtime
  -> sidecar /events
  -> CardSession / reply index / route lookup
  -> Feishu/Lark send or update card
```

Hermes 进程内的 hook 只负责提取和转发。sidecar 负责会话状态、卡片渲染、Feishu API、重试、诊断和 metrics。

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
   - terminal 事件前 flush pending delta。
4. `message.completed`
   - 渲染终态卡片。
   - 标记 session completed。
   - 抑制 Hermes 原生重复答复。

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
- 等待用户：待处理的 `interaction.requested.prompt` 临时覆盖 Header；正文只保留说明和交互控件，不重复问题。交互完成后恢复此前工具预览。
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

## Feishu topic / thread 锚点

话题场景里，用户原消息、topic thread 和 Hermes 内部 stream id 可能不是同一个值。

关键规则：

- 首张卡片通常锚定用户 topic message id。
- 后续事件可能只带 Hermes 内部 streaming `message_id`。
- hook runtime 必须保存 Relay `source.message_id`。
- sidecar 创建新 session 前先用 `reply_to_message_id` 查已有 active card。
- 找到后继续 PATCH 原卡片，不新建重复卡片。

这条规则解决：

- 话题右侧面板卡片出现但 timeline 不更新。
- 主会话卡片更新、topic 卡片停住。
- `system.notice` 同时进入卡片 timeline 又在外面出现灰色消息。

## Cron 话题线程投递

从 Feishu/Lark 话题线程创建的 cron job 也必须保留 origin 的 `thread_id`。`build_cron_event` 的目标优先级为：scheduler 已解析的 Feishu target、Feishu origin、显式环境 fallback；没有 thread id 时继续按 `chat_id` 投递。

只有 `origin.platform == feishu` 时才读取 origin thread id。Telegram 等非 Feishu origin 的 thread id 不得进入 Feishu 事件，避免跨平台路由数据泄漏。

## `system.notice`

Hermes 原生运行提示会被归一为 `system.notice`：

- `Working — ...`
- context window / auto-compaction 提示
- automatic session reset
- skill loading
- self-improvement review
- context compression

处理规则：

1. 如果当前 session 可用，notice 进入辅助 timeline。
2. 如果没有当前 session，发送独立小卡片。
3. 已识别 notice 如果卡片投递超时，也不再退回原生灰色文本。
4. 未识别 notice 保持 Hermes 原生路径，避免吞掉重要未知提示。

## 独立 slash command 卡片

独立命令不混入正在运行的 Agent 卡片：

- `/new`
- `/reset`
- `/clear`
- `/undo`
- `/stop`
- `/model`

这些命令在 Feishu/Lark WebSocket 长连接环境中优先走原生 interactive card。按钮或下拉选择通过 Hermes 原 handler 回写结果。

特殊情况：

- `/update` 是 Hermes 后台升级命令，不渲染交互命令卡片。
- sidecar 或 command card 不可用时，允许回到 Hermes 原生文本 fallback。

### 裸 `/resume` 原生选择器

V3.10.0 的 command-card adapter hook 会在运行时包装 runner 的 `_handle_resume_command`，不增加新的 patcher block：

1. 只有无参数、Feishu/Lark、session DB 可用且存在可见命名会话时才发送 `resume_picker` / `select_static` 卡片。
2. 会话先经过 Hermes `_resume_row_visible(...)`；卡片 state 只保存允许的 session id、原事件、original handler 和 5 分钟 expiry。
3. topic metadata 与 reply anchor 原样传给 Feishu send helper。
4. 点击后校验 chat、expiry、允许 id 和操作者。私聊不额外比较操作者；群聊/topic 必须匹配发起者 `open_id`。
5. callback 即时返回“会话恢复中”，后台复制原事件为 `/resume <session_id>` 并调用 original Hermes handler；成功后更新原卡，更新失败最多发送一张 fallback 卡。
6. 任一前置条件或卡片发送失败都 fail-open 到原生 `/resume` 文本流程；带参数 `/resume` 从不进入 picker。

## Agent clarify / approval 交互

Agent 任务内的 `interaction.requested` 会渲染为当前 streaming card 里的按钮。

HTTP callback 可达时，Feishu/Lark 直接 POST 到 sidecar `/card/actions`。在 WebSocket 长连接或本地/private sidecar 场景中，按钮点击会先到 Hermes Feishu adapter 的原生 card-action channel，再由 hook runtime 接管 `interaction.select` 并转发到 sidecar `/card/actions`。

关键边界：

- sidecar 仍负责校验 `interaction_id` 和 callback token。
- callback payload 带 `open_chat_id` 时，sidecar 还会确认 chat id 与 active session 匹配。
- 成功后 sidecar 记录 `interaction.completed`，Hermes hook 轮询 `/interactions/{interaction_id}` 后继续执行。
- sidecar 拒绝、超时或没有返回 card 时，hook 返回空 Feishu callback response，避免崩溃或落入未知原生 handler。

## 群聊边界

群聊准入由 Hermes Gateway 控制，包括 @机器人触发、用户白名单和群消息是否进入 Agent。sidecar 不替代这层判断。

sidecar 负责：

- 根据 `bindings.chats` 选择 bot 或 fallback/default 路由。
- 在群内 `/hfc status` 中提示是否已绑定当前 chat。
- 读取 `bindings.group_rules` 的 enabled/require_mention/计数用于安全诊断，不展示真实 chat/user id。
- 说明群内 `/new`、`/model`、`/reset` 等 slash command 先经过 Hermes 准入，再进入独立命令卡片；`/update` 仍是 Hermes 后台升级命令。

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
