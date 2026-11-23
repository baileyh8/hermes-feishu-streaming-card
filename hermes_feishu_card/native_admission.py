"""Native terminal admission using raw event identity and existing durable fences.

This synchronous boundary owns only admission/dedup decisions. It does not own
HTTP, policy, session mutation, repair scheduling, rendering, or Feishu I/O.
Call it under the existing owner lock, before policy; legacy lifecycle clearing
must remain after policy in the coordinator. Store operations remain individually
atomic, not a new transaction across multiple calls.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal, Protocol

from .event_rules import (
    TERMINAL_EVENTS, delivery_kind, event_is_terminal, is_intentional_silence_event, is_native_fallback_event,
)
from .events import SidecarEvent
from .native_handoff import (
    NativeHandoffRecord,
    NativeHandoffStoreError,
    derive_native_handoff_content_hash,
    derive_native_handoff_target_hash,
    derive_native_handoff_uuid_seed,
    handoff_identity_key,
    is_exact_native_text_scope,
)


class AdmissionStore(Protocol):
    def record_lifecycle_fence(
        self, identity_key: str, *, generation: str, event_created_at: float,
    ) -> str: ...

    def get(self, identity_key: str) -> NativeHandoffRecord | None: ...

    def restore_delivery_fence_if_missing(
        self, identity_key: str, expected_record: NativeHandoffRecord,
    ) -> tuple[NativeHandoffRecord, bool]: ...

    def expire_pending(self, identity_key: str) -> NativeHandoffRecord | None: ...


@dataclass(frozen=True, repr=False)
class NativeTerminalSnapshot:
    native_terminal: bool = False
    cached_record: NativeHandoffRecord | None = None
    allow_direct_terminal_start: bool = False


AdmissionKind = Literal[
    "continue", "ignore_lifecycle", "suppress_terminal", "use_record", "start_legacy_terminal", "reject",
]
AdmissionReason = Literal[
    "lifecycle_unavailable", "stale_lifecycle", "read_unavailable",
    "cache_mismatch", "restore_unavailable", "stale_generation", "stale_replay",
    "incomplete_binding", "binding_mismatch", "expire_unavailable", "missing_fence",
]


@dataclass(frozen=True, repr=False)
class NativeAdmission:
    kind: AdmissionKind
    reason: AdmissionReason | None = None
    lifecycle_fence_recorded: bool = False
    cached_record_update: NativeHandoffRecord | None = None
    fence_restored: bool = False
    selected_record: NativeHandoffRecord | None = None

    @property
    def fence_refused(self) -> bool:
        return self.reason in {
            "read_unavailable", "cache_mismatch", "restore_unavailable",
            "incomplete_binding", "binding_mismatch", "missing_fence",
        }


def native_handoff_identity(event: SidecarEvent) -> str:
    data = event.data if isinstance(event.data, dict) else {}
    raw_profile_id = data.get("profile_id")
    profile_id = raw_profile_id.strip() if isinstance(raw_profile_id, str) else ""
    return handoff_identity_key(
        profile_id=profile_id,
        chat_id=event.chat_id,
        conversation_id=event.conversation_id,
        message_id=event.canonical_turn_id,
    )


def native_handoff_metadata(event: SidecarEvent) -> dict[str, Any]:
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


def native_handoff_ack_capable(
    ack_verifier_available: bool,
    metadata: dict[str, Any],
    event: SidecarEvent,
) -> bool:
    capabilities = set(metadata.get("capabilities") or ())
    complete = (
        ack_verifier_available
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


def native_terminal_matches_cached_handoff(
    event: SidecarEvent,
    metadata: dict[str, Any],
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


def native_terminal_matches_durable_handoff(
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


def event_starts_new_lifecycle(event: SidecarEvent) -> bool:
    if event.event == "message.started":
        return True
    if event_is_terminal(event):
        return False
    data = event.data if isinstance(event.data, dict) else {}
    return data.get("lifecycle_start") is True or data.get("new_lifecycle") is True


def admit_native_handoff(
    incoming_event: SidecarEvent,
    owner: NativeTerminalSnapshot,
    *,
    ack_verifier_available: bool,
    store: AdmissionStore,
) -> NativeAdmission:
    """Decide admission without substituting reply aliases for canonical turns.

    ``ack_verifier_available`` is a capability fact, not authentication proof;
    the coordinator must already have authenticated the original event.
    A restore cache update survives any subsequent refusal or pending expiry.
    """
    identity = native_handoff_identity(incoming_event)
    metadata = native_handoff_metadata(incoming_event)
    generation = str(metadata.get("generation") or "")
    starts_lifecycle = event_starts_new_lifecycle(incoming_event)
    lifecycle_recorded = False
    if starts_lifecycle and generation:
        try:
            fence = store.record_lifecycle_fence(
                identity, generation=generation,
                event_created_at=incoming_event.created_at,
            )
        except (NativeHandoffStoreError, OSError, ValueError):
            return NativeAdmission("reject", "lifecycle_unavailable")
        if fence == "stale":
            return NativeAdmission("ignore_lifecycle", "stale_lifecycle")
        lifecycle_recorded = fence in {"advanced", "same"}

    if not event_is_terminal(incoming_event) or starts_lifecycle:
        return NativeAdmission("continue", lifecycle_fence_recorded=lifecycle_recorded)

    try:
        prior = store.get(identity)
    except (NativeHandoffStoreError, OSError, ValueError):
        return NativeAdmission("reject", "read_unavailable")

    cache_update = None
    restored = False
    cached = owner.cached_record if owner.native_terminal else None
    if cached is not None and cached.delivery_state in {"pending", "acked"}:
        if not native_terminal_matches_cached_handoff(incoming_event, metadata, cached):
            return NativeAdmission("reject", "cache_mismatch")
        try:
            prior, restored = store.restore_delivery_fence_if_missing(identity, cached)
        except (NativeHandoffStoreError, OSError, ValueError):
            return NativeAdmission("reject", "restore_unavailable")
        cache_update = prior

    def result(
        kind: AdmissionKind, reason: AdmissionReason | None = None,
        record: NativeHandoffRecord | None = None,
    ) -> NativeAdmission:
        return NativeAdmission(
            kind, reason, lifecycle_recorded, cache_update, restored, record,
        )

    if prior is not None:
        if (owner.allow_direct_terminal_start and not incoming_event.turn_id
                and incoming_event.event in TERMINAL_EVENTS and delivery_kind(incoming_event) in {"", "chat"}
                and incoming_event.data.get("policy_new_turn") is True
                and not is_intentional_silence_event(incoming_event) and not is_native_fallback_event(incoming_event)):
            # A consumed legacy handoff proves delivery for its own turn only.
            # A newer marked terminal can be the first event of the next turn.
            # The coordinator advances this record only after checking policy.
            if (prior.delivery_state == "legacy_consumed" and not prior.has_exact_delivery_binding
                    and incoming_event.created_at > prior.event_created_at
                    and not (generation and generation == prior.generation)):
                return result("start_legacy_terminal", record=prior)
            if (prior.delivery_state == "lifecycle" and not prior.generation
                    and incoming_event.created_at == prior.event_created_at):
                return result("start_legacy_terminal", record=prior)
        if generation and prior.generation != generation:
            if incoming_event.created_at > prior.event_created_at:
                prior = None
            else:
                return result("suppress_terminal", "stale_generation")
        if prior is not None and prior.state == "lifecycle":
            if (
                generation and prior.generation == generation
            ) or incoming_event.created_at >= prior.event_created_at:
                prior = None
            else:
                return result("suppress_terminal", "stale_replay")
        if prior is not None:
            incoming_exact = native_handoff_ack_capable(
                ack_verifier_available, metadata, incoming_event,
            )
            durable_claimed = bool(
                prior.delivery_state in {"pending", "acked", "uncertain"}
                or any((
                    prior.uuid_seed, prior.obligation_key, prior.content_hash,
                    prior.plan_fingerprint, prior.route, prior.target_hash,
                ))
            )
            durable_exact = prior.has_exact_delivery_binding
            if durable_claimed and not durable_exact:
                return result("reject", "incomplete_binding")
            if (incoming_exact or durable_exact) and (
                not incoming_exact
                or not durable_exact
                or not native_terminal_matches_durable_handoff(incoming_event, metadata, prior)
            ):
                return result("reject", "binding_mismatch")
            try:
                prior = store.expire_pending(identity)
            except (NativeHandoffStoreError, OSError, ValueError):
                prior = None
            if prior is None:
                return result("reject", "expire_unavailable")
            return result("use_record", record=prior)

    if owner.native_terminal:
        # An in-memory terminal without a restorable durable fence cannot
        # authorize a replacement UUID. Hermes retains the remaining path.
        return result("reject", "missing_fence")
    return result("continue")
