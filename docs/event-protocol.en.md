# Event Protocol

[中文](event-protocol.md) | [English](event-protocol.en.md)

The minimal Hermes hook sends message lifecycle events to the sidecar. The hook runtime converts recognizable Hermes message context into `SidecarEvent` JSON and sends it fail-open to the local sidecar `/events` endpoint. The sidecar depends on event semantics, not on Feishu logic inside the Hermes process.

## Events

| Event | Description |
| --- | --- |
| `message.started` | A new message starts; the sidecar creates or initializes a card session. |
| `thinking.delta` | Incremental model thinking content; the sidecar accumulates and displays it while streaming. |
| `tool.updated` | Tool call status changes; the sidecar updates tool call counts and status in the card. |
| `answer.delta` | Incremental final-answer content; the sidecar accumulates answer text until completion. |
| `message.completed` | The message completes successfully; the card switches to `已完成` and final answer content replaces thinking content. |
| `message.failed` | The message fails; the card stops streaming and shows a public failure state or summary. |

## Card States

Normal card states are intentionally simple:

- `思考中` (thinking)
- `已完成` (completed)

During `思考中`, the card shows accumulated `thinking.delta` content and real-time tool call counts. After `message.completed`, the card enters `已完成`, the final answer replaces thinking content, and users no longer need to see the full thinking trace in the completed state.

## Content Safety

The sidecar must filter internal thinking boundaries and must not expose `</think>` or similar control tags. Final answers should come from public response content, not raw internal streams.

The protocol and card behavior are guarded by fake client, fixture Hermes, mock sidecar, and real Feishu smoke coverage.
