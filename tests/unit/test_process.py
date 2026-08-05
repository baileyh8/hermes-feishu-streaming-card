from __future__ import annotations

import stat
import subprocess
from types import SimpleNamespace

import pytest

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


def test_pid_record_preserves_detached_manager_identity(monkeypatch, tmp_path):
    record_path = tmp_path / "sidecar.pid"
    monkeypatch.setattr(process, "pid_path", lambda: record_path)

    process.write_pid_record(4321, "sidecar-token", manager="detached")

    assert process.read_pid_record() == {
        "pid": 4321,
        "token": "sidecar-token",
        "manager": "detached",
    }


def test_pid_record_accepts_only_expected_systemd_unit_identity(
    monkeypatch, tmp_path
):
    record_path = tmp_path / "sidecar.pid"
    state_root = tmp_path / "state"
    monkeypatch.setattr(process, "pid_path", lambda: record_path)
    monkeypatch.setattr(process, "state_dir", lambda: state_root)
    owner_uid = tmp_path.stat().st_uid
    monkeypatch.setattr(process.os, "getuid", lambda: owner_uid)
    unit = process._systemd_system_unit_name()

    process.write_pid_record(
        4321,
        "sidecar-token",
        manager="systemd-system",
        unit=unit,
    )
    assert process.read_pid_record() == {
        "pid": 4321,
        "token": "sidecar-token",
        "manager": "systemd-system",
        "unit": unit,
    }

    record_path.write_text(
        '{"manager":"systemd-system","pid":4321,"token":"sidecar-token",'
        '"unit":"attacker.service"}\n',
        encoding="utf-8",
    )
    assert process.read_pid_record() is None


def test_pid_record_io_refuses_symlink_without_touching_target(monkeypatch, tmp_path):
    record_path = tmp_path / "sidecar.pid"
    target = tmp_path / "target.txt"
    target.write_text("keep-me\n", encoding="utf-8")
    record_path.symlink_to(target)
    monkeypatch.setattr(process, "pid_path", lambda: record_path)

    assert process.read_pid_record() is None
    with pytest.raises(ValueError, match="symbolic link"):
        process.write_pid_record(4321, "sidecar-token")

    assert target.read_text(encoding="utf-8") == "keep-me\n"


def test_pid_record_read_refuses_non_private_file(monkeypatch, tmp_path):
    record_path = tmp_path / "sidecar.pid"
    record_path.write_text(
        '{"manager":"detached","pid":4321,"token":"sidecar-token"}\n',
        encoding="utf-8",
    )
    record_path.chmod(0o644)
    monkeypatch.setattr(process, "pid_path", lambda: record_path)

    assert process.read_pid_record() is None


def test_migrate_legacy_detached_pidfile_tightens_same_inode_only(
    monkeypatch, tmp_path
):
    state_root = tmp_path / "legacy-state"
    state_root.mkdir(mode=0o700)
    record_path = state_root / "sidecar.pid"
    original = b'{"pid":4321,"token":"legacy-token"}\n'
    record_path.write_bytes(original)
    record_path.chmod(0o644)
    inode = record_path.stat().st_ino
    monkeypatch.setattr(process, "state_dir", lambda: state_root)

    assert process.migrate_legacy_pidfile_permissions() == "migrated"

    assert record_path.stat().st_ino == inode
    assert stat.S_IMODE(record_path.stat().st_mode) == 0o600
    assert record_path.read_bytes() == original
    assert process.read_pid_record() == {
        "pid": 4321,
        "token": "legacy-token",
        "manager": "detached",
    }


@pytest.mark.parametrize("manager", ["systemd-user", "systemd-system"])
def test_migrate_legacy_systemd_pidfile_accepts_only_fixed_unit_shape(
    monkeypatch, tmp_path, manager
):
    state_root = tmp_path / "legacy-state"
    state_root.mkdir(mode=0o700)
    monkeypatch.setattr(process, "state_dir", lambda: state_root)
    owner_uid = state_root.stat().st_uid
    monkeypatch.setattr(process.os, "getuid", lambda: owner_uid)
    unit = (
        process.SYSTEMD_UNIT_NAME
        if manager == "systemd-user"
        else process._systemd_system_unit_name()
    )
    record_path = state_root / "sidecar.pid"
    record_path.write_text(
        '{"manager":'
        + repr(manager).replace("'", '"')
        + ',"pid":4321,"token":"legacy-token","unit":'
        + repr(unit).replace("'", '"')
        + "}\n",
        encoding="utf-8",
    )
    record_path.chmod(0o644)

    assert process.migrate_legacy_pidfile_permissions() == "migrated"
    assert process.read_pid_record() == {
        "pid": 4321,
        "token": "legacy-token",
        "manager": manager,
        "unit": unit,
    }


def test_migrate_legacy_pidfile_refuses_non_private_state_root(
    monkeypatch, tmp_path
):
    state_root = tmp_path / "legacy-state"
    state_root.mkdir(mode=0o755)
    state_root.chmod(0o755)
    record_path = state_root / "sidecar.pid"
    record_path.write_text(
        '{"pid":4321,"token":"legacy-token"}\n', encoding="utf-8"
    )
    record_path.chmod(0o644)
    monkeypatch.setattr(process, "state_dir", lambda: state_root)

    assert process.migrate_legacy_pidfile_permissions().startswith("failed:")
    assert stat.S_IMODE(record_path.stat().st_mode) == 0o644


def test_migrate_legacy_pidfile_refuses_unknown_record_shape(
    monkeypatch, tmp_path
):
    state_root = tmp_path / "legacy-state"
    state_root.mkdir(mode=0o700)
    record_path = state_root / "sidecar.pid"
    original = (
        b'{"pid":4321,"token":"legacy-token","unexpected":"value"}\n'
    )
    record_path.write_bytes(original)
    record_path.chmod(0o644)
    monkeypatch.setattr(process, "state_dir", lambda: state_root)

    assert process.migrate_legacy_pidfile_permissions().startswith("failed:")
    assert stat.S_IMODE(record_path.stat().st_mode) == 0o644
    assert record_path.read_bytes() == original


def test_pid_record_read_refuses_foreign_owned_file(monkeypatch, tmp_path):
    record_path = tmp_path / "sidecar.pid"
    record_path.write_text(
        '{"manager":"detached","pid":4321,"token":"sidecar-token"}\n',
        encoding="utf-8",
    )
    record_path.chmod(0o600)
    real_fstat = process.os.fstat
    real_uid = record_path.stat().st_uid
    monkeypatch.setattr(process, "pid_path", lambda: record_path)
    monkeypatch.setattr(process.os, "getuid", lambda: real_uid)

    def foreign_fstat(descriptor):
        metadata = real_fstat(descriptor)
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_uid=real_uid + 1,
        )

    monkeypatch.setattr(process.os, "fstat", foreign_fstat)

    assert process.read_pid_record() is None


def test_pid_record_read_closes_descriptor_when_metadata_probe_fails(
    monkeypatch, tmp_path
):
    record_path = tmp_path / "sidecar.pid"
    record_path.write_text(
        '{"manager":"detached","pid":4321,"token":"sidecar-token"}\n',
        encoding="utf-8",
    )
    record_path.chmod(0o600)
    real_close = process.os.close
    closed: list[int] = []
    monkeypatch.setattr(process, "pid_path", lambda: record_path)
    monkeypatch.setattr(
        process.os,
        "fstat",
        lambda _descriptor: (_ for _ in ()).throw(OSError("probe failed")),
    )

    def tracked_close(descriptor):
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(process.os, "close", tracked_close)

    assert process.read_pid_record() is None
    assert len(closed) == 1


def test_systemd_system_unit_is_stable_and_state_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(process.os, "getuid", lambda: 501)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path / "state-a")
    first = process._systemd_system_unit_name()
    assert first == process._systemd_system_unit_name()

    monkeypatch.setattr(process, "state_dir", lambda: tmp_path / "state-b")
    second = process._systemd_system_unit_name()

    assert first != second
    assert first.startswith("hermes-feishu-card-sidecar-501-")
    assert first.endswith(".service")


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
            "-I",
            "-m",
            "hermes_feishu_card.runner",
            "--config",
            str(config_path),
                "--env-file",
                str(env_path),
                "--managed-pidfile",
                "--token",
            commands[0][-1],
        ]
    ]


def test_detached_start_uses_private_state_cwd_not_shadowing_caller(
    monkeypatch, tmp_path
):
    state_root = tmp_path / "private-state"
    shadow_root = tmp_path / "shadow-cwd"
    (shadow_root / "hermes_feishu_card").mkdir(parents=True)
    (shadow_root / "hermes_feishu_card" / "__init__.py").write_text(
        "raise RuntimeError('shadow import used')\n", encoding="utf-8"
    )
    monkeypatch.chdir(shadow_root)
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: state_root)
    monkeypatch.setattr(process, "log_path", lambda: state_root / "sidecar.log")
    monkeypatch.setattr(process, "write_pid_record", lambda *_args: None)
    monkeypatch.setattr(process, "clear_pid", lambda: None)
    monkeypatch.setattr(process, "stop_pid", lambda _pid: None)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 6)).__next__)
    popen_kwargs = {}

    def fake_popen(_command, **kwargs):
        popen_kwargs.update(kwargs)
        return SimpleNamespace(pid=123, poll=lambda: None)

    monkeypatch.setattr(process.subprocess, "Popen", fake_popen)

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "failed: health check timed out"
    assert popen_kwargs["cwd"] == str(state_root.resolve())
    assert popen_kwargs["cwd"] != str(shadow_root)


def test_systemd_working_directory_argument_escapes_specifiers(
    monkeypatch, tmp_path
):
    state_root = tmp_path / "private state % tenant"
    state_root.mkdir()
    monkeypatch.setattr(process, "state_dir", lambda: state_root)

    assert process._systemd_working_directory_arg() == (
        f"--property=WorkingDirectory={str(state_root.resolve()).replace('%', '%%')}"
    )


def test_start_sidecar_auto_never_probes_system_manager(monkeypatch, tmp_path):
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(
        process,
        "_systemd_system_available",
        lambda: (_ for _ in ()).throw(
            AssertionError("auto must never probe the system manager")
        ),
        raising=False,
    )
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process, "log_path", lambda: tmp_path / "sidecar.log")
    monkeypatch.setattr(process, "write_pid_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(process, "clear_pid", lambda: None)
    monkeypatch.setattr(process, "stop_pid", lambda _pid: None)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 6)).__next__)
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=123, poll=lambda: None),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "auto"},
        },
    )

    assert result == "failed: health check timed out"


def test_start_sidecar_creates_new_state_directory_with_private_mode(
    monkeypatch, tmp_path
):
    private_state = tmp_path / "private-state"
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: private_state)
    monkeypatch.setattr(process, "write_pid_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(process, "clear_pid", lambda: None)
    monkeypatch.setattr(process, "stop_pid", lambda _pid: None)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 6)).__next__)
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=123, poll=lambda: None),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "failed: health check timed out"
    assert stat.S_IMODE(private_state.stat().st_mode) == 0o700


def test_start_sidecar_tightens_existing_state_directory_mode(
    monkeypatch, tmp_path
):
    private_state = tmp_path / "existing-state"
    private_state.mkdir(mode=0o755)
    private_state.chmod(0o755)
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: private_state)
    monkeypatch.setattr(process, "write_pid_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(process, "clear_pid", lambda: None)
    monkeypatch.setattr(process, "stop_pid", lambda _pid: None)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 6)).__next__)
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=123, poll=lambda: None),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "failed: health check timed out"
    assert stat.S_IMODE(private_state.stat().st_mode) == 0o700


def test_prepare_state_directory_rejects_parent_traversal_before_mutation(
    monkeypatch,
):
    mutations: list[tuple[str, str]] = []
    monkeypatch.setattr(process, "state_dir", lambda: process.Path("/tmp/.."))
    monkeypatch.setattr(
        process.os,
        "mkdir",
        lambda path, *_args, **_kwargs: mutations.append(("mkdir", str(path))),
    )
    monkeypatch.setattr(
        process.os,
        "chmod",
        lambda path, *_args, **_kwargs: mutations.append(("chmod", str(path))),
    )

    result = process._prepare_private_state_dir()

    assert result == "failed: state directory path must not contain parent traversal"
    assert mutations == []


def test_prepare_state_directory_accepts_ordinary_relative_path(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        process, "state_dir", lambda: process.Path("nested/private-state")
    )

    assert process._prepare_private_state_dir() == ""
    assert (tmp_path / "nested" / "private-state").is_dir()
    assert stat.S_IMODE((tmp_path / "nested" / "private-state").stat().st_mode) == 0o700


def test_state_directory_parent_traversal_is_rejected_without_posix_permissions(
    monkeypatch,
):
    monkeypatch.setattr(process, "state_dir", lambda: process.Path("../state"))
    monkeypatch.setattr(process, "_supports_posix_state_permissions", lambda: False)
    monkeypatch.setattr(
        process.os,
        "getuid",
        lambda: (_ for _ in ()).throw(
            AssertionError("Windows path validation must not require a POSIX uid")
        ),
    )

    assert process._state_dir_security_error(
        allow_missing=True,
        require_private_mode=False,
    ) == "state directory path must not contain parent traversal"


def test_state_directory_resolution_failure_is_fail_closed(monkeypatch, tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to(loop, target_is_directory=True)
    monkeypatch.setattr(process, "state_dir", lambda: loop / "state")

    assert process._state_dir_security_error(
        allow_missing=True,
        require_private_mode=False,
    ) == "state directory could not be inspected"


def test_start_sidecar_refuses_symlink_state_directory_without_chmod_target(
    monkeypatch, tmp_path
):
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    linked_state = tmp_path / "linked-state"
    linked_state.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: linked_state)
    monkeypatch.setattr(
        process,
        "fetch_health",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("invalid state must be refused before health probing")
        ),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "failed: state directory must not be a symbolic link"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_sidecar_command_uses_absolute_config_and_env_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    command = process._sidecar_command(
        "config.yaml",
        env_file="CUSTOM.env",
        token="sidecar-token",
        hermes_dir="verified-hermes",
        managed_pidfile=True,
    )

    assert command[command.index("--config") + 1] == str(tmp_path / "config.yaml")
    assert command[command.index("--env-file") + 1] == str(tmp_path / "CUSTOM.env")
    assert command[command.index("--hermes-dir") + 1] == str(
        tmp_path / "verified-hermes"
    )
    assert "--managed-pidfile" in command
    assert command[:4] == [
        process.sys.executable,
        "-I",
        "-m",
        "hermes_feishu_card.runner",
    ]


def test_start_sidecar_explicit_detached_skips_all_systemd_detection(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(
        process,
        "_systemd_user_available",
        lambda: (_ for _ in ()).throw(AssertionError("detached probed user systemd")),
    )
    monkeypatch.setattr(
        process,
        "_systemd_system_available",
        lambda: (_ for _ in ()).throw(AssertionError("detached probed system systemd")),
        raising=False,
    )
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process, "log_path", lambda: tmp_path / "sidecar.log")
    monkeypatch.setattr(process, "write_pid_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(process, "clear_pid", lambda: None)
    monkeypatch.setattr(process, "stop_pid", lambda _pid: None)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 6)).__next__)
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=123, poll=lambda: None),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "detached"},
        },
    )

    assert result == "failed: health check timed out"


def test_start_sidecar_explicit_systemd_user_unavailable_never_falls_back(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit systemd-user must not fall back")
        ),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-user"},
        },
    )

    assert result.startswith("failed: service.manager=systemd-user is unavailable")


def test_start_sidecar_explicit_systemd_system_unavailable_never_falls_back(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(
        process, "_systemd_system_available", lambda: False, raising=False
    )
    monkeypatch.setattr(
        process,
        "_systemd_user_available",
        lambda: (_ for _ in ()).throw(
            AssertionError("systemd-system must not probe user systemd")
        ),
    )
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit systemd-system must not fall back")
        ),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-system"},
        },
    )

    assert result.startswith("failed: service.manager=systemd-system is unavailable")


def test_systemd_system_availability_uses_only_explicit_noninteractive_probe(
    monkeypatch
):
    commands: list[list[str]] = []
    monkeypatch.setattr(process.sys, "platform", "linux")
    monkeypatch.setattr(process.shutil, "which", lambda _name: "/usr/bin/tool")

    def fake_run(command, **kwargs):
        commands.append(list(command))
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["timeout"] == 3
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process.subprocess, "run", fake_run)

    assert process._systemd_system_available() is True
    assert commands == [
        ["systemctl", "--system", "--no-ask-password", "show-environment"]
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
            f"--setenv=HERMES_FEISHU_CARD_STATE_DIR={tmp_path}",
            f"--property=WorkingDirectory={tmp_path.resolve()}",
            "--property=Type=exec",
            "--property=Restart=on-failure",
            "--property=RestartSec=2s",
            f"--property=StandardOutput=append:{log_file}",
            f"--property=StandardError=append:{log_file}",
            "--",
            process.sys.executable,
            "-I",
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


def test_systemd_start_rejects_boolean_health_pid(monkeypatch, tmp_path):
    token = "fixed-sidecar-token"
    health_responses = iter(
        (
            None,
            {
                "status": "healthy",
                "process_pid": True,
                "process_token_hash": process.process_token_hash(token),
            },
        )
    )
    stopped: list[str] = []
    written: list[bool] = []

    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "_systemd_user_available", lambda: True)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process.secrets, "token_hex", lambda _length: token)
    monkeypatch.setattr(process, "_start_systemd_user_sidecar", lambda _command: True)
    monkeypatch.setattr(
        process,
        "_stop_systemd_user_sidecar",
        lambda unit: stopped.append(unit) or True,
    )
    monkeypatch.setattr(
        process,
        "write_pid_record",
        lambda *_args, **_kwargs: written.append(True),
    )
    monkeypatch.setattr(process, "clear_pid", lambda: None)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0, 6)).__next__)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-user"},
        },
    )

    assert result == "failed: health check timed out"
    assert written == []
    assert stopped == [process.SYSTEMD_UNIT_NAME]


def test_detached_start_timeout_never_signals_unmatched_health_pid(
    monkeypatch, tmp_path
):
    token = "fixed-sidecar-token"
    health_responses = iter(
        (
            None,
            {
                "status": "healthy",
                "process_pid": 999,
                "process_token_hash": process.process_token_hash(token),
            },
        )
    )
    stopped: list[int] = []

    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process.secrets, "token_hex", lambda _length: token)
    monkeypatch.setattr(process, "write_pid_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(process, "clear_pid", lambda: None)
    monkeypatch.setattr(process, "stop_pid", lambda pid: stopped.append(pid))
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0, 6)).__next__)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=123, poll=lambda: None),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "failed: health check timed out"
    assert stopped == []


def test_start_sidecar_uses_explicit_transient_system_unit(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / "CUSTOM.env"
    state_root = tmp_path / "state"
    log_file = state_root / "sidecar.log"
    commands: list[list[str]] = []
    pid_records: list[tuple[int, str, str, str]] = []
    token = "fixed-system-token"
    health_responses = iter(
        (
            None,
            {
                "status": "healthy",
                "process_pid": 5432,
                "process_token_hash": process.process_token_hash(token),
            },
        )
    )

    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "_systemd_system_available", lambda: True)
    monkeypatch.setattr(
        process,
        "_systemd_user_available",
        lambda: (_ for _ in ()).throw(
            AssertionError("systemd-system must not probe user systemd")
        ),
    )
    monkeypatch.setattr(process, "state_dir", lambda: state_root)
    monkeypatch.setattr(process, "log_path", lambda: log_file)
    monkeypatch.setattr(process.secrets, "token_hex", lambda _length: token)
    owner_uid = tmp_path.stat().st_uid
    monkeypatch.setattr(process.os, "getuid", lambda: owner_uid)
    monkeypatch.setattr(process.os, "getgid", lambda: 20)
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
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-system"},
        },
        env_file=env_path,
    )

    unit = process._systemd_system_unit_name()
    assert result == "started"
    assert commands == [
        [
            "systemd-run",
            "--system",
            "--no-ask-password",
            f"--unit={unit}",
            "--collect",
            f"--setenv=HERMES_FEISHU_CARD_STATE_DIR={state_root}",
            f"--property=WorkingDirectory={state_root.resolve()}",
            f"--uid={owner_uid}",
            "--gid=20",
            "--property=Type=exec",
            "--property=Restart=on-failure",
            "--property=RestartSec=2s",
            "--property=UMask=0077",
            "--property=NoNewPrivileges=yes",
            f"--property=StandardOutput=append:{log_file}",
            f"--property=StandardError=append:{log_file}",
            "--",
            process.sys.executable,
            "-I",
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
    flattened = " ".join(commands[0])
    for forbidden in ("sudo", "pkexec", "/etc", "daemon-reload", " enable"):
        assert forbidden not in flattened
    assert pid_records == [(5432, token, "systemd-system", unit)]


def test_stop_systemd_system_uses_noninteractive_system_bus(
    monkeypatch, tmp_path
):
    commands: list[list[str]] = []
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process.os, "getuid", lambda: 501)
    unit = process._systemd_system_unit_name()

    def fake_run(command, **kwargs):
        commands.append(list(command))
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["timeout"] == 10
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process.subprocess, "run", fake_run)

    assert process._stop_systemd_system_sidecar(unit) is True
    assert commands == [
        ["systemctl", "--system", "--no-ask-password", "stop", unit]
    ]


def test_explicit_system_service_start_failure_never_falls_back(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_system_available", lambda: True)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process, "_start_systemd_system_sidecar", lambda *_args: False, raising=False)
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("system manager failure must not fall back")
        ),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-system"},
        },
    )

    assert result.startswith("failed: systemd system service could not be started")


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
    health_responses = iter((old_health, old_health, None, new_health))
    stopped: list[int] = []
    shutdowns: list[str] = []
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
    monkeypatch.setattr(
        process,
        "_request_authenticated_shutdown",
        lambda _config, token: shutdowns.append(token) or True,
    )
    monkeypatch.setattr(process, "pid_is_running", lambda _pid: False)
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process.secrets, "token_hex", lambda _length: new_token)
    monkeypatch.setattr(process, "_start_systemd_user_sidecar", lambda command: launched.append(command) or True)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0, 0, 0)).__next__)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(process, "write_pid_record", lambda *_args, **_kwargs: None)

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "started"
    assert stopped == []
    assert shutdowns == [old_token]
    assert cleared == [True]
    assert launched


def test_start_sidecar_refuses_unverified_manager_migration(monkeypatch, tmp_path):
    old_health = {
        "status": "healthy",
        "process_pid": 1234,
        "process_token_hash": process.process_token_hash("different-token"),
    }
    stopped: list[int] = []
    launched: list[list[str]] = []

    monkeypatch.setattr(process, "fetch_health", lambda _config: old_health)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: True)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {"pid": 1234, "token": "recorded-token", "manager": "detached"},
    )
    monkeypatch.setattr(process, "stop_pid", lambda pid: stopped.append(pid))
    monkeypatch.setattr(
        process,
        "_start_systemd_user_sidecar",
        lambda command: launched.append(command) or True,
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-user"},
        },
    )

    assert result == "failed: running sidecar identity mismatch; migration refused"
    assert stopped == []
    assert launched == []


def test_start_sidecar_keeps_matching_owned_process_with_expected_identity(
    monkeypatch, tmp_path
):
    token = "owned-token"
    health = {
        "status": "healthy",
        "process_pid": 1234,
        "process_token_hash": process.process_token_hash(token),
        "package_version": "4.1.1",
        "python_identity": "python-sha256:matching",
    }
    monkeypatch.setattr(process, "fetch_health", lambda _config: health)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {"pid": 1234, "token": token, "manager": "detached"},
    )
    monkeypatch.setattr(
        process,
        "stop_pid",
        lambda _pid: (_ for _ in ()).throw(
            AssertionError("matching process must not be stopped")
        ),
    )
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("matching process must not be relaunched")
        ),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
        python_executable="/verified/python",
        expected_package_version="4.1.1",
        expected_python_identity="python-sha256:matching",
    )

    assert result == "already running"


@pytest.mark.parametrize(
    "old_identity",
    [
        {},
        {
            "package_version": "4.1.0",
            "python_identity": "python-sha256:different",
        },
    ],
)
def test_start_sidecar_restarts_owned_process_when_expected_identity_is_missing_or_mismatched(
    monkeypatch, tmp_path, old_identity
):
    old_token = "old-token"
    new_token = "new-token"
    old_health = {
        "status": "healthy",
        "process_pid": 1234,
        "process_token_hash": process.process_token_hash(old_token),
        **old_identity,
    }
    new_health = {
        "status": "healthy",
        "process_pid": 4321,
        "process_token_hash": process.process_token_hash(new_token),
        "package_version": "4.1.1",
        "python_identity": "python-sha256:target",
    }
    health_responses = iter((old_health, old_health, None, new_health))
    stopped: list[int] = []
    shutdowns: list[str] = []
    cleared: list[bool] = []
    commands: list[list[str]] = []
    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process, "log_path", lambda: tmp_path / "sidecar.log")
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {"pid": 1234, "token": old_token, "manager": "detached"},
    )
    monkeypatch.setattr(process, "stop_pid", lambda pid: stopped.append(pid))
    monkeypatch.setattr(
        process,
        "_request_authenticated_shutdown",
        lambda _config, token: shutdowns.append(token) or True,
    )
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(process.secrets, "token_hex", lambda _length: new_token)
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda command, **_kwargs: commands.append(command)
        or SimpleNamespace(pid=4321, poll=lambda: None),
    )
    monkeypatch.setattr(process, "write_pid_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0, 0, 0)).__next__)

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
        python_executable="/verified/python",
        expected_package_version="4.1.1",
        expected_python_identity="python-sha256:target",
    )

    assert result == "started"
    assert stopped == []
    assert shutdowns == [old_token]
    assert cleared == [True]
    assert commands[0][0] == "/verified/python"


def test_start_sidecar_refuses_identity_restart_when_health_changes_before_stop(
    monkeypatch, tmp_path
):
    token = "owned-token"
    initial = {
        "status": "healthy",
        "process_pid": 1234,
        "process_token_hash": process.process_token_hash(token),
        "package_version": "4.1.0",
        "python_identity": "python-sha256:old",
    }
    changed = {
        "status": "healthy",
        "process_pid": 4321,
        "process_token_hash": process.process_token_hash("replacement-token"),
        "package_version": "4.1.0",
        "python_identity": "python-sha256:old",
    }
    health_responses = iter((initial, changed))
    stopped: list[int] = []
    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {"pid": 1234, "token": token, "manager": "detached"},
    )
    monkeypatch.setattr(process, "stop_pid", lambda pid: stopped.append(pid))
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("changed process must not be relaunched")
        ),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
        expected_package_version="4.1.1",
        expected_python_identity="python-sha256:new",
    )

    assert result == "failed: running sidecar changed before stop"
    assert stopped == []


def test_stop_sidecar_refuses_when_health_changes_before_signal(
    monkeypatch, tmp_path
):
    token = "owned-token"
    initial = {
        "status": "healthy",
        "process_pid": 1234,
        "process_token_hash": process.process_token_hash(token),
    }
    changed = {
        "status": "healthy",
        "process_pid": 4321,
        "process_token_hash": process.process_token_hash("replacement-token"),
    }
    health_responses = iter((initial, changed))
    stopped: list[int] = []
    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {"pid": 1234, "token": token, "manager": "detached"},
    )
    monkeypatch.setattr(process, "pid_is_running", lambda _pid: True)
    monkeypatch.setattr(process, "stop_pid", lambda pid: stopped.append(pid))

    result = process.stop_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert result == "failed: pidfile identity changed before stop"
    assert stopped == []


def test_start_sidecar_migrates_stale_dead_legacy_pidfile_before_cleanup(
    monkeypatch, tmp_path
):
    record_path = tmp_path / process.PIDFILE_NAME
    record_path.write_text(
        '{"pid":4321,"token":"legacy-token"}\n', encoding="utf-8"
    )
    record_path.chmod(0o644)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process, "log_path", lambda: tmp_path / "sidecar.log")
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "pid_is_running", lambda _pid: False)
    cleared: list[bool] = []
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(
            pid=9999, poll=lambda: 1, returncode=1
        ),
    )
    monkeypatch.setattr(process, "write_pid_record", lambda *_args, **_kwargs: None)

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "failed: process exited with 1"
    assert stat.S_IMODE(record_path.stat().st_mode) == 0o600
    assert cleared == [True, True]


def test_start_sidecar_recovers_explicit_systemd_user_when_health_is_unavailable(
    monkeypatch, tmp_path
):
    old_token = "old-systemd-token"
    new_token = "new-systemd-token"
    health_responses = iter(
        (
            None,
            {
                "status": "healthy",
                "process_pid": 4321,
                "process_token_hash": process.process_token_hash(new_token),
            },
        )
    )
    stopped: list[tuple[str, str]] = []
    launched: list[list[str]] = []
    records: list[tuple[int, str, str, str]] = []
    cleared: list[bool] = []
    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "_systemd_user_available", lambda: True)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": old_token,
            "manager": "systemd-user",
            "unit": process.SYSTEMD_UNIT_NAME,
        },
    )
    monkeypatch.setattr(
        process,
        "_stop_systemd_sidecar",
        lambda manager, unit: stopped.append((manager, unit)) or True,
    )
    monkeypatch.setattr(
        process,
        "_start_systemd_user_sidecar",
        lambda command: launched.append(command) or True,
    )
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(process.secrets, "token_hex", lambda _length: new_token)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0)).__next__)
    monkeypatch.setattr(
        process,
        "write_pid_record",
        lambda pid, token, *, manager, unit: records.append(
            (pid, token, manager, unit)
        ),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-user"},
        },
    )

    assert result == "started"
    assert stopped == [("systemd-user", process.SYSTEMD_UNIT_NAME)]
    assert cleared == [True]
    assert len(launched) == 1
    assert records == [
        (4321, new_token, "systemd-user", process.SYSTEMD_UNIT_NAME)
    ]


def test_start_sidecar_recovers_explicit_systemd_system_when_health_is_unavailable(
    monkeypatch, tmp_path
):
    old_token = "old-system-token"
    new_token = "new-system-token"
    health_responses = iter(
        (
            None,
            {
                "status": "healthy",
                "process_pid": 5432,
                "process_token_hash": process.process_token_hash(new_token),
            },
        )
    )
    stopped: list[tuple[str, str]] = []
    launched: list[tuple[list[str], str]] = []
    owner_uid = tmp_path.stat().st_uid
    monkeypatch.setattr(process.os, "getuid", lambda: owner_uid)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    unit = process._systemd_system_unit_name()
    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "_systemd_system_available", lambda: True)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": old_token,
            "manager": "systemd-system",
            "unit": unit,
        },
    )
    monkeypatch.setattr(
        process,
        "_stop_systemd_sidecar",
        lambda manager, selected_unit: stopped.append((manager, selected_unit))
        or True,
    )
    monkeypatch.setattr(
        process,
        "_start_systemd_system_sidecar",
        lambda command, selected_unit: launched.append((command, selected_unit)) or True,
    )
    monkeypatch.setattr(process, "clear_pid", lambda: None)
    monkeypatch.setattr(process.secrets, "token_hex", lambda _length: new_token)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0)).__next__)
    records: list[tuple[int, str, str, str]] = []
    monkeypatch.setattr(
        process,
        "write_pid_record",
        lambda pid, token, *, manager, unit: records.append(
            (pid, token, manager, unit)
        ),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-system"},
        },
    )

    assert result == "started"
    assert stopped == [("systemd-system", unit)]
    assert launched and launched[0][1] == unit
    assert records == [(5432, new_token, "systemd-system", unit)]


def test_start_sidecar_auto_does_not_recover_systemd_without_health(
    monkeypatch, tmp_path
):
    stopped: list[tuple[str, str]] = []
    launched: list[list[str]] = []
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: True)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": "owned-token",
            "manager": "systemd-user",
            "unit": process.SYSTEMD_UNIT_NAME,
        },
    )
    monkeypatch.setattr(
        process,
        "_stop_systemd_sidecar",
        lambda manager, unit: stopped.append((manager, unit)) or True,
    )
    monkeypatch.setattr(
        process,
        "_start_systemd_user_sidecar",
        lambda command: launched.append(command) or True,
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "auto"},
        },
    )

    assert result == "failed: owned sidecar health is unavailable; start refused"
    assert stopped == []
    assert launched == []


def test_start_sidecar_refuses_forged_systemd_record_without_health(
    monkeypatch, tmp_path
):
    stopped: list[tuple[str, str]] = []
    launched: list[list[str]] = []
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: True)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": "owned-token",
            "manager": "systemd-user",
            "unit": "attacker.service",
        },
    )
    monkeypatch.setattr(
        process,
        "_stop_systemd_sidecar",
        lambda manager, unit: stopped.append((manager, unit)) or True,
    )
    monkeypatch.setattr(
        process,
        "_start_systemd_user_sidecar",
        lambda command: launched.append(command) or True,
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-user"},
        },
    )

    assert result == "failed: invalid pidfile exists; start refused"
    assert stopped == []
    assert launched == []


def test_start_sidecar_refuses_live_pid_record_when_health_is_unavailable(
    monkeypatch, tmp_path
):
    launched = []
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {"pid": 1234, "token": "owned", "manager": "detached"},
    )
    monkeypatch.setattr(process, "pid_is_running", lambda _pid: True)
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: launched.append(True),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "failed: owned sidecar health is unavailable; start refused"
    assert launched == []


def test_start_sidecar_refuses_invalid_existing_pidfile_without_overwriting(
    monkeypatch, tmp_path
):
    pid_file = tmp_path / process.PIDFILE_NAME
    pid_file.write_text('{"manager":"forged"}\n', encoding="utf-8")
    launched = []
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: launched.append(True),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "failed: invalid pidfile exists; start refused"
    assert launched == []
    assert pid_file.read_text(encoding="utf-8") == '{"manager":"forged"}\n'


def test_stop_sidecar_refuses_invalid_existing_pidfile(monkeypatch, tmp_path):
    pid_file = tmp_path / process.PIDFILE_NAME
    pid_file.write_text('{"manager":"forged"}\n', encoding="utf-8")
    pid_file.chmod(0o600)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "fetch_health",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("invalid pidfile must fail before health probing")
        ),
    )

    result = process.stop_sidecar(
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-user"},
        }
    )

    assert result == "failed: invalid pidfile exists; stop refused"


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


def test_stop_sidecar_uses_explicit_system_manager_unit(monkeypatch, tmp_path):
    token = "fixed-system-token"
    stopped: list[str] = []
    cleared: list[bool] = []
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    owner_uid = tmp_path.stat().st_uid
    monkeypatch.setattr(process.os, "getuid", lambda: owner_uid)
    unit = process._systemd_system_unit_name()
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": token,
            "manager": "systemd-system",
            "unit": unit,
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
        process,
        "_stop_systemd_system_sidecar",
        lambda selected: stopped.append(selected) or True,
        raising=False,
    )
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(
        process,
        "stop_pid",
        lambda _pid: (_ for _ in ()).throw(
            AssertionError("system service must be stopped through systemctl")
        ),
    )

    result = process.stop_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert result == "stopped"
    assert stopped == [unit]
    assert cleared == [True]


@pytest.mark.parametrize("manager", ("systemd-user", "systemd-system"))
def test_stop_sidecar_recovers_explicit_systemd_unit_without_health(
    monkeypatch, tmp_path, manager
):
    stopped: list[tuple[str, str]] = []
    cleared: list[bool] = []
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    owner_uid = tmp_path.stat().st_uid
    monkeypatch.setattr(process.os, "getuid", lambda: owner_uid)
    unit = process._expected_unit(manager)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": "owned-token",
            "manager": manager,
            "unit": unit,
        },
    )
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(
        process,
        "_stop_systemd_sidecar",
        lambda selected_manager, selected_unit: stopped.append(
            (selected_manager, selected_unit)
        )
        or True,
    )
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))

    result = process.stop_sidecar(
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": manager},
        }
    )

    assert result == "stopped"
    assert stopped == [(manager, unit)]
    assert cleared == [True]


def test_stop_sidecar_refuses_systemd_recovery_for_auto_manager(
    monkeypatch, tmp_path
):
    stopped: list[tuple[str, str]] = []
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": "owned-token",
            "manager": "systemd-user",
            "unit": process.SYSTEMD_UNIT_NAME,
        },
    )
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(
        process,
        "_stop_systemd_sidecar",
        lambda manager, unit: stopped.append((manager, unit)) or True,
    )

    result = process.stop_sidecar(
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "auto"},
        }
    )

    assert result == "failed: pidfile identity mismatch"
    assert stopped == []


def test_stop_sidecar_refuses_systemd_recovery_for_mismatched_explicit_manager(
    monkeypatch, tmp_path
):
    stopped: list[tuple[str, str]] = []
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": "owned-token",
            "manager": "systemd-user",
            "unit": process.SYSTEMD_UNIT_NAME,
        },
    )
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(
        process,
        "_stop_systemd_sidecar",
        lambda manager, unit: stopped.append((manager, unit)) or True,
    )

    result = process.stop_sidecar(
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-system"},
        }
    )

    assert result == "failed: pidfile identity mismatch"
    assert stopped == []


def test_stop_sidecar_refuses_systemd_recovery_when_health_identity_mismatches(
    monkeypatch, tmp_path
):
    stopped: list[tuple[str, str]] = []
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": "owned-token",
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
            "process_token_hash": process.process_token_hash("different-token"),
        },
    )
    monkeypatch.setattr(
        process,
        "_stop_systemd_sidecar",
        lambda manager, unit: stopped.append((manager, unit)) or True,
    )

    result = process.stop_sidecar(
        {
            "server": {"host": "127.0.0.1", "port": 8765},
            "service": {"manager": "systemd-user"},
        }
    )

    assert result == "failed: pidfile identity mismatch"
    assert stopped == []


def test_stop_sidecar_refuses_forged_system_unit_before_systemctl(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    owner_uid = tmp_path.stat().st_uid
    monkeypatch.setattr(process.os, "getuid", lambda: owner_uid)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": "fixed-system-token",
            "manager": "systemd-system",
            "unit": "attacker.service",
        },
    )
    monkeypatch.setattr(
        process,
        "fetch_health",
        lambda _config: {
            "status": "healthy",
            "process_pid": 1234,
            "process_token_hash": process.process_token_hash("fixed-system-token"),
        },
    )
    monkeypatch.setattr(
        process,
        "_stop_systemd_system_sidecar",
        lambda _unit: (_ for _ in ()).throw(
            AssertionError("forged unit reached systemctl")
        ),
        raising=False,
    )

    result = process.stop_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert result == "failed: pidfile manager identity mismatch"


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


def test_status_sidecar_rejects_boolean_systemd_process_pid(monkeypatch):
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
            "process_pid": True,
            "process_token_hash": process.process_token_hash(token),
        },
    )
    probed: list[int] = []
    monkeypatch.setattr(process, "pid_is_running", lambda pid: probed.append(pid) or True)

    status = process.status_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert status["pid"] == 1234
    assert probed == [1234]


def test_status_sidecar_reports_verified_manager(monkeypatch, tmp_path):
    token = "fixed-system-token"
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    owner_uid = tmp_path.stat().st_uid
    monkeypatch.setattr(process.os, "getuid", lambda: owner_uid)
    unit = process._systemd_system_unit_name()
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 1234,
            "token": token,
            "manager": "systemd-system",
            "unit": unit,
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
    monkeypatch.setattr(process, "pid_is_running", lambda _pid: True)

    status = process.status_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert status["pid"] == 4321
    assert status["manager"] == "systemd-system"
    assert status["unit"] == unit


def test_stop_sidecar_refuses_symlinked_state_parent_before_pid_or_health(
    monkeypatch, tmp_path
):
    target_parent = tmp_path / "target-parent"
    private_state = target_parent / "state"
    private_state.mkdir(parents=True, mode=0o700)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(target_parent, target_is_directory=True)
    monkeypatch.setattr(process, "state_dir", lambda: linked_parent / "state")
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: (_ for _ in ()).throw(
            AssertionError("untrusted state reached pidfile parsing")
        ),
    )
    monkeypatch.setattr(
        process,
        "fetch_health",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("untrusted state reached health ownership checks")
        ),
    )
    monkeypatch.setattr(
        process,
        "stop_pid",
        lambda _pid: (_ for _ in ()).throw(
            AssertionError("untrusted state reached process termination")
        ),
    )

    result = process.stop_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert result == "failed: state directory path must not contain symbolic links"


def test_status_sidecar_refuses_symlinked_state_parent_before_pid_or_health(
    monkeypatch, tmp_path
):
    target_parent = tmp_path / "target-parent"
    private_state = target_parent / "state"
    private_state.mkdir(parents=True, mode=0o700)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(target_parent, target_is_directory=True)
    monkeypatch.setattr(process, "state_dir", lambda: linked_parent / "state")
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: (_ for _ in ()).throw(
            AssertionError("untrusted state reached pidfile parsing")
        ),
    )
    monkeypatch.setattr(
        process,
        "fetch_health",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("untrusted state reached health ownership checks")
        ),
    )

    status = process.status_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert status == {
        "running": False,
        "pid": None,
        "health": None,
        "pid_running": False,
        "manager": "invalid",
        "unit": "",
        "error": "state directory path must not contain symbolic links",
    }


@pytest.mark.parametrize(
    ("mode", "uid_offset", "expected"),
    [
        (0o755, 0, "state directory permissions must be private"),
        (0o700, 1, "state directory is not owned by the current user"),
    ],
)
def test_status_sidecar_refuses_non_private_or_foreign_state_directory(
    monkeypatch, tmp_path, mode, uid_offset, expected
):
    private_state = tmp_path / "state"
    private_state.mkdir(mode=mode)
    private_state.chmod(mode)
    real_uid = private_state.stat().st_uid
    monkeypatch.setattr(process, "state_dir", lambda: private_state)
    monkeypatch.setattr(process.os, "getuid", lambda: real_uid + uid_offset)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: (_ for _ in ()).throw(
            AssertionError("untrusted state reached pidfile parsing")
        ),
    )

    status = process.status_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert status["error"] == expected


def test_status_sidecar_refuses_filesystem_root_as_state_directory(monkeypatch):
    monkeypatch.setattr(process, "state_dir", lambda: process.Path("/"))
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: (_ for _ in ()).throw(
            AssertionError("filesystem root reached pidfile parsing")
        ),
    )

    status = process.status_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert status["error"] == "state directory must not be filesystem root"


def test_status_sidecar_does_not_apply_posix_mode_bits_on_windows(
    monkeypatch, tmp_path
):
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    state.chmod(0o755)
    monkeypatch.setattr(process, "state_dir", lambda: state)
    monkeypatch.setattr(process, "_supports_posix_state_permissions", lambda: False)
    monkeypatch.setattr(process, "read_pid_record", lambda: None)
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)

    status = process.status_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert status["running"] is False
    assert "error" not in status


def test_pid_record_write_does_not_require_posix_fchmod_on_windows(
    monkeypatch, tmp_path
):
    record_path = tmp_path / "sidecar.pid"
    monkeypatch.setattr(process, "pid_path", lambda: record_path)
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.delattr(process.os, "fchmod", raising=False)

    process.write_pid_record(4321, "sidecar-token", manager="detached")

    assert process.read_pid_record() == {
        "pid": 4321,
        "token": "sidecar-token",
        "manager": "detached",
    }


def test_status_rejects_systemd_pid_record_on_windows_without_getuid(
    monkeypatch, tmp_path
):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(process, "state_dir", lambda: state)
    monkeypatch.setattr(process, "_supports_posix_state_permissions", lambda: False)
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.delattr(process.os, "getuid", raising=False)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {
            "pid": 4321,
            "token": "sidecar-token",
            "manager": "systemd-system",
            "unit": "forged.service",
        },
    )
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)

    status = process.status_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert status["running"] is False
    assert status["manager"] == "unknown"


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


def test_fetch_health_for_specific_non_loopback_listener_uses_local_control_host(
    monkeypatch,
):
    calls: list[tuple[str, float]] = []

    class _NoProxyOpener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            return _HealthResponse()

    monkeypatch.setattr(process, "_NO_PROXY_OPENER", _NoProxyOpener())

    assert process.fetch_health(
        {"server": {"host": "192.0.2.10", "port": 8765}}
    ) == {"status": "healthy"}
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


def test_authenticated_shutdown_posts_token_only_in_header_over_loopback(monkeypatch):
    requests = []

    class _ShutdownResponse:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"ok":true,"status":"stopping"}'

    class _NoProxyOpener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            return _ShutdownResponse()

    monkeypatch.setattr(process, "_NO_PROXY_OPENER", _NoProxyOpener())
    token = "private-process-token"

    accepted = process._request_authenticated_shutdown(
        {"server": {"host": "127.0.0.1", "port": 8765}},
        token,
    )

    assert accepted is True
    request, timeout = requests[0]
    assert request.full_url == "http://127.0.0.1:8765/control/shutdown"
    assert request.method == "POST"
    assert request.get_header("X-hfc-process-token") == token
    assert token not in request.full_url
    assert token not in (request.data or b"").decode("utf-8")
    assert timeout == 0.8


def test_authenticated_shutdown_for_specific_ipv6_listener_uses_ipv6_loopback(
    monkeypatch,
):
    urls: list[str] = []

    class _ShutdownResponse:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"ok":true,"status":"stopping"}'

    class _NoProxyOpener:
        def open(self, request, timeout):
            urls.append(request.full_url)
            return _ShutdownResponse()

    monkeypatch.setattr(process, "_NO_PROXY_OPENER", _NoProxyOpener())

    assert process._request_authenticated_shutdown(
        {"server": {"host": "2001:db8::10", "port": 8765}}, "owned-token"
    ) is True
    assert urls == ["http://[::1]:8765/control/shutdown"]


def test_managed_pidfile_handshake_requires_exact_detached_pid_and_token(
    monkeypatch,
):
    responses = iter(
        (
            None,
            {"pid": 4444, "token": "wrong", "manager": "detached"},
            {"pid": 4444, "token": "owned", "manager": "detached"},
        )
    )
    monkeypatch.setattr(process, "read_pid_record", lambda: next(responses))
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0, 0, 0)).__next__)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)

    assert process.wait_for_managed_pidfile(4444, "owned", timeout=5) is True


def test_managed_pidfile_handshake_rebinds_windows_launcher_parent(monkeypatch):
    record = {"pid": 4444, "token": "owned", "manager": "detached"}
    writes = []

    def write_record(pid, token, **kwargs):
        nonlocal record
        writes.append((pid, token, kwargs))
        record = {"pid": pid, "token": token, "manager": kwargs["manager"]}

    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process.os, "getppid", lambda: 4444)
    monkeypatch.setattr(process, "read_pid_record", lambda: record)
    monkeypatch.setattr(process, "write_pid_record", write_record)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0)).__next__)

    assert process.wait_for_managed_pidfile(5555, "owned", timeout=5) is True
    assert writes == [(5555, "owned", {"manager": "detached"})]


@pytest.mark.parametrize(
    ("platform", "record", "parent_pid"),
    (
        ("linux", {"pid": 4444, "token": "owned", "manager": "detached"}, 4444),
        ("win32", {"pid": 4444, "token": "wrong", "manager": "detached"}, 4444),
        (
            "win32",
            {
                "pid": 4444,
                "token": "owned",
                "manager": "systemd-user",
                "unit": process.SYSTEMD_UNIT_NAME,
            },
            4444,
        ),
        ("win32", {"pid": 4444, "token": "owned", "manager": "detached"}, 3333),
    ),
)
def test_managed_pidfile_handshake_rejects_untrusted_rebind(
    monkeypatch, platform, record, parent_pid
):
    monkeypatch.setattr(process.sys, "platform", platform)
    monkeypatch.setattr(process.os, "getppid", lambda: parent_pid)
    monkeypatch.setattr(process, "read_pid_record", lambda: record)
    monkeypatch.setattr(
        process,
        "write_pid_record",
        lambda *_args, **_kwargs: pytest.fail("untrusted identity must not be rebound"),
    )
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0, 6)).__next__)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)

    assert process.wait_for_managed_pidfile(5555, "owned", timeout=5) is False


def test_managed_pidfile_handshake_rejects_unverified_rebind_write(monkeypatch):
    record = {"pid": 4444, "token": "owned", "manager": "detached"}

    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process.os, "getppid", lambda: 4444)
    monkeypatch.setattr(process, "read_pid_record", lambda: record)
    monkeypatch.setattr(process, "write_pid_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0, 6)).__next__)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)

    assert process.wait_for_managed_pidfile(5555, "owned", timeout=5) is False


def test_detached_pidfile_write_failure_waits_for_managed_child_self_exit(
    monkeypatch, tmp_path
):
    poll_results = iter((None, 1))
    child = SimpleNamespace(pid=4444, poll=lambda: next(poll_results), returncode=1)
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process, "log_path", lambda: tmp_path / "sidecar.log")
    monkeypatch.setattr(process.subprocess, "Popen", lambda *_args, **_kwargs: child)
    monkeypatch.setattr(
        process,
        "write_pid_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0, 0)).__next__)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        process.os,
        "kill",
        lambda *_args: pytest.fail("pidfile failure must not signal child PID"),
    )

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "failed: pidfile could not be written: OSError"


def test_detached_health_timeout_keeps_valid_pidfile_without_unmanaged_orphan(
    monkeypatch, tmp_path
):
    written = []
    cleared = []
    monkeypatch.setattr(process, "fetch_health", lambda _config: None)
    monkeypatch.setattr(process, "_systemd_user_available", lambda: False)
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(process, "log_path", lambda: tmp_path / "sidecar.log")
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=4444, poll=lambda: None),
    )
    monkeypatch.setattr(
        process,
        "write_pid_record",
        lambda pid, token, **kwargs: written.append((pid, token, kwargs)),
    )
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(process.time, "monotonic", iter((0, 6)).__next__)

    result = process.start_sidecar(
        tmp_path / "config.yaml",
        {"server": {"host": "127.0.0.1", "port": 8765}},
    )

    assert result == "failed: health check timed out"
    assert len(written) == 1
    assert written[0][0] == 4444
    assert written[0][2] == {}
    assert cleared == []


def test_stop_sidecar_uses_authenticated_self_shutdown_without_pid_signal(
    monkeypatch, tmp_path
):
    token = "owned-token"
    health = {
        "status": "healthy",
        "process_pid": 1234,
        "process_token_hash": process.process_token_hash(token),
    }
    health_responses = iter((health, health, None))
    shutdowns: list[tuple[dict, str]] = []
    cleared: list[bool] = []
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {"pid": 1234, "token": token, "manager": "detached"},
    )
    monkeypatch.setattr(process, "fetch_health", lambda config: next(health_responses))
    monkeypatch.setattr(
        process,
        "_request_authenticated_shutdown",
        lambda config, supplied: shutdowns.append((config, supplied)) or True,
    )
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(
        process.os,
        "killpg",
        lambda *_args: pytest.fail("detached shutdown must not signal a PGID"),
    )
    monkeypatch.setattr(
        process.os,
        "kill",
        lambda *_args: pytest.fail("detached shutdown must not signal a PID"),
    )
    config = {"server": {"host": "127.0.0.1", "port": 8765}}

    result = process.stop_sidecar(config)

    assert result == "stopped"
    assert shutdowns == [(config, token)]
    assert cleared == [True]


def test_stop_sidecar_pid_reuse_race_times_out_without_killing_replacement(
    monkeypatch, tmp_path
):
    token = "owned-token"
    initial = {
        "status": "healthy",
        "process_pid": 1234,
        "process_token_hash": process.process_token_hash(token),
    }
    replacement = {
        "status": "healthy",
        "process_pid": 1234,
        "process_token_hash": process.process_token_hash("replacement-token"),
    }
    health_responses = iter((initial, initial, replacement))
    cleared: list[bool] = []
    monkeypatch.setattr(process, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        process,
        "read_pid_record",
        lambda: {"pid": 1234, "token": token, "manager": "detached"},
    )
    monkeypatch.setattr(process, "fetch_health", lambda _config: next(health_responses))
    monkeypatch.setattr(process, "_request_authenticated_shutdown", lambda *_args: True)
    monkeypatch.setattr(process, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(process.time, "monotonic", iter((0, 0, 6)).__next__)
    monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        process.os,
        "killpg",
        lambda *_args: pytest.fail("replacement PGID must never be signalled"),
    )
    monkeypatch.setattr(
        process.os,
        "kill",
        lambda *_args: pytest.fail("replacement PID must never be signalled"),
    )

    result = process.stop_sidecar(
        {"server": {"host": "127.0.0.1", "port": 8765}}
    )

    assert result == "failed: authenticated sidecar shutdown timed out"
    assert cleared == []


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


def test_numeric_pid_termination_is_disabled_on_all_platforms(monkeypatch):
    monkeypatch.setattr(
        process.os,
        "killpg",
        lambda *_args, **_kwargs: pytest.fail("numeric PGID termination is forbidden"),
    )
    monkeypatch.setattr(
        process.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("taskkill is forbidden"),
    )

    with pytest.raises(RuntimeError, match="disabled"):
        process.stop_pid(5678)
