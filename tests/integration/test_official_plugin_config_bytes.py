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
        "HFC_CONFIG_WRITER_PM_MAIN_ROOT",
        "0ab43d4f67e41be75f4f6c0fa14a86dbcfd3e5b3",
    ),
    "reported-cf4": ("HFC_CONFIG_WRITER_CF4_ROOT", "cf4f3a7396814ffcaef69d9af09f7cfdade3ddbe"),
    "rc2": ("HFC_CONFIG_WRITER_RC2_ROOT", "f83b4df3ce1ab2ec62f1e0e90f22e0a1db21cb68"),
    # The reported image source is 297552a9..., but this shared CI fixture
    # carries the same 13 writer files at 7bda2a...; both are pinned below by
    # the exact hashes in FAMILIES["pm-0221"].
    "pm-0221": ("HFC_CONFIG_WRITER_0221_ROOT", "7bda2a3368024f7ae1c70cef16583e7c2c891674"),
    "pm-0224": ("HFC_CONFIG_WRITER_0224_ROOT", "4d04303818b61bf3aa09278af48356315515965b"),
    "pm-0225": ("HFC_CONFIG_WRITER_0225_ROOT", "bf0203a794a07aac3ff764972ab9087baa4e4f4e"),
    "pm-main-4e58": ("HFC_CONFIG_WRITER_MAIN_4E58_ROOT", "4e58c721c8cf9b51c46636ea84ea6a5cb540880a"),
}
EXPECTED_FAMILIES = {"reported-cf4": "pm-0223", "rc2": "pm-0223"}


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
        if (
            len(command) >= 9
            and list(command[1:3]) == ["-I", "-c"]
            and "hermes_cli.main" in command[3]
            and list(command[5:9]) == ["plugins", "enable", plugin.PLUGIN_KEY, "--no-allow-tool-override"]
        ):
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


@pytest.mark.parametrize("family,selection,failure", [
    (family, selection, None)
    for family in ROOTS
    for selection in ("fresh", "already", "disabled-canonical", "explicit-values")
] + [
    (family, "fresh", outcome)
    for family in ("pm-0221", "pm-0224", "pm-0225")
    for outcome in ("nonzero", "exception", "journal")
])
def test_private_plan_equals_real_official_writer_and_exact_restore(
    official_runtime,  # noqa: F811 - pytest fixture imported by name
    official_enable_diagnostics,
    tmp_path,
    family,
    selection,
    failure,
    monkeypatch,
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
    path_file.write_text(str(root) + "\nimport sys; sys.dont_write_bytecode=True\n")
    enabled = (
        "[z-plugin, hermes-feishu-card, a-plugin]" if selection != "fresh" else "[]"
    )
    disabled = "[keep-disabled, hermes_feishu_card]"
    if selection == "disabled-canonical":
        disabled = "[keep-disabled, hermes_feishu_card, hermes-feishu-card]"
    version = 50 if family in {"fixed", "pm"} else 51
    original = (
        f"# preserve exact original bytes\r\nplugins:\r\n  enabled: {enabled}\r\n"
        f"  disabled: {disabled}\r\n  isolation: in_process\r\n"
        f'agent:\r\n  max_turns: 100\r\nother: "keep"\r\n_config_version: {version}\r\n'
    ).encode()
    if selection == "explicit-values":
        original = original.replace(
            b"  isolation: in_process\r\n",
            b"  isolation: in_process\r\n  entries:\r\n"
            b"    hermes-feishu-card:\r\n      allow_tool_override: true\r\n"
            b"    keep-plugin:\r\n      enabled: false\r\n      scopes: []\r\n",
        )
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
    # Keep the runtime outside the source tree: a .venv symlink would make the
    # interpreter's sysconfig library roots symlinks under the checkout, which
    # the launch/library boundary deliberately rejects.
    purelib = prefix / "lib" / "python3.12" / "site-packages"
    binding = plugin.HermesRuntimeBinding(
        checkout_root=root,
        runtime_python=python,
        runtime_python_resolved=python.resolve(),
        python_identity="sha256:" + "a" * 64,
        hermes_home=home,
        config_path=home / "config.yaml",
        purelib=purelib,
        platlib=purelib,
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
    assert plan.family == EXPECTED_FAMILIES.get(family, family)
    assert (home / "config.yaml").read_bytes() == original
    assert not list(preimage.state_dir.glob("hfc-config-plan-*"))
    prepared_journal = preimage.journal_path.read_bytes()
    if failure is not None:
        with monkeypatch.context() as fault:
            if failure == "journal":
                def fail_journal(*args, **kwargs):
                    raise OSError("injected private journal failure")
                fault.setattr(plugin, "_rewrite_private_journal", fail_journal)
                expected_error = OSError
            else:
                official = plugin._run_official_enable
                def fail_after_real_enable(candidate):
                    assert official(candidate) == 0
                    assert candidate.config_path.read_bytes() == plan.postimage
                    if failure == "exception":
                        raise OSError("injected failure after official write")
                    return 1
                fault.setattr(plugin, "_run_official_enable", fail_after_real_enable)
                expected_error = plugin.PluginConfigRefused
            with pytest.raises(expected_error):
                plugin.enable_plugin(binding, preimage)
        assert (home / "config.yaml").read_bytes() == original
        assert preimage.backup_path.read_bytes() == original
        assert preimage.journal_path.read_bytes() == prepared_journal
        assert json.loads(prepared_journal)["phase"] == "prepared"
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


@pytest.mark.parametrize("seed_version,expected_version", [(50, 51), (11, 11)])
def test_pm_0225_official_startup_migration_matches_private_plan(
    official_runtime, tmp_path, seed_version, expected_version  # noqa: F811
):
    """Compose the exact boot migration helper with the unmodified writer."""
    _, python, prefix, path_file = official_runtime
    configured = os.environ.get("HFC_CONFIG_WRITER_0225_ROOT")
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("pinned writer source missing: HFC_CONFIG_WRITER_0225_ROOT")
        pytest.skip("pinned writer source missing: HFC_CONFIG_WRITER_0225_ROOT")
    source = Path(configured)
    root, home = tmp_path / "hermes", tmp_path / "home"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    home.mkdir(mode=0o700)
    (root / "install-stamp.json").write_text(json.dumps({"commit": "bf0203a794a07aac3ff764972ab9087baa4e4f4e"}))
    path_file.write_text(str(root) + "\nimport sys; sys.dont_write_bytecode=True\n")
    original = (
        "# preserve startup migration bytes\r\nplugins:\r\n"
        "  enabled: [hfc-runtime-dependency]\r\n  disabled: [keep-disabled]\r\n"
        f'agent:\r\n  max_turns: 100\r\nother: "keep"\r\n_config_version: {seed_version}\r\n'
    ).encode()
    (home / "config.yaml").write_bytes(original)

    uv = shutil.which("uv")
    assert uv
    project = home / "pm-project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="migration-plan-test"\nversion="1"\nrequires-python=">=3.11"\ndependencies=[]\n[tool.uv]\npackage=false\n'
    )
    subprocess.run(
        [uv, "lock", "--offline"], cwd=project, check=True,
        capture_output=True, timeout=30,
    )
    (home / "hfc-pm-tools.json").write_text(
        json.dumps({"uv": uv, "python": str(python), "project": str(project)})
    )
    purelib = prefix / "lib" / "python3.12" / "site-packages"
    binding = plugin.HermesRuntimeBinding(
        checkout_root=root,
        runtime_python=python,
        runtime_python_resolved=python.resolve(),
        python_identity="sha256:" + "a" * 64,
        hermes_home=home,
        config_path=home / "config.yaml",
        purelib=purelib,
        platlib=purelib,
    )
    preimage = plugin.prepare_plugin_config(binding)
    desired, enabled_before = plugin._expected_enabled_config(
        plugin._load_config_mapping(original)
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
    assert plan.config_version == expected_version
    assert (home / "config.yaml").read_bytes() == original

    migration = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import sys; sys.path.insert(0,sys.argv[1]); "
            "from hermes_cli.post_update import step_migrate_config; "
            "step_migrate_config()",
            str(root),
        ],
        cwd=root,
        env={
            "HOME": str(home),
            "HERMES_HOME": str(home),
            "HERMES_INSTALL_ROOT": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        timeout=30,
    )
    assert migration.returncode == 0
    assert plugin._run_official_enable(binding) == 0
    assert (home / "config.yaml").read_bytes() == plan.postimage
    if seed_version == 50:
        # Historical ownership refers to these real official bytes, even after
        # the current install's bootstrap record causes a fresh enable to skip.
        key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
        record = home / "installs" / key / "bootstrap" / "default.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"identity": "bf0203a794a07aac3ff764972ab9087baa4e4f4e"}))
        arguments = dict(root=root, python=python, home=home,
                         state_dir=preimage.state_dir, before=original,
                         desired=desired, enabled_before=enabled_before)
        current = plugin_config_bytes.plan_enabled_bytes(**arguments)
        assert current.config_version == 50
        actual = (home / "config.yaml").read_bytes()
        proven = plugin_config_bytes.plan_enabled_bytes(
            **arguments, historical_post_sha256=hashlib.sha256(actual).hexdigest()
        )
        assert proven.postimage == actual
        for changed in (actual.replace(b"keep", b"lost"),
                        actual.replace(b"_config_version: 51", b"_config_version: 52")):
            assert changed != actual
            with pytest.raises(plugin_config_bytes.ConfigBytesRefused, match="historical config postimage"):
                plugin_config_bytes.plan_enabled_bytes(
                    **arguments, historical_post_sha256=hashlib.sha256(changed).hexdigest()
                )
        assert (home / "config.yaml").read_bytes() == actual


def test_pm_0225_floor_boundary_requires_new_migration_sources(
    official_runtime, tmp_path  # noqa: F811
):
    """A below-floor config is untouched; the first auto-migrated schema is bounded."""
    _, python, _, _ = official_runtime
    configured = os.environ.get("HFC_CONFIG_WRITER_0225_ROOT")
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("pinned writer source missing: HFC_CONFIG_WRITER_0225_ROOT")
        pytest.skip("pinned writer source missing: HFC_CONFIG_WRITER_0225_ROOT")
    source = Path(configured)
    root, home, state = tmp_path / "source", tmp_path / "home", tmp_path / "state"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    home.mkdir()
    (root / "install-stamp.json").write_text(json.dumps({"commit": "bf0203a794a07aac3ff764972ab9087baa4e4f4e"}))
    state.mkdir()
    before = b"plugins: {}\n_config_version: 12\n"
    (home / "config.yaml").write_bytes(before)
    desired, enabled_before = plugin._expected_enabled_config(
        plugin._load_config_mapping(before)
    )
    with pytest.raises(plugin_config_bytes.ConfigBytesRefused) as caught:
        plugin_config_bytes.plan_enabled_bytes(
            root=root,
            python=python,
            home=home,
            state_dir=state,
            before=before,
            desired=desired,
            enabled_before=enabled_before,
        )
    assert caught.value.code == "unbound-local-source"
    assert caught.value.source == "tools/bot_mode_probe.py"
    assert (home / "config.yaml").read_bytes() == before
    assert not list(state.iterdir())


@pytest.mark.parametrize(
    ("record_identity", "expected_version"),
    [("current", 50), ("stale", 51), ("invalid", None), ("symlink", None), ("lock", None)],
)
def test_pm_0225_boot_record_gates_startup_schema_migration(
    official_runtime, tmp_path, record_identity, expected_version  # noqa: F811
):
    """The private plan follows Hermes' one-bootstrap-per-install-identity gate."""
    _, python, _, _ = official_runtime
    configured = os.environ.get("HFC_CONFIG_WRITER_0225_ROOT")
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("pinned writer source missing: HFC_CONFIG_WRITER_0225_ROOT")
        pytest.skip("pinned writer source missing: HFC_CONFIG_WRITER_0225_ROOT")
    root = Path(configured).resolve()
    home = tmp_path / "home"
    state = home / ".hermes_feishu_card" / "install"
    state.mkdir(parents=True)
    before = (
        b"plugins:\r\n  enabled: [hfc-acceptance-other, hfc-runtime-dependency]\r\n"
        b"  disabled: [keep-disabled]\r\n  isolation: in_process\r\n"
        b"other: keep\r\n_config_version: 50\r\n"
    )
    (home / "config.yaml").write_bytes(before)
    identity = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if record_identity in {"stale", "lock"}:
        identity = "0" * 40
    key = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    record = home / "installs" / key / "bootstrap" / "default.json"
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps({"schemaVersion": 1, "identity": identity, "results": {}})
    )
    if record_identity == "invalid":
        record.write_text("{invalid-json")
    elif record_identity == "symlink":
        target = record.with_name("saved-record.json")
        record.rename(target)
        record.symlink_to(target)
    elif record_identity == "lock":
        record.with_name(record.name + ".lock").write_text("held")
    record_before = record.read_bytes()
    desired, enabled_before = plugin._expected_enabled_config(
        plugin._load_config_mapping(before)
    )
    def make_plan():
        return plugin_config_bytes.plan_enabled_bytes(
            root=root, python=python, home=home, state_dir=state,
            before=before, desired=desired, enabled_before=enabled_before,
        )
    if expected_version is None:
        with pytest.raises(plugin_config_bytes.ConfigBytesRefused) as caught:
            make_plan()
        assert caught.value.code == {
            "invalid": "boot-record-invalid", "symlink": "boot-record-symlink",
            "lock": "boot-lock-present",
        }[record_identity]
    else:
        plan = make_plan()
        assert plan.config_version == expected_version
        assert b"hfc-runtime-dependency" in plan.postimage
    assert record.read_bytes() == record_before
    assert (home / "config.yaml").read_bytes() == before
    assert not list(state.iterdir())


@pytest.mark.parametrize(
    "snapshot", ["reported-cf4", "rc2", "pm-0221", "pm-0224", "pm-0225", "pm-main-4e58"]
)
@pytest.mark.parametrize("changed", [
    "hermes_cli/plugins_cmd.py", "hermes_cli/config_worker_save.py",
    "hermes_cli/config_defaults.py", "hermes_cli/plugins_admission.py",
    "pm/publication.py", "utils.py", "agent/safe_worker_policy.py",
    "hermes_cli/moa_config.py",
])
def test_new_writer_drift_refuses_before_child_and_preserves_config(
    tmp_path, monkeypatch, snapshot, changed
):
    variable, _ = ROOTS[snapshot]
    configured = os.environ.get(variable)
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("pinned writer source missing: " + variable)
        pytest.skip("pinned writer source missing: " + variable)
    source = Path(configured)
    family = EXPECTED_FAMILIES.get(snapshot, snapshot)
    if changed not in plugin_config_bytes.FAMILIES[family]:
        pytest.skip("changed source is not part of this writer family")
    root, home, state = tmp_path / "source", tmp_path / "home", tmp_path / "state"
    home.mkdir()
    state.mkdir()
    for name in plugin_config_bytes.FAMILIES[family]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    plugin_config_bytes.verify_family(root, family)
    target = root / changed
    target.write_bytes(target.read_bytes() + b"\n# unverified local writer change\n")
    original = b"# original bytes\r\nplugins: {}\r\n"
    (home / "config.yaml").write_bytes(original)
    def forbidden(*args, **kwargs):
        pytest.fail("drifted writer must not start any child")
    monkeypatch.setattr(plugin_config_bytes.subprocess, "run", forbidden)
    with pytest.raises(plugin_config_bytes.ConfigBytesRefused, match="contract is unknown"):
        plugin_config_bytes.plan_enabled_bytes(
            root=root, python=Path(sys.executable), home=home, state_dir=state,
            before=original, desired={}, enabled_before=False,
        )
    assert (home / "config.yaml").read_bytes() == original
    assert not list(state.iterdir())


@pytest.mark.parametrize("changed", [
    "hermes_cli/plugins_cmd.py",
    "hermes_cli/config.py",
    "pm/workspace.py",
    "hermes_cli/left_core_migration.py",
    "hermes_cli/config_defaults.py",
    "hermes_cli/plugins_activation.py",
    "hermes_cli/config_migrations.py",
    "hermes_cli/boot_bootstrap.py",
    "hermes_cli/profiles.py",
    "hermes_cli/tools_config.py",
    "hermes_cli/platforms.py",
    "hermes_cli/tools_config_cua.py",
    "hermes_cli/tools_config_mcp.py",
    "hermes_cli/tools_config_post_setup.py",
    "hermes_cli/tools_config_providers.py",
    "hermes_cli/toolset_scope.py",
    "tools/transcription_common.py",
    "tools/tts_command_provider.py",
    "toolsets.py",
    'agent/__init__.py',
    'agent/i18n.py',
    'agent/i18n_languages.py',
    'agent/i18n_layers.py',
    'agent/jiter_preload.py',
    'hermes_cli/__init__.py',
    'hermes_cli/_subprocess_compat.py',
    'hermes_cli/cli_output.py',
    'hermes_cli/colors.py',
    'hermes_cli/default_soul.py',
    'hermes_cli/observability/__init__.py',
    'hermes_cli/personality.py',
    'hermes_cli/plugin_install_phase.py',
    'hermes_cli/plugins_cmd_git.py',
    'hermes_cli/plugins_cmd_install.py',
    'hermes_cli/plugins_cmd_listing.py',
    'hermes_cli/plugins_cmd_remove.py',
    'hermes_cli/plugins_cmd_toggle.py',
    'hermes_cli/plugins_cmd_update.py',
    'hermes_cli/secret_prompt.py',
    'hermes_cli/stale_modules.py',
    'hermes_platform/__init__.py',
    'hermes_platform/host/__init__.py',
    'hermes_platform/host/darwin_pci.py',
    'hermes_platform/host/facts.py',
    'hermes_platform/host/runtime.py',
    'providers/base.py',
])
def test_pm_0225_each_changed_source_drift_refuses_before_child(
    tmp_path, monkeypatch, changed
):
    configured = os.environ.get("HFC_CONFIG_WRITER_0225_ROOT")
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("pinned writer source missing: HFC_CONFIG_WRITER_0225_ROOT")
        pytest.skip("pinned writer source missing: HFC_CONFIG_WRITER_0225_ROOT")
    source = Path(configured)
    root, home, state = tmp_path / "source", tmp_path / "home", tmp_path / "state"
    home.mkdir()
    state.mkdir()
    family = "pm-0225"
    for name in plugin_config_bytes.FAMILIES[family]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    plugin_config_bytes.verify_family(root, family)
    target = root / changed
    target.write_bytes(target.read_bytes() + b"\n# unverified local writer change\n")
    original = b"# original bytes\r\nplugins: {}\r\n"
    (home / "config.yaml").write_bytes(original)

    def forbidden(*args, **kwargs):
        pytest.fail("drifted writer must not start any child")

    monkeypatch.setattr(plugin_config_bytes.subprocess, "run", forbidden)
    with pytest.raises(plugin_config_bytes.ConfigBytesRefused, match="contract is unknown"):
        plugin_config_bytes.plan_enabled_bytes(
            root=root,
            python=Path(sys.executable),
            home=home,
            state_dir=state,
            before=original,
            desired={},
            enabled_before=False,
        )
    assert (home / "config.yaml").read_bytes() == original
    assert not list(state.iterdir())


@pytest.mark.parametrize("family", ["pm-0221", "pm-0224", "pm-0225"])
@pytest.mark.parametrize("changed", ["hermes_cli/main.py", "hermes_cli/__init__.py"])
def test_launch_source_drift_refuses_before_official_child(tmp_path, monkeypatch, family, changed):
    variable, _ = ROOTS[family]
    configured = os.environ.get(variable)
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("pinned writer source missing: " + variable)
        pytest.skip("pinned writer source missing: " + variable)
    source = Path(configured)
    root = tmp_path / "source"
    for name in plugin_config_bytes.FAMILIES[family]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    for name in ("hermes_cli/main.py", "hermes_cli/__init__.py"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    assert plugin_config_bytes.verify_launch_sources(root, family=family) == family
    target = root / changed
    target.write_bytes(target.read_bytes() + b"\n# unreviewed launch edit\n")

    def forbidden(*args, **kwargs):
        pytest.fail("launch drift must not start the official child")

    monkeypatch.setattr(plugin_config_bytes.subprocess, "run", forbidden)
    with pytest.raises(plugin_config_bytes.ConfigBytesRefused) as caught:
        plugin_config_bytes.verify_launch_sources(root, family=family)
    assert caught.value.code == "launch-source-drift"
    assert caught.value.source == changed


def test_pm_0225_trusted_interpreter_library_boundary(official_runtime, tmp_path):  # noqa: F811
    """A stdlib under /opt/hermes is trusted; source tools are not."""
    configured = os.environ.get("HFC_CONFIG_WRITER_0225_ROOT")
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("pinned writer source missing: HFC_CONFIG_WRITER_0225_ROOT")
        pytest.skip("pinned writer source missing: HFC_CONFIG_WRITER_0225_ROOT")
    _, python, _, _ = official_runtime
    source = Path(configured)
    root = tmp_path / "source"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    fake_stdlib = root / "tools" / "python-3.14.9+20261021-linux-x64" / "lib" / "python3.14"
    fake_stdlib.mkdir(parents=True)
    shadow = tmp_path / "shadow"
    shadow.mkdir(mode=0o700)
    marker = shadow / "marker"
    (fake_stdlib / "markerlib.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n"
    )
    original = b"plugins: {}\n"
    (shadow / "config.yaml").write_bytes(original)
    desired, enabled_before = plugin._expected_enabled_config(plugin._load_config_mapping(original))
    (shadow / "request.json").write_text(json.dumps({
        "real_home": str(tmp_path / "real"),
        "family": "pm-0225",
        "sources": plugin_config_bytes.FAMILIES["pm-0225"],
        "desired": desired,
        "enabled_before": enabled_before,
    }))

    def run(paths, imported, search_path=fake_stdlib, prefix=None):
        (shadow / "config.yaml").write_bytes(original)
        code = (
            "import sysconfig,sys\n"
            + ("sys.prefix=" + repr(str(prefix)) + "\n" if prefix else "")
            + "sysconfig.get_paths=lambda: " + repr(paths) + "\n"
            + plugin_config_bytes._CHILD
            + "\nsys.path.insert(0," + repr(str(search_path)) + ")\n"
            + "import " + imported + "\n"
        )
        return subprocess.run(
            [str(python), "-I", "-c", code, str(root), str(shadow)],
            cwd=root,
            env={"HOME": str(shadow), "HERMES_HOME": str(shadow)},
            capture_output=True,
            timeout=30,
            check=False,
        )

    paths = {key: str(fake_stdlib) for key in ("stdlib", "platstdlib", "purelib", "platlib")}
    allowed = run(paths, "markerlib")
    assert allowed.returncode == 0
    assert marker.read_text() == "executed"

    # A Homebrew-style runtime alias outside the checkout remains trusted;
    # only an alias that crosses into the source tree is refused.
    external = tmp_path / "external-stdlib"
    external.mkdir()
    external_marker = shadow / "external-marker"
    (external / "externalmarker.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(external_marker)) + ").write_text('external')\n"
    )
    external_alias = tmp_path / "homebrew-runtime-alias"
    external_alias.symlink_to(external, target_is_directory=True)
    external_paths = {key: str(external_alias) for key in ("stdlib", "platstdlib", "purelib", "platlib")}
    allowed_external = run(external_paths, "externalmarker", external)
    assert allowed_external.returncode == 0
    assert external_marker.read_text() == "external"

    # Real legacy installs borrow an external venv through checkout/.venv.
    # Only the exact interpreter library alias is admitted, not source links
    # to the same otherwise-trusted canonical directory.
    alias = root / ".venv"
    alias.symlink_to(external, target_is_directory=True)
    alias_paths = {key: str(alias) for key in ("purelib", "platlib")}
    external_marker.unlink()
    borrowed = run(alias_paths, "externalmarker", alias, prefix=alias)
    assert borrowed.returncode == 0
    assert external_marker.read_text() == "external"
    external_marker.unlink()
    source_alias = root / "tools" / "borrowed-library"
    source_alias.symlink_to(external, target_is_directory=True)
    rejected_alias = run(alias_paths, "externalmarker", source_alias, prefix=alias)
    assert rejected_alias.returncode == 73
    assert b"local-source-symlink" in rejected_alias.stderr
    assert not external_marker.exists()
    source_package_alias = root / "hermes_cli" / "borrowed-library"
    source_package_alias.symlink_to(external, target_is_directory=True)
    masked = {key: str(source_package_alias) for key in ("purelib", "platlib")}
    rejected_package = run(masked, "externalmarker", source_package_alias, prefix=source_package_alias)
    assert rejected_package.returncode == 73
    assert b"library-root-overlap" in rejected_package.stderr
    assert not external_marker.exists()

    marker.unlink()
    outside = tmp_path / "outside.py"
    outside.write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('outside')\n"
    )
    (fake_stdlib / "markerlib.py").unlink()
    (fake_stdlib / "markerlib.py").symlink_to(outside)
    denied = run(paths, "markerlib")
    assert denied.returncode == 73
    assert not marker.exists()
    assert b"local-source-symlink" in denied.stderr

    (fake_stdlib / "markerlib.py").unlink()
    (root / "tools" / "unrelated.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unrelated')\n"
    )
    denied = run(paths, "unrelated", root / "tools")
    assert denied.returncode == 73
    assert not marker.exists()
    assert b"unbound-local-source" in denied.stderr

    overlap = {key: str(root) for key in ("stdlib", "platstdlib", "purelib", "platlib")}
    denied = run(overlap, "markerlib")
    assert denied.returncode == 73
    assert b"library-root-overlap" in denied.stderr


def test_pm_0225_unbound_local_import_refuses_before_module_execution(tmp_path, monkeypatch):
    """A newly reached local module cannot execute even if its importer is bound."""
    root, home, state = tmp_path / "source", tmp_path / "home", tmp_path / "state"
    root.mkdir()
    home.mkdir()
    state.mkdir()
    package = root / "providers"
    package.mkdir()
    importer = package / "__init__.py"
    importer.write_text("import unreviewed_local\n")
    # A private-HOME write would otherwise be allowed by the side-effect audit.
    marker = home / "unbound-executed"
    (root / "unreviewed_local.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n"
    )
    sources = {"providers/__init__.py": hashlib.sha256(importer.read_bytes()).hexdigest()}
    monkeypatch.setattr(plugin_config_bytes, "FAMILIES", {"pm-0225": sources})
    original = b"plugins: {}\n"
    (home / "config.yaml").write_bytes(original)
    with pytest.raises(plugin_config_bytes.ConfigBytesRefused, match="byte plan failed"):
        plugin_config_bytes.plan_enabled_bytes(
            root=root, python=Path(sys.executable), home=home, state_dir=state,
            before=original, desired={}, enabled_before=False,
        )
    assert not marker.exists()
    assert (home / "config.yaml").read_bytes() == original
    assert not list(state.iterdir())


@pytest.mark.parametrize(
    "family,expected_code", [("pm-0221", 0), ("pm-0224", 0), ("pm-0225", 73)]
)
def test_local_source_guard_is_private_child_and_new_family_only(
    tmp_path, family, expected_code
):
    root, home = tmp_path / "source", tmp_path / "home"
    root.mkdir()
    home.mkdir()
    (home / "request.json").write_text(json.dumps({
        "real_home": str(tmp_path / "real"), "family": family, "sources": {},
    }))
    marker = home / "local-executed"
    (root / "unbound.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n"
    )
    # Execute the real audit/profile setup, before its first writer import.
    setup = plugin_config_bytes._CHILD.split(
        "for name,digest in request['sources'].items():", 1
    )[0]
    code = setup + "\nimport decimal,yaml\nsys.path.insert(0,str(root))\nimport unbound\n"
    child = subprocess.run(
        [sys.executable, "-I", "-c", code, str(root), str(home)],
        env={"HOME": str(home), "HERMES_HOME": str(home)},
        capture_output=True, timeout=30,
    )
    assert child.returncode == expected_code
    assert marker.exists() is (family != "pm-0225")
    if family == "pm-0225":
        marker_line = b"HFC_CONFIG_PLAN_DENY "
        line = next(line for line in child.stderr.splitlines() if line.startswith(marker_line))
        assert json.loads(line[len(marker_line):]) == {
            "reason": "unbound-local-source",
            "source": "unbound.py",
            "stage": "exec",
        }
    # The parent can still write outside the child's HOME and launch a process.
    parent_marker = tmp_path / "parent-unaffected"
    parent_marker.touch()
    assert parent_marker.exists()
    assert subprocess.run([sys.executable, "-I", "-c", "pass"], timeout=10).returncode == 0


@pytest.mark.parametrize("family", ["pm-0224", "pm-0225"])
def test_private_new_family_compiles_verified_source_instead_of_cached_code(tmp_path, family):
    import py_compile

    root, home = tmp_path / "source", tmp_path / "home"
    root.mkdir()
    home.mkdir()
    marker = home / "cached-code-executed"
    source = root / "bound.py"
    cached = "from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n"
    source.write_text(cached)
    timestamp = source.stat()
    py_compile.compile(str(source), doraise=True)
    # Same mtime/size makes the malicious timestamp cache valid to Python;
    # only the reviewed .py bytes should be used by the newer planner.
    source.write_text("#" + "x" * (len(cached.encode()) - 2) + "\n")
    os.utime(source, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
    (home / "request.json").write_text(json.dumps({
        "real_home": str(tmp_path / "real"), "family": family,
        "sources": {"bound.py": hashlib.sha256(source.read_bytes()).hexdigest()},
    }))
    setup = plugin_config_bytes._CHILD.split(
        "for name,digest in request['sources'].items():", 1
    )[0]
    child = subprocess.run(
        [sys.executable, "-I", "-c", setup + "\nsys.path.insert(0,str(root))\nimport bound\n",
         str(root), str(home)],
        env={"HOME": str(home), "HERMES_HOME": str(home)},
        capture_output=True, timeout=30,
    )
    assert child.returncode == 0
    assert marker.exists() is (family == "pm-0224")
