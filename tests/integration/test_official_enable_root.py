"""The official enable child must execute the selected checkout's modules.

The PM generation Python has an editable workspace copy of ``hermes_cli``.
``cwd`` does not select that copy's parent, and ``HERMES_INSTALL_ROOT`` does
not replace ``pm.paths.repo_root()``. These tests use the real 0.22.5 source
and process environment; they do not inject ``repo_root`` or ``pm.client``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hermes_feishu_card.install import plugin, plugin_config_bytes


PIN = "bf0203a794a07aac3ff764972ab9087baa4e4f4e"


def _source() -> Path:
    configured = os.environ.get("HFC_CONFIG_WRITER_0225_ROOT")
    if not configured:
        message = "pinned v0.22.5 source missing: HFC_CONFIG_WRITER_0225_ROOT"
        if os.environ.get("HFC_REQUIRE_PM_ROOT_ENTRYPOINT") == "1":
            pytest.fail(message)
        pytest.skip(message)
    source = Path(configured).resolve()
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    assert head == PIN
    status = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    assert not status.strip(), "pinned source must be clean"
    return source


def _runtime_with_workspace(tmp_path: Path, workspace: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(runtime)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    python = runtime / "bin" / "python"
    site = Path(subprocess.run(
        [str(python), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout.strip())
    (site / "pm-workspace.pth").write_text(str(workspace) + "\n", encoding="utf-8")
    return python, site


def _binding(source: Path, python: Path, site: Path, home: Path) -> plugin.HermesRuntimeBinding:
    config = home / "config.yaml"
    config.write_text("plugins: {}\n", encoding="utf-8")
    return plugin.HermesRuntimeBinding(
        checkout_root=source,
        runtime_python=python,
        runtime_python_resolved=python.resolve(),
        python_identity="sha256:" + "0" * 64,
        hermes_home=home,
        config_path=config,
        purelib=site,
        platlib=site,
    )


def _copy_family_sources(root: Path, source: Path, family: str) -> None:
    relative_paths = set(plugin_config_bytes.FAMILIES[family]) | {
        "hermes_cli/main.py",
        "hermes_cli/__init__.py",
    }
    for relative in relative_paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / relative).read_bytes())


@pytest.mark.skipif(os.name != "posix", reason="PM generation launcher uses bin/python")
def test_official_enable_bootstrap_loads_checkout_main(tmp_path, monkeypatch):
    source = _source()
    workspace = tmp_path / "workspace"
    (workspace / "hermes_cli").mkdir(parents=True)
    marker = tmp_path / "workspace-loaded"
    (workspace / "hermes_cli" / "main.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('workspace', encoding='utf-8')\n",
        encoding="utf-8",
    )
    python, site = _runtime_with_workspace(tmp_path, workspace)
    home = tmp_path / "home"
    home.mkdir()
    binding = _binding(source, python, site, home)
    original_run = subprocess.run

    def version_child(argv, **kwargs):
        assert argv[0] == str(python)
        assert argv[1:3] == ["-I", "-c"]
        assert argv[4] == str(source)
        assert argv[-4:] == [
            "plugins", "enable", plugin.PLUGIN_KEY, "--no-allow-tool-override"
        ]
        assert kwargs["cwd"] == str(source)
        assert kwargs["env"] == {
            "HOME": str(home),
            "HERMES_HOME": str(home),
            "HERMES_INSTALL_ROOT": str(source),
        }
        # Exercise the same bootstrap with the real upstream CLI's dependency-free
        # fast path. The workspace copy would win if the bootstrap did not prepend
        # the selected checkout before importing ``hermes_cli.main``.
        result = original_run([*argv[:5], "--version"], **kwargs)
        assert result.returncode == 0, result.stderr
        assert f"Install directory: {source}" in result.stdout
        return result

    monkeypatch.setattr(plugin.subprocess, "run", version_child)
    assert plugin._run_official_enable(binding) == 0
    assert not marker.exists(), "the runtime workspace entrypoint was executed"


@pytest.mark.skipif(os.name != "posix", reason="PM generation launcher uses bin/python")
def test_official_enable_bootstrap_selects_checkout_pm_root_not_workspace(tmp_path):
    source = _source()
    root = tmp_path / "checkout"
    workspace = tmp_path / "workspace"
    selected = r'''
import json, os
from pathlib import Path
import pm.paths
Path(os.environ["HERMES_HOME"], "selected-root.json").write_text(
    json.dumps({
        "module": __file__,
        "repo_root": str(pm.paths.repo_root()),
        "install_root": str(pm.paths.install_root()),
        "install_root_env": os.environ.get("HERMES_INSTALL_ROOT"),
    }, sort_keys=True),
    encoding="utf-8",
)
'''
    for tree in (root, workspace):
        (tree / "hermes_cli").mkdir(parents=True)
        (tree / "hermes_cli" / "main.py").write_text(selected, encoding="utf-8")
        (tree / "pm").mkdir()
        (tree / "pm" / "__init__.py").write_text("", encoding="utf-8")
        (tree / "pm" / "paths.py").write_bytes((source / "pm" / "paths.py").read_bytes())
    python, _ = _runtime_with_workspace(tmp_path, workspace)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("plugins: {}\n", encoding="utf-8")
    command = [
        str(python), "-I", "-m", "hermes_cli.main", "pm", "status",
    ]
    old_env = {
        "HOME": str(home),
        "HERMES_HOME": str(home),
        "HERMES_INSTALL_ROOT": str(root),
    }
    old = subprocess.run(
        command,
        cwd=str(root),
        env=old_env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert old.returncode == 0, old.stderr
    old_result = json.loads((home / "selected-root.json").read_text(encoding="utf-8"))
    assert Path(old_result["module"]).resolve() == (workspace / "hermes_cli" / "main.py").resolve()
    assert old_result["repo_root"] == str(workspace.resolve())
    assert old_result["install_root"] == str(root.resolve())

    (home / "selected-root.json").unlink()
    new = subprocess.run(
        [
            str(python), "-I", "-c", plugin._OFFICIAL_ENABLE_BOOTSTRAP,
            str(root), "pm", "status",
        ],
        cwd=str(root),
        env=old_env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert new.returncode == 0, new.stderr
    result = json.loads((home / "selected-root.json").read_text(encoding="utf-8"))
    assert Path(result["module"]).resolve() == (root / "hermes_cli" / "main.py").resolve()
    assert result["repo_root"] == str(root.resolve())
    assert result["install_root"] == str(root.resolve())
    assert result["install_root_env"] == str(root)


def test_official_enable_rejects_replaced_or_symlinked_entrypoint(tmp_path, monkeypatch):
    source = _source()
    root = tmp_path / "checkout"
    _copy_family_sources(root, source, "pm-0225")
    plugin._verify_official_enable_source(root, family="pm-0225")
    with pytest.raises(plugin.PluginConfigRefused, match="root is invalid"):
        plugin._verify_official_enable_source(root, family="pm-0224")

    (root / "hermes_cli" / "main.py").write_text(
        "from pathlib import Path\nPath('untrusted-marker').write_text('executed')\n",
        encoding="utf-8",
    )
    python, site = _runtime_with_workspace(tmp_path, tmp_path / "empty-workspace")
    home = tmp_path / "home"
    home.mkdir()
    binding = _binding(root, python, site, home)
    monkeypatch.setattr(
        plugin.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("replaced entrypoint must not execute"),
    )
    with pytest.raises(plugin.PluginConfigRefused, match="root is invalid"):
        plugin._run_official_enable(binding)

    (root / "hermes_cli" / "main.py").write_bytes((source / "hermes_cli" / "main.py").read_bytes())
    (root / "hermes_cli" / "__init__.py").write_text(
        "raise RuntimeError('untrusted package initializer')\n",
        encoding="utf-8",
    )
    with pytest.raises(plugin.PluginConfigRefused, match="root is invalid"):
        plugin._verify_official_enable_source(root, family="pm-0225")

    real = tmp_path / "real"
    _copy_family_sources(real, source, "pm-0225")
    linked = tmp_path / "linked-checkout"
    linked.mkdir()
    (linked / "hermes_cli").symlink_to(real / "hermes_cli", target_is_directory=True)
    with pytest.raises(plugin.PluginConfigRefused, match="root is invalid"):
        plugin._verify_official_enable_source(linked, family="pm-0225")


def test_enable_plugin_checks_the_planned_family_before_spawn(tmp_path, monkeypatch):
    source = _source()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    python, site = _runtime_with_workspace(tmp_path, workspace)
    home = tmp_path / "home"
    home.mkdir()
    binding = _binding(source, python, site, home)
    preimage = plugin.prepare_plugin_config(binding)
    plan = SimpleNamespace(
        postimage=b"plugins: {}\n",
        family="pm-0224",
        verify_source=lambda: None,
        matches_expected=lambda expected: True,
    )
    monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", lambda *args: plan)
    monkeypatch.setattr(
        plugin,
        "_run_official_enable",
        lambda binding: pytest.fail("a different family must not reach the child"),
    )

    with pytest.raises(plugin.PluginConfigRefused, match="root is invalid"):
        plugin.enable_plugin(binding, preimage)
