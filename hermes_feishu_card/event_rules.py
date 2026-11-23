"""Pure event routing and lifecycle rules used by the HTTP coordinator.

No application, transport or persistent store is consulted here. Canonical
turn identity stays distinct from reply aliases and Feishu thread anchors.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re
from typing import Any, Protocol

from .events import SidecarEvent
from .session import CardSession, _exact_feishu_open_id


TERMINAL_EVENTS = {"message.completed", "message.failed"}


TURN_REOPENING_EVENTS = {"thinking.delta", "tool.updated", "answer.delta"}


SESSION_CREATING_EVENTS = {
    "thinking.delta",
    "tool.updated",
    "answer.delta",
    "message.completed",
    "message.failed",
    "system.notice",
    "interaction.requested",
}


PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def session_key(event: SidecarEvent) -> str:
    """Return the session key for an event.

    When profiles are active, uses composite key profile_id:message_id.
    Otherwise uses message_id directly (backward compatible).
    """
    return session_key_for_message_id(event, event.canonical_turn_id)


def policy_profile_id(event: SidecarEvent) -> str | None:
    data = event.data if isinstance(event.data, dict) else {}
    if "profile_id" not in data:
        return ""
    raw_profile_id = data.get("profile_id")
    if not isinstance(raw_profile_id, str):
        return None
    candidate = raw_profile_id.strip()
    if not candidate:
        return "default"
    if PROFILE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return None


def session_key_for_message_id(event: SidecarEvent, message_id: str) -> str:
    has_profile_id = isinstance(event.data, dict) and "profile_id" in event.data
    profile_id = safe_profile_id(event.data.get("profile_id") if has_profile_id else None)
    if has_profile_id:
        return f"{profile_id}:{message_id}"
    return message_id


def session_alias_keys_for_event(event: SidecarEvent) -> list[str]:
    data = event.data if isinstance(event.data, dict) else {}
    aliases: list[str] = []
    for field in ("reply_to_message_id", "parent_message_id", "quote_message_id"):
        value = data.get(field)
        if isinstance(value, str) and value.startswith("om_"):
            aliases.append(session_key_for_message_id(event, value))
    return aliases


def event_for_session(event: SidecarEvent, session: CardSession) -> SidecarEvent:
    if (
        event.conversation_id == session.conversation_id
        and event.message_id == session.message_id
    ):
        return event
    return replace(
        event,
        conversation_id=session.conversation_id,
        message_id=session.message_id,
    )


def explicit_thread_id(event: SidecarEvent) -> str:
    value = event.thread_id or event.data.get("thread_id")
    return value if isinstance(value, str) and value.startswith(("omt_", "om_")) else ""


def _topic_root(conversation_id: str, thread_id: str) -> str:
    # The conversation can also be repeated in the explicit thread field.
    # A different explicit root is an additional part of the route.
    return thread_id if thread_id != conversation_id else ""


def event_conversation_matches(
    owner: CardSession, event: SidecarEvent, *, allow_omitted_thread: bool = False,
) -> bool:
    if owner.chat_id != event.chat_id or owner.conversation_id != event.conversation_id:
        return False
    thread = explicit_thread_id(event)
    interaction_placement = event.event == "interaction.requested" and not owner.legacy_route_thread_id
    if allow_omitted_thread and (not thread or interaction_placement) and event.conversation_id != event.chat_id:
        # Older direct callbacks can omit thread metadata. An approval can
        # also provide its own placement before the main turn opens a thread.
        if (not owner.sender_open_id and not owner.sender_user_id and not any(_event_requester(event))
                and "group" not in (owner.chat_type, _event_chat_type(event))):
            return True
    return _topic_root(event.conversation_id, thread) == _topic_root(
        owner.conversation_id, owner.legacy_route_thread_id,
    )


def _event_requester(event: SidecarEvent) -> tuple[str, str]:
    tenant_user = event.data.get("admission_user_id")
    tenant_user = tenant_user.strip() if type(tenant_user) is str else ""
    return (
        _exact_feishu_open_id(event.data.get("sender_open_id")),
        tenant_user if len(tenant_user) <= 256 else "",
    )


def _event_chat_type(event: SidecarEvent) -> str:
    chat_type = event.data.get("chat_type")
    return chat_type.strip().lower() if isinstance(chat_type, str) else ""


def _requester_matches(owner: CardSession, event: SidecarEvent, *, referenced: bool) -> bool:
    sender, tenant_user = _event_requester(event)
    if sender and owner.sender_open_id and sender != owner.sender_open_id:
        return False
    if tenant_user and owner.sender_user_id and tenant_user != owner.sender_user_id:
        return False
    if not referenced:
        return True
    if event.data.get("sender_open_id") not in (None, "") and not sender:
        return False
    if event.data.get("admission_user_id") not in (None, "") and not tenant_user:
        return False
    chat_type = _event_chat_type(event)
    if "group" not in (owner.chat_type, chat_type):
        direct_types = {"dm", "p2p", "private"}
        if owner.chat_type in direct_types or chat_type in direct_types:
            return True
        if not (sender or tenant_user or owner.sender_open_id or owner.sender_user_id):
            return True
    return bool(
        (sender and sender == owner.sender_open_id)
        or (tenant_user and tenant_user == owner.sender_user_id)
    )


def legacy_alias_owner_matches(owner: CardSession, event: SidecarEvent) -> bool:
    return event_conversation_matches(owner, event) and _requester_matches(
        owner, event, referenced=True,
    )


def thread_id_for_event(event: SidecarEvent) -> str | None:
    data = event.data if isinstance(event.data, dict) else {}
    raw_thread = (
        event.thread_id
        or data.get("thread_id")
        or (event.conversation_id if event.conversation_id != event.chat_id else "")
    )
    if isinstance(raw_thread, str) and raw_thread.startswith(("omt_", "om_")):
        return raw_thread
    return None


def reply_to_message_id_for_event(event: SidecarEvent) -> str | None:
    data = event.data if isinstance(event.data, dict) else {}
    reply_to = data.get("reply_to_message_id")
    if (
        reply_in_thread_for_event(event)
        and isinstance(reply_to, str)
        and reply_to.startswith("om_")
    ):
        return reply_to
    if thread_id_for_event(event):
        if isinstance(reply_to, str) and reply_to.startswith("om_"):
            return reply_to
        if event.message_id.startswith("om_"):
            return event.message_id
        return None
    if event.message_id.startswith("om_"):
        return event.message_id
    if isinstance(reply_to, str) and reply_to.startswith("om_"):
        return reply_to
    return None


def reply_in_thread_for_event(event: SidecarEvent) -> bool:
    data = event.data if isinstance(event.data, dict) else {}
    value = data.get("reply_in_thread")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


def delivery_kind(event: SidecarEvent) -> str:
    data = event.data if isinstance(event.data, dict) else {}
    return str(data.get("delivery_kind") or "").strip().lower()


def is_independent_notice_event(event: SidecarEvent) -> bool:
    if event.event != "system.notice":
        return False
    data = event.data if isinstance(event.data, dict) else {}
    scope = str(data.get("notice_scope") or "session").strip().lower()
    delivery_kind = str(data.get("delivery_kind") or "").strip().lower()
    return scope == "independent" or delivery_kind == "notice"


def is_redirect_followup_event(event: SidecarEvent) -> bool:
    data = event.data if isinstance(event.data, dict) else {}
    return event.event == "message.started" and data.get("redirect_followup") is True


def is_compaction_session_start(event: SidecarEvent) -> bool:
    if event.event != "system.notice":
        return False
    data = event.data if isinstance(event.data, dict) else {}
    return (
        str(data.get("notice_kind") or "") == "context-compaction"
        and str(data.get("phase") or "") == "started"
        and data.get("create_session") is True
        and str(data.get("notice_scope") or "session").strip().lower() == "session"
    )


def is_intentional_silence_event(event: SidecarEvent) -> bool:
    if event.event != "message.completed":
        return False
    data = event.data if isinstance(event.data, dict) else {}
    return data.get("intentional_silence") is True


def is_native_fallback_event(event: SidecarEvent) -> bool:
    if event.event != "message.completed":
        return False
    data = event.data if isinstance(event.data, dict) else {}
    return data.get("native_fallback") is True


def event_is_terminal(event: SidecarEvent) -> bool:
    if event.event in TERMINAL_EVENTS:
        return True
    if not is_independent_notice_event(event):
        return False
    terminal = event.data.get("notice_terminal")
    return not (isinstance(terminal, bool) and terminal is False)


def safe_profile_id(value: Any) -> str:
    candidate = str(value or "").strip()
    if PROFILE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return "default"


def would_apply(session: CardSession, event: SidecarEvent) -> bool:
    return (
        event.conversation_id == session.conversation_id
        and event.message_id == session.message_id
        and event.chat_id == session.chat_id
        and event.sequence > session.last_sequence
        and session.status not in {"completed", "failed"}
    )

class SessionRouteView(Protocol):
    @property
    def status(self) -> str: ...

    @property
    def delivery_kind(self) -> str: ...


def active_session_key(
    sessions: Mapping[str, SessionRouteView], aliases: Mapping[str, str], key: str,
) -> str | None:
    candidates = [key]
    alias = aliases.get(key)
    if alias:
        candidates.append(alias)
    for candidate in candidates:
        session = sessions.get(candidate)
        if session is None:
            continue
        if session.status in {"completed", "failed"}:
            continue
        return candidate
    return None


def _legacy_route_prefix(event: SidecarEvent, key: str, kind: str) -> str:
    route = (
        key, event.chat_id, event.conversation_id,
        _topic_root(event.conversation_id, explicit_thread_id(event)),
    )
    digest = sha256(json.dumps(route).encode("ascii")).hexdigest()
    return session_key_for_message_id(event, f"~legacy-{kind}-{digest}-")


def _scoped_key(prefix: str, requester: tuple[str, str]) -> str:
    return prefix + sha256(json.dumps(requester).encode("ascii")).hexdigest()


def legacy_reference_owner_key(
    event: SidecarEvent, key: str, *, sessions: Mapping[str, CardSession],
    aliases: Mapping[str, str], accepts: Callable[[CardSession], bool],
) -> str | None:
    def matches(candidate: str) -> bool:
        owner = sessions.get(candidate)
        return owner is not None and accepts(owner) and legacy_alias_owner_matches(owner, event)

    prefix = _legacy_route_prefix(event, key, "reference")
    requester = _event_requester(event)
    exact = aliases.get(_scoped_key(prefix, requester))
    if exact and matches(exact):
        return exact

    # A callback may carry only one of the two requester IDs. An older
    # checkpoint may only have the bare reference. Accept a unique owner.
    candidates = {key, aliases.get(key, "")}
    if any(requester):
        candidates.update(owner for alias, owner in aliases.items() if alias.startswith(prefix))
    candidates = {candidate for candidate in candidates if matches(candidate)}
    return next(iter(candidates)) if len(candidates) == 1 else None


def _legacy_direct_key(
    event: SidecarEvent, key: str, *, sessions: Mapping[str, CardSession],
    aliases: Mapping[str, str],
) -> str:
    references_message_id = key in session_alias_keys_for_event(event)
    owner = sessions.get(key)
    requester = _event_requester(event)
    prefix = _legacy_route_prefix(event, key, "message")
    scoped = _scoped_key(prefix, requester)
    if scoped in sessions:
        return scoped
    raw_owner_matches = (
        owner is not None
        and event_conversation_matches(owner, event, allow_omitted_thread=not references_message_id)
        and _requester_matches(owner, event, referenced=references_message_id)
    )
    # A stable direct message ID still works when a legacy callback omits its
    # sender. A known reuse of that ID requires requester evidence instead.
    shared_message_id = aliases.get(key, "").startswith(prefix)
    if (raw_owner_matches and not shared_message_id and not any(requester)
            and owner is not None and (owner.chat_type == "group" or owner.sender_open_id)):
        shared_message_id = any(candidate.startswith(prefix) for candidate in sessions)
    if raw_owner_matches and not shared_message_id:
        return key
    direct = legacy_reference_owner_key(
        event, key, sessions=sessions, aliases=aliases,
        accepts=lambda current: current.message_id == event.message_id,
    )
    if direct is not None:
        return direct
    if (owner is not None or shared_message_id
            or (references_message_id and (_event_chat_type(event) == "group" or any(requester)))):
        # Known reply anchors can be reused simultaneously. Keep their new
        # session key stable even when a card create fails and is retried.
        return scoped
    return key


def resolve_session_key(
    event: SidecarEvent, *, sessions: Mapping[str, CardSession],
    aliases: Mapping[str, str], redirect_aliases: Mapping[str, str],
) -> str:
    direct_key = session_key(event)
    if event.turn_id:
        if event.event == "message.started":
            return direct_key
        redirect_key = redirect_aliases.get(direct_key)
        if redirect_key:
            active_key = active_session_key(sessions, redirect_aliases, redirect_key)
            if active_key is not None:
                return active_key
        return direct_key

    key = _legacy_direct_key(event, direct_key, sessions=sessions, aliases=aliases)
    if event.event == "message.started":
        return key
    redirect = redirect_aliases.get(key)
    if redirect:
        active = active_session_key(sessions, redirect_aliases, redirect)
        if active and legacy_alias_owner_matches(sessions[active], event):
            return active
    if key in sessions:
        return key

    notice = is_independent_notice_event(event)

    def accepts(owner: CardSession) -> bool:
        # A heartbeat can refresh its own independent card while the chat
        # turn continues on a separate card.
        return owner.status not in {"completed", "failed"} and (
            not notice or owner.delivery_kind == "notice"
        )

    for reference in dict.fromkeys([direct_key, *session_alias_keys_for_event(event)]):
        active = legacy_reference_owner_key(event, reference, sessions=sessions,
                                            aliases=aliases, accepts=accepts)
        if active is not None:
            return active
    return key


def session_alias_assignments(
    event: SidecarEvent, canonical_key: str, *, sessions: Mapping[str, CardSession],
    aliases: Mapping[str, str],
) -> dict[str, str]:
    reference_keys = session_alias_keys_for_event(event)
    if is_independent_notice_event(event):
        # Leave an existing turn's anchor with that turn, including when the
        # canonical message uses a scoped key.
        for reference in reference_keys:
            if reference in sessions or legacy_reference_owner_key(
                event, reference, sessions=sessions, aliases=aliases,
                accepts=lambda owner: owner.delivery_kind != "notice",
            ) is not None:
                return {}
    assignments: dict[str, str] = {}
    keys = {
        session_key(event),
        session_key_for_message_id(event, event.message_id),
        *reference_keys,
    }
    owner = sessions.get(canonical_key)
    for alias_key in keys:
        if alias_key and alias_key != canonical_key:
            assignments[alias_key] = canonical_key
            if owner is not None:
                prefix = _legacy_route_prefix(event, alias_key, "reference")
                key = _scoped_key(prefix, (owner.sender_open_id, owner.sender_user_id))
                assignments[key] = canonical_key
    return assignments


@dataclass(frozen=True)
class InteractionReplyPlacement:
    thread_id: str | None
    reply_to_message_id: str | None
    reply_in_thread: bool


def interaction_reply_placement(
    event: SidecarEvent,
    *,
    session_reply_to_message_id: str,
    session_reply_in_thread: bool,
) -> InteractionReplyPlacement:
    """Keep one placement for the state, reserved send and direct send.

    A verified sticky IM anchor takes precedence over a subsequent event's
    anchor. This is display routing only, never turn or authorization identity.
    """
    sticky_anchor = (
        session_reply_to_message_id
        if session_reply_in_thread and session_reply_to_message_id.startswith("om_")
        else ""
    )
    return InteractionReplyPlacement(
        thread_id=thread_id_for_event(event),
        reply_to_message_id=(
            sticky_anchor or reply_to_message_id_for_event(event)
            or session_reply_to_message_id or None
        ),
        reply_in_thread=reply_in_thread_for_event(event) or session_reply_in_thread,
    )
