from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

from hermes_feishu_card import process


UNIT_NAME = "hermes-feishu-card-sidecar.service"
MANIFEST_NAME = "persistent-service.json"
_MANIFEST_PROTOCOL = "hfc-systemd-user-service-v1"
_EXPECTED_MANIFEST_KEYS = {
    "protocol",
    "unit_name",
    "unit_path",
    "unit_sha256",
    "config_path",
    "config_sha256",
    "env_path",
    "env_sha256",
    "hermes_dir",
    "python_executable",
    "expected_package_version",
    "expected_python_identity",
    "state_dir",
}


def enable_persistent_sidecar(
    *,
    config_path: str | Path,
    config: Mapping[str, object],
    env_file: str | Path | None,
    hermes_dir: str | Path,
    python_executable: str | Path,
    expected_package_version: str,
    expected_python_identity: str,
) -> str:
    manager = _configured_manager(config)
    if manager not in {"auto", "systemd-user"}:
        return "failed: persistent service requires service.manager=auto or systemd-user"
    availability_error = _availability_error()
    if availability_error:
        return availability_error
    if not _linger_enabled():
        return "failed: systemd user linger is disabled; run loginctl enable-linger"
    try:
        inputs = _normalize_inputs(
            config_path=config_path,
            env_file=env_file,
            hermes_dir=hermes_dir,
            python_executable=python_executable,
            expected_package_version=expected_package_version,
            expected_python_identity=expected_python_identity,
        )
        unit_contents = _render_unit(inputs)
        manifest_contents = _render_manifest(inputs, unit_contents)
    except (OSError, RuntimeError, ValueError) as exc:
        return f"failed: persistent service input is invalid: {exc}"

    unit_path = _unit_path()
    manifest_path = _manifest_path()
    try:
        unit_exists = unit_path.exists() or unit_path.is_symlink()
        manifest_exists = manifest_path.exists() or manifest_path.is_symlink()
    except OSError:
        return "failed: persistent service ownership could not be inspected"
    if unit_exists or manifest_exists:
        if not (unit_exists and manifest_exists):
            return "failed: persistent service ownership is incomplete"
        try:
            if (
                _read_owned_file(unit_path, maximum=1024 * 1024) != unit_contents
                or _read_owned_file(manifest_path, maximum=64 * 1024)
                != manifest_contents
            ):
                return "failed: persistent service ownership changed"
        except (OSError, ValueError):
            return "failed: persistent service ownership changed"
        if (
            _systemctl_ok("is-enabled", UNIT_NAME)
            and _systemctl_ok("is-active", UNIT_NAME)
            and _health_matches(config, inputs)
        ):
            return "already enabled"
        return _start_owned_service(
            config=config,
            inputs=inputs,
            unit_contents=unit_contents,
            manifest_contents=manifest_contents,
            created=False,
        )

    if _unowned_unit_active():
        return "failed: an unmanaged sidecar unit is active; stop it before enable"

    state_error = _prepare_state_dir()
    if state_error:
        return state_error
    try:
        _prepare_unit_parent(unit_path.parent)
        _write_new_file(unit_path, unit_contents)
        try:
            _write_new_file(manifest_path, manifest_contents)
        except Exception:
            _unlink_if_exact(unit_path, unit_contents)
            raise
    except (OSError, ValueError):
        return "failed: persistent service ownership could not be written"
    return _start_owned_service(
        config=config,
        inputs=inputs,
        unit_contents=unit_contents,
        manifest_contents=manifest_contents,
        created=True,
    )


def disable_persistent_sidecar() -> str:
    availability_error = _availability_error(require_linger=False)
    if availability_error:
        return availability_error
    unit_path = _unit_path()
    manifest_path = _manifest_path()
    try:
        unit_exists = unit_path.exists() or unit_path.is_symlink()
        manifest_exists = manifest_path.exists() or manifest_path.is_symlink()
    except OSError:
        return "failed: persistent service ownership could not be inspected"
    if not unit_exists and not manifest_exists:
        return "not enabled"
    if not unit_exists or not manifest_exists:
        return "failed: persistent service ownership is incomplete"
    try:
        unit_contents = _read_owned_file(unit_path, maximum=1024 * 1024)
        manifest_contents = _read_owned_file(manifest_path, maximum=64 * 1024)
        manifest = _parse_manifest(manifest_contents)
    except (OSError, ValueError):
        return "failed: persistent service ownership changed; disable refused"
    if manifest["unit_sha256"] != _digest(unit_contents):
        return "failed: persistent service unit changed; disable refused"
    if not _systemctl_ok("disable", "--now", UNIT_NAME):
        return "failed: persistent systemd user service could not be disabled"
    try:
        if _read_owned_file(unit_path, maximum=1024 * 1024) != unit_contents:
            raise ValueError("unit changed")
        if _read_owned_file(manifest_path, maximum=64 * 1024) != manifest_contents:
            raise ValueError("manifest changed")
        unit_path.unlink()
        manifest_path.unlink()
    except (OSError, ValueError):
        return "failed: persistent service stopped but ownership cleanup failed"
    if not _systemctl_ok("daemon-reload"):
        return "failed: persistent service removed but systemd reload failed"
    return "disabled"


def persistent_sidecar_matches(
    *,
    config_path: str | Path,
    config: Mapping[str, object],
    env_file: str | Path | None,
    hermes_dir: str | Path,
    python_executable: str | Path,
    expected_package_version: str,
    expected_python_identity: str,
) -> bool:
    if _availability_error(require_linger=False):
        return False
    try:
        inputs = _normalize_inputs(
            config_path=config_path,
            env_file=env_file,
            hermes_dir=hermes_dir,
            python_executable=python_executable,
            expected_package_version=expected_package_version,
            expected_python_identity=expected_python_identity,
        )
        unit_contents = _render_unit(inputs)
        manifest_contents = _render_manifest(inputs, unit_contents)
        if _read_owned_file(_unit_path(), maximum=1024 * 1024) != unit_contents:
            return False
        if _read_owned_file(_manifest_path(), maximum=64 * 1024) != manifest_contents:
            return False
    except (OSError, RuntimeError, ValueError):
        return False
    return bool(
        _systemctl_ok("is-enabled", UNIT_NAME)
        and _systemctl_ok("is-active", UNIT_NAME)
        and _health_matches(config, inputs)
    )


def persistent_sidecar_active() -> bool:
    if _availability_error(require_linger=False):
        return False
    try:
        unit_contents = _read_owned_file(_unit_path(), maximum=1024 * 1024)
        manifest = _parse_manifest(
            _read_owned_file(_manifest_path(), maximum=64 * 1024)
        )
    except (OSError, ValueError):
        return False
    return bool(
        manifest["unit_sha256"] == _digest(unit_contents)
        and _systemctl_ok("is-enabled", UNIT_NAME)
        and _systemctl_ok("is-active", UNIT_NAME)
    )


def _start_owned_service(
    *,
    config: Mapping[str, object],
    inputs: dict[str, str],
    unit_contents: bytes,
    manifest_contents: bytes,
    created: bool,
) -> str:
    if not _systemctl_ok("daemon-reload"):
        if created:
            _unlink_if_exact(_manifest_path(), manifest_contents)
            _unlink_if_exact(_unit_path(), unit_contents)
        _systemctl_ok("daemon-reload")
        return "failed: persistent systemd user service could not be enabled"
    if not _systemctl_ok("enable", "--now", UNIT_NAME):
        _rollback_enable(unit_contents, manifest_contents, created=created)
        return "failed: persistent systemd user service could not be enabled"
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if _health_matches(config, inputs):
            return "enabled"
        time.sleep(0.1)
    _rollback_enable(unit_contents, manifest_contents, created=created)
    return "failed: persistent systemd user service health check timed out"


def _rollback_enable(
    unit_contents: bytes,
    manifest_contents: bytes,
    *,
    created: bool,
) -> None:
    stopped = _systemctl_ok("disable", "--now", UNIT_NAME)
    if created and stopped:
        _unlink_if_exact(_manifest_path(), manifest_contents)
        _unlink_if_exact(_unit_path(), unit_contents)
    _systemctl_ok("daemon-reload")


def _health_matches(config: Mapping[str, object], inputs: Mapping[str, str]) -> bool:
    health = _fetch_health(config)
    if type(health) is not dict:
        return False
    health_pid = health.get("process_pid")
    return bool(
        type(health_pid) is int
        and health_pid > 0
        and health.get("process_token_hash") == ""
        and process._health_matches_expected_identity(
            health,
            expected_package_version=inputs["expected_package_version"],
            expected_python_identity=inputs["expected_python_identity"],
        )
    )


def _normalize_inputs(
    *,
    config_path: str | Path,
    env_file: str | Path | None,
    hermes_dir: str | Path,
    python_executable: str | Path,
    expected_package_version: str,
    expected_python_identity: str,
) -> dict[str, str]:
    config = _regular_path(config_path, "config")
    env = _regular_path(env_file, "env file") if env_file is not None else None
    hermes = _directory_path(hermes_dir, "Hermes root")
    python = _launcher_path(python_executable)
    if (
        type(expected_package_version) is not str
        or not expected_package_version.strip()
        or any(character in expected_package_version for character in "\r\n\0")
    ):
        raise ValueError("package version is invalid")
    _hfc_identity_prefix = "python-sha256:"
    if (
        type(expected_python_identity) is not str
        or not expected_python_identity.startswith(_hfc_identity_prefix)
        or len(expected_python_identity) != len(_hfc_identity_prefix) + 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_python_identity[len(_hfc_identity_prefix):]
        )
    ):
        raise ValueError("Python identity is invalid")
    return {
        "config_path": str(config),
        "config_sha256": _digest(config.read_bytes()),
        "env_path": str(env) if env is not None else "",
        "env_sha256": _digest(env.read_bytes()) if env is not None else "",
        "hermes_dir": str(hermes),
        "python_executable": str(python),
        "expected_package_version": expected_package_version,
        "expected_python_identity": expected_python_identity,
        "state_dir": str(_state_dir().expanduser().resolve(strict=False)),
    }


def _render_unit(inputs: Mapping[str, str]) -> bytes:
    command = [
        inputs["python_executable"],
        "-I",
        "-m",
        "hermes_feishu_card.runner",
        "--config",
        inputs["config_path"],
    ]
    if inputs["env_path"]:
        command.extend(("--env-file", inputs["env_path"]))
    command.extend(("--hermes-dir", inputs["hermes_dir"]))
    exec_start = " ".join(_systemd_quote(argument) for argument in command)
    state_assignment = "HERMES_FEISHU_CARD_STATE_DIR=" + inputs["state_dir"]
    value = (
        "[Unit]\n"
        "Description=Hermes Feishu streaming card sidecar\n"
        "Wants=network-online.target\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"Environment={_systemd_quote(state_assignment)}\n"
        f"WorkingDirectory={inputs['state_dir'].replace('%', '%%')}\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=2s\n"
        "UMask=0077\n"
        "NoNewPrivileges=true\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    return value.encode("utf-8")


def _render_manifest(inputs: Mapping[str, str], unit_contents: bytes) -> bytes:
    payload = {
        "protocol": _MANIFEST_PROTOCOL,
        "unit_name": UNIT_NAME,
        "unit_path": str(_unit_path()),
        "unit_sha256": _digest(unit_contents),
        **dict(inputs),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    ) + b"\n"


def _parse_manifest(contents: bytes) -> dict[str, str]:
    try:
        payload = json.loads(contents.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest invalid") from exc
    if type(payload) is not dict or set(payload) != _EXPECTED_MANIFEST_KEYS:
        raise ValueError("manifest invalid")
    if not all(type(key) is str and type(value) is str for key, value in payload.items()):
        raise ValueError("manifest invalid")
    if (
        payload["protocol"] != _MANIFEST_PROTOCOL
        or payload["unit_name"] != UNIT_NAME
        or payload["unit_path"] != str(_unit_path())
        or not _valid_digest(payload["unit_sha256"])
        or not _valid_digest(payload["config_sha256"])
        or (payload["env_path"] and not _valid_digest(payload["env_sha256"]))
        or (not payload["env_path"] and payload["env_sha256"])
    ):
        raise ValueError("manifest invalid")
    return payload


def _configured_manager(config: Mapping[str, object]) -> object:
    if not isinstance(config, Mapping):
        return None
    service = config.get("service", {})
    return service.get("manager", "auto") if isinstance(service, Mapping) else None


def _availability_error(*, require_linger: bool = True) -> str:
    if not sys.platform.startswith("linux"):
        return "failed: persistent service requires Linux systemd"
    if shutil.which("systemctl") is None:
        return "failed: persistent service requires systemctl"
    if require_linger and shutil.which("loginctl") is None:
        return "failed: persistent service requires systemctl and loginctl"
    return ""


def _linger_enabled() -> bool:
    getuid = getattr(os, "getuid", None)
    if not callable(getuid):
        return False
    try:
        result = subprocess.run(
            [
                "loginctl",
                "show-user",
                str(getuid()),
                "--property=Linger",
                "--value",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "yes"


def _systemctl_ok(*arguments: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", *arguments],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _unowned_unit_active() -> bool:
    return _systemctl_ok("is-active", UNIT_NAME)


def _state_dir() -> Path:
    return process.state_dir()


def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def _manifest_path() -> Path:
    return _state_dir() / MANIFEST_NAME


def _fetch_health(config: Mapping[str, object]) -> dict[str, Any] | None:
    return process.fetch_health(config)  # type: ignore[arg-type]


def _regular_path(value: str | Path | None, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise ValueError(f"{label} path is missing")
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ValueError(f"{label} path is a symbolic link")
    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} path is not a regular file")
    return resolved


def _directory_path(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ValueError(f"{label} is a symbolic link")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{label} is not a directory")
    return resolved


def _launcher_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    metadata = path.lstat()
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
        raise ValueError("Python launcher is nonregular")
    resolved = path.resolve(strict=True)
    if not stat.S_ISREG(resolved.stat().st_mode) or not os.access(resolved, os.X_OK):
        raise ValueError("Python launcher is not executable")
    return path.absolute()


def _prepare_unit_parent(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("systemd user directory is unsafe")


def _prepare_state_dir() -> str:
    path = _state_dir().expanduser()
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        metadata = path.lstat()
        getuid = getattr(os, "getuid", None)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or (callable(getuid) and metadata.st_uid != getuid())
        ):
            return "failed: state directory is not safely owned"
        if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o700:
            return "failed: state directory must use mode 0700"
    except OSError:
        return "failed: state directory could not be prepared"
    return ""


def _write_new_file(path: Path, contents: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("ownership file already exists")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_owned_file(path: Path, *, maximum: int) -> bytes:
    if path.is_symlink():
        raise ValueError("ownership file is a symbolic link")
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags)
        metadata = os.fstat(descriptor)
        getuid = getattr(os, "getuid", None)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (callable(getuid) and metadata.st_uid != getuid())
            or metadata.st_size > maximum
        ):
            raise ValueError("ownership file is unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            contents = handle.read(maximum + 1)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(contents) > maximum:
        raise ValueError("ownership file is too large")
    return contents


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_if_exact(path: Path, expected: bytes) -> None:
    try:
        if _read_owned_file(path, maximum=max(64 * 1024, len(expected))) == expected:
            path.unlink()
    except (OSError, ValueError):
        return


def _systemd_quote(value: str) -> str:
    if type(value) is not str or any(character in value for character in "\r\n\0"):
        raise ValueError("systemd argument is invalid")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def _digest(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def _valid_digest(value: str) -> bool:
    return bool(
        type(value) is str
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )
