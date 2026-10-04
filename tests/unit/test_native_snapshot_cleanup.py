from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import subprocess

import pytest

from hermes_feishu_card.install import native_hooks

pytestmark = pytest.mark.skipif(os.name != "posix", reason="descriptor snapshot is POSIX-only")


def _snapshot(tmp_path: Path):
    container = tmp_path / "snapshot"
    root = container / "source"
    (root / "nested" / "deep").mkdir(parents=True)
    (root / "nested" / "deep" / "source.py").write_text("VALUE = 1\n")
    native_hooks._make_tree_read_only(root)
    descriptor = native_hooks._open_absolute_directory(root)
    info = os.fstat(descriptor)
    return native_hooks._SourceSnapshot(root, descriptor, (info.st_dev, info.st_ino), container)


def test_close_reclaims_readonly_tree_and_is_idempotent(tmp_path):
    snapshot = _snapshot(tmp_path)
    assert stat.S_IMODE(snapshot.root.stat().st_mode) == 0o500
    descriptor = snapshot.descriptor
    snapshot.close()
    assert not snapshot.container.exists()
    with pytest.raises(OSError) as closed:
        os.fstat(descriptor)
    assert closed.value.errno == errno.EBADF
    replacement = os.open(tmp_path, os.O_RDONLY)
    try:
        snapshot.close()
        os.fstat(replacement)
    finally:
        os.close(replacement)


def test_close_does_not_follow_symlinks_or_chmod_hardlinked_files(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.py"
    sentinel.write_text("DO_NOT_CHANGE = True\n")
    sentinel.chmod(0o400)
    outside_mode = stat.S_IMODE(outside.stat().st_mode)
    snapshot = _snapshot(tmp_path)
    snapshot.root.chmod(0o700)
    (snapshot.root / "linked-dir").symlink_to(outside, target_is_directory=True)
    os.link(sentinel, snapshot.root / "hardlinked.py")
    snapshot.root.chmod(0o500)
    snapshot.close()
    assert not snapshot.container.exists()
    assert sentinel.read_text() == "DO_NOT_CHANGE = True\n"
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o400
    assert stat.S_IMODE(outside.stat().st_mode) == outside_mode


def test_close_preserves_replacement_root_and_reports_incomplete_cleanup(tmp_path, caplog):
    snapshot = _snapshot(tmp_path)
    held = snapshot.root.with_name("held")
    snapshot.root.rename(held)
    snapshot.root.mkdir()
    marker = snapshot.root / "keep.txt"
    marker.write_text("replacement is not the bound snapshot")
    snapshot.close()
    assert marker.read_text() == "replacement is not the bound snapshot"
    assert held.is_dir()
    assert "snapshot cleanup incomplete" in caplog.text


@pytest.mark.parametrize("failure_stage", ["readonly", "verify"])
def test_failed_snapshot_construction_reclaims_its_private_container(tmp_path, monkeypatch, failure_stage):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("VALUE = 1\n")
    subprocess.run(["/usr/bin/git", "init", "--quiet", str(repo)], check=True)
    subprocess.run(["/usr/bin/git", "-C", str(repo), "add", "module.py"], check=True)
    subprocess.run(["/usr/bin/git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "--quiet", "-m", "fixture"], check=True)
    commit = subprocess.check_output(["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    created = []
    real_mkdtemp = native_hooks.tempfile.mkdtemp
    def scoped_mkdtemp(**kwargs):
        path = real_mkdtemp(dir=tmp_path, **kwargs)
        created.append(Path(path))
        return path
    monkeypatch.setattr(native_hooks.tempfile, "mkdtemp", scoped_mkdtemp)
    real_readonly = native_hooks._make_tree_read_only
    def fail_readonly(root):
        real_readonly(root)
        raise ValueError("fixture failure after readonly")
    def fail_verify(*args):
        raise ValueError("fixture provenance failure")
    monkeypatch.setattr(native_hooks, "_make_tree_read_only", fail_readonly if failure_stage == "readonly" else real_readonly)
    monkeypatch.setattr(native_hooks, "_verify_snapshot_provenance", fail_verify)
    with pytest.raises(ValueError, match="fixture"):
        native_hooks._build_trusted_source_snapshot(repo, commit, None)
    assert len(created) == 1
    assert not created[0].exists()


def test_close_rejects_child_directory_rebound_to_external_symlink(tmp_path, monkeypatch, caplog):
    snapshot = _snapshot(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep outside")
    outside_mode = stat.S_IMODE(outside.stat().st_mode)
    real_open = native_hooks.os.open
    swapped = False
    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "nested" and kwargs.get("dir_fd") == snapshot.descriptor and not swapped:
            swapped = True
            (snapshot.root / "nested").rename(snapshot.root / "held")
            (snapshot.root / "nested").symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(native_hooks.os, "open", swap_before_open)
    snapshot.close()
    assert swapped
    assert marker.read_text() == "keep outside"
    assert stat.S_IMODE(outside.stat().st_mode) == outside_mode
    assert "snapshot cleanup incomplete" in caplog.text
