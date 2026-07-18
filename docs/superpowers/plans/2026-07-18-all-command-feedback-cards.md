# Hermes All-Command Feedback Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every user-visible text feedback emitted by a Feishu/Lark Hermes slash command render through HFC cards, with same-card updates, safe native fallback, and a visible `/compress` running state.

**Architecture:** Generalize the existing Feishu adapter `send` wrapper from a six-command allowlist to a bounded per-message command-feedback context. The first feedback creates an interactive card, later feedback PATCHes that card under a context-local lock, and any failed create/PATCH falls back to the exact Hermes text. Install an idempotent runtime wrapper around `_handle_compress_command` so manual compression creates a running card before invoking the original handler and updates it with the unmodified result.

**Tech Stack:** Python 3.9+, asyncio, ContextVar, Feishu/Lark CardKit JSON, pytest, existing HFC runtime monkeypatch and installer hooks.

## Global Constraints

- Do not manually edit installed Hermes `gateway/run.py`; only the existing patcher-installed runtime hook may activate this behavior.
- Apply only to Feishu/Lark slash-command feedback; ordinary chat, Agent streaming events, media delivery, and other adapters remain unchanged.
- A native feedback message is suppressed only after the corresponding card create or PATCH succeeds.
- Existing `/model`, `/resume`, destructive confirmation, and `/hfc` interactive-card paths keep priority and must not duplicate cards.
- Canonicalize built-in aliases with Hermes `resolve_command(...)`; retain normalized raw names for quick/plugin/unknown commands.
- Keep topic/reply metadata and user-message anchors intact.
- Long feedback must be split into CardKit markdown elements without changing text order.
- Runtime wrappers must be idempotent and fail-open for unsupported Hermes versions.

---

### Task 1: General command context and classification

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py:150-180, 2085-2170`
- Test: `tests/unit/test_hook_runtime.py:970-1200`

**Interfaces:**
- Produces: `_hfc_canonical_command(command: str) -> str`
- Produces: `_hfc_command_result_context_from_event(event: Any) -> dict[str, Any] | None`
- Produces: `_hfc_take_feishu_command_result_context(chat_id: str, content: Any) -> dict[str, Any] | None`
- Context keys: `command`, `raw_command`, `chat_id`, `reply_to_message_id`, `thread_id`, `card_message_id`, `expires_at`, `_lock`

- [ ] **Step 1: Write failing classification tests**

Add parametrized tests proving `/status`, alias `/compact`, plugin/quick `/deploy-preview`, `/update`, and unknown `/does-not-exist` create contexts; prove ordinary text, non-Feishu events, empty slash input, expired contexts, empty feedback, and chat mismatch do not.

```python
@pytest.mark.parametrize(
    ("text", "get_command", "expected"),
    [
        ("/status", "status", "status"),
        ("/compact", "compact", "compress"),
        ("/deploy-preview now", "deploy-preview", "deploy-preview"),
        ("/update", "update", "update"),
        ("/does-not-exist", "does-not-exist", "does-not-exist"),
    ],
)
def test_all_feishu_slash_commands_create_feedback_context(text, get_command, expected):
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc", thread_id="omt_1"),
        text=text,
        message_id="om_user",
        get_command=lambda: get_command,
    )
    context = hook_runtime._hfc_command_result_context_from_event(event)
    assert context["command"] == expected
    assert context["reply_to_message_id"] == "om_user"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_hook_runtime.py -k 'all_feishu_slash_commands or feedback_context' -q
```

Expected: `/status`, `/compact`, `/update`, quick/plugin, or unknown contexts are rejected by the current six-command allowlist.

- [ ] **Step 3: Implement generic bounded context**

Remove `_HFC_COMMAND_RESULT_CARD_COMMANDS`. Resolve aliases dynamically with `hermes_cli.commands.resolve_command` inside a guarded local import, normalize underscores to hyphens only when resolution fails, store `time.monotonic() + 600.0`, and make `_hfc_take_feishu_command_result_context` validate non-empty content, chat identity, and expiry without consuming a valid context.

```python
def _hfc_canonical_command(command: str) -> str:
    raw = str(command or "").strip().lstrip("/").split(None, 1)[0].lower()
    if not raw:
        return ""
    try:
        from hermes_cli.commands import resolve_command
        resolved = resolve_command(raw)
        name = str(getattr(resolved, "name", "") or "").strip().lower()
        if name:
            return name
    except Exception:
        pass
    return raw.replace("_", "-")
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 command. Expected: all new tests pass and existing command-context tests remain green after updating their one-shot expectation to a bounded reusable context.

- [ ] **Step 5: Commit Task 1**

```bash
git add hermes_feishu_card/hook_runtime.py tests/unit/test_hook_runtime.py
git commit -m "feat: recognize all Hermes command feedback"
```

---

### Task 2: Create once, update the same card, and fail open

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py:2020-2075, 2500-2605`
- Test: `tests/unit/test_hook_runtime.py:970-1450`

**Interfaces:**
- Consumes: Task 1 command context.
- Produces: `_hfc_command_feedback_card(context: dict[str, Any], content: str) -> dict[str, Any]`
- Produces: `_hfc_deliver_command_feedback_card(adapter: Any, *, chat_id: str, content: str, reply_to: str | None, metadata: dict[str, Any] | None, context: dict[str, Any]) -> Any`
- Mutates: `context["card_message_id"]` only after a successful create.

- [ ] **Step 1: Write failing delivery lifecycle tests**

Add tests for create then two PATCHes, concurrent sends creating only one card, create failure exact-text fallback, PATCH failure exact-text fallback, topic metadata, `/update`, and long Markdown split into multiple elements.

```python
async def test_command_feedback_updates_one_card(monkeypatch):
    first = await adapter.send("oc_abc", "first", reply_to="om_user")
    second = await adapter.send("oc_abc", "second", reply_to="om_user")
    third = await adapter.send("oc_abc", "third", reply_to="om_user")
    assert first.message_id == second.message_id == third.message_id == "om_card"
    assert len(adapter.created) == 1
    assert [card["elements"][0]["content"] for card in adapter.updated] == ["second", "third"]
    assert adapter.text_sent == []
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_hook_runtime.py -k 'command_feedback or direct_command_result' -q
```

Expected: second feedback uses native text because the current context is one-shot; long feedback remains one markdown element.

- [ ] **Step 3: Implement serialized create/update delivery**

Build command cards with semantic/common titles and fallback `/<command>`. Import `MAIN_CONTENT_CHUNK_CHARS` and `split_markdown_blocks` locally from `render`; create one markdown element per chunk. Lazily place `asyncio.Lock()` in the context and hold it across create/PATCH selection. If `card_message_id` exists, call `_hfc_update_native_command_card`; otherwise call `_feishu_send_with_retry`, finalize the response, and record the returned message id only on success.

Update `_hfc_send_with_native_command_result_card` so any unsuccessful card delivery calls `_hfc_original_send` with the unchanged `content`, `reply_to`, and `metadata`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 2 command. Expected: one create, ordered same-card PATCHes, no native text on success, exact native text on each failure.

- [ ] **Step 5: Run runtime/topic integration tests**

```bash
python -m pytest tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py tests/integration/test_feishu_sdk_compat.py -q
```

Expected: all pass; the live Lark handler identity tests remain unchanged.

- [ ] **Step 6: Commit Task 2**

```bash
git add hermes_feishu_card/hook_runtime.py tests/unit/test_hook_runtime.py
git commit -m "feat: update command feedback in one Feishu card"
```

---

### Task 3: Manual `/compress` running-to-terminal card

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py:1200-1350, 4270-4445`
- Test: `tests/unit/test_hook_runtime.py`
- Test: `tests/integration/test_hook_runtime_integration.py`
- Test: `tests/integration/test_feishu_sdk_compat.py`

**Interfaces:**
- Produces: `_hfc_handle_compress_command_with_card(self: Any, event: Any) -> Any`
- Produces: `_hfc_install_compress_command_handler(runner_type: type) -> bool`
- Stores: `runner_type._hfc_original_handle_compress_command`
- Reuses: Task 2 `_hfc_deliver_command_feedback_card(...)`

- [ ] **Step 1: Write failing `/compress` wrapper tests**

Cover success, no-op, warning/aborted output, alias `/compact`, begin-create failure, terminal-PATCH failure, original exception propagation, non-Feishu bypass, and idempotent installation.

```python
@pytest.mark.asyncio
async def test_manual_compress_updates_running_card_with_original_result():
    runner, adapter = make_runner_and_adapter("/compress")
    runner._handle_compress_command = AsyncMock(
        return_value="🗜️ Compressed: 57 → 13 messages\nApprox request size: ~47,319 → ~12,910 tokens"
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)
    result = await runner._handle_compress_command(event)
    assert result is None
    assert adapter.created_card_content == "⏳ 正在压缩上下文…"
    assert "57 → 13 messages" in adapter.updated_card_content
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_hook_runtime.py -k 'manual_compress or compress_command_handler' -q
```

Expected: the runner handler is not wrapped and no running card is created.

- [ ] **Step 3: Implement the idempotent runtime wrapper**

During `install_feishu_command_card_adapter_methods`, preserve the original callable once and replace `_handle_compress_command` on the runner type. The wrapper obtains/creates the Feishu command context, creates `⏳ 正在压缩上下文…`, awaits the original handler exactly once, then updates with `str(result)` unchanged. Return `None` only after terminal card success; otherwise return the original result. Non-Feishu and missing adapter paths call the original directly.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 3 command. Expected: all branch tests pass and original handler call counts are exactly one.

- [ ] **Step 5: Run patch/install compatibility matrix**

```bash
python -m pytest tests/unit/test_hook_runtime.py tests/unit/test_patcher.py tests/integration/test_cli_install.py tests/integration/test_hook_runtime_integration.py tests/integration/test_feishu_sdk_compat.py -q
```

Expected: all pass; current command-card patch remains idempotent/removable and no new `gateway/run.py` marker is required.

- [ ] **Step 6: Commit Task 3**

```bash
git add hermes_feishu_card/hook_runtime.py tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py tests/integration/test_feishu_sdk_compat.py
git commit -m "feat: cardify manual context compression"
```

---

### Task 4: Documentation and completion gate

**Files:**
- Modify: `docs/wiki/event-flow.md`
- Modify: `docs/wiki/maintenance-guide.md`
- Modify: `docs/wiki/feishu-acceptance.md`
- Modify: `TODO.md`
- Test: `tests/unit/test_docs.py`

**Interfaces:**
- Documents the generic command-feedback lifecycle, exclusions, fail-open contract, and manual `/compress` acceptance.

- [ ] **Step 1: Update public maintainer documentation**

Replace the fixed six-command list with the generic scope; describe first-create/later-PATCH behavior, dedicated interactive-card priority, Agent-turn boundary, `/compress` running card, `/update` restart boundary, long-content splitting, and exact-text native fallback.

- [ ] **Step 2: Add documentation assertions**

Add a focused assertion that `event-flow.md` contains `all slash command feedback`, `/compress`, `same card`, and `fail-open`, while retaining the `/model`, `/resume`, and `/update` boundaries.

- [ ] **Step 3: Run documentation gate**

```bash
python -m pytest tests/unit/test_docs.py -q
git diff --check
```

Expected: all docs tests pass and diff check is clean.

- [ ] **Step 4: Run the full release-proportional gate**

```bash
python -m pytest -q
git diff --check
```

Expected: zero failures; existing skips remain explained by the test suite.

- [ ] **Step 5: Audit the actual objective**

Verify with searches and test evidence that no fixed command allowlist remains, every non-empty Feishu slash feedback enters the generic context, specialized interactive paths produce no duplicate, multi-feedback updates one card, `/compress` has start and terminal states, and every failed create/PATCH reaches original native send.

- [ ] **Step 6: Commit Task 4**

```bash
git add TODO.md docs/wiki/event-flow.md docs/wiki/maintenance-guide.md docs/wiki/feishu-acceptance.md tests/unit/test_docs.py
git commit -m "docs: document all-command feedback cards"
```
