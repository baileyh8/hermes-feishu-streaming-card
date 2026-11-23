from __future__ import annotations

import copy
from collections import OrderedDict
from dataclasses import dataclass, replace
from functools import partial
from contextlib import asynccontextmanager, suppress
from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Dict, Union

from aiohttp import web

from .runtime_ownership import runtime_target_identity, python_executable_identity
from .bots import RouteResult
from .card_limits import inspect_card_limits
from .completion_notification import elapsed_seconds, meets_threshold
from .config import load_config, resolve_operations_hermes_root
from .config_rules import (
    card_width_config, card_completion_mention_enabled,
    card_interaction_mention_enabled, merge_card_config,
    apply_chat_card_overrides, normalize_native_chats,
    normalize_suppressed_notice_kinds,
)
from .delivery import CardDeliveryResult
from .config_snapshot import (
    CONTROL_CARD_CONFIG_PATH, SNAPSHOT_SCHEMA, ConfigSnapshotUnavailable,
    config_path_identity, loaded_reading_values, scope_identity,
)
from .terminal_delivery import (
    TerminalDeliveryPlan, TerminalTarget, deliver_terminal,
)
from .delivery_policy import (
    CARD_DISPOSITION,
    ChatDeliveryDecision,
    ChatDeliveryPolicy,
    NATIVE_DISPOSITION,
)
from .diagnostics import DiagnosticFinding, DiagnosticReport, build_diagnostic_report
from .events import EventValidationError, SidecarEvent
from .interaction_input import (
    InteractionInputError, parse_choice,
    parse_form_action_name as _parse_form_action_name,
    extract_action_value as _extract_action_value,
    extract_form_value as _extract_form_value,
    extract_callback_chat_id as _extract_callback_chat_id,
    extract_callback_profile_id as _extract_callback_profile_id,
    extract_operator_open_id as _extract_operator_open_id,
    extract_operator_name as _extract_operator_name,
)
from .event_rules import (
    active_session_key, resolve_session_key, session_alias_assignments, session_alias_keys_for_event,
    event_conversation_matches, explicit_thread_id, legacy_alias_owner_matches, legacy_reference_owner_key,
    interaction_reply_placement,
    TERMINAL_EVENTS,
    TURN_REOPENING_EVENTS,
    SESSION_CREATING_EVENTS,
    PROFILE_ID_PATTERN,
    session_key as _session_key,
    policy_profile_id as _policy_profile_id,
    session_key_for_message_id as _session_key_for_message_id,
    event_for_session as _event_for_session,
    thread_id_for_event as _thread_id_for_event,
    reply_to_message_id_for_event as _reply_to_message_id_for_event,
    reply_in_thread_for_event as _reply_in_thread_for_event,
    delivery_kind as _delivery_kind,
    is_independent_notice_event as _is_independent_notice_event,
    is_redirect_followup_event as _is_redirect_followup_event,
    is_compaction_session_start as _is_compaction_session_start,
    is_intentional_silence_event as _is_intentional_silence_event,
    is_native_fallback_event as _is_native_fallback_event,
    event_is_terminal as _event_is_terminal,
    safe_profile_id as _safe_profile_id,
    would_apply as _would_apply,
)
from .event_auth import (
    EventAuthenticationError,
    EventProofVerifier,
    NativeHandoffAckAuthenticationError,
    NativeHandoffAckProofVerifier,
    NativeHandoffRecoveryAuthenticationError,
    NativeHandoffRecoveryProofVerifier,
    PolicyAuthenticationError,
    PolicyProofVerifier,
    SidecarRequestAuthenticationError,
    SidecarRequestProofVerifier,
    is_loopback_host,
)
from .flush import FlushController
from .feishu_client import FeishuAPIError, build_delivery_uuid
from .lifecycle import (
    CleanupState,
    cleanup_closed_controller,
    cleanup_orphan_message_lock,
    cleanup_runtime_state,
)
from .metrics import SidecarMetrics
from .observations import timing_summary
from .interaction_rules import (
    ApprovalWindowState, approval_window_can_reopen, RuntimeCallbackSnapshot,
    callback_rejection, runtime_admission_has_expired, interaction_preflight_rejection,
    native_text_fallback_requested, runtime_text_decline_requested,
    runtime_admission_fingerprint as _runtime_admission_fingerprint,
)
from .interaction_resolver import resolve_interaction
from .interaction_receipts import build_auxiliary_receipt
from .owner_display_policy import OwnerRefreshState
from .existing_owner_projection import project_existing_owner
from .message_navigation import MessageNavigation, with_continuation_navigation
from .notice_lifecycle import NoticeScope, RestartNoticeRegistry
from .native_admission import (
    NativeTerminalSnapshot,
    admit_native_handoff,
    event_starts_new_lifecycle as _event_starts_new_lifecycle,
    native_handoff_ack_capable,
    native_handoff_identity as _native_handoff_identity,
    native_handoff_metadata as _native_handoff_metadata,
)
from .native_handoff import (
    NativeHandoffRecord,
    NativeHandoffStore,
    NativeHandoffStoreError,
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
    _format_duration,
    _is_initial_loading,
    _primary_text_for_session,
    render_card_result,
    render_legacy_interaction_callback_card,
    render_terminal_limit_handoff_card,
)
from .process import state_dir
from .session import (
    LEGACY_TERMINAL_RECEIPT_TTL_SECONDS, CardSession, PendingReopenCreate,
    recent_legacy_terminal_receipts,
)
from .text import normalize_stream_text
from .turn_metrics import retained_output_rate
from .approval_receipts import approval_receipt_fingerprint
from .display_segments import begin_continuation, display_view, needs_continuation
from .legacy_owner import (auxiliary_receipt_card,
                           static_legacy_receipt)
from .status import StatusConfig
from .subscription_usage import fetch_codex_subscription_usage, uses_codex_subscription
from .render_options import card_render_options
from .card_view import task_observation_delay
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
SESSION_STORE_KEY = web.AppKey("session_checkpoint_store", object)
SESSION_RESTORE_TASK_KEY = web.AppKey("session_restore_task", object)
SESSIONS_KEY = web.AppKey("sessions", dict)
FEISHU_MESSAGE_IDS_KEY = web.AppKey("feishu_message_ids", dict)
SESSION_ALIASES_KEY = web.AppKey("session_aliases", dict)
REDIRECT_SESSION_ALIASES_KEY = web.AppKey("redirect_session_aliases", dict)
CARD_SUMMARIES_KEY = web.AppKey("card_summaries", dict)
CARD_SUMMARY_SESSION_KEYS_KEY = web.AppKey("card_summary_session_keys", dict)
INTERACTION_RESULTS_KEY = web.AppKey("interaction_results", dict)
PAUSED_APPROVAL_TASKS_KEY = web.AppKey("paused_approval_tasks", set)
CONTINUATION_RETIREMENT_TASKS_KEY = web.AppKey("continuation_retirement_tasks", dict)
CONTINUATION_RETIREMENT_MAX_PENDING = 64
CONTINUATION_RETIREMENT_TIMEOUT_SECONDS = 10.0
INTERACTION_RESULT_SESSION_KEYS_KEY = web.AppKey(
    "interaction_result_session_keys", dict
)
MESSAGE_BOT_IDS_KEY = web.AppKey("message_bot_ids", dict)
SESSION_CARD_CONFIGS_KEY = web.AppKey("session_card_configs", dict)
HEARTBEAT_RECALL_TASKS_KEY = web.AppKey("heartbeat_recall_tasks", dict)
# A heartbeat card is reassurance while the agent works, not a permanent record: every update
# re-arms this deadline, so the card survives exactly as long as the heartbeats keep coming.
#
# Maintainer note (contract change): this was 300s, sized to "comfortably exceed the heartbeat
# interval" so that a slow interval could not recall the card mid-run. That reasoning only applies
# while the card is the ONLY status surface — it is not: the turn's own card shows the live tool row
# throughout. The user asked for the heartbeat to behave like the other transient notices ("文案之前
# 发出去 15 秒会撤回"), and re-arming makes the shorter window safe anyway: while updates flow the
# deadline keeps moving, so the card is only withdrawn once they STOP for this long. The 15s matches
# EPHEMERAL_RECALL_DEFAULT_SECONDS below (the "↪ Redirected" acknowledgement), so both
# read-once-then-gone surfaces now share one window.
HEARTBEAT_RECALL_SECONDS = 15.0
# Acknowledgements that are read once and then only clutter the thread — the core's
# "↪ Redirected current run …" notice — are withdrawn shortly after they land. The core cannot do
# it itself (its Feishu adapter has no delete implementation: BasePlatformAdapter.delete_message
# returns False), so the gateway asks this sidecar, which owns the working Feishu delete path.
EPHEMERAL_RECALL_DEFAULT_SECONDS = 15.0
# Upper bound on a requested delay: a caller must not be able to pin a message deletion far into the
# future (a recall scheduled beyond the runtime's lifetime would simply never run).
EPHEMERAL_RECALL_MAX_SECONDS = 600.0
EPHEMERAL_RECALL_MAX_PENDING = 1024
EPHEMERAL_RECALL_TASKS_KEY = web.AppKey("ephemeral_recall_tasks", dict)
EPHEMERAL_RECALL_WAKE_KEY = web.AppKey("ephemeral_recall_wake", dict)
OWNED_NOTICE_TTL_SECONDS = 15.0
RESTART_NOTICES_KEY = web.AppKey("restart_notices", RestartNoticeRegistry)
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
SIDECAR_REQUEST_AUTH_VERIFIER_KEY = web.AppKey(
    "sidecar_request_auth_verifier", SidecarRequestProofVerifier
)
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
RUNTIME_INTERACTION_RESERVATIONS_KEY = web.AppKey(
    "runtime_interaction_reservations", dict
)
FOOTER_FIELDS_KEY = web.AppKey("footer_fields", Any)
CARD_TITLE_KEY = web.AppKey("card_title", str)
BASE_CARD_CONFIG_KEY = web.AppKey("base_card_config", dict)
OPERATIONS_STORE_KEY = web.AppKey("operations_store", OperationStore)
OPERATIONS_CONFIG_PATH_KEY = web.AppKey("operations_config_path", Path)
LOADED_CONFIG_PATH_IDENTITY_KEY = web.AppKey("loaded_config_path_identity", str)
OPERATIONS_ENV_FILE_KEY = web.AppKey("operations_env_file", Any)
OPERATIONS_SHARED_SOURCE_KEY = web.AppKey("operations_shared_source", bool)
OPERATIONS_HERMES_HOME_KEY = web.AppKey("operations_hermes_home", Path)
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
EVENT_ID_FENCE_KEY = web.AppKey("event_id_fence", object)
EVENT_ID_FENCE_MAX_ENTRIES = 4096
EVENT_ID_FENCE_TTL_SECONDS = LEGACY_TERMINAL_RECEIPT_TTL_SECONDS
EVENT_ID_FENCE_WAIT_SECONDS = 30.0
_CONFIRMED_LEGACY_TERMINAL = {"ok": True, "applied": True, "delivery": {"outcome": "delivered"}}
UPDATE_MAX_ATTEMPTS = 3
UPDATE_MIN_INTERVAL_SECONDS = 0.2
CARD_ANIMATION_INTERVAL_SECONDS = 0.8
CARD_ANIMATION_MAX_UPDATES = 15
CARD_TASK_OBSERVATION_MAX_CHECKS = 60
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
DIAGNOSTICS_KEY = web.AppKey("diagnostics", dict)
logger = logging.getLogger(__name__)


class _OperationsDiagnosticCapacityError(RuntimeError):
    pass


@dataclass
class EventIdFenceEntry:
    fingerprint: str
    future: asyncio.Future[tuple[int, dict[str, object]]]
    response_status: int | None = None
    response_payload: dict[str, object] | None = None
    expires_at: float = 0.0

    @property
    def completed(self) -> bool:
        return self.response_status is not None and self.response_payload is not None


@dataclass(frozen=True)
class EventIdFenceClaim:
    kind: str
    entry: EventIdFenceEntry | None = None


@dataclass(frozen=True, repr=False)
class RuntimeInteractionDeliveryReservation:
    owner: object
    session_key: str
    session: CardSession
    interaction: object
    admission_fingerprint: str
    sequence: int
    rollback_session: CardSession
    card: dict[str, Any]
    chat_id: str
    bot_id: str | None
    thread_id: str | None
    reply_to_message_id: str | None
    reply_in_thread: bool
    predecessor_message_id: str
    delivery_key: str


def _cleanup_state_for_app(app: web.Application) -> CleanupState:
    """Adapt live registries without giving cleanup access to the application."""
    return CleanupState(
        sessions=app[SESSIONS_KEY],
        aliases=app[SESSION_ALIASES_KEY],
        message_locks=app[MESSAGE_LOCKS_KEY],
        lock_users=app[MESSAGE_LOCK_USERS_KEY],
        controllers=app[FLUSH_CONTROLLERS_KEY],
        message_ids=app[FEISHU_MESSAGE_IDS_KEY],
        bot_ids=app[MESSAGE_BOT_IDS_KEY],
        card_configs=app[SESSION_CARD_CONFIGS_KEY],
        summary_owners=app[CARD_SUMMARY_SESSION_KEYS_KEY],
        summaries=app[CARD_SUMMARIES_KEY],
        interaction_owners=app[INTERACTION_RESULT_SESSION_KEYS_KEY],
        interaction_results=app[INTERACTION_RESULTS_KEY],
        metrics=app[METRICS_KEY],
        diagnostics=app[DIAGNOSTICS_KEY],
    )


FenceKey = Union[str, tuple[str, str]]


class EventIdFence:
    def __init__(
        self,
        metrics: SidecarMetrics,
        *,
        max_entries: int = EVENT_ID_FENCE_MAX_ENTRIES,
        ttl_seconds: float = EVENT_ID_FENCE_TTL_SECONDS,
        wait_seconds: float = EVENT_ID_FENCE_WAIT_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.metrics = metrics
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.wait_seconds = wait_seconds
        self.now = now
        self.entries: OrderedDict[FenceKey, EventIdFenceEntry] = OrderedDict()
        # Python 3.9 requires a current event loop when constructing a Lock.
        # create_app() is intentionally safe to call from synchronous startup,
        # so bind the fence lock lazily from the first async claim instead.
        self._lock: asyncio.Lock | None = None

    async def claim(self, event_id: FenceKey, fingerprint: str) -> EventIdFenceClaim:
        lock = self._lock
        if lock is None:
            lock = asyncio.Lock()
            self._lock = lock
        async with lock:
            self._evict_expired_completed_locked()
            entry = self.entries.get(event_id)
            if entry is not None:
                if entry.fingerprint != fingerprint:
                    self.metrics.event_id_conflicts += 1
                    return EventIdFenceClaim("conflict", entry)
                if entry.completed:
                    self.metrics.event_id_replays += 1
                    return EventIdFenceClaim("replay", entry)
                return EventIdFenceClaim("wait", entry)
            if len(self.entries) >= self.max_entries:
                self._evict_oldest_completed_locked()
            if len(self.entries) >= self.max_entries:
                return EventIdFenceClaim("full")
            future = asyncio.get_running_loop().create_future()
            entry = EventIdFenceEntry(fingerprint=fingerprint, future=future)
            self.entries[event_id] = entry
            return EventIdFenceClaim("owner", entry)

    async def wait(
        self, entry: EventIdFenceEntry
    ) -> tuple[int, dict[str, object]] | None:
        try:
            status, payload = await asyncio.wait_for(
                asyncio.shield(entry.future),
                timeout=self.wait_seconds,
            )
        except asyncio.TimeoutError:
            return None
        if payload.get("error") != "event unavailable":
            self.metrics.event_id_replays += 1
        return status, copy.deepcopy(payload)

    async def finalize(
        self,
        event_id: FenceKey,
        entry: EventIdFenceEntry,
        status: int,
        payload: dict[str, object],
        *,
        remaining_ttl: float | None = None,
    ) -> None:
        canonical_payload = copy.deepcopy(payload)
        async with self._lock:
            if self.entries.get(event_id) is not entry or entry.completed:
                return
            entry.response_status = int(status)
            entry.response_payload = canonical_payload
            ttl = self.ttl_seconds if remaining_ttl is None else min(self.ttl_seconds, remaining_ttl)
            entry.expires_at = self.now() + ttl
            if not entry.future.done():
                entry.future.set_result((int(status), copy.deepcopy(canonical_payload)))

    async def abandon(self, event_id: FenceKey, entry: EventIdFenceEntry) -> None:
        async with self._lock:
            if self.entries.get(event_id) is not entry or entry.completed:
                return
            self.entries.pop(event_id, None)
            if not entry.future.done():
                entry.future.set_result((503, {"ok": False, "error": "event unavailable"}))

    def replay_response(self, entry: EventIdFenceEntry) -> web.Response:
        assert entry.response_status is not None
        assert entry.response_payload is not None
        return web.json_response(
            copy.deepcopy(entry.response_payload),
            status=entry.response_status,
        )

    def replay_confirmed_legacy_terminal(self, fingerprint: str) -> web.Response | None:
        entry = self.entries.get(("legacy_terminal", fingerprint))
        if (entry is not None and entry.expires_at > self.now() and entry.response_status == 200
                and entry.response_payload == _CONFIRMED_LEGACY_TERMINAL):
            self.metrics.event_id_replays += 1
            return self.replay_response(entry)
        return None

    def _evict_expired_completed_locked(self) -> None:
        now = self.now()
        expired = [
            event_id
            for event_id, entry in self.entries.items()
            if entry.completed and entry.expires_at <= now
        ]
        for event_id in expired:
            self.entries.pop(event_id, None)
            self.metrics.event_id_evictions += 1

    def _evict_oldest_completed_locked(self) -> None:
        completed = [
            (entry.expires_at, index, event_id)
            for index, (event_id, entry) in enumerate(self.entries.items())
            if entry.completed
        ]
        if not completed:
            return
        _expires_at, _index, event_id = min(completed)
        self.entries.pop(event_id, None)
        self.metrics.event_id_evictions += 1


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
    operations_hermes_home: str | Path | None = None,
    operations_shared_source: bool = False,
    operations_transport_root_secret: bytes | None = None,
    event_auth_required: bool = False,
    noop_mode: bool = False,
    integrity_mode: str = "notify",
    expected_runtime_package_version: str = "",
    runtime_integrity_state_directory: str | Path | None = None,
    delivery_policy: Any = None,
    native_handoff_store: NativeHandoffStore | None = None,
    shutdown_callback: Callable[[], None] | None = None,
    session_store_directory: str | Path | None = None,
) -> web.Application:
    if operations_hermes_home is not None:
        from .instance_binding import configured_hermes_home
        if operations_hermes_root is None:
            raise ValueError("Hermes HOME binding requires an explicit source root")
        operations_hermes_home = configured_hermes_home(
            Path(operations_hermes_root), {"service": {"hermes_home": str(operations_hermes_home)}},
        )
    valid_transport_root = (
        isinstance(operations_transport_root_secret, bytes)
        and len(operations_transport_root_secret) == 32
    )
    if event_auth_required and not valid_transport_root:
        raise ValueError("event authentication requires a private transport root")
    app = web.Application()
    card_config = merge_card_config({}, card_config)
    app[FEISHU_CLIENT_KEY] = feishu_client
    app[SESSIONS_KEY] = {}
    app[FEISHU_MESSAGE_IDS_KEY] = {}
    app[SESSION_ALIASES_KEY] = {}
    app[REDIRECT_SESSION_ALIASES_KEY] = {}
    # TODO: replace this short-lived in-process index with bounded shared storage.
    app[CARD_SUMMARIES_KEY] = {}
    app[CARD_SUMMARY_SESSION_KEYS_KEY] = {}
    app[INTERACTION_RESULTS_KEY] = {}
    app[PAUSED_APPROVAL_TASKS_KEY] = set()
    app[CONTINUATION_RETIREMENT_TASKS_KEY] = {}
    app[INTERACTION_RESULT_SESSION_KEYS_KEY] = {}
    app[MESSAGE_BOT_IDS_KEY] = {}
    app[SESSION_CARD_CONFIGS_KEY] = {}
    app[BOT_ROUTER_KEY] = bot_router
    app[PROCESS_TOKEN_KEY] = process_token
    app[PACKAGE_VERSION_KEY] = str(package_version)
    app[PYTHON_IDENTITY_KEY] = str(python_identity)
    app[SHUTDOWN_CALLBACK_KEY] = shutdown_callback
    app[METRICS_KEY] = SidecarMetrics()
    app[EVENT_ID_FENCE_KEY] = EventIdFence(app[METRICS_KEY])
    app[NOOP_MODE_KEY] = bool(noop_mode)
    app[EVENT_AUTH_REQUIRED_KEY] = bool(event_auth_required)
    if event_auth_required:
        app[EVENT_AUTH_VERIFIER_KEY] = EventProofVerifier(
            operations_transport_root_secret
        )
        app[SIDECAR_REQUEST_AUTH_VERIFIER_KEY] = SidecarRequestProofVerifier(
            operations_transport_root_secret
        )
    runtime_supervisor = RuntimeIntegritySupervisor(
        mode=integrity_mode,
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version=expected_runtime_package_version,
        state_directory=runtime_integrity_state_directory,
        expected_target_identity=(runtime_target_identity(operations_hermes_root, home=operations_hermes_home) if operations_hermes_root is not None else None),
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
    app[RUNTIME_INTERACTION_RESERVATIONS_KEY] = {}
    app[FLUSH_CONTROLLERS_KEY] = {}
    app[CARD_ANIMATION_TASKS_KEY] = {}
    app[HEARTBEAT_RECALL_TASKS_KEY] = {}
    app[EPHEMERAL_RECALL_TASKS_KEY] = {}
    app[EPHEMERAL_RECALL_WAKE_KEY] = {}
    app[RESTART_NOTICES_KEY] = RestartNoticeRegistry()
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
        "last_terminal_delivery": {},
        "last_runtime_interaction_callback": "none",
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
    app[LOADED_CONFIG_PATH_IDENTITY_KEY] = config_path_identity(operations_config)
    app[OPERATIONS_ENV_FILE_KEY] = (
        Path(operations_env_file).expanduser()
        if operations_env_file is not None
        else None
    )
    app[OPERATIONS_HERMES_ROOT_KEY] = resolve_operations_hermes_root(
        operations_hermes_root, config_path=operations_config
    )
    if type(operations_shared_source) is not bool:
        raise ValueError("shared-source binding must be boolean")
    app[OPERATIONS_SHARED_SOURCE_KEY] = operations_shared_source
    app[OPERATIONS_HERMES_HOME_KEY] = (
        Path(operations_hermes_home).resolve() if operations_hermes_home is not None
        else app[OPERATIONS_HERMES_ROOT_KEY].resolve().parent
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
    app.router.add_post(CONTROL_CARD_CONFIG_PATH, _control_card_config)
    app.router.add_get("/messages/{message_id}/summary", _message_summary)
    app.router.add_get("/interactions/{interaction_id}", _interaction_result)
    app.router.add_get("/interactions/{interaction_id}/delivery", _interaction_delivery_result)
    app.router.add_post("/card/actions", _card_actions)
    app.router.add_post("/commands", _commands)
    app.router.add_post("/runtime/events", _runtime_events)
    app.router.add_post("/delivery/policy", _delivery_policy)
    app.router.add_post("/recall/schedule", _recall_schedule)
    app.router.add_post("/native-handoff/ack", _native_handoff_ack)
    app.router.add_post("/native-handoff/recover", _native_handoff_recover)
    app.router.add_post("/events", _events)
    app[SESSION_STORE_KEY] = None
    if session_store_directory is not None:
        app[RESTART_NOTICES_KEY] = RestartNoticeRegistry(
            directory=session_store_directory,
            identity_for_scope=lambda scope: _checkpoint_client_identity(app, scope.bot_id or None)
                if _policy_decision(app, scope.chat_id, profile_id=scope.profile_id).disposition == CARD_DISPOSITION
                else None,
            state_changed=lambda state: app[DIAGNOSTICS_KEY].__setitem__("restart_notice_store_state", state),
        )
    if session_store_directory is not None:
        from .session_store import SessionStore
        try:
            app[SESSION_STORE_KEY] = SessionStore(session_store_directory)
            app[DIAGNOSTICS_KEY]["card_checkpoint_state"] = "ready"
        except (OSError, ValueError):
            app[DIAGNOSTICS_KEY]["card_checkpoint_state"] = "unavailable"
    app.on_startup.append(_restore_card_checkpoints)
    app.on_startup.append(_restore_owned_notice_timers)
    app.on_cleanup.append(_stop_card_restore)
    app.on_startup.append(_start_runtime_cleanup)
    app.on_startup.append(_start_runtime_integrity_monitor)
    app.on_cleanup.append(_stop_operations_diagnostics)
    app.on_cleanup.append(_stop_card_animations)
    app.on_cleanup.append(_stop_heartbeat_recalls)
    app.on_cleanup.append(_stop_ephemeral_recalls)
    app.on_cleanup.append(_stop_native_handoff_repairs)
    app.on_cleanup.append(_stop_runtime_cleanup)
    app.on_cleanup.append(_stop_runtime_integrity_monitor)
    app.on_cleanup.append(_clear_runtime_interaction_admissions)
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


async def _clear_runtime_interaction_admissions(app: web.Application) -> None:
    retirements = app[CONTINUATION_RETIREMENT_TASKS_KEY]
    tasks = (*app[PAUSED_APPROVAL_TASKS_KEY], *retirements.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    app[PAUSED_APPROVAL_TASKS_KEY].clear()
    retirements.clear()
    for session in tuple(app[SESSIONS_KEY].values()):
        interaction = session.active_interaction
        if interaction is not None:
            interaction.runtime_admission = None
            interaction.runtime_turn_id = ""
    app[RUNTIME_INTERACTION_RESERVATIONS_KEY].clear()


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
        now = time.time()
        await _expire_pending_interactions(app, now=now)
        cleanup_runtime_state(_cleanup_state_for_app(app), now)


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
        "timing": timing_summary(
            (session.observation for session in sessions.values()),
            rate_samples=sum(
                1 for session in sessions.values()
                if retained_output_rate(session.tokens) is not None
            ),
        ),
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
                "terminal_delivery_state": session.terminal_delivery_state,
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


async def _control_card_config(request: web.Request) -> web.Response:
    """Read loaded defaults through the existing private process-control boundary."""
    expected = request.app[PROCESS_TOKEN_KEY]
    supplied = request.headers.get("X-HFC-Process-Token", "")
    if (
        not request.remote or not is_loopback_host(request.remote)
        or not expected or not supplied or not secrets.compare_digest(supplied, expected)
    ):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    try:
        raw = await request.read()
        if len(raw) > 4096:
            raise ValueError("request too large")
        selectors = json.loads(raw)
        required = {"profile_id", "bot_id", "config_path_hash"}
        if not isinstance(selectors, dict) or not required <= set(selectors) <= required | {"chat_type", "chat_id"}:
            raise ValueError("invalid selector")
        profile_id, bot_id = selectors["profile_id"], selectors["bot_id"]
        if any(not isinstance(value, str) or not value or len(value) > 256 for value in (profile_id, bot_id)):
            raise ValueError("invalid scope")
        chat_selectors: dict[str, str] = {}
        if "chat_type" in selectors:
            if selectors["chat_type"] not in ("group", "p2p"):
                raise ValueError("invalid chat type")
            chat_selectors["chat_type"] = selectors["chat_type"]
        if "chat_id" in selectors:
            chat_selectors["chat_id"] = normalize_native_chats(
                [selectors["chat_id"]], path="chat selector"
            )[0]
        if selectors["config_path_hash"] != request.app[LOADED_CONFIG_PATH_IDENTITY_KEY]:
            return web.json_response({"ok": False, "error": "config_mismatch"}, status=409)
        values = loaded_reading_values(
            request.app[BASE_CARD_CONFIG_KEY], request.app[FEISHU_CLIENT_KEY],
            profile_id=profile_id, bot_id=bot_id,
            **chat_selectors,
        )
    except ConfigSnapshotUnavailable:
        return web.json_response({"ok": False, "error": "scope_unavailable"}, status=404)
    except (ValueError, TypeError, UnicodeError):
        return web.json_response({"ok": False, "error": "invalid_request"}, status=400)
    return web.json_response({
        "ok": True, "schema_version": SNAPSHOT_SCHEMA,
        "config_path_hash": request.app[LOADED_CONFIG_PATH_IDENTITY_KEY],
        "scope_hash": scope_identity(profile_id, bot_id, **chat_selectors),
        "process_pid": os.getpid(),
        "process_token_hash": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        "package_version": request.app[PACKAGE_VERSION_KEY],
        "values": values,
    })


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
    auth_failure = await _authenticate_sensitive_request(request)
    if auth_failure is not None:
        return auth_failure
    summaries: Dict[str, dict[str, Any]] = request.app[CARD_SUMMARIES_KEY]
    summary = summaries.get(request.match_info["message_id"])
    if summary is None:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    return web.json_response({"ok": True, **summary})


async def _interaction_delivery_result(request: web.Request) -> web.Response:
    """Read detached delivery evidence without refreshing or expiring a waiter."""
    auth_failure = await _authenticate_sensitive_request(request)
    if auth_failure is not None:
        return auth_failure
    interaction_id = request.match_info["interaction_id"]
    result = request.app[INTERACTION_RESULTS_KEY].get(interaction_id)
    fingerprint = result.get("runtime_admission_fingerprint") if type(result) is dict else None
    if type(fingerprint) is not str or not fingerprint:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    return web.json_response({
        "ok": True,
        "interaction_id": interaction_id,
        "runtime_admission_fingerprint": fingerprint,
    })


async def _interaction_result(request: web.Request) -> web.Response:
    auth_failure = await _authenticate_sensitive_request(request)
    if auth_failure is not None:
        return auth_failure
    interaction_id = request.match_info["interaction_id"]
    owner_key = request.app[INTERACTION_RESULT_SESSION_KEYS_KEY].get(interaction_id)
    owner = request.app[SESSIONS_KEY].get(owner_key) if owner_key is not None else None
    if owner_key is not None and owner is not None:
        lock = request.app[MESSAGE_LOCKS_KEY].setdefault(str(owner_key), asyncio.Lock())
        async with lock:
            interaction = owner.active_interaction
            if (interaction is not None and interaction.interaction_id == interaction_id
                    and interaction.pause_on_timeout and interaction.status in {"pending", "paused"}):
                interaction.last_waiter_poll_at = time.time()
                owner.updated_at = interaction.last_waiter_poll_at
                if interaction.status == "paused":
                    _schedule_paused_approval_card(request.app, str(owner_key), owner, interaction)
        await _expire_pending_interaction(
            request.app,
            str(owner_key),
            owner,
            now=time.time(),
        )
    results: Dict[str, dict[str, Any]] = request.app[INTERACTION_RESULTS_KEY]
    result = results.get(interaction_id)
    if result is None:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    return web.json_response({"ok": True, **result})


async def _card_actions(request: web.Request) -> web.Response:
    auth_failure = await _authenticate_sensitive_request(request)
    if auth_failure is not None:
        return auth_failure
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
        # via the button name:
        # hfc_confirm_<callback_token> / hfc_other_<callback_token>.
        form_parsed = _parse_form_action_name(payload)
        if form_parsed is not None:
            mode, callback_token = form_parsed
            return await _interaction_action(
                request,
                payload,
                value,
                form_mode=mode,
                form_callback_token=callback_token,
            )
    return await _interaction_action(request, payload, value)


async def _authenticate_sensitive_request(
    request: web.Request,
) -> web.Response | None:
    if not request.app[EVENT_AUTH_REQUIRED_KEY]:
        return None
    body = await request.read()
    try:
        request.app[SIDECAR_REQUEST_AUTH_VERIFIER_KEY].verify(
            request.headers,
            request.method,
            request.raw_path.split("?", 1)[0],
            body,
        )
    except SidecarRequestAuthenticationError:
        request.app[METRICS_KEY].sidecar_request_auth_rejections += 1
        return web.json_response(
            {"ok": False, "error": "sidecar request authentication failed"},
            status=401,
        )
    return None


async def _resolve_runtime_interaction_callback(
    app: web.Application,
    descriptor: dict[str, Any],
    choice: str,
) -> bool:
    metrics: SidecarMetrics = app[METRICS_KEY]
    metrics.runtime_interaction_callback_attempts += 1
    try:
        outcome = await resolve_interaction(
            app.get(OPERATIONS_TRANSPORT_ROOT_KEY), descriptor, choice,
        )
    except asyncio.CancelledError:
        metrics.runtime_interaction_callback_failures += 1
        app[DIAGNOSTICS_KEY]["last_runtime_interaction_callback"] = "cancelled"
        raise
    if outcome.resolved:
        metrics.runtime_interaction_callback_successes += 1
    else:
        metrics.runtime_interaction_callback_failures += 1
    app[DIAGNOSTICS_KEY]["last_runtime_interaction_callback"] = outcome.reason
    return outcome.resolved


async def _interaction_action(
    request: web.Request,
    payload: dict[str, Any],
    value: dict[str, Any],
    *,
    form_mode: str = "",
    form_callback_token: str = "",
) -> web.Response:
    interaction_id = str(value.get("interaction_id") or "").strip()
    token = str(value.get("token") or "").strip()
    mode = str(value.get("mode") or form_mode or "").strip()
    callback_chat_id = _extract_callback_chat_id(payload)
    if form_callback_token:
        token = form_callback_token
        found = _find_session_by_callback_token(
            request.app,
            token,
            callback_chat_id,
        )
    else:
        if not interaction_id:
            return web.json_response(
                {"ok": False, "error": "invalid action"},
                status=400,
            )
        found = _find_session_by_interaction(
            request.app,
            interaction_id,
            token,
            callback_chat_id,
        )
    if found is None:
        return web.json_response(
            {"ok": False, "error": "interaction not found"},
            status=404,
        )
    session_key, session = found
    if form_callback_token:
        interaction = session.active_interaction
        if interaction is None:
            return web.json_response(
                {"ok": False, "error": "interaction not found"},
                status=404,
            )
        interaction_id = interaction.interaction_id
    if not interaction_id:
        return web.json_response({"ok": False, "error": "invalid action"}, status=400)

    interaction = session.active_interaction
    if interaction is None:
        return web.json_response(
            {"ok": False, "error": "interaction not found"}, status=404
        )
    if value.get("choice") == "__hfc_resume_approval__" and not form_callback_token:
        return await _resume_paused_approval(request, session_key, session, interaction, token, callback_chat_id)
    if not form_callback_token and _approval_card_needs_reissue(interaction):
        # Any operation on an approval that can no longer take a decision is a request to start
        # over: withdraw the dead card and put a fresh approval for the same scope in front of the
        # user. Every option used to collapse into "interaction already completed", which reads as
        # a broken button (#314 follow-up).
        return await _resume_paused_approval(request, session_key, session, interaction, token, callback_chat_id)
    allowed_values = {option.value for option in interaction.options}
    form_value = _extract_form_value(payload)
    try:
        selection = parse_choice(
            value, form_value, mode=mode, allowed_values=allowed_values,
            allow_custom_input=interaction.allow_custom_input,
        )
    except InteractionInputError as exc:
        return web.json_response(exc.payload(), status=400)
    choice, choice_label = selection.value, selection.label

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
    expired_card: dict[str, Any] | None = None
    expired_interaction = None
    expiry_sequence = -1
    runtime_callback: RuntimeCallbackSnapshot | None = None
    callback_card: dict[str, Any] | None = None
    async with lock:
        current_session = request.app[SESSIONS_KEY].get(session_key)
        current_interaction = (
            current_session.active_interaction
            if current_session is session
            else None
        )
        rejection = callback_rejection(
            session, current_session,
            interaction_id=interaction_id, callback_token=token,
            chat_id=callback_chat_id,
            delivery_pending=session_key in request.app[RUNTIME_INTERACTION_RESERVATIONS_KEY],
        )
        if rejection == "not_found":
            return web.json_response(
                {"ok": False, "error": "interaction not found"},
                status=404,
            )
        if rejection == "delivery_pending":
            return web.json_response(
                {"ok": False, "error": "interaction delivery pending"},
                status=409,
            )
        expired_at = time.time()
        if current_interaction.expire(expired_at):
            _mark_interaction_expired_locked(
                request.app,
                session_key,
                session,
                now=expired_at,
            )
            expired_card = _render_session_card(request, session)
            expired_interaction = current_interaction
            expiry_sequence = session.last_sequence
            response = None
            post_lock_task = None
        elif current_interaction.status == "failed":
            expired_card = _render_session_card(request, session)
            expired_interaction = current_interaction
            expiry_sequence = session.last_sequence
            response = None
            post_lock_task = None
        elif current_interaction.status != "pending":
            return web.json_response(
                {"ok": False, "error": "interaction already completed"},
                status=409,
            )
        elif current_interaction.runtime_admission is not None:
            expected_profile_id = (
                session_key.split(":", 1)[0] if ":" in session_key else "default"
            )
            if _extract_callback_profile_id(payload) != expected_profile_id:
                return web.json_response(
                    {"ok": False, "error": "interaction not found"},
                    status=404,
                )
            descriptor = dict(current_interaction.runtime_admission)
            runtime_callback = RuntimeCallbackSnapshot(
                session=current_session,
                interaction=current_interaction,
                interaction_id=interaction_id,
                callback_token=token,
                chat_id=callback_chat_id,
                fingerprint=_runtime_admission_fingerprint(descriptor),
                descriptor=descriptor,
                choice=choice,
                turn_id=current_interaction.runtime_turn_id,
            )
            response = None
            post_lock_task = None
        else:
            event = replace(
                event,
                sequence=current_session.last_sequence + 1,
                created_at=time.time(),
            )
            response, post_lock_task = await _apply_event_locked(
                request,
                event,
                session_key=session_key,
                advance_sequence=False,
            )
            if response.status < 400:
                callback_card = _render_interaction_callback_card_for_app(
                    request.app,
                    session,
                    session_key=session_key,
                )
    if runtime_callback is not None:
        try:
            resolved = await _resolve_runtime_interaction_callback(
                request.app,
                runtime_callback.descriptor,
                runtime_callback.choice,
            )
        except asyncio.CancelledError:
            raise
        if not resolved:
            async with lock:
                current_session = request.app[SESSIONS_KEY].get(session_key)
                current_interaction = (
                    current_session.active_interaction
                    if current_session is runtime_callback.session
                    else None
                )
                if current_interaction is runtime_callback.interaction:
                    _expire_runtime_admission_locked(
                        request.app,
                        session_key,
                        current_session,
                        current_interaction,
                        now=time.time(),
                    )
            return web.json_response(
                {
                    "ok": False,
                    "error": "interaction resolution unavailable",
                    "retryable": True,
                },
                status=503,
            )
        async with lock:
            current_session = request.app[SESSIONS_KEY].get(session_key)
            current_interaction = (
                current_session.active_interaction
                if current_session is runtime_callback.session
                else None
            )
            if not runtime_callback.is_current(current_session):
                runtime_callback.interaction.runtime_admission = None
                return web.json_response(
                    {"ok": False, "error": "interaction changed"}, status=409
                )
            if _expire_runtime_admission_locked(
                request.app,
                session_key,
                current_session,
                current_interaction,
                now=time.time(),
            ):
                return web.json_response(
                    {"ok": False, "error": "interaction changed"}, status=409
                )
            event = replace(
                event,
                turn_id=runtime_callback.turn_id,
                sequence=current_session.last_sequence + 1,
                created_at=time.time(),
            )
            response, post_lock_task = await _apply_event_locked(
                request,
                event,
                session_key=session_key,
                advance_sequence=False,
            )
            if response.status < 400:
                callback_card = _render_interaction_callback_card_for_app(
                    request.app,
                    session,
                    session_key=session_key,
                )
    if expired_card is not None:
        feishu_message_id = request.app[FEISHU_MESSAGE_IDS_KEY].get(session_key)
        if feishu_message_id:
            await _update_card_for_app(
                request.app,
                feishu_message_id,
                expired_card,
                request.app[MESSAGE_BOT_IDS_KEY].get(session_key),
                is_current=lambda: (
                    request.app[SESSIONS_KEY].get(session_key) is session
                    and session.active_interaction is expired_interaction
                    and expired_interaction is not None
                    and expired_interaction.status in {"failed", "paused"}
                    and session.last_sequence == expiry_sequence
                ),
            )
        callback_card = _render_interaction_callback_card_for_app(
            request.app,
            session,
            session_key=session_key,
        )
        return _expired_interaction_response(callback_card)
    if post_lock_task is not None:
        # The choice is already committed. A slow Feishu PATCH must not hold
        # the WebSocket callback open or cancel the controller-owned update.
        post_lock_task.add_done_callback(_log_background_task_failure)
        await asyncio.wait({post_lock_task}, timeout=0.05)
    assert response is not None
    if response.status >= 400:
        return response
    return web.json_response(
        {
            "ok": True,
            "toast": {"type": "success", "content": "已选择"},
            "card": callback_card
            or _render_interaction_callback_card_for_app(
                request.app,
                session,
                session_key=session_key,
            ),
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
        width_mode=_operation_width_mode(app, transitioned),
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
        hermes_home=app[OPERATIONS_HERMES_HOME_KEY],
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
            hermes_home=app[OPERATIONS_HERMES_HOME_KEY],
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
                hermes_home=app[OPERATIONS_HERMES_HOME_KEY],
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
            _store_operation_delivery(request.app, operation.operation_id, {
                "width_mode": _resolve_session_card_config(
                    request.app, route.bot_id if route is not None else None, event
                ).get("width_mode", "default"),
            })
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
            _store_operation_delivery(request.app, operation.operation_id, {
                "width_mode": _resolve_session_card_config(
                    request.app, route.bot_id if route is not None else None, event
                ).get("width_mode", "default"),
            })
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
            profile_id=_policy_profile_id(event),
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
    profile_id: str | None = None,
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
        profile_id=profile_id,
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
            "width_mode": app[OPERATIONS_DELIVERIES_KEY].get(operation_id, {}).get("width_mode", "default"),
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
        shared_source=app[OPERATIONS_SHARED_SOURCE_KEY],
        hermes_root=app[OPERATIONS_HERMES_ROOT_KEY],
        hermes_home=app[OPERATIONS_HERMES_HOME_KEY],
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
        width_mode=_operation_width_mode(app, operation),
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
                        width_mode=_operation_width_mode(app, operation),
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
            render_update_job_card(
                job, title=app[CARD_TITLE_KEY],
                width_mode=_operation_width_mode(app, operation),
            ),
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
                width_mode=_operation_width_mode(app, operation),
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
            hermes_home=app[OPERATIONS_HERMES_HOME_KEY],
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


def _operation_width_mode(app: web.Application, operation: OperationRecord) -> str:
    delivery = app[OPERATIONS_DELIVERIES_KEY].get(operation.operation_id, {})
    return delivery.get("width_mode", app[BASE_CARD_CONFIG_KEY].get("width_mode", "default"))


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
        width_mode=_operation_width_mode(app, operation),
    )
    # Doctor has an asynchronous operations-card path. Its report fingerprint
    # covers installation evidence, not live delivery receipts: render this
    # allowlisted warning afresh without changing repair authorization.
    handoff_lines = _native_handoff_lines_for_app(app)
    if handoff_lines:
        card["body"]["elements"][0]["content"] += (
            "\n\n**原生投递状态**\n" + "\n".join(handoff_lines)
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
        chat_type=str(payload.get("chat_type", "") or ""),
    )
    response = {
        "ok": True,
        "disposition": decision.disposition,
        "reason": decision.reason,
        "ttl_ms": 1000,
    }
    if payload.get("turn_id"):
        # A policy decision alone says nothing about actual delivery. This
        # fresh, read-only proof uses the exact owner committed after create.
        response["accepted_task_card"] = (
            decision.disposition == CARD_DISPOSITION
            and _accepted_task_card_for_policy_probe(request.app, payload)
        )
    return web.json_response(response)


def _accepted_task_card_for_policy_probe(app, payload):
    turn_id = payload.get("turn_id")
    conversation_id = payload.get("conversation_id")
    if not turn_id or not conversation_id:
        return False
    profile = payload.get("profile_id", "")
    key = f"{profile}:{turn_id}" if profile else turn_id
    session = app[SESSIONS_KEY].get(key)  # Never resolve a reply/turn alias.
    return bool(
        session is not None
        and session.chat_id == payload["chat_id"]
        and session.conversation_id == conversation_id
        and session.route_profile_id == (profile or "default")
        and session.delivery_kind == "chat"
        and not session.terminal_disposition
        and app[FEISHU_MESSAGE_IDS_KEY].get(key)
        and app[SESSION_CARD_CONFIGS_KEY].get(key, {}).get("_presentation_mode") == "task"
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
        "chat_type",
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
    chat_type = payload.get("chat_type", "")
    if chat_type and (
        not isinstance(chat_type, str)
        or len(chat_type) > 64
        or _has_control_characters(chat_type)
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
    chat_type: str = "",
) -> ChatDeliveryDecision:
    try:
        decision = app[DELIVERY_POLICY_KEY].decide(
            chat_id,
            profile_id=profile_id,
            chat_type=chat_type,
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
            "bindings.card_chats",
            "bindings.default_delivery",
            "bindings.default_delivery.group",
            "bindings.default_delivery.p2p",
            "chat_identity_missing",
            "profile_unknown",
            "policy_unavailable",
        }
    ):
        return ChatDeliveryDecision(NATIVE_DISPOSITION, "policy_unavailable")
    return decision


@asynccontextmanager
async def _event_owner_lock(app, event):
    locks = app[MESSAGE_LOCKS_KEY]
    users = app[MESSAGE_LOCK_USERS_KEY]
    while True:
        key = _resolve_session_key(app, event)
        lock = locks.setdefault(key, asyncio.Lock())
        users[key] = users.get(key, 0) + 1
        try:
            async with lock:
                # The previous owner may have finished or a new turn may have
                # claimed the quote while this request waited.
                if key != _resolve_session_key(app, event):
                    continue
                yield key
                return
        finally:
            users[key] -= 1
            if not users[key]:
                users.pop(key)
                cleanup_orphan_message_lock(_cleanup_state_for_app(app), key, lock)


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

    fence_claim: EventIdFenceClaim | None = None
    fence = request.app[EVENT_ID_FENCE_KEY]
    fence_key: FenceKey = event.event_id
    receipt_fingerprint = ""
    legacy_alias = (
        not event.event_id and not event.turn_id and event.event in TERMINAL_EVENTS
        and not event.data.get("native_handoff")
        and not _is_native_fallback_event(event) and not _is_intentional_silence_event(event)
        and (_session_key(event) in request.app[SESSION_ALIASES_KEY] or bool(session_alias_keys_for_event(event)))
    )
    if event.event_id or legacy_alias:
        try:
            fingerprint = _event_id_fingerprint(event)
        except (TypeError, ValueError):
            metrics.events_rejected += 1
            return web.json_response(
                {"ok": False, "error": "event payload is not canonicalizable"},
                status=400,
            )
        if legacy_alias:
            # Internal keys cannot collide with a producer-supplied event_id.
            fence_key = ("legacy_terminal", fingerprint)
        fence_claim = await fence.claim(fence_key, fingerprint)
        if fence_claim.kind == "conflict":
            return web.json_response(
                {"ok": False, "error": "event_id payload conflict"},
                status=409,
            )
        if fence_claim.kind == "replay":
            assert fence_claim.entry is not None
            return fence.replay_response(fence_claim.entry)
        if fence_claim.kind == "wait":
            assert fence_claim.entry is not None
            waited = await fence.wait(fence_claim.entry)
            if waited is None:
                return web.json_response(
                    {"ok": False, "error": "event_id owner pending"},
                    status=503,
                )
            status, response_payload = waited
            return web.json_response(response_payload, status=status)
        if fence_claim.kind == "full":
            return web.json_response(
                {"ok": False, "error": "event_id fence unavailable"},
                status=503,
            )
        assert fence_claim.kind == "owner" and fence_claim.entry is not None
    elif _has_legacy_receipt_identity(event):
        # No alias lock is needed just to replay an exact acknowledged
        # delivery. First attempts still use the normal terminal admission.
        receipt_fingerprint = _legacy_terminal_fingerprint(event)
        replay = fence.replay_confirmed_legacy_terminal(receipt_fingerprint)
        if replay is not None:
            return replay

    metrics.events_received += 1
    response_finalized = False
    cancelled_after_runtime_delivery = False
    try:
        async with _event_owner_lock(request.app, event) as session_key:
            if receipt_fingerprint:
                replay = fence.replay_confirmed_legacy_terminal(receipt_fingerprint)
                if replay is not None:
                    return replay
            response, post_lock_task = await _apply_event_locked(request, event, session_key=session_key)
        if isinstance(post_lock_task, RuntimeInteractionDeliveryReservation):
            completion_task = asyncio.create_task(
                _complete_runtime_interaction_delivery(request, post_lock_task)
            )
            try:
                response = await asyncio.shield(completion_task)
                _checkpoint_session(request.app, session_key, _policy_profile_id(event) or "")
            except asyncio.CancelledError:
                response = await completion_task
                cancelled_after_runtime_delivery = True
            post_lock_task = None
        if fence_claim is not None:
            response_payload = _json_response_payload(response)
            unconfirmed_alias = legacy_alias and (
                response.status != 200 or response_payload.get("applied") is not True
            )
            if unconfirmed_alias or (
                _event_has_runtime_admission(event)
                and not _is_exact_runtime_admission_response_payload(
                    response.status, response_payload
                )
            ):
                finalize_task = asyncio.create_task(
                    fence.abandon(fence_key, fence_claim.entry)
                )
            else:
                finalize_task = asyncio.create_task(
                    fence.finalize(
                        fence_key,
                        fence_claim.entry,
                        response.status,
                        response_payload,
                    )
                )
            try:
                await asyncio.shield(finalize_task)
            except asyncio.CancelledError:
                await finalize_task
                response_finalized = True
                raise
            response_finalized = True
        if cancelled_after_runtime_delivery:
            raise asyncio.CancelledError
    except BaseException:
        if fence_claim is not None and not response_finalized:
            await asyncio.shield(
                fence.abandon(fence_key, fence_claim.entry)
            )
        raise
    if post_lock_task is not None and _should_await_card_update(event):
        await post_lock_task
    if _event_is_terminal(event) and post_lock_task is None:
        cleanup_runtime_state(_cleanup_state_for_app(request.app), time.time())
    return response


def _event_has_runtime_admission(event: SidecarEvent) -> bool:
    return (
        event.event == "interaction.requested"
        and type(event.data) is dict
        and "_hfc_runtime_admission" in event.data
    )


def _is_exact_runtime_admission_response_payload(
    status: int, payload: object
) -> bool:
    if (
        status != 200
        or type(payload) is not dict
        or not all(type(key) is str for key in payload)
        or set(payload) != {"ok", "applied", "delivery", "runtime_admission"}
        or payload["ok"] is not True
        or payload["applied"] is not True
        or payload["runtime_admission"] is not True
    ):
        return False
    delivery = payload["delivery"]
    return (
        type(delivery) is dict
        and all(type(key) is str for key in delivery)
        and set(delivery) == {"outcome"}
        and type(delivery["outcome"]) is str
        and delivery["outcome"] == "delivered"
    )


def _event_id_fingerprint(event: SidecarEvent) -> str:
    canonical = {
        "schema_version": event.schema_version,
        "event": event.event,
        "turn_id": event.canonical_turn_id,
        "conversation_id": event.conversation_id,
        "message_id": event.message_id,
        "chat_id": event.chat_id,
        "thread_id": event.thread_id,
        "sequence": event.sequence,
        "created_at": event.created_at,
        "producer": event.producer,
        "phase": event.phase,
        "data": event.data,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_response_payload(response: web.Response) -> dict[str, object]:
    body = response.body
    if not isinstance(body, bytes):
        raise ValueError("event response body is not JSON")
    payload = json.loads(body.decode(response.charset or "utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("event response body is not an object")
    return copy.deepcopy(payload)


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
    card_config = _resolve_session_card_config(
        request.app, route.bot_id if route is not None else None, event
    )
    return {
        "schema": "2.0",
        "config": {
            **card_width_config(card_config.get("width_mode", "default")),
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
        "card_segmented_deliveries",
        "card_segmented_fallbacks",
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
    return _native_handoff_lines_for_app(request.app)


def _native_handoff_lines_for_app(app: web.Application) -> list[str]:
    snapshot = _safe_native_handoff_diagnostics(app)
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


def _native_handoff_ack_capable(
    app: web.Application,
    metadata: dict[str, Any],
    event: SidecarEvent,
) -> bool:
    # Compatibility for existing coordinator callers and contract tests.
    return native_handoff_ack_capable(
        NATIVE_HANDOFF_ACK_AUTH_VERIFIER_KEY in app, metadata, event,
    )


def _prepare_native_handoff_lifecycle(
    app: web.Application,
    identity_key: str,
    *,
    event_created_at: float,
    expected_record: NativeHandoffRecord | None = None,
) -> str:
    try:
        return app[NATIVE_HANDOFF_STORE_KEY].prepare_lifecycle(
            identity_key,
            event_created_at=event_created_at,
            expected_record=expected_record,
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
    card_config: dict[str, Any] | None = None,
) -> asyncio.Task[bool] | None:
    if record.card_state != "pending" or not feishu_message_id:
        return None
    current_repairs: Dict[str, asyncio.Task[bool]] = app[
        NATIVE_HANDOFF_CURRENT_REPAIRS_KEY
    ]
    current = current_repairs.get(identity_key)
    if current is not None and not current.done():
        return current
    config = card_config or {}
    title = config.get("title", app[CARD_TITLE_KEY])
    width_mode = config.get("width_mode", "default")

    async def repair() -> bool:
        card = render_terminal_limit_handoff_card(title, width_mode=width_mode)
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
    return active_session_key(app[SESSIONS_KEY], app[SESSION_ALIASES_KEY], session_key)


def _resolve_session_key(app: web.Application, event: SidecarEvent) -> str:
    sessions = app[SESSIONS_KEY]
    aliases = app[SESSION_ALIASES_KEY]
    if not event.turn_id and not event.event_id and event.event in TERMINAL_EVENTS:
        fingerprint = _legacy_terminal_fingerprint(event)
        if fingerprint:
            prior_key = legacy_reference_owner_key(
                event, _session_key(event), sessions=sessions, aliases=aliases,
                accepts=lambda owner: owner.status in {"completed", "failed"}
                and owner.legacy_terminal_fingerprint == fingerprint,
            )
            if prior_key is not None:
                return prior_key
            # The quote can already belong to another turn when the process
            # fence is absent. The retained exact fingerprint binds the old one.
            profile = _policy_profile_id(event) or "default"
            retained = [key for key, owner in sessions.items()
                        if owner.status in {"completed", "failed"}
                        and owner.legacy_terminal_fingerprint == fingerprint
                        and owner.route_profile_id == profile
                        and legacy_alias_owner_matches(owner, event)]
            if len(retained) == 1:
                return retained[0]
        prior_key = _legacy_checkpoint_replay_key(app, event)
        if prior_key is not None and sessions[prior_key].restored_unconfirmed_terminal:
            return prior_key
    return resolve_session_key(
        event, sessions=sessions, aliases=aliases,
        redirect_aliases=app.get(REDIRECT_SESSION_ALIASES_KEY) or {},
    )


def _legacy_terminal_fingerprint(event: SidecarEvent) -> str:
    try:
        return _event_id_fingerprint(event)
    except (TypeError, ValueError):
        return ""


def _is_legacy_chat_terminal(event: SidecarEvent) -> bool:
    return (
        not event.turn_id and event.event in TERMINAL_EVENTS
        and _delivery_kind(event) in {"", "chat"}
        and not _is_intentional_silence_event(event) and not _is_native_fallback_event(event)
    )


def _has_legacy_receipt_identity(event: SidecarEvent) -> bool:
    return (
        not event.event_id and _is_legacy_chat_terminal(event)
        and (event.data.get("policy_new_turn") is True or bool(_native_handoff_metadata(event)))
    )


async def _remember_confirmed_legacy_terminal(
    app: web.Application, session: CardSession, event: SidecarEvent,
) -> bool:
    if not _has_legacy_receipt_identity(event):
        return False
    fingerprint = _legacy_terminal_fingerprint(event)
    if not fingerprint:
        return False
    confirmed_at = time.time()
    session.confirmed_legacy_terminal_receipts = recent_legacy_terminal_receipts(
        (*session.confirmed_legacy_terminal_receipts, (fingerprint, confirmed_at)), now=confirmed_at,
    )
    await _install_legacy_terminal_receipt(app, fingerprint, confirmed_at=confirmed_at)
    return True


async def _install_legacy_terminal_receipt(app, fingerprint, *, confirmed_at=None):
    remaining_ttl = None
    payload = {"ok": True, "applied": True}
    if confirmed_at is not None:
        age = time.time() - confirmed_at
        if not 0 <= age < LEGACY_TERMINAL_RECEIPT_TTL_SECONDS:
            return
        remaining_ttl = LEGACY_TERMINAL_RECEIPT_TTL_SECONDS - age
        payload = _CONFIRMED_LEGACY_TERMINAL
    fence = app[EVENT_ID_FENCE_KEY]
    key = ("legacy_terminal", fingerprint)
    claim = await fence.claim(key, fingerprint)
    if claim.kind == "owner":
        await fence.finalize(key, claim.entry, 200, payload, remaining_ttl=remaining_ttl)


def _register_session_aliases(
    app: web.Application, event: SidecarEvent, canonical_key: str,
) -> None:
    # Mutation remains in the existing serialized server boundary.
    aliases = app[SESSION_ALIASES_KEY]
    aliases.update(session_alias_assignments(
        event, canonical_key, sessions=app[SESSIONS_KEY], aliases=aliases,
    ))


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

    for aliases_key in (SESSION_ALIASES_KEY, REDIRECT_SESSION_ALIASES_KEY):
        aliases: Dict[str, str] = app.get(aliases_key) or {}
        for alias_key, canonical_key in tuple(aliases.items()):
            if canonical_key == session_key and aliases.get(alias_key) == session_key:
                aliases.pop(alias_key, None)
        aliases.pop(session_key, None)

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


def _checkpoint_client_identity(app, bot_id):
    client = _client_for_bot(app, bot_id)
    config = getattr(client, 'config', None)
    app_id = getattr(config, 'app_id', '')
    base_url = getattr(config, 'base_url', '')
    material = json.dumps([str(app_id), str(base_url), type(client).__module__, type(client).__qualname__])
    return hashlib.sha256(material.encode()).hexdigest()


def _checkpoint_session(app, key, profile_id) -> bool:
    store = app.get(SESSION_STORE_KEY)
    if store is None:
        return True
    session = app[SESSIONS_KEY].get(key)
    mid = app[FEISHU_MESSAGE_IDS_KEY].get(key)
    if session is None or not mid:
        return False
    try:
        aliases = {a:k for a,k in app[SESSION_ALIASES_KEY].items() if k == key}
        store.save(key, session, mid, app[MESSAGE_BOT_IDS_KEY].get(key), profile_id, aliases,
                   _checkpoint_client_identity(app, app[MESSAGE_BOT_IDS_KEY].get(key)),
                   auxiliary_receipt=_checkpoint_auxiliary_receipt(app, key, session, mid))
        if not app[DIAGNOSTICS_KEY].get("card_checkpoint_failures"):
            app[DIAGNOSTICS_KEY]["card_checkpoint_state"] = "ready"
        return True
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        # Existing delivery stays fail-open; never turn a local disk failure into a duplicate send.
        app[DIAGNOSTICS_KEY]["card_checkpoint_state"] = "unavailable"
        app[DIAGNOSTICS_KEY]["card_checkpoint_failures"] = app[DIAGNOSTICS_KEY].get("card_checkpoint_failures", 0) + 1
        return False


def _inherit_confirmed_legacy_terminal_receipts(app, key, profile, current, previous, previous_bot):
    bot_id = app[MESSAGE_BOT_IDS_KEY].get(key)
    same_route = (
        previous is not None and (previous_bot or "") == (bot_id or "")
        and previous.chat_id == current.chat_id and previous.conversation_id == current.conversation_id
    )
    history = previous.confirmed_legacy_terminal_receipts if same_route else ()
    store = app.get(SESSION_STORE_KEY)
    if not history and store is not None and (previous is None or same_route):
        # A receipt-only checkpoint seeds the fence at boot without
        # resurrecting a writable display owner. Reattach it to the next card.
        try:
            for record in store.load(key=key):
                source = record["session"]
                if (record["profile_id"] == profile and (record["bot_id"] or "") == (bot_id or "")
                        and source.chat_id == current.chat_id and source.conversation_id == current.conversation_id
                        and record.get("client_identity") == _checkpoint_client_identity(app, bot_id)):
                    history = source.confirmed_legacy_terminal_receipts
        except (OSError, ValueError, TypeError, RuntimeError, KeyError, AttributeError):
            # An optional history read cannot revoke an acknowledged create.
            return
    if history:
        current.confirmed_legacy_terminal_receipts = recent_legacy_terminal_receipts(
            history + current.confirmed_legacy_terminal_receipts, now=time.time(),
        )


def _checkpoint_auxiliary_receipt(app, key, session, owner_id):
    interaction = session.active_interaction
    if (interaction is None or not interaction.feishu_message_id
            or interaction.feishu_message_id == owner_id):
        return None
    _, card_config, title, profile_id = _session_card_render_context(
        app, session, session_key=key,
    )
    return build_auxiliary_receipt(
        session, owner_id, title=title, profile_id=profile_id,
        presentation=card_config.get("_presentation_mode", "classic"),
        mentions_enabled=card_interaction_mention_enabled(
            card_config, kind=interaction.kind or "",
        ),
        dialect="legacy" if _interaction_mode_for_session_key(app, key) == "callback" else "2.0",
    )

async def _restore_card_checkpoints(app):
    store = app.get(SESSION_STORE_KEY)
    if store is None:
        return
    try:
        records = store.load()
    except (OSError, ValueError):
        app[DIAGNOSTICS_KEY]["card_checkpoint_state"] = "unavailable"
        return
    restored = []
    from types import SimpleNamespace
    for record in records:
        key, session = record['key'], record['session']
        profile = record['profile_id']
        if _policy_decision(app, session.chat_id, profile_id=profile).disposition != CARD_DISPOSITION:
            continue
        data = {'profile_id':profile} if profile else {}
        probe = SidecarEvent.from_dict(dict(schema_version='1', event='message.completed',
            conversation_id=session.conversation_id, message_id=session.message_id, turn_id=record["turn_id"],
            chat_id=session.chat_id, platform='feishu', sequence=0, created_at=time.time(), data=data))
        route = _resolve_route(SimpleNamespace(app=app), probe)
        if route is None or (route.bot_id or None) != (record['bot_id'] or None):
            continue
        if record.get("client_identity") != _checkpoint_client_identity(app, record["bot_id"]):
            continue
        if key != _session_key(probe):
            continue
        for fingerprint, confirmed_at in session.confirmed_legacy_terminal_receipts:
            await _install_legacy_terminal_receipt(app, fingerprint, confirmed_at=confirmed_at)
        if record.get("legacy_terminal_receipts_only") is True:
            continue
        session.route_profile_id = profile or "default"
        if session.pending_reopen_create is not None:
            pending = session.pending_reopen_create
            pending.candidate.route_profile_id = session.route_profile_id
            pending.candidate.legacy_route_thread_id = pending.thread_id
        if (session.legacy_terminal_fingerprint and session.terminal_card_confirmed
                and session.legacy_terminal_start_at is None):
            # Only a confirmed card write can seed the restart receipt. The
            # fingerprint alone may name an accepted but unfinished delivery.
            await _install_legacy_terminal_receipt(app, session.legacy_terminal_fingerprint)
        app[SESSIONS_KEY][key] = session
        app[FEISHU_MESSAGE_IDS_KEY][key] = record['message_id']
        app[MESSAGE_BOT_IDS_KEY][key] = record['bot_id']
        app[SESSION_CARD_CONFIGS_KEY][key] = _resolve_session_card_config(app, record['bot_id'], probe)
        for alias, target in record['aliases'].items():
            if target == key and isinstance(alias, str):
                app[SESSION_ALIASES_KEY][alias] = key
        restored.append((key, session, session.updated_at, record.get('auxiliary_receipt')))
    app[DIAGNOSTICS_KEY]['card_checkpoints_restored'] = len(restored)

    async def refresh():
        for key, session, revision, auxiliary in restored:

            def current():
                return app[SESSIONS_KEY].get(key) is session and session.updated_at == revision
            if not current():
                continue
            display = copy.deepcopy(session)
            if display.status not in {'completed','failed'}:
                display.presentation_state = "reconnecting"
                display.status = 'failed'
                display.display_status = ''
                display.answer_text += '\n\n连接已重建，等待本轮执行状态同步；原授权不会恢复。'
                if display.display_segment.get("active"):
                    display.display_segment["answer"] += '\n\n连接已重建，等待本轮执行状态同步；原授权不会恢复。'
                display.timeline.complete()
            # Keep the confirmed segmented lead intact while restoring the
            # separate interaction receipt on its original message.
            if (session.terminal_disposition != "segmented"
                    and session.terminal_delivery_state not in {"recovered", "recovering", "unknown"}
                    and not (session.restored_unconfirmed_terminal and session.legacy_terminal_fingerprint)):
                try:
                    updated = await asyncio.wait_for(_update_card_for_app(app, app[FEISHU_MESSAGE_IDS_KEY][key],
                        _render_session_card_for_app(app, display, session_key=key), app[MESSAGE_BOT_IDS_KEY].get(key),
                        is_current=current), timeout=10)
                    if not updated:
                        app[DIAGNOSTICS_KEY]["card_restore_update"] = "failed"
                except (asyncio.TimeoutError, OSError):
                    app[DIAGNOSTICS_KEY]["card_restore_update"] = "failed"
            if auxiliary and current():
                # This exact message shares the validated checkpoint's route
                # and application identity. It has no executable state to load.
                try:
                    updated = await asyncio.wait_for(_update_card_for_app(app,
                        auxiliary['message_id'], auxiliary_receipt_card(auxiliary),
                        app[MESSAGE_BOT_IDS_KEY].get(key), is_current=current), timeout=10)
                    if not updated:
                        app[DIAGNOSTICS_KEY]['card_restore_update'] = 'failed'
                except (asyncio.TimeoutError, OSError):
                    app[DIAGNOSTICS_KEY]['card_restore_update'] = 'failed'
    app[SESSION_RESTORE_TASK_KEY] = asyncio.create_task(refresh())


async def _stop_card_restore(app):
    task = app.get(SESSION_RESTORE_TASK_KEY)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _terminal_matches_display(owner: CardSession, event: SidecarEvent) -> bool:
    if event.event == "message.completed":
        answer = normalize_stream_text(str(event.data.get("answer") or "")).strip()
        return owner.status == "completed" and bool(answer) and owner.answer_text == answer
    error = event.data.get("error")
    return (event.event == "message.failed" and owner.status == "failed"
            and isinstance(error, str) and bool(error.strip())
            and (owner.answer_text == error or owner.answer_text.endswith("\n\n> " + error)))


def _unconfirmed_terminal_replay_response() -> web.Response:
    # Keep ownership so Hermes cannot start a second delivery. Report the
    # missing Feishu confirmation separately from the existing applied ACK.
    return web.json_response({"ok": True, "applied": True, "delivery": {"outcome": "unknown"}})


def _legacy_checkpoint_replay_key(app, event: SidecarEvent) -> str | None:
    if (event.turn_id or event.event_id or event.event not in TERMINAL_EVENTS
            or event.data.get("policy_new_turn") is True
            or _is_native_fallback_event(event) or _is_intentional_silence_event(event)):
        return None
    alias_key = _session_key(event)
    owner_key = app[SESSION_ALIASES_KEY].get(alias_key)
    owner = app[SESSIONS_KEY].get(owner_key)
    if (owner is None or owner_key == alias_key
            or not (owner.restored_legacy_terminal or owner.restored_unconfirmed_terminal)
            or owner.legacy_terminal_fingerprint
            or not app[FEISHU_MESSAGE_IDS_KEY].get(owner_key)
            or owner.conversation_id != event.conversation_id
            or not legacy_alias_owner_matches(owner, event)
            or event.sequence != owner.last_sequence):
        return None
    pending = owner.pending_reopen_create
    terminal_at = owner.updated_at if pending is None else pending.predecessor_updated_at
    if (type(terminal_at) in (int, float) and math.isfinite(terminal_at)
            and event.created_at <= terminal_at and _terminal_matches_display(owner, event)):
        return owner_key
    return None


_REOPEN_CREATE_EVENTS = TURN_REOPENING_EVENTS | {"message.started"}
_REOPEN_RECOVERY_EVENTS = _REOPEN_CREATE_EVENTS | TERMINAL_EVENTS | {"subagent.updated"}
_FEISHU_IM_UUID_WINDOW_SECONDS = 60 * 60


def _reopen_generation(metadata) -> str:
    raw = metadata.get("generation")
    return raw if isinstance(raw, str) and re.fullmatch(r"[0-9a-f]{32,64}", raw) else ""


def _pending_reopen_matches(pending: PendingReopenCreate, event: SidecarEvent, generation: str) -> bool:
    candidate = pending.candidate
    sender = event.data.get("sender_open_id")
    tenant_user = event.data.get("admission_user_id")
    thread = explicit_thread_id(event)
    return (
        event.chat_id == candidate.chat_id and event.conversation_id == candidate.conversation_id
        and (not generation or not pending.generation or generation == pending.generation)
        and (not thread or not pending.thread_id or thread == pending.thread_id)
        and (not sender or not candidate.sender_open_id or sender == candidate.sender_open_id)
        and candidate.compare_display_tenant_requester(tenant_user) is not False
    )


def _retain_reopen_predecessor(app: web.Application, key: str, session: CardSession) -> CardSession:
    if session.active_interaction is not None or session.approval_retirements:
        _remember_legacy_owner_receipt(app, key)
        session = copy.copy(session)
        session.active_interaction = None
        session.approval_retirements = []
        app[SESSIONS_KEY][key] = session
    owners = app[INTERACTION_RESULT_SESSION_KEYS_KEY]
    for interaction_id, owner in tuple(owners.items()):
        if owner == key:
            owners.pop(interaction_id, None)
            app[INTERACTION_RESULTS_KEY].pop(interaction_id, None)
    return session


async def _deliver_reopened_create(
    request: web.Request, session_key: str, predecessor: CardSession,
    pending: PendingReopenCreate | None, candidate: CardSession, event: SidecarEvent,
    card: dict[str, Any], bot_id: str, generation: str,
) -> CardDeliveryResult:
    app = request.app
    profile = _policy_profile_id(event) or ""
    previous_id = app[FEISHU_MESSAGE_IDS_KEY][session_key]
    terminal = event.event in TERMINAL_EVENTS
    if pending is None:
        previous_pending = predecessor.pending_reopen_create
        attempted_at = time.time()
        pending = PendingReopenCreate(
            candidate=candidate, start_event=event.event, start_sequence=event.sequence,
            start_at=event.created_at, attempted_at=attempted_at,
            predecessor_updated_at=(
                predecessor.updated_at if previous_pending is None else previous_pending.predecessor_updated_at
            ),
            generation=generation,
            bot_id=bot_id, client_identity=_checkpoint_client_identity(app, bot_id),
            thread_id=_thread_id_for_event(event) or "",
            reply_to_message_id=_reply_to_message_id_for_event(event) or "",
            reply_in_thread=_reply_in_thread_for_event(event),
            streaming=card.get("config", {}).get("streaming_mode") is True,
        )
        # Retain the previous owner through this reservation's retry window.
        predecessor.updated_at = max(predecessor.updated_at, attempted_at)
    elif not pending.uncertain and pending.reply_in_thread and not pending.reply_to_message_id:
        # A definite local refusal let no IM request through. Later verified
        # placement can supply the missing anchor before the first send.
        anchor = _reply_to_message_id_for_event(event)
        if anchor:
            pending = replace(pending, reply_to_message_id=anchor)
    streaming = card.get("config", {}).get("streaming_mode") is True
    staged = replace(
        pending, candidate=pending.candidate if terminal else candidate,
        streaming=pending.streaming if pending.uncertain else streaming,
        terminal_at=event.created_at if terminal else None,
    )
    cannot_retry = pending.attempts and (
        (pending.attempts >= 2 and not terminal)
        or (pending.uncertain and (
            not 0 <= time.time() - pending.attempted_at < _FEISHU_IM_UUID_WINDOW_SECONDS
            or pending.streaming
            or (pending.thread_id and not pending.reply_to_message_id)
            or not callable(getattr(_client_for_bot(app, bot_id), "send_card_delivery", None))
        ))
    )
    if cannot_retry:
        predecessor.pending_reopen_create = staged
        return CardDeliveryResult(None, "unknown" if pending.uncertain else "not_sent")

    # Write the reservation before sending. Cancellation or restart during the
    # request must leave the same UUID and the previous confirmed display.
    if pending.uncertain and streaming:
        # An ambiguous inline create must stay inline even if card settings
        # changed between hooks. A new CardKit entity cannot recover it.
        card["config"]["streaming_mode"] = False
    reservation = replace(staged, attempts=pending.attempts + 1, uncertain=True)
    predecessor.pending_reopen_create = reservation
    if not _checkpoint_session(app, session_key, profile):
        predecessor.pending_reopen_create = staged
        return CardDeliveryResult(None, "unknown" if pending.uncertain else "not_sent")
    delivery = await _send_card(
        request, candidate.chat_id, card, bot_id,
        thread_id=pending.thread_id or None,
        reply_to_message_id=pending.reply_to_message_id or None,
        reply_in_thread=pending.reply_in_thread,
        delivery_key=(
            f"{session_key}:reopen:{previous_id}:{pending.start_event}:"
            f"{pending.start_sequence}:{pending.start_at}"
        ),
        profile_id=profile,
    )
    if delivery.delivered:
        # A UUID replay can return the original placeholder. Confirm the
        # current body on that message before committing display ownership.
        if delivery.message_id == previous_id or (pending.uncertain and not await _update_card_for_app(
                app, str(delivery.message_id), card, bot_id)):
            return CardDeliveryResult(None, "unknown", error_kind="ReopenBodyUnconfirmed")
        candidate.reply_to_message_id = pending.reply_to_message_id
        candidate.reply_in_thread = pending.reply_in_thread
        return delivery
    uncertain = pending.uncertain or delivery.outcome == "unknown" or bool(delivery.retry_count)
    predecessor.pending_reopen_create = replace(reservation, uncertain=uncertain)
    return replace(delivery, outcome="unknown") if uncertain else delivery


def _unconfirmed_reopen_handoff(app: web.Application, event: SidecarEvent, metadata: dict) -> web.Response:
    if not _native_handoff_ack_capable(app, metadata, event):
        return _native_disposition_response()
    record, _ = _begin_native_handoff(
        app, _native_handoff_identity(event),
        feishu_message_id=None, bot_id=None, event_created_at=event.created_at,
        generation=str(metadata.get("generation") or ""), ack_capable=True,
        obligation_key=str(metadata.get("obligation_key") or ""),
        content_hash=str(metadata.get("content_hash") or ""),
        plan_fingerprint=str(metadata.get("plan_fingerprint") or ""),
        route=str(metadata.get("route") or ""),
        target_hash=str(metadata.get("target_hash") or ""),
        provisional_uuid_seed=str(metadata.get("provisional_uuid_seed") or ""),
    )
    return _native_disposition_response(record)


async def _apply_event_locked(request, event, *, session_key, advance_sequence=True):
    app = request.app
    key = session_key
    prior = app[SESSIONS_KEY].get(key)
    prior_message_id = app[FEISHU_MESSAGE_IDS_KEY].get(key)
    prior_bot = app[MESSAGE_BOT_IDS_KEY].get(key)
    retained = None
    retained_aliases = {}
    if (prior is not None and prior.status in {"completed", "failed"}
            and prior.terminal_disposition in {"", "segmented"}
            and app[FEISHU_MESSAGE_IDS_KEY].get(key)):
        retained = {state_key: app[state_key].get(key) for state_key in
                    (SESSIONS_KEY, FEISHU_MESSAGE_IDS_KEY, MESSAGE_BOT_IDS_KEY, SESSION_CARD_CONFIGS_KEY)}
        if _event_starts_new_lifecycle(event) or event.data.get("policy_new_turn") is True:
            for table in (SESSION_ALIASES_KEY, REDIRECT_SESSION_ALIASES_KEY):
                retained_aliases[table] = {alias: owner for alias, owner in app[table].items()
                                           if alias == key or owner == key}
    result = await _apply_event_locked_inner(
        request, event, session_key=session_key, advance_sequence=advance_sequence,
    )
    # A refused/uncertain replacement create must not erase the previous
    # completed answer or let a later fallback reuse its original delivery UUID.
    # Restore display ownership only; no execution or approval is resurrected.
    if retained is not None and result[0].status >= 400 and key not in app[SESSIONS_KEY]:
        display = copy.deepcopy(prior)
        display.active_interaction = None
        display.approval_retirements = []
        retained[SESSIONS_KEY] = display
        for state_key, value in retained.items():
            if value is not None:
                app[state_key][key] = value
        for table, bindings in retained_aliases.items():
            for alias, owner in bindings.items():
                app[table].setdefault(alias, owner)
    _remember_legacy_owner_receipt(request.app, key)
    profile = _policy_profile_id(event)
    if profile is not None:
        current = app[SESSIONS_KEY].get(key)
        message_id = app[FEISHU_MESSAGE_IDS_KEY].get(key)
        if current is not None and current is not prior and message_id and message_id != prior_message_id:
            _inherit_confirmed_legacy_terminal_receipts(app, key, profile, current, prior, prior_bot)
        _checkpoint_session(request.app, key, profile)
    return result


async def _apply_event_locked_inner(
    request: web.Request,
    event: SidecarEvent,
    *,
    session_key: str,
    advance_sequence: bool = True,
) -> tuple[web.Response, Any]:
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
    if session_key in request.app[RUNTIME_INTERACTION_RESERVATIONS_KEY]:
        return web.json_response(
            {"ok": False, "error": "interaction delivery pending"}, status=409
        ), None
    session = sessions.get(session_key)
    if session is not None:
        event = _event_for_session(incoming_event, session)
    event_is_terminal = _event_is_terminal(event)
    legacy_fingerprint = (
        _legacy_terminal_fingerprint(incoming_event)
        if session_key != _session_key(incoming_event) and not incoming_event.turn_id
        and not incoming_event.event_id and incoming_event.event in TERMINAL_EVENTS
        and not _is_intentional_silence_event(incoming_event) and not _is_native_fallback_event(incoming_event)
        else ""
    )
    resume_terminal_state = ""
    if (legacy_fingerprint and session is not None and session.restored_unconfirmed_terminal
            and (session.legacy_terminal_fingerprint == legacy_fingerprint
                 or _legacy_checkpoint_replay_key(request.app, incoming_event) == session_key)):
        resume_terminal_state = session.terminal_delivery_state
    handoff_identity = _native_handoff_identity(incoming_event)
    handoff_metadata = _native_handoff_metadata(incoming_event)
    handoff_generation = str(handoff_metadata.get("generation") or "")
    reopen_generation = _reopen_generation(handoff_metadata)
    starts_new_lifecycle = _event_starts_new_lifecycle(incoming_event)
    pending_reopen = session.pending_reopen_create if session is not None else None
    reopens_direct_terminal = False
    stale_direct_terminal = False
    terminal_fingerprint = ""
    if (
        session is not None
        and session.status in {"completed", "failed"}
        and session.delivery_kind == "chat"
        and session.terminal_disposition in {"", "segmented"}
        and feishu_message_ids.get(session_key)
        and incoming_event.event in TERMINAL_EVENTS
        and not incoming_event.turn_id
        and incoming_event.message_id == session.message_id
        and _delivery_kind(incoming_event) in {"", "chat"}
        and event_conversation_matches(session, incoming_event, allow_omitted_thread=True)
    ):
        marked_as_new = incoming_event.data.get("policy_new_turn") is True
        terminal_start_at = session.legacy_terminal_start_at
        if marked_as_new or terminal_start_at is not None:
            terminal_fingerprint = legacy_fingerprint or _legacy_terminal_fingerprint(incoming_event)
        if terminal_start_at is not None:
            if (terminal_fingerprint and terminal_fingerprint == session.legacy_terminal_fingerprint
                    and session.terminal_card_confirmed):
                metrics.events_applied += 1
                return web.json_response(
                    {"ok": True, "applied": True, "delivery": {"outcome": "delivered"}}
                ), None
            stale_direct_terminal = not marked_as_new or incoming_event.created_at <= terminal_start_at
        if marked_as_new and not stale_direct_terminal:
            # v1 stored the sidecar's display update time, not the producer's
            # terminal timestamp. An older contradictory body is ambiguous.
            previous_at = terminal_start_at if terminal_start_at is not None else (
                pending_reopen.predecessor_updated_at if pending_reopen is not None else session.updated_at
            )
            reopens_direct_terminal = bool(terminal_fingerprint and incoming_event.created_at > previous_at)
            stale_direct_terminal = not reopens_direct_terminal and not _terminal_matches_display(
                session, incoming_event,
            )
    reopens_completed_session = bool(
        session is not None
        and session.status in {"completed", "failed"}
        and (
            reopens_direct_terminal or starts_new_lifecycle
            or (
                event.event in TURN_REOPENING_EVENTS
                and incoming_event.data.get("policy_new_turn") is True
            )
        )
    )
    blocked_reopen = False
    same_reopen_generation = bool(
        pending_reopen and reopen_generation and reopen_generation == pending_reopen.generation
    )
    if pending_reopen is not None and (same_reopen_generation or incoming_event.created_at >= pending_reopen.start_at):
        newer_start = (
            (incoming_event.event == "message.started"
             or (reopens_direct_terminal and pending_reopen.terminal_at is not None
                 and incoming_event.created_at > pending_reopen.terminal_at))
            and incoming_event.created_at > pending_reopen.start_at
            and not same_reopen_generation
            and _pending_reopen_matches(pending_reopen, incoming_event, "")
            and (bool(reopen_generation and pending_reopen.generation)
                 or pending_reopen.terminal_at is not None and incoming_event.created_at > pending_reopen.terminal_at)
        )
        if newer_start:
            pending_reopen = None
        elif incoming_event.event in _REOPEN_RECOVERY_EVENTS:
            blocked_reopen = (
                pending_reopen.terminal_at is not None
                or not _pending_reopen_matches(pending_reopen, incoming_event, reopen_generation)
            )
            reopens_completed_session = not blocked_reopen
        else:
            blocked_reopen = True
    elif pending_reopen is not None:
        # The previous terminal body is still available. An older timestamp
        # alone cannot make that card acknowledge different content.
        blocked_reopen = event_is_terminal and not _terminal_matches_display(session, incoming_event)
        pending_reopen = None
        reopens_completed_session = False

    # Generation fences and terminal dedup precede policy. The domain boundary
    # returns cache updates independently of its final admission decision.
    admission = admit_native_handoff(
        incoming_event,
        NativeTerminalSnapshot(
            native_terminal=bool(
                session is not None
                and session.status in {"completed", "failed"}
                and session.terminal_disposition == "native"
            ),
            cached_record=session.terminal_handoff_record if session is not None else None,
            allow_direct_terminal_start=(
                not blocked_reopen and (session is None or reopens_direct_terminal or (
                    session.status in {"completed", "failed"} and session.terminal_disposition == "native"
                    and session.message_id == incoming_event.message_id and session.delivery_kind == "chat"
                    and event_conversation_matches(session, incoming_event, allow_omitted_thread=True)
                ))
            ),
        ),
        ack_verifier_available=NATIVE_HANDOFF_ACK_AUTH_VERIFIER_KEY in request.app,
        store=request.app[NATIVE_HANDOFF_STORE_KEY],
    )
    lifecycle_fence_recorded = admission.lifecycle_fence_recorded
    if admission.cached_record_update is not None and session is not None:
        session.terminal_handoff_record = admission.cached_record_update
    if admission.fence_restored:
        metrics.native_handoff_fence_restores += 1
    if admission.fence_refused:
        metrics.native_handoff_fence_restore_refusals += 1
    if admission.kind == "reject":
        if admission.reason == "read_unavailable":
            logger.warning("native handoff state could not be read safely")
        metrics.events_rejected += 1
        return web.json_response(
            {"ok": False, "error": "native handoff state unavailable"}, status=503,
        ), None
    if admission.kind == "ignore_lifecycle":
        metrics.events_ignored += 1
        return web.json_response({"ok": True, "applied": False}), None
    if admission.kind == "suppress_terminal":
        metrics.events_applied += 1
        if admission.reason == "stale_replay":
            request.app[DIAGNOSTICS_KEY]["last_terminal_event"] = {
                "message_id_hash": _diagnostic_id_hash(incoming_event.message_id),
                "event": incoming_event.event,
                "sequence": incoming_event.sequence,
                "applied": True,
                "disposition": "native_stale_replay",
            }
        return web.json_response({"ok": True, "applied": True}), None
    prior_handoff = admission.selected_record
    if stale_direct_terminal and not (prior_handoff is not None and prior_handoff.has_exact_delivery_binding):
        metrics.events_ignored += 1
        return _native_disposition_response(), None
    starts_after_legacy_handoff = admission.kind == "start_legacy_terminal"
    if starts_after_legacy_handoff:
        reopens_direct_terminal = True
        reopens_completed_session = session is not None
        terminal_fingerprint = _legacy_terminal_fingerprint(incoming_event)
    elif prior_handoff is not None:
        post_lock_task = _schedule_pending_native_handoff_repair(
            request.app,
            handoff_identity,
            prior_handoff,
            feishu_message_id=feishu_message_ids.get(session_key),
            bot_id=message_bot_ids.get(session_key),
            card_config=request.app[SESSION_CARD_CONFIGS_KEY].get(session_key),
        )
        descriptor = prior_handoff.descriptor()
        if descriptor is not None:
            metrics.events_ignored += 1
            response_payload = {
                "ok": True, "applied": False,
                "disposition": "native", "native_handoff": descriptor,
            }
        else:
            metrics.events_applied += 1
            response_payload = {"ok": True, "applied": True}
        request.app[DIAGNOSTICS_KEY]["last_terminal_event"] = {
            "message_id_hash": _diagnostic_id_hash(incoming_event.message_id),
            "event": incoming_event.event,
            "sequence": incoming_event.sequence,
            "applied": descriptor is None,
            "disposition": (
                "native_pending_ack" if descriptor is not None else "native_deduplicated"
            ),
        }
        return web.json_response(response_payload), post_lock_task

    if resume_terminal_state:
        # The exact previous terminal still owns its delivery even if a
        # subsequent reopen currently has a pending create on the same route.
        reopens_completed_session = False
    elif blocked_reopen:
        metrics.events_ignored += 1
        if not event_is_terminal:
            return web.json_response({"ok": True, "applied": False}), None
        if (pending_reopen is not None and pending_reopen.terminal_at is not None
                and _pending_reopen_matches(pending_reopen, incoming_event, reopen_generation)):
            return _unconfirmed_reopen_handoff(request.app, incoming_event, handoff_metadata), None
        return _native_disposition_response(), None

    if (
        session is None
        and not _native_handoff_ack_capable(request.app, handoff_metadata, incoming_event)
        and _legacy_checkpoint_replay_key(request.app, incoming_event) is not None
    ):
        # The older display record cannot prove which producer event created
        # this terminal state. Preserve the card and leave native delivery open.
        metrics.events_ignored += 1
        return _native_disposition_response(), None

    if session is None or reopens_completed_session:
        if _is_intentional_silence_event(incoming_event) or _is_native_fallback_event(incoming_event):
            if pending_reopen is not None and session is not None:
                session.pending_reopen_create = replace(pending_reopen, terminal_at=incoming_event.created_at)
            metrics.events_ignored += 1
            return web.json_response({"ok": True, "applied": False}), None
        metrics.policy_event_checks += 1
        decision = _policy_decision(
            request.app,
            incoming_event.chat_id,
            profile_id=policy_profile_id,
            chat_type=(
                str(incoming_event.data.get("chat_type", "") or "")
                if isinstance(incoming_event.data, dict) else ""
            ),
        )
        if decision.disposition == NATIVE_DISPOSITION:
            if pending_reopen is not None and session is not None and event_is_terminal:
                session.pending_reopen_create = replace(pending_reopen, terminal_at=incoming_event.created_at)
            if reopens_completed_session and pending_reopen is None and not reopens_direct_terminal:
                _reset_session_for_new_turn(request.app, session_key)
            metrics.native_bypass_events += 1
            return web.json_response(
                {
                    "ok": True,
                    "applied": False,
                    "disposition": NATIVE_DISPOSITION,
                }
            ), None

    if _is_suppressed_system_notice(
        request,
        session=session,
        session_key=session_key,
        event=incoming_event,
        profile_id=policy_profile_id,
    ):
        metrics.events_ignored += 1
        return web.json_response(
            {
                "ok": True,
                "applied": True,
                "suppressed": True,
                "delivery": {"outcome": "accepted"},
            }
        ), None

    advances_legacy_handoff = (
        starts_after_legacy_handoff and prior_handoff is not None and prior_handoff.delivery_state != "lifecycle"
    )
    if (starts_new_lifecycle or advances_legacy_handoff) and not lifecycle_fence_recorded:
        lifecycle_state = _prepare_native_handoff_lifecycle(
            request.app,
            handoff_identity,
            event_created_at=incoming_event.created_at,
            expected_record=prior_handoff if advances_legacy_handoff else None,
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
    # Without a display owner, a reused legacy message ID can refer to an
    # earlier card still in Feishu's UUID cache. Only the exact marked terminal
    # should reuse this create, including after a lost ACK or restart.
    reopen_delivery_key = ""
    if starts_after_legacy_handoff or (
        session is None and incoming_event.data.get("policy_new_turn") is True
        and _is_legacy_chat_terminal(incoming_event)
    ):
        create_fingerprint = terminal_fingerprint or _legacy_terminal_fingerprint(incoming_event)
        if create_fingerprint:
            reopen_delivery_key = f"{session_key}:terminal:{create_fingerprint}"
    reopen_predecessor = None
    if reopens_completed_session:
        # A completed topic session can share the next turn's message id. A
        # stream event is also sufficient evidence of a new turn when Hermes
        # omitted message.started. Policy was checked before mutating handoff
        # state, so a native turn cannot leave an active lifecycle floor.
        # A new lifecycle must not reuse the IM/CardKit create UUID of the
        # completed card. Keep one key across all retries of this create.
        # A lost create ACK can leave a live card on Feishu. The previous
        # confirmed main card and the incoming lifecycle stamp survive retry.
        previous_id = feishu_message_ids.get(session_key) or ""
        reopen_delivery_key = reopen_delivery_key or (
            f"{session_key}:reopen:{previous_id}:{incoming_event.event}:"
            f"{incoming_event.sequence}:{incoming_event.created_at}"
        )
        if (previous_id and session.delivery_kind == "chat" and session.terminal_disposition in {"", "segmented"}
                and (reopens_direct_terminal or pending_reopen is not None or (
                    incoming_event.event in _REOPEN_CREATE_EVENTS and _delivery_kind(incoming_event) in {"", "chat"}
                ))):
            reopen_predecessor = _retain_reopen_predecessor(request.app, session_key, session)
        else:
            _reset_session_for_new_turn(request.app, session_key)
        session = None
        event = _event_for_session(incoming_event, pending_reopen.candidate) if pending_reopen else incoming_event
        event_is_terminal = _event_is_terminal(event)

    if _decline_runtime_interaction_in_text_mode(request.app, event, session_key=session_key):
        # The runtime callback listener can only be completed by a card
        # action.  Claiming ownership while rendering text-only choices leaves
        # Hermes blocked on an Event that the Sidecar has no text-message path
        # to resolve.  Decline before mutating the session so the Hybrid patch
        # falls through to Hermes' native clarify text interceptor, which
        # consumes the first numbered/text reply.
        metrics.events_ignored += 1
        return web.json_response(
            {
                "ok": True,
                "applied": False,
                "interaction_mode": "text",
            }
        ), None

    if _skip_native_text_fallback_interaction(request.app, event, session_key=session_key):
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

    # Reject before committing pending state or sending an unanswerable card.
    preflight_rejection = interaction_preflight_rejection(
        event,
        session,
        advance_sequence=advance_sequence,
        render_disposition=lambda preview: _render_session_card_result_for_app(
            request.app, preview, session_key=session_key,
        ).disposition,
    )
    if preflight_rejection is not None:
        metrics.events_ignored += 1
        return web.json_response({
            "ok": True,
            "applied": False,
            "reason": preflight_rejection,
        }), None

    if event.event == "message.started" and session is not None:
        metrics.events_ignored += 1
        return web.json_response({"ok": True, "applied": False}), None

    if session is None:
        if (event.event == "message.started" or event.event in SESSION_CREATING_EVENTS
                or pending_reopen is not None or _is_compaction_session_start(event)):
            # Abandon stale sessions for the same conversation when a new
            # session is being created.  This handles the interrupt scenario:
            # the gateway interrupts a running turn and starts a new one
            # without sending message.completed for the old turn — the old
            # card would be stuck at "生成中" forever.
            if pending_reopen is None and not _is_independent_notice_event(event):
                await _abandon_stale_sessions_for_chat(
                    request.app,
                    event.chat_id,
                    session_key,
                    event,
                    alias_to_session_key=(
                        session_key if _is_redirect_followup_event(event) else None
                    ),
                )
            session = copy.deepcopy(pending_reopen.candidate) if pending_reopen else CardSession(
                conversation_id=event.conversation_id,
                message_id=event.message_id,
                chat_id=event.chat_id,
                route_profile_id=_policy_profile_id(event) or "default",
                legacy_route_thread_id=explicit_thread_id(event),
            )
            if reopen_predecessor is None:
                sessions[session_key] = session
            retry_initial_event = pending_reopen is not None and (
                event.event == pending_reopen.start_event and event.sequence == pending_reopen.start_sequence
                and event.created_at == pending_reopen.start_at
            )
            applied = (session.apply(event) if pending_reopen is None or event.event != "message.started"
                       else False)
            if applied and reopen_predecessor is None:
                _register_session_aliases(request.app, incoming_event, session_key)
            if applied or retry_initial_event:
                is_cron_completed = (
                    event.event == "message.completed" and _delivery_kind(event) == "cron"
                )
                if pending_reopen is None:
                    route = _resolve_route(request, event)
                else:
                    route = None
                    try:
                        if _checkpoint_client_identity(request.app, pending_reopen.bot_id) == pending_reopen.client_identity:
                            route = RouteResult(pending_reopen.bot_id, "reopen")
                    except Exception:
                        pass
                if route is None:
                    _cleanup_failed_session_state(request.app, session_key, session)
                    if reopen_predecessor is not None and event_is_terminal:
                        if pending_reopen is not None:
                            reopen_predecessor.pending_reopen_create = replace(
                                pending_reopen, terminal_at=incoming_event.created_at,
                            )
                        metrics.events_ignored += 1
                        return _unconfirmed_reopen_handoff(request.app, incoming_event, handoff_metadata), None
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
                if reopen_predecessor is None:
                    request.app[SESSION_CARD_CONFIGS_KEY][session_key] = session_card_config
                session.refresh_display_status_source(StatusConfig.from_mapping(session_card_config.get("status")))
                render_result = _render_session_card_result_for_app(
                    request.app, session, session_key=session_key, card_config_override=session_card_config,
                )
                if event_is_terminal and render_result.disposition == "native":
                    if pending_reopen is not None:
                        reopen_predecessor.pending_reopen_create = replace(
                            pending_reopen, terminal_at=incoming_event.created_at,
                        )
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
                if event.event == "interaction.requested" and session.active_interaction is not None:
                    session.active_interaction.thread_id = _thread_id_for_event(event) or ""
                    session.active_interaction.reply_to_message_id = _reply_to_message_id_for_event(event) or ""
                    session.active_interaction.reply_in_thread = _reply_in_thread_for_event(event)
                if reopen_predecessor is not None:
                    delivery = await _deliver_reopened_create(
                        request, session_key, reopen_predecessor, pending_reopen,
                        session, event, render_result.card, route.bot_id or "", reopen_generation,
                    )
                else:
                    delivery = await _send_card(
                        request,
                        event.chat_id,
                        render_result.card,
                        route.bot_id,
                        thread_id=_thread_id_for_event(event),
                        reply_to_message_id=_reply_to_message_id_for_event(event),
                        reply_in_thread=_reply_in_thread_for_event(event),
                        delivery_key=reopen_delivery_key or session_key,
                        delivery_kind=_delivery_kind(event)
                        or ("notice" if event.event == "system.notice" else "chat"),
                        profile_id=_policy_profile_id(event),
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
                    if reopen_predecessor is not None and event_is_terminal:
                        metrics.events_ignored += 1
                        return _unconfirmed_reopen_handoff(request.app, incoming_event, handoff_metadata), None
                    metrics.events_rejected += 1
                    return web.json_response(
                        {
                            "ok": False,
                            "error": "feishu send failed",
                            "delivery": _delivery_payload(delivery),
                        },
                        status=502,
                    ), None
                if reopen_predecessor is not None:
                    _reset_session_for_new_turn(request.app, session_key)
                    sessions[session_key] = session
                    if pending_reopen is not None and pending_reopen.uncertain:
                        # Keep subsequent IM updates in the dialect of the
                        # now-confirmed inline create.
                        session_card_config["streaming_mode"] = False
                    request.app[SESSION_CARD_CONFIGS_KEY][session_key] = session_card_config
                    placement = reopen_predecessor.pending_reopen_create
                    if placement and placement.reply_to_message_id:
                        _register_session_aliases(request.app, replace(
                            incoming_event, thread_id=placement.thread_id,
                            data={**incoming_event.data, "reply_to_message_id": placement.reply_to_message_id},
                        ), session_key)
                    _register_session_aliases(request.app, incoming_event, session_key)
                    applied = True
                message_id = str(delivery.message_id)
                feishu_message_ids[session_key] = message_id
                message_bot_ids[session_key] = route.bot_id
                if (
                    event_is_terminal
                    and render_result.disposition == "segmented"
                    and render_result.segments
                ):
                    # A card-less terminal turn still delivers every
                    # continuation card before the response commits. Any
                    # failure keeps the native handoff: the just-sent main
                    # card becomes the placeholder and the gateway carries
                    # the full answer, so content never goes missing.
                    segments_delivered = await _send_segment_cards(
                        request.app,
                        chat_id=event.chat_id,
                        segments=render_result.segments,
                        session_key=session_key,
                        main_message_id=message_id,
                        bot_id=route.bot_id,
                        thread_id=(pending_reopen.thread_id or None)
                        if pending_reopen else _thread_id_for_event(event),
                        reply_to_message_id=session.reply_to_message_id or None
                        if pending_reopen else _reply_to_message_id_for_event(event),
                        reply_in_thread=session.reply_in_thread if pending_reopen else _reply_in_thread_for_event(event),
                        profile_id=_policy_profile_id(event),
                    )
                    if segments_delivered:
                        session.terminal_disposition = "segmented"
                        session.terminal_limit_reason = render_result.limit_reason
                    else:
                        metrics.card_segmented_fallbacks += 1
                        fallback_result = _segmented_fallback_result(
                            request.app, session, render_result, session_key=session_key
                        )
                        await _update_card_for_app(
                            request.app, message_id, fallback_result.card, route.bot_id,
                            is_current=lambda: (
                                request.app[SESSIONS_KEY].get(session_key) is session
                            ),
                        )
                        handoff_record, handoff_created = _begin_native_handoff(
                            request.app,
                            handoff_identity,
                            feishu_message_id=message_id,
                            bot_id=route.bot_id,
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
                            metrics.events_rejected += 1
                            return web.json_response(
                                {"ok": False, "error": "native handoff state unavailable"},
                                status=503,
                            ), None
                        session.terminal_handoff_record = handoff_record
                        session.terminal_disposition = "native"
                        session.terminal_limit_reason = fallback_result.limit_reason
                        if not handoff_created and handoff_record.descriptor() is None:
                            metrics.events_applied += 1
                        return _native_disposition_response(
                            handoff_record, duplicate=not handoff_created
                        ), None
                if render_result.card.get("schema") != "2.0":
                    session.legacy_owner_receipt = static_legacy_receipt(render_result.card)
                if _is_heartbeat_notice(event):
                    # A heartbeat is transient by nature: arm the recall deadline now and re-arm it
                    # on every later edit, so it disappears once it stops carrying news.
                    _schedule_heartbeat_recall(
                        request.app,
                        session_key=session_key,
                        message_id=message_id,
                        bot_id=route.bot_id,
                        session=session,
                    )
                _ensure_card_animation(
                    request.app,
                    session_key=session_key,
                    session=session,
                    feishu_message_id=message_id,
                    bot_id=route.bot_id,
                )
                if event.event == "interaction.requested":
                    if session.active_interaction is not None:
                        session.active_interaction.feishu_message_id = message_id
                    _store_interaction_result(request.app, session)
                if event_is_terminal:
                    session.terminal_delivery_state = "delivered"
                    if reopens_direct_terminal and pending_reopen is None:
                        session.legacy_terminal_start_at = incoming_event.created_at
                        legacy_fingerprint = terminal_fingerprint
                    session.legacy_terminal_fingerprint = legacy_fingerprint
                    await _remember_confirmed_legacy_terminal(request.app, session, incoming_event)
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
                if _session_has_runtime_admission(session):
                    response_payload = {
                        "ok": True,
                        "applied": True,
                        "delivery": {"outcome": "delivered"},
                        "runtime_admission": True,
                    }
                else:
                    response_payload["interaction_mode"] = _interaction_mode_for_session_key(
                        request.app,
                        session_key,
                    )
            if (applied and reopen_predecessor is not None and event.event == "message.completed"
                    and render_result.disposition in {"card", "segmented"}
                    and isinstance(session_card_config.get("completion_notify"), dict)
                    and session_card_config["completion_notify"].get("enabled") is True):
                # The terminal create/recovery already confirmed the body. Schedule only
                # its push, using the recovered route even if the later hook omitted it.
                notice_event = replace(event, thread_id=pending_reopen.thread_id) if pending_reopen else event
                push = partial(_notify_confirmed_completion, request.app, session_key, session,
                               notice_event, card_config=session_card_config, bot_id=route.bot_id)
                controller = _flush_controller_for_session(request.app, session_key)
                return web.json_response(response_payload), _schedule_terminal_work(
                    request.app, session_key, controller, push,
                )
            return web.json_response(response_payload), None
        metrics.events_ignored += 1
        return web.json_response({"ok": True, "applied": False}), None

    feishu_message_id = feishu_message_ids.get(session_key)
    if _would_apply(session, event) and feishu_message_id is None:
        if _is_intentional_silence_event(event) or _is_native_fallback_event(event):
            if session.apply(event, advance_sequence=advance_sequence):
                session.terminal_delivery_state = "delivered"
            metrics.events_ignored += 1
            return web.json_response({"ok": True, "applied": False}), None
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
    active_interaction = session.active_interaction
    interaction_checked_at = time.time()
    if (
        active_interaction is not None
        and active_interaction.expire(interaction_checked_at)
    ):
        _mark_interaction_expired_locked(
            request.app,
            session_key,
            session,
            now=interaction_checked_at,
        )
    continuation_predecessor = (
        copy.deepcopy(display_view(session))
        if session.display_segment.get("pending")
        and event.event in {"answer.delta", "thinking.delta", "tool.updated", "subagent.updated", "message.completed", "message.failed"}
        else None
    )
    applied = bool(resume_terminal_state) or session.apply(event, advance_sequence=advance_sequence)
    if applied and not resume_terminal_state:
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
    if terminal_already_handled and session.terminal_disposition in {"native", "segmented", "silent", "native_fallback"}:
        metrics.events_applied += 1
        return web.json_response({"ok": True, "applied": True}), None
    if terminal_already_handled:
        applied = True
    render_result: CardRenderResult | None = None
    handoff_record: NativeHandoffRecord | None = None
    if resume_terminal_state:
        render_result = _existing_owner_render_result(
            request.app, session, _render_session_card_result_for_app(request.app, session),
            session_key=session_key,
        )
        if render_result.disposition != "card":
            # A different card limit on restart cannot revoke the prior
            # ownership or prove that its earlier writes never landed.
            return _unconfirmed_terminal_replay_response(), None
    elif applied and not terminal_already_handled:
        is_silence_or_fallback = _is_intentional_silence_event(event) or _is_native_fallback_event(event)
        render_result = _render_session_card_result_for_app(request.app, session)
        if (
            not is_silence_or_fallback
            and needs_continuation(session, event)
            and render_result.disposition == "card"
        ):
            await _open_display_continuation(
                request, session_key, session, render_result.card,
                profile_id=_policy_profile_id(event),
                predecessor_snapshot=continuation_predecessor,
            )
            feishu_message_id = feishu_message_ids.get(session_key)
            render_result = _render_session_card_result_for_app(request.app, session)
        if event.event != "interaction.requested":
            render_result = _existing_owner_render_result(request.app, session, render_result, session_key=session_key)
        if (
            event_is_terminal
            and not is_silence_or_fallback
            and render_result.disposition == "segmented"
        ):
            # Opt-in over-limit terminal segmentation (card.over_limit). The
            # main card and every continuation card were verified in limit at
            # render time; deliver them inside the turn so the event response
            # still decides between segmented cards and the native handoff.
            if not feishu_message_id:
                # No live card exists to carry the lead and statistics.
                metrics.card_segmented_fallbacks += 1
                render_result = _segmented_fallback_result(
                    request.app, session, render_result, session_key=session_key
                )
            else:
                delivered = False
                try:
                    delivered = await _deliver_segmented_terminal(
                        request,
                        session_key,
                        session,
                        render_result,
                        feishu_message_id=feishu_message_id,
                        bot_id=message_bot_ids.get(session_key),
                        event=event,
                    )
                except Exception:
                    # A segmented attempt must never lose content: fall back
                    # to the native handoff on any unexpected failure.
                    delivered = False
                if delivered:
                    session.terminal_disposition = "segmented"
                    session.terminal_limit_reason = render_result.limit_reason
                    session.terminal_delivery_state = "delivered"
                    await _remember_confirmed_legacy_terminal(request.app, session, incoming_event)
                else:
                    metrics.card_segmented_fallbacks += 1
                    render_result = _segmented_fallback_result(
                        request.app, session, render_result, session_key=session_key
                    )
        if (
            event_is_terminal
            and not is_silence_or_fallback
            and render_result.disposition == "native"
        ):
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
                    card_config=request.app[SESSION_CARD_CONFIGS_KEY].get(session_key),
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
        placement = interaction_reply_placement(
            incoming_event,
            session_reply_to_message_id=session.reply_to_message_id,
            session_reply_in_thread=session.reply_in_thread,
        )
        if interaction is not None:
            interaction.thread_id = placement.thread_id or ""
            interaction.reply_to_message_id = placement.reply_to_message_id or ""
            interaction.reply_in_thread = placement.reply_in_thread
        if _session_has_runtime_admission(session):
            if rollback_session_snapshot is None or interaction is None:
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "interaction admission unavailable"},
                    status=503,
                ), None
            descriptor = dict(interaction.runtime_admission)
            fingerprint = _runtime_admission_fingerprint(descriptor)
            if not fingerprint:
                _restore_session_snapshot(session, rollback_session_snapshot)
                metrics.events_rejected += 1
                return web.json_response(
                    {"ok": False, "error": "interaction admission unavailable"},
                    status=503,
                ), None
            owner = object()
            reservation = RuntimeInteractionDeliveryReservation(
                owner=owner,
                session_key=session_key,
                session=session,
                interaction=interaction,
                admission_fingerprint=fingerprint,
                sequence=event.sequence,
                rollback_session=rollback_session_snapshot,
                card=copy.deepcopy(render_result.card),
                chat_id=event.chat_id,
                bot_id=bot_id,
                thread_id=placement.thread_id,
                reply_to_message_id=placement.reply_to_message_id,
                reply_in_thread=placement.reply_in_thread,
                predecessor_message_id=feishu_message_id,
                delivery_key=f"{session_key}:interaction:{interaction_id}",
            )
            request.app[RUNTIME_INTERACTION_RESERVATIONS_KEY][session_key] = reservation
            return web.json_response(
                {"ok": False, "error": "interaction delivery pending"}, status=503
            ), reservation
        delivery = await _send_card(
            request,
            event.chat_id,
            render_result.card,
            bot_id,
            thread_id=placement.thread_id,
            reply_to_message_id=placement.reply_to_message_id,
            reply_in_thread=placement.reply_in_thread,
            delivery_key=f"{session_key}:interaction:{interaction_id}",
            delivery_kind="interaction",
            profile_id=_policy_profile_id(event),
        )
        delivered_interaction = session.active_interaction
        if delivery.delivered and delivered_interaction is not None:
            # Record the approval card's id so its timeout can refresh this card (#314).
            delivered_interaction.feishu_message_id = str(delivery.message_id or "")
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

        animation_task = request.app[CARD_ANIMATION_TASKS_KEY].pop(
            session_key, None
        )
        if rollback_session_snapshot is not None:
            await _finalize_interaction_predecessor(
                request.app,
                session_key=session_key,
                predecessor_message_id=feishu_message_id,
                bot_id=bot_id,
                predecessor_snapshot=rollback_session_snapshot,
                animation_task=animation_task,
            )
        _store_interaction_result(request.app, session)
        metrics.events_applied += 1
        if _session_has_runtime_admission(session):
            return web.json_response(
                {
                    "ok": True,
                    "applied": True,
                    "delivery": {"outcome": "delivered"},
                    "runtime_admission": True,
                }
            ), None
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
    terminal_receipt_update = None
    terminal_unresolved_receipt = (
        event_is_terminal and not terminal_already_handled
        and session.active_interaction is not None
        and session.active_interaction.status == "failed"
    )
    if applied and (event.event.startswith("interaction.") or terminal_unresolved_receipt):
        _store_interaction_result(request.app, session)
        # Callback responses replace their own legacy card in Feishu. Gateway
        # text completions and failure events have no such response: settle the
        # actual auxiliary message explicitly rather than leaving dead controls.
        receipt = session.active_interaction
        if (receipt is not None and receipt.feishu_message_id
                and receipt.status in {"completed", "failed"}
                # An interaction-first owner receives the full terminal body
                # through its existing dialect-safe path. A receipt-only PATCH
                # after that update would erase the answer.
                and (not terminal_unresolved_receipt
                     or receipt.feishu_message_id != feishu_message_id)
                and (event.event == "interaction.failed"
                     or event.event == "interaction.completed" and advance_sequence
                     or terminal_unresolved_receipt)):
            receipt_snapshot = copy.deepcopy(session)
            receipt_snapshot.display_segment = {}
            receipt_snapshot.active_interaction.receipt_fingerprint = ""
            # This snapshot targets the auxiliary receipt, not the historical
            # body owner. A restored legacy owner must not change its dialect.
            receipt_snapshot.legacy_owner_receipt = {}
            if _interaction_mode_for_session_key(request.app, session_key) == "callback":
                receipt_card = _render_interaction_callback_card_for_app(
                    request.app, receipt_snapshot, session_key=session_key
                )
            else:
                receipt_card = _render_static_display_card(
                    request.app, receipt_snapshot, session_key=session_key,
                    note="交互结果已记录，后续进展以回复卡为准",
                    display_state="display_receipt",
                )
            fingerprint = approval_receipt_fingerprint(receipt_snapshot, receipt_snapshot.active_interaction)
            async def update_receipt():
                if receipt_card is None or not inspect_card_limits(receipt_card).safe:
                    request.app[DIAGNOSTICS_KEY]["last_interaction_receipt_update"] = "failed"
                    return
                updated = await _update_card_for_app(
                    request.app, receipt.feishu_message_id, receipt_card,
                    message_bot_ids.get(session_key),
                    is_current=lambda: sessions.get(session_key) is session and session.active_interaction is receipt
                    and (not fingerprint or fingerprint == approval_receipt_fingerprint(session, receipt)),
                )
                if (updated and fingerprint and receipt.status == "failed"
                        and receipt.feishu_message_id != feishu_message_id
                        and sessions.get(session_key) is session and session.active_interaction is receipt
                        and fingerprint == approval_receipt_fingerprint(session, receipt)):
                    receipt.receipt_fingerprint = fingerprint
                request.app[DIAGNOSTICS_KEY]["last_interaction_receipt_update"] = "delivered" if updated else "failed"

            if terminal_unresolved_receipt:
                # Reuse the one managed terminal task; optional receipt cleanup
                # must not delay the terminal ACK or its actual final answer.
                terminal_receipt_update = update_receipt
            else:
                await update_receipt()
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
        is_silence_or_fallback = _is_intentional_silence_event(event) or _is_native_fallback_event(event)
        if event_is_terminal and not is_silence_or_fallback:
            _store_card_summary(request.app, event, session, feishu_message_id)
        is_terminal = event_is_terminal
        controller = _flush_controller_for_session(request.app, session_key)
        bot_id = message_bot_ids.get(session_key)
        owner_generation = session.display_segment.get("generation", 0)
        if is_silence_or_fallback:
            animation_task = request.app[CARD_ANIMATION_TASKS_KEY].pop(session_key, None)
            if animation_task is not None:
                animation_task.cancel()
        else:
            _ensure_card_animation(
                request.app,
                session_key=session_key,
                session=session,
                feishu_message_id=feishu_message_id,
                bot_id=bot_id,
            )

        terminal_target = TerminalTarget(
            session_key=session_key, message_id=feishu_message_id,
            chat_id=session.chat_id, bot_id=bot_id,
            thread_id=_thread_id_for_event(event) or None,
            reply_to_message_id=session.reply_to_message_id or None,
            reply_in_thread=session.reply_in_thread,
            profile_id=_policy_profile_id(event),
            event=event.event, sequence=event.sequence,
        ) if is_terminal else None
        resume_recovery = resume_terminal_state in {"recovering", "unknown"}
        allow_terminal_recovery = not is_silence_or_fallback and (
            not resume_terminal_state or (
                resume_terminal_state not in {"idle", "failed"} and terminal_target is not None
                and _can_replay_terminal_recovery(request.app, terminal_target, session.updated_at)
            )
        )
        if resume_recovery and not allow_terminal_recovery:
            # An ambiguous create cannot be resolved safely outside the UUID
            # window or without the same transport and reply placement.
            return _unconfirmed_terminal_replay_response(), None
        completion_card_config = (
            request.app[SESSION_CARD_CONFIGS_KEY].get(session_key)
            if event.event == "message.completed" else None
        )

        async def _render_and_update() -> bool:
            # A new turn may reuse the key while this terminal PATCH retries.
            # Keep recovery content and routing bound to the accepted old turn.
            latest_session = session if is_terminal else sessions.get(session_key)
            if latest_session is None:
                return False
            if latest_session is not session or session.display_segment.get("generation", 0) != owner_generation:
                return False
            interaction = latest_session.active_interaction
            refresh = OwnerRefreshState(
                event_kind=event.event,
                is_terminal=is_terminal,
                owner_message_id=feishu_message_id,
                interaction_present=interaction is not None,
                interaction_status=interaction.status if interaction is not None else None,
                interaction_message_id=interaction.feishu_message_id if interaction is not None else "",
                has_receipt_fingerprint=bool(interaction and interaction.receipt_fingerprint),
                has_legacy_owner_receipt=bool(latest_session.legacy_owner_receipt),
            )
            # Full replacement would wipe in-progress form input.
            if refresh.freezes_inputs:
                return False
            latest_card = render_result.card
            if refresh.refresh_failed_receipt_first:
                latest_card = _render_session_card_for_app(
                    request.app, latest_session, session_key=session_key)
            card_source = refresh.card_source(
                lambda: _interaction_mode_for_session_key(request.app, session_key)
            )
            if card_source == "separate_receipt":
                return True
            if card_source == "callback_owner":
                latest_card = _render_interaction_callback_card_for_app(
                    request.app, latest_session, session_key=session_key
                )
            elif card_source == "text_receipt_owner":
                latest_card = _render_static_display_card(
                    request.app, copy.deepcopy(latest_session), session_key=session_key,
                    note="交互结果已记录，后续进展以回复卡为准",
                    display_state="display_receipt",
                ) or latest_card
            if is_terminal and render_result.disposition == "card":
                if not is_silence_or_fallback:
                    await _populate_subscription_usage(request.app, latest_session)
                populated_result = _render_session_card_result_for_app(
                    request.app, latest_session
                )
                populated_result = _existing_owner_render_result(request.app, latest_session, populated_result, session_key=session_key)
                if populated_result.disposition == "card":
                    latest_card = populated_result.card
                else:
                    # The terminal response has already accepted card ownership.
                    # Drop only the optional late footer data rather than losing the
                    # full answer or switching disposition after Hermes decided.
                    latest_session.subscription_usage = ""
                    bounded_result = _render_session_card_result_for_app(
                        request.app, latest_session
                    )
                    bounded_result = _existing_owner_render_result(request.app, latest_session, bounded_result, session_key=session_key)
                    if bounded_result.disposition == "card":
                        latest_card = bounded_result.card
            if terminal_target is not None:
                terminal_outcome = await deliver_terminal(
                    TerminalDeliveryPlan(
                        terminal_target,
                        "card" if is_silence_or_fallback else render_result.disposition,
                        latest_card,
                        allow_recover=allow_terminal_recovery,
                        resume_recovery=resume_recovery,
                    ),
                    latest_session,
                    _AppTerminalTransport(request.app, latest_session, replay=bool(resume_terminal_state)),
                    is_current=lambda: session.display_segment.get("generation", 0) == owner_generation,
                )
                updated = terminal_outcome.updated
                if terminal_outcome.superseded:
                    request.app[DIAGNOSTICS_KEY]["last_terminal_delivery"] = {
                        "message_id_hash": _diagnostic_id_hash(terminal_target.message_id),
                        "event": terminal_target.event,
                        "outcome": "superseded",
                        "error_kind": "owner_changed",
                    }
                    return False
                if terminal_outcome.recovery is not None:
                    request.app[DIAGNOSTICS_KEY]["last_terminal_delivery"] = {
                        "message_id_hash": _diagnostic_id_hash(terminal_target.message_id),
                        "event": terminal_target.event,
                        "outcome": latest_session.terminal_delivery_state,
                        "error_kind": terminal_outcome.recovery.error_kind,
                    }
                if terminal_outcome.commit_native:
                    _commit_native_handoff(request.app, handoff_identity, handoff_record)
                if sessions.get(session_key) is latest_session:
                    # Commit the actual write result before optional notices
                    # or receipt cleanup can delay the terminal task.
                    confirmed_update = (
                        updated and render_result.disposition == "card" and not is_silence_or_fallback
                        and await _remember_confirmed_legacy_terminal(request.app, latest_session, incoming_event)
                    )
                    if confirmed_update or legacy_fingerprint:
                        _checkpoint_session(request.app, session_key, _policy_profile_id(event) or "")
            else:
                updated = await _update_card_for_app(
                    request.app, feishu_message_id, latest_card, bot_id,
                    notice_update=event.event == "system.notice",
                    is_current=lambda: session.display_segment.get("generation", 0) == owner_generation,
                )
            if updated and not is_terminal and _is_heartbeat_notice(event):
                # Each heartbeat refresh pushes the recall deadline out; the card only goes away
                # once the refreshes stop.
                _schedule_heartbeat_recall(
                    request.app,
                    session_key=session_key,
                    message_id=feishu_message_id,
                    bot_id=bot_id,
                    session=latest_session,
                )
            if (updated and completion_card_config is not None and not is_silence_or_fallback
                    and render_result.disposition in {"card", "segmented"}):
                # Notify for the confirmed turn, before any optional old-card I/O.
                await _notify_confirmed_completion(
                    request.app, session_key, latest_session, event,
                    card_config=completion_card_config, bot_id=bot_id,
                )
            if is_terminal and render_result.disposition == "card":
                try:
                    # Receipt compaction can follow retirement once the new
                    # owner has already received the terminal body.
                    retirement = request.app[CONTINUATION_RETIREMENT_TASKS_KEY].get(
                        (id(latest_session), owner_generation)
                    )
                    if retirement is not None:
                        await asyncio.shield(retirement)
                        if (sessions.get(session_key) is not latest_session
                                or feishu_message_ids.get(session_key) != feishu_message_id
                                or message_bot_ids.get(session_key) != bot_id
                                or latest_session.display_segment.get("generation", 0) != owner_generation):
                            return updated
                    await _settle_approval_displays(request.app, session_key, latest_session)
                except Exception:
                    request.app[DIAGNOSTICS_KEY]["last_approval_display_cleanup"] = "retained"
            if terminal_receipt_update is not None:
                await terminal_receipt_update()
            if is_terminal and render_result.disposition == "card":
                receipt = latest_session.active_interaction
                if (updated and latest_session.terminal_delivery_state == "delivered"
                        and request.app[SESSIONS_KEY].get(session_key) is latest_session
                        and receipt is not None and receipt.status in {"completed", "failed"}
                        and receipt.feishu_message_id != feishu_message_id
                        and receipt.receipt_fingerprint
                        and receipt.receipt_fingerprint == approval_receipt_fingerprint(latest_session, receipt)):
                    # The answer was delivered before the optional receipt I/O.
                    # Compact only after its full scope has an independent ACK.
                    compact = _render_session_card_for_app(request.app, latest_session, session_key=session_key)
                    if compact != latest_card and inspect_card_limits(compact).safe:
                        await _update_card_for_app(request.app, feishu_message_id, compact, bot_id,
                            is_current=lambda: request.app[SESSIONS_KEY].get(session_key) is latest_session
                            and request.app[FEISHU_MESSAGE_IDS_KEY].get(session_key) == feishu_message_id
                            and latest_session.display_segment.get("generation", 0) == owner_generation
                            and latest_session.active_interaction is receipt)
            if is_terminal and request.app[SESSIONS_KEY].get(session_key) is latest_session:
                _checkpoint_session(request.app, session_key, _policy_profile_id(event) or "")
            return updated

        if is_terminal:
            if not resume_recovery and session.terminal_disposition != "segmented":
                session.terminal_delivery_state = "pending"
            await controller.drain(_final_drain_timeout_seconds(request.app, session_key))
            if legacy_fingerprint and render_result.disposition in {"card", "segmented"} and not is_silence_or_fallback:
                session.legacy_terminal_fingerprint = legacy_fingerprint
            current_task = _schedule_terminal_work(request.app, session_key, controller, _render_and_update)
            session.restored_unconfirmed_terminal = False
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
        if _session_has_runtime_admission(session):
            return web.json_response(
                {
                    "ok": True,
                    "applied": True,
                    "delivery": {"outcome": "delivered"},
                    "runtime_admission": True,
                }
            ), post_lock_task
        response_payload["interaction_mode"] = _interaction_mode_for_session_key(
            request.app,
            session_key,
        )
    return web.json_response(response_payload), post_lock_task


async def _open_display_continuation(
    request, session_key, session, card, *, profile_id, predecessor_snapshot=None,
) -> None:
    """Commit a new display owner only after a confirmed, same-route send.

    Called under the turn lock. A failed/uncertain create gets no per-delta
    retries with fresh identities; the existing owner retains the full content.
    """
    app = request.app
    state = session.display_segment
    old_animation = app[CARD_ANIMATION_TASKS_KEY].pop(session_key, None)
    if old_animation is not None:
        old_animation.cancel()
        await asyncio.gather(old_animation, return_exceptions=True)
    profile = profile_id or ""
    previous_message_id = app[FEISHU_MESSAGE_IDS_KEY].get(session_key)
    bot_id = app[MESSAGE_BOT_IDS_KEY].get(session_key)
    # The accepted interaction already checkpointed the boundary. Do not save
    # a newly applied terminal event before its platform create has a result.
    delivery = await _send_card_for_app(
        app, session.chat_id, card, bot_id,
        thread_id=state["thread_id"] or None,
        reply_to_message_id=state["reply_to_message_id"] or session.reply_to_message_id or None,
        reply_in_thread=state["reply_in_thread"] or session.reply_in_thread,
        delivery_key=f"{session_key}:continuation:{state['interaction_id']}:{state['boundary_sequence']}",
        delivery_kind="chat",
        profile_id=profile_id,
    )
    state["pending"] = False
    if delivery.delivered:
        state["generation"] += 1
        state["active"] = True
        if state["reply_in_thread"] and state["reply_to_message_id"]:
            session.reply_to_message_id = state["reply_to_message_id"]
        session.legacy_owner_receipt = {}
        app[FEISHU_MESSAGE_IDS_KEY][session_key] = delivery.message_id
        app[DIAGNOSTICS_KEY]["last_continuation_delivery"] = "delivered"
    else:
        state["failed"] = True
        app[DIAGNOSTICS_KEY]["last_continuation_delivery"] = delivery.outcome
    _checkpoint_session(app, session_key, profile)
    if (not delivery.delivered or predecessor_snapshot is None
            or predecessor_snapshot.legacy_owner_receipt or not previous_message_id
            or previous_message_id == delivery.message_id):
        return

    # Retire only the captured old message. This optional I/O must not occupy
    # the turn lock or queue ahead of the new owner's updates.
    generation = state["generation"]
    tasks = app[CONTINUATION_RETIREMENT_TASKS_KEY]
    if len(tasks) >= CONTINUATION_RETIREMENT_MAX_PENDING:
        app[DIAGNOSTICS_KEY]["last_continuation_predecessor"] = "capacity"
        return

    def current():
        return (app[SESSIONS_KEY].get(session_key) is session
                and app[FEISHU_MESSAGE_IDS_KEY].get(session_key) == delivery.message_id
                and app[MESSAGE_BOT_IDS_KEY].get(session_key) == bot_id
                and session.display_segment.get("generation") == generation)

    async def retire_predecessor() -> None:
        try:
            await asyncio.wait_for(
                _retire_continuation_predecessor(
                    app, session_key, previous_message_id, predecessor_snapshot,
                    bot_id=bot_id, navigation=delivery.navigation, is_current=current,
                ),
                timeout=CONTINUATION_RETIREMENT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            if current():
                app[DIAGNOSTICS_KEY]["last_continuation_predecessor"] = "timeout"

    task_key = (id(session), generation)
    task = asyncio.create_task(retire_predecessor())
    tasks[task_key] = task

    def finished(completed: asyncio.Task[None]) -> None:
        if tasks.get(task_key) is completed:
            tasks.pop(task_key, None)

    task.add_done_callback(finished)
    task.add_done_callback(_log_background_task_failure)


async def _send_segment_cards(
    app: web.Application,
    *,
    chat_id: str,
    segments: tuple[dict[str, Any], ...],
    session_key: str,
    main_message_id: str,
    bot_id: str | None,
    thread_id: str | None,
    reply_to_message_id: str | None,
    reply_in_thread: bool,
    profile_id: str | None,
) -> bool:
    """Send continuation cards in order; False on the first failed delivery.

    Tie retries to the confirmed main card and slot. Reopened topic turns
    can share a session key and reply anchor while owning different main cards.
    """
    for index, segment_card in enumerate(segments, start=1):
        delivery = await _send_card_for_app(
            app, chat_id, segment_card, bot_id,
            thread_id=thread_id,
            reply_to_message_id=reply_to_message_id,
            reply_in_thread=reply_in_thread,
            delivery_key=f"{session_key}:segment:{main_message_id}:{index}",
            delivery_kind="chat",
            profile_id=profile_id,
        )
        if not delivery.delivered:
            app[DIAGNOSTICS_KEY]["last_segmented_delivery"] = (
                f"segment_{index}_{delivery.outcome}"
            )
            return False
    app[DIAGNOSTICS_KEY]["last_segmented_delivery"] = "delivered"
    return True


async def _deliver_segmented_terminal(
    request: web.Request,
    session_key: str,
    session: CardSession,
    render_result: CardRenderResult,
    *,
    feishu_message_id: str,
    bot_id: str | None,
    event: SidecarEvent,
) -> bool:
    """Patch the terminal main card, then send every continuation card.

    Called under the turn lock after the render decision and before the event
    response commits the disposition. Any failed or uncertain step returns
    False so the coordinator falls back to the existing native handoff:
    content completeness beats a partially segmented delivery.
    """
    app = request.app
    old_animation = app[CARD_ANIMATION_TASKS_KEY].pop(session_key, None)
    if old_animation is not None:
        old_animation.cancel()
        await asyncio.gather(old_animation, return_exceptions=True)
    updated = await _update_card_for_app(
        app, feishu_message_id, render_result.card, bot_id,
        is_current=lambda: app[SESSIONS_KEY].get(session_key) is session,
    )
    if not updated:
        app[DIAGNOSTICS_KEY]["last_segmented_delivery"] = "main_card_update_failed"
        return False
    return await _send_segment_cards(
        app,
        chat_id=session.chat_id,
        segments=render_result.segments,
        session_key=session_key,
        main_message_id=feishu_message_id,
        bot_id=bot_id,
        thread_id=_thread_id_for_event(event) or None,
        reply_to_message_id=session.reply_to_message_id or None,
        reply_in_thread=session.reply_in_thread,
        profile_id=None,
    )


def _segmented_fallback_result(
    app: web.Application,
    session: CardSession,
    render_result: CardRenderResult,
    *,
    session_key: str,
) -> CardRenderResult:
    """Keep the native limit handoff when segmented delivery is unavailable."""
    _, card_config, configured_title, _ = _session_card_render_context(
        app, session, session_key=session_key
    )
    terminal = session.status in {"completed", "failed"}
    return replace(
        render_result,
        card=render_terminal_limit_handoff_card(
            title=configured_title,
            width_mode=card_config.get("width_mode", "default"),
        ),
        segments=(),
        disposition="native" if terminal else "deferred_native",
    )


def _render_static_display_card(app, snapshot, *, session_key, note, display_state):
    """Render a private display copy, without mutating execution or deleting evidence."""
    try:
        snapshot.display_segment = {}
        snapshot.runtime_phase_text = ""
        snapshot.latest_tool_preview = ""
        snapshot.display_status = "waiting"
        snapshot.display_status_source = "explicit"
        snapshot.timeline.record_answer_started()
        from .card_timeline import TERMINAL_TOOL_STATUSES, TERMINAL_SUBAGENT_STATUSES
        for tool in snapshot.tools.values():
            if tool.status.strip().lower() not in TERMINAL_TOOL_STATUSES:
                tool.status = display_state
        for entry in snapshot.timeline.snapshot():
            if entry.kind == "tool" and entry.status.strip().lower() not in TERMINAL_TOOL_STATUSES:
                entry.status = display_state
            elif entry.kind == "subagent" and entry.status.strip().lower() not in TERMINAL_SUBAGENT_STATUSES:
                entry.status = display_state
        result = _render_session_card_result_for_app(app, snapshot, session_key=session_key)
        if result.disposition != "card" or result.card.get("schema") != "2.0":
            return None  # Do not replace preserved content with a limit placeholder.
        card = result.card
        card.setdefault("config", {})["streaming_mode"] = False
        card["config"].setdefault("summary", {})["content"] = note
        _, card_config, configured_title, _ = _session_card_render_context(app, snapshot, session_key=session_key)
        title = {"tag":"plain_text", "content":"↪ " + configured_title}
        card["header"] = {"template":"blue", "title":title,
                          "subtitle":{"tag":"plain_text", "content":note}}
        for element in card.get("body", {}).get("elements", []):
            if element.get("element_id") == "footer":
                element["content"] = note
        if card_config.get("_presentation_mode") == "task":
            card["header"]["template"] = "default"
            # The static header owns this display-transfer note. A stale
            # observation/action or repeated footer would imply live execution.
            card["body"]["elements"] = [e for e in card["body"]["elements"]
                if e.get("element_id") not in {"footer", "main_divider", "task_observation", "task_action"}]
            if display_state == "display_handoff":
                # Preserve earlier content, but do not repeat an expanded answer
                # beside its confirmed continuation. Avoid nested panels.
                from .owner_display_policy import collapsed_transfer_elements
                transferred = collapsed_transfer_elements(card["body"]["elements"])
                if transferred is None:
                    return None
                card["body"]["elements"] = transferred
                card["header"] = {
                    "template": "blue",
                    "title": {"tag": "plain_text", "content": "已转入续答"},
                }
        if not inspect_card_limits(card).safe:
            return None
        return card
    except Exception:
        return None


async def _retire_continuation_predecessor(
    app, session_key, message_id, snapshot, *, bot_id, navigation=None, is_current=None,
):
    """Freeze a confirmed handoff without claiming that the whole turn succeeded.

    Adapted from PR #339's predecessor-close proposal. Keep the pre-output
    snapshot and decision receipt; a sole legacy owner already has a static
    receipt and must never receive a schema-2 PATCH.
    """
    target_id = app[FEISHU_MESSAGE_IDS_KEY].get(session_key)
    owner = app[SESSIONS_KEY].get(session_key)
    if is_current is None:
        def is_current():
            return (app[SESSIONS_KEY].get(session_key) is owner
                    and app[FEISHU_MESSAGE_IDS_KEY].get(session_key) == target_id)
    if (not target_id or not message_id or message_id == target_id
            or snapshot.legacy_owner_receipt or not is_current()):
        return False
    try:
        card = _render_static_display_card(
            app, snapshot, session_key=session_key,
            note="本段已转入续答，后续进展与结果见下方新卡",
            display_state="display_handoff",
        )
        if card is None:
            return False
        lookup = getattr(_client_for_bot(app, bot_id), "lookup_message_navigation", None)
        if navigation is None and owner is not None and callable(lookup):
            route = owner.display_segment
            try:
                navigation = await asyncio.wait_for(lookup(
                    target_id, snapshot.chat_id,
                    thread_id=route.get("thread_id") or None,
                    reply_in_thread=bool(route.get("reply_in_thread") or owner.reply_in_thread),
                ), timeout=1.5)
            except Exception:
                navigation = None
            if not isinstance(navigation, MessageNavigation):
                navigation = None
            if not is_current():
                return False
        card = with_continuation_navigation(
            card, navigation,
            message_id=target_id,
            chat_id=snapshot.chat_id,
        )
        updated = await _update_card_for_app(app, message_id, card, bot_id, is_current=is_current)
        if not is_current():
            return False
        app[DIAGNOSTICS_KEY]["last_continuation_predecessor"] = "retired" if updated else "update_failed"
        if not updated:
            return False
        # Do not let terminal compaction race an outstanding or failed PATCH
        # to this same card.
        interaction = snapshot.active_interaction
        if (owner is not None and interaction is not None
                and interaction.kind in {"approval", "clarify"} and interaction.status == "completed"
                and interaction.feishu_message_id and interaction.feishu_message_id != message_id
                and len(owner.approval_retirements) < 32):
            clean = copy.deepcopy(card)
            elements = clean.get("body", {}).get("elements", [])
            def keep_receipt_element(element):
                return (not str(element.get("element_id", "")).startswith("interaction_review_")
                        and element.get("element_id") != "interaction_result")
            clean["body"]["elements"] = [e for e in elements if keep_receipt_element(e)]
            for element in clean["body"]["elements"]:
                if element.get("tag") == "collapsible_panel" and element.get("element_id") == "previous_content":
                    element["elements"] = [e for e in element["elements"] if keep_receipt_element(e)]
            if card != clean:
                receipt = copy.deepcopy(interaction)
                receipt.callback_token = ""
                receipt.runtime_admission = None
                owner.approval_retirements.append(dict(message_id=message_id,
                    bot_id=bot_id, card=clean, interaction=receipt,
                    fingerprint=approval_receipt_fingerprint(owner, receipt)))
        return True
    except Exception:
        if is_current():
            app[DIAGNOSTICS_KEY]["last_continuation_predecessor"] = "update_failed"
        return False


async def _settle_approval_displays(app, session_key, session):
    """Only compact terminal duplicates after an explicit full-receipt PATCH.

    This runs on the terminal flush, never on the button callback critical path.
    Evidence stays in memory; restoring a display checkpoint grants no authority.
    """
    if session.status not in {"completed", "failed"}:
        return
    current = lambda: app[SESSIONS_KEY].get(session_key) is session
    if not current():
        return
    records = list(session.approval_retirements)
    interactions = [r["interaction"] for r in records]
    if session.active_interaction is not None:
        interactions.append(session.active_interaction)
    confirmed = set()
    attempted = set()
    owner_id = app[FEISHU_MESSAGE_IDS_KEY].get(session_key)
    for interaction in interactions:
        # Unresolved terminal failures are refreshed only after the final body
        # is delivered; optional receipt I/O must never hold back the answer.
        if interaction.status != "completed":
            continue
        fingerprint = approval_receipt_fingerprint(session, interaction)
        if (not fingerprint or fingerprint in attempted
                or interaction.feishu_message_id == owner_id):
            continue
        attempted.add(fingerprint)
        snapshot = copy.copy(session)
        snapshot.approval_retirements = []
        snapshot.active_interaction = copy.deepcopy(interaction)
        snapshot.active_interaction.receipt_fingerprint = ""
        snapshot.display_segment = {}
        snapshot.legacy_owner_receipt = {}
        if _interaction_mode_for_session_key(app, session_key) == "callback":
            card = _render_interaction_callback_card_for_app(app, snapshot, session_key=session_key)
        else:
            card = _render_static_display_card(app, snapshot, session_key=session_key,
                note="交互结果已记录，后续进展以回复卡为准", display_state="display_receipt")
        if card is None or not inspect_card_limits(card).safe:
            continue
        updated = await _update_card_for_app(app, interaction.feishu_message_id, card,
            app[MESSAGE_BOT_IDS_KEY].get(session_key), is_current=current)
        if updated and current() and fingerprint == approval_receipt_fingerprint(session, interaction):
            confirmed.add(fingerprint)
    interaction = session.active_interaction
    fingerprint = approval_receipt_fingerprint(session, interaction)
    if fingerprint and fingerprint in confirmed:
        interaction.receipt_fingerprint = fingerprint
    remaining = []
    for record in records:
        if (record["fingerprint"] not in confirmed or not current()
                or record["message_id"] == owner_id):
            remaining.append(record)
            continue
        updated = await _update_card_for_app(app, record["message_id"], record["card"],
            record["bot_id"], is_current=current)
        if not updated:
            remaining.append(record)
    session.approval_retirements = remaining
    app[DIAGNOSTICS_KEY]["last_approval_display_cleanup"] = (
        "retained" if remaining else "confirmed" if confirmed else "not_applicable")


def _can_replay_terminal_recovery(app, target: TerminalTarget, accepted_at: float) -> bool:
    # The original placement must lead to the same IM create/reply endpoint.
    return (
        type(accepted_at) in (int, float)
        and 0 <= time.time() - accepted_at < _FEISHU_IM_UUID_WINDOW_SECONDS
        and (not (target.thread_id or target.reply_in_thread) or bool(target.reply_to_message_id))
        and callable(getattr(_client_for_bot(app, target.bot_id), "send_card_delivery", None))
    )


class _AppTerminalTransport:
    """Bind only delivery I/O to the app; policy stays in terminal_delivery."""

    def __init__(self, app: web.Application, session: CardSession, *, replay: bool) -> None:
        self.app = app
        self.session = session
        self.replay = replay

    async def patch(
        self, plan: TerminalDeliveryPlan, *, is_current: Callable[[], bool]
    ) -> bool:
        return await _update_card_for_app(
            self.app, plan.target.message_id, plan.card, plan.target.bot_id,
            notice_update=plan.target.event == "system.notice",
            is_current=is_current,
        )

    async def retry(
        self, plan: TerminalDeliveryPlan, *, is_current: Callable[[], bool]
    ) -> bool:
        return await _retry_terminal_update(
            self.app, plan.target.message_id, plan.card, plan.target.bot_id,
            is_current,
        )

    async def recover(self, plan: TerminalDeliveryPlan) -> CardDeliveryResult:
        target = plan.target
        if self.replay and not _can_replay_terminal_recovery(self.app, target, self.session.updated_at):
            # PATCH retries or optional rendering may have crossed the UUID expiry.
            return CardDeliveryResult(None, "unknown", error_kind="TerminalRecoveryWindowExpired")
        if (self.session.legacy_terminal_fingerprint
                and self.app[SESSIONS_KEY].get(target.session_key) is self.session):
            # Record the in-flight create before sending it. Restarted retries
            # must reuse its UUID instead of editing a second visible copy.
            _checkpoint_session(self.app, target.session_key, target.profile_id or "")
        return await _send_card_for_app(
            self.app, target.chat_id, plan.card, target.bot_id,
            thread_id=target.thread_id,
            reply_to_message_id=target.reply_to_message_id,
            reply_in_thread=target.reply_in_thread,
            delivery_key=target.recovery_key,
            delivery_kind="terminal-recovery",
            profile_id=target.profile_id,
        )


def _completion_notify_elapsed_seconds(session: CardSession) -> float:
    """Seconds this turn actually took, for the completion-notify time gate.

    Prefer the turn's own measurement (``message.completed.data.duration``,
    recorded on ``session.duration``). Hermes omits it on some terminal paths
    (resumed sessions, some interrupted/queued completions), and a long turn
    with ``duration == 0`` would otherwise be indistinguishable from a
    three-second one and get silently skipped -- the exact opposite of what a
    minimum-duration gate is for. Fall back to the age of the session record;
    a new ``CardSession`` is built per turn and its ``created_at`` matches the
    measured duration to within a few hundred milliseconds.

    Pure clock semantics live in completion_notification.elapsed_seconds;
    this wrapper keeps the session-facing call sites stable.
    """
    return elapsed_seconds(session.duration, session.created_at)


def _completion_notify_meets_threshold(
    session: CardSession, notify_config: dict[str, Any]
) -> bool:
    """Apply ``completion_notify.min_duration_seconds`` (absent/0 = no gate)."""
    return meets_threshold(
        _completion_notify_elapsed_seconds(session),
        notify_config.get("min_duration_seconds", 0),
    )


async def _notify_confirmed_completion(
    app: web.Application, session_key: str, session: CardSession, event: SidecarEvent,
    *, card_config: dict[str, Any], bot_id: str | None,
) -> bool:
    await _maybe_send_completion_notify(app, session_key, session, event, card_config=card_config, bot_id=bot_id)
    sent = session.completion_notify_state == "sent"
    if sent and app[SESSIONS_KEY].get(session_key) is session:
        _checkpoint_session(app, session_key, _policy_profile_id(event) or "")
    return sent


async def _maybe_send_completion_notify(
    app: web.Application,
    session_key: str,
    session: CardSession,
    event: SidecarEvent,
    *,
    card_config: dict[str, Any],
    bot_id: str | None,
) -> None:
    notify_config = (
        card_config.get("completion_notify")
        if type(card_config) is dict
        else None
    )
    # The @ mention is optional: the notification itself must be sent even when
    # no mention target resolves. System/background turns have no requester to
    # mention, and newer gateway events carry only the tenant user_id, so the
    # open_id is resolved below and a failed lookup must not swallow the notice.
    mention_enabled = card_completion_mention_enabled(card_config)
    if (
        type(notify_config) is not dict
        or notify_config.get("enabled") is not True
        or session.status != "completed"
        or session.delivery_kind != "chat"
        or session.completion_notify_state != "idle"
        # A claimed-but-invalid sender is a spoof signal: send nothing at all.
        or session.sender_open_id_rejected
    ):
        return
    # Synthetic turns (background-process completions, queue markers) carry a
    # composite ["completion", ...] id and no requester: never send a completion
    # notice for them, or a topic turn @s twice (in the topic and in the chat).
    if _is_synthetic_turn(session):
        return
    if notify_config.get("placement", "message") == "card":
        session.completion_notify_state = "sent"
        return
    if not _completion_notify_meets_threshold(session, notify_config):
        # Short turn: no push. Record the decision so a replayed
        # ``message.completed`` for the same turn cannot notify later.
        session.completion_notify_state = "sent"
        return
    client = _client_for_bot(app, bot_id)
    send_text = getattr(client, "send_text_message", None)
    if not callable(send_text):
        return

    session.completion_notify_state = "sending"
    duration_text = _format_duration(session.duration) if session.duration > 0 else ""
    suffix = f"（用时 {duration_text}）" if duration_text else ""
    mention_open_id = await _completion_mention_open_id(client, session) if mention_enabled else ""
    mention_prefix = (
        f'<at user_id="{mention_open_id}"></at> ' if mention_open_id else ""
    )
    text = f"{mention_prefix}✅ 本轮回复结束{suffix}"
    notice_scope = _restart_notice_scope(
        profile_id=_policy_profile_id(event), bot_id=bot_id, chat_id=session.chat_id,
        thread_id=_thread_id_for_event(event) or (
            session.reply_to_message_id if session.reply_in_thread else None),
    )
    notice_snapshot = _restart_notice_snapshot(app, notice_scope)
    try:
        thread_id = _thread_id_for_event(event)
        reply_to_message_id = session.reply_to_message_id or ""
        if (thread_id or session.reply_in_thread) and not reply_to_message_id:
            # docs/wiki/feishu-acceptance.md: an explicit thread intent
            # without a reply anchor must not fall back to a top-level
            # chat send. Keep the notice unsent with a redacted
            # classification; a replayed terminal keeps the retry window.
            app[DIAGNOSTICS_KEY]["last_route_error"] = (
                "CompletionNotifyThreadAnchorMissing"
            )
            session.completion_notify_state = "idle"
            logger.warning(
                "completion notify skipped: %s",
                "CompletionNotifyThreadAnchorMissing",
            )
            return
        send_kwargs: dict[str, Any] = {
            "thread_id": thread_id or None,
            "reply_to_message_id": reply_to_message_id or None,
        }
        if session.reply_in_thread:
            send_kwargs["reply_in_thread"] = True
        sent_message_id = await send_text(session.chat_id, text, **send_kwargs)
    except asyncio.CancelledError:
        session.completion_notify_state = "idle"
        raise
    except Exception as exc:
        session.completion_notify_state = "idle"
        logger.warning(
            "completion notify send failed: %s",
            exc.__class__.__name__,
        )
        return
    if isinstance(sent_message_id, str) and sent_message_id:
        _retire_restart_notices(app, notice_scope, notice_snapshot)
    session.completion_notify_state = "sent"
    logger.info(
        "completion notify sent (sender_hash=%s session_hash=%s)",
        _diagnostic_id_hash(session.sender_open_id, domain="completion-sender"),
        _diagnostic_id_hash(session_key, domain="completion-session"),
    )


def _is_synthetic_turn(session: CardSession) -> bool:
    """Queue/process-completion turns carry a JSON-array id, not a Feishu one."""
    message_id = session.message_id
    return isinstance(message_id, str) and message_id.lstrip().startswith("[")


async def _completion_mention_open_id(client: Any, session: CardSession) -> str:
    """Resolve the requester's open_id; "" means send the notice unmentioned."""
    if re.fullmatch(r"ou_[A-Za-z0-9_-]{1,128}", session.sender_open_id):
        return session.sender_open_id
    resolver: Any = getattr(client, "resolve_open_id", None)
    if not callable(resolver):
        return ""
    try:
        resolved = await resolver(session.sender_user_id)
    except Exception:
        return ""
    return resolved if isinstance(resolved, str) else ""


def _schedule_terminal_work(
    app: web.Application, session_key: str, controller: FlushController,
    work: Callable[[], Awaitable[bool]],
) -> asyncio.Task[None]:
    task = controller.schedule(work, terminal=True)
    controller.close()
    task.add_done_callback(partial(_post_terminal_cleanup, app, session_key, controller))
    return task


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
        cleanup_closed_controller(_cleanup_state_for_app(app), session_key, controller, now=now)
        cleanup_runtime_state(_cleanup_state_for_app(app), now)


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


async def _finalize_interaction_predecessor(
    app: web.Application,
    *,
    session_key: str,
    predecessor_message_id: str,
    bot_id: str | None,
    predecessor_snapshot: CardSession,
    animation_task: asyncio.Task[None] | None,
) -> bool:
    if animation_task is not None:
        animation_task.cancel()
        await asyncio.gather(animation_task, return_exceptions=True)

    previous_interaction = predecessor_snapshot.active_interaction
    if (previous_interaction is not None
            and previous_interaction.feishu_message_id == predecessor_message_id
            and previous_interaction.status not in {"pending", "paused"}):
        if _interaction_mode_for_session_key(app, session_key) == "callback":
            card = _render_interaction_callback_card_for_app(
                app, predecessor_snapshot, session_key=session_key
            )
        else:
            card = _render_static_display_card(
                app, predecessor_snapshot, session_key=session_key,
                note="交互结果已记录，后续进展以回复卡为准",
                display_state="display_receipt",
            )
        if card is None:
            return False
        return await _update_card_for_app(app, predecessor_message_id, card, bot_id)

    predecessor_snapshot.active_interaction = None
    _, card_config, _, _ = _session_card_render_context(
        app, predecessor_snapshot, session_key=session_key
    )
    if card_config.get("_presentation_mode") == "task":
        card = _render_static_display_card(
            app, predecessor_snapshot, session_key=session_key,
            note="已转入交互卡片，请在下方完成选择", display_state="display_receipt",
        )
        if card is None:
            return False
        return await _update_card_for_app(app, predecessor_message_id, card, bot_id)
    predecessor_snapshot.latest_tool_preview = ""
    predecessor_snapshot.runtime_phase_text = ""
    predecessor_snapshot.display_status = "completed"
    predecessor_snapshot.display_status_source = "explicit"
    card = _render_session_card_for_app(
        app,
        predecessor_snapshot,
        session_key=session_key,
    )
    if "streaming_mode" in card.get("config", {}):
        card["config"]["streaming_mode"] = False
    header = card.get("header")
    title = header.get("title") if isinstance(header, dict) else None
    if not isinstance(title, dict):
        title = {"tag": "plain_text", "content": app[CARD_TITLE_KEY]}
    card["header"] = {
        "template": "green",
        "title": title,
        "subtitle": {"tag": "plain_text", "content": "已转入交互卡片"},
    }
    card.setdefault("config", {}).setdefault("summary", {})["content"] = (
        "已转入交互卡片"
    )
    return await _update_card_for_app(
        app,
        predecessor_message_id,
        card,
        bot_id,
    )


async def _complete_runtime_interaction_delivery(
    request: web.Request,
    reservation: RuntimeInteractionDeliveryReservation,
) -> web.Response:
    app = request.app
    metrics: SidecarMetrics = app[METRICS_KEY]
    delivery = await _send_card(
        request,
        reservation.chat_id,
        copy.deepcopy(reservation.card),
        reservation.bot_id,
        thread_id=reservation.thread_id,
        reply_to_message_id=reservation.reply_to_message_id,
        reply_in_thread=reservation.reply_in_thread,
        delivery_key=reservation.delivery_key,
        delivery_kind="interaction",
        profile_id=reservation.session.route_profile_id,
    )
    if delivery.delivered:
        # Same reuse contract as the callback-mode card (#314).
        reservation.interaction.feishu_message_id = str(delivery.message_id or "")

    lock = app[MESSAGE_LOCKS_KEY].setdefault(
        reservation.session_key, asyncio.Lock()
    )
    animation_task: asyncio.Task[None] | None = None
    committed = False
    async with lock:
        reservations = app[RUNTIME_INTERACTION_RESERVATIONS_KEY]
        current_reservation = reservations.get(reservation.session_key)
        current_session = app[SESSIONS_KEY].get(reservation.session_key)
        current_interaction = (
            current_session.active_interaction
            if current_session is reservation.session
            else None
        )
        current_fingerprint = (
            _runtime_admission_fingerprint(
                dict(current_interaction.runtime_admission)
            )
            if current_interaction is reservation.interaction
            and current_interaction.runtime_admission is not None
            else ""
        )
        still_owner = bool(
            current_reservation is reservation
            and current_session is reservation.session
            and current_interaction is reservation.interaction
            and current_interaction.status == "pending"
            and current_fingerprint == reservation.admission_fingerprint
            and current_session.last_sequence == reservation.sequence
        )
        if not delivery.delivered:
            if still_owner:
                _restore_session_snapshot(
                    reservation.session, reservation.rollback_session
                )
            elif (
                current_interaction is reservation.interaction
                and current_fingerprint == reservation.admission_fingerprint
            ):
                current_interaction.runtime_admission = None
            if reservations.get(reservation.session_key) is reservation:
                reservations.pop(reservation.session_key, None)
            metrics.events_rejected += 1
            return web.json_response(
                {
                    "ok": False,
                    "error": "feishu interaction send failed",
                    "delivery": _delivery_payload(delivery),
                },
                status=502,
            )
        if not still_owner:
            if (
                current_interaction is reservation.interaction
                and current_fingerprint == reservation.admission_fingerprint
            ):
                current_interaction.runtime_admission = None
            if reservations.get(reservation.session_key) is reservation:
                reservations.pop(reservation.session_key, None)
            metrics.events_rejected += 1
            return web.json_response(
                {"ok": False, "error": "interaction delivery state changed"},
                status=409,
            )

        animation_task = app[CARD_ANIMATION_TASKS_KEY].pop(
            reservation.session_key, None
        )
        # Delivery evidence belongs to this exact interaction, never to a
        # reused id or session key. It grants no execution authority.
        reservation.interaction.runtime_delivery_fingerprint = reservation.admission_fingerprint
        _store_interaction_result(app, reservation.session)
        reservations.pop(reservation.session_key, None)
        committed = True

    if committed:
        try:
            await _finalize_interaction_predecessor(
                app,
                session_key=reservation.session_key,
                predecessor_message_id=reservation.predecessor_message_id,
                bot_id=reservation.bot_id,
                predecessor_snapshot=reservation.rollback_session,
                animation_task=animation_task,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("interaction predecessor finalization failed")
        metrics.events_applied += 1
    return web.json_response(
        {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered"},
            "runtime_admission": True,
        }
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
    task_layout = _session_card_render_context(app, session, session_key=session_key)[1].get("_presentation_mode") == "task"
    checks = CARD_ANIMATION_MAX_UPDATES + (CARD_TASK_OBSERVATION_MAX_CHECKS if task_layout else 0)
    for index in range(checks):
        observation = index >= CARD_ANIMATION_MAX_UPDATES
        delay = task_observation_delay(session) if observation else CARD_ANIMATION_INTERVAL_SECONDS
        await _CARD_ANIMATION_SLEEP(delay)
        if not _card_animation_is_current(app, session_key, session):
            return
        if observation and task_observation_delay(session) > 0:
            continue
        if not observation and not (_is_initial_loading(session) or _has_running_tool(session)):
            continue

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
        if observation:
            # One bounded update after silence, on the same card and controller.
            # New events can re-arm it; there is no perpetual polling/PATCH loop.
            return


def _card_animation_is_current(
    app: web.Application,
    session_key: str,
    session: CardSession,
) -> bool:
    # Freeze loading/tool animations while an interaction is pending —
    # periodic PATCHes would reset the user's in-progress selections/input.
    interaction = session.active_interaction
    if (session.display_segment.get("pending")
            or (interaction is not None and interaction.status in {"pending", "paused"})):
        return False
    if app[SESSIONS_KEY].get(session_key) is not session or session.status in {"completed", "failed"}:
        return False
    return (_is_initial_loading(session) or _has_running_tool(session)
            or _session_card_render_context(app, session, session_key=session_key)[1].get("_presentation_mode") == "task")


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
    result = {
        "interaction_id": interaction.interaction_id,
        "status": interaction.status,
        "choice": interaction.choice,
        "choice_label": interaction.choice_label,
    }
    if interaction.runtime_delivery_fingerprint:
        result["runtime_admission_fingerprint"] = interaction.runtime_delivery_fingerprint
    if interaction.pause_on_timeout:
        result["pause_on_timeout"] = True
    if interaction.error:
        result["error"] = interaction.error
    app[INTERACTION_RESULTS_KEY][interaction.interaction_id] = result
    app[INTERACTION_RESULT_SESSION_KEYS_KEY][interaction.interaction_id] = (
        _session_key_for_session(app, session)
    )


def _mark_interaction_expired_locked(
    app: web.Application,
    session_key: str,
    session: CardSession,
    *,
    now: float,
) -> None:
    session.updated_at = now
    interaction = session.active_interaction
    if interaction is not None and interaction.status == "paused":
        _schedule_paused_approval_card(app, session_key, session, interaction)
    elif (interaction is not None and interaction.status == "failed"
          and session.display_segment.get("interaction_id") != interaction.interaction_id):
        # Local expiry can precede Hermes' failure event. Establish the same
        # lazy boundary here, without resetting an already opened segment.
        begin_continuation(session)
    card_config = app[SESSION_CARD_CONFIGS_KEY].get(session_key, {})
    session.refresh_display_status_source(
        StatusConfig.from_mapping(card_config.get("status"))
    )
    _store_interaction_result(app, session)


def _expire_runtime_admission_locked(
    app: web.Application,
    session_key: str,
    session: CardSession,
    interaction: Any,
    *,
    now: float,
) -> bool:
    if not runtime_admission_has_expired(interaction, now=now):
        return False
    interaction.status = "failed"
    interaction.error = "交互已过期"
    interaction.runtime_admission = None
    _mark_interaction_expired_locked(
        app,
        session_key,
        session,
        now=now,
    )
    return True


def _expired_interaction_response(card: dict[str, Any]) -> web.Response:
    return web.json_response(
        {
            "ok": False,
            "status": "failed",
            "error": "interaction expired",
            "toast": {"type": "warning", "content": "交互已过期"},
            "card": card,
        },
        status=409,
    )


def _approval_card_needs_reissue(interaction: Any) -> bool:
    """Route expired approvals to renewal or an explicit expired-card response.

    Renewal still requires a live pausable waiter; an expired native admission
    or a vanished runtime can only receive an explanation, never new consent.
    """
    if interaction.kind != "approval":
        return False
    if interaction.status in {"paused", "failed"}:
        return True
    return interaction.is_expired()


def _schedule_paused_approval_card(app, session_key, session, interaction):
    if (session.status in {"completed", "failed"}
            or interaction.pause_notified_generation >= interaction.pause_generation
            or time.time() < interaction.pause_retry_after):
        return
    interaction.pause_notified_generation = interaction.pause_generation
    generation = interaction.pause_generation
    snapshot = copy.deepcopy(session)
    card = _render_interaction_callback_card_for_app(app, snapshot, session_key=session_key)

    def current():
        return (app[SESSIONS_KEY].get(session_key) is session
                and session.active_interaction is interaction
                and session.status not in {"completed", "failed"}
                and interaction.status == "paused"
                and interaction.pause_generation == generation)

    async def deliver():
        if not current():
            return
        bot_id = app[MESSAGE_BOT_IDS_KEY].get(session_key)
        existing_card_id = str(getattr(interaction, "feishu_message_id", "") or "")
        if existing_card_id:
            # The approval card is already in front of the user: refresh it in place. Sending the
            # paused notice as its own message shows one paused approval twice (#314). A failed
            # edit keeps the same generation and retries below — never a second card.
            refreshed = await _update_card_for_app(
                app, existing_card_id, card, bot_id, is_current=current,
            )
            result = CardDeliveryResult(
                message_id=existing_card_id,
                outcome="delivered" if refreshed else "not_sent",
            )
        else:
            result = await _send_card_for_app(
                app, session.chat_id, card, bot_id,
                thread_id=interaction.thread_id or None,
                reply_to_message_id=interaction.reply_to_message_id or session.reply_to_message_id or None,
                reply_in_thread=interaction.reply_in_thread or session.reply_in_thread,
                delivery_key=f"{session_key}:approval-paused:{interaction.interaction_id}:{generation}",
                delivery_kind="interaction",
                profile_id=session.route_profile_id,
            )
        if result.outcome != "delivered" and current():
            # The live waiter drives recovery after a transient Feishu outage.
            # Retain the same generation/delivery UUID; never rotate consent on retry.
            interaction.pause_retry_after = time.time() + 5.0
            interaction.pause_notified_generation = generation - 1
    task = asyncio.create_task(deliver())
    tasks = app[PAUSED_APPROVAL_TASKS_KEY]
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    task.add_done_callback(_log_background_task_failure)


async def _resume_paused_approval(request, session_key, session, interaction, token, chat_id):
    """Refresh consent only while the original pausable waiter is still alive."""
    app = request.app
    lock = app[MESSAGE_LOCKS_KEY].setdefault(session_key, asyncio.Lock())
    async with lock:
        if (app[SESSIONS_KEY].get(session_key) is not session
                or session.active_interaction is not interaction
                or interaction.callback_token != token or session.chat_id != chat_id
                or interaction.kind != "approval"):
            return web.json_response({"ok": False, "error": "interaction not found"}, status=404)
        interaction.expire()
        if interaction.status not in {"paused", "failed"}:
            return web.json_response({"ok": False, "error": "interaction not found"}, status=404)
        window = ApprovalWindowState(
            session_status=session.status,
            interaction_status=interaction.status,
            pause_on_timeout=interaction.pause_on_timeout,
            has_runtime_admission=interaction.runtime_admission is not None,
            last_waiter_poll_at=interaction.last_waiter_poll_at,
        )
        if not approval_window_can_reopen(window, now=time.time()):
            # A new card cannot resurrect a stopped runtime or expired native admission.
            # Explain the dead request in place; retain its exact scope for review.
            interaction.status = "failed"
            interaction.error = "原审批任务已结束或授权已过期，请重新发送原请求。"
            interaction.callback_token = secrets.token_urlsafe(16)
            _store_interaction_result(app, session)
            card = _render_interaction_callback_card_for_app(app, session, session_key=session_key)
            return web.json_response({"ok": False, "error": "interaction expired",
                "toast": {"type": "warning", "content": interaction.error}, "card": card}, status=409)
        interaction.status = "pending"
        interaction.callback_token = secrets.token_urlsafe(16)
        interaction.requested_at = time.time()
        interaction.error = ""
        session.updated_at = interaction.requested_at
        _store_interaction_result(app, session)
        card = _render_interaction_callback_card_for_app(app, session, session_key=session_key)
    return web.json_response({"ok": True,
        "toast": {"type": "info", "content": "审批已过期，请查看完整操作后重新审批"}, "card": card})


async def _expire_pending_interaction(
    app: web.Application,
    session_key: str,
    session: CardSession,
    *,
    now: float,
) -> bool:
    lock = app[MESSAGE_LOCKS_KEY].setdefault(session_key, asyncio.Lock())
    expired_card: dict[str, Any] | None = None
    auxiliary = None
    async with lock:
        if app[SESSIONS_KEY].get(session_key) is not session:
            return False
        interaction = session.active_interaction
        if interaction is None or not interaction.expire(now):
            return False
        _mark_interaction_expired_locked(
            app,
            session_key,
            session,
            now=now,
        )
        expired_card = _render_session_card_for_app(app, session)
        expired_interaction = interaction
        expiry_sequence = session.last_sequence
        if interaction.status == "failed":
            try:
                auxiliary = _checkpoint_auxiliary_receipt(app, session_key, session,
                    app[FEISHU_MESSAGE_IDS_KEY].get(session_key))
            except ValueError:
                app[DIAGNOSTICS_KEY]["last_interaction_receipt_update"] = "failed"
    feishu_message_id = app[FEISHU_MESSAGE_IDS_KEY].get(session_key)
    def current():
        return (app[SESSIONS_KEY].get(session_key) is session
                and session.active_interaction is expired_interaction
                and expired_interaction.status in {"failed", "paused"}
                and session.last_sequence == expiry_sequence)
    if auxiliary is not None and current():
        fingerprint = approval_receipt_fingerprint(session, expired_interaction)
        updated = await _update_card_for_app(app, auxiliary["message_id"],
            auxiliary_receipt_card(auxiliary), app[MESSAGE_BOT_IDS_KEY].get(session_key),
            is_current=current)
        if (updated and current() and fingerprint
                and fingerprint == approval_receipt_fingerprint(session, expired_interaction)):
            expired_interaction.receipt_fingerprint = fingerprint
            expired_card = _render_session_card_for_app(app, session, session_key=session_key)
        app[DIAGNOSTICS_KEY]["last_interaction_receipt_update"] = "delivered" if updated else "failed"
    if feishu_message_id and expired_card is not None:
        await _update_card_for_app(
            app,
            feishu_message_id,
            expired_card,
            app[MESSAGE_BOT_IDS_KEY].get(session_key),
            is_current=current,
        )
    return True


async def _expire_pending_interactions(
    app: web.Application,
    *,
    now: float,
) -> int:
    expired = 0
    for session_key, session in tuple(app[SESSIONS_KEY].items()):
        if await _expire_pending_interaction(
            app,
            str(session_key),
            session,
            now=now,
        ):
            expired += 1
    return expired


def _find_session_by_interaction(
    app: web.Application,
    interaction_id: str,
    token: str,
    callback_chat_id: str,
) -> tuple[str, CardSession] | None:
    if not interaction_id or not token or not callback_chat_id:
        return None
    for session_key, session in app[SESSIONS_KEY].items():
        interaction = session.active_interaction
        if interaction is None:
            continue
        if interaction.interaction_id != interaction_id:
            continue
        if interaction.callback_token != token:
            return None
        if callback_chat_id != session.chat_id:
            return None
        return str(session_key), session
    return None


def _find_session_by_callback_token(
    app: web.Application,
    token: str,
    callback_chat_id: str,
) -> tuple[str, CardSession] | None:
    if not token or not callback_chat_id:
        return None
    for session_key, session in app[SESSIONS_KEY].items():
        interaction = session.active_interaction
        if interaction is None:
            continue
        if interaction.callback_token != token:
            continue
        if callback_chat_id != session.chat_id:
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


def _skip_native_text_fallback_interaction(
    app: web.Application,
    event: SidecarEvent,
    *,
    session_key: str | None = None,
) -> bool:
    return (
        native_text_fallback_requested(event)
        and _interaction_mode_for_session_key(app, session_key or _session_key(event)) == "text"
    )


def _decline_runtime_interaction_in_text_mode(
    app: web.Application,
    event: SidecarEvent,
    *,
    session_key: str | None = None,
) -> bool:
    return (
        runtime_text_decline_requested(event)
        and _interaction_mode_for_session_key(app, session_key or _session_key(event)) == "text"
    )


def _session_has_runtime_admission(session: CardSession | None) -> bool:
    interaction = session.active_interaction if session is not None else None
    return bool(
        interaction is not None
        and interaction.status == "pending"
        and interaction.runtime_admission is not None
    )


def _should_await_card_update(event: SidecarEvent) -> bool:
    # Hermes uses the /events response to decide whether to suppress native text.
    # Slow Feishu PATCH calls must not keep terminal events waiting.
    return False


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
    app: web.Application,
    session: CardSession,
    *,
    session_key: str | None = None,
) -> dict[str, Any]:
    interaction = session.active_interaction
    if (session.status not in {"completed", "failed"} and interaction is not None
            and interaction.status == "paused" and interaction.feishu_message_id):
        key, config, _, _ = _session_card_render_context(app, session, session_key=session_key)
        owner_id = app[FEISHU_MESSAGE_IDS_KEY].get(key)
        if (config.get("_presentation_mode") == "task" and owner_id
                and not session.legacy_owner_receipt
                and interaction.feishu_message_id != owner_id):
            # The separate approval card owns the full scope and resume button.
            # Expiry must not repaint its retired predecessor as a second form.
            # A legacy predecessor must keep the existing dialect-safe fallback.
            snapshot = copy.deepcopy(session)
            snapshot.active_interaction = None
            card = _render_static_display_card(
                app, snapshot, session_key=key, note="已转入交互卡片，请在下方继续审批",
                display_state="display_receipt",
            )
            if card is not None:
                return card
    result = _render_session_card_result_for_app(
        app,
        session,
        session_key=session_key,
    )
    return _existing_owner_render_result(app, session, result, session_key=session_key).card


def _remember_legacy_owner_receipt(app, session_key):
    session = app[SESSIONS_KEY].get(session_key)
    if session is None or not session.legacy_owner_receipt:
        return
    interaction = session.active_interaction
    if interaction is None or interaction.feishu_message_id != app[FEISHU_MESSAGE_IDS_KEY].get(session_key):
        return
    snapshot = copy.deepcopy(session)
    if snapshot.active_interaction.status in {"pending", "paused"}:
        # The live request still uses its original card. This static checkpoint
        # is only the safe fallback after a restart, where its waiter is gone.
        snapshot.active_interaction.status = "failed"
        snapshot.active_interaction.error = "原交互已失效，请重新发起请求。"
    session.legacy_owner_receipt = static_legacy_receipt(
        _render_interaction_callback_card_for_app(app, snapshot, session_key=session_key)
    )


def _existing_owner_render_result(app, session, result, *, session_key=None):
    def live_card():
        resolved_key = session_key or _session_key_for_session(app, session)
        interaction = session.active_interaction
        if (session.status not in {"completed", "failed"}
                and interaction is not None and interaction.status in {"pending", "paused"}
                and interaction.feishu_message_id
                and interaction.feishu_message_id == app[FEISHU_MESSAGE_IDS_KEY].get(resolved_key)):
            # The real first-message owner retains its live controls. A restored
            # session has no InteractionState or old callback token.
            return _render_interaction_callback_card_for_app(app, session, session_key=resolved_key)
        return None

    def title():
        _, _, value, _ = _session_card_render_context(app, session, session_key=session_key)
        return value

    return project_existing_owner(
        result,
        receipt=session.legacy_owner_receipt,
        terminal=session.status in {"completed", "failed"},
        live_card=live_card,
        title=title,
    )


def _render_session_card_result_for_app(
    app: web.Application,
    session: CardSession,
    *,
    session_key: str | None = None,
    card_config_override: dict[str, Any] | None = None,
) -> CardRenderResult:
    (
        resolved_session_key,
        card_config,
        title,
        interaction_profile_id,
    ) = _session_card_render_context(
        app,
        session,
        session_key=session_key,
        card_config_override=card_config_override,
    )
    footer_fields = _footer_fields_for_session(app, session, card_config_override=card_config_override)
    interaction_mode = _interaction_mode_for_session_key(
        app,
        resolved_session_key,
        card_config_override=card_config_override,
    )
    notify = card_config.get("completion_notify") or {}
    completion_in_card = (isinstance(notify, dict) and notify.get("enabled") is True
                          and notify.get("placement", "message") == "card")
    result = render_card_result(
        display_view(session),
        footer_fields=footer_fields,
        title=title,
        interaction_mode=interaction_mode,
        interaction_profile_id=interaction_profile_id,
        **card_render_options(card_config),
        completion_mention=completion_in_card and card_completion_mention_enabled(card_config),
        mentions_enabled=card_interaction_mention_enabled(
            card_config,
            kind=getattr(session.active_interaction, "kind", "") or "",
        ),
    )

    if card_config.get("streaming_mode") is True and result.card.get("schema") == "2.0":
        result.card.setdefault("config", {})["streaming_mode"] = (
            session.status not in {"completed", "failed"}
            and session.active_interaction is None
            and session.delivery_kind == "chat"
        )
        # Added configuration must pass the same serialized size boundary.
        inspection = inspect_card_limits(result.card)
        if not inspection.safe:
            result.card["config"].pop("streaming_mode", None)
        else:
            result = replace(result, inspection=inspection)
    return result


def _render_interaction_callback_card_for_app(
    app: web.Application,
    session: CardSession,
    *,
    session_key: str | None = None,
) -> dict[str, Any]:
    _, card_config, title, interaction_profile_id = _session_card_render_context(
        app,
        session,
        session_key=session_key,
    )
    return render_legacy_interaction_callback_card(
        session,
        title=title,
        interaction_profile_id=interaction_profile_id,
        presentation=card_config.get("_presentation_mode", "classic"),
        mentions_enabled=card_interaction_mention_enabled(
            card_config,
            kind=getattr(session.active_interaction, "kind", "") or "",
        ),
    )


def _session_card_render_context(
    app: web.Application,
    session: CardSession,
    *,
    session_key: str | None = None,
    card_config_override: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], str, str]:
    resolved_session_key = (
        session_key
        if session_key is not None
        else _session_key_for_session(app, session)
    )
    card_config = card_config_override
    if card_config is None:
        card_config = app[SESSION_CARD_CONFIGS_KEY].get(resolved_session_key, {})
    title = card_config.get("title", app[CARD_TITLE_KEY])
    if not isinstance(title, str):
        title = app[CARD_TITLE_KEY]
    interaction_profile_id = (
        resolved_session_key.split(":", 1)[0]
        if ":" in resolved_session_key
        else "default"
    )
    return (
        resolved_session_key,
        card_config,
        title,
        interaction_profile_id,
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
    elif result.disposition == "segmented":
        metrics.card_segmented_deliveries += 1
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
    app: web.Application, session: CardSession, *, card_config_override: dict[str, Any] | None = None,
) -> list[str] | None:
    card_config = card_config_override
    if card_config is None:
        card_config = app[SESSION_CARD_CONFIGS_KEY].get(_session_key_for_session(app, session), {})
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
    if not uses_codex_subscription(session.model, session.provider):
        session.subscription_usage = ""
        return
    if session.status != "completed" or session.subscription_usage_checked:
        return
    footer_fields = _footer_fields_for_session(app, session)
    if not footer_fields or "subscription_usage" not in footer_fields:
        return
    session.subscription_usage_checked = True
    route = (session.model, session.provider)
    try:
        usage = await fetch_codex_subscription_usage(
            app[OPERATIONS_HERMES_ROOT_KEY]
        )
    except Exception:
        # Optional account usage must never strand the terminal card.
        usage = ""
    # A delayed account response must not attach to changed model metadata.
    session.subscription_usage = usage if route == (session.model, session.provider) else ""


def _interaction_mode_for_session_key(
    app: web.Application, session_key: str, *, card_config_override: dict[str, Any] | None = None,
) -> str:
    card_config = card_config_override
    if card_config is None:
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
            resolved = _card_config_for_client(
                factory, actual_bot_id, base_card, profile_card
            )
        else:
            resolved = dict(base_card)
    else:
        resolved = _card_config_for_client(
            feishu_client, actual_bot_id, base_card, profile_card
        )
    # Overlays are resolved once when the turn opens, then the selectors are
    # dropped: an existing card must not change its appearance mid-turn.
    return apply_chat_card_overrides(
        resolved,
        chat_type=event.data.get("chat_type") if isinstance(event.data, dict) else None,
        chat_id=event.chat_id,
    )


def _resolve_notice_chat_type(
    app: web.Application,
    session: CardSession | None,
    event: SidecarEvent,
    profile_id: str | None,
) -> str:
    raw = event.data.get("chat_type") if isinstance(event.data, dict) else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    if session is not None and isinstance(session.chat_type, str) and session.chat_type.strip():
        return session.chat_type.strip().lower()
    expected_profile = profile_id or "default"
    for candidate in reversed(list(app[SESSIONS_KEY].values())):
        if (
            candidate.chat_id == event.chat_id
            and (candidate.route_profile_id or "default") == expected_profile
            and isinstance(candidate.chat_type, str)
            and candidate.chat_type.strip()
        ):
            return candidate.chat_type.strip().lower()
    return ""


def _is_suppressed_system_notice(
    request: web.Request,
    *,
    session: CardSession | None,
    session_key: str,
    event: SidecarEvent,
    profile_id: str | None,
) -> bool:
    if event.event != "system.notice" or not isinstance(event.data, dict):
        return False
    notice_kind = str(event.data.get("notice_kind") or "").strip().lower()
    if not notice_kind:
        return False
    card_config = (
        request.app[SESSION_CARD_CONFIGS_KEY].get(session_key)
        if session is not None
        else None
    )
    if not isinstance(card_config, dict):
        effective_chat_type = _resolve_notice_chat_type(
            request.app, session, event, profile_id
        )
        effective_event = event
        if effective_chat_type and not event.data.get("chat_type"):
            from dataclasses import replace as _replace_event
            effective_event = _replace_event(
                event, data={**event.data, "chat_type": effective_chat_type}
            )
        route = _resolve_route(request, effective_event)
        card_config = _resolve_session_card_config(
            request.app,
            route.bot_id if route is not None else None,
            effective_event,
        )
    suppressed = normalize_suppressed_notice_kinds(
        card_config.get("suppressed_notice_kinds", [])
    )
    return notice_kind in suppressed


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
    reply_in_thread: bool = False,
    delivery_key: str = "",
    delivery_kind: str = "chat",
    profile_id: str | None = None,
) -> CardDeliveryResult:
    return await _send_card_for_app(
        request.app,
        chat_id,
        card,
        bot_id,
        thread_id=thread_id,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=reply_in_thread,
        delivery_key=delivery_key,
        delivery_kind=delivery_kind,
        profile_id=profile_id,
    )


async def _send_card_for_app(
    app: web.Application,
    chat_id: str,
    card: dict[str, Any],
    bot_id: str | None,
    thread_id: str | None = None,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
    delivery_key: str = "",
    delivery_kind: str = "chat",
    profile_id: str | None = None,
) -> CardDeliveryResult:
    metrics: SidecarMetrics = app[METRICS_KEY]
    metrics.feishu_send_attempts += 1
    if reply_in_thread and not reply_to_message_id:
        result = CardDeliveryResult(
            message_id=None,
            outcome="not_sent",
            error_kind="ReplyThreadAnchorMissing",
        )
        metrics.feishu_send_failures += 1
        _record_send_error(app, result, bot_id=bot_id)
        return result
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
    notice_scope = _restart_notice_scope(
        profile_id=profile_id, bot_id=bot_id, chat_id=chat_id,
        thread_id=thread_id or (reply_to_message_id if reply_in_thread else None),
    )
    notice_snapshot = (
        # A second restart/draining notice is not evidence of resumed activity.
        _restart_notice_snapshot(app, notice_scope) if delivery_kind != "notice" else ()
    )
    navigation = None
    try:
        send_delivery = getattr(client, "send_card_delivery", None)
        if callable(send_delivery):
            send_kwargs: dict[str, Any] = {
                "thread_id": thread_id,
                "reply_to_message_id": reply_to_message_id,
                "delivery_uuid": delivery_uuid,
            }
            if reply_in_thread:
                send_kwargs["reply_in_thread"] = True
            send_result = await send_delivery(chat_id, card, **send_kwargs)
            message_id = str(getattr(send_result, "message_id", "") or "")
            retry_count = int(getattr(send_result, "retry_count", 0) or 0)
            candidate = getattr(send_result, "navigation", None)
            if (isinstance(candidate, MessageNavigation) and candidate.message_id == message_id
                    and candidate.chat_id == chat_id):
                navigation = candidate
        else:
            send_kwargs = {
                "thread_id": thread_id,
                "reply_to_message_id": reply_to_message_id,
            }
            if reply_in_thread:
                send_kwargs["reply_in_thread"] = True
            message_id = await client.send_card(chat_id, card, **send_kwargs)
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
    _retire_restart_notices(app, notice_scope, notice_snapshot)
    return CardDeliveryResult(
        message_id=message_id,
        outcome="delivered",
        retry_count=retry_count,
        navigation=navigation,
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


async def _delete_card_for_app(
    app: web.Application,
    message_id: str,
    bot_id: str | None,
) -> bool:
    """Recall a card the bot posted; True when it is gone (or was already gone).

    Best-effort by design: Feishu may refuse (missing scope, message too old), so callers must keep
    a usable fallback rather than assuming the message disappeared.
    """
    if not message_id:
        return False
    try:
        await _client_for_bot(app, bot_id).delete_message(message_id)
    except Exception as exc:
        app[DIAGNOSTICS_KEY]["last_delete_error"] = exc.__class__.__name__[:200]
        # Keep useful, validated API codes without exposing exception bodies,
        # URLs, credentials or raw message/bot identifiers.
        logger.warning(
            "Feishu card recall failed: %s message_hash=%s",
            _safe_update_error_message(bot_id, exc),
            _diagnostic_id_hash(message_id),
        )
        return False
    return True


def _is_heartbeat_notice(event: SidecarEvent) -> bool:
    data = event.data if isinstance(event.data, dict) else {}
    return (
        event.event == "system.notice"
        and str(data.get("notice_kind") or "").strip().lower() == "heartbeat"
    )


def _cancel_heartbeat_recall(app: web.Application, session_key: str) -> None:
    tasks: Dict[str, asyncio.Task[None]] = app.get(HEARTBEAT_RECALL_TASKS_KEY) or {}
    task = tasks.pop(session_key, None)
    if task is not None and not task.done():
        task.cancel()


def _schedule_heartbeat_recall(
    app: web.Application,
    *,
    session_key: str,
    message_id: str,
    bot_id: str | None,
    session: CardSession | None,
) -> None:
    """(Re)arm the recall deadline for the heartbeat card of ``session_key``.

    Called on every delivery/update of a heartbeat card, so the deadline tracks the LAST time the
    card carried news: while the agent is working the card stays, and once it stops being refreshed
    the card is recalled instead of lingering in the thread as noise.
    """
    if (not session_key or not message_id or session is None
            or session.delivery_kind != "notice" or session.notice_kind != "heartbeat"):
        return
    tasks: Dict[str, asyncio.Task[None]] = app[HEARTBEAT_RECALL_TASKS_KEY]
    _cancel_heartbeat_recall(app, session_key)
    tasks[session_key] = asyncio.create_task(
        _run_heartbeat_recall(
            app,
            session_key=session_key,
            message_id=message_id,
            bot_id=bot_id,
            session=session,
        )
    )


async def _run_heartbeat_recall(
    app: web.Application,
    *,
    session_key: str,
    message_id: str,
    bot_id: str | None,
    session: CardSession | None,
) -> None:
    try:
        await asyncio.sleep(HEARTBEAT_RECALL_SECONDS)
        if session is not None and app[SESSIONS_KEY].get(session_key) is not session:
            # A newer card took over this key; its own task owns the deadline now.
            return
        if (session is None or session.delivery_kind != "notice"
                or session.notice_kind != "heartbeat"
                or session.status in {"completed", "failed"}
                or app[FEISHU_MESSAGE_IDS_KEY].get(session_key) != message_id):
            return
        if not await _delete_card_for_app(app, message_id, bot_id):
            # Recall refused (scope, age): keep the state so the card stays usable, and let the
            # next heartbeat re-arm the deadline.
            return
        app[FEISHU_MESSAGE_IDS_KEY].pop(session_key, None)
        # Drop the session too: with the card gone, a later heartbeat must post a FRESH card. Left
        # in place it would keep editing a recalled message — the 230011 failure loop.
        _cleanup_failed_session_state(app, session_key, session)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("Heartbeat recall failed: %s", exc)
    finally:
        tasks: Dict[str, asyncio.Task[None]] = app.get(HEARTBEAT_RECALL_TASKS_KEY) or {}
        if tasks.get(session_key) is asyncio.current_task():
            tasks.pop(session_key, None)


async def _stop_heartbeat_recalls(app: web.Application) -> None:
    tasks: Dict[str, asyncio.Task[None]] = app.get(HEARTBEAT_RECALL_TASKS_KEY) or {}
    pending = [task for task in tasks.values() if not task.done()]
    tasks.clear()
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _recall_schedule(request: web.Request) -> web.Response:
    """Withdraw one bot message ``delay_seconds`` after it was posted.

    The gateway asks for this on acknowledgements the user reads once ("↪ Redirected current run …"):
    leaving them keeps a stale instruction in the thread forever. Best-effort by design — a refusal
    (missing scope, message too old) leaves the message in place and is counted, never raised.
    """
    rejection = await _authenticate_sensitive_request(request)
    if rejection is not None:
        return rejection
    try:
        payload = json.loads(await request.read() or b"{}")
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "error": "payload must be an object"}, status=400)
    message_id = _safe_command_string(payload.get("message_id"))
    if not message_id:
        return web.json_response({"ok": False, "error": "message_id is required"}, status=400)
    delay = EPHEMERAL_RECALL_DEFAULT_SECONDS
    raw_delay = payload.get("delay_seconds")
    if raw_delay is not None:
        try:
            delay = float(raw_delay)
        except (TypeError, ValueError):
            return web.json_response(
                {"ok": False, "error": "delay_seconds must be a number"}, status=400
            )
    if not math.isfinite(delay):
        return web.json_response({"ok": False, "error": "delay_seconds must be finite"}, status=400)
    if _is_retained_card_message(request.app, message_id):
        return web.json_response({"ok": False, "error": "owned session card cannot be recalled"}, status=409)
    # Caller owns the delay: only the upper bound is enforced (see EPHEMERAL_RECALL_MAX_SECONDS).
    # No floor, because the sidecar has no way to know what "too soon" means for the caller — the
    # send already happened by the time the request arrives.
    delay = min(max(delay, 0.0), EPHEMERAL_RECALL_MAX_SECONDS)
    bot_id = _safe_command_string(payload.get("bot_id")) or None
    route_data = payload.get("route")
    clients = request.app[FEISHU_CLIENT_KEY]
    if route_data is not None and not isinstance(route_data, dict):
        return web.json_response({"ok":False,"error":"invalid recall route"}, status=400)
    # Family registration is narrower than the historical timer API: always
    # resolve a complete route, even when the caller also supplies a bot id.
    family = payload.get("notice_family")
    if family is not None:
        if family != "restart" or payload.get("record_only") is not True:
            return web.json_response({"ok": False, "error": "invalid notice policy"}, status=400)
        if len(message_id) > 256:
            return web.json_response({"ok": False, "error": "invalid notice message id"}, status=400)
        if not isinstance(route_data, dict) or not route_data.get("profile_id"):
            return web.json_response({"ok": False, "error": "notice route required"}, status=400)
        if not isinstance(route_data.get("conversation_id", ""), str):
            return web.json_response({"ok": False, "error": "invalid notice topic"}, status=400)
        requested_bot_id = bot_id
        bot_id = None
    if bot_id is None and (route_data is not None or isinstance(clients, dict)):
        route_data = dict(route_data or {})
        profile = route_data.get('profile_id') or 'default'
        if not isinstance(profile, str) or not PROFILE_ID_PATTERN.fullmatch(profile):
            return web.json_response({"ok":False,"error":"invalid recall profile"}, status=400)
        if isinstance(clients, dict) and profile not in clients:
            if family is not None:
                return web.json_response({"ok": False, "error": "unknown notice profile"}, status=409)
            if profile == 'default' and len(clients) == 1:
                profile = next(iter(clients))
            else:
                return web.json_response({"ok":False,"error":"recall route is ambiguous"}, status=409)
        chat = _safe_command_string(route_data.get('chat_id'))
        if not chat:
            return web.json_response({"ok":False,"error":"recall chat is required"}, status=400)
        route_fields = {'profile_id': profile}
        if family is not None and route_data.get('bot_id'):
            route_fields['bot_id'] = _safe_command_string(route_data.get('bot_id'))
        probe = SidecarEvent.from_dict(dict(schema_version='1', event='system.notice',
            conversation_id=_safe_command_string(route_data.get('conversation_id')) or chat,
            message_id=message_id, chat_id=chat, platform='feishu', sequence=0,
            created_at=time.time(), data=route_fields))
        route = _resolve_route(request, probe)
        if route is None:
            return web.json_response({"ok":False,"error":"recall route unavailable"}, status=409)
        bot_id = route.bot_id or None
    if family is not None:
        if requested_bot_id is not None and requested_bot_id != bot_id:
            return web.json_response({"ok": False, "error": "notice bot mismatch"}, status=409)
        scope = _restart_notice_scope(
            profile_id=profile, bot_id=bot_id, chat_id=chat,
            thread_id=_safe_command_string(route_data.get("conversation_id")),
        )
        if scope is None:
            return web.json_response({"ok": False, "error": "notice route unavailable"}, status=409)
    try:
        recall_client = _client_for_bot(request.app, bot_id)
    except (RuntimeError, ValueError, KeyError):
        return web.json_response({"ok":False,"error":"recall route unavailable"}, status=409)
    app_hash = (route_data or {}).get("app_id_hash")
    if app_hash is not None:
        app_id = getattr(getattr(recall_client, "config", None), "app_id", None)
        if (not isinstance(app_hash, str) or not isinstance(app_id, str) or not app_id
                or hashlib.sha256(app_id.encode()).hexdigest() != app_hash):
            return web.json_response({"ok":False,"error":"notice application mismatch"}, status=409)
    if family is not None:
        if not request.app[RESTART_NOTICES_KEY].register(scope, message_id):
            return web.json_response({"ok": False, "error": "notice capacity reached"}, status=429)
        registry = request.app[RESTART_NOTICES_KEY]
        generation = dict(registry.snapshot(scope))[message_id]
        _schedule_ephemeral_recall(request.app, message_id=message_id, bot_id=bot_id,
            delay_seconds=registry.expiry_delay(message_id, OWNED_NOTICE_TTL_SECONDS),
            notice_owner=(scope, generation))
        return web.json_response({"ok": True, "message_id": message_id, "record_only": True})
    scheduled = _schedule_ephemeral_recall(
        request.app,
        message_id=message_id,
        delay_seconds=delay,
        bot_id=bot_id,
    )
    if not scheduled:
        return web.json_response({"ok": False, "error": "recall capacity reached"}, status=429)
    return web.json_response({"ok": True, "message_id": message_id, "delay_seconds": delay})


def _is_retained_card_message(app: web.Application, message_id: str) -> bool:
    if (message_id in app[FEISHU_MESSAGE_IDS_KEY].values()
            or message_id in app[CARD_SUMMARIES_KEY]):
        return True
    return any(
        session.active_interaction is not None
        and session.active_interaction.feishu_message_id == message_id
        for session in app[SESSIONS_KEY].values()
    )


def _restart_notice_scope(*, profile_id, bot_id, chat_id, thread_id) -> NoticeScope | None:
    """Keep empty thread and empty bot exact; neither is a wildcard."""
    bot = str(bot_id or "")
    bot_profile = bot.split(":", 1)[0] if ":" in bot else ""
    profile = profile_id or bot_profile or "default"
    if (not isinstance(profile, str) or not PROFILE_ID_PATTERN.fullmatch(profile)
            or (bot_profile and bot_profile != profile) or not chat_id
            or any(len(str(value or "")) > 256 for value in (bot_id, chat_id, thread_id))):
        return None
    return NoticeScope(profile, bot, str(chat_id), str(thread_id or ""))


def _restart_notice_snapshot(app, scope):
    return app[RESTART_NOTICES_KEY].snapshot(scope) if scope is not None else ()


def _restart_scope_for_message(app, message_id, bot_id) -> NoticeScope | None:
    """Resolve an edit through actual message ownership, never session-id guessing."""
    scopes = []
    for session_key, session in app[SESSIONS_KEY].items():
        if session.delivery_kind == "notice":
            continue
        interaction = session.active_interaction
        if app[FEISHU_MESSAGE_IDS_KEY].get(session_key) == message_id:
            thread_id = session.conversation_id
            if thread_id == session.chat_id or not thread_id.startswith(("om_", "omt_")):
                thread_id = session.reply_to_message_id if session.reply_in_thread else ""
        elif interaction is not None and interaction.feishu_message_id == message_id:
            thread_id = interaction.thread_id or (
                interaction.reply_to_message_id if interaction.reply_in_thread else "")
        else:
            continue
        if app[MESSAGE_BOT_IDS_KEY].get(session_key) != bot_id:
            continue
        scope = _restart_notice_scope(
            profile_id=session.route_profile_id, bot_id=bot_id,
            chat_id=session.chat_id, thread_id=thread_id,
        )
        if scope is not None:
            scopes.append(scope)
    return scopes[0] if scopes and all(scope == scopes[0] for scope in scopes) else None


def _retire_restart_notices(app, scope, snapshot) -> None:
    if scope is None:
        return
    registry = app[RESTART_NOTICES_KEY]
    for message_id, generation in snapshot:
        if not registry.ready(scope, message_id, generation):
            continue
        # Keep ownership until actual deletion succeeds, including task-capacity
        # rejection. A later successful send can retry the same pending notice.
        _schedule_ephemeral_recall(
            app, message_id=message_id, delay_seconds=0, bot_id=scope.bot_id or None,
            notice_owner=(scope, generation),
        )


async def _restore_owned_notice_timers(app):
    # Checkpoint owners are loaded synchronously before this startup hook.
    for scope, mid, generation, delay in app[RESTART_NOTICES_KEY].expiry_schedule(OWNED_NOTICE_TTL_SECONDS):
        _schedule_ephemeral_recall(app, message_id=mid, delay_seconds=delay,
            bot_id=scope.bot_id or None, notice_owner=(scope, generation))


def _schedule_ephemeral_recall(app, *, message_id, delay_seconds, bot_id, notice_owner=None) -> bool:
    tasks = app[EPHEMERAL_RECALL_TASKS_KEY]
    key = (bot_id or "", message_id)
    if key in tasks:
        wake = app[EPHEMERAL_RECALL_WAKE_KEY].get(key)
        if notice_owner is not None and delay_seconds == 0 and wake and wake[0] == notice_owner:
            wake[1].set()  # Shorten the wait without cancelling an in-flight DELETE.
        return True
    if len(tasks) >= EPHEMERAL_RECALL_MAX_PENDING:
        return False
    wake = asyncio.Event()
    app[EPHEMERAL_RECALL_WAKE_KEY][key] = (notice_owner, wake)
    task = asyncio.create_task(_run_ephemeral_recall(
        app, message_id=message_id, delay_seconds=delay_seconds, bot_id=bot_id,
        notice_owner=notice_owner, wake=wake))
    tasks[key] = task
    def finished(done):
        if tasks.get(key) is done:
            tasks.pop(key, None)
            app[EPHEMERAL_RECALL_WAKE_KEY].pop(key, None)
    task.add_done_callback(finished)
    app[METRICS_KEY].ephemeral_recalls_scheduled += 1
    return True


async def _run_ephemeral_recall(
    app: web.Application,
    *,
    message_id: str,
    delay_seconds: float,
    bot_id: str | None,
    notice_owner: tuple[NoticeScope, int] | None = None,
    wake: asyncio.Event | None = None,
) -> None:
    metrics: SidecarMetrics = app[METRICS_KEY]
    try:
        if wake is None:
            await asyncio.sleep(delay_seconds)
        elif delay_seconds > 0:
            timer = asyncio.get_running_loop().call_later(delay_seconds, wake.set)
            try:
                await wake.wait()
            finally:
                timer.cancel()
        if notice_owner is not None:
            scope, generation = notice_owner
            if not app[RESTART_NOTICES_KEY].contains(scope, message_id, generation):
                return
        if _is_retained_card_message(app, message_id):
            # Counted as a failure, but leave a trace: this branch used to be completely silent, so a
            # recall that "failed" because the message had meanwhile become an owned session card was
            # indistinguishable from one the API refused. Debug, not warning — skipping is correct here.
            metrics.ephemeral_recall_failures += 1
            if notice_owner is not None:
                # This message acquired durable content. Retire its disposable
                # status permanently, before ordinary session retention expires.
                scope, generation = notice_owner
                app[RESTART_NOTICES_KEY].discard(scope, message_id, generation)
            logger.debug(
                "Ephemeral recall skipped: %s became an owned session card",
                _diagnostic_id_hash(message_id),
            )
            return
        if await _delete_card_for_app(app, message_id, bot_id):
            metrics.ephemeral_recalls_completed += 1
            if notice_owner is not None:
                scope, generation = notice_owner
                app[RESTART_NOTICES_KEY].discard(scope, message_id, generation)
        else:
            metrics.ephemeral_recall_failures += 1
            if notice_owner is not None:
                scope, generation = notice_owner
                app[RESTART_NOTICES_KEY].defer(scope, message_id, generation)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        metrics.ephemeral_recall_failures += 1
        if notice_owner is not None:
            scope, generation = notice_owner
            app[RESTART_NOTICES_KEY].defer(scope, message_id, generation)
        logger.debug("Ephemeral recall failed: %s", exc)


async def _stop_ephemeral_recalls(app: web.Application) -> None:
    tasks = app.get(EPHEMERAL_RECALL_TASKS_KEY, {})
    pending = [task for task in tasks.values() if not task.done()]
    tasks.clear()
    app[EPHEMERAL_RECALL_WAKE_KEY].clear()
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


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
    notice_scope = _restart_scope_for_message(app, message_id, bot_id)
    notice_snapshot = _restart_notice_snapshot(app, notice_scope)
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
        _retire_restart_notices(app, notice_scope, notice_snapshot)
        return True
    if notice_update:
        metrics.notice_update_failures += 1
    return False


async def _retry_terminal_update(
    app: web.Application, message_id: str, card: dict[str, Any], bot_id: str | None,
    is_current: Callable[[], bool] | None = None,
) -> bool:
    for delay in (1.0, 2.0, 4.0):
        if is_current is not None and not is_current():
            return False
        await asyncio.sleep(delay)
        if is_current is not None and not is_current():
            return False
        if await _update_card_for_app(app, message_id, card, bot_id, is_current=is_current):
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
    for aliases_key in (SESSION_ALIASES_KEY, REDIRECT_SESSION_ALIASES_KEY):
        aliases: Dict[str, str] = app.get(aliases_key) or {}
        aliases.pop(session_key, None)
        for alias_key, canonical_key in tuple(aliases.items()):
            if canonical_key == session_key:
                aliases.pop(alias_key, None)
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
    *,
    alias_to_session_key: str | None = None,
) -> None:
    """Mark stale active sessions for the same chat+conversation as unsuccessful.

    When the gateway interrupts a running turn and starts a new one (e.g. user
    sends a new message mid-turn), no message.completed is sent for the old turn.
    The old card stays stuck at "生成中" forever.  This function finds such
    orphaned sessions and ends them without claiming successful execution.

    Only abandons sessions that share the chat, conversation, topic root and
    profile, and skips the new session itself. In a group, matching verified
    requesters are also required: conversation membership does not prove interruption.
    Explicit redirect handoffs affect only their named source turn.
    """
    sessions: Dict[str, CardSession] = app[SESSIONS_KEY]
    feishu_message_ids: Dict[str, str] = app[FEISHU_MESSAGE_IDS_KEY]
    message_bot_ids: Dict[str, str] = app[MESSAGE_BOT_IDS_KEY]

    # Extract profile_id prefix from new_session_key (format: "profile:msg_id")
    new_profile = new_session_key.split(":", 1)[0] if ":" in new_session_key else ""
    # A redirect may move callbacks for one explicitly identified source turn.
    # Conversation membership (or a quoted message) alone is not evidence that
    # every historical turn belongs to this redirect.
    redirect_source_key = None
    if alias_to_session_key:
        data = event.data if isinstance(event.data, dict) else {}
        source_turn = data.get("redirect_from_turn_id")
        source_message = data.get("redirect_from_message_id")
        source_id = source_turn or source_message
        if isinstance(source_id, str) and source_id.strip():
            reference = _session_key_for_message_id(event, source_id.strip())
            if source_turn:
                redirect_source_key = reference
            else:
                redirect_source_key = legacy_reference_owner_key(
                    event, reference, sessions=sessions, aliases=app[SESSION_ALIASES_KEY],
                    accepts=lambda owner: owner.delivery_kind != "notice",
                )

    data = event.data if isinstance(event.data, dict) else {}
    sender = data.get("sender_open_id")
    new_sender = sender if isinstance(sender, str) and re.fullmatch(r"ou_[A-Za-z0-9_-]{1,128}", sender) else ""
    scope = data.get("execution_scope")
    new_scope = scope if type(scope) is str and re.fullmatch(r"[0-9a-f]{64}", scope) else ""
    chat_type = data.get("chat_type")
    new_is_group = isinstance(chat_type, str) and chat_type.strip().lower() == "group"

    stale_keys = []
    for key, sess in sessions.items():
        if key == new_session_key:
            continue
        if not event_conversation_matches(sess, event):
            continue
        if not event.turn_id and not alias_to_session_key and not new_scope and not legacy_alias_owner_matches(sess, event):
            continue
        if sess.status in {"completed", "failed"} and key != redirect_source_key:
            continue
        if sess.delivery_kind == "notice":
            continue
        # Match profile prefix
        key_profile = key.split(":", 1)[0] if ":" in key else ""
        if key_profile != new_profile:
            continue
        if alias_to_session_key:
            if key != redirect_source_key or not legacy_alias_owner_matches(sess, event):
                continue
        elif new_scope or sess.execution_scope or event.producer == "plugin":
            if not new_scope or new_scope != sess.execution_scope:
                continue
        elif new_is_group or sess.chat_type == "group" or new_sender or sess.sender_open_id:
            # Missing identity is not evidence that two group turns share an
            # owner. Also isolate known distinct senders in older envelopes
            # that did not carry chat_type.
            if not new_sender or not sess.sender_open_id or new_sender != sess.sender_open_id:
                continue
        stale_keys.append(key)

    for key in stale_keys:
        sess = sessions.get(key)
        if sess is None:
            continue
        already_terminal = sess.status in {"completed", "failed"}
        if alias_to_session_key and key == redirect_source_key:
            app[SESSION_ALIASES_KEY][key] = alias_to_session_key
            app[REDIRECT_SESSION_ALIASES_KEY][key] = alias_to_session_key
            for alias_key, canonical_key in tuple(app[SESSION_ALIASES_KEY].items()):
                if canonical_key == key:
                    app[SESSION_ALIASES_KEY][alias_key] = alias_to_session_key
        if already_terminal:
            continue
        sess.timeline.complete()
        sess.status = "failed"
        sess.answer_text = (
            sess.answer_text.rstrip()
            + "\n\n> 本轮已被新对话替代，任务尚未确认完成。"
        ).lstrip()
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

    _schedule_terminal_work(app, session_key, controller, render_and_update)


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
