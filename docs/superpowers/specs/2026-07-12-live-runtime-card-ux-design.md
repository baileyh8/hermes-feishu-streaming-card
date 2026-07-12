# V4.0.0 Live Runtime Card UX Design

## Summary

V4.0.0 improves the normal Hermes Feishu streaming-card experience without replacing the established card layout.

While a task is running, the card uses two independent event streams:

1. The Header title keeps the user-configured card name; its subtitle shows a deterministic action summary derived from the latest Hermes tool name and `preview`.
2. While no answer stream is active, the body displays and accumulates Hermes public interim assistant output through `thinking.delta` and `thinking_text`; `answer.delta` remains the primary answer once it begins.

The header replaces its current value rather than building a log. The body keeps its existing streaming behavior. When the task reaches a terminal state, the temporary runtime header is removed or frozen according to the state rules below. The existing footer, reply quote, timeline, interaction controls, and completed-card statistics remain structurally compatible with the current release.

## Product Promise

Users should be able to understand both what Hermes is doing now and what the Agent is communicating, without opening a complex console or receiving additional progress messages.

The experience must:

- feel like one continuously updating Agent card;
- keep tool activity and Agent commentary visually distinct;
- preserve the Hermes-provided target while adding only a deterministic action label such as reading, searching, browsing, editing, or running a terminal command;
- keep the current answer, timeline, interaction, reply quote, and footer behavior familiar;
- degrade to the current card when Hermes does not provide tool previews;
- remain safe and stable across Hermes upgrades.

## Design Principles

- **Two sources, two responsibilities:** tool `preview` belongs in the runtime header; `thinking.delta` belongs in the body.
- **Deterministic adapter, not co-author:** HFC converts tool metadata into a compact action summary without an LLM, result inference, or Agent-body rewriting.
- **Replace the present, retain the process:** the header shows one current preview; the timeline retains complete tool history.
- **No empty-state decoration:** a running card without a usable preview does not add an empty or generic header.
- **Stable geometry:** runtime updates must not create extra messages or repeatedly change the card's overall structure.
- **Terminal familiarity:** normal chat completion uses Feishu's native reply quote as the only completed-state header and does not add a second Card JSON Header.
- **Existing footer contract:** runtime states show status only; completed native-reply cards show `已完成` followed by final statistics.
- **Compatibility first:** the feature reuses the existing event contract and update pipeline instead of expanding the Hermes patch surface.

## Scope

### Included

- A temporary runtime Header subtitle derived from `tool.updated.detail` plus the tool name, while the title keeps the configured card name.
- Independent body streaming driven by `thinking.delta`.
- Explicit running, waiting, failed, and completed presentation rules.
- Safe preview normalization, redaction, and bounded display.
- Compatibility fallback when preview data is absent or unusable.
- Regression coverage for the current footer, reply quote, timeline, and interaction layout.
- Private-chat and group-chat Feishu acceptance checks.

### Excluded

- Generating new progress summaries with an LLM.
- Moving `thinking.delta` out of the body or using it as the runtime header.
- Displaying raw chain-of-thought or hidden model reasoning.
- Translating previews or inferring task results from tool arguments.
- Adding a task console, progress stepper, or synthetic completion percentage.
- Adding plugin-authored retry, continue, or next-step buttons.
- Redesigning the established completed-card layout or configured footer.
- Changing Hermes' tool lifecycle, interaction protocol, or security admission path.

## Event Sources and State

### Tool Preview State

`progress_callback` already supplies `preview` for Hermes tool lifecycle events. The installed hook forwards it as `tool.updated.detail`. HFC combines that preview with the tool name to store the latest user-facing action summary separately from the raw tool timeline.

The runtime-header state contains:

- the latest normalized non-empty action summary;
- the tool id and event sequence that supplied it;
- whether an interaction currently overrides it;
- whether a terminal event has frozen further runtime updates.

The stored preview is a current-display value, not an additional history. Existing tool state and the timeline remain the history source.

### Interim Assistant State

`_interim_assistant_cb` continues to emit `thinking.delta`. `CardSession` continues to normalize and accumulate this public interim-assistant stream in `thinking_text`. While no `answer.delta` content exists, the body displays `thinking_text`; once answer content exists, the answer remains primary. This path does not read, overwrite, or derive content from the runtime-header preview.

The card body retains the current streaming presentation. The implementation must not add labels such as `Hermes Agent`, `阶段性思考`, `preview`, or event names to the real card. Such labels are design annotations only.

## State Transitions

### Running

On `message.started`, the session enters the current running state. No temporary header is rendered until a usable preview exists.

On a non-empty `tool.updated.detail`:

1. classify the tool action and reduce the preview to a safe target;
2. normalize, redact, and replace the current runtime Header subtitle value;
3. update the same card through the existing update queue;
4. independently preserve the tool event in the existing timeline.

An empty preview does not clear an existing value. Between tools, the last usable preview remains visible until another usable preview, an interaction, or a terminal event replaces it.

`thinking.delta` continues to update only the card body. Header and body updates may arrive in any order and must not overwrite each other's state.

The running footer shows only the existing running status. It does not show duration, model, token, or context statistics.

### Waiting for User

On `interaction.requested`, the interaction temporarily owns the header. The header shows the original Hermes interaction prompt without plugin-authored wording.

The body retains Hermes' original explanation and renders only the structured options supplied by the interaction. Existing compact two-column button layout and interaction ownership/security behavior remain unchanged.

The waiting footer shows only the waiting status.

On `interaction.completed`, the session returns to running. The last cached tool preview becomes visible again until a new usable preview arrives. If no tool preview has ever been stored, the card returns to the current headerless running layout.

### Failed

On `message.failed`, the last usable tool preview remains visible as context for the failed operation. The body shows the existing Hermes/HFC error explanation. The plugin does not add retry or recovery actions unless Hermes supplied a structured interaction for them.

The failed footer shows only the failed status. Duration, model, token, and context statistics are not added by this design.

The failed event is a terminal barrier: later delayed `tool.updated` or `thinking.delta` events cannot reopen or alter the runtime state.

### Completed

On `message.completed`, the temporary runtime header is removed. In normal chats, HFC sends the card as a native Feishu reply to the triggering user message, so the native reply quote becomes the only completed-state header:

```text
回复 用户：原始指令
```

HFC does not add a second quote element or a `Hermes Agent` Card JSON Header inside that completed reply card. The final answer, timeline, attachments, divider, and configured footer keep their current order and rendering behavior. The completed native-reply Footer starts with `已完成`, followed by final duration, model, input tokens, output tokens, and context statistics supplied by the completion event. Legacy paths without a valid Feishu reply anchor retain the configured-title fallback.

Completion is a terminal barrier: delayed tool or thinking events cannot restore the runtime header or mutate the completed body.

## Visual and Interaction Rules

- Preserve the current card radius, content spacing, timeline, divider, button layout, and footer structure.
- Keep the configured title and render the runtime summary as one neutral, single-line Header subtitle above the body.
- Do not place status labels, state colors, tool icons, or explanatory labels in the runtime header.
- Keep header height stable across updates.
- Collapse whitespace and truncate overflow to one line using the supported Card JSON behavior.
- Replace the header in place when the preview changes; do not append rows or send separate messages.
- Do not add animation requirements that Feishu Card JSON cannot guarantee.
- Keep waiting buttons compact, left-aligned, and arranged two per row using the existing implementation.
- Do not show design-only captions, event selectors, update-strategy notes, or source labels in the real card.
- Remove the runtime header on successful completion rather than leaving the last tool preview above the final answer.

## Preview Safety

Tool previews may contain commands, paths, query text, URLs, or arguments. Before rendering, HFC applies a display-only summary and safety pipeline:

1. accept only string-like preview content from the existing tool event;
2. classify the tool into a deterministic action label such as `正在读取` or `正在搜索`;
3. reduce URLs to host/path, remove search operators, and reduce private file paths to the target filename;
4. normalize control characters and line breaks into bounded single-line text;
5. remove rendering-only Markdown fences and unsupported formatting;
6. reuse existing secret and identifier redaction rules;
7. enforce a conservative character limit before Card JSON serialization;
8. reject an empty result rather than replacing a safe previous preview.

The summary must not translate content, infer outcomes, or use an LLM. The full safe tool detail remains available through the existing process timeline according to current behavior.

## Compatibility and Failure Handling

- Reuse `tool.updated.detail`; do not add a required event field or new callback argument.
- Reuse the current CardSession, renderer, flush controller, update queue, retry policy, and Feishu delivery identity.
- Hermes versions that emit no preview naturally retain the current headerless running card.
- Unsupported or malformed previews are ignored without affecting tool history, thinking streaming, answer streaming, or completion.
- Duplicate and out-of-order non-terminal events obey the existing sequence rules.
- Terminal events always win over delayed runtime updates.
- A runtime-header rendering error falls back to the established card layout and must not block final delivery.
- A Feishu update failure follows the existing bounded retry and final-delivery behavior; the feature does not create a second card as compensation.
- Interaction prompt override and cached-preview restoration occur in CardSession state, not through a separate mutable global map.

## Implementation Boundaries

The expected change stays within existing ownership boundaries:

- `session.py` owns the latest safe preview, interaction override, and terminal freeze semantics.
- the existing rendering modules decide whether the temporary header is present for the current display state.
- the existing preview/redaction utilities sanitize display text; a focused helper may be added if current utilities do not cover single-line tool previews.
- `server.py` and the current flush/update pipeline remain responsible for card update scheduling.
- `install/patcher.py` should not require a new hook shape because it already forwards `preview` into `tool.updated.detail`.

No installed Hermes file is edited manually.

## Testing Strategy

### Unit Tests

- Store and replace a non-empty preview.
- Ignore empty, malformed, and fully redacted previews.
- Preserve the last preview between tool calls.
- Keep preview state independent from accumulated `thinking_text`.
- Override the header with an interaction prompt and restore the cached preview afterward.
- Render no temporary header before the first usable preview.
- Freeze preview and thinking updates after completed and failed terminal events.
- Preserve the last preview in failed state and remove it in completed state.
- Verify preview normalization, Markdown cleanup, secret redaction, and length bounds.

### Rendering Tests

- Running with preview: temporary header, streaming body, timeline, status-only footer.
- Running without preview: current headerless layout.
- Waiting: interaction prompt header, original body copy, structured buttons, status-only footer.
- Failed: retained preview, error body, status-only footer.
- Completed: native Feishu reply quote as the only header in normal chat, no duplicate Card JSON Header, final answer, timeline, and completed statistics.
- Assert that design-only labels and controls never appear in Card JSON.

### Integration and Compatibility Tests

- Interleave `tool.updated` and `thinking.delta` events and verify independent updates.
- Exercise duplicate, empty, delayed, and out-of-order tool events.
- Confirm interaction override and restoration on the same card delivery.
- Confirm delayed events cannot mutate completed or failed cards.
- Run fixtures with and without `progress_callback.preview` data.
- Verify runtime-header updates use the existing flush/update queue and do not create additional Feishu messages.
- Re-run native gray-message suppression, reply quote, topic/thread, timeline, and interaction regression tests.

### Real Feishu Acceptance

Run the following in both private chat and the configured test group:

1. A multi-tool task with at least two distinct previews and interim assistant updates.
2. A task that pauses for a structured interaction and resumes after a button click.
3. A controlled failed tool invocation.
4. A Hermes version or fixture that supplies no usable preview.

Verify that:

- the header replaces in place and does not flicker or append logs;
- the body continues streaming interim assistant content;
- the last preview remains visible between tools;
- waiting and failed footers contain status only;
- the completed normal-chat card uses the native Feishu reply quote as its only header and shows final statistics;
- no duplicate card, native gray fallback, leaked argument, or unauthorized group interaction appears.

## Acceptance Criteria

The design is complete when all of the following are true:

1. Running cards show the latest safe tool preview in a temporary single-line header when available.
2. Interim assistant content continues to stream in the body independently.
3. Tool gaps preserve the last preview without adding generic placeholder text.
4. Waiting interactions temporarily own the header and restore the cached preview after completion.
5. Failed cards retain the last preview and show status-only footers.
6. Completed normal-chat cards remove the runtime Card JSON Header, use the native Feishu reply quote as their only header, and keep the completed footer; legacy paths without a reply anchor retain the configured-title fallback.
7. Runtime, waiting, and failed cards do not show final statistics.
8. Older or changed Hermes versions without preview data retain the current usable card experience.
9. Preview rendering cannot expose known secret forms or break Card JSON layout.
10. Existing streaming, interaction, topic, reply, timeline, footer, and duplicate-suppression regressions remain green.
