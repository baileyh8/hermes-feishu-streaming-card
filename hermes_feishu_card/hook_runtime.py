from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
import os
import time
from typing import Any

DEFAULT_EVENT_URL = "http://127.0.0.1:8765/events"
DEFAULT_TIMEOUT_SECONDS = 0.8

SUPPORTED_RUNTIME_EVENTS = {
    "message.started",
    "thinking.delta",
    "answer.delta",
    "tool.updated",
    "message.completed",
    "message.failed",
}


@dataclass(frozen=True)
class RuntimeConfig:
    enabled: bool
    event_url: str
    timeout_seconds: float


_SEQUENCES: dict[str, int] = {}
_ACTIVE_FALLBACK_MESSAGE_IDS: dict[tuple[str, str, str | None], str] = {}
_CURRENT_FALLBACK_KEYS: dict[tuple[str, str], tuple[str, str, str | None]] = {}
_FALLBACK_LIFECYCLE_COUNTS: dict[tuple[str, str], int] = {}
_AMBIGUOUS_TERMINAL = object()


def reset_runtime_state() -> None:
    _SEQUENCES.clear()
    _ACTIVE_FALLBACK_MESSAGE_IDS.clear()
    _CURRENT_FALLBACK_KEYS.clear()
    _FALLBACK_LIFECYCLE_COUNTS.clear()


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
    created_at_value = local_vars.get("created_at")
    created_at = _created_at(created_at_value)
    created_at_lifecycle_token = _created_at_lifecycle_token(created_at_value)
    fallback_key = (conversation_id, chat_id)
    explicit_message_id = _first_string(
        local_vars, ("message_id", "msg_id")
    ) or _first_attr_string(
        message_obj, ("message_id", "msg_id")
    )
    message_id = explicit_message_id
    is_terminal_event = event_name in {"message.completed", "message.failed"}
    active_fallback_cache_key = (
        _terminal_fallback_cache_key(
            fallback_key, created_at_lifecycle_token, explicit_message_id
        )
        if is_terminal_event
        else None
    )
    if active_fallback_cache_key is _AMBIGUOUS_TERMINAL:
        return None
    active_fallback_message_id = (
        _ACTIVE_FALLBACK_MESSAGE_IDS.get(active_fallback_cache_key)
        if active_fallback_cache_key is not None
        else None
    )
    if active_fallback_message_id is not None:
        message_id = active_fallback_message_id
    elif is_terminal_event and message_id is None:
        return None
    elif message_id is None:
        message_id = _fallback_message_id(
            event_name,
            conversation_id,
            chat_id,
            created_at_lifecycle_token,
        )
        if message_id is None:
            return None
    sequence = _next_sequence(message_id)
    payload = {
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
    if is_terminal_event:
        if active_fallback_cache_key is not None:
            _ACTIVE_FALLBACK_MESSAGE_IDS.pop(active_fallback_cache_key, None)
            if _CURRENT_FALLBACK_KEYS.get(fallback_key) == active_fallback_cache_key:
                _CURRENT_FALLBACK_KEYS.pop(fallback_key, None)
    return payload


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
    if isinstance(obj, dict):
        return _first_string(obj, names)
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _created_at(value: Any) -> float:
    created_at = _finite_float(value)
    if created_at is None:
        return time.time()
    return created_at


def _created_at_lifecycle_token(value: Any) -> str | None:
    created_at = _finite_float(value)
    if created_at is None:
        return None
    return f"{created_at:.3f}"


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _fallback_message_id(
    event_name: str,
    conversation_id: str,
    chat_id: str,
    created_at_lifecycle_token: str | None,
) -> str | None:
    key = (conversation_id, chat_id)
    if event_name == "message.started":
        cache_key = _new_fallback_cache_key(key, created_at_lifecycle_token)
        cached = _ACTIVE_FALLBACK_MESSAGE_IDS.get(cache_key)
        if cached is not None:
            _CURRENT_FALLBACK_KEYS[key] = cache_key
            return cached
        return _create_active_fallback_message_id(
            key, cache_key, conversation_id, chat_id, created_at_lifecycle_token
        )

    active_cache_key = _active_fallback_cache_key(key, created_at_lifecycle_token)
    if active_cache_key is _AMBIGUOUS_TERMINAL:
        return None
    if active_cache_key is not None:
        cached = _ACTIVE_FALLBACK_MESSAGE_IDS.get(active_cache_key)
        if cached is not None:
            return cached

    cache_key = _new_fallback_cache_key(key, created_at_lifecycle_token)
    return _create_active_fallback_message_id(
        key, cache_key, conversation_id, chat_id, created_at_lifecycle_token
    )


def _create_active_fallback_message_id(
    key: tuple[str, str],
    cache_key: tuple[str, str, str | None],
    conversation_id: str,
    chat_id: str,
    created_at_lifecycle_token: str | None,
) -> str:
    lifecycle_count = _FALLBACK_LIFECYCLE_COUNTS.get(key, 0)
    _FALLBACK_LIFECYCLE_COUNTS[key] = lifecycle_count + 1
    lifecycle_token = f"active:{lifecycle_count}"
    if created_at_lifecycle_token is not None:
        lifecycle_token = f"{lifecycle_token}:created_at:{created_at_lifecycle_token}"
    message_id = _hash_fallback_message_id(
        conversation_id, chat_id, lifecycle_token
    )
    _ACTIVE_FALLBACK_MESSAGE_IDS[cache_key] = message_id
    _CURRENT_FALLBACK_KEYS[key] = cache_key
    return message_id


def _new_fallback_cache_key(
    key: tuple[str, str], created_at_lifecycle_token: str | None
) -> tuple[str, str, str | None]:
    if created_at_lifecycle_token is not None:
        return (key[0], key[1], created_at_lifecycle_token)
    lifecycle_count = _FALLBACK_LIFECYCLE_COUNTS.get(key, 0)
    return (key[0], key[1], f"untokened:{lifecycle_count}")


def _terminal_fallback_cache_key(
    key: tuple[str, str],
    created_at_lifecycle_token: str | None,
    explicit_message_id: str | None,
) -> tuple[str, str, str | None] | object | None:
    if explicit_message_id is not None:
        return None
    if created_at_lifecycle_token is not None:
        token_key = (key[0], key[1], created_at_lifecycle_token)
        if token_key in _ACTIVE_FALLBACK_MESSAGE_IDS:
            return token_key
        active_keys = _active_fallback_cache_keys(key)
        if len(active_keys) == 1:
            return active_keys[0]
        if len(active_keys) > 1:
            return _AMBIGUOUS_TERMINAL
        return None

    active_keys = _active_fallback_cache_keys(key)
    if len(active_keys) == 1:
        return active_keys[0]
    if len(active_keys) > 1 and explicit_message_id is None:
        return _AMBIGUOUS_TERMINAL
    return None


def _active_fallback_cache_key(
    key: tuple[str, str], created_at_lifecycle_token: str | None
) -> tuple[str, str, str | None] | object | None:
    if created_at_lifecycle_token is not None:
        token_key = (key[0], key[1], created_at_lifecycle_token)
        if token_key in _ACTIVE_FALLBACK_MESSAGE_IDS:
            return token_key
    active_keys = _active_fallback_cache_keys(key)
    if len(active_keys) > 1:
        return _AMBIGUOUS_TERMINAL
    current_key = _CURRENT_FALLBACK_KEYS.get(key)
    if current_key in _ACTIVE_FALLBACK_MESSAGE_IDS:
        return current_key
    return None


def _active_fallback_cache_keys(
    key: tuple[str, str]
) -> list[tuple[str, str, str | None]]:
    return [
        active_key
        for active_key in _ACTIVE_FALLBACK_MESSAGE_IDS
        if active_key[0] == key[0] and active_key[1] == key[1]
    ]


def _hash_fallback_message_id(
    conversation_id: str, chat_id: str, lifecycle_token: str
) -> str:
    raw = f"{conversation_id}:{chat_id}:{lifecycle_token}".encode("utf-8")
    return "hfc_" + sha256(raw).hexdigest()[:16]


def _next_sequence(message_id: str) -> int:
    sequence = _SEQUENCES.get(message_id, -1) + 1
    _SEQUENCES[message_id] = sequence
    return sequence
