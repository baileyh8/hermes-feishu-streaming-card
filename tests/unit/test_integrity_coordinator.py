from __future__ import annotations

from types import SimpleNamespace

from hermes_feishu_card import integrity as integrity_module
from hermes_feishu_card.integrity import (
    RuntimeIntegrityCoordinator,
    build_runtime_integrity_fence_binding,
    sanitize_integrity_snapshot,
)
from hermes_feishu_card.runtime_control import (
    RUNTIME_HOOK_GENERATION,
    RuntimeControlEvent,
    RuntimeIntegritySupervisor,
)


class FakeSupervisor:
    def __init__(self, reason="runtime_heartbeat_missing"):
        self.reason = reason
        self.restart_required = 0
        self.manual_review_required = 0
        self.bindings = []

    def snapshot(self):
        return {"status": "degraded", "reason": self.reason}

    def mark_restart_required(self, *, binding=None):
        self.restart_required += 1
        self.bindings.append(binding)

    def mark_manual_review_required(self, *, binding=None):
        self.manual_review_required += 1
        self.bindings.append(binding)


def _plan(*, executable=True, fingerprint="evidence-1", state="stale_unpatched"):
    return SimpleNamespace(
        executable=executable,
        fingerprint=fingerprint,
        state=state,
        reason="verified_git_upgrade" if executable else "git_target_modified",
    )


def test_fence_binding_is_domain_separated_and_contains_no_target_path(tmp_path):
    hermes_root = tmp_path / "private-hermes-target"
    hermes_root.mkdir()

    first = build_runtime_integrity_fence_binding(hermes_root, "a" * 64)
    second = build_runtime_integrity_fence_binding(hermes_root, "b" * 64)

    assert len(first.target_identity) == 64
    assert len(first.plan_fingerprint) == 64
    assert first.target_identity != first.plan_fingerprint
    assert first.target_identity == second.target_identity
    assert first.plan_fingerprint != second.plan_fingerprint
    assert str(hermes_root) not in repr(first)


def test_fence_binding_does_not_require_path_stat_keyword_arguments(
    monkeypatch,
    tmp_path,
):
    """Python 3.9 Path.stat() does not accept follow_symlinks."""
    hermes_root = tmp_path / "hermes"
    hermes_root.mkdir()
    evidence = hermes_root.lstat()

    class Python39ResolvedPath:
        def expanduser(self):
            return self

        def resolve(self, *, strict=False):
            assert strict is True
            return self

        def stat(self):
            return evidence

        def lstat(self):
            return evidence

        def __str__(self):
            return str(hermes_root)

    monkeypatch.setattr(
        integrity_module,
        "Path",
        lambda _root: Python39ResolvedPath(),
    )

    binding = build_runtime_integrity_fence_binding(hermes_root, "a" * 64)

    assert len(binding.target_identity) == 64


def test_coordinator_binds_new_fence_to_target_and_exact_plan(tmp_path):
    hermes_root = tmp_path / "hermes"
    hermes_root.mkdir()
    supervisor = FakeSupervisor()
    plan = _plan(executable=False, fingerprint="c" * 64)
    coordinator = RuntimeIntegrityCoordinator(
        mode="safe",
        hermes_root=hermes_root,
        supervisor=supervisor,
        detector=lambda _root: object(),
        planner=lambda _detection: plan,
    )

    result = coordinator.check_once()

    assert result["status"] == "manual_review_required"
    assert supervisor.bindings == [
        build_runtime_integrity_fence_binding(hermes_root, plan.fingerprint)
    ]


def test_notify_mode_reports_verified_repair_without_mutating():
    supervisor = FakeSupervisor()
    executed = []
    coordinator = RuntimeIntegrityCoordinator(
        mode="notify",
        hermes_root="/sanitized-in-test",
        supervisor=supervisor,
        detector=lambda _root: object(),
        planner=lambda _detection: _plan(),
        executor=lambda *_args, **_kwargs: executed.append(True),
    )

    result = coordinator.check_once()

    assert result == {
        "status": "repair_available",
        "reason": "verified_git_upgrade",
        "attempted": False,
    }
    assert executed == []
    assert supervisor.restart_required == 0


def test_safe_mode_executes_once_per_evidence_and_never_restarts_gateway():
    supervisor = FakeSupervisor()
    executed = []

    def execute(_detection, *, expected_fingerprint):
        executed.append(expected_fingerprint)
        return SimpleNamespace(status="repaired", restart_required=True)

    coordinator = RuntimeIntegrityCoordinator(
        mode="safe",
        hermes_root="/sanitized-in-test",
        supervisor=supervisor,
        detector=lambda _root: object(),
        planner=lambda _detection: _plan(),
        executor=execute,
    )

    first = coordinator.check_once()
    second = coordinator.check_once()

    assert first["status"] == "repaired"
    assert second["status"] == "deduplicated"
    assert executed == ["evidence-1"]
    assert supervisor.restart_required == 1
    assert coordinator.snapshot()["repair_attempts"] == 1
    assert coordinator.snapshot()["repair_successes"] == 1


def test_ambiguous_stale_state_requires_manual_review_without_mutating():
    supervisor = FakeSupervisor()
    coordinator = RuntimeIntegrityCoordinator(
        mode="safe",
        hermes_root="/sanitized-in-test",
        supervisor=supervisor,
        detector=lambda _root: object(),
        planner=lambda _detection: _plan(executable=False),
        executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not execute")
        ),
    )

    result = coordinator.check_once()

    assert result["status"] == "manual_review_required"
    assert supervisor.manual_review_required == 1
    assert coordinator.snapshot()["repair_refusals"] == 1


def test_ambiguous_evidence_is_deduplicated_instead_of_spamming_refusals():
    supervisor = FakeSupervisor()
    coordinator = RuntimeIntegrityCoordinator(
        mode="safe",
        hermes_root="/sanitized-in-test",
        supervisor=supervisor,
        detector=lambda _root: object(),
        planner=lambda _detection: _plan(
            executable=False,
            fingerprint="ambiguous-evidence",
        ),
    )

    first = coordinator.check_once()
    second = coordinator.check_once()

    assert first["status"] == "manual_review_required"
    assert second["status"] == "manual_review_required"
    assert coordinator.snapshot()["repair_refusals"] == 1
    assert supervisor.manual_review_required == 1


def test_ready_runtime_and_off_mode_do_not_inspect_or_mutate_source():
    calls = []
    for mode, reason in (("safe", "runtime_ready"), ("off", "runtime_heartbeat_missing")):
        coordinator = RuntimeIntegrityCoordinator(
            mode=mode,
            hermes_root="/sanitized-in-test",
            supervisor=FakeSupervisor(reason=reason),
            detector=lambda _root: calls.append(True),
        )
        assert coordinator.check_once()["status"] in {"ready", "disabled"}

    assert calls == []


def test_installed_plan_waits_for_first_heartbeat_without_persisting_fence(
    tmp_path,
):
    for grace_seconds, current_time, readiness_reason in (
        (30.0, 0.0, "runtime_heartbeat_waiting"),
        (0.0, 1.0, "runtime_heartbeat_missing"),
    ):
        clock = [0.0]
        state_root = tmp_path / readiness_reason
        supervisor = RuntimeIntegritySupervisor(
            mode="safe",
            expected_package_version="4.1.1",
            now=lambda: clock[0],
            startup_grace_seconds=grace_seconds,
            state_directory=state_root,
        )
        clock[0] = current_time
        assert supervisor.snapshot()["reason"] == readiness_reason
        coordinator = RuntimeIntegrityCoordinator(
            mode="safe",
            hermes_root="/sanitized-in-test",
            supervisor=supervisor,
            detector=lambda _root: object(),
            planner=lambda _detection: _plan(
                executable=False,
                state="installed",
            ),
            executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("must not execute")
            ),
        )

        result = coordinator.check_once()

        assert result == {
            "status": "idle",
            "reason": "recovery_not_required",
            "attempted": False,
        }
        readiness = supervisor.snapshot()
        assert readiness["reason"] == readiness_reason
        assert readiness["restart_required"] is False
        assert not (state_root / "runtime-integrity-fence.json").exists()

        assert supervisor.record(
            RuntimeControlEvent.from_dict(
                {
                    "schema_version": "1",
                    "event": "runtime.hello",
                    "runtime_id": f"runtime-after-{readiness_reason}",
                    "sequence": 1,
                    "created_at": clock[0],
                    "hook_generation": RUNTIME_HOOK_GENERATION,
                    "package_version": "4.1.1",
                }
            )
        )
        assert coordinator.check_once() == {
            "status": "ready",
            "reason": "runtime_ready",
            "attempted": False,
        }
        assert not (state_root / "runtime-integrity-fence.json").exists()


def test_installed_plan_does_not_persist_fence_during_gateway_restart_gap(
    tmp_path,
):
    clock = [0.0]
    state_root = tmp_path / "gateway-restart-gap"
    supervisor = RuntimeIntegritySupervisor(
        mode="safe",
        expected_package_version="4.1.2",
        now=lambda: clock[0],
        stale_after_seconds=15.0,
        state_directory=state_root,
    )
    assert supervisor.record(
        RuntimeControlEvent.from_dict(
            {
                "schema_version": "1",
                "event": "runtime.hello",
                "runtime_id": "runtime-before-restart-123",
                "sequence": 1,
                "created_at": clock[0],
                "hook_generation": RUNTIME_HOOK_GENERATION,
                "package_version": "4.1.2",
            }
        )
    )
    assert supervisor.snapshot()["reason"] == "runtime_ready"

    clock[0] = 16.0
    assert supervisor.snapshot()["reason"] == "runtime_heartbeat_stale"
    coordinator = RuntimeIntegrityCoordinator(
        mode="safe",
        hermes_root="/sanitized-in-test",
        supervisor=supervisor,
        detector=lambda _root: object(),
        planner=lambda _detection: _plan(
            executable=False,
            state="installed",
        ),
        executor=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not execute")
        ),
    )

    assert coordinator.check_once() == {
        "status": "idle",
        "reason": "recovery_not_required",
        "attempted": False,
    }
    readiness = supervisor.snapshot()
    assert readiness["reason"] == "runtime_heartbeat_stale"
    assert readiness["restart_required"] is False
    assert not (state_root / "runtime-integrity-fence.json").exists()

    assert supervisor.record(
        RuntimeControlEvent.from_dict(
            {
                "schema_version": "1",
                "event": "runtime.hello",
                "runtime_id": "runtime-after-restart-456",
                "sequence": 1,
                "created_at": clock[0],
                "hook_generation": RUNTIME_HOOK_GENERATION,
                "package_version": "4.1.2",
            }
        )
    )
    assert supervisor.snapshot()["reason"] == "runtime_ready"
    assert not (state_root / "runtime-integrity-fence.json").exists()


def test_missing_control_auth_never_triggers_source_inspection_or_repair():
    calls = []
    coordinator = RuntimeIntegrityCoordinator(
        mode="safe",
        hermes_root="/sanitized-in-test",
        supervisor=FakeSupervisor(reason="control_auth_unavailable"),
        detector=lambda _root: calls.append("detect"),
        executor=lambda *_args, **_kwargs: calls.append("execute"),
    )

    result = coordinator.check_once()

    assert result == {
        "status": "manual_review_required",
        "reason": "control_auth_unavailable",
        "attempted": False,
    }
    assert calls == []


def test_integrity_snapshot_sanitizer_allows_only_bounded_operator_fields():
    sanitized = sanitize_integrity_snapshot(
        {
            "mode": "notify",
            "last_status": "repair_available",
            "last_reason": "verified_git_upgrade",
            "repair_attempts": 2,
            "repair_successes": 1,
            "repair_refusals": 3,
            "private_path": "/private/secret",
        }
    )

    assert sanitized == {
        "mode": "notify",
        "last_status": "repair_available",
        "last_reason": "verified_git_upgrade",
        "repair_attempts": 2,
        "repair_successes": 1,
        "repair_refusals": 3,
    }
    assert sanitize_integrity_snapshot(
        {
            "mode": "unsafe",
            "last_status": "/private/secret",
            "last_reason": "token=secret",
            "repair_attempts": -1,
        }
    ) == {
        "mode": "unknown",
        "last_status": "unknown",
        "last_reason": "unknown",
        "repair_attempts": 0,
        "repair_successes": 0,
        "repair_refusals": 0,
    }


def test_missing_heartbeat_reuses_installed_observation_but_rechecks_source(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(integrity_module.time, "monotonic", lambda: clock[0])
    calls = []
    plan = [_plan(executable=False, state="installed")]
    supervisor = FakeSupervisor()
    coordinator = RuntimeIntegrityCoordinator(
        mode="safe", hermes_root="/sanitized-in-test", supervisor=supervisor,
        detector=lambda root: calls.append(root), planner=lambda _: plan[0],
    )
    coordinator.check_once()
    for second in range(15, 300, 15):
        clock[0] = 100.0 + second
        assert coordinator.check_once()["reason"] == "recovery_not_required"
    assert len(calls) == 1
    assert supervisor.reason == "runtime_heartbeat_missing"
    plan[0] = _plan(executable=False)
    clock[0] = 400.0
    assert coordinator.check_once()["status"] == "manual_review_required"
    assert len(calls) == 2
    assert supervisor.manual_review_required == 1


def test_integrity_snapshot_does_not_wait_for_source_scan():
    from concurrent.futures import ThreadPoolExecutor
    import threading

    entered, release = threading.Event(), threading.Event()

    def detect(_root):
        entered.set()
        assert release.wait(5)
        return object()

    coordinator = RuntimeIntegrityCoordinator(
        mode="safe", hermes_root="/sanitized-in-test", supervisor=FakeSupervisor(),
        detector=detect, planner=lambda _: _plan(executable=False, state="installed"),
    )
    before = coordinator.snapshot()
    with ThreadPoolExecutor(max_workers=2) as pool:
        check = pool.submit(coordinator.check_once)
        try:
            assert entered.wait(5)
            # Semantic barrier: snapshot must finish while detect remains blocked.
            snapshot = pool.submit(coordinator.snapshot)
            assert snapshot.result(timeout=1) == before
            assert not check.done()
        finally:
            release.set()
        assert check.result(timeout=5)["reason"] == "recovery_not_required"
    assert coordinator.snapshot()["last_reason"] == "recovery_not_required"


def test_installed_cooldown_never_masks_runtime_transitions(monkeypatch):
    monkeypatch.setattr(integrity_module.time, "monotonic", lambda: 100.0)
    for reason, expected in (
        ("runtime_ready", "ready"),
        ("gateway_restart_required", "restart_required"),
        ("manual_review_required", "manual_review_required"),
        ("control_auth_unavailable", "manual_review_required"),
    ):
        supervisor = FakeSupervisor()
        calls = []
        coordinator = RuntimeIntegrityCoordinator(
            mode="safe", hermes_root="/sanitized-in-test", supervisor=supervisor,
            detector=lambda root: calls.append(root),
            planner=lambda _: _plan(executable=False, state="installed"),
        )
        coordinator.check_once()
        supervisor.reason = reason
        assert coordinator.check_once()["status"] == expected
        assert len(calls) == 1
        # A new outage after observing recovery/fence must inspect afresh.
        supervisor.reason = "runtime_heartbeat_stale"
        coordinator.check_once()
        assert len(calls) == 2


def test_scan_interval_starts_at_completion_and_failures_are_not_cached(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(integrity_module.time, "monotonic", lambda: clock[0])
    calls = []
    def detect(root):
        calls.append(root)
        if len(calls) > 1:
            raise OSError("unavailable")
        clock[0] += 20.0
    coordinator = RuntimeIntegrityCoordinator(
        mode="notify", hermes_root="/sanitized-in-test", supervisor=FakeSupervisor(),
        detector=detect, planner=lambda _: _plan(executable=False, state="installed"),
    )
    coordinator.check_once()
    clock[0] = 419.0
    coordinator.check_once()
    assert len(calls) == 1
    clock[0] = 420.0
    assert coordinator.check_once()["reason"] == "integrity_evidence_unavailable"
    assert coordinator.check_once()["reason"] == "integrity_evidence_unavailable"
    assert len(calls) == 3
