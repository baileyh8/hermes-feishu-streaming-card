# Issue #133 Compaction Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Hermes context compaction as a short-lived runtime phase on the same Feishu streaming card, including when compaction is the first visible runtime event.

**Architecture:** A removable AST callback patch runs at the start of Hermes `_status_callback_sync`, before Hermes filters the status. `hook_runtime` recognizes only the exact stable `Compacting context` marker and emits an ordered `system.notice`. `CardSession` stores a non-persistent `runtime_phase_text`; the server permits only this exact notice to create a primary session when none exists, and normal runtime/terminal events clear the phase.

**Tech Stack:** Python AST source patching, asyncio, aiohttp, CardKit JSON 2.0, pytest.

## Global Constraints

- Never edit an installed Hermes `gateway/run.py` directly; only `install/patcher.py` may generate the block.
- Match `Compacting context` case-insensitively; do not match generic `compression` text or infer from silence.
- The callback hook is fail-open and never returns early from Hermes status processing.
- Reuse `system.notice`; do not introduce another protocol event name.
- A compaction-first event creates one primary card on the original reply anchor, not an independent notice card.
- Pending interaction text has higher Header priority than runtime phase; runtime phase has higher priority than tool preview.
- Thinking, answer, tool, completed, and failed events clear the phase.
- Hermes versions without the callback anchor retain all other plugin capabilities and report the optional capability as unavailable.

---

### Task 1: Add the removable status-callback patch

**Files:**
- Modify: `hermes_feishu_card/install/patcher.py:4-126,409-546,1668-1795`
- Modify: `tests/unit/test_patcher.py`
- Modify: `tests/fixtures/hermes_0_13_plus/gateway/run.py` or add a focused inline fixture in `tests/unit/test_patcher.py`

**Interfaces:**
- Produces markers `STATUS_PATCH_BEGIN` / `STATUS_PATCH_END`.
- Produces `_render_status_hook_block(indent, newline)`.
- Consumes existing `_apply_callback_patch` with callback `_status_callback_sync`.

- [ ] **Step 1: Write failing patcher tests**

Use a modern callback fixture containing outer names `source`, `event_message_id`, `_status_chat_id`, `_loop_for_step`, and `_run_still_current`. Assert:

```python
patched = patcher.apply_patch(source, strategy="gateway_run_013_plus")
assert patcher.STATUS_PATCH_BEGIN in patched
assert patched.index(patcher.STATUS_PATCH_BEGIN) < patched.index(
    "prepared_message = _prepare_gateway_status_message("
)
assert "handle_status_from_hermes_locals as _hfc_handle_status" in patched
assert patched.count(patcher.STATUS_PATCH_BEGIN) == 1
assert patcher.apply_patch(patched, strategy="gateway_run_013_plus") == patched
assert patcher.remove_patch(patched) == source
```

Add fixtures where the callback is renamed, `message` is missing, or required outer names are absent, and assert no status marker is inserted while the main patch still applies.

- [ ] **Step 2: Run focused patcher tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_patcher.py -k "status_callback" -q
```

Expected: FAIL because status markers and renderer do not exist.

- [ ] **Step 3: Implement marker insertion**

Add constants:

```python
STATUS_PATCH_BEGIN = "# HERMES_FEISHU_CARD_STATUS_PATCH_BEGIN"
STATUS_PATCH_END = "# HERMES_FEISHU_CARD_STATUS_PATCH_END"
```

In `apply_patch`, for `gateway_run_013_plus`, call `_apply_callback_patch` after the existing command/platform callback patches and before returning:

```python
content = _apply_callback_patch(
    content,
    callback_name="_status_callback_sync",
    begin_marker=STATUS_PATCH_BEGIN,
    end_marker=STATUS_PATCH_END,
    renderer=_render_status_hook_block,
    required_outer_names=(
        "source",
        "event_message_id",
        "_status_chat_id",
        "_loop_for_step",
        "_run_still_current",
    ),
    required_callback_args=("event_type", "message"),
)
```

Render this fail-open block without returning from the callback:

```python
def _render_status_hook_block(indent: str, newline: str):
    inner = _child_indent(indent)
    return [
        f"{indent}{STATUS_PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner}from hermes_feishu_card.hook_runtime "
            f"import handle_status_from_hermes_locals as _hfc_handle_status{newline}"
        ),
        f"{inner}if _run_still_current():{newline}",
        f"{_child_indent(inner)}_hfc_handle_status({{{newline}",
        f"{_child_indent(inner)}    **locals(),{newline}",
        f"{_child_indent(inner)}    \"source\": source,{newline}",
        f"{_child_indent(inner)}    \"chat_id\": _status_chat_id,{newline}",
        f"{_child_indent(inner)}    \"message_id\": event_message_id,{newline}",
        f"{_child_indent(inner)}    \"_hfc_loop\": _loop_for_step,{newline}",
        f"{_child_indent(inner)}}}, event_type=event_type, message=message){newline}",
        *_render_hook_exception_handler(indent, newline),
        f"{indent}{STATUS_PATCH_END}{newline}",
    ]
```

Use local indent variables in the real implementation to keep formatting readable.

- [ ] **Step 4: Add removal, lenient removal, and owned-block validation**

Add the status marker pair to `remove_patch`, `remove_patch_lenient`, and every callback marker tuple. Validate owned content with `_render_status_hook_block`; corrupt or duplicated markers raise the same callback-marker error as existing blocks.

- [ ] **Step 5: Run patcher/install tests and commit**

Run:

```bash
python -m pytest tests/unit/test_patcher.py tests/integration/test_cli_install.py -q
```

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/install/patcher.py tests/unit/test_patcher.py tests/fixtures
git commit -m "feat: hook Hermes compaction status callback"
```

### Task 2: Classify the exact status and emit an ordered notice

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py`
- Modify: `tests/unit/test_hook_runtime.py`

**Interfaces:**
- Produces: `handle_status_from_hermes_locals(local_vars, *, event_type, message) -> bool`.
- Produces notice data: kind `context-compaction`, id `context-compaction:active`, phase `started`, scope `session`, display status `in_progress`, and `create_session=True`.
- Consumes: `emit_from_hermes_locals_threadsafe` and the existing sequence/order machinery.

- [ ] **Step 1: Write failing classification tests**

Add a Feishu source fixture and assert:

```python
handled = hook_runtime.handle_status_from_hermes_locals(
    locals_payload,
    event_type="context",
    message="🗜️ Compacting context — summarizing earlier conversation so I can continue...",
)
assert handled is True
payload = posted_payloads[0]
assert payload["event"] == "system.notice"
assert payload["data"] == {
    **payload["data"],
    "notice_kind": "context-compaction",
    "notice_id": "context-compaction:active",
    "notice_scope": "session",
    "phase": "started",
    "title": "正在压缩上下文",
    "level": "info",
    "content": "正在总结较早的对话，完成后会继续当前任务。",
    "create_session": True,
}
assert payload["data"]["display_status"] == "in_progress"
```

Parametrize non-matches: `compression failed`, `compressing files`, `context pressure`, ordinary provider status, non-Feishu source, blank message, and stale generation guard. Assert no post and `False`.

- [ ] **Step 2: Run focused hook tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_hook_runtime.py -k "status_from_hermes or compacting_context" -q
```

Expected: FAIL because the handler is missing.

- [ ] **Step 3: Implement the exact classifier**

Add:

```python
_CONTEXT_COMPACTION_STATUS_RE = re.compile(r"\bCompacting\s+context\b", re.IGNORECASE)


def handle_status_from_hermes_locals(
    local_vars: dict[str, Any], *, event_type: str, message: str
) -> bool:
    try:
        source = local_vars.get("source")
        if _platform_name(local_vars, source) != "feishu":
            return False
        if not _CONTEXT_COMPACTION_STATUS_RE.search(str(message or "")):
            return False
        run_guard = local_vars.get("_run_still_current")
        if callable(run_guard) and not run_guard():
            return False
        event_locals = {
            **local_vars,
            "_hfc_notice_title": "正在压缩上下文",
            "_hfc_notice_level": "info",
            "_hfc_notice_kind": "context-compaction",
            "_hfc_notice_id": "context-compaction:active",
            "_hfc_notice_scope": "session",
            "_hfc_notice_phase": "started",
            "_hfc_notice_create_session": True,
            "display_status": "in_progress",
            "content": "正在总结较早的对话，完成后会继续当前任务。",
        }
        return emit_from_hermes_locals_threadsafe(
            event_locals, event_name="system.notice"
        )
    except Exception:
        return False
```

Update `_event_data` for `system.notice` to copy `_hfc_notice_phase` and boolean `_hfc_notice_create_session` into event data. Existing `display_status` normalization already copies `local_vars["display_status"]` into event data; reuse it rather than adding another field. Do not expand classification of native system notices.

- [ ] **Step 4: Verify topic anchors and ordering**

Add a topic test asserting `conversation_id`, `thread_id`, and `reply_to_message_id` match the existing runtime source/event anchor. Add two sequential status/delta events and assert their sequence numbers are increasing and use the same per-message send lock.

- [ ] **Step 5: Run runtime tests and commit**

Run:

```bash
python -m pytest tests/unit/test_hook_runtime.py -q
```

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/hook_runtime.py tests/unit/test_hook_runtime.py
git commit -m "feat: emit context compaction notices"
```

### Task 3: Store and render the runtime phase

**Files:**
- Modify: `hermes_feishu_card/session.py:65-274`
- Modify: `hermes_feishu_card/render.py:76-226`
- Modify: `tests/unit/test_session.py`
- Modify: `tests/unit/test_render.py`

**Interfaces:**
- Produces: `CardSession.runtime_phase_text: str`.
- Produces Header priority: pending interaction, runtime phase, tool preview, configured title.

- [ ] **Step 1: Write failing session lifecycle tests**

Create a `system.notice` event with the compaction fields and assert `runtime_phase_text == "正在压缩上下文"`. Apply each of `thinking.delta`, `answer.delta`, and `tool.updated` in separate parametrized cases and assert the phase clears. Apply completed/failed events and assert it clears there too.

Add a generic session notice test asserting it does not set runtime phase.

- [ ] **Step 2: Run session tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_session.py -k "runtime_phase or compaction" -q
```

Expected: FAIL because `runtime_phase_text` does not exist.

- [ ] **Step 3: Implement the session field and lifecycle**

Add `runtime_phase_text: str = ""`. At the beginning of applying `thinking.delta`, `answer.delta`, and `tool.updated`, clear it. For `system.notice` only set it when:

```python
notice_kind == "context-compaction" and phase == "started"
```

Use the event title or the fixed Chinese title. Clear it in both terminal branches. Keep it out of completed answer text, footer, summary, and serialized diagnostics.

- [ ] **Step 4: Write failing Header-priority tests**

Assert:

1. compaction alone makes Header title `正在压缩上下文`;
2. a pending interaction title wins over compaction;
3. compaction wins over `latest_tool_preview` and no tool subtitle is shown while compaction is active;
4. the next tool event restores configured title plus tool subtitle;
5. completed cards contain no compaction text.

- [ ] **Step 5: Run render tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_render.py -k "compaction or runtime_phase" -q
```

Expected: FAIL because renderer currently uses only interaction/tool preview.

- [ ] **Step 6: Implement Header priority**

Change `runtime_header_text`:

```python
if pending_interaction:
    return normalized_prompt
if self.status in {"completed", "failed"}:
    return ""
if self.runtime_phase_text:
    return self.runtime_phase_text
return self.latest_tool_preview
```

Change `_runtime_header_summary` to return `""` while `runtime_phase_text` is non-empty, so the phase is the Header title and an older tool preview cannot become a competing subtitle.

- [ ] **Step 7: Run session/render tests and commit**

Run:

```bash
python -m pytest tests/unit/test_session.py tests/unit/test_render.py -q
```

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/session.py hermes_feishu_card/render.py tests/unit/test_session.py tests/unit/test_render.py
git commit -m "feat: render context compaction runtime phase"
```

### Task 4: Allow only compaction-start to create the primary card

**Files:**
- Modify: `hermes_feishu_card/server.py:1971-2176`
- Modify: `tests/integration/test_server.py`

**Interfaces:**
- Produces: `_is_compaction_session_start(event) -> bool`.
- Extends session creation without changing `SESSION_CREATING_EVENTS` globally.

- [ ] **Step 1: Write failing existing-card and no-card integration tests**

For an existing session, post compaction and assert one existing Feishu message plus a PATCH/update whose Header shows the phase.

For no session, post exact compaction-start with `create_session=True`; assert one send, one session, the original topic/private reply anchor, and then post `answer.delta` to the same message id and assert it updates that same card.

Add negative cases changing one field at a time: wrong kind, wrong phase, missing/false `create_session`, independent scope. Assert no session/card is created.

- [ ] **Step 2: Run focused server tests and verify RED**

Run:

```bash
python -m pytest tests/integration/test_server.py -k "compaction" -q
```

Expected: FAIL because the current no-session notice guard returns `applied=False`.

- [ ] **Step 3: Implement the narrow predicate and session creation path**

Add:

```python
def _is_compaction_session_start(event: SidecarEvent) -> bool:
    data = event.data if isinstance(event.data, dict) else {}
    return (
        event.event == "system.notice"
        and str(data.get("notice_kind") or "") == "context-compaction"
        and str(data.get("phase") or "") == "started"
        and data.get("create_session") is True
        and str(data.get("notice_scope") or "session") == "session"
    )
```

Exclude this predicate from the early no-session notice rejection. In the later creation condition use:

```python
if event.event in SESSION_CREATING_EVENTS or _is_compaction_session_start(event):
```

Reuse the standard `CardSession`, routing, card config, send, aliases, reply anchor, and delivery-outcome path. Do not call independent-notice helpers.

- [ ] **Step 4: Add sequence-race coverage**

Post a newer answer delta followed by an older compaction sequence and assert the stale compaction event is ignored and cannot overwrite the Header/body. Add terminal-after-compaction and assert no phase remains.

- [ ] **Step 5: Run server and runtime matrix and commit**

Run:

```bash
python -m pytest \
  tests/unit/test_hook_runtime.py \
  tests/unit/test_session.py \
  tests/unit/test_render.py \
  tests/integration/test_server.py -q
```

Expected: PASS.

Commit:

```bash
git add hermes_feishu_card/server.py tests/integration/test_server.py
git commit -m "feat: create primary card for compaction start"
```

### Task 5: Optional capability diagnostics and release-B acceptance

**Files:**
- Modify: Hermes detection module selected by `tests/unit/test_installer_detection.py`
- Modify: `hermes_feishu_card/diagnostics.py`
- Modify: `tests/unit/test_installer_detection.py`
- Modify: `tests/unit/test_diagnostics.py`
- Modify: `docs/wiki/event-flow.md`
- Modify: `docs/wiki/maintenance-guide.md`
- Modify: `docs/wiki/feishu-acceptance.md`
- Modify: `tests/unit/test_docs.py`

**Interfaces:**
- Produces optional capability key `status_callback`.
- Documents unsupported callback as partial compatibility, not install failure.

- [ ] **Step 1: Locate the existing capability detector before editing**

Run:

```bash
rg -n "capabilities|thinking_delta_callback|cron_delivery" hermes_feishu_card tests/unit/test_installer_detection.py
```

Use the exact existing detector file returned by the command; do not create a second detector module.

- [ ] **Step 2: Write failing detector/diagnostic tests**

With and without the callback fixture, assert `capabilities["status_callback"]` is true/false. For false, assert doctor compatibility is partial and the finding says context-compaction visibility is unavailable while installation remains supported.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_installer_detection.py tests/unit/test_diagnostics.py -k "status_callback or compaction" -q
```

Expected: FAIL because the capability key is absent.

- [ ] **Step 4: Implement detection and redacted doctor explanation**

Detect an AST callback named `_status_callback_sync` with arguments `event_type` and `message`, inside the supported message handler and with the same outer-name requirements used by the patcher. Add a concise doctor message; never print source snippets or user status text.

- [ ] **Step 5: Update maintainer and acceptance docs**

Document the pre-filter callback, exact marker, one-card lifecycle, fail-open compatibility, and a real long-session smoke checklist. Explicitly reject silence watchdog and percentage progress.

- [ ] **Step 6: Run release-B focused/full gates**

Run:

```bash
python -m pytest \
  tests/unit/test_patcher.py \
  tests/integration/test_cli_install.py \
  tests/unit/test_hook_runtime.py \
  tests/unit/test_session.py \
  tests/unit/test_render.py \
  tests/integration/test_server.py \
  tests/unit/test_installer_detection.py \
  tests/unit/test_diagnostics.py \
  tests/unit/test_docs.py -q
python -m pytest -q
git diff --check
```

Expected: all tests pass and `git diff --check` prints nothing.

- [ ] **Step 7: Commit and perform real long-session smoke**

```bash
git add hermes_feishu_card tests docs/wiki
git commit -m "docs: document compaction visibility support"
```

Install through the patcher into the actual Hermes runtime, confirm both Gateway and sidecar import from the intended environment, trigger real compaction, and record redacted evidence that exactly one card remains, the phase becomes visible, subsequent output clears it, and no gray native status leaks. Version/tag/release work occurs only after this smoke and the text-size implementation both pass.
