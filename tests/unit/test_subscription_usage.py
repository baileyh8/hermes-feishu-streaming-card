from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from hermes_feishu_card import subscription_usage


def test_format_subscription_usage_uses_compact_remaining_percentages():
    payload = {
        "windows": [
            {"label": "Session", "used_percent": 74},
            {"label": "Weekly", "used_percent": 11},
        ]
    }

    assert subscription_usage.format_subscription_usage(payload) == "5h 26% · weekly 89%"


def test_format_subscription_usage_uses_neutral_label_for_single_primary_window():
    payload = {"windows": [{"label": "Session", "used_percent": 6}]}

    assert subscription_usage.format_subscription_usage(payload) == "limit 94%"


def test_format_subscription_usage_skips_invalid_or_unknown_windows():
    payload = {
        "windows": [
            {"label": "Session", "used_percent": "bad"},
            {"label": "Primary", "used_percent": float("inf")},
            {"label": "Monthly", "used_percent": 20},
            {"label": "Weekly", "used_percent": 130},
        ]
    }

    assert subscription_usage.format_subscription_usage(payload) == "weekly 0%"


def test_fetch_sync_uses_hermes_runtime_without_exposing_credentials(tmp_path, monkeypatch):
    runtime_python = tmp_path / "venv" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"windows": [{"label": "Session", "used_percent": 25}]}
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr(subscription_usage.subprocess, "run", fake_run)

    result = subscription_usage._fetch_sync(tmp_path, timeout_seconds=3.0)

    assert result == "limit 75%"
    assert captured["command"][0] == str(runtime_python)
    assert captured["command"][1] == "-c"
    assert "fetch_account_usage" in captured["command"][2]
    assert captured["kwargs"]["cwd"] == tmp_path
    assert captured["kwargs"]["timeout"] == 3.0
    assert not any("token" in part.lower() for part in captured["command"])


def test_fetch_sync_silently_skips_missing_runtime_failure_and_timeout(tmp_path, monkeypatch):
    assert subscription_usage._fetch_sync(tmp_path, timeout_seconds=1.0) == ""

    runtime_python = tmp_path / "venv" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        subscription_usage.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="no auth"),
    )
    assert subscription_usage._fetch_sync(tmp_path, timeout_seconds=1.0) == ""

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("python", 1.0)

    monkeypatch.setattr(subscription_usage.subprocess, "run", timeout)
    assert subscription_usage._fetch_sync(tmp_path, timeout_seconds=1.0) == ""
