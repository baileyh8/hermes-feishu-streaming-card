"""Session routing operates on read-only mappings without a server application."""
from types import MappingProxyType

import pytest

from hermes_feishu_card.event_rules import active_session_key, resolve_session_key, session_alias_assignments
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.session import CardSession


def route_view(status="thinking", delivery_kind="chat"):
    return CardSession("fixture-chat", "om_previous", "fixture-chat",
                       status=status, delivery_kind=delivery_kind)


def event(kind="answer.delta", message_id="om_new", turn_id=None, data=None):
    payload = dict(schema_version="1", event=kind, sequence=1, conversation_id="fixture-chat", message_id=message_id, chat_id="fixture-chat", platform="feishu", created_at=1777017600.0, data=data or {})
    if turn_id is not None:
        payload["turn_id"] = turn_id
    return SidecarEvent.from_dict(payload)


@pytest.mark.parametrize("status", ["thinking", "tool_running", "completed", "failed"])
def test_active_lookup_prefers_direct_live_session_and_never_uses_terminal_session(status):
    sessions = MappingProxyType({"direct": route_view(status), "alias-target": route_view()})
    aliases = MappingProxyType({"direct": "alias-target"})
    assert active_session_key(sessions, aliases, "direct") == ("alias-target" if status in {"completed", "failed"} else "direct")
    assert active_session_key(MappingProxyType({"direct": route_view("completed")}), {}, "direct") is None


@pytest.mark.parametrize("kind", ["message.started", "answer.delta", "message.completed"])
def test_explicit_turn_identity_fences_reply_and_message_aliases(kind):
    sessions = MappingProxyType({"other-turn": route_view()})
    aliases = MappingProxyType({"om_new": "other-turn", "explicit-turn": "other-turn", "om_reply": "other-turn"})
    value = event(kind, turn_id="explicit-turn", data={"reply_to_message_id": "om_reply"})
    assert resolve_session_key(value, sessions=sessions, aliases=aliases, redirect_aliases={}) == "explicit-turn"


@pytest.mark.parametrize("kind,expected", [("message.started", "om_new"), ("thinking.delta", "om_previous"), ("tool.updated", "om_previous"), ("answer.delta", "om_previous")])
def test_new_user_message_starts_fresh_but_legacy_stream_uses_reply_alias(kind, expected):
    value = event(kind, data={"reply_to_message_id": "om_reply"})
    assert resolve_session_key(value, sessions={"om_previous": route_view()}, aliases={"om_reply": "om_previous"}, redirect_aliases={}) == expected


@pytest.mark.parametrize("delivery_kind,status,expected", [("chat", "thinking", "om_new"), ("notice", "thinking", "om_previous"), ("notice", "completed", "om_new"), ("notice", "failed", "om_new")])
def test_independent_notice_reply_alias_only_joins_a_live_notice(delivery_kind, status, expected):
    value = event("system.notice", data={"reply_to_message_id": "om_reply", "notice_scope": "independent"})
    assert resolve_session_key(value, sessions={"om_previous": route_view(status, delivery_kind)}, aliases={"om_reply": "om_previous"}, redirect_aliases={}) == expected


@pytest.mark.parametrize("status", ["thinking", "completed", "failed"])
def test_independent_notice_cannot_claim_an_owned_anchor_even_after_terminal(status):
    value = event("system.notice", data={"reply_to_message_id": "om_reply", "delivery_kind": "notice"})
    sessions = MappingProxyType({"om_reply": route_view(status)})
    assert session_alias_assignments(value, "notice-key", sessions=sessions, aliases={}) == {}
    assert session_alias_assignments(value, "notice-key", sessions={}, aliases={}) == {"om_new": "notice-key", "om_reply": "notice-key"}


@pytest.mark.parametrize("kind,status,expected", [("answer.delta", "thinking", "a:redirected"), ("message.completed", "tool_running", "a:redirected"), ("message.started", "thinking", "a:original"), ("answer.delta", "completed", "a:original")])
def test_only_explicit_redirect_can_cross_turn_identity_and_remains_profile_scoped(kind, status, expected):
    sessions = MappingProxyType({"a:redirected": route_view(status), "b:redirected": route_view()})
    redirects = MappingProxyType({"a:original": "a:redirected"})
    value = event(kind, turn_id="original", data={"profile_id": "a"})
    assert resolve_session_key(value, sessions=sessions, aliases={}, redirect_aliases=redirects) == expected
    other = event(kind, turn_id="original", data={"profile_id": "b"})
    assert resolve_session_key(other, sessions=sessions, aliases={}, redirect_aliases=redirects) == "b:original"


def test_alias_plan_is_a_new_mapping_and_does_not_mutate_session_views():
    value = event(turn_id="explicit", data={"profile_id": "work", "reply_to_message_id": "om_reply", "quote_message_id": "not-a-feishu-id"})
    sessions = MappingProxyType({"work:explicit": route_view()})
    assignments = session_alias_assignments(value, "work:explicit", sessions=sessions, aliases={})
    assert assignments["work:om_new"] == assignments["work:om_reply"] == "work:explicit"
    assert set(assignments.values()) == {"work:explicit"}
    assert "work:not-a-feishu-id" not in assignments
    assert len(sessions) == 1


@pytest.mark.parametrize("message_id,data,anchor,threaded,expected", [
    ("om_new", {"reply_to_message_id": "om_event", "reply_in_thread": False}, "om_sticky", True, (None, "om_sticky", True)),
    ("om_new", {"thread_id": "omt_topic", "reply_to_message_id": "om_event"}, "om_old", False, ("omt_topic", "om_event", False)),
    ("om_new", {"reply_to_message_id": "om_event"}, "om_old", False, (None, "om_new", False)),
    ("internal", {"reply_to_message_id": "om_event", "reply_in_thread": "true"}, "om_old", False, (None, "om_event", True)),
    ("internal", {}, "om_fallback", False, (None, "om_fallback", False)),
    ("internal", {"reply_to_message_id": "invalid", "thread_id": "invalid"}, "", False, (None, None, False)),
    ("internal", {}, "", True, (None, None, True)),
    ("internal", {"reply_to_message_id": "om_event"}, "legacy-anchor", True, (None, "om_event", True)),
    # Keep the old final fallback, even when it is not an IM-shaped anchor.
    ("internal", {}, "legacy-anchor", False, (None, "legacy-anchor", False)),
])
def test_interaction_reply_placement_precedence_and_input_immutability(
    message_id, data, anchor, threaded, expected,
):
    import copy
    from dataclasses import FrozenInstanceError
    from hermes_feishu_card.event_rules import interaction_reply_placement

    value = event("interaction.requested", message_id=message_id, data=data)
    before = copy.deepcopy(value)
    placement = interaction_reply_placement(
        value, session_reply_to_message_id=anchor, session_reply_in_thread=threaded,
    )
    assert (placement.thread_id, placement.reply_to_message_id, placement.reply_in_thread) == expected
    assert value == before
    with pytest.raises(FrozenInstanceError):
        placement.reply_in_thread = not threaded
