"""PM registration handoff ordering for a verified legacy observer."""

from argparse import Namespace
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from hermes_feishu_card import cli
from hermes_feishu_card.install import legacy_handoff


def _args(tmp_path):
    return Namespace(
        hermes_dir=str(tmp_path),
        hermes_home=None,
        profile_id=None,
        no_repair=False,
        accept_hermes_upgrade=False,
    )


def _install_surface(monkeypatch, tmp_path):
    detection = SimpleNamespace(root=tmp_path, decomposed=True, supported=True)
    old = SimpleNamespace(python_identity="old")
    new = SimpleNamespace(python_identity="new")
    precondition = SimpleNamespace(binding=old)
    calls = []
    events = []
    registration = SimpleNamespace(
        adoption=None,
        rollback=lambda: events.append("registration_rollback"),
    )
    handoff = SimpleNamespace(
        rollback=lambda: events.append("handoff_rollback"),
        finish=lambda: events.append("handoff_finish"),
    )
    monkeypatch.setattr(cli, "detect_hermes", lambda _: detection)
    monkeypatch.setattr(cli, "_read_manifest", lambda _: None)
    monkeypatch.setattr(
        legacy_handoff, "prepare", lambda *args, **kwargs: precondition
    )
    monkeypatch.setattr(
        legacy_handoff,
        "apply",
        lambda proof, binding, current: calls.append((binding, current)) or handoff,
    )
    monkeypatch.setattr(
        cli,
        "_ensure_hermes_runtime_package",
        lambda *args, **kwargs: registration,
    )
    monkeypatch.setattr(cli, "_ensure_hermes_feishu_sdk", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_fixed_tag_v3_install", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "resolve_runtime_binding", lambda **_: new)

    from hermes_feishu_card.install import legacy_observer, recovery

    monkeypatch.setattr(legacy_observer, "available", lambda _: True)
    monkeypatch.setattr(recovery, "install_decomposed", lambda *args, **kwargs: True)
    return registration, handoff, calls, events, new, recovery


def _setup_surface(monkeypatch, tmp_path, *, decomposed):
    root = tmp_path / "hermes"
    root.mkdir()
    detection = SimpleNamespace(root=root, supported=True)
    if decomposed is not None:
        detection.decomposed = decomposed
    config_path = tmp_path / "hfc.yaml"
    events = []
    monkeypatch.setattr(
        cli,
        "_resolve_route_settings",
        lambda *args, **kwargs: {
            "env_path": config_path.parent / ".env",
            "profile_id": "default",
            "profile_source": "fallback_default",
            "event_url": "http://127.0.0.1:1/events",
        },
    )
    monkeypatch.setattr(cli, "_ensure_setup_config", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda *args, **kwargs: {
            "service": {},
            "feishu": {"app_id": "id", "app_secret": "secret"},
        },
    )
    monkeypatch.setattr(cli, "setup_hermes_home", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "update_hfc_env", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "detect_hermes", lambda _: detection)
    monkeypatch.setattr(cli, "_build_doctor_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_profile_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "_has_feishu_credentials", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        cli, "_verified_explicit_hermes_root", lambda *args, **kwargs: root
    )
    monkeypatch.setattr(cli, "_format_hermes_detection", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        cli, "_print_hermes_streaming_guidance", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        cli, "_run_repair", lambda *args, **kwargs: events.append("repair") or 0
    )
    monkeypatch.setattr(
        cli, "_run_install", lambda *args, **kwargs: events.append("install") or 0
    )
    monkeypatch.setattr(
        cli, "_provision_setup_maintenance", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        cli,
        "_resolve_sidecar_runtime_identity",
        lambda *args, **kwargs: (Path(sys.executable), "identity"),
    )
    args = Namespace(
        hermes_dir=str(root),
        config=str(config_path),
        hermes_home=None,
        profile_id=None,
        repair=True,
        no_repair=False,
        accept_hermes_upgrade=False,
        skip_start=True,
        transient=False,
    )
    return args, events


@pytest.mark.parametrize(
    "decomposed,expected",
    ((True, ["install"]), (False, ["repair", "install"]), (None, ["repair", "install"])),
)
def test_setup_repair_gate_respects_decomposed_detection(
    monkeypatch, tmp_path, decomposed, expected
):
    args, events = _setup_surface(monkeypatch, tmp_path, decomposed=decomposed)
    assert cli._run_setup(args) == 0
    assert events == expected


def test_install_hands_verified_old_binding_to_new_pm_registration(monkeypatch, tmp_path):
    registration, handoff, calls, _events, new, _recovery = _install_surface(
        monkeypatch, tmp_path
    )
    registrations = []

    assert cli._run_install_core(_args(tmp_path), registrations) == 0
    assert registrations == [registration, handoff]
    assert calls == [(new, registration)]


def test_adoption_registration_keeps_legacy_prepare_path(monkeypatch, tmp_path):
    registration, _handoff, calls, _events, new, _recovery = _install_surface(
        monkeypatch, tmp_path
    )
    adoption = object()
    registration.adoption = adoption
    prepared = []

    from hermes_feishu_card.install import legacy_observer

    monkeypatch.setattr(
        legacy_observer,
        "prepare",
        lambda binding, *, adoption=None: prepared.append((binding, adoption)),
    )

    assert cli._run_install_core(_args(tmp_path), []) == 0
    assert prepared == [(new, registration)]
    assert calls == []


def test_unknown_observer_drift_is_rejected_before_pm_registration(monkeypatch, tmp_path):
    detection = SimpleNamespace(root=tmp_path, decomposed=True, supported=True)
    calls = []
    monkeypatch.setattr(cli, "detect_hermes", lambda _: detection)
    monkeypatch.setattr(cli, "_read_manifest", lambda _: None)

    def refuse(*args, **kwargs):
        raise legacy_handoff.HandoffRefused("legacy_observer_runtime_binding_changed")

    monkeypatch.setattr(legacy_handoff, "prepare", refuse)
    monkeypatch.setattr(
        cli,
        "_ensure_hermes_runtime_package",
        lambda *args, **kwargs: calls.append(True),
    )

    assert cli._run_install_core(_args(tmp_path), []) == 1
    assert calls == []


def test_source_failure_rolls_back_handoff_then_pm_registration(monkeypatch, tmp_path):
    registration, handoff, _calls, events, _new, recovery = _install_surface(
        monkeypatch, tmp_path
    )

    def fail(*args, **kwargs):
        raise ValueError("source install failed")

    monkeypatch.setattr(recovery, "install_decomposed", fail)
    assert cli._run_install(_args(tmp_path)) == 1
    assert events == ["handoff_rollback", "registration_rollback"]


def test_finish_failure_keeps_existing_cli_error_without_atomic_claim(
    monkeypatch, tmp_path
):
    registration = SimpleNamespace(
        finish=lambda: (_ for _ in ()).throw(ValueError("PM finish failed"))
    )

    def core(args, registrations):
        registrations.append(registration)
        return 0

    monkeypatch.setattr(cli, "_run_install_core", core)
    assert cli._run_install(_args(tmp_path)) == 1


def test_core_base_exception_rolls_back_handoff_then_pm(monkeypatch, tmp_path):
    events = []
    registration = SimpleNamespace(
        rollback=lambda: events.append("registration_rollback")
    )
    handoff = SimpleNamespace(rollback=lambda: events.append("handoff_rollback"))

    def core(args, registrations):
        registrations.extend([registration, handoff])
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run_install_core", core)
    try:
        cli._run_install(_args(tmp_path))
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("KeyboardInterrupt was swallowed")
    assert events == ["handoff_rollback", "registration_rollback"]


def test_core_rollback_failure_is_reported_without_masking_original(
    monkeypatch, tmp_path, capsys
):
    registration = SimpleNamespace(
        rollback=lambda: (_ for _ in ()).throw(ValueError("rollback failed"))
    )

    def core(args, registrations):
        registrations.append(registration)
        raise ValueError("source failed")

    monkeypatch.setattr(cli, "_run_install_core", core)
    with pytest.raises(ValueError, match="source failed"):
        cli._run_install(_args(tmp_path))
    assert "install rollback did not complete" in capsys.readouterr().err
