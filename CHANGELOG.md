# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.2.0.html).

## Unreleased

### V3.9.0

See also: [docs/release-notes-v3.9.0.md](docs/release-notes-v3.9.0.md)

### Added
- Added the operations and reliability foundation: Feishu/Lark operations cards guide diagnosis, two-step safe repair, recheck, and Gateway restart while retaining CLI fallback when operations cards are unavailable.
- Operations cards preserve ownership boundaries: private chats do not compare operators; group cards require the initiating operator for repair/restart confirmation. Transport authentication uses a zero-configuration secret rooted in the private sidecar state directory.
- Added profile-aware setup, environment diagnostics, lifecycle cleanup metrics, automatic known-safe repair (with `--no-repair` opt-out), and Hermes/Docker compatibility coverage. `doctor` shows the full redacted identity/profile/event-endpoint route chain; `status` summarizes runtime routing/profile events and `/health` reports routing health.

### Credits
- PR #84 by @Zanetach contributed card progress-status routing and `.env` allowlist expansion for profile environment support.

### Validation
- Automated release gate: `1156 passed, 3 skipped`.
- Real Feishu private-chat baseline passed on 2026-07-11: `/hfc doctor` produced one operations card without a gray native unknown-command reply; details, two consecutive rechecks (including a background successor), same-card updates, and the normal streaming-card footer all passed with zero send/update failures.
- Existing-container Docker smoke plus real Feishu repair/restart, group ownership, topic, cron, and profile-mismatch smoke remain pending acceptance.

## V3.8.18 — 2026-07-10

See also: [docs/release-notes-v3.8.18.md](docs/release-notes-v3.8.18.md)

### Fixed
- Fixed issue #90, contributed by @colinaaa in PR #91: cron cards created from Feishu topic-group threads now preserve `thread_id` and post back into the originating thread instead of creating a new topic.
- Cron thread routing now prefers scheduler-resolved Feishu targets, then Feishu origins, then the explicit environment fallback; thread ids from non-Feishu origins are ignored.

### Tests
- Added unit coverage for cron thread-id source priority, empty values, environment fallback, legacy id formats, and cross-platform isolation.
- Added integration coverage proving cron cards with a Feishu `thread_id` reach the target thread while ordinary cron cards still target the chat.

## V3.8.17 — 2026-07-09

See also: [docs/release-notes-v3.8.17.md](docs/release-notes-v3.8.17.md)

### Fixed
- Fixed cron Feishu/Lark card delivery for routing-intent `deliver` values such as `origin`, `all`, and `origin,all`, contributed by @zayn-0101 in PR #77.
- Cron completions now use scheduler-resolved targets or Feishu origins before falling back to explicit delivery settings, so routing intents no longer short-circuit the platform check into plain-text delivery.
- `deliver=local` remains local-only/no-delivery, and dict-shaped `deliver` configs continue to support explicit `platform` / `chat_id` values.
- The installed cron hook pre-resolves delivery targets only when the Hermes scheduler exposes `_resolve_delivery_targets`, keeping the hook fail-open across Hermes versions.

### Tests
- Added cron coverage for `deliver=origin`, `deliver=all`, `origin,all`, `origin,feishu:...`, dict `deliver`, non-Feishu origins, and `deliver=local`.
- Updated patcher coverage for optional cron target pre-resolution in the installed hook block.

## V3.8.16 — 2026-07-09

See also: [docs/release-notes-v3.8.16.md](docs/release-notes-v3.8.16.md)

### Fixed
- Fixed issue #89, contributed by @colinaaa in PR #88: Feishu/Lark topic groups that reuse the same `message_id` across consecutive turns now send a fresh card for the second and later messages.
- Completed or failed sessions with a reused topic `message_id` now discard stale per-key delivery state before creating the new card, so clarify/approval turns do not hang without an interaction card.
- Duplicate `message.started` events while the current turn is still active remain ignored, preventing spurious extra cards.

### Tests
- Added integration coverage for reused completed topic `message_id` values creating a new card.
- Added a guard proving active duplicate `message.started` events still do not send a second card.

## V3.8.15 — 2026-07-09

See also: [docs/release-notes-v3.8.15.md](docs/release-notes-v3.8.15.md)

### Fixed
- Fixed issue #82 follow-up recurrence where a completed card with an input `.docx` / `files` context could still be followed by a duplicate native Feishu/Lark final reply.
- Structured `files` / `file` locals now remain card attachment summaries only; they no longer force `native_delivery=required` unless the final answer itself references an output path.
- Real output delivery remains fail-open for explicit `MEDIA:/tmp/...`, local file paths in the final answer, and structured output media fields such as `media_files`, `image_files`, `audio_files`, and `video_files`.

### Tests
- Added regression coverage for card-only input file context while keeping explicit media/file output paths on native delivery.

## V3.8.14 — 2026-07-09

See also: [docs/release-notes-v3.8.14.md](docs/release-notes-v3.8.14.md)

### Added
- Added WebSocket-native handling for agent clarify/approval `interaction.select` card-action clicks, contributed by @colinaaa in PR #87 and closing issue #86.
- Feishu/Lark WebSocket deployments can now keep agent interaction choices in card buttons by forwarding native card actions to the sidecar `/card/actions` endpoint without requiring a public callback URL.

### Fixed
- Rejected or expired WebSocket interaction clicks now return an empty Feishu callback response instead of crashing or falling through to the original adapter handler.

### Tests
- Added hook runtime regression coverage for successful `interaction.select` forwarding, incomplete action guards, and sidecar rejection behavior.

## V3.8.13 — 2026-07-08

See also: [docs/release-notes-v3.8.13.md](docs/release-notes-v3.8.13.md)

### Fixed
- Fixed Hermes upgrade compatibility for `v2026.7.7.2` / `0.18.2`, where the installer could reject a valid Gateway only because the upstream Git tag used four numeric components.
- Version detection now extracts numeric tokens from descriptive metadata such as `Hermes Agent v0.18.2 (...)` and falls back to verified `gateway/run.py` anchors when readable version metadata is unparseable.
- Reinstall and repair now handle stale install state left by a Hermes upgrade that replaced `gateway/run.py` with an unpatched upstream file, allowing the hook to be safely installed again without restoring an old Hermes file.

### Tests
- Added regression coverage for four-component Hermes tags, descriptive version metadata, unparseable-version anchor fallback, and stale unpatched install-state repair/reinstall paths.

## V3.8.12 — 2026-07-08

See also: [docs/release-notes-v3.8.12.md](docs/release-notes-v3.8.12.md)

### Fixed
- Fixed issue #82 recurrence where completed cards with attachment summaries such as `colors.csv` / `styles.csv` could still be followed by a duplicate native Feishu/Lark reply containing the full final answer.
- Completed events now distinguish card attachment summaries from native file/media delivery requirements via `native_delivery`, so generic `attachments` stay card-only after successful Feishu delivery.
- Native Hermes file/media paths remain fail-open: `MEDIA:/tmp/...`, local file paths, `files`, `media_files`, and image/audio/video file locals still allow Hermes' native attachment delivery path instead of being swallowed by card suppression.

### Tests
- Added regression coverage for generic attachment summaries suppressing the native Feishu final reply.
- Added coverage proving real media/file delivery paths still bypass native response suppression.
- Updated patcher and integration coverage for the new `native_delivery` completion guard.

## V3.8.11 — 2026-07-08

See also: [docs/release-notes-v3.8.11.md](docs/release-notes-v3.8.11.md)

### Fixed
- Fixed `/hfc` diagnostics in real Feishu/Lark Gateway flows where `/hfc status` could render the Hermes Agent card and still fall through to Feishu's gray native `Unknown command /hfc` reply when card delivery took longer than the Gateway hook timeout.
- `/commands` now ACKs accepted `/hfc` requests before slow Feishu card delivery finishes, then sends the command card in the background with failure logging.
- The Gateway patch intercepts accepted `/hfc` commands before Hermes' native slash-command fallback, and the hook runtime reads command text from `event.text` / `event.content` when Gateway metadata does not expose the command helper.

### Tests
- Added regression coverage for slow Feishu command-card delivery proving `/commands` returns before the send completes.
- Added hook runtime and patcher coverage for real Gateway event text extraction and early `/hfc` slash-command interception.

## V3.8.10 — 2026-07-07

See also: [docs/release-notes-v3.8.10.md](docs/release-notes-v3.8.10.md)

### Added
- Added safe `bindings.group_rules` diagnostics for group chats. Hermes Gateway still owns real @bot and allowlist admission; the sidecar reports the configured counts, mention policy, binding state, and routing reason without leaking raw chat/user ids.
- Added group-aware `/hfc status` guidance. In an unbound group it now explains that the chat is using fallback/default routing, prints the suggested `bots bind-chat ...` command, and documents that `/new`, `/model`, `/reset`, and similar slash commands first pass Hermes group admission before rendering command cards.
- Tool timeline details now include compact argument summaries, duration, and failure reason when Hermes exposes those values in `tool.updated` locals.

### Fixed
- issue #79: `install.sh` and `install-docker.sh` now suppress pip's root-user warning by default and keep recoverable `externally-managed-environment` output from looking like a fatal install failure.
- Docker installs now retry PEP 668 externally managed Python environments with `--break-system-packages`, matching the macOS/Linux installer behavior.

### Tests
- Added installer regression coverage for pip root-user warning suppression and Debian/Ubuntu externally managed Python retry output.
- Added regression coverage for tool detail extraction/rendering, hook-runtime tool metadata extraction, safe group diagnostics, route metadata, and group `/hfc status` binding guidance.

## V3.8.9 — 2026-07-04

See also: [docs/release-notes-v3.8.9.md](docs/release-notes-v3.8.9.md)

### Fixed
- Fixed Feishu/Lark topic replies where the initial card appeared but later `answer.delta`, `thinking.delta`, `tool.updated`, or `system.notice` events could fail to update the same card when Hermes used a different streaming `message_id`.
- Session-scoped native Hermes notices in topics now resolve back to the active card by `reply_to_message_id`, so accepted notices return `applied: true` and do not fall through to duplicate gray native messages.
- Recognized Hermes system notices no longer fall back to native gray Feishu/Lark text when card delivery times out. This suppresses the duplicate external notice while the active topic card continues to own the run state.
- Hook runtime stream events now preserve the original Feishu reply anchor from Relay `source.message_id`, allowing topic updates to stay associated with the triggering user message even when Hermes' internal stream id changes.

### Tests
- Added Feishu topic regression coverage for stream/tool updates and `system.notice` updates that use a different event `message_id` but the same `reply_to_message_id`.
- Added hook runtime coverage for topic stream events carrying `reply_to_message_id` from Relay source metadata.
- Added a timeout regression for native Feishu adapter `send()` proving classified system notices are suppressed instead of being resent as gray text when the card attempt misses its deadline.

## V3.8.8 — 2026-07-03

See also: [docs/release-notes-v3.8.8.md](docs/release-notes-v3.8.8.md)

### Added
- Added `system.notice` event support for native Hermes runtime/status notices that previously appeared as separate gray Feishu/Lark text messages.
- Added card/timeline rendering for session-scoped notices and compact standalone notice cards for task-external notices.
- Added runtime classification for covered Hermes notices: `Working` heartbeats, context-window/auto-compaction notices, automatic session reset notices, skill-loading notices, self-improvement review notices, and context-compression notices.

### Fixed
- Long-running heartbeat notices now update the same timeline entry via `notice_id` instead of appending repeated entries.
- Native Feishu adapter `send()` and `edit_message()` wrappers now try notice card delivery first and fall back to Hermes native text/edit paths if the sidecar is unavailable or the notice is not recognized.
- Fixed an empty slash-command parsing edge case in the Feishu adapter patch path so normal Feishu messages with `get_command() == ""` do not trip command-card installation.

### Tests
- Added unit and integration coverage for `system.notice` schema parsing, session timeline updates, independent notice cards, compact notice rendering, sidecar card creation, Feishu adapter send interception, independent fallback, and heartbeat edit updates.

## V3.8.7 — 2026-07-02

See also: [docs/release-notes-v3.8.7.md](docs/release-notes-v3.8.7.md)

### Fixed
- Fixed issue #75 for newer Hermes event streams that can start with `answer.delta`, `thinking.delta`, `tool.updated`, or `message.completed` without a prior `message.started`. The sidecar now creates the card session and sends the initial Feishu/Lark card from those first events instead of ignoring the whole stream.
- Preserved the existing cron completion behavior while sharing the same first-event session creation path, including card summary and terminal diagnostics.

### Tests
- Added regression coverage for missing-`message.started` first events across answer delta, thinking delta, tool update, and completed answer cases.

## V3.8.6 — 2026-07-02

See also: [docs/release-notes-v3.8.6.md](docs/release-notes-v3.8.6.md)

### Fixed
- Fixed issue #70 Docker/source-stripped installs where Hermes has `gateway/run.py` but no top-level `VERSION` file and no local `.git` tag metadata. `doctor --explain`, `install`, and `setup` now fall back to verified Gateway code anchors instead of failing with `Hermes VERSION missing, unknown, or invalid`.
- When the fallback is used, diagnostics now report `version_source: gateway anchors`, `version: unknown`, and the inferred `hook_strategy` (`gateway_run_013_plus` for modern Hermes anchors, `legacy_gateway_run` for legacy anchors).
- Added Hermes v0.18.0 / `v2026.7.1` compatibility coverage; it stays on `gateway_run_013_plus`.

### Changed
- Docker examples now default to `HFC_VERSION=v3.8.6`.
- README showcase image now uses the combined horizontal real-UI card collage for command cards, command result feedback, and the answer/tool timeline.

### Tests
- Added regression coverage for missing-`VERSION` Hermes roots with legacy and modern Gateway anchors, explicit invalid VERSION rejection, and parent-git-tag isolation while still accepting verified anchors.

## V3.8.5 — 2026-07-02

See also: [docs/release-notes-v3.8.5.md](docs/release-notes-v3.8.5.md)

### Fixed
- Fixed the always-allowed slash-command path: when Hermes executes `/new`, `/reset`, `/clear`, `/undo`, `/stop`, or direct `/model <model>` without asking for confirmation, Feishu/Lark now receives the command result as an interactive card instead of gray native text.
- Removed the extra direct `message.update` attempt for interactive command-card callbacks. Feishu callback responses now own the in-place card update, avoiding invalid `msg_type=interactive` update warnings.
- Updated the Gateway hook patch so Feishu command-card installation receives the current `event`, allowing command result cardification without touching unrelated normal replies.

### Changed
- `/update` remains intentionally outside command-result cardification, preserving Hermes' background upgrade behavior.
- Patcher upgrade handling accepts the V3.8.4 command-card hook block and rewrites it to the V3.8.5 `event=event` form during install.

### Tests
- Added regression coverage for always-allowed `/new` command result cards, one-shot command-result context consumption, `/update` plain-text preservation, callback-only command-card updates, and legacy command-card hook upgrade compatibility.

## V3.8.4 — 2026-07-01

See also: [docs/release-notes-v3.8.4.md](docs/release-notes-v3.8.4.md)

### Fixed
- Fixed the Feishu/Lark WebSocket long-connection path for standalone slash command cards. Local/private sidecar deployments no longer have to fall back to gray Hermes native text for `/new`, `/reset`, `/undo`, and similar slash confirmations.
- Added a native Feishu adapter `send_slash_confirm(...)` monkeypatch that renders interactive cards and resolves clicks through Hermes `tools.slash_confirm.resolve(...)`.
- Added a native Feishu adapter `/model` picker path for WebSocket deployments. Model choices render as Feishu interactive card buttons and call Hermes' original `on_model_selected` callback.
- Skipped the sidecar `interaction.requested` pre-card whenever Feishu WebSocket-native command cards are available, preventing `/new` from showing both a sidecar choice card and a native button card.
- Repaired stale in-process install markers so an upgraded Gateway class cannot silently keep missing `send_slash_confirm(...)` and fall back to text.

### Changed
- Command-card action handling now wraps Feishu `_on_card_action_trigger` and only consumes plugin-owned `hfc_action` values; existing Hermes approval/update card actions continue to use the original adapter path.
- Release and installer documentation now explicitly describe Feishu/Lark WebSocket long-connection behavior instead of implying that slash command cards require a public HTTP callback.
- Failed native slash-card sends now emit a local warning instead of silently degrading, making real-environment diagnosis clearer.

### Tests
- Added regression coverage for native Feishu slash confirmation card sending, sidecar-skip behavior, stale install-marker repair, slash card action resolution, native model picker card sending, and model picker action resolution.

## V3.8.3 — 2026-07-01

See also: [docs/release-notes-v3.8.3.md](docs/release-notes-v3.8.3.md)

### Added
- Added standalone Feishu command-card handling for Hermes slash confirmations such as `/new`, `/reset`, `/undo`, and high-cost `/model <model>` confirmation prompts.
- Added a Feishu-only `send_model_picker(...)` adapter method when Hermes asks the Feishu adapter to render `/model` choices and the native adapter has no picker implementation.
- Added async command-card polling and terminal command-card completion updates without blocking the Hermes Gateway event loop.

### Changed
- Slash-command cards are intentionally separate from active Agent streaming cards. Approval, clarify, and Agent-turn options remain attached to the active card; independent slash commands render their own command surfaces.
- `/update` remains Hermes's background upgrade command and does not render an interactive command card.

### Fixed
- If command-card posting, polling, or completion updates fail, Hermes falls back to its native text path instead of swallowing command results.
- Local/private text fallback no longer creates a residual command card before handing slash confirmation back to Hermes native text prompts.
- Gateway patching now installs Feishu command-card adapter methods before slash command dispatch while preserving idempotent patch/remove behavior.

### Tests
- Added unit/integration coverage for async slash-confirm card requests, model picker callbacks, command-card completion events, text-mode native fallback non-application, patch insertion/removal, and fallback-preserving slash confirm flow.

## V3.8.2 — 2026-07-01

See also: [docs/release-notes-v3.8.2.md](docs/release-notes-v3.8.2.md)

### Fixed
- Pre-tool `answer.delta` blocks now stay in the primary card body while tools run, and are archived into the auxiliary timeline only when the next answer block or terminal answer arrives.
- Terminal cards strip archived intermediate-answer prefixes from completed answers, keeping the final response clean in the primary content area.
- Raw `thinking.delta` remains internal stream state instead of leaking into the main content area or auxiliary timeline.

### Changed
- Auxiliary timeline rendering now separates reasoning and tool entries into compact elements: reasoning uses `small`, tools use `x-small` quoted markdown, with lighter visual hierarchy for long command details.
- README screenshots now use the latest V3.8.2 collapsed and expanded real Feishu card examples.
- E2E preview generation now reads all timeline panel elements after per-entry rendering.

### Tests
- Added regression coverage for delayed pre-tool answer folding, terminal prefix stripping, compact timeline hierarchy, and updated server/render/preview expectations.

## V3.8.1 — 2026-07-01

See also: [docs/release-notes-v3.8.1.md](docs/release-notes-v3.8.1.md)

### Added
- Added read-only Feishu-side diagnostics commands: `/hfc help`, `/hfc status`, `/hfc doctor`, and `/hfc monitor`.
- Added Gateway runtime knobs for high-frequency delta coalescing: `HERMES_FEISHU_CARD_DELTA_COALESCE_MS`, `HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS`, and `HERMES_FEISHU_CARD_DELTA_COALESCE_MAX_PENDING`.

### Fixed
- issue #74: high-frequency `thinking.delta` / `answer.delta` bursts are now coalesced inside the Hermes Gateway process before reaching the sidecar, reducing stream-reader thread pressure that could trigger `Stream stale for 180s`.
- Terminal events now flush pending coalesced deltas before rendering `message.completed` / `message.failed`, preventing missing tail content at finalization.
- Existing installed hook blocks from V3.8.0 and earlier are still recognized during upgrade/remove even though V3.8.1 adds command handling to the hook.
- `/messages/{message_id}/summary` now returns hashed diagnostic ids instead of raw `chat_id` or Feishu message ids.

### Tests
- Added regression coverage for DeepSeek/Qwen-style high-frequency delta coalescing, terminal pre-flush, `/hfc` command interception, patcher upgrades, sidecar command cards, and summary redaction.

## V3.8.0 — 2026-07-01

See also: [docs/release-notes-v3.8.0.md](docs/release-notes-v3.8.0.md)

### Added
- Separated the primary answer area from the reasoning/tool timeline so the card keeps the final response prominent while auxiliary progress remains readable.
- Added card update metrics for queue depth, burst coalescing, terminal drain latency, and Feishu update latency to make streaming regressions easier to observe.
- Added a V3.8.0 card screenshot to the README homepage and refreshed install, upgrade, and Docker examples for the new release.

### Fixed
- Burst update coalescing now merges queued card refreshes more aggressively, reducing duplicated PATCH churn during fast thinking/tool bursts.
- Terminal completion now drains pending updates before rendering the final card, preventing stale intermediate content from winning the last PATCH.
- Long Markdown tables and fenced code blocks keep safe structural boundaries across card chunking, reducing raw Markdown leaks and half-open fences.
- The bottom tool-call summary is hidden when the auxiliary timeline is visible, preventing duplicate "N tool calls" sections.
- Runtime import diagnostics now execute from the Hermes project root, preventing current-repo `PYTHONPATH` false positives.

### Docs
- Added `docs/release-notes-v3.8.0.md` and refreshed release-planning notes for the V3.8.x line.

## V3.7.0 — 2026-06-29

### Added
- issue #70: added `install-docker.sh` for existing Hermes Docker containers with `/opt/hermes`, `/opt/data`, and Hermes venv Python assumptions.
- Added `docker-compose.example.yml` as a non-official Compose example for bind/volume layout and non-interactive installer execution.
- Release packages now include Docker install assets.

### Tests
- Added Docker installer script coverage, Compose example checks, release packaging coverage, and docs assertions.

## V3.6.6 — 2026-06-26

### Fixed
- issue #67: terminal Hermes events now ACK before slow Feishu card PATCH calls finish, while the card update continues in the background. This prevents interrupted or backlogged sessions from making Hermes fall back to a duplicate native text reply while the streaming card still updates.
- issue #67: `emit_from_hermes_locals_async()` now reads the sidecar JSON response and only reports delivery when `ok` and `applied` are not false, so stale or unapplied terminal events no longer masquerade as successful card delivery.
- issue #68: when `--hermes-dir` points to a directory without `gateway/run.py`, Hermes detection reads `hermes -V`, extracts the CLI `Project:` path, and surfaces a concrete `Use --hermes-dir ...` recommendation in `doctor --explain` / install diagnostics.

### Tests
- Added regression coverage for slow terminal Feishu PATCH ACK behavior, sidecar `applied` handling in the Hermes async hook, and wrong `--hermes-dir` diagnostics using a mocked Hermes CLI.

## V3.6.5 — 2026-06-23

### Fixed
- issue #64: `gateway_run_013_plus` now emits `message.started` with the same Feishu reply anchor used by streaming callbacks, so thread sessions no longer split across different `message_id` values and increment `events_ignored` with `events_applied=0`.
- issue #65: completed-only / burst-output models such as DeepSeek can now backfill the final answer from `agent_result.final_response` when no `thinking.delta` or `answer.delta` events were emitted before `message.completed`.
- Added sidecar regression coverage proving a card created by `message.started` updates and completes correctly when the only content-bearing event is `message.completed`.

### Docs
- Added V3.6.5 release notes and refreshed install/readiness examples for the new tag.

## V3.6.4 — 2026-06-22

### Fixed
- issue #61: Feishu thread messages now keep the initial streaming card inside the originating thread by carrying `thread_id` through the event protocol and using the Feishu reply API with `reply_in_thread: true` when a reply anchor is available.
- issue #62: cron jobs with `deliver: "feishu:oc_xxx"` now parse the chat id from the `deliver` field, allowing scheduled Feishu deliveries to render as cards instead of falling back to plain text.

### Docs
- Added V3.6.4 release notes and documented the optional event `thread_id` field used for Feishu thread routing.

## V3.6.3 — 2026-06-21

### Fixed
- issue #59: patcher now prefers Hermes v0.17.0+ / `v2026.6.19+` `_run_agent_inner` when injecting streaming callbacks, so tool, answer, thinking, clarify, and approval hooks are no longer skipped when `_run_agent` is only a wrapper.
- issue #57: `card.interaction_mode: auto` now switches localhost/private sidecars to text-choice fallback, and the Hermes hook stops polling for unreachable Feishu Card Action callbacks in that mode.
- issue #56: non-Feishu platforms such as Telegram are ignored before runtime event construction, keeping native Telegram delivery untouched after hook installation.
- issue #58: Windows `HERMES_HOME` profile paths under both `hermes/profiles/<id>` and `.hermes/profiles/<id>` now resolve the correct profile id.
- Extracted PR #52's useful Windows/proxy fixes: local/private sidecar calls bypass system proxies while public sidecar URLs keep default proxy behavior, and sidecar PID stop/status uses a Windows-specific path instead of POSIX process groups.

### Docs
- Added V3.6.3 release notes, README compatibility guidance for Hermes v0.17.0+ / `v2026.6.19+`, and `card.interaction_mode` config documentation.

## V3.6.2 — 2026-06-16

### Fixed
- issue #53: `install` / `setup` now detects the Hermes Gateway runtime venv Python and installs `hermes-feishu-streaming-card` into that interpreter before patching `gateway/run.py`.
- Hermes hook import/emit failures are no longer completely silent; injected hook blocks still fail open, but now write a diagnostic `[hermes-feishu-card] hook failed: ...` warning to Hermes stderr.
- `doctor --json` and `doctor --explain` now report `runtime_import`, including whether Hermes runtime Python can import `hermes_feishu_card.hook_runtime`.

### Docs
- Documented Hermes venv deployment behavior and installer safety expectations in README and installer safety docs.
- Kept `.env` search expansion out of this release scope; it remains a separate follow-up item from the venv runtime installation fix.

## V3.6.1 — 2026-06-06

### Fixed
- issue #47: Hermes semver `VERSION` values without a `v` prefix, such as `0.15.1`, are now parsed correctly instead of being reported as unsupported.
- Hermes `0.15.x` / `v0.15.x` now uses the existing `gateway_run_013_plus` hook strategy when the required `gateway/run.py` anchors are present.

### Tests
- Added release-matrix coverage for `0.13.0`, `0.14.0`, `0.15.1`, and `v0.15.1`.
- Added a `doctor --explain` regression test for Hermes `0.15.1` without the `v` prefix.

## V3.6.0 — 2026-06-04

### Added
- Read-only `doctor --json` and `doctor --explain` diagnostics covering config, sidecar, Hermes version/anchors, streaming settings, install state, and actionable recommendations.
- Safe `repair --hermes-dir ... --yes` and `setup --repair` flows for verifiable hook state recovery without overwriting user edits.
- Structured attachment extraction for Hermes locals such as `attachments`, `files`, `media_files`, image/audio/video file objects, and URL/file dictionaries.
- Profile-scoped operations: `smoke-feishu-card --profile-id`, `bots test --profile-id`, clearer CLI `status` routing output, and `/health.routing.profiles`.
- Hermes compatibility release matrix coverage for `v2026.4.23`, `v2026.5.7`, `v2026.5.16+`, `v2026.5.29`, `0.13.x`, and `0.14.x`.
- `docs/release-notes-v3.6.0.md` and refreshed release-readiness docs for operations-focused publishing.

### Fixed
- Repairable missing manifest/backup states are now detected and explained instead of leaving users with opaque `run.py changed since install` failures.
- Cards retain attachment summaries while the hook keeps Hermes native media/file delivery paths unsuppressed.
- Multi-profile routing diagnostics now show profile-level bot counts, chat bindings, last route, last route error, and event counters.

### Tests
- Added regression coverage for doctor JSON/explain output, repair refusal/recovery paths, structured media/file events, profile-targeted smoke commands, health routing grouping, Hermes release matrix fixtures, release asset dry-run guards, and documentation constraints.

## V3.5.2 — 2026-06-04

### Added
- Cross-platform installers: `install.sh` for macOS/Linux and `install.ps1` for Windows PowerShell.
- One-line install entry points in the Chinese and English README homepages.
- GitHub Release asset packaging workflow for macOS/Linux tarballs, Windows zip packages, and SHA-256 checksums.
- `README-install.md` and `docs/release-notes-v3.5.2.md` for packaged installer usage and release publishing.
- V3.6.0 roadmap documentation focused on repair diagnostics, media/file delivery, multi-profile operations, and release/E2E matrices.

### Fixed
- `install.sh` no longer sources the whole `.env` file. It now reads only Feishu/sidecar-related variables, so unrelated values with spaces such as browser paths do not break macOS installs.
- `install.sh` detects uv/PEP 668 `externally-managed-environment` Python errors and retries pip installation with `--break-system-packages`, keeping the failure mode explicit while allowing one-line installs on uv-managed macOS Python.

### CI
- Added a Windows GitHub Actions job that parses `install.ps1` with PowerShell AST validation.
- Added installer regression tests for safe `.env` parsing and externally managed Python retry behavior.
- Added documentation tests for one-line install commands and Release asset workflow coverage.

## V3.5.1 — 2026-06-01

### Fixed
- Feishu card updates are now ordered end-to-end for the same message id, covering Hermes runtime sends, interaction requests, sidecar state updates, and terminal card patches so thinking/answer text no longer rolls back or truncates under backlog.
- Sidecar non-terminal updates are coalesced and acknowledged quickly while terminal events remain awaited, improving perceived streaming speed and preventing a long update backlog from outliving `message.completed`.
- Feishu JSON 2.0 interaction buttons now use direct `button` elements with `behaviors.callback`, fixing card PATCH failures when approval/choice buttons render inside an active card.
- Queued follow-up completions now emit `message.completed` into the card path and suppress native resend once the Feishu card is delivered, preventing final answers from spilling into gray plain-text messages.
- Runtime delta extraction preserves raw boundary spaces for `thinking.delta` and `answer.delta`, preventing sentence/code spacing loss while streaming.
- `load_config()` reads a `.env` file next to the selected config file before applying real process environment variables, preventing manual sidecar restarts from silently entering no-op mode when Feishu credentials live beside Hermes config.

### Docs
- Reorganized the Chinese README homepage around the V3.5.x value proposition, live user scenarios, installation/upgrade flow, troubleshooting, and version history.

### Tests
- Added regression coverage for ordered runtime sends, interaction event retries on transient sidecar state, Feishu JSON 2.0 button callback payloads, queued follow-up suppression, `.env` config fallback, and update coalescing.

## V3.5.0 — 2026-06-01

### Added
- Feishu card interaction loop for Hermes approval and choice prompts: `interaction.requested` renders buttons in the active card, `/card/actions` records the user's selection, and the Hermes hook polls `/interactions/{interaction_id}` so the original task can continue.
- Patcher support for Hermes `v0.14.0` / `v2026.5.16+` approval and clarify callbacks.

### Fixed
- issue #41: multi-reply/newer Hermes streaming flows keep final answers on the card path instead of falling back to native text after the first reply.
- PR #42: cron card delivery now prioritizes `job['deliver']` and scheduler-resolved Feishu targets over stale `origin.platform` metadata.
- Long single Markdown tables and fenced code blocks are split into valid repeated table/code chunks when they exceed `MAIN_CONTENT_CHUNK_CHARS`, preventing raw Markdown rendering in Feishu.
- Thinking/interim assistant text is emitted as complete `append_block` chunks so sentences are not truncated, glued, or dropped by delta-style accumulation.

### Tests
- Added regression coverage for interaction event parsing, session state, card buttons, Feishu callback resolution, Hermes hook polling, cron deliver precedence, long table/code chunking, and thinking append-block behavior.

## V3.4.3 — 2026-05-27

### Fixed
- issue #39: blank or whitespace-only `message.completed` answers no longer clear an answer that already arrived through `answer.delta`, preventing DeepSeek V4 Pro tool-call flows from ending with an empty Feishu card.
- Long Markdown card content is split at paragraph, table, and fenced-code boundaries instead of raw character offsets, so Feishu does not render split table/code fragments as broken raw Markdown.
- issue #34 follow-up: compatibility tests now cover Hermes `v0.14.0` / `v2026.5.16+` selecting the `gateway_run_013_plus` strategy, while `v2026.4.x` remains on `legacy_gateway_run`.

### Tests
- Added regression coverage for blank completed answers after streamed deltas, Markdown-aware card splitting, Hermes `v0.14.0`, and Hermes `v2026.4.30`.

## V3.4.2 — 2026-05-21

### Fixed
- issue #31: Feishu card PATCH updates are now serialized per session so older card snapshots cannot land after newer content and cause thinking/answer text to flicker or roll back.
- Concurrent Hermes callback events now allocate per-message sequence numbers under a lock, preventing duplicate sequence ids that could make valid `thinking.delta` / `answer.delta` chunks look stale.

### Tests
- Added regression coverage for out-of-order PATCH completion and concurrent runtime sequence allocation.

## V3.4.1 — 2026-05-14

### Fixed
- issue #25: Hermes v2026.5.7 started hooks now treat `event_message_id` as an explicit message id, keeping `message.started` and `message.completed` on the same card lifecycle.
- Fallback preview now reuses the active fallback cache, so `_preview_fallback_message_id` and `_create_active_fallback_message_id` do not drift when `created_at` is missing.

### Tests
- Added regression coverage for Hermes v2026.5.7-style started locals and untokened fallback preview/create lifecycle consistency.

## V3.4.0 — 2026-05-10

### Added
- Hermes 0.13+ compatibility strategy: installer and `doctor` select/report the `gateway_run_013_plus` hook strategy from Hermes version and code anchors.
- Per-bot/profile titles: card titles can be set globally, per profile, or per bot, with bot-level titles taking precedence.
- Cron final card delivery for scheduled Hermes runs.
- Attachment summaries with native media delivery, keeping summaries in cards while media uses Feishu-native delivery.
- Card reply context so reply cards retain the routing/context needed by the sidecar.

### Fixed
- issue #23: multi Hermes profile + multi Feishu bot deployments now preserve explicit profile identity and route to the intended bot.

### Compatibility
- Older Hermes strategy preserved: Hermes `v2026.4.23` through `0.12.x` continues to use `legacy_gateway_run`.
- `doctor` now exposes `hook_strategy`, `compatibility`, and anchor diagnostics to make install decisions auditable before writing hooks.

## [3.3.0] - 2026-05-01

### Fixed
- **#15 - COMPLETE_PATCH platform check**: `_render_complete_hook_block` and `_render_previous_async_complete_hook_block` now gate `return None` behind `source.platform.value == "feishu"`, preventing the complete hook from swallowing responses on QQ/WeChat/DingTalk etc. (`install/patcher.py`)
- **#18b - Tool count accuracy**: `CardSession.tool_count` now returns actual cumulative call count instead of deduplicated unique tool count. Added `_tool_call_count` field that increments on every `tool.updated` event. (`session.py`)
- **#10 - Card table limit**: Markdown tables exceeding Feishu's 5-table-per-card limit are now truncated with a notice appended. Added `count_markdown_tables()` and `MAX_CARD_TABLES` constant. (`text.py`, `render.py`)

### Added
- **#18a - DeepSeek `<thinking>` tag support**: `THINK_TAG_RE` and `THINK_TAGS` now include `<thinking>`/`</thinking>` tags alongside `<think>`/`</think>` for DeepSeek-compatible reasoning content normalization. (`text.py`)
- **#18c - Footer spinner animation**: Non-terminal card footer now shows a rotating braille spinner instead of static "生成中". Frame driven by `time.time()`, no extra API calls. (`render.py`)
- **#16 - Multi-profile support**: A single sidecar process can now serve multiple Hermes profiles with independent Feishu credentials, session isolation, and per-profile bot routing. Backward compatible — single profile behavior unchanged. (`config.py`, `runner.py`, `server.py`)

### Changed
- `_render_footer()`: "生成中" static text replaced with `_spinner_text("生成中")`
- `CardSession`: `_tool_call_count` field tracks actual call count; `tool_count` property reflects cumulative count while `tools` dict retains unique tool states
- `_render_complete_hook_block` / `_render_previous_async_complete_hook_block`: platform check added before `return None`
- `build_feishu_boundary()`: now detects profiles and delegates to `_build_multi_profile_boundary()` when configured
- `_apply_event_locked()`: uses composite `profile_id:message_id` session keys when profiles are active
- `_resolve_route()` / `_client_for_bot()`: profile-aware routing with dict-based factory selection

## [3.2.1] - 2026-04-29

### Fixed
- **HTTP Accept-Encoding header**: Add `Accept-Encoding: gzip, deflate` to Feishu API requests to avoid `ClientPayloadError: Can not decode content-encoding: br` when Feishu returns brotli-compressed responses (aiohttp limitation). Fix in `feishu_client.py` by setting request headers, not relying on server's `content-encoding` auto-decoding.

### Changed
- `feishu_client.py`: HTTP client now explicitly requests gzip/deflate encoding; brotli responses from Feishu are avoided at the server side by this header.

## [3.2.0] - 2026-04-29

### Added
- **Multi-bot registry**: `bots` section in config to define multiple Feishu bots with `app_id`/`app_secret`
- **Chat-to-bot bindings**: `bindings.chats` maps `chat_id` → `bot_id`, with `fallback_bot` for unbound sessions
- **Group rules framework**: `bindings.group_rules` section reserved for future group trigger filtering (V3.2 no-op)
- **Bot management CLI**: `hermes_feishu_card.cli bots` with `list`, `show`, `add`, `remove` commands
- **Sidecar routing diagnostics**: `/health.routing` exposes `bot_count`, `chat_binding_count`, `last_route`, `bots[]` details
- **Optional routing context extraction**: `hook_runtime._event_data()` now extracts `chat_type`, `tenant_key`, `agent_id`, `profile_id` from `message.started` for future features

### Changed
- `runner.py`: Uses `FeishuBoundary` with `BotRegistry.resolve()` to route events to bot-specific `FeishuClient`
- `server.py`: Adds bot lookup via `registry.resolve(RoutingContext(...))` before sending card updates
- `config.py`: Adds `bots`, `bindings`, `group_rules` schema validation with defaults
- `cli.py`: New `bots` command group with management subcommands and `--config` flag
- Package version: `3.1.0` → `3.2.0`

### Fixed
- `runner.py`: Ensure `NoopFeishuClient` path respects absent credentials without breaking
- `cli.py`: Default bot name resolution respects config-defined default item name
- `server.py`: Bot resolution gracefully falls back to `default_bot` when no binding matches

### Docs
- `README.md` / `README.en.md`: New "V3.2 多 bot 与群聊" section with config examples and CLI usage
- `config.yaml.example`: Full `bots` + `bindings` + `group_rules` sample
- Test suite updated to 398 tests (unit + integration coverage for bots, routing, config)

## [3.1.0] - 2026-04-XX

### Added
- Sidecar architecture: standalone aiohttp server for Feishu CardKit HTTP client
- Streaming card updates: `thinking.delta`, `answer.delta`, `tool.updated`, `message.completed/failed`
- Health endpoint (`/health`) with metrics and diagnostics
- Auto-recovery: retry with exponential backoff on transient failures
- Fail-open: Hermes continues with plain text if sidecar unavailable
- Installation wizard with version and structure guardrails
- Uninstall/restore hooks preserving user modifications

### Changed
- `feishu_streaming_card.mode: sidecar` in Hermes config (replaces `enabled: true`)
- Card rendering offloaded from Hermes process to sidecar
- Footer fields configurable via `card.footer_fields` (default: duration/model/tokens/context)

### Fixed
- Long card body splitting into multiple Markdown elements for 16k+ Chinese characters
- `<think>`/`</think>` tags stripped from streaming content
- Duplicate native text message suppression on completion

(Placeholder entries below for future minor/patch releases)

## [3.1.1] - TBD
- Patch notes...

## [3.0.0] - 2026-04-XX
Initial public release of the sidecar architecture. (Previous versions were v2.x monolith hook inside Hermes.)
