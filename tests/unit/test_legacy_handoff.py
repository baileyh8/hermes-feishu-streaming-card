"""Marker-only handoff from a verified observer to a new PM generation."""

import json
import os
import shutil
from types import SimpleNamespace

import pytest

from hermes_feishu_card.install import (
    legacy_handoff,
    legacy_observer,
    legacy_rebind,
    pm_dependency,
    posix_transaction,
)


def _setup(tmp_path, monkeypatch):
    if not posix_transaction._cli_dirfd_binding_supported():
        pytest.skip("secure directory-relative I/O is unavailable")
    root = tmp_path / "root"
    home = tmp_path / "home"
    member = home / "plugins" / pm_dependency.NAME
    backup = home / "plugins" / ".previous"
    for path in (root, home, member):
        path.mkdir(parents=True, exist_ok=True)
    config = home / "config.yaml"
    config.write_bytes(b"plugins: {}\n")
    old_identity = "sha256:" + "a" * 64
    new_identity = "sha256:" + "b" * 64
    marker = root / legacy_observer.MARKER
    record = {
        "protocol": legacy_observer.PROTOCOL,
        "home": str(home),
        "python_identity": old_identity,
        "plugin_config": {
            "enabled_before": True,
            "added_by_hfc": True,
            "pre_sha256": "a" * 64,
            "post_sha256": "b" * 64,
            "backup_sha256": "c" * 64,
            "config_backup_id": "hfc-config-preimage-" + "d" * 32,
        },
    }
    marker.write_text(json.dumps(record, sort_keys=True) + "\n")
    old_binding = SimpleNamespace(
        checkout_root=root,
        hermes_home=home,
        runtime_python=tmp_path / "old-python",
        python_identity=old_identity,
        config_path=config,
    )
    new_binding = SimpleNamespace(
        checkout_root=root,
        hermes_home=home,
        runtime_python=tmp_path / "new-python",
        python_identity=new_identity,
        config_path=config,
    )
    member_identity = posix_transaction.read_directory_identity(
        member, ValueError, "member changed"
    )
    precondition = legacy_handoff.Precondition(
        binding=old_binding,
        marker=marker,
        marker_raw=marker.read_bytes(),
        marker_snapshot=posix_transaction.read_path_snapshot(
            marker, ValueError, "marker changed"
        ),
        marker_parent_identity=posix_transaction.read_directory_identity(
            root, ValueError, "marker parent changed"
        ),
        config=config,
        config_snapshot=posix_transaction.read_path_snapshot(
            config, ValueError, "config changed"
        ),
        config_parent_identity=posix_transaction.read_directory_identity(
            home, ValueError, "config parent changed"
        ),
        marker_mode=marker.lstat().st_mode & 0o7777,
        marker_uid=marker.lstat().st_uid,
        config_mode=config.lstat().st_mode & 0o7777,
        config_uid=config.lstat().st_uid,
        source_fingerprint="source",
        member=member,
        member_identity=member_identity,
        member_owner={"version": "5.0.11"},
    )
    member.rename(backup)
    member.mkdir()
    current_member_identity = posix_transaction.read_directory_identity(
        member, ValueError, "member changed"
    )
    registration = object.__new__(pm_dependency.Registration)
    registration.root = root
    registration.home = home
    registration.python = old_binding.runtime_python
    registration.post_python = new_binding.runtime_python
    registration.member = member
    registration.backup = backup
    registration.owner = {"version": "5.0.12"}
    registration.adoption = None
    registration.identity = current_member_identity
    monkeypatch.setattr(
        legacy_handoff.plugin, "resolve_runtime_binding", lambda **_: new_binding
    )
    monkeypatch.setattr(legacy_rebind, "_verify_runtime", lambda _: None)
    monkeypatch.setattr(legacy_rebind, "_source_fingerprint", lambda _: "source")
    monkeypatch.setattr(
        pm_dependency,
        "_owned",
        lambda path: {"version": "5.0.11"}
        if path == backup
        else {"version": "5.0.12"}
        if path == member
        else None,
    )
    monkeypatch.setattr(
        legacy_observer, "inspect", lambda *args, **kwargs: (record, b"", None, None, b"")
    )
    return precondition, new_binding, registration, marker, config


def _rewrite_same_bytes(path, snapshot):
    return posix_transaction._atomic_write_text(
        path,
        path.read_bytes().decode("utf-8"),
        _expected_before=snapshot,
    )


def _restore_old_member(registration, precondition, monkeypatch):
    registration.member.rmdir()
    registration.backup.rename(registration.member)
    monkeypatch.setattr(
        pm_dependency,
        "_owned",
        lambda path: precondition.member_owner if path == precondition.member else None,
    )


def test_apply_changes_only_marker_identity_and_rolls_back_exactly(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, config = _setup(tmp_path, monkeypatch)
    before = marker.read_bytes()
    config_before = config.read_bytes()

    handoff = legacy_handoff.apply(precondition, new_binding, registration)
    after = marker.read_bytes()
    assert after != before
    assert json.loads(after)["python_identity"] == new_binding.python_identity
    assert {k: v for k, v in json.loads(after).items() if k != "python_identity"} == {
        k: v for k, v in json.loads(before).items() if k != "python_identity"
    }
    assert config.read_bytes() == config_before

    handoff.rollback()
    assert marker.read_bytes() == before
    assert config.read_bytes() == config_before
    assert handoff.rollback_snapshot == posix_transaction.read_path_snapshot(
        marker, ValueError, "marker changed"
    )


def test_concurrent_marker_change_refuses_before_write(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, _config = _setup(tmp_path, monkeypatch)
    marker.write_bytes(marker.read_bytes() + b" ")
    with pytest.raises(legacy_handoff.HandoffRefused):
        legacy_handoff.apply(precondition, new_binding, registration)


def test_rollback_refuses_a_marker_changed_after_apply(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    handoff = legacy_handoff.apply(precondition, new_binding, registration)
    marker.write_bytes(marker.read_bytes() + b" ")
    with pytest.raises(legacy_handoff.HandoffRefused, match="marker_changed"):
        handoff.rollback()


def test_rollback_refuses_a_config_changed_after_apply(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, config = _setup(
        tmp_path, monkeypatch
    )
    handoff = legacy_handoff.apply(precondition, new_binding, registration)
    after = marker.read_bytes()
    config.write_bytes(config.read_bytes() + b"# changed after apply\n")
    with pytest.raises(legacy_handoff.HandoffRefused, match="config_changed"):
        handoff.rollback()
    assert marker.read_bytes() == after


def test_rollback_refuses_a_replaced_checkout_root(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    handoff = legacy_handoff.apply(precondition, new_binding, registration)
    after = marker.read_bytes()
    root = precondition.binding.checkout_root
    moved = tmp_path / "root-moved"
    root.rename(moved)
    root.mkdir()
    os.link(moved / legacy_observer.MARKER, root / legacy_observer.MARKER)
    with pytest.raises(legacy_handoff.HandoffRefused, match="marker_changed"):
        handoff.rollback()
    assert (moved / legacy_observer.MARKER).read_bytes() == after
    assert (root / legacy_observer.MARKER).read_bytes() == after


def test_wrong_registration_type_is_refused(tmp_path, monkeypatch):
    precondition, new_binding, _registration, _marker, _config = _setup(tmp_path, monkeypatch)
    with pytest.raises(legacy_handoff.HandoffRefused, match="registration_required"):
        legacy_handoff.apply(precondition, new_binding, SimpleNamespace())


@pytest.mark.parametrize("field", ("checkout_root", "hermes_home", "config_path"))
def test_changed_scope_is_refused(tmp_path, monkeypatch, field):
    precondition, new_binding, registration, _marker, _config = _setup(
        tmp_path, monkeypatch
    )
    before = precondition.marker.read_bytes()
    setattr(new_binding, field, tmp_path / f"other-{field}")
    with pytest.raises(legacy_handoff.HandoffRefused, match="scope_changed"):
        legacy_handoff.apply(precondition, new_binding, registration)
    assert precondition.marker.read_bytes() == before


def test_source_change_during_runtime_probe_is_refused(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    state = {"source": "source"}
    monkeypatch.setattr(legacy_rebind, "_source_fingerprint", lambda _: state["source"])

    def probe(_binding):
        state["source"] = "changed"

    monkeypatch.setattr(legacy_rebind, "_verify_runtime", probe)
    before = marker.read_bytes()
    with pytest.raises(legacy_handoff.HandoffRefused, match="source_changed"):
        legacy_handoff.apply(precondition, new_binding, registration)
    assert marker.read_bytes() == before


def test_marker_change_during_runtime_probe_is_refused(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    mutated = marker.read_bytes() + b" "

    def probe(_binding):
        marker.write_bytes(mutated)

    monkeypatch.setattr(legacy_rebind, "_verify_runtime", probe)
    with pytest.raises(legacy_handoff.HandoffRefused, match="marker_changed"):
        legacy_handoff.apply(precondition, new_binding, registration)
    assert marker.read_bytes() == mutated


def test_config_change_during_runtime_probe_is_refused(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, config = _setup(
        tmp_path, monkeypatch
    )
    before = marker.read_bytes()
    mutated = config.read_bytes() + b"# changed during probe\n"

    def probe(_binding):
        config.write_bytes(mutated)

    monkeypatch.setattr(legacy_rebind, "_verify_runtime", probe)
    with pytest.raises(legacy_handoff.HandoffRefused, match="config_changed"):
        legacy_handoff.apply(precondition, new_binding, registration)
    assert marker.read_bytes() == before
    assert config.read_bytes() == mutated


def test_same_bytes_config_rewrite_after_pm_is_accepted(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, config = _setup(
        tmp_path, monkeypatch
    )
    before = marker.read_bytes()
    config_before = config.read_bytes()
    rewritten = _rewrite_same_bytes(config, precondition.config_snapshot)
    assert rewritten[:2] != precondition.config_snapshot[:2]
    assert rewritten[2] == precondition.config_snapshot[2]

    handoff = legacy_handoff.apply(precondition, new_binding, registration)
    assert marker.read_bytes() != before
    assert config.read_bytes() == config_before
    assert (config.lstat().st_dev, config.lstat().st_ino) == rewritten[:2]

    handoff.rollback()
    assert marker.read_bytes() == before
    assert config.read_bytes() == config_before
    assert (config.lstat().st_dev, config.lstat().st_ino) == rewritten[:2]


def test_same_bytes_config_rewrite_during_probe_is_refused(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, config = _setup(
        tmp_path, monkeypatch
    )
    before = marker.read_bytes()
    _rewrite_same_bytes(config, precondition.config_snapshot)

    def probe(_binding):
        current = posix_transaction.read_path_snapshot(
            config, ValueError, "config changed"
        )
        _rewrite_same_bytes(config, current)

    monkeypatch.setattr(legacy_rebind, "_verify_runtime", probe)
    with pytest.raises(legacy_handoff.HandoffRefused, match="config_changed"):
        legacy_handoff.apply(precondition, new_binding, registration)
    assert marker.read_bytes() == before


def test_config_content_change_before_apply_is_refused(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, config = _setup(
        tmp_path, monkeypatch
    )
    before = marker.read_bytes()
    config.write_bytes(config.read_bytes() + b"# changed before apply\n")
    with pytest.raises(legacy_handoff.HandoffRefused, match="evidence_changed"):
        legacy_handoff.apply(precondition, new_binding, registration)
    assert marker.read_bytes() == before


def test_same_bytes_marker_rewrite_is_refused(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    before = marker.read_bytes()
    rewritten = _rewrite_same_bytes(marker, precondition.marker_snapshot)
    assert rewritten[:2] != precondition.marker_snapshot[:2]
    assert rewritten[2] == precondition.marker_snapshot[2]
    with pytest.raises(legacy_handoff.HandoffRefused, match="evidence_changed"):
        legacy_handoff.apply(precondition, new_binding, registration)
    assert marker.read_bytes() == before


def test_reconcile_after_pm_rollback_rebinds_marker_to_real_runtime(
    tmp_path, monkeypatch
):
    precondition, new_binding, registration, marker, config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    monkeypatch.setattr(legacy_observer, "_config_state", lambda *args: "installed")
    verified = []
    monkeypatch.setattr(
        legacy_rebind, "_verify_runtime", lambda binding: verified.append(binding)
    )
    before = marker.read_bytes()
    config_before = config.read_bytes()

    outcome = legacy_handoff.reconcile_after_rollback(
        precondition,
        pm_rollback_before=precondition.binding,
        pm_rollback_after=new_binding,
    )

    assert outcome == "rebound"
    assert json.loads(marker.read_bytes())["python_identity"] == new_binding.python_identity
    assert marker.read_bytes() != before
    assert config.read_bytes() == config_before
    assert verified == [new_binding]

    after = marker.read_bytes()
    assert (
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=precondition.binding,
            pm_rollback_after=new_binding,
        )
        == "unchanged"
    )
    assert marker.read_bytes() == after


def test_reconcile_is_a_noop_when_runtime_did_not_change(tmp_path, monkeypatch):
    precondition, _new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    monkeypatch.setattr(legacy_observer, "_config_state", lambda *args: "installed")
    monkeypatch.setattr(
        legacy_handoff.plugin,
        "resolve_runtime_binding",
        lambda **kwargs: precondition.binding,
    )
    before = marker.read_bytes()
    assert (
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=precondition.binding,
            pm_rollback_after=precondition.binding,
        )
        == "unchanged"
    )
    assert marker.read_bytes() == before


def test_reconcile_allows_pm_rollback_back_to_the_original_runtime(
    tmp_path, monkeypatch
):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    monkeypatch.setattr(legacy_observer, "_config_state", lambda *args: "installed")
    monkeypatch.setattr(
        legacy_handoff.plugin,
        "resolve_runtime_binding",
        lambda **kwargs: precondition.binding,
    )
    before = marker.read_bytes()
    assert (
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=new_binding,
            pm_rollback_after=precondition.binding,
        )
        == "unchanged"
    )
    assert marker.read_bytes() == before


def test_reconcile_uses_the_marker_snapshot_produced_by_rollback(
    tmp_path, monkeypatch
):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    monkeypatch.setattr(legacy_observer, "_config_state", lambda *args: "installed")
    restored = _rewrite_same_bytes(marker, precondition.marker_snapshot)
    assert restored[:2] != precondition.marker_snapshot[:2]

    assert (
        legacy_handoff.reconcile_after_rollback(
            precondition,
            marker_snapshot=restored,
            pm_rollback_before=precondition.binding,
            pm_rollback_after=new_binding,
        )
        == "rebound"
    )
    assert json.loads(marker.read_bytes())["python_identity"] == new_binding.python_identity


def test_reconcile_validates_current_marker_before_returning_unchanged(
    tmp_path, monkeypatch
):
    precondition, new_binding, registration, marker, config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    monkeypatch.setattr(legacy_observer, "_config_state", lambda *args: "installed")
    assert (
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=precondition.binding,
            pm_rollback_after=new_binding,
        )
        == "rebound"
    )
    current = marker.read_bytes()
    config.write_bytes(config.read_bytes() + b"# drift after no-op\n")
    with pytest.raises(legacy_handoff.HandoffRefused, match="config_changed"):
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=precondition.binding,
            pm_rollback_after=new_binding,
        )
    assert marker.read_bytes() == current


def test_reconcile_refuses_replaced_marker_parent(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    before = marker.read_bytes()
    root = precondition.binding.checkout_root
    moved = tmp_path / "root-moved"
    root.rename(moved)
    root.mkdir()
    os.link(moved / legacy_observer.MARKER, root / legacy_observer.MARKER)
    with pytest.raises(legacy_handoff.HandoffRefused, match="marker_changed"):
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=precondition.binding,
            pm_rollback_after=new_binding,
        )
    assert (moved / legacy_observer.MARKER).read_bytes() == before
    assert (root / legacy_observer.MARKER).read_bytes() == before


def test_reconcile_refuses_missing_or_unchanged_pm_rollback_evidence(
    tmp_path, monkeypatch
):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    before = marker.read_bytes()
    with pytest.raises(legacy_handoff.HandoffRefused, match="binding_missing"):
        legacy_handoff.reconcile_after_rollback(precondition)
    assert marker.read_bytes() == before
    with pytest.raises(legacy_handoff.HandoffRefused, match="generation_unchanged"):
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=new_binding,
            pm_rollback_after=new_binding,
        )
    assert marker.read_bytes() == before


def test_reconcile_refuses_a_generation_not_produced_by_this_rollback(
    tmp_path, monkeypatch
):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    before = marker.read_bytes()
    other = SimpleNamespace(
        checkout_root=new_binding.checkout_root,
        hermes_home=new_binding.hermes_home,
        runtime_python=tmp_path / "other-python",
        python_identity="sha256:" + "c" * 64,
        config_path=new_binding.config_path,
    )
    with pytest.raises(legacy_handoff.HandoffRefused, match="binding_changed"):
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=precondition.binding,
            pm_rollback_after=other,
        )
    assert marker.read_bytes() == before


def test_reconcile_refuses_a_recreated_old_member(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    replacement = tmp_path / "replacement-member"
    replacement.mkdir()
    shutil.rmtree(precondition.member)
    replacement.rename(precondition.member)
    assert (
        posix_transaction.read_directory_identity(
            precondition.member, ValueError, "member changed"
        )
        != precondition.member_identity
    )
    before = marker.read_bytes()
    with pytest.raises(legacy_handoff.HandoffRefused, match="member_changed"):
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=precondition.binding,
            pm_rollback_after=new_binding,
        )
    assert marker.read_bytes() == before


@pytest.mark.parametrize("drift", ("marker", "config", "source"))
def test_reconcile_refuses_real_evidence_drift(tmp_path, monkeypatch, drift):
    precondition, new_binding, registration, marker, config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    if drift == "marker":
        marker.write_bytes(marker.read_bytes() + b" ")
        expected = "marker_changed"
    elif drift == "config":
        config.write_bytes(config.read_bytes() + b"# drift\n")
        expected = "config_changed"
    else:
        monkeypatch.setattr(legacy_rebind, "_source_fingerprint", lambda _: "drift")
        expected = "source_changed"
    before = marker.read_bytes()
    with pytest.raises(legacy_handoff.HandoffRefused, match=expected):
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=precondition.binding,
            pm_rollback_after=new_binding,
        )
    assert marker.read_bytes() == before


def test_reconcile_restores_marker_if_post_write_validation_fails(
    tmp_path, monkeypatch
):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    _restore_old_member(registration, precondition, monkeypatch)
    monkeypatch.setattr(legacy_observer, "_config_state", lambda *args: "installed")
    original_inspect = legacy_observer.inspect
    calls = []

    def inspect(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            return original_inspect(*args, **kwargs)
        raise ValueError("post-write observer inspection failed")

    monkeypatch.setattr(legacy_observer, "inspect", inspect)
    before = marker.read_bytes()
    with pytest.raises(ValueError, match="post-write observer"):
        legacy_handoff.reconcile_after_rollback(
            precondition,
            pm_rollback_before=precondition.binding,
            pm_rollback_after=new_binding,
        )
    assert marker.read_bytes() == before


def test_apply_post_write_failure_exposes_restored_marker_snapshot(
    tmp_path, monkeypatch
):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    def inspect(*args, **kwargs):
        raise ValueError("post-write inspect failed")

    monkeypatch.setattr(legacy_observer, "inspect", inspect)
    before = marker.read_bytes()
    with pytest.raises(ValueError, match="post-write inspect") as exc_info:
        legacy_handoff.apply(precondition, new_binding, registration)
    assert marker.read_bytes() == before
    assert exc_info.value.rollback_snapshot == posix_transaction.read_path_snapshot(
        marker, ValueError, "marker changed"
    )


def test_apply_rollback_failure_preserves_original_and_rollback_error(
    tmp_path, monkeypatch
):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    def inspect(*args, **kwargs):
        marker.write_bytes(marker.read_bytes() + b" ")
        raise ValueError("post-write inspect failed")

    monkeypatch.setattr(legacy_observer, "inspect", inspect)
    with pytest.raises(ValueError, match="post-write inspect") as exc_info:
        legacy_handoff.apply(precondition, new_binding, registration)
    assert isinstance(exc_info.value.rollback_error, legacy_handoff.HandoffRefused)
    assert marker.read_bytes().endswith(b" ")


def test_checkout_root_replacement_during_runtime_probe_is_refused(
    tmp_path, monkeypatch
):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    before = marker.read_bytes()
    root = precondition.binding.checkout_root
    moved = tmp_path / "root-moved"

    def probe(_binding):
        root.rename(moved)
        root.mkdir()
        os.link(moved / legacy_observer.MARKER, root / legacy_observer.MARKER)

    monkeypatch.setattr(legacy_rebind, "_verify_runtime", probe)
    with pytest.raises(legacy_handoff.HandoffRefused, match="marker_changed"):
        legacy_handoff.apply(precondition, new_binding, registration)
    assert (moved / legacy_observer.MARKER).read_bytes() == before
    assert (root / legacy_observer.MARKER).read_bytes() == before


def test_checkout_root_replacement_before_apply_is_refused(tmp_path, monkeypatch):
    precondition, new_binding, registration, marker, _config = _setup(
        tmp_path, monkeypatch
    )
    before = marker.read_bytes()
    root = precondition.binding.checkout_root
    moved = tmp_path / "root-moved"
    root.rename(moved)
    root.mkdir()
    os.link(moved / legacy_observer.MARKER, root / legacy_observer.MARKER)
    with pytest.raises(legacy_handoff.HandoffRefused, match="evidence_changed"):
        legacy_handoff.apply(precondition, new_binding, registration)
    assert (moved / legacy_observer.MARKER).read_bytes() == before
    assert (root / legacy_observer.MARKER).read_bytes() == before


@pytest.mark.parametrize("field", ("root", "home", "python"))
def test_registration_scope_mismatch_is_refused(tmp_path, monkeypatch, field):
    precondition, new_binding, registration, _marker, _config = _setup(
        tmp_path, monkeypatch
    )
    setattr(registration, field, tmp_path / f"other-{field}")
    with pytest.raises(legacy_handoff.HandoffRefused, match="registration_mismatch"):
        legacy_handoff.apply(precondition, new_binding, registration)


def test_registration_owner_mismatch_is_refused(tmp_path, monkeypatch):
    precondition, new_binding, registration, _marker, _config = _setup(
        tmp_path, monkeypatch
    )
    registration.owner = {"version": "other"}
    with pytest.raises(ValueError, match="PM dependency changed"):
        legacy_handoff.apply(precondition, new_binding, registration)


def test_registration_keeps_the_rollback_python(tmp_path, monkeypatch):
    precondition, new_binding, registration, _marker, _config = _setup(
        tmp_path, monkeypatch
    )
    registration.python = new_binding.runtime_python
    with pytest.raises(legacy_handoff.HandoffRefused, match="registration_mismatch"):
        legacy_handoff.apply(precondition, new_binding, registration)


def test_registration_post_python_must_be_the_selected_generation(tmp_path, monkeypatch):
    precondition, new_binding, registration, _marker, _config = _setup(
        tmp_path, monkeypatch
    )
    registration.post_python = precondition.binding.runtime_python
    with pytest.raises(legacy_handoff.HandoffRefused, match="registration_mismatch"):
        legacy_handoff.apply(precondition, new_binding, registration)


def test_registration_requires_post_python(tmp_path, monkeypatch):
    precondition, new_binding, registration, _marker, _config = _setup(
        tmp_path, monkeypatch
    )
    registration.post_python = None
    with pytest.raises(legacy_handoff.HandoffRefused, match="registration_mismatch"):
        legacy_handoff.apply(precondition, new_binding, registration)


def test_adoption_registration_is_not_handed_off(tmp_path, monkeypatch):
    precondition, new_binding, registration, _marker, _config = _setup(tmp_path, monkeypatch)
    registration.adoption = object()
    with pytest.raises(legacy_handoff.HandoffRefused, match="adoption_unsupported"):
        legacy_handoff.apply(precondition, new_binding, registration)


def test_prepare_refuses_restored_or_partial_config_before_pm(tmp_path, monkeypatch):
    precondition, _new_binding, _registration, _marker, _config = _setup(tmp_path, monkeypatch)
    detection = SimpleNamespace(root=precondition.binding.checkout_root, decomposed=True)
    monkeypatch.setattr(legacy_observer, "available", lambda _: True)
    monkeypatch.setattr(legacy_observer, "_read", lambda _: precondition.marker_raw)
    monkeypatch.setattr(
        legacy_observer,
        "_decode",
        lambda _: json.loads(precondition.marker_raw),
    )
    monkeypatch.setattr(
        legacy_handoff.plugin,
        "resolve_runtime_binding",
        lambda **_: precondition.binding,
    )
    monkeypatch.setattr(
        legacy_observer,
        "inspect",
        lambda *args, **kwargs: (json.loads(precondition.marker_raw), b"", None, None, b""),
    )
    monkeypatch.setattr(legacy_observer, "_config_state", lambda *args: "restored")

    with pytest.raises(legacy_handoff.HandoffRefused, match="restore_incomplete"):
        legacy_handoff.prepare(
            detection,
            hermes_home=precondition.binding.hermes_home,
            profile_id=None,
        )


def _prepare_surface(tmp_path, monkeypatch):
    original_owned = pm_dependency._owned
    precondition, _new_binding, _registration, _marker, _config = _setup(
        tmp_path, monkeypatch
    )
    detection = SimpleNamespace(root=precondition.binding.checkout_root, decomposed=True)
    monkeypatch.setattr(legacy_observer, "available", lambda _: True)
    monkeypatch.setattr(legacy_observer, "_read", lambda _: precondition.marker_raw)
    monkeypatch.setattr(
        legacy_handoff.plugin,
        "resolve_runtime_binding",
        lambda **_: precondition.binding,
    )
    monkeypatch.setattr(
        legacy_observer,
        "inspect",
        lambda *args, **kwargs: (
            json.loads(precondition.marker_raw),
            precondition.marker_raw,
            None,
            None,
            precondition.config.read_bytes(),
        ),
    )
    monkeypatch.setattr(legacy_observer, "_config_state", lambda *args: "installed")
    monkeypatch.setattr(
        legacy_rebind,
        "_source_fingerprint",
        lambda _: pytest.fail("PM source check should not run"),
    )
    monkeypatch.setattr(
        legacy_rebind,
        "_verify_runtime",
        lambda _: pytest.fail("PM runtime check should not run"),
    )
    return precondition, detection, original_owned


def test_prepare_skips_pm_handoff_when_no_pm_member_exists(tmp_path, monkeypatch):
    precondition, detection, original_owned = _prepare_surface(tmp_path, monkeypatch)
    shutil.rmtree(precondition.member)
    monkeypatch.setattr(pm_dependency, "_owned", original_owned)
    assert (
        legacy_handoff.prepare(
            detection,
            hermes_home=precondition.binding.hermes_home,
            profile_id=None,
        )
        is None
    )


def test_prepare_still_refuses_an_existing_unowned_pm_member(tmp_path, monkeypatch):
    precondition, detection, original_owned = _prepare_surface(tmp_path, monkeypatch)
    monkeypatch.setattr(pm_dependency, "_owned", original_owned)
    with pytest.raises(legacy_handoff.HandoffRefused, match="member_owner_invalid"):
        legacy_handoff.prepare(
            detection,
            hermes_home=precondition.binding.hermes_home,
            profile_id=None,
        )


def test_prepare_refuses_a_missing_member_selected_by_official_config(
    tmp_path, monkeypatch
):
    precondition, detection, original_owned = _prepare_surface(tmp_path, monkeypatch)
    shutil.rmtree(precondition.member)
    precondition.config.write_bytes(
        b"plugins:\n  enabled: [hfc-runtime-dependency]\n"
    )
    monkeypatch.setattr(pm_dependency, "_owned", original_owned)
    with pytest.raises(legacy_handoff.HandoffRefused, match="member_owner_missing"):
        legacy_handoff.prepare(
            detection,
            hermes_home=precondition.binding.hermes_home,
            profile_id=None,
        )
