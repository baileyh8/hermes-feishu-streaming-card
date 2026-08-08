from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from contextlib import suppress
from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import asyncio
import logging
import re
from typing import Any, Callable, Dict

from aiohttp import web

from .bots import RouteResult
from .config import load_config, merge_card_config, resolve_operations_hermes_root
from .delivery_policy import (
    CARD_DISPOSITION,
    ChatDeliveryDecision,
    ChatDeliveryPolicy,
    NATIVE_DISPOSITION,
)
from .diagnostics import DiagnosticFinding, DiagnosticReport, build_diagnostic_report
from .events import EventValidationError, SidecarEvent
from .event_auth import (
    EventAuthenticationError,
    EventProofVerifier,
    NativeHandoffAckAuthenticationError,
    NativeHandoffAckProofVerifier,
    NativeHandoffRecoveryAuthenticationError,
    NativeHandoffRecoveryProofVerifier,
    PolicyAuthenticationError,
    PolicyProofVerifier,
    is_loopback_host,
)
from .flush import FlushController
from .feishu_client import FeishuAPIError, build_delivery_uuid
from .lifecycle import (
    cleanup_closed_controller,
    cleanup_orphan_message_lock,
    cleanup_runtime_state,
)
from .metrics import SidecarMetrics
from .native_handoff import (
    NativeHandoffRecord,
    NativeHandoffStore,
    NativeHandoffStoreError,
    derive_native_handoff_content_hash,
    derive_native_handoff_target_hash,
    derive_native_handoff_uuid_seed,
    handoff_identity_key,
    is_exact_native_text_scope,
)
from .operations import (
    OperationRecord,
    OperationRejected,
    OperationStore,
    render_operations_card,
)
from .operations_transport import (
    CommandProofVerifier,
    TransportAuthenticationError,
    derive_operation_transport_secret,
)
from .profile_sources import PROFILE_SOURCE_FALLBACK, PROFILE_SOURCES
from .render import (
    CardRenderResult,
    _is_initial_loading,
    render_card_result,
    render_terminal_limit_handoff_card,
)
from .process import state_dir
from .session import CardSession
from .status import StatusConfig
from .subscription_usage import fetch_codex_subscription_usage
from .install.detect import HermesDetection, detect_hermes
from .install.integrity import IntegrityRepairRefused, plan_integrity_repair
from .install.recovery import execute_recovery, plan_recovery
from .runtime_control import (
    RUNTIME_HOOK_GENERATION,
    RuntimeControlEvent,
    RuntimeControlValidationError,
    RuntimeIntegritySupervisor,
    RuntimeProofVerifier,
)
from .integrity import RuntimeIntegrityCoordinator, sanitize_integrity_snapshot
from .maintenance_card import (
    render_update_inspection_card,
    render_update_job_card,
    render_update_operation_card,
)
from .maintenance_process import inspect_runtime, launch_job
from .maintenance_store import (
    MaintenanceRefused,
    PROXY_ENVIRONMENT_KEYS,
    create_job,
    discard_job_credentials,
    discard_unstarted_job,
    load_active_drain_lease,
    load_job,
    load_verified_artifact,
    maintenance_paths,
    release_drain_lease,
    reserve_drain_lease,
    sanitize_job_environment,
    stage_job_credentials,
    transition_job,
)
from .maintenance_update import (
    UpdateInspection,
    inspect_update,
    set_gateway_external_drain,
)

FEISHU_CLIENT_KEY = web.AppKey("feishu_client", Any)
SESSIONS_KEY = web.AppKey("sessions", dict)
FEISHU_MESSAGE_IDS_KEY = web.AppKey("feishu_message_ids", dict)
SESSION_ALIASES_KEY = web.AppKey("session_aliases", dict)
CARD_SUMMARIES_KEY = web.AppKey("card_summaries", dict)
CARD_SUMMARY_SESSION_KEYS_KEY = web.AppKey("card_summary_session_keys", dict)
INTERACTION_RESULTS_KEY = web.AppKey("interaction_results", dict)
INTERACTION_RESULT_SESSION_KEYS_KEY = web.AppKey(
    "interaction_result_session_keys", dict
)
MESSAGE_BOT_IDS_KEY = web.AppKey("message_bot_ids", dict)
SESSION_CARD_CONFIGS_KEY = web.AppKey("session_card_configs", dict)
BOT_ROUTER_KEY = web.AppKey("bot_router", Any)
ROUTING_DIAGNOSTICS_KEY = web.AppKey("routing_diagnostics", dict)
PROFILE_DIAGNOSTICS_KEY = web.AppKey("profile_diagnostics", dict)
PROCESS_TOKEN_KEY = web.AppKey("process_token", str)
PACKAGE_VERSION_KEY = web.AppKey("package_version", str)
PYTHON_IDENTITY_KEY = web.AppKey("python_identity", str)
SHUTDOWN_CALLBACK_KEY = web.AppKey("shutdown_callback", Any)
METRICS_KEY = web.AppKey("metrics", SidecarMetrics)
NOOP_MODE_KEY = web.AppKey("noop_mode", bool)
EVENT_AUTH_REQUIRED_KEY = web.AppKey("event_auth_required", bool)
EVENT_AUTH_VERIFIER_KEY = web.AppKey("event_auth_verifier", EventProofVerifier)
RUNTIME_AUTH_VERIFIER_KEY = web.AppKey("runtime_auth_verifier", RuntimeProofVerifier)
RUNTIME_INTEGRITY_SUPERVISOR_KEY = web.AppKey(
    "runtime_integrity_supervisor", RuntimeIntegritySupervisor
)
RUNTIME_INTEGRITY_COORDINATOR_KEY = web.AppKey(
    "runtime_integrity_coordinator", RuntimeIntegrityCoordinator
)
RUNTIME_INTEGRITY_TASK_KEY = web.AppKey("runtime_integrity_task", asyncio.Task)
DELIVERY_POLICY_KEY = web.AppKey("delivery_policy", Any)
POLICY_AUTH_VERIFIER_KEY = web.AppKey("policy_auth_verifier", PolicyProofVerifier)
NATIVE_HANDOFF_ACK_AUTH_VERIFIER_KEY = web.AppKey(
    "native_handoff_ack_auth_verifier", NativeHandoffAckProofVerifier
)
NATIVE_HANDOFF_RECOVERY_AUTH_VERIFIER_KEY = web.AppKey(
    "native_handoff_recovery_auth_verifier", NativeHandoffRecoveryProofVerifier
)
MESSAGE_LOCKS_KEY = web.AppKey("message_locks", dict)
MESSAGE_LOCK_USERS_KEY = web.AppKey("message_lock_users", dict)
FOOTER_FIELDS_KEY = web.AppKey("footer_fields", Any)
CARD_TITLE_KEY = web.AppKey("card_title", str)
BASE_CARD_CONFIG_KEY = web.AppKey("base_card_config", dict)
OPERATIONS_STORE_KEY = web.AppKey("operations_store", OperationStore)
OPERATIONS_CONFIG_PATH_KEY = web.AppKey("operations_config_path", Path)
OPERATIONS_ENV_FILE_KEY = web.AppKey("operations_env_file", Any)
OPERATIONS_HERMES_ROOT_KEY = web.AppKey("operations_hermes_root", Path)
OPERATIONS_DELIVERIES_KEY = web.AppKey("operations_deliveries", dict)
OPERATIONS_COMMAND_AUTH_KEY = web.AppKey(
    "operations_command_auth", CommandProofVerifier
)
OPERATIONS_TRANSPORT_ROOT_KEY = web.AppKey("operations_transport_root", bytes)
OPERATIONS_DIAGNOSTIC_TASKS_KEY = web.AppKey("operations_diagnostic_tasks", set)
OPERATIONS_DIAGNOSTIC_SEMAPHORE_KEY = web.AppKey(
    "operations_diagnostic_semaphore", Any
)
OPERATIONS_DIAGNOSTIC_EXECUTOR_KEY = web.AppKey(
    "operations_diagnostic_executor", ThreadPoolExecutor
)
OPERATIONS_DIAGNOSTIC_FUTURES_KEY = web.AppKey("operations_diagnostic_futures", set)
OPERATIONS_MUTATION_EXECUTOR_KEY = web.AppKey("operations_mutation_executor", ThreadPoolExecutor)
OPERATIONS_MUTATION_FUTURES_KEY = web.AppKey("operations_mutation_futures", set)
OPERATIONS_MUTATIONS_STOPPING_KEY = web.AppKey("operations_mutations_stopping", bool)
OPERATIONS_PUBLISH_LOCKS_KEY = web.AppKey("operations_publish_locks", dict)
OPERATIONS_PUBLISH_LOCKS_GUARD_KEY = web.AppKey("operations_publish_locks_guard", Any)
FLUSH_CONTROLLERS_KEY = web.AppKey("flush_controllers", dict)
CARD_ANIMATION_TASKS_KEY = web.AppKey("card_animation_tasks", dict)
CLEANUP_TASK_KEY = web.AppKey("cleanup_task", asyncio.Task)
NATIVE_HANDOFF_STORE_KEY = web.AppKey(
    "native_handoff_store", NativeHandoffStore
)
NATIVE_HANDOFF_REPAIR_TASKS_KEY = web.AppKey(
    "native_handoff_repair_tasks", set
)
NATIVE_HANDOFF_CURRENT_REPAIRS_KEY = web.AppKey(
    "native_handoff_current_repairs", dict
)
UPDATE_MAX_ATTEMPTS = 3
UPDATE_MIN_INTERVAL_SECONDS = 0.2
CARD_ANIMATION_INTERVAL_SECONDS = 0.8
CARD_ANIMATION_MAX_UPDATES = 15
_CARD_ANIMATION_SLEEP = asyncio.sleep
RUNTIME_CLEANUP_INTERVAL_SECONDS = 60.0
RUNTIME_INTEGRITY_STARTUP_GRACE_SECONDS = 30.0
RUNTIME_INTEGRITY_CHECK_INTERVAL_SECONDS = 15.0
MAX_OPERATION_DELIVERIES = 200
MAX_STALE_OPERATIONS_REPUBLISHES = 1
MAX_CONCURRENT_OPERATION_DIAGNOSTICS = 4
OPERATIONS_DIAGNOSTIC_TIMEOUT_SECONDS = 12.0
RESTART_CALLBACK_GRACE_SECONDS = 0.25
_STABLE_PROFILE_SOURCES = PROFILE_SOURCES
TERMINAL_EVENTS = {"message.completed", "message.failed"}
TURN_REOPENING_EVENTS = {"thinking.delta", "tool.updated", "answer.delta"}
SESSION_CREATING_EVENTS = {
    "thinking.delta",
    "tool.updated",
    "answer.delta",
    "message.completed",
    "message.failed",
    "system.notice",
    "interaction.requested",
}
DIAGNOSTICS_KEY = web.AppKey("diagnostics", dict)
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CardDeliveryResult:
    message_id: str | None
    outcome: str
    retry_count: int = 0
    error_kind: str = ""

    @property
    def delivered(self) -> bool:
        return self.outcome == "delivered" and bool(self.message_id)


class _OperationsDiagnosticCapacityError(RuntimeError):
    pass


class _AfterEofJsonResponse(web.Response):
    def __init__(
        self,
        data: dict[str, object],
        after_eof: Any,
        *,
        status: int = 200,
        after_eof_on_error: bool = True,
    ):
        super().__init__(
            body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            status=status,
        )
        self._after_eof = after_eof
        self._after_eof_on_error = after_eof_on_error

    async def write_eof(self, data: bytes = b"") -> None:
        after_eof = self._after_eof
        self._after_eof = None
        try:
            await super().write_eof(data)
        except BaseException:
            if self._after_eof_on_error and callable(after_eof):
                try:
                    after_eof()
                except Exception:
                    logger.warning(
                        "HFC after-EOF callback failed while response closed",
                        exc_info=True,
                    )
            raise
        if callable(after_eof):
            after_eof()


def create_app(
    feishu_client: Any,
    process_token: str = "",
    package_version: str = "",
    python_identity: str = "",
    card_config: dict[str, Any] | None = None,
    bot_router: Any = None,
    operations_config_path: str | Path | None = None,
    operations_env_file: str | Path | None = None,
    operations_hermes_root: str | Path | None = None,
    operations_transport_root_secret: bytes | None = None,
    event_auth_required: bool = False,
    noop_mode: bool = False,
    integrity_mode: str = "notify",
    expected_runtime_package_version: str = "",
    runtime_integrity_state_directory: str | Path | None = None,
    delivery_policy: Any = None,
    native_handoff_store: NativeHandoffStore | None = None,
    shutdown_callback: Callable[[], None] | None = None,
) -> web.Application:
    valid_transport_root = (
        isinstance(operations_transport_root_secret, bytes)
        and len(operations_transport_root_secret) == 32
    )
    if event_auth_required and not valid_transport_root:
        raise ValueError("event authentication requires a private transport root")
    app = web.Application()
    card_config = card_config or {}
    app[FEISHU_CLIENT_KEY] = feishu_client
    app[SESSIONS_KEY] = {}
    app[FEISHU_MESSAGE_IDS_KEY] = {}
    app[SESSION_ALIASES_KEY] = {}
    # TODO: replace this short-lived in-process index with bounded shared storage.
    app[CARD_SUMMARIES_KEY] = {}
    app[CARD_SUMMARY_SESSION_KEYS_KEY] = {}
    app[INTERACTION_RESULTS_KEY] = {}
    app[INTERACTION_RESULT_SESSION_KEYS_KEY] = {}
    app[MESSAGE_BOT_IDS_KEY] = {}
    app[SESSION_CARD_CONFIGS_KEY] = {}
    app[BOT_ROUTER_KEY] = bot_router
    app[PROCESS_TOKEN_KEY] = process_token
    app[PACKAGE_VERSION_KEY] = str(package_version)
    app[PYTHON_IDENTITY_KEY] = str(python_identity)
    app[SHUTDOWN_CALLBACK_KEY] = shutdown_callback
    app[METRICS_KEY] = SidecarMetrics()
    app[NOOP_MODE_KEY] = bool(noop_mode)
    app[EVENT_AUTH_REQUIRED_KEY] = bool(event_auth_required)
    if event_auth_required:
        app[EVENT_AUTH_VERIFIER_KEY] = EventProofVerifier(
            operations_transport_root_secret
        )
    runtime_supervisor = RuntimeIntegritySupervisor(
        mode=integrity_mode,
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version=expected_runtime_package_version,
        state_directory=runtime_integrity_state_directory,
    )
    app[RUNTIME_INTEGRITY_SUPERVISOR_KEY] = runtime_supervisor
    if valid_transport_root:
        app[RUNTIME_AUTH_VERIFIER_KEY] = RuntimeProofVerifier(
            operations_transport_root_secret
        )
    else:
        runtime_supervisor.mark_control_auth_unavailable()
    app[DELIVERY_POLICY_KEY] = (
        delivery_policy if delivery_policy is not None else ChatDeliveryPolicy()
    )
    if valid_transport_root:
        app[POLICY_AUTH_VERIFIER_KEY] = PolicyProofVerifier(
            operations_transport_root_secret
        )
        app[NATIVE_HANDOFF_ACK_AUTH_VERIFIER_KEY] = NativeHandoffAckProofVerifier(
            operations_transport_root_secret
        )
        app[NATIVE_HANDOFF_RECOVERY_AUTH_VERIFIER_KEY] = (
            NativeHandoffRecoveryProofVerifier(operations_transport_root_secret)
        )
    app[MESSAGE_LOCKS_KEY] = {}
    app[MESSAGE_LOCK_USERS_KEY] = {}
    app[FLUSH_CONTROLLERS_KEY] = {}
    app[CARD_ANIMATION_TASKS_KEY] = {}
    app[NATIVE_HANDOFF_STORE_KEY] = (
        native_handoff_store
        if native_handoff_store is not None
        else NativeHandoffStore(state_dir())
    )
    app[NATIVE_HANDOFF_REPAIR_TASKS_KEY] = set()
    app[NATIVE_HANDOFF_CURRENT_REPAIRS_KEY] = {}
    app[DIAGNOSTICS_KEY] = {
        "last_update_error": "",
        "last_route_error": "",
        "last_terminal_event": {},
    }
    app[ROUTING_DIAGNOSTICS_KEY] = _initial_routing_diagnostics(feishu_client)
    app[PROFILE_DIAGNOSTICS_KEY] = {}
    app[BASE_CARD_CONFIG_KEY] = dict(card_config)
    app[OPERATIONS_STORE_KEY] = OperationStore(secret=secrets.token_bytes(32))
    app[OPERATIONS_DIAGNOSTIC_TASKS_KEY] = set()
    app[OPERATIONS_DIAGNOSTIC_SEMAPHORE_KEY] = {"value": None}
    app[OPERATIONS_DIAGNOSTIC_EXECUTOR_KEY] = ThreadPoolExecutor(
        max_workers=MAX_CONCURRENT_OPERATION_DIAGNOSTICS,
        thread_name_prefix="hfc-operations",
    )
    app[OPERATIONS_DIAGNOSTIC_FUTURES_KEY] = set()
    app[OPERATIONS_MUTATION_EXECUTOR_KEY] = ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="hfc-operations-mutation"
    )
    app[OPERATIONS_MUTATION_FUTURES_KEY] = set()
    app[OPERATIONS_MUTATIONS_STOPPING_KEY] = {"stopping": False}
    app[OPERATIONS_PUBLISH_LOCKS_KEY] = {}
    app[OPERATIONS_PUBLISH_LOCKS_GUARD_KEY] = {"value": None}
    operations_config = Path(
        operations_config_path
        or os.environ.get("HFC_CONFIG")
        or Path.home() / ".hermes_feishu_card" / "config.yaml"
    ).expanduser()
    app[OPERATIONS_CONFIG_PATH_KEY] = operations_config
    app[OPERATIONS_ENV_FILE_KEY] = (
        Path(operations_env_file).expanduser()
        if operations_env_file is not None
        else None
    )
    app[OPERATIONS_HERMES_ROOT_KEY] = resolve_operations_hermes_root(
        operations_hermes_root, config_path=operations_config
    )
    app[RUNTIME_INTEGRITY_COORDINATOR_KEY] = RuntimeIntegrityCoordinator(
        mode=integrity_mode,
        hermes_root=app[OPERATIONS_HERMES_ROOT_KEY],
        supervisor=runtime_supervisor,
    )
    app[OPERATIONS_DELIVERIES_KEY] = {}
    if valid_transport_root:
        app[OPERATIONS_TRANSPORT_ROOT_KEY] = operations_transport_root_secret
        app[OPERATIONS_COMMAND_AUTH_KEY] = CommandProofVerifier(
            operations_transport_root_secret
        )
    footer_fields = card_config.get("footer_fields")
    app[FOOTER_FIELDS_KEY] = list(footer_fields) if isinstance(footer_fields, list) else None
    title = card_config.get("title")
    app[CARD_TITLE_KEY] = title if isinstance(title, str) else "Hermes Agent"
    app.router.add_get("/health", _health)
    app.router.add_post("/control/shutdown", _control_shutdown)
    app.router.add_get("/messages/{message_id}/summary", _message_summary)
    app.router.add_get("/interactions/{interaction_id}", _interaction_result)
    app.router.add_post("/card/actions", _card_actions)
    app.router.add_post("/commands", _commands)
    app.router.add_post("/runtime/events", _runtime_events)
    app.router.add_post("/delivery/policy", _delivery_policy)
    app.router.add_post("/native-handoff/ack", _native_handoff_ack)
    app.router.add_post("/native-handoff/recover", _native_handoff_recover)
    app.router.add_post("/events", _events)
    app.on_startup.append(_start_runtime_cleanup)
    app.on_startup.append(_start_runtime_integrity_monitor)
    app.on_cleanup.append(_stop_operations_diagnostics)
    app.on_cleanup.append(_stop_card_animations)
    app.on_cleanup.append(_stop_native_handoff_repairs)
    app.on_cleanup.append(_stop_runtime_cleanup)
    app.on_cleanup.append(_stop_runtime_integrity_monitor)
    return app


async def _start_runtime_cleanup(app: web.Application) -> None:
    task = app.get(CLEANUP_TASK_KEY)
    if task is None or task.done():
        app[CLEANUP_TASK_KEY] = asyncio.create_task(_runtime_cleanup_loop(app))


async def _stop_runtime_cleanup(app: web.Application) -> None:
    task = app.get(CLEANUP_TASK_KEY)
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _start_runtime_integrity_monitor(app: web.Application) -> None:
    coordinator = app[RUNTIME_INTEGRITY_COORDINATOR_KEY]
    if coordinator.mode == "off":
        return
    task = app.get(RUNTIME_INTEGRITY_TASK_KEY)
    if task is None or task.done():
        app[RUNTIME_INTEGRITY_TASK_KEY] = asyncio.create_task(
            _runtime_integrity_monitor_loop(app)
        )


async def _stop_runtime_integrity_monitor(app: web.Application) -> None:
    task = app.get(RUNTIME_INTEGRITY_TASK_KEY)
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _runtime_integrity_monitor_loop(app: web.Application) -> None:
    await asyncio.sleep(RUNTIME_INTEGRITY_STARTUP_GRACE_SECONDS)
    last_reported: tuple[str, str] | None = None
    while True:
        coordinator = app[RUNTIME_INTEGRITY_COORDINATOR_KEY]
        try:
            await asyncio.to_thread(coordinator.check_once)
        except Exception:
            logger.warning(
                "HFC runtime integrity check failed; manual diagnosis is required"
            )
            await asyncio.sleep(RUNTIME_INTEGRITY_CHECK_INTERVAL_SECONDS)
            continue
        snapshot = coordinator.snapshot()
        metrics = app[METRICS_KEY]
        metrics.integrity_repair_attempts = snapshot["repair_attempts"]
        metrics.integrity_repair_successes = snapshot["repair_successes"]
        metrics.integrity_repair_refusals = snapshot["repair_refusals"]
        current = (str(snapshot["last_status"]), str(snapshot["last_reason"]))
        if current != last_reported:
            log = logger.info if current[0] in {"ready", "disabled"} else logger.warning
            log(
                "HFC runtime integrity status=%s reason=%s",
                current[0],
                current[1],
            )
            last_reported = current
        await asyncio.sleep(RUNTIME_INTEGRITY_CHECK_INTERVAL_SECONDS)


async def _stop_card_animations(app: web.Application) -> None:
    tasks = list(app[CARD_ANIMATION_TASKS_KEY].values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    app[CARD_ANIMATION_TASKS_KEY].clear()


async def _stop_native_handoff_repairs(app: web.Application) -> None:
    tasks = list(app[NATIVE_HANDOFF_REPAIR_TASKS_KEY])
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    app[NATIVE_HANDOFF_REPAIR_TASKS_KEY].clear()
    app[NATIVE_HANDOFF_CURRENT_REPAIRS_KEY].clear()


async def _stop_operations_diagnostics(app: web.Application) -> None:
    app[OPERATIONS_MUTATIONS_STOPPING_KEY]["stopping"] = True
    mutation_futures = list(app[OPERATIONS_MUTATION_FUTURES_KEY])
    for future in mutation_futures:
        future.cancel()
    tasks = list(app[OPERATIONS_DIAGNOSTIC_TASKS_KEY])
    if not mutation_futures:
        for task in tasks:
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    app[OPERATIONS_DIAGNOSTIC_TASKS_KEY].clear()
    for future in app[OPERATIONS_DIAGNOSTIC_FUTURES_KEY]:
        future.cancel()
    app[OPERATIONS_DIAGNOSTIC_EXECUTOR_KEY].shutdown(wait=True, cancel_futures=True)
    app[OPERATIONS_MUTATION_EXECUTOR_KEY].shutdown(wait=True, cancel_futures=True)
    app[OPERATIONS_DIAGNOSTIC_FUTURES_KEY].clear()
    app[OPERATIONS_MUTATION_FUTURES_KEY].clear()


async def _runtime_cleanup_loop(app: web.Application) -> None:
    while True:
        await _cleanup_sleep(RUNTIME_CLEANUP_INTERVAL_SECONDS)
        cleanup_runtime_state(app, time.time())


async def _cleanup_sleep(delay: float) -> None:
    await asyncio.sleep(delay)


async def _health(request: web.Request) -> web.Response:
    sessions: Dict[str, CardSession] = request.app[SESSIONS_KEY]
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    diagnostics = request.app[DIAGNOSTICS_KEY]
    readiness = request.app[RUNTIME_INTEGRITY_SUPERVISOR_KEY].snapshot()
    try:
        drain_lease = load_active_drain_lease(maintenance_paths())
        maintenance_drain = {
            "active": drain_lease is not None,
            "owner_hash": (
                _diagnostic_id_hash(drain_lease.owner_id)
                if drain_lease is not None
                else ""
            ),
            "valid": True,
        }
    except MaintenanceRefused:
        maintenance_drain = {"active": True, "owner_hash": "", "valid": False}
    response = {
        "status": "degraded" if request.app[NOOP_MODE_KEY] else "healthy",
        "noop_mode": request.app[NOOP_MODE_KEY],
        "delivery": {"mode": "noop" if request.app[NOOP_MODE_KEY] else "live"},
        "event_auth_required": request.app[EVENT_AUTH_REQUIRED_KEY],
        "readiness": readiness,
        "integrity": sanitize_integrity_snapshot(
            request.app[RUNTIME_INTEGRITY_COORDINATOR_KEY].snapshot()
        ),
        "active_sessions": len(sessions),
        "maintenance_active_sessions": sum(
            1
            for session in sessions.values()
            if str(getattr(session, "status", "") or "").lower()
            not in {"completed", "failed", "cancelled"}
        ),
        "gateway_active_sessions": readiness.get("active_sessions"),
        "maintenance_drain": maintenance_drain,
        "process_pid": os.getpid(),
        "package_version": request.app[PACKAGE_VERSION_KEY],
        "python_identity": request.app[PYTHON_IDENTITY_KEY],
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
        "delivery_policy": _safe_delivery_policy_diagnostics(request.app),
        "native_handoffs": _safe_native_handoff_diagnostics(request.app),
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


async def _control_shutdown(request: web.Request) -> web.Response:
    expected = request.app[PROCESS_TOKEN_KEY]
    supplied = request.headers.get("X-HFC-Process-Token", "")
    callback = request.app[SHUTDOWN_CALLBACK_KEY]
    if (
        not request.remote
        or not is_loopback_host(request.remote)
        or not expected
        or not supplied
        or not secrets.compare_digest(supplied, expected)
        or not callable(callback)
    ):
        return web.json_response(
            {"ok": False, "error": "forbidden"},
            status=403,
        )
    return _AfterEofJsonResponse(
        {"ok": True, "status": "stopping"},
        callback,
        status=202,
        after_eof_on_error=False,
    )


def _safe_delivery_policy_diagnostics(app: web.Application) -> dict[str, Any]:
    try:
        diagnostics = app[DELIVERY_POLICY_KEY].safe_diagnostics()
    except Exception:
        return {"status": "unavailable"}
    if not isinstance(diagnostics, dict):
        return {"status": "unavailable"}
    return _sanitize_health_diagnostics(diagnostics)


def _safe_native_handoff_diagnostics(app: web.Application) -> dict[str, Any]:
    try:
        diagnostics = app[NATIVE_HANDOFF_STORE_KEY].safe_status()
    except Exception:
        return {"status": "unavailable", "manual_review_required": True}
    return _sanitize_health_diagnostics(diagnostics)


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
    hfc_action = str(value.get("hfc_action") or "").strip()
    if hfc_action == "interaction.noop":
        # Selection components inside a clarify form (e.g. multi_select_static)
        # fire a callback on every local change. There is nothing to do —
        # the real answer arrives with the form submit. Acknowledge quietly
        # so the client doesn't surface an error toast.
        return web.json_response({"ok": True})
    if hfc_action == "operations.select":
        try:
            return await _operations_action(request, payload, value)
        except (_OperationsDiagnosticCapacityError, asyncio.TimeoutError):
            return web.json_response(
                {"ok": False, "error": "operations unavailable"}, status=503
            )
    if not hfc_action:
        # Form-submit buttons carry NO behaviors (Feishu ignores callbacks on
        # form_action_type=submit buttons), so the interaction is identified
        # via the button name: hfc_confirm_<interaction_id> / hfc_other_<id>.
        form_parsed = _parse_form_action_name(payload)
        if form_parsed is not None:
            mode, interaction_id = form_parsed
            return await _interaction_action(
                request, payload, value, form_mode=mode, form_interaction_id=interaction_id
            )
    return await _interaction_action(request, payload, value)


def _parse_form_action_name(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Identify a clarify form submit from the button name.

    Form-submit buttons cannot carry behaviors callbacks, so render.py
    encodes the interaction id into the button name:
      hfc_confirm_<interaction_id>  (multi-select confirm / combined submit)
      hfc_other_<interaction_id>    (free-text Other submit)
    Returns (mode, interaction_id) or None when the action isn't a clarify
    form submit.
    """
    event = payload.get("event") if isinstance(payload, dict) else None
    action = event.get("action") if isinstance(event, dict) else None
    name = str(action.get("name") or "").strip() if isinstance(action, dict) else ""
    if name.startswith("hfc_confirm_"):
        return "confirm", name[len("hfc_confirm_"):]
    if name.startswith("hfc_other_"):
        return "other", name[len("hfc_other_"):]
    return None


async def _interaction_action(
    request: web.Request,
    payload: dict[str, Any],
    value: dict[str, Any],
    *,
    form_mode: str = "",
    form_interaction_id: str = "",
) -> web.Response:
    interaction_id = str(value.get("interaction_id") or "").strip()
    token = str(value.get("token") or "").strip()
    mode = str(value.get("mode") or form_mode or "").strip()
    if form_interaction_id:
        # Form-submit path: the button carries no behaviors value, so the
        # interaction id comes from the button name and there is no token.
        interaction_id = form_interaction_id
        token = ""
    if not interaction_id:
        return web.json_response({"ok": False, "error": "invalid action"}, status=400)

    callback_chat_id = _extract_callback_chat_id(payload)
    found = _find_session_by_interaction(
        request.app, interaction_id, token, callback_chat_id,
        allow_empty_token=bool(form_interaction_id),
    )
    if found is None:
        return web.json_response({"ok": False, "error": "interaction not found"}, status=404)
    session_key, session = found

    form_value = _extract_form_value(payload)
    if mode == "confirm":
        # Multi-select form submit: action.form_value.hfc_multi is a list of
        # selected option values. If the user ALSO typed a custom answer in
        # hfc_other, both are returned: the typed text is appended as
        # "[自定义] <text>" so the agent sees the selections AND the note.
        # Selections serialize as JSON so the gateway-side
        # clarify_tool._parse_multi_select_response can split reliably.
        typed = str(form_value.get("hfc_other") or "").strip()
        raw = form_value.get("hfc_multi")
        if raw is None:
            raw = []
        selected = raw if isinstance(raw, list) else ([raw] if raw != "" else [])
        selected = [str(s).strip() for s in selected if str(s).strip()]
        if typed:
            if selected:
                combined = selected + [f"[自定义] {typed}"]
                choice = json.dumps(combined, ensure_ascii=False)
                choice_label = ", ".join(combined)
            else:
                choice = typed
                choice_label = typed
        else:
            choice = json.dumps(selected, ensure_ascii=False)
            choice_label = ", ".join(selected) if selected else "(未选择)"
    elif mode == "other":
        # Free-text 'Other' answer: action.form_value.hfc_other holds the
        # user's typed input.
        typed = str(form_value.get("hfc_other") or "").strip()
        if not typed:
            return web.json_response(
                {"ok": False, "error": "empty other answer", "toast": {"type": "warning", "content": "请输入内容后再提交"}},
                status=400,
            )
        choice = typed
        choice_label = typed
    else:
        # Direct single-select button click (legacy path).
        choice = str(value.get("choice") or "").strip()
        choice_label = str(value.get("choice_label") or choice).strip()
        if not choice:
            return web.json_response({"ok": False, "error": "invalid action"}, status=400)

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


async def _operations_action(
    request: web.Request,
    payload: dict[str, Any],
    value: dict[str, Any],
) -> web.Response:
    action = str(value.get("operation_action") or "").strip()
    token = str(value.get("token") or "").strip()
    profile_scope = str(value.get("profile_scope") or "").strip()
    chat_id = _extract_callback_chat_id(payload)
    profile_id = _extract_callback_profile_id(payload)
    operator_open_id = _extract_operator_open_id(payload)
    if not action or not token or not chat_id:
        return web.json_response(
            {"ok": False, "error": "operation rejected"}, status=400
        )

    store: OperationStore = request.app[OPERATIONS_STORE_KEY]
    try:
        transport_proof = payload.get("adapter_transport_proof")
        if not isinstance(transport_proof, dict):
            raise OperationRejected("invalid transport proof")
        timestamp = transport_proof.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise OperationRejected("invalid transport proof")
        authenticated_record = store.verify_transport_proof(
            proof=str(transport_proof.get("signature") or ""),
            token=token,
            action=action,
            callback_chat_id=chat_id,
            callback_profile_id=profile_id,
            callback_profile_scope=profile_scope,
            operator_open_id=operator_open_id,
            timestamp=timestamp,
        )
    except OperationRejected:
        return web.json_response(
            {"ok": False, "error": "operation rejected"}, status=403
        )

    try:
        _claims, record = store.inspect(
            token,
            callback_chat_id=chat_id,
            callback_profile_id=authenticated_record.profile_id,
            callback_profile_scope=profile_scope,
            allow_expired=True,
            allow_recheck_predecessor=action == "recheck",
            allow_successor_predecessor=True,
        )
    except OperationRejected:
        return web.json_response({"ok": False, "error": "operation rejected"})

    if record.kind == "update":
        return _update_operation_action(
            request.app,
            record,
            token=token,
            action=action,
            operator_open_id=operator_open_id,
            chat_id=chat_id,
            evidence_fingerprint=str(
                value.get("update_evidence_fingerprint") or ""
            ).strip(),
        )

    report = _operation_report_snapshot(record)
    successor = store.current_successor(record.operation_id)
    if successor is not None and successor.operation_id != record.operation_id:
        return _operations_response(
            request.app,
            _operation_report_snapshot(successor),
            successor,
            toast="已更新",
        )
    if action == "recheck":
        if record.state in {"preparing", "executing", "restarting"}:
            return _operations_response(
                request.app,
                report,
                record,
                toast="操作进行中",
            )
        try:
            transitioned, created = store.begin_recheck(
                token,
                callback_chat_id=chat_id,
                callback_profile_id=record.profile_id,
                callback_profile_scope=profile_scope,
                callback_report_fingerprint=record.report_fingerprint,
                callback_recovery_fingerprint=record.recovery_fingerprint,
            )
        except OperationRejected:
            return _operations_response(
                request.app, report, record, ok=False, toast="操作不可用"
            )
        if created:
            _transfer_operation_delivery(
                request.app, record.operation_id, transitioned.operation_id
            )
        return _operations_response(
            request.app,
            report,
            transitioned,
            after_eof=(
                lambda: _schedule_operations_recheck(request.app, transitioned)
                if created
                else None
            ),
        )
    try:
        transitioned = store.transition(
            token,
            action=action,
            operator_open_id=operator_open_id,
            callback_chat_id=chat_id,
            callback_profile_id=record.profile_id,
            callback_report_fingerprint=record.report_fingerprint,
            callback_recovery_fingerprint=record.recovery_fingerprint,
        )
    except OperationRejected as exc:
        if str(exc) in {
            "operation expired",
            "diagnosis changed",
            "recovery changed",
        }:
            expired = _successor_operation(
                request.app,
                record,
                report,
                state="expired",
                result={"message": "诊断状态已变化，请重新检测。"},
            )
            return _operations_response(
                request.app,
                report,
                expired,
                ok=False,
                toast="诊断已过期",
            )
        return _operations_response(
            request.app,
            report,
            record,
            ok=action in {"confirm_repair", "confirm_restart"}
            and record.state in {"executing", "restarting"},
            toast=(
                "操作进行中"
                if action in {"confirm_repair", "confirm_restart"}
                and record.state in {"executing", "restarting"}
                else "操作不可用"
            ),
        )

    if action == "details":
        transitioned = store.complete(
            transitioned.operation_id,
            expected_state="diagnosed",
            state="diagnosed",
            result={"show_details": True},
        )
    elif action == "confirm_repair":
        return _operations_response(
            request.app,
            report,
            transitioned,
            after_eof=lambda: _schedule_operations_repair(
                request.app, transitioned
            ),
        )
    elif action == "confirm_restart":
        return _operations_response(
            request.app,
            report,
            transitioned,
            after_eof=lambda: _schedule_operations_restart(
                request.app, transitioned
            ),
        )
    return _operations_response(request.app, report, transitioned)


def _update_operation_action(
    app: web.Application,
    record: OperationRecord,
    *,
    token: str,
    action: str,
    operator_open_id: str,
    chat_id: str,
    evidence_fingerprint: str,
) -> web.Response:
    inspection = record.update_inspection
    if inspection is None:
        return web.json_response(
            {"ok": False, "error": "operation rejected"}, status=409
        )
    store: OperationStore = app[OPERATIONS_STORE_KEY]
    try:
        paths = maintenance_paths()
        transitioned = store.transition_update(
            token,
            action=action,
            operator_open_id=operator_open_id,
            callback_chat_id=chat_id,
            callback_profile_id=record.profile_id,
            callback_evidence_fingerprint=evidence_fingerprint,
            reserve_update=(
                (
                    lambda owner_id: _reserve_update_job_for_operation(
                        app,
                        record,
                        owner_id=owner_id,
                        paths=paths,
                    )
                )
                if action == "confirm_update"
                else None
            ),
        )
    except (OperationRejected, MaintenanceRefused):
        return web.json_response(
            {"ok": False, "error": "operation rejected"}, status=409
        )
    card = render_update_operation_card(
        inspection,
        transitioned.state,
        title=app[CARD_TITLE_KEY],
    )
    return _AfterEofJsonResponse(
        {
            "ok": True,
            "operation_id": transitioned.operation_id,
            "toast": {
                "type": "success",
                "content": (
                    "正在准备更新"
                    if transitioned.state == "locking"
                    else "已取消更新"
                ),
            },
            "card": card,
        },
        lambda: _schedule_update_operation_transition(app, transitioned),
    )


def _reserve_update_job_for_operation(
    app: web.Application,
    operation: OperationRecord,
    *,
    owner_id: str,
    paths,
) -> None:
    inspection = operation.update_inspection
    delivery = app[OPERATIONS_DELIVERIES_KEY].get(operation.operation_id)
    if inspection is None or not inspection.ready or not isinstance(delivery, dict):
        raise MaintenanceRefused("update reservation evidence unavailable")
    message_id = str(delivery.get("message_id") or "").strip()
    if not message_id:
        raise MaintenanceRefused("update reservation delivery unavailable")
    artifact = load_verified_artifact(
        paths,
        expected_version=app[PACKAGE_VERSION_KEY],
    )
    runtime_snapshot = app[RUNTIME_INTEGRITY_SUPERVISOR_KEY].snapshot()
    runtime_evidence_ready, _active = _gateway_runtime_update_evidence(
        runtime_snapshot
    )
    if not runtime_evidence_ready:
        raise MaintenanceRefused("gateway runtime evidence unavailable")
    job = create_job(
        paths,
        hermes_root=app[OPERATIONS_HERMES_ROOT_KEY],
        config_path=app[OPERATIONS_CONFIG_PATH_KEY],
        env_file=app[OPERATIONS_ENV_FILE_KEY],
        profile_id=operation.profile_id,
        chat_id=operation.chat_id,
        card_message_id=message_id,
        operator_hash=hashlib.sha256(
            operation.owner_open_id.encode("utf-8")
        ).hexdigest(),
        pre_update_version=inspection.current_version,
        pre_update_head=inspection.current_head,
        target_fingerprint=inspection.target_fingerprint,
        target_head=inspection.target_head,
        artifact=artifact,
        bot_id=str(delivery.get("bot_id") or "default"),
        job_id=owner_id,
        pre_sidecar_pid=os.getpid(),
        pre_runtime_id_hash=str(runtime_snapshot.get("runtime_id_hash") or ""),
        pre_runtime_sequence=int(runtime_snapshot.get("last_sequence") or 0),
    )
    environment = sanitize_job_environment(os.environ)
    proxy_environment = {
        key: value
        for key, value in environment.items()
        if key in PROXY_ENVIRONMENT_KEYS
    }
    try:
        reserve_drain_lease(paths, owner_id=owner_id)
        if not set_gateway_external_drain(
            app[OPERATIONS_HERMES_ROOT_KEY],
            active=True,
            proxy_environment=proxy_environment,
        ):
            raise MaintenanceRefused("gateway drain unavailable")
        stage_job_credentials(
            paths,
            job_id=owner_id,
            environment=environment,
        )
    except Exception:
        try:
            set_gateway_external_drain(
                app[OPERATIONS_HERMES_ROOT_KEY],
                active=False,
                proxy_environment=proxy_environment,
            )
        except Exception:
            pass
        try:
            release_drain_lease(paths, owner_id=owner_id)
        except Exception:
            pass
        try:
            discard_job_credentials(paths, job_id=owner_id)
        except Exception:
            pass
        try:
            discard_unstarted_job(job.path)
        except Exception:
            pass
        raise


async def _commands(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "error": "payload must be an object"}, status=400)

    command = _normalize_hfc_command(payload.get("command"))
    if command in {"doctor", "update"}:
        verifier = request.app.get(OPERATIONS_COMMAND_AUTH_KEY)
        if verifier is None:
            return web.json_response(
                {"ok": False, "error": "operations authentication unavailable"},
                status=503,
            )
        try:
            verifier.verify(payload)
        except TransportAuthenticationError:
            return web.json_response(
                {"ok": False, "error": "command authentication rejected"},
                status=403,
            )
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
    chat_type = _safe_command_string(payload.get("chat_type")) or _safe_command_string(
        data.get("chat_type")
    )
    if chat_type:
        data["chat_type"] = chat_type
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
    operation_id = ""
    if command == "update":
        profile_id = _safe_profile_id(data.get("profile_id"))
        profile_source = _safe_command_string(data.get("profile_source"))
        operator_open_id = _safe_command_operator(payload.get("operator"))
        if chat_type.lower() not in {"dm", "p2p", "private"} or not operator_open_id:
            return web.json_response(
                {"ok": False, "error": "private operator required"},
                status=403,
            )
        try:
            root_secret = request.app[OPERATIONS_TRANSPORT_ROOT_KEY]
            prepared_operation_id = secrets.token_urlsafe(18)
            operation, created = request.app[OPERATIONS_STORE_KEY].prepare_update(
                chat_id=chat_id,
                profile_id=profile_id,
                initiator_open_id=operator_open_id,
                operation_id=prepared_operation_id,
                transport_secret=derive_operation_transport_secret(
                    root_secret, prepared_operation_id
                ),
                idempotency_key=_update_idempotency_key(
                    chat_id, profile_id, message_id
                ),
            )
        except (KeyError, OperationRejected, ValueError):
            return web.json_response(
                {"ok": False, "error": "operations overloaded"},
                status=503,
            )
        if created:
            _schedule_update_inspection(
                request.app,
                operation,
                bot_id=route.bot_id if route is not None else None,
                thread_id=thread_id or None,
                reply_to_message_id=reply_to_message_id or message_id,
                profile_source=profile_source,
            )
        return web.json_response(
            {
                "ok": True,
                "handled": True,
                "command": command,
                "operation_id": operation.operation_id,
            }
        )
    if command == "doctor":
        profile_id = _safe_profile_id(data.get("profile_id"))
        profile_source = _safe_command_string(data.get("profile_source"))
        try:
            root_secret = request.app[OPERATIONS_TRANSPORT_ROOT_KEY]
            prepared_operation_id = secrets.token_urlsafe(18)
            operation, created = request.app[OPERATIONS_STORE_KEY].prepare(
                chat_id=chat_id,
                profile_id=profile_id,
                group=_is_group_chat(chat_type),
                initiator_open_id=_safe_command_operator(payload.get("operator")),
                operation_id=prepared_operation_id,
                transport_secret=derive_operation_transport_secret(
                    root_secret, prepared_operation_id
                ),
                idempotency_key=_doctor_idempotency_key(
                    chat_id, profile_id, message_id
                ),
            )
        except (KeyError, OperationRejected, ValueError):
            return web.json_response(
                {"ok": False, "error": "operations overloaded"},
                status=503,
            )
        if created:
            _schedule_operations_diagnosis(
                request.app,
                operation,
                bot_id=route.bot_id if route is not None else None,
                thread_id=thread_id or None,
                reply_to_message_id=reply_to_message_id or message_id,
                profile_source=profile_source,
            )
        operation_id = operation.operation_id
        return web.json_response(
            {
                "ok": True,
                "handled": True,
                "command": command,
                "operation_id": operation_id,
            }
        )
    else:
        card = _render_hfc_command_card(request, command, event, route)
    task = asyncio.create_task(
        _send_command_card(
            request.app,
            chat_id,
            card,
            route.bot_id if route is not None else None,
            thread_id=thread_id or None,
            reply_to_message_id=reply_to_message_id or message_id,
            operation_id=operation_id,
        )
    )
    task.add_done_callback(_log_background_task_failure)
    await asyncio.sleep(0)
    response = {"ok": True, "handled": True, "command": command}
    if operation_id:
        response["operation_id"] = operation_id
    return web.json_response(response)


async def _send_command_card(
    app: web.Application,
    chat_id: str,
    card: dict[str, Any],
    bot_id: str | None,
    thread_id: str | None = None,
    reply_to_message_id: str | None = None,
    operation_id: str = "",
) -> str | None:
    delivery = await _send_card_for_app(
        app,
        chat_id,
        card,
        bot_id,
        thread_id=thread_id,
        reply_to_message_id=reply_to_message_id,
        delivery_key=operation_id or reply_to_message_id or "command",
        delivery_kind="command",
    )
    if not delivery.delivered:
        logger.warning(
            "HFC command card send failed: chat_hash=%s bot_hash=%s outcome=%s",
            _diagnostic_id_hash(chat_id),
            _diagnostic_id_hash(bot_id or "default"),
            delivery.outcome,
        )
    elif operation_id:
        _store_operation_delivery(app, operation_id, {
            "message_id": delivery.message_id,
            "bot_id": bot_id,
        })
    return delivery.message_id if delivery.delivered else None


def _log_background_task_failure(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.warning("HFC command card background task failed", exc_info=True)


def _doctor_idempotency_key(chat_id: str, profile_id: str, message_id: str) -> str:
    value = f"doctor\0{chat_id}\0{profile_id}\0{message_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _update_idempotency_key(chat_id: str, profile_id: str, message_id: str) -> str:
    value = f"update\0{chat_id}\0{profile_id}\0{message_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _schedule_update_inspection(
    app: web.Application,
    operation: OperationRecord,
    *,
    bot_id: str | None,
    thread_id: str | None,
    reply_to_message_id: str | None,
    profile_source: str,
) -> None:
    task = asyncio.create_task(
        _run_update_inspection(
            app,
            operation,
            bot_id=bot_id,
            thread_id=thread_id,
            reply_to_message_id=reply_to_message_id,
            profile_source=profile_source,
        )
    )
    _track_operations_task(app, task)


async def _run_update_inspection(
    app: web.Application,
    operation: OperationRecord,
    *,
    bot_id: str | None,
    thread_id: str | None,
    reply_to_message_id: str | None,
    profile_source: str,
) -> None:
    del profile_source
    store: OperationStore = app[OPERATIONS_STORE_KEY]
    try:
        inspection = await _inspect_update_for_app(app)
        diagnosed = store.diagnose_update(operation.operation_id, inspection)
    except asyncio.CancelledError:
        raise
    except Exception:
        inspection = _unavailable_update_inspection(
            app, "maintenance_runtime_unavailable"
        )
        try:
            diagnosed = store.diagnose_update(operation.operation_id, inspection)
        except OperationRejected:
            return
    card = _render_update_inspection_for_app(app, diagnosed, inspection)
    await _send_command_card(
        app,
        diagnosed.chat_id,
        card,
        bot_id,
        thread_id=thread_id,
        reply_to_message_id=reply_to_message_id,
        operation_id=diagnosed.operation_id,
    )


async def _inspect_update_for_app(app: web.Application) -> UpdateInspection:
    paths = maintenance_paths()
    artifact = load_verified_artifact(
        paths,
        expected_version=app[PACKAGE_VERSION_KEY],
    )
    sessions = app[SESSIONS_KEY]
    sidecar_active_sessions = sum(
        1
        for session in sessions.values()
        if str(getattr(session, "status", "") or "").lower()
        not in {"completed", "failed", "cancelled"}
    )
    readiness = app[RUNTIME_INTEGRITY_SUPERVISOR_KEY].snapshot()
    gateway_evidence_ready, gateway_active = _gateway_runtime_update_evidence(
        readiness
    )
    if not gateway_evidence_ready:
        return _unavailable_update_inspection(app, "gateway_runtime_unavailable")
    active_sessions = max(
        sidecar_active_sessions,
        gateway_active,
    )
    runtime = await _run_operations_mutation(
        app,
        inspect_runtime,
        paths,
        artifact,
        hermes_root=app[OPERATIONS_HERMES_ROOT_KEY],
    )
    environment = sanitize_job_environment(os.environ)
    proxy_environment = {
        key: value
        for key, value in environment.items()
        if key in PROXY_ENVIRONMENT_KEYS
    }
    inspection = await _run_operations_mutation(
        app,
        inspect_update,
        hermes_root=app[OPERATIONS_HERMES_ROOT_KEY],
        artifact=artifact,
        installed_hfc_version=app[PACKAGE_VERSION_KEY],
        active_sessions=active_sessions,
        proxy_environment=proxy_environment,
    )
    if not runtime.available:
        return replace(
            inspection,
            ready=False,
            reason_code="maintenance_runtime_unavailable",
            maintenance_ready=False,
        )
    return inspection


def _gateway_runtime_update_evidence(
    readiness: object,
) -> tuple[bool, int]:
    if not isinstance(readiness, dict):
        return False, 0
    runtime_hash = readiness.get("runtime_id_hash")
    sequence = readiness.get("last_sequence")
    active_sessions = readiness.get("active_sessions")
    valid = (
        readiness.get("status") == "ready"
        and isinstance(runtime_hash, str)
        and len(runtime_hash) == 64
        and all(character in "0123456789abcdef" for character in runtime_hash)
        and isinstance(sequence, int)
        and not isinstance(sequence, bool)
        and sequence >= 1
        and isinstance(active_sessions, int)
        and not isinstance(active_sessions, bool)
        and active_sessions >= 0
        and isinstance(readiness.get("admission_draining"), bool)
        and readiness.get("active_work_count_complete") is True
        and readiness.get("drain_home_verified") is True
    )
    return valid, active_sessions if valid else 0


def _unavailable_update_inspection(
    app: web.Application,
    reason_code: str,
) -> UpdateInspection:
    return UpdateInspection(
        ready=False,
        reason_code=reason_code,
        current_version="",
        current_head="",
        target_summary="",
        target_fingerprint="",
        hfc_version=app[PACKAGE_VERSION_KEY],
        artifact_sha256="",
        active_sessions=0,
        requires_drain=False,
        hook_state="",
        hook_fingerprint="",
        maintenance_ready=False,
        changed_paths=(),
        created_at=time.time(),
        target_head="",
    )


def _render_update_inspection_for_app(
    app: web.Application,
    operation: OperationRecord,
    inspection: UpdateInspection,
) -> dict[str, object]:
    def value(action: str) -> dict[str, object]:
        return {
            "hfc_action": "operations.select",
            "operation_action": action,
            "token": app[OPERATIONS_STORE_KEY].token(operation, action),
            "profile_scope": app[OPERATIONS_STORE_KEY].scope_fingerprint(operation),
            "transport_lineage_id": operation.transport_lineage_id,
            "update_evidence_fingerprint": inspection.fingerprint,
        }

    return render_update_inspection_card(
        inspection,
        value("confirm_update"),
        value("cancel_update"),
        title=app[CARD_TITLE_KEY],
    )


def _schedule_update_job(app: web.Application, operation: OperationRecord) -> None:
    _track_operations_task(
        app, asyncio.create_task(_run_update_job_launch(app, operation))
    )


def _schedule_update_operation_transition(
    app: web.Application,
    operation: OperationRecord,
) -> None:
    _track_operations_task(
        app,
        asyncio.create_task(_publish_update_operation_transition(app, operation)),
    )


async def _publish_update_operation_transition(
    app: web.Application,
    operation: OperationRecord,
) -> None:
    try:
        inspection = operation.update_inspection
        delivery = app[OPERATIONS_DELIVERIES_KEY].get(operation.operation_id)
        if inspection is not None and isinstance(delivery, dict):
            message_id = str(delivery.get("message_id") or "").strip()
            if message_id:
                await _update_card_for_app(
                    app,
                    message_id,
                    render_update_operation_card(
                        inspection,
                        operation.state,
                        title=app[CARD_TITLE_KEY],
                    ),
                    delivery.get("bot_id"),
                )
    finally:
        if operation.state == "locking":
            _schedule_update_job(app, operation)


async def _run_update_job_launch(
    app: web.Application,
    operation: OperationRecord,
) -> None:
    if operation.state != "locking" or operation.update_inspection is None:
        return
    delivery = app[OPERATIONS_DELIVERIES_KEY].get(operation.operation_id)
    if not isinstance(delivery, dict):
        _release_update_reservation(app, operation.operation_id)
        return
    message_id = str(delivery.get("message_id") or "").strip()
    if not message_id:
        _release_update_reservation(app, operation.operation_id)
        return
    try:
        paths = maintenance_paths()
        job = load_job(paths.jobs / f"{operation.operation_id}.json")
        current = await _inspect_update_for_app(app)
        if (
            not current.ready
            or current.fingerprint != operation.update_evidence_fingerprint
            or current.current_head != job.pre_update_head
            or current.target_head != job.target_head
            or current.target_fingerprint != job.target_fingerprint
        ):
            raise MaintenanceRefused("update evidence changed")
        artifact = load_verified_artifact(
            paths, expected_version=app[PACKAGE_VERSION_KEY]
        )
        runtime = await _run_operations_mutation(
            app,
            inspect_runtime,
            paths,
            artifact,
            hermes_root=app[OPERATIONS_HERMES_ROOT_KEY],
        )
        if not runtime.available:
            raise MaintenanceRefused("maintenance runtime unavailable")
        launched = await _run_operations_mutation(
            app,
            launch_job,
            runtime,
            job,
        )
        if not launched.started:
            job = transition_job(
                job.path,
                expected_phase="locking",
                phase="failed",
                result={
                    "error_code": launched.reason_code,
                    "recovery_boundary": "before_hermes_update",
                },
            )
            _release_update_reservation(app, operation.operation_id)
        await _update_card_for_app(
            app,
            message_id,
            render_update_job_card(job, title=app[CARD_TITLE_KEY]),
            delivery.get("bot_id"),
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        _fail_reserved_update_job(operation.operation_id)
        _release_update_reservation(app, operation.operation_id)
        unavailable = replace(
            operation.update_inspection,
            ready=False,
            reason_code="maintenance_runtime_unavailable",
        )
        await _update_card_for_app(
            app,
            message_id,
            render_update_inspection_card(
                unavailable,
                {},
                {},
                title=app[CARD_TITLE_KEY],
            ),
            delivery.get("bot_id"),
        )


def _fail_reserved_update_job(operation_id: str) -> None:
    try:
        paths = maintenance_paths()
        job = load_job(paths.jobs / f"{operation_id}.json")
        if job.phase not in {"succeeded", "failed", "cancelled"}:
            transition_job(
                job.path,
                expected_phase=job.phase,
                phase="failed",
                result={
                    "error_code": "preflight_evidence_changed",
                    "recovery_boundary": "no_mutation",
                    "status": "failed",
                },
            )
    except Exception:
        pass


def _release_update_reservation(
    app: web.Application,
    operation_id: str,
) -> None:
    paths = maintenance_paths()
    try:
        set_gateway_external_drain(
            app[OPERATIONS_HERMES_ROOT_KEY],
            active=False,
        )
    except Exception:
        pass
    try:
        release_drain_lease(paths, owner_id=operation_id)
    except Exception:
        pass
    try:
        discard_job_credentials(paths, job_id=operation_id)
    except Exception:
        pass


def _schedule_operations_diagnosis(
    app: web.Application,
    operation: OperationRecord,
    *,
    bot_id: str | None,
    thread_id: str | None,
    reply_to_message_id: str | None,
    profile_source: str,
) -> None:
    task = asyncio.create_task(
        _run_operations_diagnosis(
            app,
            operation,
            bot_id=bot_id,
            thread_id=thread_id,
            reply_to_message_id=reply_to_message_id,
            profile_source=profile_source,
        )
    )
    _track_operations_task(app, task)


def _track_operations_task(app: web.Application, task: asyncio.Task[None]) -> None:
    if app[OPERATIONS_MUTATIONS_STOPPING_KEY]["stopping"]:
        task.cancel()
        return
    tasks = app[OPERATIONS_DIAGNOSTIC_TASKS_KEY]
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    task.add_done_callback(_log_background_task_failure)


async def _run_operations_diagnosis(
    app: web.Application,
    operation: OperationRecord,
    *,
    bot_id: str | None,
    thread_id: str | None,
    reply_to_message_id: str | None,
    profile_source: str,
) -> None:
    store: OperationStore = app[OPERATIONS_STORE_KEY]
    if not store.is_preparing(operation.operation_id):
        return
    try:
        report, _detection = await _bounded_operations_report(
            app,
            profile_id=operation.profile_id,
            profile_source=profile_source,
            preparing_operation_id=operation.operation_id,
        )
        diagnosed = store.diagnose(operation.operation_id, report=report)
        message_id = await _send_command_card(
            app,
            operation.chat_id,
            _render_operations_for_app(app, report, diagnosed),
            bot_id,
            thread_id=thread_id,
            reply_to_message_id=reply_to_message_id,
            operation_id=operation.operation_id,
        )
        if message_id is not None:
            return
    except asyncio.CancelledError:
        raise
    except Exception:
        pass

    failed_report = _failed_operations_report(operation.profile_id)
    failed = _mark_operations_diagnosis_failed(
        store, operation.operation_id, report=failed_report
    )
    if failed is None:
        return
    await _send_command_card(
        app,
        failed.chat_id,
        _render_operations_for_app(
            app, failed_report, failed
        ),
        bot_id,
        thread_id=thread_id,
        reply_to_message_id=reply_to_message_id,
        operation_id=failed.operation_id,
    )


def _mark_operations_diagnosis_failed(
    store: OperationStore,
    operation_id: str,
    *,
    report: DiagnosticReport | None = None,
) -> OperationRecord | None:
    for expected_state in ("preparing", "diagnosed"):
        try:
            failed = store.complete(
                operation_id,
                expected_state=expected_state,
                state="failed",
                result={"message": "诊断暂时不可用，请稍后重新检测。"},
            )
            if report is not None:
                failed.report = report
                failed.report_fingerprint = report.fingerprint
                failed.recovery_fingerprint = report.recovery_fingerprint
            return failed
        except OperationRejected:
            continue
    return None


def _failed_operations_report(profile_id: str) -> DiagnosticReport:
    return DiagnosticReport(
        status="error",
        created_at=time.time(),
        config={"loaded": False},
        hermes={"checked": False, "status": "unavailable"},
        streaming={"status": "not_checked"},
        install_state={"status": "unavailable", "recovery_executable": False},
        routing={"profile_id": profile_id},
        runtime={},
        findings=(
            DiagnosticFinding(
                code="operations_diagnosis_failed",
                severity="error",
                message="Operations diagnosis could not be completed.",
            ),
        ),
    )


def _operations_report_available(report: DiagnosticReport) -> bool:
    return not any(
        finding.code == "operations_diagnosis_failed"
        for finding in report.findings
    )


async def _build_operations_report(
    app: web.Application,
    *,
    profile_id: str,
    profile_source: str,
    preparing_operation_id: str = "",
) -> tuple[DiagnosticReport, HermesDetection]:
    routing = app[ROUTING_DIAGNOSTICS_KEY]
    last_route = routing.get("last_route") if isinstance(routing, dict) else None
    health = {
        "status": "degraded" if app[NOOP_MODE_KEY] else "healthy",
        "routing": {"last_route": dict(last_route or {})},
        "readiness": app[RUNTIME_INTEGRITY_SUPERVISOR_KEY].snapshot(),
        "integrity": app[RUNTIME_INTEGRITY_COORDINATOR_KEY].snapshot(),
        "metrics": app[METRICS_KEY].snapshot(),
    }
    async with _operations_diagnostic_semaphore(app):
        if preparing_operation_id and not app[OPERATIONS_STORE_KEY].is_preparing(
            preparing_operation_id
        ):
            raise OperationRejected("operation state changed")
        futures = app[OPERATIONS_DIAGNOSTIC_FUTURES_KEY]
        if len(futures) >= MAX_CONCURRENT_OPERATION_DIAGNOSTICS:
            raise _OperationsDiagnosticCapacityError("operations diagnostics busy")
        future = asyncio.get_running_loop().run_in_executor(
            app[OPERATIONS_DIAGNOSTIC_EXECUTOR_KEY],
            _build_operations_report_sync,
            app[OPERATIONS_CONFIG_PATH_KEY],
            app[OPERATIONS_HERMES_ROOT_KEY],
            profile_id,
            profile_source,
            health,
            app[OPERATIONS_ENV_FILE_KEY],
        )
        futures.add(future)
        future.add_done_callback(futures.discard)
        return await asyncio.shield(future)


def _operations_diagnostic_semaphore(app: web.Application) -> asyncio.Semaphore:
    holder = app[OPERATIONS_DIAGNOSTIC_SEMAPHORE_KEY]
    semaphore = holder["value"]
    if semaphore is None:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_OPERATION_DIAGNOSTICS)
        holder["value"] = semaphore
    return semaphore


async def _bounded_operations_report(
    app: web.Application,
    *,
    profile_id: str,
    profile_source: str,
    preparing_operation_id: str = "",
) -> tuple[DiagnosticReport, HermesDetection]:
    return await asyncio.wait_for(
        _build_operations_report(
            app,
            profile_id=profile_id,
            profile_source=profile_source,
            preparing_operation_id=preparing_operation_id,
        ),
        timeout=OPERATIONS_DIAGNOSTIC_TIMEOUT_SECONDS,
    )


def _build_operations_report_sync(
    config_path: Path,
    hermes_root: Path,
    profile_id: str,
    profile_source: str,
    health: dict[str, object],
    env_file: Path | None = None,
) -> tuple[DiagnosticReport, HermesDetection]:
    detection = detect_hermes(hermes_root)
    try:
        config = (
            load_config(config_path, env_file=env_file)
            if env_file is not None
            else load_config(config_path)
        )
        recovery_plan = plan_recovery(detection)
        try:
            integrity_plan = plan_integrity_repair(detection)
        except (IntegrityRepairRefused, OSError, RuntimeError, ValueError):
            integrity_plan = None
        server = config.get("server", {})
        event_url = (
            f"http://{server.get('host', '127.0.0.1')}:"
            f"{server.get('port', 8765)}/events"
        )
        report = build_diagnostic_report(
            config_path,
            config,
            detection,
            recovery_plan,
            integrity_plan=integrity_plan,
            health=health,
            profile_id=profile_id,
            profile_source=profile_source,
            event_url=event_url,
        )
    except Exception:
        report = DiagnosticReport(
            status="error",
            created_at=time.time(),
            config={"loaded": False},
            hermes={"checked": True, "status": "unsupported"},
            streaming={"status": "not_checked"},
            install_state={
                "status": "unavailable",
                "recovery_executable": False,
                "recovery_fingerprint": "",
            },
            routing={"profile_id": profile_id},
            runtime={},
            findings=(
                DiagnosticFinding(
                    code="operations_diagnosis_failed",
                    severity="error",
                    message="Operations diagnosis could not be completed.",
                ),
            ),
        )
    return report, detection


def _create_operation(
    app: web.Application,
    report: DiagnosticReport,
    *,
    chat_id: str,
    profile_id: str,
    group: bool,
    initiator_open_id: str = "",
    transport_secret: bytes | None = None,
    transport_source_operation_id: str = "",
) -> OperationRecord:
    store: OperationStore = app[OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id=chat_id,
        profile_id=profile_id,
        report_fingerprint=report.fingerprint,
        recovery_fingerprint=report.recovery_fingerprint,
        group=group,
        initiator_open_id=initiator_open_id,
        transport_secret=transport_secret,
        transport_source_operation_id=transport_source_operation_id,
    )
    operation.report = report
    return operation


def _successor_operation(
    app: web.Application,
    previous: OperationRecord,
    report: DiagnosticReport,
    *,
    state: str = "diagnosed",
    result: dict[str, object] | None = None,
) -> OperationRecord:
    store: OperationStore = app[OPERATIONS_STORE_KEY]
    successor = store.create_successor(previous.operation_id, report=report)
    if state != "diagnosed" or result is not None:
        successor = store.complete(
            successor.operation_id,
            expected_state="diagnosed",
            state=state,
            result=result or {},
        )
    _transfer_operation_delivery(app, previous.operation_id, successor.operation_id)
    return successor


def _transfer_operation_delivery(
    app: web.Application, previous_operation_id: str, successor_operation_id: str
) -> None:
    delivery = app[OPERATIONS_DELIVERIES_KEY].pop(previous_operation_id, None)
    if isinstance(delivery, dict):
        transferred = dict(delivery)
        transferred["generation"] = int(transferred.get("generation") or 0) + 1
        _store_operation_delivery(app, successor_operation_id, transferred)


def _store_operation_delivery(
    app: web.Application,
    operation_id: str,
    delivery: dict[str, object],
) -> None:
    deliveries = app[OPERATIONS_DELIVERIES_KEY]
    stored = dict(delivery)
    generation = stored.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int):
        generation = 1
    stored["generation"] = generation
    deliveries[operation_id] = stored
    while len(deliveries) > MAX_OPERATION_DELIVERIES:
        store: OperationStore = app[OPERATIONS_STORE_KEY]
        candidate = next(
            (
                item_id
                for item_id in deliveries
                if not store.is_inflight(item_id)
            ),
            None,
        )
        if candidate is None:
            break
        deliveries.pop(candidate, None)


def _render_operations_for_app(
    app: web.Application,
    report: DiagnosticReport,
    operation: OperationRecord,
) -> dict[str, object]:
    card = render_operations_card(
        report,
        operation,
        "Hermes Feishu Card · 本地运行诊断",
        store=app[OPERATIONS_STORE_KEY],
    )
    title = app[CARD_TITLE_KEY]
    if isinstance(title, str) and title.strip():
        card["header"]["title"]["content"] = title.strip()
    return card


def _operations_response(
    app: web.Application,
    report: DiagnosticReport,
    operation: OperationRecord,
    *,
    ok: bool = True,
    toast: str = "已更新",
    after_eof: Any = None,
) -> web.Response:
    data = {
        "ok": ok,
        "operation_id": operation.operation_id,
        "toast": {
            "type": "success" if ok else "warning",
            "content": toast,
        },
        "card": _render_operations_for_app(app, report, operation),
    }
    return _AfterEofJsonResponse(
        data,
        lambda: _schedule_operations_transition(
            app, report, operation, after_eof
        ),
    )


def _operation_report_snapshot(operation: OperationRecord) -> DiagnosticReport:
    return operation.report or _failed_operations_report(operation.profile_id)


def _operation_profile_source(operation: OperationRecord) -> str:
    routing = operation.report.routing if operation.report is not None else {}
    source = str(routing.get("profile_source") or "") if isinstance(routing, dict) else ""
    return source if source in _STABLE_PROFILE_SOURCES else PROFILE_SOURCE_FALLBACK


def _operation_evidence_matches(
    operation: OperationRecord, report: DiagnosticReport
) -> bool:
    return (
        report.fingerprint == operation.report_fingerprint
        and report.recovery_fingerprint == operation.recovery_fingerprint
    )


def _schedule_operations_recheck(
    app: web.Application, operation: OperationRecord
) -> None:
    _track_operations_task(
        app, asyncio.create_task(_run_operations_recheck(app, operation))
    )


def _schedule_operations_transition(
    app: web.Application,
    report: DiagnosticReport,
    operation: OperationRecord,
    follow_up: Callable[[], None] | None = None,
) -> None:
    if app[OPERATIONS_MUTATIONS_STOPPING_KEY]["stopping"]:
        return
    _track_operations_task(
        app,
        asyncio.create_task(_publish_operations_transition(app, report, operation)),
    )
    if follow_up is not None:
        follow_up()


async def _publish_operations_transition(
    app: web.Application,
    report: DiagnosticReport,
    operation: OperationRecord,
) -> None:
    await _publish_operations_card(app, report, operation)


def _schedule_operations_repair(
    app: web.Application, operation: OperationRecord
) -> None:
    _track_operations_task(
        app, asyncio.create_task(_run_operations_repair(app, operation))
    )


def _schedule_operations_restart(
    app: web.Application, operation: OperationRecord
) -> None:
    _track_operations_task(
        app, asyncio.create_task(_run_operations_restart(app, operation))
    )


async def _run_operations_mutation(app: web.Application, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    if app[OPERATIONS_MUTATIONS_STOPPING_KEY]["stopping"]:
        raise OperationRejected("operations are stopping")
    future: Future[Any] = app[OPERATIONS_MUTATION_EXECUTOR_KEY].submit(func, *args, **kwargs)
    futures: set[Future[Any]] = app[OPERATIONS_MUTATION_FUTURES_KEY]
    futures.add(future)
    loop = asyncio.get_running_loop()
    future.add_done_callback(
        lambda completed: loop.call_soon_threadsafe(futures.discard, completed)
    )
    return await asyncio.shield(asyncio.wrap_future(future))


async def _run_operations_recheck(
    app: web.Application, operation: OperationRecord
) -> None:
    store: OperationStore = app[OPERATIONS_STORE_KEY]
    if not store.is_preparing(operation.operation_id):
        return
    try:
        report, _detection = await _bounded_operations_report(
            app,
            profile_id=operation.profile_id,
            profile_source=_operation_profile_source(operation),
            preparing_operation_id=operation.operation_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        failed_report = _failed_operations_report(operation.profile_id)
        failed = _complete_operations_recheck(
            app,
            operation,
            failed_report,
            state="failed",
            result={"message": "诊断暂时不可用，请稍后重新检测。"},
        )
        if failed is not None:
            await _publish_operations_card(app, failed_report, failed)
        return
    diagnosed = _complete_operations_recheck(app, operation, report)
    if diagnosed is not None:
        await _publish_operations_card(app, report, diagnosed)


def _complete_operations_recheck(
    app: web.Application,
    preparing: OperationRecord,
    report: DiagnosticReport,
    *,
    state: str = "diagnosed",
    result: dict[str, object] | None = None,
) -> OperationRecord | None:
    store: OperationStore = app[OPERATIONS_STORE_KEY]
    if not store.is_preparing(preparing.operation_id):
        return None
    try:
        return _successor_operation(
            app,
            preparing,
            report,
            state=state,
            result=result,
        )
    except OperationRejected:
        return None


async def _run_operations_repair(
    app: web.Application, operation: OperationRecord
) -> None:
    if operation.state != "executing":
        return
    try:
        report, detection = await _bounded_operations_report(
            app,
            profile_id=operation.profile_id,
            profile_source=_operation_profile_source(operation),
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        await _finish_operations_repair(
            app,
            operation,
            _failed_operations_report(operation.profile_id),
            state="failed",
            result={"message": "诊断暂时不可用，请重新检测后再决定下一步。"},
        )
        return

    if not _operation_evidence_matches(operation, report):
        await _finish_operations_repair(
            app,
            operation,
            report,
            state="failed",
            result={"message": "诊断状态已变化，请重新检测后再决定下一步。"},
        )
        return

    metrics: SidecarMetrics = app[METRICS_KEY]
    metrics.recovery_attempts += 1
    try:
        recovery_result = await _run_operations_mutation(
            app, execute_recovery, detection, operation.recovery_fingerprint
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        metrics.recovery_refusals += 1
        await _finish_operations_repair(
            app,
            operation,
            report,
            state="failed",
            result={"message": "安全修复未执行；当前证据不再满足自动修复条件。"},
        )
        return

    metrics.recovery_successes += 1
    try:
        post_repair_report, _post_repair_detection = await _bounded_operations_report(
            app,
            profile_id=operation.profile_id,
            profile_source=_operation_profile_source(operation),
        )
        post_repair_available = _operations_report_available(post_repair_report)
    except asyncio.CancelledError:
        raise
    except Exception:
        post_repair_report = _failed_operations_report(operation.profile_id)
        post_repair_available = False
    await _finish_operations_repair(
        app,
        operation,
        post_repair_report,
        state="repaired",
        result={
            "status": str(getattr(recovery_result, "status", "repaired")),
            "message": (
                "已完成安全修复并重新检测。"
                if post_repair_available
                else "已完成安全修复，但重新检测暂时不可用。"
            ),
            "restart_available": post_repair_available and bool(shutil.which("hermes")),
        },
    )


async def _finish_operations_repair(
    app: web.Application,
    operation: OperationRecord,
    report: DiagnosticReport,
    *,
    state: str,
    result: dict[str, object],
) -> None:
    if operation.state != "executing":
        return
    store: OperationStore = app[OPERATIONS_STORE_KEY]
    try:
        store.complete(
            operation.operation_id,
            expected_state="executing",
            state=state,
            result=result,
        )
        completed = _successor_operation(
            app, operation, report, state=state, result=result
        )
    except OperationRejected:
        return
    await _publish_operations_card(app, report, completed)


async def _run_operations_restart(
    app: web.Application, operation: OperationRecord
) -> None:
    if operation.state != "restarting":
        return
    try:
        report, detection = await _bounded_operations_report(
            app,
            profile_id=operation.profile_id,
            profile_source=_operation_profile_source(operation),
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        await _finish_operations_restart(
            app,
            operation,
            _failed_operations_report(operation.profile_id),
            state="restart_failed",
            result={"message": "Gateway 重启前的诊断暂时不可用，请重新检测。"},
        )
        return

    if not _operation_evidence_matches(operation, report):
        await _finish_operations_restart(
            app,
            operation,
            report,
            state="restart_failed",
            result={"message": "诊断状态已变化，请重新检测后再决定下一步。"},
        )
        return

    hermes_binary = shutil.which("hermes")
    if not hermes_binary:
        await _finish_operations_restart(
            app,
            operation,
            report,
            state="restart_failed",
            result={"message": "未找到可用的 Hermes Gateway 重启命令。"},
        )
        return

    await asyncio.sleep(RESTART_CALLBACK_GRACE_SECONDS)
    try:
        completed = await _run_operations_mutation(
            app,
            subprocess.run,
            [hermes_binary, "gateway", "restart"],
            cwd=detection.root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return_code = int(completed.returncode)
        output_status = _restart_output_status(
            f"{completed.stdout or ''}\n{completed.stderr or ''}"
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        return_code = -1
        output_status = "unavailable"
    await _finish_operations_restart(
        app,
        operation,
        report,
        state="restarted" if return_code == 0 else "restart_failed",
        result={
            "return_code": return_code,
            "output_status": output_status,
            "message": (
                "Gateway 重启已完成。"
                if return_code == 0
                else "安全修复已完成，但 Gateway 重启失败。"
            ),
        },
    )


async def _finish_operations_restart(
    app: web.Application,
    operation: OperationRecord,
    report: DiagnosticReport,
    *,
    state: str,
    result: dict[str, object],
) -> None:
    if operation.state != "restarting":
        return
    store: OperationStore = app[OPERATIONS_STORE_KEY]
    try:
        store.complete(
            operation.operation_id,
            expected_state="restarting",
            state=state,
            result=result,
        )
        completed = _successor_operation(
            app, operation, report, state=state, result=result
        )
    except OperationRejected:
        return
    await _publish_operations_card(app, report, completed)


async def _publish_operations_card(
    app: web.Application,
    report: DiagnosticReport,
    operation: OperationRecord,
) -> bool:
    delivery = app[OPERATIONS_DELIVERIES_KEY].get(operation.operation_id)
    if not isinstance(delivery, dict):
        return False
    message_id = str(delivery.get("message_id") or "")
    if not message_id:
        return False
    lock_key = (str(delivery.get("bot_id") or ""), message_id)
    locks: dict[tuple[str, str], dict[str, Any]] = app[OPERATIONS_PUBLISH_LOCKS_KEY]
    guard = _operations_publish_locks_guard(app)
    async with guard:
        entry = locks.get(lock_key)
        if entry is None:
            entry = {"lock": asyncio.Lock(), "users": 0}
            locks[lock_key] = entry
        entry["users"] += 1
    try:
        async with entry["lock"]:
            while True:
                delivery = app[OPERATIONS_DELIVERIES_KEY].get(operation.operation_id)
                if not isinstance(delivery, dict) or str(delivery.get("message_id") or "") != message_id:
                    return False
                generation = delivery.get("generation")

                def still_current() -> bool:
                    current = app[OPERATIONS_DELIVERIES_KEY].get(operation.operation_id)
                    return current is delivery and current.get("generation") == generation

                updated = await _update_card_for_app(
                    app, message_id, _render_operations_for_app(app, report, operation),
                    delivery.get("bot_id"), is_current=still_current,
                )
                current = app[OPERATIONS_STORE_KEY].current_successor(operation.operation_id)
                if current is not None and current.operation_id != operation.operation_id:
                    operation = current
                    report = _operation_report_snapshot(current)
                    continue
                if not still_current():
                    latest = app[OPERATIONS_DELIVERIES_KEY].get(operation.operation_id)
                    if (
                        isinstance(latest, dict)
                        and str(latest.get("message_id") or "") == message_id
                    ):
                        continue
                    return False
                if not updated:
                    result = dict(operation.result or {})
                    result["delivery_error"] = "card update unavailable"
                    operation.result = result
                return updated
    finally:
        async with guard:
            entry["users"] -= 1
            if entry["users"] == 0 and locks.get(lock_key) is entry:
                locks.pop(lock_key, None)


def _operations_publish_locks_guard(app: web.Application) -> asyncio.Lock:
    holder = app[OPERATIONS_PUBLISH_LOCKS_GUARD_KEY]
    guard = holder["value"]
    if guard is None:
        guard = asyncio.Lock()
        holder["value"] = guard
    return guard


def _restart_output_status(output: str) -> str:
    normalized = " ".join(str(output or "").split()).strip().lower()
    if not normalized:
        return "empty"
    if normalized in {
        "gateway restart completed",
        "gateway restarted",
        "restart completed",
        "restart successful",
    }:
        return "reported_success"
    return "suppressed"


async def _runtime_events(request: web.Request) -> web.Response:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    verifier = request.app.get(RUNTIME_AUTH_VERIFIER_KEY)
    if verifier is None:
        return web.json_response(
            {"ok": False, "error": "runtime control unavailable"},
            status=503,
        )
    body = await request.read()
    if len(body) > 4096:
        return web.json_response(
            {"ok": False, "error": "invalid runtime control event"},
            status=400,
        )
    try:
        verifier.verify(request.headers, body)
    except RuntimeControlValidationError:
        metrics.runtime_control_auth_rejections += 1
        return web.json_response(
            {"ok": False, "error": "runtime authentication failed"},
            status=401,
        )
    metrics.runtime_control_events_received += 1
    try:
        payload = json.loads(body)
        event = RuntimeControlEvent.from_dict(payload)
    except (json.JSONDecodeError, RuntimeControlValidationError, TypeError, ValueError):
        return web.json_response(
            {"ok": False, "error": "invalid runtime control event"},
            status=400,
        )
    accepted = request.app[RUNTIME_INTEGRITY_SUPERVISOR_KEY].record(event)
    if accepted:
        metrics.runtime_control_events_accepted += 1
    return web.json_response({"ok": True, "accepted": accepted})


async def _delivery_policy(request: web.Request) -> web.Response:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    verifier = request.app.get(POLICY_AUTH_VERIFIER_KEY)
    if verifier is None:
        metrics.policy_auth_rejections += 1
        return web.json_response(
            {"ok": False, "error": "policy authentication unavailable"},
            status=503,
        )
    body = await request.read()
    try:
        verifier.verify(request.headers, body)
    except PolicyAuthenticationError:
        metrics.policy_auth_rejections += 1
        return web.json_response(
            {"ok": False, "error": "policy authentication failed"},
            status=401,
        )
    try:
        payload = json.loads(body)
    except (TypeError, ValueError, UnicodeDecodeError):
        payload = None
    if not _valid_delivery_policy_payload(payload):
        metrics.policy_invalid_requests += 1
        return web.json_response(
            {"ok": False, "error": "invalid policy request"},
            status=400,
        )
    metrics.policy_queries += 1
    decision = _policy_decision(
        request.app,
        payload["chat_id"],
        profile_id=payload.get("profile_id", ""),
    )
    return web.json_response(
        {
            "ok": True,
            "disposition": decision.disposition,
            "reason": decision.reason,
            "ttl_ms": 1000,
        }
    )


def _valid_delivery_policy_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or payload.get("schema_version") != "1":
        return False
    allowed = {
        "schema_version",
        "chat_id",
        "profile_id",
        "message_id",
        "conversation_id",
        "turn_id",
    }
    if set(payload) - allowed:
        return False
    chat_id = payload.get("chat_id")
    if (
        not isinstance(chat_id, str)
        or not chat_id.strip()
        or len(chat_id) > 512
        or _has_control_characters(chat_id)
    ):
        return False
    profile_id = payload.get("profile_id", "")
    if (
        not isinstance(profile_id, str)
        or len(profile_id) > 64
        or _has_control_characters(profile_id)
        or (profile_id and PROFILE_ID_PATTERN.fullmatch(profile_id) is None)
    ):
        return False
    for field in ("message_id", "conversation_id", "turn_id"):
        value = payload.get(field, "")
        if (
            not isinstance(value, str)
            or len(value) > 512
            or _has_control_characters(value)
        ):
            return False
    return True


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


async def _native_handoff_ack(request: web.Request) -> web.Response:
    verifier = request.app.get(NATIVE_HANDOFF_ACK_AUTH_VERIFIER_KEY)
    if verifier is None:
        return web.json_response(
            {"ok": False, "error": "native handoff authentication unavailable"},
            status=503,
        )
    body = await request.read()
    try:
        verifier.verify(request.headers, body)
    except NativeHandoffAckAuthenticationError:
        return web.json_response(
            {"ok": False, "error": "native handoff authentication failed"},
            status=401,
        )
    try:
        descriptor = json.loads(body.decode("utf-8"))
        record, changed = request.app[NATIVE_HANDOFF_STORE_KEY].acknowledge(
            descriptor
        )
    except (UnicodeError, ValueError, NativeHandoffStoreError, OSError):
        return web.json_response(
            {"ok": False, "error": "invalid native handoff ack"},
            status=400,
        )
    _sync_acknowledged_native_handoff(request.app, record)
    return web.json_response({"ok": True, "acknowledged": changed})


def _sync_acknowledged_native_handoff(
    app: web.Application,
    record: NativeHandoffRecord,
) -> None:
    for session in app[SESSIONS_KEY].values():
        cached = session.terminal_handoff_record
        if (
            cached is not None
            and cached.handoff_id == record.handoff_id
            and cached.uuid_seed == record.uuid_seed
        ):
            session.terminal_handoff_record = record


async def _native_handoff_recover(request: web.Request) -> web.Response:
    verifier = request.app.get(NATIVE_HANDOFF_RECOVERY_AUTH_VERIFIER_KEY)
    if verifier is None:
        return web.json_response(
            {"ok": False, "error": "native handoff authentication unavailable"},
            status=503,
        )
    body = await request.read()
    try:
        verifier.verify(request.headers, body)
    except NativeHandoffRecoveryAuthenticationError:
        return web.json_response(
            {"ok": False, "error": "native handoff authentication failed"},
            status=401,
        )
    try:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {
            "protocol",
            "obligation_key",
            "content_hash",
            "plan_fingerprint",
            "route",
            "target_hash",
        }:
            raise ValueError("invalid recovery lookup")
        if payload.get("protocol") != "hfc-native-handoff-recovery-v2":
            raise ValueError("invalid recovery lookup")
        obligation_key = payload.get("obligation_key")
        content_hash = payload.get("content_hash")
        plan_fingerprint = payload.get("plan_fingerprint")
        route = payload.get("route")
        target_hash = payload.get("target_hash")
        if any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (
                obligation_key,
                content_hash,
                plan_fingerprint,
                target_hash,
            )
        ) or route not in {"create", "thread-create"}:
            raise ValueError("invalid recovery lookup")
        record = request.app[NATIVE_HANDOFF_STORE_KEY].get_by_exact_binding(
            obligation_key=obligation_key,
            content_hash=content_hash,
            plan_fingerprint=plan_fingerprint,
            route=route,
            target_hash=target_hash,
        )
    except (UnicodeError, ValueError, NativeHandoffStoreError, OSError):
        return web.json_response(
            {"ok": False, "error": "invalid native handoff recovery"},
            status=400,
        )
    # Exact lookup normalizes expiry against the store's own clock.
    descriptor = record.descriptor() if record is not None else None
    if descriptor is None:
        return web.json_response({"ok": True, "found": False})
    return web.json_response(
        {"ok": True, "found": True, "native_handoff": descriptor}
    )


def _policy_decision(
    app: web.Application,
    chat_id: str,
    *,
    profile_id: str = "",
) -> ChatDeliveryDecision:
    try:
        decision = app[DELIVERY_POLICY_KEY].decide(
            chat_id,
            profile_id=profile_id,
        )
    except Exception:
        return ChatDeliveryDecision(NATIVE_DISPOSITION, "policy_unavailable")
    if (
        not isinstance(decision, ChatDeliveryDecision)
        or decision.disposition not in {CARD_DISPOSITION, NATIVE_DISPOSITION}
        or decision.reason
        not in {
            "default_card",
            "bindings.native_chats",
            "chat_identity_missing",
            "profile_unknown",
            "policy_unavailable",
        }
    ):
        return ChatDeliveryDecision(NATIVE_DISPOSITION, "policy_unavailable")
    return decision


async def _events(request: web.Request) -> web.Response:
    metrics: SidecarMetrics = request.app[METRICS_KEY]
    if request.app[EVENT_AUTH_REQUIRED_KEY]:
        body = await request.read()
        try:
            request.app[EVENT_AUTH_VERIFIER_KEY].verify(request.headers, body)
        except EventAuthenticationError:
            metrics.events_rejected += 1
            metrics.event_auth_rejections += 1
            return web.json_response(
                {"ok": False, "error": "event authentication failed"},
                status=401,
            )
    try:
        payload = await request.json()
        event = SidecarEvent.from_dict(payload)
    except (EventValidationError, ValueError) as exc:
        metrics.events_rejected += 1
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    metrics.events_received += 1
    message_locks: Dict[str, asyncio.Lock] = request.app[MESSAGE_LOCKS_KEY]
    lock_users: Dict[str, int] = request.app[MESSAGE_LOCK_USERS_KEY]
    lock_key = _session_key(event)
    lock = message_locks.setdefault(lock_key, asyncio.Lock())
    lock_users[lock_key] = lock_users.get(lock_key, 0) + 1
    try:
        async with lock:
            response, post_lock_task = await _apply_event_locked(request, event)
    finally:
        remaining_users = lock_users.get(lock_key, 1) - 1
        if remaining_users > 0:
            lock_users[lock_key] = remaining_users
        else:
            lock_users.pop(lock_key, None)
            cleanup_orphan_message_lock(request.app, lock_key, lock)
    if post_lock_task is not None and _should_await_card_update(event):
        await post_lock_task
    if _event_is_terminal(event) and post_lock_task is None:
        cleanup_runtime_state(request.app, time.time())
    return response


def _normalize_hfc_command(value: Any) -> str:
    command = str(value or "").strip().lower()
    if command in {"status", "doctor", "monitor", "update"}:
        return command
    return "help"


def _safe_command_string(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _safe_command_operator(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("open_id") or "").strip()
    return ""


def _is_group_chat(chat_type: str) -> bool:
    normalized = str(chat_type or "").strip().lower()
    return normalized in {"group", "group_chat", "chat", "groupchat"}


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
        _hfc_sidecar_line(request),
        *_hfc_readiness_lines(request),
        *_hfc_native_handoff_lines(request),
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
        _hfc_sidecar_line(request),
        *_hfc_readiness_lines(request),
        *_hfc_native_handoff_lines(request),
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
        "native_handoff_fence_restores",
        "native_handoff_fence_restore_refusals",
        "runtime_control_events_received",
        "runtime_control_events_accepted",
        "runtime_control_auth_rejections",
        "update_scheduled",
        "update_coalesced",
        "update_queue_peak",
        "terminal_drains",
        "terminal_drain_timeouts",
        "feishu_send_attempts",
        "feishu_noop_attempts",
        "feishu_send_successes",
        "feishu_send_failures",
        "feishu_send_retries",
        "feishu_send_unknown_outcomes",
        "notice_native_fallbacks",
        "notice_uncertain_warnings",
        "notice_update_failures",
        "feishu_update_attempts",
        "feishu_update_successes",
        "feishu_update_failures",
        "feishu_update_retries",
        "table_compactions",
        "table_truncations",
        "card_limit_deferrals",
        "card_native_handoffs",
        "card_limit_json_bytes",
        "card_limit_elements",
        "card_limit_tables",
    )
    lines = [
        "**/hfc monitor**",
        "",
        *_hfc_readiness_lines(request),
        *_hfc_native_handoff_lines(request),
    ]
    lines.extend([f"- {key}: {snapshot.get(key, 0)}" for key in keys])
    lines.append(f"- active_sessions: {len(request.app[SESSIONS_KEY])}")
    lines.extend(_hfc_context_lines(event, None))
    return lines


def _hfc_native_handoff_lines(request: web.Request) -> list[str]:
    snapshot = _safe_native_handoff_diagnostics(request.app)
    delivery_states = snapshot.get("delivery_states")
    if not isinstance(delivery_states, dict):
        delivery_states = {}

    def safe_count(value: Any) -> int:
        return value if type(value) is int and value >= 0 else 0

    records = safe_count(snapshot.get("records"))
    pending = safe_count(delivery_states.get("pending"))
    acked = safe_count(delivery_states.get("acked"))
    uncertain = safe_count(delivery_states.get("uncertain"))
    manual_review_required = (
        snapshot.get("manual_review_required") is True or uncertain > 0
    )
    if not (records or pending or uncertain or manual_review_required):
        return []

    lines = [
        f"- native_handoff.records: {records}",
        f"- native_handoff.pending: {pending}",
        f"- native_handoff.acked: {acked}",
        f"- native_handoff.uncertain: {uncertain}",
    ]
    if manual_review_required:
        lines.extend(
            [
                "- native_handoff.manual_review_required: true",
                "- native_handoff.next_action: "
                "先确认飞书原生会话是否已收到答案并核对 Hermes delivery ledger，"
                "再决定是否人工重试；不要删除 handoff state，也不要自动重试",
            ]
        )
    return lines


def _hfc_readiness_lines(request: web.Request) -> list[str]:
    readiness = request.app[RUNTIME_INTEGRITY_SUPERVISOR_KEY].snapshot()
    integrity = sanitize_integrity_snapshot(
        request.app[RUNTIME_INTEGRITY_COORDINATOR_KEY].snapshot()
    )
    lines = [
        f"- readiness: {readiness['status']}",
        f"- readiness_reason: {readiness['reason']}",
        f"- integrity.mode: {readiness['integrity_mode']}",
        "- gateway.restart_required: "
        f"{'true' if readiness['restart_required'] else 'false'}",
        f"- integrity.status: {integrity['last_status']}",
        f"- integrity.reason: {integrity['last_reason']}",
        f"- integrity.repair_attempts: {integrity['repair_attempts']}",
        f"- integrity.repair_successes: {integrity['repair_successes']}",
        f"- integrity.repair_refusals: {integrity['repair_refusals']}",
    ]
    integrity_action = {
        "repair_available": (
            "先审核 doctor 证据，再运行 integrity migrate-safe 并重启 sidecar"
        ),
        "manual_review_required": "运行 doctor --explain 后人工检查，不要强制修复",
        "restart_required": "在无活动对话时手动重启 Hermes Gateway 后复查",
        "repaired": "在无活动对话时手动重启 Hermes Gateway 后复查",
    }.get(str(integrity["last_status"]))
    readiness_action = {
        "gateway_restart_required": "重启 Hermes Gateway 后重新检查",
        "runtime_heartbeat_missing": "确认 Hermes Gateway 正在运行，必要时重启",
        "runtime_heartbeat_stale": "检查 Hermes Gateway 状态，必要时重启",
        "control_auth_unavailable": "重新运行 setup 并重启 sidecar 与 Gateway",
        "manual_review_required": "运行 hermes-feishu-card doctor 后人工检查",
    }.get(str(readiness["reason"]))
    action = integrity_action or readiness_action
    if action:
        prefix = "integrity.next_action" if integrity_action else "next_action"
        lines.append(f"- {prefix}: {action}")
    return lines


def _hfc_sidecar_line(request: web.Request) -> str:
    readiness = request.app[RUNTIME_INTEGRITY_SUPERVISOR_KEY].snapshot()
    status = str(readiness.get("status") or "degraded")
    return f"- sidecar: {'ready' if status in {'ready', 'disabled'} else status}"


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
        lines.extend(_hfc_group_context_lines(event, route))
    return lines


def _hfc_group_context_lines(event: SidecarEvent, route: RouteResult) -> list[str]:
    metadata = getattr(route, "metadata", {}) or {}
    group = metadata.get("group") if isinstance(metadata, dict) else None
    if not isinstance(group, dict) or not group.get("is_group"):
        return []
    lines = [
        "",
        "**群聊**",
        "- @机器人触发: 由 Hermes @/白名单准入控制，sidecar 只负责卡片路由和诊断。",
        "- 群内 slash command: 先通过 Hermes @/白名单；所有非空文本反馈使用独立命令卡片。`/update` 仍保持后台升级流程，仅将重启前反馈卡片化。",
    ]
    if group.get("enabled"):
        allowed = "yes" if group.get("chat_allowed") else "no"
        mention = "yes" if group.get("require_mention") else "no"
        lines.append(
            f"- group_rules: enabled, allowed={allowed}, require_mention={mention}"
        )
    if not group.get("chat_bound"):
        bot_id = str(route.bot_id or "default").split(":", 1)[-1]
        lines.extend(
            [
                "- 当前群未绑定到指定 Bot，正在使用 fallback/default 路由。",
                f"- 建议绑定: `hermes-feishu-card bots bind-chat CHAT_ID {bot_id} --config config.yaml`",
                "- 将 `CHAT_ID` 替换为本地配置中的真实群 ID；卡片不会回显原始 ID。",
            ]
        )
    return lines


def _session_key(event: SidecarEvent) -> str:
    """Return the session key for an event.

    When profiles are active, uses composite key profile_id:message_id.
    Otherwise uses message_id directly (backward compatible).
    """
    return _session_key_for_message_id(event, event.canonical_turn_id)


def _policy_profile_id(event: SidecarEvent) -> str | None:
    data = event.data if isinstance(event.data, dict) else {}
    if "profile_id" not in data:
        return ""
    raw_profile_id = data.get("profile_id")
    if not isinstance(raw_profile_id, str):
        return None
    candidate = raw_profile_id.strip()
    if not candidate:
        return "default"
    if PROFILE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return None


def _session_key_for_message_id(event: SidecarEvent, message_id: str) -> str:
    has_profile_id = isinstance(event.data, dict) and "profile_id" in event.data
    profile_id = _safe_profile_id(event.data.get("profile_id") if has_profile_id else None)
    if has_profile_id:
        return f"{profile_id}:{message_id}"
    return message_id


def _session_alias_keys_for_event(event: SidecarEvent) -> list[str]:
    data = event.data if isinstance(event.data, dict) else {}
    aliases: list[str] = []
    for field in ("reply_to_message_id", "parent_message_id", "quote_message_id"):
        value = data.get(field)
        if isinstance(value, str) and value.startswith("om_"):
            aliases.append(_session_key_for_message_id(event, value))
    return aliases


def _native_handoff_identity(event: SidecarEvent) -> str:
    data = event.data if isinstance(event.data, dict) else {}
    raw_profile_id = data.get("profile_id")
    profile_id = raw_profile_id.strip() if isinstance(raw_profile_id, str) else ""
    return handoff_identity_key(
        profile_id=profile_id,
        chat_id=event.chat_id,
        conversation_id=event.conversation_id,
        message_id=event.canonical_turn_id,
    )


def _native_handoff_metadata(event: SidecarEvent) -> dict[str, Any]:
    data = event.data if isinstance(event.data, dict) else {}
    value = data.get("native_handoff")
    if not isinstance(value, dict):
        return {}
    generation = value.get("generation")
    if (
        not isinstance(generation, str)
        or re.fullmatch(r"[0-9a-f]{32,64}", generation) is None
    ):
        return {}
    normalized: dict[str, Any] = {"generation": generation}
    capabilities = value.get("capabilities")
    if isinstance(capabilities, list) and all(
        isinstance(item, str) and len(item) <= 64 for item in capabilities
    ):
        normalized["capabilities"] = tuple(capabilities)
    obligation_key = value.get("obligation_key")
    if (
        isinstance(obligation_key, str)
        and re.fullmatch(r"[0-9a-f]{64}", obligation_key) is not None
    ):
        normalized["obligation_key"] = obligation_key
    for field in ("content_hash", "plan_fingerprint", "target_hash"):
        field_value = value.get(field)
        if (
            isinstance(field_value, str)
            and re.fullmatch(r"[0-9a-f]{64}", field_value) is not None
        ):
            normalized[field] = field_value
    provisional_uuid_seed = value.get("provisional_uuid_seed")
    if (
        isinstance(provisional_uuid_seed, str)
        and re.fullmatch(r"[0-9a-f]{32}", provisional_uuid_seed) is not None
    ):
        normalized["provisional_uuid_seed"] = provisional_uuid_seed
    route = value.get("route")
    if route in {"create", "thread-create"}:
        normalized["route"] = route
    return normalized


def _native_handoff_ack_capable(
    app: web.Application,
    metadata: dict[str, Any],
    event: SidecarEvent,
) -> bool:
    capabilities = set(metadata.get("capabilities") or ())
    complete = (
        NATIVE_HANDOFF_ACK_AUTH_VERIFIER_KEY in app
        and {
            "native-ack-v2",
            "stable-feishu-uuid-v2",
            "exact-base-delivery-v1",
        }.issubset(capabilities)
        and re.fullmatch(
            r"[0-9a-f]{64}", str(metadata.get("obligation_key") or "")
        )
        is not None
        and re.fullmatch(
            r"[0-9a-f]{64}", str(metadata.get("content_hash") or "")
        )
        is not None
        and re.fullmatch(
            r"[0-9a-f]{64}", str(metadata.get("plan_fingerprint") or "")
        )
        is not None
        and re.fullmatch(
            r"[0-9a-f]{64}", str(metadata.get("target_hash") or "")
        )
        is not None
        and metadata.get("route") in {"create", "thread-create"}
    )
    if not complete:
        return False
    data = event.data if isinstance(event.data, dict) else {}
    if event.event != "message.completed" or not is_exact_native_text_scope(data):
        return False
    profile_id = str(data.get("profile_id") or "").strip()
    profile_source = str(data.get("profile_source") or "")
    if profile_id != "default" or profile_source.startswith("sanitized_"):
        return False
    route = str(metadata.get("route") or "")
    try:
        expected_target = derive_native_handoff_target_hash(
            profile_id=profile_id,
            chat_id=event.chat_id,
            thread_id=event.thread_id,
            route=route,
        )
        expected_seed = derive_native_handoff_uuid_seed(
            obligation_key=str(metadata.get("obligation_key") or ""),
            content_hash=str(metadata.get("content_hash") or ""),
            plan_fingerprint=str(metadata.get("plan_fingerprint") or ""),
            route=route,
            target_hash=str(metadata.get("target_hash") or ""),
        )
    except ValueError:
        return False
    return (
        metadata.get("content_hash")
        == derive_native_handoff_content_hash(data["answer"])
        and metadata.get("target_hash") == expected_target
        and metadata.get("provisional_uuid_seed") == expected_seed
    )


def _native_terminal_matches_cached_handoff(
    event: SidecarEvent,
    metadata: dict[str, Any],
    session: CardSession,
    record: NativeHandoffRecord,
) -> bool:
    return (
        str(metadata.get("generation") or "") == record.generation
        and str(metadata.get("obligation_key") or "") == record.obligation_key
        and str(metadata.get("content_hash") or "") == record.content_hash
        and str(metadata.get("plan_fingerprint") or "")
        == record.plan_fingerprint
        and str(metadata.get("route") or "") == record.route
        and str(metadata.get("target_hash") or "") == record.target_hash
        and event.created_at == record.event_created_at
    )


def _native_terminal_matches_durable_handoff(
    event: SidecarEvent,
    metadata: dict[str, Any],
    record: NativeHandoffRecord,
) -> bool:
    return (
        bool(record.obligation_key)
        and bool(record.content_hash)
        and bool(record.plan_fingerprint)
        and bool(record.route)
        and str(metadata.get("generation") or "") == record.generation
        and str(metadata.get("obligation_key") or "") == record.obligation_key
        and str(metadata.get("content_hash") or "") == record.content_hash
        and str(metadata.get("plan_fingerprint") or "")
        == record.plan_fingerprint
        and str(metadata.get("route") or "") == record.route
        and str(metadata.get("target_hash") or "") == record.target_hash
        and event.created_at == record.event_created_at
    )


def _event_starts_new_lifecycle(event: SidecarEvent) -> bool:
    if event.event == "message.started":
        return True
    if _event_is_terminal(event):
        return False
    data = event.data if isinstance(event.data, dict) else {}
    return data.get("lifecycle_start") is True or data.get("new_lifecycle") is True


def _get_native_handoff(
    app: web.Application, identity_key: str
) -> NativeHandoffRecord | None:
    return app[NATIVE_HANDOFF_STORE_KEY].get(identity_key)


def _prepare_native_handoff_lifecycle(
    app: web.Application,
    identity_key: str,
    *,
    event_created_at: float,
) -> str:
    try:
        return app[NATIVE_HANDOFF_STORE_KEY].prepare_lifecycle(
            identity_key,
            event_created_at=event_created_at,
        )
    except (NativeHandoffStoreError, OSError, ValueError):
        logger.warning("native handoff lifecycle state could not be prepared safely")
        return "unavailable"


def _begin_native_handoff(
    app: web.Application,
    identity_key: str,
    *,
    feishu_message_id: str | None,
    bot_id: str | None,
    event_created_at: float,
    generation: str = "",
    ack_capable: bool = False,
    obligation_key: str = "",
    content_hash: str = "",
    plan_fingerprint: str = "",
    route: str = "",
    target_hash: str = "",
    provisional_uuid_seed: str = "",
) -> tuple[NativeHandoffRecord | None, bool]:
    try:
        store = app[NATIVE_HANDOFF_STORE_KEY]
        if feishu_message_id is None:
            return store.begin_no_card(
                identity_key,
                event_created_at=event_created_at,
                generation=generation,
                ack_capable=ack_capable,
                obligation_key=obligation_key,
                content_hash=content_hash,
                plan_fingerprint=plan_fingerprint,
                route=route,
                target_hash=target_hash,
                provisional_uuid_seed=provisional_uuid_seed,
            )
        return store.begin(
            identity_key,
            feishu_message_id=feishu_message_id,
            bot_id=bot_id or "",
            event_created_at=event_created_at,
            generation=generation,
            ack_capable=ack_capable,
            obligation_key=obligation_key,
            content_hash=content_hash,
            plan_fingerprint=plan_fingerprint,
            route=route,
            target_hash=target_hash,
            provisional_uuid_seed=provisional_uuid_seed,
        )
    except (NativeHandoffStoreError, OSError, ValueError):
        logger.warning("native handoff state could not be persisted safely")
        return None, False


def _commit_native_handoff(
    app: web.Application,
    identity_key: str,
    expected_record: NativeHandoffRecord,
) -> None:
    try:
        app[NATIVE_HANDOFF_STORE_KEY].mark_committed(
            identity_key,
            expected_record=expected_record,
        )
    except (NativeHandoffStoreError, OSError, ValueError):
        # A pending record remains safe: a later duplicate retries the same
        # answer-free card update without allowing a second native answer.
        logger.warning("native handoff state could not be committed safely")


def _schedule_pending_native_handoff_repair(
    app: web.Application,
    identity_key: str,
    record: NativeHandoffRecord,
    *,
    feishu_message_id: str | None = None,
    bot_id: str | None = None,
) -> asyncio.Task[bool] | None:
    if record.card_state != "pending" or not feishu_message_id:
        return None
    current_repairs: Dict[str, asyncio.Task[bool]] = app[
        NATIVE_HANDOFF_CURRENT_REPAIRS_KEY
    ]
    current = current_repairs.get(identity_key)
    if current is not None and not current.done():
        return current

    async def repair() -> bool:
        card = render_terminal_limit_handoff_card(app[CARD_TITLE_KEY])
        updated = await _update_card_for_app(
            app,
            feishu_message_id,
            card,
            bot_id,
        )
        if not updated:
            updated = await _retry_terminal_update(
                app,
                feishu_message_id,
                card,
                bot_id,
            )
        if updated:
            _commit_native_handoff(app, identity_key, record)
        return updated

    task = asyncio.create_task(repair())
    _track_native_handoff_repair_task(app, identity_key, task)
    return task


def _track_native_handoff_repair_task(
    app: web.Application,
    identity_key: str,
    task: asyncio.Task[Any],
) -> None:
    tasks: set[asyncio.Task[Any]] = app[NATIVE_HANDOFF_REPAIR_TASKS_KEY]
    current_repairs: Dict[str, asyncio.Task[Any]] = app[
        NATIVE_HANDOFF_CURRENT_REPAIRS_KEY
    ]
    tasks.add(task)
    current_repairs[identity_key] = task

    def finished(completed: asyncio.Task[Any]) -> None:
        tasks.discard(completed)
        if current_repairs.get(identity_key) is completed:
            current_repairs.pop(identity_key, None)
        if completed.cancelled():
            return
        try:
            completed.exception()
        except asyncio.CancelledError:
            return

    task.add_done_callback(finished)


def _cancel_current_native_handoff_repair(
    app: web.Application,
    identity_key: str,
) -> None:
    current_repairs: Dict[str, asyncio.Task[Any]] = app[
        NATIVE_HANDOFF_CURRENT_REPAIRS_KEY
    ]
    current = current_repairs.pop(identity_key, None)
    if current is not None and not current.done():
        current.cancel()


def _active_session_key(app: web.Application, session_key: str) -> str | None:
    sessions: Dict[str, CardSession] = app[SESSIONS_KEY]
    aliases: Dict[str, str] = app[SESSION_ALIASES_KEY]
    candidates = [session_key]
    alias = aliases.get(session_key)
    if alias:
        candidates.append(alias)
    for candidate in candidates:
        session = sessions.get(candidate)
        if session is None:
            continue
        if session.status in {"completed", "failed"}:
            continue
        return candidate
    return None


def _resolve_session_key(app: web.Application, event: SidecarEvent) -> str:
    direct_key = _session_key(event)
    if event.turn_id:
        return direct_key
    active_key = _active_session_key(app, direct_key)
    if active_key is not None:
        return active_key
    # A brand-new user message must ALWAYS start a fresh card, even when
    # the user replied to (quoted) a previous message. Resolving through
    # the reply_to alias here would route the new turn into the old card
    # session and overwrite the previous reply's content. Alias resolution
    # stays available for in-turn stream events (thinking/answer deltas,
    # tool updates) whose Hermes-internal message_id may differ.
    if event.event == "message.started":
        return direct_key
    for alias_key in _session_alias_keys_for_event(event):
        active_key = _active_session_key(app, alias_key)
        if active_key is not None:
            return active_key
    return direct_key


def _register_session_aliases(
    app: web.Application,
    event: SidecarEvent,
    canonical_key: str,
) -> None:
    aliases: Dict[str, str] = app[SESSION_ALIASES_KEY]
    keys = {_session_key(event), *_session_alias_keys_for_event(event)}
    for alias_key in keys:
        if alias_key and alias_key != canonical_key:
            aliases[alias_key] = canonical_key


def _cleanup_failed_session_state(
    app: web.Application,
    session_key: str,
    failed_session: CardSession | None = None,
    session_card_config: dict[str, Any] | None = None,
) -> None:
    sessions: Dict[str, CardSession] = app[SESSIONS_KEY]
    current_session = sessions.get(session_key)
    if failed_session is not None:
        if current_session is not failed_session:
            return
        sessions.pop(session_key, None)
    elif current_session is not None:
        return

    if sessions.get(session_key) is not None:
        return

    aliases: Dict[str, str] = app[SESSION_ALIASES_KEY]
    for alias_key, canonical_key in tuple(aliases.items()):
        if canonical_key == session_key and aliases.get(alias_key) == session_key:
            aliases.pop(alias_key, None)

    owned_state = (
        (CARD_SUMMARIES_KEY, CARD_SUMMARY_SESSION_KEYS_KEY),
        (INTERACTION_RESULTS_KEY, INTERACTION_RESULT_SESSION_KEYS_KEY),
    )
    for values_key, owners_key in owned_state:
        values = app[values_key]
        owners = app[owners_key]
        for value_key, owner_key in tuple(owners.items()):
            if owner_key == session_key and owners.get(value_key) == session_key:
                owners.pop(value_key, None)
                values.pop(value_key, None)

    if (
        session_card_config is not None
        and app[SESSION_CARD_CONFIGS_KEY].get(session_key) is session_card_config
    ):
        app[SESSION_CARD_CONFIGS_KEY].pop(session_key, None)


def _event_for_session(event: SidecarEvent, session: CardSession) -> SidecarEvent:
    if (
        event.conversation_id == session.conversation_id
        and event.message_id == session.message_id
    ):
        return event
    return replace(
        event,
        conversation_id=session.conversation_id,
        message_id=session.message_id,
    )


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
    if _thread_id_for_event(event):
        if isinstance(reply_to, str) and reply_to.startswith("om_"):
            return reply_to
        if event.message_id.startswith("om_"):
            return event.message_id
        return None
    if event.message_id.startswith("om_"):
        return event.message_id
    if isinstance(reply_to, str) and reply_to.startswith("om_"):
        return reply_to
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
    policy_profile_id = _policy_profile_id(event)
    if policy_profile_id is None:
        metrics.policy_event_checks += 1
        metrics.native_bypass_events += 1
        return web.json_response(
            {
                "ok": True,
                "applied": False,
                "disposition": NATIVE_DISPOSITION,
            }
        ), None
    _record_profile_diagnostics(request.app, event)
    _record_attachment_diagnostics(request.app, event)
    incoming_event = event
    session_key = _resolve_session_key(request.app, incoming_event)
    session = sessions.get(session_key)
    if session is not None:
        event = _event_for_session(incoming_event, session)
    event_is_terminal = _event_is_terminal(event)
    handoff_identity = _native_handoff_identity(incoming_event)
    handoff_metadata = _native_handoff_metadata(incoming_event)
    handoff_generation = str(handoff_metadata.get("generation") or "")
    starts_new_lifecycle = _event_starts_new_lifecycle(incoming_event)
    reopens_completed_session = bool(
        session is not None
        and session.status in {"completed", "failed"}
        and (
            starts_new_lifecycle
            or (
                event.event in TURN_REOPENING_EVENTS
                and incoming_event.data.get("policy_new_turn") is True
            )
        )
    )

    # A signed new-hook lifecycle token is a durable turn fence.  Record it
    # before the per-chat policy bypass so a native started event cannot leave
    # an older card handoff tombstone authoritative for the next completion.
    lifecycle_fence_recorded = False
    if starts_new_lifecycle and handoff_generation:
        try:
            fence_result = request.app[NATIVE_HANDOFF_STORE_KEY].record_lifecycle_fence(
                handoff_identity,
                generation=handoff_generation,
                event_created_at=incoming_event.created_at,
            )
        except (NativeHandoffStoreError, OSError, ValueError):
            metrics.events_rejected += 1
            return web.json_response(
                {"ok": False, "error": "native handoff state unavailable"},
                status=503,
            ), None
        if fence_result == "stale":
            metrics.events_ignored += 1
            return web.json_response({"ok": True, "applied": False}), None
        lifecycle_fence_recorded = fence_result in {"advanced", "same"}

    # A duplicate terminal handoff belongs to the prior card turn even when
    # policy has since changed. Suppress it before considering a new policy
    # decision, but do not mutate handoff state for a genuinely native turn.
    if event_is_terminal and not starts_new_lifecycle:
        has_native_terminal_session = bool(
            session is not None
            and session.status in {"completed", "failed"}
            and session.terminal_disposition == "native"
        )
        try:
            prior_handoff = _get_native_handoff(request.app, handoff_identity)
        except (NativeHandoffStoreError, OSError, ValueError):
            logger.warning("native handoff state could not be read safely")
            metrics.native_handoff_fence_restore_refusals += 1
            metrics.events_rejected += 1
            return web.json_response(
                {"ok": False, "error": "native handoff state unavailable"},
                status=503,
            ), None
        cached_handoff = (
            session.terminal_handoff_record
            if has_native_terminal_session and session is not None
            else None
        )
        if cached_handoff is not None and cached_handoff.delivery_state in {
            "pending",
            "acked",
        }:
            if not _native_terminal_matches_cached_handoff(
                incoming_event,
                handoff_metadata,
                session,
                cached_handoff,
            ):
                metrics.native_handoff_fence_restore_refusals += 1
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "native handoff state unavailable"},
                    status=503,
                ), None
            try:
                prior_handoff, restored = request.app[
                    NATIVE_HANDOFF_STORE_KEY
                ].restore_delivery_fence_if_missing(
                    handoff_identity,
                    cached_handoff,
                )
            except (NativeHandoffStoreError, OSError, ValueError):
                metrics.native_handoff_fence_restore_refusals += 1
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "native handoff state unavailable"},
                    status=503,
                ), None
            session.terminal_handoff_record = prior_handoff
            if restored:
                metrics.native_handoff_fence_restores += 1
        if prior_handoff is not None:
            if handoff_generation and prior_handoff.generation != handoff_generation:
                if incoming_event.created_at > prior_handoff.event_created_at:
                    prior_handoff = None
                else:
                    metrics.events_applied += 1
                    return web.json_response(
                        {"ok": True, "applied": True}
                    ), None
            if prior_handoff is not None and prior_handoff.state == "lifecycle":
                if (
                    handoff_generation
                    and prior_handoff.generation == handoff_generation
                ) or incoming_event.created_at >= prior_handoff.event_created_at:
                    prior_handoff = None
                else:
                    metrics.events_applied += 1
                    request.app[DIAGNOSTICS_KEY]["last_terminal_event"] = {
                        "message_id_hash": _diagnostic_id_hash(
                            incoming_event.message_id
                        ),
                        "event": incoming_event.event,
                        "sequence": incoming_event.sequence,
                        "applied": True,
                        "disposition": "native_stale_replay",
                    }
                    return web.json_response(
                        {"ok": True, "applied": True}
                    ), None
            if prior_handoff is not None:
                incoming_exact = _native_handoff_ack_capable(
                    request.app,
                    handoff_metadata,
                    incoming_event,
                )
                durable_claimed = bool(
                    prior_handoff.delivery_state
                    in {"pending", "acked", "uncertain"}
                    or any(
                        (
                            prior_handoff.uuid_seed,
                            prior_handoff.obligation_key,
                            prior_handoff.content_hash,
                            prior_handoff.plan_fingerprint,
                            prior_handoff.route,
                            prior_handoff.target_hash,
                        )
                    )
                )
                durable_exact = prior_handoff.has_exact_delivery_binding
                if durable_claimed and not durable_exact:
                    metrics.native_handoff_fence_restore_refusals += 1
                    metrics.events_rejected += 1
                    return web.json_response(
                        {"ok": False, "error": "native handoff state unavailable"},
                        status=503,
                    ), None
                if (incoming_exact or durable_exact) and (
                    not incoming_exact
                    or not durable_exact
                    or not _native_terminal_matches_durable_handoff(
                        incoming_event,
                        handoff_metadata,
                        prior_handoff,
                    )
                ):
                    metrics.native_handoff_fence_restore_refusals += 1
                    metrics.events_rejected += 1
                    return web.json_response(
                        {"ok": False, "error": "native handoff state unavailable"},
                        status=503,
                    ), None
                try:
                    prior_handoff = request.app[
                        NATIVE_HANDOFF_STORE_KEY
                    ].expire_pending(handoff_identity)
                except (NativeHandoffStoreError, OSError, ValueError):
                    prior_handoff = None
                if prior_handoff is None:
                    metrics.events_rejected += 1
                    return web.json_response(
                        {"ok": False, "error": "native handoff state unavailable"},
                        status=503,
                    ), None
                post_lock_task = _schedule_pending_native_handoff_repair(
                    request.app,
                    handoff_identity,
                    prior_handoff,
                    feishu_message_id=feishu_message_ids.get(session_key),
                    bot_id=message_bot_ids.get(session_key),
                )
                descriptor = prior_handoff.descriptor()
                if descriptor is not None:
                    metrics.events_ignored += 1
                    response_payload = {
                        "ok": True,
                        "applied": False,
                        "disposition": "native",
                        "native_handoff": descriptor,
                    }
                else:
                    metrics.events_applied += 1
                    response_payload = {"ok": True, "applied": True}
                request.app[DIAGNOSTICS_KEY]["last_terminal_event"] = {
                    "message_id_hash": _diagnostic_id_hash(
                        incoming_event.message_id
                    ),
                    "event": incoming_event.event,
                    "sequence": incoming_event.sequence,
                    "applied": descriptor is None,
                    "disposition": (
                        "native_pending_ack"
                        if descriptor is not None
                        else "native_deduplicated"
                    ),
                }
                return web.json_response(response_payload), post_lock_task
        if has_native_terminal_session:
            # A legacy, expired, uncertain, mismatched, or otherwise
            # non-restorable in-memory record is not authority to invent a new
            # UUID. Fail open so Hermes retains the only remaining answer path.
            metrics.native_handoff_fence_restore_refusals += 1
            metrics.events_rejected += 1
            return web.json_response(
                {"ok": False, "error": "native handoff state unavailable"},
                status=503,
            ), None

    if session is None or reopens_completed_session:
        metrics.policy_event_checks += 1
        decision = _policy_decision(
            request.app,
            incoming_event.chat_id,
            profile_id=policy_profile_id,
        )
        if decision.disposition == NATIVE_DISPOSITION:
            if reopens_completed_session:
                _reset_session_for_new_turn(request.app, session_key)
            metrics.native_bypass_events += 1
            return web.json_response(
                {
                    "ok": True,
                    "applied": False,
                    "disposition": NATIVE_DISPOSITION,
                }
            ), None

    if starts_new_lifecycle and not lifecycle_fence_recorded:
        lifecycle_state = _prepare_native_handoff_lifecycle(
            request.app,
            handoff_identity,
            event_created_at=incoming_event.created_at,
        )
        if lifecycle_state == "stale":
            metrics.events_ignored += 1
            return web.json_response({"ok": True, "applied": False}), None
        if lifecycle_state == "unavailable":
            metrics.events_rejected += 1
            return web.json_response(
                {"ok": False, "error": "native handoff state unavailable"},
                status=503,
            ), None
        if lifecycle_state == "cleared":
            _cancel_current_native_handoff_repair(
                request.app,
                handoff_identity,
            )
    if reopens_completed_session:
        # A completed topic session can share the next turn's message id. A
        # stream event is also sufficient evidence of a new turn when Hermes
        # omitted message.started. Policy was checked before mutating handoff
        # state, so a native turn cannot leave an active lifecycle floor.
        _reset_session_for_new_turn(request.app, session_key)
        session = None
        event = incoming_event
        event_is_terminal = _event_is_terminal(event)

    if _skip_native_text_fallback_interaction(request.app, event):
        metrics.events_ignored += 1
        return web.json_response(
            {
                "ok": True,
                "applied": False,
                "interaction_mode": _interaction_mode_for_session_key(
                    request.app,
                    session_key,
                ),
            }
        ), None

    if (
        session is None
        and event.event == "system.notice"
        and not _is_independent_notice_event(event)
        and not _is_compaction_session_start(event)
    ):
        # Session-scoped notices are auxiliary timeline entries, not a reason
        # to create a new primary card. Background callbacks can outlive the
        # turn that supplied their reply anchor; report applied=False so the
        # runtime wrapper retries it as an independent card with its own lifecycle.
        metrics.events_ignored += 1
        return web.json_response({"ok": True, "applied": False}), None

    if event.event == "message.started":
        if session is not None:
            metrics.events_ignored += 1
            return web.json_response({"ok": True, "applied": False}), None
    if event.event == "message.started" and session is None:
        # Abandon stale sessions for the same conversation — covers the case
        # where a new message arrives with its own explicit message_id (e.g.
        # after /stop or a generation-bump interrupt).
        await _abandon_stale_sessions_for_chat(
            request.app, event.chat_id, session_key, event,
        )
        session = CardSession(
            conversation_id=event.conversation_id,
            message_id=event.message_id,
            chat_id=event.chat_id,
        )
        sessions[session_key] = session
        applied = session.apply(event)
        if applied:
            _register_session_aliases(request.app, incoming_event, session_key)
        if applied and session_key not in feishu_message_ids:
            route = _resolve_route(request, event)
            if route is None:
                _cleanup_failed_session_state(request.app, session_key, session)
                metrics.events_rejected += 1
                delivery = CardDeliveryResult(
                    message_id=None,
                    outcome="not_sent",
                    error_kind="RouteResolutionError",
                )
                return web.json_response(
                    {
                        "ok": False,
                        "error": "bot route failed",
                        "delivery": _delivery_payload(delivery),
                    },
                    status=502,
                ), None
            session_card_config = _resolve_session_card_config(
                request.app, route.bot_id, event
            )
            request.app[SESSION_CARD_CONFIGS_KEY][session_key] = session_card_config
            _refresh_session_display_status(request, session)
            delivery = await _send_card(
                request,
                event.chat_id,
                _render_session_card(request, session),
                route.bot_id,
                thread_id=_thread_id_for_event(event),
                reply_to_message_id=_reply_to_message_id_for_event(event),
                delivery_key=session_key,
                delivery_kind=_delivery_kind(event) or "chat",
            )
            if not delivery.delivered:
                _cleanup_failed_session_state(
                    request.app,
                    session_key,
                    session,
                    session_card_config,
                )
                _record_notice_delivery_decision(metrics, event, delivery)
                metrics.events_rejected += 1
                return web.json_response(
                    {
                        "ok": False,
                        "error": "feishu send failed",
                        "delivery": _delivery_payload(delivery),
                    },
                    status=502,
                ), None
            feishu_message_ids[session_key] = delivery.message_id
            message_bot_ids[session_key] = route.bot_id
            _ensure_card_animation(
                request.app,
                session_key=session_key,
                session=session,
                feishu_message_id=str(delivery.message_id),
                bot_id=route.bot_id,
            )
        if applied:
            metrics.events_applied += 1
        else:
            metrics.events_ignored += 1
        return web.json_response(
            {
                "ok": True,
                "applied": applied,
                "delivery": _delivery_payload(delivery),
            }
        ), None

    if session is None:
        if event.event in SESSION_CREATING_EVENTS or _is_compaction_session_start(event):
            # Abandon stale sessions for the same conversation when a new
            # session is being created.  This handles the interrupt scenario:
            # the gateway interrupts a running turn and starts a new one
            # without sending message.completed for the old turn — the old
            # card would be stuck at "生成中" forever.
            if not _is_independent_notice_event(event):
                await _abandon_stale_sessions_for_chat(
                    request.app, event.chat_id, session_key, event,
                )
            session = CardSession(
                conversation_id=event.conversation_id,
                message_id=event.message_id,
                chat_id=event.chat_id,
            )
            sessions[session_key] = session
            applied = session.apply(event)
            if applied:
                _register_session_aliases(request.app, incoming_event, session_key)
            if applied:
                is_cron_completed = (
                    event.event == "message.completed" and _delivery_kind(event) == "cron"
                )
                route = _resolve_route(request, event)
                if route is None:
                    _cleanup_failed_session_state(request.app, session_key, session)
                    if is_cron_completed:
                        metrics.cron_fallbacks += 1
                    delivery = CardDeliveryResult(
                        message_id=None,
                        outcome="not_sent",
                        error_kind="RouteResolutionError",
                    )
                    _record_notice_delivery_decision(metrics, event, delivery)
                    metrics.events_rejected += 1
                    return web.json_response(
                        {
                            "ok": False,
                            "error": "bot route failed",
                            "delivery": _delivery_payload(delivery),
                        },
                        status=502,
                    ), None
                session_card_config = _resolve_session_card_config(
                    request.app, route.bot_id, event
                )
                request.app[SESSION_CARD_CONFIGS_KEY][session_key] = session_card_config
                _refresh_session_display_status(request, session)
                render_result = _render_session_card_result_for_app(
                    request.app, session
                )
                if event_is_terminal and render_result.disposition == "native":
                    handoff_record, handoff_created = _begin_native_handoff(
                        request.app,
                        handoff_identity,
                        feishu_message_id=None,
                        bot_id=None,
                        event_created_at=incoming_event.created_at,
                        generation=handoff_generation,
                        ack_capable=_native_handoff_ack_capable(
                            request.app, handoff_metadata, incoming_event
                        ),
                        obligation_key=str(
                            handoff_metadata.get("obligation_key") or ""
                        ),
                        content_hash=str(
                            handoff_metadata.get("content_hash") or ""
                        ),
                        plan_fingerprint=str(
                            handoff_metadata.get("plan_fingerprint") or ""
                        ),
                        route=str(handoff_metadata.get("route") or ""),
                        target_hash=str(
                            handoff_metadata.get("target_hash") or ""
                        ),
                        provisional_uuid_seed=str(
                            handoff_metadata.get("provisional_uuid_seed") or ""
                        ),
                    )
                    if handoff_record is None:
                        _cleanup_failed_session_state(
                            request.app,
                            session_key,
                            session,
                            session_card_config,
                        )
                        metrics.events_rejected += 1
                        return web.json_response(
                            {"ok": False, "error": "native handoff state unavailable"},
                            status=503,
                        ), None
                    session.terminal_handoff_record = handoff_record
                    if not handoff_created:
                        duplicate_response = _native_disposition_response(
                            handoff_record,
                            duplicate=True,
                        )
                        if handoff_record.descriptor() is None:
                            metrics.events_applied += 1
                        else:
                            metrics.events_ignored += 1
                        return duplicate_response, None
                    _record_card_render_decision(metrics, render_result)
                    session.terminal_disposition = "native"
                    session.terminal_limit_reason = render_result.limit_reason
                    if is_cron_completed:
                        metrics.cron_fallbacks += 1
                    metrics.events_applied += 1
                    request.app[DIAGNOSTICS_KEY]["last_terminal_event"] = {
                        "message_id_hash": _diagnostic_id_hash(event.message_id),
                        "event": event.event,
                        "sequence": event.sequence,
                        "applied": False,
                        "disposition": "native",
                        "session_status": session.status,
                        "answer_chars": len(session.answer_text),
                    }
                    return _native_disposition_response(handoff_record), None
                _record_card_render_decision(metrics, render_result)
                delivery = await _send_card(
                    request,
                    event.chat_id,
                    render_result.card,
                    route.bot_id,
                    thread_id=_thread_id_for_event(event),
                    reply_to_message_id=_reply_to_message_id_for_event(event),
                    delivery_key=session_key,
                    delivery_kind=_delivery_kind(event)
                    or ("notice" if event.event == "system.notice" else "chat"),
                )
                if not delivery.delivered:
                    _cleanup_failed_session_state(
                        request.app,
                        session_key,
                        session,
                        session_card_config,
                    )
                    if is_cron_completed:
                        metrics.cron_fallbacks += 1
                    _record_notice_delivery_decision(metrics, event, delivery)
                    metrics.events_rejected += 1
                    return web.json_response(
                        {
                            "ok": False,
                            "error": "feishu send failed",
                            "delivery": _delivery_payload(delivery),
                        },
                        status=502,
                    ), None
                message_id = str(delivery.message_id)
                feishu_message_ids[session_key] = message_id
                message_bot_ids[session_key] = route.bot_id
                _ensure_card_animation(
                    request.app,
                    session_key=session_key,
                    session=session,
                    feishu_message_id=message_id,
                    bot_id=route.bot_id,
                )
                if event.event == "interaction.requested":
                    _store_interaction_result(request.app, session)
                if event_is_terminal:
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
            if applied:
                response_payload["delivery"] = _delivery_payload(delivery)
            if event.event == "interaction.requested":
                response_payload["interaction_mode"] = _interaction_mode_for_session_key(
                    request.app,
                    session_key,
                )
            return web.json_response(response_payload), None
        metrics.events_ignored += 1
        return web.json_response({"ok": True, "applied": False}), None

    feishu_message_id = feishu_message_ids.get(session_key)
    if _would_apply(session, event) and feishu_message_id is None:
        metrics.events_rejected += 1
        return web.json_response(
            {"ok": False, "error": "feishu_message_id missing"},
            status=409,
        ), None

    rollback_session_snapshot = (
        copy.deepcopy(session)
        if event_is_terminal or event.event == "interaction.requested"
        else None
    )
    applied = session.apply(event)
    if applied:
        _refresh_session_display_status(request, session)
        _register_session_aliases(request.app, incoming_event, session_key)
    # When a terminal event arrives for a session already completed (e.g. by
    # _abandon_stale_sessions_for_chat), the apply() returns False but the
    # session IS handled — report applied=True so the gateway hook suppresses
    # the native text message (avoiding duplicate delivery).
    terminal_already_handled = (
        not applied
        and event_is_terminal
        and session.status in {"completed", "failed"}
    )
    if terminal_already_handled and session.terminal_disposition == "native":
        metrics.events_applied += 1
        return web.json_response({"ok": True, "applied": True}), None
    if terminal_already_handled:
        applied = True
    render_result: CardRenderResult | None = None
    handoff_record: NativeHandoffRecord | None = None
    if applied and not terminal_already_handled:
        render_result = _render_session_card_result_for_app(request.app, session)
        if event_is_terminal and render_result.disposition == "native":
            handoff_record, handoff_created = _begin_native_handoff(
                request.app,
                handoff_identity,
                feishu_message_id=feishu_message_id,
                bot_id=message_bot_ids.get(session_key),
                event_created_at=incoming_event.created_at,
                generation=handoff_generation,
                ack_capable=_native_handoff_ack_capable(
                    request.app, handoff_metadata, incoming_event
                ),
                obligation_key=str(handoff_metadata.get("obligation_key") or ""),
                content_hash=str(handoff_metadata.get("content_hash") or ""),
                plan_fingerprint=str(
                    handoff_metadata.get("plan_fingerprint") or ""
                ),
                route=str(handoff_metadata.get("route") or ""),
                target_hash=str(handoff_metadata.get("target_hash") or ""),
                provisional_uuid_seed=str(
                    handoff_metadata.get("provisional_uuid_seed") or ""
                ),
            )
            if handoff_record is None:
                if rollback_session_snapshot is not None:
                    _restore_session_snapshot(session, rollback_session_snapshot)
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "native handoff state unavailable"},
                    status=503,
                ), None
            session.terminal_handoff_record = handoff_record
            if not handoff_created:
                duplicate_response = _native_disposition_response(
                    handoff_record,
                    duplicate=True,
                )
                if handoff_record.descriptor() is None:
                    metrics.events_applied += 1
                else:
                    metrics.events_ignored += 1
                return duplicate_response, _schedule_pending_native_handoff_repair(
                    request.app,
                    handoff_identity,
                    handoff_record,
                    feishu_message_id=feishu_message_id,
                    bot_id=message_bot_ids.get(session_key),
                )
            _record_card_render_decision(metrics, render_result)
            session.terminal_disposition = "native"
            session.terminal_limit_reason = render_result.limit_reason
            if _delivery_kind(event) == "cron":
                metrics.cron_fallbacks += 1
        else:
            _record_card_render_decision(metrics, render_result)
    if (
        applied
        and event.event == "interaction.requested"
        and feishu_message_id is not None
        and render_result is not None
    ):
        interaction = session.active_interaction
        interaction_id = (
            interaction.interaction_id if interaction is not None else "pending"
        )
        bot_id = message_bot_ids.get(session_key)
        reply_to_message_id = (
            _reply_to_message_id_for_event(incoming_event)
            or session.reply_to_message_id
            or None
        )
        delivery = await _send_card(
            request,
            event.chat_id,
            render_result.card,
            bot_id,
            thread_id=_thread_id_for_event(incoming_event),
            reply_to_message_id=reply_to_message_id,
            delivery_key=f"{session_key}:interaction:{interaction_id}",
            delivery_kind="interaction",
        )
        if not delivery.delivered:
            if rollback_session_snapshot is not None:
                _restore_session_snapshot(session, rollback_session_snapshot)
            metrics.events_rejected += 1
            return web.json_response(
                {
                    "ok": False,
                    "error": "feishu interaction send failed",
                    "delivery": _delivery_payload(delivery),
                },
                status=502,
            ), None

        promoted_message_id = str(delivery.message_id)
        feishu_message_ids[session_key] = promoted_message_id
        animation_task = request.app[CARD_ANIMATION_TASKS_KEY].pop(
            session_key, None
        )
        if animation_task is not None:
            animation_task.cancel()
        _store_interaction_result(request.app, session)
        _ensure_card_animation(
            request.app,
            session_key=session_key,
            session=session,
            feishu_message_id=promoted_message_id,
            bot_id=bot_id,
        )
        metrics.events_applied += 1
        return web.json_response(
            {
                "ok": True,
                "applied": True,
                "interaction_mode": _interaction_mode_for_session_key(
                    request.app,
                    session_key,
                ),
            }
        ), None
    if applied and event.event.startswith("interaction."):
        _store_interaction_result(request.app, session)
    if event_is_terminal:
        request.app[DIAGNOSTICS_KEY]["last_terminal_event"] = {
            "message_id_hash": _diagnostic_id_hash(event.message_id),
            "event": event.event,
            "sequence": event.sequence,
            "applied": applied
            and not (
                render_result is not None
                and render_result.disposition == "native"
            ),
            "disposition": (
                render_result.disposition if render_result is not None else "card"
            ),
            "session_status": session.status,
            "answer_chars": len(session.answer_text),
        }
    if terminal_already_handled:
        metrics.events_applied += 1
        return web.json_response({"ok": True, "applied": True}), None
    post_lock_task = None
    if applied and feishu_message_id is not None and render_result is not None:
        if event_is_terminal:
            _store_card_summary(request.app, event, session, feishu_message_id)
        is_terminal = event_is_terminal
        controller = _flush_controller_for_session(request.app, session_key)
        bot_id = message_bot_ids.get(session_key)
        _ensure_card_animation(
            request.app,
            session_key=session_key,
            session=session,
            feishu_message_id=feishu_message_id,
            bot_id=bot_id,
        )

        async def _render_and_update() -> bool:
            latest_session = sessions.get(session_key)
            if latest_session is None:
                return False
            # Freeze the card while an interaction is pending: Feishu card
            # updates are full replacements, so any PATCH resets the
            # multi-select dropdown and the free-text input, wiping the
            # user's in-progress answer. Only interaction lifecycle events
            # (completed/failed) may update the card in that state.
            interaction = latest_session.active_interaction
            if (
                interaction is not None
                and interaction.status == "pending"
                and not str(event.event or "").startswith("interaction.")
            ):
                return False
            latest_card = render_result.card
            if is_terminal and render_result.disposition == "card":
                await _populate_subscription_usage(request.app, latest_session)
                populated_result = _render_session_card_result_for_app(
                    request.app, latest_session
                )
                if populated_result.disposition == "card":
                    latest_card = populated_result.card
                else:
                    # The terminal response has already acknowledged card delivery.
                    # Drop only the optional late footer data rather than losing the
                    # full answer or switching disposition after Hermes decided.
                    latest_session.subscription_usage = ""
                    bounded_result = _render_session_card_result_for_app(
                        request.app, latest_session
                    )
                    if bounded_result.disposition == "card":
                        latest_card = bounded_result.card
            updated = await _update_card_for_app(
                request.app,
                feishu_message_id,
                latest_card,
                bot_id,
                notice_update=event.event == "system.notice",
            )
            if not updated and is_terminal:
                updated = await _retry_terminal_update(
                    request.app,
                    feishu_message_id,
                    latest_card,
                    bot_id,
                )
            if (
                updated
                and is_terminal
                and render_result.disposition == "native"
            ):
                _commit_native_handoff(
                    request.app,
                    handoff_identity,
                    handoff_record,
                )
            return updated

        if is_terminal:
            await controller.drain(_final_drain_timeout_seconds(request.app, session_key))
            current_task = controller.schedule(_render_and_update, terminal=True)
            controller.close()
            current_task.add_done_callback(
                lambda task: _post_terminal_cleanup(
                    request.app,
                    session_key,
                    controller,
                    task,
                )
            )
            if render_result.disposition == "native":
                _track_native_handoff_repair_task(
                    request.app,
                    handoff_identity,
                    current_task,
                )
        else:
            current_task = controller.schedule(_render_and_update, terminal=False)
        post_lock_task = current_task
    if applied:
        metrics.events_applied += 1
    else:
        metrics.events_ignored += 1
    native_disposition = bool(
        applied
        and render_result is not None
        and render_result.disposition == "native"
    )
    response_payload = {
        "ok": True,
        "applied": applied and not native_disposition,
    }
    if native_disposition:
        response_payload["disposition"] = "native"
        descriptor = (
            handoff_record.descriptor() if handoff_record is not None else None
        )
        if descriptor is not None:
            response_payload["native_handoff"] = descriptor
    if (
        applied
        and not native_disposition
        and event.event == "system.notice"
        and post_lock_task is not None
    ):
        response_payload["delivery"] = {"outcome": "accepted"}
    if event.event == "interaction.requested":
        response_payload["interaction_mode"] = _interaction_mode_for_session_key(
            request.app,
            _session_key(event),
        )
    return web.json_response(response_payload), post_lock_task


def _post_terminal_cleanup(
    app: web.Application,
    session_key: str,
    controller: FlushController,
    task: asyncio.Task[None],
) -> None:
    try:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("terminal card update task failed", exc_info=error)
    except asyncio.CancelledError:
        return
    finally:
        now = time.time()
        cleanup_closed_controller(app, session_key, controller, now=now)
        cleanup_runtime_state(app, now)


def _ensure_card_animation(
    app: web.Application,
    *,
    session_key: str,
    session: CardSession,
    feishu_message_id: str,
    bot_id: str | None,
) -> None:
    if not _card_animation_is_current(app, session_key, session):
        return
    tasks: Dict[str, asyncio.Task[None]] = app[CARD_ANIMATION_TASKS_KEY]
    existing = tasks.get(session_key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(
        _run_card_animation(
            app,
            session_key=session_key,
            session=session,
            feishu_message_id=feishu_message_id,
            bot_id=bot_id,
        )
    )
    tasks[session_key] = task
    task.add_done_callback(
        lambda completed: _finish_card_animation(
            app,
            session_key,
            completed,
        )
    )


async def _run_card_animation(
    app: web.Application,
    *,
    session_key: str,
    session: CardSession,
    feishu_message_id: str,
    bot_id: str | None,
) -> None:
    controller = _flush_controller_for_session(app, session_key)
    for _ in range(CARD_ANIMATION_MAX_UPDATES):
        await _CARD_ANIMATION_SLEEP(CARD_ANIMATION_INTERVAL_SECONDS)
        if not _card_animation_is_current(app, session_key, session):
            return

        update_failed = False

        async def render_and_update() -> bool:
            nonlocal update_failed
            if not _card_animation_is_current(app, session_key, session):
                return False
            updated = await _update_card_for_app(
                app,
                feishu_message_id,
                _render_session_card_for_app(app, session),
                bot_id,
                is_current=lambda: _card_animation_is_current(
                    app,
                    session_key,
                    session,
                ),
            )
            update_failed = not updated
            return updated

        await controller.schedule(render_and_update, terminal=False)
        if update_failed:
            return
        if not _card_animation_is_current(app, session_key, session):
            return


def _card_animation_is_current(
    app: web.Application,
    session_key: str,
    session: CardSession,
) -> bool:
    # Freeze loading/tool animations while an interaction is pending —
    # periodic PATCHes would reset the user's in-progress selections/input.
    interaction = session.active_interaction
    if interaction is not None and interaction.status == "pending":
        return False
    return app[SESSIONS_KEY].get(session_key) is session and (
        _is_initial_loading(session) or _has_running_tool(session)
    )


def _has_running_tool(session: CardSession) -> bool:
    return any(
        str(tool.status or "").strip().lower()
        in {
            "running",
            "in_progress",
            "in-progress",
            "pending",
            "运行中",
            "进行中",
            "处理中",
        }
        for tool in session.tools.values()
    )


def _finish_card_animation(
    app: web.Application,
    session_key: str,
    task: asyncio.Task[None],
) -> None:
    tasks: Dict[str, asyncio.Task[None]] = app[CARD_ANIMATION_TASKS_KEY]
    if tasks.get(session_key) is task:
        tasks.pop(session_key, None)
    try:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.warning("runtime card animation task failed", exc_info=error)
    except asyncio.CancelledError:
        return


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
    app[INTERACTION_RESULT_SESSION_KEYS_KEY][interaction.interaction_id] = (
        _session_key_for_session(app, session)
    )


def _extract_action_value(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event") if isinstance(payload, dict) else None
    action = event.get("action") if isinstance(event, dict) else None
    value = action.get("value") if isinstance(action, dict) else None
    if isinstance(value, dict):
        return value
    action = payload.get("action") if isinstance(payload, dict) else None
    value = action.get("value") if isinstance(action, dict) else None
    return value if isinstance(value, dict) else {}


def _extract_form_value(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the form container submission payload.

    When a user submits a card form (form_action_type=submit), Feishu returns
    ``action.form_value`` — a mapping of every form component's ``name`` to
    its submitted value (string for input, list for multi_select_static)."""
    event = payload.get("event") if isinstance(payload, dict) else None
    action = event.get("action") if isinstance(event, dict) else None
    form_value = action.get("form_value") if isinstance(action, dict) else None
    if isinstance(form_value, dict):
        return form_value
    action = payload.get("action") if isinstance(payload, dict) else None
    form_value = action.get("form_value") if isinstance(action, dict) else None
    return form_value if isinstance(form_value, dict) else {}


def _extract_callback_chat_id(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload, dict) else None
    context = event.get("context") if isinstance(event, dict) else None
    if isinstance(context, dict):
        return str(context.get("open_chat_id") or context.get("chat_id") or "").strip()
    return ""


def _extract_callback_profile_id(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload, dict) else None
    context = event.get("context") if isinstance(event, dict) else None
    if not isinstance(context, dict):
        return ""
    return _safe_profile_id(context.get("profile_id")) if context.get("profile_id") else ""


def _extract_operator_open_id(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload, dict) else None
    operator = event.get("operator") if isinstance(event, dict) else None
    if not isinstance(operator, dict):
        return ""
    return str(operator.get("open_id") or "").strip()


def _extract_operator_name(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload, dict) else None
    operator = event.get("operator") if isinstance(event, dict) else None
    if not isinstance(operator, dict):
        return ""
    return str(
        operator.get("name")
        or operator.get("user_name")
        or operator.get("display_name")
        or ""
    ).strip()


def _find_session_by_interaction(
    app: web.Application,
    interaction_id: str,
    token: str,
    callback_chat_id: str,
    *,
    allow_empty_token: bool = False,
) -> tuple[str, CardSession] | None:
    for session_key, session in app[SESSIONS_KEY].items():
        interaction = session.active_interaction
        if interaction is None:
            continue
        if interaction.interaction_id != interaction_id:
            continue
        if not allow_empty_token and interaction.callback_token != token:
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
    app[CARD_SUMMARY_SESSION_KEYS_KEY][feishu_message_id] = (
        _session_key_for_session(app, session)
    )


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
    native_delivery = str(data.get("native_delivery") or "allowed").strip().lower()
    if native_delivery not in {"allowed", "required"}:
        native_delivery = "allowed"
    app[DIAGNOSTICS_KEY]["last_attachment_event"] = {
        "message_id_hash": _diagnostic_id_hash(event.message_id),
        "event": event.event,
        "attachment_count": len(
            [item for item in attachments if isinstance(item, dict)]
        ),
        "native_delivery": native_delivery,
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


def _is_independent_notice_event(event: SidecarEvent) -> bool:
    if event.event != "system.notice":
        return False
    data = event.data if isinstance(event.data, dict) else {}
    scope = str(data.get("notice_scope") or "session").strip().lower()
    delivery_kind = str(data.get("delivery_kind") or "").strip().lower()
    return scope == "independent" or delivery_kind == "notice"


def _is_compaction_session_start(event: SidecarEvent) -> bool:
    if event.event != "system.notice":
        return False
    data = event.data if isinstance(event.data, dict) else {}
    return (
        str(data.get("notice_kind") or "") == "context-compaction"
        and str(data.get("phase") or "") == "started"
        and data.get("create_session") is True
        and str(data.get("notice_scope") or "session").strip().lower() == "session"
    )


def _event_is_terminal(event: SidecarEvent) -> bool:
    if event.event in TERMINAL_EVENTS:
        return True
    if not _is_independent_notice_event(event):
        return False
    terminal = event.data.get("notice_terminal")
    return not (isinstance(terminal, bool) and terminal is False)


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


def _refresh_session_display_status(
    request: web.Request, session: CardSession
) -> None:
    card_config = request.app[SESSION_CARD_CONFIGS_KEY].get(
        _session_key_for_session(request.app, session),
        {},
    )
    session.refresh_display_status_source(
        StatusConfig.from_mapping(card_config.get("status"))
    )


def _render_session_card(request: web.Request, session: CardSession) -> dict[str, Any]:
    return _render_session_card_for_app(request.app, session)


def _render_session_card_for_app(
    app: web.Application, session: CardSession
) -> dict[str, Any]:
    return _render_session_card_result_for_app(app, session).card


def _render_session_card_result_for_app(
    app: web.Application, session: CardSession
) -> CardRenderResult:
    footer_fields = _footer_fields_for_session(app, session)
    card_config = app[SESSION_CARD_CONFIGS_KEY].get(
        _session_key_for_session(app, session),
        {},
    )
    title = card_config.get("title", app[CARD_TITLE_KEY])
    if not isinstance(title, str):
        title = app[CARD_TITLE_KEY]
    interaction_mode = _interaction_mode_for_session_key(
        app,
        _session_key_for_session(app, session),
    )
    raw_table_overflow_mode = card_config.get("table_overflow_mode", "compact")
    table_overflow_mode = (
        raw_table_overflow_mode.strip().lower()
        if isinstance(raw_table_overflow_mode, str)
        else "compact"
    )
    if table_overflow_mode not in {"compact", "truncate"}:
        table_overflow_mode = "compact"
    return render_card_result(
        session,
        footer_fields=footer_fields,
        title=title,
        interaction_mode=interaction_mode,
        show_reasoning=_safe_bool(card_config.get("show_reasoning"), True),
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
        status_config=StatusConfig.from_mapping(card_config.get("status")),
        text_sizes=(
            card_config.get("text_sizes")
            if isinstance(card_config.get("text_sizes"), dict)
            else None
        ),
        table_overflow_mode=table_overflow_mode,
    )


def _record_card_render_decision(
    metrics: SidecarMetrics, result: CardRenderResult
) -> None:
    metrics.table_compactions += result.table_overflow.compacted_table_count
    metrics.table_truncations += result.table_overflow.truncated_table_count
    if result.disposition == "deferred_native":
        metrics.card_limit_deferrals += 1
    elif result.disposition == "native":
        metrics.card_native_handoffs += 1
    for violation in result.inspection.violations:
        if violation == "json_bytes":
            metrics.card_limit_json_bytes += 1
        elif violation == "elements":
            metrics.card_limit_elements += 1
        elif violation == "tables":
            metrics.card_limit_tables += 1


def _native_disposition_response(
    record: NativeHandoffRecord | None = None,
    *,
    duplicate: bool = False,
) -> web.Response:
    descriptor = record.descriptor() if record is not None else None
    if duplicate and descriptor is None:
        return web.json_response({"ok": True, "applied": True})
    payload: dict[str, Any] = {
        "ok": True,
        "applied": False,
        "disposition": "native",
    }
    if descriptor is not None:
        payload["native_handoff"] = descriptor
    return web.json_response(payload)


def _footer_fields_for_session(
    app: web.Application, session: CardSession
) -> list[str] | None:
    card_config = app[SESSION_CARD_CONFIGS_KEY].get(
        _session_key_for_session(app, session),
        {},
    )
    footer_fields = card_config.get("footer_fields", app[FOOTER_FIELDS_KEY])
    if isinstance(footer_fields, list):
        return list(footer_fields)
    elif footer_fields is not None:
        fallback = app[FOOTER_FIELDS_KEY]
        return list(fallback) if isinstance(fallback, list) else None
    return None


async def _populate_subscription_usage(
    app: web.Application, session: CardSession
) -> None:
    if session.status != "completed" or session.subscription_usage_checked:
        return
    footer_fields = _footer_fields_for_session(app, session)
    if not footer_fields or "subscription_usage" not in footer_fields:
        return
    session.subscription_usage_checked = True
    session.subscription_usage = await fetch_codex_subscription_usage(
        app[OPERATIONS_HERMES_ROOT_KEY]
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
    return merge_card_config(base_card, profile_card)


async def _send_card(
    request: web.Request,
    chat_id: str,
    card: dict[str, Any],
    bot_id: str | None,
    thread_id: str | None = None,
    reply_to_message_id: str | None = None,
    delivery_key: str = "",
    delivery_kind: str = "chat",
) -> CardDeliveryResult:
    return await _send_card_for_app(
        request.app,
        chat_id,
        card,
        bot_id,
        thread_id=thread_id,
        reply_to_message_id=reply_to_message_id,
        delivery_key=delivery_key,
        delivery_kind=delivery_kind,
    )


async def _send_card_for_app(
    app: web.Application,
    chat_id: str,
    card: dict[str, Any],
    bot_id: str | None,
    thread_id: str | None = None,
    reply_to_message_id: str | None = None,
    delivery_key: str = "",
    delivery_kind: str = "chat",
) -> CardDeliveryResult:
    metrics: SidecarMetrics = app[METRICS_KEY]
    metrics.feishu_send_attempts += 1
    if app[NOOP_MODE_KEY]:
        result = CardDeliveryResult(
            message_id=None,
            outcome="not_sent",
            error_kind="NoopDeliveryMode",
        )
        metrics.feishu_noop_attempts += 1
        metrics.feishu_send_failures += 1
        _record_send_error(app, result, bot_id=bot_id)
        return result
    delivery_uuid = build_delivery_uuid(
        bot_id=bot_id or "default",
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id or "",
        session_key=delivery_key,
        delivery_kind=delivery_kind,
    )
    client = _client_for_bot(app, bot_id)
    try:
        send_delivery = getattr(client, "send_card_delivery", None)
        if callable(send_delivery):
            send_result = await send_delivery(
                chat_id,
                card,
                thread_id=thread_id,
                reply_to_message_id=reply_to_message_id,
                delivery_uuid=delivery_uuid,
            )
            message_id = str(getattr(send_result, "message_id", "") or "")
            retry_count = int(getattr(send_result, "retry_count", 0) or 0)
        else:
            message_id = await client.send_card(
                chat_id,
                card,
                thread_id=thread_id,
                reply_to_message_id=reply_to_message_id,
            )
            retry_count = 0
        if not isinstance(message_id, str) or not message_id:
            raise FeishuAPIError(
                "Feishu send result missing message_id",
                retryable=False,
                outcome="unknown",
                retry_count=retry_count,
            )
    except FeishuAPIError as exc:
        outcome = exc.outcome if exc.outcome in {"not_sent", "unknown"} else "unknown"
        result = CardDeliveryResult(
            message_id=None,
            outcome=outcome,
            retry_count=max(0, int(exc.retry_count)),
            error_kind=exc.__class__.__name__,
        )
        metrics.feishu_send_failures += 1
        metrics.feishu_send_retries += result.retry_count
        if result.outcome == "unknown":
            metrics.feishu_send_unknown_outcomes += 1
        _record_send_error(
            app,
            result,
            bot_id=bot_id,
            status_code=exc.status_code,
            api_code=exc.api_code,
        )
        return result
    except Exception as exc:
        result = CardDeliveryResult(
            message_id=None,
            outcome="unknown",
            error_kind=exc.__class__.__name__,
        )
        metrics.feishu_send_failures += 1
        metrics.feishu_send_unknown_outcomes += 1
        _record_send_error(app, result, bot_id=bot_id)
        return result
    metrics.feishu_send_retries += retry_count
    metrics.feishu_send_successes += 1
    return CardDeliveryResult(
        message_id=message_id,
        outcome="delivered",
        retry_count=retry_count,
    )


def _delivery_payload(result: CardDeliveryResult) -> dict[str, str]:
    return {"outcome": result.outcome}


def _record_send_error(
    app: web.Application,
    result: CardDeliveryResult,
    *,
    bot_id: str | None,
    status_code: int | None = None,
    api_code: int | str | None = None,
) -> None:
    diagnostic: dict[str, Any] = {
        "outcome": result.outcome,
        "error_kind": result.error_kind,
        "bot_hash": _diagnostic_id_hash(bot_id or "default"),
    }
    if status_code is not None:
        diagnostic["status_code"] = status_code
    if api_code is not None:
        diagnostic["api_code"] = api_code
    app[DIAGNOSTICS_KEY]["last_send_error"] = diagnostic


def _record_notice_delivery_decision(
    metrics: SidecarMetrics,
    event: SidecarEvent,
    result: CardDeliveryResult,
) -> None:
    if event.event != "system.notice" or result.delivered:
        return
    if result.outcome == "not_sent":
        metrics.notice_native_fallbacks += 1
    else:
        metrics.notice_uncertain_warnings += 1


async def _update_card(
    request: web.Request, message_id: str, card: dict[str, Any], bot_id: str | None
) -> bool:
    return await _update_card_for_app(request.app, message_id, card, bot_id)


async def _update_card_for_app(
    app: web.Application,
    message_id: str,
    card: dict[str, Any],
    bot_id: str | None,
    *,
    is_current: Callable[[], bool] | None = None,
    notice_update: bool = False,
) -> bool:
    metrics: SidecarMetrics = app[METRICS_KEY]
    for attempt in range(UPDATE_MAX_ATTEMPTS):
        if is_current is not None and not is_current():
            return False
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
            if is_current is not None and not is_current():
                return False
            continue
        metrics.feishu_update_latency_ms = int((time.monotonic() - started_at) * 1000)
        metrics.feishu_update_successes += 1
        if is_current is not None and not is_current():
            return False
        return True
    if notice_update:
        metrics.notice_update_failures += 1
    return False


async def _retry_terminal_update(
    app: web.Application, message_id: str, card: dict[str, Any], bot_id: str | None
) -> bool:
    for delay in (1.0, 2.0, 4.0):
        await asyncio.sleep(delay)
        if await _update_card_for_app(app, message_id, card, bot_id):
            return True
    return False


def _restore_session_snapshot(
    session: CardSession,
    snapshot: CardSession,
) -> None:
    session.__dict__.clear()
    session.__dict__.update(snapshot.__dict__)


def _reset_session_for_new_turn(app: web.Application, session_key: str) -> None:
    """Discard a finished session and all its per-key bookkeeping.

    Used when a Feishu topic (thread) group reuses the same message_id for a new
    turn: the previous session for that key is already completed/failed, and we
    must clear it (and its delivery/config/flush state) so the next
    message.started sends a brand-new card instead of trying to update the old
    one or ignoring the event.
    """
    app[SESSIONS_KEY].pop(session_key, None)
    app[FEISHU_MESSAGE_IDS_KEY].pop(session_key, None)
    app[MESSAGE_BOT_IDS_KEY].pop(session_key, None)
    app[SESSION_CARD_CONFIGS_KEY].pop(session_key, None)
    controllers: Dict[str, FlushController] = app[FLUSH_CONTROLLERS_KEY]
    controller = controllers.pop(session_key, None)
    if controller is not None:
        controller.close()
    animation_task = app[CARD_ANIMATION_TASKS_KEY].pop(session_key, None)
    if animation_task is not None:
        animation_task.cancel()


async def _abandon_stale_sessions_for_chat(
    app: web.Application,
    chat_id: str,
    new_session_key: str,
    event: "SidecarEvent",
) -> None:
    """Mark stale active sessions for the same chat+conversation as completed.

    When the gateway interrupts a running turn and starts a new one (e.g. user
    sends a new message mid-turn), no message.completed is sent for the old turn.
    The old card stays stuck at "生成中" forever.  This function finds such
    orphaned sessions and marks them completed so their cards render properly.

    Only abandons sessions that share the same chat_id AND conversation_id AND
    profile_id prefix (to avoid cross-profile or cross-thread interference),
    and skips the new session itself.
    """
    sessions: Dict[str, CardSession] = app[SESSIONS_KEY]
    feishu_message_ids: Dict[str, str] = app[FEISHU_MESSAGE_IDS_KEY]
    message_bot_ids: Dict[str, str] = app[MESSAGE_BOT_IDS_KEY]

    # Extract profile_id prefix from new_session_key (format: "profile:msg_id")
    new_profile = new_session_key.split(":", 1)[0] if ":" in new_session_key else ""
    new_conversation_id = event.conversation_id

    stale_keys = []
    for key, sess in sessions.items():
        if key == new_session_key:
            continue
        if sess.chat_id != chat_id:
            continue
        if sess.conversation_id != new_conversation_id:
            continue
        if sess.status in {"completed", "failed"}:
            continue
        if sess.delivery_kind == "notice":
            continue
        # Match profile prefix
        key_profile = key.split(":", 1)[0] if ":" in key else ""
        if key_profile != new_profile:
            continue
        stale_keys.append(key)

    for key in stale_keys:
        sess = sessions.get(key)
        if sess is None:
            continue
        sess.timeline.complete()
        sess.status = "completed"
        sess.updated_at = time.time()
        card_config = app[SESSION_CARD_CONFIGS_KEY].get(
            key, app[BASE_CARD_CONFIG_KEY]
        )
        sess.refresh_display_status_source(
            StatusConfig.from_mapping(card_config.get("status"))
        )
        logger.info(
            "Abandoning stale session %s (chat_hash=%s, ans=%d chars) "
            "— new session %s is taking over",
            _diagnostic_id_hash(key),
            _diagnostic_id_hash(chat_id),
            len(sess.answer_text),
            _diagnostic_id_hash(new_session_key),
        )
        feishu_msg_id = feishu_message_ids.get(key)
        bot_id = message_bot_ids.get(key)
        if feishu_msg_id is not None:
            await _schedule_abandoned_session_terminal_update(
                app,
                session_key=key,
                session=sess,
                feishu_message_id=feishu_msg_id,
                bot_id=bot_id,
            )


async def _schedule_abandoned_session_terminal_update(
    app: web.Application,
    *,
    session_key: str,
    session: CardSession,
    feishu_message_id: str,
    bot_id: str | None,
) -> None:
    controller = _flush_controller_for_session(app, session_key)
    await controller.drain(_final_drain_timeout_seconds(app, session_key))

    async def render_and_update() -> bool:
        if app[SESSIONS_KEY].get(session_key) is not session:
            return False
        return await _update_card_for_app(
            app,
            feishu_message_id,
            _render_session_card_for_app(app, session),
            bot_id,
        )

    task = controller.schedule(render_and_update, terminal=True)
    controller.close()
    task.add_done_callback(
        lambda completed: _post_terminal_cleanup(
            app,
            session_key,
            controller,
            completed,
        )
    )


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
        route = RouteResult(
            f"{current_profile_id}:{route.bot_id}",
            route.reason,
            metadata=route.metadata,
        )
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
    parts = [
        f"bot_hash={_diagnostic_id_hash(bot_id or 'default', domain='bot')}",
        exc.__class__.__name__,
    ]
    status_code = getattr(exc, "status_code", None)
    if (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 100 <= status_code <= 599
    ):
        parts.append(f"status_code={status_code}")
    api_code = getattr(exc, "api_code", None)
    if isinstance(api_code, int) and not isinstance(api_code, bool):
        parts.append(f"api_code={api_code}")
    elif isinstance(api_code, str):
        normalized_api_code = api_code.strip()
        if (
            normalized_api_code
            and len(normalized_api_code) <= 32
            and all(
                char.isalnum() or char in {"_", "-"}
                for char in normalized_api_code
            )
        ):
            parts.append(f"api_code={normalized_api_code}")
    return " ".join(parts)


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


def _diagnostic_id_hash(value: Any, *, domain: str = "identifier") -> str:
    if not isinstance(value, str) or not value:
        return ""
    encoded = f"hfc-diagnostic-{domain}-v1\0{value}".encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _full_diagnostic_hash(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def python_executable_identity(executable: str | os.PathLike[str]) -> str:
    """Return a path-free, domain-separated identity for a Python runtime."""
    path = Path(executable).expanduser()
    try:
        canonical_parent = path.parent.resolve(strict=False)
    except (OSError, RuntimeError):
        canonical_parent = path.parent.absolute()
    canonical = os.path.normcase(str(canonical_parent / path.name))
    material = b"hermes-feishu-streaming-card:python-executable:v1\0" + os.fsencode(
        canonical
    )
    return f"python-sha256:{hashlib.sha256(material).hexdigest()}"


def _would_apply(session: CardSession, event: SidecarEvent) -> bool:
    return (
        event.conversation_id == session.conversation_id
        and event.message_id == session.message_id
        and event.chat_id == session.chat_id
        and event.sequence > session.last_sequence
        and session.status not in {"completed", "failed"}
    )
