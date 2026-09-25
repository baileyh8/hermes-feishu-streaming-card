from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from threading import Barrier, Event, Thread, current_thread

import pytest

import hermes_feishu_card.hermes_plugin_runtime as plugin_runtime
from hermes_feishu_card import profile_sources

from hermes_feishu_card.hermes_plugin_runtime import (
    IngressBinding,
    IngressBindingRegistry,
    TurnEventCoordinator,
    TurnState,
    register_callbacks,
    reset_plugin_runtime_state,
)
from tests.fixtures.hermes_v020_plugin_api import PluginContext


def binding(
    generation="generation-a",
    *,
    profile_id="default",
    profile_source="fallback_default",
    session_id="session-1",
    gateway_session_key="gateway-session-1",
    expires_at=200.0,
):
    return IngressBinding(
        profile_id=profile_id,
        profile_source=profile_source,
        session_id=session_id,
        gateway_session_key=gateway_session_key,
        generation=generation,
        chat_id="oc_1",
        incoming_message_id="om_1",
        reply_to_message_id="om_1",
        thread_id="",
        expires_at=expires_at,
    )


class AcceptedDict(dict):
    pass


class PretendsDelivered:
    def __eq__(self, other):
        return other == "delivered"


class PretendsKey:
    def __init__(self, target):
        self.target = target

    def __hash__(self):
        return hash(self.target)

    def __eq__(self, other):
        return other == self.target


class StringSubclass(str):
    pass


class FloatSubclass(float):
    pass


class IntSubclass(int):
    pass


def test_new_generation_replaces_old_ingress_binding():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding("generation-a")) is True
    assert registry.bind(binding("generation-b")) is True
    assert registry.claim("default", "session-1", "generation-a", "turn-old") is None
    turn = registry.claim("default", "session-1", "generation-b", "turn-new")
    assert turn is not None
    assert turn.turn_id == "turn-new"


def test_claim_requires_exact_unique_binding_and_never_uses_recent_chat():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.claim("default", "missing", "generation-a", "turn-1") is None
    assert registry.bind(binding()) is True
    assert registry.claim("default", "session-1", "other", "turn-1") is None
    assert registry.claim("default", "session-1", "generation-a", "") is None
    assert registry.claim("default", "session-1", "generation-a", "turn-1") is not None
    assert registry.claim("default", "session-1", "generation-a", "turn-2") is None


def test_official_pre_llm_claims_only_one_unambiguous_agent_session():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding()) is True
    turn = registry.claim_unique_session("session-1", "turn-1")
    assert turn is not None
    assert turn.turn_id == "turn-1"
    assert turn.ingress.gateway_session_key == "gateway-session-1"
    assert turn.ingress.profile_source == "fallback_default"
    assert registry.claim_unique_session("session-1", "turn-2") is None


@pytest.mark.parametrize("profile_source", ("env", "locals", "hermes_home", "fallback_default"))
def test_bind_and_unique_claim_preserve_allowed_profile_source(profile_source):
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding(profile_source=profile_source)) is True
    turn = registry.claim_unique_session("session-1", "turn-1")
    assert turn is not None
    assert turn.ingress.profile_source == profile_source


def test_official_pre_llm_refuses_ambiguity_without_consuming_either_binding():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding(profile_id="profile-a")) is True
    assert registry.bind(binding(profile_id="profile-b")) is True
    assert registry.claim_unique_session("session-1", "turn-1") is None
    assert registry.claim("profile-a", "session-1", "generation-a", "turn-a") is not None
    assert registry.claim("profile-b", "session-1", "generation-a", "turn-b") is not None


def test_expiry_resolves_ambiguous_agent_session_before_unique_claim():
    now = [100.0]
    registry = IngressBindingRegistry(now=lambda: now[0])
    assert registry.bind(binding(profile_id="profile-a", expires_at=101.0)) is True
    assert registry.bind(binding(profile_id="profile-b", expires_at=200.0)) is True
    assert registry.claim_unique_session("session-1", "turn-early") is None
    now[0] = 101.0
    turn = registry.claim_unique_session("session-1", "turn-after-expiry")
    assert turn is not None
    assert turn.ingress.profile_id == "profile-b"


def test_concurrent_unique_session_claims_consume_exactly_once():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding()) is True
    barrier = Barrier(16)

    def claim(index):
        barrier.wait()
        return registry.claim_unique_session("session-1", f"turn-{index}")

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(claim, range(16)))

    claimed = [turn for turn in results if turn is not None]
    assert len(claimed) == 1
    assert claimed[0].turn_id.startswith("turn-")


@pytest.mark.parametrize(
    ("session_id", "turn_id"),
    (
        ("", "turn-1"),
        ("  ", "turn-1"),
        (StringSubclass("session-1"), "turn-1"),
        (1, "turn-1"),
        ("session-1", ""),
        ("session-1", StringSubclass("turn-1")),
        ("session-1", None),
    ),
)
def test_unique_session_claim_rejects_nonordinary_or_blank_identities_without_consuming(
    session_id, turn_id
):
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding()) is True
    assert registry.claim_unique_session(session_id, turn_id) is None
    assert registry.claim_unique_session("session-1", "turn-valid") is not None


@pytest.mark.parametrize("invalid_value", ("", StringSubclass("session-1"), None))
def test_invalid_unique_session_claim_still_prunes_expired_bindings(invalid_value):
    now = [100.0]
    registry = IngressBindingRegistry(now=lambda: now[0])
    assert registry.bind(binding(expires_at=101.0)) is True
    now[0] = 101.0
    assert registry.claim_unique_session(invalid_value, "turn-1") is None
    now[0] = 100.0
    assert registry.claim_unique_session("session-1", "turn-valid") is None


def test_started_transitions_only_on_explicit_sidecar_acceptance():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    registry.bind(binding())
    turn = registry.claim("default", "session-1", "generation-a", "turn-1")
    assert turn.state is TurnState.PENDING_START
    assert turn.record_started_result({"ok": True, "applied": False}) is TurnState.NATIVE_BYPASS
    assert turn.record_started_result({"ok": True, "applied": True}) is TurnState.NATIVE_BYPASS


def test_started_requires_boolean_true_not_integer_values():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    for result in (
        {"ok": 1, "applied": True},
        {"ok": True, "applied": 1},
    ):
        registry.bind(binding())
        turn = registry.claim("default", "session-1", "generation-a", "turn-1")
        assert turn.record_started_result(result) is TurnState.NATIVE_BYPASS


def test_started_accepts_exact_delivered_sidecar_response():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding()) is True
    turn = registry.claim("default", "session-1", "generation-a", "turn-1")
    result = {
        "ok": True,
        "applied": True,
        "delivery": {"outcome": "delivered"},
    }
    assert turn.record_started_result(result) is TurnState.CARD_ACTIVE


@pytest.mark.parametrize(
    "result",
    (
        AcceptedDict(ok=True, applied=True),
        AcceptedDict(
            ok=True,
            applied=True,
            delivery={"outcome": "delivered"},
        ),
        {
            "ok": True,
            "applied": True,
            "delivery": AcceptedDict(outcome="delivered"),
        },
        {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered", "extra": False},
        },
        {"ok": True, "applied": True, "delivery": {}},
        {"ok": True, "applied": True, "delivery": {"outcome": 1}},
        {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "unknown"},
        },
        {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": PretendsDelivered()},
        },
        {PretendsKey("ok"): True, "applied": True},
        {"ok": True, PretendsKey("applied"): True},
        {
            "ok": True,
            "applied": True,
            PretendsKey("delivery"): {"outcome": "delivered"},
        },
        {
            "ok": True,
            "applied": True,
            "delivery": {PretendsKey("outcome"): "delivered"},
        },
        {"ok": True, "applied": True, "extra": False},
        {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered"},
            "extra": False,
        },
    ),
    ids=(
        "top-level-dict-subclass-two-key",
        "top-level-dict-subclass-with-delivery",
        "delivery-dict-subclass",
        "delivery-extra-key",
        "delivery-missing-outcome",
        "delivery-non-string-outcome",
        "delivery-non-delivered-outcome",
        "delivery-equality-spoofed-outcome",
        "top-level-spoofed-ok-key",
        "top-level-spoofed-applied-key",
        "top-level-spoofed-delivery-key",
        "delivery-spoofed-outcome-key",
        "top-level-unknown-key",
        "top-level-unknown-key-with-delivery",
    ),
)
def test_started_rejects_non_allowlisted_sidecar_responses(result):
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding()) is True
    turn = registry.claim("default", "session-1", "generation-a", "turn-1")
    assert turn.record_started_result(result) is TurnState.NATIVE_BYPASS


def test_card_active_turn_becomes_terminal_once_and_rejects_late_events():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    registry.bind(binding())
    turn = registry.claim("default", "session-1", "generation-a", "turn-1")
    assert turn.record_started_result({"ok": True, "applied": True}) is TurnState.CARD_ACTIVE
    assert turn.finish() is True
    assert turn.finish() is False
    assert turn.state is TurnState.TERMINAL
    assert turn.accepts_observer_events is False
    assert turn.record_started_result({"ok": True, "applied": True}) is TurnState.TERMINAL


def test_finish_is_atomic_when_many_threads_finish_one_active_turn():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding()) is True
    turn = registry.claim("default", "session-1", "generation-a", "turn-1")
    assert turn.record_started_result({"ok": True, "applied": True}) is TurnState.CARD_ACTIVE
    barrier = Barrier(16)

    def finish_at_once():
        barrier.wait()
        return turn.finish()

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(lambda _: finish_at_once(), range(16)))

    assert results.count(True) == 1
    assert turn.state is TurnState.TERMINAL


def test_started_result_racing_finish_cannot_reopen_terminal():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding()) is True
    turn = registry.claim("default", "session-1", "generation-a", "turn-1")
    barrier = Barrier(2)

    def record_started():
        barrier.wait()
        return turn.record_started_result({"ok": True, "applied": True})

    def finish():
        barrier.wait()
        return turn.finish()

    with ThreadPoolExecutor(max_workers=2) as executor:
        started = executor.submit(record_started)
        finished = executor.submit(finish)
        started.result()
        assert finished.result() is True

    assert turn.state is TurnState.TERMINAL


def test_bind_rejects_blank_identity_and_expired_bindings():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding(profile_id="")) is False
    assert registry.bind(binding(generation="  ")) is False
    assert registry.bind(binding(expires_at=100.0)) is False
    assert registry.claim("default", "session-1", "generation-a", "turn-1") is None


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("profile_id", ""),
        ("session_id", "  "),
        ("gateway_session_key", ""),
        ("generation", StringSubclass("generation-a")),
        ("chat_id", StringSubclass("oc_1")),
        ("incoming_message_id", 1),
        ("reply_to_message_id", None),
        ("thread_id", StringSubclass("")),
    ),
)
def test_bind_rejects_blank_nonstring_or_string_subclass_identity_fields(
    field_name, invalid_value
):
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(replace(binding(), **{field_name: invalid_value})) is False


@pytest.mark.parametrize(
    "profile_source",
    (
        "",
        "unknown",
        StringSubclass("env"),
        "sanitized_env",
        "sanitized_locals",
        "sanitized_hermes_home",
        "sanitized_fallback_default",
    ),
)
def test_bind_rejects_unverified_profile_sources_without_consuming_valid_binding(
    profile_source
):
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding()) is True
    assert registry.bind(
        binding(generation="generation-b", profile_source=profile_source)
    ) is False
    turn = registry.claim_unique_session("session-1", "turn-1")
    assert turn is not None
    assert turn.ingress.profile_source == "fallback_default"


def test_ingress_registry_consumes_the_authoritative_trusted_profile_sources():
    assert (
        IngressBindingRegistry._PROFILE_SOURCES
        is profile_sources.TRUSTED_PROFILE_SOURCES
    )


def test_invalid_profile_source_bind_still_prunes_expired_bindings():
    now = [100.0]
    registry = IngressBindingRegistry(now=lambda: now[0])
    assert registry.bind(binding(expires_at=101.0)) is True
    now[0] = 101.0
    assert registry.bind(binding(profile_source="sanitized_env")) is False
    now[0] = 100.0
    assert registry.claim_unique_session("session-1", "turn-1") is None


@pytest.mark.parametrize("expires_at", (True, float("nan"), float("inf"), -float("inf"), "200", None))
def test_bind_rejects_non_numeric_or_nonfinite_expiry(expires_at):
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding(expires_at=expires_at)) is False


@pytest.mark.parametrize(
    "expires_at",
    (FloatSubclass(200.0), IntSubclass(200), 10**10000),
    ids=("float-subclass", "int-subclass", "huge-int"),
)
def test_bind_rejects_numeric_subclasses_and_overflowing_expiry(expires_at):
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding(expires_at=expires_at)) is False


def test_invalid_bind_still_prunes_expired_bindings():
    now = [100.0]
    registry = IngressBindingRegistry(now=lambda: now[0])
    assert registry.bind(binding(expires_at=101.0)) is True
    now[0] = 101.0
    assert registry.bind(binding(gateway_session_key="")) is False
    now[0] = 100.0
    assert registry.claim("default", "session-1", "generation-a", "turn-1") is None


def test_bind_rejects_ingress_binding_subclasses():
    class DerivedIngressBinding(IngressBinding):
        pass

    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(DerivedIngressBinding(**binding().__dict__)) is False


@pytest.mark.parametrize(
    ("profile_id", "session_id", "generation", "turn_id"),
    (
        (StringSubclass("default"), "session-1", "generation-a", "turn-1"),
        ("default", StringSubclass("session-1"), "generation-a", "turn-1"),
        ("default", "session-1", StringSubclass("generation-a"), "turn-1"),
        ("default", "session-1", "generation-a", StringSubclass("turn-1")),
    ),
)
def test_explicit_claim_rejects_string_subclasses_without_consuming(
    profile_id, session_id, generation, turn_id
):
    registry = IngressBindingRegistry(now=lambda: 100.0)
    assert registry.bind(binding()) is True
    assert registry.claim(profile_id, session_id, generation, turn_id) is None
    assert registry.claim("default", "session-1", "generation-a", "turn-valid") is not None


def test_invalid_explicit_claim_still_prunes_expired_bindings():
    now = [100.0]
    registry = IngressBindingRegistry(now=lambda: now[0])
    assert registry.bind(binding(expires_at=101.0)) is True
    now[0] = 101.0
    assert registry.claim("", "session-1", "generation-a", "turn-1") is None
    now[0] = 100.0
    assert registry.claim("default", "session-1", "generation-a", "turn-valid") is None


def test_expired_binding_is_pruned_before_claim():
    now = [100.0]
    registry = IngressBindingRegistry(now=lambda: now[0])
    assert registry.bind(binding(expires_at=101.0)) is True
    now[0] = 101.0
    assert registry.claim("default", "session-1", "generation-a", "turn-1") is None


def test_registry_evicts_oldest_binding_when_capacity_is_exceeded():
    registry = IngressBindingRegistry(now=lambda: 100.0)
    for index in range(1025):
        assert registry.bind(binding(profile_id=f"profile-{index}")) is True
    assert registry.claim("profile-0", "session-1", "generation-a", "turn-0") is None
    assert registry.claim("profile-1", "session-1", "generation-a", "turn-1") is not None
    assert registry.claim("profile-1024", "session-1", "generation-a", "turn-last") is not None


def test_reset_clears_module_state_without_breaking_callback_registration():
    reset_plugin_runtime_state()
    context = PluginContext()
    assert register_callbacks(context) is None
    assert set(context.registered) == {
        "pre_llm_call", "post_llm_call", "on_session_end",
        "on_session_reset", "on_session_finalize", "pre_tool_call",
        "post_tool_call", "pre_approval_request", "post_approval_response",
        "subagent_start", "subagent_stop",
    }
    assert reset_plugin_runtime_state() is None


def test_event_ids_match_the_public_deterministic_contract():
    coordinator = TurnEventCoordinator("turn-1", max_pending=2)
    assert coordinator.event_id("started") == "turn:turn-1:started"
    assert coordinator.event_id("tool", item_id="call-1", phase="started") == "tool:turn-1:call-1:started"
    assert coordinator.event_id("approval", item_id="fp-1", phase="terminal") == "approval:turn-1:fp-1:terminal"
    assert coordinator.event_id("subagent", item_id="child-1", phase="started") == "subagent:turn-1:child-1:started"
    assert coordinator.event_id("completed") == "turn:turn-1:completed"
    assert coordinator.event_id("failed") == "turn:turn-1:failed"


def test_event_identity_rejects_blank_or_invalid_public_values():
    import pytest

    with pytest.raises(ValueError, match="turn"):
        TurnEventCoordinator("  ")
    coordinator = TurnEventCoordinator("turn-1")
    with pytest.raises(ValueError, match="identity"):
        coordinator.event_id("unknown")
    with pytest.raises(ValueError, match="identity"):
        coordinator.event_id("tool", phase="started")
    with pytest.raises(ValueError, match="phase"):
        coordinator.event_id("tool", item_id="call-1", phase="updated")


def test_plugin_and_patch_share_one_monotonic_sequence():
    coordinator = TurnEventCoordinator("turn-1", max_pending=4)
    assert coordinator.next_sequence("plugin") == 0
    assert coordinator.next_sequence("patch") == 1
    assert coordinator.next_sequence("plugin") == 2


def test_unknown_producer_is_rejected_at_sequence_and_submission_boundaries():
    import pytest

    coordinator = TurnEventCoordinator("turn-1")
    with pytest.raises(ValueError, match="producer"):
        coordinator.next_sequence("unknown")
    with pytest.raises(ValueError, match="producer"):
        coordinator.submit_observer({"event": "tool.updated"}, producer="unknown")


def test_nonpositive_max_pending_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="max_pending"):
        TurnEventCoordinator("turn-1", max_pending=0)
    with pytest.raises(ValueError, match="max_pending"):
        TurnEventCoordinator("turn-1", max_pending=-1)


def test_terminal_barrier_rejects_late_items_and_terminal_is_after_accepted_items():
    coordinator = TurnEventCoordinator("turn-1", max_pending=4, start_worker=False)
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is True
    assert coordinator.submit_observer({"event": "subagent.updated"}, producer="plugin") is True
    barrier = coordinator.close_terminal_barrier()
    assert barrier == 1
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is False
    assert coordinator.next_terminal_sequence() == 2


def test_terminal_sequence_is_reused_without_advancing_shared_sequence_on_retry():
    coordinator = TurnEventCoordinator("turn-1", start_worker=False)
    coordinator.close_terminal_barrier()
    assert coordinator.next_terminal_sequence() == 0
    assert coordinator.next_terminal_sequence() == 0
    assert coordinator.next_sequence("patch") == 1


def test_concurrent_terminal_sequence_retries_all_reuse_one_value():
    coordinator = TurnEventCoordinator("turn-1", start_worker=False)
    coordinator.close_terminal_barrier()
    start = Barrier(8)

    def get_terminal_sequence():
        start.wait()
        return coordinator.next_terminal_sequence()

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(lambda _: get_terminal_sequence(), range(8)))

    assert sequences == [0] * 8
    assert coordinator.next_sequence("plugin") == 1


def test_queue_full_drops_observer_work_without_raising_and_can_leave_sequence_gap():
    coordinator = TurnEventCoordinator("turn-1", max_pending=1, start_worker=False)
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is True
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is False
    assert coordinator.next_sequence("patch") == 2


def test_worker_delivery_exception_always_marks_work_done_and_drain_returns():
    delivery_started = Event()

    def fail_delivery(_event):
        delivery_started.set()
        raise RuntimeError("delivery failed")

    coordinator = TurnEventCoordinator("turn-1", deliver=fail_delivery)
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is True
    assert delivery_started.wait(timeout=0.5)
    coordinator.drain_before_terminal(timeout_seconds=0.5)
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is False


def test_drain_timeout_closes_admission_without_waiting_for_an_unstarted_worker():
    coordinator = TurnEventCoordinator("turn-1", start_worker=False)
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is True
    coordinator.drain_before_terminal(timeout_seconds=0)
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is False


def test_drain_race_never_returns_after_accepting_undrained_observer_work():
    import queue

    class DrainGateQueue(queue.Queue):
        def __init__(self):
            super().__init__(maxsize=1)
            self.drain_checked_empty = Event()
            self.release_drain_check = Event()
            self._first_drain_check = True

        @property
        def unfinished_tasks(self):
            if current_thread().name == "drainer" and self._first_drain_check:
                self._first_drain_check = False
                self.drain_checked_empty.set()
                assert self.release_drain_check.wait(timeout=0.5)
            return self._unfinished_tasks

        @unfinished_tasks.setter
        def unfinished_tasks(self, value):
            self._unfinished_tasks = value

    coordinator = TurnEventCoordinator("turn-1", max_pending=1, start_worker=False)
    gate = DrainGateQueue()
    coordinator._queue = gate
    drain_returned = Event()
    submit_finished = Event()
    result = []

    def drain():
        coordinator.drain_before_terminal(timeout_seconds=0)
        drain_returned.set()

    def submit():
        result.append(coordinator.submit_observer({"event": "tool.updated"}, producer="plugin"))
        submit_finished.set()

    drainer = Thread(target=drain, name="drainer")
    drainer.start()
    assert gate.drain_checked_empty.wait(timeout=0.5)
    submitter = Thread(target=submit, name="submitter")
    submitter.start()
    accepted_before_drain_close = submit_finished.wait(timeout=0.5)
    gate.release_drain_check.set()
    drainer.join(timeout=0.5)
    submitter.join(timeout=0.5)
    assert drain_returned.is_set()
    assert submit_finished.is_set()
    assert not (accepted_before_drain_close and gate.unfinished_tasks)
    assert result == [False]


def test_concurrent_barrier_and_submit_never_accept_an_event_after_barrier():
    coordinator = TurnEventCoordinator("turn-1", max_pending=4, start_worker=False)
    start = Barrier(2)

    def submit():
        start.wait()
        return coordinator.submit_observer({"event": "tool.updated"}, producer="plugin")

    def close_barrier():
        start.wait()
        return coordinator.close_terminal_barrier()

    with ThreadPoolExecutor(max_workers=2) as executor:
        submit_future = executor.submit(submit)
        barrier_future = executor.submit(close_barrier)
        accepted = submit_future.result()
        barrier = barrier_future.result()

    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is False
    assert (barrier == 0) is accepted


def test_close_is_idempotent_and_does_not_wait_for_a_blocked_daemon_delivery():
    delivery_started = Event()
    release_delivery = Event()

    def block_delivery(_event):
        delivery_started.set()
        assert release_delivery.wait(timeout=0.5)

    coordinator = TurnEventCoordinator("turn-1", deliver=block_delivery)
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is True
    assert delivery_started.wait(timeout=0.5)
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(coordinator.close).result(timeout=0.25) is None
    assert coordinator.close() is None
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin") is False
    release_delivery.set()
    coordinator.drain_before_terminal(timeout_seconds=0.5)


def task4_runtime(posted, *, responses=None, now=None, max_pending=64):
    queued_responses = list(responses or ())

    def post(payload, timeout_seconds):
        posted.append(payload)
        if queued_responses:
            response = queued_responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return {"ok": True, "applied": True}

    runtime = plugin_runtime.PluginRuntime(
        post=post,
        now=now or (lambda: 100.0),
        max_pending_observers=max_pending,
    )
    assert runtime.bind_ingress_from_values(
        "default",
        "fallback_default",
        "session-1",
        "gateway-session-1",
        "generation-1",
        "oc_1",
        "om_1",
        "om_parent",
        "thread-1",
    ) is True
    return runtime


def active_task4_runtime(posted, **kwargs):
    runtime = task4_runtime(posted, **kwargs)
    assert runtime.handle_pre_llm_call(
        session_id="session-1",
        task_id="task-1",
        turn_id="turn-1",
        platform="feishu",
        user_message="USER-CANARY",
        conversation_history=[{"content": "HISTORY-CANARY"}],
        sender_id="SENDER-CANARY",
        telemetry_schema_version="future-1",
    ) is None
    assert runtime.turn_state("turn-1") is TurnState.CARD_ACTIVE
    posted.clear()
    return runtime


def test_task4_real_pre_llm_kwargs_without_generation_build_honest_started_payload():
    posted = []
    runtime = task4_runtime(posted)

    assert runtime.handle_pre_llm_call(
        session_id="session-1",
        task_id="task-1",
        turn_id="turn-1",
        platform="feishu",
        user_message="USER-CANARY",
        conversation_history=[{"content": "HISTORY-CANARY"}],
        sender_id="SENDER-CANARY",
        telemetry_schema_version="future-1",
    ) is None

    assert posted == [
        {
            "schema_version": "1",
            "event": "message.started",
            "conversation_id": "thread-1",
            "message_id": "om_1",
            "chat_id": "oc_1",
            "thread_id": "thread-1",
            "platform": "feishu",
            "turn_id": "turn-1",
            "sequence": 0,
            "created_at": 100.0,
            "event_id": "turn:turn-1:started",
            "producer": "plugin",
            "phase": "started",
            "data": {
                "profile_id": "default",
                "profile_source": "fallback_default",
                "reply_to_message_id": "om_parent",
                "execution_scope": sha256(b"gateway-session-1").hexdigest(),
            },
        }
    ]
    assert runtime.turn_state("turn-1") is TurnState.CARD_ACTIVE
    assert "USER-CANARY" not in repr(runtime)
    assert "HISTORY-CANARY" not in repr(runtime)
    assert "SENDER-CANARY" not in repr(runtime)


def test_task4_started_unknown_retries_exact_payload_but_rejection_does_not_retry():
    retried = []
    runtime = task4_runtime(
        retried,
        responses=[TimeoutError("unavailable"), {"ok": True, "applied": True}],
    )
    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-1", platform="feishu"
    )
    assert len(retried) == 2
    assert retried[0] == retried[1]
    assert runtime.turn_state("turn-1") is TurnState.CARD_ACTIVE

    rejected = []
    runtime = task4_runtime(
        rejected,
        responses=[{"ok": True, "applied": False, "disposition": "native"}],
    )
    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-2", platform="feishu"
    )
    assert len(rejected) == 1
    assert runtime.turn_state("turn-2") is TurnState.NATIVE_BYPASS


def test_task4_post_llm_is_cache_only_and_failed_end_ignores_nonempty_explanation():
    posted = []
    runtime = active_task4_runtime(posted)

    assert runtime.handle_post_llm_call(
        session_id="session-1",
        turn_id="turn-1",
        assistant_response="FAILURE-EXPLANATION-CANARY",
        user_message="USER-CANARY",
        conversation_history=[{"content": "HISTORY-CANARY"}],
    ) is None
    assert posted == []
    assert runtime.handle_on_session_end(
        session_id="session-1",
        turn_id="turn-1",
        completed=False,
        failed=True,
        interrupted=False,
        turn_exit_reason="local_processing_error(PRIVATE-TRACE-CANARY)",
    ) is None

    assert len(posted) == 1
    assert posted[0]["event"] == "message.failed"
    assert posted[0]["event_id"] == "turn:turn-1:failed"
    assert posted[0]["data"] == {
        "error": "消息处理失败",
        "turn_exit_reason": "runtime_error",
    }
    assert "FAILURE-EXPLANATION-CANARY" not in repr(posted)
    assert "PRIVATE-TRACE-CANARY" not in repr(posted)


@pytest.mark.parametrize("completed,reason", [
    (False, "budget_exhausted"),
    (True, "max_iterations_reached(10/10)"),
    (False, "unknown"),
])
def test_native_incomplete_end_never_reports_success_or_leaves_card_running(completed, reason):
    posted = []
    runtime = active_task4_runtime(posted)
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="partial answer")
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=completed, failed=False,
        interrupted=False, turn_exit_reason=reason,
    )
    assert len(posted) == 1
    assert posted[0]["event"] == "message.failed"
    assert "未报告执行完成" in posted[0]["data"]["error"]
    runtime.handle_on_session_end(turn_id="turn-1", completed=True, failed=False, interrupted=False)
    assert len(posted) == 1


def test_task4_only_literal_success_flags_with_exact_cached_answer_complete():
    posted = []
    runtime = active_task4_runtime(posted)
    answer = "exact final answer"
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response=answer)

    assert runtime.handle_on_session_end(
        session_id="session-1",
        turn_id="turn-1",
        completed=True,
        failed=False,
        interrupted=False,
        turn_exit_reason="text_response(finish_reason=stop)",
    ) is None

    assert posted == [
        {
            "schema_version": "1",
            "event": "message.completed",
            "conversation_id": "thread-1",
            "message_id": "om_1",
            "chat_id": "oc_1",
            "thread_id": "thread-1",
            "platform": "feishu",
            "turn_id": "turn-1",
            "sequence": 1,
            "created_at": 100.0,
            "event_id": "turn:turn-1:completed",
            "producer": "plugin",
            "phase": "terminal",
            "data": {"answer": answer},
        }
    ]
    assert runtime.take_terminal_disposition("turn-1") == {
        "ok": True,
        "applied": True,
    }
    assert runtime.take_terminal_disposition("turn-1") is None


@pytest.mark.parametrize(
    ("completed", "failed", "interrupted"),
    (
        (1, False, False),
        (True, 0, False),
        (True, False, 0),
        ("true", False, False),
        (True, "false", False),
        (True, False, "false"),
    ),
)
def test_task4_nonliteral_terminal_flags_never_guess_completion(
    completed, failed, interrupted
):
    posted = []
    runtime = active_task4_runtime(posted)
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        turn_id="turn-1",
        completed=completed,
        failed=failed,
        interrupted=interrupted,
    )
    assert posted == []
    assert runtime.take_terminal_disposition("turn-1") is None


def test_task4_official_tool_kwargs_drop_every_raw_canary_and_map_statuses():
    posted = []
    runtime = active_task4_runtime(posted)
    runtime.handle_pre_tool_call(
        tool_name="shell",
        args={"secret": "ARGS-CANARY"},
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
        turn_id="turn-1",
        api_request_id="API-CANARY",
        middleware_trace=[{"raw": "TRACE-CANARY"}],
        detail="DETAIL-CANARY",
    )
    runtime.handle_post_tool_call(
        tool_name="shell",
        args={"secret": "ARGS-CANARY"},
        result={"body": "RESULT-CANARY"},
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
        turn_id="turn-1",
        api_request_id="API-CANARY",
        duration_ms=9,
        status="error",
        error_type="ERROR-TYPE-CANARY",
        error_message="ERROR-MESSAGE-CANARY",
        middleware_trace=[{"raw": "TRACE-CANARY"}],
    )
    runtime.drain_observers(1.0)

    assert [payload["data"] for payload in posted] == [
        {"tool_id": "call-1", "call_id": "call-1", "name": "shell", "status": "pending"},
        {
            "tool_id": "call-1",
            "call_id": "call-1",
            "name": "shell",
            "status": "failed",
            "duration_ms": 9,
        },
    ]
    serialized = repr(posted)
    for canary in (
        "ARGS-CANARY",
        "RESULT-CANARY",
        "API-CANARY",
        "ERROR-TYPE-CANARY",
        "ERROR-MESSAGE-CANARY",
        "TRACE-CANARY",
        "DETAIL-CANARY",
    ):
        assert canary not in serialized


@pytest.mark.parametrize(
    ("official", "safe"),
    (
        ("ok", "completed"),
        ("error", "failed"),
        ("blocked", "blocked"),
        ("timeout", "timeout"),
        ("cancelled", "cancelled"),
        ("canceled", "cancelled"),
        ("unknown", "failed"),
    ),
)
def test_task4_tool_status_mapping_never_guesses_unknown_success(official, safe):
    posted = []
    runtime = active_task4_runtime(posted)
    runtime.handle_post_tool_call(
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="shell",
        status=official,
    )
    runtime.drain_observers(1.0)
    assert posted[0]["data"]["status"] == safe


def test_task4_approval_five_part_key_prevents_cross_consumption():
    posted = []
    runtime = active_task4_runtime(posted)
    for tool_call_id in ("approval-call-1", "approval-call-2"):
        runtime.handle_pre_approval_request(
            session_key="gateway-session-1",
            turn_id="turn-1",
            tool_call_id=tool_call_id,
            command="  git   status  ",
            description="DESCRIPTION-CANARY",
            surface="gateway",
        )
    runtime.drain_observers(1.0)

    assert runtime.take_pending_approval(
        "gateway-session-1",
        "turn-1",
        "approval-call-1",
        "git status",
        "gateway",
    ) is not None
    assert runtime.take_pending_approval(
        "gateway-session-1",
        "turn-1",
        "approval-call-1",
        "git status",
        "gateway",
    ) is None
    assert runtime.take_pending_approval(
        "gateway-session-1",
        "turn-1",
        "approval-call-2",
        "git status",
        "gateway",
    ) is not None
    serialized = repr(posted)
    assert "git status" not in serialized
    assert "DESCRIPTION-CANARY" not in serialized


def test_task4_same_command_two_tool_calls_have_distinct_event_ids():
    posted = []
    runtime = active_task4_runtime(posted)
    for tool_call_id in ("approval-call-1", "approval-call-2"):
        runtime.handle_pre_approval_request(
            session_key="gateway-session-1",
            turn_id="turn-1",
            tool_call_id=tool_call_id,
            command="git status",
            surface="gateway",
        )
    runtime.drain_observers(1.0)
    assert len(posted) == 2
    assert posted[0]["event_id"] != posted[1]["event_id"]
    assert "approval-call-1" in posted[0]["event_id"]
    assert "approval-call-2" in posted[1]["event_id"]


def test_task4_take_claim_is_one_shot_but_official_post_still_closes_interaction():
    posted = []
    runtime = active_task4_runtime(posted)
    exact = {
        "session_key": "gateway-session-1",
        "turn_id": "turn-1",
        "tool_call_id": "approval-call-1",
        "command": "git status",
        "surface": "gateway",
    }
    runtime.handle_pre_approval_request(**exact)
    assert runtime.take_pending_approval(*exact.values()) is not None
    assert runtime.take_pending_approval(*exact.values()) is None
    runtime.handle_post_approval_response(**exact, choice="once")
    runtime.drain_observers(1.0)
    assert [payload["event"] for payload in posted] == [
        "interaction.requested",
        "interaction.completed",
    ]
    assert posted[0]["data"]["interaction_id"] == posted[1]["data"]["interaction_id"]


def test_hybrid_owned_approval_keeps_official_hooks_observer_only_without_second_ui():
    posted = []
    runtime = active_task4_runtime(posted)
    command = "  git   status  "
    fingerprint = sha256(b"git status").hexdigest()
    interaction_id = f"approval:turn-1:approval-call-1:{fingerprint[:16]}"
    handle = object()
    values = (
        "approval",
        "gateway-session-1",
        "turn-1",
        interaction_id,
        fingerprint,
        handle,
    )
    assert runtime.register_patch_interaction(*values) is True
    state = next(iter(runtime._patch_interactions.values()))
    state.hfc_owned = True

    official = {
        "session_key": "gateway-session-1",
        "turn_id": "turn-1",
        "tool_call_id": "approval-call-1",
        "command": command,
        "surface": "gateway",
    }
    runtime.handle_pre_approval_request(**official)
    runtime.handle_post_approval_response(**official, choice="once")
    runtime.drain_observers(1.0)

    assert posted == []


def test_hybrid_approval_ui_ownership_is_exact_not_turn_wide():
    posted = []
    runtime = active_task4_runtime(posted)
    handle = object()
    first_fingerprint = sha256(b"git status").hexdigest()
    first_interaction_id = (
        f"approval:turn-1:approval-call-1:{first_fingerprint[:16]}"
    )
    values = (
        "approval",
        "gateway-session-1",
        "turn-1",
        first_interaction_id,
        first_fingerprint,
        handle,
    )
    assert runtime.register_patch_interaction(*values) is True
    state = next(iter(runtime._patch_interactions.values()))
    state.hfc_owned = True

    runtime.handle_pre_approval_request(
        session_key="gateway-session-1",
        turn_id="turn-1",
        tool_call_id="approval-call-2",
        command="git diff",
        surface="gateway",
    )
    runtime.drain_observers(1.0)

    assert [payload["event"] for payload in posted] == ["interaction.requested"]


@pytest.mark.parametrize(
    "overrides",
    (
        {"session_key": "wrong"},
        {"turn_id": "wrong"},
        {"tool_call_id": ""},
        {"surface": "cli"},
    ),
)
def test_task4_approval_mismatch_and_missing_ids_are_noop(overrides):
    posted = []
    runtime = active_task4_runtime(posted)
    values = {
        "session_key": "gateway-session-1",
        "turn_id": "turn-1",
        "tool_call_id": "approval-call-1",
        "command": "git status",
        "surface": "gateway",
    }
    values.update(overrides)
    runtime.handle_pre_approval_request(**values)
    runtime.drain_observers(1.0)
    assert posted == []


def test_task4_subagent_stop_reuses_start_id_and_drops_tool_history():
    posted = []
    runtime = active_task4_runtime(posted)
    runtime.handle_subagent_start(
        parent_session_id="session-1",
        parent_turn_id="turn-1",
        parent_subagent_id="parent",
        child_session_id="child-session",
        child_subagent_id="child-1",
        child_role="research",
        child_goal=("safe goal " * 100) + "GOAL-CANARY",
    )
    runtime.handle_subagent_stop(
        parent_session_id="session-1",
        parent_turn_id="turn-1",
        child_session_id="child-session",
        child_role="research",
        child_summary=("safe summary " * 100) + "SUMMARY-CANARY",
        child_status="completed",
        tool_call_history=[{"secret": "TOOL-HISTORY-CANARY"}],
        duration_ms=4,
    )
    runtime.drain_observers(1.0)

    assert [payload["data"]["child_id"] for payload in posted] == [
        "child-1",
        "child-1",
    ]
    assert len(posted[0]["data"]["goal_preview"]) <= 240
    assert len(posted[1]["data"]["summary_preview"]) <= 240
    assert "TOOL-HISTORY-CANARY" not in repr(posted)


def test_task4_concurrent_session_end_has_one_terminal_owner_and_take_once():
    posted = []
    runtime = active_task4_runtime(posted)
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    start = Barrier(8)

    def end_once(_index):
        start.wait()
        return runtime.handle_on_session_end(
            turn_id="turn-1", completed=True, failed=False, interrupted=False
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(end_once, range(8))) == [None] * 8

    assert [payload["event"] for payload in posted] == ["message.completed"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        dispositions = list(
            executor.map(lambda _: runtime.take_terminal_disposition("turn-1"), range(2))
        )
    assert dispositions.count({"ok": True, "applied": True}) == 1
    assert dispositions.count(None) == 1


def test_task4_reset_uses_old_session_and_finalize_uses_exact_closing_session():
    posted = []
    runtime = active_task4_runtime(posted)
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="ANSWER-CANARY")

    runtime.handle_on_session_reset(
        session_id="new-session",
        old_session_id="wrong-old-session",
        new_session_id="new-session",
    )
    assert runtime.turn_state("turn-1") is TurnState.CARD_ACTIVE
    runtime.handle_on_session_finalize(session_id="session-1")
    assert runtime.turn_state("turn-1") is None
    assert "ANSWER-CANARY" not in repr(runtime)


def test_task4_module_callbacks_dispatch_dynamically_and_always_return_none():
    posted = []
    runtime = task4_runtime(posted)
    plugin_runtime.configure_plugin_runtime(runtime)
    context = PluginContext()
    register_callbacks(context)
    try:
        for name, callback in context.registered.items():
            assert callback(telemetry_schema_version="future-1") is None, name
    finally:
        reset_plugin_runtime_state()
    assert posted == []


def test_task4_platform_equality_spoof_cannot_claim_ingress():
    class PretendsFeishu:
        def __eq__(self, other):
            return other == "feishu"

    posted = []
    runtime = task4_runtime(posted)
    runtime.handle_pre_llm_call(
        session_id="session-1",
        turn_id="turn-spoofed",
        platform=PretendsFeishu(),
    )
    assert posted == []
    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-real", platform="feishu"
    )
    assert [payload["turn_id"] for payload in posted] == ["turn-real"]


@pytest.mark.parametrize(
    "malformed",
    (
        AcceptedDict(ok=True, applied=False),
        {"ok": True, "applied": False, "extra": "bad"},
        {PretendsKey("ok"): True, "applied": False},
        {"ok": True, "applied": False, "disposition": PretendsDelivered()},
    ),
)
def test_task4_malformed_started_response_retries_before_native_bypass(malformed):
    posted = []
    runtime = task4_runtime(
        posted,
        responses=[malformed, {"ok": True, "applied": True}],
    )
    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-1", platform="feishu"
    )
    assert len(posted) == 2
    assert posted[0] == posted[1]
    assert runtime.turn_state("turn-1") is TurnState.CARD_ACTIVE


@pytest.mark.parametrize(
    "not_explicit",
    (
        {"ok": False, "applied": True},
        {"ok": True, "applied": False},
        {"ok": True, "applied": True, "delivery": {"outcome": "unknown"}},
        {"ok": True, "applied": True, "delivery": {"outcome": "accepted"}},
    ),
)
def test_task4_only_exact_started_success_or_native_rejection_stops_retry(not_explicit):
    posted = []
    runtime = task4_runtime(
        posted,
        responses=[not_explicit, {"ok": True, "applied": True}],
    )
    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-1", platform="feishu"
    )
    assert len(posted) == 2
    assert runtime.turn_state("turn-1") is TurnState.CARD_ACTIVE


def test_task4_missing_answer_cleanup_closes_coordinator_outside_runtime_lock():
    posted = []
    runtime = active_task4_runtime(posted)
    coordinator = runtime._coordinators["turn-1"]
    original_close = coordinator.close

    def checked_close():
        assert not runtime._lock._is_owned()
        return original_close()

    coordinator.close = checked_close
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )
    assert runtime.turn_state("turn-1") is None
    assert posted == []


def test_task4_duplicate_turn_closes_discarded_coordinator_outside_runtime_lock(monkeypatch):
    posted = []
    runtime = active_task4_runtime(posted)
    assert runtime.bind_ingress_from_values(
        "second",
        "fallback_default",
        "session-2",
        "gateway-session-2",
        "generation-2",
        "oc_2",
        "om_2",
        "om_2",
        "",
    )
    real_close = TurnEventCoordinator.close

    def checked_close(coordinator):
        assert not runtime._lock._is_owned()
        return real_close(coordinator)

    monkeypatch.setattr(TurnEventCoordinator, "close", checked_close)
    runtime.handle_pre_llm_call(
        session_id="session-2", turn_id="turn-1", platform="feishu"
    )
    assert posted == []


def test_task4_finalize_during_terminal_transport_cannot_resurrect_disposition():
    posted = []
    transport_entered = Event()
    release_transport = Event()

    def post(payload, timeout_seconds):
        posted.append(payload)
        if payload["event"] == "message.completed":
            transport_entered.set()
            assert release_transport.wait(timeout=1.0)
        return {"ok": True, "applied": True}

    runtime = plugin_runtime.PluginRuntime(post=post, now=lambda: 100.0)
    runtime.bind_ingress_from_values(
        "default", "fallback_default", "session-1", "gateway-session-1",
        "generation-1", "oc_1", "om_1", "om_parent", "thread-1",
    )
    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-1", platform="feishu"
    )
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    terminal = Thread(
        target=lambda: runtime.handle_on_session_end(
            turn_id="turn-1", completed=True, failed=False, interrupted=False
        )
    )
    terminal.start()
    assert transport_entered.wait(timeout=0.5)
    runtime.handle_on_session_finalize(session_id="session-1")
    release_transport.set()
    terminal.join(timeout=1.0)
    assert not terminal.is_alive()
    assert runtime.take_terminal_disposition("turn-1") is None
    assert "answer" not in repr(runtime)


def test_task4_unknown_structured_native_descriptor_is_strictly_rejected():
    descriptor = {
        "future": ["structured", {"opaque": "descriptor"}],
        "protocol": "future-protocol",
    }
    response = {
        "ok": True,
        "applied": False,
        "disposition": "native",
        "native_handoff": descriptor,
    }
    posted = []
    runtime = active_task4_runtime(
        posted,
        responses=[{"ok": True, "applied": True}, response, response],
    )
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )
    assert runtime.take_terminal_disposition("turn-1") is None
    assert runtime.take_terminal_record("turn-1")["response"] is None


def test_task4_terminal_delivery_accepted_is_malformed_and_retries_exact_payload():
    posted = []
    runtime = active_task4_runtime(
        posted,
        responses=[
            {"ok": True, "applied": True},
            {"ok": True, "applied": True, "delivery": {"outcome": "accepted"}},
            {"ok": True, "applied": True},
        ],
    )
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )
    assert len(posted) == 2
    assert posted[0] == posted[1]
    assert runtime.take_terminal_disposition("turn-1") == {
        "ok": True,
        "applied": True,
    }


def test_task4_answer_cache_expires_and_reset_finalize_clear_exact_state():
    now = [100.0]
    posted = []
    runtime = active_task4_runtime(posted, now=lambda: now[0])
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="EXPIRED-ANSWER")
    now[0] += runtime._ANSWER_TTL_SECONDS
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )
    assert posted == []
    assert runtime.turn_state("turn-1") is None
    assert "EXPIRED-ANSWER" not in repr(runtime)

    assert runtime.bind_ingress_from_values(
        "default", "fallback_default", "old-session", "gateway-old",
        "generation-old", "oc_old", "om_old", "om_old", "",
    )
    runtime.handle_pre_llm_call(
        session_id="old-session", turn_id="turn-old", platform="feishu"
    )
    runtime.handle_post_llm_call(turn_id="turn-old", assistant_response="RESET-ANSWER")
    posted.clear()
    runtime.handle_on_session_reset(
        session_id="new-session", old_session_id="old-session"
    )
    assert runtime.turn_state("turn-old") is None
    assert "RESET-ANSWER" not in repr(runtime)


def test_task4_queue_saturation_and_terminal_barrier_drop_late_observers():
    posted = []
    runtime = active_task4_runtime(posted, max_pending=1)
    coordinator = runtime._coordinators["turn-1"]
    coordinator._deliver = lambda _event: None
    coordinator._queue = __import__("queue").Queue(maxsize=1)

    runtime.handle_pre_tool_call(
        turn_id="turn-1", tool_call_id="call-1", tool_name="shell"
    )
    runtime.handle_pre_tool_call(
        turn_id="turn-1", tool_call_id="call-2", tool_name="shell"
    )
    assert coordinator._queue.qsize() == 1
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )
    runtime.handle_post_tool_call(
        turn_id="turn-1", tool_call_id="call-late", tool_name="shell", status="ok"
    )
    assert [payload["event"] for payload in posted] == ["message.completed"]


def test_task4_missing_turn_session_end_never_closes_another_turn():
    posted = []
    runtime = active_task4_runtime(posted)
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        session_id="session-1", completed=True, failed=False, interrupted=False
    )
    assert runtime.turn_state("turn-1") is TurnState.CARD_ACTIVE
    assert posted == []


def test_task4_terminal_retry_reuses_one_payload_and_native_remains_native():
    native = {"ok": True, "applied": False, "disposition": "native"}
    posted = []
    runtime = active_task4_runtime(
        posted,
        responses=[{"ok": True, "applied": True}, None, native],
    )
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )
    assert len(posted) == 2
    assert posted[0] == posted[1]
    assert runtime.take_terminal_disposition("turn-1") is None
    assert runtime.take_terminal_record("turn-1")["response"] == native


def test_task4_post_approval_real_official_kwargs_exactly_close_pending():
    posted = []
    runtime = active_task4_runtime(posted)
    official = {
        "command": "git status",
        "description": "check state",
        "pattern_key": "git status",
        "pattern_keys": ["git status"],
        "session_key": "gateway-session-1",
        "surface": "gateway",
        "turn_id": "turn-1",
        "tool_call_id": "approval-call-1",
    }
    runtime.handle_pre_approval_request(**official)
    runtime.handle_post_approval_response(**official, choice="deny")
    runtime.drain_observers(1.0)
    assert [payload["event"] for payload in posted] == [
        "interaction.requested",
        "interaction.completed",
    ]


def test_task4_all_runtime_maps_are_explicitly_bounded_to_1024():
    runtime = plugin_runtime.PluginRuntime(post=lambda payload, timeout: None)
    assert runtime._MAX_ENTRIES <= 1024
    assert runtime._registry._MAX_BINDINGS <= 1024


def test_task4_runtime_reset_closes_coordinator_and_restores_inert_callbacks():
    posted = []
    runtime = active_task4_runtime(posted)
    coordinator = runtime._coordinators["turn-1"]
    plugin_runtime.configure_plugin_runtime(runtime)

    assert reset_plugin_runtime_state() is None
    assert coordinator.submit_observer(
        {"event": "tool.updated"}, producer="plugin"
    ) is False
    assert plugin_runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-new", platform="feishu"
    ) is None
    assert posted == []


def test_task4_finalize_linearizes_with_claim_and_pre_cannot_resurrect_turn(monkeypatch):
    posted = []
    runtime = task4_runtime(posted)
    claimed = Event()
    release_claim = Event()
    original_claim = runtime._registry.claim_unique_session

    def gated_claim(session_id, turn_id):
        turn = original_claim(session_id, turn_id)
        claimed.set()
        assert release_claim.wait(timeout=1.0)
        return turn

    monkeypatch.setattr(runtime._registry, "claim_unique_session", gated_claim)
    pre = Thread(
        target=lambda: runtime.handle_pre_llm_call(
            session_id="session-1", turn_id="turn-race", platform="feishu"
        )
    )
    pre.start()
    assert claimed.wait(timeout=0.5)
    finalized = Event()
    cleanup = Thread(
        target=lambda: (
            runtime.handle_on_session_finalize(session_id="session-1"),
            finalized.set(),
        )
    )
    cleanup.start()
    cleanup_finished_early = finalized.wait(timeout=0.05)
    try:
        assert not cleanup_finished_early
    finally:
        release_claim.set()
        pre.join(timeout=1.0)
        cleanup.join(timeout=1.0)
    assert not pre.is_alive() and not cleanup.is_alive()
    assert runtime.turn_state("turn-race") is None
    assert len(posted) <= 1


def test_task4_capacity_never_evicts_live_terminal_owner_and_all_owner_refuses_admission():
    entered = Event()
    release = Event()
    posted = []

    def post(payload, timeout):
        posted.append(payload)
        if payload["event"] == "message.completed":
            entered.set()
            assert release.wait(timeout=1.0)
        return {"ok": True, "applied": True}

    runtime = plugin_runtime.PluginRuntime(post=post, now=lambda: 100.0)
    runtime._MAX_ENTRIES = 1
    assert runtime.bind_ingress_from_values(
        "default", "fallback_default", "session-1", "gateway-session-1",
        "generation-1", "oc_1", "om_1", "om_1", "",
    )
    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-owner", platform="feishu"
    )
    runtime.handle_post_llm_call(turn_id="turn-owner", assistant_response="answer")
    terminal = Thread(
        target=lambda: runtime.handle_on_session_end(
            turn_id="turn-owner", completed=True, failed=False, interrupted=False
        )
    )
    terminal.start()
    assert entered.wait(timeout=0.5)
    assert runtime.bind_ingress_from_values(
        "second", "fallback_default", "session-2", "gateway-session-2",
        "generation-2", "oc_2", "om_2", "om_2", "",
    )
    runtime.handle_pre_llm_call(
        session_id="session-2", turn_id="turn-refused", platform="feishu"
    )
    assert "turn-owner" in runtime._terminal_owners
    assert runtime.turn_state("turn-refused") is None
    assert runtime._registry.claim_unique_session(
        "session-2", "turn-still-unclaimed"
    ) is not None
    release.set()
    terminal.join(timeout=1.0)
    assert not terminal.is_alive()


def test_task4_capacity_evicts_oldest_nonowner_and_cleans_all_cross_map_state():
    posted = []
    runtime = plugin_runtime.PluginRuntime(post=lambda payload, timeout: posted.append(payload) or {"ok": True, "applied": True}, now=lambda: 100.0)
    runtime._MAX_ENTRIES = 1
    for index in (1, 2):
        assert runtime.bind_ingress_from_values(
            f"p-{index}", "fallback_default", f"session-{index}",
            f"gateway-{index}", f"generation-{index}", f"oc_{index}",
            f"om_{index}", f"om_{index}", "",
        )
        runtime.handle_pre_llm_call(
            session_id=f"session-{index}", turn_id=f"turn-{index}", platform="feishu"
        )
        if index == 1:
            runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="secret")
    assert runtime.turn_state("turn-1") is None
    assert "turn-1" not in runtime._coordinators
    assert "turn-1" not in runtime._answers
    assert "turn-1" not in runtime._terminal_owners
    assert runtime.turn_state("turn-2") is TurnState.CARD_ACTIVE


def test_task4_coordinator_close_reaps_idle_workers_without_thread_leak():
    baseline = sum(t.name == "hfc-turn-observer" for t in __import__("threading").enumerate())
    coordinators = [TurnEventCoordinator(f"turn-close-{i}") for i in range(40)]
    for coordinator in coordinators:
        coordinator.close()
    deadline = __import__("time").monotonic() + 1.0
    while __import__("time").monotonic() < deadline:
        live = sum(t.name == "hfc-turn-observer" for t in __import__("threading").enumerate())
        if live <= baseline:
            break
        Event().wait(0.01)
    assert live <= baseline


def test_task4_coordinator_close_is_bounded_when_delivery_is_blocked():
    entered = Event()
    release = Event()
    coordinator = TurnEventCoordinator(
        "turn-blocked-close",
        deliver=lambda event: (entered.set(), release.wait(timeout=1.0)),
    )
    assert coordinator.submit_observer({"event": "tool.updated"}, producer="plugin")
    assert entered.wait(timeout=0.5)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(coordinator.close).result(timeout=0.5) is None
    finally:
        release.set()


def native_terminal_response(descriptor=None):
    response = {"ok": True, "applied": False, "disposition": "native"}
    if descriptor is not None:
        response["native_handoff"] = descriptor
    return response


def exact_native_descriptor(*, expires_at=3700.0):
    return {
        "protocol": "hfc-native-handoff-v2",
        "id": "a" * 64,
        "uuid_seed": "b" * 32,
        "expires_at": expires_at,
    }


@pytest.mark.parametrize(
    "descriptor",
    (
        AcceptedDict(exact_native_descriptor()),
        {**exact_native_descriptor(), "extra": False},
        {key: value for key, value in exact_native_descriptor().items() if key != "id"},
        {PretendsKey("protocol"): "hfc-native-handoff-v2", "id": "a" * 64, "uuid_seed": "b" * 32, "expires_at": 3700.0},
        {**exact_native_descriptor(), "protocol": StringSubclass("hfc-native-handoff-v2")},
        {**exact_native_descriptor(), "id": "A" * 64},
        {**exact_native_descriptor(), "uuid_seed": "g" * 32},
        exact_native_descriptor(expires_at=True),
        exact_native_descriptor(expires_at=100.0),
        exact_native_descriptor(expires_at=3731.0),
        exact_native_descriptor(expires_at=float("inf")),
    ),
)
def test_task4_rejects_malformed_native_descriptor(descriptor):
    posted = []
    malformed = native_terminal_response(descriptor)
    runtime = active_task4_runtime(
        posted,
        responses=[{"ok": True, "applied": True}, malformed, malformed],
        now=lambda: 100.0,
    )
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )
    assert runtime.take_terminal_record("turn-1")["response"] is None


def test_task4_accepts_exact_native_descriptor_and_plain_native_fallback():
    for response in (
        native_terminal_response(),
        native_terminal_response(exact_native_descriptor(expires_at=3730.0)),
    ):
        posted = []
        runtime = active_task4_runtime(
            posted,
            responses=[{"ok": True, "applied": True}, response],
            now=lambda: 100.0,
        )
        runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
        runtime.handle_on_session_end(
            turn_id="turn-1", completed=True, failed=False, interrupted=False
        )
        assert runtime.take_terminal_record("turn-1")["response"] == response


def test_task4_terminal_record_is_deep_copied_one_shot_and_concurrent_take_once():
    posted = []
    runtime = active_task4_runtime(posted)
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        records = list(executor.map(lambda _: (barrier.wait(), runtime.take_terminal_record("turn-1"))[1], range(2)))
    assert sum(record is not None for record in records) == 1
    record = next(record for record in records if record is not None)
    assert record["payload"]["data"] == {"answer": "answer"}
    record["payload"]["data"]["answer"] = "mutated"
    assert runtime.take_terminal_record("turn-1") is None
    assert runtime.take_terminal_disposition("turn-1") is None


def test_task4_failed_terminal_never_exposes_card_suppression_disposition():
    posted = []
    runtime = active_task4_runtime(posted)
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=False, failed=True, interrupted=False,
        turn_exit_reason="runtime_error",
    )
    assert runtime.take_terminal_disposition("turn-1") is None
    record = runtime.take_terminal_record("turn-1")
    assert record["payload"]["event"] == "message.failed"
    assert record["response"] == {"ok": True, "applied": True}


def test_task4_completed_native_is_available_only_through_full_terminal_record():
    posted = []
    native = native_terminal_response(exact_native_descriptor())
    runtime = active_task4_runtime(
        posted,
        responses=[{"ok": True, "applied": True}, native],
    )
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )
    assert runtime.take_terminal_disposition("turn-1") is None
    assert runtime.take_terminal_record("turn-1")["response"] == native


def test_task4_terminal_descriptor_expiring_during_transport_is_not_recorded():
    clock = {"now": 100.0}
    posted = []
    native = native_terminal_response(exact_native_descriptor(expires_at=101.0))

    def post(payload, timeout_seconds):
        posted.append(payload)
        if payload["event"] == "message.started":
            return {"ok": True, "applied": True}
        clock["now"] = 102.0
        return native

    runtime = plugin_runtime.PluginRuntime(post=post, now=lambda: clock["now"])
    assert runtime.bind_ingress_from_values(
        "default", "fallback_default", "session-1", "gateway-session-1",
        "generation-1", "oc_1", "om_1", "om_parent", "thread-1",
    )
    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-1", platform="feishu"
    )
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )

    record = runtime.take_terminal_record("turn-1")
    assert record["response"] is None
    assert [payload["event"] for payload in posted].count("message.completed") == 2


def test_task4_finalize_cancels_unclaimed_started_transport_without_waiting(monkeypatch):
    order = []

    def post(payload, timeout_seconds):
        order.append("started-posted")
        return {"ok": True, "applied": True}

    runtime = plugin_runtime.PluginRuntime(post=post, now=lambda: 100.0)
    assert runtime.bind_ingress_from_values(
        "default", "fallback_default", "session-1", "gateway-session-1",
        "generation-1", "oc_1", "om_1", "om_parent", "thread-1",
    )
    payload_entered = Event()
    release_payload = Event()
    original_base_payload = runtime._base_payload

    def gated_base_payload(turn, *, sequence, created_at):
        payload_entered.set()
        assert release_payload.wait(timeout=1.0)
        return original_base_payload(turn, sequence=sequence, created_at=created_at)

    monkeypatch.setattr(runtime, "_base_payload", gated_base_payload)
    pre = Thread(
        target=lambda: runtime.handle_pre_llm_call(
            session_id="session-1", turn_id="turn-1", platform="feishu"
        )
    )
    pre.start()
    assert payload_entered.wait(timeout=0.5)
    finalized = Event()

    def finalize():
        runtime.handle_on_session_finalize(session_id="session-1")
        order.append("finalize-returned")
        finalized.set()

    cleanup = Thread(target=finalize)
    cleanup.start()
    cleanup_finished_early = finalized.wait(timeout=0.25)
    try:
        assert cleanup_finished_early
    finally:
        release_payload.set()
        pre.join(timeout=1.0)
        cleanup.join(timeout=1.0)
    assert not pre.is_alive() and not cleanup.is_alive()
    assert order == ["finalize-returned"]
    assert runtime.turn_state("turn-1") is None


@pytest.mark.parametrize("cleanup_kind", ("finalize", "close"))
def test_task4_cleanup_serializes_started_check_to_call_transport(
    monkeypatch, cleanup_kind
):
    order = []

    def post(payload, timeout_seconds):
        order.append("started-posted")
        return {"ok": True, "applied": True}

    runtime = plugin_runtime.PluginRuntime(
        post=post, now=lambda: 100.0, observer_timeout_seconds=0.0
    )
    assert runtime.bind_ingress_from_values(
        "default", "fallback_default", "session-1", "gateway-session-1",
        "generation-1", "oc_1", "om_1", "om_parent", "thread-1",
    )
    transport_entered = Event()
    release_transport = Event()
    original_post_retry = runtime._post_retry_unknown

    def gated_post_retry(payload, timeout, is_explicit):
        if payload["event"] == "message.started":
            transport_entered.set()
            assert release_transport.wait(timeout=1.0)
        return original_post_retry(payload, timeout, is_explicit)

    monkeypatch.setattr(runtime, "_post_retry_unknown", gated_post_retry)
    pre = Thread(
        target=lambda: runtime.handle_pre_llm_call(
            session_id="session-1", turn_id="turn-1", platform="feishu"
        )
    )
    pre.start()
    assert transport_entered.wait(timeout=0.5)
    cleanup_returned = Event()

    def cleanup():
        if cleanup_kind == "finalize":
            runtime.handle_on_session_finalize(session_id="session-1")
        else:
            runtime.close()
        order.append("cleanup-returned")
        cleanup_returned.set()

    cleanup_thread = Thread(target=cleanup)
    cleanup_thread.start()
    cleanup_finished_early = cleanup_returned.wait(timeout=0.25)
    try:
        assert not cleanup_finished_early
    finally:
        release_transport.set()
        pre.join(timeout=1.0)
        cleanup_thread.join(timeout=1.0)
    assert not pre.is_alive() and not cleanup_thread.is_alive()
    assert order == ["started-posted", "cleanup-returned"]
    assert runtime.turn_state("turn-1") is None


@pytest.mark.parametrize(
    "malformed",
    (
        AcceptedDict(ok=True, applied=True),
        {"ok": True, "applied": True, "extra": False},
        {PretendsKey("ok"): True, "applied": True},
        {"ok": True, "applied": 1},
        {"ok": True, "applied": False, "disposition": PretendsDelivered()},
        {"ok": True, "applied": False, "disposition": "native", "extra": False},
        {
            "ok": True,
            "applied": False,
            "disposition": "native",
            "native_handoff": AcceptedDict(protocol="future"),
        },
    ),
)
def test_task4_malformed_terminal_response_is_not_cached(malformed):
    posted = []
    runtime = active_task4_runtime(
        posted,
        responses=[{"ok": True, "applied": True}, malformed, malformed],
    )
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    runtime.handle_on_session_end(
        turn_id="turn-1", completed=True, failed=False, interrupted=False
    )
    assert runtime.take_terminal_disposition("turn-1") is None


def patch_interaction_args(
    pending_handle,
    *,
    kind="approval",
    session_identity="gateway-session-1",
    turn_id="turn-1",
    interaction_id="interaction-1",
    fingerprint="a" * 64,
):
    return (
        kind,
        session_identity,
        turn_id,
        interaction_id,
        fingerprint,
        pending_handle,
    )


def test_task6_patch_delta_uses_shared_sequence_and_exact_patch_payload(monkeypatch):
    posted = []
    runtime = active_task4_runtime(posted)
    coordinator = runtime._coordinators["turn-1"]
    monkeypatch.setattr(
        coordinator,
        "next_sequence",
        lambda *_args, **_kwargs: pytest.fail("delta must not allocate a second sequence"),
    )

    assert runtime.submit_patch_delta(
        "turn-1", "answer.delta", "answer text", "delta"
    ) is True
    assert runtime.submit_patch_delta(
        "turn-1", "thinking.delta", "thinking text", "append_block"
    ) is True
    runtime.drain_observers(1.0)

    assert posted == [
        {
            "schema_version": "1",
            "event": "answer.delta",
            "conversation_id": "thread-1",
            "message_id": "om_1",
            "chat_id": "oc_1",
            "thread_id": "thread-1",
            "platform": "feishu",
            "turn_id": "turn-1",
            "sequence": 1,
            "created_at": 100.0,
            "event_id": "patch:turn-1:answer.delta:1",
            "producer": "patch",
            "phase": "delta",
            "data": {"text": "answer text", "mode": "delta"},
        },
        {
            "schema_version": "1",
            "event": "thinking.delta",
            "conversation_id": "thread-1",
            "message_id": "om_1",
            "chat_id": "oc_1",
            "thread_id": "thread-1",
            "platform": "feishu",
            "turn_id": "turn-1",
            "sequence": 2,
            "created_at": 100.0,
            "event_id": "patch:turn-1:thinking.delta:2",
            "producer": "patch",
            "phase": "delta",
            "data": {"text": "thinking text", "mode": "append_block"},
        },
    ]


def test_task6_patch_delta_accepts_exact_64_kib_utf8_boundary():
    posted = []
    runtime = active_task4_runtime(posted)
    text = "界" * 21845 + "a"
    assert len(text.encode("utf-8")) == 64 * 1024

    assert runtime.submit_patch_delta(
        "turn-1", "answer.delta", text, "delta"
    ) is True
    runtime.drain_observers(1.0)
    assert posted[0]["data"] == {"text": text, "mode": "delta"}


@pytest.mark.parametrize(
    ("turn_id", "event_name", "text", "mode"),
    (
        ("", "answer.delta", "x", "delta"),
        (StringSubclass("turn-1"), "answer.delta", "x", "delta"),
        ("turn-1", StringSubclass("answer.delta"), "x", "delta"),
        ("turn-1", "message.completed", "x", "delta"),
        ("turn-1", "answer.delta", "x", "append_block"),
        ("turn-1", "thinking.delta", "x", "delta"),
        ("turn-1", "answer.delta", "", "delta"),
        ("turn-1", "answer.delta", StringSubclass("x"), "delta"),
        ("turn-1", "answer.delta", "界" * 21846, "delta"),
        ("turn-1", "answer.delta", "x", StringSubclass("delta")),
    ),
)
def test_task6_patch_delta_rejects_inexact_spoofed_or_oversize_inputs(
    turn_id, event_name, text, mode
):
    posted = []
    runtime = active_task4_runtime(posted)

    assert runtime.submit_patch_delta(turn_id, event_name, text, mode) is False
    runtime.close()
    assert posted == []


def test_task6_patch_delta_requires_exact_card_active_turn_and_live_coordinator():
    posted = []
    runtime = task4_runtime(posted)
    assert runtime.submit_patch_delta(
        "turn-missing", "answer.delta", "x", "delta"
    ) is False

    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-1", platform="feishu"
    )
    turn = runtime._turns["turn-1"]
    with turn._lock:
        turn._state = TurnState.PENDING_START
    assert runtime.submit_patch_delta(
        "turn-1", "answer.delta", "x", "delta"
    ) is False
    with turn._lock:
        turn._state = TurnState.NATIVE_BYPASS
    assert runtime.submit_patch_delta(
        "turn-1", "answer.delta", "x", "delta"
    ) is False
    with turn._lock:
        turn._state = TurnState.CARD_ACTIVE
    original = runtime._coordinators["turn-1"]
    wrong = TurnEventCoordinator("turn-other", start_worker=False)
    runtime._coordinators["turn-1"] = wrong
    try:
        assert runtime.submit_patch_delta(
            "turn-1", "answer.delta", "x", "delta"
        ) is False
    finally:
        runtime._coordinators["turn-1"] = original
        wrong.close()
    turn.finish()
    assert runtime.submit_patch_delta(
        "turn-1", "answer.delta", "x", "delta"
    ) is False
    runtime.close()


def test_task6_patch_delta_queue_saturation_barrier_and_exception_fail_closed(
    monkeypatch,
):
    posted = []
    runtime = active_task4_runtime(posted, max_pending=1)
    original = runtime._coordinators["turn-1"]
    coordinator = TurnEventCoordinator("turn-1", max_pending=1, start_worker=False)
    runtime._coordinators["turn-1"] = coordinator
    original.close()
    assert runtime.submit_patch_delta(
        "turn-1", "answer.delta", "first", "delta"
    ) is True
    assert runtime.submit_patch_delta(
        "turn-1", "answer.delta", "full", "delta"
    ) is False
    coordinator.close_terminal_barrier()
    assert runtime.submit_patch_delta(
        "turn-1", "answer.delta", "late", "delta"
    ) is False

    other_posted = []
    other = active_task4_runtime(other_posted)
    monkeypatch.setattr(
        other._coordinators["turn-1"],
        "submit_observer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("canary")),
    )
    assert other.submit_patch_delta(
        "turn-1", "answer.delta", "safe", "delta"
    ) is False
    other.close()
    runtime.close()
    assert other_posted == []


def test_patch_status_notice_uses_shared_sequence_and_exact_sanitized_payload(
    monkeypatch,
):
    posted = []
    runtime = active_task4_runtime(posted)
    coordinator = runtime._coordinators["turn-1"]
    monkeypatch.setattr(
        coordinator,
        "next_sequence",
        lambda *_args, **_kwargs: pytest.fail(
            "status notice must not allocate a second sequence"
        ),
    )

    assert runtime.submit_patch_status_notice(
        "turn-1",
        notice_kind="context-compaction",
        notice_id="context-compaction:active",
    ) is True
    runtime.drain_observers(1.0)

    assert posted == [
        {
            "schema_version": "1",
            "event": "system.notice",
            "conversation_id": "thread-1",
            "message_id": "om_1",
            "chat_id": "oc_1",
            "thread_id": "thread-1",
            "platform": "feishu",
            "turn_id": "turn-1",
            "sequence": 1,
            "created_at": 100.0,
            "event_id": (
                "patch:turn-1:system.notice:context-compaction:active:1"
            ),
            "producer": "patch",
            "phase": "started",
            "data": {
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
        }
    ]
    assert "session-1" not in repr(posted[0]["data"])
    assert "gateway-session-1" not in repr(posted[0]["data"])
    assert "oc_1" not in repr(posted[0]["data"])


@pytest.mark.parametrize(
    ("turn_id", "notice_kind", "notice_id"),
    (
        ("", "context-compaction", "context-compaction:active"),
        (StringSubclass("turn-1"), "context-compaction", "context-compaction:active"),
        ("turn-1", StringSubclass("context-compaction"), "context-compaction:active"),
        ("turn-1", "other", "context-compaction:active"),
        ("turn-1", "context-compaction", StringSubclass("context-compaction:active")),
        ("turn-1", "context-compaction", "other"),
    ),
)
def test_patch_status_notice_rejects_inexact_or_unknown_fixed_tags(
    turn_id, notice_kind, notice_id
):
    posted = []
    runtime = active_task4_runtime(posted)

    assert runtime.submit_patch_status_notice(
        turn_id, notice_kind=notice_kind, notice_id=notice_id
    ) is False
    runtime.close()
    assert posted == []


def test_patch_status_notice_requires_exact_card_active_turn_and_live_coordinator():
    posted = []
    runtime = task4_runtime(posted)
    tags = {
        "notice_kind": "context-compaction",
        "notice_id": "context-compaction:active",
    }
    assert runtime.submit_patch_status_notice("turn-missing", **tags) is False

    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-1", platform="feishu"
    )
    turn = runtime._turns["turn-1"]
    with turn._lock:
        turn._state = TurnState.PENDING_START
    assert runtime.submit_patch_status_notice("turn-1", **tags) is False
    with turn._lock:
        turn._state = TurnState.NATIVE_BYPASS
    assert runtime.submit_patch_status_notice("turn-1", **tags) is False
    with turn._lock:
        turn._state = TurnState.CARD_ACTIVE
    runtime._terminal_owners["turn-1"] = object()
    assert runtime.submit_patch_status_notice("turn-1", **tags) is False
    runtime._terminal_owners.pop("turn-1")
    original = runtime._coordinators["turn-1"]
    wrong = TurnEventCoordinator("turn-other", start_worker=False)
    runtime._coordinators["turn-1"] = wrong
    try:
        assert runtime.submit_patch_status_notice("turn-1", **tags) is False
    finally:
        runtime._coordinators["turn-1"] = original
        wrong.close()
    runtime.handle_on_session_reset(old_session_id="session-1")
    assert runtime.submit_patch_status_notice("turn-1", **tags) is False
    runtime.close()


def test_patch_status_notice_queue_saturation_barrier_and_exception_fail_closed(
    monkeypatch,
):
    tags = {
        "notice_kind": "context-compaction",
        "notice_id": "context-compaction:active",
    }
    posted = []
    runtime = active_task4_runtime(posted, max_pending=1)
    original = runtime._coordinators["turn-1"]
    coordinator = TurnEventCoordinator("turn-1", max_pending=1, start_worker=False)
    runtime._coordinators["turn-1"] = coordinator
    original.close()
    assert runtime.submit_patch_status_notice("turn-1", **tags) is True
    assert runtime.submit_patch_status_notice("turn-1", **tags) is False
    coordinator.close_terminal_barrier()
    assert runtime.submit_patch_status_notice("turn-1", **tags) is False

    other_posted = []
    other = active_task4_runtime(other_posted)
    monkeypatch.setattr(
        other._coordinators["turn-1"],
        "submit_observer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("canary")),
    )
    assert other.submit_patch_status_notice("turn-1", **tags) is False
    other.close()
    runtime.close()
    assert other_posted == []


def test_patch_status_notice_and_cleanup_race_is_linearizable():
    posted = []
    runtime = active_task4_runtime(posted)
    barrier = Barrier(2)
    tags = {
        "notice_kind": "context-compaction",
        "notice_id": "context-compaction:active",
    }

    def submit():
        barrier.wait()
        return runtime.submit_patch_status_notice("turn-1", **tags)

    def cleanup():
        barrier.wait()
        runtime.handle_on_session_reset(old_session_id="session-1")

    with ThreadPoolExecutor(max_workers=2) as executor:
        submitted = executor.submit(submit)
        cleaned = executor.submit(cleanup)
        assert type(submitted.result(timeout=1.0)) is bool
        assert cleaned.result(timeout=1.0) is None
    assert runtime.turn_state("turn-1") is None
    assert runtime.submit_patch_status_notice("turn-1", **tags) is False
    runtime.close()


@pytest.mark.parametrize("kind", ("approval", "clarify", "slash"))
def test_task6_patch_interaction_register_resolve_and_claim_once(kind):
    posted = []
    runtime = active_task4_runtime(posted)
    pending_handle = object()
    values = patch_interaction_args(
        pending_handle, kind=kind, interaction_id=f"{kind}:safe_1"
    )

    assert runtime.register_patch_interaction(*values) is True
    assert runtime.register_patch_interaction(*values) is True
    assert runtime.resolve_patch_interaction(*values, " selected value ") is True
    assert runtime.resolve_patch_interaction(*values, " selected value ") is True
    assert runtime.resolve_patch_interaction(*values, "conflict") is False
    assert runtime.claim_patch_interaction(*values) == " selected value "
    assert runtime.register_patch_interaction(*values) is False
    assert runtime.resolve_patch_interaction(*values, " selected value ") is False
    assert runtime.resolve_patch_interaction(*values, "conflict") is False
    assert runtime.claim_patch_interaction(*values) is None
    assert posted == []
    assert runtime._coordinators["turn-1"]._queue.qsize() == 0


def _runtime_interaction_ui():
    return {
        "prompt": "允许执行命令吗？",
        "description": "仅用于本次操作",
        "allow_custom_input": False,
        "multi_select": False,
        "timeout_seconds": 20.0,
        "options": [
            {"label": "允许一次", "value": "once", "style": "primary"},
            {"label": "拒绝", "value": "deny", "style": "danger"},
        ],
    }


def test_runtime_interaction_ui_allows_empty_options_only_for_custom_input():
    custom = {
        **_runtime_interaction_ui(),
        "allow_custom_input": True,
        "options": [],
    }
    assert plugin_runtime.PluginRuntime._valid_interaction_ui_data(custom) is True
    assert plugin_runtime.PluginRuntime._valid_interaction_ui_data(
        {**custom, "allow_custom_input": False}
    ) is False


def test_runtime_interaction_admission_descriptor_matches_original_wait_window():
    posted = []
    now = [100.0]
    runtime = active_task4_runtime(posted, now=lambda: now[0])

    class Listener:
        resolve_url = "http://127.0.0.1:12345/runtime/interactions/resolve"

        @staticmethod
        def accepts():
            return True

    runtime._runtime_interaction_listener = Listener()
    handle = object()
    values = patch_interaction_args(
        handle, kind="clarify", interaction_id="clarify:safe_1"
    )
    assert runtime.register_patch_interaction(*values) is True
    runtime._post = lambda payload, timeout: posted.append(payload) or {
        "ok": True,
        "applied": True,
        "delivery": {"outcome": "delivered"},
        "runtime_admission": True,
    }
    ui = {
        **_runtime_interaction_ui(),
        "allow_custom_input": True,
        "options": [],
        "timeout_seconds": 3600.0,
    }

    assert runtime.admit_patch_interaction(
        *values, lambda choice: True, ui
    ) is True
    descriptor = posted[0]["data"]["_hfc_runtime_admission"]
    state = next(iter(runtime._patch_interactions.values()))
    assert descriptor["expires_at"] == 3700.0
    assert state.expires_at == 3700.0
    assert plugin_runtime.PluginRuntime._valid_interaction_ui_data(
        {**ui, "timeout_seconds": 3600.001}
    ) is False


def test_runtime_interaction_admission_posts_once_and_owns_ui_only_on_exact_delivery():
    posted = []
    runtime = active_task4_runtime(posted)
    assert runtime.start_runtime_interaction_listener(b"r" * 32) is True
    try:
        handle = object()
        values = patch_interaction_args(handle)
        assert runtime.register_patch_interaction(*values) is True

        runtime._post = lambda payload, timeout: posted.append(payload) or {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered"},
            "runtime_admission": True,
        }
        resolver = lambda choice: choice in {"once", "deny"}
        assert runtime.admit_patch_interaction(
            *values, resolver, _runtime_interaction_ui()
        ) is True

        assert len(posted) == 1
        payload = posted[0]
        assert payload["event"] == "interaction.requested"
        assert payload["producer"] == "patch"
        assert payload["data"]["_hfc_runtime_admission"]["resolve_url"].startswith(
            "http://127.0.0.1:"
        )
        state = next(iter(runtime._patch_interactions.values()))
        assert state.hfc_owned is True
        assert "token" not in state.admission_payload["data"][
            "_hfc_runtime_admission"
        ]
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "response",
    (
        {"ok": True, "applied": True},
        {"ok": True, "applied": True, "delivery": {"outcome": "delivered"}},
        {"ok": 1, "applied": True, "delivery": {"outcome": "delivered"}, "runtime_admission": True},
        AcceptedDict(ok=True, applied=True, delivery={"outcome": "delivered"}, runtime_admission=True),
        {"ok": True, "applied": True, "delivery": {"outcome": "delivered"}, "runtime_admission": True, "extra": False},
    ),
)
def test_runtime_interaction_admission_rejects_lookalikes_without_consuming_native(response):
    posted = []
    runtime = active_task4_runtime(posted)
    assert runtime.start_runtime_interaction_listener(b"r" * 32) is True
    handle = object()
    values = patch_interaction_args(handle)
    assert runtime.register_patch_interaction(*values) is True
    runtime._post = lambda payload, timeout: posted.append(payload) or response

    assert runtime.admit_patch_interaction(
        *values, lambda choice: True, _runtime_interaction_ui()
    ) is False
    state = next(iter(runtime._patch_interactions.values()))
    assert state.hfc_owned is False
    assert state.selected_value is None
    assert runtime.claim_patch_interaction(*values) is None
    runtime.close()


def test_runtime_interaction_unknown_transport_retry_reuses_byte_equivalent_event():
    posted = []
    responses = [None, {
        "ok": True,
        "applied": True,
        "delivery": {"outcome": "delivered"},
        "runtime_admission": True,
    }]
    runtime = active_task4_runtime(posted)
    assert runtime.start_runtime_interaction_listener(b"r" * 32) is True
    handle = object()
    values = patch_interaction_args(handle)
    assert runtime.register_patch_interaction(*values) is True
    runtime._post = lambda payload, timeout: posted.append(payload) or responses.pop(0)

    assert runtime.admit_patch_interaction(
        *values, lambda choice: True, _runtime_interaction_ui()
    ) is True
    assert len(posted) == 2
    assert posted[0] == posted[1]
    runtime.close()


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("kind", "other"),
        ("kind", StringSubclass("approval")),
        ("session_identity", ""),
        ("session_identity", "gateway-session-other"),
        ("session_identity", StringSubclass("gateway-session-1")),
        ("turn_id", "turn-missing"),
        ("turn_id", StringSubclass("turn-1")),
        ("interaction_id", ""),
        ("interaction_id", "x" * 129),
        ("interaction_id", "unsafe/value"),
        ("interaction_id", StringSubclass("interaction-1")),
        ("fingerprint", "A" * 64),
        ("fingerprint", "a" * 63),
        ("fingerprint", StringSubclass("a" * 64)),
        ("pending_handle", None),
    ),
)
def test_task6_patch_interaction_rejects_inexact_or_spoofed_binding_fields(
    field, invalid
):
    posted = []
    runtime = active_task4_runtime(posted)
    pending_handle = object()
    values = (
        patch_interaction_args(invalid)
        if field == "pending_handle"
        else patch_interaction_args(pending_handle, **{field: invalid})
    )

    assert runtime.register_patch_interaction(*values) is False
    assert runtime.resolve_patch_interaction(*values, "selected") is False
    assert runtime.claim_patch_interaction(*values) is None
    assert posted == []


@pytest.mark.parametrize(
    "selected",
    (
        "",
        "   ",
        StringSubclass("selected"),
        "界" * 1366 + "xxx",
        None,
    ),
)
def test_task6_patch_interaction_rejects_invalid_selected_values(selected):
    posted = []
    runtime = active_task4_runtime(posted)
    pending_handle = object()
    values = patch_interaction_args(pending_handle)
    assert runtime.register_patch_interaction(*values) is True

    assert runtime.resolve_patch_interaction(*values, selected) is False
    assert runtime.claim_patch_interaction(*values) is None


def test_task6_patch_interaction_accepts_exact_4096_byte_selected_value():
    posted = []
    runtime = active_task4_runtime(posted)
    pending_handle = object()
    values = patch_interaction_args(pending_handle)
    selected = "界" * 1365 + "a"
    assert len(selected.encode("utf-8")) == 4096
    assert runtime.register_patch_interaction(*values) is True
    assert runtime.resolve_patch_interaction(*values, selected) is True
    assert runtime.claim_patch_interaction(*values) == selected


def test_task6_patch_interaction_requires_object_identity_not_equality():
    class EqualityHandle:
        def __eq__(self, other):
            return True

    posted = []
    runtime = active_task4_runtime(posted)
    original = EqualityHandle()
    different = EqualityHandle()
    original_values = patch_interaction_args(original)
    different_values = patch_interaction_args(different)

    assert runtime.register_patch_interaction(*original_values) is True
    assert runtime.register_patch_interaction(*different_values) is False
    assert runtime.register_patch_interaction(
        *patch_interaction_args(original, interaction_id="different-key")
    ) is False
    assert runtime.resolve_patch_interaction(*different_values, "selected") is False
    assert runtime.claim_patch_interaction(*different_values) is None
    assert runtime.resolve_patch_interaction(*original_values, "selected") is True
    assert runtime.claim_patch_interaction(*original_values) == "selected"


def test_task6_patch_interaction_capacity_never_evicts_live_or_consumed_entries():
    now = [100.0]
    posted = []
    runtime = active_task4_runtime(posted, now=lambda: now[0])
    runtime._MAX_PATCH_INTERACTIONS = 2
    handles = [object(), object(), object()]
    values = [
        patch_interaction_args(handle, interaction_id=f"interaction-{index}")
        for index, handle in enumerate(handles)
    ]

    assert runtime.register_patch_interaction(*values[0]) is True
    assert runtime.register_patch_interaction(*values[1]) is True
    assert runtime.register_patch_interaction(*values[0]) is True
    assert runtime.register_patch_interaction(*values[2]) is False
    assert len(runtime._patch_interactions) == 2
    assert runtime.resolve_patch_interaction(*values[0], "first") is True
    assert runtime.claim_patch_interaction(*values[0]) == "first"
    assert len(runtime._patch_interactions) == 2
    assert runtime.register_patch_interaction(*values[2]) is False
    assert runtime.resolve_patch_interaction(*values[1], "second") is True
    assert runtime.claim_patch_interaction(*values[1]) == "second"
    assert len(runtime._patch_interactions) == 2
    assert runtime.register_patch_interaction(*values[2]) is False

    now[0] += 300.0
    assert runtime.register_patch_interaction(*values[2]) is True
    assert len(runtime._patch_interactions) == 1


def test_task6_patch_interaction_expiry_prunes_and_frees_capacity():
    now = [100.0]
    posted = []
    runtime = active_task4_runtime(posted, now=lambda: now[0])
    runtime._MAX_PATCH_INTERACTIONS = 1
    first = patch_interaction_args(object(), interaction_id="first")
    second = patch_interaction_args(object(), interaction_id="second")
    assert runtime.register_patch_interaction(*first) is True
    assert runtime.register_patch_interaction(*second) is False

    now[0] += 300.0
    assert runtime.claim_patch_interaction(*first) is None
    assert runtime.register_patch_interaction(*second) is True
    assert len(runtime._patch_interactions) == 1


def test_task6_consumed_patch_interaction_expires_before_exact_key_can_register():
    now = [100.0]
    posted = []
    runtime = active_task4_runtime(posted, now=lambda: now[0])
    values = patch_interaction_args(object())
    assert runtime.register_patch_interaction(*values) is True
    assert runtime.resolve_patch_interaction(*values, "once") is True
    assert runtime.claim_patch_interaction(*values) == "once"

    now[0] = 399.999
    assert runtime.register_patch_interaction(*values) is False
    now[0] = 400.0
    assert runtime.register_patch_interaction(*values) is True


@pytest.mark.parametrize("cleanup_kind", ("reset", "finalize", "close", "terminal"))
def test_task6_patch_interaction_cleanup_removes_selected_value_and_handle(
    cleanup_kind,
):
    posted = []
    runtime = active_task4_runtime(posted)
    pending_handle = object()
    values = patch_interaction_args(pending_handle)
    assert runtime.register_patch_interaction(*values) is True
    assert runtime.resolve_patch_interaction(*values, "SELECTED-CANARY") is True
    assert runtime.claim_patch_interaction(*values) == "SELECTED-CANARY"
    assert len(runtime._patch_interactions) == 1

    if cleanup_kind == "reset":
        runtime.handle_on_session_reset(old_session_id="session-1")
    elif cleanup_kind == "finalize":
        runtime.handle_on_session_finalize(session_id="session-1")
    elif cleanup_kind == "close":
        runtime.close()
    else:
        runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
        runtime.handle_on_session_end(
            turn_id="turn-1", completed=True, failed=False, interrupted=False
        )

    assert runtime._patch_interactions == {}
    assert runtime.claim_patch_interaction(*values) is None
    assert "SELECTED-CANARY" not in repr(runtime)


def test_task6_patch_interaction_turn_capacity_eviction_cleans_entry():
    posted = []
    runtime = active_task4_runtime(posted)
    runtime._MAX_ENTRIES = 1
    first = patch_interaction_args(object())
    assert runtime.register_patch_interaction(*first) is True
    assert runtime.resolve_patch_interaction(*first, "selected") is True
    assert runtime.claim_patch_interaction(*first) == "selected"
    assert len(runtime._patch_interactions) == 1
    assert runtime.bind_ingress_from_values(
        "second", "fallback_default", "session-2", "gateway-session-2",
        "generation-2", "oc_2", "om_2", "om_2", "",
    ) is True
    runtime.handle_pre_llm_call(
        session_id="session-2", turn_id="turn-2", platform="feishu"
    )

    assert runtime.turn_state("turn-1") is None
    assert runtime._patch_interactions == {}
    assert runtime.claim_patch_interaction(*first) is None


def test_task6_patch_interaction_terminal_owner_blocks_claim_before_cleanup():
    entered = Event()
    release = Event()
    posted = []

    def post(payload, timeout_seconds):
        posted.append(payload)
        if payload["event"] == "message.completed":
            entered.set()
            assert release.wait(timeout=1.0)
        return {"ok": True, "applied": True}

    runtime = plugin_runtime.PluginRuntime(post=post, now=lambda: 100.0)
    assert runtime.bind_ingress_from_values(
        "default", "fallback_default", "session-1", "gateway-session-1",
        "generation-1", "oc_1", "om_1", "om_1", "",
    ) is True
    runtime.handle_pre_llm_call(
        session_id="session-1", turn_id="turn-1", platform="feishu"
    )
    values = patch_interaction_args(object())
    assert runtime.register_patch_interaction(*values) is True
    assert runtime.resolve_patch_interaction(*values, "selected") is True
    runtime.handle_post_llm_call(turn_id="turn-1", assistant_response="answer")
    terminal = Thread(
        target=lambda: runtime.handle_on_session_end(
            turn_id="turn-1", completed=True, failed=False, interrupted=False
        )
    )
    terminal.start()
    assert entered.wait(timeout=0.5)
    try:
        assert runtime.claim_patch_interaction(*values) is None
        assert runtime.resolve_patch_interaction(*values, "selected") is False
    finally:
        release.set()
        terminal.join(timeout=1.0)
    assert not terminal.is_alive()
    assert runtime._patch_interactions == {}


def test_task6_patch_interaction_key_is_digest_and_claim_drops_raw_value():
    posted = []
    runtime = active_task4_runtime(posted)
    pending_handle = object()
    values = patch_interaction_args(pending_handle)
    assert runtime.register_patch_interaction(*values) is True
    key = next(iter(runtime._patch_interactions))
    state = runtime._patch_interactions[key]
    assert type(key) is str and len(key) == 64
    assert "gateway-session-1" not in key
    assert not hasattr(state, "session_identity")
    assert not hasattr(state, "interaction_id")
    assert not hasattr(state, "fingerprint")
    assert runtime.resolve_patch_interaction(*values, "SELECTED-CANARY") is True
    assert runtime.claim_patch_interaction(*values) == "SELECTED-CANARY"
    tombstone = runtime._patch_interactions[key]
    assert tombstone.state == "consumed"
    assert type(tombstone.turn_digest) is str and len(tombstone.turn_digest) == 64
    assert not hasattr(tombstone, "pending_handle")
    assert not hasattr(tombstone, "selected_value")
    assert not hasattr(tombstone, "turn_id")
    assert tombstone.expires_at == 400.0
    assert len(runtime._patch_interactions) == 1
    assert "SELECTED-CANARY" not in repr(runtime)


def test_task6_claimed_patch_interaction_blocks_reregister_conflict_and_retry():
    posted = []
    runtime = active_task4_runtime(posted)
    values = patch_interaction_args(object())

    assert [
        runtime.register_patch_interaction(*values),
        runtime.resolve_patch_interaction(*values, "once"),
        runtime.claim_patch_interaction(*values),
        runtime.register_patch_interaction(*values),
        runtime.resolve_patch_interaction(*values, "once"),
        runtime.resolve_patch_interaction(*values, "deny"),
        runtime.claim_patch_interaction(*values),
    ] == [True, True, "once", False, False, False, None]


def test_task6_claimed_patch_interaction_rejects_concurrent_replay_attempts():
    posted = []
    runtime = active_task4_runtime(posted)
    values = patch_interaction_args(object())
    assert runtime.register_patch_interaction(*values) is True
    assert runtime.resolve_patch_interaction(*values, "once") is True
    assert runtime.claim_patch_interaction(*values) == "once"
    start = Barrier(12)

    def replay(index):
        start.wait()
        if index % 3 == 0:
            return runtime.register_patch_interaction(*values)
        if index % 3 == 1:
            selected = "once" if index % 2 else "deny"
            return runtime.resolve_patch_interaction(*values, selected)
        return runtime.claim_patch_interaction(*values)

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(replay, range(12)))

    assert results == [
        False, False, None,
        False, False, None,
        False, False, None,
        False, False, None,
    ]
    assert len(runtime._patch_interactions) == 1
    assert next(iter(runtime._patch_interactions.values())).state == "consumed"


def test_task6_patch_interaction_concurrent_resolve_and_claim_are_linearizable():
    posted = []
    runtime = active_task4_runtime(posted)
    values = patch_interaction_args(object())
    assert runtime.register_patch_interaction(*values) is True
    start = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        resolve_future = executor.submit(
            lambda: (start.wait(), runtime.resolve_patch_interaction(*values, "selected"))[1]
        )
        claim_future = executor.submit(
            lambda: (start.wait(), runtime.claim_patch_interaction(*values))[1]
        )
        resolved = resolve_future.result()
        first_claim = claim_future.result()

    assert resolved is True
    second_claim = runtime.claim_patch_interaction(*values)
    assert [first_claim, second_claim].count("selected") == 1
    assert [first_claim, second_claim].count(None) == 1
    assert len(runtime._patch_interactions) == 1
    assert next(iter(runtime._patch_interactions.values())).state == "consumed"
