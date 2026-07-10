import pytest

from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.lifecycle import CleanupPolicy, cleanup_runtime_state, session_cleanup_reason
from hermes_feishu_card import server
from hermes_feishu_card import session as session_module
from hermes_feishu_card.session import CardSession, InteractionState, ToolState


def _session(*, status: str = "thinking", updated_at: float = 100.0) -> CardSession:
    session = CardSession(conversation_id="oc_1", message_id="om_1", chat_id="oc_1")
    session.status = status
    session.updated_at = updated_at
    return session


def _reason(
    session: CardSession,
    *,
    now: float,
    has_card: bool = False,
    has_inflight_send: bool = False,
    controller_closed: bool = True,
) -> str | None:
    return session_cleanup_reason(
        session,
        now=now,
        has_card=has_card,
        has_inflight_send=has_inflight_send,
        controller_closed=controller_closed,
        policy=CleanupPolicy(),
    )


def _event(name: str, sequence: int) -> SidecarEvent:
    return SidecarEvent(
        schema_version="1",
        event=name,
        conversation_id="oc_1",
        message_id="om_1",
        chat_id="oc_1",
        platform="feishu",
        sequence=sequence,
        created_at=50.0,
        data={"text": "accepted"},
    )


def test_cleanup_policy_defaults_are_bounded():
    assert CleanupPolicy() == CleanupPolicy(
        session_retention_seconds=3600,
        zombie_grace_seconds=120,
        history_limit=50,
    )


def test_completed_session_is_collected_after_retention():
    session = _session(status="completed")

    assert _reason(session, now=3701.0, has_card=True) == "terminal_retention_expired"


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_terminal_session_uses_inclusive_retention_boundary(status):
    session = _session(status=status)

    assert _reason(session, now=3699.999, has_card=True) is None
    assert _reason(session, now=3700.0, has_card=True) == "terminal_retention_expired"


def test_terminal_session_waits_for_controller_and_inflight_work():
    session = _session(status="completed")

    assert _reason(session, now=5000.0, has_card=True, controller_closed=False) is None
    assert _reason(session, now=5000.0, has_card=True, has_inflight_send=True) is None


def test_empty_session_waits_for_zombie_grace_and_never_collects_active_interaction():
    session = _session()

    assert _reason(session, now=219.999, controller_closed=False) is None
    session.active_interaction = InteractionState(
        interaction_id="interaction-1",
        kind="approval",
        prompt="允许吗？",
    )
    assert _reason(session, now=500.0, controller_closed=False) is None


def test_empty_thinking_session_is_collected_at_zombie_boundary():
    session = _session()

    assert _reason(session, now=220.0, controller_closed=False) == "zombie_grace_expired"


@pytest.mark.parametrize("field", ["answer_text", "thinking_text"])
def test_nonempty_text_prevents_zombie_collection(field):
    session = _session()
    setattr(session, field, "progress")

    assert _reason(session, now=500.0) is None


def test_tool_or_sequence_progress_prevents_zombie_collection():
    tool_session = _session()
    tool_session.tools["tool-1"] = ToolState("tool-1", "search", "completed")
    sequence_session = _session()
    sequence_session.last_sequence = 0

    assert _reason(tool_session, now=500.0) is None
    assert _reason(sequence_session, now=500.0) is None


def test_card_binding_prevents_zombie_collection():
    assert _reason(_session(), now=500.0, has_card=True) is None


def test_completed_interaction_does_not_block_terminal_retention():
    session = _session(status="completed")
    session.active_interaction = InteractionState(
        interaction_id="interaction-1",
        kind="approval",
        prompt="允许吗？",
        status="completed",
    )

    assert _reason(session, now=3700.0, has_card=True) == "terminal_retention_expired"


def test_card_session_timestamps_only_advance_for_accepted_events(monkeypatch):
    times = iter((10.0, 11.0, 20.0))
    monkeypatch.setattr(session_module.time, "time", lambda: next(times))
    session = CardSession(conversation_id="oc_1", message_id="om_1", chat_id="oc_1")

    assert session.created_at == 10.0
    assert session.updated_at == 11.0
    assert session.apply(_event("thinking.delta", 1))
    assert session.updated_at == 20.0
    assert not session.apply(_event("thinking.delta", 1))
    assert session.updated_at == 20.0


def test_cleanup_preserves_alias_reassigned_to_new_active_session():
    app = server.create_app(object())
    old_key = "om_old"
    new_key = "om_new"
    old = _session(status="completed")
    old.updated_at = 100.0
    new = _session()
    new.updated_at = 3700.0
    new.answer_text = "active"
    app[server.SESSIONS_KEY][old_key] = old
    app[server.SESSIONS_KEY][new_key] = new
    class ReassignedAliases(dict):
        def __init__(self):
            super().__init__({"om_reply": new_key})
            self._first_items_call = True

        def items(self):
            if self._first_items_call:
                self._first_items_call = False
                return (("om_reply", old_key),)
            return super().items()

    app[server.SESSION_ALIASES_KEY] = ReassignedAliases()
    assert app[server.SESSION_ALIASES_KEY]["om_reply"] == new_key

    cleanup_runtime_state(app, now=3700.0)

    assert old_key not in app[server.SESSIONS_KEY]
    assert app[server.SESSION_ALIASES_KEY]["om_reply"] == new_key


def test_cleanup_keeps_old_canonical_key_when_it_has_been_reassigned_as_alias():
    app = server.create_app(object())
    old_key = "om_old"
    new_key = "om_new"
    old = _session(status="completed")
    old.updated_at = 100.0
    new = _session()
    new.answer_text = "active"
    app[server.SESSIONS_KEY][old_key] = old
    app[server.SESSIONS_KEY][new_key] = new
    app[server.SESSION_ALIASES_KEY][old_key] = new_key

    cleanup_runtime_state(app, now=3700.0)

    assert old_key not in app[server.SESSIONS_KEY]
    assert app[server.SESSION_ALIASES_KEY][old_key] == new_key
    assert server._active_session_key(app, old_key) == new_key
