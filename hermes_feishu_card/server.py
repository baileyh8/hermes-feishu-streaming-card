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
INTERACTION_RESULTS_KEY = web.AppKey("interaction_results", dict)
MESSAGE_BOT_IDS_KEY = web.AppKey("message_bot_ids", dict)
SESSION_CARD_CONFIGS_KEY = web.AppKey("session_card_configs", dict)
UPDATE_TASKS_KEY = web.AppKey("update_tasks", dict)
PENDING_UPDATE_REQUESTS_KEY = web.AppKey("pending_update_requests", dict)
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
UPDATE_MIN_INTERVAL_SECONDS = 0.2
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
    app[INTERACTION_RESULTS_KEY] = {}
    app[MESSAGE_BOT_IDS_KEY] = {}
    app[SESSION_CARD_CONFIGS_KEY] = {}
    app[UPDATE_TASKS_KEY] = {}
    app[PENDING_UPDATE_REQUESTS_KEY] = {}
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
    app.router.add_get("/interactions/{interaction_id}", _interaction_result)
    app.router.add_post("/card/actions", _card_actions)
    app.router.add_post("/events", _events)
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


async def _interaction_result(request: web.Request) -> web.Response:
    results: Dict[str, dict[str, Any]] = request.app[INTERACTION_RESULTS_KEY]
    result = results.get(request.match_info["interaction_id"])
    if result is None:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    return web.json_response({"ok": True, **result})


async def _card_actions(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)

    value = _extract_action_value(payload)
    interaction_id = str(value.get("interaction_id") or "").strip()
    token = str(value.get("token") or "").strip()
    choice = str(value.get("choice") or "").strip()
    choice_label = str(value.get("choice_label") or choice).strip()
    if not interaction_id or not token or not choice:
        return web.json_response({"ok": False, "error": "invalid action"}, status=400)

    callback_chat_id = _extract_callback_chat_id(payload)
    found = _find_session_by_interaction(request.app, interaction_id, token, callback_chat_id)
    if found is None:
        return web.json_response({"ok": False, "error": "interaction not found"}, status=404)
    session_key, session = found
    user_name = _extract_operator_name(payload)
    data = {
        "interaction_id": interaction_id,
        "choice": choice,
        "choice_label": choice_label,
        "user_name": user_name,
    }
    if ":" in session_key:
        data["profile_id"] = session_key.split(":", 1)[0]
    event = SidecarEvent(
        schema_version="1",
        event="interaction.completed",
        conversation_id=session.conversation_id,
        message_id=session.message_id,
        chat_id=session.chat_id,
        platform="feishu",
        sequence=session.last_sequence + 1,
        created_at=time.time(),
        data=data,
    )

    message_locks: Dict[str, asyncio.Lock] = request.app[MESSAGE_LOCKS_KEY]
    lock = message_locks.setdefault(session_key, asyncio.Lock())
    async with lock:
        response, post_lock_task = await _apply_event_locked(request, event)
    if post_lock_task is not None:
        await post_lock_task
    if response.status >= 400:
        return response
    return web.json_response(
        {
            "ok": True,
            "toast": {"type": "success", "content": "已选择"},
            "card": _render_session_card(request, session),
        }
    )


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
    if post_lock_task is not None and _should_await_card_update(event):
        await post_lock_task
    return response


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


def _thread_id_for_event(event: SidecarEvent) -> str | None:
    """Return a Feishu thread id from sidecar conversation metadata."""
    raw_thread = event.conversation_id if event.conversation_id != event.chat_id else None
    if isinstance(raw_thread, str) and raw_thread.startswith(("omt_", "om_")):
        return raw_thread
    return None


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
    pending_update_requests: Dict[str, bool] = request.app[PENDING_UPDATE_REQUESTS_KEY]
    _record_profile_diagnostics(request.app, event)
    _record_attachment_diagnostics(request.app, event)
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
                thread_id=_thread_id_for_event(event),
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
        if event.event == "interaction.requested":
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
                    thread_id=_thread_id_for_event(event),
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
                _store_interaction_result(request.app, session)
                metrics.events_applied += 1
            else:
                metrics.events_ignored += 1
            return web.json_response({"ok": True, "applied": applied}), None
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
                    thread_id=_thread_id_for_event(event),
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
    if applied and event.event.startswith("interaction."):
        _store_interaction_result(request.app, session)
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
        session_key = _session_key(event)
        previous_task = update_tasks.get(session_key)
        is_terminal = event.event in TERMINAL_EVENTS
        has_pending_update = previous_task is not None and not previous_task.done()
        should_update = _should_update_card(last_update_at, event)
        if should_update and has_pending_update and not is_terminal:
            pending_update_requests[session_key] = True
            should_update = False
        if should_update:
            if is_terminal:
                pending_update_requests.pop(session_key, None)
            # 锁内立即标记，防止后续事件在API完成前重复触发更新
            last_update_at[session_key] = time.monotonic()
            card = _render_session_card(request, session)
            bot_id = message_bot_ids.get(_session_key(event))

            async def _do_update():
                if is_terminal:
                    delay = _update_delay_seconds(last_update_at, event)
                    if delay > 0:
                        await asyncio.sleep(delay)
                updated = await _update_card_for_app(request.app, feishu_message_id, card, bot_id)
                if not updated and is_terminal:
                    await _retry_terminal_update(request.app, feishu_message_id, card, bot_id)

            async def _queued_update():
                if previous_task is not None:
                    try:
                        await previous_task
                    except Exception:
                        logger.exception("previous Feishu card update task failed")
                try:
                    await _do_update()
                    while (
                        not is_terminal
                        and update_tasks.get(session_key) is current_task
                        and pending_update_requests.pop(session_key, None) is not None
                    ):
                        latest_session = sessions.get(session_key)
                        if latest_session is None or latest_session.status in {"completed", "failed"}:
                            break
                        last_update_at[session_key] = time.monotonic()
                        latest_card = _render_session_card(request, latest_session)
                        await _update_card_for_app(
                            request.app,
                            feishu_message_id,
                            latest_card,
                            bot_id,
                        )
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


def _store_interaction_result(app: web.Application, session: CardSession) -> None:
    interaction = session.active_interaction
    if interaction is None:
        return
    app[INTERACTION_RESULTS_KEY][interaction.interaction_id] = {
        "interaction_id": interaction.interaction_id,
        "status": interaction.status,
        "choice": interaction.choice,
        "choice_label": interaction.choice_label,
    }


def _extract_action_value(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event") if isinstance(payload, dict) else None
    action = event.get("action") if isinstance(event, dict) else None
    value = action.get("value") if isinstance(action, dict) else None
    if isinstance(value, dict):
        return value
    action = payload.get("action") if isinstance(payload, dict) else None
    value = action.get("value") if isinstance(action, dict) else None
    return value if isinstance(value, dict) else {}


def _extract_callback_chat_id(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload, dict) else None
    context = event.get("context") if isinstance(event, dict) else None
    if isinstance(context, dict):
        return str(context.get("open_chat_id") or context.get("chat_id") or "").strip()
    return ""


def _extract_operator_name(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload, dict) else None
    operator = event.get("operator") if isinstance(event, dict) else None
    if not isinstance(operator, dict):
        return ""
    return str(
        operator.get("name")
        or operator.get("open_id")
        or operator.get("user_id")
        or ""
    ).strip()


def _find_session_by_interaction(
    app: web.Application,
    interaction_id: str,
    token: str,
    callback_chat_id: str,
) -> tuple[str, CardSession] | None:
    for session_key, session in app[SESSIONS_KEY].items():
        interaction = session.active_interaction
        if interaction is None:
            continue
        if interaction.interaction_id != interaction_id:
            continue
        if interaction.callback_token != token:
            return None
        if callback_chat_id and callback_chat_id != session.chat_id:
            return None
        return str(session_key), session
    return None


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


def _record_attachment_diagnostics(app: web.Application, event: SidecarEvent) -> None:
    data = event.data if isinstance(event.data, dict) else {}
    attachments = data.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return
    app[DIAGNOSTICS_KEY]["last_attachment_event"] = {
        "message_id": event.message_id,
        "event": event.event,
        "attachment_count": len(
            [item for item in attachments if isinstance(item, dict)]
        ),
        "native_delivery": "allowed",
    }


def _delivery_kind(event: SidecarEvent) -> str:
    data = event.data if isinstance(event.data, dict) else {}
    return str(data.get("delivery_kind") or "").strip().lower()


def _should_await_card_update(event: SidecarEvent) -> bool:
    return event.event in TERMINAL_EVENTS


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
    request: web.Request,
    chat_id: str,
    card: dict[str, Any],
    bot_id: str | None,
    thread_id: str | None = None,
) -> str | None:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    metrics.feishu_send_attempts += 1
    try:
        message_id = await _client_for_bot(request.app, bot_id).send_card(
            chat_id,
            card,
            thread_id=thread_id,
        )
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
            error = f"no factory for profile {current_profile_id}"
            diagnostics["last_route_error"] = error
            _record_profile_route_error(diagnostics, current_profile_id, error)
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
        if current_profile_id is not None:
            _record_profile_route_error(diagnostics, current_profile_id, safe_error)
        return None

    route_diagnostics = {
        "message_id": event.message_id,
        "chat_id": event.chat_id,
        "bot_id": route.bot_id,
        "reason": route.reason,
    }
    if current_profile_id is not None:
        route_diagnostics["profile_id"] = current_profile_id
    diagnostics["last_route"] = route_diagnostics
    diagnostics["last_route_error"] = ""
    app_diagnostics["last_route_error"] = ""
    if current_profile_id is not None:
        _record_profile_route_success(diagnostics, current_profile_id, route_diagnostics)
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
    if isinstance(feishu_client, dict):
        profiles: dict[str, Any] = {}
        total_bots = 0
        total_bindings = 0
        for profile_id, factory in sorted(feishu_client.items()):
            profile_diagnostics = _routing_diagnostics_for_factory(factory)
            profiles[str(profile_id)] = profile_diagnostics
            bot_count = profile_diagnostics.get("bot_count")
            chat_binding_count = profile_diagnostics.get("chat_binding_count")
            if isinstance(bot_count, int):
                total_bots += bot_count
            if isinstance(chat_binding_count, int):
                total_bindings += chat_binding_count
        diagnostics.update(
            {
                "profile_count": len(profiles),
                "bot_count": total_bots,
                "chat_binding_count": total_bindings,
                "profiles": profiles,
            }
        )
        return diagnostics
    diagnostics.update(_routing_diagnostics_for_factory(feishu_client))
    return diagnostics


def _routing_diagnostics_for_factory(feishu_client: Any) -> dict[str, Any]:
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


def _record_profile_route_success(
    diagnostics: dict[str, Any], profile_id: str, route: dict[str, Any]
) -> None:
    profiles = diagnostics.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        return
    profile = profiles.setdefault(
        profile_id,
        {
            "default_bot": "",
            "bot_count": 0,
            "chat_binding_count": 0,
            "last_route": {},
            "last_route_error": "",
        },
    )
    if not isinstance(profile, dict):
        return
    profile["last_route"] = dict(route)
    profile["last_route_error"] = ""


def _record_profile_route_error(
    diagnostics: dict[str, Any], profile_id: str, error: str
) -> None:
    profiles = diagnostics.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        return
    profile = profiles.setdefault(
        profile_id,
        {
            "default_bot": "",
            "bot_count": 0,
            "chat_binding_count": 0,
            "last_route": {},
            "last_route_error": "",
        },
    )
    if not isinstance(profile, dict):
        return
    profile["last_route"] = {}
    profile["last_route_error"] = error


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
    if event.event in {"interaction.completed", "interaction.failed"}:
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
