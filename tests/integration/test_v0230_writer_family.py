"""Exact Hermes v0.23.0 writer and launch source contracts (#497)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from hermes_feishu_card.install import plugin_config_bytes as planner


COMMIT = "2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19"
ENV = "HFC_CONFIG_WRITER_0230_ROOT"
FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "hermes_v0230_writer_family.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _root() -> Path:
    configured = os.environ.get(ENV)
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("required v0.23.0 writer source missing")
        pytest.skip("v0.23.0 writer source not supplied")
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
    assert status == "", "pinned v0.23.0 checkout must be clean"
    return root


def _minimal_root(source: Path, tmp_path: Path, fixture: dict) -> Path:
    root = tmp_path / "hermes"
    for name in sorted(set(fixture["writer_files"]) | set(fixture["launch_files"])):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    return root


def test_v0230_writer_and_launch_sources_are_exact():
    root = _root()
    fixture = _fixture()
    assert fixture["commit"] == COMMIT
    assert fixture["tag"] == "v0.23.0"
    assert planner.FAMILIES["pm-0230"] == fixture["writer_files"]
    assert planner.LAUNCH_SOURCES["pm-0230"] == fixture["launch_files"]
    assert set(fixture["changed_writer_sources"]) == {
        "hermes_cli/config_defaults.py",
        "hermes_cli/plugins_cmd.py",
        "hermes_cli/plugins_cmd_install.py",
        "hermes_cli/plugins_cmd_update.py",
        "hermes_cli/profiles.py",
        "utils.py",
    }
    assert "hermes_cli/cpu_profile.py" in fixture["launch_files"]
    assert "hermes_cli/main_dashboard.py" in fixture["launch_files"]
    assert "pm-0230" in planner.BOOT_GATE_FAMILIES

    planner.verify_family(root, "pm-0230")
    assert planner.verify_launch_sources(root) == "pm-0230"
    for name, digest in fixture["writer_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    for name, digest in fixture["launch_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    for old in ("pm-0226", "pm-0227"):
        with pytest.raises(planner.ConfigBytesRefused):
            planner.verify_family(root, old)
        with pytest.raises(planner.ConfigBytesRefused):
            planner.verify_launch_sources(root, family=old)
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_family(root, "pm-0230-unknown")
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_launch_sources(root, family="pm-0230-unknown")


def test_v0230_every_registered_file_drift_is_rejected(tmp_path):
    source = _root()
    fixture = _fixture()
    root = _minimal_root(source, tmp_path, fixture)
    writer = fixture["writer_files"]
    launch = fixture["launch_files"]
    assert planner.verify_launch_sources(root) == "pm-0230"

    checked = 0
    for name in sorted(set(writer) | set(launch)):
        target = root / name
        original = target.read_bytes()
        target.write_bytes(original + b"\n# unowned drift\n")
        try:
            if name in writer:
                with pytest.raises(planner.ConfigBytesRefused):
                    planner.verify_family(root, "pm-0230")
            else:
                planner.verify_family(root, "pm-0230")
            if name in launch:
                with pytest.raises(planner.ConfigBytesRefused):
                    planner.verify_launch_sources(root, family="pm-0230")
            with pytest.raises(planner.ConfigBytesRefused):
                planner.verify_launch_sources(root)
        finally:
            target.write_bytes(original)
        assert planner.verify_launch_sources(root) == "pm-0230", name
        checked += 1
    assert checked == len(set(writer) | set(launch))
