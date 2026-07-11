from __future__ import annotations

import asyncio
import base64
import copy
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from ipaddress import ip_address
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import secrets
import sys
from types import SimpleNamespace
import threading
import time
from typing import Any, Callable
from urllib import parse
from urllib import request

from .operations import sign_transport_proof
from .operations_transport import (
    derive_operation_transport_secret,
    read_transport_root_secret,
    sign_command_transport_proof,
)
from .status import normalize_display_status

logger = logging.getLogger(__name__)

DEFAULT_EVENT_URL = "http://127.0.0.1:8765/events"
DEFAULT_TIMEOUT_SECONDS = 0.8
TERMINAL_TIMEOUT_SECONDS = 10.0
OPERATIONS_ACTION_TIMEOUT_SECONDS = 10.0
OPERATIONS_ACTION_FORWARD_ATTEMPTS = 2
OPERATIONS_ACTION_RETRY_DELAY_SECONDS = 0.1
OPERATIONS_ACTION_WORKERS = 4
OPERATIONS_ACTION_QUEUE_LIMIT = 64
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MEDIA_RE = re.compile(r"MEDIA:([^\s\]]+)")
LOCAL_FILE_RE = re.compile(
    r"(?<![:\w/])(/[^\s`]+\.(?:png|jpg|jpeg|webp|gif|pdf|txt|md|csv|xlsx|docx|mp3|wav|ogg|mp4|mov|webm))"
)
ATTACHMENT_TRAILING_PUNCTUATION = ",.;:)]}，。；：）】}"
NATIVE_DELIVERY_ATTACHMENT_FIELDS = (
    "files",
    "file",
    "media_files",
    "media",
    "images",
    "image_files",
    "audio_files",
    "video_files",
)
NATIVE_DELIVERY_OUTPUT_ATTACHMENT_FIELDS = (
    "media_files",
    "media",
    "image_files",
    "audio_files",
    "video_files",
)

SUPPORTED_RUNTIME_EVENTS = {
    "message.started",
    "thinking.delta",
    "answer.delta",
    "tool.updated",
    "message.completed",
    "message.failed",
    "system.notice",
    "interaction.requested",
    "interaction.completed",
    "interaction.failed",
}


@dataclass(frozen=True)
class RuntimeConfig:
    enabled: bool
    event_url: str
    timeout_seconds: float
    delta_coalesce_ms: int
    delta_coalesce_chars: int
    delta_coalesce_max_pending: int


@dataclass
class _PendingDelta:
    event_name: str
    event_url: str
    timeout_seconds: float
    loop: Any
    base_locals: dict[str, Any]
    text_parts: list[str]
    char_count: int = 0
    scheduled: bool = False


_SEQUENCES: dict[str, int] = {}
_SEQUENCE_LOCK = threading.Lock()
_ACTIVE_FALLBACK_MESSAGE_IDS: dict[tuple[str, str, str | None], str] = {}
_CURRENT_FALLBACK_KEYS: dict[tuple[str, str], tuple[str, str, str | None]] = {}
_FALLBACK_LIFECYCLE_COUNTS: dict[tuple[str, str], int] = {}
_AMBIGUOUS_TERMINAL = object()
_SEND_LOCKS: dict[tuple[int, str, str], asyncio.Lock] = {}
_SEND_LOCKS_GUARD = threading.Lock()
_POST_FAILED = object()
_PENDING_DELTAS: dict[tuple[int, str, str, str, str], _PendingDelta] = {}
_PENDING_DELTAS_LOCK = threading.Lock()
_HFC_FEISHU_COMMAND_RESULT_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "hfc_feishu_command_result_context",
    default=None,
)
_HFC_FEISHU_NOTICE_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "hfc_feishu_notice_context",
    default=None,
)
_HFC_COMMAND_RESULT_CARD_COMMANDS = {"new", "reset", "clear", "undo", "stop", "model"}
_OPERATION_TRANSPORT_SECRETS: dict[str, tuple[bytes, str, float]] = {}
_OPERATION_TRANSPORT_SECRETS_LOCK = threading.Lock()
_OPERATION_TRANSPORT_SECRET_TTL_SECONDS = 600.0
_OPERATION_TRANSPORT_SECRET_LIMIT = 256


class _OperationsActionDispatcher:
    def __init__(self, *, workers: int, max_pending: int):
        self._workers = workers
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue(
            maxsize=max_pending
        )
        self._start_lock = threading.Lock()
        self._started = False

    def submit(self, task: Callable[[], None]) -> bool:
        self._ensure_started()
        try:
            self._queue.put_nowait(task)
        except queue.Full:
            return False
        return True

    def wait(self) -> None:
        self._queue.join()

    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._start_lock:
            if self._started:
                return
            for index in range(self._workers):
                threading.Thread(
                    target=self._run,
                    name=f"hfc-operations-action-{index + 1}",
                    daemon=True,
                ).start()
            self._started = True

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            try:
                task()
            except Exception as exc:
                _hfc_warn(
                    "operations.select background worker failed: "
                    f"{exc.__class__.__name__}"
                )
            finally:
                self._queue.task_done()


_OPERATIONS_ACTION_DISPATCHER = _OperationsActionDispatcher(
    workers=OPERATIONS_ACTION_WORKERS,
    max_pending=OPERATIONS_ACTION_QUEUE_LIMIT,
)


def reset_runtime_state() -> None:
    with _SEQUENCE_LOCK:
        _SEQUENCES.clear()
    _ACTIVE_FALLBACK_MESSAGE_IDS.clear()
    _CURRENT_FALLBACK_KEYS.clear()
    _FALLBACK_LIFECYCLE_COUNTS.clear()
    with _SEND_LOCKS_GUARD:
        _SEND_LOCKS.clear()
    with _PENDING_DELTAS_LOCK:
        _PENDING_DELTAS.clear()
    with _OPERATION_TRANSPORT_SECRETS_LOCK:
        _OPERATION_TRANSPORT_SECRETS.clear()
    _HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(None)
    _HFC_FEISHU_NOTICE_CONTEXT.set(None)


def load_runtime_config() -> RuntimeConfig:
    enabled_value = os.environ.get("HERMES_FEISHU_CARD_ENABLED", "1").strip().lower()
    enabled = enabled_value not in {"0", "false", "no", "off"}
    event_url = os.environ.get("HERMES_FEISHU_CARD_EVENT_URL", DEFAULT_EVENT_URL).strip()
    if not event_url:
        event_url = DEFAULT_EVENT_URL
    timeout_seconds = _timeout_from_env(os.environ.get("HERMES_FEISHU_CARD_TIMEOUT_MS"))
    delta_coalesce_ms = _int_from_env(
        os.environ.get("HERMES_FEISHU_CARD_DELTA_COALESCE_MS"),
        default=250,
        minimum=0,
        maximum=5000,
    )
    delta_coalesce_chars = _int_from_env(
        os.environ.get("HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS"),
        default=600,
        minimum=1,
        maximum=20000,
    )
    delta_coalesce_max_pending = _int_from_env(
        os.environ.get("HERMES_FEISHU_CARD_DELTA_COALESCE_MAX_PENDING"),
        default=128,
        minimum=1,
        maximum=5000,
    )
    return RuntimeConfig(
        enabled=enabled,
        event_url=event_url,
        timeout_seconds=timeout_seconds,
        delta_coalesce_ms=delta_coalesce_ms,
        delta_coalesce_chars=delta_coalesce_chars,
        delta_coalesce_max_pending=delta_coalesce_max_pending,
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


def _int_from_env(
    value: str | None,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if not minimum <= parsed <= maximum:
        return default
    return parsed


def _queue_coalesced_delta(
    config: RuntimeConfig,
    local_vars: dict[str, Any],
    event_name: str,
) -> bool:
    if event_name not in {"thinking.delta", "answer.delta"}:
        return False
    if config.delta_coalesce_ms <= 0:
        return False
    identity = _delta_coalesce_identity(config, local_vars, event_name)
    if identity is None:
        return False
    key, loop, base_locals, text = identity
    should_flush_now = False
    should_schedule = False
    with _PENDING_DELTAS_LOCK:
        pending = _PENDING_DELTAS.get(key)
        if pending is None:
            if len(_PENDING_DELTAS) >= config.delta_coalesce_max_pending:
                _PENDING_DELTAS.pop(next(iter(_PENDING_DELTAS)), None)
            pending = _PendingDelta(
                event_name=event_name,
                event_url=config.event_url,
                timeout_seconds=_timeout_for_event(config, event_name),
                loop=loop,
                base_locals=base_locals,
                text_parts=[],
            )
            _PENDING_DELTAS[key] = pending
        pending.text_parts.append(text)
        pending.char_count += len(text)
        if pending.char_count >= config.delta_coalesce_chars:
            should_flush_now = True
        elif not pending.scheduled:
            pending.scheduled = True
            should_schedule = True
    if should_flush_now:
        _schedule_delta_flush(loop, key, 0.0)
    elif should_schedule:
        _schedule_delta_flush(loop, key, config.delta_coalesce_ms / 1000.0)
    return True


def _delta_coalesce_identity(
    config: RuntimeConfig,
    local_vars: dict[str, Any],
    event_name: str,
) -> tuple[tuple[int, str, str, str, str], Any, dict[str, Any], str] | None:
    source_obj = local_vars.get("source")
    if _platform_name(local_vars, source_obj) != "feishu":
        return None
    message_obj = local_vars.get("message")
    gateway_event_obj = local_vars.get("event")
    message_id = _first_string(
        local_vars, ("message_id", "msg_id", "event_message_id")
    ) or _first_attr_string(
        message_obj, ("message_id", "msg_id")
    ) or _first_attr_string(
        gateway_event_obj, ("message_id", "msg_id")
    )
    if not message_id:
        return None
    text = _first_raw_string(local_vars, ("text", "delta", "delta_text", "content"))
    if text is None:
        text = _first_attr_raw_string(message_obj, ("text", "content"))
    if not text:
        return None
    loop = local_vars.get("_hfc_loop")
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
    profile_id, _profile_source = _profile_identity(local_vars, source_obj, message_obj)
    key = (id(loop), config.event_url, message_id, event_name, profile_id)
    return key, loop, _delta_base_locals(local_vars), str(text)


def _delta_base_locals(local_vars: dict[str, Any]) -> dict[str, Any]:
    keep_keys = {
        "source",
        "event",
        "message",
        "chat_id",
        "open_chat_id",
        "receive_id",
        "conversation_id",
        "thread_id",
        "session_id",
        "message_id",
        "msg_id",
        "event_message_id",
        "created_at",
        "profile_id",
        "hermes_profile",
        "profile",
        "mode",
        "_hfc_text_mode",
    }
    return {key: value for key, value in local_vars.items() if key in keep_keys}


def _schedule_delta_flush(loop: Any, key: tuple[int, str, str, str, str], delay: float) -> None:
    async def flush_later() -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await _flush_pending_delta_key(key)

    def create_task() -> None:
        asyncio.create_task(flush_later())

    try:
        if loop.is_running():
            loop.call_soon_threadsafe(create_task)
    except Exception:
        return


async def flush_pending_deltas_for_message(message_id: str) -> None:
    message_id = str(message_id or "").strip()
    if not message_id:
        return
    with _PENDING_DELTAS_LOCK:
        keys = [
            key
            for key, pending in _PENDING_DELTAS.items()
            if _pending_message_id(key, pending) == message_id
        ]
    for key in keys:
        await _flush_pending_delta_key(key)


async def _flush_pending_deltas_for_local_vars(local_vars: dict[str, Any]) -> None:
    message_id = _message_id_from_local_vars(local_vars)
    if message_id:
        await flush_pending_deltas_for_message(message_id)


def _has_pending_deltas_for_local_vars(local_vars: dict[str, Any]) -> bool:
    message_id = _message_id_from_local_vars(local_vars)
    if not message_id:
        return False
    with _PENDING_DELTAS_LOCK:
        return any(
            _pending_message_id(key, pending) == message_id
            for key, pending in _PENDING_DELTAS.items()
        )


def _message_id_from_local_vars(local_vars: dict[str, Any]) -> str | None:
    message_obj = local_vars.get("message")
    gateway_event_obj = local_vars.get("event")
    message_id = _first_string(
        local_vars, ("message_id", "msg_id", "event_message_id")
    ) or _first_attr_string(
        message_obj, ("message_id", "msg_id")
    ) or _first_attr_string(
        gateway_event_obj, ("message_id", "msg_id")
    )
    return message_id


def _pending_message_id(
    key: tuple[int, str, str, str, str], pending: _PendingDelta
) -> str:
    return key[2]


async def _flush_pending_delta_key(key: tuple[int, str, str, str, str]) -> None:
    with _PENDING_DELTAS_LOCK:
        pending = _PENDING_DELTAS.pop(key, None)
    if pending is None or not pending.text_parts:
        return
    payload = build_event(
        pending.event_name,
        {**pending.base_locals, "text": "".join(pending.text_parts)},
    )
    if payload is None:
        return
    await _send_fail_open_ordered(
        pending.event_url,
        payload,
        pending.timeout_seconds,
    )


async def _flush_build_send_ordered(
    config: RuntimeConfig,
    local_vars: dict[str, Any],
    event_name: str,
) -> None:
    await _flush_pending_deltas_for_local_vars(local_vars)
    payload = build_event(event_name, local_vars)
    if payload is None:
        return
    await _send_fail_open_ordered(
        config.event_url,
        payload,
        _timeout_for_event(config, event_name),
    )


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
        asyncio.get_running_loop()
        asyncio.create_task(
            _send_fail_open_ordered(
                config.event_url,
                payload,
                _timeout_for_event(config, event_name),
            )
        )
        return True
    except Exception:
        return False


def emit_from_hermes_locals_threadsafe(
    local_vars: dict[str, Any],
    event_name: str = "message.started",
) -> bool:
    try:
        config = load_runtime_config()
        if not config.enabled:
            return False
        if _queue_coalesced_delta(config, local_vars, event_name):
            return True
        if _has_pending_deltas_for_local_vars(local_vars):
            if "_hfc_loop" in local_vars:
                coroutine = _flush_build_send_ordered(config, local_vars, event_name)
                try:
                    asyncio.run_coroutine_threadsafe(coroutine, local_vars["_hfc_loop"])
                except Exception:
                    coroutine.close()
                    raise
            else:
                asyncio.get_running_loop()
                asyncio.create_task(
                    _flush_build_send_ordered(config, local_vars, event_name)
                )
            return True
        payload = build_event(event_name, local_vars)
        if payload is None:
            return False
        if "_hfc_loop" in local_vars:
            coroutine = _send_fail_open_ordered(
                config.event_url,
                payload,
                _timeout_for_event(config, event_name),
            )
            try:
                asyncio.run_coroutine_threadsafe(coroutine, local_vars["_hfc_loop"])
            except Exception:
                coroutine.close()
                raise
        else:
            asyncio.get_running_loop()
            asyncio.create_task(
                _send_fail_open_ordered(
                    config.event_url,
                    payload,
                    _timeout_for_event(config, event_name),
                )
            )
        return True
    except Exception:
        return False


async def emit_from_hermes_locals_async(
    local_vars: dict[str, Any],
    event_name: str = "message.started",
) -> bool:
    try:
        config = load_runtime_config()
        if not config.enabled:
            return False
        if event_name not in {"thinking.delta", "answer.delta"}:
            await _flush_pending_deltas_for_local_vars(local_vars)
        payload = build_event(event_name, local_vars)
        if payload is None:
            return False
        result = await _post_json_ordered_response(
            config.event_url,
            payload,
            _timeout_for_event(config, event_name),
        )
        return _event_was_applied(result)
    except Exception:
        return False


def _event_was_applied(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("ok") is False:
        return False
    if result.get("applied") is False:
        return False
    return True


def emit_cron_delivery(local_vars: dict[str, Any]) -> bool:
    try:
        config = load_runtime_config()
        if not config.enabled:
            return False
        payload = build_cron_event(local_vars)
        if payload is None:
            return False
        return _post_json_sync(config.event_url, payload, TERMINAL_TIMEOUT_SECONDS)
    except Exception:
        return False


def handle_hfc_command_from_hermes_locals(local_vars: dict[str, Any]) -> bool:
    try:
        config = load_runtime_config()
        if not config.enabled:
            return False
        source_obj = local_vars.get("source")
        if _platform_name(local_vars, source_obj) != "feishu":
            return False
        command = _parse_hfc_command(_command_text(local_vars))
        if command is None:
            return False
        message_obj = local_vars.get("message")
        gateway_event_obj = local_vars.get("event")
        chat_id = _first_string(local_vars, ("chat_id", "open_chat_id", "receive_id"))
        if chat_id is None:
            chat_id = _first_attr_string(
                message_obj, ("chat_id", "open_chat_id", "receive_id")
            )
        if chat_id is None:
            chat_id = _first_attr_string(
                source_obj, ("chat_id", "open_chat_id", "receive_id")
            )
        if chat_id is None:
            return False
        message_id = _first_string(
            local_vars, ("message_id", "msg_id", "event_message_id")
        ) or _first_attr_string(
            message_obj, ("message_id", "msg_id")
        ) or _first_attr_string(
            gateway_event_obj, ("message_id", "msg_id")
        )
        if not message_id:
            return False
        profile_id, profile_source = _profile_identity(local_vars, source_obj, message_obj)
        payload = {
            "command": command,
            "chat_id": chat_id,
            "message_id": message_id,
            "thread_id": _thread_id_for_runtime_event(local_vars, message_obj, source_obj),
            "reply_to_message_id": _reply_to_message_id_from_runtime(
                local_vars,
                message_obj,
                gateway_event_obj,
            ),
            "profile_id": profile_id,
            "profile_source": profile_source,
            "chat_type": _command_chat_type(
                local_vars, source_obj, gateway_event_obj
            ),
            "operator": _command_operator(
                local_vars, source_obj, gateway_event_obj
            ),
            "created_at": _created_at(local_vars.get("created_at")),
            "platform": "feishu",
        }
        if command == "doctor":
            root_secret = read_transport_root_secret()
            if root_secret is None:
                return False
            payload["adapter_command_proof"] = sign_command_transport_proof(
                root_secret,
                payload,
                timestamp=int(time.time()),
                nonce=secrets.token_urlsafe(18),
            )
        url = f"{_summary_base_url(config.event_url)}/commands"
        if command != "doctor":
            return _post_json_sync(url, payload, config.timeout_seconds)
        result = _post_json_sync_response(url, payload, config.timeout_seconds)
        if not isinstance(result, dict) or result.get("ok") is not True:
            return False
        operation_id = str(result.get("operation_id") or "").strip()
        if not operation_id:
            return False
        _remember_operation_transport(
            operation_id,
            derive_operation_transport_secret(root_secret, operation_id),
            profile_id,
        )
        return True
    except Exception:
        return False


def _remember_operation_transport(
    operation_id: str,
    secret: str | bytes,
    profile_id: str | None = None,
    transport_lineage_id: str = "",
) -> None:
    operation_id = str(operation_id or "").strip()
    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not operation_id or not isinstance(secret_bytes, bytes) or len(secret_bytes) < 16:
        return
    now = time.time()
    with _OPERATION_TRANSPORT_SECRETS_LOCK:
        _prune_operation_transport_secrets_locked(now)
        existing = _OPERATION_TRANSPORT_SECRETS.get(operation_id)
        trusted_profile_id = (
            str(profile_id).strip()
            if isinstance(profile_id, str) and profile_id.strip()
            else existing[1] if existing is not None else "default"
        )
        context = (
            secret_bytes,
            trusted_profile_id,
            now + _OPERATION_TRANSPORT_SECRET_TTL_SECONDS,
        )
        _OPERATION_TRANSPORT_SECRETS[operation_id] = context
        lineage_id = str(transport_lineage_id or "").strip()
        if lineage_id:
            _OPERATION_TRANSPORT_SECRETS[lineage_id] = context
        while len(_OPERATION_TRANSPORT_SECRETS) > _OPERATION_TRANSPORT_SECRET_LIMIT:
            _OPERATION_TRANSPORT_SECRETS.pop(
                next(iter(_OPERATION_TRANSPORT_SECRETS))
            )


def _operation_transport_context(operation_id: str) -> tuple[bytes, str] | None:
    now = time.time()
    with _OPERATION_TRANSPORT_SECRETS_LOCK:
        _prune_operation_transport_secrets_locked(now)
        item = _OPERATION_TRANSPORT_SECRETS.get(operation_id)
        return (item[0], item[1]) if item is not None else None


def _transport_secret_for_token(token: str) -> bytes | None:
    operation_id = _operation_id_from_token(token)
    context = _operation_transport_context(operation_id) if operation_id else None
    return context[0] if context is not None else None


def _operation_id_from_token(token: str) -> str:
    try:
        if not isinstance(token, str) or not token or len(token) > 2048:
            return ""
        encoded, _signature = token.rsplit(".", 1)
        padding = "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(
            encoded + padding,
            altchars=b"-_",
            validate=True,
        )
        if len(decoded) > 1024:
            return ""
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict):
            return ""
        operation_id = payload.get("operation_id")
        return str(operation_id).strip() if isinstance(operation_id, str) else ""
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""


def _prune_operation_transport_secrets_locked(now: float) -> None:
    for operation_id, (_secret, _profile_id, expires_at) in list(
        _OPERATION_TRANSPORT_SECRETS.items()
    ):
        if expires_at <= now:
            _OPERATION_TRANSPORT_SECRETS.pop(operation_id, None)


def _command_text(local_vars: dict[str, Any]) -> str:
    text = _first_raw_string(local_vars, ("text", "content", "message_text", "query"))
    if text is not None:
        return text
    message_obj = local_vars.get("message")
    text = _first_attr_raw_string(message_obj, ("text", "content"))
    if text is not None:
        return text
    gateway_event_obj = local_vars.get("event")
    text = _first_attr_raw_string(gateway_event_obj, ("text", "content"))
    return text or ""


def _command_chat_type(
    local_vars: dict[str, Any], source_obj: Any, gateway_event_obj: Any
) -> str:
    message_obj = local_vars.get("message")
    return (
        _first_string(local_vars, ("chat_type",))
        or _first_attr_string(message_obj, ("chat_type",))
        or _first_attr_string(source_obj, ("chat_type",))
        or _first_attr_string(gateway_event_obj, ("chat_type",))
        or ""
    )


def _command_operator(
    local_vars: dict[str, Any], source_obj: Any, gateway_event_obj: Any
) -> str:
    aliases = ("operator_open_id", "sender_open_id", "open_id")
    direct = _first_string(local_vars, aliases)
    if direct:
        return direct
    message_obj = local_vars.get("message")
    for candidate in (
        local_vars.get("operator"),
        local_vars.get("sender_id"),
        getattr(message_obj, "operator", None),
        getattr(message_obj, "sender_id", None),
        getattr(source_obj, "operator", None),
        getattr(source_obj, "sender_id", None),
        getattr(gateway_event_obj, "operator", None),
        getattr(gateway_event_obj, "sender_id", None),
    ):
        value = _first_attr_string(candidate, ("open_id",))
        if value:
            return value
    return ""


def _parse_hfc_command(text: str) -> str | None:
    stripped = str(text or "").strip()
    if not stripped:
        return None
    match = re.match(r"^/hfc(?:\s+([A-Za-z0-9_-]+))?\s*$", stripped, re.IGNORECASE)
    if not match:
        return None
    command = (match.group(1) or "help").lower()
    if command not in {"help", "status", "doctor", "monitor"}:
        return "help"
    return command


def _reply_to_message_id_from_runtime(
    local_vars: dict[str, Any],
    message_obj: Any,
    gateway_event_obj: Any,
) -> str:
    aliases = ("reply_to_message_id", "quote_message_id", "parent_message_id")
    source_obj = local_vars.get("source")
    value = (
        _first_string(local_vars, aliases)
        or _first_attr_string(message_obj, aliases)
        or _first_attr_string(gateway_event_obj, aliases)
        or _first_attr_string(source_obj, aliases)
    )
    if not value:
        source_message_id = _first_attr_string(
            source_obj, ("message_id", "msg_id", "event_message_id")
        )
        event_message_id = _message_id_from_local_vars(local_vars)
        if (
            source_message_id
            and source_message_id != event_message_id
            and _thread_id_for_runtime_event(local_vars, message_obj, source_obj)
        ):
            value = source_message_id
    return value or ""


def build_interaction_event(
    local_vars: dict[str, Any],
    *,
    kind: str,
    interaction_id: str,
    prompt: str,
    options: list[dict[str, Any]] | None = None,
    description: str = "",
    timeout_seconds: float | None = None,
    fallback_policy: str = "",
) -> dict[str, Any] | None:
    event_locals = {
        **local_vars,
        "_hfc_interaction_id": interaction_id,
        "_hfc_interaction_kind": kind,
        "_hfc_interaction_prompt": prompt,
        "_hfc_interaction_description": description,
        "_hfc_interaction_options": options or [],
        "_hfc_interaction_timeout_seconds": timeout_seconds,
        "_hfc_interaction_fallback_policy": fallback_policy,
    }
    return build_event("interaction.requested", event_locals)


def request_interaction_from_hermes_locals(
    local_vars: dict[str, Any],
    *,
    kind: str,
    interaction_id: str,
    prompt: str,
    options: list[dict[str, Any]] | None = None,
    description: str = "",
    timeout_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
) -> dict[str, Any] | None:
    try:
        config = load_runtime_config()
        if not config.enabled:
            return None
        payload = build_interaction_event(
            local_vars,
            kind=kind,
            interaction_id=interaction_id,
            prompt=prompt,
            options=options or [],
            description=description,
            timeout_seconds=timeout_seconds,
        )
        if payload is None:
            return None
        post_result = _post_interaction_event(
            local_vars,
            config.event_url,
            payload,
            _timeout_for_event(config, payload["event"]),
        )
        if post_result is _POST_FAILED:
            return None
        if isinstance(post_result, dict) and post_result.get("ok") is False:
            return None
        if _uses_text_interaction_fallback(post_result):
            return None
        if isinstance(post_result, dict) and post_result.get("applied") is False:
            for _ in range(2):
                time.sleep(0.05)
                payload = build_interaction_event(
                    local_vars,
                    kind=kind,
                    interaction_id=interaction_id,
                    prompt=prompt,
                    options=options or [],
                    description=description,
                    timeout_seconds=timeout_seconds,
                )
                if payload is None:
                    return None
                post_result = _post_interaction_event(
                    local_vars,
                    config.event_url,
                    payload,
                    _timeout_for_event(config, payload["event"]),
                )
                if post_result is _POST_FAILED:
                    return None
                if isinstance(post_result, dict) and post_result.get("ok") is False:
                    return None
                if _uses_text_interaction_fallback(post_result):
                    return None
                if not (
                    isinstance(post_result, dict)
                    and post_result.get("applied") is False
                ):
                    break
            else:
                return None
        base_url = _summary_base_url(config.event_url)
        url = f"{base_url}/interactions/{parse.quote(interaction_id, safe='')}"
        timeout = _interaction_timeout(timeout_seconds)
        poll_interval = _interaction_poll_interval(poll_interval_seconds)
        deadline = time.monotonic() + timeout
        while True:
            try:
                result = _get_json_sync(url, config.timeout_seconds)
            except Exception:
                result = None
            if isinstance(result, dict) and result.get("status") in {"completed", "failed"}:
                return result
            if time.monotonic() >= deadline:
                return {
                    "ok": False,
                    "status": "timeout",
                    "interaction_id": interaction_id,
                }
            time.sleep(poll_interval)
    except Exception:
        return None


async def request_slash_confirm_from_hermes_locals_async(
    local_vars: dict[str, Any],
    *,
    command: str,
    title: str,
    message: str,
    interaction_id: str,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
) -> str | None:
    try:
        config = load_runtime_config()
        if not config.enabled:
            return None
        if _hfc_native_feishu_command_cards_available(local_vars):
            return None
        command_text = str(command or "").strip().lstrip("/")
        prompt = str(title or "").strip() or f"Confirm /{command_text or 'command'}"
        payload = build_interaction_event(
            local_vars,
            kind="slash_confirm",
            interaction_id=interaction_id,
            prompt=prompt,
            description=str(message or "").strip(),
            options=[
                {"label": "允许一次", "value": "once", "style": "primary"},
                {"label": "始终允许", "value": "always"},
                {"label": "取消", "value": "cancel", "style": "danger"},
            ],
            timeout_seconds=timeout_seconds,
            fallback_policy="native_text",
        )
        if payload is None:
            return None
        try:
            post_result = await _post_json_ordered_response(
                config.event_url,
                payload,
                _timeout_for_event(config, payload["event"]),
            )
        except Exception:
            return None
        if isinstance(post_result, dict) and post_result.get("ok") is False:
            return None
        if _uses_text_interaction_fallback(post_result):
            return None
        if isinstance(post_result, dict) and post_result.get("applied") is False:
            for _ in range(2):
                await asyncio.sleep(0.05)
                payload = build_interaction_event(
                    local_vars,
                    kind="slash_confirm",
                    interaction_id=interaction_id,
                    prompt=prompt,
                    description=str(message or "").strip(),
                    options=[
                        {"label": "允许一次", "value": "once", "style": "primary"},
                        {"label": "始终允许", "value": "always"},
                        {"label": "取消", "value": "cancel", "style": "danger"},
                    ],
                    timeout_seconds=timeout_seconds,
                    fallback_policy="native_text",
                )
                if payload is None:
                    return None
                try:
                    post_result = await _post_json_ordered_response(
                        config.event_url,
                        payload,
                        _timeout_for_event(config, payload["event"]),
                    )
                except Exception:
                    return None
                if isinstance(post_result, dict) and post_result.get("ok") is False:
                    return None
                if _uses_text_interaction_fallback(post_result):
                    return None
                if not (
                    isinstance(post_result, dict)
                    and post_result.get("applied") is False
                ):
                    break
            else:
                return None
        base_url = _summary_base_url(config.event_url)
        url = f"{base_url}/interactions/{parse.quote(interaction_id, safe='')}"
        timeout = _interaction_timeout(timeout_seconds)
        poll_interval = _interaction_poll_interval(poll_interval_seconds)
        deadline = time.monotonic() + timeout
        while True:
            try:
                result = await _get_json(url, config.timeout_seconds)
            except Exception:
                result = None
            if isinstance(result, dict) and result.get("status") == "completed":
                choice = str(result.get("choice") or "").strip()
                if choice in {"once", "always", "cancel"}:
                    return choice
                return None
            if isinstance(result, dict) and result.get("status") == "failed":
                return None
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(poll_interval)
    except Exception:
        return None


def _uses_text_interaction_fallback(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and str(result.get("interaction_mode") or "").strip().lower()
        in {"text", "markdown", "reply"}
    )


def _hfc_native_feishu_command_cards_available(local_vars: dict[str, Any]) -> bool:
    try:
        source_obj = local_vars.get("source")
        if _platform_name(local_vars, source_obj) != "feishu":
            return False
        runner = local_vars.get("self") or local_vars.get("runner")
        adapters = getattr(runner, "adapters", None)
        if not isinstance(adapters, dict):
            return False
        for key, adapter in list(adapters.items()):
            if not _is_feishu_adapter_key(key, adapter):
                continue
            if not getattr(adapter, "_client", None):
                continue
            if not hasattr(adapter, "_feishu_send_with_retry"):
                continue
            install_feishu_command_card_adapter_methods(runner)
            return callable(getattr(adapter, "send_slash_confirm", None))
        return False
    except Exception:
        return False


def _is_feishu_adapter_key(key: Any, adapter: Any) -> bool:
    key_text = str(getattr(key, "value", key) or "").strip().lower()
    if key_text == "feishu":
        return True
    name = str(getattr(adapter, "name", "") or "").strip().lower()
    if name == "feishu":
        return True
    platform = getattr(adapter, "platform", None)
    return str(getattr(platform, "value", platform) or "").strip().lower() == "feishu"


async def _hfc_send_model_picker(
    self,
    chat_id: str,
    providers: Any,
    current_model: str = "",
    current_provider: str = "",
    session_key: str = "",
    on_model_selected: Any = None,
    metadata: dict[str, Any] | None = None,
):
    try:
        options = _model_picker_options(providers, current_model=current_model)
        if not options:
            return _send_result(False, error="no model options")
        reply_to = _metadata_reply_to(metadata)
        message_id = reply_to or "model_" + sha256(
            f"{chat_id}:{session_key}:{time.time()}".encode("utf-8")
        ).hexdigest()[:16]
        interaction_id = "model_" + sha256(
            f"{chat_id}:{session_key}:{message_id}".encode("utf-8")
        ).hexdigest()[:16]
        prompt = "选择模型"
        description_parts = []
        if current_model:
            description_parts.append(f"当前模型：`{current_model}`")
        if current_provider:
            description_parts.append(f"当前 provider：`{current_provider}`")
        choice = await _request_command_card_choice_async(
            {
                "chat_id": chat_id,
                "conversation_id": session_key or chat_id,
                "message_id": message_id,
                "reply_to_message_id": reply_to,
            },
            kind="model_picker",
            interaction_id=interaction_id,
            prompt=prompt,
            description="\n".join(description_parts),
            options=options,
        )
        if choice is None:
            return _send_result(False, error="model picker card unavailable")
        selected = _parse_model_picker_choice(choice)
        if selected is None:
            await complete_command_card_from_hermes_locals_async(
                {
                    "chat_id": chat_id,
                    "conversation_id": session_key or chat_id,
                    "message_id": message_id,
                },
                answer="模型选择无效，请重新发送 `/model`。",
            )
            return _send_result(True, message_id=message_id)
        provider_slug, model_id = selected
        if on_model_selected is None:
            result_text = f"已选择 {provider_slug}/{model_id}"
        else:
            result_text = await on_model_selected(chat_id, model_id, provider_slug)
        await complete_command_card_from_hermes_locals_async(
            {
                "chat_id": chat_id,
                "conversation_id": session_key or chat_id,
                "message_id": message_id,
            },
            answer=result_text,
        )
        return _send_result(True, message_id=message_id)
    except Exception as exc:
        return _send_result(False, error=str(exc))


async def _request_command_card_choice_async(
    local_vars: dict[str, Any],
    *,
    kind: str,
    interaction_id: str,
    prompt: str,
    options: list[dict[str, Any]],
    description: str = "",
    timeout_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
) -> str | None:
    try:
        config = load_runtime_config()
        if not config.enabled:
            return None
        payload = build_interaction_event(
            local_vars,
            kind=kind,
            interaction_id=interaction_id,
            prompt=prompt,
            options=options,
            description=description,
            timeout_seconds=timeout_seconds,
            fallback_policy="native_text",
        )
        if payload is None:
            return None
        try:
            post_result = await _post_json_ordered_response(
                config.event_url,
                payload,
                _timeout_for_event(config, payload["event"]),
            )
        except Exception:
            return None
        if isinstance(post_result, dict) and post_result.get("ok") is False:
            return None
        if _uses_text_interaction_fallback(post_result):
            return None
        base_url = _summary_base_url(config.event_url)
        url = f"{base_url}/interactions/{parse.quote(interaction_id, safe='')}"
        timeout = _interaction_timeout(timeout_seconds)
        poll_interval = _interaction_poll_interval(poll_interval_seconds)
        deadline = time.monotonic() + timeout
        while True:
            try:
                result = await _get_json(url, config.timeout_seconds)
            except Exception:
                result = None
            if isinstance(result, dict) and result.get("status") == "completed":
                choice = str(result.get("choice") or "").strip()
                return choice or None
            if isinstance(result, dict) and result.get("status") == "failed":
                return None
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(poll_interval)
    except Exception:
        return None


def _model_picker_options(
    providers: Any,
    *,
    current_model: str = "",
    max_options: int = 24,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    if not isinstance(providers, list):
        return options
    current = str(current_model or "").strip()
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        provider_slug = str(provider.get("slug") or provider.get("provider") or "").strip()
        provider_name = str(provider.get("name") or provider_slug or "provider").strip()
        models = provider.get("models")
        if not isinstance(models, list):
            continue
        for model in models:
            model_id = str(model or "").strip()
            if not model_id:
                continue
            label = f"{provider_name} · {model_id}"
            if model_id == current:
                label = f"当前 · {label}"
            options.append(
                {
                    "label": label[:80],
                    "value": json.dumps(
                        {"provider": provider_slug, "model": model_id},
                        ensure_ascii=False,
                    ),
                    "style": "primary" if model_id == current else "default",
                }
            )
            if len(options) >= max_options:
                return options
    return options


def _resume_picker_options(
    sessions: Any,
    *,
    current_session_id: str = "",
    max_options: int = 10,
) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    if not isinstance(sessions, list):
        return options
    current = str(current_session_id or "").strip()
    for row in sessions:
        if not isinstance(row, dict):
            continue
        session_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not session_id or not title:
            continue
        preview = str(row.get("preview") or "").strip()[:40]
        prefix = "当前 · " if session_id == current else ""
        suffix = f" — {preview}" if preview else ""
        label = f"{prefix}{title}{suffix}"[:80]
        options.append({"label": label, "value": session_id})
        if len(options) >= max_options:
            break
    return options


def _hfc_resume_source_name(source: Any) -> str | None:
    platform = getattr(source, "platform", None)
    value = str(getattr(platform, "value", platform) or "").strip().lower()
    return value or None


def _hfc_resume_metadata(runner: Any, event: Any, source: Any) -> dict[str, Any]:
    reply_anchor = _hfc_command_event_message_id(event)
    get_reply_anchor = getattr(runner, "_reply_anchor_for_event", None)
    if callable(get_reply_anchor):
        try:
            reply_anchor = str(get_reply_anchor(event) or reply_anchor).strip()
        except Exception:
            pass
    get_metadata = getattr(runner, "_thread_metadata_for_source", None)
    if callable(get_metadata):
        try:
            metadata = get_metadata(source, reply_anchor)
            if isinstance(metadata, dict):
                return dict(metadata)
        except Exception:
            pass
    metadata: dict[str, Any] = {}
    if reply_anchor:
        metadata["reply_to_message_id"] = reply_anchor
    thread_id = str(getattr(source, "thread_id", "") or "").strip()
    if thread_id:
        metadata["thread_id"] = thread_id
    return metadata


def _hfc_resume_operator_open_id(event: Any) -> str:
    source = getattr(event, "source", None)
    raw = getattr(event, "raw_message", None)
    raw_event = getattr(raw, "event", None)
    candidates = [
        getattr(event, "sender_id", None),
        getattr(source, "sender_id", None),
        getattr(raw, "sender_id", None),
        getattr(getattr(raw, "sender", None), "sender_id", None),
        getattr(getattr(raw_event, "sender", None), "sender_id", None),
    ]
    for candidate in candidates:
        open_id = str(getattr(candidate, "open_id", "") or "").strip()
        if open_id:
            return open_id
    source_user_id = str(getattr(source, "user_id", "") or "").strip()
    return source_user_id if source_user_id.startswith("ou_") else ""


async def _hfc_try_resume_picker(
    runner: Any,
    event: Any,
    original_handler: Any,
) -> bool:
    try:
        source = getattr(event, "source", None)
        if source is None or _platform_name({}, source) != "feishu":
            return False
        session_db = getattr(runner, "_session_db", None)
        list_sessions = getattr(session_db, "list_sessions_rich", None)
        if not callable(list_sessions):
            return False
        rows = await list_sessions(source=_hfc_resume_source_name(source), limit=10)
        if not isinstance(rows, list):
            return False
        visible: list[dict[str, Any]] = []
        row_visible = getattr(runner, "_resume_row_visible", None)
        if not callable(row_visible):
            return False
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("title") or "").strip():
                continue
            if await row_visible(source, row, False):
                visible.append(row)
            if len(visible) >= 10:
                break
        if not visible:
            return False

        adapter = None
        adapters = getattr(runner, "adapters", None)
        if isinstance(adapters, dict):
            for key, candidate in adapters.items():
                if _is_feishu_adapter_key(key, candidate):
                    adapter = candidate
                    break
        if adapter is None or not getattr(adapter, "_client", None):
            return False
        send_picker = getattr(adapter, "send_resume_picker", None)
        if not callable(send_picker):
            return False

        current_session_id = ""
        session_store = getattr(runner, "session_store", None)
        get_current = getattr(session_store, "get_or_create_session", None)
        if callable(get_current):
            try:
                current = get_current(source)
                current_session_id = str(getattr(current, "session_id", "") or "")
            except Exception:
                pass
        result = await send_picker(
            chat_id=str(getattr(source, "chat_id", "") or ""),
            sessions=visible,
            current_session_id=current_session_id,
            runner=runner,
            event=event,
            original_handler=original_handler,
            metadata=_hfc_resume_metadata(runner, event, source),
        )
        return bool(getattr(result, "success", False))
    except Exception as exc:
        _hfc_warn(f"resume picker failed open: {exc.__class__.__name__}: {exc}")
        return False


async def _hfc_handle_resume_command_with_picker(runner: Any, event: Any) -> Any:
    original = getattr(type(runner), "_hfc_original_handle_resume_command", None)
    if not callable(original):
        return None
    try:
        get_args = getattr(event, "get_command_args", None)
        raw_args = str(get_args() or "").strip() if callable(get_args) else ""
    except Exception:
        raw_args = ""
    if raw_args:
        return await original(runner, event)
    if await _hfc_try_resume_picker(runner, event, original):
        return None
    return await original(runner, event)


def _hfc_install_resume_picker_handler(runner_type: type[Any]) -> bool:
    current = runner_type.__dict__.get("_handle_resume_command")
    if current is _hfc_handle_resume_command_with_picker:
        setattr(runner_type, "_hfc_resume_picker_wrapped", True)
        return True
    if getattr(runner_type, "_hfc_resume_picker_wrapped", False):
        return callable(getattr(runner_type, "_handle_resume_command", None))
    original = current or getattr(runner_type, "_handle_resume_command", None)
    if not callable(original):
        return False
    setattr(runner_type, "_hfc_original_handle_resume_command", original)
    setattr(runner_type, "_handle_resume_command", _hfc_handle_resume_command_with_picker)
    setattr(runner_type, "_hfc_resume_picker_wrapped", True)
    return True


def _parse_model_picker_choice(choice: str) -> tuple[str, str] | None:
    try:
        data = json.loads(choice)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    provider = str(data.get("provider") or "").strip()
    model = str(data.get("model") or "").strip()
    if not provider or not model:
        return None
    return provider, model


def _metadata_reply_to(metadata: dict[str, Any] | None) -> str:
    if not isinstance(metadata, dict):
        return ""
    return str(
        metadata.get("reply_to_message_id")
        or metadata.get("message_id")
        or metadata.get("reply_to")
        or ""
    ).strip()


def _send_result(success: bool, message_id: str | None = None, error: str | None = None):
    return SimpleNamespace(success=success, message_id=message_id, error=error)


def _hfc_feishu_response_types(adapter: Any) -> tuple[Any, Any]:
    module = sys.modules.get(type(adapter).__module__)
    if module is None:
        return None, None
    return (
        getattr(module, "P2CardActionTriggerResponse", None),
        getattr(module, "CallBackCard", None),
    )


def _hfc_empty_feishu_callback_response(adapter: Any) -> Any:
    response_type, _ = _hfc_feishu_response_types(adapter)
    return response_type() if response_type is not None else None


def _hfc_toast_feishu_callback_response(
    adapter: Any, content: str, *, toast_type: str = "warning"
) -> Any:
    response_type, _ = _hfc_feishu_response_types(adapter)
    if response_type is None:
        return None
    response = response_type()
    module = sys.modules.get(type(adapter).__module__)
    callback_toast_type = getattr(module, "CallBackToast", None) if module else None
    if callback_toast_type is None:
        response_types = getattr(response_type, "_types", {})
        if isinstance(response_types, dict):
            callback_toast_type = response_types.get("toast")
    if callback_toast_type is None:
        return response
    toast = callback_toast_type()
    toast.type = toast_type
    toast.content = content
    response.toast = toast
    return response


def _hfc_raw_feishu_callback_response(adapter: Any, card_data: dict[str, Any]) -> Any:
    response_type, card_type = _hfc_feishu_response_types(adapter)
    if response_type is None:
        return None
    response = response_type()
    if card_type is not None:
        card = card_type()
        card.type = "raw"
        card.data = card_data
        response.card = card
    return response


def _hfc_response_success(response: Any) -> bool:
    success_value = getattr(response, "success", None)
    if callable(success_value):
        try:
            return bool(success_value())
        except Exception:
            return False
    if success_value is not None:
        return bool(success_value)
    return False


def _hfc_response_message_id(response: Any) -> str:
    direct = str(getattr(response, "message_id", "") or "")
    if direct:
        return direct
    data = getattr(response, "data", None)
    data_message_id = str(getattr(data, "message_id", "") or "")
    if data_message_id:
        return data_message_id
    raw_response = getattr(response, "raw_response", None)
    if raw_response is not None and raw_response is not response:
        return _hfc_response_message_id(raw_response)
    return ""


def _hfc_feishu_send_success(response: Any) -> tuple[bool, str]:
    return _hfc_response_success(response), _hfc_response_message_id(response)


def _hfc_update_response_success(response: Any) -> bool:
    return _hfc_response_success(response)


def _hfc_update_response_error(response: Any) -> str:
    try:
        code = getattr(response, "code", None)
        msg = getattr(response, "msg", None)
        if code or msg:
            return f"code={code!r} msg={msg!r}"
    except Exception:
        pass
    raw_response = getattr(response, "raw_response", None)
    if raw_response is not None and raw_response is not response:
        return _hfc_update_response_error(raw_response)
    return repr(response)


def _hfc_warn(message: str) -> None:
    try:
        logger.warning("[hermes-feishu-card] %s", message)
    except Exception:
        pass
    try:
        print(f"[hermes-feishu-card] {message}", file=sys.stderr)
    except Exception:
        pass


def _hfc_info(message: str) -> None:
    try:
        logger.info("[hermes-feishu-card] %s", message)
    except Exception:
        pass


def _hfc_slash_confirm_detail(message: str) -> str:
    text = str(message or "").strip()
    text = re.sub(r"^⚠️\s*\*\*Confirm /[^*]+\*\*\s*", "", text).strip()
    text = re.split(r"\n\s*Choose:\s*\n", text, maxsplit=1)[0].strip()
    text = re.sub(r"\n\s*_Text fallback:.*$", "", text, flags=re.DOTALL).strip()
    return text or str(message or "").strip()


def _hfc_button(label: str, value: dict[str, Any], button_type: str = "default") -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": button_type,
        "value": value,
    }


def _hfc_select_static(
    *,
    placeholder: str,
    value: dict[str, Any],
    options: list[dict[str, str]],
    initial_option: str = "",
) -> dict[str, Any]:
    element: dict[str, Any] = {
        "tag": "select_static",
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "value": value,
        "options": [
            {
                "text": {"tag": "plain_text", "content": str(option.get("label") or "")[:80]},
                "value": str(option.get("value") or ""),
            }
            for option in options
            if option.get("label") and option.get("value")
        ],
    }
    if initial_option:
        element["initial_option"] = initial_option
    return element


def _hfc_command_result_card(
    *,
    title: str,
    content: str,
    template: str = "green",
) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": template,
        },
        "elements": [
            {
                "tag": "markdown",
                "content": str(content or "").strip() or "已处理。",
            }
        ],
    }


def _hfc_command_from_event(event: Any) -> str:
    command = ""
    getter = getattr(event, "get_command", None)
    if callable(getter):
        try:
            command = str(getter() or "")
        except Exception:
            command = ""
    if not command:
        text = str(getattr(event, "text", "") or "").strip()
        if text.startswith("/"):
            command = text
    command = command.strip()
    if command.startswith("/"):
        command = command[1:]
    if not command:
        return ""
    return command.split(None, 1)[0].strip().lower()


def _hfc_command_event_message_id(event: Any) -> str:
    for obj in (event, getattr(event, "source", None)):
        if obj is None:
            continue
        for name in ("message_id", "id", "event_message_id"):
            try:
                value = str(getattr(obj, name, "") or "").strip()
            except Exception:
                value = ""
            if value:
                return value
    return ""


def _hfc_command_result_context_from_event(event: Any) -> dict[str, str] | None:
    if event is None:
        return None
    source = getattr(event, "source", None)
    if _platform_name({}, source) != "feishu":
        return None
    command = _hfc_command_from_event(event)
    if command not in _HFC_COMMAND_RESULT_CARD_COMMANDS:
        return None
    return {
        "command": command,
        "chat_id": str(getattr(source, "chat_id", "") or "").strip(),
        "message_id": _hfc_command_event_message_id(event),
    }


def _hfc_command_result_title(command: str) -> str:
    return {
        "new": "会话已重置",
        "reset": "会话已重置",
        "clear": "上下文已清理",
        "undo": "已撤销上一步",
        "stop": "已停止",
        "model": "模型已更新",
    }.get(command, "命令已完成")


def _hfc_command_result_template(content: str) -> str:
    text = str(content or "").strip().lower()
    if text.startswith(("❌", "error", "failed")) or "失败" in text or "error:" in text:
        return "red"
    if text.startswith(("⚠️", "warning")) or "cancel" in text or "取消" in text:
        return "orange"
    return "green"


def _hfc_take_feishu_command_result_context(
    *,
    chat_id: str,
    content: Any,
) -> dict[str, str] | None:
    context = _HFC_FEISHU_COMMAND_RESULT_CONTEXT.get()
    if not isinstance(context, dict):
        return None
    command = str(context.get("command") or "").strip().lower()
    if command not in _HFC_COMMAND_RESULT_CARD_COMMANDS:
        _HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(None)
        return None
    if not str(content or "").strip():
        return None
    expected_chat_id = str(context.get("chat_id") or "").strip()
    actual_chat_id = str(chat_id or "").strip()
    if expected_chat_id and actual_chat_id and expected_chat_id != actual_chat_id:
        _HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(None)
        return None
    _HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(None)
    return context


def _hfc_notice_context_from_event(event: Any) -> dict[str, str] | None:
    if event is None:
        return None
    source = getattr(event, "source", None)
    if _platform_name({}, source) != "feishu":
        return None
    return _hfc_notice_context_from_source(source, event=event)


def _hfc_notice_context_from_source(
    source: Any,
    *,
    event: Any = None,
) -> dict[str, str] | None:
    if _platform_name({}, source) != "feishu":
        return None
    chat_id = str(getattr(source, "chat_id", "") or "").strip()
    if not chat_id:
        return None
    message_id = str(
        getattr(source, "message_id", "")
        or getattr(event, "message_id", "")
        or ""
    ).strip()
    thread_id = str(
        getattr(source, "thread_id", "")
        or (getattr(event, "thread_id", "") if event is not None else "")
        or ""
    ).strip()
    return {
        "chat_id": chat_id,
        "message_id": message_id,
        "conversation_id": thread_id or chat_id,
        "thread_id": thread_id,
    }


def _hfc_classify_system_notice(content: Any) -> dict[str, str] | None:
    text = str(content or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if text.startswith("⏳") or lowered.startswith("working ") or "working —" in lowered:
        return {
            "title": "运行中",
            "level": "info",
            "notice_kind": "heartbeat",
            "notice_id": "heartbeat",
        }
    if "caps context" in lowered and "auto-compaction" in lowered:
        return {
            "title": "上下文窗口提示",
            "level": "info",
            "notice_kind": "context-cap",
            "notice_id": "context-cap",
        }
    if "session automatically reset" in lowered:
        return {
            "title": "会话已自动重置",
            "level": "success",
            "notice_kind": "session-reset",
            "notice_id": "session-reset",
        }
    if "reading skill" in lowered:
        return {
            "title": "技能加载",
            "level": "info",
            "notice_kind": "skill-loading",
            "notice_id": _hfc_content_notice_id("skill-loading", text),
        }
    if "self-improvement review" in lowered:
        return {
            "title": "自我改进",
            "level": "info",
            "notice_kind": "self-improvement",
            "notice_id": _hfc_content_notice_id("self-improvement", text),
        }
    if "context compression" in lowered or "compression model" in lowered:
        return {
            "title": "上下文压缩提示",
            "level": "info",
            "notice_kind": "compression",
            "notice_id": _hfc_content_notice_id("compression", text),
        }
    return None


def _hfc_content_notice_id(kind: str, content: str) -> str:
    digest = sha256(f"{kind}:{content}".encode("utf-8")).hexdigest()[:10]
    return f"{kind}:{digest}"


async def _hfc_send_system_notice_card(
    adapter: Any,
    *,
    chat_id: str,
    content: Any,
    reply_to: str | None = None,
    metadata: dict[str, Any] | None = None,
    existing_message_id: str | None = None,
) -> Any:
    notice = _hfc_classify_system_notice(content)
    if notice is None:
        return _send_result(False, error="not a system notice")
    try:
        config = load_runtime_config()
        if not config.enabled:
            return _send_result(False, error="disabled")
        context = _HFC_FEISHU_NOTICE_CONTEXT.get()
        if not isinstance(context, dict):
            context = {}
        message_id = str(existing_message_id or context.get("message_id") or "").strip()
        if message_id and not message_id.startswith("notice_"):
            payload = _hfc_build_system_notice_payload(
                chat_id=chat_id,
                content=str(content or ""),
                reply_to=reply_to,
                metadata=metadata,
                context=context,
                notice=notice,
                notice_scope="session",
                message_id=message_id,
            )
            post_result = await _post_json_ordered_response(
                config.event_url,
                payload,
                max(_timeout_for_event(config, payload["event"]), TERMINAL_TIMEOUT_SECONDS),
            )
            if _hfc_notice_post_applied(post_result):
                return _send_result(True, message_id=payload["message_id"])

        independent_message_id = (
            message_id
            if message_id.startswith("notice_")
            else _hfc_independent_notice_message_id(chat_id, str(content or ""), notice)
        )
        payload = _hfc_build_system_notice_payload(
            chat_id=chat_id,
            content=str(content or ""),
            reply_to=reply_to,
            metadata=metadata,
            context=context,
            notice=notice,
            notice_scope="independent",
            message_id=independent_message_id,
        )
        post_result = await _post_json_ordered_response(
            config.event_url,
            payload,
            max(_timeout_for_event(config, payload["event"]), TERMINAL_TIMEOUT_SECONDS),
        )
        if _hfc_notice_post_applied(post_result):
            return _send_result(True, message_id=payload["message_id"])
    except Exception as exc:
        _hfc_warn(f"send system notice card failed: {exc.__class__.__name__}: {exc}")
        return _send_result(False, error=str(exc))
    return _send_result(False, error="system notice card not applied")


def _hfc_notice_post_applied(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("ok") is False:
        return False
    return result.get("applied") is not False


def _hfc_independent_notice_message_id(
    chat_id: str,
    content: str,
    notice: dict[str, str],
) -> str:
    bucket = int(time.time() // 300)
    raw = (
        f"{chat_id}:"
        f"{notice.get('notice_id', '')}:"
        f"{content}:"
        f"{bucket}"
    ).encode("utf-8")
    return "notice_" + sha256(raw).hexdigest()[:16]


def _hfc_build_system_notice_payload(
    *,
    chat_id: str,
    content: str,
    reply_to: str | None,
    metadata: dict[str, Any] | None,
    context: dict[str, str],
    notice: dict[str, str],
    notice_scope: str,
    message_id: str,
) -> dict[str, Any]:
    reply_id = str(reply_to or "").strip() or _metadata_reply_to(metadata)
    conversation_id = str(context.get("conversation_id") or chat_id).strip() or chat_id
    thread_id = str(context.get("thread_id") or "").strip()
    source = SimpleNamespace(platform="feishu", chat_id=chat_id, thread_id=thread_id)
    local_vars: dict[str, Any] = {
        "source": source,
        "chat_id": chat_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "content": content,
        "_hfc_notice_title": notice.get("title") or "运行提示",
        "_hfc_notice_level": notice.get("level") or "info",
        "_hfc_notice_kind": notice.get("notice_kind") or "system",
        "_hfc_notice_id": notice.get("notice_id") or "",
        "_hfc_notice_scope": notice_scope,
        "delivery_kind": "notice" if notice_scope == "independent" else "chat",
    }
    if reply_id:
        local_vars["reply_to_message_id"] = reply_id
    payload = build_event("system.notice", local_vars)
    if payload is None:
        raise RuntimeError("failed to build system.notice payload")
    return payload


async def _hfc_send_native_command_result_card(
    adapter: Any,
    *,
    chat_id: str,
    content: str,
    reply_to: str | None,
    metadata: dict[str, Any] | None,
    context: dict[str, str],
) -> Any:
    if not getattr(adapter, "_client", None):
        return _send_result(False, error="not connected")
    if not hasattr(adapter, "_feishu_send_with_retry"):
        return _send_result(False, error="feishu send unavailable")

    command = str(context.get("command") or "").strip().lower()
    card = _hfc_command_result_card(
        title=_hfc_command_result_title(command),
        content=content,
        template=_hfc_command_result_template(content),
    )
    effective_reply_to = (
        str(reply_to or "").strip()
        or _metadata_reply_to(metadata)
        or str(context.get("message_id") or "").strip()
        or None
    )
    try:
        response = await adapter._feishu_send_with_retry(
            chat_id=chat_id,
            msg_type="interactive",
            payload=json.dumps(card, ensure_ascii=False),
            reply_to=effective_reply_to,
            metadata=metadata,
        )
    except Exception as exc:
        _hfc_warn(f"send command result card failed: {exc.__class__.__name__}: {exc}")
        return _send_result(False, error=str(exc))

    finalizer = getattr(adapter, "_finalize_send_result", None)
    if callable(finalizer):
        try:
            return finalizer(response, "send command result card failed")
        except Exception:
            pass
    success, message_id = _hfc_feishu_send_success(response)
    if not success:
        _hfc_warn(f"send command result card failed: response={response!r}")
        return _send_result(False, error="send command result card failed")
    return _send_result(True, message_id=message_id)


async def _hfc_send_with_native_command_result_card(
    self: Any,
    chat_id: str,
    content: str,
    reply_to: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    original = getattr(type(self), "_hfc_original_send", None)
    context = _hfc_take_feishu_command_result_context(chat_id=chat_id, content=content)
    if context is not None:
        result = await _hfc_send_native_command_result_card(
            self,
            chat_id=chat_id,
            content=str(content or ""),
            reply_to=reply_to,
            metadata=metadata,
            context=context,
        )
        if getattr(result, "success", False):
            return result
    notice_result = await _hfc_send_system_notice_card(
        self,
        chat_id=chat_id,
        content=content,
        reply_to=reply_to,
        metadata=metadata,
    )
    if getattr(notice_result, "success", False):
        return notice_result
    if _hfc_classify_system_notice(content) is not None:
        error = getattr(notice_result, "error", None) or "system notice card not applied"
        _hfc_warn(f"system notice native fallback suppressed: {error}")
        return _send_result(
            True,
            message_id=(
                str(reply_to or "").strip()
                or _metadata_reply_to(metadata)
                or "notice_suppressed"
            ),
        )
    if callable(original):
        return await original(self, chat_id, content, reply_to=reply_to, metadata=metadata)
    return _send_result(False, error="original Feishu send unavailable")


async def _hfc_edit_message_with_system_notice_card(self: Any, *args: Any, **kwargs: Any) -> Any:
    original = getattr(type(self), "_hfc_original_edit_message", None)
    parsed = _hfc_parse_edit_message_args(args, kwargs)
    if parsed is not None:
        chat_id, message_id, content, metadata = parsed
        notice_result = await _hfc_send_system_notice_card(
            self,
            chat_id=chat_id,
            content=content,
            metadata=metadata,
            existing_message_id=message_id,
        )
        if getattr(notice_result, "success", False):
            return notice_result
    if callable(original):
        return await original(self, *args, **kwargs)
    return _send_result(False, error="original Feishu edit_message unavailable")


def handle_platform_notice_from_hermes(runner: Any, source: Any, content: str) -> bool:
    """Route Hermes native platform notices into Feishu cards before text fallback."""
    try:
        if _platform_name({}, source) != "feishu":
            return False
        chat_id = str(getattr(source, "chat_id", "") or "").strip()
        if not chat_id:
            return False
        if _hfc_classify_system_notice(str(content or "")) is None:
            return False
        adapter = _hfc_feishu_adapter_from_runner(runner, source)
        if adapter is None:
            return False
        _hfc_schedule_platform_notice_card(
            adapter=adapter,
            chat_id=chat_id,
            content=str(content or ""),
            reply_to=str(getattr(source, "message_id", "") or "").strip() or None,
            notice_context=_hfc_notice_context_from_source(source),
        )
        return True
    except Exception as exc:
        _hfc_warn(
            "platform notice hook failed: "
            f"{exc.__class__.__name__}: {exc}"
        )
        return False


async def _hfc_deliver_platform_notice_with_card(
    self: Any,
    source: Any,
    content: str,
) -> Any:
    original = getattr(type(self), "_hfc_original_deliver_platform_notice", None)
    if handle_platform_notice_from_hermes(self, source, content):
        return _send_result(
            True,
            message_id=str(getattr(source, "message_id", "") or "").strip() or None,
        )
    if callable(original):
        return await original(self, source, content)
    return None


def _hfc_feishu_adapter_from_runner(runner: Any, source: Any) -> Any:
    adapters = getattr(runner, "adapters", {})
    if not isinstance(adapters, dict):
        return None
    adapter = adapters.get(getattr(source, "platform", None))
    if adapter is not None:
        return adapter
    for key, candidate in list(adapters.items()):
        if _is_feishu_adapter_key(key, candidate):
            return candidate
    return None


def _hfc_schedule_platform_notice_card(
    *,
    adapter: Any,
    chat_id: str,
    content: str,
    reply_to: str | None,
    notice_context: dict[str, str] | None,
) -> None:
    async def send_notice() -> None:
        token = None
        if notice_context is not None:
            token = _HFC_FEISHU_NOTICE_CONTEXT.set(notice_context)
        try:
            notice_result = await _hfc_send_system_notice_card(
                adapter,
                chat_id=chat_id,
                content=content,
                reply_to=reply_to,
                metadata=None,
            )
            if not getattr(notice_result, "success", False):
                error = getattr(notice_result, "error", None) or "unknown"
                _hfc_warn(
                    "system notice card delivery failed; native notice suppressed: "
                    f"{error}"
                )
        except Exception as exc:
            _hfc_warn(
                "system notice card delivery failed; native notice suppressed: "
                f"{exc.__class__.__name__}: {exc}"
            )
        finally:
            if token is not None:
                _HFC_FEISHU_NOTICE_CONTEXT.reset(token)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(send_notice())
        return
    loop.create_task(send_notice())


def _hfc_parse_edit_message_args(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[str, str, str, dict[str, Any] | None] | None:
    chat_id = str(kwargs.get("chat_id") or "").strip()
    message_id = str(kwargs.get("message_id") or kwargs.get("msg_id") or "").strip()
    content = kwargs.get("content", kwargs.get("text"))
    metadata = kwargs.get("metadata")
    if len(args) >= 3:
        chat_id = chat_id or str(args[0] or "").strip()
        message_id = message_id or str(args[1] or "").strip()
        if content is None:
            content = args[2]
        if metadata is None and len(args) >= 4 and isinstance(args[3], dict):
            metadata = args[3]
    elif len(args) >= 2:
        message_id = message_id or str(args[0] or "").strip()
        if content is None:
            content = args[1]
    if not chat_id:
        context = _HFC_FEISHU_NOTICE_CONTEXT.get()
        if isinstance(context, dict):
            chat_id = str(context.get("chat_id") or "").strip()
    if not chat_id or not message_id or content is None:
        return None
    return chat_id, message_id, str(content or ""), metadata if isinstance(metadata, dict) else None


def _hfc_slash_choice_label(choice: str) -> tuple[str, str]:
    if choice == "always":
        return "已始终允许", "green"
    if choice == "cancel":
        return "已取消", "red"
    return "已允许一次", "green"


async def _hfc_send_native_slash_confirm(
    self: Any,
    chat_id: str,
    title: str,
    message: str,
    session_key: str,
    confirm_id: str,
    metadata: dict[str, Any] | None = None,
):
    if not getattr(self, "_client", None):
        _hfc_warn("send_slash_confirm skipped: Feishu adapter is not connected")
        return _send_result(False, error="not connected")
    if not hasattr(self, "_feishu_send_with_retry"):
        _hfc_warn("send_slash_confirm skipped: Feishu adapter send helper is unavailable")
        return _send_result(False, error="feishu send unavailable")

    prompt_title = str(title or "").strip() or "确认命令"
    detail = _hfc_slash_confirm_detail(message)
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": prompt_title, "tag": "plain_text"},
            "template": "orange",
        },
        "elements": [
            {"tag": "markdown", "content": detail},
            {
                "tag": "action",
                "actions": [
                    _hfc_button(
                        "允许一次",
                        {
                            "hfc_action": "slash_confirm",
                            "hfc_confirm_id": confirm_id,
                            "hfc_choice": "once",
                        },
                        "primary",
                    ),
                    _hfc_button(
                        "始终允许",
                        {
                            "hfc_action": "slash_confirm",
                            "hfc_confirm_id": confirm_id,
                            "hfc_choice": "always",
                        },
                    ),
                    _hfc_button(
                        "取消",
                        {
                            "hfc_action": "slash_confirm",
                            "hfc_confirm_id": confirm_id,
                            "hfc_choice": "cancel",
                        },
                        "danger",
                    ),
                ],
            },
        ],
    }
    try:
        response = await self._feishu_send_with_retry(
            chat_id=chat_id,
            msg_type="interactive",
            payload=json.dumps(card, ensure_ascii=False),
            reply_to=_metadata_reply_to(metadata) or None,
            metadata=metadata,
        )
    except Exception as exc:
        _hfc_warn(f"send_slash_confirm failed: {exc.__class__.__name__}: {exc}")
        return _send_result(False, error=str(exc))

    success, message_id = _hfc_feishu_send_success(response)
    if not success:
        _hfc_warn(f"send_slash_confirm failed: response={response!r}")
        return _send_result(False, error="send_slash_confirm failed")
    _hfc_info(f"send_slash_confirm stored confirm_id={confirm_id!r} message_id={message_id!r}")
    state = getattr(self, "_hfc_slash_confirm_state", None)
    if not isinstance(state, dict):
        state = {}
        setattr(self, "_hfc_slash_confirm_state", state)
    state[str(confirm_id)] = {
        "session_key": str(session_key or ""),
        "chat_id": str(chat_id or ""),
        "message_id": message_id,
    }
    return _send_result(True, message_id=message_id)


async def _hfc_send_native_model_picker(
    self: Any,
    chat_id: str,
    providers: Any,
    current_model: str = "",
    current_provider: str = "",
    session_key: str = "",
    on_model_selected: Any = None,
    metadata: dict[str, Any] | None = None,
):
    if not getattr(self, "_client", None) or not hasattr(self, "_feishu_send_with_retry"):
        return await _hfc_send_model_picker(
            self,
            chat_id,
            providers,
            current_model=current_model,
            current_provider=current_provider,
            session_key=session_key,
            on_model_selected=on_model_selected,
            metadata=metadata,
        )

    options = _model_picker_options(providers, current_model=current_model, max_options=100)
    if not options:
        return _send_result(False, error="no model options")
    all_options_count = len(_model_picker_options(providers, current_model=current_model, max_options=1000))
    picker_id = "model_" + sha256(
        f"{chat_id}:{session_key}:{time.time()}".encode("utf-8")
    ).hexdigest()[:16]
    description_parts = []
    if current_model:
        description_parts.append(f"当前模型：`{current_model}`")
    if current_provider:
        description_parts.append(f"当前 provider：`{current_provider}`")
    if all_options_count > len(options):
        description_parts.append(f"展示前 {len(options)} 个可选模型，可继续用 `/model <模型名>` 精确切换。")
    initial_option = ""
    for option in options:
        if option.get("style") == "primary":
            initial_option = str(option.get("value") or "")
            break

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": "选择模型", "tag": "plain_text"},
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": "\n".join(description_parts) or "请选择模型。"},
            {
                "tag": "action",
                "actions": [
                    _hfc_select_static(
                        placeholder="选择模型",
                        value={
                            "hfc_action": "model_picker",
                            "hfc_model_picker_id": picker_id,
                        },
                        options=options,
                        initial_option=initial_option,
                    )
                ],
            },
        ],
    }
    try:
        response = await self._feishu_send_with_retry(
            chat_id=chat_id,
            msg_type="interactive",
            payload=json.dumps(card, ensure_ascii=False),
            reply_to=_metadata_reply_to(metadata) or None,
            metadata=metadata,
        )
    except Exception as exc:
        return _send_result(False, error=str(exc))

    success, message_id = _hfc_feishu_send_success(response)
    if not success:
        return _send_result(False, error="send_model_picker failed")
    _hfc_info(f"send_model_picker stored picker_id={picker_id!r} message_id={message_id!r}")
    state = getattr(self, "_hfc_model_picker_state", None)
    if not isinstance(state, dict):
        state = {}
        setattr(self, "_hfc_model_picker_state", state)
    state[picker_id] = {
        "chat_id": str(chat_id or ""),
        "session_key": str(session_key or ""),
        "message_id": message_id,
        "on_model_selected": on_model_selected,
    }
    return _send_result(True, message_id=message_id)


async def _hfc_send_native_resume_picker(
    self: Any,
    *,
    chat_id: str,
    sessions: Any,
    current_session_id: str = "",
    runner: Any,
    event: Any,
    original_handler: Any,
    metadata: dict[str, Any] | None = None,
):
    if not getattr(self, "_client", None) or not hasattr(
        self, "_feishu_send_with_retry"
    ):
        return _send_result(False, error="native resume picker unavailable")
    options = _resume_picker_options(
        sessions,
        current_session_id=current_session_id,
        max_options=10,
    )
    if not options:
        return _send_result(False, error="no resume options")
    picker_id = "resume_" + sha256(
        f"{chat_id}:{time.time()}:{secrets.token_hex(8)}".encode("utf-8")
    ).hexdigest()[:16]
    initial_option = next(
        (
            option["value"]
            for option in options
            if option["value"] == str(current_session_id or "")
        ),
        "",
    )
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": "恢复会话", "tag": "plain_text"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": "选择一个最近的命名会话。",
            },
            {
                "tag": "action",
                "actions": [
                    _hfc_select_static(
                        placeholder="选择会话",
                        value={
                            "hfc_action": "resume_picker",
                            "hfc_resume_picker_id": picker_id,
                        },
                        options=options,
                        initial_option=initial_option,
                    )
                ],
            },
        ],
    }
    try:
        response = await self._feishu_send_with_retry(
            chat_id=chat_id,
            msg_type="interactive",
            payload=json.dumps(card, ensure_ascii=False),
            reply_to=_metadata_reply_to(metadata) or None,
            metadata=metadata,
        )
    except Exception as exc:
        return _send_result(False, error=str(exc))
    success, message_id = _hfc_feishu_send_success(response)
    if not success:
        return _send_result(False, error="send_resume_picker failed")

    source = getattr(event, "source", None)
    state = getattr(self, "_hfc_resume_picker_state", None)
    if not isinstance(state, dict):
        state = {}
        setattr(self, "_hfc_resume_picker_state", state)
    state[picker_id] = {
        "allowed_session_ids": {option["value"] for option in options},
        "chat_id": str(chat_id or ""),
        "chat_type": str(getattr(source, "chat_type", "") or "").lower(),
        "operator_open_id": _hfc_resume_operator_open_id(event),
        "message_id": message_id,
        "runner": runner,
        "event": event,
        "original_handler": original_handler,
        "expires_at": time.time() + 300,
    }
    _hfc_info(
        f"send_resume_picker stored picker_id={picker_id!r} message_id={message_id!r}"
    )
    return _send_result(True, message_id=message_id)


def _hfc_action_value_from_data(data: Any) -> dict[str, Any]:
    event = getattr(data, "event", None)
    action = getattr(event, "action", None)
    action_value = getattr(action, "value", {}) or {}
    if isinstance(action_value, dict):
        value = dict(action_value)
    elif isinstance(action_value, str):
        try:
            parsed = json.loads(action_value)
        except Exception:
            parsed = {}
        value = dict(parsed) if isinstance(parsed, dict) else {}
    else:
        value = {}

    form_value = getattr(action, "form_value", {}) or {}
    if isinstance(form_value, dict):
        for key in (
            "hfc_action",
            "hfc_confirm_id",
            "hfc_choice",
            "hfc_model_picker_id",
            "hfc_resume_picker_id",
        ):
            if key not in value and form_value.get(key):
                value[key] = form_value.get(key)

    option = str(getattr(action, "option", "") or "").strip()
    if option and "hfc_choice" not in value:
        value["hfc_choice"] = option
    return value


def _hfc_action_metadata(data: Any) -> dict[str, Any] | None:
    """Best-effort extraction of message metadata from a card-action event.

    Used when sending a follow-up card so Feishu threading/metadata stays
    consistent. Returns None if not available (callers must accept None).
    """
    event = getattr(data, "event", None)
    if event is None:
        return None
    meta = getattr(event, "message", None)
    if isinstance(meta, dict):
        return meta.get("metadata")
    meta = getattr(event, "metadata", None)
    if isinstance(meta, dict):
        return meta
    return None


def _hfc_action_chat_id(data: Any) -> str:
    event = getattr(data, "event", None)
    context = getattr(event, "context", None)
    return str(getattr(context, "open_chat_id", "") or "")


def _hfc_action_open_id(data: Any) -> str:
    event = getattr(data, "event", None)
    operator = getattr(event, "operator", None)
    return str(getattr(operator, "open_id", "") or "")


def _hfc_card_operator_allowed(adapter: Any, data: Any, chat_id: str) -> bool:
    open_id = _hfc_action_open_id(data)
    if not open_id:
        return False
    allow_group_message = getattr(adapter, "_allow_group_message", None)
    if not callable(allow_group_message):
        return True
    sender_id = SimpleNamespace(open_id=open_id, user_id=str(getattr(getattr(getattr(data, "event", None), "operator", None), "user_id", "") or ""))
    try:
        return bool(allow_group_message(sender_id, chat_id, is_bot=False))
    except TypeError:
        return bool(allow_group_message(sender_id, chat_id))
    except Exception:
        return False


def _hfc_prepare_native_slash_action(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> dict[str, Any] | None:
    loop = getattr(adapter, "_loop", None)
    loop_accepts = getattr(adapter, "_loop_accepts_callbacks", None)
    if callable(loop_accepts) and not loop_accepts(loop):
        return None
    if loop is None:
        return None

    confirm_id = str(action_value.get("hfc_confirm_id") or "")
    choice = str(action_value.get("hfc_choice") or "")
    if choice not in {"once", "always", "cancel"}:
        return None
    state = getattr(adapter, "_hfc_slash_confirm_state", {})
    if not isinstance(state, dict):
        return None
    item = state.get(confirm_id)
    if not isinstance(item, dict):
        return None
    chat_id = _hfc_action_chat_id(data)
    expected_chat_id = str(item.get("chat_id") or "")
    if expected_chat_id and chat_id and expected_chat_id != chat_id:
        return None
    if not _hfc_card_operator_allowed(adapter, data, expected_chat_id or chat_id):
        return None
    return {
        "loop": loop,
        "state": state,
        "confirm_id": confirm_id,
        "choice": choice,
        "session_key": str(item.get("session_key") or ""),
        "message_id": str(item.get("message_id") or ""),
    }


def _hfc_native_slash_result_card(
    adapter: Any,
    data: Any,
    choice: str,
    result: Any,
) -> dict[str, Any]:
    label, template = _hfc_slash_choice_label(choice)
    open_id = _hfc_action_open_id(data)
    get_cached_name = getattr(adapter, "_get_cached_sender_name", None)
    user_name = get_cached_name(open_id) if callable(get_cached_name) else ""
    actor = f"\n\n操作人：{user_name or open_id}" if (user_name or open_id) else ""
    return _hfc_command_result_card(
        title=f"{label}",
        content=f"{str(result or label).strip()}{actor}",
        template=template,
    )


def _hfc_prepare_native_model_action(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> dict[str, Any] | None:
    loop = getattr(adapter, "_loop", None)
    loop_accepts = getattr(adapter, "_loop_accepts_callbacks", None)
    if callable(loop_accepts) and not loop_accepts(loop):
        return None
    if loop is None:
        return None

    picker_id = str(action_value.get("hfc_model_picker_id") or "")
    state = getattr(adapter, "_hfc_model_picker_state", {})
    if not isinstance(state, dict):
        return None
    item = state.get(picker_id)
    if not isinstance(item, dict):
        return None
    chat_id = _hfc_action_chat_id(data)
    expected_chat_id = str(item.get("chat_id") or "")
    if expected_chat_id and chat_id and expected_chat_id != chat_id:
        return None
    if not _hfc_card_operator_allowed(adapter, data, expected_chat_id or chat_id):
        return None
    return {
        "loop": loop,
        "state": state,
        "picker_id": picker_id,
        "item": item,
        "chat_id": chat_id,
        "expected_chat_id": expected_chat_id,
        "choice": str(action_value.get("hfc_choice") or ""),
    }


def _hfc_prepare_native_resume_action(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> dict[str, Any] | None:
    loop = getattr(adapter, "_loop", None)
    loop_accepts = getattr(adapter, "_loop_accepts_callbacks", None)
    if callable(loop_accepts) and not loop_accepts(loop):
        return None
    if loop is None:
        return None
    picker_id = str(action_value.get("hfc_resume_picker_id") or "")
    state = getattr(adapter, "_hfc_resume_picker_state", {})
    if not picker_id or not isinstance(state, dict):
        return None
    item = state.get(picker_id)
    if not isinstance(item, dict):
        return None
    if float(item.get("expires_at") or 0) <= time.time():
        state.pop(picker_id, None)
        return None
    chat_id = _hfc_action_chat_id(data)
    expected_chat_id = str(item.get("chat_id") or "")
    if expected_chat_id and chat_id and expected_chat_id != chat_id:
        return None
    if not _hfc_card_operator_allowed(adapter, data, expected_chat_id or chat_id):
        return None
    chat_type = str(item.get("chat_type") or "").strip().lower()
    initiating_operator = str(item.get("operator_open_id") or "").strip()
    action_operator = _hfc_action_open_id(data)
    if chat_type not in {"", "dm", "p2p", "private"}:
        if not initiating_operator or action_operator != initiating_operator:
            return None
    choice = str(action_value.get("hfc_choice") or "").strip()
    allowed = item.get("allowed_session_ids")
    if not choice or not isinstance(allowed, (set, frozenset, list, tuple)):
        return None
    if choice not in {str(value) for value in allowed}:
        return None
    state.pop(picker_id, None)
    return {
        "loop": loop,
        "picker_id": picker_id,
        "item": item,
        "choice": choice,
        "chat_id": chat_id,
        "expected_chat_id": expected_chat_id,
    }


def _hfc_on_feishu_card_action_trigger(self: Any, data: Any) -> Any:
    action_value = _hfc_action_value_from_data(data)
    action = str(action_value.get("hfc_action") or "").strip()

    # FIX-1: dedupe duplicate card-action deliveries (Feishu retries on
    # callback timeout). A repeated delivery after the first resolve finds the
    # picker/confirm state already popped and would otherwise return an empty
    # ack -> Feishu shows "callback error". Reply success immediately so a
    # retried click never triggers a false callback error.
    if action in (
        "slash_confirm",
        "model_picker",
        "resume_picker",
        "interaction.select",
    ):
        if _hfc_is_duplicate_card_action(self, data):
            return _hfc_empty_feishu_callback_response(self)

    if action == "slash_confirm":
        return _hfc_handle_native_slash_action(self, data, action_value)
    if action == "model_picker":
        return _hfc_handle_native_model_action(self, data, action_value)
    if action == "resume_picker":
        return _hfc_handle_native_resume_action(self, data, action_value)
    if action == "interaction.select":
        return _hfc_handle_interaction_select_action(self, data, action_value)
    if action == "operations.select":
        return _hfc_handle_operations_select_action(self, data, action_value)

    original = getattr(type(self), "_hfc_original_on_card_action_trigger", None)
    if callable(original):
        return original(self, data)
    return _hfc_empty_feishu_callback_response(self)


def _hfc_handle_operations_select_action(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> Any:
    _hfc_info("inline card action received: operations.select")
    operation_action = str(action_value.get("operation_action") or "").strip()
    token = str(action_value.get("token") or "").strip()
    transport_lineage_id = str(action_value.get("transport_lineage_id") or "").strip()
    profile_scope = str(action_value.get("profile_scope") or "").strip()
    chat_id = _hfc_action_chat_id(data)
    if not operation_action or not token or not chat_id:
        _hfc_info("operations.select ignored: missing action/token/chat")
        return _hfc_empty_feishu_callback_response(adapter)
    if not _hfc_card_operator_allowed(adapter, data, chat_id):
        _hfc_info("operations.select rejected by Hermes admission")
        return _hfc_empty_feishu_callback_response(adapter)

    open_id = _hfc_action_open_id(data)
    operation_id = _operation_id_from_token(token)
    transport_context = _operation_transport_context(transport_lineage_id or operation_id)
    if transport_context is None:
        _hfc_info("operations.select rejected: authentication session expired")
        return _hfc_empty_feishu_callback_response(adapter)
    transport_secret, profile_id = transport_context
    timestamp = int(time.time())
    forwarded_value = {
        "hfc_action": "operations.select",
        "operation_action": operation_action,
        "token": token,
    }
    if profile_scope:
        forwarded_value["profile_scope"] = profile_scope
    if transport_lineage_id:
        forwarded_value["transport_lineage_id"] = transport_lineage_id
    sidecar_payload = {
        "adapter_transport_proof": {
            "timestamp": timestamp,
            "signature": sign_transport_proof(
                transport_secret,
                token=token,
                action=operation_action,
                callback_chat_id=chat_id,
                callback_profile_id=profile_id,
                callback_profile_scope=profile_scope,
                operator_open_id=open_id,
                timestamp=timestamp,
            ),
        },
        "event": {
            "action": {"value": forwarded_value},
            "context": {
                "open_chat_id": chat_id,
                "profile_id": profile_id,
            },
            "operator": {"open_id": open_id},
        }
    }
    try:
        config = load_runtime_config()
        url = f"{_summary_base_url(config.event_url)}/card/actions"
    except Exception as exc:
        _hfc_warn(
            "operations.select background forward setup failed: "
            f"{exc.__class__.__name__}"
        )
        return _hfc_toast_feishu_callback_response(
            adapter, "操作暂不可用，请稍后重试"
        )

    def forward() -> None:
        last_error: Exception | None = None
        for attempt in range(OPERATIONS_ACTION_FORWARD_ATTEMPTS):
            try:
                result = _post_json_sync_response(
                    url,
                    sidecar_payload,
                    OPERATIONS_ACTION_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                last_error = exc
                if attempt + 1 < OPERATIONS_ACTION_FORWARD_ATTEMPTS:
                    time.sleep(OPERATIONS_ACTION_RETRY_DELAY_SECONDS)
                    continue
                break
            if isinstance(result, dict):
                successor_id = str(result.get("operation_id") or "").strip()
                if successor_id:
                    _remember_operation_transport(
                        successor_id,
                        transport_secret,
                        profile_id,
                        transport_lineage_id or operation_id,
                    )
            return
        if last_error is not None:
            _hfc_warn(
                "operations.select background forward failed: "
                f"{last_error.__class__.__name__}"
            )

    try:
        accepted = _OPERATIONS_ACTION_DISPATCHER.submit(forward)
    except Exception as exc:
        _hfc_warn(
            "operations.select background dispatch failed: "
            f"{exc.__class__.__name__}"
        )
        accepted = False
    if not accepted:
        _hfc_warn("operations.select background dispatch unavailable: capacity")
        return _hfc_toast_feishu_callback_response(
            adapter, "操作繁忙，请稍后重试"
        )
    return _hfc_empty_feishu_callback_response(adapter)


def _hfc_handle_interaction_select_action(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> Any:
    """Forward an agent clarify/approval interaction card click to the sidecar.

    Agent-initiated interaction option cards (``interaction.requested``) render
    Feishu ``callback`` buttons whose value carries
    ``hfc_action=interaction.select`` plus ``interaction_id`` / ``choice`` /
    ``choice_label`` / ``token``. Under a Feishu/Lark WebSocket long-connection
    deployment the click is delivered here (the adapter's card action channel),
    NOT to the sidecar's ``/card/actions`` HTTP endpoint — the sidecar is on
    localhost and Feishu cannot POST to it. Prior to this, such clicks fell
    through to the original adapter handler and were dropped, so the card looked
    unresponsive (and ``card.interaction_mode: auto`` had to fall back to text).

    This mirrors the existing ``slash_confirm`` / ``model_picker`` WS-native
    paths: rebuild the native Feishu card-action payload, POST it to the
    sidecar's ``/card/actions`` endpoint (which marks the interaction completed
    so the Hermes hook polling ``/interactions/{id}`` unblocks), and return the
    sidecar's updated card so Feishu updates the card in place.
    """
    _hfc_info("inline card action received: interaction.select")
    interaction_id = str(action_value.get("interaction_id") or "").strip()
    token = str(action_value.get("token") or "").strip()
    choice = str(action_value.get("choice") or action_value.get("hfc_choice") or "").strip()
    choice_label = str(action_value.get("choice_label") or choice).strip()
    if not interaction_id or not token or not choice:
        _hfc_info("interaction.select ignored: missing interaction_id/token/choice")
        return _hfc_empty_feishu_callback_response(adapter)

    chat_id = _hfc_action_chat_id(data)
    open_id = _hfc_action_open_id(data)
    operator_name = ""
    event_obj = getattr(data, "event", None)
    operator_obj = getattr(event_obj, "operator", None)
    if operator_obj is not None:
        operator_name = str(
            getattr(operator_obj, "user_name", "")
            or getattr(operator_obj, "name", "")
            or ""
        ).strip()

    operator_payload: dict[str, Any] = {}
    if operator_name:
        operator_payload["name"] = operator_name
    if open_id:
        operator_payload["open_id"] = open_id

    sidecar_payload = {
        "event": {
            "action": {
                "value": {
                    "hfc_action": "interaction.select",
                    "interaction_id": interaction_id,
                    "choice": choice,
                    "choice_label": choice_label,
                    "token": token,
                }
            },
            "context": {"open_chat_id": chat_id},
            "operator": operator_payload,
        }
    }

    try:
        config = load_runtime_config()
        base_url = _summary_base_url(config.event_url)
        url = f"{base_url}/card/actions"
        result = _post_json_sync_response(url, sidecar_payload, 5.0)
    except Exception as exc:
        _hfc_warn(f"interaction.select forward failed: {exc.__class__.__name__}: {exc}")
        return _hfc_empty_feishu_callback_response(adapter)

    if isinstance(result, dict) and isinstance(result.get("card"), dict):
        _hfc_info(f"interaction.select resolved: interaction_id={interaction_id!r}")
        return _hfc_raw_feishu_callback_response(adapter, result["card"])
    _hfc_info("interaction.select forwarded but no card returned")
    return _hfc_empty_feishu_callback_response(adapter)


def _hfc_resolve_native_slash_action(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    prepared = _hfc_prepare_native_slash_action(adapter, data, action_value)
    if prepared is None:
        return None

    try:
        from tools import slash_confirm

        result = slash_confirm.resolve_sync_compat(
            prepared["loop"],
            prepared["session_key"],
            prepared["confirm_id"],
            prepared["choice"],
        )
    except Exception as exc:
        result = f"处理失败：{exc}"
    prepared["state"].pop(prepared["confirm_id"], None)
    return (
        _hfc_native_slash_result_card(adapter, data, prepared["choice"], result),
        prepared["message_id"],
    )


async def _hfc_resolve_native_slash_action_async(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    prepared = _hfc_prepare_native_slash_action(adapter, data, action_value)
    if prepared is None:
        return None

    try:
        from tools import slash_confirm

        resolve = getattr(slash_confirm, "resolve", None)
        if callable(resolve):
            result = await resolve(
                prepared["session_key"],
                prepared["confirm_id"],
                prepared["choice"],
            )
        else:
            result = slash_confirm.resolve_sync_compat(
                prepared["loop"],
                prepared["session_key"],
                prepared["confirm_id"],
                prepared["choice"],
            )
    except Exception as exc:
        result = f"处理失败：{exc}"
    prepared["state"].pop(prepared["confirm_id"], None)
    return (
        _hfc_native_slash_result_card(adapter, data, prepared["choice"], result),
        prepared["message_id"],
    )


def _hfc_schedule_native_command_card_update(
    adapter: Any,
    message_id: str,
    card: dict[str, Any],
) -> None:
    message_id = str(message_id or "").strip()
    if not message_id:
        _hfc_warn("native command card update skipped: missing message_id")
        return
    loop = getattr(adapter, "_loop", None)
    loop_accepts = getattr(adapter, "_loop_accepts_callbacks", None)
    if callable(loop_accepts) and not loop_accepts(loop):
        _hfc_warn("native command card update skipped: adapter loop is not ready")
        return
    submit = getattr(adapter, "_submit_on_loop", None)
    if not callable(submit):
        _hfc_warn("native command card update skipped: submit helper unavailable")
        return
    coro = _hfc_update_native_command_card(adapter, message_id, card)
    submitted = False
    try:
        submitted = bool(submit(loop, coro))
    except Exception as exc:
        _hfc_warn(f"native command card update schedule failed: {exc.__class__.__name__}: {exc}")
    finally:
        if not submitted:
            try:
                coro.close()
            except Exception:
                pass
    if not submitted:
        _hfc_warn("native command card update schedule failed")


def _hfc_handle_native_slash_action(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> Any:
    _hfc_info("inline card action received: slash_confirm")
    resolved = _hfc_resolve_native_slash_action(adapter, data, action_value)
    if resolved is None:
        _hfc_info("inline slash_confirm ignored: unresolved")
        return _hfc_empty_feishu_callback_response(adapter)
    card, message_id = resolved
    _hfc_info(f"inline slash_confirm resolved: message_id={message_id!r}")
    return _hfc_raw_feishu_callback_response(adapter, card)


def _hfc_resolve_native_model_action(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    prepared = _hfc_prepare_native_model_action(adapter, data, action_value)
    if prepared is None:
        return None

    item = prepared["item"]
    selected = _parse_model_picker_choice(prepared["choice"])
    if selected is None:
        return (
            _hfc_command_result_card(
                title="模型选择无效",
                content="请重新发送 `/model`。",
                template="red",
            ),
            str(item.get("message_id") or ""),
        )
    provider_slug, model_id = selected
    callback = item.get("on_model_selected")
    try:
        if callback is None:
            result = f"已选择 {provider_slug}/{model_id}"
        else:
            future = asyncio.run_coroutine_threadsafe(
                callback(
                    prepared["expected_chat_id"] or prepared["chat_id"],
                    model_id,
                    provider_slug,
                ),
                prepared["loop"],
            )
            result = future.result(timeout=30)
    except Exception as exc:
        result = f"模型切换失败：{exc}"
    prepared["state"].pop(prepared["picker_id"], None)
    return (
        _hfc_command_result_card(
            title="模型已更新",
            content=str(result or f"已选择 {provider_slug}/{model_id}"),
            template="green",
        ),
        str(item.get("message_id") or ""),
    )


async def _hfc_resolve_native_model_action_async(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    prepared = _hfc_prepare_native_model_action(adapter, data, action_value)
    if prepared is None:
        return None

    item = prepared["item"]
    selected = _parse_model_picker_choice(prepared["choice"])
    if selected is None:
        return (
            _hfc_command_result_card(
                title="模型选择无效",
                content="请重新发送 `/model`。",
                template="red",
            ),
            str(item.get("message_id") or ""),
        )
    provider_slug, model_id = selected
    callback = item.get("on_model_selected")
    try:
        if callback is None:
            result = f"已选择 {provider_slug}/{model_id}"
        else:
            result = await callback(
                prepared["expected_chat_id"] or prepared["chat_id"],
                model_id,
                provider_slug,
            )
    except Exception as exc:
        result = f"模型切换失败：{exc}"
    prepared["state"].pop(prepared["picker_id"], None)
    return (
        _hfc_command_result_card(
            title="模型已更新",
            content=str(result or f"已选择 {provider_slug}/{model_id}"),
            template="green",
        ),
        str(item.get("message_id") or ""),
    )


def _hfc_switch_model_background_task(adapter: Any, data: Any, action_value: dict[str, Any], message_id: str) -> None:
    """Run the real model switch off the callback path.

    The synchronous card-action callback must return within Feishu's callback
    timeout (a few seconds). The actual switch_model call can be much slower
    (provider slow / network jitter), so we resolve the click instantly with a
    "switching" card and perform the switch in the background.

    After the switch finishes, update the original card when Feishu permits it.
    A single result card is sent only when the original message cannot be
    updated.
    """
    async def _run() -> None:
        resolved = await _hfc_resolve_native_model_action_async(adapter, data, action_value)
        if resolved is None:
            _hfc_info("background model_picker ignored: unresolved")
            return
        card, resolved_message_id = resolved
        target_message_id = resolved_message_id or message_id
        if target_message_id and await _hfc_update_native_command_card(
            adapter, target_message_id, card
        ):
            _hfc_info("background model switch: original card updated")
            return
        chat_id = _hfc_action_chat_id(data)
        if not chat_id:
            _hfc_warn("background model switch: cannot determine chat_id for result card")
            return
        if not hasattr(adapter, "_feishu_send_with_retry"):
            _hfc_warn("background model switch: adapter has no _feishu_send_with_retry")
            return
        metadata = _hfc_action_metadata(data)
        try:
            await adapter._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type="interactive",
                payload=json.dumps(card, ensure_ascii=False),
                reply_to=message_id or None,
                metadata=metadata,
            )
            _hfc_info("background model switch: result card sent")
        except Exception as exc:
            _hfc_warn(f"background model switch: direct send failed: {exc.__class__.__name__}: {exc}")
            # Fallback: reuse the well-tested native command result card sender
            # (it passes metadata correctly and handles reply threading).
            try:
                await _hfc_send_native_command_result_card(
                    adapter,
                    chat_id=chat_id,
                    content=str(card.get("elements", [{}])[0].get("content", "") or ""),
                    reply_to=message_id or None,
                    metadata=metadata,
                    context={"command": "model"},
                )
                _hfc_info("background model switch: result card sent (fallback)")
            except Exception as exc2:
                _hfc_warn(f"background model switch: fallback send failed: {exc2.__class__.__name__}: {exc2}")

    loop = getattr(adapter, "_loop", None)
    submit = getattr(adapter, "_submit_on_loop", None)
    if loop is not None and callable(submit):
        coroutine = _run()
        submitted = False
        try:
            submitted = bool(submit(loop, coroutine))
            if not submitted:
                _hfc_warn("background model switch schedule failed")
            return
        except Exception as exc:
            _hfc_warn(f"background model switch schedule failed: {exc.__class__.__name__}: {exc}")
        finally:
            if not submitted:
                coroutine.close()


def _hfc_handle_native_model_action(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> Any:
    _hfc_info("inline card action received: model_picker")
    prepared = _hfc_prepare_native_model_action(adapter, data, action_value)
    if prepared is None:
        _hfc_info("inline model_picker ignored: unresolved")
        return _hfc_empty_feishu_callback_response(adapter)

    message_id = str(prepared["item"].get("message_id") or "")
    # Parse the model choice properly — prepared["choice"] is a raw JSON string
    # like '{"provider":"zai","model":"glm-5.2"}', not a "provider/model" path.
    parsed = _parse_model_picker_choice(prepared["choice"])
    if parsed is not None:
        provider_slug, model_id = parsed
    else:
        provider_slug, model_id = "?", prepared["choice"]

    # FIX-3: acknowledge the click immediately with a "switching" card and run
    # the real (potentially slow) switch off the callback path.
    switching_card = _hfc_command_result_card(
        title="模型切换中",
        content=f"正在切换到 `{provider_slug}/{model_id}` …",
        template="blue",
    )
    _hfc_switch_model_background_task(adapter, data, action_value, message_id)
    return _hfc_raw_feishu_callback_response(adapter, switching_card)


def _hfc_resume_result_card(result: Any) -> dict[str, Any]:
    content = str(result or "会话已恢复。").strip()
    template = _hfc_command_result_template(content)
    title = "会话已恢复" if template == "green" else "会话恢复失败"
    return _hfc_command_result_card(
        title=title,
        content=content,
        template=template,
    )


def _hfc_resume_picker_background_task(
    adapter: Any,
    data: Any,
    prepared: dict[str, Any],
) -> None:
    async def _run() -> None:
        item = prepared["item"]
        runner = item.get("runner")
        original_handler = item.get("original_handler")
        original_event = item.get("event")
        if runner is None or not callable(original_handler) or original_event is None:
            card = _hfc_command_result_card(
                title="会话选择已失效",
                content="请重新发送 `/resume`。",
                template="red",
            )
        else:
            try:
                resume_event = copy.copy(original_event)
                resume_event.text = f"/resume {prepared['choice']}"
                result = await original_handler(runner, resume_event)
                card = _hfc_resume_result_card(result)
            except Exception as exc:
                card = _hfc_command_result_card(
                    title="会话恢复失败",
                    content=f"请重新发送 `/resume`。\n\n{exc.__class__.__name__}: {exc}",
                    template="red",
                )

        message_id = str(item.get("message_id") or "")
        if message_id and await _hfc_update_native_command_card(
            adapter, message_id, card
        ):
            _hfc_info("background resume: original card updated")
            return
        chat_id = prepared["expected_chat_id"] or prepared["chat_id"]
        if not chat_id or not hasattr(adapter, "_feishu_send_with_retry"):
            _hfc_warn("background resume: result card cannot be delivered")
            return
        try:
            await adapter._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type="interactive",
                payload=json.dumps(card, ensure_ascii=False),
                reply_to=message_id or None,
                metadata=_hfc_action_metadata(data),
            )
            _hfc_info("background resume: fallback result card sent")
        except Exception as exc:
            _hfc_warn(
                "background resume: fallback send failed: "
                f"{exc.__class__.__name__}: {exc}"
            )

    loop = prepared["loop"]
    submit = getattr(adapter, "_submit_on_loop", None)
    if not callable(submit):
        _hfc_warn("background resume schedule failed: submit helper unavailable")
        return
    coroutine = _run()
    submitted = False
    try:
        submitted = bool(submit(loop, coroutine))
        if not submitted:
            _hfc_warn("background resume schedule failed")
    except Exception as exc:
        _hfc_warn(
            f"background resume schedule failed: {exc.__class__.__name__}: {exc}"
        )
    finally:
        if not submitted:
            coroutine.close()


def _hfc_handle_native_resume_action(
    adapter: Any,
    data: Any,
    action_value: dict[str, Any],
) -> Any:
    _hfc_info("inline card action received: resume_picker")
    prepared = _hfc_prepare_native_resume_action(adapter, data, action_value)
    if prepared is None:
        return _hfc_raw_feishu_callback_response(
            adapter,
            _hfc_command_result_card(
                title="会话选择已失效",
                content="请由原发起者重新发送 `/resume`。",
                template="red",
            ),
        )
    switching_card = _hfc_command_result_card(
        title="会话恢复中",
        content="正在恢复所选会话…",
        template="blue",
    )
    _hfc_resume_picker_background_task(adapter, data, prepared)
    return _hfc_raw_feishu_callback_response(adapter, switching_card)


async def _hfc_update_native_command_card(adapter: Any, message_id: str, card: dict[str, Any]) -> bool:
    message_id = str(message_id or "").strip()
    if not message_id:
        _hfc_warn("native command card update skipped: missing message_id")
        return False
    client = getattr(adapter, "_client", None)
    if client is None:
        _hfc_warn("native command card update skipped: Feishu client unavailable")
        return False
    try:
        body_builder = getattr(adapter, "_build_update_message_body", None)
        request_builder = getattr(adapter, "_build_update_message_request", None)
        run_blocking = getattr(adapter, "_run_blocking", None)
        if not (callable(body_builder) and callable(request_builder) and callable(run_blocking)):
            _hfc_warn("native command card update skipped: Feishu update helpers unavailable")
            return False
        request_body = body_builder(
            msg_type="interactive",
            content=json.dumps(card, ensure_ascii=False),
        )
        request = request_builder(message_id, request_body)
        update_call = client.im.v1.message.update
        _hfc_info(f"native command card update attempting: message_id={message_id!r}")
        response = await run_blocking(update_call, request)
        success = _hfc_update_response_success(response)
        if not success:
            _hfc_warn(f"native command card update failed: {_hfc_update_response_error(response)}")
        else:
            _hfc_info(f"native command card update succeeded: message_id={message_id!r}")
        return success
    except Exception as exc:
        _hfc_warn(f"native command card update failed: {exc.__class__.__name__}: {exc}")
        return False


def _hfc_is_duplicate_card_action(adapter: Any, data: Any) -> bool:
    event = getattr(data, "event", None)
    token = str(getattr(event, "token", "") or "").strip()
    is_duplicate = getattr(adapter, "_is_card_action_duplicate", None)
    if token and callable(is_duplicate):
        try:
            return bool(is_duplicate(token))
        except Exception:
            return False
    return False


async def _hfc_handle_feishu_card_action_event(self: Any, data: Any) -> None:
    action_value = _hfc_action_value_from_data(data)
    action = str(action_value.get("hfc_action") or "").strip()
    if action:
        _hfc_info(f"background card action received: {action}")
    if action == "slash_confirm":
        if _hfc_is_duplicate_card_action(self, data):
            return
        resolved = await _hfc_resolve_native_slash_action_async(self, data, action_value)
        if resolved is not None:
            card, message_id = resolved
            _hfc_info(
                "background slash_confirm resolved without direct update: "
                f"message_id={message_id!r}"
            )
        else:
            _hfc_info("background slash_confirm ignored: unresolved")
        return
    if action == "model_picker":
        if _hfc_is_duplicate_card_action(self, data):
            return
        resolved = await _hfc_resolve_native_model_action_async(self, data, action_value)
        if resolved is not None:
            card, message_id = resolved
            _hfc_info(
                "background model_picker resolved without direct update: "
                f"message_id={message_id!r}"
            )
        else:
            _hfc_info("background model_picker ignored: unresolved")
        return
    if action == "resume_picker":
        if _hfc_is_duplicate_card_action(self, data):
            return
        prepared = _hfc_prepare_native_resume_action(self, data, action_value)
        if prepared is not None:
            _hfc_resume_picker_background_task(self, data, prepared)
        else:
            _hfc_info("background resume_picker ignored: unresolved")
        return
    if action == "operations.select":
        _hfc_info("background operations.select claimed by HFC")
        return

    original = getattr(type(self), "_hfc_original_handle_card_action_event", None)
    if callable(original):
        await original(self, data)


def _hfc_refresh_feishu_event_handler(adapter: Any) -> bool:
    if getattr(adapter, "_hfc_command_card_event_handler_refreshed", False):
        return False

    current_handler = getattr(adapter, "_event_handler", None)
    ws_client = getattr(adapter, "_ws_client", None)
    ws_handler = getattr(ws_client, "_event_handler", None) if ws_client is not None else None
    if current_handler is None and ws_handler is None:
        return False

    build_event_handler = getattr(adapter, "_build_event_handler", None)
    if not callable(build_event_handler):
        return False

    try:
        rebuilt_handler = build_event_handler()
    except Exception as exc:
        _hfc_warn(f"Feishu event handler refresh failed: {exc.__class__.__name__}: {exc}")
        return False
    if rebuilt_handler is None:
        _hfc_warn("Feishu event handler refresh skipped: builder returned None")
        return False

    try:
        setattr(adapter, "_event_handler", rebuilt_handler)
        if ws_client is not None:
            setattr(ws_client, "_event_handler", rebuilt_handler)
        setattr(adapter, "_hfc_command_card_event_handler_refreshed", True)
        _hfc_info("Feishu event handler refreshed for command card callbacks")
        return True
    except Exception as exc:
        _hfc_warn(f"Feishu event handler refresh failed: {exc.__class__.__name__}: {exc}")
        return False


def _command_card_answer_text(answer: Any) -> str:
    if answer is None:
        return ""
    text = str(answer).strip()
    return text


def request_approval_choice_from_hermes_locals(
    local_vars: dict[str, Any],
    approval_data: dict[str, Any],
    *,
    interaction_id: str,
    timeout_seconds: float | None = None,
) -> str | None:
    command = str(approval_data.get("command") or "").strip()
    description = str(approval_data.get("description") or "dangerous command").strip()
    result = request_interaction_from_hermes_locals(
        local_vars,
        kind="approval",
        interaction_id=interaction_id,
        prompt="需要授权后继续执行",
        description=f"```\n{command[:3000]}\n```\n\n{description}",
        options=[
            {"label": "允许一次", "value": "once", "style": "primary"},
            {"label": "本会话允许", "value": "session"},
            {"label": "始终允许", "value": "always"},
            {"label": "拒绝", "value": "deny", "style": "danger"},
        ],
        timeout_seconds=timeout_seconds,
    )
    if isinstance(result, dict) and result.get("status") == "completed":
        choice = str(result.get("choice") or "").strip()
        return choice or None
    return None


async def complete_command_card_from_hermes_locals_async(
    local_vars: dict[str, Any],
    *,
    answer: Any,
) -> bool:
    try:
        config = load_runtime_config()
        if not config.enabled:
            return False
        payload = build_event(
            "message.completed",
            {
                **local_vars,
                "answer": _command_card_answer_text(answer),
                "delivery_kind": "command",
            },
        )
        post_result = await _post_json_ordered_response(
            config.event_url,
            payload,
            _timeout_for_event(config, payload["event"]),
        )
        return not (isinstance(post_result, dict) and post_result.get("ok") is False)
    except Exception:
        return False


def install_feishu_command_card_adapter_methods(runner: Any, event: Any = None) -> bool:
    try:
        adapters = getattr(runner, "adapters", None)
        if not isinstance(adapters, dict):
            if event is not None:
                _HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(None)
            _HFC_FEISHU_NOTICE_CONTEXT.set(None)
            return False
        runner_type = type(runner)
        _hfc_install_resume_picker_handler(runner_type)
        current_notice_delivery = runner_type.__dict__.get("_deliver_platform_notice")
        if current_notice_delivery is _hfc_deliver_platform_notice_with_card:
            setattr(runner_type, "_hfc_platform_notice_wrapped", True)
        else:
            original_notice_delivery = (
                getattr(runner_type, "_hfc_original_deliver_platform_notice", None)
                or current_notice_delivery
                or getattr(runner_type, "_deliver_platform_notice", None)
            )
            if callable(original_notice_delivery):
                setattr(
                    runner_type,
                    "_hfc_original_deliver_platform_notice",
                    original_notice_delivery,
                )
                setattr(
                    runner_type,
                    "_deliver_platform_notice",
                    _hfc_deliver_platform_notice_with_card,
                )
                setattr(runner_type, "_hfc_platform_notice_wrapped", True)

        command_result_context = (
            _hfc_command_result_context_from_event(event) if event is not None else None
        )
        notice_context = _hfc_notice_context_from_event(event) if event is not None else None
        installed = False
        for key, adapter in list(adapters.items()):
            if not _is_feishu_adapter_key(key, adapter):
                continue
            adapter_type = type(adapter)
            adapter_ready = False
            existing_slash_confirm = adapter_type.__dict__.get("send_slash_confirm")
            if (
                existing_slash_confirm is None
                or getattr(existing_slash_confirm, "__module__", "") == __name__
            ):
                setattr(adapter_type, "send_slash_confirm", _hfc_send_native_slash_confirm)
                adapter_ready = True
            elif callable(existing_slash_confirm):
                adapter_ready = True

            existing_model_picker = adapter_type.__dict__.get("send_model_picker")
            if (
                existing_model_picker is None
                or existing_model_picker is _hfc_send_model_picker
                or getattr(existing_model_picker, "__module__", "") == __name__
            ):
                setattr(adapter_type, "send_model_picker", _hfc_send_native_model_picker)
                adapter_ready = True
            elif callable(existing_model_picker):
                adapter_ready = True

            existing_resume_picker = adapter_type.__dict__.get("send_resume_picker")
            if (
                existing_resume_picker is None
                or getattr(existing_resume_picker, "__module__", "") == __name__
            ):
                setattr(
                    adapter_type,
                    "send_resume_picker",
                    _hfc_send_native_resume_picker,
                )
                adapter_ready = True
            elif callable(existing_resume_picker):
                adapter_ready = True

            current_action_handler = adapter_type.__dict__.get("_on_card_action_trigger")
            if current_action_handler is _hfc_on_feishu_card_action_trigger:
                setattr(adapter_type, "_hfc_command_card_action_wrapped", True)
                adapter_ready = True
            elif not getattr(adapter_type, "_hfc_command_card_action_wrapped", False):
                original = current_action_handler or getattr(adapter_type, "_on_card_action_trigger", None)
                if callable(original):
                    setattr(adapter_type, "_hfc_original_on_card_action_trigger", original)
                    setattr(adapter_type, "_on_card_action_trigger", _hfc_on_feishu_card_action_trigger)
                    setattr(adapter_type, "_hfc_command_card_action_wrapped", True)
                    adapter_ready = True
            elif callable(getattr(adapter_type, "_on_card_action_trigger", None)):
                adapter_ready = True

            current_event_handler = adapter_type.__dict__.get("_handle_card_action_event")
            if current_event_handler is _hfc_handle_feishu_card_action_event:
                setattr(adapter_type, "_hfc_command_card_event_wrapped", True)
                adapter_ready = True
            elif not getattr(adapter_type, "_hfc_command_card_event_wrapped", False):
                original_event_handler = current_event_handler or getattr(
                    adapter_type, "_handle_card_action_event", None
                )
                if callable(original_event_handler):
                    setattr(
                        adapter_type,
                        "_hfc_original_handle_card_action_event",
                        original_event_handler,
                    )
                    setattr(
                        adapter_type,
                        "_handle_card_action_event",
                        _hfc_handle_feishu_card_action_event,
                    )
                    setattr(adapter_type, "_hfc_command_card_event_wrapped", True)
                    adapter_ready = True
            elif callable(getattr(adapter_type, "_handle_card_action_event", None)):
                adapter_ready = True

            current_send = adapter_type.__dict__.get("send")
            if current_send is _hfc_send_with_native_command_result_card:
                setattr(adapter_type, "_hfc_command_result_send_wrapped", True)
                adapter_ready = True
            elif not getattr(adapter_type, "_hfc_command_result_send_wrapped", False):
                original_send = current_send or getattr(adapter_type, "send", None)
                if callable(original_send):
                    setattr(adapter_type, "_hfc_original_send", original_send)
                    setattr(adapter_type, "send", _hfc_send_with_native_command_result_card)
                    setattr(adapter_type, "_hfc_command_result_send_wrapped", True)
                    adapter_ready = True
            elif callable(getattr(adapter_type, "send", None)):
                adapter_ready = True

            current_edit_message = adapter_type.__dict__.get("edit_message")
            if current_edit_message is _hfc_edit_message_with_system_notice_card:
                setattr(adapter_type, "_hfc_system_notice_edit_wrapped", True)
                adapter_ready = True
            elif not getattr(adapter_type, "_hfc_system_notice_edit_wrapped", False):
                original_edit_message = current_edit_message or getattr(
                    adapter_type, "edit_message", None
                )
                if callable(original_edit_message):
                    setattr(
                        adapter_type,
                        "_hfc_original_edit_message",
                        original_edit_message,
                    )
                    setattr(
                        adapter_type,
                        "edit_message",
                        _hfc_edit_message_with_system_notice_card,
                    )
                    setattr(adapter_type, "_hfc_system_notice_edit_wrapped", True)
                    adapter_ready = True
            elif callable(getattr(adapter_type, "edit_message", None)):
                adapter_ready = True

            if adapter_ready:
                _hfc_refresh_feishu_event_handler(adapter)
                setattr(adapter_type, "_hfc_command_card_methods_installed", True)
                installed = True
        if event is not None:
            _HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(
                command_result_context if installed else None
            )
            _HFC_FEISHU_NOTICE_CONTEXT.set(notice_context if installed else None)
        return installed
    except Exception:
        if event is not None:
            try:
                _HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(None)
                _HFC_FEISHU_NOTICE_CONTEXT.set(None)
            except Exception:
                pass
        return False


def request_clarify_response_from_hermes_locals(
    local_vars: dict[str, Any],
    *,
    interaction_id: str,
    question: str,
    choices: Any,
    timeout_seconds: float | None = None,
) -> str | None:
    if not choices:
        return None
    options = []
    for index, choice in enumerate(list(choices)):
        label = str(choice).strip()
        if label:
            options.append(
                {
                    "label": label,
                    "value": label,
                    "style": "primary" if index == 0 else "default",
                }
            )
    if not options:
        return None
    result = request_interaction_from_hermes_locals(
        local_vars,
        kind="clarify",
        interaction_id=interaction_id,
        prompt=str(question or "请选择").strip(),
        options=options,
        timeout_seconds=timeout_seconds,
    )
    if isinstance(result, dict) and result.get("status") == "completed":
        choice = str(result.get("choice") or "").strip()
        return choice or None
    return None


def should_suppress_native_response(
    platform: str,
    delivered: bool,
    attachments: Any = None,
    native_delivery: Any = None,
) -> bool:
    if not delivered:
        return False
    if str(platform or "").lower() != "feishu":
        return False
    if str(native_delivery or "").strip().lower() == "required":
        return False
    if native_delivery is None and attachments:
        return False
    return True


def _post_json_sync(url: str, payload: dict[str, Any], timeout: float) -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(_post_json(url, payload, timeout))
        except Exception:
            return False
        return True

    result: dict[str, BaseException | None] = {"error": None}

    def run_in_thread() -> None:
        try:
            asyncio.run(_post_json(url, payload, timeout))
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()
    return result["error"] is None


def _post_interaction_event(
    local_vars: dict[str, Any],
    url: str,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any] | None | object:
    loop = local_vars.get("_hfc_loop")
    if loop is not None:
        try:
            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    _post_json_ordered_response(url, payload, timeout),
                    loop,
                )
                return future.result(timeout=max(1.0, timeout + 1.0))
        except Exception:
            return _POST_FAILED
    try:
        return _post_json_sync_response(url, payload, timeout)
    except Exception:
        return _POST_FAILED


def _timeout_for_event(config: RuntimeConfig, event_name: str) -> float:
    if event_name in {"message.completed", "message.failed"}:
        return max(config.timeout_seconds, TERMINAL_TIMEOUT_SECONDS)
    return config.timeout_seconds


async def _send_fail_open(
    url: str, payload: dict[str, Any], timeout: float
) -> None:
    try:
        await _post_json(url, payload, timeout)
    except Exception:
        return


async def _send_fail_open_ordered(
    url: str, payload: dict[str, Any], timeout: float
) -> None:
    try:
        await _post_json_ordered(url, payload, timeout)
    except Exception:
        return


async def _post_json_ordered(
    url: str, payload: dict[str, Any], timeout: float
) -> None:
    lock = _send_lock(url, payload)
    if lock is None:
        await _post_json(url, payload, timeout)
        return
    async with lock:
        await _post_json(url, payload, timeout)


async def _post_json_ordered_response(
    url: str, payload: dict[str, Any], timeout: float
) -> Any:
    lock = _send_lock(url, payload)
    if lock is None:
        return await _post_json_response(url, payload, timeout)
    async with lock:
        return await _post_json_response(url, payload, timeout)


def _send_lock(url: str, payload: dict[str, Any]) -> asyncio.Lock | None:
    message_id = payload.get("message_id")
    if not isinstance(message_id, str) or not message_id:
        return None
    loop = asyncio.get_running_loop()
    key = (id(loop), url, message_id)
    with _SEND_LOCKS_GUARD:
        lock = _SEND_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SEND_LOCKS[key] = lock
        return lock


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


async def _post_json_response(url: str, payload: dict[str, Any], timeout: float) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _open_json_request, req, timeout)


def _post_json_sync_response(url: str, payload: dict[str, Any], timeout: float) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _open_json_request(req, timeout)


async def lookup_card_summary(
    message_id: str,
    event_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    try:
        message_id = str(message_id or "").strip()
        if not message_id:
            return None
        base_url = _summary_base_url(event_url or load_runtime_config().event_url)
        quoted_message_id = parse.quote(message_id, safe="")
        url = f"{base_url}/messages/{quoted_message_id}/summary"
        result = await _get_json(url, timeout)
        if not isinstance(result, dict) or result.get("ok") is False:
            return None
        summary = result.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return None
        return summary
    except Exception:
        return None


def _summary_base_url(event_url: str) -> str:
    parsed = parse.urlsplit(event_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/events"):
        path = path[: -len("/events")]
    rebuilt = parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return rebuilt.rstrip("/")


async def _get_json(url: str, timeout: float) -> Any:
    req = request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _open_json_request, req, timeout)


_NO_PROXY_OPENER = request.build_opener(request.ProxyHandler({}))


def _open_request(req: request.Request, timeout: float) -> None:
    with _open_sidecar_request(req, timeout) as response:
        response.read()


def _open_json_request(req: request.Request, timeout: float) -> Any:
    with _open_sidecar_request(req, timeout) as response:
        body = response.read()
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _open_sidecar_request(req: request.Request, timeout: float):
    if _should_bypass_proxy(req.full_url):
        return _NO_PROXY_OPENER.open(req, timeout=timeout)
    return request.urlopen(req, timeout=timeout)


def _should_bypass_proxy(url: str) -> bool:
    host = parse.urlsplit(url).hostname or ""
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
    )


def _get_json_sync(url: str, timeout: float) -> Any:
    req = request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    return _open_json_request(req, timeout)


def build_event(
    event_name: str, local_vars: dict[str, Any], preview: bool = False
) -> dict[str, Any] | None:
    return _build_event(event_name, local_vars, preview=preview)


def _build_event(
    event_name: str, local_vars: dict[str, Any], *, preview: bool
) -> dict[str, Any] | None:
    if event_name not in SUPPORTED_RUNTIME_EVENTS:
        return None
    source_obj = local_vars.get("source")
    platform = _platform_name(local_vars, source_obj)
    if platform != "feishu":
        return None
    gateway_event_obj = local_vars.get("event")
    chat_id = _first_string(local_vars, ("chat_id", "open_chat_id", "receive_id"))
    message_obj = local_vars.get("message")
    if chat_id is None:
        chat_id = _first_attr_string(message_obj, ("chat_id", "open_chat_id", "receive_id"))
    if chat_id is None:
        chat_id = _first_attr_string(source_obj, ("chat_id", "open_chat_id", "receive_id"))
    if chat_id is None:
        return None
    thread_id = _thread_id_for_runtime_event(local_vars, message_obj, source_obj)

    conversation_id = (
        _first_string(local_vars, ("conversation_id", "thread_id", "session_id"))
        or _first_attr_string(message_obj, ("conversation_id", "thread_id", "session_id"))
        or _first_attr_string(source_obj, ("conversation_id", "thread_id", "session_id"))
        or chat_id
    )
    created_at_value = local_vars.get("created_at")
    created_at = _created_at(created_at_value)
    created_at_lifecycle_token = _created_at_lifecycle_token(created_at_value)
    fallback_key = (conversation_id, chat_id)
    explicit_message_id = _first_string(
        local_vars, ("message_id", "msg_id", "event_message_id")
    ) or _first_attr_string(
        message_obj, ("message_id", "msg_id")
    ) or _first_attr_string(
        gateway_event_obj, ("message_id", "msg_id")
    )
    message_id = explicit_message_id
    is_terminal_event = event_name in {"message.completed", "message.failed"}
    active_fallback_cache_key = None
    if event_name == "message.started" and explicit_message_id is not None:
        active_fallback_cache_key = _active_fallback_cache_key(
            fallback_key, created_at_lifecycle_token
        )
    elif event_name != "message.started":
        if is_terminal_event:
            active_fallback_cache_key = _terminal_fallback_cache_key(
                fallback_key, created_at_lifecycle_token
            )
        else:
            active_fallback_cache_key = _active_fallback_cache_key(
                fallback_key, created_at_lifecycle_token
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
            preview=preview,
        )
        if message_id is None:
            return None
    sequence = _peek_next_sequence(message_id) if preview else _next_sequence(message_id)
    event_data = _event_data(event_name, local_vars, source_obj, message_obj)
    if thread_id:
        event_data.setdefault("thread_id", thread_id)
    payload = {
        "schema_version": "1",
        "event": event_name,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "platform": platform,
        "sequence": sequence,
        "created_at": created_at,
        "data": event_data,
    }
    if is_terminal_event:
        if not preview:
            if (
                explicit_message_id is not None
                and created_at_lifecycle_token is None
                and active_fallback_cache_key is None
            ):
                _retire_current_fallback_key(fallback_key)
            if active_fallback_cache_key is not None:
                _ACTIVE_FALLBACK_MESSAGE_IDS.pop(active_fallback_cache_key, None)
                if _CURRENT_FALLBACK_KEYS.get(fallback_key) == active_fallback_cache_key:
                    _CURRENT_FALLBACK_KEYS.pop(fallback_key, None)
    return payload


def build_cron_event(local_vars: dict[str, Any]) -> dict[str, Any] | None:
    job = local_vars.get("job")
    content = _first_string(
        local_vars,
        ("cleaned_delivery_content", "delivery_content", "content"),
    )
    if not isinstance(job, dict) or content is None:
        return None

    origin = job.get("origin")
    if not isinstance(origin, dict):
        origin = {}
    resolved_targets = _resolved_cron_targets(local_vars, job)
    resolved_chat_id = _resolved_target_chat_id(resolved_targets, "feishu")
    # Routing-intent tokens ("origin", "all") and comma-separated
    # combinations are not real platform names.  When _deliver_platform()
    # returns one of these, the platform chain short-circuits and never
    # reaches origin.get("platform") — causing every deliver=origin or
    # deliver=all cron job to silently fall back to plain-text delivery.
    # Extract the first real platform name, skipping routing intents.
    # "local" is NOT a routing intent — it means "no delivery" and will
    # cause the platform check below to fail naturally.
    deliver_platform = _extract_real_platform(job.get("deliver"))
    platform = str(
        deliver_platform
        or _first_target_platform(resolved_targets)
        or origin.get("platform")
        or os.environ.get("HERMES_CRON_AUTO_DELIVER_PLATFORM")
        or "feishu"
    ).strip().lower()
    origin_platform = str(origin.get("platform") or "").strip().lower()
    origin_chat_id = origin.get("chat_id") if origin_platform == "feishu" else ""
    origin_thread_id = origin.get("thread_id") if origin_platform == "feishu" else ""
    chat_id = str(
        resolved_chat_id
        or _deliver_chat_id(job.get("deliver"))
        or origin_chat_id
        or os.environ.get("HERMES_CRON_AUTO_DELIVER_CHAT_ID")
        or ""
    ).strip()
    if platform != "feishu" or not chat_id:
        return None

    # Resolve thread_id for topic-group delivery (cron jobs targeting a thread
    # inside a topic group).  Priority: resolved targets > origin > env var.
    thread_id = str(
        _resolved_target_thread_id(resolved_targets, "feishu")
        or origin_thread_id
        or os.environ.get("HERMES_CRON_AUTO_DELIVER_THREAD_ID", "")
    ).strip() or ""

    profile_id, profile_source = _profile_identity(local_vars, None, None)
    created_at = time.time()
    job_id = str(job.get("id") or "").strip()
    message_id = "cron_" + sha256(f"{job_id}:{created_at}".encode("utf-8")).hexdigest()[
        :16
    ]
    return {
        "schema_version": "1",
        "event": "message.completed",
        "conversation_id": thread_id or str(job.get("id") or chat_id),
        "message_id": message_id,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "platform": "feishu",
        "sequence": 0,
        "created_at": created_at,
        "data": {
            "answer": content,
            "delivery_kind": "cron",
            "profile_id": profile_id,
            "profile_source": profile_source,
            "attachments": _extract_attachments(content, local_vars),
        },
    }


def _interaction_timeout(value: float | None) -> float:
    if value is not None and math.isfinite(value) and value >= 0:
        return value
    env_value = _finite_float(os.environ.get("HERMES_FEISHU_CARD_INTERACTION_TIMEOUT_SECONDS"))
    if env_value is not None and env_value >= 0:
        return env_value
    return 300.0


def _interaction_poll_interval(value: float | None) -> float:
    if value is not None and math.isfinite(value) and value >= 0:
        return value
    env_value = _finite_float(os.environ.get("HERMES_FEISHU_CARD_INTERACTION_POLL_SECONDS"))
    if env_value is not None and 0 <= env_value <= 5:
        return env_value
    return 0.5


def _coerce_interaction_options(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    options: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("text") or item.get("value") or "").strip()
        option_value = str(item.get("value") or label).strip()
        if not label or not option_value:
            continue
        style = str(item.get("style") or item.get("type") or "default").strip() or "default"
        options.append({"label": label, "value": option_value, "style": style})
    return options


def _resolved_cron_targets(
    local_vars: dict[str, Any], job: dict[str, Any]
) -> list[dict[str, Any]]:
    value = local_vars.get("_hfc_resolved_targets")
    if value is None:
        value = job.get("_hfc_resolved_targets")
    if value is None:
        value = job.get("resolved_targets")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


_ROUTING_INTENT_TOKENS = frozenset({"origin", "all"})


def _is_routing_intent(platform: str) -> bool:
    """Return True if *platform* is a routing-intent token, not a real platform.

    Routing intents (``origin``, ``all``) and comma-separated combinations
    like ``origin,all`` must be resolved by the scheduler before they map to
    a concrete platform name.  The hook runs before that resolution, so it
    should skip them and let the platform chain fall through to
    ``resolved_targets`` or ``origin``.

    ``local`` is intentionally excluded — it is a delivery target (meaning
    "no delivery"), not a routing intent that needs resolution.
    """
    if not platform:
        return False
    # Single token: "origin", "all"
    if platform in _ROUTING_INTENT_TOKENS:
        return True
    # Comma-separated: "origin,all", "all,telegram:123", etc.
    # If every part (after stripping) is a routing-intent token, treat the
    # whole thing as a routing intent.  Mixed combos like "origin,feishu:123"
    # contain a real platform and should NOT be skipped — the caller should
    # extract the real platform part.
    if "," in platform:
        parts = [p.strip() for p in platform.split(",") if p.strip()]
        if parts and all(p in _ROUTING_INTENT_TOKENS for p in parts):
            return True
    return False


def _extract_real_platform(deliver: Any) -> str:
    """Extract the first real platform name from a deliver value.

    Handles comma-separated deliver strings (``"origin,feishu:chat_id"``)
    by splitting on ``,`` and returning the first part whose platform is not
    a routing intent.  Returns ``""`` when all parts are routing intents or
    the value is empty.  ``"local"`` passes through as-is (it is not a
    routing intent and will cause the platform check in ``build_cron_event``
    to fail naturally).
    """
    if not deliver:
        return ""
    if isinstance(deliver, dict):
        platform = _deliver_platform(deliver)
        if platform in _ROUTING_INTENT_TOKENS:
            return ""
        return platform
    text = str(deliver).strip().lower()
    if not text:
        return ""
    # Fast path: no comma — just use _deliver_platform directly.
    if "," not in text:
        platform = _deliver_platform(text)
        if platform in _ROUTING_INTENT_TOKENS:
            return ""
        return platform
    # Split by comma and find the first real platform part.
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        platform = _deliver_platform(part)
        if platform and platform not in _ROUTING_INTENT_TOKENS:
            return platform
    return ""


def _deliver_platform(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("platform") or value.get("type") or "").strip().lower()
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if ":" in text:
        return text.split(":", 1)[0].strip()
    return text


def _deliver_chat_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(
            value.get("chat_id")
            or value.get("open_chat_id")
            or value.get("receive_id")
            or value.get("target")
            or ""
        ).strip()
    text = str(value or "").strip()
    if ":" not in text:
        return ""
    return text.split(":", 1)[1].strip()


def _first_target_platform(targets: list[dict[str, Any]]) -> str:
    for target in targets:
        platform = str(target.get("platform") or target.get("type") or "").strip().lower()
        if platform:
            return platform
    return ""


def _resolved_target_chat_id(targets: list[dict[str, Any]], platform: str) -> str:
    for target in targets:
        target_platform = str(target.get("platform") or target.get("type") or "").strip().lower()
        if target_platform != platform:
            continue
        chat_id = str(
            target.get("chat_id")
            or target.get("open_chat_id")
            or target.get("receive_id")
            or ""
        ).strip()
        if chat_id:
            return chat_id
    return ""


def _resolved_target_thread_id(targets: list[dict[str, Any]], platform: str) -> str:
    for target in targets:
        target_platform = str(target.get("platform") or target.get("type") or "").strip().lower()
        if target_platform != platform:
            continue
        thread_id = str(target.get("thread_id") or "").strip()
        if thread_id:
            return thread_id
    return ""


def _event_data(
    event_name: str, local_vars: dict[str, Any], source_obj: Any, message_obj: Any
) -> dict[str, Any]:
    profile_id, profile_source = _profile_identity(local_vars, source_obj, message_obj)
    data: dict[str, Any] = {
        "profile_id": profile_id,
        "profile_source": profile_source,
    }
    display_status = normalize_display_status(local_vars.get("display_status"))
    if display_status:
        data["display_status"] = display_status
    reply_to_message_id = _reply_to_message_id_from_runtime(
        local_vars,
        message_obj,
        local_vars.get("event"),
    )
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id
    if event_name in {"thinking.delta", "answer.delta"}:
        text = _first_raw_string(local_vars, ("text", "delta", "delta_text", "content"))
        if text is None:
            text = _first_attr_raw_string(message_obj, ("text", "content"))
        data["text"] = text or ""
        mode = _first_string(local_vars, ("mode", "_hfc_text_mode"))
        if mode:
            data["mode"] = mode
        return data
    if event_name == "system.notice":
        content = _first_raw_string(local_vars, ("content", "text", "message"))
        if content is None:
            content = _first_attr_raw_string(message_obj, ("text", "content"))
        title = _first_string(local_vars, ("_hfc_notice_title", "title")) or "运行提示"
        level = _first_string(local_vars, ("_hfc_notice_level", "level")) or "info"
        notice_kind = _first_string(local_vars, ("_hfc_notice_kind", "notice_kind")) or "system"
        notice_id = _first_string(local_vars, ("_hfc_notice_id", "notice_id")) or ""
        notice_scope = (
            _first_string(local_vars, ("_hfc_notice_scope", "notice_scope"))
            or "session"
        )
        data.update(
            {
                "title": title,
                "content": content or "",
                "level": level,
                "notice_kind": notice_kind,
                "notice_id": notice_id,
                "notice_scope": notice_scope,
            }
        )
        delivery_kind = _first_string(local_vars, ("delivery_kind",))
        if delivery_kind:
            data["delivery_kind"] = delivery_kind
        reply_id = (
            _first_string(
                local_vars,
                ("reply_to_message_id", "quote_message_id", "parent_message_id"),
            )
            or _first_attr_string(
                message_obj,
                ("reply_to_message_id", "quote_message_id", "parent_message_id"),
            )
        )
        if reply_id:
            data["reply_to_message_id"] = reply_id
        return data
    if event_name.startswith("interaction."):
        data.update(
            {
                "interaction_id": (
                    _first_string(local_vars, ("_hfc_interaction_id", "interaction_id"))
                    or ""
                ),
                "kind": (
                    _first_string(local_vars, ("_hfc_interaction_kind", "kind"))
                    or "choice"
                ),
                "prompt": (
                    _first_string(
                        local_vars,
                        ("_hfc_interaction_prompt", "prompt", "question"),
                    )
                    or ""
                ),
                "description": (
                    _first_string(
                        local_vars,
                        ("_hfc_interaction_description", "description"),
                    )
                    or ""
                ),
                "options": _coerce_interaction_options(
                    local_vars.get("_hfc_interaction_options", local_vars.get("options"))
                ),
            }
        )
        timeout_value = _finite_float(local_vars.get("_hfc_interaction_timeout_seconds"))
        if timeout_value is not None:
            data["timeout_seconds"] = timeout_value
        fallback_policy = _first_string(
            local_vars,
            ("_hfc_interaction_fallback_policy", "fallback_policy"),
        )
        if fallback_policy:
            data["fallback_policy"] = fallback_policy
        return data
    if event_name == "tool.updated":
        tool_id = _first_string(local_vars, ("tool_id", "tool_call_id", "name")) or "tool"
        name = _first_string(local_vars, ("name", "tool_name")) or tool_id
        status = _first_string(local_vars, ("status", "tool_status")) or "running"
        detail = _first_string(local_vars, ("detail", "tool_detail")) or ""
        data.update({"tool_id": tool_id, "name": name, "status": status, "detail": detail})
        arguments = _tool_arguments(local_vars)
        if arguments is not None:
            data["arguments"] = arguments
        duration_ms = _tool_duration_milliseconds(local_vars)
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        error = _tool_error(local_vars)
        if error:
            data["error"] = error
        return data
    if event_name == "message.completed":
        answer = _completion_answer(local_vars)
        attachments = _extract_attachments(answer, local_vars)
        data.update({
            "answer": answer,
            "attachments": attachments,
            "native_delivery": _native_delivery_policy(answer, local_vars),
            "duration": _completion_duration(local_vars),
            "model": _completion_model(local_vars),
            "tokens": _completion_tokens(local_vars, answer),
            "context": _completion_context(local_vars),
        })
        delivery_kind = _first_string(local_vars, ("delivery_kind",))
        if delivery_kind:
            data["delivery_kind"] = delivery_kind
        return data
    if event_name == "message.failed":
        error = _first_string(local_vars, ("error", "exception")) or "消息处理失败"
        data["error"] = error
        return data
    if event_name == "message.started":
        for source_key, data_key in (
            ("chat_type", "chat_type"),
            ("tenant_key", "tenant_key"),
            ("agent_id", "agent_id"),
        ):
            value = _first_string(local_vars, (source_key,)) or _first_attr_string(message_obj, (source_key,))
            if value:
                data[data_key] = value
        reply_aliases = (
            "reply_to_message_id",
            "quote_message_id",
            "parent_message_id",
        )
        canonical_reply_id = (
            _first_string(local_vars, reply_aliases)
            or _first_attr_string(message_obj, reply_aliases)
            or _first_attr_string(local_vars.get("event"), reply_aliases)
        )
        if canonical_reply_id:
            data["reply_to_message_id"] = canonical_reply_id
        for reply_key in reply_aliases:
            if reply_key == "reply_to_message_id":
                continue
            value = _first_string(local_vars, (reply_key,))
            if value is None:
                value = _first_attr_string(message_obj, (reply_key,))
            if value is None:
                value = _first_attr_string(local_vars.get("event"), (reply_key,))
            if value:
                data[reply_key] = value
        return data
    return {}


def _profile_identity(local_vars: dict[str, Any], source_obj: Any, message_obj: Any) -> tuple[str, str]:
    env_profile = os.environ.get("HERMES_FEISHU_CARD_PROFILE_ID", "").strip()
    if env_profile:
        return _safe_profile_identity(env_profile, "env")
    direct = (
        _first_string(local_vars, ("profile_id", "hermes_profile", "profile"))
        or _first_attr_string(source_obj, ("profile_id", "hermes_profile", "profile"))
        or _first_attr_string(message_obj, ("profile_id", "hermes_profile", "profile"))
    )
    if direct:
        return _safe_profile_identity(direct, "locals")
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    profile = _profile_from_path(hermes_home)
    if profile:
        return _safe_profile_identity(profile, "hermes_home")
    return "default", "fallback_default"


def _tool_arguments(local_vars: dict[str, Any]) -> Any:
    for name in ("arguments", "parameters", "args", "tool_args", "tool_input", "input"):
        if name not in local_vars:
            continue
        value = local_vars.get(name)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return _json_safe_tool_value(value)
    return None


def _tool_duration_milliseconds(local_vars: dict[str, Any]) -> int | float | None:
    for name in ("duration_ms", "elapsed_ms", "tool_duration_ms"):
        value = _finite_float(local_vars.get(name))
        if value is not None and value >= 0:
            return int(value) if value.is_integer() else value
    for name in ("duration", "elapsed", "tool_duration"):
        value = _finite_float(local_vars.get(name))
        if value is not None and value >= 0:
            milliseconds = value * 1000
            return int(milliseconds) if milliseconds.is_integer() else milliseconds
    return None


def _tool_error(local_vars: dict[str, Any]) -> str:
    for name in ("error", "exception", "tool_error", "error_message", "failure_reason"):
        value = local_vars.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _json_safe_tool_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe_tool_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_tool_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_profile_identity(value: str, source: str) -> tuple[str, str]:
    profile_id = _safe_profile_id(value)
    if profile_id == "default" and value.strip() != "default":
        return profile_id, f"sanitized_{source}"
    return profile_id, source


def _safe_profile_id(value: str) -> str:
    candidate = value.strip()
    if PROFILE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return "default"


def _profile_from_path(path: str) -> str | None:
    if not path:
        return None
    normalized = str(Path(path).expanduser()).replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part)
    for index in range(len(parts) - 2):
        if parts[index] in {".hermes", "hermes"} and parts[index + 1] == "profiles":
            if index + 3 != len(parts):
                return None
            candidate = parts[index + 2].strip()
            if candidate:
                return candidate
    return None


def _thread_id_for_runtime_event(
    local_vars: dict[str, Any], message_obj: Any, source_obj: Any
) -> str:
    value = (
        _first_string(local_vars, ("thread_id",))
        or _first_attr_string(message_obj, ("thread_id",))
        or _first_attr_string(source_obj, ("thread_id",))
    )
    if _is_feishu_thread_id(value):
        return value or ""
    return ""


def _is_feishu_thread_id(value: str | None) -> bool:
    return bool(value and value.startswith(("omt_", "om_")))


def _first_string(source: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = source.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_raw_string(source: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = source.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_attachments(
    text: str, local_vars: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    seen = set()
    attachments = []
    for candidate in _structured_attachment_candidates(local_vars or {}):
        attachment = _coerce_attachment(candidate)
        if attachment is None:
            continue
        name = attachment["name"]
        if name in seen:
            continue
        seen.add(name)
        attachments.append(attachment)
    for raw in list(MEDIA_RE.findall(text or "")) + list(LOCAL_FILE_RE.findall(text or "")):
        name = _attachment_name(raw)
        if not name or name in seen:
            continue
        seen.add(name)
        attachments.append({"kind": _attachment_kind(name), "name": name, "summary": name})
    return attachments


def _native_delivery_policy(
    text: str, local_vars: dict[str, Any] | None = None
) -> str:
    if MEDIA_RE.search(text or "") or LOCAL_FILE_RE.search(text or ""):
        return "required"
    for candidate in _structured_native_delivery_candidates(local_vars or {}):
        if _coerce_attachment(candidate) is not None:
            return "required"
    return "allowed"


def _structured_attachment_candidates(local_vars: dict[str, Any]) -> list[Any]:
    return _structured_candidates(
        local_vars,
        (
            "attachments",
            "attachment",
            *NATIVE_DELIVERY_ATTACHMENT_FIELDS,
        ),
    )


def _structured_native_delivery_candidates(local_vars: dict[str, Any]) -> list[Any]:
    return _structured_candidates(local_vars, NATIVE_DELIVERY_OUTPUT_ATTACHMENT_FIELDS)


def _structured_candidates(
    local_vars: dict[str, Any], names: tuple[str, ...]
) -> list[Any]:
    candidates: list[Any] = []
    for name in names:
        value = local_vars.get(name)
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            candidates.extend(value)
        else:
            candidates.append(value)
    return candidates


def _coerce_attachment(value: Any) -> dict[str, str] | None:
    if isinstance(value, str):
        name = _attachment_name(value)
        if not name:
            return None
        return {"kind": _attachment_kind(name), "name": name, "summary": name}
    if isinstance(value, dict):
        raw_name = _first_attachment_mapping_value(
            value,
            ("name", "filename", "file_name", "path", "file_path", "url", "display_name", "summary"),
        )
        summary = _first_attachment_mapping_value(
            value,
            ("summary", "display_name", "title", "name", "filename", "file_name"),
        )
        kind_hint = _first_attachment_mapping_value(
            value,
            ("kind", "type", "media_type", "mime_type", "mime"),
        )
    else:
        raw_name = _first_attachment_attr_value(
            value,
            ("name", "filename", "file_name", "path", "file_path", "url", "display_name", "summary"),
        )
        summary = _first_attachment_attr_value(
            value,
            ("summary", "display_name", "title", "name", "filename", "file_name"),
        )
        kind_hint = _first_attachment_attr_value(
            value,
            ("kind", "type", "media_type", "mime_type", "mime"),
        )
    name = _attachment_name(raw_name or "")
    if not name:
        return None
    clean_summary = str(summary or name).strip() or name
    return {
        "kind": _attachment_kind_from_hint(name, kind_hint),
        "name": name,
        "summary": clean_summary,
    }


def _first_attachment_mapping_value(
    value: dict[str, Any], names: tuple[str, ...]
) -> str:
    for name in names:
        item = value.get(name)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _first_attachment_attr_value(value: Any, names: tuple[str, ...]) -> str:
    for name in names:
        item = getattr(value, name, None)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _attachment_name(raw: str) -> str:
    return Path(raw.strip().rstrip(ATTACHMENT_TRAILING_PUNCTUATION)).name.strip()


def _attachment_kind_from_hint(name: str, hint: str) -> str:
    normalized = str(hint or "").strip().lower()
    if normalized.startswith("image/") or normalized in {"image", "img", "photo"}:
        return "image"
    if normalized.startswith("audio/") or normalized in {"audio", "voice"}:
        return "audio"
    if normalized.startswith("video/") or normalized == "video":
        return "video"
    if normalized in {"file", "document", "doc"}:
        return "file"
    return _attachment_kind(name)


def _attachment_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "image"
    if ext in {".mp3", ".wav", ".ogg"}:
        return "audio"
    if ext in {".mp4", ".mov", ".webm"}:
        return "video"
    if ext:
        return "file"
    return "unknown"


def _completion_duration(local_vars: dict[str, Any]) -> float:
    for name in ("duration", "duration_seconds", "response_time", "_response_time"):
        value = _finite_float(local_vars.get(name))
        if value is not None and value >= 0:
            return value
    return 0.0


def _completion_answer(local_vars: dict[str, Any]) -> str:
    direct = _first_string(
        local_vars,
        ("answer", "response", "final_answer", "final_response", "text", "content"),
    )
    if direct is not None:
        return direct

    agent_result = local_vars.get("agent_result")
    answer = _first_attr_string(
        agent_result,
        ("answer", "response", "final_answer", "final_response", "text", "content"),
    )
    if answer is not None:
        return answer
    if isinstance(agent_result, dict):
        for key in ("message", "assistant_message", "result", "output"):
            nested = agent_result.get(key)
            answer = _first_attr_string(
                nested,
                (
                    "answer",
                    "response",
                    "final_answer",
                    "final_response",
                    "text",
                    "content",
                ),
            )
            if answer is not None:
                return answer
    return ""


def _completion_model(local_vars: dict[str, Any]) -> str:
    model = _first_string(local_vars, ("model", "current_model", "resolved_model"))
    if model is not None:
        return model
    agent_result = local_vars.get("agent_result")
    if isinstance(agent_result, dict):
        result_model = _first_string(agent_result, ("model", "current_model", "resolved_model"))
        if result_model is not None:
            return result_model
    return "Unknown"


def _completion_tokens(local_vars: dict[str, Any], answer: str) -> dict[str, int]:
    explicit_tokens = local_vars.get("tokens")
    agent_result = local_vars.get("agent_result")
    if not isinstance(agent_result, dict):
        agent_result = {}

    input_tokens = _token_value(explicit_tokens, "input_tokens")
    output_tokens = _token_value(explicit_tokens, "output_tokens")
    last_prompt_tokens = _positive_int(agent_result.get("last_prompt_tokens"))
    estimated_output_tokens = _estimate_output_tokens(answer) if answer else 0

    if last_prompt_tokens > 0 and input_tokens > last_prompt_tokens * 2:
        input_tokens = last_prompt_tokens
    if estimated_output_tokens > 0 and output_tokens > max(estimated_output_tokens * 4, 256):
        output_tokens = estimated_output_tokens

    if input_tokens <= 0:
        input_tokens = _positive_int(agent_result.get("input_tokens"))
    if last_prompt_tokens > 0 and input_tokens > last_prompt_tokens * 2:
        input_tokens = last_prompt_tokens
    if input_tokens <= 0:
        input_tokens = last_prompt_tokens
    if input_tokens <= 0:
        input_tokens = _positive_int(local_vars.get("input_tokens"))

    if output_tokens <= 0:
        output_tokens = _positive_int(agent_result.get("output_tokens"))
    if estimated_output_tokens > 0 and output_tokens > max(estimated_output_tokens * 4, 256):
        output_tokens = estimated_output_tokens
    if output_tokens <= 0:
        output_tokens = _positive_int(local_vars.get("output_tokens"))
    if estimated_output_tokens > 0 and output_tokens > max(estimated_output_tokens * 4, 256):
        output_tokens = estimated_output_tokens
    if output_tokens <= 0 and answer:
        output_tokens = estimated_output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _completion_context(local_vars: dict[str, Any]) -> dict[str, int]:
    explicit_context = local_vars.get("context")
    agent_result = local_vars.get("agent_result")
    if not isinstance(agent_result, dict):
        agent_result = {}

    used_tokens = _context_value(explicit_context, "used_tokens")
    max_tokens = _context_value(explicit_context, "max_tokens")
    if used_tokens <= 0:
        used_tokens = _positive_int(agent_result.get("last_prompt_tokens"))
    if used_tokens <= 0:
        used_tokens = _positive_int(agent_result.get("context_used_tokens"))
    if max_tokens <= 0:
        max_tokens = _positive_int(agent_result.get("context_window"))
    if max_tokens <= 0:
        max_tokens = _positive_int(agent_result.get("context_length"))
    if max_tokens <= 0:
        max_tokens = _model_context_length(_completion_model(local_vars))
    return {"used_tokens": used_tokens, "max_tokens": max_tokens}


def _context_value(context: Any, name: str) -> int:
    if not isinstance(context, dict):
        return 0
    return _positive_int(context.get(name))


def _model_context_length(model: str) -> int:
    if not model or model == "Unknown":
        return 0
    try:
        from agent.model_metadata import get_model_context_length
    except Exception:
        return 0
    try:
        return _positive_int(get_model_context_length(model))
    except Exception:
        return 0


def _token_value(tokens: Any, name: str) -> int:
    if not isinstance(tokens, dict):
        return 0
    return _positive_int(tokens.get(name))


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _estimate_output_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    ascii_chars = sum(1 for char in stripped if ord(char) < 128)
    non_ascii_chars = len(stripped) - ascii_chars
    ascii_tokens = (ascii_chars + 3) // 4 if ascii_chars else 0
    estimated = non_ascii_chars + ascii_tokens
    return max(1, estimated)


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


def _first_attr_raw_string(obj: Any, names: tuple[str, ...]) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return _first_raw_string(obj, names)
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            continue
        if isinstance(value, str) and value:
            return value
    return None


def _platform_name(local_vars: dict[str, Any], source_obj: Any) -> str:
    platform = _coerce_platform_value(local_vars.get("platform"))
    if platform is None and source_obj is not None:
        try:
            platform = _coerce_platform_value(getattr(source_obj, "platform", None))
        except Exception:
            platform = None
    if platform is None:
        return "feishu"
    if "." in platform:
        platform = platform.rsplit(".", 1)[-1]
    return platform.lower()


def _coerce_platform_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str) and enum_value.strip():
        return enum_value.strip()
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
    *,
    preview: bool = False,
) -> str | None:
    key = (conversation_id, chat_id)
    if event_name == "message.started":
        if preview:
            cache_key = _new_fallback_cache_key(key, created_at_lifecycle_token)
            cached = _ACTIVE_FALLBACK_MESSAGE_IDS.get(cache_key)
            if cached is not None:
                return cached
            return _preview_fallback_message_id(
                key, conversation_id, chat_id, created_at_lifecycle_token
            )
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

    if preview:
        return _preview_fallback_message_id(
            key, conversation_id, chat_id, created_at_lifecycle_token
        )
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


def _preview_fallback_message_id(
    key: tuple[str, str],
    conversation_id: str,
    chat_id: str,
    created_at_lifecycle_token: str | None,
) -> str:
    if created_at_lifecycle_token is not None:
        token_key = (key[0], key[1], created_at_lifecycle_token)
        cached = _ACTIVE_FALLBACK_MESSAGE_IDS.get(token_key)
        if cached is not None:
            return cached
    else:
        current_key = _CURRENT_FALLBACK_KEYS.get(key)
        if current_key in _ACTIVE_FALLBACK_MESSAGE_IDS:
            return _ACTIVE_FALLBACK_MESSAGE_IDS[current_key]
    lifecycle_count = _FALLBACK_LIFECYCLE_COUNTS.get(key, 0)
    lifecycle_token = f"active:{lifecycle_count}"
    if created_at_lifecycle_token is not None:
        lifecycle_token = f"{lifecycle_token}:created_at:{created_at_lifecycle_token}"
    return _hash_fallback_message_id(conversation_id, chat_id, lifecycle_token)


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
) -> tuple[str, str, str | None] | object | None:
    if created_at_lifecycle_token is not None:
        token_key = (key[0], key[1], created_at_lifecycle_token)
        if token_key in _ACTIVE_FALLBACK_MESSAGE_IDS:
            return token_key
        if _active_fallback_cache_keys(key):
            return _AMBIGUOUS_TERMINAL
        return None

    active_keys = _active_fallback_cache_keys(key)
    if len(active_keys) == 1:
        return active_keys[0]
    if len(active_keys) > 1:
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


def _retire_current_fallback_key(key: tuple[str, str]) -> None:
    current_key = _CURRENT_FALLBACK_KEYS.pop(key, None)
    if current_key is not None:
        _ACTIVE_FALLBACK_MESSAGE_IDS.pop(current_key, None)


def _retire_all_fallback_keys(key: tuple[str, str]) -> None:
    for active_key in _active_fallback_cache_keys(key):
        _ACTIVE_FALLBACK_MESSAGE_IDS.pop(active_key, None)
    _CURRENT_FALLBACK_KEYS.pop(key, None)


def _hash_fallback_message_id(
    conversation_id: str, chat_id: str, lifecycle_token: str
) -> str:
    raw = f"{conversation_id}:{chat_id}:{lifecycle_token}".encode("utf-8")
    return "hfc_" + sha256(raw).hexdigest()[:16]


def _next_sequence(message_id: str) -> int:
    with _SEQUENCE_LOCK:
        sequence = _SEQUENCES.get(message_id, -1) + 1
        _SEQUENCES[message_id] = sequence
        return sequence


def _peek_next_sequence(message_id: str) -> int:
    with _SEQUENCE_LOCK:
        return _SEQUENCES.get(message_id, -1) + 1
