from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib import parse, request

from .operations_transport import read_transport_root_secret


RUNTIME_TIMESTAMP_HEADER = "X-HFC-Runtime-Timestamp"
RUNTIME_NONCE_HEADER = "X-HFC-Runtime-Nonce"
RUNTIME_SIGNATURE_HEADER = "X-HFC-Runtime-Signature"
RUNTIME_HOOK_GENERATION = "hfc-runtime-control-v2"

_ROOT_SECRET_BYTES = 32
_PROOF_MAX_AGE_SECONDS = 5
_MAX_NONCES = 512
_RUNTIME_EVENTS = frozenset({"runtime.hello", "runtime.heartbeat"})
_RUNTIME_INTEGRITY_FENCE_STATE_NAME = "runtime-integrity-fence.json"
_RUNTIME_INTEGRITY_FENCE_LOCK_NAME = ".runtime-integrity-fence.lock"
_RUNTIME_INTEGRITY_FENCE_MAX_BYTES = 4096
_RUNTIME_ID_HASH_DOMAIN = b"hfc-runtime-id-v1\0"
_RUNTIME_FENCE_SNAPSHOT_DOMAIN = b"hfc-runtime-integrity-fence-snapshot-v1\0"
_RUNTIME_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "event",
        "runtime_id",
        "sequence",
        "created_at",
        "hook_generation",
        "package_version",
    }
)
_RUNTIME_EVENT_FIELDS_V2 = _RUNTIME_EVENT_FIELDS | {
    "active_sessions",
    "admission_draining",
    "active_work_count_complete",
    "drain_home_verified",
}
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NO_PROXY_OPENER = request.build_opener(request.ProxyHandler({}))


class RuntimeControlValidationError(ValueError):
    pass


class _RuntimeIntegrityFenceStateError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeIntegrityFenceBinding:
    """Opaque hashes binding a persisted fence to one Hermes target and plan."""

    target_identity: str
    plan_fingerprint: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.target_identity, str)
            or not isinstance(self.plan_fingerprint, str)
            or _SHA256_RE.fullmatch(self.target_identity) is None
            or _SHA256_RE.fullmatch(self.plan_fingerprint) is None
        ):
            raise ValueError("runtime integrity fence binding is invalid")


@dataclass(frozen=True)
class _RuntimeIntegrityFenceState:
    restart_required: bool = False
    manual_review_required: bool = False
    pre_repair_runtime_hash: str = ""
    binding: RuntimeIntegrityFenceBinding | None = None
    legacy_unbound: bool = False


@dataclass(frozen=True)
class RuntimeIntegrityReviewSnapshot:
    state_token: str
    state_present: bool
    manual_review_required: bool
    restart_required: bool
    binding: RuntimeIntegrityFenceBinding | None
    legacy_unbound_empty_restart: bool


def _review_snapshot(
    state: _RuntimeIntegrityFenceState,
    raw: bytes | None,
) -> RuntimeIntegrityReviewSnapshot:
    token_material = b"<absent>" if raw is None else raw
    return RuntimeIntegrityReviewSnapshot(
        state_token=hashlib.sha256(
            _RUNTIME_FENCE_SNAPSHOT_DOMAIN + token_material
        ).hexdigest(),
        state_present=raw is not None,
        manual_review_required=state.manual_review_required,
        restart_required=state.restart_required,
        binding=state.binding,
        legacy_unbound_empty_restart=bool(
            state.legacy_unbound
            and state.restart_required
            and state.manual_review_required
            and not state.pre_repair_runtime_hash
        ),
    )


class _RuntimeIntegrityFenceStore:
    def __init__(self, directory: str | Path):
        self.root = Path(directory).expanduser()
        self.path = self.root / _RUNTIME_INTEGRITY_FENCE_STATE_NAME

    @contextmanager
    def locked(self):
        with _exclusive_private_fence_lock(self.root):
            yield

    def load(self) -> _RuntimeIntegrityFenceState:
        with self.locked():
            state, _raw = self._load_unlocked()
            return state

    def load_snapshot(self) -> RuntimeIntegrityReviewSnapshot:
        with self.locked():
            state, raw = self._load_unlocked()
            return _review_snapshot(state, raw)

    def _load_unlocked(self) -> tuple[_RuntimeIntegrityFenceState, bytes | None]:
        _prepare_private_fence_root(self.root)
        if _fence_lstat(self.path) is None:
            return _RuntimeIntegrityFenceState(), None
        raw = _read_private_fence_file(self.root, self.path)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state is invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state is invalid"
            )
        schema_version = payload.get("schema_version")
        common_fields = {
            "schema_version",
            "restart_required",
            "manual_review_required",
            "pre_repair_runtime_hash",
        }
        expected_fields = (
            common_fields
            if schema_version == "1"
            else common_fields | {"target_identity", "plan_fingerprint"}
        )
        if schema_version not in {"1", "2"} or set(payload) != expected_fields:
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state is invalid"
            )
        restart_required = payload.get("restart_required")
        manual_review_required = payload.get("manual_review_required")
        runtime_hash = payload.get("pre_repair_runtime_hash")
        if (
            not isinstance(restart_required, bool)
            or not isinstance(manual_review_required, bool)
            or not isinstance(runtime_hash, str)
            or (runtime_hash and _SHA256_RE.fullmatch(runtime_hash) is None)
            or (not restart_required and runtime_hash)
        ):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state is invalid"
            )
        binding = None
        if schema_version == "2":
            try:
                binding = RuntimeIntegrityFenceBinding(
                    target_identity=payload.get("target_identity"),
                    plan_fingerprint=payload.get("plan_fingerprint"),
                )
            except (TypeError, ValueError) as exc:
                raise _RuntimeIntegrityFenceStateError(
                    "runtime integrity fence state is invalid"
                ) from exc
        return _RuntimeIntegrityFenceState(
            restart_required=restart_required,
            manual_review_required=manual_review_required,
            pre_repair_runtime_hash=runtime_hash,
            binding=binding,
            legacy_unbound=schema_version == "1",
        ), raw

    def write(self, state: _RuntimeIntegrityFenceState) -> None:
        with self.locked():
            self._write_unlocked(state)

    def _write_unlocked(self, state: _RuntimeIntegrityFenceState) -> None:
        if (
            not isinstance(state, _RuntimeIntegrityFenceState)
            or state.binding is None
            or state.legacy_unbound
            or (
                state.pre_repair_runtime_hash
                and _SHA256_RE.fullmatch(state.pre_repair_runtime_hash) is None
            )
            or (not state.restart_required and state.pre_repair_runtime_hash)
        ):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state is invalid"
            )
        _prepare_private_fence_root(self.root)
        payload = (
            json.dumps(
                {
                    "schema_version": "2",
                    "restart_required": state.restart_required,
                    "manual_review_required": state.manual_review_required,
                    "pre_repair_runtime_hash": state.pre_repair_runtime_hash,
                    "target_identity": state.binding.target_identity,
                    "plan_fingerprint": state.binding.plan_fingerprint,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        _atomic_write_private_fence(self.root, self.path, payload)

    def acknowledge(
        self,
        *,
        expected_state_token: str,
        expected_binding: RuntimeIntegrityFenceBinding,
        allow_legacy_unbound_empty_restart: bool,
        allow_same_target_plan_transition: bool,
    ) -> bool:
        with self.locked():
            state, raw = self._load_unlocked()
            snapshot = _review_snapshot(state, raw)
            if not hmac.compare_digest(snapshot.state_token, expected_state_token):
                raise _RuntimeIntegrityFenceStateError(
                    "runtime integrity fence changed before acknowledgement"
                )
            if state.binding is None:
                if not state.manual_review_required and raw is None:
                    return False
                if not (
                    allow_legacy_unbound_empty_restart
                    and snapshot.legacy_unbound_empty_restart
                ):
                    raise _RuntimeIntegrityFenceStateError(
                        "runtime integrity fence is not bound"
                    )
                output_binding = expected_binding
            else:
                if state.binding != expected_binding:
                    # The caller must independently verify the current installed
                    # plan before opting in. The store still anchors the change to
                    # the exact old snapshot and never permits a target transition.
                    if not (
                        allow_same_target_plan_transition
                        and hmac.compare_digest(
                            state.binding.target_identity,
                            expected_binding.target_identity,
                        )
                    ):
                        raise _RuntimeIntegrityFenceStateError(
                            "runtime integrity fence binding changed"
                        )
                    output_binding = expected_binding
                else:
                    output_binding = state.binding
            if not state.manual_review_required:
                return False
            unresolved_restart = bool(
                state.restart_required and not state.pre_repair_runtime_hash
            )
            self._write_unlocked(
                _RuntimeIntegrityFenceState(
                    restart_required=(
                        False if unresolved_restart else state.restart_required
                    ),
                    manual_review_required=False,
                    pre_repair_runtime_hash=(
                        "" if unresolved_restart else state.pre_repair_runtime_hash
                    ),
                    binding=output_binding,
                )
            )
            return True


def inspect_runtime_integrity_review(
    state_directory: str | Path,
) -> RuntimeIntegrityReviewSnapshot:
    """Return an opaque review snapshot suitable for later CAS acknowledgement."""
    store = _RuntimeIntegrityFenceStore(state_directory)
    try:
        return store.load_snapshot()
    except (OSError, _RuntimeIntegrityFenceStateError) as exc:
        raise RuntimeControlValidationError(
            "runtime integrity review could not be inspected safely"
        ) from exc


def acknowledge_runtime_integrity_review(
    state_directory: str | Path,
    *,
    expected_state_token: str,
    expected_binding: RuntimeIntegrityFenceBinding,
    allow_legacy_unbound_empty_restart: bool = False,
    allow_same_target_plan_transition: bool = False,
) -> bool:
    """CAS-clear a review fence with explicit legacy/same-target permissions."""
    if (
        not isinstance(expected_state_token, str)
        or _SHA256_RE.fullmatch(expected_state_token) is None
        or not isinstance(expected_binding, RuntimeIntegrityFenceBinding)
        or not isinstance(allow_legacy_unbound_empty_restart, bool)
        or not isinstance(allow_same_target_plan_transition, bool)
    ):
        raise RuntimeControlValidationError(
            "runtime integrity review could not be acknowledged safely"
        )
    store = _RuntimeIntegrityFenceStore(state_directory)
    try:
        return store.acknowledge(
            expected_state_token=expected_state_token,
            expected_binding=expected_binding,
            allow_legacy_unbound_empty_restart=allow_legacy_unbound_empty_restart,
            allow_same_target_plan_transition=allow_same_target_plan_transition,
        )
    except (OSError, _RuntimeIntegrityFenceStateError) as exc:
        raise RuntimeControlValidationError(
            "runtime integrity review could not be acknowledged safely"
        ) from exc


@dataclass(frozen=True)
class RuntimeControlEvent:
    schema_version: str
    event: str
    runtime_id: str
    sequence: int
    created_at: float
    hook_generation: str
    package_version: str
    active_sessions: int | None = None
    admission_draining: bool | None = None
    active_work_count_complete: bool | None = None
    drain_home_verified: bool | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RuntimeControlEvent":
        if not isinstance(payload, Mapping):
            raise RuntimeControlValidationError("invalid runtime control event")
        schema_version = payload.get("schema_version")
        expected_fields = (
            _RUNTIME_EVENT_FIELDS_V2
            if schema_version == "2"
            else _RUNTIME_EVENT_FIELDS
        )
        if set(payload) != expected_fields:
            raise RuntimeControlValidationError("invalid runtime control event")
        event = payload.get("event")
        runtime_id = payload.get("runtime_id")
        sequence = payload.get("sequence")
        created_at = payload.get("created_at")
        hook_generation = payload.get("hook_generation")
        package_version = payload.get("package_version")
        if schema_version not in {"1", "2"} or event not in _RUNTIME_EVENTS:
            raise RuntimeControlValidationError("invalid runtime control event")
        if (
            not isinstance(runtime_id, str)
            or not 16 <= len(runtime_id) <= 128
            or _SAFE_ID_RE.fullmatch(runtime_id) is None
        ):
            raise RuntimeControlValidationError("invalid runtime control event")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise RuntimeControlValidationError("invalid runtime control event")
        if (
            isinstance(created_at, bool)
            or not isinstance(created_at, (int, float))
            or not math.isfinite(float(created_at))
            or float(created_at) < 0
        ):
            raise RuntimeControlValidationError("invalid runtime control event")
        for value in (hook_generation, package_version):
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 128
                or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
            ):
                raise RuntimeControlValidationError("invalid runtime control event")
        active_sessions = payload.get("active_sessions")
        if schema_version == "2" and (
            isinstance(active_sessions, bool)
            or not isinstance(active_sessions, int)
            or not 0 <= active_sessions <= 1_000_000
        ):
            raise RuntimeControlValidationError("invalid runtime control event")
        admission_draining = payload.get("admission_draining")
        if schema_version == "2" and not isinstance(admission_draining, bool):
            raise RuntimeControlValidationError("invalid runtime control event")
        active_work_count_complete = payload.get("active_work_count_complete")
        if schema_version == "2" and not isinstance(
            active_work_count_complete, bool
        ):
            raise RuntimeControlValidationError("invalid runtime control event")
        drain_home_verified = payload.get("drain_home_verified")
        if schema_version == "2" and not isinstance(drain_home_verified, bool):
            raise RuntimeControlValidationError("invalid runtime control event")
        return cls(
            schema_version=schema_version,
            event=event,
            runtime_id=runtime_id,
            sequence=sequence,
            created_at=float(created_at),
            hook_generation=hook_generation,
            package_version=package_version,
            active_sessions=(active_sessions if schema_version == "2" else None),
            admission_draining=(
                admission_draining if schema_version == "2" else None
            ),
            active_work_count_complete=(
                active_work_count_complete if schema_version == "2" else None
            ),
            drain_home_verified=(
                drain_home_verified if schema_version == "2" else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "event": self.event,
            "runtime_id": self.runtime_id,
            "sequence": self.sequence,
            "created_at": self.created_at,
            "hook_generation": self.hook_generation,
            "package_version": self.package_version,
        }
        if self.schema_version == "2":
            payload["active_sessions"] = self.active_sessions
            payload["admission_draining"] = self.admission_draining
            payload["active_work_count_complete"] = self.active_work_count_complete
            payload["drain_home_verified"] = self.drain_home_verified
        return payload


def sign_runtime_request(
    secret: bytes,
    body: bytes,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    _validate_secret(secret)
    if not isinstance(body, bytes):
        raise ValueError("runtime request body must be bytes")
    signed_at = int(time.time()) if timestamp is None else timestamp
    request_nonce = secrets.token_urlsafe(18) if nonce is None else nonce
    if (
        isinstance(signed_at, bool)
        or not isinstance(signed_at, int)
        or not isinstance(request_nonce, str)
        or not 16 <= len(request_nonce) <= 128
    ):
        raise ValueError("runtime proof metadata is invalid")
    signature = hmac.new(
        secret,
        _runtime_signing_input(signed_at, request_nonce, _body_hash(body)),
        hashlib.sha256,
    ).hexdigest()
    return {
        RUNTIME_TIMESTAMP_HEADER: str(signed_at),
        RUNTIME_NONCE_HEADER: request_nonce,
        RUNTIME_SIGNATURE_HEADER: signature,
    }


class RuntimeProofVerifier:
    def __init__(
        self,
        secret: bytes,
        *,
        now: Callable[[], float] = time.time,
        max_nonces: int = _MAX_NONCES,
    ):
        _validate_secret(secret)
        if max_nonces < 1:
            raise ValueError("max_nonces must be positive")
        self._secret = secret
        self._now = now
        self._max_nonces = max_nonces
        self._nonces: dict[str, float] = {}
        self._lock = threading.Lock()

    def verify(self, headers: Mapping[str, str], body: bytes) -> None:
        if not isinstance(body, bytes):
            raise RuntimeControlValidationError("invalid runtime proof")
        timestamp_text = _header_value(headers, RUNTIME_TIMESTAMP_HEADER)
        nonce = _header_value(headers, RUNTIME_NONCE_HEADER)
        signature = _header_value(headers, RUNTIME_SIGNATURE_HEADER)
        try:
            timestamp = int(timestamp_text) if timestamp_text is not None else None
        except (TypeError, ValueError):
            timestamp = None
        if (
            timestamp is None
            or isinstance(timestamp, bool)
            or not isinstance(nonce, str)
            or not 16 <= len(nonce) <= 128
            or not isinstance(signature, str)
            or len(signature) != 64
        ):
            raise RuntimeControlValidationError("invalid runtime proof")

        now = self._now()
        if abs(now - timestamp) > _PROOF_MAX_AGE_SECONDS:
            raise RuntimeControlValidationError("runtime proof expired")
        expected = hmac.new(
            self._secret,
            _runtime_signing_input(timestamp, nonce, _body_hash(body)),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise RuntimeControlValidationError("invalid runtime proof")

        with self._lock:
            self._prune_nonces_locked(now)
            if nonce in self._nonces:
                raise RuntimeControlValidationError("runtime proof replayed")
            if len(self._nonces) >= self._max_nonces:
                raise RuntimeControlValidationError("runtime proof verifier overloaded")
            self._nonces[nonce] = timestamp + _PROOF_MAX_AGE_SECONDS

    def _prune_nonces_locked(self, now: float) -> None:
        for nonce, expires_at in list(self._nonces.items()):
            if expires_at < now:
                self._nonces.pop(nonce, None)


def runtime_events_url(event_url: str) -> str:
    parsed = parse.urlsplit(str(event_url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("runtime event URL is invalid")
    path = parsed.path.rstrip("/")
    if path.endswith("/events"):
        path = path[: -len("/events")]
    return parse.urlunsplit(
        (parsed.scheme, parsed.netloc, f"{path}/runtime/events", "", "")
    )


class RuntimeControlEmitter:
    def __init__(
        self,
        *,
        event_url: str,
        hook_generation: str,
        package_version: str,
        runtime_id: str | None = None,
        now: Callable[[], float] = time.time,
        secret_reader: Callable[[], bytes | None] = read_transport_root_secret,
        poster: Callable[[str, bytes, dict[str, str], float], bool] | None = None,
        timeout_seconds: float = 1.0,
        active_work_snapshot_provider: Callable[[], tuple[int, bool]] | None = None,
        admission_draining_provider: Callable[[], bool] | None = None,
        drain_home_verified_provider: Callable[[], bool] | None = None,
        runtime_snapshot_provider: (
            Callable[[], tuple[int, bool, bool, bool]] | None
        ) = None,
    ):
        self.runtime_url = runtime_events_url(event_url)
        self.hook_generation = _bounded_text(hook_generation, "hook generation")
        self.package_version = _bounded_text(package_version, "package version")
        self.runtime_id = runtime_id or f"runtime-{secrets.token_urlsafe(18)}"
        if (
            not 16 <= len(self.runtime_id) <= 128
            or _SAFE_ID_RE.fullmatch(self.runtime_id) is None
        ):
            raise ValueError("runtime id is invalid")
        if timeout_seconds <= 0 or timeout_seconds > 5:
            raise ValueError("runtime timeout is invalid")
        self._now = now
        self._secret_reader = secret_reader
        self._poster = poster or _post_runtime_request
        self._timeout_seconds = timeout_seconds
        self._active_work_snapshot_provider = (
            active_work_snapshot_provider or (lambda: (0, False))
        )
        self._admission_draining_provider = (
            admission_draining_provider or (lambda: False)
        )
        self._drain_home_verified_provider = (
            drain_home_verified_provider or (lambda: False)
        )
        self._runtime_snapshot_provider = runtime_snapshot_provider
        self._sequence = 0
        self._lock = threading.Lock()

    def emit_once(self, event_name: str) -> bool:
        if event_name not in _RUNTIME_EVENTS:
            return False
        try:
            with self._lock:
                self._sequence += 1
                sequence = self._sequence
            created_at = float(self._now())
            if self._runtime_snapshot_provider is not None:
                (
                    active_sessions,
                    active_work_count_complete,
                    admission_draining,
                    drain_home_verified,
                ) = self._runtime_snapshot_provider()
            else:
                active_sessions, active_work_count_complete = (
                    self._active_work_snapshot_provider()
                )
                admission_draining = self._admission_draining_provider()
                drain_home_verified = self._drain_home_verified_provider()
            event = RuntimeControlEvent.from_dict(
                {
                    "schema_version": "2",
                    "event": event_name,
                    "runtime_id": self.runtime_id,
                    "sequence": sequence,
                    "created_at": created_at,
                    "hook_generation": self.hook_generation,
                    "package_version": self.package_version,
                    "active_sessions": active_sessions,
                    "admission_draining": admission_draining,
                    "active_work_count_complete": active_work_count_complete,
                    "drain_home_verified": drain_home_verified,
                }
            )
            body = json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            secret = self._secret_reader()
            if secret is None:
                return False
            headers = {"Content-Type": "application/json"}
            headers.update(sign_runtime_request(secret, body, timestamp=int(created_at)))
            return bool(
                self._poster(
                    self.runtime_url,
                    body,
                    headers,
                    self._timeout_seconds,
                )
            )
        except Exception:
            return False

    def run(self, stop_event: threading.Event, interval_seconds: float = 15.0) -> None:
        self.emit_once("runtime.hello")
        while not stop_event.wait(interval_seconds):
            self.emit_once("runtime.heartbeat")


class RuntimeIntegritySupervisor:
    def __init__(
        self,
        *,
        mode: str,
        expected_hook_generation: str = RUNTIME_HOOK_GENERATION,
        expected_package_version: str = "",
        now: Callable[[], float] = time.monotonic,
        startup_grace_seconds: float = 30.0,
        stale_after_seconds: float = 45.0,
        state_directory: str | Path | None = None,
    ):
        mode = str(mode or "").strip().lower()
        if mode not in {"safe", "notify", "off"}:
            raise ValueError("integrity mode is invalid")
        if startup_grace_seconds < 0 or stale_after_seconds <= 0:
            raise ValueError("runtime readiness timing is invalid")
        self.mode = mode
        self.expected_hook_generation = expected_hook_generation
        self.expected_package_version = expected_package_version
        self._now = now
        self._started_at = now()
        self._startup_grace_seconds = startup_grace_seconds
        self._stale_after_seconds = stale_after_seconds
        self._last_seen_at: float | None = None
        self._runtime_id = ""
        self._last_sequence = 0
        self._generation_match = False
        self._active_sessions: int | None = None
        self._admission_draining: bool | None = None
        self._active_work_count_complete: bool | None = None
        self._drain_home_verified: bool | None = None
        self._restart_required = False
        self._manual_review_required = False
        self._pre_repair_runtime_hash = ""
        self._fence_binding: RuntimeIntegrityFenceBinding | None = None
        self._legacy_unbound_fence = False
        self._fence_store = (
            _RuntimeIntegrityFenceStore(state_directory)
            if state_directory is not None
            else None
        )
        if self._fence_store is not None:
            try:
                fence = self._fence_store.load()
            except (OSError, _RuntimeIntegrityFenceStateError):
                self._manual_review_required = True
            else:
                self._restart_required = fence.restart_required
                self._manual_review_required = fence.manual_review_required
                self._pre_repair_runtime_hash = fence.pre_repair_runtime_hash
                self._fence_binding = fence.binding
                self._legacy_unbound_fence = bool(
                    fence.legacy_unbound
                    and (fence.restart_required or fence.manual_review_required)
                )
        self._control_auth_unavailable = False
        self._lock = threading.Lock()

    def record(self, event: RuntimeControlEvent) -> bool:
        if not isinstance(event, RuntimeControlEvent) or self.mode == "off":
            return False
        now = self._now()
        with self._lock:
            if event.runtime_id == self._runtime_id and event.sequence <= self._last_sequence:
                return False
            self._runtime_id = event.runtime_id
            self._last_sequence = event.sequence
            self._last_seen_at = now
            generation_match = bool(
                event.hook_generation == self.expected_hook_generation
                and (
                    not self.expected_package_version
                    or event.package_version == self.expected_package_version
                )
            )
            self._generation_match = generation_match
            self._active_sessions = event.active_sessions
            self._admission_draining = event.admission_draining
            self._active_work_count_complete = event.active_work_count_complete
            self._drain_home_verified = event.drain_home_verified
            if (
                event.event == "runtime.hello"
                and generation_match
                and self._restart_required
                and bool(self._pre_repair_runtime_hash)
                and _runtime_id_hash(event.runtime_id)
                != self._pre_repair_runtime_hash
            ):
                previous_hash = self._pre_repair_runtime_hash
                self._restart_required = False
                self._pre_repair_runtime_hash = ""
                if not self._persist_fence_locked():
                    self._restart_required = True
                    self._pre_repair_runtime_hash = previous_hash
        return True

    def mark_restart_required(
        self,
        *,
        binding: RuntimeIntegrityFenceBinding | None = None,
    ) -> None:
        with self._lock:
            binding_ready = self._adopt_fence_binding_locked(binding)
            if not self._restart_required:
                self._pre_repair_runtime_hash = _runtime_id_hash(self._runtime_id)
                if not self._pre_repair_runtime_hash:
                    self._manual_review_required = True
            self._restart_required = True
            if not binding_ready:
                self._manual_review_required = True
                return
            self._persist_fence_locked()

    def mark_manual_review_required(
        self,
        *,
        binding: RuntimeIntegrityFenceBinding | None = None,
    ) -> None:
        with self._lock:
            if not self._adopt_fence_binding_locked(binding):
                self._manual_review_required = True
                return
            self._manual_review_required = True
            self._persist_fence_locked()

    def _adopt_fence_binding_locked(
        self,
        binding: RuntimeIntegrityFenceBinding | None,
    ) -> bool:
        if binding is not None and not isinstance(
            binding, RuntimeIntegrityFenceBinding
        ):
            return False
        if self._legacy_unbound_fence:
            return False
        if self._fence_binding is None:
            if binding is None:
                return self._fence_store is None
            self._fence_binding = binding
            return True
        if binding is None or binding == self._fence_binding:
            return True
        if self._restart_required or self._manual_review_required:
            return False
        self._fence_binding = binding
        return True

    def mark_control_auth_unavailable(self) -> None:
        with self._lock:
            self._control_auth_unavailable = True

    def _persist_fence_locked(self) -> bool:
        if self._fence_store is None:
            return True
        if self._fence_binding is None or self._legacy_unbound_fence:
            self._manual_review_required = True
            return False
        try:
            self._fence_store.write(
                _RuntimeIntegrityFenceState(
                    restart_required=self._restart_required,
                    manual_review_required=self._manual_review_required,
                    pre_repair_runtime_hash=self._pre_repair_runtime_hash,
                    binding=self._fence_binding,
                )
            )
        except (OSError, _RuntimeIntegrityFenceStateError):
            self._manual_review_required = True
            return False
        return True

    def snapshot(self) -> dict[str, Any]:
        now = self._now()
        with self._lock:
            last_seen_at = self._last_seen_at
            generation_match = self._generation_match
            restart_required = self._restart_required
            manual_review_required = self._manual_review_required
            control_auth_unavailable = self._control_auth_unavailable
            runtime_id_hash = _runtime_id_hash(self._runtime_id)
            last_sequence = self._last_sequence
            active_sessions = self._active_sessions
            admission_draining = self._admission_draining
            active_work_count_complete = self._active_work_count_complete
            drain_home_verified = self._drain_home_verified

        if self.mode == "off":
            status = "disabled"
            reason = "integrity_disabled"
        elif control_auth_unavailable:
            status = "degraded"
            reason = "control_auth_unavailable"
        elif manual_review_required:
            status = "degraded"
            reason = "manual_review_required"
        elif restart_required:
            status = "degraded"
            reason = "gateway_restart_required"
        elif last_seen_at is None:
            status = "starting" if now - self._started_at <= self._startup_grace_seconds else "degraded"
            reason = (
                "runtime_heartbeat_waiting"
                if status == "starting"
                else "runtime_heartbeat_missing"
            )
        elif now - last_seen_at > self._stale_after_seconds:
            status = "degraded"
            reason = "runtime_heartbeat_stale"
        elif not generation_match:
            status = "degraded"
            reason = "gateway_restart_required"
            restart_required = True
        else:
            status = "ready"
            reason = "runtime_ready"

        age = None if last_seen_at is None else max(0, int(now - last_seen_at))
        return {
            "status": status,
            "reason": reason,
            "integrity_mode": self.mode,
            "runtime_seen": last_seen_at is not None,
            "generation_match": generation_match,
            "restart_required": restart_required,
            "last_seen_age_seconds": age,
            "runtime_id_hash": runtime_id_hash,
            "last_sequence": last_sequence,
            "active_sessions": active_sessions,
            "admission_draining": admission_draining,
            "active_work_count_complete": active_work_count_complete,
            "drain_home_verified": drain_home_verified,
        }


_CONTROL_LOCK = threading.Lock()
_CONTROL_EMITTER: RuntimeControlEmitter | None = None
_CONTROL_STOP: threading.Event | None = None
_CONTROL_THREAD: threading.Thread | None = None
_CONTROL_CONFIG: tuple[object, ...] | None = None
_CONTROL_OWNERS: set[object] = set()
_CONTROL_PROVIDERS: dict[
    object,
    tuple[
        Callable[[], tuple[int, bool]] | None,
        Callable[[], bool] | None,
        Callable[[], bool] | None,
        bool,
    ],
] = {}
_CONTROL_PROVIDER_EPOCH = 0
_CONTROL_SNAPSHOT_ATTEMPTS = 3
_CONTROL_STOPPING = False
_LEGACY_CONTROL_LEASE: "RuntimeControlLease | None" = None


class RuntimeControlLease:
    """One process-local owner of the shared runtime-control worker."""

    def __init__(self, owner: object):
        self._owner = owner
        self._closed = False
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return None
            self._closed = True
        _release_runtime_control(self._owner)
        return None


def acquire_runtime_control(
    *,
    event_url: str,
    package_version: str,
    hook_generation: str = RUNTIME_HOOK_GENERATION,
    interval_seconds: float = 15.0,
    active_work_snapshot_provider: Callable[[], tuple[int, bool]] | None = None,
    admission_draining_provider: Callable[[], bool] | None = None,
    drain_home_verified_provider: Callable[[], bool] | None = None,
    gateway_admission_dependent: bool = False,
) -> RuntimeControlLease | None:
    """Acquire the shared worker; explicit native observers depend on Gateway admission.

    Dependent owners still supply complete activity counts. They cannot establish
    Gateway authority themselves or make an absent/unknown owner safe to stop.
    """
    global _CONTROL_EMITTER, _CONTROL_STOP, _CONTROL_THREAD, _CONTROL_CONFIG
    global _CONTROL_PROVIDER_EPOCH, _CONTROL_STOPPING
    try:
        if type(gateway_admission_dependent) is not bool:
            return None
        if gateway_admission_dependent and (
            not callable(active_work_snapshot_provider)
            or admission_draining_provider is not None
            or drain_home_verified_provider is not None
        ):
            return None
        if interval_seconds <= 0:
            return None
        config = (
            runtime_events_url(event_url),
            _bounded_text(hook_generation, "hook generation"),
            _bounded_text(package_version, "package version"),
            float(interval_seconds),
        )
        owner = object()
        with _CONTROL_LOCK:
            if _CONTROL_CONFIG is not None:
                if _CONTROL_STOPPING:
                    if _CONTROL_THREAD is not None and _CONTROL_THREAD.is_alive():
                        return None
                    _clear_runtime_control_locked()
                if _CONTROL_CONFIG is not None:
                    if _CONTROL_CONFIG != config:
                        return None
                    if _CONTROL_THREAD is None or not _CONTROL_THREAD.is_alive():
                        return None
                    _CONTROL_OWNERS.add(owner)
                    _CONTROL_PROVIDERS[owner] = (
                        active_work_snapshot_provider,
                        admission_draining_provider,
                        drain_home_verified_provider,
                        gateway_admission_dependent,
                    )
                    _CONTROL_PROVIDER_EPOCH += 1
                    return RuntimeControlLease(owner)
            emitter = RuntimeControlEmitter(
                event_url=event_url,
                hook_generation=hook_generation,
                package_version=package_version,
                active_work_snapshot_provider=_combined_active_work_snapshot,
                admission_draining_provider=_combined_admission_draining,
                drain_home_verified_provider=_combined_drain_home_verified,
                runtime_snapshot_provider=_combined_runtime_snapshot,
            )
            stop_event = threading.Event()
            thread = threading.Thread(
                target=emitter.run,
                args=(stop_event, float(interval_seconds)),
                name="hfc-runtime-control",
                daemon=True,
            )
            _CONTROL_EMITTER = emitter
            _CONTROL_STOP = stop_event
            _CONTROL_THREAD = thread
            _CONTROL_CONFIG = config
            _CONTROL_OWNERS.add(owner)
            _CONTROL_PROVIDERS[owner] = (
                active_work_snapshot_provider,
                admission_draining_provider,
                drain_home_verified_provider,
                gateway_admission_dependent,
            )
            _CONTROL_PROVIDER_EPOCH += 1
            _CONTROL_STOPPING = False
            try:
                thread.start()
            except Exception:
                _clear_runtime_control_locked()
                stop_event.set()
                return None
        return RuntimeControlLease(owner)
    except Exception:
        return None


def _release_runtime_control(owner: object) -> None:
    global _CONTROL_EMITTER, _CONTROL_STOP, _CONTROL_THREAD, _CONTROL_CONFIG
    global _CONTROL_PROVIDER_EPOCH, _CONTROL_STOPPING
    with _CONTROL_LOCK:
        if owner not in _CONTROL_OWNERS:
            return None
        _CONTROL_OWNERS.remove(owner)
        _CONTROL_PROVIDERS.pop(owner, None)
        _CONTROL_PROVIDER_EPOCH += 1
        if _CONTROL_OWNERS:
            return None
        stop_event = _CONTROL_STOP
        thread = _CONTROL_THREAD
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if thread is not None and thread.is_alive():
            _CONTROL_STOPPING = True
            return None
        _clear_runtime_control_locked()
    return None


def _clear_runtime_control_locked() -> None:
    global _CONTROL_EMITTER, _CONTROL_STOP, _CONTROL_THREAD, _CONTROL_CONFIG
    global _CONTROL_PROVIDER_EPOCH, _CONTROL_STOPPING
    _CONTROL_EMITTER = None
    _CONTROL_STOP = None
    _CONTROL_THREAD = None
    _CONTROL_CONFIG = None
    _CONTROL_STOPPING = False
    _CONTROL_OWNERS.clear()
    _CONTROL_PROVIDERS.clear()
    _CONTROL_PROVIDER_EPOCH += 1


def _evaluate_runtime_snapshot(
    providers: tuple[
        tuple[
            Callable[[], tuple[int, bool]] | None,
            Callable[[], bool] | None,
            Callable[[], bool] | None,
            bool,
        ],
        ...,
    ],
) -> tuple[int, bool, bool, bool]:
    total = 0
    complete = bool(providers)
    gateway_aggregate_present = False
    admission_draining = not providers
    drain_home_verified = bool(providers)
    for active_provider, admission_provider, home_provider, dependent in providers:
        if (
            not dependent
            and active_provider is not None
            and admission_provider is not None
            and home_provider is not None
        ):
            gateway_aggregate_present = True
        if active_provider is None:
            complete = False
        else:
            try:
                active, owner_complete = active_provider()
                if (
                    type(active) is not int
                    or active < 0
                    or type(owner_complete) is not bool
                ):
                    raise ValueError("invalid active work snapshot")
                total = min(1_000_000, total + active)
                complete = complete and owner_complete
            except Exception:
                complete = False
        # Only the native observer explicitly opts into the same-process Gateway
        # admission boundary. Unknown leases retain the conservative defaults.
        if dependent:
            continue
        if admission_provider is None:
            admission_draining = True
        else:
            try:
                value = admission_provider()
                if type(value) is not bool or value:
                    admission_draining = True
            except Exception:
                admission_draining = True
        if home_provider is None:
            drain_home_verified = False
        else:
            try:
                if home_provider() is not True:
                    drain_home_verified = False
            except Exception:
                drain_home_verified = False
    return (
        total,
        complete and gateway_aggregate_present,
        admission_draining or not gateway_aggregate_present,
        drain_home_verified and gateway_aggregate_present,
    )


def _combined_runtime_snapshot() -> tuple[int, bool, bool, bool]:
    for _attempt in range(_CONTROL_SNAPSHOT_ATTEMPTS):
        with _CONTROL_LOCK:
            epoch = _CONTROL_PROVIDER_EPOCH
            providers = tuple(_CONTROL_PROVIDERS.values())
        snapshot = _evaluate_runtime_snapshot(providers)
        with _CONTROL_LOCK:
            if epoch == _CONTROL_PROVIDER_EPOCH:
                return snapshot
    return 0, False, True, False


def _combined_active_work_snapshot() -> tuple[int, bool]:
    active, complete, _draining, _home_verified = _combined_runtime_snapshot()
    return active, complete


def _combined_admission_draining() -> bool:
    _active, _complete, draining, _home_verified = _combined_runtime_snapshot()
    return draining


def _combined_drain_home_verified() -> bool:
    _active, _complete, _draining, home_verified = _combined_runtime_snapshot()
    return home_verified


def start_runtime_control(
    *,
    event_url: str,
    package_version: str,
    hook_generation: str = RUNTIME_HOOK_GENERATION,
    interval_seconds: float = 15.0,
    active_work_snapshot_provider: Callable[[], tuple[int, bool]] | None = None,
    admission_draining_provider: Callable[[], bool] | None = None,
    drain_home_verified_provider: Callable[[], bool] | None = None,
) -> bool:
    global _LEGACY_CONTROL_LEASE
    lease = acquire_runtime_control(
        event_url=event_url,
        package_version=package_version,
        hook_generation=hook_generation,
        interval_seconds=interval_seconds,
        active_work_snapshot_provider=active_work_snapshot_provider,
        admission_draining_provider=admission_draining_provider,
        drain_home_verified_provider=drain_home_verified_provider,
    )
    if lease is None:
        return False
    with _CONTROL_LOCK:
        if _LEGACY_CONTROL_LEASE is None:
            _LEGACY_CONTROL_LEASE = lease
            return True
    lease.close()
    return True


def reset_runtime_control_for_tests() -> None:
    global _CONTROL_EMITTER, _CONTROL_STOP, _CONTROL_THREAD, _CONTROL_CONFIG
    global _LEGACY_CONTROL_LEASE, _CONTROL_PROVIDER_EPOCH, _CONTROL_STOPPING
    with _CONTROL_LOCK:
        stop_event = _CONTROL_STOP
        thread = _CONTROL_THREAD
        _CONTROL_EMITTER = None
        _CONTROL_STOP = None
        _CONTROL_THREAD = None
        _CONTROL_CONFIG = None
        _CONTROL_OWNERS.clear()
        _CONTROL_PROVIDERS.clear()
        _CONTROL_PROVIDER_EPOCH += 1
        _CONTROL_STOPPING = False
        _LEGACY_CONTROL_LEASE = None
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=1.0)


def _post_runtime_request(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
) -> bool:
    req = request.Request(url, data=body, headers=headers, method="POST")
    with _open_runtime_request(req, timeout) as response:
        response.read(4096)
        return 200 <= int(getattr(response, "status", 0)) < 300


def _open_runtime_request(req: request.Request, timeout: float):
    host = (parse.urlsplit(req.full_url).hostname or "").strip().lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return _NO_PROXY_OPENER.open(req, timeout=timeout)
    return request.urlopen(req, timeout=timeout)


def _bounded_text(value: str, label: str) -> str:
    normalized = str(value or "")
    if (
        not normalized
        or len(normalized) > 128
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized)
    ):
        raise ValueError(f"{label} is invalid")
    return normalized


def _validate_secret(secret: bytes) -> None:
    if not isinstance(secret, bytes) or len(secret) != _ROOT_SECRET_BYTES:
        raise ValueError("runtime transport root is invalid")


def _body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _runtime_id_hash(runtime_id: str) -> str:
    normalized = str(runtime_id or "")
    if not normalized:
        return ""
    return hashlib.sha256(
        _RUNTIME_ID_HASH_DOMAIN + normalized.encode("utf-8")
    ).hexdigest()


def _runtime_signing_input(timestamp: int, nonce: str, body_hash: str) -> bytes:
    return f"hfc-runtime-v1\0{timestamp}\0{nonce}\0{body_hash}".encode("utf-8")


@contextmanager
def _exclusive_private_fence_lock(root: Path):
    """Serialize all cooperating fence readers/writers across processes."""
    _prepare_private_fence_root(root)
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI only
        import msvcrt

        lock_path = root / _RUNTIME_INTEGRITY_FENCE_LOCK_NAME
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.chmod(lock_path, 0o600)
            _validate_private_fence_file(lock_path)
            if os.fstat(descriptor).st_size < 1:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)
        return

    import fcntl

    root_descriptor = _open_private_fence_root(root)
    descriptor = -1
    try:
        opened_root = os.fstat(root_descriptor)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(
            _RUNTIME_INTEGRITY_FENCE_LOCK_NAME,
            flags,
            0o600,
            dir_fd=root_descriptor,
        )
        os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        current = _fence_stat_at(
            root_descriptor, _RUNTIME_INTEGRITY_FENCE_LOCK_NAME
        )
        if (
            current is None
            or not _same_fence_identity(opened, current)
            or not _fence_root_matches_descriptor(root, opened_root)
        ):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence lock changed while opening"
            )
        _validate_private_fence_stat(opened)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            current = _fence_stat_at(
                root_descriptor, _RUNTIME_INTEGRITY_FENCE_LOCK_NAME
            )
            if (
                current is None
                or not _same_fence_identity(opened, current)
                or not _fence_root_matches_descriptor(root, opened_root)
            ):
                raise _RuntimeIntegrityFenceStateError(
                    "runtime integrity fence lock changed while waiting"
                )
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except _RuntimeIntegrityFenceStateError:
        raise
    except OSError as exc:
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence lock is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_descriptor)


def _prepare_private_fence_root(root: Path) -> None:
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence root is unavailable"
        ) from exc
    current = _fence_lstat(root)
    if current is None or not stat.S_ISDIR(current.st_mode):
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence root is invalid"
        )
    getuid = getattr(os, "getuid", None)
    if os.name != "nt" and (
        stat.S_IMODE(current.st_mode) != 0o700
        or not callable(getuid)
        or current.st_uid != getuid()
    ):
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence root is not private"
        )


def _read_private_fence_file(root: Path, path: Path) -> bytes:
    if os.name == "nt":
        return _read_private_fence_file_by_path(root, path)

    _prepare_private_fence_root(root)
    root_descriptor = _open_private_fence_root(root)
    descriptor = -1
    try:
        opened_root = os.fstat(root_descriptor)
        if not _fence_root_matches_descriptor(root, opened_root):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence root changed while opening"
            )
        before = _fence_stat_at(root_descriptor, path.name)
        if before is None:
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state disappeared while opening"
            )
        _validate_private_fence_stat(before)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path.name, flags, dir_fd=root_descriptor)
        opened = os.fstat(descriptor)
        current = _fence_stat_at(root_descriptor, path.name)
        if (
            not stat.S_ISREG(opened.st_mode)
            or current is None
            or not _same_fence_identity(before, opened)
            or not _same_fence_identity(current, opened)
        ):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state changed while opening"
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(_RUNTIME_INTEGRITY_FENCE_MAX_BYTES + 1)
        after = _fence_stat_at(root_descriptor, path.name)
        if (
            after is None
            or not _same_fence_identity(after, opened)
            or not _fence_root_matches_descriptor(root, opened_root)
        ):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state changed while reading"
            )
        _validate_private_fence_stat(after)
    except _RuntimeIntegrityFenceStateError:
        raise
    except OSError as exc:
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence state could not be read safely"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_descriptor)
    if len(raw) > _RUNTIME_INTEGRITY_FENCE_MAX_BYTES:
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence state is too large"
        )
    return raw


def _read_private_fence_file_by_path(root: Path, path: Path) -> bytes:
    _prepare_private_fence_root(root)
    root_before = _fence_lstat(root)
    before = _validate_private_fence_file(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence state could not be opened"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        current = _fence_lstat(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or current is None
            or not _same_fence_identity(before, opened)
            or not _same_fence_identity(current, opened)
        ):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state changed while opening"
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(_RUNTIME_INTEGRITY_FENCE_MAX_BYTES + 1)
        root_after = _fence_lstat(root)
        after = _fence_lstat(path)
        if (
            root_before is None
            or root_after is None
            or after is None
            or not _same_fence_identity(root_before, root_after)
            or not _same_fence_identity(opened, after)
        ):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state changed while reading"
            )
        _prepare_private_fence_root(root)
        _validate_private_fence_file(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > _RUNTIME_INTEGRITY_FENCE_MAX_BYTES:
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence state is too large"
        )
    return raw


def _atomic_write_private_fence(root: Path, path: Path, payload: bytes) -> None:
    if len(payload) > _RUNTIME_INTEGRITY_FENCE_MAX_BYTES:
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence state is too large"
        )
    if os.name == "nt":
        _atomic_write_private_fence_by_path(root, path, payload)
        return

    _prepare_private_fence_root(root)
    root_descriptor = _open_private_fence_root(root)
    temporary_name = ""
    descriptor = -1
    try:
        opened_root = os.fstat(root_descriptor)
        if not _fence_root_matches_descriptor(root, opened_root):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence root changed while opening"
            )
        existing = _fence_stat_at(root_descriptor, path.name)
        if existing is not None:
            _validate_private_fence_stat(existing)
        for _attempt in range(16):
            temporary_name = (
                f".{path.name}.{secrets.token_hex(8)}.tmp"
            )
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=root_descriptor,
                )
                break
            except FileExistsError:
                temporary_name = ""
        if descriptor < 0 or not temporary_name:
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence state could not be staged"
            )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not _fence_root_matches_descriptor(root, opened_root):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence root changed before replacement"
            )
        current = _fence_stat_at(root_descriptor, path.name)
        if existing is None:
            if current is not None:
                raise _RuntimeIntegrityFenceStateError(
                    "runtime integrity fence state changed before replacement"
                )
        else:
            if current is None:
                raise _RuntimeIntegrityFenceStateError(
                    "runtime integrity fence state changed before replacement"
                )
            _validate_private_fence_stat(current)
            if (
                current.st_dev != existing.st_dev
                or current.st_ino != existing.st_ino
            ):
                raise _RuntimeIntegrityFenceStateError(
                    "runtime integrity fence state changed before replacement"
                )
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=root_descriptor,
            dst_dir_fd=root_descriptor,
        )
        temporary_name = ""
        os.fsync(root_descriptor)
        if not _fence_root_matches_descriptor(root, opened_root):
            raise _RuntimeIntegrityFenceStateError(
                "runtime integrity fence root changed during replacement"
            )
    except _RuntimeIntegrityFenceStateError:
        raise
    except OSError as exc:
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence state could not be written atomically"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=root_descriptor)
            except FileNotFoundError:
                pass
        os.close(root_descriptor)


def _atomic_write_private_fence_by_path(
    root: Path,
    path: Path,
    payload: bytes,
) -> None:
    _prepare_private_fence_root(root)
    existing = _fence_lstat(path)
    if existing is not None:
        _validate_private_fence_file(path)
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(root)
        )
    except OSError as exc:
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence state could not be staged"
        ) from exc
    temporary = Path(temporary_name)
    try:
        try:
            handle = os.fdopen(descriptor, "wb")
        except Exception:
            os.close(descriptor)
            raise
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        current = _fence_lstat(path)
        if existing is None:
            if current is not None:
                raise _RuntimeIntegrityFenceStateError(
                    "runtime integrity fence state changed before replacement"
                )
        else:
            validated = _validate_private_fence_file(path)
            if (
                validated.st_dev != existing.st_dev
                or validated.st_ino != existing.st_ino
            ):
                raise _RuntimeIntegrityFenceStateError(
                    "runtime integrity fence state changed before replacement"
                )
        os.replace(temporary, path)
        _fsync_fence_directory(root)
    finally:
        temporary.unlink(missing_ok=True)


def _open_private_fence_root(root: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence root could not be opened"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        _validate_private_fence_stat(opened, require_directory=True)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _fence_root_matches_descriptor(root: Path, opened: os.stat_result) -> bool:
    current = _fence_lstat(root)
    matches = bool(
        current is not None
        and stat.S_ISDIR(current.st_mode)
        and current.st_dev == opened.st_dev
        and current.st_ino == opened.st_ino
    )
    if not matches or current is None:
        return False
    try:
        _validate_private_fence_stat(current, require_directory=True)
    except _RuntimeIntegrityFenceStateError:
        return False
    return True


def _fence_stat_at(directory_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None


def _same_fence_identity(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _validate_private_fence_stat(
    current: os.stat_result,
    *,
    require_directory: bool = False,
) -> None:
    expected_type = (
        stat.S_ISDIR(current.st_mode)
        if require_directory
        else stat.S_ISREG(current.st_mode)
    )
    getuid = getattr(os, "getuid", None)
    expected_mode = 0o700 if require_directory else 0o600
    if not expected_type or (
        os.name != "nt"
        and (
            stat.S_IMODE(current.st_mode) != expected_mode
            or not callable(getuid)
            or current.st_uid != getuid()
        )
    ):
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence state is not private"
        )


def _validate_private_fence_file(path: Path) -> os.stat_result:
    current = _fence_lstat(path)
    getuid = getattr(os, "getuid", None)
    if current is None or not stat.S_ISREG(current.st_mode):
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence state is invalid"
        )
    if os.name != "nt" and (
        stat.S_IMODE(current.st_mode) != 0o600
        or not callable(getuid)
        or current.st_uid != getuid()
    ):
        raise _RuntimeIntegrityFenceStateError(
            "runtime integrity fence state is not private"
        )
    return current


def _fsync_fence_directory(root: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(root, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _fence_lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is not None:
        return value
    normalized = name.lower()
    for key, candidate in headers.items():
        if str(key).lower() == normalized:
            return candidate
    return None
