from __future__ import annotations

import os
from typing import Any, Dict

from aiohttp import web

from .events import EventValidationError, SidecarEvent
from .render import render_card
from .session import CardSession

FEISHU_CLIENT_KEY = web.AppKey("feishu_client", Any)
SESSIONS_KEY = web.AppKey("sessions", dict)
FEISHU_MESSAGE_IDS_KEY = web.AppKey("feishu_message_ids", dict)
PROCESS_TOKEN_KEY = web.AppKey("process_token", str)


def create_app(feishu_client: Any, process_token: str = "") -> web.Application:
    app = web.Application()
    app[FEISHU_CLIENT_KEY] = feishu_client
    app[SESSIONS_KEY] = {}
    app[FEISHU_MESSAGE_IDS_KEY] = {}
    app[PROCESS_TOKEN_KEY] = process_token
    app.router.add_get("/health", _health)
    app.router.add_post("/events", _events)
    return app


async def _health(request: web.Request) -> web.Response:
    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    response = {
        "status": "healthy",
        "active_sessions": len(sessions),
        "process_pid": os.getpid(),
    }
    process_token = request.app[PROCESS_TOKEN_KEY]
    if process_token:
        response["process_token"] = process_token
    return web.json_response(response)


async def _events(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        event = SidecarEvent.from_dict(payload)
    except (EventValidationError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    feishu_message_ids: Dict[str, str] = request.app[FEISHU_MESSAGE_IDS_KEY]
    session = sessions.get(event.message_id)

    if event.event == "message.started":
        if session is not None:
            return web.json_response({"ok": True, "applied": False})
        session = CardSession(
            conversation_id=event.conversation_id,
            message_id=event.message_id,
            chat_id=event.chat_id,
        )
        sessions[event.message_id] = session
        applied = session.apply(event)
        if applied and event.message_id not in feishu_message_ids:
            feishu_message_ids[event.message_id] = await request.app[FEISHU_CLIENT_KEY].send_card(
                event.chat_id,
                render_card(session),
            )
        return web.json_response({"ok": True, "applied": applied})

    if session is None:
        return web.json_response({"ok": True, "applied": False})

    feishu_message_id = feishu_message_ids.get(event.message_id)
    if _would_apply(session, event) and feishu_message_id is None:
        return web.json_response(
            {"ok": False, "error": "feishu_message_id missing"},
            status=409,
        )

    applied = session.apply(event)
    if applied and feishu_message_id is not None:
        await request.app[FEISHU_CLIENT_KEY].update_card_message(
            feishu_message_id,
            render_card(session),
        )
    return web.json_response({"ok": True, "applied": applied})


def _would_apply(session: CardSession, event: SidecarEvent) -> bool:
    return (
        event.conversation_id == session.conversation_id
        and event.message_id == session.message_id
        and event.chat_id == session.chat_id
        and event.sequence > session.last_sequence
        and session.status not in {"completed", "failed"}
    )
