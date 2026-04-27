# Sidecar Health Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose sidecar health metrics and safe Feishu update retry counters.

**Architecture:** Add a small process-local metrics object owned by the aiohttp app. `/events` updates counters at validation, apply, Feishu send, and Feishu update boundaries; `/health` and CLI `status` expose snapshots.

**Tech Stack:** Python, aiohttp, pytest, existing `hermes_feishu_card` sidecar package.

---

### Task 1: Add Server Metrics Tests

**Files:**
- Modify: `tests/integration/test_server.py`

- [x] **Step 1: Write failing tests for `/health` metrics**

Expected zero metrics:

```python
assert body["metrics"]["events_received"] == 0
assert body["metrics"]["feishu_update_retries"] == 0
```

- [x] **Step 2: Write failing tests for event lifecycle counters**

Expected normal flow:

```python
assert metrics["events_received"] == 3
assert metrics["events_applied"] == 3
assert metrics["feishu_send_successes"] == 1
assert metrics["feishu_update_successes"] == 2
```

- [x] **Step 3: Write failing tests for update retry and send failure**

Expected update retry:

```python
assert metrics["feishu_update_attempts"] == 2
assert metrics["feishu_update_failures"] == 1
assert metrics["feishu_update_retries"] == 1
```

Expected send failure:

```python
assert failed.status == 502
assert failure_body["active_sessions"] == 0
```

### Task 2: Implement Metrics and Retry Boundary

**Files:**
- Create: `hermes_feishu_card/metrics.py`
- Modify: `hermes_feishu_card/server.py`

- [x] **Step 1: Add `SidecarMetrics`**

Counters:

```python
events_received
events_applied
events_ignored
events_rejected
feishu_send_attempts
feishu_send_successes
feishu_send_failures
feishu_update_attempts
feishu_update_successes
feishu_update_failures
feishu_update_retries
```

- [x] **Step 2: Expose metrics from `/health`**

`/health` includes `metrics: metrics.snapshot()`.

- [x] **Step 3: Add Feishu delivery wrappers**

`_send_card` records one send attempt and never retries. `_update_card` retries once, records failures and retry attempts, and returns a boolean.

### Task 3: Expose Metrics in CLI and Docs

**Files:**
- Modify: `hermes_feishu_card/cli.py`
- Modify: `tests/integration/test_cli_process.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/testing.md`
- Modify: `TODO.md`
- Modify: `tests/unit/test_docs.py`

- [x] **Step 1: Print metrics from `status`**

When sidecar is running, print known integer metric keys from `/health`.

- [x] **Step 2: Document the behavior**

Docs state that metrics are process-local, send is not retried to avoid duplicate cards, and updates retry once.

### Task 4: Verification

- [x] **Step 1: Run focused tests**

```bash
python3 -m pytest tests/integration/test_server.py tests/integration/test_cli_process.py tests/unit/test_docs.py -q
```

- [x] **Step 2: Run full tests**

```bash
python3 -m pytest -q
```
