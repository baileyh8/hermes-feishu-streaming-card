"""Exact private planning versus the pinned official command, without Gateway.

These tests deliberately do not replace the planner or official enable process.
The shared runtime is an ordinary wheel; inputs are the existing CI source roots.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from hermes_feishu_card.install import plugin, plugin_config_bytes
from test_legacy_observer_install import official_runtime  # noqa: F401


ROOTS = {
    "fixed": ("HFC_FIXED_TAG_SOURCE_ROOT", "3c27eb6234bf91b8ceee9e9071591b31e9b148cb"),
    "pm": ("HFC_LEGACY_PLUGIN_SOURCE_ROOT", "d923a5eb487a17c0a68801bba9ef0f56c81040a8"),
    "pm-main": (
        "HFC_UPSTREAM_LATEST_MAIN_ROOT",
        "0ab43d4f67e41be75f4f6c0fa14a86dbcfd3e5b3",
    ),
}


def _run_with_failure_diagnostic(run, *args, **kwargs):
    """Return the original result; never retry or disclose arbitrary output."""
    result = run(*args, **kwargs)
    if result.returncode:
        stderr = result.stderr or b""
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8", errors="replace")
        missing = re.findall(
            rb"(?m)^ModuleNotFoundError: No module named '([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)'\r?$",
            stderr,
        )
        print(
            json.dumps(
                {
                    "official_enable_returncode": result.returncode,
                    "missing_modules": sorted(
                        {name.decode("ascii") for name in missing if len(name) <= 200}
                    ),
                    "stderr_bytes": len(stderr),
                    "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    return result


@pytest.fixture
def official_enable_diagnostics(monkeypatch):
    original = subprocess.run

    def recorded(*args, **kwargs):
        command = args[0] if args else kwargs.get("args", ())
        if list(command[1:6]) == ["-I", "-m", "hermes_cli.main", "plugins", "enable"]:
            return _run_with_failure_diagnostic(original, *args, **kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recorded)


@pytest.mark.parametrize("missing", [True, False])
def test_official_failure_diagnostic_redacts_real_child_output(capsys, missing):
    secret = "SYNTHETIC-PRIVATE-CONFIG-SECRET"
    stderr = (
        secret
        + "\n"
        + (
            "ModuleNotFoundError: No module named 'cryptography.hazmat'\n"
            if missing
            else "ModuleNotFoundError: No module named 'bad/" + secret + "'\n"
        )
    )
    calls = []

    def once(*args, **kwargs):
        calls.append(args)
        return subprocess.run(*args, **kwargs)

    result = _run_with_failure_diagnostic(
        once,
        [
            sys.executable,
            "-I",
            "-c",
            "import sys;sys.stdout.write('"
            + secret
            + "');sys.stderr.write("
            + repr(stderr)
            + ");sys.exit(7)",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 7
    assert len(calls) == 1
    assert result.stderr == stderr  # The caller still receives the original result.
    output = capsys.readouterr().out
    assert secret not in output
    report = json.loads(output)
    assert report == {
        "official_enable_returncode": 7,
        "missing_modules": ["cryptography.hazmat"] if missing else [],
        "stderr_bytes": len(stderr.encode()),
        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
    }


@pytest.mark.parametrize("family", ROOTS)
@pytest.mark.parametrize("selection", ["fresh", "already", "disabled-canonical"])
def test_private_plan_equals_real_official_writer_and_exact_restore(
    official_runtime,
    official_enable_diagnostics,
    tmp_path,
    family,
    selection,
):
    _, python, prefix, path_file = official_runtime
    variable, expected_head = ROOTS[family]
    configured = os.environ.get(variable)
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("pinned writer source missing: " + variable)
        pytest.skip("pinned writer source missing: " + variable)
    source = Path(configured)
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    assert head == expected_head
    clean = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert not clean.stdout.strip(), "pinned writer fixture must be clean"
    root, home = tmp_path / "hermes", tmp_path / "home"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    home.mkdir(mode=0o700)
    (root / ".venv").symlink_to(prefix, target_is_directory=True)
    path_file.write_text(str(root) + "\nimport sys; sys.dont_write_bytecode=True\n")
    enabled = (
        "[z-plugin, hermes-feishu-card, a-plugin]" if selection != "fresh" else "[]"
    )
    disabled = "[keep-disabled, hermes_feishu_card]"
    if selection == "disabled-canonical":
        disabled = "[keep-disabled, hermes_feishu_card, hermes-feishu-card]"
    original = (
        f"# preserve exact original bytes\r\nplugins:\r\n  enabled: {enabled}\r\n"
        f"  disabled: {disabled}\r\n  isolation: in_process\r\n"
        'agent:\r\n  max_turns: 100\r\nother: "keep"\r\n_config_version: 50\r\n'
    ).encode()
    (home / "config.yaml").write_bytes(original)
    if family != "fixed":
        uv = shutil.which("uv")
        assert uv
        project = home / "pm-project"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname="byte-plan-test"\nversion="1"\nrequires-python=">=3.11"\ndependencies=[]\n[tool.uv]\npackage=false\n'
        )
        subprocess.run(
            [uv, "lock", "--offline"],
            cwd=project,
            check=True,
            capture_output=True,
            timeout=30,
        )
        (home / "hfc-pm-tools.json").write_text(
            json.dumps({"uv": uv, "python": str(python), "project": str(project)})
        )
    binding = plugin.resolve_runtime_binding(
        checkout_root=root, hermes_home=home, profile_id=None
    )
    preimage = plugin.prepare_plugin_config(binding)
    desired, enabled_before = plugin._expected_enabled_config(
        plugin._load_config_mapping(original)
    )
    if enabled_before:
        desired, _ = plugin._expected_enabled_config(
            plugin._load_config_mapping(original), preserve_enabled_lists=True
        )
    plan = plugin_config_bytes.plan_enabled_bytes(
        root=root,
        python=python,
        home=home,
        state_dir=preimage.state_dir,
        before=original,
        desired=desired,
        enabled_before=enabled_before,
    )
    assert plan.family == family
    assert (home / "config.yaml").read_bytes() == original
    assert not list(preimage.state_dir.glob("hfc-config-plan-*"))
    ownership = plugin.enable_plugin(binding, preimage)
    assert (home / "config.yaml").read_bytes() == plan.postimage
    assert (
        "hermes_feishu_card"
        in plugin._load_config_mapping(plan.postimage)["plugins"]["disabled"]
    )
    plugin.restore_plugin_config(binding, preimage, ownership)
    assert (home / "config.yaml").read_bytes() == original
    assert not (home / "hfc-forbidden.jsonl").exists()

    # Completed source copies are owned scratch, not retained upstream fixtures.
    shutil.rmtree(root)
    if family != "fixed":
        shutil.rmtree(home / "pm-project")
