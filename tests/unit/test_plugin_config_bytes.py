"""Byte ownership at the official-enable boundary; config semantics are not receipts."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
import yaml

from hermes_feishu_card.install import plugin


@pytest.fixture
def planned(tmp_path, monkeypatch):
    root, home = tmp_path / "checkout", tmp_path / "home"
    root.mkdir()
    home.mkdir()
    original = b"# original\r\nplugins: {}\r\nother: keep\r\n"
    (home / "config.yaml").write_bytes(original)
    binding = plugin.HermesRuntimeBinding(
        root,
        Path(sys.executable),
        Path(sys.executable),
        "sha256:" + "a" * 64,
        home,
        home / "config.yaml",
        root,
        root,
    )
    preimage = plugin.prepare_plugin_config(binding)
    expected, _ = plugin._expected_enabled_config(yaml.safe_load(original))
    official = yaml.safe_dump(expected).encode()
    # This is a deterministic injected process port, not a production serializer.
    monkeypatch.setattr(
        plugin,
        "_plan_enabled_config_bytes",
        lambda *a: SimpleNamespace(
            postimage=official,
            verify_source=lambda: None,
            matches_expected=lambda expected: yaml.safe_load(official) == expected,
        ),
        raising=False,
    )
    return binding, preimage, original, official


@pytest.mark.parametrize("outcome", ["exception", "nonzero", "success", "journal"])
def test_same_mapping_concurrent_bytes_are_never_claimed(planned, monkeypatch, outcome):
    binding, preimage, _, official = planned
    changed = b"# concurrent user comment\r\n" + official.replace(b"\n", b"\r\n")
    assert yaml.safe_load(changed) == yaml.safe_load(official)

    def enable(candidate):
        candidate.config_path.write_bytes(official)
        candidate.config_path.write_bytes(changed)
        if outcome == "exception":
            raise OSError("official failed after concurrent comment edit")
        return 1 if outcome == "nonzero" else 0

    monkeypatch.setattr(plugin, "_run_official_enable", enable)
    journal = Mock(side_effect=OSError("journal failure"))
    if outcome == "journal":
        monkeypatch.setattr(plugin, "_rewrite_private_journal", journal)
    with pytest.raises(plugin.PluginConfigRefused):
        plugin.enable_plugin(binding, preimage)
    assert binding.config_path.read_bytes() == changed
    assert preimage.backup_path.is_file()
    if outcome == "journal":
        journal.assert_not_called()  # Reject before the bytes become ownership.


@pytest.mark.parametrize("later", ["journal", "restore"])
def test_byte_edit_after_verified_enable_refuses_later_restore(
    planned, monkeypatch, later
):
    binding, preimage, _, official = planned
    changed = b"# concurrent after write\n" + official

    def enable(candidate):
        candidate.config_path.write_bytes(official)
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", enable)
    if later == "journal":

        def fail_journal(*args, **kwargs):
            binding.config_path.write_bytes(changed)
            raise OSError("journal failed after concurrent edit")

        monkeypatch.setattr(plugin, "_rewrite_private_journal", fail_journal)
        with pytest.raises(plugin.PluginConfigRefused, match="changed"):
            plugin.enable_plugin(binding, preimage)
    else:
        ownership = plugin.enable_plugin(binding, preimage)
        binding.config_path.write_bytes(changed)
        with pytest.raises(plugin.PluginConfigRefused, match="changed"):
            plugin.restore_plugin_config(binding, preimage, ownership)
    assert binding.config_path.read_bytes() == changed
    assert preimage.backup_path.is_file()


def test_non_alias_disabled_entry_is_preserved():
    # Official entrypoint name/key/leaf are all hyphenated. An underscore name
    # is another user selection, not HFC's alias to remove.
    before = {"plugins": {"disabled": ["hermes_feishu_card", "keep"]}}
    expected, enabled = plugin._expected_enabled_config(before)
    assert not enabled
    assert expected["plugins"]["disabled"] == ["hermes_feishu_card", "keep"]


def test_config_plan_allows_only_the_writer_schema_stamp():
    from hermes_feishu_card.install.plugin_config_bytes import ConfigBytesPlan

    root = Path("/verified/writer")
    expected = {
        "plugins": {"enabled": ["hermes-feishu-card"], "disabled": []},
        "other": "keep",
    }
    plan = ConfigBytesPlan(
        b"plugins: {enabled: [hermes-feishu-card], disabled: []}\nother: keep\n_config_version: 51\n",
        root,
        "pm-0224",
        51,
    )
    assert plan.matches_expected(expected)
    assert plan.matches_expected({**expected, "_config_version": 50})
    assert plan.matches_expected({**expected, "_config_version": 51})
    assert not plan.matches_expected({**expected, "_config_version": 52})
    assert not plan.matches_expected({**expected, "_config_version": "51"})
    assert not plan.matches_expected({**expected, "other": "changed"})
    assert not plan.matches_expected({**expected, "plugins": {"enabled": [], "disabled": []}})


def test_config_plan_rejects_a_stamp_that_did_not_come_from_its_writer():
    from hermes_feishu_card.install.plugin_config_bytes import ConfigBytesPlan

    expected = {"plugins": {}, "other": "keep"}
    plan = ConfigBytesPlan(
        b"plugins: {}\nother: keep\n_config_version: 52\n",
        Path("/verified/writer"),
        "pm-0224",
        51,
    )
    assert not plan.matches_expected(expected)
    assert not plan.matches_expected({**expected, "_config_version": 52})


def test_unknown_writer_refuses_before_any_child(tmp_path, monkeypatch):
    from hermes_feishu_card.install import plugin_config_bytes as planner

    root, home, state = tmp_path / "root", tmp_path / "home", tmp_path / "state"
    for directory in (root, home, state):
        directory.mkdir()
    child = Mock(side_effect=AssertionError("unknown writer must not execute"))
    monkeypatch.setattr(planner.subprocess, "run", child)
    with pytest.raises(planner.ConfigBytesRefused, match="contract is unknown"):
        planner.plan_enabled_bytes(
            root=root,
            python=Path(sys.executable),
            home=home,
            state_dir=state,
            before=b"plugins: {}\n",
            desired={},
            enabled_before=False,
        )
    child.assert_not_called()
    assert not list(state.iterdir())


def test_private_shadow_modes_cleanup_and_stderr_redaction(tmp_path, monkeypatch):
    from hermes_feishu_card.install import plugin_config_bytes as planner
    import stat
    import subprocess

    root, home, state = tmp_path / "root", tmp_path / "home", tmp_path / "state"
    for directory in (root, home, state):
        directory.mkdir()
    before = b"private: SYNTHETIC-CONFIG-SECRET\nplugins: {}\n"
    (home / "config.yaml").write_bytes(before)
    monkeypatch.setattr(planner, "verify_family", lambda *args: None)

    def failed_child(argv, **kwargs):
        shadow = Path(argv[-1])
        if os.name != "nt":
            assert stat.S_IMODE(shadow.stat().st_mode) == 0o700
            assert stat.S_IMODE(shadow.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE((shadow / "config.yaml").stat().st_mode) == 0o600
            assert stat.S_IMODE((shadow / "request.json").stat().st_mode) == 0o600
        request = json.loads((shadow / "request.json").read_text())
        assert request["family"] in planner.FAMILIES
        assert request["sources"] == planner.FAMILIES[request["family"]]
        assert kwargs["env"] == planner.isolated_child_env(
            shadow, temp_dir=shadow / "tmp"
        )
        assert "SYNTHETIC-CONFIG-SECRET" not in repr(argv)
        return subprocess.CompletedProcess(argv, 73, stderr=b"SYNTHETIC-STDERR-SECRET")

    monkeypatch.setattr(planner.subprocess, "run", failed_child)
    with pytest.raises(planner.ConfigBytesRefused) as caught:
        planner.plan_enabled_bytes(
            root=root,
            python=Path(sys.executable),
            home=home,
            state_dir=state,
            before=before,
            desired={},
            enabled_before=False,
        )
    assert "SECRET" not in str(caught.value)
    assert not list(state.iterdir())
    assert (home / "config.yaml").read_bytes() == before
    assert "SYNTHETIC" not in repr(planner.ConfigBytesPlan(before, root, "fixed"))


def test_isolated_child_environment_is_bounded_and_windows_home_safe(tmp_path, monkeypatch):
    from hermes_feishu_card.install import plugin_config_bytes as planner

    home = tmp_path / "home with spaces" / "用户"
    root = tmp_path / "checkout with spaces"
    monkeypatch.setenv("HFC_PRIVATE_SECRET", "do-not-inherit")
    monkeypatch.setattr(planner.os, "name", "nt")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("WINDIR", r"C:\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setenv("PATH", r"C:\Windows\System32")
    monkeypatch.setenv("TEMP", r"C:\private-temp")
    monkeypatch.setenv("TMP", r"C:\private-temp")
    environment = planner.isolated_child_env(
        home, install_root=root, temp_dir=home / "tmp", include_path=True
    )
    assert environment["HOME"] == environment["HERMES_HOME"] == str(home)
    assert environment["USERPROFILE"] == str(home)
    assert environment["HERMES_INSTALL_ROOT"] == str(root)
    assert environment["TEMP"] == environment["TMP"] == str(home / "tmp")
    assert environment["SYSTEMROOT"] == environment["WINDIR"] == r"C:\Windows"
    assert environment["COMSPEC"] == r"C:\Windows\System32\cmd.exe"
    assert environment["PATH"] == r"C:\Windows\System32"
    assert "HFC_PRIVATE_SECRET" not in environment


def test_private_child_neutralizes_platform_version_probe(tmp_path):
    from hermes_feishu_card.install import plugin_config_bytes as planner

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "request.json").write_text(json.dumps({
        "real_home": str(tmp_path / "real"),
        "family": "pm-0224",
        "sources": {},
        "desired": {},
        "enabled_before": False,
    }))
    setup = planner._CHILD.split("root=Path(sys.argv[1])", 1)[0]
    setup = setup.replace("sys.platform=='win32'", "True")
    probe = setup + "\nassert platform._syscmd_ver('a','b','c') == ('a','b','c')\n"
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(tmp_path), str(shadow)],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_git_audit_accepts_only_the_exact_bound_windows_argv(tmp_path):
    from hermes_feishu_card.install import plugin_config_bytes as planner

    root, home = tmp_path / "checkout with spaces", tmp_path / "shadow"
    root.mkdir()
    home.mkdir()
    git = r"C:\Program Files\Git\cmd\git.exe"
    (home / "request.json").write_text(json.dumps({
        "real_home": str(tmp_path / "real"),
        "family": "pm-0224",
        "sources": {},
        "git": git,
        "desired": {},
        "enabled_before": False,
    }))
    setup = planner._CHILD.split("for name,digest in request['sources'].items():", 1)[0]
    setup = setup.replace(
        "and sys.platform=='win32' and isinstance(command,str)",
        "and isinstance(command,str)",
    )
    expected = [git, "-C", str(root), "rev-parse", "HEAD"]
    accepted = setup + (
        "\nscope={'__name__':'hermes_cli.boot_bootstrap','audit':audit,"
        "'subprocess':subprocess,'root':root,'expected':" + repr(expected) + "}\n"
        "exec(\"def read_git_head():\\n"
        "    audit('subprocess.Popen',(expected[0],expected,str(root),{}))\\n"
        "    audit('subprocess.Popen',(expected[0],subprocess.list2cmdline(expected),str(root),{}))\",scope)\n"
        "scope['read_git_head']()\n(home/'accepted').write_text('yes')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", accepted, str(root), str(home)],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert (home / "accepted").read_text() == "yes"

    refused = setup + (
        "\nscope={'__name__':'hermes_cli.boot_bootstrap','audit':audit,"
        "'subprocess':subprocess,'root':root,'expected':" + repr(expected) + "}\n"
        "exec(\"def read_git_head():\\n"
        "    audit('subprocess.Popen',(expected[0],expected+['--format=evil'],str(root),{}))\",scope)\n"
        "scope['read_git_head']()\n(home/'continued').write_text('bad')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", refused, str(root), str(home)],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 73
    assert not (home / "continued").exists()


@pytest.mark.parametrize("family", ["pm-0224", "pm-0225"])
def test_profile_guard_hard_stops_caught_forbidden_call(tmp_path, family):
    """A real child must not resume after an upstream catch disables profiling."""
    import subprocess
    from hermes_feishu_card.install import plugin_config_bytes as planner

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "request.json").write_text(
        json.dumps({
            "real_home": str(tmp_path / "real"),
            "family": family,
            "sources": planner.FAMILIES[family],
            "desired": {},
            "enabled_before": False,
        })
    )
    sentinel = shadow / "forbidden-body"
    # Exercise the actual installed guard prefix, before any upstream import.
    guard = planner._CHILD.split("for name,digest in request['sources'].items():", 1)[0]
    probe = (
        guard
        + """
scope={'__name__':'providers','sentinel':home/'forbidden-body'}
exec("def _discover_providers():\\n    sentinel.write_text('executed')",scope)
try:
    scope['_discover_providers']()
except BaseException:
    pass
scope['_discover_providers']()
(home/'continued').write_text('continued')
"""
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(tmp_path), str(shadow)],
        env=planner.isolated_child_env(shadow),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 73
    assert not sentinel.exists()
    assert not (shadow / "continued").exists()
    marker = b"HFC_CONFIG_PLAN_DENY "
    line = next(line for line in result.stderr.splitlines() if line.startswith(marker))
    assert json.loads(line[len(marker):]) == {"reason": "plugin-load", "stage": "profile"}


def test_source_binding_failure_after_enable_preserves_unowned_bytes(
    planned, monkeypatch
):
    binding, preimage, _, official = planned
    checks = 0

    def verify():
        nonlocal checks
        checks += 1
        if checks == 2:
            from hermes_feishu_card.install.plugin_config_bytes import (
                ConfigBytesRefused,
            )

            raise ConfigBytesRefused("official config writer source changed")

    monkeypatch.setattr(
        plugin,
        "_plan_enabled_config_bytes",
        lambda *a: SimpleNamespace(
            postimage=official,
            verify_source=verify,
            matches_expected=lambda expected: yaml.safe_load(official) == expected,
        ),
    )

    def enable(candidate):
        candidate.config_path.write_bytes(b"# concurrent source change\r\n" + official)
        return 0

    monkeypatch.setattr(plugin, "_run_official_enable", enable)
    with pytest.raises(plugin.PluginConfigRefused, match="writer contract is unknown"):
        plugin.enable_plugin(binding, preimage)
    assert binding.config_path.read_bytes().startswith(
        b"# concurrent source change\r\n"
    )
    assert preimage.backup_path.is_file()
