from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from .session import CardSession


@dataclass(frozen=True)
class CleanupPolicy:
    session_retention_seconds: float = 3600
    zombie_grace_seconds: float = 120
    history_limit: int = 50


@dataclass(frozen=True)
class CleanupResult:
    session_keys: tuple[str, ...]
    reasons: tuple[str, ...]
    controllers_collected: int


def session_cleanup_reason(
    session: CardSession,
    *,
    now: float,
    has_card: bool,
    has_inflight_send: bool,
    controller_closed: bool,
    policy: CleanupPolicy,
) -> str | None:
    interaction = session.active_interaction
    interaction_active = interaction is not None and interaction.status not in {
        "completed",
        "failed",
    }
    if interaction_active or has_inflight_send:
        return None

    age = max(0.0, now - session.updated_at)
    if session.status in {"completed", "failed"}:
        if not controller_closed:
            return None
        if age >= policy.session_retention_seconds:
            return "terminal_retention_expired"
        return None

    if session.status != "thinking" or age < policy.zombie_grace_seconds:
        return None
    if has_card or session.last_sequence >= 0:
        return None
    if session.answer_text or session.thinking_text or session.tools:
        return None
    return "zombie_grace_expired"


def cleanup_runtime_state(app: Any, now: float) -> CleanupResult:
    from . import server

    policy = CleanupPolicy()
    sessions = app[server.SESSIONS_KEY]
    aliases = app[server.SESSION_ALIASES_KEY]
    message_locks = app[server.MESSAGE_LOCKS_KEY]
    lock_users = app[server.MESSAGE_LOCK_USERS_KEY]
    controllers = app[server.FLUSH_CONTROLLERS_KEY]
    collected_keys: list[str] = []
    reasons: list[str] = []
    controllers_collected = 0

    for session_key, session in tuple(sessions.items()):
        alias_keys = tuple(
            alias_key
            for alias_key, canonical_key in aliases.items()
            if canonical_key == session_key
        )
        related_keys = (session_key, *alias_keys)
        controller = controllers.get(session_key)
        controller_closed, controller_active = _controller_state(controller)
        has_inflight_send = controller_active or any(
            _lock_is_locked(message_locks.get(key)) or lock_users.get(key, 0) > 0
            for key in related_keys
        )
        reason = session_cleanup_reason(
            session,
            now=now,
            has_card=session_key in app[server.FEISHU_MESSAGE_IDS_KEY],
            has_inflight_send=has_inflight_send,
            controller_closed=controller is None or controller_closed,
            policy=policy,
        )
        if reason is None or sessions.get(session_key) is not session:
            continue

        sessions.pop(session_key, None)
        for alias_key in alias_keys:
            if aliases.get(alias_key) == session_key:
                aliases.pop(alias_key, None)
        if aliases.get(session_key) == session_key:
            aliases.pop(session_key, None)
        for key in related_keys:
            message_locks.pop(key, None)
            lock_users.pop(key, None)
        summary_owners = app[server.CARD_SUMMARY_SESSION_KEYS_KEY]
        for feishu_message_id, owner_key in tuple(summary_owners.items()):
            if owner_key == session_key:
                app[server.CARD_SUMMARIES_KEY].pop(feishu_message_id, None)
                summary_owners.pop(feishu_message_id, None)
        interaction_owners = app[server.INTERACTION_RESULT_SESSION_KEYS_KEY]
        for interaction_id, owner_key in tuple(interaction_owners.items()):
            if owner_key == session_key:
                app[server.INTERACTION_RESULTS_KEY].pop(interaction_id, None)
                interaction_owners.pop(interaction_id, None)
        app[server.FEISHU_MESSAGE_IDS_KEY].pop(session_key, None)
        app[server.MESSAGE_BOT_IDS_KEY].pop(session_key, None)
        app[server.SESSION_CARD_CONFIGS_KEY].pop(session_key, None)
        if controllers.get(session_key) is controller and controller is not None:
            # Pending send/update and last-flush state are owned by the controller.
            controller.close()
            controllers.pop(session_key, None)
            controllers_collected += 1
            app[server.METRICS_KEY].flush_controllers_collected += 1

        collected_keys.append(session_key)
        reasons.append(reason)
        metrics = app[server.METRICS_KEY]
        metrics.sessions_collected += 1
        if reason == "zombie_grace_expired":
            metrics.zombie_sessions_collected += 1
        _record_cleanup(app, session_key, reason, now, policy)

    for session_key, controller in tuple(controllers.items()):
        if cleanup_closed_controller(app, session_key, controller, now=now):
            controllers_collected += 1

    for lock_key, lock in tuple(message_locks.items()):
        cleanup_orphan_message_lock(app, lock_key, lock)

    return CleanupResult(
        session_keys=tuple(collected_keys),
        reasons=tuple(reasons),
        controllers_collected=controllers_collected,
    )


def cleanup_orphan_message_lock(app: Any, lock_key: str, lock: Any) -> bool:
    from . import server

    message_locks = app[server.MESSAGE_LOCKS_KEY]
    if message_locks.get(lock_key) is not lock:
        return False
    if _lock_is_locked(lock) or app[server.MESSAGE_LOCK_USERS_KEY].get(lock_key, 0) > 0:
        return False

    canonical_key = app[server.SESSION_ALIASES_KEY].get(lock_key, lock_key)
    sessions = app[server.SESSIONS_KEY]
    if lock_key in sessions or canonical_key in sessions:
        return False
    for controller_key in {lock_key, canonical_key}:
        controller = app[server.FLUSH_CONTROLLERS_KEY].get(controller_key)
        if controller is not None and _controller_state(controller)[1]:
            return False

    if message_locks.get(lock_key) is not lock:
        return False
    message_locks.pop(lock_key, None)
    return True


def cleanup_closed_controller(
    app: Any,
    session_key: str,
    controller: Any,
    *,
    now: float,
) -> bool:
    from . import server

    controllers = app[server.FLUSH_CONTROLLERS_KEY]
    if controllers.get(session_key) is not controller:
        return False
    closed, active = _controller_state(controller)
    if not closed or active:
        return False
    controllers.pop(session_key, None)
    app[server.METRICS_KEY].flush_controllers_collected += 1
    _record_cleanup(
        app,
        session_key,
        "closed_flush_controller",
        now,
        CleanupPolicy(),
    )
    return True


def _controller_state(controller: Any) -> tuple[bool, bool]:
    if controller is None:
        return False, False
    snapshot = controller.snapshot()
    return bool(snapshot.get("closed")), bool(snapshot.get("task_active"))


def _lock_is_locked(lock: Any) -> bool:
    return lock is not None and bool(lock.locked())


def _record_cleanup(
    app: Any,
    session_key: str,
    reason: str,
    now: float,
    policy: CleanupPolicy,
) -> None:
    from . import server

    history = app[server.DIAGNOSTICS_KEY].setdefault("cleanup_history", [])
    history.append(
        {
            "session_key_hash": hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:12],
            "reason": reason,
            "recorded_at": now,
        }
    )
    if len(history) > policy.history_limit:
        del history[: len(history) - policy.history_limit]
