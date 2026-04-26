from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
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
_ACTIVE_FALLBACK_MESSAGE_IDS: dict[tuple[str, str], str] = {}
_FALLBACK_LIFECYCLE_COUNTS: dict[tuple[str, str], int] = {}


def reset_runtime_state() -> None:
    _SEQUENCES.clear()
    _ACTIVE_FALLBACK_MESSAGE_IDS.clear()
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
    fallback_key = (conversation_id, chat_id)
    message_id = _first_string(local_vars, ("message_id", "msg_id")) or _first_attr_string(
        message_obj, ("message_id", "msg_id")
    )
    used_fallback = message_id is None
    if used_fallback:
        message_id = _fallback_message_id(
            event_name,
            conversation_id,
            chat_id,
            created_at_value,
            created_at,
        )
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
    if used_fallback and event_name in {"message.completed", "message.failed"}:
        _ACTIVE_FALLBACK_MESSAGE_IDS.pop(fallback_key, None)
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
    for name in names:
        value = getattr(obj, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(obj, dict):
        return _first_string(obj, names)
    return None


def _created_at(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return time.time()


def _fallback_message_id(
    event_name: str,
    conversation_id: str,
    chat_id: str,
    created_at_value: Any,
    created_at: float,
) -> str:
    if created_at_value is not None:
        return _hash_fallback_message_id(conversation_id, chat_id, f"{created_at:.3f}")

    key = (conversation_id, chat_id)
    if event_name != "message.started":
        cached = _ACTIVE_FALLBACK_MESSAGE_IDS.get(key)
        if cached is not None:
            return cached
    return _create_active_fallback_message_id(key, conversation_id, chat_id)


def _create_active_fallback_message_id(
    key: tuple[str, str], conversation_id: str, chat_id: str
) -> str:
    lifecycle_count = _FALLBACK_LIFECYCLE_COUNTS.get(key, 0)
    _FALLBACK_LIFECYCLE_COUNTS[key] = lifecycle_count + 1
    message_id = _hash_fallback_message_id(
        conversation_id, chat_id, f"active:{lifecycle_count}"
    )
    _ACTIVE_FALLBACK_MESSAGE_IDS[key] = message_id
    return message_id


def _hash_fallback_message_id(
    conversation_id: str, chat_id: str, lifecycle_token: str
) -> str:
    raw = f"{conversation_id}:{chat_id}:{lifecycle_token}".encode("utf-8")
    return "hfc_" + sha256(raw).hexdigest()[:16]


def _next_sequence(message_id: str) -> int:
    sequence = _SEQUENCES.get(message_id, -1) + 1
    _SEQUENCES[message_id] = sequence
    return sequence
