"""Exact v0.22.7 writer and launch family (#495)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from hermes_feishu_card.install import plugin_config_bytes as planner

COMMIT = "b200f5bc60e31588ba2cb60e288029c4a54ba5fa"
ENV = "HFC_CONFIG_WRITER_0227_ROOT"


def _root() -> Path:
    configured = os.environ.get(ENV)
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("required v0.22.7 writer source missing")
        pytest.skip("v0.22.7 writer source not supplied")
    root = Path(configured)
    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert head == COMMIT, (head, COMMIT)
    assert subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain"], text=True
    ) == ""
    return root


def test_v0227_writer_and_launch_sources_are_exact():
    root = _root()
    planner.verify_family(root, "pm-0227")
    assert planner.verify_launch_sources(root) == "pm-0227"
    assert planner.LAUNCH_SOURCES["pm-0227"] == planner.LAUNCH_SOURCES["pm-0226"]
    for name, digest in planner.FAMILIES["pm-0227"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_family(root, "pm-0226")


def test_v0227_drift_in_one_writer_file_is_rejected(tmp_path):
    root = _root()
    clone = tmp_path / "root"
    shutil.copytree(root, clone, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    target = clone / "pm/environments.py"
    target.write_bytes(target.read_bytes() + b"\n# unowned drift\n")
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_family(clone, "pm-0227")
