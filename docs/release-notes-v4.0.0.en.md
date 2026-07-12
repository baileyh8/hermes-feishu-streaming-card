# V4.0.0

V4.0.0 turns the normal Hermes Feishu/Lark card into a more natural live Agent surface while preserving the sidecar-only architecture, existing interaction security, and completed-card layout.

## Live dual-stream cards

- The running Header title keeps the user-configured card name (`Hermes Agent` by default), while the subtitle turns Hermes tool names and `progress_callback.preview`, carried through `tool.updated.detail`, into action summaries such as searching, reading, editing, or running a terminal command.
- Tool gaps retain the last non-empty preview instead of showing plugin-authored placeholder copy.
- The body independently streams public Hermes `thinking.delta` interim output; `answer.delta` remains primary once the answer starts.
- The waiting Header shows the original Hermes interaction prompt while the body keeps its explanation and structured choices.
- Failed cards retain the last tool preview to show where execution stopped.
- Completed normal-chat cards remove the Card JSON Header and use Feishu's native reply quote as their only Header. Compatibility paths without a valid reply anchor retain the configured-title fallback.

## Footer and layout

- Running, waiting, and failed Footers contain status only. Unsettled model, token, duration, and context data is not shown early.
- Completed normal-chat Footers show `已完成` followed by final statistics, without repeating the status in a Card Header.
- Timeline, attachments, button layout, topic anchors, and same-card update order remain compatible.

## `/model` parity with CLI

- Feishu `/model` uses the same Provider/model list as Hermes CLI by consuming Hermes' already-filtered picker data, without reading or inferring local credentials or auxiliary-model configuration.
- The card selects a Provider first and then opens only that Provider's models in the same card. Provider → Model navigation, Back, and Cancel do not switch models.
- Provider/model counts and current markers retain Hermes `total_models` / `is_current`; final selection still calls Hermes' original `on_model_selected`, so the plugin does not rewrite switching or persistence semantics.

## Real Feishu states

| Running | Waiting for user |
|---|---|
| ![Running state with a live tool Header](assets/feishu-v4-runtime-running.png) | ![Waiting state with native interaction buttons](assets/feishu-v4-runtime-waiting.png) |
| Failed | Completed |
| ![Failed state retaining the last tool preview](assets/feishu-v4-runtime-failed.png) | ![Completed state with only the native reply Header](assets/feishu-v4-runtime-completed.png) |

## Compatibility and safety

- The Hermes patch protocol is unchanged. Hermes versions without `preview` retain the established card title and layout.
- `card.interaction_mode: auto` now keeps WebSocket-native buttons on localhost/private sidecars too; explicit `text` still retains numbered-text fallback. This path builds on @colinaaa's [PR #87](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/87) / [issue #86](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/86) contribution, with V4 completing the default-mode wiring and real group-chat regression.
- Action summaries use no LLM and infer no result. URLs keep only host/path, search operators and private paths are reduced, then the text is collapsed to one line, limited to 120 characters, stripped of Markdown fences, and redacted. Full commands remain in the timeline.
- `thinking.delta` carries only public Hermes interim-assistant output; HFC does not hook or reveal hidden reasoning or chain-of-thought.
- HFC does not create a duplicate reply quote or a second `Hermes Agent` Card JSON Header inside completed cards.
- Existing update queues, retries, terminal barriers, group-click ownership, and native gray-message suppression remain authoritative.

## Validation status

- Unit and integration coverage includes preview replacement, empty-preview retention, interaction override/restore, failed retention, completed clearing, redaction, queue coalescing, and delayed-event rejection.
- Real-Feishu group acceptance has verified the live runtime Header, native waiting buttons, interaction callbacks, failed state, and completion with only the native reply Header. Public-package smoke will be recorded before release.

## Release assets

- `hermes-feishu-card-v4.0.0-macos.tar.gz`
- `hermes-feishu-card-v4.0.0-linux.tar.gz`
- `hermes-feishu-card-v4.0.0-windows.zip`
- `hermes-feishu-card-v4.0.0-checksums.txt`
