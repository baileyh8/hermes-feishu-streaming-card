# Issue #133 Context Compaction 可见性设计

## 1. 根因

Hermes 当前会通过 `agent.status_callback` 发出稳定状态：

```text
🗜️ Compacting context — summarizing earlier conversation so I can continue...
```

Gateway 的 `_prepare_gateway_status_message()` 会在消息平台 adapter 之前把该状态作为 noisy compression chatter 过滤。因此 HFC 现有的 adapter `send` wrapper 和 `system.notice` classifier 看不到 compaction 开始事件。现有代码只识别“auto-compaction threshold raised”和部分 compression 失败提示，不能保证 compaction 过程中主卡存在或更新。

这不是简单的 Feishu PATCH 失败，也不应使用“静默 N 秒”推断 compaction；长工具、慢模型、网络等待都会造成相同静默。

## 2. 目标

1. 精确捕获 Hermes 的 compaction start status，不依赖时间启发式。
2. 已有主卡时在同一张卡的 Header 显示“正在压缩上下文”。
3. 尚无主卡时，以同一 message/reply anchor 创建主卡，后续 delta 继续更新该卡，而不是建立独立重复 notice 卡。
4. compaction 后的第一条 thinking/answer/tool 事件恢复正常 Header；terminal 清理状态。
5. 旧 Hermes 没有兼容 callback anchor 时保持 fail-open，不阻断 Gateway。

## 3. 非目标

- 不显示百分比或虚构进度；Hermes 没有提供可验证进度。
- 不根据无输出时长猜测 compaction。
- 不读取 Gateway 日志、不轮询 state.db、不修改 Hermes compression 算法。
- 不把 compaction summary 或历史正文复制到卡片。

## 4. 方案比较

### 方案 A：静默 watchdog

实现容易，但慢模型、工具等待和限流都会误报，且无法证明任务正在 compaction。拒绝。

### 方案 B：解析 Gateway 日志或 session DB

耦合日志文案和存储实现，跨 Docker/版本脆弱，并引入额外权限。拒绝。

### 方案 C：patcher 接入 `_status_callback_sync`（推荐）

在 Hermes 自己过滤状态之前，将稳定 marker 转换为 HFC 事件。沿用项目现有 AST callback patch、事件序列、sidecar session 和卡片更新路径，改动边界最小且可测试。

## 5. 架构设计

### 5.1 新的 status callback hook

`install/patcher.py` 增加独立 marker：

- `HERMES_FEISHU_CARD_STATUS_PATCH_BEGIN`
- `HERMES_FEISHU_CARD_STATUS_PATCH_END`

目标 callback 为 `_status_callback_sync(event_type, message)`。hook 位于 Hermes `_prepare_gateway_status_message()` 之前，仅把当前 Feishu source、reply anchor、loop、generation guard 和原始 status 文本传给 `hook_runtime.handle_status_from_hermes_locals(...)`。

hook 规则：

- 只处理 Feishu/Lark。
- 只匹配稳定短语 `Compacting context`，大小写不敏感；不匹配一般的 `compression` 单词。
- 不阻止 Hermes 自身 callback 后续逻辑；hook 异常全部 fail-open。
- callback anchor 不存在时不猜位置、不文本替换；记录 unsupported capability，由 install/doctor 明确说明，其他 HFC 能力继续可用。

### 5.2 事件结构

继续复用 `system.notice`，不增加协议 event 名：

```json
{
  "notice_kind": "context-compaction",
  "notice_id": "context-compaction:active",
  "notice_scope": "session",
  "phase": "started",
  "title": "正在压缩上下文",
  "level": "info",
  "content": "正在总结较早的对话，完成后会继续当前任务。",
  "display_status": "in_progress",
  "create_session": true
}
```

`create_session` 只对 HFC 自己精确分类的 `context-compaction/started` 生效；普通 session-scoped notice 仍不能凭空创建主卡。

### 5.3 Session 状态

`CardSession` 新增短生命周期字段 `runtime_phase_text`，避免借用 `latest_tool_preview`：

- compaction start：`runtime_phase_text = "正在压缩上下文"`。
- Header 优先级：pending interaction > runtime phase > latest tool preview > configured title。
- 下一条 `thinking.delta`、`answer.delta` 或 `tool.updated`：清空 runtime phase，再按现有实时 Header 规则渲染。
- `message.completed` / `message.failed`：清空 runtime phase。
- compression aborted/fallback notice：timeline 保留脱敏说明，Header 显示“上下文压缩失败”直到下一事件或终态。

`runtime_phase_text` 不写入完成卡，不进入 footer，不持久化。

### 5.4 无主卡时创建

server 当前会拒绝“无 session 的 session-scoped notice”。新增窄例外：当且仅当事件是 `system.notice`、kind 为 `context-compaction`、phase 为 `started` 且 `create_session is True`，允许按 `SESSION_CREATING_EVENTS` 建立普通 chat session 和主卡。

该卡使用用户消息的 reply anchor、thread id、bot/profile route 和 session key；后续 Hermes delta 必须命中同一 session。不能退化成 independent notice card，否则会出现两张卡。

### 5.5 Sequence 与并发

status hook 复用 `hook_runtime` 现有 per-message sequence 生成器和 ordered POST lock。generation guard 失效时不发送旧 compaction 状态。若 compaction start 与首个 answer delta 竞争，sequence 较新的事件决定最终 Header，旧事件不得覆盖新正文状态。

## 6. 数据流

```text
Hermes agent._emit_status(COMPACTION_STATUS)
  -> Gateway _status_callback_sync
  -> HFC callback hook（在 Hermes noisy filter 之前）
  -> system.notice(context-compaction, phase=started)
  -> existing session: PATCH same card
     no session: create primary card with same reply anchor
  -> Header: 正在压缩上下文
  -> next thinking/answer/tool event clears runtime phase
```

## 7. 错误处理

- status 无法精确分类：忽略，不吞 Hermes 行为。
- event POST 失败：遵守 #135 的结果语义；不得在 callback 线程阻塞 Gateway。
- 无 chat/reply anchor：不创建无归属卡，记录有界 warning。
- route 失败：不建立 session 残留。
- 旧 Hermes anchor 缺失：install 保持其他能力，doctor 报告 compaction visibility unsupported。

## 8. 测试设计

### Patcher

- 精确插入 status callback hook，幂等 apply/remove/repair。
- callback 重命名、缺参数、缺 outer names 时不误插入。
- 旧 fixture 无 callback 时保持可解释兼容。

### Hook runtime

- `Compacting context` 生成正确 session notice。
- 普通 lifecycle/status、provider error、compression 字样不误匹配。
- Feishu topic 保留 reply anchor/thread id。
- stale generation 不发送。

### Session/render/server

- 已有卡：compaction start 更新同卡 Header 和 timeline。
- 无卡：只创建一张主卡，后续 answer delta 更新它。
- interaction prompt 优先级高于 compaction phase。
- 下一条 thinking/tool/answer 清除 compaction Header。
- terminal 卡不残留 compaction 文案。
- topic 与普通私聊各覆盖一次。

### 真实验收

在可触发 compaction 的长会话中记录：飞书消息数量、message id、卡片 PATCH 次数、status 时间线、最终 answer。验收为全过程一张主卡、compaction 期间可见、完成后无额外灰色状态正文。

## 9. 兼容与风险

最大风险是新增 patcher anchor。必须遵守现有 hot-file 规则，不能扩大为重写 Hermes status pipeline；anchor 只包围一个稳定 callback。不同 Hermes 版本没有该 callback 时功能降级但安装不破坏。

## 10. 验收标准

1. compaction 开始后一个 sidecar round trip 内，主卡显示“正在压缩上下文”。
2. 无首卡场景只创建一张可继续流式更新的主卡。
3. 下一运行事件恢复工具/回答 Header，终态无 compaction 残留。
4. 不使用 silence watchdog，不产生新的原生灰色重复消息。
5. patcher 可安装、检测、移除、恢复，旧 Hermes 明确降级。
