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


def test_process_token_hash_is_stable_and_empty_safe():
    assert process.process_token_hash("") == ""
    assert process.process_token_hash(None) == ""
    assert process.process_token_hash("sidecar-token")
    assert process.process_token_hash("sidecar-token") == process.process_token_hash("sidecar-token")
    assert process.process_token_hash("sidecar-token") != process.process_token_hash("other-token")


def test_start_sidecar_passes_selected_env_file_to_runner(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / "CUSTOM.env"
    commands: list[list[str]] = []

    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
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
