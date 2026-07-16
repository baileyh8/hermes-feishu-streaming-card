# V4.0.9

V4.0.9 fixes Issue #130: after installing the hook, the Feishu/Lark WebSocket could stop delivering inbound messages and enter a Gateway restart loop after a periodic disconnect.

## Root cause

- When Hermes establishes the WebSocket, the Lark SDK retains a complete `EventDispatcherHandler` containing message, card, reaction, bot-lifecycle, drive, meeting, and other processors.
- The previous HFC startup hook called `_build_event_handler()` after the connection was already live, then directly replaced both `adapter._event_handler` and the live `ws_client._event_handler`.
- This was HFC's only write into the internal state of an already-connected Lark WS client. It changed the handler identity used by the SDK receive loop and matches Issue #130's clean-versus-patched evidence that inbound messages stopped with the hook and recovered after restoring the unpatched file.
- Reconnect exhaustion after a periodic close is a separate upstream Hermes issue, NousResearch/hermes-agent#64712/#64741. V4.0.9 removes HFC's live-handler mutation without taking ownership of Hermes reconnect behavior.

## Fix

- Never rebuild or replace a live `EventDispatcherHandler`.
- Locate only the existing `p2.card.action.trigger` processor and update its callback so HFC slash/model/resume/operations buttons retain inline card responses.
- In WebSocket mode, schedule that callback update on the adapter's own SDK thread through `_ws_thread_loop.call_soon_threadsafe(...)`.
- If a future Lark SDK changes the internal processor shape, HFC fails open and skips callback refresh instead of falling back to whole-handler replacement.

## Credits

- Thanks to @Jasonsun77 for Issue #130's Linux clean-versus-patched A/B, complete 3–6 minute disconnect timeline, exact Python/Lark/websockets versions, healthy-sidecar evidence, and upstream reconnect issue/PR correlation.

## Validation

- A TDD regression proves that the old implementation rebuilt the live handler and that the new implementation preserves the handler identity shared by the adapter and WS client.
- Focused hot-file matrix: `404 passed, 1 skipped`; the skip is the optional Feishu SDK check in the default test environment.
- Exact compatibility smoke passed with Python 3.11.15, `lark-oapi==1.6.8`, and `websockets==15.0.1`.
- Full automated gate: `1330 passed, 4 skipped`, plus `git diff --check`.
- Live Hermes v2026.7.7.2 / Feishu WebSocket: pre-idle, a 420-second idle window, post-idle, and an additional liveness message all completed. Gateway and sidecar PIDs stayed stable; the sidecar applied 10/10 events with 3 successful sends, 7 successful updates, and zero delivery failures.
- The `/model` Provider callback updated the original card into the model list, and Bailey then switched models manually and confirmed success with no callback timeout.
- The sdist and wheel build succeeded; a clean Python 3.12 environment imported `hermes_feishu_card==4.0.9` from the wheel and ran the CLI entry point.
- GitHub Actions now has a dedicated Ubuntu/Python 3.11 exact-SDK job. All four public asset checksums and the public `v4.0.9` tagged-installer fixture smoke passed.

## Release assets

- `hermes-feishu-card-v4.0.9-macos.tar.gz`
- `hermes-feishu-card-v4.0.9-linux.tar.gz`
- `hermes-feishu-card-v4.0.9-windows.zip`
- `hermes-feishu-card-v4.0.9-checksums.txt`
