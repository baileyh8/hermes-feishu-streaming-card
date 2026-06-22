# 事件协议

[中文](event-protocol.md) | [English](event-protocol.en.md)

Hermes 最小 hook 向 sidecar 发送消息生命周期事件。第二阶段 hook runtime 会把可识别的 Hermes 消息上下文组装为 `SidecarEvent` JSON，并 fail-open 发送到本机 sidecar `/events`。sidecar 只依赖事件语义，不把飞书卡片逻辑放回 Hermes 进程。

## 事件列表

| 事件 | 说明 |
| --- | --- |
| `message.started` | 新消息开始，sidecar 创建或初始化会话卡片。 |
| `thinking.delta` | 模型思考内容增量。sidecar 累积显示思考内容。 |
| `tool.updated` | 工具调用状态变化。sidecar 在卡片内实时显示工具调用次数。 |
| `answer.delta` | 最终答案增量。sidecar 累积答案内容，等待完成事件落最终态。 |
| `message.completed` | 消息正常完成。卡片状态切换为 `已完成`，最终答案覆盖思考内容。 |
| `message.failed` | 消息失败。卡片结束当前流式更新，并展示可公开的失败状态或摘要。 |
| `interaction.requested` | Hermes 需要用户授权或选择时发出。sidecar 在同一张卡片中渲染按钮或编号文本选项，并在 `/interactions/{interaction_id}` 暴露等待状态。响应会带 `interaction_mode`，`text` 模式下 hook 会立即交还 Hermes 原生文本交互流程。 |
| `interaction.completed` | 用户点击卡片按钮后发出。sidecar 更新原卡片为已选择状态，并让 Hermes hook 轮询到选择结果后继续执行。 |
| `interaction.failed` | 交互请求失败或超时。sidecar 保留失败状态，Hermes hook 可 fail-open 回到原生 Hermes 交互路径。 |

## 路由字段

所有事件都保留 `conversation_id`、`message_id` 和 `chat_id` 三个必填字段。V3.6.4 起，事件还可以携带可选 `thread_id` 字段；当它是飞书 `om_` / `omt_` thread 上下文时，sidecar 会在创建初始卡片时使用飞书 reply API，把卡片发回用户所在的同一 thread。后续更新仍然 PATCH 这条已创建的卡片消息。

## 卡片状态

卡片正常状态只有两个：

- `思考中`
- `等待选择`
- `已完成`

`思考中` 阶段显示累积的 `thinking.delta` 内容，并在同一张卡片内实时更新工具调用次数。`interaction.requested` 到达后，卡片进入 `等待选择`。公开 callback 模式下按钮点击会触发 sidecar `/card/actions`，更新原卡片并写入选择结果；localhost/private sidecar 的 text fallback 会显示编号选项，并让 Hermes 原生文本交互接管后续选择。`message.completed` 后，卡片进入 `已完成`，最终答案替换思考内容；用户不需要在完成态继续看到完整思考轨迹。

## 内容安全

sidecar 必须过滤模型内部思考边界，不得向卡片泄露 `</think>` 标签或类似控制标记。最终答案应来自对外可见的回答内容，而不是原始内部流。

当前协议和卡片行为通过 fake client、fixture Hermes、mock sidecar、Feishu callback 模拟和真实 Feishu smoke 测试守卫。
