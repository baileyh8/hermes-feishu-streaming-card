"""Platform-specific availability message tests (macOS vs Linux) for
``persistent_service._availability_error`` / ``persistent_sidecar_setup_blocker``.

Regression context: on macOS the availability error is worded identically to a
fixable Linux gap ("requires Linux systemd"), so guided setup suggests running
``hermes-feishu-card enable`` even though that command can never succeed
without systemd. The darwin branch makes the platform constraint explicit.
"""

from __future__ import annotations

from hermes_feishu_card import persistent_service


def test_availability_error_names_macos_constraint(monkeypatch):
    monkeypatch.setattr(persistent_service.sys, "platform", "darwin")
    message = persistent_service._availability_error()
    assert "macOS" in message
    assert "Linux systemd" in message


def test_availability_error_linux_without_systemctl(monkeypatch):
    monkeypatch.setattr(persistent_service.sys, "platform", "linux")
    monkeypatch.setattr(
        persistent_service.shutil, "which", lambda name: None
    )
    message = persistent_service._availability_error()
    assert message == "failed: persistent service requires systemctl"


def test_setup_blocker_propagates_macos_constraint(monkeypatch):
    monkeypatch.setattr(persistent_service.sys, "platform", "darwin")
    blocker = persistent_service.persistent_sidecar_setup_blocker(
        {"service": {"manager": "auto"}}
    )
    assert blocker.startswith("persistent service requires Linux systemd")
    assert "macOS" in blocker


def test_setup_blocker_non_auto_manager_is_platform_independent(monkeypatch):
    monkeypatch.setattr(persistent_service.sys, "platform", "darwin")
    blocker = persistent_service.persistent_sidecar_setup_blocker(
        {"service": {"manager": "detached"}}
    )
    assert blocker == (
        "persistent service requires service.manager=auto or systemd-user"
    )
