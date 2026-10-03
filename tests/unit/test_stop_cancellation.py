"""A /stop cancellation can precede the executor's interrupted result."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.install import patcher


BASE = Path(__file__).parents[1] / "fixtures/hermes_decomposed/gateway/platforms/base.py"
CONTRACT = '''
    async def _dispatch_active_session_command(self, event, session_key, cmd):
        current_guard = self._active_sessions.get(session_key)
        command_guard = asyncio.Event()
        self._active_sessions[session_key] = command_guard
        try:
            await self._dispatch_inline_reply(event, log_cmd=cmd)
            await self.cancel_session_processing(session_key, release_guard=False, discard_pending=False)
        except Exception:
            raise
        await self._drain_pending_after_session_command(session_key, command_guard)

    async def cancel_session_processing(self, session_key, *, release_guard=True, discard_pending=True):
        task = self._session_tasks.pop(session_key, None)
        if task is not None and not task.done():
            self._expected_cancelled_tasks.add(task)
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                pass
'''


def source():
    return BASE.read_text() + CONTRACT


def event(turn, *, profile="fixture", chat="chat", thread="topic", platform="feishu"):
    return SimpleNamespace(message_id=turn, internal=False, source=SimpleNamespace(
        profile_id=profile, platform=platform, chat_id=chat, thread_id=thread))


def route(event):
    s = event.source
    return (s.profile_id, s.platform, s.chat_id, s.thread_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", ["stop", "new", "reset"])
async def test_stop_cancels_handler_before_worker_result(monkeypatch, cmd):
    emitted, order = [], []
    async def emit(values, *, event_name):
        emitted.append((event_name, values))
        order.append("terminal")
        return True
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
    namespace = {"asyncio": asyncio}
    exec(patcher.apply_base_patch(source()), namespace)
    adapter = namespace["BasePlatformAdapter"]()
    old_event, stop_event = event("original"), event("command")
    key = route(old_event)
    adapter._event_session_key = route
    adapter._active_sessions = {key: asyncio.Event()}
    adapter._session_tasks = {}
    adapter._expected_cancelled_tasks = set()
    adapter._background_tasks = set()
    ready, worker_finished = asyncio.Event(), asyncio.Event()
    async def original_handler():
        owned_source = hook_runtime.queued_followup_source(old_event.source, old_event)
        hook_runtime.build_event("message.started", {"source": owned_source, "event": old_event})
        ready.set()
        try:
            await worker_finished.wait()
            order.append("worker-result")
        except asyncio.CancelledError:
            order.append("handler-cancelled")
            raise
    task = asyncio.create_task(original_handler())
    adapter._session_tasks[key] = task
    await ready.wait()
    async def reply(*args, **kwargs):
        order.append("stop-reply")
    async def drain(*args):
        order.append("drain")
    adapter._dispatch_inline_reply = reply
    adapter._drain_pending_after_session_command = drain
    await adapter._dispatch_active_session_command(stop_event, key, cmd)
    await asyncio.gather(*adapter._background_tasks)
    assert task.cancelled()
    assert not worker_finished.is_set()
    assert len(emitted) == (1 if cmd == "stop" else 0)
    if cmd == "stop":
        name, values = emitted[0]
        assert name == "message.failed"
        assert values["turn_id"] == values["message_id"] == "original"
        assert values["source"].thread_id == "topic"
        assert values["source"].profile_id == "fixture"
        assert order == ["stop-reply", "handler-cancelled", "drain", "terminal"]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_stop_patch_roundtrip(newline):
    original = source().replace("\n", newline)
    patched = patcher.apply_base_patch(original)
    assert "HERMES_FEISHU_CARD_BASE_STOP_CANCEL_PATCH_BEGIN" in patched
    assert patcher.apply_base_patch(patched) == patched
    assert patcher.remove_base_patch(patched) == original
    assert patcher.remove_base_patch_lenient(patched) == original


@pytest.mark.parametrize("before,after", [
    ("release_guard=False, discard_pending=False", "release_guard=True, discard_pending=False"),
    ("cancel_session_processing(session_key,", "cancel_session_processing(other_key,"),
    ("_dispatch_inline_reply(event, log_cmd=cmd)", "_dispatch_inline_reply(other_event, log_cmd=cmd)"),
    ("_session_tasks.pop(session_key, None)", "_session_tasks.pop(other_key, None)"),
    ("task.cancel()", "other_task.cancel()"),
    ("_expected_cancelled_tasks.add(task)", "_expected_cancelled_tasks.add(other_task)"),
    ("asyncio.shield(task)", "asyncio.shield(other_task)"),
])
def test_stop_cancel_contract_drift_is_refused(before, after):
    with pytest.raises(ValueError, match="stop cancellation"):
        patcher.apply_base_patch(source().replace(before, after))


async def pending_owner():
    original = event("original")
    ready, release = asyncio.Event(), asyncio.Event()
    async def handler():
        s = hook_runtime.queued_followup_source(original.source, original)
        hook_runtime.build_event("message.started", {"source": s, "event": original})
        ready.set()
        await release.wait()
    task = asyncio.create_task(handler())
    await ready.wait()
    key = route(original)
    adapter = SimpleNamespace(_session_tasks={key: task}, _expected_cancelled_tasks=set(),
                              _event_session_key=route)
    return adapter, task, key, original, release


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["profile", "chat", "thread", "platform", "turn",
                                   "key", "unbound", "source_turn", "conversation",
                                   "expected_cancel", "already_cancelling"])
async def test_capture_requires_exact_live_owner(change):
    adapter, task, key, original, release = await pending_owner()
    command = event("command")
    s = task._hfc_stop_turn_owner[0]
    if change in {"profile", "chat", "thread", "platform"}:
        setattr(command.source, {"profile": "profile_id", "chat": "chat_id",
                                 "thread": "thread_id", "platform": "platform"}[change], "other")
    elif change == "turn":
        command.message_id = original.message_id
    elif change == "key":
        key = ("other",)
    elif change == "unbound":
        del task._hfc_stop_turn_owner
    elif change == "source_turn":
        s._hfc_turn_id = "newer-turn"
    elif change == "conversation":
        s._hfc_conversation_id = "other"
    elif change == "expected_cancel":
        adapter._expected_cancelled_tasks.add(task)
    elif change == "already_cancelling":
        task.cancel()
        # Public cancellation-in-progress observation was added in Python 3.11.
        if not hasattr(task, "cancelling"):
            adapter._expected_cancelled_tasks.add(task)
    try:
        assert hook_runtime.capture_stop_cancellation(adapter, command, key, "stop") is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["normal", "pending", "changed_source", "changed_owner",
                                    "duplicate", "transport_retry"])
async def test_cancelled_stop_terminal_proof_and_dedup(monkeypatch, outcome):
    adapter, task, key, original, release = await pending_owner()
    proof = hook_runtime.capture_stop_cancellation(adapter, event("command"), key, "stop")
    assert proof is not None
    calls = []
    async def emit(values, *, event_name):
        calls.append((values, event_name))
        return not (outcome == "transport_retry" and len(calls) == 1)
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
    try:
        if outcome == "normal":
            release.set()
            await task
        elif outcome != "pending":
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if outcome == "changed_source":
            proof[1][0].chat_id = "another-chat"
        elif outcome == "changed_owner":
            task._hfc_stop_turn_owner = tuple(list(task._hfc_stop_turn_owner))
        first = await hook_runtime.emit_cancelled_stop_turn_async(proof)
        assert first is (outcome == "duplicate")
        if outcome == "duplicate":
            assert not await hook_runtime.emit_cancelled_stop_turn_async(proof)
            assert len(calls) == 1
        elif outcome == "transport_retry":
            assert await hook_runtime.emit_cancelled_stop_turn_async(proof)
            assert len(calls) == 2
        else:
            assert not calls
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("before,after", [
    ("_hfc_schedule_stop(self, _hfc_stop_owner)", "_hfc_schedule_stop(self, None)"),
    ("HERMES_FEISHU_CARD_BASE_STOP_CANCEL_PATCH_END", "MISSING_END"),
])
def test_owned_stop_markers_reject_corruption(before, after):
    corrupt = patcher.apply_base_patch(source()).replace(before, after)
    for remove in (patcher.remove_base_patch, patcher.remove_base_patch_lenient):
        with pytest.raises(ValueError, match="stop cancellation"):
            remove(corrupt)


def test_old_base_without_cancel_contract_remains_supported():
    original = BASE.read_text()
    patched = patcher.apply_base_patch(original)
    assert "BASE_STOP_CANCEL" not in patched
    assert patcher.remove_base_patch(patched) == original


@pytest.mark.parametrize("docstring", [False, True])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_latest_main_same_session_requeue_cleanup_roundtrip(docstring, newline):
    prefix = ('        """Known cancellation contract."""\n' if docstring else "")
    prefix += "        self._requeue_counts.pop(session_key, None)\n"
    original = source().replace("        task = self._session_tasks.pop(session_key, None)",
                                prefix + "        task = self._session_tasks.pop(session_key, None)")
    original = original.replace("\n", newline)
    patched = patcher.apply_base_patch(original)
    assert "BASE_STOP_CANCEL_PATCH_BEGIN" in patched
    assert patcher.apply_base_patch(patched) == patched
    assert patcher.remove_base_patch(patched) == original


@pytest.mark.parametrize("prefix", [
    "self._requeue_counts.pop(other_session, None)",
    "await self._requeue_counts.pop(session_key, None)",
    "task = self._requeue_counts.pop(session_key, None)",
    "self._other_counts.pop(session_key, None)",
    "self._requeue_counts.pop(session_key, default=None)",
    "self._requeue_counts.pop(session_key, None)\n        self._requeue_counts.pop(session_key, None)",
    "self._requeue_counts.pop(session_key, None)\n        task = other_task",
])
def test_requeue_cleanup_drift_is_rejected(prefix):
    original = source().replace("        task = self._session_tasks.pop(session_key, None)",
                                "        " + prefix + "\n        task = self._session_tasks.pop(session_key, None)")
    with pytest.raises(ValueError, match="stop cancellation"):
        patcher.apply_base_patch(original)


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ["slow", "error", "timeout", "shutdown"])
async def test_stop_display_uses_bounded_shutdown_owned_background(monkeypatch, result):
    adapter, task, key, original, release = await pending_owner()
    adapter._active_sessions = {key: asyncio.Event()}
    adapter._background_tasks = set()
    namespace = {"asyncio": asyncio}
    exec(patcher.apply_base_patch(source()), namespace)
    cls = namespace["BasePlatformAdapter"]
    adapter.cancel_session_processing = lambda *a, **kw: cls.cancel_session_processing(adapter, *a, **kw)
    entered, unblock = asyncio.Event(), asyncio.Event()
    drained = []
    async def reply(*args, **kwargs):
        pass
    async def drain(*args):
        drained.append(True)
    async def emit(*args, **kwargs):
        entered.set()
        await unblock.wait()
        if result == "error":
            raise OSError("fixture transport failure")
        return True
    adapter._dispatch_inline_reply, adapter._drain_pending_after_session_command = reply, drain
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
    if result == "timeout":
        monkeypatch.setattr(hook_runtime, "TERMINAL_DELIVERY_RETRY_BUDGET_SECONDS", 0.01)
    await asyncio.wait_for(cls._dispatch_active_session_command(adapter, event("command"), key, "stop"), 0.2)
    assert drained == [True]
    await asyncio.wait_for(entered.wait(), 0.2)
    deliveries = list(adapter._background_tasks)
    assert len(deliveries) == 1
    assert not hook_runtime.schedule_cancelled_stop_turn(adapter, (task, task._hfc_stop_turn_owner))
    if result in {"slow", "error"}:
        assert not deliveries[0].done()
        unblock.set()
    elif result == "shutdown":
        deliveries[0].cancel()
    await asyncio.wait_for(asyncio.gather(*deliveries, return_exceptions=True), 0.2)
    await asyncio.sleep(0)
    assert not adapter._background_tasks
    assert not task._hfc_stop_delivery_scheduled


@pytest.mark.asyncio
async def test_stop_display_capacity_never_creates_an_unowned_task(monkeypatch):
    adapter, task, key, original, release = await pending_owner()
    proof = hook_runtime.capture_stop_cancellation(adapter, event("command"), key, "stop")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    blocker = asyncio.Event()
    tasks = [asyncio.create_task(blocker.wait()) for _ in range(16)]
    for pending in tasks:
        pending._hfc_stop_display_delivery = True
    adapter._background_tasks = set(tasks)
    try:
        assert not hook_runtime.schedule_cancelled_stop_turn(adapter, proof)
        assert adapter._background_tasks == set(tasks)
        del adapter._background_tasks
        assert not hook_runtime.schedule_cancelled_stop_turn(adapter, proof)
    finally:
        for pending in tasks:
            pending.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_consecutive_started_turn_replaces_or_clears_task_owner():
    task = asyncio.current_task()
    for turn in ("first", "second"):
        incoming = event(turn)
        s = hook_runtime.queued_followup_source(incoming.source, incoming)
        hook_runtime.build_event("message.started", {"source": s, "event": incoming})
        assert task._hfc_stop_turn_owner[2] == turn
    incoming = event("")
    incoming.internal = True
    s = hook_runtime.queued_followup_source(incoming.source, incoming)
    hook_runtime.build_event("message.started", {"source": s, "event": incoming})
    assert task._hfc_stop_turn_owner is None
