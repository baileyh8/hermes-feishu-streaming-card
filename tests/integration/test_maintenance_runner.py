import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

import hermes_feishu_card.maintenance_runner as maintenance_runner_module
import hermes_feishu_card.maintenance_update as maintenance_update_module
from hermes_feishu_card.maintenance_store import (
    MaintenanceRefused,
    UpdateLockLease,
    acquire_update_lock,
    create_job,
    load_active_drain_lease,
    load_job,
    maintenance_paths,
    require_update_lock_lease,
    reserve_drain_lease,
    stage_job_credentials,
    stage_wheel_artifact,
    transition_job,
)
from hermes_feishu_card.maintenance_update import (
    CommandResult,
    run_job,
)


UPDATE_CHECK_TEXT = "3 updates available; target upstream/f3cda0ce"
TARGET_HEAD = "e" * 40
TARGET_FINGERPRINT = hashlib.sha256(
    ("origin/main\0" + TARGET_HEAD).encode()
).hexdigest()


def _write_wheel(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "hermes_feishu_streaming_card-4.2.0.dist-info/METADATA",
            "Metadata-Version: 2.1\n"
            "Name: hermes-feishu-streaming-card\n"
            "Version: 4.2.0\n",
        )
        archive.writestr(
            "hermes_feishu_card/__init__.py",
            '__version__ = "4.2.0"\n',
        )
    return path


@pytest.fixture
def maintenance_fixture(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    (root / ".git").mkdir(parents=True)
    (root / "gateway" / "platforms").mkdir(parents=True)
    (root / "cron").mkdir()
    for relative in (
        "gateway/run.py",
        "gateway/platforms/base.py",
        "cron/scheduler.py",
    ):
        (root / relative).write_text("patched\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "server:\n  host: 127.0.0.1\n  port: 8765\n"
        "feishu:\n  app_id: test\n  app_secret: test\n",
        encoding="utf-8",
    )
    paths = maintenance_paths(tmp_path / "state" / "maintenance")
    wheel = _write_wheel(
        tmp_path / "hermes_feishu_streaming_card-4.2.0-py3-none-any.whl"
    )
    artifact = stage_wheel_artifact(
        paths,
        wheel,
        expected_version="4.2.0",
        now=lambda: 100.0,
    )
    job = create_job(
        paths,
        hermes_root=root,
        config_path=config,
        env_file=None,
        profile_id="default",
        chat_id="oc_private",
        card_message_id="om_card",
        operator_hash="sha256:operator",
        pre_update_version="0.19.1",
        pre_update_head="a" * 40,
        target_fingerprint=TARGET_FINGERPRINT,
        target_head=TARGET_HEAD,
        artifact=artifact,
        pre_sidecar_pid=111,
        pre_runtime_id_hash="1" * 64,
        pre_runtime_sequence=5,
        job_id="job-1",
        now=lambda: 100.0,
    )
    reserve_drain_lease(paths, owner_id=job.job_id)
    state = {"hook": "installed", "version": "0.19.1"}

    def detection(current_root):
        return SimpleNamespace(
            root=current_root,
            version=state["version"],
            supported=True,
            compatibility="full",
            run_py=current_root / "gateway" / "run.py",
            run_py_exists=True,
            cron_py=current_root / "cron" / "scheduler.py",
            cron_py_exists=True,
            base_py=current_root / "gateway" / "platforms" / "base.py",
            base_py_exists=True,
            base_required=True,
        )

    def recovery(_detection):
        return SimpleNamespace(
            state=state["hook"],
            actions=(),
            executable=False,
            fingerprint="d" * 64,
            findings=(),
        )

    monkeypatch.setattr(
        "hermes_feishu_card.maintenance_update.detect_hermes",
        detection,
    )
    monkeypatch.setattr(
        "hermes_feishu_card.maintenance_update.plan_recovery",
        recovery,
    )
    return SimpleNamespace(
        root=root,
        config=config,
        paths=paths,
        artifact=artifact,
        job=job,
        state=state,
    )


class CommandHarness:
    def __init__(self, fixture):
        self.fixture = fixture
        self.actual_head = "a" * 40
        self.git_status = (
            " M gateway/run.py\n"
            " M gateway/platforms/base.py\n"
            " M cron/scheduler.py\n"
        )
        self.mutations = []
        self.commands = []
        self.command_contexts = []
        self.hermes_mutations = []
        self.default_hermes_mutations = []
        self.rebuild_runtime = False
        self.external_drain_active = True
        self.drain_transitions = []
        self.semantic_trace = []
        self.fail_at = ""
        self.hermes_update_calls = 0
        self.pinned_install_calls = 0
        self.runtime_python = (
            fixture.root
            / "venv"
            / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        self.package_location = (
            fixture.root
            / "venv"
            / "lib"
            / "python3.12"
            / "site-packages"
            / "hermes_feishu_card"
            / "__init__.py"
        )
        self.runtime_python.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_python.write_text("#!python\n", encoding="utf-8")
        self.runtime_python.chmod(0o700)
        self.package_location.parent.mkdir(parents=True, exist_ok=True)
        self.package_location.write_text(
            '__version__ = "4.2.0"\n',
            encoding="utf-8",
        )

    def __call__(self, argv, timeout, *, cwd=None, env=None):
        command = tuple(str(value) for value in argv)
        self.commands.append(command)
        self.command_contexts.append((command, cwd, dict(env or {})))
        if (
            command[0] == str(self.runtime_python)
            and command[1:3] == ("-I", "-c")
            and "gateway.drain_control" in command[3]
        ):
            self.external_drain_active = "write_drain_request" in command[3]
            self.drain_transitions.append(self.external_drain_active)
            self.semantic_trace.append(
                ("external_drain", self.external_drain_active)
            )
            return CommandResult(command, 0, "", "")
        if command[-2:] == ("rev-parse", "HEAD"):
            return CommandResult(command, 0, self.actual_head + "\n", "")
        if command[-3:] == ("rev-parse", "--verify", "origin/main"):
            return CommandResult(command, 0, TARGET_HEAD + "\n", "")
        if command[-4:] == ("fetch", "--quiet", "origin", "main"):
            return CommandResult(command, 0, "", "")
        if "merge-base" in command:
            return CommandResult(command, 0, "", "")
        if "log" in command and "--format=%h %s" in command:
            return CommandResult(command, 0, "e123456 target commit\n", "")
        if "status" in command and command[:2] == ("git", "-C"):
            return CommandResult(command, 0, self.git_status, "")
        if command[-2:] == ("update", "--check"):
            return CommandResult(command, 0, UPDATE_CHECK_TEXT + "\n", "")
        if command[-3:] == ("maintenance", "drain", "--status"):
            return CommandResult(command, 0, '{"active_sessions": 0}', "")
        if command[-3:] == ("gateway", "stop", "--all"):
            self.semantic_trace.append(
                ("gateway_command", "gateway", "stop", "--all")
            )
            target = (
                self.default_hermes_mutations
                if command[0] == "hermes"
                else self.hermes_mutations
            )
            target.append("gateway-stop")
            return self._mutation("gateway-stop", command)
        if "hermes_feishu_card.cli" in command and "stop" in command:
            return self._mutation("sidecar-stop", command)
        if "hermes_feishu_card.cli" in command and "restore" in command:
            result = self._mutation("hfc-restore", command)
            if result.returncode == 0:
                self.fixture.state["hook"] = "clean"
                self.git_status = ""
            return result
        if command[-2:] == ("update", "--yes"):
            self.semantic_trace.append(("gateway_command", "update", "--yes"))
            target = (
                self.default_hermes_mutations
                if command[0] == "hermes"
                else self.hermes_mutations
            )
            target.append("hermes-update")
            self.hermes_update_calls += 1
            result = self._mutation("hermes-update", command)
            if result.returncode == 0:
                self.actual_head = "e" * 40
                self.fixture.state["version"] = "0.19.2"
                if self.rebuild_runtime:
                    self.runtime_python.unlink(missing_ok=True)
                    self.runtime_python = (
                        self.fixture.root
                        / ".venv"
                        / (
                            "Scripts/python.exe"
                            if os.name == "nt"
                            else "bin/python"
                        )
                    )
                    self.package_location = (
                        self.fixture.root
                        / ".venv"
                        / "lib"
                        / "python3.12"
                        / "site-packages"
                        / "hermes_feishu_card"
                        / "__init__.py"
                    )
                self.runtime_python.parent.mkdir(parents=True, exist_ok=True)
                self.runtime_python.write_text("#!python\n", encoding="utf-8")
                self.runtime_python.chmod(0o700)
                self.package_location.parent.mkdir(parents=True, exist_ok=True)
                self.package_location.write_text(
                    '__version__ = "4.2.0"\n',
                    encoding="utf-8",
                )
            return result
        if command[0] == str(self.runtime_python) and command[1:4] == (
            "-I",
            "-m",
            "pip",
        ):
            self.pinned_install_calls += 1
            return self._mutation("pinned-wheel-install", command)
        if (
            command[0] == str(self.runtime_python)
            and "hermes_feishu_card.cli" in command
            and "install" in command
        ):
            result = self._mutation("hfc-install", command)
            if result.returncode == 0:
                self.fixture.state["hook"] = "installed"
                self.git_status = (
                    " M gateway/run.py\n"
                    " M gateway/platforms/base.py\n"
                    " M cron/scheduler.py\n"
                )
            return result
        if (
            command[0] == str(self.runtime_python)
            and "hermes_feishu_card.cli" in command
            and "start" in command
        ):
            return self._mutation("sidecar-start", command)
        if command[-2:] == ("gateway", "restart"):
            self.semantic_trace.append(
                ("gateway_command", "gateway", "restart")
            )
            target = (
                self.default_hermes_mutations
                if command[0] == "hermes"
                else self.hermes_mutations
            )
            target.append("gateway-restart")
            return self._mutation("gateway-restart", command)
        if command[0] == str(self.runtime_python) and command[1:3] == ("-I", "-c"):
            return CommandResult(
                command,
                0,
                json.dumps(
                    {
                        "version": "4.2.0",
                        "location": str(self.package_location),
                    }
                ),
                "",
            )
        raise AssertionError(f"unexpected command: {command}")

    def _mutation(self, name, command):
        self.mutations.append(name)
        if self.fail_at == name:
            return CommandResult(command, 1, "", f"{name} failed")
        return CommandResult(command, 0, "", "")


class HealthHarness:
    def __init__(self, runtime_python, commands):
        self.ready = True
        self.sequence = 0
        self.runtime_python = runtime_python
        self.commands = commands
        self.admission_samples = []

    def __call__(self):
        self.sequence += 1
        self.admission_samples.append(self.commands.external_drain_active)
        return {
            "status": "healthy" if self.ready else "degraded",
            "maintenance_active_sessions": 0,
            "gateway_active_sessions": 0,
            "maintenance_drain": {"active": True, "valid": True},
            "readiness": {
                "status": "ready" if self.ready else "degraded",
                "runtime_ready": self.ready,
                "last_sequence": self.sequence,
                "last_seen_age_seconds": 0,
                "runtime_id_hash": "2" * 64,
                "admission_draining": self.commands.external_drain_active,
                "active_work_count_complete": True,
                "drain_home_verified": True,
            },
            "package_version": "4.2.0",
            "process_pid": 222,
            "python_identity": _python_identity_for_fixture(self.runtime_python),
        }


def _python_identity_for_fixture(python_path):
    canonical = os.path.normcase(
        str(Path(python_path).parent.resolve(strict=False) / Path(python_path).name)
    )
    material = b"hermes-feishu-streaming-card:python-executable:v1\0" + os.fsencode(
        canonical
    )
    return f"python-sha256:{hashlib.sha256(material).hexdigest()}"


def test_supervisor_restores_updates_reinstalls_and_verifies(maintenance_fixture):
    commands = CommandHarness(maintenance_fixture)
    published = []

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        publish=lambda current: published.append(current.phase) or True,
        sleep=lambda delay: None,
        maintenance_python=Path("/maintenance/bin/python"),
    )

    assert result.phase == "succeeded"
    assert commands.mutations == [
        "gateway-stop",
        "sidecar-stop",
        "hfc-restore",
        "hermes-update",
        "pinned-wheel-install",
        "hfc-install",
        "sidecar-start",
        "gateway-restart",
    ]
    assert result.result["hermes_version"] == "0.19.2"
    assert result.result["hfc_version"] == "4.2.0"
    assert result.result["import_origin"] == "site-packages"
    assert result.result["service_status"] == "ready"
    assert published == [
        "locking",
        "draining",
        "restoring_hooks",
        "updating_hermes",
        "reinstalling_hfc",
        "starting_services",
        "verifying",
        "succeeded",
    ]


def test_binding_is_re_resolved_after_updater_rebuilds_venv(maintenance_fixture):
    commands = CommandHarness(maintenance_fixture)
    old_runtime = commands.runtime_python
    commands.rebuild_runtime = True
    health = HealthHarness(old_runtime, commands)

    def fetch_health():
        health.runtime_python = commands.runtime_python
        return health()

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=fetch_health,
        publish=lambda current: True,
        sleep=lambda _delay: None,
        maintenance_python=Path("/maintenance/bin/python"),
    )

    assert result.phase == "succeeded"
    assert commands.runtime_python != old_runtime
    restart = next(
        command
        for command in commands.commands
        if command[-2:] == ("gateway", "restart")
    )
    assert restart[0] == str(commands.runtime_python.resolve(strict=False))


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv symlink layout")
def test_maintenance_runner_preserves_venv_python_symlink(
    maintenance_fixture,
    tmp_path,
):
    commands = CommandHarness(maintenance_fixture)
    backing_python = tmp_path / "runtime" / "bin" / "python3.12"
    backing_python.parent.mkdir(parents=True)
    backing_python.write_text("#!python\n", encoding="utf-8")
    maintenance_python = tmp_path / "maintenance" / "venv" / "bin" / "python"
    maintenance_python.parent.mkdir(parents=True)
    maintenance_python.symlink_to(backing_python)

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        publish=lambda current: True,
        sleep=lambda _delay: None,
        maintenance_python=maintenance_python,
    )

    assert result.phase == "succeeded"
    stop = next(
        command
        for command in commands.commands
        if "hermes_feishu_card.cli" in command and "stop" in command
    )
    assert stop[0] == str(maintenance_python)


def test_custom_root_upgrade_never_mutates_default_hermes(
    maintenance_fixture,
    tmp_path,
):
    default_root = tmp_path / "default" / "hermes-agent"
    default_root.mkdir(parents=True)
    default_marker = default_root / "HEAD"
    default_marker.write_bytes(b"default-head-before\n")
    before_default = default_marker.read_bytes()
    commands = CommandHarness(maintenance_fixture)

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        publish=lambda current: True,
        sleep=lambda _delay: None,
        maintenance_python=Path("/maintenance/bin/python"),
        base_environment={"PATH": str(default_root / "bin")},
        proxy_environment={"HTTPS_PROXY": "http://127.0.0.1:7897"},
    )

    assert result.phase == "succeeded"
    assert commands.hermes_mutations == [
        "gateway-stop",
        "hermes-update",
        "gateway-restart",
    ]
    assert commands.default_hermes_mutations == []
    assert default_marker.read_bytes() == before_default
    bound_contexts = [
        (command, cwd, env)
        for command, cwd, env in commands.command_contexts
        if command[-3:] == ("gateway", "stop", "--all")
        or command[-2:] in {("update", "--yes"), ("gateway", "restart")}
    ]
    assert all(cwd == maintenance_fixture.root.resolve(strict=False) for _, cwd, _ in bound_contexts)
    assert all(
        env["HERMES_DIR"] == str(maintenance_fixture.root.resolve(strict=False))
        and env["HERMES_HOME"]
        == str(maintenance_fixture.root.parent.resolve(strict=False))
        for _, _, env in bound_contexts
    )


def test_card_failure_before_mutation_aborts_without_commands(maintenance_fixture):
    commands = CommandHarness(maintenance_fixture)

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        publish=lambda current: False,
        maintenance_python=Path("/maintenance/bin/python"),
    )

    assert result.phase == "failed"
    assert result.result["recovery_boundary"] == "no_mutation"
    assert commands.mutations == []


def test_official_update_failure_never_runs_custom_git_recovery(
    maintenance_fixture,
):
    commands = CommandHarness(maintenance_fixture)
    commands.fail_at = "hermes-update"

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        publish=lambda current: True,
        maintenance_python=Path("/maintenance/bin/python"),
    )

    assert result.phase == "failed"
    assert result.result["error_code"] == "hermes_update_failed"
    assert result.result["recovery_boundary"] == "old_hfc_restored"
    assert commands.mutations == [
        "gateway-stop",
        "sidecar-stop",
        "hfc-restore",
        "hermes-update",
        "pinned-wheel-install",
        "hfc-install",
        "sidecar-start",
        "gateway-restart",
    ]
    assert all(
        command not in {"reset", "checkout", "stash"}
        for mutation in commands.mutations
        for command in mutation.split()
    )


def test_resume_after_completed_update_does_not_run_update_again(
    maintenance_fixture,
):
    commands = CommandHarness(maintenance_fixture)
    commands.actual_head = "e" * 40
    commands.fixture.state["version"] = "0.19.2"
    commands.fixture.state["hook"] = "clean"
    commands.git_status = ""
    commands.runtime_python.parent.mkdir(parents=True, exist_ok=True)
    commands.runtime_python.write_text("#!python\n", encoding="utf-8")
    commands.package_location.parent.mkdir(parents=True, exist_ok=True)
    commands.package_location.write_text('__version__="4.2.0"\n', encoding="utf-8")
    transition_job(
        maintenance_fixture.job.path,
        expected_phase="locking",
        phase="draining",
    )
    transition_job(
        maintenance_fixture.job.path,
        expected_phase="draining",
        phase="restoring_hooks",
    )
    transition_job(
        maintenance_fixture.job.path,
        expected_phase="restoring_hooks",
        phase="updating_hermes",
    )

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        publish=lambda current: True,
        maintenance_python=Path("/maintenance/bin/python"),
    )

    assert result.phase == "succeeded"
    assert commands.hermes_update_calls == 0
    assert commands.pinned_install_calls == 1


def _prepare_resumed_updated_fixture(fixture, commands, phase):
    commands.actual_head = TARGET_HEAD
    fixture.state["version"] = "0.19.2"
    if phase in {"updating_hermes", "reinstalling_hfc"}:
        fixture.state["hook"] = "clean"
        commands.git_status = ""
    else:
        fixture.state["hook"] = "installed"
    transition_job(
        fixture.job.path,
        expected_phase="locking",
        phase=phase,
    )


def test_resume_from_verifying_releases_drain_before_readiness(
    maintenance_fixture,
):
    commands = CommandHarness(maintenance_fixture)
    _prepare_resumed_updated_fixture(
        maintenance_fixture,
        commands,
        "verifying",
    )
    health = HealthHarness(commands.runtime_python, commands)

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=health,
        publish=lambda current: True,
        sleep=lambda _delay: None,
        maintenance_python=Path("/maintenance/bin/python"),
    )

    assert result.phase == "succeeded"
    assert commands.drain_transitions[0] is False
    assert health.admission_samples
    assert all(sample is False for sample in health.admission_samples)


@pytest.mark.parametrize(
    "phase",
    ["updating_hermes", "reinstalling_hfc", "starting_services"],
)
def test_resume_from_each_post_restore_phase_clears_external_drain(
    maintenance_fixture,
    phase,
):
    commands = CommandHarness(maintenance_fixture)
    _prepare_resumed_updated_fixture(maintenance_fixture, commands, phase)
    health = HealthHarness(commands.runtime_python, commands)

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=health,
        publish=lambda current: True,
        sleep=lambda _delay: None,
        maintenance_python=Path("/maintenance/bin/python"),
    )

    assert result.phase == "succeeded"
    assert commands.drain_transitions[0] is False
    assert health.admission_samples
    assert all(sample is False for sample in health.admission_samples)


def test_resume_from_restoring_hooks_keeps_drain_until_gateway_stop(
    maintenance_fixture,
):
    commands = CommandHarness(maintenance_fixture)
    transition_job(
        maintenance_fixture.job.path,
        expected_phase="locking",
        phase="restoring_hooks",
    )

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        publish=lambda current: True,
        sleep=lambda _delay: None,
        maintenance_python=Path("/maintenance/bin/python"),
    )

    assert result.phase == "succeeded"
    stop_index = commands.semantic_trace.index(
        ("gateway_command", "gateway", "stop", "--all")
    )
    first_clear_index = next(
        index
        for index, entry in enumerate(commands.semantic_trace)
        if entry == ("external_drain", False)
    )
    assert stop_index < first_clear_index


def test_resume_rejects_foreign_post_update_head(maintenance_fixture):
    commands = CommandHarness(maintenance_fixture)
    commands.actual_head = "f" * 40
    commands.git_status = ""
    commands.fixture.state["hook"] = "clean"
    commands.fixture.state["version"] = "0.19.2"
    transition_job(
        maintenance_fixture.job.path,
        expected_phase="locking",
        phase="draining",
    )
    transition_job(
        maintenance_fixture.job.path,
        expected_phase="draining",
        phase="restoring_hooks",
    )
    transition_job(
        maintenance_fixture.job.path,
        expected_phase="restoring_hooks",
        phase="updating_hermes",
    )

    result = run_job(
        maintenance_fixture.job.path,
        run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        publish=lambda current: True,
        maintenance_python=Path("/maintenance/bin/python"),
    )

    assert result.phase == "failed"
    assert result.result["error_code"] == "post_update_target_mismatch"
    assert result.result["service_status"] == "ready"
    assert result.result["recovery_boundary"] == "new_hfc_restored"
    assert commands.hermes_update_calls == 0
    assert commands.pinned_install_calls == 1


def test_repeated_terminal_run_is_idempotent(maintenance_fixture):
    commands = CommandHarness(maintenance_fixture)
    completed = transition_job(
        maintenance_fixture.job.path,
        expected_phase="locking",
        phase="failed",
        result={
            "error_code": "preflight_failed",
            "recovery_boundary": "no_mutation",
        },
    )

    result = run_job(
        completed.path,
        run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        publish=lambda current: True,
        maintenance_python=Path("/maintenance/bin/python"),
    )

    assert result == load_job(completed.path)
    assert commands.mutations == []


def test_contending_run_job_preserves_active_job_and_fences(
    maintenance_fixture,
    monkeypatch,
):
    paths = maintenance_fixture.paths
    job = maintenance_fixture.job
    credential_path = stage_job_credentials(
        paths,
        job_id=job.job_id,
        environment={"FEISHU_APP_ID": "test", "FEISHU_APP_SECRET": "test"},
    )
    assert credential_path is not None
    before_job = job.path.read_bytes()
    before_credentials = credential_path.read_bytes()
    marker = {"active": True, "calls": []}

    def external_drain(_root, *, active, run=None):
        marker["calls"].append(active)
        marker["active"] = active
        return True

    monkeypatch.setattr(
        "hermes_feishu_card.maintenance_update.set_gateway_external_drain",
        external_drain,
    )
    commands = CommandHarness(maintenance_fixture)
    with acquire_update_lock(paths, job_id=job.job_id):
        result = run_job(
            job.path,
            run=commands,
            fetch_health=HealthHarness(commands.runtime_python, commands),
            publish=lambda current: True,
        )

    assert job.path.read_bytes() == before_job
    assert credential_path.read_bytes() == before_credentials
    assert result.phase == "locking"
    assert load_active_drain_lease(paths).owner_id == job.job_id
    assert marker == {"active": True, "calls": []}
    assert commands.mutations == []


def test_duplicate_runner_does_not_consume_job_environment(maintenance_fixture):
    job = maintenance_fixture.job
    path = stage_job_credentials(
        maintenance_fixture.paths,
        job_id=job.job_id,
        environment={"FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "secret"},
    )
    assert path is not None
    before_job = job.path.read_bytes()
    before_environment = path.read_bytes()

    with acquire_update_lock(maintenance_fixture.paths, job_id=job.job_id):
        return_code = maintenance_runner_module.main(["--job", str(job.path)])

    assert return_code == 0
    assert job.path.read_bytes() == before_job
    assert path.read_bytes() == before_environment


def test_terminal_cleanup_runs_before_update_lock_release(
    maintenance_fixture,
    monkeypatch,
):
    commands = CommandHarness(maintenance_fixture)
    paths = maintenance_fixture.paths
    original_release = maintenance_update_module.release_drain_lease
    observed = []
    with acquire_update_lock(paths, job_id="job-1") as lease:

        def checked_release(selected_paths, *, owner_id):
            require_update_lock_lease(
                selected_paths,
                job_id=owner_id,
                lease=lease,
            )
            observed.append("owned")
            return original_release(selected_paths, owner_id=owner_id)

        monkeypatch.setattr(
            maintenance_update_module,
            "release_drain_lease",
            checked_release,
        )
        result = run_job(
            maintenance_fixture.job.path,
            lock_lease=lease,
            run=commands,
            fetch_health=HealthHarness(commands.runtime_python, commands),
            publish=lambda current: True,
            sleep=lambda _delay: None,
            maintenance_python=Path("/maintenance/bin/python"),
        )

    assert result.phase == "succeeded"
    assert observed == ["owned"]


def test_runner_invalid_lock_path_fails_without_owned_cleanup(
    maintenance_fixture,
    monkeypatch,
):
    job = maintenance_fixture.job
    before_job = job.path.read_bytes()
    cleanup_calls = []

    @contextmanager
    def invalid_lock(*_args, **_kwargs):
        raise MaintenanceRefused("update lock path is invalid")
        yield

    monkeypatch.setattr(
        maintenance_runner_module,
        "acquire_update_lock",
        invalid_lock,
    )
    monkeypatch.setattr(
        maintenance_runner_module,
        "_cleanup_terminal_owned_job",
        lambda *_args, **_kwargs: cleanup_calls.append(True),
    )

    assert maintenance_runner_module.main(["--job", str(job.path)]) == 1
    assert job.path.read_bytes() == before_job
    assert cleanup_calls == []


def test_runner_state_machine_exception_uses_persisted_phase_boundary(
    maintenance_fixture,
    monkeypatch,
):
    job = transition_job(
        maintenance_fixture.job.path,
        expected_phase="locking",
        phase="updating_hermes",
    )
    cleanup_calls = []

    def raise_state_machine_error(*_args, **_kwargs):
        raise OSError("journal write failed after updater classification")

    monkeypatch.setattr(
        maintenance_runner_module,
        "run_job",
        raise_state_machine_error,
    )
    monkeypatch.setattr(
        maintenance_runner_module,
        "_cleanup_terminal_owned_job",
        lambda current, **_kwargs: cleanup_calls.append(current.phase),
    )

    assert maintenance_runner_module.main(["--job", str(job.path)]) == 1
    failed = load_job(job.path)
    assert failed.phase == "failed"
    assert failed.result == {
        "error_code": "runner_state_machine_exception",
        "recovery_boundary": "updater_result_classified",
        "status": "failed",
    }
    assert cleanup_calls == ["failed"]


def test_runner_initialization_failure_terminalizes_job_and_releases_fence(
    maintenance_fixture,
    monkeypatch,
):
    monkeypatch.setattr(
        maintenance_runner_module,
        "load_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad config")),
    )
    monkeypatch.setattr(
        maintenance_update_module,
        "set_gateway_external_drain",
        lambda *_args, **_kwargs: True,
    )

    code = maintenance_runner_module.main(
        ["--job", str(maintenance_fixture.job.path)]
    )

    failed = load_job(maintenance_fixture.job.path)
    assert code == 1
    assert failed.phase == "failed"
    assert failed.result["error_code"] == "runner_initialization_failed"
    assert load_active_drain_lease(maintenance_fixture.paths) is None


@pytest.mark.parametrize("late", [False, True])
def test_persistent_sidecar_refused_before_gateway_stop(maintenance_fixture, monkeypatch, late):
    commands = CommandHarness(maintenance_fixture)
    calls = []
    def persistent():
        calls.append(True)
        return not late or len(calls) > 1
    monkeypatch.setattr(maintenance_update_module, "persistent_sidecar_active", persistent, raising=False)
    result = run_job(
        maintenance_fixture.job.path, run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        sleep=lambda _: None, maintenance_python=Path("/maintenance/bin/python"),
    )
    assert result.phase == "failed"
    assert result.result["error_code"] == "persistent_sidecar_requires_manual_update"
    assert commands.mutations == []
    assert commands.external_drain_active is False


def test_sidecar_stop_failure_recovers_unchanged_gateway(maintenance_fixture):
    commands = CommandHarness(maintenance_fixture)
    commands.fail_at = "sidecar-stop"
    result = run_job(
        maintenance_fixture.job.path, run=commands,
        fetch_health=HealthHarness(commands.runtime_python, commands),
        sleep=lambda _: None, maintenance_python=Path("/maintenance/bin/python"),
    )
    assert result.phase == "failed"
    assert result.result["error_code"] == "sidecar_stop_failed"
    assert commands.mutations == ["gateway-stop", "sidecar-stop", "gateway-restart"]
    assert result.result["recovery_boundary"] == "gateway_restart_requested"


@pytest.mark.parametrize("failure", ["head_changed", "hooks_changed", "restart_failed"])
def test_stop_failure_recovery_does_not_start_unverified_runtime(maintenance_fixture, failure):
    base = CommandHarness(maintenance_fixture)
    def commands(argv, timeout, **kwargs):
        if "hermes_feishu_card.cli" in argv and "stop" in argv:
            base.fail_at = "sidecar-stop"
            result = base(argv, timeout, **kwargs)
            if failure == "head_changed":
                base.actual_head = "f" * 40
            elif failure == "hooks_changed":
                maintenance_fixture.state["hook"] = "unknown"
            else:
                base.fail_at = "gateway-restart"
            return result
        return base(argv, timeout, **kwargs)
    result = run_job(maintenance_fixture.job.path, run=commands,
        fetch_health=HealthHarness(base.runtime_python, base), sleep=lambda _: None,
        maintenance_python=Path("/maintenance/bin/python"))
    assert result.phase == "failed"
    assert result.result["error_code"] == "sidecar_stop_failed"
    assert result.result["recovery_boundary"] == "old_hfc_or_manual"
    assert ("gateway-restart" in base.mutations) is (failure == "restart_failed")
    assert "hfc-restore" not in base.mutations
    assert "hermes-update" not in base.mutations
