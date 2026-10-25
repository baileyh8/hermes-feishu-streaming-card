"""Explicit recovery preserves unrelated config and the original admission proof."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest
import yaml

from hermes_feishu_card.install import legacy_observer, plugin
from hermes_feishu_card.install import plugin_config_bytes
from hermes_feishu_card.install.plugin_config_bytes import ConfigBytesPlan


class _ExactPlan(ConfigBytesPlan):
    def verify_source(self) -> None:
        pass


def _exact_plan(postimage: bytes) -> ConfigBytesPlan:
    return _ExactPlan(postimage, Path("/verified"), "fixed")


def _copy_fixed_support(root: Path) -> None:
    configured = os.environ.get("HFC_FIXED_TAG_SOURCE_ROOT")
    if not configured:
        pytest.skip("HFC_FIXED_TAG_SOURCE_ROOT required for launch guard")
    source = Path(configured)
    support = {"hermes_cli/main.py", "hermes_cli/__init__.py"}
    support.update(plugin_config_bytes.FAMILIES["fixed"])
    for relative in support:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / relative).read_bytes())


@pytest.fixture
def admission(tmp_path, monkeypatch):
    from hermes_feishu_card.install import posix_transaction
    if not posix_transaction._cli_dirfd_binding_supported():
        pytest.skip("observer transaction fixture requires secure directory-relative I/O")
    root, home = tmp_path / "checkout", tmp_path / "home"
    shutil.copytree(Path(__file__).parents[1] / "fixtures/hermes_decomposed", root,
                    ignore=shutil.ignore_patterns("__pycache__"))
    _copy_fixed_support(root)
    (root / "VERSION").write_text("0.22.2\n")
    home.mkdir()
    config = home / "config.yaml"
    original = b"plugins: {}\nmodel: original\n"
    config.write_bytes(original)
    binding = plugin.HermesRuntimeBinding(root, Path(sys.executable), Path(sys.executable),
        "sha256:" + "a" * 64, home, config, root, root)
    monkeypatch.setattr(plugin, "resolve_runtime_binding", lambda **_: binding)
    monkeypatch.setattr(plugin, "probe_plugin_entrypoint", lambda _: SimpleNamespace(status="verified"))
    monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", lambda b, p, raw, desired, enabled:
        _exact_plan(yaml.safe_dump(desired, sort_keys=False).encode()))
    def enable(_):
        desired, _ = plugin._expected_enabled_config(plugin._load_config_mapping(config.read_bytes()))
        config.write_text(yaml.safe_dump(desired, sort_keys=False))
        return 0
    monkeypatch.setattr(plugin, "_run_official_enable", enable)
    legacy_observer.prepare(binding)
    from hermes_feishu_card.install import recovery
    recovery.install_decomposed(__import__("hermes_feishu_card.install.detect", fromlist=["detect_hermes"]).detect_hermes(root))
    return binding, original


def test_runtime_change_reproduction_and_explicit_rebind(admission, monkeypatch):
    binding, original = admission
    changed = replace(binding, python_identity="sha256:" + "b" * 64)
    monkeypatch.setattr(plugin, "resolve_runtime_binding", lambda **_: changed)
    with pytest.raises(ValueError, match="runtime binding changed"):
        legacy_observer.prepare(changed)
    with pytest.raises(ValueError, match="runtime binding changed"):
        legacy_observer.restore(binding.checkout_root)
    from hermes_feishu_card.install import legacy_rebind
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    assert legacy_rebind.rebind(changed, apply=False)["status"] == "ready"
    legacy_rebind.rebind(changed, apply=True)
    assert legacy_observer.prepare(changed) is None
    legacy_observer.restore(binding.checkout_root)
    assert binding.config_path.read_bytes() == original


def test_unrelated_config_reproduction_and_preserved_restore(admission, monkeypatch):
    binding, _ = admission
    before_marker = (binding.checkout_root / legacy_observer.MARKER).read_bytes()
    record = json.loads(before_marker)
    old_backup = binding.hermes_home / ".hermes_feishu_card/install" / (record["plugin_config"]["config_backup_id"] + ".yaml")
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"unrelated_model: keep-me\n")
    with pytest.raises(ValueError, match="config changed"):
        legacy_observer.inspect(binding.checkout_root)
    from hermes_feishu_card.install import legacy_rebind
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    legacy_rebind.rebind(binding, apply=True)
    assert old_backup.read_bytes() == b"plugins: {}\nmodel: original\n"
    assert legacy_observer.prepare(binding) is None
    legacy_observer.restore(binding.checkout_root)
    assert binding.config_path.read_bytes() == b"plugins: {}\nmodel: original\nunrelated_model: keep-me\n"


@pytest.mark.parametrize("damage", ["plugins", "duplicate", "alias", "source", "preimage", "journal", "home", "unknown_wheel"])
def test_rebind_rejects_unsafe_evidence_without_mutation(admission, monkeypatch, damage):
    from hermes_feishu_card.install import legacy_rebind
    binding, _ = admission
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    marker = binding.checkout_root / legacy_observer.MARKER
    record = json.loads(marker.read_bytes())
    state = binding.hermes_home / ".hermes_feishu_card/install"
    if damage == "plugins":
        binding.config_path.write_bytes(binding.config_path.read_bytes().replace(b"allow_tool_override: false", b"allow_tool_override: true"))
    elif damage == "duplicate":
        binding.config_path.write_bytes(binding.config_path.read_bytes() + b"model: duplicate\n")
    elif damage == "alias":
        binding.config_path.write_bytes(binding.config_path.read_bytes() + b"a: &a value\nb: *a\n")
    elif damage == "source":
        target = binding.checkout_root / "gateway/run.py"
        target.write_bytes(target.read_bytes() + b"\n# user drift\n")
    elif damage in ("preimage", "journal"):
        suffix = ".yaml" if damage == "preimage" else ".json"
        (state / (record["plugin_config"]["config_backup_id"] + suffix)).write_text("corrupt")
    elif damage == "home":
        record["home"] = str(binding.hermes_home / "other")
        marker.write_text(json.dumps(record))
    else:
        monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: (_ for _ in ()).throw(legacy_rebind.RebindRefused("unknown wheel")))
    snapshot = {p: p.read_bytes() for p in [marker, binding.config_path, *state.iterdir()]}
    with pytest.raises(ValueError):
        legacy_rebind.rebind(binding, apply=True)
    assert snapshot == {p: p.read_bytes() for p in snapshot}
    assert set(state.iterdir()) == {p for p in snapshot if p.parent == state}


@pytest.mark.parametrize("fail_stage", ["journal", "marker", "validation"])
def test_transaction_failure_restores_all_original_bytes(admission, monkeypatch, fail_stage):
    from hermes_feishu_card.install import legacy_rebind, posix_transaction
    binding, _ = admission
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    state = binding.hermes_home / ".hermes_feishu_card/install"
    marker = binding.checkout_root / legacy_observer.MARKER
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"extra: retained\n")
    snapshot = {p: p.read_bytes() for p in [marker, binding.config_path, *state.iterdir()]}
    write = posix_transaction._atomic_write_text
    failed = []
    def fail(path, *args, **kwargs):
        wanted = path == marker if fail_stage == "marker" else path.suffix == ".json"
        if fail_stage != "validation" and wanted and not failed:
            failed.append(True)
            raise OSError("synthetic publication failure")
        return write(path, *args, **kwargs)
    monkeypatch.setattr(posix_transaction, "_atomic_write_text", fail)
    if fail_stage == "validation":
        inspect = legacy_observer.inspect
        def after(*a, **k):
            if not k.get("allow_config_drift"):
                raise ValueError("synthetic final validation failure")
            return inspect(*a, **k)
        monkeypatch.setattr(legacy_observer, "inspect", after)
    with pytest.raises((ValueError, OSError)):
        legacy_rebind.rebind(binding, apply=True)
    assert snapshot == {p: p.read_bytes() for p in snapshot}
    assert set(state.iterdir()) == {p for p in snapshot if p.parent == state}


def test_preview_and_cli_are_read_only_until_yes(admission, monkeypatch, capsys):
    from hermes_feishu_card import cli
    from hermes_feishu_card.install import legacy_rebind
    binding, _ = admission
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    monkeypatch.setattr(cli, "resolve_runtime_binding", lambda **_: binding)
    marker = binding.checkout_root / legacy_observer.MARKER
    original = marker.read_bytes()
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"new: keep\n")
    args = ["integrity", "rebind-legacy", "--hermes-dir", str(binding.checkout_root),
            "--hermes-home", str(binding.hermes_home)]
    assert cli.main(args) == 0
    assert "ready" in capsys.readouterr().out
    assert marker.read_bytes() == original
    assert cli.main(args + ["--yes"]) == 0
    assert "rebound" in capsys.readouterr().out
    assert marker.read_bytes() != original


def test_config_concurrent_edit_is_preserved_and_rebind_refused(admission, monkeypatch):
    from hermes_feishu_card.install import legacy_rebind
    binding, _ = admission
    count = []
    def changed(_):
        count.append(1)
        if len(count) == 2:
            binding.config_path.write_bytes(binding.config_path.read_bytes() + b"concurrent: keep\n")
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", changed)
    marker = binding.checkout_root / legacy_observer.MARKER
    original = marker.read_bytes()
    with pytest.raises(ValueError):
        legacy_rebind.rebind(binding, apply=True)
    assert marker.read_bytes() == original
    assert binding.config_path.read_bytes().endswith(b"concurrent: keep\n")


def test_repeated_rebind_is_byte_exact_noop(admission, monkeypatch):
    from hermes_feishu_card.install import legacy_rebind
    binding, _ = admission
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"new: keep\n")
    assert legacy_rebind.rebind(binding, apply=True)["status"] == "rebound"
    state = binding.hermes_home / ".hermes_feishu_card/install"
    marker = binding.checkout_root / legacy_observer.MARKER
    snapshot = {p: p.read_bytes() for p in [marker, *state.iterdir()]}
    assert legacy_rebind.rebind(binding, apply=False)["status"] == "unchanged"
    assert legacy_rebind.rebind(binding, apply=True)["status"] == "unchanged"
    assert set(state.iterdir()) == {p for p in snapshot if p.parent == state}
    assert snapshot == {p: p.read_bytes() for p in snapshot}


@pytest.mark.parametrize("target", ["config", "marker", "journal", "directory"])
def test_symlink_evidence_refused(admission, monkeypatch, target):
    from hermes_feishu_card.install import legacy_rebind
    binding, _ = admission
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    marker = binding.checkout_root / legacy_observer.MARKER
    state = binding.hermes_home / ".hermes_feishu_card/install"
    record = json.loads(marker.read_bytes())
    chosen = {"config": binding.config_path, "marker": marker,
        "journal": state / (record["plugin_config"]["config_backup_id"] + ".json"), "directory": state}[target]
    moved = chosen.with_name(chosen.name + ".saved")
    chosen.rename(moved)
    try:
        chosen.symlink_to(moved, target_is_directory=target == "directory")
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation requires privilege (WinError 1314)")
        raise
    with pytest.raises(ValueError):
        legacy_rebind.rebind(binding, apply=True)
    assert chosen.is_symlink()


@pytest.mark.parametrize("kind", ["runtime", "source", "marker"])
def test_concurrent_binding_or_source_replacement_refused(admission, monkeypatch, kind):
    from hermes_feishu_card.install import legacy_rebind
    binding, _ = admission
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"extra: keep\n")
    calls = []
    marker = binding.checkout_root / legacy_observer.MARKER
    original = marker.read_bytes()
    def change(_):
        calls.append(1)
        if len(calls) == 2:
            if kind == "runtime":
                monkeypatch.setattr(plugin, "resolve_runtime_binding", lambda **_: replace(binding, python_identity="sha256:" + "b" * 64))
            elif kind == "source":
                path = binding.checkout_root / "gateway/run.py"
                path.write_bytes(path.read_bytes() + b"\n# concurrent\n")
            else:
                marker.write_bytes(original + b" ")
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", change)
    with pytest.raises(ValueError):
        legacy_rebind.rebind(binding, apply=True)
    assert marker.read_bytes() == (original + b" " if kind == "marker" else original)
    state = binding.hermes_home / ".hermes_feishu_card/install"
    assert len(list(state.iterdir())) == 2


def test_runtime_verification_requires_owned_member_and_all_resources(tmp_path, monkeypatch):
    from hermes_feishu_card.install import legacy_rebind, pm_dependency
    binding = SimpleNamespace(hermes_home=tmp_path, runtime_python=Path(sys.executable))
    with pytest.raises(ValueError, match="pm_owner_required"):
        legacy_rebind._verify_runtime(binding)
    monkeypatch.setattr(pm_dependency, "_owned", lambda _: {"schema": 2})
    monkeypatch.setattr(pm_dependency, "verify_installed", lambda *a: False)
    with pytest.raises(ValueError, match="wheel_resources_unverified"):
        legacy_rebind._verify_runtime(binding)


def test_rebound_evidence_allows_next_verified_runtime_generation(admission, monkeypatch):
    from hermes_feishu_card.install import legacy_rebind
    binding, _ = admission
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"extra: keep\n")
    legacy_rebind.rebind(binding, apply=True)
    next_binding = replace(binding, python_identity="sha256:" + "c" * 64)
    monkeypatch.setattr(plugin, "resolve_runtime_binding", lambda **_: next_binding)
    assert legacy_rebind.rebind(next_binding, apply=True)["status"] == "rebound"
    legacy_observer.restore(binding.checkout_root)
    assert binding.config_path.read_bytes().endswith(b"extra: keep\n")


def test_fixed_diagnostics_for_config_and_platform_boundaries(admission, monkeypatch):
    from hermes_feishu_card.install import legacy_rebind, recovery
    from hermes_feishu_card.install.detect import detect_hermes
    binding, _ = admission
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"extra: keep\n")
    assert legacy_observer.observer_status(binding.checkout_root)["reason"] == "observer_config_changed"
    report = recovery.sanitize_recovery_plan(recovery.plan_recovery(detect_hermes(binding.checkout_root)))
    assert report["findings"][0]["code"] == "observer_config_changed"
    assert "rebind-legacy" in report["findings"][0]["message"]
    monkeypatch.setattr(legacy_rebind.io, "_cli_dirfd_binding_supported", lambda: False)
    with pytest.raises(legacy_rebind.RebindRefused, match="secure_platform_required"):
        legacy_rebind.rebind(binding, apply=False)


def test_cli_restore_after_rebind_restores_sources_and_retains_unrelated_config(admission, monkeypatch):
    from hermes_feishu_card import cli
    from hermes_feishu_card.install import legacy_rebind, decomposed_ownership
    binding, _ = admission
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    monkeypatch.setattr(cli, "resolve_runtime_binding", lambda **_: binding)
    binding.config_path.write_bytes(binding.config_path.read_bytes() + b"extra: keep\n")
    legacy_rebind.rebind(binding, apply=True)
    cli._restore(binding.checkout_root, hermes_home=binding.hermes_home)
    assert not decomposed_ownership.is_managed(binding.checkout_root)
    assert not (binding.checkout_root / legacy_observer.MARKER).exists()
    assert binding.config_path.read_bytes() == b"plugins: {}\nmodel: original\nextra: keep\n"


@pytest.mark.parametrize("apply", [False, True])
def test_non_dirfd_function_and_cli_refuse_without_admission(tmp_path, monkeypatch, capsys, apply):
    from hermes_feishu_card import cli
    from hermes_feishu_card.install import legacy_rebind
    binding = SimpleNamespace(checkout_root=tmp_path / "checkout", hermes_home=tmp_path / "home")
    monkeypatch.setattr(legacy_rebind.io, "_cli_dirfd_binding_supported", lambda: False)
    monkeypatch.setattr(cli, "resolve_runtime_binding", lambda **_: binding)
    with pytest.raises(legacy_rebind.RebindRefused, match="legacy_rebind_secure_platform_required"):
        legacy_rebind.rebind(binding, apply=apply)
    args = ["integrity", "rebind-legacy", "--hermes-dir", str(binding.checkout_root),
            "--hermes-home", str(binding.hermes_home)]
    assert cli.main(args + (["--yes"] if apply else [])) == 1
    output = capsys.readouterr()
    assert "legacy_rebind_secure_platform_required" in output.err
    assert str(tmp_path) not in output.err
    assert not list(tmp_path.iterdir())


def test_yaml_rebase_preserves_unrelated_bytes_without_transaction():
    from hermes_feishu_card.install import legacy_rebind
    before = "plugins: {}\nmodel: 原始\n".encode()
    post = "plugins:\n  enabled: [hermes-feishu-card]\nmodel: 原始\n".encode()
    current = post + "# exact comment\nextra: 新模型\n".encode()
    assert legacy_rebind._rebased_preimage(before, post, current) == before + "# exact comment\nextra: 新模型\n".encode()


@pytest.mark.parametrize("raw", [b"plugins: {}\nplugins: {}\n", b"a: &x value\nb: *x\n", b"{plugins: {}}\n"])
def test_ambiguous_yaml_refused_without_transaction(raw):
    from hermes_feishu_card.install import legacy_rebind
    with pytest.raises(legacy_rebind.RebindRefused):
        legacy_rebind._plugins_span(raw)
