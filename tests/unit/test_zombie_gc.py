"""Unit tests for zombie session GC.

Background: when a non-message.started event reaches the sidecar first
(e.g., a `message.delta` or normal `message.completed`), the fallback
message_id path in hook_runtime allocates an `om_xxx` id and stores it
in `_ACTIVE_FALLBACK_MESSAGE_IDS`. But server.py's `if session is None:`
branch only handles `interaction.requested` and cron `message.completed` —
all other event types return 200/ignored without ever calling
`session.apply()`. Result: a `CardSession` is created in the dict with
all counters at initial values and no Feishu card binding. These zombie
sessions accumulate forever, pollute `/health` output, and cause
`last_route` to be sticky-stale.

This test covers `_is_zombie_session()` and `_gc_zombie_sessions()` in
server.py. The contract: a zombie is a session with no progress
(last_sequence < 0, no text, no tools) and no Feishu card binding.
GC removes it from sessions + last_update_at + update_tasks +
pending_update_requests dicts and increments metrics.
"""
import pytest

from hermes_feishu_card.server import (
    DIAGNOSTICS_KEY,
    _gc_zombie_sessions,
    _is_zombie_session,
)
from hermes_feishu_card.session import CardSession
from hermes_feishu_card.metrics import SidecarMetrics


def test_empty_session_with_no_card_is_zombie():
    s = CardSession(conversation_id="c1", message_id="om_xxx", chat_id="chat1")
    assert _is_zombie_session(s, has_feishu_card=False)


def test_empty_session_with_card_binding_is_not_zombie():
    """Even an empty session that already has a card binding is kept — it
    might be in the middle of `message.started` setup where feishu send is
    pending and we don't want to race the lock."""
    s = CardSession(conversation_id="c1", message_id="om_xxx", chat_id="chat1")
    assert not _is_zombie_session(s, has_feishu_card=True)


def test_session_with_answer_text_is_not_zombie():
    s = CardSession(conversation_id="c1", message_id="m1", chat_id="chat1")
    s.answer_text = "hello"
    assert not _is_zombie_session(s, has_feishu_card=False)


def test_session_with_thinking_text_is_not_zombie():
    s = CardSession(conversation_id="c1", message_id="m1", chat_id="chat1")
    s.thinking_text = "thinking..."
    assert not _is_zombie_session(s, has_feishu_card=False)


def test_session_with_sequence_progress_is_not_zombie():
    """last_sequence >= 0 means apply() was called at least once."""
    s = CardSession(conversation_id="c1", message_id="m1", chat_id="chat1")
    s.last_sequence = 5
    assert not _is_zombie_session(s, has_feishu_card=False)


def test_session_with_tool_count_is_not_zombie():
    s = CardSession(conversation_id="c1", message_id="m1", chat_id="chat1")
    s._tool_call_count = 3
    assert not _is_zombie_session(s, has_feishu_card=False)


def test_gc_removes_zombies_and_keeps_active():
    sessions = {
        "zombie1": CardSession(conversation_id="c1", message_id="om_z1", chat_id="chat1"),
        "active1": CardSession(conversation_id="c1", message_id="m1", chat_id="chat1"),
        "zombie2": CardSession(conversation_id="c1", message_id="om_z2", chat_id="chat1"),
    }
    sessions["active1"].last_sequence = 10
    sessions["active1"].answer_text = "in progress"
    feishu_ids: dict = {}
    last_update = {"zombie1": 1.0, "zombie2": 2.0, "active1": 3.0}
    update_tasks: dict = {}
    pending: dict = {}
    metrics = SidecarMetrics()
    app = {DIAGNOSTICS_KEY: {"zombie_sessions": []}}

    _gc_zombie_sessions(app, sessions, feishu_ids, last_update, update_tasks, pending, metrics)

    assert "zombie1" not in sessions
    assert "zombie2" not in sessions
    assert "active1" in sessions
    assert "zombie1" not in last_update
    assert "zombie2" not in last_update
    assert "active1" in last_update
    assert metrics.zombie_sessions_removed == 2
    assert len(app[DIAGNOSTICS_KEY]["zombie_sessions"]) == 2


def test_gc_is_idempotent():
    """Running GC twice does not double-count."""
    sessions = {
        "zombie": CardSession(conversation_id="c1", message_id="om_z", chat_id="chat1"),
    }
    last_update = {"zombie": 1.0}
    metrics = SidecarMetrics()
    app = {DIAGNOSTICS_KEY: {"zombie_sessions": []}}

    _gc_zombie_sessions(app, sessions, {}, last_update, {}, {}, metrics)
    assert metrics.zombie_sessions_removed == 1

    # Second call — sessions dict is now empty, nothing to do
    _gc_zombie_sessions(app, sessions, {}, last_update, {}, {}, metrics)
    assert metrics.zombie_sessions_removed == 1


def test_gc_caps_log_at_50_entries():
    """The diagnostics log must not grow unbounded."""
    metrics = SidecarMetrics()
    sessions = {
        f"zombie_{i}": CardSession(
            conversation_id="c1", message_id=f"om_{i}", chat_id="chat1"
        )
        for i in range(60)
    }
    app = {DIAGNOSTICS_KEY: {"zombie_sessions": []}}

    _gc_zombie_sessions(app, sessions, {}, {}, {}, {}, metrics)

    assert len(app[DIAGNOSTICS_KEY]["zombie_sessions"]) == 50
    assert metrics.zombie_sessions_removed == 60


def test_gc_handles_already_removed_session_gracefully():
    """A session dict where the key is in last_update_at but not in sessions
    (e.g. from a previous run with mismatched state) must not crash GC."""
    sessions: dict = {}
    last_update = {"ghost": 1.0}
    metrics = SidecarMetrics()
    app = {DIAGNOSTICS_KEY: {"zombie_sessions": []}}

    _gc_zombie_sessions(app, sessions, {}, last_update, {}, {}, metrics)
    assert metrics.zombie_sessions_removed == 0


def test_sidecar_metrics_has_zombie_counter():
    """The new metric field must exist on SidecarMetrics for /health to
    expose it. This is a regression guard."""
    m = SidecarMetrics()
    assert hasattr(m, "zombie_sessions_removed")
    assert m.zombie_sessions_removed == 0
