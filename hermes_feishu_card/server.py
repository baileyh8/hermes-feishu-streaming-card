from __future__ import annotations

import hashlib
import os
import time
import asyncio
import logging
import re
from typing import Any, Dict

from aiohttp import web

from .bots import RouteResult
from .events import EventValidationError, SidecarEvent
from .flush import FlushController
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
BOT_ROUTER_KEY = web.AppKey("bot_router", Any)
ROUTING_DIAGNOSTICS_KEY = web.AppKey("routing_diagnostics", dict)
PROFILE_DIAGNOSTICS_KEY = web.AppKey("profile_diagnostics", dict)
PROCESS_TOKEN_KEY = web.AppKey("process_token", str)
METRICS_KEY = web.AppKey("metrics", SidecarMetrics)
MESSAGE_LOCKS_KEY = web.AppKey("message_locks", dict)
FOOTER_FIELDS_KEY = web.AppKey("footer_fields", Any)
CARD_TITLE_KEY = web.AppKey("card_title", str)
BASE_CARD_CONFIG_KEY = web.AppKey("base_card_config", dict)
FLUSH_CONTROLLERS_KEY = web.AppKey("flush_controllers", dict)
UPDATE_MAX_ATTEMPTS = 3
UPDATE_MIN_INTERVAL_SECONDS = 0.2
TERMINAL_EVENTS = {"message.completed", "message.failed"}
SESSION_CREATING_EVENTS = {
    "thinking.delta",
    "tool.updated",
    "answer.delta",
    "message.completed",
    "message.failed",
    "interaction.requested",
}
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
    app[BOT_ROUTER_KEY] = bot_router
    app[PROCESS_TOKEN_KEY] = process_token
    app[METRICS_KEY] = SidecarMetrics()
    app[MESSAGE_LOCKS_KEY] = {}
    app[FLUSH_CONTROLLERS_KEY] = {}
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
    app.router.add_post("/commands", _commands)
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
            "last_lookup": _sanitize_health_diagnostics(diagnostics.get("last_reply_lookup", {})),
        },
        "cron": {
            "cards_sent": metrics.cron_cards_sent,
            "fallbacks": metrics.cron_fallbacks,
        },
        "sessions": {
            _diagnostic_id_hash(message_id): {
                "status": session.status,
                "last_sequence": session.last_sequence,
                "answer_chars": len(session.answer_text),
                "thinking_chars": len(session.thinking_text),
                "tool_count": session.tool_count,
            }
            for message_id, session in sessions.items()
        },
        "diagnostics": _sanitize_health_diagnostics(diagnostics),
        "routing": _sanitize_health_diagnostics(request.app[ROUTING_DIAGNOSTICS_KEY]),
        "profile_diagnostics": _sanitize_health_diagnostics(request.app[PROFILE_DIAGNOSTICS_KEY]),
    }
    process_token = request.app[PROCESS_TOKEN_KEY]
    if process_token:
        response["process_token_hash"] = _full_diagnostic_hash(process_token)

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
                    _diagnostic_id_hash(key.replace(f"{profile_id}:", "")): {
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


async def _commands(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "error": "payload must be an object"}, status=400)

    command = _normalize_hfc_command(payload.get("command"))
    chat_id = _safe_command_string(payload.get("chat_id"))
    message_id = _safe_command_string(payload.get("message_id"))
    reply_to_message_id = _safe_command_string(payload.get("reply_to_message_id"))
    thread_id = _safe_command_string(payload.get("thread_id"))
    if not chat_id or not message_id:
        return web.json_response(
            {"ok": False, "error": "chat_id and message_id are required"},
            status=400,
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    for key in ("profile_id", "profile_source"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            data[key] = value.strip()
    event = SidecarEvent(
        schema_version="1",
        event="message.started",
        conversation_id=thread_id or chat_id,
        message_id=message_id,
        chat_id=chat_id,
        thread_id=thread_id,
        platform="feishu",
        sequence=0,
        created_at=time.time(),
        data=data,
    )
    route = _resolve_route(request, event)
    card = _render_hfc_command_card(request, command, event, route)
    feishu_message_id = await _send_card(
        request,
        chat_id,
        card,
        route.bot_id if route is not None else None,
        thread_id=thread_id or None,
        reply_to_message_id=reply_to_message_id or message_id,
    )
    if feishu_message_id is None:
        return web.json_response({"ok": False, "error": "send failed"}, status=502)
    return web.json_response({"ok": True, "handled": True, "command": command})


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


def _normalize_hfc_command(value: Any) -> str:
    command = str(value or "").strip().lower()
    if command in {"status", "doctor", "monitor"}:
        return command
    return "help"


def _safe_command_string(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _render_hfc_command_card(
    request: web.Request,
    command: str,
    event: SidecarEvent,
    route: RouteResult | None,
) -> dict[str, Any]:
    lines = _hfc_command_lines(request, command, event, route)
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": f"/hfc {command}"},
        },
        "header": {
            "template": "blue" if command != "doctor" else "green",
            "title": {"tag": "plain_text", "content": request.app[CARD_TITLE_KEY]},
            "subtitle": {"tag": "plain_text", "content": f"/hfc {command}"},
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "element_id": f"hfc_{command}",
                    "content": "\n".join(lines),
                }
            ]
        },
    }


def _hfc_command_lines(
    request: web.Request,
    command: str,
    event: SidecarEvent,
    route: RouteResult | None,
) -> list[str]:
    if command == "status":
        return _hfc_status_lines(request, event, route)
    if command == "doctor":
        return _hfc_doctor_lines(request, event, route)
    if command == "monitor":
        return _hfc_monitor_lines(request, event)
    return [
        "**Hermes Feishu Card 诊断命令**",
        "",
        "- `/hfc help`: 查看只读命令列表",
        "- `/hfc status`: 查看 sidecar、会话和路由摘要",
        "- `/hfc doctor`: 查看安装/运行健康检查摘要",
        "- `/hfc monitor`: 查看流式更新与飞书发送指标",
        "",
        *_hfc_context_lines(event, route),
    ]


def _hfc_status_lines(
    request: web.Request,
    event: SidecarEvent,
    route: RouteResult | None,
) -> list[str]:
    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    return [
        "**/hfc status**",
        "",
        f"- sidecar: healthy",
        f"- active_sessions: {len(sessions)}",
        f"- events_received: {metrics.events_received}",
        f"- events_applied: {metrics.events_applied}",
        f"- feishu_send_successes: {metrics.feishu_send_successes}",
        f"- update_queue_peak: {metrics.update_queue_peak}",
        *_hfc_context_lines(event, route),
    ]


def _hfc_doctor_lines(
    request: web.Request,
    event: SidecarEvent,
    route: RouteResult | None,
) -> list[str]:
    diagnostics = request.app[DIAGNOSTICS_KEY]
    routing = request.app[ROUTING_DIAGNOSTICS_KEY]
    last_update_error = str(diagnostics.get("last_update_error") or "")
    last_route_error = str(diagnostics.get("last_route_error") or "")
    return [
        "**/hfc doctor**",
        "",
        f"- sidecar: healthy",
        f"- routing: {'ok' if not last_route_error else 'warning'}",
        f"- last_route_error: {last_route_error or 'none'}",
        f"- last_update_error: {last_update_error or 'none'}",
        f"- configured_bots: {routing.get('bot_count', 0)}",
        f"- chat_bindings: {routing.get('chat_binding_count', 0)}",
        *_hfc_context_lines(event, route),
    ]


def _hfc_monitor_lines(request: web.Request, event: SidecarEvent) -> list[str]:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    snapshot = metrics.snapshot()
    keys = (
        "events_received",
        "events_applied",
        "events_ignored",
        "events_rejected",
        "update_scheduled",
        "update_coalesced",
        "update_queue_peak",
        "terminal_drains",
        "terminal_drain_timeouts",
        "feishu_send_attempts",
        "feishu_send_successes",
        "feishu_send_failures",
        "feishu_update_attempts",
        "feishu_update_successes",
        "feishu_update_failures",
        "feishu_update_retries",
    )
    lines = ["**/hfc monitor**", ""]
    lines.extend([f"- {key}: {snapshot.get(key, 0)}" for key in keys])
    lines.append(f"- active_sessions: {len(request.app[SESSIONS_KEY])}")
    lines.extend(_hfc_context_lines(event, None))
    return lines


def _hfc_context_lines(event: SidecarEvent, route: RouteResult | None) -> list[str]:
    lines = [
        "",
        "**上下文**",
        f"- chat_id_hash: {_diagnostic_id_hash(event.chat_id)}",
        f"- message_id_hash: {_diagnostic_id_hash(event.message_id)}",
    ]
    thread_hash = _diagnostic_id_hash(event.thread_id)
    if thread_hash:
        lines.append(f"- thread_id_hash: {thread_hash}")
    if route is not None:
        lines.append(f"- route: {route.reason}")
        if route.bot_id:
            lines.append(f"- bot_id: {route.bot_id}")
    return lines


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
    data = event.data if isinstance(event.data, dict) else {}
    raw_thread = (
        event.thread_id
        or data.get("thread_id")
        or (event.conversation_id if event.conversation_id != event.chat_id else "")
    )
    if isinstance(raw_thread, str) and raw_thread.startswith(("omt_", "om_")):
        return raw_thread
    return None


def _reply_to_message_id_for_event(event: SidecarEvent) -> str | None:
    data = event.data if isinstance(event.data, dict) else {}
    reply_to = data.get("reply_to_message_id")
    if isinstance(reply_to, str) and reply_to.startswith("om_"):
        return reply_to
    if _thread_id_for_event(event) and event.message_id.startswith("om_"):
        return event.message_id
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
    _record_profile_diagnostics(request.app, event)
    _record_attachment_diagnostics(request.app, event)
    session = sessions.get(_session_key(event))

    if _skip_native_text_fallback_interaction(request.app, event):
        metrics.events_ignored += 1
        return web.json_response(
            {
                "ok": True,
                "applied": False,
                "interaction_mode": _interaction_mode_for_session_key(
                    request.app,
                    _session_key(event),
                ),
            }
        ), None

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
                reply_to_message_id=_reply_to_message_id_for_event(event),
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
        if event.event in SESSION_CREATING_EVENTS:
            session = CardSession(
                conversation_id=event.conversation_id,
                message_id=event.message_id,
                chat_id=event.chat_id,
            )
            sessions[_session_key(event)] = session
            applied = session.apply(event)
            if applied:
                is_cron_completed = (
                    event.event == "message.completed" and _delivery_kind(event) == "cron"
                )
                route = _resolve_route(request, event)
                if route is None:
                    sessions.pop(_session_key(event), None)
                    if is_cron_completed:
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
                    reply_to_message_id=_reply_to_message_id_for_event(event),
                )
                if message_id is None:
                    sessions.pop(_session_key(event), None)
                    request.app[SESSION_CARD_CONFIGS_KEY].pop(_session_key(event), None)
                    if is_cron_completed:
                        metrics.cron_fallbacks += 1
                    metrics.events_rejected += 1
                    return web.json_response(
                        {"ok": False, "error": "feishu send failed"},
                        status=502,
                    ), None
                feishu_message_ids[_session_key(event)] = message_id
                message_bot_ids[_session_key(event)] = route.bot_id
                if event.event == "interaction.requested":
                    _store_interaction_result(request.app, session)
                if event.event in TERMINAL_EVENTS:
                    _store_card_summary(request.app, event, session, message_id)
                    request.app[DIAGNOSTICS_KEY]["last_terminal_event"] = {
                        "message_id_hash": _diagnostic_id_hash(event.message_id),
                        "event": event.event,
                        "sequence": event.sequence,
                        "applied": applied,
                        "session_status": session.status,
                        "answer_chars": len(session.answer_text),
                    }
                if is_cron_completed:
                    metrics.cron_cards_sent += 1
                metrics.events_applied += 1
            else:
                metrics.events_ignored += 1
            response_payload = {"ok": True, "applied": applied}
            if event.event == "interaction.requested":
                response_payload["interaction_mode"] = _interaction_mode_for_session_key(
                    request.app,
                    _session_key(event),
                )
            return web.json_response(response_payload), None
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
            "message_id_hash": _diagnostic_id_hash(event.message_id),
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
        is_terminal = event.event in TERMINAL_EVENTS
        controller = _flush_controller_for_session(request.app, session_key)
        bot_id = message_bot_ids.get(session_key)

        async def _render_and_update() -> bool:
            latest_session = sessions.get(session_key)
            if latest_session is None:
                return False
            latest_card = _render_session_card(request, latest_session)
            updated = await _update_card_for_app(
                request.app,
                feishu_message_id,
                latest_card,
                bot_id,
            )
            if not updated and is_terminal:
                await _retry_terminal_update(
                    request.app,
                    feishu_message_id,
                    latest_card,
                    bot_id,
                )
            return updated

        if is_terminal:
            await controller.drain(_final_drain_timeout_seconds(request.app, session_key))
            current_task = controller.schedule(_render_and_update, terminal=True)
            controller.close()
        else:
            current_task = controller.schedule(_render_and_update, terminal=False)
        post_lock_task = current_task
    if applied:
        metrics.events_applied += 1
    else:
        metrics.events_ignored += 1
    response_payload = {"ok": True, "applied": applied}
    if event.event == "interaction.requested":
        response_payload["interaction_mode"] = _interaction_mode_for_session_key(
            request.app,
            _session_key(event),
        )
    return web.json_response(response_payload), post_lock_task


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
        "chat_id_hash": _diagnostic_id_hash(event.chat_id),
        "message_id_hash": _diagnostic_id_hash(feishu_message_id),
        "source_message_id_hash": _diagnostic_id_hash(event.message_id),
    }


def _record_profile_diagnostics(app: web.Application, event: SidecarEvent) -> None:
    data = event.data if isinstance(event.data, dict) else {}
    profile_id = _safe_profile_id(data.get("profile_id"))
    source = str(data.get("profile_source") or "")
    diagnostics = app[PROFILE_DIAGNOSTICS_KEY].setdefault(
        profile_id,
        {"events": 0, "last_profile_source": "", "last_message_id_hash": ""},
    )
    diagnostics["events"] += 1
    diagnostics["last_profile_source"] = source
    diagnostics["last_message_id_hash"] = _diagnostic_id_hash(event.message_id)


def _record_attachment_diagnostics(app: web.Application, event: SidecarEvent) -> None:
    data = event.data if isinstance(event.data, dict) else {}
    attachments = data.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return
    app[DIAGNOSTICS_KEY]["last_attachment_event"] = {
        "message_id_hash": _diagnostic_id_hash(event.message_id),
        "event": event.event,
        "attachment_count": len(
            [item for item in attachments if isinstance(item, dict)]
        ),
        "native_delivery": "allowed",
    }


def _delivery_kind(event: SidecarEvent) -> str:
    data = event.data if isinstance(event.data, dict) else {}
    return str(data.get("delivery_kind") or "").strip().lower()


def _skip_native_text_fallback_interaction(
    app: web.Application,
    event: SidecarEvent,
) -> bool:
    if event.event != "interaction.requested":
        return False
    data = event.data if isinstance(event.data, dict) else {}
    fallback_policy = str(data.get("fallback_policy") or "").strip().lower()
    if fallback_policy != "native_text":
        return False
    return _interaction_mode_for_session_key(app, _session_key(event)) == "text"


def _should_await_card_update(event: SidecarEvent) -> bool:
    # Hermes uses the /events response to decide whether to suppress native text.
    # Slow Feishu PATCH calls must not keep terminal events waiting.
    return False


def _safe_profile_id(value: Any) -> str:
    candidate = str(value or "").strip()
    if PROFILE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return "default"


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _safe_non_negative_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        return default
    return default


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
    interaction_mode = _interaction_mode_for_session_key(
        request.app,
        _session_key_for_session(request.app, session),
    )
    return render_card(
        session,
        footer_fields=footer_fields,
        title=title,
        interaction_mode=interaction_mode,
        show_reasoning=_safe_bool(card_config.get("show_reasoning"), True),
        timeline_position=str(card_config.get("timeline_position", "top") or "top").strip(),
        timeline_expanded=_safe_bool(card_config.get("timeline_expanded"), False),
        max_timeline_items=_safe_positive_int(
            card_config.get("max_timeline_items"), 12
        ),
        max_reasoning_chars=_safe_positive_int(
            card_config.get("max_reasoning_chars"), 1200
        ),
        max_tool_result_chars=_safe_positive_int(
            card_config.get("max_tool_result_chars"), 600
        ),
    )


def _interaction_mode_for_session_key(app: web.Application, session_key: str) -> str:
    card_config = app[SESSION_CARD_CONFIGS_KEY].get(session_key, {})
    raw_mode = card_config.get(
        "interaction_mode",
        app[BASE_CARD_CONFIG_KEY].get("interaction_mode", "callback"),
    )
    mode = str(raw_mode or "").strip().lower()
    if mode in {"text", "markdown", "reply"}:
        return "text"
    return "callback"


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
    reply_to_message_id: str | None = None,
) -> str | None:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    metrics.feishu_send_attempts += 1
    try:
        message_id = await _client_for_bot(request.app, bot_id).send_card(
            chat_id,
            card,
            thread_id=thread_id,
            reply_to_message_id=reply_to_message_id,
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
        started_at = time.monotonic()
        try:
            await _client_for_bot(app, bot_id).update_card_message(message_id, card)
        except Exception as exc:
            metrics.feishu_update_latency_ms = int(
                (time.monotonic() - started_at) * 1000
            )
            message = _safe_update_error_message(bot_id, exc)
            app[DIAGNOSTICS_KEY]["last_update_error"] = message[:500]
            logger.warning("Feishu card update failed: %s", message)
            metrics.feishu_update_failures += 1
            continue
        metrics.feishu_update_latency_ms = int((time.monotonic() - started_at) * 1000)
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


def _flush_controller_for_session(
    app: web.Application, session_key: str
) -> FlushController:
    controllers: Dict[str, FlushController] = app[FLUSH_CONTROLLERS_KEY]
    controller = controllers.get(session_key)
    if controller is not None:
        return controller
    card_config = app[SESSION_CARD_CONFIGS_KEY].get(session_key, app[BASE_CARD_CONFIG_KEY])
    default_interval_ms = max(0, int(UPDATE_MIN_INTERVAL_SECONDS * 1000))
    interval_ms = _safe_non_negative_int(
        card_config.get("flush_interval_ms"),
        default_interval_ms,
    )
    controller = FlushController(
        interval_seconds=interval_ms / 1000.0,
        metrics=app[METRICS_KEY],
    )
    controllers[session_key] = controller
    return controller


def _final_drain_timeout_seconds(app: web.Application, session_key: str) -> float:
    card_config = app[SESSION_CARD_CONFIGS_KEY].get(session_key, app[BASE_CARD_CONFIG_KEY])
    timeout_ms = _safe_non_negative_int(
        card_config.get("final_drain_timeout_ms"),
        900,
    )
    return timeout_ms / 1000.0


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
            "message_id_hash": _diagnostic_id_hash(event.message_id),
            "chat_id_hash": _diagnostic_id_hash(event.chat_id),
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
        "message_id_hash": _diagnostic_id_hash(event.message_id),
        "chat_id_hash": _diagnostic_id_hash(event.chat_id),
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


def _sanitize_health_diagnostics(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered.endswith("_hash"):
                sanitized[key_text] = item
                continue
            if _health_key_should_redact(lowered):
                continue
            if _health_key_should_hash(lowered):
                sanitized[f"{key_text}_hash"] = _diagnostic_id_hash(item)
                continue
            sanitized[key_text] = _sanitize_health_diagnostics(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_health_diagnostics(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_health_diagnostics(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _health_key_should_redact(key: str) -> bool:
    return any(part in key for part in ("secret", "token", "password"))


def _health_key_should_hash(key: str) -> bool:
    return any(part in key for part in ("chat_id", "open_id", "message_id"))


def _diagnostic_id_hash(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _full_diagnostic_hash(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _would_apply(session: CardSession, event: SidecarEvent) -> bool:
    return (
        event.conversation_id == session.conversation_id
        and event.message_id == session.message_id
        and event.chat_id == session.chat_id
        and event.sequence > session.last_sequence
        and session.status not in {"completed", "failed"}
    )
