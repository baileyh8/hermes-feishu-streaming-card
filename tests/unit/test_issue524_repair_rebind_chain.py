"""Issue #524 regression: repair chain without a dead PM runtime interpreter.

A decomposed install with an observer marker loses its PM generation
interpreter (the runtime Python path is deleted). An upstream update then
resets the managed source to plain upstream bytes (simulated with the same
backed-up original + upgrade comment construction as
test_decomposed_patcher.test_upgrade_requires_explicit_acceptance...).
No protective record is deleted or forced: the marker, manifest, backups,
config evidence and the HOME binding stay authoritative throughout.

Covers the actionable chain agreed with root:
1. repair --accept-hermes-upgrade --hermes-home recovers stale_unpatched
   source without ever resolving the dead runtime;
2. a wrong explicit --hermes-home is refused before any source mutation;
3. the rebind-legacy preview refuses the dead runtime up front (its own
   runtime gate, not bypassed) while the marker/config evidence stay intact;
4. after the runtime is restored (simulated by un-monkeypatching), the
   verified rebind preview is ready against the untouched evidence.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest
import yaml

from hermes_feishu_card import cli
from hermes_feishu_card.install import decomposed_ownership, legacy_observer, plugin, recovery
from hermes_feishu_card.install import plugin_config_bytes
from hermes_feishu_card.install.plugin_config_bytes import ConfigBytesPlan
from hermes_feishu_card.install.decomposed import BACKUP_SUFFIX
from hermes_feishu_card.install.plugin import HermesRuntimeBinding


class _ExactPlan(ConfigBytesPlan):
    def verify_source(self) -> None:
        pass


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
def orphan_chain(tmp_path, monkeypatch):
    """Marker + stale source + dead PM runtime, all real on-disk evidence."""
    from hermes_feishu_card.install import posix_transaction
    if not posix_transaction._cli_dirfd_binding_supported():
        pytest.skip("observer transaction fixture requires secure directory-relative I/O")
    root = tmp_path / "checkout"
    home = tmp_path / "home"
    shutil.copytree(Path(__file__).parents[1] / "fixtures/hermes_decomposed", root,
                    ignore=shutil.ignore_patterns("__pycache__"))
    _copy_fixed_support(root)
    (root / "VERSION").write_text("0.20.0\n")
    home.mkdir()
    config = home / "config.yaml"
    config.write_bytes(b"plugins: {}\nmodel: original\n")
    dead_python = tmp_path / "pm-generation" / "venv" / "bin" / "python"
    binding = HermesRuntimeBinding(root, dead_python, dead_python,
        "sha256:" + "a" * 64, home, config, root, root)
    monkeypatch.setattr(plugin, "resolve_runtime_binding", lambda **_: binding)
    monkeypatch.setattr(plugin, "probe_plugin_entrypoint", lambda _: SimpleNamespace(status="verified"))
    monkeypatch.setattr(plugin, "_plan_enabled_config_bytes", lambda b, p, raw, desired, enabled, **kwargs:
        _ExactPlan(yaml.safe_dump(desired, sort_keys=False).encode(), Path("/verified"), "fixed"))
    def enable(_):
        desired, _ = plugin._expected_enabled_config(plugin._load_config_mapping(config.read_bytes()))
        config.write_text(yaml.safe_dump(desired, sort_keys=False))
        return 0
    monkeypatch.setattr(plugin, "_run_official_enable", enable)
    legacy_observer.prepare(binding)
    recovery.install_decomposed(__import__("hermes_feishu_card.install.detect", fromlist=["detect_hermes"]).detect_hermes(root))
    marker_before = (root / legacy_observer.MARKER).read_bytes()

    # Real stale_unpatched construction (same as test_decomposed_patcher):
    # write the backed-up original plus an upgrade comment; manifest untouched.
    target = root / "gateway/run_turn_runner.py"
    original = (root / ("gateway/run_turn_runner.py" + BACKUP_SUFFIX)).read_bytes()
    target.write_bytes(original + b"\n# simulated source upgrade\n")

    # The PM generation interpreter is gone; resolve_runtime_binding refuses.
    def refuse_binding(**_kwargs):
        raise plugin.RuntimeBindingRefused("Hermes runtime Python is missing")
    monkeypatch.setattr(plugin, "resolve_runtime_binding", refuse_binding)
    monkeypatch.setattr(cli, "resolve_runtime_binding", refuse_binding)
    return SimpleNamespace(root=root, home=home, binding=binding, config=config,
                           target=target, original=original,
                           marker_before=marker_before)


def test_repair_chain_without_runtime(orphan_chain):
    root = orphan_chain.root
    outcome = cli.main(["repair", "--hermes-dir", str(root),
                        "--hermes-home", str(orphan_chain.home),
                        "--accept-hermes-upgrade", "--yes"])
    assert outcome == 0, "expected repair chain to recover stale source without runtime"
    assert decomposed_ownership._inspect(root)[3] == "installed"
    # Repair re-renders the accepted source; the reset comment survives.
    assert b"# simulated source upgrade" in orphan_chain.target.read_bytes()
    assert b"HERMES_FEISHU_CARD_" in orphan_chain.target.read_bytes()
    assert (root / legacy_observer.MARKER).read_bytes() == orphan_chain.marker_before


def test_wrong_explicit_home_refused_before_source_mutation(orphan_chain):
    root = orphan_chain.root
    wrong_home = root.parent / "unrelated-home"
    wrong_home.mkdir()
    outcome = cli.main(["repair", "--hermes-dir", str(root),
                        "--hermes-home", str(wrong_home),
                        "--accept-hermes-upgrade", "--yes"])
    assert outcome != 0, "wrong explicit HOME must be refused before source mutation"
    assert decomposed_ownership._inspect(root)[3] == "stale_unpatched"
    assert orphan_chain.target.read_bytes().startswith(orphan_chain.original)
    assert b"# simulated source upgrade" in orphan_chain.target.read_bytes()
    assert (root / legacy_observer.MARKER).read_bytes() == orphan_chain.marker_before


def test_rebind_refuses_stale_source_before_runtime(orphan_chain):
    """On stale source the rebind source gate fires first; evidence untouched."""
    from hermes_feishu_card.install import legacy_rebind
    with pytest.raises(legacy_rebind.RebindRefused, match="legacy_rebind_source_not_installed"):
        legacy_rebind.rebind(orphan_chain.binding, apply=False)
    assert (orphan_chain.root / legacy_observer.MARKER).read_bytes() == (
        orphan_chain.marker_before
    )


def test_rebind_after_repair_refuses_dead_runtime(orphan_chain, capsys):
    """After repair, the dead runtime is refused at the CLI resolver gate."""
    root = orphan_chain.root
    assert cli.main(["repair", "--hermes-dir", str(root),
                     "--hermes-home", str(orphan_chain.home),
                     "--accept-hermes-upgrade", "--yes"]) == 0
    outcome = cli.main(["integrity", "rebind-legacy",
                        "--hermes-dir", str(root),
                        "--hermes-home", str(orphan_chain.home)])
    captured = capsys.readouterr()
    assert outcome == 1
    assert "legacy_rebind_evidence_unverified" in captured.err
    assert (root / legacy_observer.MARKER).read_bytes() == (
        orphan_chain.marker_before
    )


def test_rebind_preview_ready_after_repair_and_runtime_restored(
    orphan_chain, monkeypatch
):
    """After repair with a live runtime, verified evidence previews ready."""
    from hermes_feishu_card.install import legacy_rebind
    root = orphan_chain.root
    assert cli.main(["repair", "--hermes-dir", str(root),
                     "--hermes-home", str(orphan_chain.home),
                     "--accept-hermes-upgrade", "--yes"]) == 0
    live = replace(orphan_chain.binding, python_identity="sha256:" + "b" * 64)
    monkeypatch.setattr(cli, "resolve_runtime_binding", lambda **_: live)
    monkeypatch.setattr(plugin, "resolve_runtime_binding", lambda **_: live)
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    assert legacy_rebind.rebind(live, apply=False)["status"] == "ready"
    # Preview must not have mutated the marker or config.
    assert (root / legacy_observer.MARKER).read_bytes() == (
        orphan_chain.marker_before
    )
    assert orphan_chain.config.read_bytes().endswith(
        b"plugins:\n  enabled:\n  - hermes-feishu-card\n  disabled: []\n"
        b"  entries:\n    hermes-feishu-card:\n"
        b"      allow_tool_override: false\nmodel: original\n"
    )
