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
   - terminal 事件前 flush pending delta。
4. `message.completed`
   - 渲染终态卡片。
   - 标记 session completed。
   - 抑制 Hermes 原生重复答复。

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

如果 Feishu UI 出现灰色重复文本，同时 `/health` 显示卡片成功更新，应优先查 hook runtime 的 native fallback suppression。

