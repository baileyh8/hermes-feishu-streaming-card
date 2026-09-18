from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, Thread
from time import monotonic

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card import runtime_control
from hermes_feishu_card.event_auth import sign_event_request
from hermes_feishu_card.runtime_control import (
    RUNTIME_HOOK_GENERATION,
    RuntimeControlEmitter,
    RuntimeControlEvent,
    RuntimeControlValidationError,
    RuntimeIntegrityFenceBinding,
    RuntimeIntegritySupervisor,
    RuntimeProofVerifier,
    acquire_runtime_control,
    inspect_runtime_integrity_review,
    runtime_events_url,
    sign_runtime_request,
)


@pytest.fixture(autouse=True)
def reset_runtime_control_owners():
    runtime_control.reset_runtime_control_for_tests()
    yield
    runtime_control.reset_runtime_control_for_tests()


def _payload(**changes):
    payload = {
        "schema_version": "1",
        "event": "runtime.hello",
        "runtime_id": "runtime-1234567890",
        "sequence": 1,
        "created_at": 100.0,
        "hook_generation": RUNTIME_HOOK_GENERATION,
        "package_version": "4.1.0",
    }
    payload.update(changes)
    if payload.get("schema_version") == "2":
        payload.setdefault("active_sessions", 0)
        payload.setdefault("admission_draining", False)
        payload.setdefault("active_work_count_complete", True)
        payload.setdefault("drain_home_verified", True)
    return payload


def _fence_binding(seed: str = "a") -> RuntimeIntegrityFenceBinding:
    return RuntimeIntegrityFenceBinding(
        target_identity=seed * 64,
        plan_fingerprint=("b" if seed != "b" else "c") * 64,
    )


def test_runtime_control_event_accepts_only_bounded_safe_fields():
    event = RuntimeControlEvent.from_dict(_payload())

    assert event.event == "runtime.hello"
    assert event.sequence == 1

    current = RuntimeControlEvent.from_dict(
        _payload(
            schema_version="2",
            active_sessions=3,
            admission_draining=True,
            active_work_count_complete=True,
            drain_home_verified=True,
        )
    )
    assert current.active_sessions == 3
    assert current.admission_draining is True
    assert current.active_work_count_complete is True
    assert current.drain_home_verified is True

    for changes in (
        {"event": "message.completed"},
        {"sequence": -1},
        {"runtime_id": "short"},
        {"package_version": "v" * 129},
        {"local_path": "/private/secret"},
        {"schema_version": "2", "active_sessions": -1},
        {"schema_version": "2", "admission_draining": "yes"},
        {"schema_version": "2", "active_work_count_complete": "yes"},
        {"schema_version": "2", "drain_home_verified": "yes"},
    ):
        with pytest.raises(RuntimeControlValidationError):
            RuntimeControlEvent.from_dict(_payload(**changes))


def test_runtime_proof_binds_body_rejects_replay_and_is_domain_separated():
    secret = b"r" * 32
    body = json.dumps(_payload(), sort_keys=True).encode()
    headers = sign_runtime_request(
        secret,
        body,
        timestamp=100,
        nonce="nonce-1234567890",
    )
    verifier = RuntimeProofVerifier(secret, now=lambda: 100.0)

    verifier.verify(headers, body)
    with pytest.raises(RuntimeControlValidationError, match="replayed"):
        verifier.verify(headers, body)

    event_headers = sign_event_request(
        secret,
        body,
        timestamp=100,
        nonce="event-nonce-123456",
    )
    with pytest.raises(RuntimeControlValidationError, match="invalid"):
        RuntimeProofVerifier(secret, now=lambda: 100.0).verify(event_headers, body)


def test_runtime_proof_rejects_expired_and_wrong_body():
    secret = b"r" * 32
    body = b'{}'
    headers = sign_runtime_request(
        secret,
        body,
        timestamp=100,
        nonce="nonce-1234567890",
    )

    with pytest.raises(RuntimeControlValidationError, match="expired"):
        RuntimeProofVerifier(secret, now=lambda: 106.0).verify(headers, body)
    with pytest.raises(RuntimeControlValidationError, match="invalid"):
        RuntimeProofVerifier(secret, now=lambda: 100.0).verify(headers, b'{"x":1}')


def test_runtime_events_url_replaces_events_path_without_leaking_query():
    assert (
        runtime_events_url("http://127.0.0.1:18765/events?token=ignored")
        == "http://127.0.0.1:18765/runtime/events"
    )


def test_emitter_rereads_transport_secret_and_increments_sequence():
    secrets = iter((b"a" * 32, b"b" * 32))
    calls = []
    clock = iter((100.0, 101.0))
    emitter = RuntimeControlEmitter(
        event_url="http://127.0.0.1:18765/events",
        hook_generation=RUNTIME_HOOK_GENERATION,
        package_version="4.1.0",
        runtime_id="runtime-1234567890",
        now=lambda: next(clock),
        secret_reader=lambda: next(secrets),
        poster=lambda url, body, headers, timeout: calls.append(
            (url, json.loads(body), headers, timeout)
        )
        or True,
    )

    assert emitter.emit_once("runtime.hello") is True
    assert emitter.emit_once("runtime.heartbeat") is True

    assert [call[1]["sequence"] for call in calls] == [1, 2]
    assert [call[1]["event"] for call in calls] == [
        "runtime.hello",
        "runtime.heartbeat",
    ]
    assert [call[1]["active_sessions"] for call in calls] == [0, 0]
    assert [call[1]["admission_draining"] for call in calls] == [False, False]
    assert [call[1]["active_work_count_complete"] for call in calls] == [False, False]
    assert [call[1]["drain_home_verified"] for call in calls] == [False, False]
    assert calls[0][2] != calls[1][2]
    assert all(call[0].endswith("/runtime/events") for call in calls)


def test_emitter_fails_open_when_secret_or_post_is_unavailable():
    missing = RuntimeControlEmitter(
        event_url="http://127.0.0.1:18765/events",
        hook_generation=RUNTIME_HOOK_GENERATION,
        package_version="4.1.0",
        secret_reader=lambda: None,
    )
    failing = RuntimeControlEmitter(
        event_url="http://127.0.0.1:18765/events",
        hook_generation=RUNTIME_HOOK_GENERATION,
        package_version="4.1.0",
        secret_reader=lambda: b"r" * 32,
        poster=lambda *_args: (_ for _ in ()).throw(OSError("offline")),
    )

    assert missing.emit_once("runtime.hello") is False
    assert failing.emit_once("runtime.hello") is False


@pytest.mark.parametrize(
    "url",
    (
        "http://127.0.0.1:18765/runtime/events",
        "http://localhost:18765/runtime/events",
        "http://[::1]:18765/runtime/events",
    ),
)
def test_runtime_control_post_bypasses_environment_proxy_for_loopback(
    monkeypatch,
    url,
):
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"{}"

    class NoProxyOpener:
        def open(self, req, timeout):
            calls.append((req.full_url, timeout))
            return Response()

    monkeypatch.setattr(runtime_control, "_NO_PROXY_OPENER", NoProxyOpener())
    monkeypatch.setattr(
        runtime_control.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("loopback runtime control used the system proxy path")
        ),
    )

    assert runtime_control._post_runtime_request(url, b"{}", {}, 1.0) is True
    assert calls == [(url, 1.0)]


def test_runtime_control_post_preserves_system_proxy_path_for_remote_endpoint(
    monkeypatch,
):
    calls = []

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b""

    class FailingNoProxyOpener:
        def open(self, *_args, **_kwargs):
            raise AssertionError("remote runtime control bypassed the system proxy")

    monkeypatch.setattr(
        runtime_control,
        "_NO_PROXY_OPENER",
        FailingNoProxyOpener(),
    )
    monkeypatch.setattr(
        runtime_control.request,
        "urlopen",
        lambda req, timeout: calls.append((req.full_url, timeout)) or Response(),
    )

    assert runtime_control._post_runtime_request(
        "https://sidecar.example/runtime/events",
        b"{}",
        {},
        1.0,
    ) is True
    assert calls == [("https://sidecar.example/runtime/events", 1.0)]


def test_supervisor_has_independent_liveness_readiness_state_machine():
    clock = [0.0]
    supervisor = RuntimeIntegritySupervisor(
        mode="notify",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: clock[0],
        startup_grace_seconds=30.0,
        stale_after_seconds=45.0,
    )

    assert supervisor.snapshot() == {
        "status": "starting",
        "reason": "runtime_heartbeat_waiting",
        "integrity_mode": "notify",
        "runtime_seen": False,
        "generation_match": False,
        "restart_required": False,
        "last_seen_age_seconds": None,
        "runtime_id_hash": "",
        "last_sequence": 0,
        "active_sessions": None,
        "admission_draining": None,
        "active_work_count_complete": None,
        "drain_home_verified": None,
    }

    clock[0] = 31.0
    assert supervisor.snapshot()["reason"] == "runtime_heartbeat_missing"

    supervisor.record(RuntimeControlEvent.from_dict(_payload(created_at=31.0)))
    assert supervisor.snapshot()["status"] == "ready"
    assert supervisor.snapshot()["generation_match"] is True

    clock[0] = 77.0
    assert supervisor.snapshot()["reason"] == "runtime_heartbeat_stale"


def test_supervisor_requires_matching_generation_and_can_mark_restart_required():
    supervisor = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 100.0,
    )
    supervisor.record(
        RuntimeControlEvent.from_dict(
            _payload(hook_generation="older-hook", package_version="4.0.21")
        )
    )
    assert supervisor.snapshot()["reason"] == "gateway_restart_required"

    supervisor.mark_restart_required()
    supervisor.record(
        RuntimeControlEvent.from_dict(
            _payload(runtime_id="runtime-restarted-123", created_at=101.0)
        )
    )
    assert supervisor.snapshot()["status"] == "ready"
    assert supervisor.snapshot()["restart_required"] is False


def test_restart_fence_survives_sidecar_restart_until_different_matching_hello(
    tmp_path,
):
    state_root = tmp_path / "private-state"
    old_runtime_id = "runtime-before-repair-123"
    new_runtime_id = "runtime-after-repair-456"
    first = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 100.0,
        state_directory=state_root,
    )
    assert first.record(
        RuntimeControlEvent.from_dict(
            _payload(runtime_id=old_runtime_id, created_at=99.0)
        )
    )
    first.mark_restart_required(binding=_fence_binding())

    persisted = (state_root / "runtime-integrity-fence.json").read_text()
    assert old_runtime_id not in persisted
    assert new_runtime_id not in persisted
    assert os.stat(state_root).st_mode & 0o777 == 0o700
    assert os.stat(state_root / "runtime-integrity-fence.json").st_mode & 0o777 == 0o600

    restarted = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 101.0,
        state_directory=state_root,
    )
    assert restarted.snapshot()["reason"] == "gateway_restart_required"

    current = inspect_runtime_integrity_review(state_root)
    with pytest.raises(ValueError, match="could not be acknowledged safely"):
        runtime_control.acknowledge_runtime_integrity_review(
            state_root,
            expected_state_token=current.state_token,
            expected_binding=_fence_binding("d"),
        )

    assert restarted.record(
        RuntimeControlEvent.from_dict(
            _payload(
                event="runtime.heartbeat",
                runtime_id=old_runtime_id,
                sequence=2,
                created_at=101.0,
            )
        )
    )
    assert restarted.snapshot()["reason"] == "gateway_restart_required"
    assert restarted.record(
        RuntimeControlEvent.from_dict(
            _payload(
                runtime_id=old_runtime_id,
                sequence=3,
                created_at=102.0,
            )
        )
    )
    assert restarted.snapshot()["reason"] == "gateway_restart_required"

    assert restarted.record(
        RuntimeControlEvent.from_dict(
            _payload(
                event="runtime.heartbeat",
                runtime_id=new_runtime_id,
                sequence=1,
                created_at=103.0,
            )
        )
    )
    assert restarted.snapshot()["reason"] == "gateway_restart_required"
    assert restarted.record(
        RuntimeControlEvent.from_dict(
            _payload(
                runtime_id=new_runtime_id,
                sequence=2,
                created_at=104.0,
            )
        )
    )
    assert restarted.snapshot()["status"] == "ready"

    reloaded = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 105.0,
        state_directory=state_root,
    )
    assert reloaded.snapshot()["restart_required"] is False


def test_restart_fence_without_pre_repair_runtime_requires_manual_resolution(
    tmp_path,
):
    state_root = tmp_path / "private-state"
    first = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 100.0,
        state_directory=state_root,
    )

    first.mark_restart_required(binding=_fence_binding())

    assert first.snapshot()["reason"] == "manual_review_required"
    restarted = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 101.0,
        state_directory=state_root,
    )
    assert restarted.record(
        RuntimeControlEvent.from_dict(
            _payload(runtime_id="runtime-delayed-old-123", created_at=102.0)
        )
    )
    snapshot = restarted.snapshot()
    assert snapshot["reason"] == "manual_review_required"
    assert snapshot["restart_required"] is True


def test_manual_review_fence_survives_restart_and_matching_new_runtime_hello(
    tmp_path,
):
    state_root = tmp_path / "private-state"
    first = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 100.0,
        state_directory=state_root,
    )
    first.mark_manual_review_required(binding=_fence_binding())

    restarted = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 101.0,
        state_directory=state_root,
    )
    assert restarted.snapshot()["reason"] == "manual_review_required"
    assert restarted.record(
        RuntimeControlEvent.from_dict(
            _payload(runtime_id="runtime-after-review-789", created_at=102.0)
        )
    )
    assert restarted.snapshot()["reason"] == "manual_review_required"


def test_acknowledge_manual_review_preserves_independent_restart_fence(tmp_path):
    state_root = tmp_path / "private-state"
    binding = _fence_binding()
    first = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 100.0,
        state_directory=state_root,
    )
    assert first.record(
        RuntimeControlEvent.from_dict(
            _payload(runtime_id="runtime-before-review-123", created_at=100.0)
        )
    )
    first.mark_restart_required(binding=binding)
    first.mark_manual_review_required(binding=binding)
    before = json.loads(
        (state_root / "runtime-integrity-fence.json").read_text(encoding="utf-8")
    )

    review = inspect_runtime_integrity_review(state_root)
    assert review.binding == binding
    assert runtime_control.acknowledge_runtime_integrity_review(
        state_root,
        expected_state_token=review.state_token,
        expected_binding=binding,
    ) is True

    after = json.loads(
        (state_root / "runtime-integrity-fence.json").read_text(encoding="utf-8")
    )
    assert after["manual_review_required"] is False
    assert after["restart_required"] is True
    assert after["pre_repair_runtime_hash"] == before["pre_repair_runtime_hash"]
    restarted = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 101.0,
        state_directory=state_root,
    )
    assert restarted.snapshot()["reason"] == "gateway_restart_required"


def test_acknowledge_manual_review_is_noop_when_no_review_is_pending(tmp_path):
    state_root = tmp_path / "private-state"
    RuntimeIntegritySupervisor(mode="safe", state_directory=state_root)
    review = inspect_runtime_integrity_review(state_root)

    assert runtime_control.acknowledge_runtime_integrity_review(
        state_root,
        expected_state_token=review.state_token,
        expected_binding=_fence_binding(),
    ) is False


def test_acknowledge_manual_review_clears_unresolvable_empty_hash_restart_fence(
    tmp_path,
):
    state_root = tmp_path / "private-state"
    state_root.mkdir(mode=0o700)
    fence = state_root / "runtime-integrity-fence.json"
    fence.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "restart_required": True,
                "manual_review_required": True,
                "pre_repair_runtime_hash": "",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    fence.chmod(0o600)

    review = inspect_runtime_integrity_review(state_root)
    assert review.legacy_unbound_empty_restart is True
    binding = _fence_binding()
    with pytest.raises(ValueError, match="could not be acknowledged safely"):
        runtime_control.acknowledge_runtime_integrity_review(
            state_root,
            expected_state_token=review.state_token,
            expected_binding=binding,
        )
    assert runtime_control.acknowledge_runtime_integrity_review(
        state_root,
        expected_state_token=review.state_token,
        expected_binding=binding,
        allow_legacy_unbound_empty_restart=True,
    ) is True

    payload = json.loads(fence.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": "2",
        "restart_required": False,
        "manual_review_required": False,
        "pre_repair_runtime_hash": "",
        "target_identity": binding.target_identity,
        "plan_fingerprint": binding.plan_fingerprint,
    }


def test_acknowledge_manual_review_refuses_non_private_fence(tmp_path):
    state_root = tmp_path / "private-state"
    binding = _fence_binding()
    supervisor = RuntimeIntegritySupervisor(mode="safe", state_directory=state_root)
    supervisor.mark_manual_review_required(binding=binding)
    fence = state_root / "runtime-integrity-fence.json"
    fence.chmod(0o644)

    with pytest.raises(ValueError, match="could not be inspected safely"):
        inspect_runtime_integrity_review(state_root)

    assert json.loads(fence.read_text(encoding="utf-8"))["manual_review_required"]


def test_bound_fence_acknowledge_rejects_wrong_binding_and_stale_snapshot(tmp_path):
    state_root = tmp_path / "private-state"
    binding = _fence_binding("a")
    supervisor = RuntimeIntegritySupervisor(mode="safe", state_directory=state_root)
    supervisor.mark_manual_review_required(binding=binding)
    review = inspect_runtime_integrity_review(state_root)
    before = (state_root / "runtime-integrity-fence.json").read_bytes()

    with pytest.raises(ValueError, match="could not be acknowledged safely"):
        runtime_control.acknowledge_runtime_integrity_review(
            state_root,
            expected_state_token=review.state_token,
            expected_binding=_fence_binding("d"),
        )
    assert (state_root / "runtime-integrity-fence.json").read_bytes() == before

    supervisor.mark_restart_required(binding=binding)
    with pytest.raises(ValueError, match="could not be acknowledged safely"):
        runtime_control.acknowledge_runtime_integrity_review(
            state_root,
            expected_state_token=review.state_token,
            expected_binding=binding,
        )
    assert inspect_runtime_integrity_review(state_root).manual_review_required


def test_bound_fence_acknowledge_allows_explicit_same_target_plan_transition(
    tmp_path,
):
    state_root = tmp_path / "private-state"
    previous_binding = RuntimeIntegrityFenceBinding(
        target_identity="a" * 64,
        plan_fingerprint="b" * 64,
    )
    current_binding = RuntimeIntegrityFenceBinding(
        target_identity=previous_binding.target_identity,
        plan_fingerprint="c" * 64,
    )
    supervisor = RuntimeIntegritySupervisor(mode="safe", state_directory=state_root)
    assert supervisor.record(
        RuntimeControlEvent.from_dict(
            _payload(runtime_id="runtime-before-transition-123", created_at=100.0)
        )
    )
    supervisor.mark_restart_required(binding=previous_binding)
    supervisor.mark_manual_review_required(binding=previous_binding)
    review = inspect_runtime_integrity_review(state_root)
    before = json.loads(
        (state_root / "runtime-integrity-fence.json").read_text(encoding="utf-8")
    )

    with pytest.raises(ValueError, match="could not be acknowledged safely"):
        runtime_control.acknowledge_runtime_integrity_review(
            state_root,
            expected_state_token=review.state_token,
            expected_binding=current_binding,
        )

    assert runtime_control.acknowledge_runtime_integrity_review(
        state_root,
        expected_state_token=review.state_token,
        expected_binding=current_binding,
        allow_same_target_plan_transition=True,
    ) is True

    after = json.loads(
        (state_root / "runtime-integrity-fence.json").read_text(encoding="utf-8")
    )
    assert after["target_identity"] == current_binding.target_identity
    assert after["plan_fingerprint"] == current_binding.plan_fingerprint
    assert after["manual_review_required"] is False
    assert after["restart_required"] is True
    assert after["pre_repair_runtime_hash"] == before["pre_repair_runtime_hash"]


def test_bound_fence_plan_transition_never_changes_target_identity(tmp_path):
    state_root = tmp_path / "private-state"
    previous_binding = RuntimeIntegrityFenceBinding(
        target_identity="a" * 64,
        plan_fingerprint="b" * 64,
    )
    supervisor = RuntimeIntegritySupervisor(mode="safe", state_directory=state_root)
    supervisor.mark_manual_review_required(binding=previous_binding)
    review = inspect_runtime_integrity_review(state_root)
    before = (state_root / "runtime-integrity-fence.json").read_bytes()

    with pytest.raises(ValueError, match="could not be acknowledged safely"):
        runtime_control.acknowledge_runtime_integrity_review(
            state_root,
            expected_state_token=review.state_token,
            expected_binding=RuntimeIntegrityFenceBinding(
                target_identity="d" * 64,
                plan_fingerprint="e" * 64,
            ),
            allow_same_target_plan_transition=True,
        )

    assert (state_root / "runtime-integrity-fence.json").read_bytes() == before


def test_legacy_unbound_nonempty_restart_fence_is_never_acknowledged(tmp_path):
    state_root = tmp_path / "private-state"
    state_root.mkdir(mode=0o700)
    fence = state_root / "runtime-integrity-fence.json"
    fence.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "restart_required": True,
                "manual_review_required": True,
                "pre_repair_runtime_hash": "d" * 64,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    fence.chmod(0o600)
    review = inspect_runtime_integrity_review(state_root)

    assert review.legacy_unbound_empty_restart is False
    with pytest.raises(ValueError, match="could not be acknowledged safely"):
        runtime_control.acknowledge_runtime_integrity_review(
            state_root,
            expected_state_token=review.state_token,
            expected_binding=_fence_binding(),
            allow_legacy_unbound_empty_restart=True,
        )
    assert fence.read_text(encoding="utf-8").find('"schema_version": "1"') >= 0


def test_new_persisted_fence_requires_binding_and_does_not_leak_target_path(tmp_path):
    state_root = tmp_path / "private-state"
    supervisor = RuntimeIntegritySupervisor(mode="safe", state_directory=state_root)

    supervisor.mark_manual_review_required()
    assert supervisor.snapshot()["reason"] == "manual_review_required"
    assert not (state_root / "runtime-integrity-fence.json").exists()

    binding = _fence_binding()
    supervisor.mark_manual_review_required(binding=binding)
    payload = (state_root / "runtime-integrity-fence.json").read_text(
        encoding="utf-8"
    )
    assert '"schema_version":"2"' in payload
    assert str(tmp_path) not in payload
    assert inspect_runtime_integrity_review(state_root).binding == binding


def test_fence_state_lstat_permission_error_fails_closed(monkeypatch, tmp_path):
    state_root = tmp_path / "private-state"
    state_root.mkdir(mode=0o700)
    real_lstat = runtime_control.os.lstat

    def guarded_lstat(path):
        if runtime_control.Path(path).name == "runtime-integrity-fence.json":
            raise PermissionError("denied")
        return real_lstat(path)

    monkeypatch.setattr(runtime_control.os, "lstat", guarded_lstat)

    supervisor = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        state_directory=state_root,
    )

    assert supervisor.snapshot()["reason"] == "manual_review_required"


def test_fence_load_fails_closed_when_private_root_is_swapped_during_read(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "private-state"
    replacement_root = tmp_path / "replacement-private-state"
    displaced_root = tmp_path / "displaced-private-state"

    def write_state(root, *, restart_required, runtime_hash):
        root.mkdir(mode=0o700)
        path = root / "runtime-integrity-fence.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "restart_required": restart_required,
                    "manual_review_required": False,
                    "pre_repair_runtime_hash": runtime_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    write_state(state_root, restart_required=False, runtime_hash="")
    write_state(
        replacement_root,
        restart_required=True,
        runtime_hash="a" * 64,
    )
    real_fdopen = runtime_control.os.fdopen
    swapped = []

    class SwappingHandle:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self, *args, **kwargs):
            raw = self.handle.read(*args, **kwargs)
            state_root.rename(displaced_root)
            replacement_root.rename(state_root)
            swapped.append(True)
            return raw

    def swapping_fdopen(*args, **kwargs):
        return SwappingHandle(real_fdopen(*args, **kwargs))

    monkeypatch.setattr(runtime_control.os, "fdopen", swapping_fdopen)

    supervisor = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        state_directory=state_root,
    )

    assert swapped == [True]
    assert supervisor.snapshot()["reason"] == "manual_review_required"
    assert json.loads(
        (state_root / "runtime-integrity-fence.json").read_text(encoding="utf-8")
    )["restart_required"] is True


def test_atomic_fence_write_never_replaces_or_unlinks_through_swapped_root(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "private-state"
    displaced_root = tmp_path / "displaced-private-state"
    state_root.mkdir(mode=0o700)
    real_replace = runtime_control.os.replace
    decoys = []

    def swap_root_then_replace(source, destination, *args, **kwargs):
        state_root.rename(displaced_root)
        state_root.mkdir(mode=0o700)
        decoy = state_root / runtime_control.Path(source).name
        decoy.write_text("user-owned", encoding="utf-8")
        decoys.append(decoy)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(runtime_control.os, "replace", swap_root_then_replace)
    supervisor = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        state_directory=state_root,
    )

    supervisor.mark_restart_required(binding=_fence_binding())

    assert supervisor.snapshot()["reason"] == "manual_review_required"
    assert len(decoys) == 1
    assert decoys[0].read_text(encoding="utf-8") == "user-owned"


def test_atomic_fence_write_fails_closed_if_root_loses_private_mode(
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "private-state"
    state_root.mkdir(mode=0o700)
    real_replace = runtime_control.os.replace

    def expose_root_then_replace(source, destination, *args, **kwargs):
        state_root.chmod(0o755)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(runtime_control.os, "replace", expose_root_then_replace)
    supervisor = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        state_directory=state_root,
    )

    supervisor.mark_restart_required(binding=_fence_binding())

    assert supervisor.snapshot()["reason"] == "manual_review_required"


def test_matching_runtime_hello_does_not_clear_manual_review_requirement():
    supervisor = RuntimeIntegritySupervisor(
        mode="safe",
        expected_hook_generation=RUNTIME_HOOK_GENERATION,
        expected_package_version="4.1.0",
        now=lambda: 100.0,
    )
    supervisor.mark_manual_review_required()

    assert supervisor.record(
        RuntimeControlEvent.from_dict(
            _payload(runtime_id="runtime-reviewed-123", created_at=101.0)
        )
    )

    snapshot = supervisor.snapshot()
    assert snapshot["status"] == "degraded"
    assert snapshot["reason"] == "manual_review_required"


def test_supervisor_off_mode_is_disabled_even_without_runtime():
    supervisor = RuntimeIntegritySupervisor(mode="off")

    assert supervisor.snapshot()["status"] == "disabled"
    assert supervisor.snapshot()["reason"] == "integrity_disabled"


def test_existing_startup_adapter_call_starts_runtime_control_without_new_patch(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: type(
            "Config",
            (),
            {
                "enabled": True,
                "event_url": "http://127.0.0.1:18765/events",
            },
        )(),
    )
    monkeypatch.setattr(
        hook_runtime,
        "start_runtime_control",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    assert (
        hook_runtime.install_feishu_command_card_adapter_methods(
            type("Runner", (), {"adapters": {}})()
        )
        is False
    )

    assert calls == [
        {
            "event_url": "http://127.0.0.1:18765/events",
            "package_version": hook_runtime.__version__,
            "active_work_snapshot_provider": hook_runtime.gateway_active_work_snapshot,
            "admission_draining_provider": hook_runtime.gateway_external_drain_active,
            "drain_home_verified_provider": hook_runtime.gateway_drain_home_verified,
        }
    ]


def test_runtime_control_concurrent_acquire_shares_one_worker_until_last_release(
    monkeypatch,
):
    created = []
    running = Event()
    stopped = Event()
    counter_lock = Lock()

    class FakeEmitter:
        def __init__(self, **kwargs):
            with counter_lock:
                created.append(kwargs)

        def run(self, stop_event, interval_seconds):
            running.set()
            stop_event.wait()
            stopped.set()

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)

    def acquire(_index):
        return acquire_runtime_control(
            event_url="http://127.0.0.1:18765/events",
            package_version="4.3.0",
            interval_seconds=30.0,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        leases = list(executor.map(acquire, range(8)))

    assert all(lease is not None for lease in leases)
    assert running.wait(timeout=0.5)
    assert len(created) == 1
    for lease in leases[:-1]:
        assert lease.close() is None
    assert stopped.is_set() is False
    assert leases[-1].close() is None
    assert stopped.wait(timeout=0.5)


def test_runtime_control_refuses_different_live_config_and_double_close_is_noop(
    monkeypatch,
):
    stopped = Event()

    class FakeEmitter:
        def __init__(self, **_kwargs):
            pass

        def run(self, stop_event, interval_seconds):
            stop_event.wait()
            stopped.set()

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)
    first = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
    )
    assert first is not None
    assert acquire_runtime_control(
        event_url="http://127.0.0.1:28765/events",
        package_version="4.3.0",
    ) is None

    assert first.close() is None
    assert stopped.wait(timeout=0.5)
    before = monotonic()
    assert first.close() is None
    assert monotonic() - before < 0.1


def test_runtime_control_same_lease_concurrent_close_releases_owner_once(monkeypatch):
    release_calls = []
    owner = object()
    lease = runtime_control.RuntimeControlLease(owner)
    monkeypatch.setattr(
        runtime_control,
        "_release_runtime_control",
        lambda released: release_calls.append(released),
    )

    with ThreadPoolExecutor(max_workers=16) as executor:
        assert list(executor.map(lambda _index: lease.close(), range(32))) == [None] * 32

    assert release_calls == [owner]


def test_runtime_control_thread_start_failure_leaves_no_worker_or_owner(monkeypatch):
    class FakeEmitter:
        def __init__(self, **_kwargs):
            pass

        def run(self, stop_event, interval_seconds):
            raise AssertionError("thread must not run")

    class FailingThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)
    monkeypatch.setattr(runtime_control.threading, "Thread", FailingThread)

    assert acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
    ) is None
    assert runtime_control._CONTROL_EMITTER is None
    assert runtime_control._CONTROL_STOP is None
    assert runtime_control._CONTROL_THREAD is None
    assert runtime_control._CONTROL_CONFIG is None
    assert runtime_control._CONTROL_OWNERS == set()


def test_legacy_start_owner_survives_temporary_shared_lease_rollback(monkeypatch):
    stopped = Event()
    starts = []

    class FakeEmitter:
        def __init__(self, **kwargs):
            starts.append(kwargs)

        def run(self, stop_event, interval_seconds):
            stop_event.wait()
            stopped.set()

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)
    options = {
        "event_url": "http://127.0.0.1:18765/events",
        "package_version": "4.3.0",
    }
    assert runtime_control.start_runtime_control(**options) is True
    temporary = acquire_runtime_control(**options)
    assert temporary is not None
    assert temporary.close() is None
    assert stopped.is_set() is False
    assert len(starts) == 1

    runtime_control.reset_runtime_control_for_tests()
    assert stopped.wait(timeout=0.5)


def test_shared_worker_combines_every_owner_provider_conservatively(monkeypatch):
    captured = {}

    class FakeEmitter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, stop_event, interval_seconds):
            stop_event.wait()

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)
    first = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
        active_work_snapshot_provider=lambda: (0, True),
        admission_draining_provider=lambda: False,
        drain_home_verified_provider=lambda: True,
    )
    second = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
        active_work_snapshot_provider=lambda: (2, False),
        admission_draining_provider=lambda: True,
        drain_home_verified_provider=lambda: False,
    )
    assert first is not None and second is not None

    assert captured["active_work_snapshot_provider"]() == (2, False)
    assert captured["admission_draining_provider"]() is True
    assert captured["drain_home_verified_provider"]() is False

    second.close()
    assert captured["active_work_snapshot_provider"]() == (0, True)
    assert captured["admission_draining_provider"]() is False
    assert captured["drain_home_verified_provider"]() is True
    first.close()


def test_shared_worker_unknown_owner_never_reports_complete_zero(monkeypatch):
    captured = {}

    class FakeEmitter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, stop_event, interval_seconds):
            stop_event.wait()

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)
    known = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
        active_work_snapshot_provider=lambda: (0, True),
        admission_draining_provider=lambda: False,
        drain_home_verified_provider=lambda: True,
    )
    unknown = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
    )
    assert known is not None and unknown is not None
    assert captured["active_work_snapshot_provider"]() == (0, False)
    assert captured["admission_draining_provider"]() is True
    assert captured["drain_home_verified_provider"]() is False
    unknown.close()
    known.close()


@pytest.mark.parametrize('options', [
    {'gateway_admission_dependent': 'true'},
    {'gateway_admission_dependent': True},
    {'gateway_admission_dependent': True, 'active_work_snapshot_provider': lambda: (0, True),
     'admission_draining_provider': lambda: False},
    {'gateway_admission_dependent': True, 'active_work_snapshot_provider': lambda: (0, True),
     'drain_home_verified_provider': lambda: True},
])
def test_dependent_owner_cannot_claim_gateway_authority(options):
    assert acquire_runtime_control(
        event_url='http://127.0.0.1:18765/events', package_version='4.6.2', **options
    ) is None
    assert runtime_control._CONTROL_OWNERS == set()


def test_plugin_local_owner_is_incomplete_until_gateway_aggregate_owner_exists(
    monkeypatch,
):
    captured = {}

    class FakeEmitter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, stop_event, interval_seconds):
            stop_event.wait()

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)
    plugin = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
        active_work_snapshot_provider=lambda: (0, True),
    )
    assert plugin is not None
    assert captured["active_work_snapshot_provider"]() == (0, False)

    gateway = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
        active_work_snapshot_provider=lambda: (0, True),
        admission_draining_provider=lambda: False,
        drain_home_verified_provider=lambda: True,
    )
    assert gateway is not None
    assert captured["active_work_snapshot_provider"]() == (0, True)

    gateway.close()
    assert captured["active_work_snapshot_provider"]() == (0, False)
    plugin.close()


def test_shared_worker_provider_exception_is_conservative(monkeypatch):
    captured = {}

    def broken():
        raise RuntimeError("provider failed")

    class FakeEmitter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, stop_event, interval_seconds):
            stop_event.wait()

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)
    lease = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
        active_work_snapshot_provider=broken,
        admission_draining_provider=broken,
        drain_home_verified_provider=broken,
    )
    assert lease is not None
    assert captured["active_work_snapshot_provider"]() == (0, False)
    assert captured["admission_draining_provider"]() is True
    assert captured["drain_home_verified_provider"]() is False
    lease.close()


def test_last_owner_join_timeout_keeps_truthful_poisoned_worker_until_exit(
    monkeypatch,
):
    threads = []

    class FakeEmitter:
        def __init__(self, **_kwargs):
            pass

        def run(self, stop_event, interval_seconds):
            return None

    class SlowStoppingThread:
        def __init__(self, **_kwargs):
            self.alive = True
            threads.append(self)

        def start(self):
            return None

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            return None

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)
    monkeypatch.setattr(runtime_control.threading, "Thread", SlowStoppingThread)
    lease = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
    )
    assert lease is not None
    lease.close()

    assert runtime_control._CONTROL_THREAD is threads[0]
    assert runtime_control._CONTROL_STOPPING is True
    assert acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
    ) is None
    assert len(threads) == 1

    threads[0].alive = False
    replacement = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
    )
    assert replacement is not None
    assert len(threads) == 2
    threads[1].alive = False
    replacement.close()


def test_combined_snapshot_retries_after_real_acquire_changes_owner_epoch(
    monkeypatch,
):
    captured = {}
    provider_entered = Event()
    provider_release = Event()
    acquire_finished = Event()
    first_calls = []
    results = []
    second_leases = []

    class FakeEmitter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, stop_event, interval_seconds):
            stop_event.wait()

    def first_active():
        first_calls.append(True)
        if len(first_calls) == 1:
            provider_entered.set()
            provider_release.wait(timeout=1.0)
        return 0, True

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)
    first = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
        active_work_snapshot_provider=first_active,
        admission_draining_provider=lambda: False,
        drain_home_verified_provider=lambda: True,
    )
    assert first is not None

    sample_thread = Thread(
        target=lambda: results.append(captured["active_work_snapshot_provider"]())
    )
    sample_thread.start()
    assert provider_entered.wait(timeout=0.5)

    def acquire_second():
        second_leases.append(
            acquire_runtime_control(
                event_url="http://127.0.0.1:18765/events",
                package_version="4.3.0",
                active_work_snapshot_provider=lambda: (3, True),
            )
        )
        acquire_finished.set()

    acquire_thread = Thread(target=acquire_second)
    acquire_thread.start()
    acquired_without_provider_lock = acquire_finished.wait(timeout=0.5)
    provider_release.set()
    acquire_thread.join(timeout=0.5)
    sample_thread.join(timeout=0.5)

    assert acquired_without_provider_lock is True
    assert second_leases and second_leases[0] is not None
    assert results == [(3, True)]
    assert len(first_calls) >= 2

    second_leases[0].close()
    first.close()


def test_shared_emitter_uses_one_epoch_validated_bundle_per_event(monkeypatch):
    provider_entered = Event()
    provider_release = Event()
    first_calls = []
    emitted = []
    parked_threads = []

    class ParkedThread:
        def __init__(self, **_kwargs):
            self.alive = True
            parked_threads.append(self)

        def start(self):
            return None

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            self.alive = False

    def first_active():
        first_calls.append(True)
        if len(first_calls) == 1:
            provider_entered.set()
            provider_release.wait(timeout=1.0)
        return 0, True

    monkeypatch.setattr(runtime_control.threading, "Thread", ParkedThread)
    first = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
        active_work_snapshot_provider=first_active,
        admission_draining_provider=lambda: False,
        drain_home_verified_provider=lambda: True,
    )
    assert first is not None
    emitter = runtime_control._CONTROL_EMITTER
    assert isinstance(emitter, RuntimeControlEmitter)
    bundle_calls = []
    original_bundle = getattr(emitter, "_runtime_snapshot_provider", None)

    def bundled_snapshot():
        bundle_calls.append(True)
        assert original_bundle is not None
        return original_bundle()

    emitter._runtime_snapshot_provider = bundled_snapshot
    emitter._secret_reader = lambda: b"r" * 32

    def poster(_url, body, _headers, _timeout):
        emitted.append(json.loads(body.decode("utf-8")))
        return True

    emitter._poster = poster
    emit_results = []
    emit_thread = Thread(
        target=lambda: emit_results.append(emitter.emit_once("runtime.heartbeat"))
    )
    emit_thread.start()
    assert provider_entered.wait(timeout=0.5)

    second = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
        active_work_snapshot_provider=lambda: (4, True),
    )
    assert second is not None
    provider_release.set()
    emit_thread.join(timeout=0.5)

    assert emit_results == [True]
    assert bundle_calls == [True]
    assert len(first_calls) >= 2
    assert len(emitted) == 1
    assert emitted[0]["active_sessions"] == 4
    assert emitted[0]["active_work_count_complete"] is True
    assert emitted[0]["admission_draining"] is True
    assert emitted[0]["drain_home_verified"] is False

    second.close()
    first.close()
    assert parked_threads


def test_bundle_returns_conservative_snapshot_after_bounded_epoch_churn(
    monkeypatch,
):
    captured = {}
    churn_leases = []

    class FakeEmitter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, stop_event, interval_seconds):
            stop_event.wait()

    def churning_active():
        lease = acquire_runtime_control(
            event_url="http://127.0.0.1:18765/events",
            package_version="4.3.0",
            active_work_snapshot_provider=lambda: (0, True),
        )
        assert lease is not None
        churn_leases.append(lease)
        return 0, True

    monkeypatch.setattr(runtime_control, "RuntimeControlEmitter", FakeEmitter)
    first = acquire_runtime_control(
        event_url="http://127.0.0.1:18765/events",
        package_version="4.3.0",
        active_work_snapshot_provider=churning_active,
        admission_draining_provider=lambda: False,
        drain_home_verified_provider=lambda: True,
    )
    assert first is not None

    assert captured["runtime_snapshot_provider"]() == (0, False, True, False)
    assert len(churn_leases) == runtime_control._CONTROL_SNAPSHOT_ATTEMPTS

    for lease in reversed(churn_leases):
        lease.close()
    first.close()
