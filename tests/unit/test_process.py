from __future__ import annotations

import subprocess

from hermes_feishu_card import process


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
