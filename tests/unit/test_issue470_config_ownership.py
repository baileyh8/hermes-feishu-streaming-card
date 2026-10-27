"""Configuration recovery owns only the verified HFC admission delta."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from hermes_feishu_card.install import legacy_observer, plugin, plugin_config_bytes, plugin_config_delta
from test_legacy_rebind import admission  # noqa: F401


@pytest.mark.parametrize("addition", [
    b"provider: new\n",
    b"display:\n  platforms:\n    feishu:\n      show_reasoning: true\n",
    b"aliases:\n  fast: synthetic-model\n",
])
def test_unrelated_edits_are_observable_and_survive_restore(admission, addition):
    binding, original = admission
    marker = binding.checkout_root / legacy_observer.MARKER
    binding.config_path.write_bytes(binding.config_path.read_bytes() + addition)
    before = binding.config_path.read_bytes()
    marker_before = marker.read_bytes()
    assert legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == before
    assert marker.read_bytes() == marker_before
    legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == original + addition


def test_other_plugin_selection_and_entry_survive_restore(admission):
    binding, _ = admission
    current = binding.config_path.read_bytes()
    current = current.replace(b"  - hermes-feishu-card\n", b"  - hermes-feishu-card\n  - another-plugin\n")
    current = current.replace(b"    hermes-feishu-card:\n", b"    another-plugin:\n      flag: keep\n    hermes-feishu-card:\n")
    binding.config_path.write_bytes(current)
    assert legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    restored = yaml.safe_load(binding.config_path.read_bytes())
    assert restored["plugins"]["enabled"] == ["another-plugin"]
    assert restored["plugins"]["entries"]["another-plugin"]["flag"] == "keep"


@pytest.mark.parametrize("suffix", [
    b"plugins: {}\n", b"x: &a 1\ny: *a\n", b"x: !unknown value\n",
    b"1: value\n", b"other:\n  <<: {x: 1}\n",
])
def test_ambiguous_yaml_refused_without_writes(admission, suffix):
    binding, _ = admission
    altered = binding.config_path.read_bytes() + suffix
    binding.config_path.write_bytes(altered)
    with pytest.raises(ValueError):
        legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == altered


def test_managed_value_change_refused_without_writes(admission):
    binding, _ = admission
    changed = binding.config_path.read_bytes().replace(b"allow_tool_override: false", b"allow_tool_override: true")
    binding.config_path.write_bytes(changed)
    with pytest.raises(ValueError):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == changed


def _make_legacy_record(binding):
    marker = binding.checkout_root / legacy_observer.MARKER
    record = json.loads(marker.read_bytes())
    record.pop("config_delta")
    marker.write_text(json.dumps(record, sort_keys=True) + "\n")
    journal = binding.hermes_home / ".hermes_feishu_card/install" / (record["plugin_config"]["config_backup_id"] + ".json")
    value = json.loads(journal.read_bytes())
    value.pop("config_delta")
    journal.write_text(json.dumps(value, sort_keys=True) + "\n")


def _tree_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_legacy_replay_is_private_read_only_and_restores_unrelated_edit(admission, tmp_path, monkeypatch):
    binding, original = admission
    _make_legacy_record(binding)
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"provider: keep\n")
    home_before = _tree_bytes(binding.hermes_home)
    marker = binding.checkout_root / legacy_observer.MARKER
    marker_before = marker.read_bytes()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(legacy_observer.tempfile, "tempdir", str(scratch))
    evidence = legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    assert evidence and evidence[0]["config_delta"]["schema"] == 2
    assert _tree_bytes(binding.hermes_home) == home_before
    assert marker.read_bytes() == marker_before
    assert not list(scratch.iterdir())
    legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == original + b"provider: keep\n"
    assert not list(scratch.iterdir())


def test_legacy_replay_rejects_unverified_historical_postimage(admission, monkeypatch):
    binding, _ = admission
    _make_legacy_record(binding)
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"provider: keep\n")
    current = _tree_bytes(binding.hermes_home)
    original_plan = plugin._plan_enabled_config_bytes
    def wrong_plan(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        return replace(plan, postimage=plan.postimage + b"# different\n")
    monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", wrong_plan)
    with pytest.raises(ValueError, match="postimage"):
        legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    assert _tree_bytes(binding.hermes_home) == current


@pytest.mark.parametrize("kind", ["config", "same-bytes-inode", "marker", "journal", "preimage", "stage"])
def test_restore_compare_and_swap_refuses_staged_concurrent_changes(admission, tmp_path, monkeypatch, kind):
    from hermes_feishu_card.install import posix_transaction as io
    binding, _ = admission
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"provider: keep\n")
    marker = binding.checkout_root / legacy_observer.MARKER
    record = json.loads(marker.read_bytes())
    state = binding.hermes_home / ".hermes_feishu_card/install"
    key = record["plugin_config"]["config_backup_id"]
    journal, backup = state / (key + ".json"), state / (key + ".yaml")
    initial = binding.config_path.read_bytes()
    original_stage = io.stage_file
    def race(*args, **kwargs):
        stage = original_stage(*args, **kwargs)
        if kind == "config":
            binding.config_path.write_bytes(initial + b"concurrent: keep\n")
        elif kind == "same-bytes-inode":
            other = binding.hermes_home / "replacement.yaml"
            other.write_bytes(initial)
            os.replace(other, binding.config_path)
        elif kind == "marker":
            marker.write_bytes(marker.read_bytes() + b" ")
        elif kind == "journal":
            journal.write_bytes(journal.read_bytes() + b" ")
        elif kind == "preimage":
            backup.write_bytes(backup.read_bytes() + b"# concurrent\n")
        else:
            stage.path.write_bytes(b"unexpected stage contents\n")
        return stage
    monkeypatch.setattr(io, "stage_file", race)
    with pytest.raises(ValueError):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == (initial + b"concurrent: keep\n" if kind == "config" else initial)
    assert marker.exists() and journal.exists() and backup.exists()
    assert not list(binding.hermes_home.glob(".config.yaml.*.tmp"))


@pytest.mark.parametrize("pre", [
    {"plugins": {"enabled": ["alpha"], "disabled": ["one", "hermes-feishu-card", "two"],
                 "entries": {"hermes-feishu-card": {"allow_tool_override": True, "custom": "keep"}}}},
    {"plugins": {"enabled": ["hermes-feishu-card"], "entries": {"hermes-feishu-card": {}}}},
    {"plugins": {"entries": {}}},
    {"model": "unchanged"},
])
def test_reverse_delta_keeps_other_plugin_and_original_owned_values(pre):
    before = yaml.safe_dump(pre, sort_keys=False).encode()
    expected, _ = plugin._expected_enabled_config(pre, preserve_enabled_lists=True)
    # The official selection writer preserves enabled lists for an already enabled plugin.
    if "hermes-feishu-card" not in pre.get("plugins", {}).get("enabled", []):
        expected, _ = plugin._expected_enabled_config(pre)
    post = yaml.safe_dump(expected, sort_keys=False).encode()
    receipt = plugin_config_delta.make_receipt(before, post)
    current = yaml.safe_load(post)
    current["plugins"]["enabled"].append("another")
    current["plugins"]["entries"]["another"] = {"flag": "keep"}
    changed = yaml.safe_dump(current, sort_keys=False).encode()
    restored = yaml.safe_load(plugin_config_delta.restore_bytes(before, changed, receipt))
    assert "another" in restored["plugins"]["enabled"]
    assert restored["plugins"]["entries"]["another"] == {"flag": "keep"}
    old_plugins = pre.get("plugins", {})
    assert ("hermes-feishu-card" in restored["plugins"].get("enabled", [])) == ("hermes-feishu-card" in old_plugins.get("enabled", []))
    assert restored["plugins"].get("disabled", []) == old_plugins.get("disabled", [])
    assert restored["plugins"]["entries"].get("hermes-feishu-card") == old_plugins.get("entries", {}).get("hermes-feishu-card")


def test_yaml_boolean_is_not_interchangeable_with_zero(admission):
    binding, _ = admission
    altered = binding.config_path.read_bytes().replace(b"allow_tool_override: false", b"allow_tool_override: 0")
    binding.config_path.write_bytes(altered)
    with pytest.raises(ValueError):
        legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)


@pytest.mark.parametrize("addition", [b"", b"provider: keep\n"])
def test_unreceipted_return_to_preimage_is_not_a_restore(admission, addition):
    binding, original = admission
    changed = original + addition
    binding.config_path.write_bytes(changed)
    with pytest.raises(ValueError):
        legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    with pytest.raises(ValueError):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == changed


def test_receipted_config_restore_survives_later_source_phase(admission):
    binding, original = admission
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"provider: keep\n")
    evidence = legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    assert evidence is not None
    plugin.restore_plugin_config(binding, evidence[2], evidence[3])
    assert binding.config_path.read_bytes() == original + b"provider: keep\n"
    assert legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    assert legacy_observer.observer_status(binding.checkout_root, home=binding.hermes_home)["reason"] == "restore_incomplete"
    legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == original + b"provider: keep\n"
    assert not list((binding.hermes_home / ".hermes_feishu_card/install").glob("*.restore.json"))


def test_interrupted_stage_can_be_retried_from_bound_receipt(admission, monkeypatch):
    from hermes_feishu_card.install import posix_transaction as io
    binding, original = admission
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"provider: keep\n")
    original_replace = io.replace_stage
    def fail(*args, **kwargs):
        raise OSError("synthetic publish failure")
    monkeypatch.setattr(io, "replace_stage", fail)
    with pytest.raises(OSError):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert list((binding.hermes_home / ".hermes_feishu_card/install").glob("*.restore.json"))
    monkeypatch.setattr(io, "replace_stage", original_replace)
    legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == original + b"provider: keep\n"


@pytest.mark.parametrize("changed", ["config", "marker", "journal", "backup"])
def test_final_evidence_check_runs_after_stage_reread(admission, monkeypatch, changed):
    from hermes_feishu_card.install import posix_transaction as io
    binding, _ = admission
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"provider: keep\n")
    marker = binding.checkout_root / legacy_observer.MARKER
    record = json.loads(marker.read_bytes())
    state = binding.hermes_home / ".hermes_feishu_card/install"
    key = record["plugin_config"]["config_backup_id"]
    paths = {"config": binding.config_path, "marker": marker,
             "journal": state / (key + ".json"), "backup": state / (key + ".yaml")}
    original = paths[changed].read_bytes()
    reads = []
    read = io.read_file_state
    def race(target, **kwargs):
        result = read(target, **kwargs)
        if isinstance(target, str) and target.startswith(".config.yaml."):
            reads.append(target)
            if len(reads) == 2:
                paths[changed].write_bytes(original + b"# concurrent\n")
        return result
    monkeypatch.setattr(io, "read_file_state", race)
    with pytest.raises(ValueError):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert len(reads) == 2
    assert paths[changed].read_bytes() == original + b"# concurrent\n"
    assert marker.exists() and paths["journal"].exists() and paths["backup"].exists()


def test_pending_receipt_inode_replacement_cannot_be_reclaimed(admission, monkeypatch):
    from hermes_feishu_card.install import posix_transaction as io
    binding, _ = admission
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"provider: keep\n")
    original_replace = io.replace_stage
    monkeypatch.setattr(io, "replace_stage", lambda *a, **k: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    monkeypatch.setattr(io, "replace_stage", original_replace)
    discard = legacy_observer._discard_pending_ticket
    def swap(binding_arg, preimage, ticket, snapshot):
        path = legacy_observer._restore_ticket_path(preimage)
        replacement = path.with_name(path.name + ".other")
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path)
        return discard(binding_arg, preimage, ticket, snapshot)
    monkeypatch.setattr(legacy_observer, "_discard_pending_ticket", swap)
    before = binding.config_path.read_bytes()
    with pytest.raises(ValueError):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == before
    assert list((binding.hermes_home / ".hermes_feishu_card/install").glob("*.restore.json"))


def test_cleanup_cannot_reclaim_same_bytes_receipt_replacement(admission, monkeypatch):
    from hermes_feishu_card.install import posix_transaction as io
    binding, _ = admission
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"provider: keep\n")
    remove = io._remove_verified_restore_evidence
    def swap(paths, identities, directories):
        ticket = next(path for path in paths if path.name.endswith(".restore.json"))
        replacement = ticket.with_name(ticket.name + ".other")
        replacement.write_bytes(ticket.read_bytes())
        os.replace(replacement, ticket)
        return remove(paths, identities, directories)
    monkeypatch.setattr(io, "_remove_verified_restore_evidence", swap)
    with pytest.raises(ValueError):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert (binding.checkout_root / legacy_observer.MARKER).exists()
    assert list((binding.hermes_home / ".hermes_feishu_card/install").glob("*.restore.json"))


def test_change_at_transaction_entry_refused_before_rename(admission, monkeypatch):
    from hermes_feishu_card.install import posix_transaction as io
    binding, _ = admission
    original = binding.config_path.read_bytes() + b"provider: keep\n"
    binding.config_path.write_bytes(original)
    replace_stage = io.replace_stage
    def race(*args, **kwargs):
        binding.config_path.write_bytes(original + b"concurrent: retained\n")
        return replace_stage(*args, **kwargs)
    monkeypatch.setattr(io, "replace_stage", race)
    with pytest.raises(ValueError):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == original + b"concurrent: retained\n"
    assert (binding.checkout_root / legacy_observer.MARKER).exists()


def test_parent_swap_during_stage_refused_without_overwrite(admission, monkeypatch):
    from hermes_feishu_card.install import posix_transaction as io
    binding, _ = admission
    current = binding.config_path.read_bytes() + b"provider: keep\n"
    binding.config_path.write_bytes(current)
    moved = binding.hermes_home.with_name("home-moved")
    original_stage = io.stage_file
    def race(*args, **kwargs):
        stage = original_stage(*args, **kwargs)
        binding.hermes_home.rename(moved)
        binding.hermes_home.mkdir()
        binding.config_path.write_bytes(b"concurrent: replacement\n")
        return stage
    monkeypatch.setattr(io, "stage_file", race)
    with pytest.raises(ValueError):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == b"concurrent: replacement\n"
    assert (moved / "config.yaml").read_bytes() == current
    assert (binding.checkout_root / legacy_observer.MARKER).exists()


def test_portable_exclusive_failure_restores_only_owned_config(admission, monkeypatch):
    from hermes_feishu_card.install import file_transaction as portable, posix_transaction as io
    binding, _ = admission
    evidence = legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    assert evidence is not None
    marker = binding.checkout_root / legacy_observer.MARKER
    paths = (binding.config_path, marker, evidence[2].journal_path, evidence[2].backup_path)
    snapshots = {path: io.read_path_snapshot(path, ValueError, "changed") for path in paths}
    directories = {path.parent: io.read_directory_identity(path.parent, ValueError, "changed") for path in paths}
    before = binding.config_path.read_bytes()
    original_write = portable._write_portable_exclusive_bytes
    calls = []
    def fail_once(fd, value, mode):
        calls.append(value)
        if len(calls) == 1:
            original_write(fd, b"partial", mode)
            raise OSError("synthetic short write")
        original_write(fd, value, mode)
    monkeypatch.setattr(portable, "_write_portable_exclusive_bytes", fail_once)
    with pytest.raises(OSError):
        legacy_observer._portable_restore_config(paths, snapshots, directories, before, b"replacement\n")
    assert binding.config_path.read_bytes() == before
    assert len(calls) == 2
    assert marker.exists() and evidence[2].journal_path.exists() and evidence[2].backup_path.exists()


def test_legacy_replay_with_pinned_official_0225_writer(tmp_path, monkeypatch):
    configured = os.environ.get("HFC_CONFIG_WRITER_0225_ROOT")
    if not configured:
        pytest.skip("HFC_CONFIG_WRITER_0225_ROOT not configured")
    source = Path(configured)
    assert subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() == "bf0203a794a07aac3ff764972ab9087baa4e4f4e"
    home, marker_root = tmp_path / "home", tmp_path / "marker"
    home.mkdir()
    marker_root.mkdir()
    state = home / ".hermes_feishu_card/install"
    state.mkdir(parents=True)
    before = b"plugins: {}\nmodel: original\n_config_version: 50\n"
    desired, enabled = plugin._expected_enabled_config(plugin._load_config_mapping(before))
    plan = plugin_config_bytes._plan_enabled_bytes(root=source, python=Path(sys.executable), home=home,
        state_dir=tmp_path, before=before, desired=desired, enabled_before=enabled, historical_migration=False)
    key = "hfc-config-preimage-" + "a" * 32
    ownership = plugin.PluginOwnership(enabled, True, plugin._sha256(before), plugin._sha256(plan.postimage), key, plugin._sha256(before))
    (state / (key + ".yaml")).write_bytes(before)
    (state / (key + ".json")).write_text(json.dumps({"protocol": "hfc-plugin-config-preimage-v1",
        "phase": "installed", **ownership.sanitized()}, sort_keys=True) + "\n")
    record = {"protocol": legacy_observer.PROTOCOL, "home": str(home),
              "python_identity": "sha256:" + "a" * 64, "plugin_config": ownership.sanitized()}
    marker = marker_root / legacy_observer.MARKER
    marker.write_text(json.dumps(record, sort_keys=True) + "\n")
    config = home / "config.yaml"
    config.write_bytes(plan.postimage + b"provider: changed\n")
    binding = plugin.HermesRuntimeBinding(source, Path(sys.executable), Path(sys.executable),
        record["python_identity"], home, config, source, source)
    monkeypatch.setattr(plugin, "resolve_runtime_binding", lambda **_: binding)
    old_home, old_marker = _tree_bytes(home), marker.read_bytes()
    evidence = legacy_observer.inspect(marker_root, home=home)
    assert evidence and evidence[0]["config_delta"]["schema"] == 2
    assert _tree_bytes(home) == old_home and marker.read_bytes() == old_marker


def test_multiline_managed_selection_cannot_remove_following_user_key():
    before = b"model: old\n"
    post = (b"model: old\nplugins:\n  enabled: [hermes-feishu-card]\n  disabled: []\n"
            b"  entries:\n    hermes-feishu-card:\n      allow_tool_override: false\n")
    current = (b"model: old\nplugins:\n  enabled:\n    - |-\n      hermes-feishu-card\n"
               b"  custom_option: keep-me\n  disabled: []\n  entries:\n"
               b"    hermes-feishu-card:\n      allow_tool_override: false\n  isolation: process\n")
    receipt = plugin_config_delta.make_receipt(before, post)
    with pytest.raises(ValueError, match="multiline"):
        plugin_config_delta.restore_bytes(before, current, receipt)


def test_unrelated_multiline_scalar_and_next_key_survive_restore():
    before = b"model: old\n"
    post = (b"model: old\nplugins:\n  enabled: [hermes-feishu-card]\n  disabled: []\n"
            b"  entries:\n    hermes-feishu-card:\n      allow_tool_override: false\n")
    current = (b"model: old\nplugins:\n  enabled: [hermes-feishu-card, other]\n  disabled: []\n"
               b"  entries:\n    hermes-feishu-card:\n      allow_tool_override: false\n"
               b"  custom_option: |-\n    keep this text\nnext_key: keep-too\n")
    receipt = plugin_config_delta.make_receipt(before, post)
    restored = plugin_config_delta.restore_bytes(before, current, receipt)
    assert b"  custom_option: |-\n    keep this text\nnext_key: keep-too\n" in restored
    assert yaml.safe_load(restored)["plugins"]["enabled"] == ["other"]


def test_unrelated_enabled_order_is_current_order_after_config_edit():
    original = {"plugins": {"enabled": ["zeta", "alpha"]}, "model": "old"}
    before = yaml.safe_dump(original, sort_keys=False).encode()
    desired, _ = plugin._expected_enabled_config(original)
    post = yaml.safe_dump(desired, sort_keys=False).encode()
    current = post + b"provider: newer\n"
    receipt = plugin_config_delta.make_receipt(before, post)
    restored = plugin_config_delta.restore_bytes(before, current, receipt)
    result = yaml.safe_load(restored)
    assert result["plugins"]["enabled"] == ["alpha", "zeta"]
    assert result["provider"] == "newer"


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_original_section_without_final_newline_preserves_later_key(newline):
    before = b"plugins: {}"
    post = newline.join([b"plugins:", b"  enabled: [hermes-feishu-card]", b"  disabled: []",
                         b"  entries:", b"    hermes-feishu-card:",
                         b"      allow_tool_override: false", b""])
    current = post + b"provider: keep" + newline
    receipt = plugin_config_delta.make_receipt(before, post)
    assert plugin_config_delta.restore_bytes(before, current, receipt) == b"plugins: {}" + newline + b"provider: keep" + newline


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_original_disabled_item_without_final_newline_keeps_next_entry(newline):
    before = newline.join([b"plugins:", b"  disabled:", b"  - hermes-feishu-card"])
    post = newline.join([b"plugins:", b"  enabled:", b"  - hermes-feishu-card", b"  disabled: []",
                         b"  entries:", b"    hermes-feishu-card:", b"      allow_tool_override: false", b""])
    current = post.replace(b"  disabled: []" + newline,
                           b"  disabled:" + newline + b"  - other" + newline)
    current = current.replace(b"    hermes-feishu-card:" + newline,
                              b"    other:" + newline + b"      keep: yes" + newline
                              + b"    hermes-feishu-card:" + newline)
    receipt = plugin_config_delta.make_receipt(before, post)
    restored = plugin_config_delta.restore_bytes(before, current, receipt)
    parsed = yaml.safe_load(restored)
    assert parsed["plugins"]["disabled"] == ["other", "hermes-feishu-card"]
    assert parsed["plugins"]["entries"]["other"]["keep"] is True


@pytest.mark.parametrize("kind", ["extra-entry", "same-bytes-inode", "journal-publication"])
def test_fresh_receipt_never_claims_changed_official_postimage(admission, monkeypatch, kind):
    binding, _ = admission
    legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    original_enable = plugin.enable_plugin
    original_publish = plugin.mark_plugin_config_installed
    changed = []
    def modify():
        before = binding.config_path.read_bytes()
        if kind == "same-bytes-inode":
            replacement = binding.hermes_home / "replacement.yaml"
            replacement.write_bytes(before)
            os.replace(replacement, binding.config_path)
            changed.append(before)
        else:
            after = before.replace(b"    hermes-feishu-card:\n", b"    unrelated-plugin:\n      keep: yes\n    hermes-feishu-card:\n")
            assert after != before
            binding.config_path.write_bytes(after)
            changed.append(after)
    def enable(*args, **kwargs):
        owner = original_enable(*args, **kwargs)
        if kind != "journal-publication":
            modify()
        return owner
    def publish(*args, **kwargs):
        result = original_publish(*args, **kwargs)
        if kind == "journal-publication":
            modify()
        return result
    monkeypatch.setattr(plugin, "enable_plugin", enable)
    monkeypatch.setattr(plugin, "mark_plugin_config_installed", publish)
    with pytest.raises(ValueError, match="postimage changed"):
        legacy_observer.prepare(binding)
    assert binding.config_path.read_bytes() == changed[0]
    assert not (binding.checkout_root / legacy_observer.MARKER).exists()
    assert list((binding.hermes_home / ".hermes_feishu_card/install").glob("*.yaml"))


@pytest.mark.parametrize("kind", ["enable-journal-bytes", "enable-journal-inode", "enable-backup", "install-journal", "install-backup"])
def test_fresh_enable_preserves_concurrent_evidence_changes(admission, monkeypatch, kind):
    binding, _ = admission
    legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    state = binding.hermes_home / ".hermes_feishu_card/install"
    observed = []
    def mutate():
        target = next(state.glob("*.yaml" if kind.endswith("backup") else "*.json"))
        if kind == "enable-journal-inode":
            other = target.with_name(target.name + ".other")
            other.write_bytes(target.read_bytes())
            os.replace(other, target)
        else:
            target.write_bytes(target.read_bytes() + b"\n# concurrent\n")
        observed.append((target, target.read_bytes(), target.stat().st_ino))
    original_enable = plugin._run_official_enable
    original_publish = plugin.mark_plugin_config_installed
    def enable(*args, **kwargs):
        result = original_enable(*args, **kwargs)
        if kind.startswith("enable-"):
            mutate()
        return result
    def publish(*args, **kwargs):
        if kind.startswith("install-"):
            mutate()
        return original_publish(*args, **kwargs)
    monkeypatch.setattr(plugin, "_run_official_enable", enable)
    monkeypatch.setattr(plugin, "mark_plugin_config_installed", publish)
    with pytest.raises(ValueError):
        legacy_observer.prepare(binding)
    target, contents, inode = observed[0]
    assert target.read_bytes() == contents and target.stat().st_ino == inode
    assert not (binding.checkout_root / legacy_observer.MARKER).exists()


def test_receipt_rejects_unrelated_official_output_change():
    before = b"plugins: {}\nmodel: old\n"
    post = (b"plugins:\n  enabled: [hermes-feishu-card]\n  disabled: []\n  entries:\n"
            b"    hermes-feishu-card:\n      allow_tool_override: false\n"
            b"    other:\n      keep: true\nmodel: old\n")
    with pytest.raises(ValueError, match="unrelated"):
        plugin_config_delta.make_receipt(before, post)


def test_invalid_postimage_digest_refuses_even_if_journal_matches(admission):
    binding, _ = admission
    marker = binding.checkout_root / legacy_observer.MARKER
    record = json.loads(marker.read_bytes())
    record["config_delta"]["post_plugins_sha256"] = "not-a-digest"
    marker.write_text(json.dumps(record, sort_keys=True) + "\n")
    journal = binding.hermes_home / ".hermes_feishu_card/install" / (record["plugin_config"]["config_backup_id"] + ".json")
    value = json.loads(journal.read_bytes())
    value["config_delta"] = record["config_delta"]
    journal.write_text(json.dumps(value, sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)


@pytest.mark.parametrize("schema", [2.0, "2", True, None])
def test_receipt_schema_requires_exact_integer(admission, schema):
    binding, _ = admission
    marker = binding.checkout_root / legacy_observer.MARKER
    record = json.loads(marker.read_bytes())
    record["config_delta"]["schema"] = schema
    marker.write_text(json.dumps(record, sort_keys=True) + "\n")
    journal = binding.hermes_home / ".hermes_feishu_card/install" / (record["plugin_config"]["config_backup_id"] + ".json")
    value = json.loads(journal.read_bytes())
    value["config_delta"] = record["config_delta"]
    journal.write_text(json.dumps(value, sort_keys=True) + "\n")
    with pytest.raises(ValueError):
        legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)


def test_legacy_rebind_preview_replays_outside_home(admission, tmp_path, monkeypatch):
    from hermes_feishu_card.install import legacy_rebind
    binding, _ = admission
    _make_legacy_record(binding)
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"provider: keep\n")
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    old_home = _tree_bytes(binding.hermes_home)
    old_marker = (binding.checkout_root / legacy_observer.MARKER).read_bytes()
    original_plan = plugin._plan_enabled_config_bytes
    scratch_paths = []
    def observe(candidate, preimage, *args, **kwargs):
        scratch_paths.append(preimage.state_dir)
        assert preimage.state_dir != binding.hermes_home / ".hermes_feishu_card/install"
        return original_plan(candidate, preimage, *args, **kwargs)
    monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", observe)
    assert legacy_rebind.rebind(binding, apply=False)["status"] == "ready"
    assert scratch_paths and all(not path.exists() for path in scratch_paths)
    assert _tree_bytes(binding.hermes_home) == old_home
    assert (binding.checkout_root / legacy_observer.MARKER).read_bytes() == old_marker
