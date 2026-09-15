from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import hmac
from math import isfinite
import atexit
import json
import os
import queue
import re
import secrets
import threading
from threading import Lock, RLock
from time import monotonic, time
from typing import Any
from urllib import parse, request

from . import __version__
from .event_auth import sign_event_request
from .events import completion_turn_outcome
from .operations_transport import read_transport_root_secret
from .profile_sources import TRUSTED_PROFILE_SOURCES
from .runtime_control import RuntimeControlLease, acquire_runtime_control
from .runtime_interaction_transport import RuntimeInteractionListener


OFFICIAL_HOOKS = (
    "pre_llm_call", "post_llm_call", "on_session_end",
    "on_session_reset", "on_session_finalize", "pre_tool_call",
    "post_tool_call", "pre_approval_request", "post_approval_response",
    "subagent_start", "subagent_stop",
)

DEFAULT_EVENT_URL = "http://127.0.0.1:8765/events"
DEFAULT_EVENT_TIMEOUT_SECONDS = 0.8
MAX_EVENT_REQUEST_BYTES = 256 * 1024
MAX_EVENT_RESPONSE_BYTES = 64 * 1024
MAX_EVENT_JSON_DEPTH = 16
MAX_EVENT_JSON_NODES = 4096
MAX_EVENT_JSON_TEXT_BYTES = 256 * 1024


class _RejectRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_PROXY_EVENT_OPENER = request.build_opener(
    request.ProxyHandler({}),
    _RejectRedirects(),
)


@dataclass(frozen=True)
class ProductionRuntimeConfig:
    enabled: bool
    event_url: str
    timeout_seconds: float


class SignedEventTransport:
    """Canonical, signed, bounded loopback transport for production hooks."""

    def __init__(
        self,
        *,
        event_url: str,
        timeout_seconds: float,
        secret_reader: Callable[[], bytes | None] | None = None,
    ) -> None:
        self.event_url = _canonical_loopback_event_url(event_url)
        if (
            type(timeout_seconds) not in (int, float)
            or not isfinite(timeout_seconds)
            or not 0.05 <= float(timeout_seconds) <= 5.0
        ):
            raise ValueError("event timeout is invalid")
        if secret_reader is not None and not callable(secret_reader):
            raise ValueError("event secret reader is invalid")
        self._timeout_seconds = float(timeout_seconds)
        self._secret_reader = secret_reader or read_transport_root_secret

    def __call__(
        self, payload: dict[str, object], timeout_seconds: float
    ) -> dict[str, object] | None:
        try:
            if not _is_bounded_ordinary_json_object(payload):
                return None
            body = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            if len(body) > MAX_EVENT_REQUEST_BYTES:
                return None
            secret = self._secret_reader()
            if type(secret) is not bytes or len(secret) != 32:
                return None
            requested_timeout = float(timeout_seconds)
            if not isfinite(requested_timeout) or requested_timeout <= 0:
                return None
            timeout = min(requested_timeout, self._timeout_seconds)
            headers = {"Content-Type": "application/json"}
            headers.update(sign_event_request(secret, body))
            req = request.Request(
                self.event_url,
                data=body,
                headers=headers,
                method="POST",
            )
            with _NO_PROXY_EVENT_OPENER.open(req, timeout=timeout) as response:
                status = int(getattr(response, "status", 0))
                if not 200 <= status < 300:
                    return None
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except (TypeError, ValueError):
                        return None
                    if not 0 <= declared_length <= MAX_EVENT_RESPONSE_BYTES:
                        return None
                raw = response.read(MAX_EVENT_RESPONSE_BYTES + 1)
            if not isinstance(raw, bytes) or len(raw) > MAX_EVENT_RESPONSE_BYTES:
                return None
            value = json.loads(raw.decode("utf-8"))
            if not _is_bounded_ordinary_json_object(value):
                return None
            return value
        except Exception:
            return None


def _is_bounded_ordinary_json_object(value: object) -> bool:
    if type(value) is not dict:
        return False
    nodes = 0
    active_containers: set[int] = set()
    stack: list[tuple[object, int, bool]] = [(value, 1, False)]
    while stack:
        current, depth, exiting = stack.pop()
        if exiting:
            active_containers.discard(id(current))
            continue
        nodes += 1
        if nodes > MAX_EVENT_JSON_NODES:
            return False
        current_type = type(current)
        if current_type is dict:
            if depth > MAX_EVENT_JSON_DEPTH or id(current) in active_containers:
                return False
            active_containers.add(id(current))
            stack.append((current, depth, True))
            if nodes + len(current) > MAX_EVENT_JSON_NODES:
                return False
            items = tuple(current.items())
            nodes += len(items)
            for key, item in reversed(items):
                if (
                    type(key) is not str
                    or len(key) > MAX_EVENT_JSON_TEXT_BYTES
                    or len(key.encode("utf-8")) > MAX_EVENT_JSON_TEXT_BYTES
                ):
                    return False
                stack.append((item, depth + 1, False))
            continue
        if current_type is list:
            if depth > MAX_EVENT_JSON_DEPTH or id(current) in active_containers:
                return False
            if nodes + len(current) > MAX_EVENT_JSON_NODES:
                return False
            active_containers.add(id(current))
            stack.append((current, depth, True))
            for item in reversed(current):
                stack.append((item, depth + 1, False))
            continue
        if current_type is str:
            if (
                len(current) > MAX_EVENT_JSON_TEXT_BYTES
                or len(current.encode("utf-8")) > MAX_EVENT_JSON_TEXT_BYTES
            ):
                return False
            continue
        if current is None or current_type is bool or current_type is int:
            continue
        if current_type is float and isfinite(current):
            continue
        return False
    return True


def _canonical_loopback_event_url(value: object) -> str:
    if (
        type(value) is not str
        or value != value.strip()
        or "?" in value
        or "#" in value
    ):
        raise ValueError("event URL is invalid")
    try:
        parsed = parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("event URL is invalid") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.path != "/events"
    ):
        raise ValueError("event URL is invalid")
    hostname = parsed.hostname
    normalized_host = f"[{hostname}]" if ":" in hostname else hostname
    canonical = f"http://{normalized_host}:{port}/events"
    if value != canonical:
        raise ValueError("event URL is invalid")
    return canonical


def _load_production_runtime_config() -> ProductionRuntimeConfig:
    enabled_text = os.environ.get("HERMES_FEISHU_CARD_ENABLED", "1").strip().lower()
    enabled = enabled_text not in {"0", "false", "no", "off"}
    event_url = os.environ.get(
        "HERMES_FEISHU_CARD_EVENT_URL", DEFAULT_EVENT_URL
    )
    timeout_text = os.environ.get("HERMES_FEISHU_CARD_TIMEOUT_MS")
    timeout_seconds = DEFAULT_EVENT_TIMEOUT_SECONDS
    if timeout_text is not None:
        try:
            timeout_ms = int(timeout_text)
        except (TypeError, ValueError):
            timeout_ms = 0
        if 50 <= timeout_ms <= 5000:
            timeout_seconds = timeout_ms / 1000.0
    return ProductionRuntimeConfig(
        enabled=enabled,
        event_url=_canonical_loopback_event_url(event_url or DEFAULT_EVENT_URL),
        timeout_seconds=timeout_seconds,
    )


class TurnState(str, Enum):
    PENDING_START = "pending-start"
    CARD_ACTIVE = "card-active"
    NATIVE_BYPASS = "native-bypass"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class IngressBinding:
    profile_id: str
    profile_source: str
    session_id: str
    gateway_session_key: str
    generation: str
    chat_id: str
    incoming_message_id: str
    reply_to_message_id: str
    thread_id: str
    expires_at: float


@dataclass
class TurnBinding:
    ingress: IngressBinding
    turn_id: str
    _state: TurnState = field(default=TurnState.PENDING_START, init=False, repr=False, compare=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False, compare=False)

    @property
    def state(self) -> TurnState:
        with self._lock:
            return self._state

    @property
    def accepts_observer_events(self) -> bool:
        with self._lock:
            return self._state is TurnState.CARD_ACTIVE

    def record_started_result(self, result: object) -> TurnState:
        with self._lock:
            if self._state is not TurnState.PENDING_START:
                return self._state
            if self._is_accepted_started_result(result):
                self._state = TurnState.CARD_ACTIVE
            else:
                self._state = TurnState.NATIVE_BYPASS
            return self._state

    @staticmethod
    def _is_accepted_started_result(result: object) -> bool:
        if type(result) is not dict:
            return False
        if not all(type(key) is str for key in result):
            return False
        keys = set(result)
        if keys not in (
            {"ok", "applied"},
            {"ok", "applied", "delivery"},
        ):
            return False
        if result["ok"] is not True or result["applied"] is not True:
            return False
        if "delivery" not in result:
            return True
        delivery = result["delivery"]
        if type(delivery) is not dict:
            return False
        if not all(type(key) is str for key in delivery):
            return False
        if set(delivery) != {"outcome"}:
            return False
        outcome = delivery["outcome"]
        return type(outcome) is str and outcome == "delivered"

    def finish(self) -> bool:
        with self._lock:
            if self._state is TurnState.TERMINAL:
                return False
            self._state = TurnState.TERMINAL
            return True


@dataclass(frozen=True)
class ObserverEvent:
    sequence: int
    producer: str
    payload: dict[str, object]


class TurnEventCoordinator:
    """Allocate one turn-local sequence and bound asynchronous observer work."""

    _PRODUCERS = frozenset({"plugin", "patch", "legacy-patch"})
    _WORKER_POLL_SECONDS = 0.05
    _CLOSE_JOIN_SECONDS = 0.1

    def __init__(
        self,
        turn_id: str,
        *,
        max_pending: int = 64,
        deliver: Callable[[ObserverEvent], None] | None = None,
        start_worker: bool = True,
    ) -> None:
        if not self._is_nonblank(turn_id):
            raise ValueError("turn_id must be nonblank")
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending <= 0:
            raise ValueError("max_pending must be a positive integer")
        self.turn_id = turn_id
        self._next = 0
        self._barrier: int | None = None
        self._terminal_sequence: int | None = None
        self._closed = False
        self._lock = Lock()
        self._queue: queue.Queue[ObserverEvent] = queue.Queue(maxsize=max_pending)
        self._deliver = deliver or (lambda event: None)
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(
                target=self._deliver_observer_events,
                name="hfc-turn-observer",
                daemon=True,
            )
            self._worker.start()

    def next_sequence(self, producer: str) -> int:
        self._validate_producer(producer)
        with self._lock:
            value, self._next = self._next, self._next + 1
            return value

    def close_terminal_barrier(self) -> int:
        with self._lock:
            if self._barrier is None:
                self._barrier = self._next - 1
            return self._barrier

    def next_terminal_sequence(self) -> int:
        with self._lock:
            if self._barrier is None:
                raise ValueError("terminal barrier is not closed")
            if self._terminal_sequence is not None:
                return self._terminal_sequence
            value, self._next = self._next, self._next + 1
            self._terminal_sequence = value
            self._closed = True
            return value

    def event_id(self, kind: str, *, item_id: str = "", phase: str = "") -> str:
        if kind in {"started", "completed", "failed"}:
            return f"turn:{self.turn_id}:{kind}"
        if kind not in {"tool", "approval", "subagent"} or not self._is_nonblank(item_id):
            raise ValueError("invalid event identity")
        if phase not in {"started", "terminal"}:
            raise ValueError("invalid event phase")
        return f"{kind}:{self.turn_id}:{item_id}:{phase}"

    def submit_observer(
        self,
        payload: dict[str, object],
        *,
        producer: str,
        event_id_for_sequence: Callable[[int], str] | None = None,
    ) -> bool:
        self._validate_producer(producer)
        with self._lock:
            if self._closed or self._barrier is not None:
                return False
            sequence, self._next = self._next, self._next + 1
            event = ObserverEvent(sequence, producer, dict(payload, sequence=sequence))
            if event_id_for_sequence is not None:
                event.payload["event_id"] = event_id_for_sequence(sequence)
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                return False
            return True

    def drain_before_terminal(self, timeout_seconds: float) -> None:
        deadline = monotonic() + max(0.0, timeout_seconds)
        with self._lock:
            self._closed = True
        while self._queue.unfinished_tasks and monotonic() < deadline:
            threading.Event().wait(0.001)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=self._CLOSE_JOIN_SECONDS)

    def _deliver_observer_events(self) -> None:
        while True:
            try:
                event = self._queue.get(timeout=self._WORKER_POLL_SECONDS)
            except queue.Empty:
                with self._lock:
                    if self._closed:
                        return
                continue
            try:
                self._deliver(event)
            except Exception:
                pass
            finally:
                self._queue.task_done()

    @staticmethod
    def _is_nonblank(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @classmethod
    def _validate_producer(cls, producer: object) -> None:
        if producer not in cls._PRODUCERS:
            raise ValueError("unknown producer")


class IngressBindingRegistry:
    """A bounded, one-shot registry for Feishu ingress bindings."""

    _MAX_BINDINGS = 1024
    _PROFILE_SOURCES = TRUSTED_PROFILE_SOURCES

    def __init__(
        self,
        now: Callable[[], float] = time,
        *,
        lock: RLock | None = None,
    ) -> None:
        self._now = now
        self._bindings: OrderedDict[tuple[str, str, str], IngressBinding] = OrderedDict()
        self._lock = lock or RLock()

    def bind(self, binding: IngressBinding) -> bool:
        with self._lock:
            now = self._now()
            self._prune_expired(now)
            if not self._is_valid_binding(binding):
                return False
            if binding.expires_at <= now:
                return False
            pair = (binding.profile_id, binding.session_id)
            for key in tuple(self._bindings):
                if key[:2] == pair:
                    del self._bindings[key]
            key = (*pair, binding.generation)
            self._bindings[key] = binding
            while len(self._bindings) > self._MAX_BINDINGS:
                self._bindings.popitem(last=False)
            return True

    def claim(
        self, profile_id: str, session_id: str, generation: str, turn_id: str
    ) -> TurnBinding | None:
        with self._lock:
            self._prune_expired(self._now())
            if not all(
                self._is_exact_nonblank(value)
                for value in (profile_id, session_id, generation, turn_id)
            ):
                return None
            binding = self._bindings.pop((profile_id, session_id, generation), None)
            if binding is None:
                return None
            return TurnBinding(ingress=binding, turn_id=turn_id)

    def claim_unique_session(self, session_id: str, turn_id: str) -> TurnBinding | None:
        with self._lock:
            self._prune_expired(self._now())
            if not all(self._is_exact_nonblank(value) for value in (session_id, turn_id)):
                return None
            candidates = [
                (key, binding)
                for key, binding in self._bindings.items()
                if binding.session_id == session_id
            ]
            if len(candidates) != 1:
                return None
            key, binding = candidates[0]
            del self._bindings[key]
            return TurnBinding(ingress=binding, turn_id=turn_id)

    def clear(self) -> None:
        with self._lock:
            self._bindings.clear()

    def remove_session(self, session_id: object) -> None:
        with self._lock:
            self._prune_expired(self._now())
            if not self._is_exact_nonblank(session_id):
                return
            for key, binding in tuple(self._bindings.items()):
                if binding.session_id == session_id:
                    del self._bindings[key]

    @staticmethod
    def _is_exact_nonblank(value: object) -> bool:
        return type(value) is str and bool(value.strip())

    @classmethod
    def _is_valid_binding(cls, binding: object) -> bool:
        if type(binding) is not IngressBinding:
            return False
        if not all(
            cls._is_exact_nonblank(value)
            for value in (
                binding.profile_id,
                binding.session_id,
                binding.gateway_session_key,
                binding.generation,
                binding.chat_id,
                binding.incoming_message_id,
                binding.reply_to_message_id,
            )
        ):
            return False
        if (
            type(binding.profile_source) is not str
            or binding.profile_source not in cls._PROFILE_SOURCES
        ):
            return False
        if type(binding.thread_id) is not str:
            return False
        expires_at = binding.expires_at
        if type(expires_at) not in (int, float):
            return False
        try:
            return isfinite(expires_at)
        except (OverflowError, TypeError, ValueError):
            return False

    def _prune_expired(self, now: float) -> None:
        for key, binding in tuple(self._bindings.items()):
            if binding.expires_at <= now:
                del self._bindings[key]


@dataclass(frozen=True, repr=False)
class PendingApproval:
    session_key: str
    turn_id: str
    tool_call_id: str
    command_fingerprint: str
    surface: str
    interaction_id: str
    expires_at: float
    hfc_owned: bool = False


@dataclass(frozen=True, repr=False)
class _AnswerEntry:
    answer: str
    expires_at: float


@dataclass(frozen=True, repr=False)
class _TerminalRecord:
    payload: dict[str, object]
    response: dict[str, object] | None
    expires_at: float


@dataclass(repr=False)
class _PatchInteraction:
    turn_digest: str
    pending_handle: object
    selected_value: str | None
    expires_at: float
    interaction_key: str | None = None
    token_digest: str | None = None
    descriptor_expires_at: float | None = None
    resolver: Callable[[str], bool] | None = None
    resolving_value: str | None = None
    resolution_complete: threading.Event | None = None
    hfc_owned: bool = False
    admission_payload: dict[str, object] | None = None


@dataclass(frozen=True, repr=False)
class _ConsumedPatchInteraction:
    turn_digest: str
    state: str
    expires_at: float


@dataclass
class _StartedTransport:
    gate: Lock = field(default_factory=Lock)
    completion: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False
    posting: bool = False


class PluginRuntime:
    """Bounded official-hook coordinator.

    Lock order is runtime lock -> registry/turn/coordinator locks.  Transport,
    coordinator drain, and coordinator close always run after releasing the
    runtime lock, so callbacks never wait while blocking cross-map cleanup.
    """

    _MAX_ENTRIES = 1024
    _ANSWER_TTL_SECONDS = 30.0
    _STATE_TTL_SECONDS = 300.0
    _NATIVE_HANDOFF_PROTOCOL = "hfc-native-handoff-v2"
    _NATIVE_HANDOFF_MAX_FUTURE_SECONDS = 3630.0
    _HANDOFF_ID_RE = re.compile(r"[0-9a-f]{64}")
    _UUID_SEED_RE = re.compile(r"[0-9a-f]{32}")
    _PATCH_INTERACTION_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
    _PATCH_INTERACTION_KINDS = frozenset({"approval", "clarify", "slash"})
    _MAX_PATCH_INTERACTIONS = 1024
    _PATCH_INTERACTION_TTL_SECONDS = 300.0
    _PATCH_INTERACTION_MAX_TTL_SECONDS = 3600.0
    _MAX_PATCH_DELTA_BYTES = 64 * 1024
    _MAX_PATCH_SELECTED_BYTES = 4096
    _RUNTIME_INTERACTION_PROTOCOL = "hfc-runtime-interaction-v1"
    _RUNTIME_INTERACTION_DESCRIPTOR_TTL_SECONDS = 30.0

    def __init__(
        self,
        *,
        post: Callable[[dict[str, object], float], object],
        now: Callable[[], float] = time,
        observer_timeout_seconds: float = 0.8,
        terminal_timeout_seconds: float = 10.0,
        max_pending_observers: int = 64,
    ) -> None:
        if not callable(post) or not callable(now):
            raise ValueError("post and now must be callable")
        if (
            type(max_pending_observers) is not int
            or max_pending_observers <= 0
            or max_pending_observers > self._MAX_ENTRIES
        ):
            raise ValueError("max_pending_observers is invalid")
        self._post = post
        self._now = now
        self._observer_timeout_seconds = max(0.0, float(observer_timeout_seconds))
        self._terminal_timeout_seconds = max(0.0, float(terminal_timeout_seconds))
        self._max_pending_observers = max_pending_observers
        self._lock = RLock()
        self._registry = IngressBindingRegistry(now=now, lock=self._lock)
        self._turns: OrderedDict[str, TurnBinding] = OrderedDict()
        self._coordinators: OrderedDict[str, TurnEventCoordinator] = OrderedDict()
        self._answers: OrderedDict[str, _AnswerEntry] = OrderedDict()
        self._terminal_records: OrderedDict[str, _TerminalRecord] = OrderedDict()
        self._dispositions: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._pending_approvals: OrderedDict[
            tuple[str, str, str, str, str], PendingApproval
        ] = OrderedDict()
        self._claimed_approvals: set[tuple[str, str, str, str, str]] = set()
        self._subagents: OrderedDict[tuple[str, str], tuple[str, float]] = OrderedDict()
        self._terminal_owners: OrderedDict[str, object] = OrderedDict()
        self._started_transports: OrderedDict[str, _StartedTransport] = OrderedDict()
        self._patch_interactions: OrderedDict[
            str, _PatchInteraction | _ConsumedPatchInteraction
        ] = OrderedDict()
        self._interaction_cleanup_turns: set[str] = set()
        self._deferred_interaction_cleanup_turns: set[str] = set()
        self._runtime_id = secrets.token_hex(32)
        self._runtime_interaction_token_root = secrets.token_bytes(32)
        self._runtime_interaction_listener: RuntimeInteractionListener | None = None
        self._runtime_interaction_listener_lock = Lock()
        self._closed = False
        self._close_lock = Lock()
        self._close_started = False
        self._close_failed = False
        self._close_complete = threading.Event()

    def bind_ingress_from_values(
        self,
        profile_id: object,
        profile_source: object,
        session_id: object,
        gateway_session_key: object,
        generation: object,
        chat_id: object,
        incoming_message_id: object,
        reply_to_message_id: object,
        thread_id: object,
    ) -> bool:
        binding = IngressBinding(
            profile_id=profile_id,
            profile_source=profile_source,
            session_id=session_id,
            gateway_session_key=gateway_session_key,
            generation=generation,
            chat_id=chat_id,
            incoming_message_id=incoming_message_id,
            reply_to_message_id=reply_to_message_id,
            thread_id=thread_id,
            expires_at=self._now() + self._STATE_TTL_SECONDS,
        )
        with self._lock:
            return self._registry.bind(binding)

    def turn_state(self, turn_id: object) -> TurnState | None:
        with self._lock:
            self._expire_locked(self._now())
            turn = self._turns.get(turn_id) if self._exact_nonblank(turn_id) else None
        return turn.state if turn is not None else None

    def submit_patch_delta(
        self,
        turn_id: object,
        event_name: object,
        text: object,
        mode: object,
    ) -> bool:
        try:
            if (
                not self._exact_nonblank(turn_id)
                or type(event_name) is not str
                or type(mode) is not str
                or type(text) is not str
                or not text
                or len(text) > self._MAX_PATCH_DELTA_BYTES
                or (event_name, mode)
                not in {
                    ("answer.delta", "delta"),
                    ("thinking.delta", "append_block"),
                }
                or len(text.encode("utf-8")) > self._MAX_PATCH_DELTA_BYTES
            ):
                return False
            with self._lock:
                self._expire_locked(self._now())
                turn = self._turns.get(turn_id)
                coordinator = self._coordinators.get(turn_id)
                if (
                    turn is None
                    or turn.state is not TurnState.CARD_ACTIVE
                    or turn_id in self._terminal_owners
                    or coordinator is None
                    or coordinator.turn_id != turn_id
                ):
                    return False
                payload = self._base_payload(
                    turn, sequence=0, created_at=self._now()
                )
                payload.update(
                    event=event_name,
                    event_id="",
                    producer="patch",
                    phase="delta",
                    data={"text": text, "mode": mode},
                )
                return coordinator.submit_observer(
                    payload,
                    producer="patch",
                    event_id_for_sequence=lambda sequence: (
                        f"patch:{turn_id}:{event_name}:{sequence}"
                    ),
                )
        except Exception:
            return False

    def submit_patch_status_notice(
        self,
        turn_id: object,
        *,
        notice_kind: object,
        notice_id: object,
    ) -> bool:
        try:
            if (
                not self._exact_nonblank(turn_id)
                or type(notice_kind) is not str
                or type(notice_id) is not str
                or (notice_kind, notice_id)
                != ("context-compaction", "context-compaction:active")
            ):
                return False
            with self._lock:
                self._expire_locked(self._now())
                coordinator = self._card_active_coordinator_locked(turn_id)
                turn = self._turns.get(turn_id)
                if coordinator is None or turn is None:
                    return False
                payload = self._base_payload(
                    turn, sequence=0, created_at=self._now()
                )
                payload.update(
                    event="system.notice",
                    event_id="",
                    producer="patch",
                    phase="started",
                    data={
                        "notice_kind": "context-compaction",
                        "notice_id": "context-compaction:active",
                        "notice_scope": "session",
                        "phase": "started",
                        "title": "正在压缩上下文",
                        "level": "info",
                        "content": "正在总结较早的对话，完成后会继续当前任务。",
                        "create_session": True,
                        "display_status": "in_progress",
                    },
                )
                return coordinator.submit_observer(
                    payload,
                    producer="patch",
                    event_id_for_sequence=lambda sequence: (
                        "patch:"
                        f"{turn_id}:system.notice:context-compaction:active:{sequence}"
                    ),
                )
        except Exception:
            return False

    def register_patch_interaction(
        self,
        kind: object,
        session_identity: object,
        turn_id: object,
        interaction_id: object,
        fingerprint: object,
        pending_handle: object,
    ) -> bool:
        try:
            with self._lock:
                now = self._now()
                self._expire_locked(now)
                key = self._patch_interaction_key_locked(
                    kind,
                    session_identity,
                    turn_id,
                    interaction_id,
                    fingerprint,
                )
                if (
                    self._closed
                    or key is None
                    or pending_handle is None
                    or self._patch_turn_digest(turn_id)
                    in self._interaction_cleanup_turns
                ):
                    return False
                existing = self._patch_interactions.get(key)
                if existing is not None:
                    return (
                        type(existing) is _PatchInteraction
                        and existing.pending_handle is pending_handle
                    )
                if any(
                    type(state) is _PatchInteraction
                    and state.pending_handle is pending_handle
                    for state in self._patch_interactions.values()
                ):
                    return False
                if len(self._patch_interactions) >= self._MAX_PATCH_INTERACTIONS:
                    return False
                self._patch_interactions[key] = _PatchInteraction(
                    turn_digest=self._patch_turn_digest(turn_id),
                    pending_handle=pending_handle,
                    selected_value=None,
                    expires_at=now + self._PATCH_INTERACTION_TTL_SECONDS,
                )
                return True
        except Exception:
            return False

    def resolve_patch_interaction(
        self,
        kind: object,
        session_identity: object,
        turn_id: object,
        interaction_id: object,
        fingerprint: object,
        pending_handle: object,
        selected_value: object,
    ) -> bool:
        try:
            with self._lock:
                self._expire_locked(self._now())
                if self._closed:
                    return False
                key = self._patch_interaction_key_locked(
                    kind,
                    session_identity,
                    turn_id,
                    interaction_id,
                    fingerprint,
                )
                if (
                    key is None
                    or pending_handle is None
                    or not self._valid_patch_selected_value(selected_value)
                ):
                    return False
                state = self._patch_interactions.get(key)
                if (
                    type(state) is not _PatchInteraction
                    or state.pending_handle is not pending_handle
                    or state.turn_digest in self._interaction_cleanup_turns
                    or state.resolving_value is not None
                ):
                    return False
                if state.selected_value is None:
                    state.selected_value = selected_value
                    return True
                return state.selected_value == selected_value
        except Exception:
            return False

    def claim_patch_interaction(
        self,
        kind: object,
        session_identity: object,
        turn_id: object,
        interaction_id: object,
        fingerprint: object,
        pending_handle: object,
    ) -> str | None:
        try:
            with self._lock:
                now = self._now()
                self._expire_locked(now)
                if self._closed:
                    return None
                key = self._patch_interaction_key_locked(
                    kind,
                    session_identity,
                    turn_id,
                    interaction_id,
                    fingerprint,
                )
                if key is None or pending_handle is None:
                    return None
                state = self._patch_interactions.get(key)
                if (
                    type(state) is not _PatchInteraction
                    or state.pending_handle is not pending_handle
                    or state.turn_digest in self._interaction_cleanup_turns
                    or state.selected_value is None
                    or state.resolving_value is not None
                ):
                    return None
                selected_value = state.selected_value
                self._patch_interactions[key] = _ConsumedPatchInteraction(
                    turn_digest=state.turn_digest,
                    state="consumed",
                    expires_at=now + self._PATCH_INTERACTION_TTL_SECONDS,
                )
                return selected_value
        except Exception:
            return None

    def start_runtime_interaction_listener(self, secret: object) -> bool:
        if type(secret) is not bytes or len(secret) != 32:
            return False
        candidate: RuntimeInteractionListener | None = None
        try:
            with self._runtime_interaction_listener_lock:
                with self._lock:
                    if self._closed:
                        return False
                    current = self._runtime_interaction_listener
                if current is not None:
                    return current.snapshot()["accepting"] is True
                candidate = RuntimeInteractionListener(
                    secret, self.resolve_runtime_interaction_payload
                )
                candidate.start()
                with self._lock:
                    if self._closed or self._runtime_interaction_listener is not None:
                        accepted = False
                    else:
                        self._runtime_interaction_listener = candidate
                        accepted = True
                if not accepted:
                    candidate.close()
                    return False
                return True
        except Exception:
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            return False

    def arm_patch_interaction_descriptor(
        self,
        kind: object,
        session_identity: object,
        turn_id: object,
        interaction_id: object,
        fingerprint: object,
        pending_handle: object,
        resolver: object,
        descriptor_ttl_seconds: object = None,
    ) -> dict[str, object] | None:
        if not callable(resolver):
            return None
        if descriptor_ttl_seconds is None:
            descriptor_ttl = self._RUNTIME_INTERACTION_DESCRIPTOR_TTL_SECONDS
        elif type(descriptor_ttl_seconds) in (int, float):
            try:
                descriptor_ttl = float(descriptor_ttl_seconds)
                if (
                    not isfinite(descriptor_ttl)
                    or not 0 < descriptor_ttl <= self._PATCH_INTERACTION_MAX_TTL_SECONDS
                ):
                    return None
            except (OverflowError, TypeError, ValueError):
                return None
        else:
            return None
        try:
            with self._lock:
                now = self._now()
                self._expire_locked(now)
                if self._closed:
                    return None
                listener = self._runtime_interaction_listener
                key = self._patch_interaction_key_locked(
                    kind,
                    session_identity,
                    turn_id,
                    interaction_id,
                    fingerprint,
                )
                state = None if key is None else self._patch_interactions.get(key)
                if (
                    listener is None
                    or type(state) is not _PatchInteraction
                    or state.pending_handle is not pending_handle
                    or state.turn_digest in self._interaction_cleanup_turns
                    or state.selected_value is not None
                    or state.resolving_value is not None
                ):
                    return None
                resolve_url = listener.resolve_url
                if not resolve_url or not listener.accepts():
                    return None
                if state.resolver is not None and state.resolver is not resolver:
                    return None
                interaction_key = self._runtime_interaction_key(key)
                token = self._runtime_interaction_token(interaction_key)
                token_digest = self._runtime_interaction_token_digest(token)
                if state.interaction_key is None:
                    state.interaction_key = interaction_key
                    state.token_digest = token_digest
                    state.descriptor_expires_at = min(
                        state.expires_at,
                        now + descriptor_ttl,
                    )
                    state.resolver = resolver
                elif (
                    state.interaction_key != interaction_key
                    or state.token_digest != token_digest
                    or state.descriptor_expires_at is None
                    or state.resolver is not resolver
                ):
                    return None
                if now >= state.descriptor_expires_at:
                    return None
                return {
                    "protocol": self._RUNTIME_INTERACTION_PROTOCOL,
                    "runtime_id": self._runtime_id,
                    "resolve_url": resolve_url,
                    "interaction_key": interaction_key,
                    "token": token,
                    "expires_at": state.descriptor_expires_at,
                }
        except Exception:
            return None

    def admit_patch_interaction(
        self,
        kind: object,
        session_identity: object,
        turn_id: object,
        interaction_id: object,
        fingerprint: object,
        pending_handle: object,
        resolver: object,
        ui_data: object,
    ) -> bool:
        """Synchronously select HFC UI only after exact Sidecar delivery proof."""
        if not callable(resolver) or not self._valid_interaction_ui_data(ui_data):
            return False
        assert type(ui_data) is dict
        interaction_ttl = float(ui_data["timeout_seconds"])
        try:
            with self._lock:
                now = self._now()
                self._expire_locked(now)
                key = self._patch_interaction_key_locked(
                    kind,
                    session_identity,
                    turn_id,
                    interaction_id,
                    fingerprint,
                )
                state = None if key is None else self._patch_interactions.get(key)
                if (
                    self._closed
                    or type(state) is not _PatchInteraction
                    or state.pending_handle is not pending_handle
                    or state.turn_digest in self._interaction_cleanup_turns
                    or state.selected_value is not None
                    or state.resolving_value is not None
                ):
                    return False
                state.expires_at = max(state.expires_at, now + interaction_ttl)
        except Exception:
            return False
        descriptor = self.arm_patch_interaction_descriptor(
            kind,
            session_identity,
            turn_id,
            interaction_id,
            fingerprint,
            pending_handle,
            resolver,
            interaction_ttl,
        )
        if descriptor is None:
            return False
        key: str | None = None
        state: _PatchInteraction | None = None
        try:
            with self._lock:
                now = self._now()
                self._expire_locked(now)
                key = self._patch_interaction_key_locked(
                    kind,
                    session_identity,
                    turn_id,
                    interaction_id,
                    fingerprint,
                )
                candidate = None if key is None else self._patch_interactions.get(key)
                coordinator = (
                    self._card_active_coordinator_locked(turn_id)
                    if type(turn_id) is str
                    else None
                )
                turn = self._turns.get(turn_id) if type(turn_id) is str else None
                if (
                    self._closed
                    or type(candidate) is not _PatchInteraction
                    or candidate.pending_handle is not pending_handle
                    or candidate.resolver is not resolver
                    or candidate.turn_digest in self._interaction_cleanup_turns
                    or candidate.selected_value is not None
                    or candidate.resolving_value is not None
                    or coordinator is None
                    or turn is None
                    or now >= descriptor["expires_at"]
                ):
                    return False
                state = candidate
                if state.hfc_owned:
                    return True
                if state.admission_payload is None:
                    sequence = coordinator.next_sequence("patch")
                    payload = self._base_payload(
                        turn, sequence=sequence, created_at=now
                    )
                    safe_ui = deepcopy(ui_data)
                    data = {
                        "interaction_id": interaction_id,
                        "kind": kind,
                        **safe_ui,
                        "_hfc_runtime_admission": deepcopy(descriptor),
                    }
                    payload.update(
                        event="interaction.requested",
                        event_id=(
                            f"patch:{turn_id}:interaction:{key}:{sequence}"
                        ),
                        producer="patch",
                        phase="started",
                        data=data,
                    )
                    stored_payload = deepcopy(payload)
                    stored_descriptor = stored_payload["data"][
                        "_hfc_runtime_admission"
                    ]
                    stored_descriptor.pop("token", None)
                    state.admission_payload = stored_payload
                stored_payload = deepcopy(state.admission_payload)
                if state.interaction_key is None:
                    return False
                payload = deepcopy(stored_payload)
                payload["data"]["_hfc_runtime_admission"]["token"] = (
                    self._runtime_interaction_token(state.interaction_key)
                )
            result: object = None
            for attempt in range(2):
                try:
                    result = self._post(deepcopy(payload), self._terminal_timeout_seconds)
                except Exception:
                    result = None
                if result is not None or attempt == 1:
                    break
            if not self._is_exact_runtime_interaction_admission_response(result):
                return False
            with self._lock:
                checked_at = self._now()
                self._expire_locked(checked_at)
                current = None if key is None else self._patch_interactions.get(key)
                if (
                    current is not state
                    or type(current) is not _PatchInteraction
                    or current.pending_handle is not pending_handle
                    or current.resolver is not resolver
                    or current.turn_digest in self._interaction_cleanup_turns
                    or current.selected_value is not None
                    or current.resolving_value is not None
                    or current.admission_payload != stored_payload
                    or current.descriptor_expires_at is None
                    or checked_at >= current.descriptor_expires_at
                ):
                    return False
                current.hfc_owned = True
                return True
        except Exception:
            return False

    @staticmethod
    def _is_exact_runtime_interaction_admission_response(value: object) -> bool:
        if (
            type(value) is not dict
            or not all(type(key) is str for key in value)
            or set(value) != {"ok", "applied", "delivery", "runtime_admission"}
            or value["ok"] is not True
            or value["applied"] is not True
            or value["runtime_admission"] is not True
        ):
            return False
        delivery = value["delivery"]
        return (
            type(delivery) is dict
            and all(type(key) is str for key in delivery)
            and set(delivery) == {"outcome"}
            and type(delivery["outcome"]) is str
            and delivery["outcome"] == "delivered"
        )

    @classmethod
    def _valid_interaction_ui_data(cls, value: object) -> bool:
        if (
            type(value) is not dict
            or not all(type(key) is str for key in value)
            or set(value)
            != {
                "prompt",
                "description",
                "allow_custom_input",
                "multi_select",
                "timeout_seconds",
                "options",
            }
            or type(value["prompt"]) is not str
            or not value["prompt"].strip()
            or len(value["prompt"].encode("utf-8")) > 4096
            or type(value["description"]) is not str
            or len(value["description"].encode("utf-8")) > 4096
            or type(value["allow_custom_input"]) is not bool
            or type(value["multi_select"]) is not bool
            or type(value["timeout_seconds"]) not in (int, float)
            or type(value["options"]) is not list
            or len(value["options"]) > 32
            or (
                not value["options"]
                and value["allow_custom_input"] is not True
            )
        ):
            return False
        try:
            timeout = value["timeout_seconds"]
            if (
                not isfinite(timeout)
                or not 0 < timeout <= cls._PATCH_INTERACTION_MAX_TTL_SECONDS
            ):
                return False
        except (OverflowError, TypeError, ValueError):
            return False
        allowed_styles = {"default", "primary", "danger"}
        seen: set[str] = set()
        for option in value["options"]:
            if (
                type(option) is not dict
                or not all(type(key) is str for key in option)
                or set(option) != {"label", "value", "style"}
                or type(option["label"]) is not str
                or not option["label"].strip()
                or type(option["value"]) is not str
                or not cls._valid_patch_selected_value(option["value"])
                or type(option["style"]) is not str
                or option["style"] not in allowed_styles
                or option["value"] in seen
                or len(option["label"].encode("utf-8")) > 256
            ):
                return False
            seen.add(option["value"])
        return True

    def resolve_runtime_interaction_payload(self, payload: object) -> bool:
        if type(payload) is not dict or set(payload) != {
            "protocol", "runtime_id", "interaction_key", "token", "choice",
            "expires_at",
        }:
            return False
        if not all(type(key) is str for key in payload):
            return False
        protocol = payload["protocol"]
        runtime_id = payload["runtime_id"]
        interaction_key = payload["interaction_key"]
        token = payload["token"]
        choice = payload["choice"]
        expires_at = payload["expires_at"]
        if (
            type(protocol) is not str
            or protocol != self._RUNTIME_INTERACTION_PROTOCOL
            or type(runtime_id) is not str
            or runtime_id != self._runtime_id
            or type(interaction_key) is not str
            or self._HANDOFF_ID_RE.fullmatch(interaction_key) is None
            or type(token) is not str
            or self._HANDOFF_ID_RE.fullmatch(token) is None
            or not self._valid_patch_selected_value(choice)
            or type(expires_at) not in (int, float)
        ):
            return False
        try:
            if not isfinite(expires_at):
                return False
        except (OverflowError, TypeError, ValueError):
            return False

        state: _PatchInteraction | None = None
        state_key: str | None = None
        resolver: Callable[[str], bool] | None = None
        completion: threading.Event | None = None
        with self._lock:
            now = self._now()
            self._expire_locked(now)
            if self._closed or now >= expires_at:
                return False
            for candidate_key, candidate in self._patch_interactions.items():
                if (
                    type(candidate) is _PatchInteraction
                    and candidate.interaction_key == interaction_key
                ):
                    state = candidate
                    state_key = candidate_key
                    break
            if (
                state is None
                or state.turn_digest in self._interaction_cleanup_turns
                or state.descriptor_expires_at != expires_at
                or state.expires_at <= now
                or state.token_digest is None
                or not hmac.compare_digest(
                    state.token_digest,
                    self._runtime_interaction_token_digest(token),
                )
                or state.resolver is None
            ):
                return False
            if state.selected_value is not None:
                return state.selected_value == choice
            if state.resolving_value is not None:
                return False
            state.resolving_value = choice
            completion = threading.Event()
            state.resolution_complete = completion
            resolver = state.resolver

        try:
            accepted = resolver(choice) is True
        except Exception:
            accepted = False

        with self._lock:
            current = self._patch_interactions.get(state_key)
            if (
                not accepted
                or current is not state
                or state.resolving_value != choice
                or state.descriptor_expires_at != expires_at
            ):
                if current is state and state.resolving_value == choice:
                    state.resolving_value = None
                    state.resolution_complete = None
                resolved = False
            else:
                state.selected_value = choice
                state.resolving_value = None
                state.resolution_complete = None
                resolved = True
        if completion is not None:
            completion.set()
        if state is not None:
            self._complete_deferred_interaction_cleanup(state.turn_digest)
        return resolved

    def runtime_interaction_listener_snapshot(self) -> dict[str, object]:
        with self._lock:
            listener = self._runtime_interaction_listener
        if listener is None:
            return {"accepting": False, "poisoned": False, "worker_name": ""}
        return listener.snapshot()

    def pause_runtime_interaction_listener_for_replacement(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            listener = self._runtime_interaction_listener
        return listener is not None and listener.pause_for_replacement()

    def resume_runtime_interaction_listener_after_failed_replacement(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            listener = self._runtime_interaction_listener
        return listener is not None and listener.resume_after_failed_replacement()

    def _runtime_interaction_key(self, patch_key: str) -> str:
        digest = sha256(b"hfc-runtime-interaction-key-v1\0")
        digest.update(self._runtime_id.encode("ascii"))
        digest.update(b"\0")
        digest.update(patch_key.encode("ascii"))
        return digest.hexdigest()

    def _runtime_interaction_token(self, interaction_key: str) -> str:
        return hmac.new(
            self._runtime_interaction_token_root,
            b"hfc-runtime-interaction-token-v1\0" + interaction_key.encode("ascii"),
            "sha256",
        ).hexdigest()

    @staticmethod
    def _runtime_interaction_token_digest(token: str) -> str:
        return sha256(
            b"hfc-runtime-interaction-token-digest-v1\0" + token.encode("ascii")
        ).hexdigest()

    def handle_pre_llm_call(self, **kwargs: object) -> None:
        session_id = kwargs.get("session_id")
        turn_id = kwargs.get("turn_id")
        if type(kwargs.get("platform")) is not str or kwargs.get("platform") != "feishu":
            return None
        if not all(self._exact_nonblank(value) for value in (session_id, turn_id)):
            return None
        coordinator = TurnEventCoordinator(
            turn_id,
            max_pending=self._max_pending_observers,
            deliver=self._deliver_observer,
        )
        turn: TurnBinding | None = None
        started_transport = _StartedTransport()
        admitted = False
        evicted_coordinators: list[TurnEventCoordinator] = []
        evicted_started: list[_StartedTransport] = []
        with self._lock:
            self._expire_locked(self._now())
            if turn_id not in self._turns:
                admitted, evicted_coordinators, evicted_started = (
                    self._make_turn_room_locked()
                )
            if admitted:
                turn = self._registry.claim_unique_session(session_id, turn_id)
                if turn is not None:
                    self._turns[turn_id] = turn
                    self._coordinators[turn_id] = coordinator
                    self._started_transports[turn_id] = started_transport
                else:
                    admitted = False
        self._wait_started_transports(evicted_started)
        for evicted in evicted_coordinators:
            evicted.close()
        if not admitted or turn is None:
            coordinator.close()
            return None
        payload = self._base_payload(
            turn,
            sequence=coordinator.next_sequence("plugin"),
            created_at=self._now(),
        )
        payload.update(
            event="message.started",
            event_id=coordinator.event_id("started"),
            phase="started",
            data={
                "profile_id": turn.ingress.profile_id,
                "profile_source": turn.ingress.profile_source,
                "reply_to_message_id": turn.ingress.reply_to_message_id,
            },
        )
        with started_transport.gate:
            with self._lock:
                may_post = (
                    self._turns.get(turn_id) is turn
                    and self._started_transports.get(turn_id) is started_transport
                    and not started_transport.cancelled
                )
                started_transport.posting = may_post
            try:
                if may_post:
                    result = self._post_retry_unknown(
                        payload,
                        self._observer_timeout_seconds,
                        self._is_exact_started_response,
                    )
                    turn.record_started_result(result)
            finally:
                with self._lock:
                    started_transport.posting = False
                    if self._started_transports.get(turn_id) is started_transport:
                        self._started_transports.pop(turn_id, None)
                started_transport.completion.set()
        return None

    def handle_post_llm_call(self, **kwargs: object) -> None:
        turn_id = kwargs.get("turn_id")
        answer = kwargs.get("assistant_response")
        if not self._exact_nonblank(turn_id) or type(answer) is not str:
            return None
        with self._lock:
            now = self._now()
            self._expire_locked(now)
            turn = self._turns.get(turn_id)
            if turn is None or turn.state is TurnState.TERMINAL:
                return None
            self._answers[turn_id] = _AnswerEntry(answer, now + self._ANSWER_TTL_SECONDS)
            self._answers.move_to_end(turn_id)
            self._trim_locked(self._answers)
        return None

    def handle_on_session_end(self, **kwargs: object) -> None:
        turn_id = kwargs.get("turn_id")
        if not self._exact_nonblank(turn_id):
            with self._lock:
                self._expire_locked(self._now())
            return None
        if not self._prepare_turn_cleanup(turn_id):
            return None
        completed = kwargs.get("completed")
        failed = kwargs.get("failed")
        interrupted = kwargs.get("interrupted")
        flags_exact = all(type(value) is bool for value in (completed, failed, interrupted))
        outcome = completion_turn_outcome(kwargs) if flags_exact else None
        coordinator: TurnEventCoordinator | None = None
        turn: TurnBinding | None = None
        owner_token: object | None = None
        answer: str | None = None
        terminal_kind: str | None = None
        with self._lock:
            now = self._now()
            self._expire_locked(now)
            turn = self._turns.get(turn_id)
            if turn is None or turn.state is TurnState.TERMINAL or turn_id in self._terminal_owners:
                self._interaction_cleanup_turns.discard(
                    self._patch_turn_digest(turn_id)
                )
                return None
            entry = self._answers.get(turn_id)
            if entry is not None:
                answer = entry.answer
            if outcome is not None:
                terminal_kind = "failed"
            elif (
                flags_exact
                and completed is True
                and failed is False
                and interrupted is False
                and answer is not None
            ):
                terminal_kind = "completed"
            else:
                cleanup_coordinator = self._cleanup_turn_locked(
                    turn_id, keep_disposition=False
                )
                turn.finish()
                coordinator = cleanup_coordinator
                terminal_kind = None
            if terminal_kind is not None:
                owner_token = object()
                self._terminal_owners[turn_id] = owner_token
                self._terminal_owners.move_to_end(turn_id)
                coordinator = self._coordinators.get(turn_id)
        if terminal_kind is None:
            if coordinator is not None:
                coordinator.close()
            return None
        if coordinator is not None and turn.state is TurnState.CARD_ACTIVE:
            coordinator.close_terminal_barrier()
            coordinator.drain_before_terminal(self._observer_timeout_seconds)
            sequence = coordinator.next_terminal_sequence()
        elif coordinator is not None:
            coordinator.close_terminal_barrier()
            sequence = coordinator.next_terminal_sequence()
        else:
            sequence = 1
        created_at = self._now()
        payload = self._base_payload(turn, sequence=sequence, created_at=created_at)
        payload.update(
            event=f"message.{terminal_kind}",
            event_id=f"turn:{turn_id}:{terminal_kind}",
            phase="terminal",
            data=(
                {"answer": answer}
                if terminal_kind == "completed"
                else {
                    "error": (
                        "本轮已结束，但 Hermes 未报告执行完成。"
                        if outcome == "incomplete" else "消息处理失败"
                    ),
                    "turn_exit_reason": self._classify_exit_reason(
                        kwargs.get("turn_exit_reason"), interrupted is True
                    ),
                }
            ),
        )
        response = self._post_retry_unknown(
            payload,
            self._terminal_timeout_seconds,
            lambda value: self._valid_terminal_response(value, now=self._now())
            is not None,
        )
        with self._lock:
            now = self._now()
            valid_response = self._valid_terminal_response(response, now=now)
            still_owner = self._terminal_owners.get(turn_id) is owner_token
            if still_owner:
                self._terminal_owners.pop(turn_id, None)
            if still_owner:
                payload_copy = deepcopy(payload)
                response_copy = deepcopy(valid_response)
                record = _TerminalRecord(
                    payload_copy, response_copy, now + self._STATE_TTL_SECONDS
                )
                self._terminal_records[turn_id] = record
                self._terminal_records.move_to_end(turn_id)
                if (
                    response_copy is not None
                    and payload_copy.get("event") == "message.completed"
                    and response_copy == {"ok": True, "applied": True}
                ):
                    self._dispositions[turn_id] = deepcopy(response_copy)
                    self._dispositions.move_to_end(turn_id)
                self._cleanup_turn_locked(turn_id, keep_disposition=True)
                self._trim_locked(self._terminal_records)
                self._trim_locked(self._dispositions)
        turn.finish()
        if coordinator is not None:
            coordinator.close()
        return None

    def handle_on_session_reset(self, **kwargs: object) -> None:
        self._cleanup_session(kwargs.get("old_session_id"))
        return None

    def handle_on_session_finalize(self, **kwargs: object) -> None:
        self._cleanup_session(kwargs.get("session_id"))
        return None

    def handle_pre_tool_call(self, **kwargs: object) -> None:
        self._submit_tool(kwargs, pending=True)
        return None

    def handle_post_tool_call(self, **kwargs: object) -> None:
        self._submit_tool(kwargs, pending=False)
        return None

    def handle_pre_approval_request(self, **kwargs: object) -> None:
        values = self._approval_values(kwargs)
        if values is None:
            return None
        session_key, turn_id, tool_call_id, surface, fingerprint = values
        interaction_id = f"approval:{turn_id}:{tool_call_id}:{fingerprint[:16]}"
        key = (session_key, turn_id, tool_call_id, surface, fingerprint)
        with self._lock:
            now = self._now()
            self._expire_locked(now)
            if key in self._pending_approvals:
                return None
            coordinator = self._card_active_coordinator_locked(turn_id)
            if coordinator is None:
                return None
            patch_key = self._patch_interaction_key_locked(
                "approval",
                session_key,
                turn_id,
                interaction_id,
                fingerprint,
            )
            patch_state = (
                None if patch_key is None else self._patch_interactions.get(patch_key)
            )
            hfc_owned = (
                type(patch_state) is _PatchInteraction
                and patch_state.hfc_owned is True
            )
            pending = PendingApproval(
                session_key=session_key,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                command_fingerprint=fingerprint,
                surface=surface,
                interaction_id=interaction_id,
                expires_at=now + self._STATE_TTL_SECONDS,
                hfc_owned=hfc_owned,
            )
            self._pending_approvals[key] = pending
            self._pending_approvals.move_to_end(key)
            self._trim_pending_approvals_locked()
        if hfc_owned:
            return None
        payload = self._observer_payload(
            turn_id,
            event="interaction.requested",
            event_id=(
                f"approval:{turn_id}:{tool_call_id}:{fingerprint}:requested"
            ),
            phase="started",
            data={
                "interaction_id": interaction_id,
                "kind": "approval",
                "prompt": "需要授权后继续执行",
                "allow_custom_input": False,
                "options": [
                    {"label": "允许一次", "value": "once", "style": "primary"},
                    {"label": "本会话允许", "value": "session"},
                    {"label": "始终允许", "value": "always"},
                    {"label": "拒绝", "value": "deny", "style": "danger"},
                ],
            },
        )
        if payload is None or not coordinator.submit_observer(payload, producer="plugin"):
            with self._lock:
                self._pending_approvals.pop(key, None)
        return None

    def handle_post_approval_response(self, **kwargs: object) -> None:
        values = self._approval_values(kwargs)
        if values is None:
            return None
        choice = kwargs.get("choice")
        if type(choice) is not str:
            return None
        choice = choice.strip().lower()
        if choice not in {"once", "session", "always", "deny", "timeout"}:
            return None
        session_key, turn_id, tool_call_id, surface, fingerprint = values
        key = (session_key, turn_id, tool_call_id, surface, fingerprint)
        with self._lock:
            self._expire_locked(self._now())
            pending = self._pending_approvals.pop(key, None)
            self._claimed_approvals.discard(key)
            coordinator = self._card_active_coordinator_locked(turn_id)
        if pending is None or coordinator is None:
            return None
        if pending.hfc_owned:
            return None
        event = "interaction.completed" if choice != "timeout" else "interaction.failed"
        data: dict[str, object] = {"interaction_id": pending.interaction_id}
        if choice == "timeout":
            data["error"] = "交互已过期"
        else:
            data["choice"] = choice
        payload = self._observer_payload(
            turn_id,
            event=event,
            event_id=f"approval:{turn_id}:{tool_call_id}:{fingerprint}:terminal",
            phase="terminal",
            data=data,
        )
        if payload is not None:
            coordinator.submit_observer(payload, producer="plugin")
        return None

    def take_pending_approval(
        self,
        session_key: object,
        turn_id: object,
        tool_call_id: object,
        command: object,
        surface: object,
    ) -> PendingApproval | None:
        values = self._approval_values(
            {
                "session_key": session_key,
                "turn_id": turn_id,
                "tool_call_id": tool_call_id,
                "command": command,
                "surface": surface,
            }
        )
        if values is None:
            return None
        key = values
        with self._lock:
            self._expire_locked(self._now())
            pending = self._pending_approvals.get(key)
            if pending is None or key in self._claimed_approvals:
                return None
            self._claimed_approvals.add(key)
            return pending

    def handle_subagent_start(self, **kwargs: object) -> None:
        turn_id = kwargs.get("parent_turn_id")
        child_session_id = kwargs.get("child_session_id")
        child_id = kwargs.get("child_subagent_id")
        if not self._exact_nonblank(child_id):
            child_id = child_session_id
        if not all(self._exact_nonblank(value) for value in (turn_id, child_session_id, child_id)):
            return None
        with self._lock:
            self._expire_locked(self._now())
            coordinator = self._card_active_coordinator_locked(turn_id)
            if coordinator is None:
                return None
            key = (turn_id, child_session_id)
            self._subagents[key] = (child_id, self._now() + self._STATE_TTL_SECONDS)
            self._subagents.move_to_end(key)
            self._trim_locked(self._subagents)
        data: dict[str, object] = {"child_id": child_id, "status": "queued"}
        role = self._preview(kwargs.get("child_role"))
        goal = self._preview(kwargs.get("child_goal"))
        if role:
            data["role"] = role
        if goal:
            data["goal_preview"] = goal
        self._submit_subagent(coordinator, turn_id, child_id, "started", data)
        return None

    def handle_subagent_stop(self, **kwargs: object) -> None:
        turn_id = kwargs.get("parent_turn_id")
        child_session_id = kwargs.get("child_session_id")
        if not all(self._exact_nonblank(value) for value in (turn_id, child_session_id)):
            return None
        with self._lock:
            self._expire_locked(self._now())
            coordinator = self._card_active_coordinator_locked(turn_id)
            identity = self._subagents.pop((turn_id, child_session_id), None)
        if coordinator is None or identity is None:
            return None
        child_id = identity[0]
        status = kwargs.get("child_status")
        safe_status = self._subagent_status(status)
        data: dict[str, object] = {"child_id": child_id, "status": safe_status}
        role = self._preview(kwargs.get("child_role"))
        summary = self._preview(kwargs.get("child_summary"))
        duration = self._safe_duration(kwargs.get("duration_ms"))
        if role:
            data["role"] = role
        if summary:
            data["summary_preview"] = summary
        if duration is not None:
            data["duration_ms"] = duration
        self._submit_subagent(coordinator, turn_id, child_id, "terminal", data)
        return None

    def take_terminal_disposition(self, turn_id: object) -> dict[str, object] | None:
        if not self._exact_nonblank(turn_id):
            return None
        with self._lock:
            self._expire_locked(self._now())
            record = self._terminal_records.get(turn_id)
            disposition = self._dispositions.get(turn_id)
            if (
                record is None
                or record.payload.get("event") != "message.completed"
                or disposition != {"ok": True, "applied": True}
            ):
                return None
            self._terminal_records.pop(turn_id, None)
            self._dispositions.pop(turn_id, None)
            return deepcopy(disposition)

    def take_terminal_record(self, turn_id: object) -> dict[str, object] | None:
        if not self._exact_nonblank(turn_id):
            return None
        with self._lock:
            self._expire_locked(self._now())
            record = self._terminal_records.pop(turn_id, None)
            self._dispositions.pop(turn_id, None)
            if record is None:
                return None
            return {
                "payload": deepcopy(record.payload),
                "response": deepcopy(record.response),
            }

    def drain_observers(self, timeout_seconds: float) -> None:
        with self._lock:
            coordinators = tuple(self._coordinators.values())
        for coordinator in coordinators:
            coordinator.drain_before_terminal(timeout_seconds)
        return None

    def runtime_activity_snapshot(self) -> tuple[int, bool]:
        """Return bounded counts only; runtime-control never receives identities."""
        with self._lock:
            self._expire_locked(self._now())
            return min(len(self._turns), self._MAX_ENTRIES), True

    def close(self) -> None:
        with self._close_lock:
            if self._close_started:
                wait_for_close = True
            else:
                self._close_started = True
                wait_for_close = False
        if wait_for_close:
            completed = self._close_complete.wait(timeout=2.0)
            with self._close_lock:
                close_failed = self._close_failed
            if not completed or close_failed:
                raise RuntimeError(
                    "runtime interaction listener close failed"
                ) from None
            return None
        with self._lock:
            self._closed = True
            listener = self._runtime_interaction_listener
        if listener is not None:
            try:
                listener.close()
            except Exception:
                try:
                    listener.mark_close_failed()
                except Exception:
                    pass
                with self._close_lock:
                    self._close_failed = True
                self._close_complete.set()
                raise RuntimeError(
                    "runtime interaction listener close failed"
                ) from None
            if listener.snapshot()["poisoned"] is True:
                self._close_complete.set()
                return None
        with self._lock:
            coordinators = tuple(self._coordinators.values())
            started = tuple(self._started_transports.values())
            for transport in started:
                transport.cancelled = True
            self._turns.clear()
            self._coordinators.clear()
            self._answers.clear()
            self._terminal_records.clear()
            self._dispositions.clear()
            self._pending_approvals.clear()
            self._claimed_approvals.clear()
            self._subagents.clear()
            self._terminal_owners.clear()
            self._started_transports.clear()
            self._patch_interactions.clear()
            self._interaction_cleanup_turns.clear()
            self._deferred_interaction_cleanup_turns.clear()
            self._registry.clear()
        self._wait_started_transports(list(started))
        for coordinator in coordinators:
            coordinator.close()
        self._close_complete.set()
        return None

    def _deliver_observer(self, event: ObserverEvent) -> None:
        try:
            self._post(event.payload, self._observer_timeout_seconds)
        except Exception:
            pass

    def _submit_tool(self, kwargs: dict[str, object], *, pending: bool) -> None:
        turn_id = kwargs.get("turn_id")
        tool_call_id = kwargs.get("tool_call_id")
        tool_name = kwargs.get("tool_name")
        if not all(self._exact_nonblank(value) for value in (turn_id, tool_call_id, tool_name)):
            return
        with self._lock:
            self._expire_locked(self._now())
            coordinator = self._card_active_coordinator_locked(turn_id)
        if coordinator is None:
            return
        status = "pending" if pending else self._tool_status(kwargs.get("status"))
        data: dict[str, object] = {
            "tool_id": tool_call_id,
            "name": self._preview(tool_name),
            "status": status,
        }
        duration = self._safe_duration(kwargs.get("duration_ms"))
        if not pending and duration is not None:
            data["duration_ms"] = duration
        phase = "started" if pending else "terminal"
        payload = self._observer_payload(
            turn_id,
            event="tool.updated",
            event_id=coordinator.event_id("tool", item_id=tool_call_id, phase=phase),
            phase=phase,
            data=data,
        )
        if payload is not None:
            coordinator.submit_observer(payload, producer="plugin")

    def _submit_subagent(
        self,
        coordinator: TurnEventCoordinator,
        turn_id: str,
        child_id: str,
        phase: str,
        data: dict[str, object],
    ) -> None:
        payload = self._observer_payload(
            turn_id,
            event="subagent.updated",
            event_id=coordinator.event_id("subagent", item_id=child_id, phase=phase),
            phase=phase,
            data=data,
        )
        if payload is not None:
            coordinator.submit_observer(payload, producer="plugin")

    def _observer_payload(
        self,
        turn_id: str,
        *,
        event: str,
        event_id: str,
        phase: str,
        data: dict[str, object],
    ) -> dict[str, object] | None:
        with self._lock:
            turn = self._turns.get(turn_id)
        if turn is None or not turn.accepts_observer_events:
            return None
        payload = self._base_payload(turn, sequence=0, created_at=self._now())
        payload.update(event=event, event_id=event_id, phase=phase, data=data)
        return payload

    def _approval_values(
        self, kwargs: dict[str, object]
    ) -> tuple[str, str, str, str, str] | None:
        session_key = kwargs.get("session_key")
        turn_id = kwargs.get("turn_id")
        tool_call_id = kwargs.get("tool_call_id")
        command = kwargs.get("command")
        surface = kwargs.get("surface")
        if not all(
            self._exact_nonblank(value)
            for value in (session_key, turn_id, tool_call_id, command, surface)
        ):
            return None
        if surface != "gateway":
            return None
        normalized = " ".join(command.split())
        fingerprint = sha256(normalized.encode("utf-8")).hexdigest()
        with self._lock:
            turn = self._turns.get(turn_id)
            valid_turn = bool(
                turn is not None
                and turn.accepts_observer_events
                and turn.ingress.gateway_session_key == session_key
            )
        if not valid_turn:
            return None
        return session_key, turn_id, tool_call_id, surface, fingerprint

    def _card_active_coordinator_locked(
        self, turn_id: str
    ) -> TurnEventCoordinator | None:
        turn = self._turns.get(turn_id)
        if (
            turn is None
            or not turn.accepts_observer_events
            or turn_id in self._terminal_owners
        ):
            return None
        coordinator = self._coordinators.get(turn_id)
        if coordinator is None or coordinator.turn_id != turn_id:
            return None
        return coordinator

    def _patch_interaction_key_locked(
        self,
        kind: object,
        session_identity: object,
        turn_id: object,
        interaction_id: object,
        fingerprint: object,
    ) -> str | None:
        if (
            type(kind) is not str
            or kind not in self._PATCH_INTERACTION_KINDS
            or not self._exact_nonblank(session_identity)
            or not self._exact_nonblank(turn_id)
            or type(interaction_id) is not str
            or self._PATCH_INTERACTION_ID_RE.fullmatch(interaction_id) is None
            or type(fingerprint) is not str
            or self._HANDOFF_ID_RE.fullmatch(fingerprint) is None
        ):
            return None
        coordinator = self._card_active_coordinator_locked(turn_id)
        turn = self._turns.get(turn_id)
        if (
            coordinator is None
            or turn is None
            or turn.ingress.gateway_session_key != session_identity
        ):
            return None
        digest = sha256(b"hfc-patch-interaction-v1\0")
        for value in (
            session_identity,
            kind,
            turn_id,
            interaction_id,
            fingerprint,
        ):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        return digest.hexdigest()

    @staticmethod
    def _patch_turn_digest(turn_id: str) -> str:
        encoded = turn_id.encode("utf-8")
        digest = sha256(b"hfc-patch-interaction-turn-v1\0")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        return digest.hexdigest()

    @classmethod
    def _valid_patch_selected_value(cls, value: object) -> bool:
        if (
            type(value) is not str
            or not value
            or len(value) > cls._MAX_PATCH_SELECTED_BYTES
            or not value.strip()
        ):
            return False
        try:
            return len(value.encode("utf-8")) <= cls._MAX_PATCH_SELECTED_BYTES
        except (UnicodeEncodeError, ValueError):
            return False

    @staticmethod
    def _preview(value: object) -> str:
        if type(value) is not str:
            return ""
        return " ".join(value.split())[:240]

    @staticmethod
    def _safe_duration(value: object) -> int | float | None:
        if type(value) not in (int, float):
            return None
        try:
            if value < 0 or not isfinite(value):
                return None
        except (OverflowError, TypeError, ValueError):
            return None
        return value

    @staticmethod
    def _tool_status(value: object) -> str:
        if type(value) is not str:
            return "failed"
        return {
            "ok": "completed",
            "error": "failed",
            "blocked": "blocked",
            "timeout": "timeout",
            "cancelled": "cancelled",
            "canceled": "cancelled",
        }.get(value.strip().lower(), "failed")

    @staticmethod
    def _subagent_status(value: object) -> str:
        if type(value) is not str:
            return "failed"
        return {
            "queued": "queued",
            "running": "running",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "interrupted": "interrupted",
            "timeout": "failed",
        }.get(value.strip().lower(), "failed")

    def _post_retry_unknown(
        self,
        payload: dict[str, object],
        timeout: float,
        is_explicit: Callable[[object], bool],
    ) -> object:
        result: object = None
        for _attempt in range(2):
            try:
                result = self._post(payload, timeout)
            except Exception:
                result = None
            if is_explicit(result):
                return result
        return result

    @classmethod
    def _is_exact_started_response(cls, value: object) -> bool:
        if type(value) is not dict or not all(type(key) is str for key in value):
            return False
        keys = set(value)
        if keys == {"ok", "applied"}:
            return value.get("ok") is True and value.get("applied") is True
        if keys == {"ok", "applied", "delivery"}:
            delivery = value["delivery"]
            return (
                value.get("ok") is True
                and value.get("applied") is True
                and type(delivery) is dict
                and all(type(key) is str for key in delivery)
                and set(delivery) == {"outcome"}
                and type(delivery["outcome"]) is str
                and delivery["outcome"] == "delivered"
            )
        if keys == {"ok", "applied", "disposition"}:
            return (
                value.get("ok") is True
                and value.get("applied") is False
                and type(value.get("disposition")) is str
                and value.get("disposition") == "native"
            )
        return False

    @classmethod
    def _valid_terminal_response(
        cls,
        value: object,
        *,
        now: float,
    ) -> dict[str, object] | None:
        if type(value) is not dict or not all(type(key) is str for key in value):
            return None
        keys = set(value)
        if keys == {"ok", "applied"}:
            if value.get("ok") is not True or value.get("applied") is not True:
                return None
            return deepcopy(value)
        if keys not in (
            {"ok", "applied", "disposition"},
            {"ok", "applied", "disposition", "native_handoff"},
        ):
            return None
        if (
            value.get("ok") is not True
            or value.get("applied") is not False
            or type(value.get("disposition")) is not str
            or value.get("disposition") != "native"
        ):
            return None
        if "native_handoff" in value:
            descriptor = value["native_handoff"]
            if not cls._valid_native_handoff_descriptor(descriptor, now=now):
                return None
        return deepcopy(value)

    @classmethod
    def _valid_native_handoff_descriptor(cls, value: object, *, now: float) -> bool:
        if type(value) is not dict or not all(type(key) is str for key in value):
            return False
        if set(value) != {"protocol", "id", "uuid_seed", "expires_at"}:
            return False
        protocol = value["protocol"]
        handoff_id = value["id"]
        uuid_seed = value["uuid_seed"]
        expires_at = value["expires_at"]
        if type(protocol) is not str or protocol != cls._NATIVE_HANDOFF_PROTOCOL:
            return False
        if type(handoff_id) is not str or cls._HANDOFF_ID_RE.fullmatch(handoff_id) is None:
            return False
        if type(uuid_seed) is not str or cls._UUID_SEED_RE.fullmatch(uuid_seed) is None:
            return False
        if type(expires_at) not in (int, float):
            return False
        try:
            return (
                isfinite(expires_at)
                and now < expires_at <= now + cls._NATIVE_HANDOFF_MAX_FUTURE_SECONDS
            )
        except (OverflowError, TypeError, ValueError):
            return False

    def _base_payload(
        self, turn: TurnBinding, *, sequence: int, created_at: float
    ) -> dict[str, object]:
        ingress = turn.ingress
        return {
            "schema_version": "1",
            "event": "",
            "conversation_id": ingress.thread_id or ingress.chat_id,
            "message_id": ingress.incoming_message_id,
            "chat_id": ingress.chat_id,
            "thread_id": ingress.thread_id,
            "platform": "feishu",
            "turn_id": turn.turn_id,
            "sequence": sequence,
            "created_at": created_at,
            "event_id": "",
            "producer": "plugin",
            "phase": "",
            "data": {},
        }

    def _cleanup_session(self, session_id: object) -> None:
        if not self._exact_nonblank(session_id):
            with self._lock:
                self._expire_locked(self._now())
            return
        with self._lock:
            self._expire_locked(self._now())
            turn_ids = [
                turn_id
                for turn_id, turn in self._turns.items()
                if turn.ingress.session_id == session_id
            ]
            turn_digests = {self._patch_turn_digest(value) for value in turn_ids}
            self._interaction_cleanup_turns.update(turn_digests)
            resolution_events = self._resolution_events_for_turns_locked(
                turn_digests
            )
        if not self._wait_interaction_resolutions(resolution_events):
            with self._lock:
                self._registry.remove_session(session_id)
                self._deferred_interaction_cleanup_turns.update(turn_digests)
            for turn_digest in turn_digests:
                self._complete_deferred_interaction_cleanup(turn_digest)
            return
        with self._lock:
            self._expire_locked(self._now())
            self._registry.remove_session(session_id)
            turn_ids = [
                turn_id
                for turn_id, turn in self._turns.items()
                if turn.ingress.session_id == session_id
            ]
            coordinators = [self._coordinators.get(turn_id) for turn_id in turn_ids]
            started: list[_StartedTransport] = []
            for turn_id in turn_ids:
                turn = self._turns.get(turn_id)
                self._cleanup_turn_locked(
                    turn_id, keep_disposition=False, started_waits=started
                )
                if turn is not None:
                    turn.finish()
        self._wait_started_transports(started)
        for coordinator in coordinators:
            if coordinator is not None:
                coordinator.close()

    def _cleanup_turn_locked(
        self,
        turn_id: str,
        *,
        keep_disposition: bool,
        started_waits: list[_StartedTransport] | None = None,
    ) -> TurnEventCoordinator | None:
        self._turns.pop(turn_id, None)
        coordinator = self._coordinators.pop(turn_id, None)
        started = self._started_transports.pop(turn_id, None)
        if started is not None:
            started.cancelled = True
            if started_waits is not None:
                started_waits.append(started)
        self._answers.pop(turn_id, None)
        self._terminal_owners.pop(turn_id, None)
        for key in tuple(self._pending_approvals):
            if key[1] == turn_id:
                del self._pending_approvals[key]
                self._claimed_approvals.discard(key)
        for key in tuple(self._subagents):
            if key[0] == turn_id:
                del self._subagents[key]
        turn_digest = self._patch_turn_digest(turn_id)
        for key, state in tuple(self._patch_interactions.items()):
            if state.turn_digest == turn_digest:
                del self._patch_interactions[key]
        self._interaction_cleanup_turns.discard(turn_digest)
        self._deferred_interaction_cleanup_turns.discard(turn_digest)
        if not keep_disposition:
            self._terminal_records.pop(turn_id, None)
            self._dispositions.pop(turn_id, None)
        return coordinator

    def _expire_locked(self, now: float) -> None:
        for turn_id, answer in tuple(self._answers.items()):
            if answer.expires_at <= now:
                del self._answers[turn_id]
        for turn_id, record in tuple(self._terminal_records.items()):
            if record.expires_at <= now:
                del self._terminal_records[turn_id]
                self._dispositions.pop(turn_id, None)
        for key, pending in tuple(self._pending_approvals.items()):
            if pending.expires_at <= now:
                del self._pending_approvals[key]
                self._claimed_approvals.discard(key)
        for key, (_child_id, expires_at) in tuple(self._subagents.items()):
            if expires_at <= now:
                del self._subagents[key]
        for key, state in tuple(self._patch_interactions.items()):
            if (
                state.expires_at <= now
                and not (
                    type(state) is _PatchInteraction
                    and state.resolution_complete is not None
                )
            ):
                del self._patch_interactions[key]

    def _prepare_turn_cleanup(self, turn_id: str) -> bool:
        turn_digest = self._patch_turn_digest(turn_id)
        with self._lock:
            self._interaction_cleanup_turns.add(turn_digest)
            events = self._resolution_events_for_turns_locked({turn_digest})
        if self._wait_interaction_resolutions(events):
            return True
        with self._lock:
            self._deferred_interaction_cleanup_turns.add(turn_digest)
        self._complete_deferred_interaction_cleanup(turn_digest)
        return False

    def _complete_deferred_interaction_cleanup(self, turn_digest: str) -> None:
        coordinator: TurnEventCoordinator | None = None
        started: list[_StartedTransport] = []
        with self._lock:
            if turn_digest not in self._deferred_interaction_cleanup_turns:
                return None
            if self._resolution_events_for_turns_locked({turn_digest}):
                return None
            turn_id = next(
                (
                    value
                    for value in self._turns
                    if self._patch_turn_digest(value) == turn_digest
                ),
                None,
            )
            self._deferred_interaction_cleanup_turns.discard(turn_digest)
            if turn_id is None:
                self._interaction_cleanup_turns.discard(turn_digest)
                return None
            turn = self._turns.get(turn_id)
            if turn is not None:
                self._registry.remove_session(turn.ingress.session_id)
            coordinator = self._cleanup_turn_locked(
                turn_id, keep_disposition=False, started_waits=started
            )
            if turn is not None:
                turn.finish()
        self._wait_started_transports(started)
        if coordinator is not None:
            coordinator.close()
        return None

    def _resolution_events_for_turns_locked(
        self, turn_digests: set[str]
    ) -> list[threading.Event]:
        return [
            state.resolution_complete
            for state in self._patch_interactions.values()
            if (
                type(state) is _PatchInteraction
                and state.turn_digest in turn_digests
                and state.resolution_complete is not None
            )
        ]

    @staticmethod
    def _wait_interaction_resolutions(
        events: list[threading.Event], timeout_seconds: float = 1.25
    ) -> bool:
        deadline = monotonic() + timeout_seconds
        for event in events:
            remaining = deadline - monotonic()
            if remaining <= 0 or not event.wait(timeout=remaining):
                return False
        return True

    @classmethod
    def _trim_locked(cls, mapping: OrderedDict) -> None:
        while len(mapping) > cls._MAX_ENTRIES:
            mapping.popitem(last=False)

    def _trim_pending_approvals_locked(self) -> None:
        while len(self._pending_approvals) > self._MAX_ENTRIES:
            key, _pending = self._pending_approvals.popitem(last=False)
            self._claimed_approvals.discard(key)

    def _make_turn_room_locked(
        self,
    ) -> tuple[bool, list[TurnEventCoordinator], list[_StartedTransport]]:
        evicted: list[TurnEventCoordinator] = []
        started: list[_StartedTransport] = []
        while len(self._turns) >= self._MAX_ENTRIES:
            victim = next(
                (
                    turn_id
                    for turn_id in self._turns
                    if (
                        turn_id not in self._terminal_owners
                        and self._patch_turn_digest(turn_id)
                        not in self._interaction_cleanup_turns
                        and not any(
                            type(state) is _PatchInteraction
                            and state.turn_digest == self._patch_turn_digest(turn_id)
                            and state.resolution_complete is not None
                            for state in self._patch_interactions.values()
                        )
                    )
                ),
                None,
            )
            if victim is None:
                return False, evicted, started
            coordinator = self._cleanup_turn_locked(
                victim, keep_disposition=False, started_waits=started
            )
            if coordinator is not None:
                evicted.append(coordinator)
        return True, evicted, started

    @staticmethod
    def _wait_started_transports(transports: list[_StartedTransport]) -> None:
        for transport in transports:
            with transport.gate:
                pass

    @staticmethod
    def _exact_nonblank(value: object) -> bool:
        return type(value) is str and bool(value.strip())

    @staticmethod
    def _classify_exit_reason(value: object, interrupted: bool) -> str:
        if interrupted:
            return "interrupted"
        if type(value) is not str:
            return "failed"
        text = value.strip().lower()
        if "timeout" in text or "timed_out" in text:
            return "timeout"
        if "budget" in text or "max_iterations" in text:
            return "budget_exhausted"
        if "error" in text or "failed" in text or "retries_exhausted" in text:
            return "runtime_error"
        return "failed"


_ACTIVE_RUNTIME: PluginRuntime | None = None
_ACTIVE_RUNTIME_LOCK = RLock()
_ingress_registry = IngressBindingRegistry()


def configure_plugin_runtime(runtime: PluginRuntime | None) -> None:
    old_runtime = _swap_active_runtime(runtime)
    if old_runtime is not None and old_runtime is not runtime:
        old_runtime.close()
    return None


def _swap_active_runtime(runtime: PluginRuntime | None) -> PluginRuntime | None:
    global _ACTIVE_RUNTIME
    with _ACTIVE_RUNTIME_LOCK:
        old_runtime = _ACTIVE_RUNTIME
        _ACTIVE_RUNTIME = runtime
    return old_runtime


def active_plugin_runtime() -> PluginRuntime | None:
    """Return only the process runtime protected by its authoritative lock."""
    with _ACTIVE_RUNTIME_LOCK:
        return _ACTIVE_RUNTIME


def reset_plugin_runtime_state() -> None:
    configure_plugin_runtime(None)
    _ingress_registry.clear()
    return None


def _no_op(**kwargs: Any) -> None:
    return None


def _dispatch(method_name: str, **kwargs: Any) -> None:
    with _ACTIVE_RUNTIME_LOCK:
        runtime = _ACTIVE_RUNTIME
    if runtime is not None:
        getattr(runtime, method_name)(**kwargs)
    return None


def handle_pre_llm_call(**kwargs: Any) -> None:
    return _dispatch("handle_pre_llm_call", **kwargs)


def handle_post_llm_call(**kwargs: Any) -> None:
    return _dispatch("handle_post_llm_call", **kwargs)


def handle_on_session_end(**kwargs: Any) -> None:
    return _dispatch("handle_on_session_end", **kwargs)


def handle_on_session_reset(**kwargs: Any) -> None:
    return _dispatch("handle_on_session_reset", **kwargs)


def handle_on_session_finalize(**kwargs: Any) -> None:
    return _dispatch("handle_on_session_finalize", **kwargs)


def handle_pre_tool_call(**kwargs: Any) -> None:
    return _dispatch("handle_pre_tool_call", **kwargs)


def handle_post_tool_call(**kwargs: Any) -> None:
    return _dispatch("handle_post_tool_call", **kwargs)


def handle_pre_approval_request(**kwargs: Any) -> None:
    return _dispatch("handle_pre_approval_request", **kwargs)


def handle_post_approval_response(**kwargs: Any) -> None:
    return _dispatch("handle_post_approval_response", **kwargs)


def handle_subagent_start(**kwargs: Any) -> None:
    return _dispatch("handle_subagent_start", **kwargs)


def handle_subagent_stop(**kwargs: Any) -> None:
    return _dispatch("handle_subagent_stop", **kwargs)


HOOK_HANDLERS = {
    "pre_llm_call": "handle_pre_llm_call",
    "post_llm_call": "handle_post_llm_call",
    "on_session_end": "handle_on_session_end",
    "on_session_reset": "handle_on_session_reset",
    "on_session_finalize": "handle_on_session_finalize",
    "pre_tool_call": "handle_pre_tool_call",
    "post_tool_call": "handle_post_tool_call",
    "pre_approval_request": "handle_pre_approval_request",
    "post_approval_response": "handle_post_approval_response",
    "subagent_start": "handle_subagent_start",
    "subagent_stop": "handle_subagent_stop",
}


def _callback(handler_name: str) -> Callable[..., None]:
    def invoke(**kwargs: Any) -> None:
        try:
            globals()[handler_name](**kwargs)
        except Exception:
            return None
        return None

    return invoke


def register_callbacks(ctx: Any) -> None:
    for name, handler_name in HOOK_HANDLERS.items():
        try:
            ctx.register_hook(name, _callback(handler_name))
        except Exception:
            continue
    return None


@dataclass
class _ContextGate:
    context: object
    registration_complete: bool = False
    registered_hooks: set[str] = field(default_factory=set)
    _runtime: PluginRuntime | None = None
    _lock: RLock = field(default_factory=RLock)

    def invoke(self, handler_name: str, kwargs: dict[str, Any]) -> None:
        try:
            with _ACTIVE_RUNTIME_LOCK:
                with self._lock:
                    expected_runtime = self._runtime
                if expected_runtime is None or _ACTIVE_RUNTIME is not expected_runtime:
                    return None
            getattr(expected_runtime, handler_name)(**kwargs)
        except Exception:
            return None
        return None

    def set_runtime(self, runtime: PluginRuntime | None) -> None:
        with self._lock:
            self._runtime = runtime
        return None

    def clear_runtime(self, expected_runtime: PluginRuntime) -> None:
        with self._lock:
            if self._runtime is expected_runtime:
                self._runtime = None
        return None


@dataclass
class _ProductionBootstrap:
    context: object
    gate: _ContextGate
    runtime: PluginRuntime
    lease: RuntimeControlLease
    config: ProductionRuntimeConfig
    _closed: bool = False

    def close(self) -> bool:
        global _ACTIVE_RUNTIME
        if self._closed:
            return True
        try:
            self.runtime.close()
        except Exception:
            return False
        if self.runtime.runtime_interaction_listener_snapshot()["poisoned"] is True:
            return False
        self._closed = True
        with _ACTIVE_RUNTIME_LOCK:
            if _ACTIVE_RUNTIME is self.runtime:
                _ACTIVE_RUNTIME = None
            self.gate.clear_runtime(self.runtime)
        try:
            self.lease.close()
        except Exception:
            pass
        return True


_BOOTSTRAP_LOCK = RLock()
_PRODUCTION_BOOTSTRAP: _ProductionBootstrap | None = None
_ATEXIT_REGISTERED = False
_CONTEXT_GATES: list[_ContextGate] = []


def bootstrap_plugin_runtime(ctx: Any) -> None:
    """Install one production runtime before activating official callbacks."""
    global _ACTIVE_RUNTIME, _PRODUCTION_BOOTSTRAP, _ATEXIT_REGISTERED
    paused_current: _ProductionBootstrap | None = None
    candidate_runtime: PluginRuntime | None = None
    candidate_lease: RuntimeControlLease | None = None
    try:
        with _BOOTSTRAP_LOCK:
            current = _PRODUCTION_BOOTSTRAP
            config = _load_production_runtime_config()
            if not config.enabled:
                gate = _ensure_context_gate(ctx)
                if current is not None:
                    if not current.close():
                        return None
                gate.set_runtime(None)
                _PRODUCTION_BOOTSTRAP = None
                return None
            secret = read_transport_root_secret()
            if type(secret) is not bytes or len(secret) != 32:
                gate = _find_context_gate(ctx)
                if (
                    current is not None
                    and current.context is ctx
                    and gate is current.gate
                ):
                    return None
                _ensure_context_gate(ctx).set_runtime(None)
                return None
            gate = _find_context_gate(ctx)
            if (
                current is not None
                and current.context is ctx
                and current.config == config
                and gate is current.gate
            ):
                return None
            if current is not None:
                if not current.runtime.pause_runtime_interaction_listener_for_replacement():
                    return None
                paused_current = current
            transport = SignedEventTransport(
                event_url=config.event_url,
                timeout_seconds=config.timeout_seconds,
                secret_reader=read_transport_root_secret,
            )
            runtime = PluginRuntime(
                post=transport,
                observer_timeout_seconds=config.timeout_seconds,
            )
            candidate_runtime = runtime
            if not runtime.start_runtime_interaction_listener(secret):
                runtime.close()
                candidate_runtime = None
                _ensure_context_gate(ctx)
                _resume_paused_bootstrap(paused_current)
                return None
            lease = acquire_runtime_control(
                event_url=config.event_url,
                package_version=__version__,
                active_work_snapshot_provider=_runtime_activity_provider(runtime),
            )
            candidate_lease = lease
            if lease is None:
                runtime.close()
                candidate_runtime = None
                _ensure_context_gate(ctx)
                _resume_paused_bootstrap(paused_current)
                return None
            try:
                if not _ATEXIT_REGISTERED:
                    atexit.register(_close_process_plugin_runtime)
                    _ATEXIT_REGISTERED = True
                gate = _ensure_context_gate(ctx)
                if not gate.registration_complete:
                    raise RuntimeError("official callback registration incomplete")
            except Exception:
                _ensure_context_gate(ctx)
                _close_runtime_and_lease(runtime, lease)
                candidate_runtime = None
                candidate_lease = None
                _resume_paused_bootstrap(paused_current)
                return None
            bootstrap = _ProductionBootstrap(ctx, gate, runtime, lease, config)
            if current is not None and not current.close():
                _close_runtime_and_lease(runtime, lease)
                candidate_runtime = None
                candidate_lease = None
                _resume_paused_bootstrap(paused_current)
                return None
            paused_current = None
            with _ACTIVE_RUNTIME_LOCK:
                _ACTIVE_RUNTIME = runtime
                gate.set_runtime(runtime)
            _PRODUCTION_BOOTSTRAP = bootstrap
            candidate_runtime = None
            candidate_lease = None
        return None
    except Exception:
        if candidate_runtime is not None:
            if candidate_lease is not None:
                _close_runtime_and_lease(candidate_runtime, candidate_lease)
            else:
                try:
                    candidate_runtime.close()
                except Exception:
                    pass
        _resume_paused_bootstrap(paused_current)
        try:
            _ensure_context_gate(ctx)
        except Exception:
            pass
        return None


def _resume_paused_bootstrap(
    bootstrap: _ProductionBootstrap | None,
) -> None:
    if bootstrap is None:
        return None
    try:
        bootstrap.runtime.resume_runtime_interaction_listener_after_failed_replacement()
    except Exception:
        pass
    return None


def _register_callbacks_checked(ctx: Any, runtime: PluginRuntime) -> bool:
    return _ensure_context_gate(ctx).registration_complete


def _runtime_callback(
    handler_name: str, expected_runtime: PluginRuntime
) -> Callable[..., None]:
    def invoke(**kwargs: Any) -> None:
        try:
            with _ACTIVE_RUNTIME_LOCK:
                if _ACTIVE_RUNTIME is not expected_runtime:
                    return None
            getattr(expected_runtime, handler_name)(**kwargs)
        except Exception:
            return None
        return None

    return invoke


def _find_context_gate(ctx: Any) -> _ContextGate | None:
    for gate in _CONTEXT_GATES:
        if gate.context is ctx:
            return gate
    return None


def _ensure_context_gate(ctx: Any) -> _ContextGate:
    gate = _find_context_gate(ctx)
    if gate is None:
        gate = _ContextGate(ctx)
        _CONTEXT_GATES.append(gate)
    for name, handler_name in HOOK_HANDLERS.items():
        if name in gate.registered_hooks:
            continue
        try:
            ctx.register_hook(name, _gate_callback(gate, handler_name))
            gate.registered_hooks.add(name)
        except Exception:
            continue
    gate.registration_complete = len(gate.registered_hooks) == len(HOOK_HANDLERS)
    return gate


def _gate_callback(
    gate: _ContextGate, handler_name: str
) -> Callable[..., None]:
    def invoke(**kwargs: Any) -> None:
        return gate.invoke(handler_name, kwargs)

    return invoke


def _inert_callback() -> Callable[..., None]:
    def invoke(**_kwargs: Any) -> None:
        return None

    return invoke


def _runtime_activity_snapshot() -> tuple[int, bool]:
    with _ACTIVE_RUNTIME_LOCK:
        runtime = _ACTIVE_RUNTIME
    if runtime is None:
        return 0, True
    try:
        return runtime.runtime_activity_snapshot()
    except Exception:
        return 0, False


def _runtime_activity_provider(
    runtime: PluginRuntime,
) -> Callable[[], tuple[int, bool]]:
    def snapshot() -> tuple[int, bool]:
        try:
            return runtime.runtime_activity_snapshot()
        except Exception:
            return 0, False

    return snapshot


def _close_runtime_and_lease(
    runtime: PluginRuntime, lease: RuntimeControlLease
) -> None:
    try:
        try:
            runtime.close()
        except Exception:
            pass
    finally:
        try:
            lease.close()
        except Exception:
            pass
    return None


def _close_process_plugin_runtime() -> None:
    global _PRODUCTION_BOOTSTRAP
    with _BOOTSTRAP_LOCK:
        bootstrap = _PRODUCTION_BOOTSTRAP
        if bootstrap is not None:
            if bootstrap.close():
                _PRODUCTION_BOOTSTRAP = None
    return None


def reset_production_plugin_runtime_for_tests() -> None:
    global _ATEXIT_REGISTERED
    _close_process_plugin_runtime()
    with _BOOTSTRAP_LOCK:
        _ATEXIT_REGISTERED = False
        _CONTEXT_GATES.clear()
    return None
