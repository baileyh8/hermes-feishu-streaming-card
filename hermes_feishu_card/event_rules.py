"""Pure event routing and lifecycle rules used by the HTTP coordinator.

No application, transport or persistent store is consulted here. Canonical
turn identity stays distinct from reply aliases and Feishu thread anchors.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import re
from typing import Any, Protocol, TYPE_CHECKING

from .events import SidecarEvent

if TYPE_CHECKING:
    from .session import CardSession


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


def legacy_alias_owner_matches(owner: CardSession, event: SidecarEvent) -> bool:
    if owner.chat_id != event.chat_id:
        return False
    # A topic root (om_*) and its conversation (omt_*) can both be present.
    # Compare each with its own source field.
    if (owner.conversation_id.startswith(("omt_", "om_"))
            and event.conversation_id.startswith(("omt_", "om_"))
            and owner.conversation_id != event.conversation_id):
        return False
    thread = explicit_thread_id(event)
    if thread and owner.legacy_route_thread_id and owner.legacy_route_thread_id != thread:
        return False
    sender = event.data.get("sender_open_id")
    tenant_user = event.data.get("admission_user_id")
    if sender and owner.sender_open_id and sender != owner.sender_open_id:
        return False
    if tenant_user and owner.sender_user_id and tenant_user != owner.sender_user_id:
        return False
    chat_type = event.data.get("chat_type")
    if owner.chat_type == "group" or (isinstance(chat_type, str) and chat_type.strip().lower() == "group"):
        return bool((sender and sender == owner.sender_open_id)
                    or (tenant_user and tenant_user == owner.sender_user_id))
    return True


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


def resolve_session_key(
    event: SidecarEvent, *, sessions: Mapping[str, SessionRouteView],
    aliases: Mapping[str, str], redirect_aliases: Mapping[str, str],
) -> str:
    direct_key = session_key(event)
    if event.event == "message.started":
        return direct_key
    if event.turn_id:
        redirect_key = redirect_aliases.get(direct_key)
        if redirect_key:
            active_key = active_session_key(sessions, aliases, redirect_key)
            if active_key is not None:
                return active_key
        return direct_key
    active_key = active_session_key(sessions, aliases, direct_key)
    if active_key is not None:
        return active_key
    if is_independent_notice_event(event):
        # An independent notice only joins a session that is ITSELF a notice session — never the
        # turn's. That distinction is the whole fix, and both halves are load-bearing:
        #
        #   * orphaned heartbeat (no turn session for the anchor yet): the notice's own card IS the
        #     card the turn will complete onto, so the alias MUST keep working — that is
        #     test_orphaned_heartbeats_update_one_running_notice_card.
        #   * live turn (a session already owns the anchor): resolving through the alias folds the
        #     ⏳ Working line into the turn's card, where nothing can ever withdraw it —
        #     /recall/schedule refuses an owned session card (409) and the card recall path wants
        #     delivery_kind=="notice". The user reported exactly that ("变成卡片的时候好像不会自动
        #     撤销了"). The notice takes its own key instead.
        for alias_key in session_alias_keys_for_event(event):
            active_key = active_session_key(sessions, aliases, alias_key)
            if active_key is None:
                continue
            existing = sessions.get(active_key)
            if existing is not None and existing.delivery_kind == "notice":
                return active_key
        return direct_key
    for alias_key in session_alias_keys_for_event(event):
        active_key = active_session_key(sessions, aliases, alias_key)
        if active_key is not None:
            return active_key
    return direct_key


def session_alias_assignments(
    event: SidecarEvent, canonical_key: str, *, sessions: Mapping[str, SessionRouteView],
) -> dict[str, str]:
    if is_independent_notice_event(event):
        # Register the anchor alias ONLY while nothing else owns it. The mirror of
        # resolve_session_key's guard, and it exists for the reverse hazard: active_session_key
        # consults the alias table, so an alias pointing the TURN's key (reply_to_message_id) at this
        # notice's session would let the turn's later events drift into the heartbeat's card.
        # When the anchor is already owned — a live turn — the notice stays separate instead.
        if any(sessions.get(key) is not None
               for key in session_alias_keys_for_event(event)):
            return {}
    aliases: dict[str, str] = {}
    keys = {
        session_key(event),
        session_key_for_message_id(event, event.message_id),
        *session_alias_keys_for_event(event),
    }
    for alias_key in keys:
        if alias_key and alias_key != canonical_key:
            aliases[alias_key] = canonical_key
    return aliases


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
