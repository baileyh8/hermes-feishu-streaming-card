# Hermes Event Forwarding Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the installed Hermes hook from a safe `pass` placeholder into a real, fail-open event forwarding path from Hermes to the local sidecar.

**Architecture:** Keep the hook block tiny and owned by the installer. Put extraction, event building, sequencing, configuration, and HTTP dispatch in a new `hermes_feishu_card.hook_runtime` module that can be unit-tested without real Hermes or real Feishu. Continue to use fake Feishu clients; this phase does not implement CardKit HTTP APIs.

**Tech Stack:** Python 3.9+, standard library `asyncio`, `json`, `os`, `time`, `urllib.request`, `urllib.error`, `hashlib`, `weakref`; existing `aiohttp` only for tests and sidecar server; pytest and pytest-asyncio.

---

## File Structure

- Create `hermes_feishu_card/hook_runtime.py`: runtime used by the installed Hermes hook. It owns environment config parsing, context extraction, event sequencing, JSON payload construction, and fail-open HTTP dispatch.
- Create `tests/unit/test_hook_runtime.py`: pure unit tests for config, extraction, event payload building, sequencing, disabled mode, and fail-open sender behavior.
- Modify `hermes_feishu_card/install/patcher.py`: render the real hook block that imports and calls `emit_from_hermes_locals(locals())`; support recognizing/removing the phase-one placeholder block for restore and upgrade.
- Modify `tests/unit/test_patcher.py`: update expected hook block tests and add migration tests for placeholder-to-real block.
- Modify `tests/fixtures/hermes_v2026_4_23/gateway/run.py`: expand fixture handler enough to execute a patched async handler and return a value for integration tests.
- Modify `tests/integration/test_cli_install.py`: keep install/restore safety tests passing with the new hook block.
- Create `tests/integration/test_hook_runtime_integration.py`: use a local aiohttp mock sidecar to verify the installed hook can post valid event JSON and fail open when sidecar is down.
- Modify `README.md`, `docs/architecture.md`, `docs/event-protocol.md`, `docs/testing.md`, `TODO.md`: update phase boundary after event forwarding is implemented.
- Modify `tests/unit/test_docs.py`: add a low-brittleness assertion that docs mention Hermes-to-sidecar event forwarding while still marking Feishu CardKit integration as future work.

## Event Payload Contract

`hook_runtime.emit_from_hermes_locals(local_vars)` will build a `SidecarEvent`-compatible dict:

```python
{
    "schema_version": "1",
    "event": "message.started",
    "conversation_id": "oc_abc",
    "message_id": "msg_123",
    "chat_id": "oc_abc",
    "platform": "feishu",
    "sequence": 0,
    "created_at": 1777017600.0,
    "data": {},
}
```

This phase guarantees `message.started` and a terminal event when the handler locals provide enough information. It also supports `thinking.delta`, `answer.delta`, and `tool.updated` when explicit local fields are present. It does not guess hidden Hermes internals.

## Task 1: Hook Runtime Config, Extraction, And Event Building

**Files:**
- Create: `hermes_feishu_card/hook_runtime.py`
- Create: `tests/unit/test_hook_runtime.py`

- [ ] **Step 1: Write failing tests for runtime config**

Add these tests to `tests/unit/test_hook_runtime.py`:

```python
import os

import pytest

from hermes_feishu_card import hook_runtime


@pytest.fixture(autouse=True)
def clear_hook_env(monkeypatch):
    for name in (
        "HERMES_FEISHU_CARD_ENABLED",
        "HERMES_FEISHU_CARD_EVENT_URL",
        "HERMES_FEISHU_CARD_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)
    hook_runtime.reset_runtime_state()


def test_load_runtime_config_defaults():
    config = hook_runtime.load_runtime_config()

    assert config.enabled is True
    assert config.event_url == "http://127.0.0.1:8765/events"
    assert config.timeout_seconds == 0.8


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "OFF"])
def test_load_runtime_config_disabled_values(monkeypatch, value):
    monkeypatch.setenv("HERMES_FEISHU_CARD_ENABLED", value)

    assert hook_runtime.load_runtime_config().enabled is False


def test_load_runtime_config_custom_url_and_timeout(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://localhost:9000/events")
    monkeypatch.setenv("HERMES_FEISHU_CARD_TIMEOUT_MS", "250")

    config = hook_runtime.load_runtime_config()

    assert config.event_url == "http://localhost:9000/events"
    assert config.timeout_seconds == 0.25


@pytest.mark.parametrize("value", ["1", "49", "5001", "abc"])
def test_load_runtime_config_invalid_timeout_falls_back(monkeypatch, value):
    monkeypatch.setenv("HERMES_FEISHU_CARD_TIMEOUT_MS", value)

    assert hook_runtime.load_runtime_config().timeout_seconds == 0.8
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/unit/test_hook_runtime.py -q`

Expected: FAIL with `ImportError` or `AttributeError` because `hook_runtime` does not exist.

- [ ] **Step 3: Implement minimal runtime config**

Create `hermes_feishu_card/hook_runtime.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
import os

DEFAULT_EVENT_URL = "http://127.0.0.1:8765/events"
DEFAULT_TIMEOUT_SECONDS = 0.8


@dataclass(frozen=True)
class RuntimeConfig:
    enabled: bool
    event_url: str
    timeout_seconds: float


def reset_runtime_state() -> None:
    _SEQUENCES.clear()


_SEQUENCES: dict[str, int] = {}


def load_runtime_config() -> RuntimeConfig:
    enabled_value = os.environ.get("HERMES_FEISHU_CARD_ENABLED", "1").strip().lower()
    enabled = enabled_value not in {"0", "false", "no", "off"}
    event_url = os.environ.get("HERMES_FEISHU_CARD_EVENT_URL", DEFAULT_EVENT_URL).strip()
    if not event_url:
        event_url = DEFAULT_EVENT_URL
    timeout_seconds = _timeout_from_env(os.environ.get("HERMES_FEISHU_CARD_TIMEOUT_MS"))
    return RuntimeConfig(
        enabled=enabled,
        event_url=event_url,
        timeout_seconds=timeout_seconds,
    )


def _timeout_from_env(value: str | None) -> float:
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        timeout_ms = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    if not 50 <= timeout_ms <= 5000:
        return DEFAULT_TIMEOUT_SECONDS
    return timeout_ms / 1000.0
```

- [ ] **Step 4: Run config tests**

Run: `python3 -m pytest tests/unit/test_hook_runtime.py -q`

Expected: PASS for the config tests.

- [ ] **Step 5: Add failing tests for context extraction**

Append:

```python
class MessageObject:
    def __init__(self):
        self.open_chat_id = "oc_object"
        self.message_id = "msg_object"
        self.text = "对象文本"


def test_build_event_extracts_direct_fields():
    payload = hook_runtime.build_event(
        "message.started",
        {
            "chat_id": "oc_direct",
            "message_id": "msg_direct",
            "conversation_id": "conv_direct",
        },
    )

    assert payload["event"] == "message.started"
    assert payload["chat_id"] == "oc_direct"
    assert payload["message_id"] == "msg_direct"
    assert payload["conversation_id"] == "conv_direct"
    assert payload["sequence"] == 0
    assert payload["platform"] == "feishu"
    assert payload["data"] == {}


def test_build_event_extracts_nested_message_object():
    payload = hook_runtime.build_event("answer.delta", {"message": MessageObject()})

    assert payload["chat_id"] == "oc_object"
    assert payload["message_id"] == "msg_object"
    assert payload["conversation_id"] == "oc_object"
    assert payload["data"] == {"text": "对象文本"}


def test_build_event_returns_none_when_chat_id_missing():
    assert hook_runtime.build_event("message.started", {"message_id": "msg"}) is None


def test_build_event_uses_stable_message_id_fallback():
    local_vars = {"chat_id": "oc_abc", "created_at": 1777017600.0}

    first = hook_runtime.build_event("message.started", local_vars)
    second = hook_runtime.build_event("message.started", local_vars)

    assert first["message_id"] == second["message_id"]
    assert first["message_id"].startswith("hfc_")


def test_build_event_increments_sequence_per_message():
    local_vars = {"chat_id": "oc_abc", "message_id": "msg_seq"}

    first = hook_runtime.build_event("message.started", local_vars)
    second = hook_runtime.build_event("answer.delta", {**local_vars, "text": "hi"})

    assert first["sequence"] == 0
    assert second["sequence"] == 1
```

- [ ] **Step 6: Run tests to verify extraction failures**

Run: `python3 -m pytest tests/unit/test_hook_runtime.py -q`

Expected: FAIL with `AttributeError: module ... has no attribute 'build_event'`.

- [ ] **Step 7: Implement extraction and event building**

Extend `hook_runtime.py`:

```python
from hashlib import sha256
import time
from typing import Any

SUPPORTED_RUNTIME_EVENTS = {
    "message.started",
    "thinking.delta",
    "answer.delta",
    "tool.updated",
    "message.completed",
    "message.failed",
}


def build_event(event_name: str, local_vars: dict[str, Any]) -> dict[str, Any] | None:
    if event_name not in SUPPORTED_RUNTIME_EVENTS:
        return None
    chat_id = _first_string(local_vars, ("chat_id", "open_chat_id", "receive_id"))
    message_obj = local_vars.get("message")
    if chat_id is None:
        chat_id = _first_attr_string(message_obj, ("chat_id", "open_chat_id", "receive_id"))
    if chat_id is None:
        return None

    conversation_id = (
        _first_string(local_vars, ("conversation_id", "thread_id", "session_id"))
        or _first_attr_string(message_obj, ("conversation_id", "thread_id", "session_id"))
        or chat_id
    )
    created_at = _created_at(local_vars)
    message_id = (
        _first_string(local_vars, ("message_id", "msg_id"))
        or _first_attr_string(message_obj, ("message_id", "msg_id"))
        or _fallback_message_id(conversation_id, chat_id, created_at)
    )
    sequence = _next_sequence(message_id)
    return {
        "schema_version": "1",
        "event": event_name,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "chat_id": chat_id,
        "platform": "feishu",
        "sequence": sequence,
        "created_at": created_at,
        "data": _event_data(event_name, local_vars, message_obj),
    }


def _event_data(
    event_name: str, local_vars: dict[str, Any], message_obj: Any
) -> dict[str, Any]:
    if event_name in {"thinking.delta", "answer.delta"}:
        text = _first_string(local_vars, ("text", "delta", "delta_text", "content"))
        if text is None:
            text = _first_attr_string(message_obj, ("text", "content"))
        return {"text": text or ""}
    if event_name == "tool.updated":
        tool_id = _first_string(local_vars, ("tool_id", "tool_call_id", "name")) or "tool"
        name = _first_string(local_vars, ("name", "tool_name")) or tool_id
        status = _first_string(local_vars, ("status", "tool_status")) or "running"
        detail = _first_string(local_vars, ("detail", "tool_detail")) or ""
        return {"tool_id": tool_id, "name": name, "status": status, "detail": detail}
    if event_name == "message.completed":
        answer = _first_string(local_vars, ("answer", "final_answer", "text", "content")) or ""
        return {"answer": answer}
    if event_name == "message.failed":
        error = _first_string(local_vars, ("error", "exception")) or "消息处理失败"
        return {"error": error}
    return {}


def _first_string(source: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = source.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_attr_string(obj: Any, names: tuple[str, ...]) -> str | None:
    if obj is None:
        return None
    for name in names:
        value = getattr(obj, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(obj, dict):
        return _first_string(obj, names)
    return None


def _created_at(local_vars: dict[str, Any]) -> float:
    value = local_vars.get("created_at")
    try:
        return float(value)
    except (TypeError, ValueError):
        return time.time()


def _fallback_message_id(conversation_id: str, chat_id: str, created_at: float) -> str:
    raw = f"{conversation_id}:{chat_id}:{created_at:.3f}".encode("utf-8")
    return "hfc_" + sha256(raw).hexdigest()[:16]


def _next_sequence(message_id: str) -> int:
    sequence = _SEQUENCES.get(message_id, -1) + 1
    _SEQUENCES[message_id] = sequence
    return sequence
```

- [ ] **Step 8: Run hook runtime tests**

Run: `python3 -m pytest tests/unit/test_hook_runtime.py -q`

Expected: all Task 1 tests pass.

- [ ] **Step 9: Commit Task 1**

```bash
git add hermes_feishu_card/hook_runtime.py tests/unit/test_hook_runtime.py
git commit -m "feat: add Hermes hook runtime event builder"
```

## Task 2: Non-Blocking Fail-Open HTTP Dispatch

**Files:**
- Modify: `hermes_feishu_card/hook_runtime.py`
- Modify: `tests/unit/test_hook_runtime.py`

- [ ] **Step 1: Add failing tests for emit behavior**

Append:

```python
import asyncio


class SenderProbe:
    def __init__(self):
        self.payloads = []
        self.raise_error = False

    async def __call__(self, url, payload, timeout):
        self.payloads.append((url, payload, timeout))
        if self.raise_error:
            raise RuntimeError("network failed")


async def drain_tasks():
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_schedules_sender(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    result = hook_runtime.emit_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.started",
    )
    await drain_tasks()

    assert result is True
    assert len(sender.payloads) == 1
    url, payload, timeout = sender.payloads[0]
    assert url == "http://sidecar.test/events"
    assert payload["event"] == "message.started"
    assert timeout == 0.8


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_disabled_does_not_send(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_ENABLED", "0")

    result = hook_runtime.emit_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.started",
    )
    await drain_tasks()

    assert result is False
    assert sender.payloads == []


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_sender_error_is_swallowed(monkeypatch):
    sender = SenderProbe()
    sender.raise_error = True
    monkeypatch.setattr(hook_runtime, "_post_json", sender)

    result = hook_runtime.emit_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.started",
    )
    await drain_tasks()

    assert result is True
    assert len(sender.payloads) == 1


def test_emit_from_hermes_locals_without_running_loop_fails_open(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)

    result = hook_runtime.emit_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.started",
    )

    assert result is False
    assert sender.payloads == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/unit/test_hook_runtime.py -q`

Expected: FAIL because `emit_from_hermes_locals` and `_post_json` are missing.

- [ ] **Step 3: Implement fail-open scheduling and HTTP POST**

Add:

```python
import asyncio
import json
from urllib import error, request


def emit_from_hermes_locals(
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
        loop = asyncio.get_running_loop()
        loop.create_task(_send_fail_open(config.event_url, payload, config.timeout_seconds))
        return True
    except Exception:
        return False


async def _send_fail_open(url: str, payload: dict[str, Any], timeout: float) -> None:
    try:
        await _post_json(url, payload, timeout)
    except Exception:
        return


async def _post_json(url: str, payload: dict[str, Any], timeout: float) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _open_request, req, timeout)


def _open_request(req: request.Request, timeout: float) -> None:
    try:
        with request.urlopen(req, timeout=timeout) as response:
            response.read()
    except (OSError, error.URLError, error.HTTPError):
        raise
```

- [ ] **Step 4: Run hook runtime tests**

Run: `python3 -m pytest tests/unit/test_hook_runtime.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add hermes_feishu_card/hook_runtime.py tests/unit/test_hook_runtime.py
git commit -m "feat: send Hermes hook events fail open"
```

## Task 3: Patcher Real Hook Block And Placeholder Upgrade

**Files:**
- Modify: `hermes_feishu_card/install/patcher.py`
- Modify: `tests/unit/test_patcher.py`

- [ ] **Step 1: Add failing tests for real hook block**

Add to `tests/unit/test_patcher.py`:

```python
def test_apply_patch_inserts_real_runtime_hook_call():
    content = (
        "async def _handle_message_with_agent(message):\n"
        "    return message\n"
    )

    patched = patcher.apply_patch(content)

    assert "from hermes_feishu_card.hook_runtime import emit_from_hermes_locals" in patched
    assert "_hfc_emit(locals())" in patched
    assert "            pass\n" not in patched


def test_apply_patch_upgrades_phase_one_placeholder_block():
    placeholder = (
        "async def _handle_message_with_agent(message):\n"
        "    # HERMES_FEISHU_CARD_PATCH_BEGIN\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        pass\n"
        "    # HERMES_FEISHU_CARD_PATCH_END\n"
        "    return message\n"
    )

    upgraded = patcher.apply_patch(placeholder)

    assert "emit_from_hermes_locals" in upgraded
    assert "        pass\n" not in upgraded
    assert upgraded.count(patcher.PATCH_BEGIN) == 1


def test_remove_patch_removes_phase_one_placeholder_block():
    placeholder = (
        "async def _handle_message_with_agent(message):\n"
        "    # HERMES_FEISHU_CARD_PATCH_BEGIN\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        pass\n"
        "    # HERMES_FEISHU_CARD_PATCH_END\n"
        "    return message\n"
    )

    restored = patcher.remove_patch(placeholder)

    assert patcher.PATCH_BEGIN not in restored
    assert "    return message\n" in restored
```

- [ ] **Step 2: Run patcher tests to verify failure**

Run: `python3 -m pytest tests/unit/test_patcher.py -q`

Expected: FAIL because patcher still emits the placeholder block and treats placeholder as current block.

- [ ] **Step 3: Update hook rendering and owned block recognition**

In `patcher.py`, change `_render_hook_block()` to:

```python
def _render_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        (
            f"{inner_indent}from hermes_feishu_card.hook_runtime "
            f"import emit_from_hermes_locals as _hfc_emit{newline}"
        ),
        f"{inner_indent}_hfc_emit(locals()){newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{PATCH_END}{newline}",
    ]
```

Add a helper for the phase-one placeholder shape:

```python
def _render_placeholder_hook_block(indent: str, newline: str):
    inner_indent = _child_indent(indent)
    return [
        f"{indent}{PATCH_BEGIN}{newline}",
        f"{indent}try:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}except Exception:{newline}",
        f"{inner_indent}pass{newline}",
        f"{indent}{PATCH_END}{newline}",
    ]
```

Update `_find_owned_block()` so current block and placeholder block are both owned. Return enough information for `apply_patch()` to upgrade placeholders:

```python
def _find_owned_block(content: str):
    ...
    current = _render_hook_block(indent, newline)
    placeholder = _render_placeholder_hook_block(indent, newline)
    actual = lines[begin_index : end_index + 1]
    if actual not in (current, placeholder):
        raise ValueError("corrupt patch markers")
    ...
    return begin_index, end_index
```

Then update `apply_patch()`:

```python
owned_block = _find_owned_block(content)
if owned_block is not None:
    lines = content.splitlines(keepends=True)
    begin_index, end_index = owned_block
    indent = _leading_whitespace(_strip_line_ending(lines[begin_index]))
    newline = _line_ending(lines[begin_index]) or _detect_newline(content)
    expected = _render_hook_block(indent, newline)
    if lines[begin_index : end_index + 1] == expected:
        return content
    return "".join(lines[:begin_index] + expected + lines[end_index + 1 :])
```

- [ ] **Step 4: Run patcher tests**

Run: `python3 -m pytest tests/unit/test_patcher.py -q`

Expected: all patcher tests pass. Existing tests that currently expect the placeholder body must be updated in the same patch to assert the runtime import, `_hfc_emit(locals())`, and the single `except` body `pass`.

- [ ] **Step 5: Run install CLI tests**

Run: `python3 -m pytest tests/integration/test_cli_install.py -q`

Expected: pass; install/restore safety still works with the real hook block.

- [ ] **Step 6: Commit Task 3**

```bash
git add hermes_feishu_card/install/patcher.py tests/unit/test_patcher.py tests/integration/test_cli_install.py
git commit -m "feat: install Hermes event forwarding hook"
```

## Task 4: Installed Hook Fixture Execution

**Files:**
- Modify: `tests/fixtures/hermes_v2026_4_23/gateway/run.py`
- Create: `tests/integration/test_hook_runtime_integration.py`

- [ ] **Step 1: Expand fixture handler**

Change `tests/fixtures/hermes_v2026_4_23/gateway/run.py` to:

```python
async def _handle_message_with_agent(message, hooks):
    chat_id = getattr(message, "chat_id", "oc_fixture")
    message_id = getattr(message, "message_id", "msg_fixture")
    text = getattr(message, "text", "fixture answer")
    hooks.emit("agent:end", {"message": message})
    return text
```

- [ ] **Step 2: Verify detection fixture tests still pass**

Run: `python3 -m pytest tests/unit/test_installer_detection.py -q`

Expected: pass.

- [ ] **Step 3: Add integration test for installed hook fail-open return value**

Create `tests/integration/test_hook_runtime_integration.py`:

```python
import asyncio
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"


class Message:
    chat_id = "oc_fixture"
    message_id = "msg_fixture"
    text = "fixture answer"


class Hooks:
    def __init__(self):
        self.events = []

    def emit(self, name, data):
        self.events.append((name, data))


def copy_hermes(tmp_path):
    hermes_dir = tmp_path / "hermes"
    shutil.copytree(FIXTURE, hermes_dir)
    return hermes_dir


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def load_run_py(path):
    spec = importlib.util.spec_from_file_location("fixture_run", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_installed_hook_preserves_handler_return_when_sidecar_down(tmp_path, monkeypatch):
    hermes_dir = copy_hermes(tmp_path)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:9/events")

    install = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install.returncode == 0, install.stderr
    module = load_run_py(hermes_dir / "gateway" / "run.py")
    hooks = Hooks()

    result = asyncio.run(module._handle_message_with_agent(Message(), hooks))

    assert result == "fixture answer"
    assert len(hooks.events) == 1
    assert hooks.events[0][0] == "agent:end"
    assert hooks.events[0][1]["message"].chat_id == "oc_fixture"
```

- [ ] **Step 4: Add integration test with mock sidecar**

Append:

```python
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


async def test_installed_hook_posts_started_event_to_mock_sidecar(tmp_path, monkeypatch):
    received = []

    async def events(request):
        received.append(await request.json())
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/events", events)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        hermes_dir = copy_hermes(tmp_path)
        monkeypatch.setenv(
            "HERMES_FEISHU_CARD_EVENT_URL",
            str(client.make_url("/events")),
        )
        install = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
        assert install.returncode == 0, install.stderr
        module = load_run_py(hermes_dir / "gateway" / "run.py")

        result = await module._handle_message_with_agent(Message(), Hooks())
        await asyncio.sleep(0.1)

        assert result == "fixture answer"
        assert received
        assert received[0]["event"] == "message.started"
        assert received[0]["chat_id"] == "oc_fixture"
        assert received[0]["message_id"] == "msg_fixture"
    finally:
        await client.close()
```

- [ ] **Step 5: Run integration tests**

Run: `python3 -m pytest tests/integration/test_hook_runtime_integration.py -q`

Expected: pass.

- [ ] **Step 6: Run install integration tests**

Run: `python3 -m pytest tests/integration/test_cli_install.py -q`

Expected: pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add tests/fixtures/hermes_v2026_4_23/gateway/run.py tests/integration/test_hook_runtime_integration.py tests/integration/test_cli_install.py
git commit -m "test: verify installed Hermes hook forwarding"
```

## Task 5: Documentation And Scope Update

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/event-protocol.md`
- Modify: `docs/testing.md`
- Modify: `TODO.md`
- Modify: `tests/unit/test_docs.py`

- [x] **Step 1: Add failing doc tests**

Append to `tests/unit/test_docs.py`:

```python
def test_docs_describe_event_forwarding_but_not_cardkit_completion():
    docs = "\n".join(
        [
            read_doc("README.md"),
            read_doc("docs/architecture.md"),
            read_doc("TODO.md"),
        ]
    )

    assert "Hermes 到 sidecar" in docs or "Hermes -> sidecar" in docs
    assert "Feishu CardKit" in docs
    assert "仍未完成" in docs or "后续阶段" in docs
```

- [x] **Step 2: Run doc test to verify failure**

Run: `python3 -m pytest tests/unit/test_docs.py -q`

Expected: FAIL until docs are updated.

- [x] **Step 3: Update README**

In `README.md`, change the first-phase boundary paragraph to state:

```markdown
当前已完成第二阶段最小事件转发：安装后的 Hermes hook 会调用 `hermes_feishu_card.hook_runtime`，把可识别的 Hermes 消息上下文以 `SidecarEvent` JSON 发送到本机 sidecar `/events`。该链路 fail-open，sidecar 不可用时 Hermes 原生文本回复继续运行。

真实 Feishu CardKit 创建/更新仍未完成，当前卡片侧联调使用 fake client 或 mock server。
```

- [x] **Step 4: Update architecture and TODO**

In `docs/architecture.md`, update "第一阶段的 hook block 仍是安全占位" to:

```markdown
第二阶段 hook block 已升级为真实 runtime 调用，复杂提取和发送逻辑在 `hermes_feishu_card.hook_runtime` 中测试。hook 仍保持 fail-open，不直接包含飞书凭据或长逻辑。
```

In `TODO.md`, mark "补齐真实 Hermes 运行环境下的最小 hook 事件转发验证" as done for fixture/mock sidecar, and add a remaining item:

```markdown
- [ ] 在真实 Hermes Gateway 进程中做人工 smoke test。
```

- [x] **Step 5: Update testing docs**

Add to `docs/testing.md`:

````markdown
## Hermes hook runtime tests

```bash
python3 -m pytest tests/unit/test_hook_runtime.py tests/integration/test_hook_runtime_integration.py -q
```

这些测试只使用 fixture 和 mock sidecar，不访问真实飞书。
````

- [x] **Step 6: Run docs tests**

Run: `python3 -m pytest tests/unit/test_docs.py -q`

Expected: pass.

- [x] **Step 7: Commit Task 5**

```bash
git add README.md docs/architecture.md docs/event-protocol.md docs/testing.md TODO.md tests/unit/test_docs.py
git commit -m "docs: document Hermes event forwarding phase"
```

## Task 6: Final Verification

**Files:**
- Modify: none unless verification exposes defects.

- [x] **Step 1: Run full test suite**

Run: `python3 -m pytest -q`

Expected: all tests pass.

- [x] **Step 2: Run targeted hook tests**

Run: `python3 -m pytest tests/unit/test_hook_runtime.py tests/unit/test_patcher.py tests/integration/test_hook_runtime_integration.py tests/integration/test_cli_install.py -q`

Expected: all targeted tests pass.

- [x] **Step 3: Run doctor**

Run: `python3 -m hermes_feishu_card.cli doctor --config config.yaml.example --skip-hermes`

Expected: stdout contains `doctor: ok`; exit code 0.

- [x] **Step 4: Run fixture install/restore smoke test**

Run:

```bash
tmpdir="$(mktemp -d)"
cp -R tests/fixtures/hermes_v2026_4_23 "$tmpdir/hermes"
python3 -m hermes_feishu_card.cli install --hermes-dir "$tmpdir/hermes" --yes
python3 -m hermes_feishu_card.cli restore --hermes-dir "$tmpdir/hermes" --yes
rg "HERMES_FEISHU_CARD_PATCH_BEGIN|emit_from_hermes_locals" "$tmpdir/hermes/gateway/run.py" && exit 1 || true
```

Expected: install and restore both succeed; restored `run.py` contains no patch marker or runtime import.

- [x] **Step 5: Check git status**

Run: `git status --short`

Expected: only known unrelated `.DS_Store` may remain modified.

- [x] **Step 6: Fix and commit only if verification exposes defects**

If defects appear, fix them with focused tests and commit:

```bash
git add hermes_feishu_card tests docs README.md TODO.md
git commit -m "fix: stabilize Hermes event forwarding"
```

If no defects appear, do not create an empty commit.
