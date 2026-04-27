# Feishu Card Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual CLI command that sends and updates one real Feishu interactive card using local credentials.

**Architecture:** Keep the smoke command in the existing CLI surface and reuse `load_config`, `FeishuClient`, `CardSession`, `SidecarEvent`, and `render_card`. Automated tests use a mock Feishu server and never touch real credentials.

**Tech Stack:** Python 3.9+, argparse, aiohttp test server, pytest/pytest-asyncio, existing `hermes_feishu_card` modules.

---

## File Structure

- Modify `hermes_feishu_card/cli.py`: add `smoke-feishu-card` parser and command handler.
- Modify `tests/integration/test_feishu_client_http.py`: add CLI smoke tests using a mock Feishu server.
- Modify `README.md`, `docs/testing.md`, `TODO.md`, `tests/unit/test_docs.py`: document command and preserve live-smoke boundary.

## Task 1: CLI Smoke Command

**Files:**
- Modify: `hermes_feishu_card/cli.py`
- Modify: `tests/integration/test_feishu_client_http.py`

- [ ] **Step 1: Add failing success test**

Add a test that starts a mock Feishu server, runs `python3 -m hermes_feishu_card.cli smoke-feishu-card --config <config> --chat-id oc_abc`, and asserts token/send/update requests were received and stdout contains `smoke ok`.

- [ ] **Step 2: Run failure**

Run: `python3 -m pytest tests/integration/test_feishu_client_http.py -q`

Expected: FAIL because `smoke-feishu-card` is not a recognized command.

- [ ] **Step 3: Implement command**

Add parser support, an async implementation that builds a `FeishuClient`, sends a thinking card, updates it to completed, and a sync wrapper using `asyncio.run()`.

- [ ] **Step 4: Run success test**

Run: `python3 -m pytest tests/integration/test_feishu_client_http.py -q`

Expected: PASS.

## Task 2: Error Handling Tests

**Files:**
- Modify: `tests/integration/test_feishu_client_http.py`
- Modify: `hermes_feishu_card/cli.py`

- [ ] **Step 1: Add failing error tests**

Cover missing credentials, send failure, update failure, and no secret leakage in stdout/stderr.

- [ ] **Step 2: Implement minimal error handling**

Ensure missing credentials returns non-zero, `FeishuAPIError` returns non-zero, and diagnostics do not include secrets.

- [ ] **Step 3: Verify**

Run: `python3 -m pytest tests/integration/test_feishu_client_http.py -q`

Expected: PASS.

## Task 3: Docs And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/testing.md`
- Modify: `TODO.md`
- Modify: `tests/unit/test_docs.py`

- [ ] **Step 1: Update docs**

Document the command, state it sends a real test card, and keep the live smoke checklist item unchecked until actually run with a real bot.

- [ ] **Step 2: Add doc guard**

Add a low-brittleness assertion that docs mention `smoke-feishu-card`, `--chat-id`, and the live smoke remains manual.

- [ ] **Step 3: Run full verification**

Run: `python3 -m pytest -q`

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add hermes_feishu_card/cli.py tests README.md docs TODO.md
git commit -m "feat: add Feishu card smoke command"
```
