from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

from hermes_feishu_card import cli, persistent_service


def _inputs(tmp_path: Path):
    state = tmp_path / "state"
    config = tmp_path / "config.yaml"
    env_file = tmp_path / ".env"
    hermes = tmp_path / "hermes-agent"
    python = hermes / ".venv" / "bin" / "python"
    config.write_text("server:\n  host: 127.0.0.1\n  port: 8765\n", encoding="utf-8")
    env_file.write_text("FEISHU_APP_ID=test-app\n", encoding="utf-8")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    python.chmod(0o755)
    return state, config, env_file, hermes, python


def _patch_paths(monkeypatch, tmp_path: Path):
    state, config, env_file, hermes, python = _inputs(tmp_path)
    owner_uid = tmp_path.stat().st_uid
    unit = tmp_path / "home" / ".config" / "systemd" / "user" / persistent_service.UNIT_NAME
    manifest = state / persistent_service.MANIFEST_NAME
    monkeypatch.setattr(persistent_service, "_state_dir", lambda: state)
    monkeypatch.setattr(persistent_service, "_unit_path", lambda: unit)
    monkeypatch.setattr(persistent_service.sys, "platform", "linux")
    monkeypatch.setattr(persistent_service.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(persistent_service.os, "getuid", lambda: owner_uid)
    monkeypatch.setattr(persistent_service, "_unowned_unit_active", lambda: False)
    return state, config, env_file, hermes, python, unit, manifest


def _healthy():
    return {
        "status": "healthy",
        "process_pid": 4321,
        "process_token_hash": "",
        "package_version": "4.3.0",
        "python_identity": "python-sha256:" + "a" * 64,
    }


def test_enable_refuses_missing_linger_without_mutation(monkeypatch, tmp_path):
    state, config, env_file, hermes, python, unit, manifest = _patch_paths(
        monkeypatch, tmp_path
    )
    commands = []
    monkeypatch.setattr(persistent_service, "_linger_enabled", lambda: False)
    monkeypatch.setattr(
        persistent_service.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command),
    )

    result = persistent_service.enable_persistent_sidecar(
        config_path=config,
        config={"service": {"manager": "systemd-user"}},
        env_file=env_file,
        hermes_dir=hermes,
        python_executable=python,
        expected_package_version="4.3.0",
        expected_python_identity="python-sha256:" + "a" * 64,
    )

    assert result == "failed: systemd user linger is disabled; run loginctl enable-linger"
    assert commands == []
    assert not unit.exists()
    assert not manifest.exists()
    assert not state.exists()


def test_enable_writes_bound_unit_and_manifest_then_starts(monkeypatch, tmp_path):
    state, config, env_file, hermes, python, unit, manifest = _patch_paths(
        monkeypatch, tmp_path
    )
    commands = []
    health = iter((None, _healthy()))
    monkeypatch.setattr(persistent_service, "_linger_enabled", lambda: True)
    monkeypatch.setattr(
        persistent_service, "_fetch_health", lambda _config: next(health)
    )
    monkeypatch.setattr(persistent_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        persistent_service.time, "monotonic", iter((0.0, 0.0, 1.0)).__next__
    )

    def fake_run(command, **kwargs):
        commands.append(list(command))
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["timeout"] == 10
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(persistent_service.subprocess, "run", fake_run)

    result = persistent_service.enable_persistent_sidecar(
        config_path=config,
        config={"service": {"manager": "systemd-user"}},
        env_file=env_file,
        hermes_dir=hermes,
        python_executable=python,
        expected_package_version="4.3.0",
        expected_python_identity="python-sha256:" + "a" * 64,
    )

    assert result == "enabled"
    assert commands == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", persistent_service.UNIT_NAME],
    ]
    assert stat.S_IMODE(unit.stat().st_mode) == 0o600
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600
    unit_text = unit.read_text(encoding="utf-8")
    assert "[Install]\nWantedBy=default.target" in unit_text
    assert "Restart=on-failure" in unit_text
    assert 'WorkingDirectory="' not in unit_text
    assert "\nWorkingDirectory=/" in unit_text
    assert "--managed-pidfile" not in unit_text
    assert "--token" not in unit_text
    assert str(python) in unit_text
    assert str(config) in unit_text
    assert str(env_file) in unit_text
    assert str(hermes) in unit_text
    assert "test-app" not in unit_text
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["protocol"] == "hfc-systemd-user-service-v1"
    assert payload["unit_sha256"].startswith("sha256:")
    assert payload["expected_package_version"] == "4.3.0"
    assert payload["expected_python_identity"] == "python-sha256:" + "a" * 64


def test_enable_is_idempotent_for_exact_active_unit(monkeypatch, tmp_path):
    state, config, env_file, hermes, python, unit, manifest = _patch_paths(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(persistent_service, "_linger_enabled", lambda: True)
    monkeypatch.setattr(persistent_service, "_fetch_health", lambda _config: _healthy())
    calls = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(persistent_service.subprocess, "run", fake_run)
    kwargs = dict(
        config_path=config,
        config={"service": {"manager": "systemd-user"}},
        env_file=env_file,
        hermes_dir=hermes,
        python_executable=python,
        expected_package_version="4.3.0",
        expected_python_identity="python-sha256:" + "a" * 64,
    )
    assert persistent_service.enable_persistent_sidecar(**kwargs) == "enabled"
    first_unit = unit.read_bytes()
    first_manifest = manifest.read_bytes()
    calls.clear()

    assert persistent_service.enable_persistent_sidecar(**kwargs) == "already enabled"
    assert calls == [
        ["systemctl", "--user", "is-enabled", persistent_service.UNIT_NAME],
        ["systemctl", "--user", "is-active", persistent_service.UNIT_NAME],
    ]
    assert unit.read_bytes() == first_unit
    assert manifest.read_bytes() == first_manifest


def test_enable_rolls_back_owned_files_when_systemctl_fails(monkeypatch, tmp_path):
    state, config, env_file, hermes, python, unit, manifest = _patch_paths(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(persistent_service, "_linger_enabled", lambda: True)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 1 if "enable" in command else 0)

    monkeypatch.setattr(persistent_service.subprocess, "run", fake_run)

    result = persistent_service.enable_persistent_sidecar(
        config_path=config,
        config={"service": {"manager": "auto"}},
        env_file=env_file,
        hermes_dir=hermes,
        python_executable=python,
        expected_package_version="4.3.0",
        expected_python_identity="python-sha256:" + "a" * 64,
    )

    assert result == "failed: persistent systemd user service could not be enabled"
    assert not unit.exists()
    assert not manifest.exists()
    assert commands[-2:] == [
        ["systemctl", "--user", "disable", "--now", persistent_service.UNIT_NAME],
        ["systemctl", "--user", "daemon-reload"],
    ]


def test_enable_preserves_ownership_when_failed_start_cannot_be_stopped(
    monkeypatch, tmp_path
):
    _, config, env_file, hermes, python, unit, manifest = _patch_paths(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(persistent_service, "_linger_enabled", lambda: True)

    def fake_run(command, **kwargs):
        if command[2] in {"enable", "disable"}:
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(persistent_service.subprocess, "run", fake_run)

    result = persistent_service.enable_persistent_sidecar(
        config_path=config,
        config={"service": {"manager": "systemd-user"}},
        env_file=env_file,
        hermes_dir=hermes,
        python_executable=python,
        expected_package_version="4.3.0",
        expected_python_identity="python-sha256:" + "a" * 64,
    )

    assert result == "failed: persistent systemd user service could not be enabled"
    assert unit.exists()
    assert manifest.exists()


def test_disable_refuses_unit_drift_without_systemctl_or_deletion(monkeypatch, tmp_path):
    state, config, env_file, hermes, python, unit, manifest = _patch_paths(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(persistent_service, "_linger_enabled", lambda: True)
    monkeypatch.setattr(persistent_service, "_fetch_health", lambda _config: _healthy())
    monkeypatch.setattr(
        persistent_service.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )
    assert persistent_service.enable_persistent_sidecar(
        config_path=config,
        config={"service": {"manager": "systemd-user"}},
        env_file=env_file,
        hermes_dir=hermes,
        python_executable=python,
        expected_package_version="4.3.0",
        expected_python_identity="python-sha256:" + "a" * 64,
    ) == "enabled"
    unit.write_text(unit.read_text(encoding="utf-8") + "# user drift\n", encoding="utf-8")
    commands = []
    monkeypatch.setattr(
        persistent_service.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command),
    )

    assert persistent_service.disable_persistent_sidecar() == (
        "failed: persistent service unit changed; disable refused"
    )
    assert commands == []
    assert unit.exists()
    assert manifest.exists()


def test_disable_stops_removes_and_reloads_exact_owned_unit(monkeypatch, tmp_path):
    state, config, env_file, hermes, python, unit, manifest = _patch_paths(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(persistent_service, "_linger_enabled", lambda: True)
    health = [_healthy()]
    monkeypatch.setattr(
        persistent_service, "_fetch_health", lambda _config: health.pop(0) if health else None
    )
    commands = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(persistent_service.subprocess, "run", fake_run)
    assert persistent_service.enable_persistent_sidecar(
        config_path=config,
        config={"service": {"manager": "systemd-user"}},
        env_file=env_file,
        hermes_dir=hermes,
        python_executable=python,
        expected_package_version="4.3.0",
        expected_python_identity="python-sha256:" + "a" * 64,
    ) == "enabled"
    commands.clear()

    assert persistent_service.disable_persistent_sidecar() == "disabled"
    assert commands == [
        ["systemctl", "--user", "disable", "--now", persistent_service.UNIT_NAME],
        ["systemctl", "--user", "daemon-reload"],
    ]
    assert not unit.exists()
    assert not manifest.exists()


@pytest.mark.parametrize("manager", ["detached", "systemd-system"])
def test_enable_refuses_non_user_service_manager(monkeypatch, tmp_path, manager):
    _, config, env_file, hermes, python, _, _ = _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(persistent_service, "_linger_enabled", lambda: True)

    assert persistent_service.enable_persistent_sidecar(
        config_path=config,
        config={"service": {"manager": manager}},
        env_file=env_file,
        hermes_dir=hermes,
        python_executable=python,
        expected_package_version="4.3.0",
        expected_python_identity="python-sha256:" + "a" * 64,
    ) == "failed: persistent service requires service.manager=auto or systemd-user"


def test_enable_refuses_active_unowned_transient_unit(monkeypatch, tmp_path):
    state, config, env_file, hermes, python, unit, manifest = _patch_paths(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(persistent_service, "_linger_enabled", lambda: True)
    monkeypatch.setattr(persistent_service, "_unowned_unit_active", lambda: True)

    assert persistent_service.enable_persistent_sidecar(
        config_path=config,
        config={"service": {"manager": "systemd-user"}},
        env_file=env_file,
        hermes_dir=hermes,
        python_executable=python,
        expected_package_version="4.3.0",
        expected_python_identity="python-sha256:" + "a" * 64,
    ) == "failed: an unmanaged sidecar unit is active; stop it before enable"
    assert not state.exists()
    assert not unit.exists()
    assert not manifest.exists()


def test_cli_parser_dispatches_enable_and_disable(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(cli, "_run_enable", lambda args: called.append(("enable", args)) or 0)
    monkeypatch.setattr(cli, "_run_disable", lambda args: called.append(("disable", args)) or 0)

    assert cli.main(
        [
            "enable",
            "--config",
            str(tmp_path / "config.yaml"),
            "--env-file",
            str(tmp_path / ".env"),
            "--hermes-dir",
            str(tmp_path / "hermes-agent"),
            "--yes",
        ]
    ) == 0
    assert cli.main(["disable"]) == 0
    assert called[0][0] == "enable"
    assert called[0][1].yes is True
    assert called[1][0] == "disable"


def test_cli_enable_uses_verified_runtime_binding(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    hermes = tmp_path / "hermes-agent"
    hermes.mkdir()
    python = hermes / ".venv" / "bin" / "python"
    expected_config = {"service": {"manager": "systemd-user"}}
    observed = []
    monkeypatch.setattr(cli, "load_config", lambda *args, **kwargs: expected_config)
    monkeypatch.setattr(
        cli,
        "_lifecycle_hook_check",
        lambda _args: {"status": "installed", "blocking": False, "root": hermes},
    )
    monkeypatch.setattr(
        cli,
        "_resolve_start_runtime_identity",
        lambda root: (python, "python-sha256:" + "a" * 64),
    )
    monkeypatch.setattr(
        cli,
        "enable_persistent_sidecar",
        lambda **kwargs: observed.append(kwargs) or "enabled",
    )
    monkeypatch.setattr(cli, "persistent_sidecar_matches", lambda **kwargs: False)
    monkeypatch.setattr(cli, "stop_sidecar", lambda _config: "not running")

    result = cli._run_enable(
        SimpleNamespace(
            config=str(config_path),
            env_file=str(tmp_path / ".env"),
            hermes_dir=str(hermes),
            yes=True,
        )
    )

    assert result == 0
    assert capsys.readouterr().out == "enable ok\n"
    assert observed == [
        {
            "config_path": str(config_path),
            "config": expected_config,
            "env_file": str(tmp_path / ".env"),
            "hermes_dir": hermes,
            "python_executable": python,
            "expected_package_version": cli.PACKAGE_VERSION,
            "expected_python_identity": "python-sha256:" + "a" * 64,
        }
    ]


def test_cli_start_noops_for_exact_persistent_service(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    hermes = tmp_path / "hermes-agent"
    hermes.mkdir()
    python = hermes / ".venv" / "bin" / "python"
    config = {"service": {"manager": "systemd-user"}}
    monkeypatch.setattr(cli, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(
        cli,
        "_lifecycle_hook_check",
        lambda _args: {"status": "installed", "blocking": False, "root": hermes},
    )
    monkeypatch.setattr(
        cli,
        "_resolve_start_runtime_identity",
        lambda root: (python, "python-sha256:" + "b" * 64),
    )
    monkeypatch.setattr(cli, "persistent_sidecar_matches", lambda **kwargs: True)
    monkeypatch.setattr(
        cli,
        "start_sidecar",
        lambda *args, **kwargs: pytest.fail("persistent service must not be replaced"),
    )

    result = cli._run_start(
        SimpleNamespace(
            config=str(config_path),
            env_file=None,
            hermes_dir=str(hermes),
            hermes_home=None,
        )
    )

    assert result == 0
    assert capsys.readouterr().out == "start: already running\n"


def test_cli_disable_reports_exact_result(monkeypatch, capsys):
    monkeypatch.setattr(cli, "disable_persistent_sidecar", lambda: "disabled")

    assert cli._run_disable(SimpleNamespace()) == 0
    assert capsys.readouterr().out == "disable ok\n"


def test_active_requires_exact_owned_unit_and_systemd_state(monkeypatch, tmp_path):
    _, config, env_file, hermes, python, unit, _ = _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(persistent_service, "_linger_enabled", lambda: True)
    monkeypatch.setattr(persistent_service, "_fetch_health", lambda _config: _healthy())
    monkeypatch.setattr(
        persistent_service.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )
    assert persistent_service.enable_persistent_sidecar(
        config_path=config,
        config={"service": {"manager": "systemd-user"}},
        env_file=env_file,
        hermes_dir=hermes,
        python_executable=python,
        expected_package_version="4.3.0",
        expected_python_identity="python-sha256:" + "a" * 64,
    ) == "enabled"

    assert persistent_service.persistent_sidecar_active() is True
    unit.write_text(unit.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    assert persistent_service.persistent_sidecar_active() is False


def test_cli_stop_refuses_to_bypass_persistent_owner(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(cli, "persistent_sidecar_active", lambda: True)
    monkeypatch.setattr(
        cli,
        "stop_sidecar",
        lambda _config: pytest.fail("persistent service must be disabled explicitly"),
    )

    result = cli._run_stop(
        SimpleNamespace(config=str(tmp_path / "config.yaml"), env_file=None)
    )

    assert result == 1
    assert "hermes-feishu-card disable" in capsys.readouterr().err


def test_cli_status_labels_verified_persistent_manager(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(cli, "persistent_sidecar_active", lambda: True)
    monkeypatch.setattr(
        cli,
        "status_sidecar",
        lambda _config: {
            "running": True,
            "pid": None,
            "manager": "unknown",
            "health": {"status": "healthy", "active_sessions": 0, "metrics": {}},
        },
    )

    assert cli._run_status(
        SimpleNamespace(
            config=str(tmp_path / "config.yaml"),
            env_file=None,
            hermes_dir=None,
            hermes_home=None,
        )
    ) == 0
    assert "manager: systemd-user-persistent" in capsys.readouterr().out
