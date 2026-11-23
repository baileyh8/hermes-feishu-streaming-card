"""Native admission keeps lifecycle fences, cache restoration and expiry distinct."""
import copy
from dataclasses import replace

import pytest

from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.native_admission import NativeTerminalSnapshot, admit_native_handoff, native_handoff_identity
from hermes_feishu_card.native_handoff import NativeHandoffRecord, derive_native_handoff_content_hash, derive_native_handoff_target_hash, derive_native_handoff_uuid_seed


def exact_event(kind="message.completed", created_at=100.0, generation="a" * 32, **data):
    binding = {"obligation_key": "b" * 64, "content_hash": derive_native_handoff_content_hash("COMPLETE_ANSWER"), "plan_fingerprint": "c" * 64, "route": "create", "target_hash": derive_native_handoff_target_hash(profile_id="default", chat_id="fixture-chat", thread_id="", route="create")}
    metadata = {**binding, "generation": generation, "provisional_uuid_seed": derive_native_handoff_uuid_seed(**binding), "capabilities": ["native-ack-v2", "stable-feishu-uuid-v2", "exact-base-delivery-v1"]}
    return SidecarEvent.from_dict(dict(schema_version="1", event=kind, sequence=2, conversation_id="fixture-conversation", message_id="reply-alias-must-not-be-identity", turn_id="canonical-turn", chat_id="fixture-chat", platform="feishu", created_at=created_at, data={"profile_id": "default", "answer": "COMPLETE_ANSWER", "attachments": [], "native_delivery": "allowed", "native_handoff": metadata, **data}))


def exact_record(value):
    metadata = value.data["native_handoff"]
    return NativeHandoffRecord(card_state="committed", delivery_state="pending", card_message_hash="d" * 64, bot_hash="e" * 64, generation=metadata["generation"], handoff_id="f" * 64, uuid_seed=metadata["provisional_uuid_seed"], obligation_key=metadata["obligation_key"], content_hash=metadata["content_hash"], plan_fingerprint=metadata["plan_fingerprint"], route=metadata["route"], target_hash=metadata["target_hash"], created_at=100.0, updated_at=100.0, event_created_at=value.created_at, expires_at=200.0)


class RecordingStore:
    def __init__(self, prior=None, restored=None, expired=None, fence="advanced", fail=None):
        self.prior, self.restored, self.expired = prior, restored, expired
        self.fence, self.fail, self.calls = fence, fail, []

    def call(self, operation, identity):
        self.calls.append((operation, identity))
        if operation == self.fail:
            raise OSError("synthetic store fault")

    def record_lifecycle_fence(self, identity, **kwargs):
        self.call("lifecycle", identity)
        return self.fence

    def get(self, identity):
        self.call("get", identity)
        return self.prior

    def restore_delivery_fence_if_missing(self, identity, cached):
        self.call("restore", identity)
        return self.restored, True

    def expire_pending(self, identity):
        self.call("expire", identity)
        return self.expired


@pytest.mark.parametrize("fence,expected_kind,recorded", [("advanced", "continue", True), ("same", "continue", True), ("stale", "ignore_lifecycle", False)])
@pytest.mark.parametrize("event_kind,data", [("message.started", {}), ("answer.delta", {"new_lifecycle": True})])
def test_lifecycle_is_fenced_before_other_store_work(fence, expected_kind, recorded, event_kind, data):
    value, store = exact_event(event_kind, **data), RecordingStore(fence=fence)
    result = admit_native_handoff(value, NativeTerminalSnapshot(), ack_verifier_available=True, store=store)
    assert result.kind == expected_kind
    assert result.lifecycle_fence_recorded is recorded
    assert store.calls == [("lifecycle", native_handoff_identity(value))]


def test_ordinary_nonterminal_never_touches_store():
    store = RecordingStore()
    result = admit_native_handoff(exact_event("answer.delta"), NativeTerminalSnapshot(), ack_verifier_available=True, store=store)
    assert result.kind == "continue" and not store.calls


@pytest.mark.parametrize("created_at,expected", [(99.0, "suppress_terminal"), (100.0, "suppress_terminal"), (101.0, "continue")])
def test_new_generation_requires_strictly_newer_event_time(created_at, expected):
    prior = exact_record(exact_event(generation="b" * 32))
    store = RecordingStore(prior=prior)
    result = admit_native_handoff(exact_event(created_at=created_at), NativeTerminalSnapshot(), ack_verifier_available=True, store=store)
    assert result.kind == expected
    assert [call[0] for call in store.calls] == ["get"]


@pytest.mark.parametrize("created_at,expected", [(99.0, "suppress_terminal"), (100.0, "continue"), (101.0, "continue")])
def test_legacy_event_lifecycle_timestamp_fence_includes_equal_boundary(created_at, expected):
    prior = replace(exact_record(exact_event()), delivery_state="lifecycle")
    store = RecordingStore(prior=prior)
    value = exact_event(created_at=created_at, native_handoff={})
    result = admit_native_handoff(value, NativeTerminalSnapshot(), ack_verifier_available=True, store=store)
    assert result.kind == expected
    if created_at < 100:
        assert result.reason == "stale_replay"
    assert [call[0] for call in store.calls] == ["get"]


def test_exact_restore_updates_cache_before_expiry_but_returns_post_expiry_record():
    value = exact_event()
    cached = exact_record(value)
    expired = replace(cached, delivery_state="uncertain", updated_at=201.0)
    store = RecordingStore(restored=cached, expired=expired)
    owner = NativeTerminalSnapshot(True, cached)
    before = copy.deepcopy(owner)
    result = admit_native_handoff(value, owner, ack_verifier_available=True, store=store)
    assert result.kind == "use_record" and result.fence_restored
    assert result.cached_record_update is cached
    assert result.selected_record is expired
    assert result.cached_record_update.delivery_state == "pending"
    assert owner == before
    assert store.calls == [(operation, native_handoff_identity(value)) for operation in ("get", "restore", "expire")]


@pytest.mark.parametrize("refusal", ["incomplete_binding", "binding_mismatch", "expire_unavailable"])
def test_successful_restore_cache_update_survives_later_refusal(refusal):
    value = exact_event()
    cached = exact_record(value)
    restored = replace(cached, plan_fingerprint="") if refusal == "incomplete_binding" else replace(cached, event_created_at=101) if refusal == "binding_mismatch" else cached
    store = RecordingStore(restored=restored)
    result = admit_native_handoff(value, NativeTerminalSnapshot(True, cached), ack_verifier_available=True, store=store)
    assert (result.kind, result.reason) == ("reject", refusal)
    assert result.cached_record_update is restored and result.fence_restored
    assert result.selected_record is None
    assert [call[0] for call in store.calls] == ["get", "restore"] + (["expire"] if refusal == "expire_unavailable" else [])


@pytest.mark.parametrize("operation,reason", [("get", "read_unavailable"), ("restore", "restore_unavailable"), ("expire", "expire_unavailable")])
def test_store_faults_stop_later_operations_without_reauthorizing_delivery(operation, reason):
    value = exact_event()
    cached = exact_record(value)
    store = RecordingStore(prior=cached, restored=cached, expired=cached, fail=operation)
    result = admit_native_handoff(value, NativeTerminalSnapshot(True, cached), ack_verifier_available=True, store=store)
    assert (result.kind, result.reason) == ("reject", reason)
    calls = ["get", "restore", "expire"]
    assert [call[0] for call in store.calls] == calls[:calls.index(operation) + 1]
    assert (result.cached_record_update is cached) is (operation == "expire")


def test_mismatched_cached_generation_never_attempts_restore_or_expiry():
    value = exact_event()
    cached = replace(exact_record(value), generation="b" * 32)
    store = RecordingStore()
    result = admit_native_handoff(value, NativeTerminalSnapshot(True, cached), ack_verifier_available=True, store=store)
    assert (result.kind, result.reason) == ("reject", "cache_mismatch")
    assert [call[0] for call in store.calls] == ["get"]


def test_missing_durable_fence_cannot_replace_terminal_owner_uuid():
    result = admit_native_handoff(exact_event(), NativeTerminalSnapshot(True), ack_verifier_available=True, store=RecordingStore())
    assert (result.kind, result.reason) == ("reject", "missing_fence")
    assert result.fence_refused


def test_exact_record_requires_ack_verifier_and_uses_canonical_turn_identity():
    value = exact_event()
    record = exact_record(value)
    store = RecordingStore(prior=record)
    result = admit_native_handoff(value, NativeTerminalSnapshot(), ack_verifier_available=False, store=store)
    assert (result.kind, result.reason) == ("reject", "binding_mismatch")
    aliased = replace(value, message_id="another-reply-alias")
    assert native_handoff_identity(aliased) == native_handoff_identity(value)
    assert store.calls == [("get", native_handoff_identity(value))]


@pytest.mark.parametrize("generation", ["a" * 32, "b" * 32])
def test_marked_direct_terminal_preserves_an_exact_pending_handoff(generation):
    original = replace(exact_event(), turn_id="")
    prior = exact_record(original)
    incoming = replace(exact_event(created_at=101, generation=generation, policy_new_turn=True), turn_id="")
    owner = NativeTerminalSnapshot(native_terminal=True, cached_record=prior, allow_direct_terminal_start=True)
    store = RecordingStore(prior=prior)

    result = admit_native_handoff(incoming, owner, ack_verifier_available=True, store=store)

    assert (result.kind, result.reason) == ("reject", "cache_mismatch")
    assert store.calls == [("get", native_handoff_identity(incoming))]
