from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_feishu_card.maintenance_store import (
    ArtifactMetadata,
    MaintenanceRefused,
)
from hermes_feishu_card.maintenance_update import (
    CommandResult,
    _verified_import,
    gateway_drain_required,
    inspect_update,
    resolve_hermes_command_binding,
    run_hermes_command,
    set_gateway_external_drain,
)


class CommandHarness:
    def __init__(self, hermes_root: Path):
        self.hermes_root = hermes_root
        self.commands = []
        self.git_head = "a" * 40 + "\n"
        self.git_status = (
            " M gateway/run.py\n"
            " M gateway/platforms/base.py\n"
            " M cron/scheduler.py\n"
        )
        self.update_result = CommandResult(
            argv=("hermes", "update", "--check"),
            returncode=0,
            stdout="3 updates available; target upstream/f3cda0ce\n",
            stderr="",
        )
        self.target_head = "e" * 40 + "\n"

    def __call__(self, argv, timeout, *, cwd=None, env=None):
        normalized = tuple(str(value) for value in argv)
        self.commands.append(normalized)
        if normalized[-2:] == ("rev-parse", "HEAD"):
            return CommandResult(normalized, 0, self.git_head, "")
        if normalized[-3:] == ("rev-parse", "--verify", "origin/main"):
            return CommandResult(normalized, 0, self.target_head, "")
        if normalized[-4:] == ("fetch", "--quiet", "origin", "main"):
            return CommandResult(normalized, 0, "", "")
        if "merge-base" in normalized:
            return CommandResult(normalized, 0, "", "")
        if "log" in normalized and "--format=%h %s" in normalized:
            return CommandResult(normalized, 0, "e123456 target commit\n", "")
        if "status" in normalized:
            return CommandResult(normalized, 0, self.git_status, "")
        if normalized[-2:] == ("update", "--check"):
            return replace(self.update_result, argv=normalized)
        raise AssertionError(f"unexpected command: {normalized}")


@pytest.fixture
def clean_hermes(tmp_path):
    root = tmp_path / "hermes"
    (root / ".git").mkdir(parents=True)
    (root / "gateway" / "platforms").mkdir(parents=True)
    (root / "cron").mkdir()
    (root / "gateway" / "run.py").write_text("patched\n", encoding="utf-8")
    (root / "gateway" / "platforms" / "base.py").write_text(
        "patched\n", encoding="utf-8"
    )
    (root / "cron" / "scheduler.py").write_text("patched\n", encoding="utf-8")
    runtime = root / "venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    runtime.parent.mkdir(parents=True)
    runtime.write_text("#!python\n", encoding="utf-8")
    runtime.chmod(0o700)
    return root


@pytest.fixture
def artifact(tmp_path):
    wheel = tmp_path / "hfc.whl"
    wheel.write_bytes(b"wheel")
    return ArtifactMetadata(
        schema_version=1,
        distribution="hermes-feishu-streaming-card",
        version="4.2.0",
        sha256="b" * 64,
        wheel_path=wheel,
        metadata_path=tmp_path / "artifact.json",
        source_kind="installer_spec",
        created_at=100.0,
    )


@pytest.fixture(autouse=True)
def healthy_detection(monkeypatch, clean_hermes):
    detection = SimpleNamespace(
        root=clean_hermes,
        version="0.19.1",
        supported=True,
        compatibility="full",
        run_py=clean_hermes / "gateway" / "run.py",
        cron_py=clean_hermes / "cron" / "scheduler.py",
        cron_py_exists=True,
        base_py=clean_hermes / "gateway" / "platforms" / "base.py",
        base_py_exists=True,
        base_required=True,
    )
    recovery = SimpleNamespace(
        state="installed",
        actions=(),
        executable=False,
        fingerprint="recovery-fingerprint",
        findings=(),
    )
    monkeypatch.setattr(
        "hermes_feishu_card.maintenance_update.detect_hermes",
        lambda root: detection,
    )
    monkeypatch.setattr(
        "hermes_feishu_card.maintenance_update.plan_recovery",
        lambda current: recovery,
    )


def _inspect(clean_hermes, artifact, runner, *, active_sessions=0):
    return inspect_update(
        hermes_root=clean_hermes,
        artifact=artifact,
        installed_hfc_version="4.2.0",
        active_sessions=active_sessions,
        run=runner,
        now=lambda: 200.0,
    )


@pytest.mark.parametrize(
    ("phase", "required"),
    [
        ("locking", True),
        ("draining", True),
        ("restoring_hooks", True),
        ("updating_hermes", False),
        ("reinstalling_hfc", False),
        ("starting_services", False),
        ("verifying", False),
        ("succeeded", False),
        ("failed", False),
        ("cancelled", False),
    ],
)
def test_gateway_drain_requirement_is_phase_complete(phase, required):
    assert gateway_drain_required(phase) is required


def test_gateway_drain_requirement_rejects_unknown_phase():
    with pytest.raises(MaintenanceRefused, match="phase is invalid"):
        gateway_drain_required("unknown")


def test_hermes_command_binding_ignores_path_decoy(tmp_path):
    root = tmp_path / "custom" / "hermes-agent"
    runtime = root / "venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    runtime.parent.mkdir(parents=True)
    runtime.write_text("#!python\n", encoding="utf-8")

    binding = resolve_hermes_command_binding(root)

    assert binding.runtime_python == runtime.resolve(strict=False)
    assert binding.argv_prefix == (
        str(runtime.resolve(strict=False)),
        "-I",
        "-m",
        "hermes_cli.main",
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv symlink layout")
def test_hermes_command_binding_accepts_standard_venv_python_symlink(tmp_path):
    root = tmp_path / "custom" / "hermes-agent"
    runtime = root / "venv" / "bin" / "python"
    runtime.parent.mkdir(parents=True)
    backing_python = tmp_path / "python3.12"
    backing_python.write_text("#!python\n", encoding="utf-8")
    backing_python.chmod(0o700)
    runtime.symlink_to(backing_python)
    entrypoint = runtime.with_name("hermes")
    entrypoint.write_text("#!python\n", encoding="utf-8")
    entrypoint.chmod(0o700)

    binding = resolve_hermes_command_binding(root)

    assert binding.runtime_python == runtime
    assert binding.argv_prefix == (str(entrypoint),)


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv symlink layout")
def test_runtime_import_verification_uses_venv_path_for_symlink(tmp_path):
    runtime = tmp_path / "venv" / "bin" / "python"
    runtime.parent.mkdir(parents=True)
    backing_python = tmp_path / "runtime" / "bin" / "python3.12"
    backing_python.parent.mkdir(parents=True)
    backing_python.write_text("#!python\n", encoding="utf-8")
    runtime.symlink_to(backing_python)
    location = (
        tmp_path
        / "venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "hermes_feishu_card"
        / "__init__.py"
    )
    location.parent.mkdir(parents=True)
    location.write_text("", encoding="utf-8")
    result = CommandResult(
        argv=(str(runtime),),
        returncode=0,
        stdout='{"version":"4.2.5","location":"' + str(location) + '"}',
        stderr="",
    )

    assert _verified_import(result, runtime, expected_version="4.2.5") == (
        True,
        "site-packages",
    )


def test_bound_hermes_command_sets_exact_root_home_and_cwd(tmp_path):
    root = tmp_path / "custom" / "hermes-agent"
    runtime = root / "venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    runtime.parent.mkdir(parents=True)
    runtime.write_text("#!python\n", encoding="utf-8")
    runtime.chmod(0o700)
    captured = {}

    def runner(argv, timeout, *, cwd=None, env=None):
        captured.update(
            argv=tuple(argv),
            timeout=timeout,
            cwd=cwd,
            env=dict(env or {}),
        )
        return CommandResult(tuple(argv), 0, "", "")

    binding = resolve_hermes_command_binding(root)
    result = run_hermes_command(
        binding,
        ("gateway", "stop", "--all"),
        120.0,
        run=runner,
        base_environment={
            "PATH": "/decoy/bin",
            "FEISHU_APP_SECRET": "must-not-leak",
        },
        proxy_environment={"HTTPS_PROXY": "http://127.0.0.1:7897"},
    )

    assert result.returncode == 0
    assert captured["argv"][:4] == (
        str(runtime.resolve(strict=False)),
        "-I",
        "-m",
        "hermes_cli.main",
    )
    assert captured["argv"][4:] == ("gateway", "stop", "--all")
    assert captured["cwd"] == root.resolve(strict=False)
    assert captured["env"]["HERMES_DIR"] == str(root.resolve(strict=False))
    assert captured["env"]["HERMES_HOME"] == str(root.parent.resolve(strict=False))
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert "FEISHU_APP_SECRET" not in captured["env"]


def test_binding_bound_drain_uses_runtime_python_and_hides_home_from_argv(tmp_path):
    root = tmp_path / "custom" / "hermes-agent"
    runtime = root / "venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    runtime.parent.mkdir(parents=True)
    runtime.write_text("#!python\n", encoding="utf-8")
    runtime.chmod(0o700)
    captured = {}

    def runner(argv, timeout, *, cwd=None, env=None):
        captured.update(argv=tuple(argv), cwd=cwd, env=dict(env or {}))
        return CommandResult(tuple(argv), 0, "", "")

    proxy_value = "http://127.0.0.1:7897"
    assert set_gateway_external_drain(
        root,
        active=True,
        run=runner,
        proxy_environment={"HTTPS_PROXY": proxy_value},
    )

    assert captured["argv"][:3] == (
        str(runtime.resolve(strict=False)),
        "-I",
        "-c",
    )
    assert "hermes_cli.main" not in captured["argv"]
    assert all(
        str(root.resolve(strict=False)) not in value
        for value in captured["argv"][1:]
    )
    assert all(
        str(root.parent.resolve(strict=False)) not in value
        for value in captured["argv"][1:]
    )
    assert all(proxy_value not in value for value in captured["argv"])
    assert captured["cwd"] == root.resolve(strict=False)
    assert captured["env"]["HERMES_HOME"] == str(root.parent.resolve(strict=False))


def test_gateway_external_drain_uses_hermes_runtime_and_home(tmp_path):
    root = tmp_path / "home" / "hermes-agent"
    python = root / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!python\n", encoding="utf-8")
    commands = []

    def runner(argv, timeout, *, cwd=None, env=None):
        commands.append((tuple(argv), timeout))
        return CommandResult(tuple(argv), 0, "", "")

    assert set_gateway_external_drain(root, active=True, run=runner) is True
    assert set_gateway_external_drain(root, active=False, run=runner) is True
    assert all(command[0][0] == str(python) for command in commands)
    assert "write_drain_request" in commands[0][0][3]
    assert "clear_drain_request" in commands[1][0][3]


def test_inspect_update_runs_only_read_only_commands(
    clean_hermes, artifact
):
    runner = CommandHarness(clean_hermes)

    inspection = _inspect(clean_hermes, artifact, runner)

    assert inspection.ready is True
    assert runner.commands == [
        ("git", "-C", str(clean_hermes), "rev-parse", "HEAD"),
        (
            "git",
            "-C",
            str(clean_hermes),
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
        ),
        (
            str(
                clean_hermes
                / "venv"
                / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            ),
            "-I",
            "-m",
            "hermes_cli.main",
            "update",
            "--check",
        ),
        ("git", "-C", str(clean_hermes), "fetch", "--quiet", "origin", "main"),
        ("git", "-C", str(clean_hermes), "rev-parse", "--verify", "origin/main"),
        (
            "git",
            "-C",
            str(clean_hermes),
            "merge-base",
            "--is-ancestor",
            "a" * 40,
            "e" * 40,
        ),
        (
            "git",
            "-C",
            str(clean_hermes),
            "log",
            "-1",
            "--format=%h %s",
            "e" * 40,
        ),
    ]
    assert inspection.current_version == "0.19.1"
    assert inspection.current_head == "a" * 40
    assert inspection.target_summary == "origin/main e123456 target commit"
    assert inspection.target_head == "e" * 40
    assert inspection.hfc_version == "4.2.0"
    assert inspection.hook_state == "installed"
    assert inspection.maintenance_ready is True
    assert inspection.created_at == 200.0
    assert len(inspection.fingerprint) == 64


def test_inspect_update_refuses_unrelated_tracked_change(
    clean_hermes, artifact
):
    runner = CommandHarness(clean_hermes)
    runner.git_status += " M gateway/unrelated.py\n"

    inspection = _inspect(clean_hermes, artifact, runner)

    assert inspection.ready is False
    assert inspection.reason_code == "unrelated_tracked_changes"
    assert inspection.changed_paths == ("gateway/unrelated.py",)
    assert not any(command[-2:] == ("update", "--check") for command in runner.commands)


def test_inspect_update_allows_untracked_files(clean_hermes, artifact):
    (clean_hermes / "notes.local.md").write_text("keep", encoding="utf-8")
    runner = CommandHarness(clean_hermes)

    inspection = _inspect(clean_hermes, artifact, runner)

    assert inspection.ready is True
    assert "notes.local.md" not in inspection.changed_paths


def test_inspect_update_uses_origin_apply_target_not_upstream_summary(
    clean_hermes,
    artifact,
):
    runner = CommandHarness(clean_hermes)
    runner.update_result = replace(
        runner.update_result,
        stdout="99 upstream commits available\n",
    )

    inspection = _inspect(clean_hermes, artifact, runner)

    assert inspection.ready is True
    assert inspection.target_head == "e" * 40
    assert "upstream" not in inspection.target_summary


def test_inspect_update_reports_noop_when_origin_target_is_current(
    clean_hermes,
    artifact,
):
    runner = CommandHarness(clean_hermes)
    runner.target_head = runner.git_head

    inspection = _inspect(clean_hermes, artifact, runner)

    assert inspection.ready is False
    assert inspection.reason_code == "no_update_available"


@pytest.mark.parametrize(
    "marker",
    [
        "MERGE_HEAD",
        "rebase-merge",
        "rebase-apply",
    ],
)
def test_inspect_update_refuses_incomplete_git_operation(
    clean_hermes, artifact, marker
):
    marker_path = clean_hermes / ".git" / marker
    if "." not in marker:
        marker_path.mkdir()
    else:
        marker_path.write_text("pending\n", encoding="utf-8")
    runner = CommandHarness(clean_hermes)

    inspection = _inspect(clean_hermes, artifact, runner)

    assert inspection.ready is False
    assert inspection.reason_code == "git_operation_incomplete"
    assert runner.commands == []


def test_inspect_update_refuses_unmerged_status(clean_hermes, artifact):
    runner = CommandHarness(clean_hermes)
    runner.git_status = "UU gateway/run.py\n"

    inspection = _inspect(clean_hermes, artifact, runner)

    assert inspection.ready is False
    assert inspection.reason_code == "git_operation_incomplete"


def test_inspection_reports_active_work_without_mutating(
    clean_hermes, artifact
):
    runner = CommandHarness(clean_hermes)

    inspection = _inspect(
        clean_hermes,
        artifact,
        runner,
        active_sessions=3,
    )

    assert inspection.ready is True
    assert inspection.active_sessions == 3
    assert inspection.requires_drain is True


def test_update_check_timeout_is_not_ready(clean_hermes, artifact):
    runner = CommandHarness(clean_hermes)
    runner.update_result = CommandResult(
        ("hermes", "update", "--check"),
        -1,
        "",
        "",
        timed_out=True,
    )

    inspection = _inspect(clean_hermes, artifact, runner)

    assert inspection.ready is False
    assert inspection.reason_code == "update_check_timeout"


def test_update_check_failure_is_sanitized_and_bounded(clean_hermes, artifact):
    runner = CommandHarness(clean_hermes)
    runner.update_result = CommandResult(
        ("hermes", "update", "--check"),
        2,
        "",
        "token=secret\n" + "x" * 1000,
    )

    inspection = _inspect(clean_hermes, artifact, runner)

    assert inspection.ready is False
    assert inspection.reason_code == "update_check_failed"
    assert inspection.target_summary == ""
    assert "secret" not in repr(inspection)


def test_artifact_version_drift_blocks_before_commands(clean_hermes, artifact):
    runner = CommandHarness(clean_hermes)

    inspection = inspect_update(
        hermes_root=clean_hermes,
        artifact=replace(artifact, version="4.1.4"),
        installed_hfc_version="4.2.0",
        active_sessions=0,
        run=runner,
    )

    assert inspection.ready is False
    assert inspection.reason_code == "artifact_version_mismatch"
    assert runner.commands == []


def test_unsupported_or_partial_hermes_is_refused(
    clean_hermes, artifact, monkeypatch
):
    runner = CommandHarness(clean_hermes)
    monkeypatch.setattr(
        "hermes_feishu_card.maintenance_update.detect_hermes",
        lambda root: SimpleNamespace(
            root=root,
            version="0.19.1",
            supported=True,
            compatibility="partial",
        ),
    )

    inspection = _inspect(clean_hermes, artifact, runner)

    assert inspection.ready is False
    assert inspection.reason_code == "hermes_not_fully_supported"
    assert runner.commands == []


def test_inspect_update_refuses_persistent_sidecar_before_commands(
    clean_hermes, artifact, monkeypatch,
):
    import hermes_feishu_card.maintenance_update as module
    monkeypatch.setattr(module, "persistent_sidecar_active", lambda: True, raising=False)
    commands = CommandHarness(clean_hermes)
    result = _inspect(clean_hermes, artifact, commands)
    assert result.ready is False
    assert result.reason_code == "persistent_sidecar_requires_manual_update"
    assert commands.commands == []
