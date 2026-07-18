from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import shutil
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_STATE_DIR = Path.home() / ".hermes_feishu_card"
PIDFILE_NAME = "sidecar.pid"
LOGFILE_NAME = "sidecar.log"
SYSTEMD_UNIT_NAME = "hermes-feishu-card-sidecar.service"
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def process_token_hash(token: str | None) -> str:
    if not isinstance(token, str) or not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def status_sidecar(config: dict[str, dict[str, Any]]) -> dict[str, Any]:
    record = read_pid_record()
    pid = record["pid"] if record is not None else None
    health = fetch_health(config)
    if (
        record is not None
        and record.get("manager") == "systemd-user"
        and health is not None
        and health.get("process_token_hash") == process_token_hash(record["token"])
        and isinstance(health.get("process_pid"), int)
    ):
        pid = health["process_pid"]
    running = health is not None
    return {
        "running": running,
        "pid": pid,
        "health": health,
        "pid_running": pid_is_running(pid) if pid is not None else False,
    }


def start_sidecar(
    config_path: str | Path,
    config: dict[str, dict[str, Any]],
    *,
    env_file: str | Path | None = None,
) -> str:
    health = fetch_health(config)
    systemd_user = _systemd_user_available()
    if health is not None:
        record = read_pid_record()
        if not _can_migrate_to_systemd(record, health, systemd_user=systemd_user):
            return "already running"
        stop_pid(record["pid"])
        clear_pid()

    state_dir().mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    command = _sidecar_command(config_path, env_file=env_file, token=token)

    if systemd_user:
        if not _start_systemd_user_sidecar(command):
            return "failed: systemd user service could not be started"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            health = fetch_health(config)
            if (
                health is not None
                and health.get("process_token_hash") == process_token_hash(token)
                and isinstance(health.get("process_pid"), int)
            ):
                try:
                    write_pid_record(
                        health["process_pid"],
                        token,
                        manager="systemd-user",
                        unit=SYSTEMD_UNIT_NAME,
                    )
                except OSError as exc:
                    _stop_systemd_user_sidecar(SYSTEMD_UNIT_NAME)
                    return f"failed: pidfile could not be written: {exc.__class__.__name__}"
                return "started"
            time.sleep(0.1)
        _stop_systemd_user_sidecar(SYSTEMD_UNIT_NAME)
        clear_pid()
        return "failed: health check timed out"

    log_handle = log_path().open("ab")
    try:
        process = subprocess.Popen(
            command,
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
        if health is not None and health.get("process_token_hash") == process_token_hash(token):
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
    if record.get("manager") == "systemd-user":
        if health is not None and (
            health.get("process_token_hash") != process_token_hash(record["token"])
        ):
            return "failed: pidfile identity mismatch"
        unit = str(record.get("unit") or SYSTEMD_UNIT_NAME)
        if not _stop_systemd_user_sidecar(unit):
            return "failed: systemd user service could not be stopped"
        clear_pid()
        return "stopped"
    if health is None:
        if pid_is_running(pid):
            return "failed: pidfile identity mismatch"
        clear_pid()
        return "not running"
    if (
        health.get("process_token_hash") != process_token_hash(record["token"])
        or health.get("process_pid") != pid
    ):
        return "failed: pidfile identity mismatch"

    if pid_is_running(pid):
        stop_pid(pid)
    clear_pid()
    return "stopped"


def fetch_health(config: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    server = config["server"]
    host = str(server["host"])
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    url = f"http://{url_host}:{server['port']}/health"
    try:
        with _open_health_url(url, timeout=0.4) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if isinstance(payload, dict) and payload.get("status") in {"healthy", "degraded"}:
        return payload
    return None


def _open_health_url(url: str, timeout: float):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return _NO_PROXY_OPENER.open(urllib.request.Request(url), timeout=timeout)
    return urllib.request.urlopen(url, timeout=timeout)


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
        result = {"pid": pid, "token": token}
        manager = record.get("manager")
        unit = record.get("unit")
        if manager == "systemd-user" and isinstance(unit, str) and unit:
            result.update({"manager": manager, "unit": unit})
        return result
    return None


def write_pid_record(
    pid: int,
    token: str,
    *,
    manager: str = "",
    unit: str = "",
) -> None:
    payload = {"pid": pid, "token": token}
    if manager == "systemd-user" and unit:
        payload.update({"manager": manager, "unit": unit})
    pid_path().write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _sidecar_command(
    config_path: str | Path,
    *,
    env_file: str | Path | None,
    token: str,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "hermes_feishu_card.runner",
        "--config",
        str(config_path),
    ]
    if env_file is not None:
        command.extend(("--env-file", str(env_file)))
    command.extend(("--token", token))
    return command


def _can_migrate_to_systemd(
    record: dict[str, Any] | None,
    health: dict[str, Any],
    *,
    systemd_user: bool,
) -> bool:
    if not systemd_user or record is None or record.get("manager") == "systemd-user":
        return False
    return (
        health.get("process_pid") == record.get("pid")
        and health.get("process_token_hash") == process_token_hash(record.get("token"))
    )


def _systemd_user_available() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    if shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _start_systemd_user_sidecar(command: list[str]) -> bool:
    log_file = log_path()
    try:
        result = subprocess.run(
            [
                "systemd-run",
                "--user",
                f"--unit={SYSTEMD_UNIT_NAME}",
                "--collect",
                "--property=Type=exec",
                "--property=Restart=on-failure",
                "--property=RestartSec=2s",
                f"--property=StandardOutput=append:{log_file}",
                f"--property=StandardError=append:{log_file}",
                "--",
                *command,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _stop_systemd_user_sidecar(unit: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "stop", unit],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def clear_pid() -> None:
    pid_path().unlink(missing_ok=True)


def pid_is_running(pid: int) -> bool:
    if sys.platform == "win32":
        return _pid_is_running_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_pid(pid: int) -> None:
    if sys.platform == "win32":
        _stop_pid_windows(pid)
        return
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


def _pid_is_running_windows(pid: int) -> bool:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        process_handle = kernel32.OpenProcess(0x1000, False, pid)
        if process_handle:
            kernel32.CloseHandle(process_handle)
            return True
        return False
    except Exception:
        return False


def _stop_pid_windows(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
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
