from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from typing import Any, Callable, Iterator

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - unavailable on Windows.
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - unavailable on POSIX.
    _msvcrt = None


NATIVE_HANDOFF_STATE_NAME = "native-handoffs.json"
NATIVE_HANDOFF_LOCK_NAME = "native-handoffs.lock"
DEFAULT_MAX_RECORDS = 512
DEFAULT_MAX_FILE_BYTES = 256 * 1024
NATIVE_HANDOFF_PROTOCOL = "hfc-native-handoff-v2"
NATIVE_HANDOFF_TTL_SECONDS = 60 * 60
LIFECYCLE_FENCE_TTL_SECONDS = 60 * 60
NATIVE_HANDOFF_UUID_SEED_DOMAIN = b"hfc-native-handoff-uuid-seed-v1\0"
NATIVE_HANDOFF_TARGET_HASH_DOMAIN = b"hfc-native-handoff-target-v1\0"
NATIVE_HANDOFF_CONTENT_HASH_DOMAIN = b"hfc-native-content-v1\0"
NATIVE_HANDOFF_MANUAL_REVIEW_ACTION = (
    "review_native_delivery_before_manual_retry"
)
_IDENTITY_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_GENERATION_RE = re.compile(r"^[0-9a-f]{32,64}$")
_HANDOFF_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_SEED_RE = re.compile(r"^[0-9a-f]{32}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_VALID_CARD_STATES = frozenset({"none", "pending", "committed"})
_VALID_DELIVERY_STATES = frozenset(
    {"lifecycle", "legacy_consumed", "pending", "acked", "uncertain"}
)
_VALID_EXACT_ROUTES = frozenset({"create", "thread-create"})
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MAX_FEISHU_MESSAGE_ID_CHARS = 512
_MAX_BOT_ID_CHARS = 256
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


class NativeHandoffStoreError(OSError):
    """The private handoff state could not be read or updated safely."""


@dataclass(frozen=True)
class NativeHandoffRecord:
    card_state: str
    delivery_state: str
    card_message_hash: str
    bot_hash: str
    generation: str
    handoff_id: str
    uuid_seed: str
    obligation_key: str
    content_hash: str
    plan_fingerprint: str
    route: str
    target_hash: str
    created_at: float
    updated_at: float
    event_created_at: float
    expires_at: float

    @property
    def state(self) -> str:
        """Compatibility view for pre-ACK callers.

        Native delivery is deliberately not inferred from the card-notice
        PATCH state.  New code must inspect ``card_state`` and
        ``delivery_state`` independently.
        """

        if self.delivery_state == "lifecycle":
            return "lifecycle"
        if self.delivery_state == "pending" or self.card_state == "pending":
            return "pending"
        if self.card_state == "committed":
            return "committed"
        return "no_card"

    @property
    def feishu_message_id(self) -> str:
        # Raw platform identifiers are intentionally never persisted.
        return ""

    @property
    def bot_id(self) -> str:
        return ""

    @property
    def has_exact_delivery_binding(self) -> bool:
        return _record_has_exact_delivery_binding(self)

    def descriptor(self, *, now: float | None = None) -> dict[str, Any] | None:
        if (
            self.delivery_state != "pending"
            or not _record_has_exact_delivery_binding(self)
        ):
            return None
        if now is not None and _finite_timestamp(now) > self.expires_at:
            return None
        return {
            "protocol": NATIVE_HANDOFF_PROTOCOL,
            "id": self.handoff_id,
            "uuid_seed": self.uuid_seed,
            "expires_at": self.expires_at,
        }


def handoff_identity_key(
    *,
    profile_id: str,
    chat_id: str,
    conversation_id: str,
    message_id: str,
) -> str:
    """Return a stable opaque key without retaining any routing identifiers."""

    identity = json.dumps(
        {
            "profile_id": str(profile_id or ""),
            "chat_id": str(chat_id or ""),
            "conversation_id": str(conversation_id or ""),
            "message_id": str(message_id or ""),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(b"hfc-native-handoff-v1\0" + identity).hexdigest()


def derive_native_handoff_content_hash(content: Any) -> str:
    """Bind an exact handoff descriptor to the terminal text bytes."""

    return hashlib.sha256(
        NATIVE_HANDOFF_CONTENT_HASH_DOMAIN
        + str(content or "").encode("utf-8")
    ).hexdigest()


def is_exact_native_text_scope(data: Any) -> bool:
    """Return whether event data is the canonical ordinary-text ACK scope."""

    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("answer"), str) or not data["answer"]:
        return False
    if "delivery_kind" in data and data["delivery_kind"] != "":
        return False
    attachments = data.get("attachments")
    if not isinstance(attachments, list) or attachments:
        return False
    return data.get("native_delivery") == "allowed"


def derive_native_handoff_uuid_seed(
    *,
    obligation_key: str,
    content_hash: str,
    plan_fingerprint: str,
    route: str,
    target_hash: str,
) -> str:
    """Derive the non-secret stable Feishu UUID seed for one exact binding."""

    normalized_obligation = _validated_obligation_key(
        obligation_key,
        required=True,
    )
    normalized_content, normalized_plan, normalized_route, normalized_target = (
        _validated_exact_binding(
            content_hash,
            plan_fingerprint,
            route,
            target_hash,
            required=True,
        )
    )
    material = b"\0".join(
        value.encode("ascii")
        for value in (
            normalized_obligation,
            normalized_content,
            normalized_plan,
            normalized_route,
            normalized_target,
        )
    )
    return hashlib.sha256(NATIVE_HANDOFF_UUID_SEED_DOMAIN + material).hexdigest()[:32]


def derive_native_handoff_target_hash(
    *,
    profile_id: str,
    chat_id: str,
    thread_id: str,
    route: str,
) -> str:
    """Bind a deterministic seed to one opaque Feishu delivery target."""

    bounded_profile = str(profile_id or "").strip()
    bounded_chat = str(chat_id or "").strip()
    bounded_thread = str(thread_id or "").strip()
    normalized_route = str(route or "").strip().lower()
    if _PROFILE_ID_RE.fullmatch(bounded_profile) is None:
        raise ValueError("profile_id is invalid")
    if (
        not bounded_chat
        or len(bounded_chat) > 512
        or any(ord(character) < 32 for character in bounded_chat)
    ):
        raise ValueError("chat_id is invalid")
    if len(bounded_thread) > 512 or any(
        ord(character) < 32 for character in bounded_thread
    ):
        raise ValueError("thread_id is invalid")
    if normalized_route not in _VALID_EXACT_ROUTES:
        raise ValueError("route is invalid")
    if (normalized_route == "thread-create") != bool(bounded_thread):
        raise ValueError("route does not match thread target")
    material = json.dumps(
        {
            "profile_id": bounded_profile,
            "chat_id": bounded_chat,
            "thread_id": bounded_thread,
            "route": normalized_route,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(NATIVE_HANDOFF_TARGET_HASH_DOMAIN + material).hexdigest()


class NativeHandoffStore:
    def __init__(
        self,
        root: str | Path,
        *,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        now: Callable[[], float] = time.time,
    ) -> None:
        if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 1:
            raise ValueError("max_records must be a positive integer")
        if (
            isinstance(max_file_bytes, bool)
            or not isinstance(max_file_bytes, int)
            or max_file_bytes < 1024
        ):
            raise ValueError("max_file_bytes must be at least 1024")
        expanded_root = Path(root).expanduser()
        if ".." in expanded_root.parts:
            raise ValueError(
                "handoff state root must not traverse toward a filesystem root"
            )
        self.root = (
            expanded_root
            if expanded_root.is_absolute()
            else (Path.cwd() / expanded_root).absolute()
        )
        if self.root == Path(self.root.anchor):
            raise ValueError("handoff state root must not be a filesystem root")
        self.path = self.root / NATIVE_HANDOFF_STATE_NAME
        self.lock_path = self.root / NATIVE_HANDOFF_LOCK_NAME
        self.max_records = max_records
        self.max_file_bytes = max_file_bytes
        self._now = now
        self._lock = threading.RLock()

    def get(self, identity_key: str) -> NativeHandoffRecord | None:
        _validate_identity_key(identity_key)
        with self._lock:
            with self._persistent_lock():
                return self._load_records().get(identity_key)

    def begin(
        self,
        identity_key: str,
        *,
        feishu_message_id: str,
        bot_id: str,
        event_created_at: float,
        generation: str = "",
        ack_capable: bool = False,
        obligation_key: str = "",
        content_hash: str = "",
        plan_fingerprint: str = "",
        route: str = "",
        target_hash: str = "",
        provisional_uuid_seed: str = "",
    ) -> tuple[NativeHandoffRecord, bool]:
        _validate_identity_key(identity_key)
        message_id = _bounded_identifier(
            feishu_message_id,
            name="feishu_message_id",
            max_chars=_MAX_FEISHU_MESSAGE_ID_CHARS,
            required=True,
        )
        bounded_bot_id = _bounded_identifier(
            bot_id,
            name="bot_id",
            max_chars=_MAX_BOT_ID_CHARS,
            required=False,
        )
        if ack_capable:
            exact_binding = _validated_exact_binding(
                content_hash,
                plan_fingerprint,
                route,
                target_hash,
                required=True,
            )
            normalized_obligation = _validated_obligation_key(
                obligation_key,
                required=True,
            )
            uuid_seed = _validated_provisional_uuid_seed(
                provisional_uuid_seed,
                obligation_key=normalized_obligation,
                content_hash=exact_binding[0],
                plan_fingerprint=exact_binding[1],
                route=exact_binding[2],
                target_hash=exact_binding[3],
                required=True,
            )
        else:
            # Rolling or legacy payloads are fail-open native delivery only.
            # Never persist attacker-controlled pseudo-exact bindings for them.
            exact_binding = ("", "", "", "")
            normalized_obligation = ""
            uuid_seed = ""
        return self._begin(
            identity_key,
            card_state="pending",
            card_message_hash=_routing_hash("card-message", message_id),
            bot_hash=(
                _routing_hash("bot", bounded_bot_id) if bounded_bot_id else ""
            ),
            event_created_at=_finite_timestamp(event_created_at),
            generation=_validated_generation(generation, required=ack_capable),
            ack_capable=bool(ack_capable),
            obligation_key=normalized_obligation,
            content_hash=exact_binding[0],
            plan_fingerprint=exact_binding[1],
            route=exact_binding[2],
            target_hash=exact_binding[3],
            uuid_seed=uuid_seed,
        )

    def begin_no_card(
        self,
        identity_key: str,
        *,
        event_created_at: float,
        generation: str = "",
        ack_capable: bool = False,
        obligation_key: str = "",
        content_hash: str = "",
        plan_fingerprint: str = "",
        route: str = "",
        target_hash: str = "",
        provisional_uuid_seed: str = "",
    ) -> tuple[NativeHandoffRecord, bool]:
        _validate_identity_key(identity_key)
        if ack_capable:
            exact_binding = _validated_exact_binding(
                content_hash,
                plan_fingerprint,
                route,
                target_hash,
                required=True,
            )
            normalized_obligation = _validated_obligation_key(
                obligation_key,
                required=True,
            )
            uuid_seed = _validated_provisional_uuid_seed(
                provisional_uuid_seed,
                obligation_key=normalized_obligation,
                content_hash=exact_binding[0],
                plan_fingerprint=exact_binding[1],
                route=exact_binding[2],
                target_hash=exact_binding[3],
                required=True,
            )
        else:
            exact_binding = ("", "", "", "")
            normalized_obligation = ""
            uuid_seed = ""
        return self._begin(
            identity_key,
            card_state="none",
            card_message_hash="",
            bot_hash="",
            event_created_at=_finite_timestamp(event_created_at),
            generation=_validated_generation(generation, required=ack_capable),
            ack_capable=bool(ack_capable),
            obligation_key=normalized_obligation,
            content_hash=exact_binding[0],
            plan_fingerprint=exact_binding[1],
            route=exact_binding[2],
            target_hash=exact_binding[3],
            uuid_seed=uuid_seed,
        )

    def mark_card_committed(
        self,
        identity_key: str,
        *,
        expected_record: NativeHandoffRecord | None = None,
    ) -> NativeHandoffRecord | None:
        _validate_identity_key(identity_key)
        with self._lock:
            with self._persistent_lock():
                records = self._load_records()
                current = records.get(identity_key)
                if current is None:
                    return None
                if expected_record is not None and (
                    current.handoff_id != expected_record.handoff_id
                    or current.generation != expected_record.generation
                    or current.created_at != expected_record.created_at
                    or current.event_created_at != expected_record.event_created_at
                ):
                    # A newer lifecycle may have reused the same stable key
                    # while an older asynchronous PATCH was still in flight.
                    # Delivery ACK is an independent axis and may legitimately
                    # race ahead of this card-notice PATCH.
                    return current
                if current.card_state in {"committed", "none"}:
                    return current
                updated = replace(
                    current,
                    card_state="committed",
                    updated_at=self._timestamp(),
                )
                pending = dict(records)
                pending[identity_key] = updated
                self._write_records(pending, protected_key=identity_key)
                return updated

    # Backward-compatible spelling used by the existing server integration.
    mark_committed = mark_card_committed

    def acknowledge(
        self,
        descriptor: dict[str, Any],
    ) -> tuple[NativeHandoffRecord, bool]:
        normalized = _validated_descriptor(descriptor)
        with self._lock:
            with self._persistent_lock():
                records = self._load_records()
                match = next(
                    (
                        (key, record)
                        for key, record in records.items()
                        if record.handoff_id == normalized["id"]
                    ),
                    None,
                )
                if match is None:
                    raise ValueError("native handoff not found")
                identity_key, current = match
                if not _record_has_exact_delivery_binding(current):
                    raise ValueError("native handoff is not exact")
                if not _descriptor_matches(current, normalized):
                    raise ValueError("native handoff descriptor mismatch")
                if current.delivery_state == "acked":
                    return current, False
                if current.delivery_state != "pending":
                    raise ValueError("native handoff is not pending")
                if self._timestamp() > current.expires_at:
                    raise ValueError("native handoff is not pending")
                updated = replace(
                    current,
                    delivery_state="acked",
                    updated_at=self._timestamp(),
                )
                pending = dict(records)
                pending[identity_key] = updated
                self._write_records(pending, protected_key=identity_key)
                return updated, True

    def restore_delivery_fence_if_missing(
        self,
        identity_key: str,
        expected_record: NativeHandoffRecord,
    ) -> tuple[NativeHandoffRecord, bool]:
        """Atomically restore one exact, unexpired ACK delivery fence.

        The caller must supply a record previously returned by this store and
        retained only in process memory.  This method deliberately refuses to
        synthesize a replacement descriptor: restoring the original UUID seed
        is the only safe way to preserve delivery deduplication after the state
        file disappears.
        """

        _validate_identity_key(identity_key)
        _validate_restorable_delivery_fence(identity_key, expected_record)
        with self._lock:
            with self._persistent_lock():
                records = self._load_records()
                current = records.get(identity_key)
                if current is not None:
                    if not _same_delivery_fence(current, expected_record):
                        raise ValueError("native handoff fence conflicts with durable state")
                    if (
                        expected_record.delivery_state == "acked"
                        and current.delivery_state != "acked"
                    ):
                        raise ValueError("native handoff fence would roll back ACK state")
                    return current, False
                if self._timestamp() > expected_record.expires_at:
                    raise ValueError("native handoff fence is expired")
                pending = dict(records)
                pending[identity_key] = expected_record
                self._write_records(pending, protected_key=identity_key)
                return expected_record, True

    def expire_pending(self, identity_key: str) -> NativeHandoffRecord | None:
        _validate_identity_key(identity_key)
        with self._lock:
            with self._persistent_lock():
                records = self._load_records()
                current = records.get(identity_key)
                if (
                    current is None
                    or current.delivery_state != "pending"
                    or self._timestamp() <= current.expires_at
                ):
                    return current
                updated = replace(
                    current,
                    delivery_state="uncertain",
                    updated_at=self._timestamp(),
                )
                pending = dict(records)
                pending[identity_key] = updated
                self._write_records(pending, protected_key=identity_key)
                return updated

    def get_by_exact_binding(
        self,
        *,
        obligation_key: str,
        content_hash: str,
        plan_fingerprint: str,
        route: str,
        target_hash: str,
    ) -> NativeHandoffRecord | None:
        key = _validated_obligation_key(obligation_key, required=True)
        exact = _validated_exact_binding(
            content_hash,
            plan_fingerprint,
            route,
            target_hash,
            required=True,
        )
        with self._lock:
            with self._persistent_lock():
                matches = [
                    record
                    for record in self._load_records().values()
                    if (
                        record.obligation_key == key
                        and record.content_hash == exact[0]
                        and record.plan_fingerprint == exact[1]
                        and record.route == exact[2]
                        and record.target_hash == exact[3]
                    )
                ]
        return matches[0] if len(matches) == 1 else None

    def safe_status(self) -> dict[str, Any]:
        """Return bounded aggregate diagnostics without identifiers."""
        with self._lock:
            with self._persistent_lock():
                records = self._load_records()
        counts = {state: 0 for state in sorted(_VALID_DELIVERY_STATES)}
        for record in records.values():
            counts[record.delivery_state] += 1
        status = {
            "records": len(records),
            "delivery_states": counts,
            "manual_review_required": counts["uncertain"] > 0,
        }
        if counts["uncertain"] > 0:
            status["next_action"] = NATIVE_HANDOFF_MANUAL_REVIEW_ACTION
        return status

    def record_lifecycle_fence(
        self,
        identity_key: str,
        *,
        generation: str,
        event_created_at: float,
    ) -> str:
        _validate_identity_key(identity_key)
        bounded_generation = _validated_generation(generation, required=True)
        lifecycle_at = _finite_timestamp(event_created_at)
        with self._lock:
            with self._persistent_lock():
                records = self._load_records()
                current = records.get(identity_key)
                if current is not None:
                    if current.generation == bounded_generation:
                        return "same"
                    if lifecycle_at <= current.event_created_at:
                        return "stale"
                timestamp = self._timestamp()
                fence = NativeHandoffRecord(
                    card_state="none",
                    delivery_state="lifecycle",
                    card_message_hash="",
                    bot_hash="",
                    generation=bounded_generation,
                    handoff_id=_handoff_id(identity_key, bounded_generation),
                    uuid_seed="",
                    obligation_key="",
                    content_hash="",
                    plan_fingerprint="",
                    route="",
                    target_hash="",
                    created_at=timestamp,
                    updated_at=timestamp,
                    event_created_at=lifecycle_at,
                    expires_at=timestamp,
                )
                pending = dict(records)
                pending[identity_key] = fence
                self._write_records(pending, protected_key=identity_key)
                return "advanced"

    def clear(self, identity_key: str) -> bool:
        _validate_identity_key(identity_key)
        with self._lock:
            with self._persistent_lock():
                records = self._load_records()
                if identity_key not in records:
                    return False
                pending = dict(records)
                pending.pop(identity_key, None)
                self._write_records(pending)
                return True

    def prepare_lifecycle(
        self, identity_key: str, *, event_created_at: float,
        expected_record: NativeHandoffRecord | None = None,
    ) -> str:
        """Clear a tombstone only for a strictly newer lifecycle event."""

        _validate_identity_key(identity_key)
        lifecycle_at = _finite_timestamp(event_created_at)
        with self._lock:
            with self._persistent_lock():
                records = self._load_records()
                current = records.get(identity_key)
                if expected_record is not None and (
                    current is None or current.delivery_state != "legacy_consumed"
                    or not _same_delivery_fence(current, expected_record)
                ):
                    return "stale"
                if current is None:
                    return "absent"
                if lifecycle_at <= current.event_created_at:
                    return "stale"
                timestamp = self._timestamp()
                lifecycle_floor = NativeHandoffRecord(
                    card_state="none",
                    delivery_state="lifecycle",
                    card_message_hash="",
                    bot_hash="",
                    generation="",
                    handoff_id=_handoff_id(identity_key, ""),
                    uuid_seed="",
                    obligation_key="",
                    content_hash="",
                    plan_fingerprint="",
                    route="",
                    target_hash="",
                    created_at=timestamp,
                    updated_at=timestamp,
                    event_created_at=lifecycle_at,
                    expires_at=timestamp,
                )
                pending = dict(records)
                pending[identity_key] = lifecycle_floor
                self._write_records(pending, protected_key=identity_key)
                return "cleared"

    def _begin(
        self,
        identity_key: str,
        *,
        card_state: str,
        card_message_hash: str,
        bot_hash: str,
        event_created_at: float,
        generation: str,
        ack_capable: bool,
        obligation_key: str,
        content_hash: str,
        plan_fingerprint: str,
        route: str,
        target_hash: str,
        uuid_seed: str,
    ) -> tuple[NativeHandoffRecord, bool]:
        with self._lock:
            with self._persistent_lock():
                records = self._load_records()
                current = records.get(identity_key)
                if current is not None:
                    same_generation = current.generation == generation
                    if same_generation and current.delivery_state != "lifecycle":
                        current_is_exact = _record_has_exact_delivery_binding(current)
                        if ack_capable or current_is_exact:
                            if (
                                not ack_capable
                                or not current_is_exact
                                or current.obligation_key != obligation_key
                                or current.content_hash != content_hash
                                or current.plan_fingerprint != plan_fingerprint
                                or current.route != route
                                or current.target_hash != target_hash
                                or current.event_created_at != event_created_at
                            ):
                                raise ValueError(
                                    "native handoff delivery fence conflicts"
                                )
                        return current, False
                    if current.delivery_state == "lifecycle" and same_generation:
                        if event_created_at < current.event_created_at:
                            return current, False
                    elif event_created_at <= current.event_created_at:
                        return current, False
                timestamp = self._timestamp()
                delivery_state = "pending" if ack_capable else "legacy_consumed"
                record = NativeHandoffRecord(
                    card_state=card_state,
                    delivery_state=delivery_state,
                    card_message_hash=card_message_hash,
                    bot_hash=bot_hash,
                    generation=generation,
                    handoff_id=_handoff_id(identity_key, generation),
                    uuid_seed=uuid_seed if ack_capable else "",
                    obligation_key=obligation_key,
                    content_hash=content_hash,
                    plan_fingerprint=plan_fingerprint,
                    route=route,
                    target_hash=target_hash,
                    created_at=timestamp,
                    updated_at=timestamp,
                    event_created_at=event_created_at,
                    expires_at=(
                        timestamp + NATIVE_HANDOFF_TTL_SECONDS
                        if ack_capable
                        else timestamp
                    ),
                )
                pending = dict(records)
                pending[identity_key] = record
                self._write_records(pending, protected_key=identity_key)
                return record, True

    def _timestamp(self) -> float:
        try:
            value = float(self._now())
        except (TypeError, ValueError, OverflowError) as exc:
            raise NativeHandoffStoreError("handoff state clock is invalid") from exc
        if not math.isfinite(value) or value < 0:
            raise NativeHandoffStoreError("handoff state clock is invalid")
        return value

    @contextmanager
    def _persistent_lock(self) -> Iterator[None]:
        _prepare_private_root(self.root)
        local_lock = _process_lock_for(self.lock_path)
        with local_lock:
            descriptor = _open_private_lock_file(self.root, self.lock_path)
            locked = False
            try:
                _acquire_persistent_lock(descriptor)
                locked = True
                yield
            finally:
                try:
                    if locked:
                        _release_persistent_lock(descriptor)
                finally:
                    os.close(descriptor)

    def _load_records(self) -> dict[str, NativeHandoffRecord]:
        if _lstat(self.path) is None:
            return {}
        raw = _read_private_file(self.root, self.path, self.max_file_bytes)
        records = self._normalize_records(_decode_records(raw))
        # Bound every read as well as every write. Fresh active handoffs are
        # never silently discarded; if they alone exceed the configured
        # bound, surface an explicit operational error.
        self._evict_to_count(records, protected_key="")
        return records

    def _write_records(
        self,
        records: dict[str, NativeHandoffRecord],
        *,
        protected_key: str = "",
    ) -> None:
        pending = self._normalize_records(records)
        _validate_existing_private_file(self.root, self.path)
        self._evict_to_count(pending, protected_key=protected_key)
        payload = self._serialized_payload(pending)
        while len(payload) > self.max_file_bytes and len(pending) > 1:
            if not self._evict_one(pending, protected_key=protected_key):
                break
            payload = self._serialized_payload(pending)
        if len(payload) > self.max_file_bytes:
            raise NativeHandoffStoreError("handoff state exceeds bounded file size")
        _atomic_write_private(self.root, self.path, payload)

    def _normalize_records(
        self,
        records: dict[str, NativeHandoffRecord],
    ) -> dict[str, NativeHandoffRecord]:
        if not any(
            record.delivery_state == "pending" for record in records.values()
        ):
            return dict(records)
        now = self._timestamp()
        normalized: dict[str, NativeHandoffRecord] = {}
        for key, record in records.items():
            if record.delivery_state == "pending" and now > record.expires_at:
                record = replace(
                    record,
                    delivery_state="uncertain",
                    updated_at=now,
                )
            normalized[key] = record
        return normalized

    def _serialized_payload(
        self, values: dict[str, NativeHandoffRecord]
    ) -> bytes:
        records = {
            key: {
                "state": record.state,
                "card_state": record.card_state,
                "delivery_state": record.delivery_state,
                "card_message_hash": record.card_message_hash,
                "bot_hash": record.bot_hash,
                "generation": record.generation,
                "handoff_id": record.handoff_id,
                "uuid_seed": record.uuid_seed,
                "obligation_key": record.obligation_key,
                "content_hash": record.content_hash,
                "plan_fingerprint": record.plan_fingerprint,
                "route": record.route,
                "target_hash": record.target_hash,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "event_created_at": record.event_created_at,
                "expires_at": record.expires_at,
            }
            for key, record in sorted(values.items())
        }
        return (
            json.dumps(
                {"version": 4, "records": records},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

    def _evict_to_count(
        self,
        records: dict[str, NativeHandoffRecord],
        *,
        protected_key: str,
    ) -> None:
        while len(records) > self.max_records:
            if not self._evict_one(records, protected_key=protected_key):
                raise NativeHandoffStoreError("handoff state cannot be bounded")

    def _evict_one(
        self,
        records: dict[str, NativeHandoffRecord],
        *,
        protected_key: str,
    ) -> bool:
        now = self._timestamp()
        candidates: list[tuple[str, NativeHandoffRecord]] = []
        for key, record in records.items():
            if key == protected_key or record.delivery_state == "pending":
                continue
            age = now - record.updated_at
            if record.delivery_state == "uncertain":
                candidates.append((key, record))
                continue
            if record.delivery_state == "lifecycle":
                if age >= LIFECYCLE_FENCE_TTL_SECONDS:
                    candidates.append((key, record))
                continue
            if record.delivery_state == "acked":
                # The ACK is the dedupe fence for the same one-hour window as
                # the stable descriptor, independent of whether the optional
                # in-process card notice finished. Never evict it early, but
                # also never let an abandoned notice pin capacity forever.
                if now > record.expires_at:
                    candidates.append((key, record))
                continue
            if record.card_state == "pending":
                # Legacy hooks have no ACK descriptor. Preserve their terminal
                # fence for one bounded window, then allow an unrepairable
                # post-crash decorative notice to age out.
                if age >= LIFECYCLE_FENCE_TTL_SECONDS:
                    candidates.append((key, record))
                continue
            candidates.append((key, record))
        if not candidates:
            return False
        key, _ = min(
            candidates,
            key=lambda item: (
                item[1].updated_at,
                item[0],
            ),
        )
        records.pop(key, None)
        return True


def _decode_records(raw: bytes) -> dict[str, NativeHandoffRecord]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise NativeHandoffStoreError("handoff state is invalid") from exc
    if not isinstance(payload, dict) or payload.get("version") not in {1, 2, 3, 4}:
        raise NativeHandoffStoreError("handoff state is invalid")
    version = payload["version"]
    values = payload.get("records")
    if not isinstance(values, dict):
        raise NativeHandoffStoreError("handoff state is invalid")
    decoded: dict[str, NativeHandoffRecord] = {}
    for key, value in values.items():
        try:
            _validate_identity_key(key)
            decoded[key] = _decode_record(key, value, version=version)
        except (TypeError, ValueError, OverflowError) as exc:
            raise NativeHandoffStoreError("handoff state is invalid") from exc
    return decoded


def _validate_restorable_delivery_fence(
    identity_key: str,
    record: NativeHandoffRecord,
) -> None:
    if not isinstance(record, NativeHandoffRecord):
        raise ValueError("native handoff fence is not restorable")
    if record.delivery_state not in {"pending", "acked"}:
        raise ValueError("native handoff fence is not restorable")
    generation = _validated_generation(record.generation, required=True)
    if record.handoff_id != _handoff_id(identity_key, generation):
        raise ValueError("native handoff fence identity mismatch")
    if _UUID_SEED_RE.fullmatch(record.uuid_seed) is None:
        raise ValueError("native handoff fence UUID is invalid")
    if not record.has_exact_delivery_binding:
        raise ValueError("native handoff fence binding is invalid")
    _validated_obligation_key(record.obligation_key, required=True)
    _validated_exact_binding(
        record.content_hash,
        record.plan_fingerprint,
        record.route,
        record.target_hash,
        required=True,
    )
    if record.card_state not in _VALID_CARD_STATES:
        raise ValueError("native handoff fence card state is invalid")
    _validated_optional_hash(record.card_message_hash, "card_message_hash")
    _validated_optional_hash(record.bot_hash, "bot_hash")
    if record.card_state == "none" and (
        record.card_message_hash or record.bot_hash
    ):
        raise ValueError("native handoff fence contains invalid routing hashes")
    created_at = _finite_timestamp(record.created_at)
    updated_at = _finite_timestamp(record.updated_at)
    event_created_at = _finite_timestamp(record.event_created_at)
    expires_at = _finite_timestamp(record.expires_at)
    # ``event_created_at`` is supplied by Hermes and can come from a clock
    # with independent skew.  Its exact value is part of the fence match, but
    # comparing it to the sidecar's local TTL clock would reject a valid
    # already-persisted descriptor during duplicate handling.
    if updated_at < created_at:
        raise ValueError("native handoff fence timestamps are invalid")
    if expires_at != created_at + NATIVE_HANDOFF_TTL_SECONDS:
        raise ValueError("native handoff fence expiry is invalid")


def _same_delivery_fence(
    current: NativeHandoffRecord,
    expected: NativeHandoffRecord,
) -> bool:
    return (
        current.card_message_hash == expected.card_message_hash
        and current.bot_hash == expected.bot_hash
        and current.generation == expected.generation
        and current.handoff_id == expected.handoff_id
        and current.uuid_seed == expected.uuid_seed
        and current.obligation_key == expected.obligation_key
        and current.content_hash == expected.content_hash
        and current.plan_fingerprint == expected.plan_fingerprint
        and current.route == expected.route
        and current.target_hash == expected.target_hash
        and current.created_at == expected.created_at
        and current.event_created_at == expected.event_created_at
        and current.expires_at == expected.expires_at
    )


def _record_has_exact_delivery_binding(record: NativeHandoffRecord) -> bool:
    try:
        obligation_key = _validated_obligation_key(
            record.obligation_key,
            required=True,
        )
        content_hash, plan_fingerprint, route, target_hash = (
            _validated_exact_binding(
                record.content_hash,
                record.plan_fingerprint,
                record.route,
                record.target_hash,
                required=True,
            )
        )
        expected_seed = derive_native_handoff_uuid_seed(
            obligation_key=obligation_key,
            content_hash=content_hash,
            plan_fingerprint=plan_fingerprint,
            route=route,
            target_hash=target_hash,
        )
    except ValueError:
        return False
    return record.uuid_seed == expected_seed


def _decode_record(
    identity_key: str,
    value: Any,
    *,
    version: int,
) -> NativeHandoffRecord:
    if not isinstance(value, dict):
        raise ValueError("record must be an object")
    created_at = _finite_timestamp(value.get("created_at"))
    updated_at = _finite_timestamp(value.get("updated_at"))
    event_created_at = _finite_timestamp(value.get("event_created_at", created_at))
    if updated_at < created_at:
        raise ValueError("record timestamps are invalid")
    if version == 1:
        state = value.get("state")
        if state not in {"pending", "committed", "no_card", "lifecycle"}:
            raise ValueError("invalid record state")
        message_id = _bounded_identifier(
            value.get("feishu_message_id"),
            name="feishu_message_id",
            max_chars=_MAX_FEISHU_MESSAGE_ID_CHARS,
            required=state in {"pending", "committed"},
        )
        bot_id = _bounded_identifier(
            value.get("bot_id"),
            name="bot_id",
            max_chars=_MAX_BOT_ID_CHARS,
            required=False,
        )
        if state in {"no_card", "lifecycle"} and (message_id or bot_id):
            raise ValueError("record must not contain delivery identifiers")
        card_state = {
            "pending": "pending",
            "committed": "committed",
            "no_card": "none",
            "lifecycle": "none",
        }[state]
        return NativeHandoffRecord(
            card_state=card_state,
            delivery_state=(
                "lifecycle" if state == "lifecycle" else "legacy_consumed"
            ),
            card_message_hash=(
                _routing_hash("card-message", message_id) if message_id else ""
            ),
            bot_hash=_routing_hash("bot", bot_id) if bot_id else "",
            generation="",
            handoff_id=_handoff_id(identity_key, ""),
            uuid_seed="",
            obligation_key="",
            content_hash="",
            plan_fingerprint="",
            route="",
            target_hash="",
            created_at=created_at,
            updated_at=updated_at,
            event_created_at=event_created_at,
            expires_at=updated_at,
        )

    card_state = value.get("card_state")
    delivery_state = value.get("delivery_state")
    if (
        card_state not in _VALID_CARD_STATES
        or delivery_state not in _VALID_DELIVERY_STATES
    ):
        raise ValueError("invalid record state")
    if version < 4 and delivery_state == "pending":
        # A pre-V4 pending ACK lacks the target-bound contract. Keep a durable
        # manual-review fence, but never rewrite it as a current exact pending.
        delivery_state = "uncertain"
    exact_delivery_state = delivery_state in {
        "pending",
        "acked",
        "uncertain",
    }
    generation = _validated_generation(
        value.get("generation", ""),
        required=delivery_state == "pending" or exact_delivery_state,
    )
    handoff_id = _validated_hash(value.get("handoff_id"), "handoff_id")
    uuid_seed = str(value.get("uuid_seed") or "")
    if delivery_state == "pending":
        if _UUID_SEED_RE.fullmatch(uuid_seed) is None:
            raise ValueError("invalid uuid seed")
    elif uuid_seed and _UUID_SEED_RE.fullmatch(uuid_seed) is None:
        raise ValueError("invalid uuid seed")
    card_message_hash = _validated_optional_hash(
        value.get("card_message_hash", ""), "card_message_hash"
    )
    bot_hash = _validated_optional_hash(value.get("bot_hash", ""), "bot_hash")
    if version >= 4:
        obligation_key = _validated_obligation_key(
            value.get("obligation_key", "")
        )
        content_hash, plan_fingerprint, route, target_hash = _validated_exact_binding(
            value.get("content_hash", ""),
            value.get("plan_fingerprint", ""),
            value.get("route", ""),
            value.get("target_hash", ""),
            required=False,
        )
        has_exact_fields = bool(
            obligation_key
            or content_hash
            or plan_fingerprint
            or route
            or target_hash
        )
    else:
        obligation_key = ""
        content_hash, plan_fingerprint, route, target_hash = "", "", "", ""
        has_exact_fields = False
    valid_exact_record = exact_delivery_state and has_exact_fields
    if valid_exact_record:
        obligation_key = _validated_obligation_key(
            obligation_key,
            required=True,
        )
        content_hash, plan_fingerprint, route, target_hash = _validated_exact_binding(
            content_hash,
            plan_fingerprint,
            route,
            target_hash,
            required=True,
        )
        if handoff_id != _handoff_id(identity_key, generation):
            raise ValueError("invalid exact handoff id")
        expected_uuid_seed = derive_native_handoff_uuid_seed(
            obligation_key=obligation_key,
            content_hash=content_hash,
            plan_fingerprint=plan_fingerprint,
            route=route,
            target_hash=target_hash,
        )
        if uuid_seed != expected_uuid_seed:
            raise ValueError("invalid exact handoff uuid seed")
    elif not exact_delivery_state:
        # Non-exact V4 records are legacy fences. Never promote pseudo fields
        # from rolling payloads into exact authority.
        obligation_key = ""
        content_hash, plan_fingerprint, route, target_hash = "", "", "", ""
    expires_at = _finite_timestamp(value.get("expires_at", updated_at))
    if valid_exact_record and expires_at != created_at + NATIVE_HANDOFF_TTL_SECONDS:
        raise ValueError("invalid exact handoff expiry")
    if card_state == "none" and (card_message_hash or bot_hash):
        raise ValueError("card-free record contains routing hashes")
    return NativeHandoffRecord(
        card_state=card_state,
        delivery_state=delivery_state,
        card_message_hash=card_message_hash,
        bot_hash=bot_hash,
        generation=generation,
        handoff_id=handoff_id,
        uuid_seed=uuid_seed,
        obligation_key=obligation_key,
        content_hash=content_hash,
        plan_fingerprint=plan_fingerprint,
        route=route,
        target_hash=target_hash,
        created_at=created_at,
        updated_at=updated_at,
        event_created_at=event_created_at,
        expires_at=expires_at,
    )


def _validate_identity_key(value: Any) -> None:
    if not isinstance(value, str) or _IDENTITY_KEY_RE.fullmatch(value) is None:
        raise ValueError("identity key must be a SHA-256 digest")


def _validated_generation(value: Any, *, required: bool) -> str:
    if not isinstance(value, str):
        raise ValueError("generation must be a string")
    normalized = value.strip().lower()
    if not normalized:
        if required:
            raise ValueError("generation is required")
        return ""
    if _GENERATION_RE.fullmatch(normalized) is None:
        raise ValueError("generation is invalid")
    return normalized


def _validated_obligation_key(value: Any, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("obligation key must be a string")
    normalized = value.strip().lower()
    if not normalized:
        if required:
            raise ValueError("obligation key is required")
        return ""
    if _HASH_RE.fullmatch(normalized) is None:
        raise ValueError("obligation key is invalid")
    return normalized


def _validated_exact_binding(
    content_hash: Any,
    plan_fingerprint: Any,
    route: Any,
    target_hash: Any,
    *,
    required: bool,
) -> tuple[str, str, str, str]:
    normalized_content_hash = _validated_optional_hash(content_hash, "content_hash")
    normalized_plan_fingerprint = _validated_optional_hash(
        plan_fingerprint, "plan_fingerprint"
    )
    if not isinstance(route, str):
        raise ValueError("route must be a string")
    normalized_route = route.strip().lower()
    normalized_target_hash = _validated_optional_hash(target_hash, "target_hash")
    present = (
        bool(normalized_content_hash),
        bool(normalized_plan_fingerprint),
        bool(normalized_route),
        bool(normalized_target_hash),
    )
    if any(present) and not all(present):
        raise ValueError("exact native handoff binding is incomplete")
    if required and not all(present):
        raise ValueError("exact native handoff binding is required")
    if normalized_route and normalized_route not in _VALID_EXACT_ROUTES:
        raise ValueError("route is invalid")
    return (
        normalized_content_hash,
        normalized_plan_fingerprint,
        normalized_route,
        normalized_target_hash,
    )


def _validated_provisional_uuid_seed(
    value: Any,
    *,
    obligation_key: str,
    content_hash: str,
    plan_fingerprint: str,
    route: str,
    target_hash: str,
    required: bool,
) -> str:
    if not required:
        return ""
    expected = derive_native_handoff_uuid_seed(
        obligation_key=obligation_key,
        content_hash=content_hash,
        plan_fingerprint=plan_fingerprint,
        route=route,
        target_hash=target_hash,
    )
    if value in {None, ""}:
        return expected
    if not isinstance(value, str):
        raise ValueError("provisional UUID seed is invalid")
    normalized = value.strip().lower()
    if _UUID_SEED_RE.fullmatch(normalized) is None or normalized != expected:
        raise ValueError("provisional UUID seed does not match exact binding")
    return normalized


def _validated_hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _validated_optional_hash(value: Any, name: str) -> str:
    if value == "":
        return ""
    return _validated_hash(value, name)


def _routing_hash(domain: str, value: str) -> str:
    return hashlib.sha256(
        f"hfc-native-handoff-{domain}-v1\0{value}".encode("utf-8")
    ).hexdigest()


def _handoff_id(identity_key: str, generation: str) -> str:
    return hashlib.sha256(
        f"hfc-native-handoff-id-v1\0{identity_key}\0{generation}".encode("ascii")
    ).hexdigest()


def _validated_descriptor(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "protocol",
        "id",
        "uuid_seed",
        "expires_at",
    }:
        raise ValueError("native handoff descriptor is invalid")
    if value.get("protocol") != NATIVE_HANDOFF_PROTOCOL:
        raise ValueError("native handoff descriptor is invalid")
    handoff_id = value.get("id")
    uuid_seed = value.get("uuid_seed")
    if not isinstance(handoff_id, str) or _HANDOFF_ID_RE.fullmatch(handoff_id) is None:
        raise ValueError("native handoff descriptor is invalid")
    if not isinstance(uuid_seed, str) or _UUID_SEED_RE.fullmatch(uuid_seed) is None:
        raise ValueError("native handoff descriptor is invalid")
    return {
        "protocol": NATIVE_HANDOFF_PROTOCOL,
        "id": handoff_id,
        "uuid_seed": uuid_seed,
        "expires_at": _finite_timestamp(value.get("expires_at")),
    }


def _descriptor_matches(
    record: NativeHandoffRecord,
    descriptor: dict[str, Any],
) -> bool:
    return (
        descriptor["protocol"] == NATIVE_HANDOFF_PROTOCOL
        and descriptor["id"] == record.handoff_id
        and descriptor["uuid_seed"] == record.uuid_seed
        and descriptor["expires_at"] == record.expires_at
    )


def _bounded_identifier(
    value: Any,
    *,
    name: str,
    max_chars: int,
    required: bool,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > max_chars or any(ord(char) < 0x20 for char in normalized):
        raise ValueError(f"{name} is invalid")
    return normalized


def _finite_timestamp(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("timestamp is invalid")
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("timestamp is invalid") from exc
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("timestamp is invalid")
    return timestamp


def _prepare_private_root(root: Path) -> None:
    root = root.absolute()
    _reject_symlink_components(root, allow_missing=True)
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise NativeHandoffStoreError("handoff state directory could not be prepared") from exc
    _reject_symlink_components(root, allow_missing=False)
    metadata = _lstat(root)
    if metadata is None or not stat.S_ISDIR(metadata.st_mode):
        raise NativeHandoffStoreError("handoff state directory is invalid")
    _require_current_owner(metadata, "handoff state directory")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o700:
        try:
            root.chmod(0o700)
        except OSError as exc:
            raise NativeHandoffStoreError("handoff state directory permissions are invalid") from exc
        metadata = _lstat(root)
        if metadata is None or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise NativeHandoffStoreError("handoff state directory permissions are invalid")


def _reject_symlink_components(path: Path, *, allow_missing: bool) -> None:
    absolute = path.absolute()
    parts = absolute.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        metadata = _lstat(current)
        if metadata is None:
            if allow_missing:
                return
            raise NativeHandoffStoreError("handoff state directory is missing")
        if stat.S_ISLNK(metadata.st_mode):
            raise NativeHandoffStoreError("handoff state path contains a symbolic link")
        if current != absolute and not stat.S_ISDIR(metadata.st_mode):
            raise NativeHandoffStoreError("handoff state parent is not a directory")


def _validate_existing_private_file(root: Path, path: Path) -> None:
    metadata = _lstat(path)
    if metadata is None:
        return
    if stat.S_ISLNK(metadata.st_mode):
        raise NativeHandoffStoreError("handoff state file must not be a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise NativeHandoffStoreError("handoff state file is invalid")
    _require_current_owner(metadata, "handoff state file")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600:
        raise NativeHandoffStoreError("handoff state file permissions must be 0600")
    root_metadata = _lstat(root)
    if root_metadata is None or not stat.S_ISDIR(root_metadata.st_mode):
        raise NativeHandoffStoreError("handoff state directory is invalid")


def _process_lock_for(path: Path) -> threading.RLock:
    key = str(path.absolute())
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


def _open_private_lock_file(root: Path, path: Path) -> int:
    _validate_existing_private_file(root, path)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise NativeHandoffStoreError("handoff state lock could not be opened") from exc
    try:
        opened = os.fstat(descriptor)
        current = _lstat(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or current is None
            or stat.S_ISLNK(current.st_mode)
            or current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino
        ):
            raise NativeHandoffStoreError("handoff state lock is invalid")
        _require_current_owner(opened, "handoff state lock")
        if os.name != "nt":
            if stat.S_IMODE(opened.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)
            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                raise NativeHandoffStoreError(
                    "handoff state lock permissions must be 0600"
                )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _acquire_persistent_lock(descriptor: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(descriptor, _fcntl.LOCK_EX)
        return
    if _msvcrt is not None:
        # msvcrt locks a byte range from the current file position.  Ensure a
        # real byte exists so independent Windows processes contend on the
        # same range, then use its blocking lock mode.
        if os.fstat(descriptor).st_size < 1:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        _msvcrt.locking(descriptor, _msvcrt.LK_LOCK, 1)
        return
    raise NativeHandoffStoreError("persistent file locking is unavailable")


def _release_persistent_lock(descriptor: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(descriptor, _fcntl.LOCK_UN)
        return
    if _msvcrt is not None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        _msvcrt.locking(descriptor, _msvcrt.LK_UNLCK, 1)
        return
    raise NativeHandoffStoreError("persistent file locking is unavailable")


def _read_private_file(root: Path, path: Path, max_file_bytes: int) -> bytes:
    _validate_existing_private_file(root, path)
    before = _lstat(path)
    if before is None:
        return b""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NativeHandoffStoreError("handoff state file could not be opened") from exc
    try:
        opened = os.fstat(descriptor)
        current = _lstat(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or current is None
            or before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino
        ):
            raise NativeHandoffStoreError("handoff state file changed while opening")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(max_file_bytes + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > max_file_bytes:
        raise NativeHandoffStoreError("handoff state exceeds bounded file size")
    return raw


def _atomic_write_private(root: Path, path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(root)
    )
    temporary = Path(temporary_name)
    try:
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "wb")
        except Exception:
            os.close(descriptor)
            raise
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _validate_existing_private_file(root, path)
        os.replace(temporary, path)
        if os.name != "nt":
            current = _lstat(path)
            if current is None or stat.S_IMODE(current.st_mode) != 0o600:
                path.chmod(0o600)
        _fsync_directory(root)
    except NativeHandoffStoreError:
        raise
    except OSError as exc:
        raise NativeHandoffStoreError("handoff state could not be written atomically") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _require_current_owner(metadata: os.stat_result, label: str) -> None:
    getuid = getattr(os, "getuid", None)
    if callable(getuid) and metadata.st_uid != getuid():
        raise NativeHandoffStoreError(f"{label} is not owned by the current user")


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise NativeHandoffStoreError("handoff state path could not be inspected") from exc
