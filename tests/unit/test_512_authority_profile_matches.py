"""#512 absolute authority profile_id binds to its own DB before naming."""
from __future__ import annotations

import errno
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_feishu_card.admission_completion import _authority_profile_matches


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    root = tmp_path / "opt" / "data"
    root.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(
        get_default_hermes_root=lambda: root,
    ))
    return root


def _named(root: Path, name: str) -> Path:
    home = root / "profiles" / name
    home.mkdir(parents=True)
    return home


class _StubDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path


def test_docker_named_profile_db_mismatch_refused(hermes_home):
    work = _named(hermes_home, "work")
    other = _named(hermes_home, "other")
    authority = SimpleNamespace(profile_id=str(work), db=_StubDB(other / "state.db"))
    assert _authority_profile_matches(authority, "work") is False


def test_standard_hermes_shape_db_mismatch_refused(tmp_path, monkeypatch):
    root = tmp_path / "x" / ".hermes"
    work = _named(root, "work")
    other = _named(root, "other")
    monkeypatch.setenv("HERMES_HOME", str(root))
    authority = SimpleNamespace(profile_id=str(work), db=_StubDB(other / "state.db"))
    assert _authority_profile_matches(authority, "work") is False


def test_root_db_claiming_named_profile_refused(hermes_home):
    work = _named(hermes_home, "work")
    authority = SimpleNamespace(
        profile_id=str(work), db=_StubDB(hermes_home / "state.db")
    )
    assert _authority_profile_matches(authority, "work") is False


def test_docker_named_profile_correct_db_matches(hermes_home):
    work = _named(hermes_home, "work")
    authority = SimpleNamespace(
        profile_id=str(work), db=_StubDB(work / "state.db")
    )
    assert _authority_profile_matches(authority, "work") is True
    assert _authority_profile_matches(authority, "default") is False


def test_docker_default_root_db_matches(hermes_home):
    authority = SimpleNamespace(
        profile_id=str(hermes_home), db=_StubDB(hermes_home / "state.db")
    )
    assert _authority_profile_matches(authority, "default") is True
    assert _authority_profile_matches(authority, "work") is False


def test_outside_root_custom_path_refused_with_verified_root(hermes_home, monkeypatch):
    elsewhere = hermes_home.parent / "elsewhere" / "profiles" / "x"
    elsewhere.mkdir(parents=True)
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(
        get_default_hermes_root=lambda: hermes_home,
    ))
    authority = SimpleNamespace(
        profile_id=str(elsewhere), db=_StubDB(elsewhere / "state.db")
    )
    # DB proof passes but the path is not the verified root's shape.
    assert _authority_profile_matches(authority, "x") is False


def test_symlink_alias_resolves_to_same_home(hermes_home):
    work = _named(hermes_home, "work")
    alias = hermes_home / "profiles" / "alias-work"
    try:
        alias.symlink_to(work, target_is_directory=True)
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EOPNOTSUPP} or getattr(exc, "winerror", None) == 1314:
            pytest.skip("platform cannot create symlinks")
        raise
    authority = SimpleNamespace(
        profile_id=str(alias), db=_StubDB(work / "state.db")
    )
    # The alias resolves to work; DB binds at work; the name is work.
    assert _authority_profile_matches(authority, "work") is True


def test_symlink_escape_refused(hermes_home):
    outside = hermes_home.parent / "outside-home"
    outside.mkdir()
    (outside / "state.db").write_bytes(b"")
    alias = hermes_home / "profiles" / "escape"
    alias.parent.mkdir(parents=True, exist_ok=True)
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EOPNOTSUPP} or getattr(exc, "winerror", None) == 1314:
            pytest.skip("platform cannot create symlinks")
        raise
    authority = SimpleNamespace(
        profile_id=str(alias), db=_StubDB(outside / "state.db")
    )
    # DB resolves beside the symlink target, outside the root shape.
    assert _authority_profile_matches(authority, "escape") is False


def test_helper_error_refuses_authority_match(hermes_home, monkeypatch):
    work = _named(hermes_home, "work")
    authority = SimpleNamespace(
        profile_id=str(work), db=_StubDB(work / "state.db")
    )
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(
        get_default_hermes_root=lambda: (_ for _ in ()).throw(OSError("boom")),
    ))
    assert _authority_profile_matches(authority, "work") is False


def test_missing_helper_keeps_pre_512_shape_rule(tmp_path, monkeypatch):
    root = tmp_path / "x" / ".hermes"
    work = _named(root, "work")
    monkeypatch.setenv("HERMES_HOME", str(root))
    authority = SimpleNamespace(
        profile_id=str(work), db=_StubDB(work / "state.db")
    )
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace())
    assert _authority_profile_matches(authority, "work") is True


def test_uninspectable_authority_path_refuses(hermes_home, monkeypatch):
    work = _named(hermes_home, "work")
    authority = SimpleNamespace(profile_id=str(work), db=_StubDB(work / "state.db"))
    def broken(*args, **kwargs):
        raise RuntimeError("symlink loop")
    monkeypatch.setattr(Path, "resolve", broken)
    assert _authority_profile_matches(authority, "work") is False
