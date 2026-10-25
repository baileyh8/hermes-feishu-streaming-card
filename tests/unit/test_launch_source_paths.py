"""Portable launch-source and child-library path-boundary tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
from types import SimpleNamespace

import pytest

from hermes_feishu_card.install import plugin_config_bytes as planner


def _launch_fixture(base: Path, monkeypatch):
    root = base / "source"
    package = root / "hermes_cli"
    package.mkdir(parents=True)
    (package / "config.py").write_text("# writer source\n")
    (package / "__init__.py").write_text("# package init\n")
    (package / "main.py").write_text("# launch main\n")
    sources = {
        "hermes_cli/config.py": hashlib.sha256((package / "config.py").read_bytes()).hexdigest()
    }
    launch = {
        "hermes_cli/__init__.py": hashlib.sha256((package / "__init__.py").read_bytes()).hexdigest(),
        "hermes_cli/main.py": hashlib.sha256((package / "main.py").read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(planner, "FAMILIES", {"synthetic": sources})
    monkeypatch.setattr(planner, "LAUNCH_SOURCES", {"synthetic": launch})
    return root, package


def _reparse_metadata(real):
    return SimpleNamespace(
        st_mode=real.st_mode,
        st_dev=real.st_dev,
        st_ino=real.st_ino,
        st_size=real.st_size,
        st_mtime_ns=real.st_mtime_ns,
        st_file_attributes=0x400,
    )


def test_launch_sources_require_exact_non_symlink_bytes(tmp_path, monkeypatch):
    root, package = _launch_fixture(tmp_path, monkeypatch)
    assert planner.verify_launch_sources(root, family="synthetic") == "synthetic"

    (package / "main.py").write_text("# changed launch main\n")
    with pytest.raises(planner.ConfigBytesRefused) as caught:
        planner.verify_launch_sources(root, family="synthetic")
    assert caught.value.code == "launch-source-drift"
    assert caught.value.source == "hermes_cli/main.py"


def test_every_writer_family_has_a_launch_source_contract():
    assert set(planner.FAMILIES) <= set(planner.LAUNCH_SOURCES)
    for sources in planner.LAUNCH_SOURCES.values():
        assert set(sources) == {"hermes_cli/main.py", "hermes_cli/__init__.py"}
        assert all(len(digest) == 64 for digest in sources.values())


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not a portable Windows fixture")
def test_launch_sources_reject_symlinks_and_ancestor_symlinks(tmp_path, monkeypatch):
    root, package = _launch_fixture(tmp_path, monkeypatch)
    target = tmp_path / "outside-main.py"
    target.write_text("# outside\n")
    (package / "main.py").unlink()
    (package / "main.py").symlink_to(target)
    with pytest.raises(planner.ConfigBytesRefused) as caught:
        planner.verify_launch_sources(root, family="synthetic")
    assert caught.value.code == "launch-source-symlink"

    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(root, target_is_directory=True)
    with pytest.raises(planner.ConfigBytesRefused) as caught:
        planner.verify_launch_sources(linked_root, family="synthetic")
    assert caught.value.code == "launch-root-symlink"

    parent = tmp_path / "real-parent"
    parent.mkdir()
    nested = parent / "source"
    nested.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(parent, target_is_directory=True)
    with pytest.raises(planner.ConfigBytesRefused) as caught:
        planner.verify_launch_sources(linked_parent / "source", family="synthetic")
    assert caught.value.code == "launch-ancestor-symlink"


def test_reparse_metadata_is_rejected_by_launch_and_child_helpers(tmp_path, monkeypatch):
    root, package = _launch_fixture(tmp_path, monkeypatch)
    assert planner._is_reparse(SimpleNamespace(st_file_attributes=0x400))
    assert not planner._is_reparse(SimpleNamespace(st_file_attributes=0))

    original_lstat = Path.lstat

    def fake_lstat(path):
        metadata = original_lstat(path)
        if path == root:
            return _reparse_metadata(metadata)
        return metadata

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    with pytest.raises(planner.ConfigBytesRefused) as caught:
        planner.verify_launch_sources(root, family="synthetic")
    assert caught.value.code == "launch-root-reparse"

    monkeypatch.setattr(Path, "lstat", original_lstat)
    target = package / "main.py"

    def fake_target_lstat(path):
        metadata = original_lstat(path)
        if path == target:
            return _reparse_metadata(metadata)
        return metadata

    monkeypatch.setattr(Path, "lstat", fake_target_lstat)
    with pytest.raises(planner.ConfigBytesRefused) as caught:
        planner.verify_launch_sources(root, family="synthetic")
    assert caught.value.code == "launch-source-reparse"

    # Exercise the exact helpers embedded in the private child, including the
    # reparse flag that Windows junctions expose instead of S_IFLNK.
    start = planner._CHILD.index("def reparse(metadata):")
    end = planner._CHILD.index("library_roots=[]")
    namespace = {"Path": Path, "os": os, "stat": stat, "getattr": getattr}
    exec(planner._CHILD[start:end], namespace)
    monkeypatch.setattr(Path, "lstat", fake_target_lstat)
    assert namespace["nonsymlink_path"](target) is False
    assert namespace["has_symlink"](target) is True


@pytest.mark.skipif(os.name != "nt", reason="requires Windows junction metadata")
def test_windows_junction_is_reparse_for_launch_root_and_source(tmp_path, monkeypatch):
    cmd = shutil.which("cmd")
    if cmd is None:
        pytest.skip("cmd.exe is unavailable for mklink /J")

    root, package = _launch_fixture(tmp_path / "source-case", monkeypatch)
    real_package = tmp_path / "real-package"
    shutil.copytree(package, real_package)
    shutil.rmtree(package)
    created = subprocess.run(
        [cmd, "/c", "mklink", "/J", str(package), str(real_package)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, "mklink /J could not create the test junction"
    with pytest.raises(planner.ConfigBytesRefused) as caught:
        planner.verify_launch_sources(root, family="synthetic")
    assert caught.value.code == "launch-source-reparse"

    root, _ = _launch_fixture(tmp_path / "root-case", monkeypatch)
    real_root = tmp_path / "real-root"
    shutil.copytree(root, real_root)
    shutil.rmtree(root)
    created = subprocess.run(
        [cmd, "/c", "mklink", "/J", str(root), str(real_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, "mklink /J could not create the root junction"
    with pytest.raises(planner.ConfigBytesRefused) as caught:
        planner.verify_launch_sources(root, family="synthetic")
    assert caught.value.code == "launch-root-reparse"
