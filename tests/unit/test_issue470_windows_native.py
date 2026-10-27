"""Native Windows observer recovery using real exclusive file handles.

The fake official enable supplies only a deterministic postimage; every HFC
snapshot, journal, receipt, restore and cleanup operation is the real code.
"""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml

from hermes_feishu_card.install import legacy_observer, plugin


pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows exclusive-handle acceptance")


@pytest.fixture
def observer(tmp_path, monkeypatch):
    root, home = tmp_path / "checkout", tmp_path / "home"
    root.mkdir()
    home.mkdir()
    config = home / "config.yaml"
    original = b"# original CRLF\r\nplugins: {}\r\nmodel: original\r\n"
    config.write_bytes(original)
    binding = plugin.HermesRuntimeBinding(root, Path(sys.executable), Path(sys.executable),
        "sha256:" + "a" * 64, home, config, root, root)
    monkeypatch.setattr(plugin, "resolve_runtime_binding", lambda **_: binding)
    monkeypatch.setattr(plugin, "probe_plugin_entrypoint", lambda _: SimpleNamespace(status="verified"))
    def plan(_binding, _preimage, _before, desired, _enabled, **_kwargs):
        post = yaml.safe_dump(desired, sort_keys=False).encode()
        return SimpleNamespace(postimage=post, verify_source=lambda: None,
                               matches_expected=lambda expected: yaml.safe_load(post) == expected)
    def enable(_binding):
        desired, _ = plugin._expected_enabled_config(plugin._load_config_mapping(config.read_bytes()))
        config.write_bytes(yaml.safe_dump(desired, sort_keys=False).encode())
        return 0
    monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", plan)
    monkeypatch.setattr(plugin, "_run_official_enable", enable)
    legacy_observer.prepare(binding)
    return binding, original


def test_windows_exact_restore_preserves_original_bytes_and_cleans_evidence(observer):
    binding, original = observer
    legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == original
    assert not (binding.checkout_root / legacy_observer.MARKER).exists()
    assert not list((binding.hermes_home / ".hermes_feishu_card/install").glob("hfc-config-preimage-*"))


def test_windows_delta_restore_preserves_other_plugins_and_cleans_receipt(observer):
    binding, _ = observer
    current = yaml.safe_load(binding.config_path.read_bytes())
    current["plugins"]["enabled"].append("other-plugin")
    current["plugins"]["entries"]["other-plugin"] = {"setting": "preserved"}
    current["provider"] = "new-provider"
    binding.config_path.write_bytes(yaml.safe_dump(current, sort_keys=False).encode() + b"# user comment\n")
    evidence = legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    assert evidence is not None
    plugin.restore_plugin_config(binding, evidence[2], evidence[3])
    restored = binding.config_path.read_bytes()
    data = yaml.safe_load(restored)
    assert data["plugins"]["enabled"] == ["other-plugin"]
    assert data["plugins"]["entries"]["other-plugin"]["setting"] == "preserved"
    assert data["provider"] == "new-provider" and b"# user comment\n" in restored
    assert list((binding.hermes_home / ".hermes_feishu_card/install").glob("*.restore.json"))
    assert legacy_observer.inspect(binding.checkout_root, home=binding.hermes_home)
    legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert binding.config_path.read_bytes() == restored
    assert not list((binding.hermes_home / ".hermes_feishu_card/install").glob("hfc-config-preimage-*"))


@pytest.mark.parametrize("changed", ["config", "marker", "journal", "preimage"])
def test_windows_concurrent_edit_refuses_and_preserves_evidence(observer, monkeypatch, changed):
    binding, _ = observer
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"provider: keep\n")
    marker = binding.checkout_root / legacy_observer.MARKER
    record = json.loads(marker.read_bytes())
    state = binding.hermes_home / ".hermes_feishu_card/install"
    key = record["plugin_config"]["config_backup_id"]
    paths = {"config": binding.config_path, "marker": marker,
             "journal": state / (key + ".json"), "preimage": state / (key + ".yaml")}
    original = paths[changed].read_bytes()
    portable_restore = legacy_observer._portable_restore_config
    def race(*args, **kwargs):
        paths[changed].write_bytes(original + b"# concurrent\n")
        return portable_restore(*args, **kwargs)
    monkeypatch.setattr(legacy_observer, "_portable_restore_config", race)
    with pytest.raises((OSError, ValueError)):
        legacy_observer.restore(binding.checkout_root, home=binding.hermes_home)
    assert paths[changed].read_bytes() == original + b"# concurrent\n"
    assert marker.exists() and paths["journal"].exists() and paths["preimage"].exists()
