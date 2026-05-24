from __future__ import annotations

import os
import time
import asyncio
import logging
import re
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
CARD_SUMMARIES_KEY = web.AppKey("card_summaries", dict)
MESSAGE_BOT_IDS_KEY = web.AppKey("message_bot_ids", dict)
SESSION_CARD_CONFIGS_KEY = web.AppKey("session_card_configs", dict)
UPDATE_TASKS_KEY = web.AppKey("update_tasks", dict)
BOT_ROUTER_KEY = web.AppKey("bot_router", Any)
ROUTING_DIAGNOSTICS_KEY = web.AppKey("routing_diagnostics", dict)
PROFILE_DIAGNOSTICS_KEY = web.AppKey("profile_diagnostics", dict)
PROCESS_TOKEN_KEY = web.AppKey("process_token", str)
METRICS_KEY = web.AppKey("metrics", SidecarMetrics)
LAST_UPDATE_AT_KEY = web.AppKey("last_update_at", dict)
MESSAGE_LOCKS_KEY = web.AppKey("message_locks", dict)
FOOTER_FIELDS_KEY = web.AppKey("footer_fields", Any)
CARD_TITLE_KEY = web.AppKey("card_title", str)
BASE_CARD_CONFIG_KEY = web.AppKey("base_card_config", dict)
UPDATE_MAX_ATTEMPTS = 3
UPDATE_MIN_INTERVAL_SECONDS = 0.5
TERMINAL_EVENTS = {"message.completed", "message.failed"}
DIAGNOSTICS_KEY = web.AppKey("diagnostics", dict)
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
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
    # TODO: replace this short-lived in-process index with bounded shared storage.
    app[CARD_SUMMARIES_KEY] = {}
    app[MESSAGE_BOT_IDS_KEY] = {}
    app[SESSION_CARD_CONFIGS_KEY] = {}
    app[UPDATE_TASKS_KEY] = {}
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
    app[PROFILE_DIAGNOSTICS_KEY] = {}
    app[BASE_CARD_CONFIG_KEY] = dict(card_config)
    footer_fields = card_config.get("footer_fields")
    app[FOOTER_FIELDS_KEY] = list(footer_fields) if isinstance(footer_fields, list) else None
    title = card_config.get("title")
    app[CARD_TITLE_KEY] = title if isinstance(title, str) else "Hermes Agent"
    app.router.add_get("/health", _health)
    app.router.add_get("/messages/{message_id}/summary", _message_summary)
    app.router.add_post("/events", _events)
    app.router.add_post("/progress", _progress)
    return app


async def _health(request: web.Request) -> web.Response:
    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    diagnostics = request.app[DIAGNOSTICS_KEY]
    response = {
        "status": "healthy",
        "active_sessions": len(sessions),
        "process_pid": os.getpid(),
        "metrics": metrics.snapshot(),
        "reply_index": {
            "entries": len(request.app[CARD_SUMMARIES_KEY]),
            "last_lookup": diagnostics.get("last_reply_lookup", {}),
        },
        "cron": {
            "cards_sent": metrics.cron_cards_sent,
            "fallbacks": metrics.cron_fallbacks,
        },
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
        "diagnostics": diagnostics,
        "routing": request.app[ROUTING_DIAGNOSTICS_KEY],
        "profile_diagnostics": request.app[PROFILE_DIAGNOSTICS_KEY],
    }
    process_token = request.app[PROCESS_TOKEN_KEY]
    if process_token:
        response["process_token"] = process_token

    # Multi-profile stats
    boundary = request.app.get(FEISHU_CLIENT_KEY)
    if isinstance(boundary, dict):
        profile_stats = {}
        for profile_id, factory in boundary.items():
            profile_sessions = {
                k: v for k, v in sessions.items() if k.startswith(f"{profile_id}:")
            }
            profile_stats[profile_id] = {
                "active_sessions": len(profile_sessions),
                "sessions": {
                    key.replace(f"{profile_id}:", ""): {
                        "status": s.status,
                        "last_sequence": s.last_sequence,
                    }
                    for key, s in profile_sessions.items()
                },
            }
        response["profiles"] = profile_stats

    return web.json_response(response)


async def _message_summary(request: web.Request) -> web.Response:
    summaries: Dict[str, dict[str, Any]] = request.app[CARD_SUMMARIES_KEY]
    summary = summaries.get(request.match_info["message_id"])
    if summary is None:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    return web.json_response({"ok": True, **summary})


async def _progress(request: web.Request) -> web.Response:
    """POST /progress — update tool progress on an active card.

    Accepts: { tool_id, percent, detail?, eta?, message_id? }
    Updates the tool state directly in the CardSession and triggers a card update.
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)

    tool_id = data.get("tool_id", data.get("name", ""))
    if not isinstance(tool_id, str) or not tool_id:
        return web.json_response({"ok": False, "error": "tool_id required"}, status=400)

    percent = data.get("percent")
    if not isinstance(percent, int):
        return web.json_response({"ok": False, "error": "percent must be int"}, status=400)

    detail = data.get("detail", "")
    eta = data.get("eta", 0)
    message_id = data.get("message_id", "")

    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    feishu_message_ids: Dict[str, str] = request.app[FEISHU_MESSAGE_IDS_KEY]

    # Find the session to update
    target_session = None
    target_key = ""

    if message_id and message_id in sessions:
        target_key = message_id
        target_session = sessions[message_id]
    elif feishu_message_ids:
        # Match by feishu_message_id (which maps card msg_id -> internal session key)
        for sk, fm_id in feishu_message_ids.items():
            if fm_id == message_id and sk in sessions:
                target_key = sk
                target_session = sessions[sk]
                break

    if not target_session:
        # Try any active session with running tools
        for sk, session in sessions.items():
            if session.status in ("thinking", "answering"):
                target_key = sk
                target_session = session
                break

    if not target_session:
        return web.json_response({
            "ok": False,
            "error": "no active session found",
            "message_id": message_id,
            "active_sessions": list(sessions.keys()),
        }, status=404)

    # Update the tool state
    existing = target_session.tools.get(tool_id)
    if existing:
        existing.percent = min(100, max(0, percent))
        existing.detail = str(detail) if detail else existing.detail
        if isinstance(eta, int) and eta >= 0:
            existing.eta = eta
    else:
        from .session import ToolState
        target_session.tools[tool_id] = ToolState(
            tool_id=tool_id,
            name=tool_id,
            status="running" if percent < 100 else "completed",
            detail=str(detail),
            percent=min(100, max(0, percent)),
            eta=eta if isinstance(eta, int) and eta >= 0 else 0,
        )
        target_session._tool_call_count += 1

    # Trigger card update
    # Look up the Feishu message ID and bot_id for this session
    feishu_msg_id = feishu_message_ids.get(target_key)
    bot_id = request.app[MESSAGE_BOT_IDS_KEY].get(target_key)

    if not feishu_msg_id:
        return web.json_response({
            "ok": False,
            "error": "no feishu message id for session (card not created yet)",
            "session": target_key,
        }, status=409)

    from .render import render_card
    card = render_card(
        session=target_session,
        footer_fields=request.app[FOOTER_FIELDS_KEY],
        title=request.app[CARD_TITLE_KEY],
    )

    update_tasks: Dict[str, asyncio.Task] = request.app[UPDATE_TASKS_KEY]

    # Cancel any pending update for this session
    old_task = update_tasks.get(target_key)
    if old_task and not old_task.done():
        old_task.cancel()

    async def _do_update():
        try:
            await _update_card_for_app(request.app, feishu_msg_id, card, bot_id)
        except Exception:
            pass

    task = asyncio.create_task(_do_update())
    update_tasks[target_key] = task

    return web.json_response({
        "ok": True,
        "tool_id": tool_id,
        "percent": percent,
        "session": target_key,
    })


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
    lock = message_locks.setdefault(_session_key(event), asyncio.Lock())
    async with lock:
        response, post_lock_task = await _apply_event_locked(request, event)
    if post_lock_task is not None:
        await post_lock_task
    return response


async def _progress(request: web.Request) -> web.Response:
    """Simple progress update endpoint for long-running tools.

    Accepts {tool_id, percent, detail, eta} and updates the active
    session's tool state without requiring a full SidecarEvent.
    """
    import math
    import time as _time

    metrics: SidecarMetrics = request.app[METRICS_KEY]
    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)

    tool_id = body.get("tool_id", "tool")
    percent = max(0, min(100, int(body.get("percent", 0))))
    detail = str(body.get("detail", ""))
    eta = max(0, int(body.get("eta", 0)))
    message_id = str(body.get("message_id", ""))

    if not message_id:
        return web.json_response({"ok": False, "error": "message_id required"}, status=400)

    # Find the session by exact message_id or composite key
    session = sessions.get(message_id)
    if session is None:
        for key, s in sessions.items():
            if key.endswith(f":{message_id}") or key == message_id:
                session = s
                break

    if session is None:
        metrics.events_rejected += 1
        return web.json_response({"ok": False, "error": "session not found"}, status=404)

    # Create a lightweight event-like data dict and update the session
    data = {
        "tool_id": tool_id,
        "name": tool_id,
        "status": "running",
        "detail": detail,
        "percent": percent,
        "eta": eta,
    }

    # Manually update the tool state in the session
    tool = session.tools.get(tool_id)
    if tool is None:
        from .session import ToolState
        tool = ToolState(tool_id=tool_id, name=tool_id, status="running", detail=detail, percent=percent, eta=eta)
        session.tools[tool_id] = tool
        session._tool_call_count += 1
    else:
        tool.status = "running"
        tool.detail = detail
        tool.percent = percent
        tool.eta = eta

    # Queue a card update (same pattern as _apply_event_locked)
    last_update_at: Dict[str, float] = request.app[LAST_UPDATE_AT_KEY]
    update_tasks: Dict[str, asyncio.Task] = request.app[UPDATE_TASKS_KEY]
    feishu_message_ids: Dict[str, str] = request.app[FEISHU_MESSAGE_IDS_KEY]
    message_bot_ids: Dict[str, str] = request.app[MESSAGE_BOT_IDS_KEY]

    feishu_message_id = feishu_message_ids.get(message_id)
    if feishu_message_id:
        from .render import render_card
        card = render_card(session, request.app.get(FOOTER_FIELDS_KEY), request.app.get(CARD_TITLE_KEY, "Hermes Agent"))
        bot_id = message_bot_ids.get(message_id)

        async def _do_update():
            try:
                await _update_card_for_app(request.app, feishu_message_id, card, bot_id)
            except Exception:
                pass

        async def _queued_update():
            previous_task = update_tasks.get(message_id)
            if previous_task is not None:
                try:
                    await previous_task
                except Exception:
                    pass
            try:
                await _do_update()
            finally:
                if update_tasks.get(message_id) is current_task:
                    update_tasks.pop(message_id, None)

        current_task = asyncio.create_task(_queued_update())
        update_tasks[message_id] = current_task
        metrics.events_applied += 1

    return web.json_response({"ok": True, "applied": True})


def _session_key(event: SidecarEvent) -> str:
    """Return the session key for an event.

    When profiles are active, uses composite key profile_id:message_id.
    Otherwise uses message_id directly (backward compatible).
    """
    has_profile_id = isinstance(event.data, dict) and "profile_id" in event.data
    profile_id = _safe_profile_id(event.data.get("profile_id") if has_profile_id else None)
    if has_profile_id:
        return f"{profile_id}:{event.message_id}"
    return event.message_id


async def _apply_event_locked(request: web.Request, event: SidecarEvent) -> tuple[web.Response, Any]:
    """Process event state inside the lock. Returns (response, post_lock_task).

    post_lock_task is a coroutine that performs Feishu API calls outside the lock
    to avoid blocking subsequent event processing.
    """
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    feishu_message_ids: Dict[str, str] = request.app[FEISHU_MESSAGE_IDS_KEY]
    message_bot_ids: Dict[str, str] = request.app[MESSAGE_BOT_IDS_KEY]
    last_update_at: Dict[str, float] = request.app[LAST_UPDATE_AT_KEY]
    update_tasks: Dict[str, asyncio.Task] = request.app[UPDATE_TASKS_KEY]
    _record_profile_diagnostics(request.app, event)
    session = sessions.get(_session_key(event))

    if event.event == "message.started":
        if session is not None:
            metrics.events_ignored += 1
            return web.json_response({"ok": True, "applied": False}), None
        session = CardSession(
            conversation_id=event.conversation_id,
            message_id=event.message_id,
            chat_id=event.chat_id,
        )
        sessions[_session_key(event)] = session
        applied = session.apply(event)
        if applied and _session_key(event) not in feishu_message_ids:
            route = _resolve_route(request, event)
            if route is None:
                sessions.pop(_session_key(event), None)
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "bot route failed"},
                    status=502,
                ), None
            request.app[SESSION_CARD_CONFIGS_KEY][_session_key(event)] = (
                _resolve_session_card_config(request.app, route.bot_id, event)
            )
            message_id = await _send_card(
                request,
                event.chat_id,
                _render_session_card(request, session),
                route.bot_id,
            )
            if message_id is None:
                sessions.pop(_session_key(event), None)
                request.app[SESSION_CARD_CONFIGS_KEY].pop(_session_key(event), None)
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "feishu send failed"},
                    status=502,
                ), None
            feishu_message_ids[_session_key(event)] = message_id
            message_bot_ids[_session_key(event)] = route.bot_id
        if applied:
            metrics.events_applied += 1
        else:
            metrics.events_ignored += 1
        return web.json_response({"ok": True, "applied": applied}), None

    if session is None:
        if event.event == "message.completed" and _delivery_kind(event) == "cron":
            session = CardSession(
                conversation_id=event.conversation_id,
                message_id=event.message_id,
                chat_id=event.chat_id,
            )
            sessions[_session_key(event)] = session
            applied = session.apply(event)
            if applied:
                route = _resolve_route(request, event)
                if route is None:
                    sessions.pop(_session_key(event), None)
                    metrics.cron_fallbacks += 1
                    metrics.events_rejected += 1
                    return web.json_response(
                        {"ok": False, "error": "bot route failed"},
                        status=502,
                    ), None
                request.app[SESSION_CARD_CONFIGS_KEY][_session_key(event)] = (
                    _resolve_session_card_config(request.app, route.bot_id, event)
                )
                message_id = await _send_card(
                    request,
                    event.chat_id,
                    _render_session_card(request, session),
                    route.bot_id,
                )
                if message_id is None:
                    sessions.pop(_session_key(event), None)
                    request.app[SESSION_CARD_CONFIGS_KEY].pop(_session_key(event), None)
                    metrics.cron_fallbacks += 1
                    metrics.events_rejected += 1
                    return web.json_response(
                        {"ok": False, "error": "feishu send failed"},
                        status=502,
                    ), None
                feishu_message_ids[_session_key(event)] = message_id
                message_bot_ids[_session_key(event)] = route.bot_id
                _store_card_summary(request.app, event, session, message_id)
                request.app[DIAGNOSTICS_KEY]["last_terminal_event"] = {
                    "message_id": event.message_id,
                    "event": event.event,
                    "sequence": event.sequence,
                    "applied": applied,
                    "session_status": session.status,
                    "answer_chars": len(session.answer_text),
                }
                metrics.events_applied += 1
                metrics.cron_cards_sent += 1
            else:
                metrics.events_ignored += 1
            return web.json_response({"ok": True, "applied": applied}), None
        metrics.events_ignored += 1
        return web.json_response({"ok": True, "applied": False}), None

    feishu_message_id = feishu_message_ids.get(_session_key(event))
    if _would_apply(session, event) and feishu_message_id is None:
        metrics.events_rejected += 1
        return web.json_response(
            {"ok": False, "error": "feishu_message_id missing"},
            status=409,
        ), None

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
    post_lock_task = None
    if applied and feishu_message_id is not None:
        if event.event in TERMINAL_EVENTS:
            _store_card_summary(request.app, event, session, feishu_message_id)
        should_update = _should_update_card(last_update_at, event)
        if should_update:
            # 锁内立即标记，防止后续事件在API完成前重复触发更新
            last_update_at[_session_key(event)] = time.monotonic()
            card = _render_session_card(request, session)
            bot_id = message_bot_ids.get(_session_key(event))
            is_terminal = event.event in TERMINAL_EVENTS
            session_key = _session_key(event)

            async def _do_update():
                if is_terminal:
                    delay = _update_delay_seconds(last_update_at, event)
                    if delay > 0:
                        await asyncio.sleep(delay)
                updated = await _update_card_for_app(request.app, feishu_message_id, card, bot_id)
                if not updated and is_terminal:
                    await _retry_terminal_update(request.app, feishu_message_id, card, bot_id)

            previous_task = update_tasks.get(session_key)

            async def _queued_update():
                if previous_task is not None:
                    try:
                        await previous_task
                    except Exception:
                        logger.exception("previous Feishu card update task failed")
                try:
                    await _do_update()
                finally:
                    if update_tasks.get(session_key) is current_task:
                        update_tasks.pop(session_key, None)

            current_task = asyncio.create_task(_queued_update())
            update_tasks[session_key] = current_task
            post_lock_task = current_task
    if applied:
        metrics.events_applied += 1
    else:
        metrics.events_ignored += 1
    return web.json_response({"ok": True, "applied": applied}), post_lock_task


def _store_card_summary(
    app: web.Application,
    event: SidecarEvent,
    session: CardSession,
    feishu_message_id: str,
) -> None:
    summary = session.answer_text.strip()
    if not summary:
        return
    data = event.data if isinstance(event.data, dict) else {}
    profile_id = _safe_profile_id(data.get("profile_id"))
    app[CARD_SUMMARIES_KEY][feishu_message_id] = {
        "summary": summary[:4000],
        "profile_id": profile_id,
        "chat_id": event.chat_id,
        "message_id": feishu_message_id,
    }


def _record_profile_diagnostics(app: web.Application, event: SidecarEvent) -> None:
    data = event.data if isinstance(event.data, dict) else {}
    profile_id = _safe_profile_id(data.get("profile_id"))
    source = str(data.get("profile_source") or "")
    diagnostics = app[PROFILE_DIAGNOSTICS_KEY].setdefault(
        profile_id,
        {"events": 0, "last_profile_source": "", "last_message_id": ""},
    )
    diagnostics["events"] += 1
    diagnostics["last_profile_source"] = source
    diagnostics["last_message_id"] = event.message_id


def _delivery_kind(event: SidecarEvent) -> str:
    data = event.data if isinstance(event.data, dict) else {}
    return str(data.get("delivery_kind") or "").strip().lower()


def _safe_profile_id(value: Any) -> str:
    candidate = str(value or "").strip()
    if PROFILE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return "default"


def _render_session_card(request: web.Request, session: CardSession) -> dict[str, Any]:
    card_config = request.app[SESSION_CARD_CONFIGS_KEY].get(
        _session_key_for_session(request.app, session),
        {},
    )
    footer_fields = card_config.get("footer_fields", request.app[FOOTER_FIELDS_KEY])
    if isinstance(footer_fields, list):
        footer_fields = list(footer_fields)
    elif footer_fields is not None:
        footer_fields = request.app[FOOTER_FIELDS_KEY]
    title = card_config.get("title", request.app[CARD_TITLE_KEY])
    if not isinstance(title, str):
        title = request.app[CARD_TITLE_KEY]
    return render_card(
        session,
        footer_fields=footer_fields,
        title=title,
    )


def _session_key_for_session(app: web.Application, session: CardSession) -> str:
    for key, candidate in app[SESSIONS_KEY].items():
        if candidate is session:
            return key
    return session.message_id


def _resolve_session_card_config(
    app: web.Application, bot_id: str | None, event: SidecarEvent
) -> dict[str, Any]:
    base_card = app[BASE_CARD_CONFIG_KEY]
    profile_card = event.data.get("card", {}) if isinstance(event.data, dict) else {}
    actual_bot_id = bot_id
    feishu_client = app[FEISHU_CLIENT_KEY]
    if isinstance(feishu_client, dict):
        profile_id = "default"
        if isinstance(bot_id, str) and ":" in bot_id:
            profile_id, actual_bot_id = bot_id.split(":", 1)
        factory = feishu_client.get(profile_id) or feishu_client.get("default")
        if factory is not None:
            return _card_config_for_client(factory, actual_bot_id, base_card, profile_card)
        return dict(base_card)
    return _card_config_for_client(feishu_client, actual_bot_id, base_card, profile_card)


def _card_config_for_client(
    feishu_client: Any,
    bot_id: str | None,
    base_card: dict[str, Any],
    profile_card: dict[str, Any],
) -> dict[str, Any]:
    resolver = getattr(feishu_client, "card_config_for_bot", None)
    if callable(resolver) and bot_id:
        try:
            return resolver(bot_id, base_card=base_card, profile_card=profile_card)
        except Exception:
            return dict(base_card)
    resolved = dict(base_card)
    if isinstance(profile_card, dict):
        resolved.update(profile_card)
    return resolved


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

    # 记录当前 profile_id（多 profile 模式下需要注入到 route.bot_id）
    current_profile_id: str | None = None

    # Multi-profile: select profile-specific factory
    if isinstance(feishu_client, dict):
        raw_profile_id = event.data.get("profile_id") if isinstance(event.data, dict) else None
        current_profile_id = _safe_profile_id(raw_profile_id)
        factory = feishu_client.get(current_profile_id) or feishu_client.get("default")
        if factory is None:
            diagnostics["last_route_error"] = f"no factory for profile {current_profile_id}"
            return None
        feishu_client = factory

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
    # 多 profile 模式：将 profile_id 注入 bot_id，以便 _client_for_bot 正确路由
    if current_profile_id is not None:
        route = RouteResult(f"{current_profile_id}:{route.bot_id}", route.reason)
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
    # Multi-profile: feishu_client is a dict keyed by profile -> factory
    if isinstance(feishu_client, dict):
        if bot_id is None:
            # Use default profile's default bot
            factory = feishu_client.get("default")
            if factory is None:
                raise RuntimeError("no default profile factory")
            return factory.get_client("default")
        # bot_id format: "profile_id:bot_id" or just "bot_id"
        if ":" in str(bot_id):
            profile_id, actual_bot_id = str(bot_id).split(":", 1)
        else:
            profile_id, actual_bot_id = "default", str(bot_id)
        factory = feishu_client.get(profile_id)
        if factory is None:
            raise RuntimeError(f"no factory for profile {profile_id}")
        return factory.get_client(actual_bot_id)

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
    previous = last_update_at.get(_session_key(event))
    if previous is None:
        return True
    return time.monotonic() - previous >= UPDATE_MIN_INTERVAL_SECONDS


def _update_delay_seconds(last_update_at: Dict[str, float], event: SidecarEvent) -> float:
    if event.event not in TERMINAL_EVENTS:
        return 0.0
    previous = last_update_at.get(_session_key(event))
    if previous is None:
        return 0.0
    return max(0.0, UPDATE_MIN_INTERVAL_SECONDS - (time.monotonic() - previous))
