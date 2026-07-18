from __future__ import annotations

import subprocess
from types import SimpleNamespace

from hermes_feishu_card import process


class _HealthResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return b'{"status":"healthy"}'


class _DegradedHealthResponse(_HealthResponse):
    def read(self) -> bytes:
        return b'{"status":"degraded","noop_mode":true}'


def test_process_token_hash_is_stable_and_empty_safe():
    assert process.process_token_hash("") == ""
    assert process.process_token_hash(None) == ""
    assert process.process_token_hash("sidecar-token")
    assert process.process_token_hash("sidecar-token") == process.process_token_hash("sidecar-token")
    assert process.process_token_hash("sidecar-token") != process.process_token_hash("other-token")


def test_pid_record_preserves_systemd_manager_identity(monkeypatch, tmp_path):
    record_path = tmp_path / "sidecar.pid"
    monkeypatch.setattr(process, "pid_path", lambda: record_path)

    process.write_pid_record(
        4321,
        "sidecar-token",
        manager="systemd-user",
        unit=process.SYSTEMD_UNIT_NAME,
    )

    assert process.read_pid_record() == {
        "pid": 4321,
        "token": "sidecar-token",
        "manager": "systemd-user",
        "unit": process.SYSTEMD_UNIT_NAME,
    }


def test_start_sidecar_passes_selected_env_file_to_runner(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / "CUSTOM.env"
    commands: list[list[str]] = []

    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process, "log_path", lambda: tmp_path / "sidecar.log")
    monkeypatch.setattr(process, "write_pid_record", lambda *_args: None)
    monkeypatch.setattr(process, "clear_pid", lambda: None)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 6)).__next__)

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(pid=123, poll=lambda: None)

    monkeypatch.setattr(process.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(process, "stop_pid", lambda _pid: None)

    assert process.start_sidecar(config_path, {"server": {"host": "127.0.0.1", "port": 0}}, env_file=env_path) == "failed: health check timed out"
    assert commands == [
        [
            process.sys.executable,
            "-m",
            "hermes_feishu_card.runner",
            "--config",
            str(config_path),
            "--env-file",
            str(env_path),
            "--token",
            commands[0][-1],
        ]
    ]


def test_start_sidecar_uses_restartable_systemd_user_unit(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / "CUSTOM.env"
    log_file = tmp_path / "sidecar.log"
    commands: list[list[str]] = []
    pid_records: list[tuple[int, str, str, str]] = []
    token = "fixed-sidecar-token"
    health_responses = iter(
        (
            None,
            {
                "status": "healthy",
                "process_pid": 4321,
                "process_token_hash": process.process_token_hash(token),
            },
        )
    )

    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "_systemd_user_available", lambda: True)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process, "log_path", lambda: log_file)
    monkeypatch.setattr(process.secrets, "token_hex", lambda _length: token)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0)).__next__)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)

    def fake_run(command, **kwargs):
        commands.append(list(command))
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["timeout"] == 10
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process.subprocess, "run", fake_run)
    monkeypatch.setattr(
        process,
        "write_pid_record",
        lambda pid, record_token, *, manager, unit: pid_records.append(
            (pid, record_token, manager, unit)
        ),
    )

    result = process.start_sidecar(
        config_path,
        {"server": {"host": "127.0.0.1", "port": 8765}},
        env_file=env_path,
    )

    assert result == "started"
    assert commands == [
        [
            "systemd-run",
            "--user",
            f"--unit={process.SYSTEMD_UNIT_NAME}",
            "--collect",
            "--property=Type=exec",
            "--property=Restart=on-failure",
            "--property=RestartSec=2s",
            f"--property=StandardOutput=append:{log_file}",
            f"--property=StandardError=append:{log_file}",
            "--",
            process.sys.executable,
            "-m",
            "hermes_feishu_card.runner",
            "--config",
            str(config_path),
            "--env-file",
            str(env_path),
            "--token",
            token,
        ]
    ]
    assert pid_records == [(4321, token, "systemd-user", process.SYSTEMD_UNIT_NAME)]


def test_start_sidecar_migrates_owned_process_into_systemd_unit(monkeypatch, tmp_path):
    old_token = "old-sidecar-token"
    new_token = "new-sidecar-token"
    old_health = {
        "status": "healthy",
        "process_pid": 1234,
        "process_token_hash": process.process_token_hash(old_token),
    }
    new_health = {
        "status": "healthy",
        "process_pid": 4321,
        "process_token_hash": process.process_token_hash(new_token),
    }
    health_responses = iter((old_health, new_health))
    stopped: list[int] = []
    cleared: list[bool] = []
    launched: list[list[str]] = []

    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "_systemd_user_available", lambda: True)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {"pid": 1234, "token": old_token},
    )
    monkeypatch.setattr(process, "stop_pid", lambda pid: stopped.append(pid))
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process.secrets, "token_hex", lambda _length: new_token)
    monkeypatch.setattr(process, "_start_systemd_user_sidecar", lambda command: launched.append(command) or True)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0)).__next__)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(process, "write_pid_record", lambda *_args, **_kwargs: None)

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "started"
    assert stopped == [1234]
    assert cleared == [True]
    assert launched


def test_stop_sidecar_uses_systemd_unit_after_service_restart(monkeypatch):
    token = "fixed-sidecar-token"
    stopped: list[str] = []
    cleared: list[bool] = []
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": token,
            "manager": "systemd-user",
            "unit": process.SYSTEMD_UNIT_NAME,
        },
    )
    monkeypatch.setattr(
        process,
        "fetch_health",
        lambda _config: {
            "status": "healthy",
            "process_pid": 4321,
            "process_token_hash": process.process_token_hash(token),
        },
    )
    monkeypatch.setattr(
        process, "_stop_systemd_user_sidecar", lambda unit: stopped.append(unit) or True
    )
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(
        process,
        "pid_is_running",
        lambda _pid: (_ for _ in ()).throw(
            AssertionError("systemd-managed sidecars must be stopped through their unit")
        ),
    )

    result = process.stop_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert result == "stopped"
    assert stopped == [process.SYSTEMD_UNIT_NAME]
    assert cleared == [True]


def test_status_sidecar_reports_restarted_systemd_process_pid(monkeypatch):
    token = "fixed-sidecar-token"
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": token,
            "manager": "systemd-user",
            "unit": process.SYSTEMD_UNIT_NAME,
        },
    )
    monkeypatch.setattr(
        process,
        "fetch_health",
        lambda _config: {
            "status": "healthy",
            "process_pid": 4321,
            "process_token_hash": process.process_token_hash(token),
        },
    )
    probed: list[int] = []
    monkeypatch.setattr(
        process, "pid_is_running", lambda pid: probed.append(pid) or True
    )

    status = process.status_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert status["pid"] == 4321
    assert status["pid_running"] is True
    assert probed == [4321]


def test_fetch_health_bypasses_proxy_for_loopback(monkeypatch):
    calls: list[tuple[str, float]] = []

    class _NoProxyOpener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            return _HealthResponse()

    monkeypatch.setattr(process, "_NO_PROXY_OPENER", _NoProxyOpener(), raising=False)
    monkeypatch.setattr(
        process.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("loopback health check used the system proxy path")
        ),
    )

    health = process.fetch_health(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert health == {"status": "healthy"}
    assert calls == [("http://127.0.0.1:8765/health", 0.4)]


def test_fetch_health_recognizes_degraded_sidecar_as_running(monkeypatch):
    class _NoProxyOpener:
        def open(self, _request, timeout):
            assert timeout == 0.4
            return _DegradedHealthResponse()

    monkeypatch.setattr(process, "_NO_PROXY_OPENER", _NoProxyOpener(), raising=False)

    health = process.fetch_health(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert health == {"status": "degraded", "noop_mode": True}


def test_pid_is_running_uses_windows_process_probe(monkeypatch):
    calls: list[int] = []

    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process, "_pid_is_running_windows", lambda pid: calls.append(pid) or True)
    monkeypatch.setattr(
        process.os,
        "kill",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("os.kill should not be used")),
    )

    assert process.pid_is_running(1234) is True
    assert calls == [1234]


def test_stop_pid_uses_windows_taskkill(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["timeout"] == 5
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(process.subprocess, "run", fake_run)
    monkeypatch.setattr(process, "pid_is_running", lambda _pid: False)

    process._stop_pid_windows(4321)

    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]


def test_stop_pid_dispatches_to_windows_helper(monkeypatch):
    calls: list[int] = []

    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process, "_stop_pid_windows", lambda pid: calls.append(pid))
    monkeypatch.setattr(
        process.os,
        "killpg",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("os.killpg should not be used")),
    )

    process.stop_pid(5678)

    assert calls == [5678]
