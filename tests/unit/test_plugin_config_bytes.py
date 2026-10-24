"""Byte ownership at the official-enable boundary; config semantics are not receipts."""

import json
from pathlib import Path
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
        lambda *a: SimpleNamespace(postimage=official, verify_source=lambda: None),
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
        assert stat.S_IMODE(shadow.stat().st_mode) == 0o700
        assert stat.S_IMODE(shadow.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE((shadow / "config.yaml").stat().st_mode) == 0o600
        assert stat.S_IMODE((shadow / "request.json").stat().st_mode) == 0o600
        request = json.loads((shadow / "request.json").read_text())
        assert request["family"] in planner.FAMILIES
        assert request["sources"] == planner.FAMILIES[request["family"]]
        assert kwargs["env"] == {"HOME": str(shadow), "HERMES_HOME": str(shadow)}
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
        env={"HOME": str(shadow), "HERMES_HOME": str(shadow)},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 73
    assert not sentinel.exists()
    assert not (shadow / "continued").exists()
    assert result.stderr == b"private config byte plan side effect refused\n"


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
        lambda *a: SimpleNamespace(postimage=official, verify_source=verify),
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
