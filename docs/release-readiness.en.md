# Release Readiness

[中文](release-readiness.md) | [English](release-readiness.en.md)

Current release candidate: `4.4.1`. This cycle addresses Hermes 0.21 facade decomposition, topic follow-up replies, single-process profile routing, complete approval scope, reasoning presentation, and CodeQL updates. Focused automation has passed; full-suite, regular-wheel, and GitHub CI results belong to this release's validation record. Real Feishu/Lark client retesting has not been performed for this candidate; earlier release smoke does not constitute current acceptance. The release PR, exact merge SHA, public tag/install, and Release assets are marked passed only after completion; automation is not represented as platform acceptance.

V3.9.0 was released on 2026-07-11, and V3.9.1 was released on 2026-07-11. The V4.0.13 all-command lifecycle remains intact; V4.2.0 narrows only a private-chat bare `/update` into the stricter dedicated maintenance card.

## Ready

- Hermes `v2026.4.23+` detection and fail-closed installation.
- Minimal Hermes hook, backup, manifest, restore, and uninstall.
- Sidecar `/events`, `/health`, and process `start/status/stop`.
- Feishu CardKit HTTP client, covered by mock Feishu server and real Feishu test app for tenant token, send, and update flows.
- Manual `smoke-feishu-card` command.
- E2E preview artifacts and generator.
- Real long-card stress test: one Feishu card updated to 16k Chinese characters.
- Real Hermes `v2026.4.23` `restore -> install` loop verification.
- Hermes `0.13.0+` / `0.14.0` / `0.15.x` / `0.17.x` / `0.18.x` / `v2026.5.16+` / `v2026.6.19+` / `v2026.7.1+` / `v2026.7.7.2` use the `gateway_run_013_plus` hook strategy, while older `v2026.4.x` keeps `legacy_gateway_run`.
- Feishu card button interactions are covered through local mock acceptance for `interaction.requested`, `/card/actions`, and `/interactions/{interaction_id}`; localhost/private sidecars use WebSocket-native callbacks in default `auto` mode, while explicit `card.interaction_mode: text` retains numbered-text fallback.
- Feishu thread messages can carry optional `thread_id`; with a reply anchor, the sidecar uses the Feishu reply API to create the initial card in the original thread, and later updates keep PATCHing the same card.
- Cron delivery can extract chat ids from `deliver: "feishu:oc_xxx"` and can resolve `deliver: origin`, `deliver: all`, and `origin,all` through Feishu origins or scheduler targets, avoiding plain-text fallback for scheduled Feishu deliveries; `deliver: local` remains no delivery.
- Long Markdown tables and fenced code blocks over `MAIN_CONTENT_CHUNK_CHARS` are split as complete repeated structures to avoid raw Markdown rendering.
- Thinking/interim assistant messages use complete `append_block` chunks to avoid delta accumulation truncation or missing text.
- Runtime event sends, sidecar updates, and terminal PATCH calls are ordered/coalesced for the same message id.
- Newer Hermes streams that begin with `answer.delta`, `thinking.delta`, `tool.updated`, or `message.completed` without `message.started` still create the initial Feishu/Lark card.
- Native Hermes `Working` heartbeats, context-window/compression notices, automatic session resets, skill loading, and self-improvement reviews are normalized as `system.notice`; session notices prefer the active card timeline, while task-external notices use compact standalone cards.
- In Feishu/Lark topic replies, later `answer.delta`, `thinking.delta`, `tool.updated`, and `system.notice` events resolve through `reply_to_message_id` back to the same card even when Hermes uses a different internal streaming `message_id`, preventing frozen topic timelines and duplicate gray native notices.
- In Feishu/Lark topic groups that reuse the same `message_id` across consecutive turns, completed or failed old sessions are cleared and a fresh card is created; duplicate `message.started` events during an active turn still stay ignored to avoid accidental second cards.
- Gateway runtime coalesces high-frequency `thinking.delta` / `answer.delta` events inside the Hermes process, covering V3.8.1 issue #74 and reducing stream-reader thread pressure.
- Terminal events flush pending deltas for the same message before final card rendering.
- Feishu-side `/hfc help/status/doctor/monitor` commands return read-only diagnostic cards with hashed context ids.
- Accepted `/hfc` diagnostic commands ACK Hermes Gateway quickly and send the real Feishu/Lark card in the background, preventing `/hfc status` from double-sending a card plus the gray native `Unknown command /hfc` reply.
- Generic attachment summaries in completed cards no longer trigger native final-reply fallback; real `MEDIA:`, local file paths, and Hermes media/file locals still keep the native file/media delivery path available.
- Group `/hfc status` reports chat binding state, fallback/default routing, the suggested `bots bind-chat` command, and group slash-command behavior boundaries while real @robot and allowlist admission remains owned by Hermes Gateway.
- Pre-tool answers stay in the primary body first, then archive into the auxiliary timeline when the next answer or terminal event arrives; terminal cards strip already archived intermediate prefaces.
- Auxiliary timeline reasoning and tool details use separate text sizes and visual weight, while raw `thinking.delta` stays out of the user-visible timeline.
- Tool details can show argument summaries, duration, and failure reasons while keeping timeline rendering compact.
- Independent slash-command confirmations support Feishu command cards: `/new`, `/reset`, `/undo`, and high-cost `/model <model>` prompts render as standalone command cards when available.
- Feishu/Lark WebSocket long-connection deployments dynamically gain native `send_slash_confirm(...)` and `send_model_picker(...)` card support; button clicks route through `_on_card_action_trigger` back into Hermes' original handlers.
- When WebSocket-native cards are available, the sidecar `interaction.requested` pre-card is skipped so the same slash command does not show both a sidecar choice card and a native button card.
- No-argument `/model` selection can use a Feishu-only `send_model_picker(...)` card, call Hermes's callback, and update the same command card with the result.
- V4.2.0 intercepts only a bare `/update` in a Feishu private chat: after read-only inspection it shows a 120-second maintenance confirmation, then an independent runtime runs `hermes update --yes`, reinstalls the same HFC version, and restores the hook, sidecar, and Gateway. Group, non-Feishu, alias, and parameterized commands retain Hermes' original path. Run `maintenance status` first.
- Terminal events ACK Hermes quickly while slow Feishu PATCH calls complete in the background, preventing duplicate native replies after interrupts or update backlogs.
- `load_config()` reads a `.env` file next to the selected config file while preserving real process environment variables as the highest-precedence source.
- `install.sh` imports only Feishu/sidecar variables from `.env`, avoiding execution of unrelated values such as paths with spaces.
- `install.sh` retries pip with `--break-system-packages` when uv/PEP 668 reports an externally managed Python environment.
- Windows sidecar process `stop/status` avoids POSIX process-group signals and uses Windows-specific PID/`taskkill` handling.
- `doctor --json` / `doctor --explain` report config, sidecar, Hermes, streaming, install_state, and recommendations.
- `doctor --explain` / `install` suggest the Hermes CLI `Project:` directory as the correct `--hermes-dir` when `gateway/run.py` is missing and `hermes -V` is available.
- `setup` / `install` detect the Hermes runtime venv Python and install the same plugin release there; `doctor` reports `runtime_import`.
- `install-docker.sh` supports installer/update workflows inside existing Hermes Docker containers with defaults `HERMES_DIR=/opt/hermes`, `HFC_CONFIG=/opt/data/config.yaml`, and `HFC_ENV_FILE=/opt/data/.env`.
- `docker-compose.example.yml` documents bind mounts and one-shot `bash install-docker.sh` execution for container topologies.
- Docker/source-stripped Hermes roots without `VERSION` and `.git` metadata can fall back to `gateway/run.py` anchors in `doctor`, `install`, and `setup`; diagnostics report `version_source: gateway anchors`. If version metadata exists but is unparseable, verifiable anchors allow diagnostics to report `VERSION + gateway anchors` or `git tag + gateway anchors` and continue.
- Hook import/emit failures remain fail-open but write `[hermes-feishu-card] hook failed: ...` diagnostic warnings to Hermes stderr.
- `repair --hermes-dir ... --yes` and `setup --repair` repair verifiable manifest/backup state and refuse unverifiable user edits.
- Structured attachment, media, and file objects keep card summaries while preserving Hermes native media/file delivery paths.
- `smoke-feishu-card --profile-id`, `bots test --profile-id`, CLI `status`, and `/health.routing.profiles` support profile-scoped troubleshooting.
- Hermes key release matrix covers `v2026.4.23`, `v2026.5.7`, `v2026.5.16+`, `v2026.5.29`, `v2026.6.19+`, `v2026.7.1+`, `v2026.7.7.2`, `0.13.x`, `0.14.x`, `0.15.x`, `0.17.x`, `0.18.x`, semantic versions with or without a `v` prefix, and descriptive version metadata.
- GitHub Actions Python 3.9 / 3.12 test matrix for PRs and pushes, plus Windows parser validation for `install.ps1`.
- Release assets workflow packages macOS/Linux/Windows installers and checksums for tags.
- V3.9.0 operations cards support diagnosis, recheck, two-step safe repair, and restart confirmation; private chats do not compare operators, while group repair/restart confirmation stays with the initiator. Use CLI fallback when the card is unavailable.
- The state-dir transport root automatically creates a private-permission transport secret. No secret configuration is required, and diagnostics/cards never output it.
- Setup resolves profile/event URL by explicit argument, process environment, selected env file, then default; only `doctor` shows the complete redacted identity/profile/event-endpoint route chain, `status` summarizes runtime routing/profile events, and `/health` reports actual routing-health fields.
- Install/setup can automatically repair known-safe state; `--no-repair` opts out, and unverifiable user edits remain refused. Cleanup history and metrics are bounded and hashed.
- Operations-card WebSocket callbacks ACK immediately, authenticated actions enter a bounded background queue with finite retry, and every authenticated state PATCHes the original card without making recheck/repair/restart wait for Feishu PATCH completion.
- Automated release gate: `1172 passed, 3 skipped` on Python 3.9 and Python 3.12. Operations semaphore/publish-lock state is initialized only inside the active event loop, preserving the declared Python 3.9 support.
- Real Feishu private-chat acceptance passed on 2026-07-11: `/hfc doctor` produced no gray native unknown-command reply; localized details and two consecutive rechecks (including the background successor) ACKed in 156–201 ms without a target-callback timeout toast and updated the same card; sandboxed two-step safe repair, card-triggered Gateway restart, and the normal completed-card footer passed with zero sidecar send/update failures.
- V3.9.1 regression coverage includes completed-answer boundaries, interrupted terminal ordering, asynchronous model-picker callbacks, loopback no-proxy behavior, marker-only recovery, and refusal of unknown edits.
- V3.9.1 automated release gate: `1198 passed, 3 skipped` on both Python 3.9 and Python 3.12, followed by `git diff --check`.
- V3.10.0 bare `/resume` picker tests cover the original Hermes handler, group initiator, topic metadata, expired/invalid state, fail-open behavior, and immediate ACK.
- V3.10.0 footer tests prove only the escaped model label changes color; element ids, field order, separators, text size, and non-completed states remain unchanged.
- V4.0.0 combines Hermes tool names and `tool.updated.detail` into deterministic non-completed Header action summaries and streams public `thinking.delta` independently in the body; final `answer.delta` remains the primary body content.

## Required Pre-release Checks

```bash
python3 -m pytest -q
python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --hermes-dir ~/.hermes/hermes-agent --explain
python3 -m hermes_feishu_card.cli install --hermes-dir ~/.hermes/hermes-agent --yes
python3 -m hermes_feishu_card.cli restore --hermes-dir ~/.hermes/hermes-agent --yes
```

Real Feishu integration must use local config or environment variables for `FEISHU_APP_ID` and `FEISHU_APP_SECRET`. Do not commit App Secret, tenant token, real chat_id, or sensitive screenshots. Public screenshots must be checked for secrets and private conversation content before being added to the repository.

## V4.4.1 Release Gates

- Focused render/config/hook-runtime tests: `726 passed`; renderer and mention compatibility retest: `221 passed`; server reasoning configuration integration: `2 passed`.
- Documentation and version metadata: `101 passed`. Regular-wheel, full-suite, GitHub CI, and release-asset outcomes are recorded at the final release gate.
- Real Feishu/Lark private/group, topic restart recovery, multiplex profiles, and mobile/desktop approval readability require retesting. V4.4.0 smoke is not reused as evidence for the current candidate.

## V4.4.0 Release Gates

- Dynamic `COMMAND_REGISTRY` loading on Hermes `v2026.8.27` / `0.20.6` and isolated `main@4f225435`: **passed**, discovering 66 Gateway commands on current main, including `/bg`, `/btw`, `/plan`, and Gateway `/busy`.
- Gateway/Base/Cron patch apply, idempotent reinstall, and byte-for-byte restoration on current main: **passed**.
- Three-level capability navigation, safe copied-event dispatch, group initiator/original-chat binding, state-changing-command rejection, KPI full-text preservation, real backlog depth, and adversarial-table fallback: **focused automation passed**.
- Full pytest **`3356 passed, 5 skipped`**, `git diff --check`, sdist/wheel, and clean Python 3.12 `site-packages` package/distribution/CLI provenance: **passed**.
- Real Feishu private/group `/commands`, category/detail/back navigation, the safe `/status` quick action, private `/context` empty and populated usage views, and ordinary streaming completion footers: **passed on 2026-08-31**. The `4.4.0` wheel ran with official Hermes `v2026.8.27` / `0.20.6` in an isolated CLI environment. Final sidecar state was `healthy / runtime_ready`, with events `14/14`, sends `3/3`, updates `43/43`, and zero rejections, send/update failures, or profile mismatches. Changed-operator rejection remains automation-backed because the test group had one human operator.
- Release PR, exact merge SHA, annotated tag, public install, and Release assets/checksums: **pending explicit release approval**.

## V3.9.0 Manual Acceptance Progress

- Existing-container Docker: fresh install, pinned upgrade, known-safe corrupt-marker auto-repair, user-edit refusal, main/child profile endpoint mapping, and final `doctor`. **Pending acceptance**.
- Real Feishu private chat: `/hfc doctor`, localized details, recheck, a second click from the background successor, same-card PATCH, sandboxed two-step safe repair, card-triggered Gateway restart, and the normal footer snapshot. **Passed on 2026-07-11**.
- Real Feishu cron: a no-agent one-shot result reached a normal completed card; sidecar event receive/apply/card-send metrics succeeded with no fallback. **Passed on 2026-07-11**.
- Profile route mismatch: a temporary invalid `HERMES_FEISHU_CARD_PROFILE_ID` produced a redacted `profile_unknown` route chain, and removing the temporary environment restored the default profile without changing persistent config. **Passed on 2026-07-11**.
- V3.10.0 real Feishu `/resume`: private chat, group initiator, topic placement, and same-card PATCH passed; changed-operator rejection remains automation-backed because the test group had one human participant.

Acceptance also exposed an upstream Hermes `cron run` status-reporting bug: a successful finite one-shot can print `Ran now: failed` because Hermes re-reads `last_status` after the completed job record has already been deleted. This does not indicate a card-delivery failure; the acceptance decision uses the matching Feishu card, sidecar metrics, and saved cron output. The plugin deliberately does not add another source patch for Hermes `tools/cronjob_tools.py` just to mask this upstream CLI issue.

## V3.9.1 Release Gates

- Python 3.9 / 3.12 full automation: **passed (`1198 passed, 3 skipped`)**.
- `git diff --check`: **passed**.
- Real Feishu focus: model-picker callbacks, interrupted terminal cards, and completed-answer preservation follow the [Feishu acceptance checklist](wiki/feishu-acceptance.md); public evidence remains redacted.
- Release assets: verify macOS, Linux, Windows, and checksums after tagging.

## V3.10.0 Release Gates

- Focused interaction/installer/render matrix: **passed (`416 passed`)**.
- Python 3.9 / 3.12 full automation: **passed (`1216 passed, 3 skipped`)**.
- Real Feishu: private chat, group initiator, topic same-thread update, and footer passed; changed-operator rejection is covered by automation.
- `v4.0.0`: **released on 2026-07-12**. The release-assets workflow succeeded; the macOS, Linux, Windows, and checksums assets were complete and checksum-verified; installation from the public tag reported version `4.0.0` and the CLI started successfully.
- `v3.10.0`: **released on 2026-07-11** with all four assets verified.

## V4.0.1 Release Gates

- Issue #106 data-flow regression, normal/queued completion, and V4.0.0 hook-upgrade tests: **passed**.
- Hook/patcher/install/server hot-path matrix: **passed (`509 passed`)**.
- Hermes `extract_media()` verification: **passed**, preserving the media path with an empty native-visible text body.
- Full automation: **passed (`1257 passed, 3 skipped`)**; `git diff --check` passed.
- Local package smoke: **passed**. The sdist and wheel built successfully, and a clean venv imported version `4.0.1`.
- V4.0.1 public installation and Release assets: **passed**; all four assets were present and checksum-verified.

## V4.0.3 Release Gates

- Stale-hook exact media-text deduplication, one-shot consumption, media preservation, and sidecar fail-open regressions: **passed**.
- Hook/patcher/install/server hot-path matrix: **passed (`513 passed`)**.
- Full automation: **passed (`1269 passed, 3 skipped`)**; `git diff --check` passed.
- Local package: **passed**. The sdist and wheel built successfully, and a clean venv imported version `4.0.3` from `site-packages`.
- Public installation and Release assets: **pending post-tag verification**.

## V4.0.2 Release Gates

- Recovery/install regression matrix: **passed (`121 passed`)**.
- Real local upgrade from an older owned hook: **passed**. Recovery emitted `run.py: reapplied current hook`; doctor reported a complete, consistent install state; Gateway and sidecar resumed.
- Issue #107 opt-in quota footer: **passed**. The server/render/subscription-usage focused matrix reported `237 passed`; a read-only call through the local Hermes native API returned and formatted both Session and Weekly windows.
- Full automation: **passed (`1266 passed, 3 skipped`)**; `git diff --check` passed.
- Local package: **passed**. The sdist and wheel built successfully, and a clean venv imported version `4.0.2` from `site-packages`.
- Public installation and Release assets: **pending post-tag verification**.

## V4.0.0 Release Gates

- Session/render/status focused tests: **passed (`139 passed`)**.
- Server/hook/model-picker hot-path matrix: **passed (`341 passed`)**.
- Private/group real-Feishu four-state acceptance: **passed on 2026-07-12**. Running, waiting, failed, and completed states updated one card in place; runtime action summaries remained independent from public interim output; non-completed footers contained status only; completed cards kept the native reply quote without a duplicate Card JSON Header; no gray native duplicate or callback timeout appeared.
- Real Feishu `/model`: **passed on 2026-07-12**. Provider and model data came directly from the same upstream Hermes CLI picker list; provider navigation, Back, model switching, and same-card result updates all succeeded.
- All four public screenshots: **passed privacy and visual review**, retaining only redacted real-Feishu card regions.
- Full automation: **passed (`1252 passed, 3 skipped`)**; `git diff --check` passed.
- Local release-package smoke: **passed**. The sdist and wheel built successfully, a clean Python 3.12 venv imported version `4.0.0`, and the Hermes `v2026.7.7.2` doctor confirmed runtime import, streaming, and install state.
- Verify macOS, Linux, Windows, and checksums assets after tagging.

The `v3.9.0` release-assets workflow publishes four assets: the macOS tarball, Linux tarball, Windows zip, and checksums file: `hermes-feishu-card-v3.9.0-macos.tar.gz`, `hermes-feishu-card-v3.9.0-linux.tar.gz`, `hermes-feishu-card-v3.9.0-windows.zip`, and `hermes-feishu-card-v3.9.0-checksums.txt`.

## V4.3.8 Release Gates

- Issue #244: `setup` defaults to the owned persistent service only when `service.manager=auto|systemd-user`, the user manager works, and linger is already enabled. Every other environment gets an explicit warning, transient fallback, and exact `enable` command; `--transient` explicitly opts out.
- Issue #245: an authenticated out-of-band card-action completion does not advance the `/events` transport `last_sequence`. The second batch-clarify request still passes strict identity and monotonic-sequence validation, and the first callback card is snapshotted under the session lock.
- PR #242: remote Feishu/Lark HTTP honors proxy environment variables while loopback/private/link-local/unspecified addresses bypass them; original-author commit `c25e2c4c36d5bc795b3c92df6796e7c971e9dba4` remains independent.
- Focused proxy client: **`81 passed`**; session/hook/server: **`937 passed`**; persistent/process/install: **`649 passed, 5 skipped`**; fresh normal-wheel process lifecycle: **`8 passed`**.
- Full pytest in a fresh Python 3.12 normal-wheel environment: **`3343 passed, 6 skipped in 690.84s`**; `git diff --check`: **passed**. Release-candidate CI, exact merge SHA, annotated tag, public tagged install, and Release assets/checksums remain final gates.
- Real Feishu/Lark client smoke and real Linux systemd-user + linger host smoke: **not run**. Automation, mocks, and CI are not represented as real-platform acceptance.

## V4.3.7 Release Gates (Historical Record)

- Issue #240 / PR #241: the exact matchers for Base `filter_media_delivery_paths` / `filter_local_delivery_paths` must accept both the legacy single-positional-argument call and the new call with exactly `session_key=session_key`, avoiding `exact_delivery_contract: missing_or_unsupported`.
- Extra, wrong, or unpacked keywords, wrong values, and missing or extra positional arguments must all fail closed. Apply/remove/restore must remain idempotent and byte-exact.
- Exact PR head `5e75650b0f147a24e65d5f0e499fe8b5a3f8f22f`: focused regression **`460 passed, 1 skipped`**; all six adversarial call shapes were rejected; real Hermes source at `82b32f32ef` passed apply/idempotent/remove roundtrip verification.
- Full pytest in a fresh Python 3.12 regular-wheel environment **`3330 passed, 5 skipped in 569.93s`**; `git diff --check` **passed**.
- All 12 GitHub checks on PR #241 passed; exact merge `7fcf3cbd67d3a5100739e9e3d3d7cdcce080cb62`. Release-candidate CI, exact release merge, annotated tag, public tagged install, and Release assets/checksums: **pending the final gates**.
- Real Feishu client smoke: **not run**. This fix only changes installer AST-contract recognition and does not alter Feishu API/card runtime behavior; automation is not represented as platform acceptance.

## V4.3.6 Release Gates (historical)

- Issue #237 / PR #238: an unanchored topic path must not use `thread_id` as the create API's `receive_id_type` or `receive_id`; the actual request must target the parent `chat_id`. A path with `reply_to_message_id` continues using the reply API and `reply_in_thread`.
- PR #228: approval/clarify cards and the opt-in completion notification may `@` mention the requester. `card.mentions_in_cards: false` must override per-kind and completion settings. With `completion_notify.mention: false`, a system/background turn without a sender sends a plain completion notification; mention-enabled delivery still rejects a missing or malformed `open_id`.
- The schema 2.0 streaming card remains the only PATCH owner and legacy interaction cards remain auxiliary. Native-handoff route/UUID identity remains bound to the logical topic even when the actual unanchored create falls back to the parent chat.
- Exact feature/fix merges: PR #238 `199d0390269693e74d1ff130cb7b4ecc4570dcfe`; PR #228 `69f47123611bb1639e74d9a076212ce621322805`.
- Existing regression evidence: #237 full pytest in a disposable regular-wheel environment **`3283 passed, 5 skipped`**; #228 final-combination related units **`225 passed`**, server integration **`324 passed`**, the two new completion regressions separately **`2 passed`**, and all 12 CI checks on the final rebased head passed.
- v4.3.6 release candidate: `git diff --check` **passed**; full pytest in a fresh Python 3.12 regular-wheel environment **`3325 passed, 5 skipped in 560.94s`**; PEP 517 sdist/wheel, package/distribution `4.3.6` from isolated `site-packages`, the single plugin entrypoint, all 24 slices, and the main CLI plus `enable/disable --help` are verified.
- Release PR CI, exact release merge `a2a244659f198ecd57c862455d3f4d658a827b66`, annotated tag, public tagged install, and Release assets/checksums: **completed**.
- Real Feishu: the Issue #237 reporter verified that invalid `thread_id` creation returns `99992402`, while `chat_id` creation and the reply API succeed, and reported successful creates after a local hotfix. Independent maintainer client smoke in this cycle: **not run**. Warning throttling is outside this release.

## V4.3.5 Release Gates (historical)

- PR #235: the Hermes v2026.8.3 Feishu adapter exposes `edit_message(chat_id, message_id, content, *, finalize=False)` without `metadata`; when card routing does not take ownership, the HFC wrapper may remove only that wrapper-owned internal keyword before calling the original method.
- If the original method explicitly accepts `metadata` or `**kwargs`, forwarding must remain intact. Unrelated unknown keywords must not be swallowed and must still raise `TypeError` from the original method.
- Independent direct regressions: **passed (`4 passed`)**. Hook/server hot-area suites: **passed (`841 passed`)**. Full pytest on the exact PR head: **passed (`3279 passed, 6 skipped in 599.42s`)**.
- Focused v4.3.5 docs/package/native-provenance gate: **passed (`99 passed`)**. Full pytest in a disposable wheel environment: **passed (`3280 passed, 5 skipped in 555.86s`)**. `git diff --check`: **passed**.
- PEP 517 sdist/wheel and fresh Python 3.12 wheel-only provenance: **passed**. Package/distribution `4.3.5`, isolated `site-packages` import, the single Hermes plugin entrypoint, all 24 provenance slices, and the main CLI plus `enable/disable --help` are verified.
- PR #235 HEAD `5b3bf428eb688df4b95607cba1a4ce50e2eeb8d0`: Tests run `32719244038` attempt 3 and CodeQL run `32719244032` **passed**. Attempts 1 and 2 failed only because the fixed Hermes fixture clone received GitHub HTTP 429; the third attempt passed the fixture and every platform job.
- Exact PR merge `d56555bf9e716de67ed14f8ed992df1ec55cea21`, release merge `7829e51c4c7851aa09347e56bb8c2a7136c4b0cb`, annotated tag, public install, and Release assets/checksums are complete.
- This cycle does not change card ownership, thread placement, callback authentication, Feishu API payloads, Hermes patch ownership, or the archived `legacy/` runtime.

## V4.3.4 Release Gates (historical)

- PR #229: the runtime interaction listener bind path must not invoke reverse DNS; its `serve_forever` thread must be a daemon so a short-lived command can exit without explicitly calling `close()`.
- Issue #233: a valid `manifest_version: 3` Hybrid install must be checked through the V3 runtime binding, plugin entrypoint, and fixed-tag inspector and reported as `installed`; no Legacy install diagnosis, recovery, or integrity-repair planner may run.
- V3 phase/config/target/backup/runtime-identity drift must fail closed with a V3-specific finding, must not expose Legacy automatic repair, and must direct operators to the official V3 restore/reinstall flow.
- The hosted-macOS blocked-delivery close regression uses a Future deadline to verify bounded completion instead of including runner scheduling overhead in a raw `<0.25s` wall-clock assertion. The production timeout is unchanged.
- Combined #229/#233/diagnostics/CLI/macOS-timing regressions: **passed (`191 passed`)**. Full pytest in a disposable 4.3.4 venv: **passed (`3275 passed, 6 skipped in 634.95s`)**. `git diff --check`: **passed**.
- PEP 517 sdist/wheel and fresh Python 3.12 wheel-only provenance: **passed**. Package/distribution `4.3.4`, isolated `site-packages` import, the single Hermes plugin entrypoint, all 24 provenance slices, and the main CLI plus `enable/disable --help` are verified.
- PR #234 candidate HEAD `435ea4e355719e0f2d904cf1bac986ff18f70876`: Tests run `32710110323` (10 jobs) and CodeQL run `32710110375` **passed**. Exact merge `2f1abcfcad50997c615103e3cdf1302c61f94c91`, tag, and Release assets/checksums are complete.
- This cycle changes no Feishu card/API delivery semantics and sends no additional real Feishu test message. It does not replace V4.3.3's outstanding first-reply thread client acceptance.

## V4.3.3 Release Gates (historical record)

- When the first reply has no concrete `thread_id` but has explicit `reply_in_thread=true` and a verified `om_` anchor, the streaming card, ordinary/repeated/runtime-admission interactions, and opt-in completion notification must remain in one thread.
- `send_text_message()` with either `reply_in_thread=true` or a non-empty `thread_id`, but without `reply_to_message_id`, must reject before token/API work and must not post a top-level fallback; the default path with no thread-placement intent remains compatible.
- Local regressions and full pytest: **passed (`3267 passed, 6 skipped`)**. `git diff --check`, sdist/wheel builds, fresh Python 3.12 wheel-only provenance, the single Hermes plugin entry point, all 24 provenance slices, and CLI help smoke: **passed**.
- Tests run `32657674121` (10 jobs) and CodeQL run `32657674120` for PR #232 candidate HEAD `f7de533d67f9e50afcd2c4d80fad89b572054605`: **passed**.
- The exact merge SHA, public tag/install, and Release assets/checksums remain recorded during publication; real Feishu/Lark client acceptance is currently unverified.

## V4.3.2 Release Gates (historical record)

- Issue #227: the original schema 2.0 streaming message must remain the `FEISHU_MESSAGE_IDS_KEY` owner. A newly sent legacy interaction card receives callbacks only and never becomes a schema 2.0 PATCH target.
- Direct select, custom-input form, runtime admission, repeated interactions, and expiry must return same-dialect legacy terminal cards. If the Gateway receives a schema 2.0 callback card, it returns a success toast instead of a raw callback card.
- The dialect-aware fake must reject cross-dialect PATCH operations like Feishu. Every later answer/thinking/tool/terminal update targets only the original schema 2.0 message.
- Combined renderer/hook/server/Feishu SDK compatibility regression: **passed (`932 passed, 1 skipped`)**; `git diff --check`: **passed**.
- Full pytest: **passed (`3253 passed, 5 skipped in 413.97s`)**. PEP 517 sdist/wheel, fresh Python 3.12 + `lark-oapi 1.6.8` wheel-only `site-packages` provenance, package/distribution `4.3.2`, unique Hermes plugin entrypoint, all 24 slices, and main CLI/`enable`/`disable` help: **passed**.
- Exact merge SHA, remote CI, annotated tag, public install, Release assets/checksums, and real Feishu direct-choice/custom-input-form acceptance: **recorded during publication**.

## V4.3.1 Release Gates

- Issue #216: a real Feishu click must reach the Hermes WebSocket card-action channel, carry the exact profile to the sidecar, and wake the original pending handle through the signed listener. Later answer/reasoning deltas for the same turn must keep PATCHing the latest card without another user message.
- Explicit `card.interaction_mode: text` must decline runtime callback ownership before session mutation. Hermes' native interceptor consumes the first numbered/text reply without a second waiter or stale card.
- PR #226: persistent enable accepts exact `python-sha256:` identity; systemd `WorkingDirectory` rejects relative/control-character input and safely handles `%`/backslashes; tokenless health returns an explicit empty hash while token-bearing health returns SHA-256 only.
- Two physical clicks through fixed Hermes `v2026.8.3` and a real Feishu WebSocket reached the listener and resolved, after which the card displayed the next result. Acceptance records retain no real identifiers, tokens, answer text, or screenshots.
- Both README contributor lists must reconcile historical releases, merged/absorbed PRs, accepted issue evidence, and commit/co-author records. GitHub's Contributors graph uses only real authorship; no synthetic commits or attribution are allowed.
- Full pytest: **`3245 passed, 6 skipped in 425.58s`**. Sdist/wheel plus fresh Python 3.12 wheel-only venv version, `site-packages` origin, unique plugin entrypoint, 24 slices, and CLI help: **passed**.
- Diff-check, exact merge SHA, remote CI, annotated tag, public install, and Release assets: **recorded during publication**.

## V4.3.0 Release Gates

- The fixed Hermes `v2026.8.3` / commit `3c27eb6234bf91b8ceee9e9071591b31e9b148cb` probe must jointly verify source hashes/call-site slices, runtime Python, entrypoint/distribution origin, and real PluginManager subprocess evidence. A version string or hook-name list is not capability proof.
- Hybrid must detect exactly 17 patch groups across seven targets and compile every file. Repeated install must preserve the manifest hash; restore must leave a Git-clean Hermes checkout, recover the exact pre-install config SHA-256, and remove ownership evidence.
- Interaction callbacks must wake the original Hermes pending handle/future directly. Sidecar listener POST and Feishu create/PATCH must hold no session/message lock; event-id fence, terminal/native handoff, expiry, session replacement, and caller-cancellation attacks must pass.
- Persistent `enable` must require `Linger=yes`, safely migrate a verified transient owner, bind mode-`0600` unit/manifest by SHA-256, and retain ownership when shutdown fails. `disable` fails closed on drift.
- Completed focused evidence: V3 installer/restore/scripts `340 passed, 5 skipped`; persistent process/CLI loopback `302 passed`; real fixed-tag install/idempotence/restore passed end to end.
- Full pytest: **`3227 passed, 6 skipped in 378.84s`**. Sdist/wheel, fresh Python 3.12 isolated-`site-packages` provenance, exactly one Hermes plugin entrypoint, all 24 provenance slices, and the main CLI plus `enable/disable --help`: **passed locally**. The post-commit gate still reruns `git diff --check` and docs/package tests.
- V4.3.0 classified Issue #216 as a platform zero-event boundary. Later real-world retesting exposed an additional local profile/callback/streaming-resume gap, fixed separately in V4.3.1. PR #203 changes only archived `legacy/` and is excluded from the active runtime.
- Exact merge SHA, remote CI, annotated tag, public tag/install, and Release assets: **recorded during publication**.

## V4.2.12 Release Gates

- PR #206 covers the default, `allow_permanent=false`, `allow_session=false`, and `smart_denied=true` approval capability matrix. Approval accepts only protocol choices declared by the current card by default, while clarify retains custom input.
- The sidecar rejects forged fixed choices, approval custom forms, and truthy-string capabilities. Rejection keeps the interaction pending, while token/chat/operator/expiry/idempotency boundaries remain unchanged.
- With the reasoning timeline enabled, PR #205 keeps the same collapsed zero-tool panel in running, completed, and failed cards with an explicit empty state. `show_reasoning=false` retains the plain tool summary, and raw thinking remains hidden.
- GitHub multi-platform CI passed after both PRs were updated onto the same main. The merged runtime baseline full suite reported **`2481 passed, 6 skipped`**.
- V4.2.12 candidate gates: docs/package **`94 passed`**, focused matrix **`830 passed`**, and full pytest **`2481 passed, 6 skipped`**; sdist/wheel, clean-venv `site-packages` provenance, CLI help, and `git diff --check`: **passed**.
- This cycle sends no additional real Feishu test message. PR #205's real-Feishu result is contributor evidence; PR #206 and the combined result use automation, independent adversarial checks, and multi-platform CI and are not represented as a maintainer client smoke.
- Exact merge SHA, annotated tag, public tag/install, and Release assets: **recorded during the publication flow**.

## V4.2.11 Release Gates

- The Issue #202 regression first observed that the predecessor received no final PATCH, then verified the green “moved to the interaction card” header/summary, visible content and tool preservation, and removal of transient runtime state and pending controls.
- Repeated interactions final-PATCH every predecessor once. Old pending tokens and buttons do not remain, and only the newest card receives callbacks and later updates.
- Replacement-send failure restores the pre-request session. Exhausting every predecessor PATCH attempt still promotes the replacement and records only the existing redacted update metrics/diagnostics.
- Animation cancellation completes before predecessor PATCH, and canonical `turn_id` sessions retain their per-session card configuration.
- Session/render/server/clarify focused matrix: **`450 passed`**; `git diff --check`: **passed**.
- Full pytest in the isolated v4.2.11 candidate: **`2478 passed, 6 skipped`**.
- Local sdist/wheel and fresh-venv candidate-wheel `site-packages` provenance/CLI smoke: **passed**.
- PR CI, exact merge SHA, public tag/install, and Release assets: **recorded during the publication flow**.

## V4.2.10 Release Gates

- The sidecar request proof binds the HTTP method, canonical path, and raw body under the separate `hfc-sidecar-request-v1` domain. Missing, expired, cross-method/path/body, and replayed proofs fail closed; rejection responses and metrics contain no signatures, identifiers, bodies, or choices.
- Default loopback deployments remain compatible. With non-loopback event authentication enabled, `/card/actions`, `/interactions/{id}`, and `/messages/{id}/summary` verify the proof before parsing or returning state.
- The sidecar owns the interaction deadline from receipt time. Late direct buttons and form submits return an expired state, periodic expiry refreshes the original card, expired pending state no longer blocks cleanup forever, and Gateway poll timeout sends one distinct `interaction.failed` without replaying `interaction.requested`.
- Session/lifecycle/render/hook unit regressions: **`556 passed`**; full server/clarify integration regression: **`297 passed`**; CI workflow contracts: **`15 passed`**.
- GitHub Actions runs full pytest on Ubuntu Python 3.9/3.10/3.11/3.12, Windows 3.12, and macOS 3.12 while retaining Feishu SDK, PowerShell installer, and Docker Compose smoke jobs. Official Actions are pinned to verified immutable SHAs for Node 24-capable releases.
- CodeQL scans Python on push, pull request, and weekly schedule; Dependabot checks pip and GitHub Actions weekly.
- Full pytest in the isolated v4.2.10 runtime: **`2473 passed, 6 skipped`**. Exact PR merge, detached merge-SHA verification, public tag/install, and Release assets are recorded during the release process.

## V4.2.9 Release Gates

- The original PR #196/#199 commits retain authorship. Additional regressions cover submission failure, duplicate resolution, callback token/chat authentication, old Hermes callback signatures, single-attempt `/events`, and redacted diagnostics.
- Focused hook/runtime/render/server/patcher/install matrix: **`1161 passed, 2 skipped`**.
- Full pytest with an isolated v4.2.9 runtime: **`2452 passed, 6 skipped`**; `git diff --check`: **passed**.
- Local sdist/wheel builds passed with metadata at `4.2.9`. A fresh venv installed the wheel and public dependencies, reported package/distribution versions of `4.2.9`, imported from venv `site-packages`, and exited 0 for the console entrypoint and CLI help.
- GitHub Actions (Python 3.9/3.12, Feishu SDK, PowerShell, Docker): **passed** ([run 31318602152](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/31318602152)).
- Exact merge SHA `dc332212c14423abb3b42f524dce46ff0ff28479`; annotated tag `v4.2.9` and [Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v4.2.9): **released on 2026-08-09**.
- Release-assets [run 31319394583](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/31319394583): **passed**; the macOS, Linux, Windows, and checksums assets are uploaded with GitHub SHA256 digests.

## V4.2.8 Release Gates

- A fresh macOS install from the public v4.2.7 tag imported from the target venv `site-packages` and installed a consistent hook, but process credentials were not written to the selected `.env`; a later isolated doctor therefore reported missing credentials.
- TDD regressions cover process-credential persistence, a secret containing spaces, POSIX mode `0600`, and log redaction across `install.sh`, `install-docker.sh`, and `install.ps1`.
- Installer and CLI install/setup/restore focused matrix: **`285 passed, 5 skipped`**; version/docs contracts: **`92 passed`**; the dynamic PowerShell case runs in Windows GitHub Actions.
- Full pytest in an isolated v4.2.8 test venv: **`2431 passed, 6 skipped`**; `git diff --check`: **passed**.
- Local sdist/wheel builds passed with metadata at `4.2.8`. A second fresh venv installed the wheel and public dependencies, reported package/distribution versions of `4.2.8`, imported from venv `site-packages`, and exited 0 for CLI help.
- PR CI, exact merge, public tag/install, and all four Release assets are recorded during the release process.

## V4.2.7 Release Gates

Accepted candidate SHA: `18d0346bd041a7c7b2c049ace116b78c720bad98`

- Candidate GitHub Actions [run 30966895426](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/30966895426) passed all five jobs: Python 3.9/3.12, PowerShell installer, Docker Compose runtime smoke, and Feishu SDK compatibility.
- Candidate focused regressions: **`632 passed, 4 skipped`**; candidate full pytest: **`2429 passed, 5 skipped`**; `git diff --check`: **passed**.
- Windows-specific coverage includes 30-second SDK/HFC probes, POSIX manifest writes, exact legacy backslash reads, escape rejection, parent `HERMES_HOME`, detached-runner PID rebinding, and PowerShell non-zero exit propagation.
- Release-branch version/docs contracts: **`92 passed`**; Windows/installer/CLI focused matrix: **`628 passed, 4 skipped`**; full pytest: **`2429 passed, 5 skipped`**; `git diff --check`: **passed**.
- Local sdist/wheel builds passed with metadata at `4.2.7`. A fresh venv installed the wheel and public dependencies, reported package/distribution versions of `4.2.7`, imported from venv `site-packages`, and exited 0 for CLI help.
- Exact merge SHA, public tag/install, and all four Release assets are recorded during the release process.

## V4.2.6 Release Gates

Accepted candidate SHA: `a03838da5f012aae112ca549cbe03727a91b578a`

- As of 2026-08-04 15:00 CST, Issues #187, #188, #189 and PR #190 had no valid candidate retest report containing Hermes/HFC versions, a real scenario, and a concrete result.
- Candidate GitHub Actions [run 30880188189](https://github.com/baileyh8/hermes-feishu-streaming-card/actions/runs/30880188189) succeeded with all five jobs green.
- Candidate full pytest: **`2412 passed, 5 skipped`**.
- A local bare Feishu private-chat `/update` against Hermes `0.20.0` passed preflight, confirmation-card, and independent update execution; the sidecar returned ready and version reporting showed `0.20.0`. Public records contain no local paths, credentials, or chat ids.
- Release-branch version/docs contracts: **`103 passed`**; focused release matrix: **`1201 passed, 1 skipped`**; full pytest: **`2413 passed, 5 skipped`**; `git diff --check`: **passed**.
- Local sdist/wheel build passed; wheel and sdist metadata both report `4.2.6`, while isolated `-I` import resolves from venv `site-packages` with package/distribution versions both at `4.2.6`.
- PR CI, exact merge, public tag/install, and all four Release assets are recorded during the release process.

## V4.2.5 Release Gates

Accepted runtime SHA: `7f87beed8a37a365c10483f3d638092fd422782e`

- Candidate acceptance record: **2026-08-02 11:31:18 CST (Asia/Shanghai)** on **macOS arm64**; the read-only Hermes checkout identified itself as `v2026.7.30-15-gce6dd1a65-dirty` and was not modified by this flow; the isolated HFC package and runtime both reported `4.2.5`.
- Real-Feishu topic acceptance: **passed**. Exactly two A/B cards were created in the most recent still-valid topic of the existing test group. The sidecar reported `events_applied=4/4`, `feishu_send_successes=2/2`, `events_rejected=0`, and zero send/update failures. After A was successfully created, the harness stopped early because it incorrectly treated the hook Boolean as the sole delivery result; recovery reused that same A card and never sent a third card. A's first marker and its late marker after B started were both PATCHed successfully; B's first delta and terminal were completed through the candidate hook/sidecar; B's indexed summary contained no A/late marker; and the two cards had distinct IDs.
- Feishu history returns the initial body snapshot for a PATCHed card, so that stale body is not used as current-content evidence for A. A's state transition is instead supported by two successful PATCH calls, `updated=true`, an advanced `update_time`, and zero update failures. B is supported by the sidecar summary and event/send/update counters. This limitation is retained as an explicit evidence boundary and does not change the two-card, no-cross-write conclusion.
- Accepted-runtime automation: runtime focused `938 passed`; maintenance focused `223 passed`; installer/release focused `159 passed, 3 skipped`; disposable maintenance smoke `6 passed`; full pytest **`2400 passed, 5 skipped`**. The first sandboxed runtime-focused attempt failed only because binding ephemeral `127.0.0.1` ports was denied; the unchanged command passed completely when rerun outside the sandbox under the project's loopback-test authorization.
- Named regressions cover all nine audit IDs across quoted turns, maintenance ownership/binding/drain, doctor actions, installer pinning, and the config marker.
- A failed `latest` lookup must stop before pip/setup/doctor and Docker state mutation; explicit `main` is the only moving ref.
- Release Assets must run `resolve-release -> reusable exact-commit tests -> package`, with full annotated-tag verification before build and again before upload.
- Candidate full pytest, compileall, package provenance, disposable maintenance smoke, real acceptance, PR CI, exact merge, public tag/install, and all four assets are **recorded only as each release gate completes**.

## V4.2.4 Release Gates

- `message.started` must use the real incoming message ID so every quoted reply opens an independent card, falling back to the reply anchor only when the ID is missing: **patcher unit regression passed**.
- The sidecar must bypass reply aliases only for a new turn; later `answer.delta` and other stream events must still update that turn's new card: **real HTTP `/events` integration regression passed**.
- Full pytest: **`2311 passed, 5 skipped`**; `git diff --check`, sdist/wheel, and clean isolated Python `site-packages` package/distribution/CLI provenance: **local candidate gate passed**. PR CI, exact merge SHA, public tag/install, and Release assets: **verified during release**.
- The PR #177 contributor reports that the consecutive quoted-reply scenario passes in real Feishu. Post-tag runtime retesting remains a user-side acceptance item and does not replace automation or exact-SHA gates.

## V4.2.3 Release Gates

- The WebSocket hook must forward `update_evidence_fingerprint` unchanged from the card value to the sidecar; the missing-field regression was observed red before the fix and green afterward: **passed**.
- The related hook/runtime/server/Feishu SDK matrix reports **`670 passed, 1 skipped`**. Full pytest reports **`2309 passed, 5 skipped`**; `git diff --check`, sdist/wheel, clean Python 3.12 `site-packages` provenance, PR CI, exact merge SHA, public tag/install, and Release assets: **release flow passed**.
- Real acceptance must observe a sidecar update attempt, the original-card transition, and proof that cancel did not start the updater; a click or Gateway action log alone is insufficient.
- Local-candidate real Feishu cancellation acceptance: **passed (2026-08-01)**. The new card reported HFC 4.2.3 and the original card reached “cancelled / Hermes update not executed”; sidecar reported `feishu_update_attempts=1`, `successes=1`, and `failures=0`, Hermes HEAD was unchanged, `update.log` remained at 2026-07-31 15:01:52, and no updater or maintenance-run process existed. Repeat after installing the public tag.

## V4.2.2 Release Gates

- The native card action must return its empty acknowledgement first, then let the sidecar asynchronously PATCH the original confirmation card; Feishu API latency must stay outside the callback deadline: **focused regression passed**.
- Cancel must persist `cancelled`, render the terminal cancellation card, and never schedule the updater; confirm must attempt the locking/preparing card transition before independent maintenance is scheduled: **related operations/server/hook-runtime matrix passed (`378 passed`)**.
- Full pytest reports **`2307 passed, 5 skipped`** on both Python 3.9 and 3.12; `git diff --check`, wheel/sdist, clean Python 3.12 `site-packages` package/distribution/CLI provenance, PR CI, exact merge SHA, public tag/install, and Release assets: **release flow passed**. The subsequent real Feishu click exposed the dropped WebSocket evidence fingerprint, so terminal cancellation did not complete and is superseded by the V4.2.3 candidate.

## V4.2.1 Release Gates

- Startup adapter installation must register the live Gateway runner before runtime control starts, and the first heartbeat must carry complete `_active_work_count()` aggregate evidence: **focused regression passed**.
- Missing, failing, negative, or non-integer aggregates remain refused and are never downgraded to zero work: **safety boundary retained**.
- Full pytest reported **`2306 passed, 4 skipped`** on both Python 3.9 and 3.12; `git diff --check`, wheel/sdist, clean `site-packages`, and the maintenance runtime: **local candidate gate passed**. PR CI, exact merge SHA, public tag/install, and Release assets: **verified during release**.

## V4.2.0 Release Gates

- Read-only inspection, 120-second confirmation binding, cancel/expiry/replay/cross-operator rejection, and the dedicated maintenance card for a bare private-chat `/update`: **automated coverage passed**.
- Exact-wheel provisioning, durable job/journal/lock, official `hermes update --yes`, same-version HFC reinstall, hook/service restoration, and `maintenance status/resume`: **automated coverage passed**.
- Unrelated tracked changes, incomplete Git state, artifact/version drift, and failed final verification stop; untracked files remain and no custom Git rollback runs: **automated coverage passed**.
- Full pytest reported **`2304 passed, 4 skipped`** on Python 3.9 and **`2303 passed, 5 skipped`** on Python 3.12; `git diff --check`, wheel/sdist, clean Python 3.12 `site-packages` package/distribution/CLI provenance, and real `maintenance provision/status` independent-runtime and runner-import checks: **local candidate gate passed**.
- PR CI, real Feishu private-chat card acceptance, exact merge SHA, public tag/install, and Release assets: **verified during release**.

## V4.1.4 Release Gates

- Remove the manifest from regular Gateway, Hermes v0.19.0 required exact Base, and optional Cron states produced by the public v4.0.14 package imported from `site-packages`; V4.1.4 official install must print `manifest: rebuilt` / `install ok` and doctor must return to `installed`: **passed isolated local reproduction**.
- Unicode comments plus all-CRLF source, native Windows relative paths, and the no-directory-fd portable install path must pass without being mislabeled as the root cause: **isolated old-package fixtures and the equivalent branch passed; reporter's real Windows confirmation pending**.
- Migration is allowed only when lenient legacy Gateway block removal exactly matches the clean backup and strict Cron/Base removal independently matches each backup; `--no-repair`, missing targets, outside-block edits, and concurrent edits before write/rollback must preserve evidence and refuse: **safety-boundary regression passed**.
- Full pytest **`2221 passed, 5 skipped`**, `git diff --check`, wheel/sdist, and isolated Python 3.12 `site-packages` package/distribution/CLI provenance: **passed locally**; PR CI, Issue #171 official Windows-flow retest, exact merge SHA, public tag/install, and Release assets: **pending**.

## V4.1.3 Release Gates

- An old plan binding for the same target can transition only after two current recovery/integrity-plan checks report installed, two checks confirm the sidecar stopped, and the fence CAS snapshot remains unchanged: **focused regression passed**.
- A different target, state drift, remaining pidfile/health, unknown legacy fence, or unverifiable plan remains fail-closed; a non-empty restart/hash fence is preserved: **safety-boundary regression passed**.
- `doctor --explain` must print complete `integrity migrate-safe` and `integrity acknowledge-review` commands without exposing paths, fingerprints, or private state evidence: **diagnostic regression passed**.
- PR #168 must select only the native text callback that calls `_stream_consumer.on_delta` and relocate an older hook: **independent review, full automation, and real Hermes source injection passed; merged with contributor authorship preserved**.
- Hermes `1a3a9de` TurnRunner source must restore 14 managed hook blocks, one of each of the six moved hooks, place status after ctx binding, remain idempotent and byte-for-byte removable, and report `supported/full`; unknown shapes must be `not safely patchable`: **regression, real-source verification, and PR #170 CI passed**.
- Combined-candidate full pytest **`2207 passed, 4 skipped`**, `git diff --check`, wheel/sdist, and isolated Python 3.12 `site-packages` package/distribution/CLI entry-point provenance: **passed locally**; Issue #158 official Ubuntu retest, Issue #169 latest-Hermes real Feishu retest, candidate CI, exact merge SHA, public tagged install, and Release assets: **pending**.

## V4.1.2 Release Gates

- The installed-plan, old-runtime hello, stale heartbeat, coordinator check, and new matching hello race must create no fence and restore ready in one cycle: **focused regression passed**.
- Generation/package mismatch, unavailable control authentication, manual-review/restart fences, and real strict repair remain fail-closed with no automatic Gateway restart: **safety-boundary regression passed**.
- Stable-wrapper detection plus the explicit fail-open fallback must prevent one call from traversing both the stable and legacy progress paths; real Hermes-configured-model runs on the local and remote MacBook Pro each rendered one timed terminal entry: **automation and dual-machine real Feishu passed**.
- Candidate full pytest **`2197 passed, 4 skipped`**, `git diff --check`, wheel/sdist, and isolated `site-packages`/CLI provenance: **passed**; exact merge SHA, public tagged install, and Release assets: **pending after merge**.

## V4.1.1 Release Gates

- A verified `installed` plan neither repairs nor writes a restart/manual-review fence while the first heartbeat is waiting/missing, and resumes normal evaluation after a matching `runtime.hello`: **candidate focused and full regressions passed**.
- `integrity acknowledge-review` requires installed + unreachable sidecar health + no pidfile; empty hash can clear an unresolvable fence while a non-empty hash keeps the restart fence until a different-runtime-id matching hello: **CLI, persistence, and restart simulation passed**.
- A legacy `0644` pidfile is tightened only inside a private owned `0700` state directory through fd identity binding; a pidfile-less process is never silently adopted/killed and requires the operator to stop the old service before rerunning: **real macOS process tests passed; Linux CI remains pending**.
- Setup installs/rechecks through the Hermes runtime venv and uses `/health` package version plus Python identity to decide whether to restart sidecar; sidecar and Gateway are then restarted manually: **local and remote upgrade acceptance pending**.
- Candidate `20b7b06`: full pytest **`2194 passed, 4 skipped`**, `git diff --check`, wheel/sdist build, isolated `site-packages` provenance, and wheel real-process tests **`8 passed`**; **CI, exact merge SHA, public tagged install, Release assets, Linux/Docker, and real Feishu remain pending release gates**.

## V4.1.0 Release Gates

- Exact/profile-scoped `bindings.native_chats`, two-stage hook/sidecar enforcement, fail-open direct-card paths, and card-based `/hfc`: **focused matrix and real card → native → card acceptance pending**.
- Default `table_overflow_mode=compact`, fenced fake-table exclusion, and a text-only terminal native handoff above 28,000 bytes using a V2 descriptor, stable UUIDs, the Hermes ledger, and delivered-then-ACK order; outside the window the exact descriptor expires while visible-marker bounded upstream recovery remains ordinary fail-open: **real seven-table and oversized-handoff acceptance pending**.
- `integrity.mode` safe/notify/off, signed `runtime.hello` / `runtime.heartbeat`, strict repair, `sidecar.restart_required`, and no automatic Gateway restart: **upgrade simulation pending**.
- All four `service.manager` modes, non-escalating `auto`, and ordinary Docker containers: **Linux manager and Docker Compose smoke pending**.
- Full pytest, `git diff --check`, build/isolated `site-packages`, exact merge SHA, public tagged install, and Release assets: **release workflow pending**.

## V4.0.21 Release Gates

- Issue #155: only an explicit `answer -> tool` boundary can archive an answer; `tool -> answer -> completed` must retain the full user-visible terminal answer: **passed focused ordering coverage (`74 passed`)**.
- Issue #147: after the completed card accepts the event, matching native media text is suppressed once, the native image still delivers, and an accepted queued notice emits no uncertain-delivery warning: **passed hook-runtime combination coverage (`277 passed`)**.
- Current README, install guidance, Docker Compose, and bilingual user guides pin `v4.0.21`; UI and configuration remain unchanged: **passed documentation gate**.
- Real Feishu image acceptance: **passed (2026-07-28)**. Observed one marker-bearing, non-running completion card plus one native image with no uncertain-delivery warning; a normal tool turn retained two answer segments in one card, with zero bot native marker duplicates.
- Final sidecar metrics were `events_received/events_applied=23/23`, 1 send success and 16 update successes; event/auth rejection, send/update failures, notice uncertain warnings, and notice update failures were all zero. Gateway Feishu WebSocket was connected, and Hermes venv site-packages was 4.0.21.
- Final local release gate: full pytest reported `1526 passed, 4 skipped in 53.56s`; `uv build` produced `hermes_feishu_streaming_card-4.0.21.tar.gz` and `hermes_feishu_streaming_card-4.0.21-py3-none-any.whl`. A clean Python 3.12 venv installed from the wheel with imports in `site-packages`, package/distribution versions both `4.0.21`, `hermes-feishu-card = hermes_feishu_card.cli:main` present, and CLI --help exit 0.
- This acceptance does not claim screenshot or desktop/mobile visual QA and does not replace real fault injection; public tagged installer and Release-asset post-tag verification remain pending.

## V4.0.20 Release Gates

- Existing-card notices return `accepted` only when `applied=true` and the asynchronous PATCH is queued; the hook treats that explicit acknowledgement as handled: **passed hook/server regressions**.
- Initial independent notice create/reply keeps the three delivery outcomes; queued work is not represented as delivered, and the request does not wait for every PATCH: **passed existing delivery regressions**.
- After PATCH retry exhaustion, `notice_update_failures` increments once and `last_update_error` retains only the exception type plus validated `status_code` / `api_code`: **passed fault-injection and redaction assertions**.
- Final full automation: **passed (`1517 passed, 4 skipped`)**; sdist/wheel, isolated `site-packages` import of `4.0.20`, the public tagged installer, and Release assets are rechecked during release.

## V4.0.19 Release Gates

- Hermes venv Python omits `--user` by default while the system-Python fallback keeps user installs: **passed installer regression**.
- Pip failures preserve their real exit status and prevent setup from running: **passed red/green regression**.
- A fresh Hermes venv without `HFC_PIP_USER` completed installation and imported the target version from venv `site-packages`: **passed real install smoke**.
- Final full automation, sdist/wheel, public tagged installer, and Release assets are rechecked during release.

## V4.0.18 Release Gates

- When the Hermes adapter uses `extra_ua_tags`, the installer checks the real SDK constructor; older adapters do not trigger installation and compatible newer SDKs are not forced backward: **passed CLI/diagnostics regressions**.
- `doctor` reports `feishu_sdk_incompatible` read-only; `setup/install` must install `lark-oapi==1.6.8` and pass the follow-up capability check: **passed red/green integration coverage**.
- A real Hermes v0.19.0 Gateway recovered `✓ feishu connected` after moving from `lark-oapi 1.5.3` to `1.6.8`; all 214 runtime packages are dependency-compatible: **passed**.
- Final full automation: **passed (`1511 passed, 4 skipped`)**; sdist/wheel, isolated `site-packages` import of `4.0.18`, the public tagged installer, and Release assets are rechecked during release.

## V4.0.17 Release Gates

- Two parallel same-name tools use distinct `call_id` values and preserve independent previews and completion events: **passed session/patcher regressions**.
- Started/completed counts once per invocation, all duration metadata is removed from details, and one duration remains on each headline: **passed session/renderer regressions**.
- Patch compilation, idempotency, and exact restore against the current local Hermes original Gateway source: **passed**; the compatibility fallback without stable callback anchors is unchanged.
- Final full automation: **passed (`1508 passed, 4 skipped`)**; sdist/wheel, isolated `site-packages` import of `4.0.17`, public tagged installer, and local runtime provenance are rechecked during release.

## V4.0.16 Release Gates

- Initial Header/body responsibilities, removal of the empty body placeholder after tool start, and unchanged final-answer/footer behavior: **passed renderer/session/server regression coverage**.
- Hermes `kwargs.duration` extraction, `duration_ms` propagation, started/completed fallback, terminal-only non-fabrication, and query/argument preservation: **passed real callback-shape smoke plus automation**.
- Final full automation: **passed (`1504 passed, 4 skipped`)**; sdist/wheel, isolated `site-packages` import of `4.0.16`, the public tagged installer, and local runtime provenance are rechecked during release.
- This patch does not claim a new Feishu client visual retest. V4.0.15 covered the real Hermes/Feishu loading and tool-state path; this delta is verified through the real callback shape and card JSON smoke.

## V4.0.15 Release Gates

- Issue #141's compact tool timeline, loading/running spinner, same-card PATCH path, stop conditions, terminal drain, and topic/reply anchors: **passed focused automation and real Hermes/Feishu model validation**.
- Read-only detection after a Hermes upgrade, `start` refusal, explicit recovery, installed state after recovery, and fail-closed user edits: **passed a temporary-fixture upgrade loop plus the local real-upgrade diagnosis**.
- Final full automation: **passed (`1498 passed, 4 skipped`)**; sdist/wheel, isolated `site-packages` import of `4.0.15`, and CLI smoke: **passed**; `git diff --check` is rerun before tagging.

## V4.0.14 Release Gates

- Non-terminal heartbeat state, same-anchor reuse, different-anchor isolation, orphaned six/nine-minute updates, and final completion: **passed focused automation**.
- Stable independent-card recovery after an unknown delivery outcome and the existing fail-open branches: **passed regression coverage**.
- The real-Feishu `v4.0.13` reproduction for Issue #142 is recorded. This candidate does not wait another real six/nine minutes and does not describe the equivalent automated replay as a client visual retest.
- Final full automation: **passed (`1488 passed, 3 skipped`)**; sdist/wheel, isolated Python 3.12 `site-packages` import of `4.0.14`, and CLI smoke: **passed**; `git diff --check` is rerun before tagging.

## V4.0.13 Release Gates

- Generic command contexts, same-card multi-feedback, concurrent single-create behavior, long Markdown, exact create/PATCH fallback, and all `/compress` branches: **passed**.
- Dedicated `/model`, bare `/resume`, confirmation, `/hfc`, Agent-turn, media, and `/update` restart-boundary regressions: **passed**.
- Real Feishu client command matrix and final desktop/mobile visual acceptance: **not run and not claimed as passed**.
- Final full automation: **passed (`1482 passed, 4 skipped`)**; `git diff --check`, sdist/wheel, and isolated Python 3.12 import/CLI smoke are verified before tagging.

## V4.0.12 Release Gates

- Focused compaction hook/session/render/server and text-size schema/merge/render/device matrices: **passed**.
- A real selected-env subprocess starts as `healthy/live`; a credential-free subprocess starts as `degraded/noop`, returns `not_sent`, and does not increase successes: **passed**.
- Automatic long-session compaction smoke and final desktop/mobile visual confirmation: **not run by release decision and not claimed as passed**.
- Final full automation: **passed (`1460 passed, 4 skipped`)**; `git diff --check`, sdist/wheel, and clean Python 3.12 import `4.0.12` also passed.
- Annotated tag `v4.0.12` points to merge commit `00a48a7`; release-assets workflow `29632908140` succeeded, and all four assets/checksums plus the public tagged installer: **passed**.

## Current Boundaries

Automated tests do not access real Feishu and do not start a real Hermes Gateway. Real integration remains a local/manual acceptance flow. After successful testing, record only redacted results; never commit credentials, real chat_id, or sensitive screenshots.
