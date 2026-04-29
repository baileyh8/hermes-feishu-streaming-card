from __future__ import annotations

import os
import time
import asyncio
import logging
from typing import Any, Dict

from aiohttp import web

from .bots import RouteResult
from .events import EventValidationError, SidecarEvent
from .metrics import SidecarMetrics
from .render import render_card
from .session import CardSession

FEISHU_CLIENT_KEY = web.AppKey("feishu_client", Any)
SESSIONS_KEY = web.AppKey("sessions", dict)
FEISHU_MESSAGE_IDS_KEY = web.AppKey("feishu_message_ids", dict)
MESSAGE_BOT_IDS_KEY = web.AppKey("message_bot_ids", dict)
BOT_ROUTER_KEY = web.AppKey("bot_router", Any)
ROUTING_DIAGNOSTICS_KEY = web.AppKey("routing_diagnostics", dict)
PROCESS_TOKEN_KEY = web.AppKey("process_token", str)
METRICS_KEY = web.AppKey("metrics", SidecarMetrics)
LAST_UPDATE_AT_KEY = web.AppKey("last_update_at", dict)
MESSAGE_LOCKS_KEY = web.AppKey("message_locks", dict)
FOOTER_FIELDS_KEY = web.AppKey("footer_fields", Any)
CARD_TITLE_KEY = web.AppKey("card_title", str)
UPDATE_MAX_ATTEMPTS = 3
UPDATE_MIN_INTERVAL_SECONDS = 2.0
TERMINAL_EVENTS = {"message.completed", "message.failed"}
DIAGNOSTICS_KEY = web.AppKey("diagnostics", dict)
logger = logging.getLogger(__name__)


def create_app(
    feishu_client: Any,
    process_token: str = "",
    card_config: dict[str, Any] | None = None,
    bot_router: Any = None,
) -> web.Application:
    app = web.Application()
    card_config = card_config or {}
    app[FEISHU_CLIENT_KEY] = feishu_client
    app[SESSIONS_KEY] = {}
    app[FEISHU_MESSAGE_IDS_KEY] = {}
    app[MESSAGE_BOT_IDS_KEY] = {}
    app[BOT_ROUTER_KEY] = bot_router
    app[PROCESS_TOKEN_KEY] = process_token
    app[METRICS_KEY] = SidecarMetrics()
    app[LAST_UPDATE_AT_KEY] = {}
    app[MESSAGE_LOCKS_KEY] = {}
    app[DIAGNOSTICS_KEY] = {
        "last_update_error": "",
        "last_route_error": "",
        "last_terminal_event": {},
    }
    app[ROUTING_DIAGNOSTICS_KEY] = _initial_routing_diagnostics(feishu_client)
    footer_fields = card_config.get("footer_fields")
    app[FOOTER_FIELDS_KEY] = list(footer_fields) if isinstance(footer_fields, list) else None
    title = card_config.get("title")
    app[CARD_TITLE_KEY] = title if isinstance(title, str) else "Hermes Agent"
    app.router.add_get("/health", _health)
    app.router.add_post("/events", _events)
    return app


async def _health(request: web.Request) -> web.Response:
    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    response = {
        "status": "healthy",
        "active_sessions": len(sessions),
        "process_pid": os.getpid(),
        "metrics": metrics.snapshot(),
        "sessions": {
            message_id: {
                "status": session.status,
                "last_sequence": session.last_sequence,
                "answer_chars": len(session.answer_text),
                "thinking_chars": len(session.thinking_text),
                "tool_count": session.tool_count,
            }
            for message_id, session in sessions.items()
        },
        "diagnostics": request.app[DIAGNOSTICS_KEY],
        "routing": request.app[ROUTING_DIAGNOSTICS_KEY],
    }
    process_token = request.app[PROCESS_TOKEN_KEY]
    if process_token:
        response["process_token"] = process_token
    return web.json_response(response)


async def _events(request: web.Request) -> web.Response:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    try:
        payload = await request.json()
        event = SidecarEvent.from_dict(payload)
    except (EventValidationError, ValueError) as exc:
        metrics.events_rejected += 1
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    metrics.events_received += 1
    message_locks: Dict[str, asyncio.Lock] = request.app[MESSAGE_LOCKS_KEY]
    lock = message_locks.setdefault(event.message_id, asyncio.Lock())
    async with lock:
        response = await _apply_event_locked(request, event)
    return response


async def _apply_event_locked(request: web.Request, event: SidecarEvent) -> web.Response:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    feishu_message_ids: Dict[str, str] = request.app[FEISHU_MESSAGE_IDS_KEY]
    message_bot_ids: Dict[str, str] = request.app[MESSAGE_BOT_IDS_KEY]
    last_update_at: Dict[str, float] = request.app[LAST_UPDATE_AT_KEY]
    session = sessions.get(event.message_id)

    if event.event == "message.started":
        if session is not None:
            metrics.events_ignored += 1
            return web.json_response({"ok": True, "applied": False})
        session = CardSession(
            conversation_id=event.conversation_id,
            message_id=event.message_id,
            chat_id=event.chat_id,
        )
        sessions[event.message_id] = session
        applied = session.apply(event)
        if applied and event.message_id not in feishu_message_ids:
            route = _resolve_route(request, event)
            if route is None:
                sessions.pop(event.message_id, None)
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "bot route failed"},
                    status=502,
                )
            message_id = await _send_card(
                request,
                event.chat_id,
                _render_session_card(request, session),
                route.bot_id,
            )
            if message_id is None:
                sessions.pop(event.message_id, None)
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "feishu send failed"},
                    status=502,
                )
            feishu_message_ids[event.message_id] = message_id
            message_bot_ids[event.message_id] = route.bot_id
        if applied:
            metrics.events_applied += 1
        else:
            metrics.events_ignored += 1
        return web.json_response({"ok": True, "applied": applied})

    if session is None:
        metrics.events_ignored += 1
        return web.json_response({"ok": True, "applied": False})

    feishu_message_id = feishu_message_ids.get(event.message_id)
    if _would_apply(session, event) and feishu_message_id is None:
        metrics.events_rejected += 1
        return web.json_response(
            {"ok": False, "error": "feishu_message_id missing"},
            status=409,
        )

    applied = session.apply(event)
    if event.event in TERMINAL_EVENTS:
        request.app[DIAGNOSTICS_KEY]["last_terminal_event"] = {
            "message_id": event.message_id,
            "event": event.event,
            "sequence": event.sequence,
            "applied": applied,
            "session_status": session.status,
            "answer_chars": len(session.answer_text),
        }
    if applied and feishu_message_id is not None:
        should_update = _should_update_card(last_update_at, event)
        if should_update:
            update_delay = _update_delay_seconds(last_update_at, event)
            if update_delay > 0:
                await asyncio.sleep(update_delay)
        if should_update:
            updated = await _update_card(
                request,
                feishu_message_id,
                _render_session_card(request, session),
                message_bot_ids.get(event.message_id),
            )
            if not updated and event.event not in TERMINAL_EVENTS:
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "feishu update failed"},
                    status=502,
                )
            if not updated and event.event in TERMINAL_EVENTS:
                asyncio.create_task(
                    _retry_terminal_update(
                        request.app,
                        feishu_message_id,
                        _render_session_card(request, session),
                        message_bot_ids.get(event.message_id),
                    )
                )
        if should_update:
            last_update_at[event.message_id] = time.monotonic()
    if applied:
        metrics.events_applied += 1
    else:
        metrics.events_ignored += 1
    return web.json_response({"ok": True, "applied": applied})


def _render_session_card(request: web.Request, session: CardSession) -> dict[str, Any]:
    footer_fields = request.app[FOOTER_FIELDS_KEY]
    return render_card(
        session,
        footer_fields=footer_fields,
        title=request.app[CARD_TITLE_KEY],
    )


async def _send_card(
    request: web.Request, chat_id: str, card: dict[str, Any], bot_id: str | None
) -> str | None:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    metrics.feishu_send_attempts += 1
    try:
        message_id = await _client_for_bot(request.app, bot_id).send_card(chat_id, card)
    except Exception:
        metrics.feishu_send_failures += 1
        return None
    metrics.feishu_send_successes += 1
    return message_id


async def _update_card(
    request: web.Request, message_id: str, card: dict[str, Any], bot_id: str | None
) -> bool:
    return await _update_card_for_app(request.app, message_id, card, bot_id)


async def _update_card_for_app(
    app: web.Application, message_id: str, card: dict[str, Any], bot_id: str | None
) -> bool:
    metrics: SidecarMetrics = app[METRICS_KEY]
    for attempt in range(UPDATE_MAX_ATTEMPTS):
        if attempt > 0:
            metrics.feishu_update_retries += 1
        metrics.feishu_update_attempts += 1
        try:
            await _client_for_bot(app, bot_id).update_card_message(message_id, card)
        except Exception as exc:
            message = _safe_update_error_message(bot_id, exc)
            app[DIAGNOSTICS_KEY]["last_update_error"] = message[:500]
            logger.warning("Feishu card update failed: %s", message)
            metrics.feishu_update_failures += 1
            continue
        metrics.feishu_update_successes += 1
        return True
    return False


async def _retry_terminal_update(
    app: web.Application, message_id: str, card: dict[str, Any], bot_id: str | None
) -> None:
    for delay in (1.0, 2.0, 4.0):
        await asyncio.sleep(delay)
        if await _update_card_for_app(app, message_id, card, bot_id):
            return


def _resolve_route(request: web.Request, event: SidecarEvent) -> RouteResult | None:
    feishu_client = request.app[FEISHU_CLIENT_KEY]
    diagnostics = request.app[ROUTING_DIAGNOSTICS_KEY]
    app_diagnostics = request.app[DIAGNOSTICS_KEY]
    if not _is_client_factory(feishu_client):
        diagnostics["last_route"] = {
            "message_id": event.message_id,
            "chat_id": event.chat_id,
            "bot_id": "",
            "reason": "legacy",
        }
        diagnostics["last_route_error"] = ""
        app_diagnostics["last_route_error"] = ""
        return RouteResult("", "legacy")

    bot_router = request.app[BOT_ROUTER_KEY]
    try:
        route = _coerce_route_result(bot_router(event))
        feishu_client.get_client(route.bot_id)
    except Exception as exc:
        safe_error = exc.__class__.__name__
        diagnostics["last_route_error"] = safe_error
        app_diagnostics["last_route_error"] = safe_error
        diagnostics["last_route"] = {}
        return None

    diagnostics["last_route"] = {
        "message_id": event.message_id,
        "chat_id": event.chat_id,
        "bot_id": route.bot_id,
        "reason": route.reason,
    }
    diagnostics["last_route_error"] = ""
    app_diagnostics["last_route_error"] = ""
    return route


def _coerce_route_result(value: Any) -> RouteResult:
    if isinstance(value, RouteResult):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        bot_id, reason = value
        return RouteResult(str(bot_id), str(reason))
    raise TypeError("bot_router must return RouteResult or (bot_id, reason)")


def _client_for_bot(app: web.Application, bot_id: str | None) -> Any:
    feishu_client = app[FEISHU_CLIENT_KEY]
    if _is_client_factory(feishu_client):
        if bot_id is None:
            raise RuntimeError("bot id missing")
        return feishu_client.get_client(bot_id)
    return feishu_client


def _is_client_factory(feishu_client: Any) -> bool:
    return callable(getattr(feishu_client, "get_client", None))


def _safe_update_error_message(bot_id: str | None, exc: Exception) -> str:
    return f"bot_id={bot_id or ''} {exc.__class__.__name__}"


def _initial_routing_diagnostics(feishu_client: Any) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "default_bot": "",
        "bot_count": 0,
        "chat_binding_count": 0,
        "last_route": {},
        "last_route_error": "",
    }
    registry = getattr(feishu_client, "registry", None)
    safe_diagnostics = getattr(registry, "safe_diagnostics", None)
    if callable(safe_diagnostics):
        try:
            diagnostics.update(_sanitize_routing_diagnostics(safe_diagnostics()))
        except Exception as exc:
            diagnostics["last_route_error"] = exc.__class__.__name__
    for key in ("default_bot", "bot_count", "chat_binding_count"):
        diagnostics.setdefault(key, "" if key == "default_bot" else 0)
    diagnostics.setdefault("last_route", {})
    diagnostics.setdefault("last_route_error", "")
    return diagnostics


def _sanitize_routing_diagnostics(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                continue
            sanitized[key_text] = _sanitize_routing_diagnostics(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_routing_diagnostics(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in ("secret", "token", "password", "key"))


def _would_apply(session: CardSession, event: SidecarEvent) -> bool:
    return (
        event.conversation_id == session.conversation_id
        and event.message_id == session.message_id
        and event.chat_id == session.chat_id
        and event.sequence > session.last_sequence
        and session.status not in {"completed", "failed"}
    )


def _should_update_card(last_update_at: Dict[str, float], event: SidecarEvent) -> bool:
    if event.event in TERMINAL_EVENTS:
        return True
    previous = last_update_at.get(event.message_id)
    if previous is None:
        return True
    return time.monotonic() - previous >= UPDATE_MIN_INTERVAL_SECONDS


def _update_delay_seconds(last_update_at: Dict[str, float], event: SidecarEvent) -> float:
    if event.event not in TERMINAL_EVENTS:
        return 0.0
    previous = last_update_at.get(event.message_id)
    if previous is None:
        return 0.0
    return max(0.0, UPDATE_MIN_INTERVAL_SECONDS - (time.monotonic() - previous))
