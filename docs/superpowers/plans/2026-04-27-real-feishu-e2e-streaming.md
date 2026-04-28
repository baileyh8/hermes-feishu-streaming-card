# Real Feishu E2E Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete real Feishu end-to-end validation by connecting Hermes streaming deltas, tool progress, completion stats, and fallback behavior to the sidecar card lifecycle.

**Architecture:** Keep the Gateway patch minimal and owned by the installer. The existing start/completion hook remains in `_handle_message_with_agent`; new runtime bridge calls are inserted at existing Hermes callback closures inside `_run_agent`: `_stream_delta_cb` for `answer.delta`, `progress_callback` for `tool.updated`, and `_interim_assistant_cb` for best-effort `thinking.delta` when Hermes exposes interim commentary. The sidecar remains the sole owner of session state, CardKit rendering, Feishu send/update, throttling, metrics, and tag filtering.

**Tech Stack:** Python 3.9+, standard library `asyncio`, existing `hook_runtime`, `patcher`, `CardSession`, `SidecarEvent`, pytest, pytest-asyncio, live Feishu manual verification.

---

## Completion Verified State

- Real Feishu card create/update works.
- The Gateway completion hook suppresses duplicate native gray text after sidecar completion succeeds.
- `message_id` extraction uses the real Gateway event id, so consecutive messages do not update stale cards.
- Gateway callback hooks now emit `thinking.delta`, `answer.delta`, and `tool.updated` to the sidecar.
- Sidecar update throttling and per-message locking keep long streamed answers from flooding Feishu card update APIs.
- Completion events preserve duration and token metadata for the card footer, with a safe output-token estimate when the provider does not report usage.
- Full local suite passed after the lifecycle, streaming, throttling, and footer fixes.
- Real Feishu E2E was manually verified with a live Hermes Gateway and sidecar: one new card was created, the answer streamed into the card, no gray native Feishu text was emitted below the card, and the footer displayed non-zero duration/token data.

## File Structure

- Modify `hermes_feishu_card/install/patcher.py`: add owned patch blocks for Hermes `_run_agent` callback closures.
- Modify `hermes_feishu_card/hook_runtime.py`: add a thread-safe helper that can be called from sync callbacks and schedules/dispatches events safely.
- Modify `tests/unit/test_patcher.py`: cover insertion, idempotence, upgrade, and removal for new streaming/tool callback blocks.
- Modify `tests/unit/test_hook_runtime.py`: cover sync callback dispatch success/fail-open and event payload data for deltas/tools.
- Modify `tests/integration/test_hook_runtime_integration.py`: execute a patched fixture handler that simulates callbacks and assert mock sidecar receives start, delta/tool, completion.
- Modify `tests/fixtures/hermes_v2026_4_23/gateway/run.py`: expand the fixture enough to include `_run_agent`-like callback closures for patcher integration tests.
- Modify `docs/e2e-verification.md`, `docs/testing.md`, `TODO.md`: distinguish local preview, live Feishu smoke, and full live E2E acceptance.

## Live E2E Acceptance Matrix

| Case | User prompt | Required evidence |
| --- | --- | --- |
| Short answer lifecycle | `只回复 OK，短答验收` | One new card, completed state, no gray native text |
| Long Markdown answer | Ask for a table and code block | Card content remains readable, final answer replaces thinking |
| Thinking sanitization | Ask model to reason with `<think>` tags in output | Card never shows `<think>` or `</think>` |
| Progressive answer | Ask for a 6-step answer | sidecar metrics show multiple update attempts for one card before completion |
| Tool progress | Ask for a task that triggers a Hermes tool | Card shows tool count while running and final count after completion |
| Consecutive messages | Send two short messages back to back | Two separate cards, no stale card update |
| Fallback | Stop sidecar, send one message | Hermes sends native text and does not crash |

## Task 1: Patcher Tests For Streaming And Tool Callback Blocks

**Files:**
- Modify: `tests/unit/test_patcher.py`
- Modify: `hermes_feishu_card/install/patcher.py`

- [ ] **Step 1: Add failing patcher tests**

Add tests that use this minimal Hermes-like source:

```python
SOURCE = '''
async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):
    return await self._run_agent(event_message_id=event.message_id)

async def _run_agent(self, event_message_id=None):
    def progress_callback(event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):
        progress_queue.put(tool_name)

    def _stream_delta_cb(text: str) -> None:
        if _run_still_current():
            _stream_consumer.on_delta(text)

    def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:
        if already_streamed:
            return
'''
```

Assert `patcher.apply_patch(SOURCE)` includes three owned marker pairs:

```python
assert "# HERMES_FEISHU_CARD_TOOL_PATCH_BEGIN" in patched
assert "# HERMES_FEISHU_CARD_ANSWER_DELTA_PATCH_BEGIN" in patched
assert "# HERMES_FEISHU_CARD_THINKING_DELTA_PATCH_BEGIN" in patched
```

Also assert `patcher.remove_patch(patched) == SOURCE`.

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m pytest tests/unit/test_patcher.py::test_apply_patch_inserts_streaming_callback_hooks -q
```

Expected: FAIL because the marker constants and insertion logic do not exist.

- [ ] **Step 3: Implement minimal marker insertion**

In `hermes_feishu_card/install/patcher.py`, add owned marker constants:

```python
TOOL_PATCH_BEGIN = "# HERMES_FEISHU_CARD_TOOL_PATCH_BEGIN"
TOOL_PATCH_END = "# HERMES_FEISHU_CARD_TOOL_PATCH_END"
ANSWER_DELTA_PATCH_BEGIN = "# HERMES_FEISHU_CARD_ANSWER_DELTA_PATCH_BEGIN"
ANSWER_DELTA_PATCH_END = "# HERMES_FEISHU_CARD_ANSWER_DELTA_PATCH_END"
THINKING_DELTA_PATCH_BEGIN = "# HERMES_FEISHU_CARD_THINKING_DELTA_PATCH_BEGIN"
THINKING_DELTA_PATCH_END = "# HERMES_FEISHU_CARD_THINKING_DELTA_PATCH_END"
```

Extend `apply_patch()` after completion patch to insert:

```python
from hermes_feishu_card.hook_runtime import emit_from_hermes_locals_threadsafe as _hfc_emit_threadsafe
```

inside each callback:

```python
_hfc_emit_threadsafe({
    **locals(),
    "event": event,
    "source": source,
    "event_message_id": event_message_id,
    "tool_id": tool_name or "tool",
    "name": tool_name or "tool",
    "status": "running",
    "detail": preview or "",
}, event_name="tool.updated")
```

```python
_hfc_emit_threadsafe({
    **locals(),
    "event": event,
    "source": source,
    "event_message_id": event_message_id,
    "text": text,
}, event_name="answer.delta")
```

```python
_hfc_emit_threadsafe({
    **locals(),
    "event": event,
    "source": source,
    "event_message_id": event_message_id,
    "text": text,
}, event_name="thinking.delta")
```

If a callback or required variables are absent, leave the source unchanged for that block.

- [ ] **Step 4: Verify patcher tests**

Run:

```bash
python3 -m pytest tests/unit/test_patcher.py -q
```

Expected: PASS.

## Task 2: Thread-Safe Hook Runtime Event Dispatch

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py`
- Modify: `tests/unit/test_hook_runtime.py`

- [ ] **Step 1: Add failing tests**

Add tests for:

```python
result = hook_runtime.emit_from_hermes_locals_threadsafe(
    {"chat_id": "oc_abc", "message_id": "msg_1", "text": "第一段。"},
    event_name="answer.delta",
)
assert result is True
```

and for missing chat id:

```python
assert hook_runtime.emit_from_hermes_locals_threadsafe(
    {"text": "orphan"},
    event_name="answer.delta",
) is False
```

Use the existing `SenderProbe` and `drain_tasks()` to assert one payload is posted for the valid case.

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m pytest tests/unit/test_hook_runtime.py::test_emit_from_hermes_locals_threadsafe_schedules_sender -q
```

Expected: FAIL with missing function.

- [ ] **Step 3: Implement helper**

Add to `hook_runtime.py`:

```python
def emit_from_hermes_locals_threadsafe(
    local_vars: dict[str, Any],
    event_name: str = "message.started",
) -> bool:
    try:
        config = load_runtime_config()
        if not config.enabled:
            return False
        payload = build_event(event_name, local_vars)
        if payload is None:
            return False
        loop = local_vars.get("_hfc_loop")
        if loop is not None:
            asyncio.run_coroutine_threadsafe(
                _send_fail_open(config.event_url, payload, config.timeout_seconds),
                loop,
            )
            return True
        asyncio.get_running_loop()
        asyncio.create_task(
            _send_fail_open(config.event_url, payload, config.timeout_seconds)
        )
        return True
    except Exception:
        return False
```

The Gateway callback patch must include `"_hfc_loop": _loop_for_step` in payload locals, because Hermes tool and stream callbacks run from the agent executor thread.

- [ ] **Step 4: Verify hook runtime tests**

Run:

```bash
python3 -m pytest tests/unit/test_hook_runtime.py -q
```

Expected: PASS.

## Task 3: Fixture Integration For Callback Events

**Files:**
- Modify: `tests/fixtures/hermes_v2026_4_23/gateway/run.py`
- Modify: `tests/integration/test_hook_runtime_integration.py`

- [ ] **Step 1: Expand fixture**

Make the fixture contain `_handle_message_with_agent()` plus `_run_agent()` with `progress_callback`, `_stream_delta_cb`, and `_interim_assistant_cb` closures. The fixture must call those closures with:

```python
_interim_assistant_cb("我先分析。")
progress_callback("tool.started", tool_name="search", preview="查资料")
_stream_delta_cb("最终答案第一段。")
```

- [ ] **Step 2: Add failing integration test**

Install into the fixture, execute the handler, and assert mock sidecar receives events:

```python
names = [payload["event"] for payload in received]
assert "message.started" in names
assert "thinking.delta" in names
assert "tool.updated" in names
assert "answer.delta" in names
assert "message.completed" in names
```

- [ ] **Step 3: Run test to verify failure**

Run:

```bash
python3 -m pytest tests/integration/test_hook_runtime_integration.py -q
```

Expected: FAIL before Tasks 1-2 implementation, PASS afterward.

## Task 4: Real Feishu E2E Execution

**Files:**
- Modify: `docs/e2e-verification.md`
- Modify: `TODO.md`

- [ ] **Step 1: Start sidecar and Gateway**

Use the existing local test configuration. Confirm:

```bash
curl -fsS http://127.0.0.1:18765/health
```

Expected: JSON with `"status": "healthy"`.

- [ ] **Step 2: Run live acceptance cases**

The human sends messages in Feishu. The agent records:

- Gateway logs showing inbound message and response ready.
- sidecar `/health` before/after metrics.
- screenshot evidence for the visible card.

- [ ] **Step 3: Mark each case**

For each case, write one line to `docs/e2e-verification.md`:

```markdown
- [x] 2026-04-27 短答验收：新卡片完成，无灰色文本，send/update +1。
```

If a case fails, stop and switch to `superpowers:systematic-debugging`.

## Task 5: Final Verification And Branch Finish

**Files:**
- Modify: `docs/e2e-verification.md`
- Modify: `TODO.md`

- [ ] **Step 1: Run full suite**

```bash
python3 -m pytest -q -p no:cacheprovider
```

Expected: all tests pass.

- [ ] **Step 2: Commit**

```bash
git add hermes_feishu_card tests docs TODO.md
git commit -m "feat: forward live Hermes card stream events"
```

- [ ] **Step 3: Push**

```bash
git push origin codex/sidecar-only-phase1
```

- [ ] **Step 4: Use finishing branch workflow**

After all live E2E cases pass, use `superpowers:finishing-a-development-branch` to decide merge/PR cleanup.
