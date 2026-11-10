"""Exact Hermes v0.23.1 writer and launch source contracts (#520).

The exact upstream checkout is supplied by CI. A local run can opt in with
'HFC_CONFIG_WRITER_0231_ROOT'; every negative control uses a private copy.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from hermes_feishu_card.install import plugin_config_bytes as planner


COMMIT = "3b421578588243f920886e6fe79a6faa87ea026e"
ENV = "HFC_CONFIG_WRITER_0231_ROOT"
FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "hermes_v0231_writer_family.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _root() -> Path:
    configured = os.environ.get(ENV)
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("required v0.23.1 writer source missing")
        pytest.skip("v0.23.1 writer source not supplied")
    root = Path(configured)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    assert head == COMMIT, (head, COMMIT)
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout
    assert status == "", "pinned v0.23.1 checkout must be clean"
    return root


def _minimal_root(source: Path, tmp_path: Path, fixture: dict) -> Path:
    root = tmp_path / "hermes"
    for name in sorted(set(fixture["writer_files"]) | set(fixture["launch_files"])):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    return root


def test_v0231_writer_and_launch_sources_are_exact():
    root = _root()
    fixture = _fixture()
    assert fixture["commit"] == COMMIT
    assert fixture["tag"] == "v0.23.1"
    assert planner.FAMILIES["pm-0231"] == fixture["writer_files"]
    assert planner.LAUNCH_SOURCES["pm-0231"] == fixture["launch_files"]
    assert set(fixture["new_bound_sources"]) == {
        "hermes_cli/profiles_export_scrub.py",
        "hermes_cli/_startup_fast.py",
    }
    assert "pm-0231" in planner.BOOT_GATE_FAMILIES

    planner.verify_family(root, "pm-0231")
    assert planner.verify_launch_sources(root) == "pm-0231"
    for name, digest in fixture["writer_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    for name, digest in fixture["launch_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    for old in ("pm-0226", "pm-0227", "pm-0230"):
        with pytest.raises(planner.ConfigBytesRefused):
            planner.verify_family(root, old)
        with pytest.raises(planner.ConfigBytesRefused):
            planner.verify_launch_sources(root, family=old)
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_family(root, "pm-0231-unknown")
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_launch_sources(root, family="pm-0231-unknown")


def test_v0231_every_registered_file_drift_is_rejected(tmp_path):
    source = _root()
    fixture = _fixture()
    root = _minimal_root(source, tmp_path, fixture)
    writer = fixture["writer_files"]
    launch = fixture["launch_files"]
    assert planner.verify_launch_sources(root) == "pm-0231"

    checked = 0
    for name in sorted(set(writer) | set(launch)):
        target = root / name
        original = target.read_bytes()
        target.write_bytes(original + b"\n# unowned drift\n")
        try:
            if name in writer:
                with pytest.raises(planner.ConfigBytesRefused):
                    planner.verify_family(root, "pm-0231")
            else:
                with pytest.raises(planner.ConfigBytesRefused):
                    planner.verify_launch_sources(root, family="pm-0231")
            checked += 1
        finally:
            target.write_bytes(original)
    assert checked == len(set(writer) | set(launch))


def test_v0231_family_is_mutually_exclusive_with_0230(tmp_path):
    source = _root()
    fixture = _fixture()
    root = _minimal_root(source, tmp_path, fixture)
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_family(root, "pm-0230")
    assert planner.verify_launch_sources(root) == "pm-0231"
