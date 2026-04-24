# Hermes Feishu Streaming Card Sidecar-only Design

## Summary

This refactor rebuilds `hermes-feishu-streaming-card` as a clean-room, sidecar-only Hermes plugin. The current repository implementation is treated as historical reference, not as the runtime foundation. The new mainline removes legacy and dual-mode behavior from the active path so the project can become a stable, installable, open-source plugin for Hermes Gateway on Feishu/Lark.

The primary compatibility baseline is Hermes Agent `v2026.4.23` / `v0.11.0` and newer. The installer must check both version and structure/capability before writing any Hermes files. Versions older than this baseline are not part of the default support target.

## Goals

- Provide reliable streaming card replies in Feishu for Hermes Agent conversations.
- Keep Hermes Gateway stable by limiting changes to a minimal event hook or official extension point.
- Show thinking content progressively while the message is in progress.
- Track tool calls live in the card and show the final tool-call count when complete.
- Replace thinking content with the final answer when the message completes.
- Prevent raw `<think>` and `</think>` tags from ever appearing in Feishu cards.
- Provide automated installation, compatibility checks, backups, restore, uninstall, and diagnostics.
- Ship clear documentation for installation, architecture, event protocol, and recovery.

## Non-goals

- Preserve legacy or dual runtime modes.
- Support Hermes versions older than `v2026.4.23` by default.
- Modify Hermes core logic beyond the minimal event transport needed for this plugin.
- Depend on real Feishu API calls in unit tests or default CI.
- Build the full pip/package release in the first implementation pass.

## Architecture

The runtime has three boundaries:

1. Hermes Gateway keeps the native agent and text streaming flow. The plugin adds the thinnest possible event transport. If Hermes exposes a stable plugin, hook, or transport extension in `v2026.4.23+`, the implementation must prefer it. If not, the installer applies a bounded, hash-checked patch.
2. The sidecar owns all streaming-card behavior: event validation, session state, thinking accumulation, tool tracking, update throttling, Feishu card rendering, Feishu API calls, metrics, and diagnostics.
3. Feishu/Lark receives interactive card messages and progressive message updates from the sidecar.

Gateway hook failures must never block Hermes. When the sidecar is down or unhealthy, Hermes should continue with its native text behavior and the hook should only log debug-level diagnostics.

## Event Protocol

Events are local HTTP JSON payloads sent from Hermes Gateway to the sidecar.

Every event includes:

- `schema_version`: `"1"`
- `event`: one of the event names below
- `conversation_id`: stable conversation or chat identifier
- `message_id`: Hermes message identifier for this assistant turn
- `chat_id`: Feishu chat ID
- `platform`: `"feishu"`
- `sequence`: monotonically increasing integer per message
- `created_at`: Unix timestamp
- `data`: event-specific object

Event names:

- `message.started`: starts a card session and creates the Feishu card.
- `thinking.delta`: carries incremental thinking text.
- `tool.updated`: reports tool status changes and tool metadata.
- `answer.delta`: optional incremental final-answer text when Hermes can distinguish it during streaming.
- `message.completed`: finalizes the card and replaces thinking content with the final answer.
- `message.failed`: marks the card as failed and shows a concise failure message.

The sidecar must use `message_id` plus `sequence` to reject duplicates and ignore out-of-order stale updates. Completion and failure events force an immediate flush.

## Card State Model

The user-facing card has only two normal states:

- `思考中`
- `已完成`

An error state may be displayed when `message.failed` is received, but it is not a separate normal workflow state.

While the card is `思考中`:

- The main card body accumulates thinking text.
- Thinking content is refreshed progressively with sentence/paragraph-aware flushing.
- Tool calls update inside the card without changing the top-level card state.
- The tool area shows live tool-call entries and a running count.

When the card becomes `已完成`:

- The main card body is replaced by the final answer.
- Thinking content is no longer shown.
- The tool area shows the final tool-call count and a concise tool summary.
- The footer shows model, duration, and token statistics when available.

## Streaming Text Rules

The sidecar must normalize text before rendering.

Thinking updates use sentence/paragraph-aware flushing rather than blindly updating on every token. The sidecar accumulates deltas and prefers to flush when one of these conditions is met:

- Chinese or English sentence terminator appears.
- A newline or paragraph boundary appears.
- A tool event arrives.
- A maximum wait threshold is reached.
- A maximum buffered length threshold is reached.
- A completion or failure event arrives.

The normalization module strips `<think>` and `</think>` before any text reaches the renderer. This filtering must be centralized and tested so raw thinking tags cannot leak into Feishu cards.

## Sidecar Modules

The clean-room implementation should use focused modules:

- `hermes_feishu_card/cli.py`: `doctor`, `install`, `start`, `stop`, `status`, `restore`, and `uninstall` commands.
- `hermes_feishu_card/server.py`: local HTTP API and lifecycle.
- `hermes_feishu_card/events.py`: event schema, validation, versioning, and normalization.
- `hermes_feishu_card/session.py`: `CardSession` state machine, idempotency, ordering, and flush decisions.
- `hermes_feishu_card/text.py`: thinking tag stripping and sentence/paragraph-aware buffering.
- `hermes_feishu_card/render.py`: Feishu Card JSON v2 rendering from normalized session state.
- `hermes_feishu_card/feishu_client.py`: tenant token handling, retries, rate-limit handling, card send/update APIs.
- `hermes_feishu_card/install/`: Hermes detection, patch planning, backup manifest, restore, and uninstall.
- `tests/`: unit tests, sidecar integration tests, installer fixture tests, and optional real Feishu smoke tests.

Legacy, dual, and archived code should not be imported by the new runtime.

## Installation And Compatibility

The CLI should expose:

- `doctor`: checks Python version, Hermes path, Hermes version, Hermes structure/capabilities, Feishu credential configuration, port availability, and sidecar health.
- `install`: runs `doctor`, creates backups, installs sidecar files, applies the minimal hook or extension registration, writes a manifest, and verifies the result.
- `start` / `stop` / `status`: manage the sidecar process.
- `restore`: restores files from a manifest-backed backup.
- `uninstall`: removes sidecar files and restores Hermes files if a plugin patch was applied.

Compatibility rules:

- Default support starts at Hermes Agent `v2026.4.23` / `v0.11.0`.
- The installer must reject older Hermes versions unless an explicit advanced compatibility flag is provided.
- Version checks are not enough: the installer must also verify expected extension points or patch anchors.
- If structure/capability checks fail, installation stops before writing to Hermes files.
- The installer must prefer official Hermes plugin/hook/transport extension points over source patching.

Backup and restore rules:

- Before writing, back up every file that may be changed.
- Store a manifest with timestamp, Hermes version, file hashes, patch boundaries, destination paths, and plugin version.
- Reinstalling upgrades only the plugin-owned region and never edits unrelated user or Hermes code.
- Restore uses the manifest instead of fuzzy search deletion.

## Testing Strategy

Unit tests cover:

- Event schema validation.
- Duplicate and out-of-order sequence handling.
- Thinking tag stripping.
- Sentence and paragraph-aware flush decisions.
- Tool-call live count and final count.
- Completion replacing thinking content with final answer.
- Failure rendering and gateway degradation behavior.
- Token and footer formatting.

Integration tests cover:

- Sidecar HTTP API with a fake Feishu client.
- Full session lifecycle: start, thinking, tool updates, optional answer deltas, completion.
- Installer detection, patch planning, backup, restore, and uninstall against Hermes `v2026.4.23` fixtures.
- Compatibility fixture for Hermes upstream `main`.

Optional smoke tests cover:

- Real Feishu card creation and update using explicit user-provided credentials.
- These tests must be opt-in and skipped by default in CI.

## Documentation

The project should publish:

- `README.md`: what the plugin does, supported Hermes versions, requirements, quick install, `doctor`, start/status, recovery, and FAQ.
- `docs/architecture.md`: sidecar-only architecture and Gateway boundary.
- `docs/event-protocol.md`: event schema and examples.
- `docs/installer-safety.md`: compatibility checks, backup, restore, uninstall, and failure modes.
- `docs/testing.md`: local test commands, fixture tests, and optional Feishu smoke tests.

## Release Strategy

Phase 1 ships a GitHub source install with a reliable CLI and test suite.

Phase 2 packages the plugin as a pip package or release archive after the clean-room runtime and installer are stable. Each release must state the tested Hermes versions, starting with `v2026.4.23+`, and update the test fixture matrix when Hermes publishes a new stable release.

## Open Questions Resolved In This Spec

- Runtime mode: sidecar-only.
- Legacy/dual support: removed from active runtime path.
- Card states: only `思考中` and `已完成` for normal operation.
- Tool calls: live updates inside the card with running count; final card shows final count.
- Thinking content: accumulated while thinking, then replaced by final answer on completion.
- Thinking tags: stripped centrally before rendering.
- Compatibility baseline: current stable Hermes `v2026.4.23` / `v0.11.0` and newer.
