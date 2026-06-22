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
| `interaction.requested` | Hermes needs user approval or a choice. The sidecar renders buttons or numbered text choices in the same card and exposes pending state through `/interactions/{interaction_id}`. Responses include `interaction_mode`; in `text` mode the hook immediately falls back to Hermes' native text interaction path. |
| `interaction.completed` | A card button was clicked. The sidecar updates the original card with the selected option and lets the Hermes hook poll the result to continue. |
| `interaction.failed` | The interaction failed or timed out. The sidecar preserves the failed state and the Hermes hook can fail open to native Hermes behavior. |

## Routing Fields

All events keep the required `conversation_id`, `message_id`, and `chat_id` fields. From V3.6.4, events may also carry an optional `thread_id`; when it represents a Feishu `om_` / `omt_` thread context, the sidecar uses the Feishu reply API to create the initial card in the same thread where the user sent the message. Later updates still PATCH the created card message.

## Card States

Normal card states are intentionally simple:

- `思考中` (thinking)
- `等待选择` (waiting for choice)
- `已完成` (completed)

During `思考中`, the card shows accumulated `thinking.delta` content and real-time tool call counts. When `interaction.requested` arrives, the card enters `等待选择`. In public callback mode, button clicks hit the sidecar `/card/actions` route, update the original card, and store the selected result; localhost/private sidecar text fallback shows numbered choices and lets Hermes' native text interaction path take over. After `message.completed`, the card enters `已完成`, the final answer replaces thinking content, and users no longer need to see the full thinking trace in the completed state.

## Content Safety

The sidecar must filter internal thinking boundaries and must not expose `</think>` or similar control tags. Final answers should come from public response content, not raw internal streams.

The protocol and card behavior are guarded by fake client, fixture Hermes, mock sidecar, Feishu callback simulation, and real Feishu smoke coverage.
