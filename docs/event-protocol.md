# 事件协议

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

## 卡片状态

卡片正常状态只有两个：

- `思考中`
- `已完成`

`思考中` 阶段显示累积的 `thinking.delta` 内容，并在同一张卡片内实时更新工具调用次数。`message.completed` 后，卡片进入 `已完成`，最终答案替换思考内容；用户不需要在完成态继续看到完整思考轨迹。

## 内容安全

sidecar 必须过滤模型内部思考边界，不得向卡片泄露 `</think>` 标签或类似控制标记。最终答案应来自对外可见的回答内容，而不是原始内部流。

真实 Feishu CardKit 创建/更新仍属于后续联调范围；当前协议和卡片行为通过 fake client、fixture Hermes 和 mock sidecar 测试守卫。
