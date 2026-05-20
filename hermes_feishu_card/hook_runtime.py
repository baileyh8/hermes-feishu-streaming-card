from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib import parse
from urllib import request

DEFAULT_EVENT_URL = "http://127.0.0.1:8765/events"
DEFAULT_TIMEOUT_SECONDS = 0.8
TERMINAL_TIMEOUT_SECONDS = 10.0
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MEDIA_RE = re.compile(r"MEDIA:([^\s\]]+)")
LOCAL_FILE_RE = re.compile(
    r"(?<![:\w/])(/[^\s`]+\.(?:png|jpg|jpeg|webp|gif|pdf|txt|md|csv|xlsx|docx|mp3|wav|ogg|mp4|mov|webm))"
)
ATTACHMENT_TRAILING_PUNCTUATION = ",.;:)]}，。；：）】}"

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
_SEQUENCE_LOCK = threading.Lock()
_ACTIVE_FALLBACK_MESSAGE_IDS: dict[tuple[str, str, str | None], str] = {}
_CURRENT_FALLBACK_KEYS: dict[tuple[str, str], tuple[str, str, str | None]] = {}
_FALLBACK_LIFECYCLE_COUNTS: dict[tuple[str, str], int] = {}
_AMBIGUOUS_TERMINAL = object()


def reset_runtime_state() -> None:
    with _SEQUENCE_LOCK:
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
            _send_fail_open(config.event_url, payload, config.timeout_seconds)
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
        payload = build_event(event_name, local_vars)
        if payload is None:
            return False
        if "_hfc_loop" in local_vars:
            coroutine = _send_fail_open(config.event_url, payload, config.timeout_seconds)
            try:
                asyncio.run_coroutine_threadsafe(coroutine, local_vars["_hfc_loop"])
            except Exception:
                coroutine.close()
                raise
        else:
            asyncio.get_running_loop()
            asyncio.create_task(
                _send_fail_open(config.event_url, payload, config.timeout_seconds)
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
        payload = build_event(event_name, local_vars)
        if payload is None:
            return False
        await _post_json(config.event_url, payload, _timeout_for_event(config, event_name))
        return True
    except Exception:
        return False


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


def should_suppress_native_response(
    platform: str, delivered: bool, attachments: Any = None
) -> bool:
    if not delivered:
        return False
    if str(platform or "").lower() != "feishu":
        return False
    if attachments:
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


def _open_request(req: request.Request, timeout: float) -> None:
    with request.urlopen(req, timeout=timeout) as response:
        response.read()


def _open_json_request(req: request.Request, timeout: float) -> Any:
    with request.urlopen(req, timeout=timeout) as response:
        body = response.read()
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


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
    gateway_event_obj = local_vars.get("event")
    chat_id = _first_string(local_vars, ("chat_id", "open_chat_id", "receive_id"))
    message_obj = local_vars.get("message")
    if chat_id is None:
        chat_id = _first_attr_string(message_obj, ("chat_id", "open_chat_id", "receive_id"))
    if chat_id is None:
        chat_id = _first_attr_string(source_obj, ("chat_id", "open_chat_id", "receive_id"))
    if chat_id is None:
        return None

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
    payload = {
        "schema_version": "1",
        "event": event_name,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "chat_id": chat_id,
        "platform": _platform_name(local_vars, source_obj),
        "sequence": sequence,
        "created_at": created_at,
        "data": _event_data(event_name, local_vars, source_obj, message_obj),
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
    chat_id = str(
        origin.get("chat_id") or os.environ.get("HERMES_CRON_AUTO_DELIVER_CHAT_ID") or ""
    ).strip()
    platform = str(
        origin.get("platform")
        or os.environ.get("HERMES_CRON_AUTO_DELIVER_PLATFORM")
        or "feishu"
    ).strip().lower()
    if platform != "feishu" or not chat_id:
        return None

    profile_id, profile_source = _profile_identity(local_vars, None, None)
    created_at = time.time()
    job_id = str(job.get("id") or "").strip()
    message_id = "cron_" + sha256(f"{job_id}:{created_at}".encode("utf-8")).hexdigest()[
        :16
    ]
    return {
        "schema_version": "1",
        "event": "message.completed",
        "conversation_id": str(job.get("id") or chat_id),
        "message_id": message_id,
        "chat_id": chat_id,
        "platform": "feishu",
        "sequence": 0,
        "created_at": created_at,
        "data": {
            "answer": content,
            "delivery_kind": "cron",
            "profile_id": profile_id,
            "profile_source": profile_source,
            "attachments": _extract_attachments(content),
        },
    }


def _event_data(
    event_name: str, local_vars: dict[str, Any], source_obj: Any, message_obj: Any
) -> dict[str, Any]:
    profile_id, profile_source = _profile_identity(local_vars, source_obj, message_obj)
    data: dict[str, Any] = {
        "profile_id": profile_id,
        "profile_source": profile_source,
    }
    if event_name in {"thinking.delta", "answer.delta"}:
        text = _first_string(local_vars, ("text", "delta", "delta_text", "content"))
        if text is None:
            text = _first_attr_string(message_obj, ("text", "content"))
        data["text"] = text or ""
        return data
    if event_name == "tool.updated":
        tool_id = _first_string(local_vars, ("tool_id", "tool_call_id", "name")) or "tool"
        name = _first_string(local_vars, ("name", "tool_name")) or tool_id
        status = _first_string(local_vars, ("status", "tool_status")) or "running"
        detail = _first_string(local_vars, ("detail", "tool_detail")) or ""
        data.update({"tool_id": tool_id, "name": name, "status": status, "detail": detail})
        return data
    if event_name == "message.completed":
        answer = _first_string(local_vars, ("answer", "response", "final_answer", "text", "content")) or ""
        data.update({
            "answer": answer,
            "attachments": _extract_attachments(answer),
            "duration": _completion_duration(local_vars),
            "model": _completion_model(local_vars),
            "tokens": _completion_tokens(local_vars, answer),
            "context": _completion_context(local_vars),
        })
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
    parts = Path(path).expanduser().parts
    for index in range(len(parts) - 2):
        if parts[index] == ".hermes" and parts[index + 1] == "profiles":
            if index + 3 != len(parts):
                return None
            candidate = parts[index + 2].strip()
            if candidate:
                return candidate
    return None


def _first_string(source: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = source.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_attachments(text: str) -> list[dict[str, str]]:
    seen = set()
    attachments = []
    for raw in list(MEDIA_RE.findall(text or "")) + list(LOCAL_FILE_RE.findall(text or "")):
        name = _attachment_name(raw)
        if not name or name in seen:
            continue
        seen.add(name)
        attachments.append({"kind": _attachment_kind(name), "name": name, "summary": name})
    return attachments


def _attachment_name(raw: str) -> str:
    return Path(raw.strip().rstrip(ATTACHMENT_TRAILING_PUNCTUATION)).name.strip()


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


def _platform_name(local_vars: dict[str, Any], source_obj: Any) -> str:
    platform = _first_string(local_vars, ("platform",)) or _first_attr_string(
        source_obj, ("platform",)
    )
    if platform is None:
        return "feishu"
    if "." in platform:
        platform = platform.rsplit(".", 1)[-1]
    return platform.lower()


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
