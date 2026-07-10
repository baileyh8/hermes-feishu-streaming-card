# V3.9.0 Operations, Reliability, and Calm Card UX Design

## Summary

V3.9.0 makes Hermes Feishu Streaming Card easier to recover, configure, and operate after Hermes upgrades while giving users a visible but restrained card-experience improvement.

The release has two connected layers:

1. An operations and reliability foundation: structured diagnosis, evidence-based automatic repair, consistent installer arguments, profile routing validation, and bounded runtime cleanup.
2. A calm user-facing layer: preserve the existing card layout and footer, improve status accuracy, and add interactive operations buttons only when diagnosis or recovery is needed.

The release does not introduce a general write-action platform. Agent actions such as continue, retry, and cancel remain a separate future design.

## Evidence and Motivation

The scope is grounded in current repository work and user feedback:

- Issue #82 reports repeated `corrupt completion patch markers` during Docker upgrade despite an otherwise installed and running sidecar.
- Issue #83 shows that users do not understand how HFC profile keys map to main and child Hermes processes.
- PR #84 from @Zanetach proposes two useful directions: importing `HERMES_FEISHU_CARD_EVENT_URL` / `HERMES_FEISHU_CARD_PROFILE_ID` from `.env`, and avoiding misleading completed styling for intermediate progress handoffs.
- PR #50 identifies long-running session state that can drift or remain unbounded.
- The repository roadmap already identifies Docker lifecycle operations, profile UX, E2E fixtures, closed `FlushController` cleanup, progress status, and long-term diagnostics as the next maintenance surface.

## Product Promise

After a Hermes upgrade, an HFC user should be able to:

1. Run the installer or `setup` without first learning HFC's internal repair model.
2. Have known-safe install-state damage repaired automatically.
3. See a clear refusal instead of an unsafe overwrite when evidence is incomplete.
4. Verify which Hermes process, HFC profile, sidecar endpoint, and Feishu bot route are active.
5. Use Feishu buttons to inspect, recheck, repair, and restart when an operations card is available.
6. Keep the normal streaming-card reading experience and configured footer unchanged.

## Design Principles

- **Zero extra operations configuration:** users do not configure operator `open_id` values or a new operations allowlist.
- **Evidence before mutation:** automatic repair runs only when backup, manifest, hashes, marker state, and Hermes anchors establish a complete safe-recovery chain.
- **Fresh validation at execution:** confirmation never authorizes a stale plan; execution recomputes the plan under a lock.
- **Existing admission is the authority:** every Feishu operations click independently reuses Hermes' existing private/group message admission check.
- **Group mutations keep one owner:** in a group chat, mutation actions bind to the `/hfc doctor` initiator when available; otherwise the first mutation click claims the operation. Confirmation must come from the same Feishu operator.
- **Private-chat confirmation stays frictionless:** a repair or restart still requires two steps to prevent accidental clicks, but private chat does not add a same-operator comparison.
- **Progressive disclosure:** normal cards stay answer-first; operational detail appears only in operations cards or explicit diagnostics.
- **Layout compatibility:** existing header, main content, timeline, divider, and footer ordering remain stable.
- **Sidecar-only ownership:** no installed Hermes file is edited manually; patching and recovery remain inside HFC's installer modules.
- **Fail-open messaging, fail-closed mutation:** unsupported runtime paths keep Hermes usable, while uncertain file mutation is refused.

## Scope

### Included

- Structured diagnostic reports shared by CLI and Feishu operations cards.
- Safe automatic repair for verified corrupt or stale hook installation state.
- Interactive diagnosis, recheck, repair confirmation, and Gateway restart cards.
- Consistent installer arguments and `.env` handling across shell, Docker, and PowerShell.
- CLI profile setup and route validation for main/child Hermes deployments.
- Explicit-first card display status with a conservative fallback classifier.
- Terminal session, alias, lock, card-state, and closed flush-controller cleanup.
- Bounded zombie-session cleanup and diagnostics retention.
- Expanded fixture and E2E coverage for installation, Docker, profiles, topic, cron, actions, and long-running state.
- Explicit contribution credit for PR #84 and @Zanetach when its ideas are implemented.

### Excluded

- An official Hermes + HFC combined Docker image.
- A browser-based configuration interface.
- General Agent continue, retry, cancel, or arbitrary command execution actions.
- Native CardKit conversion for arbitrary Markdown tables.
- Cross-chat or cross-group conversation migration.
- A unified V4 action/state platform.
- Automatic scanning or modification of Hermes agent definitions.

## Architecture

### 1. Structured Diagnostics

A focused diagnostics module produces a `DiagnosticReport` rather than formatting conclusions directly in CLI code.

The report contains typed sections for:

- HFC config load and credential presence.
- Hermes root, version source, hook strategy, and verified anchors.
- Gateway and cron install-state evidence.
- backup, manifest, current-file, and expected hashes.
- marker classification: clean, owned, stale, corrupt, or user-modified.
- sidecar endpoint and health.
- profile identity source and profile-key match.
- resolved bot route and a sanitized route reason.
- runtime cleanup and recovery counters.
- findings with severity, stable code, short user impact, and available actions.

The report never includes secrets, full chat/open/message identifiers, or complete local paths in card and health output. CLI `--json` may include normalized paths only when running locally; card serialization always hashes or shortens paths.

Existing `doctor` remains read-only and formats the same report as text, JSON, or a Feishu operations card.

### 2. Recovery Planner and Executor

Recovery is split into two explicit phases:

- `plan`: read-only evidence collection and an immutable recovery proposal.
- `execute`: locked, fresh evidence collection followed by an atomic mutation when the new proposal is still safe.

A recovery proposal is automatically executable only when all applicable conditions hold:

- The backup exists and parses as the expected unpatched Hermes source.
- The manifest exists or can be reconstructed from an owned patch with verifiable hashes.
- Current file state is either the exact manifest-installed hash, a known stale unpatched upstream state, or a recognized corrupt-marker shape whose trusted preimage is the verified backup.
- Hermes version or gateway anchors select a supported hook strategy.
- Reapplying the current patch to the verified backup succeeds in memory.
- The generated file parses as Python and passes marker ownership validation.
- No unrelated user changes exist outside the owned state.

Execution builds the complete repaired file in a temporary file, validates it, then performs one atomic replace. It updates the manifest only after file replacement succeeds. The damaged current file is retained as a hash-labelled quarantine snapshot; at most three snapshots are retained per Hermes root.

An exclusive operation lock is keyed by normalized Hermes root. Repeated or concurrent requests reuse the current operation result and do not perform a second mutation.

### 3. Installer Lifecycle

The existing-container workflow remains the supported Docker boundary. HFC does not publish or maintain a Hermes image.

Shell and Docker installers accept explicit arguments in addition to existing environment variables:

```text
--config PATH
--env-file PATH
--version VERSION
--profile-id PROFILE_ID
--event-url URL
--no-repair
```

PowerShell exposes equivalent named parameters.

Argument values take priority over process environment, which takes priority over selected `.env`, which takes priority over script defaults. The scripts pass normalized values to the Python CLI rather than reimplementing recovery logic.

`setup` and `install` automatically execute an available safe recovery plan before applying the current hook. `--no-repair` disables this behavior for advanced users. `doctor` never mutates files.

The lifecycle is:

1. Resolve explicit arguments and selected `.env`.
2. Install the selected HFC package into the invoking and Hermes runtime Python environments as needed.
3. Build a diagnostic report.
4. Automatically execute a safe recovery plan unless `--no-repair` is present.
5. Apply the current hook through the patcher.
6. Start or refresh the sidecar.
7. Build a final diagnostic report and print exact remaining actions.

Gateway restart remains an explicit operation because it interrupts active Hermes work. When available in Feishu, it is a separate confirmed button after repair. CLI output provides the exact restart command when the user is outside Feishu.

### 4. Operations Cards and Actions

Operations cards reuse the current CardKit visual structure. They do not change normal answer cards.

Action buttons use Card JSON 2.0 `column_set` rows. Buttons keep their existing
order and are grouped two per row in equal-width columns. An odd final button
occupies the left column while the right column remains empty. The button grid
sits between the diagnostic summary and the existing divider, so the header,
summary, divider, configured footer, and all normal streaming-card layouts stay
unchanged. Each column contains one direct Card JSON 2.0 `button` with its
existing callback behavior; the removed JSON 1.0 `action` container is not
reintroduced.

Available actions are:

- `查看诊断`: read-only; updates the same card with sanitized findings.
- `重新检测`: read-only; generates a fresh report and updates the same card.
- `安全修复`: shown only when the current report exposes an automatically executable recovery plan.
- `确认修复`: second step; recomputes and executes the plan.
- `重启 Gateway`: shown after a successful repair when a supported restart command is available.
- `确认重启`: second step; schedules restart after returning the card callback response.
- `暂不处理` / `取消`: returns the card to a stable non-pending state.

No new operator allowlist is introduced. Each click must pass the existing Hermes Feishu adapter admission function.

Private-chat operations do not compare operator identity across the two confirmation steps. Group-chat mutation operations do: the operation binds to the `/hfc doctor` initiator when the command event provides an operator identity. If that identity is unavailable, the first `安全修复` or `重启 Gateway` click claims the operation. The matching `确认修复` or `确认重启` click must carry the same Feishu operator id. Read-only `查看诊断` and `重新检测` remain available to any user admitted by Hermes.

Action tokens bind to operation id, chat, profile, diagnostic fingerprint, action type, and expiry. Group mutation state additionally binds to an operator identity; the identity is never rendered or exposed in health output. Mutation tokens expire after 120 seconds. Read-only recheck actions may issue a fresh token from a new report.

Accepted operations actions are claimed by HFC and must not fall through to gray native unknown-action messages.

### 5. Profile Setup and Route Validation

PR #84's environment-routing direction is implemented consistently across `install.sh`, `install-docker.sh`, and `install.ps1`.

The CLI accepts:

```text
setup --profile-id PROFILE_ID --event-url URL
doctor --profile-id PROFILE_ID --explain
```

Setup atomically updates only HFC-owned keys in the selected `.env`:

- `HERMES_FEISHU_CARD_PROFILE_ID`
- `HERMES_FEISHU_CARD_EVENT_URL`

Unknown keys and comments are preserved. Existing process environment still wins over `.env`.

Doctor renders the route chain:

```text
Hermes process identity source
  -> HFC profile id
  -> sidecar event endpoint
  -> config profile key
  -> selected bot id
  -> binding/fallback reason
```

The report distinguishes missing identity, unknown profile, endpoint mismatch, missing profile credentials, unknown bot, and fallback routing. It provides main/child Hermes environment snippets without writing Hermes agent definitions.

### 6. Status Semantics and Calm Card UX

Normal cards preserve their current element order:

1. Existing header.
2. Existing main Markdown content.
3. Existing reasoning/tool timeline.
4. Existing task interaction elements.
5. Existing attachments.
6. Existing divider and tool summary.
7. Existing configured footer with duration, model, input tokens, output tokens, and context in the configured order.

V3.9 adds an explicit display-status channel with supported values:

- `thinking`
- `in_progress`
- `waiting`
- `completed`
- `failed`

Explicit metadata wins over inferred status. When metadata is absent, a conservative progress-handoff classifier may keep a terminal event visually in progress only when the answer contains both an active-work signal and an explicit future-continuation signal. A single generic phrase such as “请稍等” or “正在分析” is insufficient.

Classifier markers are configurable. Diagnostics record the source as explicit, inferred, or session-state without showing internal reasoning in the normal card.

PR #84 and @Zanetach receive credit for the progress-status and environment-routing direction. The implementation is refreshed against current main rather than merging the conflicting branch unchanged.

### 7. Runtime Lifecycle Cleanup

Runtime cleanup is centralized so related maps cannot drift independently.

- Closed `FlushController` instances are removed immediately after terminal drain and final update completion.
- Completed and failed sessions are retained for 3,600 seconds by default for reply aliases and diagnostics, then removed with their aliases, locks, delivery state, and controller state.
- A zombie session is eligible for cleanup only when it has no sequence progress, no answer/thinking/tool content, no card binding, and has remained unchanged for at least 120 seconds.
- Active interactions and in-flight sends are never collected.
- Cleanup runs on a bounded periodic task and opportunistically after terminal completion; it does not scan the full map on every delta.
- Diagnostic history retains at most 50 cleanup and recovery entries.

New metrics include:

- `sessions_collected`
- `zombie_sessions_collected`
- `flush_controllers_collected`
- `recovery_plans_available`
- `recovery_attempts`
- `recovery_successes`
- `recovery_refusals`
- `profile_mismatches`

Health and card output expose counts and hashed identifiers only.

## Data Flows

### Installer Automatic Recovery

```text
installer arguments / env
  -> package installation
  -> DiagnosticReport
  -> RecoveryPlan
  -> safe? yes: locked execute / no: refusal with reason
  -> patch current hook
  -> start or refresh sidecar
  -> final DiagnosticReport
```

### Feishu Repair Flow

```text
/hfc doctor
  -> existing Hermes command admission
  -> group: bind initiator when available
  -> DiagnosticReport
  -> operations card
  -> 安全修复 click
  -> existing Hermes action admission
  -> group: verify or claim operation owner
  -> confirmation card
  -> 确认修复 click
  -> existing Hermes action admission
  -> group: require matching operation owner
  -> operation lock + fresh RecoveryPlan
  -> atomic execute or refusal
  -> fresh DiagnosticReport
  -> same card updated
```

### Profile Setup Flow

```text
setup arguments
  -> validate profile id and event URL
  -> atomic HFC .env update
  -> load config
  -> resolve profile and bot route
  -> install/refresh hook
  -> doctor route chain
```

## Error Handling

- **Stale action:** return a stable “诊断已过期” state with a `重新检测` button.
- **Concurrent action:** show the current operation state; do not execute twice.
- **User-modified file:** refuse mutation and expose a sanitized reason.
- **Missing or changed backup:** refuse mutation.
- **Unsupported Hermes anchors:** refuse mutation while preserving native Hermes operation.
- **Repair generation failure:** leave the current file unchanged.
- **Atomic replace failure:** retain the current file and report the local failure category.
- **Restart failure:** preserve the repaired hook and show “修复完成，重启失败” with recheck available.
- **Unknown profile or endpoint mismatch:** keep sidecar usable for other profiles and show the mismatched route stage.
- **Sidecar unavailable:** CLI installer and repair remain independently usable.
- **Group mutation by a different operator:** reject the mutation with a generic ownership message; do not expose the bound operator identity.
- **Feishu callback without an operator identity:** group mutation cannot claim or confirm ownership; read-only actions and CLI diagnostics remain available.

## Compatibility

- Python remains 3.9+.
- No new mandatory runtime dependency is introduced.
- Existing single-profile config remains valid.
- Existing multi-profile config remains valid.
- Existing installer environment variables remain valid.
- Existing normal card JSON structure, configured footer fields, and ordering remain stable.
- Existing interaction.select approval/clarify cards continue using the current action path.
- Existing `/update` behavior remains outside HFC operations cards.
- Unknown Hermes runtime paths remain fail-open.

## Testing Strategy

### Unit Tests

- Diagnostic report classification and redaction.
- Recovery planning for clean, stale, corrupt, incomplete, and user-modified states.
- Recovery execution revalidation, locking, atomic replacement, quarantine retention, and idempotency.
- Installer argument precedence and `.env` preservation.
- Profile route-chain classification.
- Explicit and inferred display status, including false-positive guards.
- Operations-card buttons, tokens, expiry, confirmation, and current-layout/footer snapshots.
- Session/controller/zombie retention predicates and bounded history.

### Integration Tests

- `doctor`, `setup`, `install`, and `repair` sharing one diagnostic/recovery model.
- Known #82 corrupt completion marker fixture auto-repairing during setup.
- Missing backup, hash mismatch, and user-edited source refusing repair.
- Shell, Docker, and PowerShell argument parity.
- main/child profile environment and route validation.
- HTTP and WebSocket Feishu operations actions.
- Duplicate, stale, concurrent, and unauthorized action behavior through existing Hermes admission.
- Group initiator binding, first-click ownership fallback, different-operator rejection, and private-chat confirmation without identity comparison.
- Topic and cron regression coverage.
- Runtime cleanup across all related state maps.

### Release Gates

- Full pytest suite passes.
- `git diff --check` passes.
- Installer syntax checks pass on shell and PowerShell.
- Docker existing-container smoke covers install, upgrade, auto-repair, and final doctor.
- Windows parser/process smoke passes.
- Real Feishu smoke covers doctor card, recheck, repair confirmation, repair result, Gateway restart confirmation, normal streaming, completed footer, topic reply, and cron delivery.
- Release notes and contributor records include PR #84 / @Zanetach for the adopted directions.

## Acceptance Criteria

V3.9.0 is complete when all of the following are true:

1. A known #82 corrupt-marker fixture is automatically repaired by `setup` without extra user configuration.
2. Incomplete evidence and user edits are never overwritten.
3. A Feishu user can diagnose and safely repair through buttons without configuring an operator id.
4. Every mutation still passes existing Hermes admission and a two-step confirmation.
5. Group mutations require the same operator across ownership and confirmation, while private chats add no same-operator comparison.
6. Repeated and concurrent clicks execute at most once.
7. Main/child Hermes profile mappings can be created and verified from the CLI without editing agent definitions.
8. Shell, Docker, and PowerShell expose equivalent inputs and precedence.
9. PR #84's useful environment and status directions are implemented with false-positive guards and contribution credit.
10. Normal card layout and configured footer snapshots remain unchanged.
11. Terminal and zombie state is collected without removing active work.
12. Operations output contains no secrets, raw identity values, or complete local paths.
13. Automated release gates and real Feishu operations smoke pass.

## Rollout Order

Implementation should proceed in independently verifiable vertical slices:

1. Structured diagnostics and recovery planning.
2. Locked atomic recovery execution and installer automatic repair.
3. Installer argument and profile environment parity.
4. Profile route-chain diagnostics.
5. Runtime lifecycle cleanup and metrics.
6. Explicit-first status semantics and PR #84 refresh.
7. Operations cards and WebSocket/HTTP action flow.
8. Cross-platform fixtures, real Feishu smoke, documentation, and v3.9.0 release.
