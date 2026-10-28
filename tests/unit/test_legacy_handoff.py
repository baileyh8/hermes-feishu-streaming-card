"""Marker-only handoff from a verified observer to a new PM generation."""

import json
import os
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
    registration.python = new_binding.runtime_python
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
