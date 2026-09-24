# Release history

[Back to README](../README.en.md) · [Full CHANGELOG](../CHANGELOG.md)

| Version | Highlights |
|---|---|
| [v4.6.9](release-notes-v4.6.9.en.md) | Independent concurrent group cards, compact single-select choices and clearer onboarding |
| [v4.6.8](release-notes-v4.6.8.en.md) | macOS setup and recovery guidance with explicit startup and ownership boundaries |
| [v4.6.7](release-notes-v4.6.7.en.md) | Preserve editable heartbeats and compact approval buttons |
| [v4.6.6](release-notes-v4.6.6.en.md) | Verified approval-receipt compaction, visible active tools and native notice expiry |
| [v4.6.5](release-notes-v4.6.5.en.md) | Persistent notice ownership, explicit tool-call identity, optional timeline controls and provider-error deduplication |
| [v4.6.4](release-notes-v4.6.4.en.md) | First-click callbacks, chronological continuation, optional reading presets and scoped notices |
| [v4.6.3](release-notes-v4.6.3.en.md) | Live thinking visibility, tool duration and interrupted-turn metrics |
| [v4.6.2](release-notes-v4.6.2.en.md) | Shared Gateway drain proof and optional terminal tool rows |
| [v4.6.1](release-notes-v4.6.1.en.md) | Hermes 0.21.3 hooks, safe status recall and approval display |
| [v4.6.0](release-notes-v4.6.0.en.md) | Profile-aware recall, structured reasoning, bounded retries and card restart recovery |
| [v4.5.2](release-notes-v4.5.2.en.md) | Queued failure preservation and bounded transient notice recall |
| [v4.5.1](release-notes-v4.5.1.en.md) | CardKit ID limits, topic delivery, approval lifecycle and restart feedback |
| [v4.5.0](release-notes-v4.5.0.en.md) | Topic and mobile interaction fixes, CardKit streaming, requester mentions and live approval pause |
| [v4.4.6](release-notes-v4.4.6.en.md) | Recover terminal delivery, accept current Hermes attachment anchors, preserve incomplete outcomes and interaction context |
| [v4.4.5](release-notes-v4.4.5.en.md) | Preserve unsuccessful and superseded turn outcomes; support the verified split-ledger contract with stronger stability regressions |
| [v4.4.4](release-notes-v4.4.4.en.md) | Keeps Hermes restart/shutdown notices inside the originating Feishu topic and activates the startup routing hook before boot notifications |
| [v4.4.3](release-notes-v4.4.3.en.md) | Hermes upgrades carrying older owned hooks, integrity snapshots that preserve local source customization, and omission of an empty zero-reasoning/zero-tool timeline |
| [v4.4.2](release-notes-v4.4.2.en.md) | Hermes 0.21 integrity migration, source-only ownership, multiplex adapters, and approval interactions |
| [v4.4.1](release-notes-v4.4.1.en.md) | Hermes 0.21 facade-decomposition compatibility, topic follow-ups, single-process profiles, complete approval scope, optional reasoning code blocks, actual provider attribution, and CodeQL updates |
| [v4.4.0](release-notes-v4.4.0.en.md) | Adds a Feishu-native capability center driven by the latest Hermes `COMMAND_REGISTRY`, category/detail navigation, safe quick actions, KPI cards, `/bg`/`/btw`/`/plan` compatibility, real backlog metrics, and extreme-Markdown safe folds |
| [v4.3.8](release-notes-v4.3.8.en.md) | Makes guided setup persistent when capabilities are ready and explicit about transient reboot risk otherwise, fixes the next-prompt sequence race in batch clarify, and honors proxy environment variables for remote Feishu/Lark HTTP while keeping local/private bypass |
| [v4.3.7](release-notes-v4.3.7.en.md) | Supports Hermes 2026-08-25 core session-scoped delivery filters: the installer accepts the exact new `session_key=session_key` call while preserving the legacy call and rejecting every other keyword shape |
| [v4.3.6](release-notes-v4.3.6.en.md) | Replaces invalid unanchored topic creation with `receive_id_type=chat_id` to prevent Feishu `99992402`; approval/clarify cards and completion notifications can optionally `@` mention the requester without changing the schema 2.0 owner card |
| [v4.3.5](release-notes-v4.3.5.en.md) | Supports the Hermes v2026.8.3 Feishu adapter whose `edit_message` method has no `metadata` parameter: the wrapper removes only unsupported internal metadata, preserves metadata-aware/`**kwargs` adapters, and still raises `TypeError` for unrelated unknown keywords |
| [v4.3.4](release-notes-v4.3.4.en.md) | Prevents reverse-DNS stalls while starting the runtime interaction listener and lets a process exit when that listener is not explicitly closed; `doctor --json` now validates V3 Hybrid installs with the V3 inspector instead of reporting Legacy manifest/hash/path failures |
| [v4.3.3](release-notes-v4.3.3.en.md) | Preserves the reply anchor and `reply_in_thread` placement when the first reply creates a thread; completion notifications stay in that thread, while an explicit thread reply without an anchor fails closed instead of posting top-level text |
| [v4.3.2](release-notes-v4.3.2.en.md) | Fixes Issue #227 by keeping schema 2.0 streaming cards and legacy interaction cards on stable rails, preventing `230099/200800`; the Gateway also rejects schema 2.0 raw callback cards to prevent `200673` |
| [v4.3.1](release-notes-v4.3.1.en.md) | Restores clarify/approval streaming after a Feishu WebSocket click on Hermes 0.20, wakes text fallback on the first reply, and fixes v4.3.0 persistent-service identity, systemd working-directory, and tokenless-health reconciliation |
| [v4.2.11](release-notes-v4.2.11.en.md) | Fixes Issue #202 by freezing each superseded streaming card as a green “moved to the interaction card” history snapshot after replacement delivery; predecessor PATCH failure remains fail-open and only the newest card receives choices and later updates |
| [v4.2.10](release-notes-v4.2.10.en.md) | Authenticates non-loopback sidecar callbacks and result reads with method/path/body-bound HMAC, enforces absolute interaction expiry with late-button/form rejection and same-card refresh, and adds cross-platform CI, CodeQL, Dependabot, and Node 24 Action SHA gates; see [v4.2.9](release-notes-v4.2.9.en.md) for the preceding release |
| [v4.2.8](release-notes-v4.2.8.en.md) | Fixes the installer contract so `install.sh`, `install-docker.sh`, and `install.ps1` persist process-supplied Feishu credentials into the private `.env` instead of using them only for the current process |
| [v4.2.7](release-notes-v4.2.7.en.md) | Fixes Issue #193 Windows cold-import timeouts and legacy backslash manifest paths, integrates PR #180 parent `HERMES_HOME` discovery and PR #181 safe detached-runner PID rebinding, and propagates PowerShell installer failures |
| [v4.2.6](release-notes-v4.2.6.en.md) | Fixes Issue #187 repeated choice-card position, #188 short terminal postscripts replacing answers, #189/PR #190 exact Base compatibility for Hermes 0.20, and bare Feishu `/update` venv-symlink, slow-fetch, and version-reporting failures; see [v4.2.5](release-notes-v4.2.5.en.md) for the preceding audit safety hotfix |
| [v4.2.4](release-notes-v4.2.4.en.md) | Fixes consecutive Feishu/Lark topic replies quoting the same message overwriting the first reply card; every new message opens an independent card while in-turn streaming still resolves through the reply alias |
| [v4.2.3](release-notes-v4.2.3.en.md) | Preserves `update_evidence_fingerprint` when the WebSocket hook forwards `/update` actions, allowing the sidecar to complete evidence-bound confirm/cancel transitions while missing or mismatched evidence remains fail-closed |
| [v4.2.2](release-notes-v4.2.2.en.md) | Fixes `/update` confirmation actions that changed durable state without PATCHing the original card; cancel now renders a terminal state and never starts the updater, while confirm shows preparation before scheduling maintenance |
| [v4.2.1](release-notes-v4.2.1.en.md) | Registers the live Gateway runner before the first runtime heartbeat, so the first bare private-chat `/update` after restart has complete active-work evidence; missing evidence remains fail-closed |
| [v4.2.0](release-notes-v4.2.0.en.md) | A bare `/update` in a Feishu private chat uses a 120-second confirmation and an independent maintenance process to run the official Hermes updater, then restores the same HFC version, hooks, sidecar, and Gateway; group and parameterized commands keep native Hermes behavior |
| [v4.1.4](release-notes-v4.1.4.en.md) | Fixes Issue #171: on Windows, official install/setup can rebuild a missing manifest for a legacy owned hook only after byte-for-byte gateway, cron, and exact Base evidence checks; edits outside owned blocks still fail closed |
| [v4.1.3](release-notes-v4.1.3.en.md) | Fixes the same-target fence-binding convergence gap from Issue #158, includes PR #168's native delta-callback selection, and restores tool/streaming/interaction hooks plus truthful doctor detection after Hermes' `TurnRunner` refactor from Issue #169 |
| [v4.1.0](release-notes-v4.1.0.en.md) | Exact per-chat card/native policy, lossless compaction after five tables, authenticated runtime integrity with strict repair, and four explicit sidecar managers with no privilege escalation from `auto`; follow-up fixes are documented in [v4.1.1](release-notes-v4.1.1.en.md) and [v4.1.2](release-notes-v4.1.2.en.md) |
| [v4.0.21](release-notes-v4.0.21.en.md) | Issue #155 archives answers only at an explicit `answer -> tool` boundary so post-tool final answers stay visible; Issue #147 real Feishu acceptance observed a completion card plus native image with no matching native duplicate or uncertain-delivery warning; UI and configuration remain unchanged |
| [v4.0.20](release-notes-v4.0.20.en.md) | Fixes Issue #153: queued notice updates return `accepted` without false unknown-delivery warnings, while real PATCH failures retain redacted metrics and error codes |
| [v4.0.19](release-notes-v4.0.19.en.md) | Prevents the one-line installer from using `pip --user` inside the Hermes venv and stops immediately on pip failures, avoiding false upgrade success |
| [v4.0.18](release-notes-v4.0.18.en.md) | Checks the real Hermes Feishu SDK constructor capability, diagnoses stale `lark-oapi`, and repairs it during setup/install |
| [v4.0.17](release-notes-v4.0.17.en.md) | Correlates parallel same-name tools by real call ID, counts invocations once, and removes duplicate duration detail |
| [v4.0.16](release-notes-v4.0.16.en.md) | Removes duplicate initial loading text, drops the stale body placeholder once tools start, and restores real tool durations |
| [v4.0.15](release-notes-v4.0.15.en.md) | Fixes Issue #141 with a compact semantic tool timeline and real loading animation; CLI detects Hermes upgrades that removed the hook |
| [v4.0.14](release-notes-v4.0.14.en.md) | Fixes Issue #142 so orphaned long-task heartbeats stay running, update one card per original message anchor, and still complete on the final event |
| [v4.0.13](release-notes-v4.0.13.en.md) | Routes every non-empty Hermes slash-command feedback message through a standalone command card, updates one card for multi-message feedback, keeps manual `/compress` progress/results in place, and falls back to exact native text on failure |
| [v4.0.12](release-notes-v4.0.12.en.md) | Issue #133 adds visible context-compaction phases and configurable body/reasoning/tool/notice/footer text sizes; Issue #136 loads selected-env credentials and exposes degraded Noop delivery |
| [v4.0.11](release-notes-v4.0.11.en.md) | Fixes Issue #135 with stable-UUID bounded initial delivery retries and safe `delivered/not_sent/unknown` notice fallback semantics |
| [v4.0.10](release-notes-v4.0.10.en.md) | Hardens sidecar event transport: non-loopback listeners require explicit opt-in plus HMAC-SHA256 anti-forgery/replay proofs, while loopback installs stay compatible |
| [v4.0.9](release-notes-v4.0.9.en.md) / [v4.0.8](release-notes-v4.0.8.en.md) | Fixes Issue #130's live WebSocket handler identity and Issue #127's native cron attachment delivery |
| [v4.0.7](release-notes-v4.0.7.en.md) | Isolates the Linux/systemd sidecar in a restartable user service, prefers Hermes venv Python during upgrades, and includes PR #124's orphaned self-improvement notice fix |
| [v4.0.6](release-notes-v4.0.6.en.md) | Fixes Hermes 0.18.x terminal/queued completion hooks and terminal background notice cards without gray native output, with explicit fail-closed recovery after Hermes source upgrades |
| [v4.0.5](release-notes-v4.0.5.en.md) | Fixes upgrades that left the Gateway venv loading an older plugin; the installer compares runtime versions, synchronizes when needed, and verifies the installed version and path |
| [v4.0.4](release-notes-v4.0.4.en.md) | Fixes Markdown `MEDIA:` literals, interaction forwarding with an SDK-retained callback, and misleading `5h` labels when Codex exposes one ambiguous limit window |
| [v4.0.3](release-notes-v4.0.3.en.md) | Fixes duplicate gray answer text when the package is upgraded and restarted while a V4.0.0 completion hook remains; suppresses one exact text copy while preserving native media |
| [v4.0.2](release-notes-v4.0.2.en.md) | Allows safe upgrades from verified older owned hooks when manifest and backup evidence match; includes the v4.0.1 media-text deduplication fix |
| [v4.0.0](release-notes-v4.0.0.en.md) | The running Header shows the latest Hermes tool preview while public interim output streams independently in the body; waiting, failed, and completed states preserve established Footer and reply boundaries |
| [v3.10.0](release-notes-v3.10.0.md) | Bare `/resume` uses a native session picker while retaining Hermes' security path; the model footer gains escaped semantic color without changing layout or field order |
| [v3.9.1](release-notes-v3.9.1.md) | Reliability hotfix: preserve completed answers, serialize interrupted terminal cards, make model-picker callbacks asynchronous, and recover verifiable marker-only installer damage; normal streaming-card footer/layout remains unchanged |
| [v3.8.18](release-notes-v3.8.18.md) | Cron cards preserve `thread_id` and return to the originating Feishu topic thread (PR #91, contributed by @colinaaa) |
| [v3.8.17](release-notes-v3.8.17.md) | Cron `deliver=origin/all` routing intents resolve to Feishu targets and send cards |
| [v3.8.16](release-notes-v3.8.16.md) | Topic groups that reuse `message_id` now send a fresh card for the second and later messages |
| [v3.8.15](release-notes-v3.8.15.md) | Input `.docx/files` context stays as card attachment summaries and no longer duplicates the native final reply |
| [v3.8.14](release-notes-v3.8.14.md) | Agent clarify/approval buttons resolve through WebSocket-native `interaction.select` card actions |
| [v3.8.13](release-notes-v3.8.13.md) | Hermes `v2026.7.7.2` / `0.18.2` upgrades can fall back to anchors and repair stale install state |
| [v3.8.12](release-notes-v3.8.12.md) | Completed cards with attachment summaries such as `colors.csv` / `styles.csv` no longer duplicate the final native reply |
| [v3.8.11](release-notes-v3.8.11.md) | `/hfc status` no longer triggers the gray native `Unknown command /hfc` reply after the card is accepted |
| [v3.8.10](release-notes-v3.8.10.md) | Group `/hfc status` binding hints and slash-command boundaries; tool details show arguments, duration, and failures |
| [v3.8.9](release-notes-v3.8.9.md) | Feishu/Lark topic card continuity; `system.notice` no longer duplicates outside the card |
| [v3.8.8](release-notes-v3.8.8.md) | Cardifies native Hermes notices: Working, context compression, skill loading, and self-improvement review |
| [v3.8.7](release-notes-v3.8.7.md) | Newer Hermes streams can create cards even when `message.started` is missing |
| [v3.8.5](release-notes-v3.8.5.md) | Direct slash-command results delivered as interactive cards |
