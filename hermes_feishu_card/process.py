from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_STATE_DIR = Path.home() / ".hermes_feishu_card"
PIDFILE_NAME = "sidecar.pid"
LOGFILE_NAME = "sidecar.log"
CONTROL_SHUTDOWN_PATH = "/control/shutdown"
CONTROL_TOKEN_HEADER = "X-HFC-Process-Token"
DETACHED_PIDFILE_HANDSHAKE_SECONDS = 5.0
SYSTEMD_UNIT_NAME = "hermes-feishu-card-sidecar.service"
SERVICE_MANAGER_VALUES = frozenset(
    {"auto", "systemd-user", "systemd-system", "detached"}
)
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def process_token_hash(token: str | None) -> str:
    if not isinstance(token, str) or not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def status_sidecar(config: dict[str, dict[str, Any]]) -> dict[str, Any]:
    state_error = _state_dir_security_error(
        allow_missing=True,
        require_private_mode=True,
    )
    if state_error:
        return {
            "running": False,
            "pid": None,
            "health": None,
            "pid_running": False,
            "manager": "invalid",
            "unit": "",
            "error": state_error,
        }
    record = read_pid_record()
    if record is not None and not _record_identity_valid(record):
        record = None
    pid = record["pid"] if record is not None else None
    health = fetch_health(config)
    if (
        record is not None
        and _record_manager(record) in {"systemd-user", "systemd-system"}
        and health is not None
        and _record_matches_health(record, health)
    ):
        pid = health["process_pid"]
    running = health is not None
    return {
        "running": running,
        "pid": pid,
        "health": health,
        "pid_running": pid_is_running(pid) if pid is not None else False,
        "manager": _record_manager(record) if record is not None else "unknown",
        "unit": record.get("unit", "") if record is not None else "",
    }


def start_sidecar(
    config_path: str | Path,
    config: dict[str, dict[str, Any]],
    *,
    env_file: str | Path | None = None,
    hermes_dir: str | Path | None = None,
    python_executable: str | Path | None = None,
    expected_package_version: str | None = None,
    expected_python_identity: str | None = None,
) -> str:
    selected_manager, manager_error = _select_service_manager(config)
    if manager_error:
        return manager_error
    migration = migrate_legacy_pidfile_permissions()
    if migration.startswith("failed:"):
        return "failed: invalid pidfile exists; start refused"
    state_error = _prepare_private_state_dir()
    if state_error:
        return state_error
    health = fetch_health(config)
    record = read_pid_record()
    record_path = pid_path()
    try:
        record_file_exists = record_path.exists() or record_path.is_symlink()
    except OSError:
        return "failed: pidfile state could not be inspected; start refused"
    if record is not None and not _record_identity_valid(record):
        return "failed: invalid pidfile exists; start refused"
    if health is not None:
        if record is None or not _record_identity_valid(record):
            return (
                "failed: running sidecar has no verified pidfile; "
                "manager transition refused"
            )
        if not _record_matches_health(record, health):
            return "failed: running sidecar identity mismatch; migration refused"
        if (
            _record_manager(record) == selected_manager
            and _health_matches_expected_identity(
                health,
                expected_package_version=expected_package_version,
                expected_python_identity=expected_python_identity,
            )
        ):
            return "already running"
        current_health = fetch_health(config)
        if current_health is None or not _record_matches_health(
            record, current_health
        ):
            return "failed: running sidecar changed before stop"
        if (
            _record_manager(record) == selected_manager
            and _health_matches_expected_identity(
                current_health,
                expected_package_version=expected_package_version,
                expected_python_identity=expected_python_identity,
            )
        ):
            return "already running"
        stop_result = _stop_owned_record(record, config)
        if stop_result != "stopped":
            if stop_result == "timeout":
                return "failed: authenticated sidecar shutdown timed out"
            return "failed: owned sidecar could not be stopped for manager migration"
        clear_pid()
    elif record is None and record_file_exists:
        return "failed: invalid pidfile exists; start refused"
    elif record is not None:
        record_manager = _record_manager(record)
        if (
            record_manager in {"systemd-user", "systemd-system"}
            and _explicit_systemd_manager(config) == record_manager
        ):
            if _stop_owned_record(record, config) != "stopped":
                return "failed: owned systemd sidecar could not be stopped for recovery"
            clear_pid()
        elif record_manager != "detached" or pid_is_running(record["pid"]):
            return "failed: owned sidecar health is unavailable; start refused"
        else:
            clear_pid()

    token = secrets.token_hex(16)
    command = _sidecar_command(
        config_path,
        env_file=env_file,
        token=token,
        hermes_dir=hermes_dir,
        managed_pidfile=selected_manager == "detached",
        python_executable=python_executable,
    )

    if selected_manager in {"systemd-user", "systemd-system"}:
        unit = _expected_unit(selected_manager)
        if selected_manager == "systemd-user":
            started = _start_systemd_user_sidecar(command)
            failure = "failed: systemd user service could not be started"
        else:
            started = _start_systemd_system_sidecar(command, unit)
            failure = (
                "failed: systemd system service could not be started; "
                "verify explicit caller permission"
            )
        if not started:
            return failure
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            health = fetch_health(config)
            if (
                health is not None
                and _health_matches_token(health, token)
                and _health_matches_expected_identity(
                    health,
                    expected_package_version=expected_package_version,
                    expected_python_identity=expected_python_identity,
                )
            ):
                try:
                    write_pid_record(
                        health["process_pid"],
                        token,
                        manager=selected_manager,
                        unit=unit,
                    )
                except (OSError, ValueError) as exc:
                    _stop_systemd_sidecar(selected_manager, unit)
                    return f"failed: pidfile could not be written: {exc.__class__.__name__}"
                return "started"
            time.sleep(0.1)
        _stop_systemd_sidecar(selected_manager, unit)
        clear_pid()
        return "failed: health check timed out"

    log_handle = log_path().open("ab")
    try:
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=_private_state_working_directory(),
        )
    finally:
        log_handle.close()

    try:
        write_pid_record(process.pid, token)
    except (OSError, ValueError) as exc:
        _wait_for_child_exit(
            process,
            timeout=DETACHED_PIDFILE_HANDSHAKE_SECONDS + 1.0,
        )
        return f"failed: pidfile could not be written: {exc.__class__.__name__}"

    detached_record = {
        "pid": process.pid,
        "token": token,
        "manager": "detached",
    }
    health = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            clear_pid()
            return f"failed: process exited with {process.returncode}"
        health = fetch_health(config)
        if (
            health is not None
            and _record_matches_health(detached_record, health)
            and _health_matches_expected_identity(
                health,
                expected_package_version=expected_package_version,
                expected_python_identity=expected_python_identity,
            )
        ):
            return "started"
        time.sleep(0.1)

    if health is not None and _record_matches_health(detached_record, health):
        if _stop_owned_record(detached_record, config) == "stopped":
            clear_pid()
    return "failed: health check timed out"


def stop_sidecar(config: dict[str, dict[str, Any]]) -> str:
    migration = migrate_legacy_pidfile_permissions()
    if migration.startswith("failed:"):
        return "failed: invalid pidfile exists; stop refused"
    state_error = _state_dir_security_error(
        allow_missing=True,
        require_private_mode=True,
    )
    if state_error:
        return f"failed: {state_error}"
    record = read_pid_record()
    if record is None:
        try:
            record_file_exists = pid_path().exists() or pid_path().is_symlink()
        except OSError:
            return "failed: pidfile state could not be inspected; stop refused"
        if record_file_exists:
            return "failed: invalid pidfile exists; stop refused"
        if fetch_health(config) is not None:
            return "failed: running sidecar has no pidfile; stop refused"
        return "not running"

    if not _record_identity_valid(record):
        return "failed: pidfile manager identity mismatch"
    pid = record["pid"]
    manager = _record_manager(record)
    health = fetch_health(config)
    if manager in {"systemd-user", "systemd-system"}:
        if health is None:
            if _explicit_systemd_manager(config) != manager:
                return "failed: pidfile identity mismatch"
        elif not _record_matches_health(record, health):
            return "failed: pidfile identity mismatch"
        elif not _record_matches_health(record, fetch_health(config) or {}):
            return "failed: pidfile identity changed before stop"
        unit = str(record["unit"])
        if not _stop_systemd_sidecar(manager, unit):
            label = "user" if manager == "systemd-user" else "system"
            return f"failed: systemd {label} service could not be stopped"
        clear_pid()
        return "stopped"
    if health is None:
        if pid_is_running(pid):
            return "failed: pidfile identity mismatch"
        clear_pid()
        return "not running"
    if not _record_matches_health(record, health):
        return "failed: pidfile identity mismatch"

    if not _record_matches_health(record, fetch_health(config) or {}):
        return "failed: pidfile identity changed before stop"
    stop_result = _stop_owned_record(record, config)
    if stop_result == "timeout":
        return "failed: authenticated sidecar shutdown timed out"
    if stop_result != "stopped":
        return "failed: authenticated sidecar shutdown refused"
    clear_pid()
    return "stopped"


def fetch_health(config: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    server = config["server"]
    host = local_control_host(str(server["host"]))
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


def local_control_host(configured_host: str) -> str:
    """Return the loopback endpoint paired with the configured listener."""
    normalized = configured_host.strip().lower().strip("[]")
    if normalized in {"", "0.0.0.0"}:
        return "127.0.0.1"
    if normalized == "::":
        return "::1"
    if normalized == "localhost" or normalized.startswith("127."):
        return normalized
    if normalized == "::1":
        return normalized
    return "::1" if ":" in normalized else "127.0.0.1"


def migrate_legacy_pidfile_permissions() -> str:
    """Tighten a known official 0644 pidfile without replacing its inode."""
    if os.name == "nt" or not callable(getattr(os, "getuid", None)):
        return "not needed"
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        return "failed: secure legacy pidfile migration is unavailable"
    root = state_dir().expanduser()
    state_error = _state_dir_security_error(
        allow_missing=False,
        require_private_mode=False,
    )
    if state_error:
        return "not needed"

    root_descriptor = -1
    record_descriptor = -1
    try:
        root_descriptor = os.open(
            str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        opened_root = os.fstat(root_descriptor)
        current_root = os.lstat(root)
        getuid = os.getuid
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or opened_root.st_uid != getuid()
            or opened_root.st_dev != current_root.st_dev
            or opened_root.st_ino != current_root.st_ino
        ):
            return "failed: legacy pidfile state is not safely owned"
        try:
            record_descriptor = os.open(
                PIDFILE_NAME,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                dir_fd=root_descriptor,
            )
        except FileNotFoundError:
            return "not needed"
        opened = os.fstat(record_descriptor)
        if stat.S_IMODE(opened_root.st_mode) != 0o700:
            return "failed: legacy pidfile state directory must already be private"
        if not stat.S_ISREG(opened.st_mode) or opened.st_uid != getuid():
            return "failed: legacy pidfile could not be verified"
        mode = stat.S_IMODE(opened.st_mode)
        if mode == 0o600:
            return "not needed"
        if mode != 0o644:
            return "failed: legacy pidfile could not be verified"
        raw = os.read(record_descriptor, 4097)
        if len(raw) > 4096 or not _known_legacy_record(raw):
            return "failed: legacy pidfile could not be verified"
        before_change = os.fstat(record_descriptor)
        if not _same_pidfile_snapshot(opened, before_change):
            return "failed: legacy pidfile changed during verification"
        os.fchmod(record_descriptor, 0o600)
        os.fsync(record_descriptor)
        after_change = os.fstat(record_descriptor)
        final_root = os.fstat(root_descriptor)
        if (
            after_change.st_dev != opened.st_dev
            or after_change.st_ino != opened.st_ino
            or after_change.st_uid != opened.st_uid
            or stat.S_IMODE(after_change.st_mode) != 0o600
            or final_root.st_dev != opened_root.st_dev
            or final_root.st_ino != opened_root.st_ino
        ):
            return "failed: legacy pidfile changed during migration"
        return "migrated"
    except OSError:
        return "failed: legacy pidfile could not be migrated safely"
    finally:
        if record_descriptor >= 0:
            os.close(record_descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)


def _known_legacy_record(raw: bytes) -> bool:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    keys = set(payload)
    manager = payload.get("manager", "detached")
    if manager == "detached":
        if keys not in ({"pid", "token"}, {"pid", "token", "manager"}):
            return False
    elif manager in {"systemd-user", "systemd-system"}:
        if keys != {"pid", "token", "manager", "unit"}:
            return False
    else:
        return False
    normalized = {
        "pid": payload.get("pid"),
        "token": payload.get("token"),
        "manager": manager,
    }
    if "unit" in payload:
        normalized["unit"] = payload.get("unit")
    return _record_identity_valid(normalized)


def _same_pidfile_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return bool(
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_uid == right.st_uid
        and left.st_mode == right.st_mode
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _open_health_url(url: str, timeout: float):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return _NO_PROXY_OPENER.open(urllib.request.Request(url), timeout=timeout)
    return urllib.request.urlopen(url, timeout=timeout)


def read_pid() -> int | None:
    record = read_pid_record()
    return record["pid"] if record is not None else None


def read_pid_record() -> dict[str, Any] | None:
    path = pid_path()
    if path.is_symlink():
        return None
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(str(path), flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return None
        if _supports_posix_state_permissions():
            getuid = getattr(os, "getuid", None)
            if callable(getuid) and metadata.st_uid != getuid():
                return None
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                return None
        handle = os.fdopen(descriptor, "r", encoding="utf-8")
        descriptor = None
        with handle:
            text = handle.read(4097)
    except (OSError, UnicodeError):
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(text) > 4096:
        return None
    text = text.strip()
    try:
        record = json.loads(text)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    pid = record.get("pid")
    token = record.get("token")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if not isinstance(token, str) or not token:
        return None
    manager = record.get("manager", "detached")
    result = {"pid": pid, "token": token, "manager": manager}
    unit = record.get("unit")
    if unit is not None:
        result["unit"] = unit
    return result if _record_identity_valid(result) else None


def write_pid_record(
    pid: int,
    token: str,
    *,
    manager: str = "detached",
    unit: str = "",
) -> None:
    payload: dict[str, Any] = {"pid": pid, "token": token, "manager": manager}
    if unit:
        payload["unit"] = unit
    if not _record_identity_valid(payload):
        raise ValueError("invalid pidfile manager identity")
    path = pid_path()
    if path.is_symlink():
        raise ValueError("pidfile must not be a symbolic link")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        try:
            fchmod = getattr(os, "fchmod", None)
            if callable(fchmod):
                fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "w", encoding="utf-8")
        except Exception:
            os.close(descriptor)
            raise
        with handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            raise ValueError("pidfile must not be a symbolic link")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sidecar_command(
    config_path: str | Path,
    *,
    env_file: str | Path | None,
    token: str,
    hermes_dir: str | Path | None = None,
    managed_pidfile: bool = False,
    python_executable: str | Path | None = None,
) -> list[str]:
    resolved_config = Path(config_path).expanduser().resolve(strict=False)
    selected_python = (
        str(Path(python_executable).expanduser())
        if python_executable is not None
        else sys.executable
    )
    command = [
        selected_python,
        "-I",
        "-m",
        "hermes_feishu_card.runner",
        "--config",
        str(resolved_config),
    ]
    if env_file is not None:
        resolved_env = Path(env_file).expanduser().resolve(strict=False)
        command.extend(("--env-file", str(resolved_env)))
    if hermes_dir is not None:
        resolved_hermes_dir = Path(hermes_dir).expanduser().resolve(strict=False)
        command.extend(("--hermes-dir", str(resolved_hermes_dir)))
    if managed_pidfile:
        command.append("--managed-pidfile")
    command.extend(("--token", token))
    return command


def wait_for_managed_pidfile(
    pid: int,
    token: str,
    *,
    timeout: float = DETACHED_PIDFILE_HANDSHAKE_SECONDS,
) -> bool:
    """Wait until the launcher has persisted this exact detached identity."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not token:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = read_pid_record()
        expected = {"pid": pid, "token": token, "manager": "detached"}
        if record == expected:
            return True
        if sys.platform == "win32":
            launcher_pid = os.getppid()
            launcher_record = {
                "pid": launcher_pid,
                "token": token,
                "manager": "detached",
            }
            if record == launcher_record:
                try:
                    write_pid_record(pid, token, manager="detached")
                except (OSError, ValueError):
                    return False
                return read_pid_record() == expected
        time.sleep(0.05)
    return False


def _wait_for_child_exit(process: Any, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.05)
    return process.poll() is not None


def _prepare_private_state_dir() -> str:
    private_state_dir = state_dir()
    security_error = _state_dir_security_error(
        allow_missing=True,
        require_private_mode=False,
    )
    if security_error:
        return f"failed: {security_error}"
    try:
        private_state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        security_error = _state_dir_security_error(
            allow_missing=False,
            require_private_mode=False,
        )
        if security_error:
            return f"failed: {security_error}"
        private_state_dir.chmod(0o700)
    except OSError:
        return "failed: state directory could not be prepared"
    security_error = _state_dir_security_error(
        allow_missing=False,
        require_private_mode=True,
    )
    if security_error:
        return f"failed: {security_error}"
    return ""


def _state_dir_security_error(
    *,
    allow_missing: bool,
    require_private_mode: bool,
) -> str:
    configured = state_dir().expanduser()
    if ".." in configured.parts:
        return "state directory path must not contain parent traversal"
    candidate = configured
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).absolute()
    try:
        canonical = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return "state directory could not be inspected"
    if candidate == Path(candidate.anchor) or canonical == Path(canonical.anchor):
        return "state directory must not be filesystem root"
    parts = candidate.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return "" if allow_missing else "state directory does not exist"
        except OSError:
            return "state directory could not be inspected"
        if stat.S_ISLNK(metadata.st_mode):
            if current == candidate:
                return "state directory must not be a symbolic link"
            return "state directory path must not contain symbolic links"
        if current != candidate and not stat.S_ISDIR(metadata.st_mode):
            return "state directory parent is not a directory"
        if current != candidate:
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            return "state directory is not a private directory"
        if _supports_posix_state_permissions():
            getuid = getattr(os, "getuid", None)
            if callable(getuid) and metadata.st_uid != getuid():
                return "state directory is not owned by the current user"
            if require_private_mode and stat.S_IMODE(metadata.st_mode) & 0o077:
                return "state directory permissions must be private"
    return ""


def _supports_posix_state_permissions() -> bool:
    return os.name != "nt"


def _record_manager(record: dict[str, Any]) -> str:
    manager = record.get("manager", "detached")
    return manager if isinstance(manager, str) else ""


def _expected_unit(manager: str) -> str:
    if manager == "systemd-user":
        return SYSTEMD_UNIT_NAME
    if manager == "systemd-system":
        return _systemd_system_unit_name()
    return ""


def _record_identity_valid(record: dict[str, Any]) -> bool:
    pid = record.get("pid")
    token = record.get("token")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if not isinstance(token, str) or not token:
        return False
    manager = _record_manager(record)
    unit = record.get("unit", "")
    if manager == "detached":
        return unit in {"", None}
    if manager not in {"systemd-user", "systemd-system"}:
        return False
    if sys.platform.startswith("win") or not callable(getattr(os, "getuid", None)):
        return False
    return isinstance(unit, str) and unit == _expected_unit(manager)


def _record_matches_health(
    record: dict[str, Any], health: dict[str, Any]
) -> bool:
    if health.get("process_token_hash") != process_token_hash(record.get("token")):
        return False
    health_pid = health.get("process_pid")
    if not isinstance(health_pid, int) or isinstance(health_pid, bool) or health_pid <= 0:
        return False
    if _record_manager(record) == "detached":
        return health_pid == record.get("pid")
    return True


def _health_matches_token(health: dict[str, Any], token: str) -> bool:
    health_pid = health.get("process_pid")
    return (
        health.get("process_token_hash") == process_token_hash(token)
        and isinstance(health_pid, int)
        and not isinstance(health_pid, bool)
        and health_pid > 0
    )


def _health_matches_expected_identity(
    health: dict[str, Any],
    *,
    expected_package_version: str | None,
    expected_python_identity: str | None,
) -> bool:
    if (
        expected_package_version is not None
        and health.get("package_version") != expected_package_version
    ):
        return False
    if (
        expected_python_identity is not None
        and health.get("python_identity") != expected_python_identity
    ):
        return False
    return True


def _stop_owned_record(
    record: dict[str, Any],
    config: dict[str, dict[str, Any]],
) -> str:
    manager = _record_manager(record)
    if manager == "detached":
        if not _request_authenticated_shutdown(config, str(record["token"])):
            return "request-refused"
        return "stopped" if _wait_for_health_disappearance(config) else "timeout"
    return (
        "stopped"
        if _stop_systemd_sidecar(manager, str(record["unit"]))
        else "manager-failed"
    )


def _request_authenticated_shutdown(
    config: dict[str, dict[str, Any]], token: str
) -> bool:
    server = config["server"]
    control_host = local_control_host(str(server["host"]))
    url_host = f"[{control_host}]" if ":" in control_host else control_host
    request = urllib.request.Request(
        f"http://{url_host}:{server['port']}{CONTROL_SHUTDOWN_PATH}",
        data=b"",
        headers={CONTROL_TOKEN_HEADER: token},
        method="POST",
    )
    try:
        with _NO_PROXY_OPENER.open(request, timeout=0.8) as response:
            if getattr(response, "status", None) != 202:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return payload == {"ok": True, "status": "stopping"}


def _wait_for_health_disappearance(
    config: dict[str, dict[str, Any]], *, timeout: float = 5.0
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fetch_health(config) is None:
            return True
        time.sleep(0.05)
    return False


def _stop_systemd_sidecar(manager: str, unit: str) -> bool:
    if manager == "systemd-user":
        return _stop_systemd_user_sidecar(unit)
    if manager == "systemd-system":
        return _stop_systemd_system_sidecar(unit)
    return False


def _systemd_system_unit_name() -> str:
    getuid = getattr(os, "getuid", None)
    if not callable(getuid):
        raise RuntimeError("systemd system units require POSIX user identity")
    uid = getuid()
    scope = f"{uid}:{state_dir().expanduser().resolve(strict=False)}"
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]
    return f"hermes-feishu-card-sidecar-{uid}-{digest}.service"


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


def _systemd_system_available() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    if shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--system", "--no-ask-password", "show-environment"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _select_service_manager(
    config: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    requested = _configured_service_manager(config)
    if requested not in SERVICE_MANAGER_VALUES:
        return "", "failed: invalid service.manager"
    if requested == "auto":
        return ("systemd-user", "") if _systemd_user_available() else ("detached", "")
    if requested == "detached":
        return "detached", ""
    if requested == "systemd-user":
        if _systemd_user_available():
            return requested, ""
        return (
            "",
            "failed: service.manager=systemd-user is unavailable; "
            "start the user systemd manager or select detached",
        )
    if _systemd_system_available():
        return requested, ""
    return (
        "",
        "failed: service.manager=systemd-system is unavailable; "
        "it requires Linux with systemd-run and systemctl",
    )


def _configured_service_manager(config: dict[str, dict[str, Any]]) -> Any:
    service = config.get("service", {})
    return service.get("manager", "auto") if isinstance(service, dict) else "auto"


def _explicit_systemd_manager(config: dict[str, dict[str, Any]]) -> str:
    requested = _configured_service_manager(config)
    return requested if requested in {"systemd-user", "systemd-system"} else ""


def _start_systemd_user_sidecar(command: list[str]) -> bool:
    log_file = log_path()
    try:
        result = subprocess.run(
            [
                "systemd-run",
                "--user",
                f"--unit={SYSTEMD_UNIT_NAME}",
                "--collect",
                _systemd_state_environment_arg(),
                _systemd_working_directory_arg(),
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


def _start_systemd_system_sidecar(command: list[str], unit: str) -> bool:
    if unit != _systemd_system_unit_name():
        return False
    log_file = log_path()
    try:
        result = subprocess.run(
            [
                "systemd-run",
                "--system",
                "--no-ask-password",
                f"--unit={unit}",
                "--collect",
                _systemd_state_environment_arg(),
                _systemd_working_directory_arg(),
                f"--uid={os.getuid()}",
                f"--gid={os.getgid()}",
                "--property=Type=exec",
                "--property=Restart=on-failure",
                "--property=RestartSec=2s",
                "--property=UMask=0077",
                "--property=NoNewPrivileges=yes",
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


def _systemd_state_environment_arg() -> str:
    return f"--setenv=HERMES_FEISHU_CARD_STATE_DIR={state_dir().expanduser()}"


def _private_state_working_directory() -> str:
    return str(state_dir().expanduser().resolve(strict=False))


def _systemd_working_directory_arg() -> str:
    working_directory = _private_state_working_directory().replace("%", "%%")
    return f"--property=WorkingDirectory={working_directory}"


def _stop_systemd_user_sidecar(unit: str) -> bool:
    if unit != SYSTEMD_UNIT_NAME:
        return False
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


def _stop_systemd_system_sidecar(unit: str) -> bool:
    if unit != _systemd_system_unit_name():
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--system", "--no-ask-password", "stop", unit],
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
    del pid
    raise RuntimeError("numeric PID termination is disabled")


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
    del pid
    raise RuntimeError("numeric PID termination is disabled")


def pid_path() -> Path:
    return state_dir() / PIDFILE_NAME


def log_path() -> Path:
    return state_dir() / LOGFILE_NAME


def state_dir() -> Path:
    configured = os.environ.get("HERMES_FEISHU_CARD_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_STATE_DIR
