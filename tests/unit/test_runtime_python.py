from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from hermes_feishu_card.install import plugin, runtime_python
from hermes_feishu_card import cli


def _pm(root, body):
    package = root / "pm"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "environments.py").write_text(body)


def _legacy(root):
    path = root / "venv" / "bin" / "python"
    path.parent.mkdir(parents=True)
    path.write_text("stale runtime")
    return path


def test_absent_pm_does_not_probe_and_keeps_legacy_binding(tmp_path, monkeypatch):
    legacy = _legacy(tmp_path)
    monkeypatch.setattr(runtime_python.subprocess, "run", lambda *a, **k: pytest.fail("unexpected probe"))
    assert runtime_python.pm_managed_runtime_python(tmp_path) is None
    assert plugin._runtime_launcher(tmp_path) == legacy
    assert cli._detect_hermes_runtime_python(tmp_path) == legacy


def test_explicitly_unselected_pm_keeps_original_launch_contract(tmp_path):
    legacy = _legacy(tmp_path)
    _pm(tmp_path, "def committed_venv(root): return None\n")
    assert plugin._runtime_launcher(tmp_path) == legacy
    assert cli._detect_hermes_runtime_python(tmp_path) == legacy


@pytest.mark.parametrize("body", [
    "def committed_venv(root): raise RuntimeError('private diagnostic')\n",
    "def committed_venv(root): return 'relative/venv'\n",
    "def committed_venv(root): return str(root / 'deleted-generation')\n",
    "def committed_venv(root): return ''\n",
])
def test_broken_pm_never_falls_back_to_existing_legacy(tmp_path, body):
    _legacy(tmp_path)
    _pm(tmp_path, body)
    with pytest.raises(plugin.RuntimeBindingRefused, match="Hermes PM") as exc:
        plugin._runtime_launcher(tmp_path)
    assert "private diagnostic" not in str(exc.value)
    with pytest.raises(runtime_python.PMRuntimeRefused):
        cli._detect_hermes_runtime_python(tmp_path)


def test_pm_timeout_is_refused_without_legacy_fallback(tmp_path, monkeypatch):
    _legacy(tmp_path)
    _pm(tmp_path, "")
    def timeout(*args, **kwargs):
        assert args[0][1] == "-I"
        assert kwargs["timeout"] == 30.0
        raise subprocess.TimeoutExpired("probe", 30)
    monkeypatch.setattr(runtime_python.subprocess, "run", timeout)
    with pytest.raises(plugin.RuntimeBindingRefused, match="probe failed"):
        plugin._runtime_launcher(tmp_path)
    with pytest.raises(runtime_python.PMRuntimeRefused):
        cli._detect_hermes_runtime_python(tmp_path)


@pytest.mark.parametrize("stdout", ["", "not json", "{}", '{"venv":true}'])
def test_malformed_pm_probe_is_refused(tmp_path, monkeypatch, stdout):
    _legacy(tmp_path)
    _pm(tmp_path, "")
    monkeypatch.setattr(runtime_python.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, stdout, ""))
    with pytest.raises(plugin.RuntimeBindingRefused, match="invalid"):
        plugin._runtime_launcher(tmp_path)


def test_pm_uses_explicit_home_and_preserves_venv_launcher(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    parent_module = object()
    monkeypatch.setitem(sys.modules, "pm.environments", parent_module)
    home = tmp_path / "selected home"
    launcher = home / "generation" / "Scripts" / "python.exe"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(sys.executable)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "wrong-home"))
    _pm(root, "import os\nfrom pathlib import Path\ndef committed_venv(root): return Path(os.environ['HERMES_HOME']) / 'generation'\n")
    selected = plugin._runtime_launcher(root, hermes_home=home)
    assert selected == launcher
    assert selected != launcher.resolve()
    assert sys.modules["pm.environments"] is parent_module


def test_failed_pm_stops_install_before_hook_mutation(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    _legacy(tmp_path)
    _pm(tmp_path, "def committed_venv(root): raise RuntimeError('private diagnostic')\n")
    detection = SimpleNamespace(root=tmp_path, supported=True)
    monkeypatch.setattr(cli, "detect_hermes", lambda root: detection)
    monkeypatch.setattr(cli, "_read_manifest", lambda path: None)
    monkeypatch.setattr(cli, "_run_fixed_tag_v3_install", lambda *a: pytest.fail("unexpected mutation"))
    assert cli._run_install(SimpleNamespace(hermes_dir=tmp_path)) == 1
    assert "Hermes PM" in capsys.readouterr().err
    report = cli._doctor_runtime_import_report(detection)
    assert report["checked"] and report["status"] == "failed"
    assert "private diagnostic" not in report["message"]
    monkeypatch.setattr(cli, "_hermes_requires_feishu_sdk_capability", lambda root: True)
    assert cli._doctor_feishu_sdk_report(detection)["status"] == "failed"
