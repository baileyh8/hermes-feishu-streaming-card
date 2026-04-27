from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request


DEFAULT_STATE_DIR = Path.home() / ".hermes_feishu_card"
PIDFILE_NAME = "sidecar.pid"
LOGFILE_NAME = "sidecar.log"


def status_sidecar(config: dict[str, dict[str, Any]]) -> dict[str, Any]:
    record = read_pid_record()
    pid = record["pid"] if record is not None else None
    health = fetch_health(config)
    running = health is not None
    return {
        "running": running,
        "pid": pid,
        "health": health,
        "pid_running": pid_is_running(pid) if pid is not None else False,
    }


def start_sidecar(config_path: str | Path, config: dict[str, dict[str, Any]]) -> str:
    if fetch_health(config) is not None:
        return "already running"

    state_dir().mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    log_handle = log_path().open("ab")
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "hermes_feishu_card.runner",
                "--config",
                str(config_path),
                "--token",
                token,
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_handle.close()

    try:
        write_pid_record(process.pid, token)
    except OSError as exc:
        stop_pid(process.pid)
        return f"failed: pidfile could not be written: {exc.__class__.__name__}"

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            clear_pid()
            return f"failed: process exited with {process.returncode}"
        health = fetch_health(config)
        if health is not None and health.get("process_token") == token:
            return "started"
        time.sleep(0.1)

    stop_pid(process.pid)
    clear_pid()
    return "failed: health check timed out"


def stop_sidecar(config: dict[str, dict[str, Any]]) -> str:
    record = read_pid_record()
    if record is None:
        if fetch_health(config) is not None:
            return "failed: running sidecar has no pidfile"
        return "not running"

    pid = record["pid"]
    health = fetch_health(config)
    if health is None:
        if pid_is_running(pid):
            return "failed: pidfile identity mismatch"
        clear_pid()
        return "not running"
    if health.get("process_token") != record["token"] or health.get("process_pid") != pid:
        return "failed: pidfile identity mismatch"

    if pid_is_running(pid):
        stop_pid(pid)
    clear_pid()
    return "stopped"


def fetch_health(config: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    server = config["server"]
    url = f"http://{server['host']}:{server['port']}/health"
    try:
        with urllib.request.urlopen(url, timeout=0.4) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if isinstance(payload, dict) and payload.get("status") == "healthy":
        return payload
    return None


def read_pid() -> int | None:
    record = read_pid_record()
    return record["pid"] if record is not None else None


def read_pid_record() -> dict[str, Any] | None:
    try:
        text = pid_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    pid = record.get("pid")
    token = record.get("token")
    if isinstance(pid, int) and isinstance(token, str) and token:
        return {"pid": pid, "token": token}
    return None


def write_pid_record(pid: int, token: str) -> None:
    payload = {"pid": pid, "token": token}
    pid_path().write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def clear_pid() -> None:
    pid_path().unlink(missing_ok=True)


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_pid(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except OSError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not pid_is_running(pid):
                return
            time.sleep(0.05)


def pid_path() -> Path:
    return state_dir() / PIDFILE_NAME


def log_path() -> Path:
    return state_dir() / LOGFILE_NAME


def state_dir() -> Path:
    configured = os.environ.get("HERMES_FEISHU_CARD_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_STATE_DIR
