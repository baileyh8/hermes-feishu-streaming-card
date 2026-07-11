# Async Operations Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Feishu operations-card callback acknowledge promptly while long diagnosis, repair, and restart work completes in bounded background tasks that update the same card.

**Architecture:** Store the latest `DiagnosticReport` on each in-memory operation record so synchronous actions never rebuild diagnostics. Add atomic background claims for recheck, repair, and restart, reuse the existing tracked task/executor lifecycle, and PATCH the transferred delivery only when the completing operation still owns it. Keep transport proof, token scope, expiry, group ownership, double confirmation, and recovery fingerprint validation unchanged.

**Tech Stack:** Python 3.9+, asyncio, aiohttp, Feishu WebSocket callbacks, pytest.

## Global Constraints

- Callback processing performs no Hermes detection, recovery classification, file mutation, subprocess restart, or Feishu PATCH before responding.
- Callback target latency is below 500 ms; the Gateway local forward timeout is 2.0 seconds.
- Background diagnosis timeout is 12.0 seconds and remains bounded by the existing four-worker executor and operation capacity.
- `details`, first-step `repair` / `restart`, `cancel`, and `dismiss` render synchronously from the stored safe report snapshot.
- `recheck`, `confirm_repair`, and `confirm_restart` are exactly-once tracked background operations and immediately return an in-progress card.
- Recheck updates the existing card through a `preparing` successor; it never sends a second result card.
- Repair/restart retain double confirmation, group same-operator ownership, fresh fingerprint validation, and safe refusal on evidence changes.
- Late, duplicate, cancelled, expired, or superseded workers cannot overwrite the current delivery owner.
- Failure and timeout produce a visible state with `重新检测`; configured footer and the compact 2x2 layout remain unchanged.
- Do not edit installed Hermes files manually, weaken transport proof, expose secrets/identifiers/paths, or add dependencies.

---

### Task 1: Operation snapshots and atomic recheck claims

**Files:**
- Modify: `hermes_feishu_card/operations.py`
- Test: `tests/unit/test_operations.py`

**Interfaces:**
- `OperationRecord.report: DiagnosticReport | None`
- `OperationStore.begin_recheck(...) -> tuple[OperationRecord, bool]`
- `OperationStore.diagnose(..., report: DiagnosticReport) -> OperationRecord`

- [ ] Add failing tests for report retention, atomic recheck successor creation, duplicate reuse, inherited transport/owner, capacity safety, stale predecessor handling, and late completion refusal.
- [ ] Run `tests/unit/test_operations.py` and verify the new tests fail for the missing APIs.
- [ ] Implement the minimal locked state/store changes; keep existing token and transition verification intact.
- [ ] Render `preparing` as a visible “正在重新检测” state without action buttons.
- [ ] Run `tests/unit/test_operations.py -q` and commit the task.

### Task 2: Fast callbacks and same-card background completion

**Files:**
- Modify: `hermes_feishu_card/server.py`
- Test: `tests/integration/test_server.py`

**Interfaces:**
- Consumes Task 1 report snapshots and `begin_recheck`.
- Produces tracked recheck/repair/restart workers and a delivery-owner-checked card publisher.

- [ ] Add failing integration tests proving every action returns before a blocked report builder, synchronous actions never call it, repeated recheck/confirm clicks create one task, and recheck PATCHes rather than sends.
- [ ] Add failing tests for group different-operator rejection, changed recovery fingerprint refusal, background timeout, superseded worker, PATCH failure, and cleanup cancellation.
- [ ] Move fresh report/recovery/restart work out of `_operations_action`; use `_AfterEofJsonResponse` to schedule only after the quick response is written.
- [ ] Reuse the existing task set, semaphore, executor, delivery transfer, and update retry helpers; set background diagnosis timeout to 12.0 seconds.
- [ ] Keep failure cards recheckable and preserve footer/button layout snapshots.
- [ ] Run `tests/integration/test_server.py tests/unit/test_operations.py -q` and commit the task.

### Task 3: Gateway forwarding boundary and end-to-end regression

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py`
- Test: `tests/unit/test_hook_runtime.py`
- Test: `tests/integration/test_hook_runtime_integration.py`

**Interfaces:**
- Sidecar `/card/actions` returns an immediate card response for every accepted operations action.
- Gateway forwards that card to Feishu and remembers successor transport context.

- [ ] Add a failing test that the operations local POST uses a 2.0-second timeout and accepted in-progress cards are returned through the native callback response.
- [ ] Change only the operations forward timeout; do not alter normal streaming or interaction-select behavior.
- [ ] Run hook unit/integration tests plus the operations server suite.
- [ ] Run `python -m pytest -q` and `git diff --check`; commit the task.

### Task 4: Real Feishu acceptance

**Files:**
- No repository files.

- [x] Restart the candidate sidecar and Gateway only when needed for updated hook code.
- [x] Send `/hfc doctor`; verify no gray Unknown command.
- [x] Click `重新检测`; verify no timeout toast, an immediate in-progress card, and one later same-card update.
- [ ] Verify `安全修复` first-step confirmation remains immediate; do not execute mutation without explicit user confirmation.
- [x] Record private-chat acceptance and leave group same/different-operator smoke as a separate explicit gate when no safe test group is available.
