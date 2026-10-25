"""Static and command-shape checks for the official-image acceptance tools."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import zipfile

import pytest

from tools import official_image_acceptance as acceptance
from tools import official_image_diagnose as diagnose


VERSION = "5.0.8"
WHEEL_NAME = f"hermes_feishu_streaming_card-{VERSION}-py3-none-any.whl"


def _wheel(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "hermes_feishu_card/__init__.py",
            f"__version__ = {VERSION!r}\n",
        )
        archive.writestr("hermes_feishu_card/hook_runtime.py", "# fixture\n")
        archive.writestr(
            f"hermes_feishu_streaming_card-{VERSION}.dist-info/METADATA",
            f"Name: hermes-feishu-streaming-card\nVersion: {VERSION}\n",
        )
        archive.writestr(
            f"hermes_feishu_streaming_card-{VERSION}.dist-info/entry_points.txt",
            "[console_scripts]\n"
            "hermes-feishu-card = hermes_feishu_card.cli:main\n"
            "\n[hermes_agent.plugins]\n"
            "hermes-feishu-card = hermes_feishu_card.hermes_plugin\n",
        )
    return path


def test_image_matrix_is_pinned_and_includes_0222():
    assert set(acceptance.IMAGE_SPECS) == {
        "v0.22.1",
        "v0.22.2",
        "v0.22.3",
        "v0.22.4",
        "v0.22.5",
    }
    for spec in acceptance.IMAGE_SPECS.values():
        assert spec.reference.startswith("nousresearch/hermes-agent@sha256:")
        assert len(spec.source_sha) == 40
        assert spec.index_digest.startswith("sha256:")
        assert spec.amd64_manifest.startswith("sha256:")


def test_wheel_validation_requires_real_basename(tmp_path):
    wheel = _wheel(tmp_path / WHEEL_NAME)
    assert acceptance.inspect_wheel(wheel).version == VERSION
    assert diagnose._wheel(wheel)["filename"] == WHEEL_NAME
    wrong = tmp_path / "wheel.whl"
    wrong.write_bytes(wheel.read_bytes())
    with pytest.raises(acceptance.AcceptanceFailure):
        acceptance.inspect_wheel(wrong)
    with pytest.raises(diagnose.Refused):
        diagnose._wheel(wrong)


def test_replacement_wheel_keeps_valid_basename_and_changes_bytes(tmp_path):
    original = _wheel(tmp_path / "source" / WHEEL_NAME)
    replacement = acceptance._make_replacement_wheel(tmp_path / "evidence", original)
    assert replacement == tmp_path / "evidence" / "replacement-wheel" / WHEEL_NAME
    assert replacement.name == WHEEL_NAME
    assert replacement.name != "replacement.whl"
    assert acceptance._sha256_file(replacement) != acceptance._sha256_file(original)
    assert acceptance.inspect_wheel(replacement).version == VERSION


def test_legacy_seed_is_preenabled_and_versioned(tmp_path, monkeypatch):
    home = tmp_path / "home"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setattr(acceptance, "HERMES_HOME", str(home))
    digest = acceptance._seed_legacy_config(evidence)
    assert (home / "config.yaml").read_bytes() == acceptance.LEGACY_SEED
    assert (evidence / "legacy-seed.yaml").read_bytes() == acceptance.LEGACY_SEED
    metadata = json.loads((evidence / "legacy-seed.json").read_text())
    assert metadata["source"] == "LEGACY_SEED"
    assert metadata["sha256"] == digest
    assert metadata["preenabled"] == [acceptance.PLUGIN_NAME]
    assert metadata["config_version"] == 51


def test_legacy_phase_uses_legacy_seed_before_other_setup(tmp_path, monkeypatch):
    marker = RuntimeError("legacy seed called")
    calls = []
    monkeypatch.setattr(
        acceptance,
        "_seed_legacy_config",
        lambda evidence: calls.append(evidence) or (_ for _ in ()).throw(marker),
    )
    with pytest.raises(RuntimeError, match="legacy seed called"):
        acceptance._legacy_phase(tmp_path, SimpleNamespace(), SimpleNamespace())
    assert calls == [tmp_path]


def test_baseline_registers_other_before_install(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        acceptance,
        "_layout_report",
        lambda: {
            "repo_root": acceptance.HERMES_ROOT,
            "repo_root_expected": acceptance.HERMES_ROOT,
            "hermes_install_root_present": False,
            "pythonpath_present": False,
        },
    )
    monkeypatch.setattr(acceptance, "_current_wheel", lambda: tmp_path / "candidate.whl")
    monkeypatch.setattr(acceptance, "_base_python", lambda: Path("/base/python"))
    monkeypatch.setattr(
        acceptance,
        "_pyc_baseline",
        lambda *a, **k: {"cleared": {}, "operation_root": {}, "operation_home": {}},
    )
    monkeypatch.setattr(acceptance, "_run_runtime_verify", lambda *a, **k: {})
    monkeypatch.setattr(acceptance, "_seed", lambda *a, **k: calls.append("seed"))
    monkeypatch.setattr(
        acceptance,
        "_register_other_plugin",
        lambda *a, **k: calls.append("register") or (_ for _ in ()).throw(RuntimeError("stop")),
    )
    monkeypatch.setattr(acceptance, "_install", lambda *a, **k: calls.append("install"))
    with pytest.raises(RuntimeError, match="stop"):
        acceptance._baseline_phase(
            tmp_path, SimpleNamespace(sha256="a" * 64, version=VERSION)
        )
    assert calls == ["seed", "register"]


def test_legacy_registers_other_before_legacy_install(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        acceptance,
        "_seed_legacy_config",
        lambda evidence: calls.append("seed") or acceptance.LEGACY_SEED,
    )
    monkeypatch.setattr(acceptance, "_source_snapshot", lambda _: {})
    monkeypatch.setattr(
        acceptance,
        "_pyc_baseline",
        lambda *a, **k: calls.append("pyc") or {"operation_root": {}, "operation_home": {}},
    )
    monkeypatch.setattr(
        acceptance,
        "_register_other_plugin",
        lambda *a, **k: calls.append("register") or (_ for _ in ()).throw(RuntimeError("stop")),
    )
    with pytest.raises(RuntimeError, match="stop"):
        acceptance._legacy_phase(tmp_path, SimpleNamespace(), SimpleNamespace())
    assert calls == ["seed", "pyc", "register"]


def test_other_plugin_registration_happens_before_install(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    config = home / "config.yaml"
    config.write_text("plugins:\n  enabled: []\n  disabled: []\n")
    monkeypatch.setattr(acceptance, "HERMES_ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(acceptance, "HERMES_HOME", str(home))
    monkeypatch.setattr(acceptance, "_base_python", lambda: Path("/base/python"))
    monkeypatch.setattr(acceptance, "_clean_container_env", lambda extra=None: {})

    def admit(name, command, **kwargs):
        assert name == "other-before-install-admission"
        config.write_text(
            "plugins:\n  enabled: [hfc-acceptance-other]\n"
            "  disabled: []\n"
        )
        return SimpleNamespace(
            returncode=0,
            stdout='{"enabled": ["hfc-acceptance-other"], "disabled": []}\n',
        )

    monkeypatch.setattr(acceptance, "_run_logged", admit)
    after = acceptance._register_other_plugin(evidence, "other-before-install")
    assert after == config.read_bytes()
    assert "hfc-acceptance-other" in acceptance._plugin_selection()[0]
    record = json.loads((evidence / "other-before-install.json").read_text())
    assert record["enabled_before"] == []
    assert record["enabled_after"] == ["hfc-acceptance-other"]


def test_generation_change_keeps_config_bytes_and_requires_binding_change(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    config = home / "config.yaml"
    before = (
        "plugins:\n  enabled:\n    - hermes-feishu-card\n"
        "    - hfc-acceptance-other\n  disabled: []\n"
    ).encode()
    config.write_bytes(before)
    marker = tmp_path / "root" / ".hermes_feishu_card_observer.json"
    marker.parent.mkdir()
    marker.write_bytes(b"marker")
    old_generation = tmp_path / "old-generation"
    old_generation.mkdir()
    seen = {}
    monkeypatch.setattr(acceptance, "HERMES_ROOT", str(marker.parent))
    monkeypatch.setattr(acceptance, "HERMES_HOME", str(home))
    monkeypatch.setattr(acceptance, "_base_python", lambda: Path("/base/python"))
    monkeypatch.setattr(acceptance, "_clean_container_env", lambda extra=None: {})
    monkeypatch.setattr(acceptance, "_selected_python", lambda: tmp_path / "new-generation")
    bindings = iter(("binding-before", "binding-after"))
    monkeypatch.setattr(acceptance, "_binding_identity", lambda: next(bindings))

    def admit(name, command, **kwargs):
        seen["name"] = name
        seen["config"] = config.read_bytes()
        seen["other_version"] = (home / "plugins" / acceptance.OTHER_PLUGIN / "pyproject.toml").read_text()
        return SimpleNamespace(
            returncode=0,
            stdout='{"enabled": ["hermes-feishu-card", "hfc-acceptance-other"], "disabled": []}\n',
        )

    monkeypatch.setattr(acceptance, "_run_logged", admit)
    selected, after, binding = acceptance._advance_other_generation(
        evidence, "generation", old_generation, b"marker"
    )
    assert selected == tmp_path / "new-generation"
    assert after == before
    assert binding == "binding-after"
    assert seen["config"] == before
    assert 'version="2"' in seen["other_version"]


def test_legacy_recovery_restores_before_fresh_upgrade(tmp_path, monkeypatch):
    root = tmp_path / "root"
    home = tmp_path / "home"
    evidence = tmp_path / "evidence"
    root.mkdir()
    home.mkdir()
    evidence.mkdir()
    (evidence / "legacy-observer").mkdir()
    marker = root / ".hermes_feishu_card_observer.json"
    marker.write_bytes(b"rebound")
    config = home / "config.yaml"
    config.write_text(
        "plugins:\n  enabled: [hermes-feishu-card, hfc-acceptance-other]\n"
        "  disabled: []\nother: keep\n"
    )
    member = home / "plugins" / "hfc-runtime-dependency"
    member.mkdir(parents=True)
    owner = member / ".hfc-owner.json"
    owner.write_text(json.dumps({"schema": 1, "version": "5.0.7"}))
    old_generation = tmp_path / "old-generation"
    old_generation.mkdir()
    source_before = {"gateway/run.py": "digest"}
    calls = []
    monkeypatch.setattr(acceptance, "HERMES_ROOT", str(root))
    monkeypatch.setattr(acceptance, "HERMES_HOME", str(home))
    monkeypatch.setattr(acceptance, "_source_snapshot", lambda _: source_before)
    monkeypatch.setattr(acceptance, "_current_wheel", lambda: tmp_path / "candidate.whl")
    monkeypatch.setattr(acceptance, "_selected_python", lambda: Path("/selected/python"))
    monkeypatch.setattr(acceptance, "_observer_status", lambda: {"status": "active"})
    monkeypatch.setattr(acceptance, "_run_runtime_verify", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(acceptance, "_check_pm_installed", lambda _: True)

    def cli(_evidence, name, arguments, **kwargs):
        calls.append(name)
        if name == "legacy-restore":
            assert json.loads(owner.read_text())["schema"] == 1
            marker.unlink()
        elif name == "legacy-uninstall":
            shutil.rmtree(member)
        return SimpleNamespace(returncode=0)

    def install(_evidence, *, name, **kwargs):
        calls.append(name)
        assert not member.exists()
        member.mkdir(parents=True)
        owner.write_text(json.dumps({"schema": 2, "version": "5.0.8"}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(acceptance, "_cli", cli)
    monkeypatch.setattr(acceptance, "_install", install)
    report = acceptance._legacy_recover_after_rebind(
        evidence,
        SimpleNamespace(version="5.0.8", sha256="candidate"),
        SimpleNamespace(version="5.0.7", sha256="legacy"),
        source_before=source_before,
        config_before_rebind=config.read_bytes(),
        marker_before_rebind=b"old",
        old_generation=old_generation,
    )
    assert calls == ["legacy-restore", "legacy-uninstall", "legacy-fresh-install"]
    assert report["restored_owner_schema"] == 1
    assert report["candidate_owner_schema"] == 2


def test_acceptance_docker_command_mounts_wheel_directory(tmp_path):
    spec = acceptance.IMAGE_SPECS["v0.22.5"]
    wheel_dir = tmp_path / "wheels"
    command = acceptance._docker_run_command(
        docker="docker",
        spec=spec,
        state=tmp_path / "state",
        evidence=tmp_path / "evidence",
        wheel_dir=wheel_dir,
        wheel_name=WHEEL_NAME,
        legacy_name=None,
        phase="diagnose",
        pyc_mode="preserved",
        capture_enable=False,
        uid=1000,
        gid=1000,
        tool=tmp_path / "tool.py",
    )
    assert f"{wheel_dir}:/opt/hfc/wheels:ro" in command
    assert f"HFC_WHEEL_FILENAME={WHEEL_NAME}" in command
    assert "/opt/hfc/wheel.whl" not in " ".join(command)
    assert "--container-diagnose" in " ".join(command)
    assert "python -B -I /opt/hfc/tool.py" in " ".join(command)
    assert not any("PYTHONPATH" in value for value in command)
    assert not any("HERMES_INSTALL_ROOT" in value for value in command)
    assert "HFC_CAPTURE_ENABLE=1" not in command
    diagnostic = acceptance._docker_run_command(
        docker="docker",
        spec=spec,
        state=tmp_path / "state",
        evidence=tmp_path / "evidence",
        wheel_dir=wheel_dir,
        wheel_name=WHEEL_NAME,
        legacy_name=None,
        phase="diagnose",
        pyc_mode="preserved",
        capture_enable=True,
        uid=1000,
        gid=1000,
        tool=tmp_path / "tool.py",
    )
    assert "HFC_CAPTURE_ENABLE=1" in diagnostic


def test_diagnostic_host_stages_real_filename(tmp_path, monkeypatch):
    wheel = _wheel(tmp_path / WHEEL_NAME)
    calls = []

    def run(command, **kwargs):
        calls.append(list(command))
        if list(command[1:2]) == ["pull"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if list(command[1:3]) == ["image", "inspect"]:
            env = ["PYTHONDONTWRITEBYTECODE=1", "HERMES_HOME=/opt/data"]
            digests = [f"nousresearch/hermes-agent@{diagnose.INDEX_DIGEST}"]
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    json.dumps(env)
                    + "|amd64|linux|"
                    + json.dumps(digests)
                ),
                stderr="",
            )
        if list(command[1:2]) == ["run"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(diagnose, "_run", run)
    evidence = tmp_path / "evidence"
    assert diagnose._host(
        [
            "--wheel",
            str(wheel),
            "--evidence-dir",
            str(evidence),
            "--docker",
            "docker",
        ]
    ) == 0
    docker_run = next(command for command in calls if command[1:2] == ["run"])
    joined = " ".join(docker_run)
    assert f"HFC_WHEEL_FILENAME={WHEEL_NAME}" in joined
    assert "/opt/hfc/wheels:ro" in joined
    assert "/opt/hfc/wheel.whl" not in joined


def test_diagnostic_collector_is_compilable():
    code = diagnose._diagnostic_code()
    compile(code, "<hfc-diagnostic>", "exec")
    assert "def collect(event,args):" in code
    assert "library_path(path)" in code
    assert "unbound" in code
    assert "request['sources']" in code


def test_diagnostic_sysconfig_allowlist_has_paths():
    paths = diagnose._sysconfig_paths()
    assert set(paths) <= {"stdlib", "platstdlib", "purelib", "platlib"}
    for value in paths.values():
        assert value["path"].startswith("/")
        assert value["resolved"].startswith("/")
        assert isinstance(value["is_symlink"], bool)


def test_pyc_baseline_and_cleared_mode_are_bounded(tmp_path):
    root = tmp_path / "root"
    cache = root / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.cpython-314.pyc").write_bytes(b"old")
    (root / "outside.pyc").write_bytes(b"outside")
    before = acceptance._pyc_snapshot(root)
    assert before["count"] == 2
    cleared = acceptance._clear_pyc(root)
    assert cleared["files"] == 2
    assert acceptance._pyc_snapshot(root)["count"] == 0
    assert not cache.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX venv launcher symlinks")
def test_python_launcher_accepts_valid_symlink_and_keeps_original_path(tmp_path):
    target = tmp_path / "python-real"
    target.write_text("#!/bin/sh\nexit 0\n")
    target.chmod(0o755)
    launcher = tmp_path / "bin" / "python"
    launcher.parent.mkdir()
    launcher.symlink_to(target)
    assert acceptance._python_launcher(launcher, "launcher") == launcher
    assert acceptance._python_launcher(target, "target") == target


@pytest.mark.skipif(os.name != "posix", reason="POSIX venv launcher symlinks")
def test_python_launcher_rejects_broken_directory_and_nonexec_targets(tmp_path):
    broken = tmp_path / "broken"
    broken.symlink_to(tmp_path / "missing")
    with pytest.raises(acceptance.AcceptanceFailure):
        acceptance._python_launcher(broken, "broken")
    directory = tmp_path / "directory"
    directory.mkdir()
    directory_link = tmp_path / "directory-link"
    directory_link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(acceptance.AcceptanceFailure):
        acceptance._python_launcher(directory_link, "directory")
    non_exec = tmp_path / "non-exec"
    non_exec.write_text("not executable\n")
    non_exec.chmod(0o644)
    with pytest.raises(acceptance.AcceptanceFailure):
        acceptance._python_launcher(non_exec, "non-exec")


def test_bootstrap_uses_supported_ensurepip_and_pip_flags(tmp_path, monkeypatch):
    calls = []

    def record(name, command, **kwargs):
        calls.append((name, list(command)))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        acceptance,
        "_base_python",
        lambda: Path("/opt/hermes/.venv/bin/python"),
    )
    monkeypatch.setattr(
        acceptance,
        "_current_wheel",
        lambda: Path("/opt/hfc/wheels/hermes_feishu_streaming_card-5.0.8-py3-none-any.whl"),
    )
    monkeypatch.setattr(acceptance, "_run_logged", record)
    acceptance._bootstrap_pip(tmp_path)
    assert [name for name, _ in calls] == ["ensurepip", "install-wheel"]
    assert "--no-compile" not in calls[0][1]
    assert "--no-compile" in calls[1][1]


def test_pyc_baseline_prepares_before_clearing(tmp_path):
    root = tmp_path / "root"
    home = tmp_path / "home"
    (root / "cache").mkdir(parents=True)
    home.mkdir()
    (root / "cache" / "image.pyc").write_bytes(b"image")

    def prepare():
        (root / "cache" / "bootstrap.pyc").write_bytes(b"bootstrap")

    baseline = acceptance._pyc_baseline(
        root,
        home,
        "cleared",
        prepare=prepare,
        evidence=tmp_path / "evidence",
    )
    assert baseline["image_root"]["count"] == 1
    assert baseline["bootstrap_root"]["count"] == 2
    assert baseline["cleared"]["hermes"]["files"] == 2
    assert baseline["operation_root"]["count"] == 0


def test_base_cli_uses_controller_bytecode_guard(monkeypatch):
    monkeypatch.setattr(
        acceptance,
        "_base_python",
        lambda: Path("/opt/hermes/.venv/bin/python"),
    )
    assert acceptance._base_cli()[:3] == [
        "/opt/hermes/.venv/bin/python",
        "-B",
        "-I",
    ]


@pytest.mark.parametrize("capture", [False, True])
def test_install_uses_explicit_enable_capture_mode(tmp_path, monkeypatch, capture):
    calls = []
    monkeypatch.setattr(acceptance, "HERMES_ROOT", "/opt/hermes")
    monkeypatch.setattr(acceptance, "HERMES_HOME", "/opt/data")
    monkeypatch.setattr(acceptance, "_base_python", lambda: Path("/base/python"))
    monkeypatch.setattr(acceptance, "_current_wheel", lambda: Path("/wheel.whl"))
    monkeypatch.setattr(acceptance, "_clean_container_env", lambda extra=None: {})
    monkeypatch.setenv("HFC_CAPTURE_ENABLE", "1" if capture else "0")
    monkeypatch.setattr(
        acceptance,
        "_run_logged",
        lambda name, command, **kwargs: calls.append((name, list(command))),
    )
    acceptance._install(tmp_path, name="install")

    name, command = calls[0]
    assert name == "install"
    arguments = [
        "install",
        "--hermes-dir",
        "/opt/hermes",
        "--hermes-home",
        "/opt/data",
        "--yes",
    ]
    if capture:
        assert command[:4] == ["/base/python", "-B", "-I", "-c"]
        assert command[5] == str(tmp_path / "install-enable-observations")
        assert command[6:] == arguments
    else:
        assert command == ["/base/python", "-B", "-I", "-m", "hermes_feishu_card.cli", *arguments]
    compile(acceptance._observed_cli_code(), "<hfc-observed-cli>", "exec")


def test_failure_state_snapshot_preserves_exact_regular_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    state = home / ".hermes_feishu_card" / "install"
    state.mkdir(parents=True)
    preimage = b"plugins:\n  enabled:\n    - hfc-runtime-dependency\n"
    (state / "hfc-config-preimage-fixed.yaml").write_bytes(preimage)
    (state / "hfc-config-preimage-fixed.json").write_text('{"phase":"installed"}\n')
    (state / "linked.yaml").symlink_to(state / "hfc-config-preimage-fixed.yaml")
    monkeypatch.setattr(acceptance, "HERMES_HOME", str(home))
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    result = acceptance._snapshot_install_state(evidence, "failure-install")

    copied = evidence / "failure-install-install-state"
    assert (copied / "hfc-config-preimage-fixed.yaml").read_bytes() == preimage
    assert result["files"]["hfc-config-preimage-fixed.yaml"] == acceptance._sha256_bytes(preimage)
    assert result["skipped"] == ["linked.yaml"]


def test_config_plan_probe_uses_exact_preimage_and_runtime(tmp_path, monkeypatch):
    home = tmp_path / "home"
    state = home / ".hermes_feishu_card" / "install"
    state.mkdir(parents=True)
    preimage = state / "hfc-config-preimage-fixed.yaml"
    preimage.write_bytes(b"plugins:\n  enabled: []\n")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    calls = []

    def run(command, **kwargs):
        calls.append(list(command))
        Path(command[-2]).write_bytes(b"plugins:\n  enabled: [hermes-feishu-card]\n")
        Path(command[-1]).write_text("")
        return SimpleNamespace(
            returncode=0,
            stdout='{"family":"fixed","actual_equals_plan":false}\n',
            stderr="",
        )

    monkeypatch.setattr(acceptance, "HERMES_HOME", str(home))
    monkeypatch.setattr(acceptance, "HERMES_ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(acceptance, "_base_python", lambda: Path("/base/python"))
    monkeypatch.setattr(acceptance, "_selected_python", lambda: Path("/pm/python"))
    monkeypatch.setattr(acceptance, "_run_command", run)
    result = acceptance._snapshot_config_plan(evidence, "failure-install")

    command = calls[0]
    assert command[:4] == ["/base/python", "-B", "-I", "-c"]
    assert command[-5:-3] == [str(state), str(preimage)]
    assert command[-2] == str(evidence / "failure-install-plan-postimage.yaml")
    assert result["status"] == "captured"
    assert result["preimage"] == preimage.name
    compile(acceptance._config_plan_probe_code(), "<hfc-config-plan-probe>", "exec")


def test_legacy_failure_records_bounded_snapshot(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    info = SimpleNamespace(as_dict=lambda: {"version": VERSION})
    snapshots = []
    monkeypatch.setenv("HFC_LEGACY_WHEEL_FILENAME", "legacy.whl")
    monkeypatch.setattr(acceptance, "_current_wheel", lambda: tmp_path / "wheel.whl")
    monkeypatch.setattr(acceptance, "_legacy_wheel", lambda: tmp_path / "legacy.whl")
    monkeypatch.setattr(acceptance, "inspect_wheel", lambda _: info)
    monkeypatch.setattr(
        acceptance,
        "_legacy_phase",
        lambda *_: (_ for _ in ()).throw(acceptance.AcceptanceFailure("legacy red")),
    )
    monkeypatch.setattr(
        acceptance,
        "_failure_snapshot",
        lambda path, stage: snapshots.append((path, stage)),
    )
    result = acceptance._container_main(
        ["--container-phase", "legacy", "--evidence", str(evidence)]
    )
    assert result == 1
    assert snapshots == [(evidence, "legacy")]
    assert json.loads((evidence / "container-result.json").read_text())["reason"] == "legacy red"


def test_runtime_verify_does_not_overwrite_capture_mode(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    marker = evidence / "capture-mode.json"
    marker.write_text('{"diagnostic_wrapper": true}\n')
    output = tmp_path / "verify.json"
    monkeypatch.setattr(acceptance, "_runtime_verify", lambda **_: 0)
    result = acceptance._container_main(
        [
            "--container-verify",
            "--verify-wheel",
            str(tmp_path / "wheel.whl"),
            "--verify-expected-sha256",
            "a" * 64,
            "--verify-output",
            str(output),
            "--evidence",
            str(evidence),
        ]
    )
    assert result == 0
    assert json.loads(marker.read_text()) == {"diagnostic_wrapper": True}
