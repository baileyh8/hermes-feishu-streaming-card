"""Static and command-shape checks for the official-image acceptance tools."""
from __future__ import annotations

import json
import os
from pathlib import Path
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
