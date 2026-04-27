from __future__ import annotations

import os
from typing import Any, Dict

from aiohttp import web

from .events import EventValidationError, SidecarEvent
from .metrics import SidecarMetrics
from .render import render_card
from .session import CardSession

FEISHU_CLIENT_KEY = web.AppKey("feishu_client", Any)
SESSIONS_KEY = web.AppKey("sessions", dict)
FEISHU_MESSAGE_IDS_KEY = web.AppKey("feishu_message_ids", dict)
PROCESS_TOKEN_KEY = web.AppKey("process_token", str)
METRICS_KEY = web.AppKey("metrics", SidecarMetrics)
UPDATE_MAX_ATTEMPTS = 2


def create_app(feishu_client: Any, process_token: str = "") -> web.Application:
    app = web.Application()
    app[FEISHU_CLIENT_KEY] = feishu_client
    app[SESSIONS_KEY] = {}
    app[FEISHU_MESSAGE_IDS_KEY] = {}
    app[PROCESS_TOKEN_KEY] = process_token
    app[METRICS_KEY] = SidecarMetrics()
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
    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    feishu_message_ids: Dict[str, str] = request.app[FEISHU_MESSAGE_IDS_KEY]
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
            message_id = await _send_card(request, event.chat_id, render_card(session))
            if message_id is None:
                sessions.pop(event.message_id, None)
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "feishu send failed"},
                    status=502,
                )
            feishu_message_ids[event.message_id] = message_id
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
    if applied and feishu_message_id is not None:
        if not await _update_card(request, feishu_message_id, render_card(session)):
            metrics.events_rejected += 1
            return web.json_response(
                {"ok": False, "error": "feishu update failed"},
                status=502,
            )
    if applied:
        metrics.events_applied += 1
    else:
        metrics.events_ignored += 1
    return web.json_response({"ok": True, "applied": applied})


async def _send_card(request: web.Request, chat_id: str, card: dict[str, Any]) -> str | None:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    metrics.feishu_send_attempts += 1
    try:
        message_id = await request.app[FEISHU_CLIENT_KEY].send_card(chat_id, card)
    except Exception:
        metrics.feishu_send_failures += 1
        return None
    metrics.feishu_send_successes += 1
    return message_id


async def _update_card(request: web.Request, message_id: str, card: dict[str, Any]) -> bool:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    for attempt in range(UPDATE_MAX_ATTEMPTS):
        if attempt > 0:
            metrics.feishu_update_retries += 1
        metrics.feishu_update_attempts += 1
        try:
            await request.app[FEISHU_CLIENT_KEY].update_card_message(message_id, card)
        except Exception:
            metrics.feishu_update_failures += 1
            continue
        metrics.feishu_update_successes += 1
        return True
    return False


def _would_apply(session: CardSession, event: SidecarEvent) -> bool:
    return (
        event.conversation_id == session.conversation_id
        and event.message_id == session.message_id
        and event.chat_id == session.chat_id
        and event.sequence > session.last_sequence
        and session.status not in {"completed", "failed"}
    )
