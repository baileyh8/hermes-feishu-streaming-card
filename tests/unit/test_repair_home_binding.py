"""An explicit repair HOME must be checked before source mutation."""
from argparse import Namespace
from types import SimpleNamespace

import pytest

from hermes_feishu_card import cli
from hermes_feishu_card.install import legacy_observer


@pytest.mark.parametrize("selection,expected", [(None, 0), ("bound", 0), ("other", 1)])
def test_repair_checks_explicit_observer_home_before_mutating(
    tmp_path, monkeypatch, selection, expected
):
    root = tmp_path / "hermes"
    bound = tmp_path / "bound"
    mutations = []
    inspected = []

    def inspect_observer(_root, *, home=None):
        inspected.append(home)
        if home is not None and home.resolve() != bound.resolve():
            raise ValueError("legacy observer HOME binding changed")

    monkeypatch.setattr(cli, "_run_fixed_tag_v3_repair", lambda _: None)
    monkeypatch.setattr(
        cli, "detect_hermes", lambda _: SimpleNamespace(root=root, supported=True)
    )
    monkeypatch.setattr(cli, "_read_manifest", lambda _: {})
    monkeypatch.setattr(legacy_observer, "inspect", inspect_observer)
    plan = SimpleNamespace(
        actions=("restore_owned_hooks",), executable=True, fingerprint="a" * 64
    )
    monkeypatch.setattr(cli, "plan_recovery", lambda *args, **kwargs: plan)

    def execute(*args, **kwargs):
        mutations.append(kwargs)
        return SimpleNamespace(actions=("restored verified source",))

    monkeypatch.setattr(cli, "execute_recovery", execute)
    args = Namespace(
        hermes_dir=str(root), hermes_home=str(tmp_path / selection) if selection else None,
        accept_hermes_upgrade=True, yes=True,
    )
    assert cli._run_repair(args) == expected
    assert bool(mutations) is (expected == 0)
    if selection:
        assert inspected == [tmp_path / selection]


def test_install_runtime_drift_points_to_source_repair_without_mutation(
    tmp_path, monkeypatch, capsys
):
    from hermes_feishu_card.install import legacy_handoff

    root = tmp_path / "checkout with spaces"
    home = tmp_path / "bound home"
    monkeypatch.setattr(
        cli, "detect_hermes", lambda _: SimpleNamespace(root=root, supported=True)
    )
    monkeypatch.setattr(cli, "_read_manifest", lambda _: {})

    def changed_runtime(*args, **kwargs):
        raise legacy_handoff.HandoffRefused("legacy_observer_runtime_binding_changed")

    monkeypatch.setattr(legacy_handoff, "prepare", changed_runtime)
    monkeypatch.setattr(
        cli, "_ensure_hermes_runtime_package",
        lambda *args, **kwargs: pytest.fail("refusal must precede PM/config mutation"),
    )
    args = Namespace(hermes_dir=str(root), hermes_home=str(home))
    assert cli._run_install_core(args, []) == 1
    error = capsys.readouterr().err
    assert "legacy_observer_runtime_binding_changed" in error
    assert "hermes-feishu-card repair" in error
    assert "--accept-hermes-upgrade --yes" in error
    assert "--hermes-home" in error
    assert "rebind-legacy" in error
    assert "pm-recovery.md" in error
