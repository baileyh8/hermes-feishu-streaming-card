"""Exact Hermes v0.23.3 writer and launch source contracts (#537).

The exact upstream checkout is supplied by CI. A local run can opt in with
'HFC_CONFIG_WRITER_0233_ROOT'; every negative control uses a private copy.
Drift baseline vs pm-0232: 119 tracked files, 102 unchanged, 14 changed.
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


COMMIT = "caafe5389ae585ef77387a962aee05b1bcf17029"
ENV = "HFC_CONFIG_WRITER_0233_ROOT"
FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "hermes_v0233_writer_family.json"
)

# Files whose bytes differ between the pm-0232 and pm-0233 families.
CHANGED_FROM_PM0232 = {
    "agent/file_safety.py",
    "hermes_cli/archive_safe.py",
    "hermes_cli/config_defaults.py",
    "hermes_cli/config_home.py",
    "hermes_cli/config_migrations.py",
    "hermes_cli/home_data_layout.py",
    "hermes_cli/main.py",
    "hermes_cli/plugins_cmd.py",
    "hermes_cli/plugins_cmd_install.py",
    "hermes_cli/profile_identity.py",
    "hermes_cli/profiles.py",
    "hermes_constants.py",
    "hermes_dir_acl.py",
    "providers/base.py",
}


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _root() -> Path:
    configured = os.environ.get(ENV)
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("required v0.23.3 writer source missing")
        pytest.skip("v0.23.3 writer source not supplied")
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
    assert status == "", "pinned v0.23.3 checkout must be clean"
    return root


def _minimal_root(source: Path, tmp_path: Path, fixture: dict) -> Path:
    root = tmp_path / "hermes"
    for name in sorted(set(fixture["writer_files"]) | set(fixture["launch_files"])):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    return root


def test_v0233_writer_and_launch_sources_are_exact():
    root = _root()
    fixture = _fixture()
    assert fixture["commit"] == COMMIT
    assert fixture["tag"] == "v0.23.3"
    assert planner.FAMILIES["pm-0233"] == fixture["writer_files"]
    assert planner.LAUNCH_SOURCES["pm-0233"] == fixture["launch_files"]
    assert "utils.py" not in planner.FAMILIES["pm-0233"]
    assert "pm-0233" in planner.BOOT_GATE_FAMILIES
    # The v0.23.3 drift against pm-0232 is exactly the documented set:
    # 14 drifted files plus hermes_scope.py newly bound into the family.
    drifted = {
        name for name, digest in fixture["writer_files"].items()
        if pm0232_digest(name) != digest
    } | {
        name for name, digest in fixture["launch_files"].items()
        if pm0232_digest(name) != digest
    }
    assert drifted == set(fixture["changed_from_pm0232"]) | set(fixture["new_bound_sources"])
    assert set(fixture["new_bound_sources"]) == {"hermes_scope.py"}

    planner.verify_family(root, "pm-0233")
    assert planner.verify_launch_sources(root) == "pm-0233"
    for name, digest in fixture["writer_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    for name, digest in fixture["launch_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    for old in ("pm-0226", "pm-0227", "pm-0230", "pm-0231", "pm-0232"):
        with pytest.raises(planner.ConfigBytesRefused):
            planner.verify_family(root, old)
        with pytest.raises(planner.ConfigBytesRefused):
            planner.verify_launch_sources(root, family=old)
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_family(root, "pm-0233-unknown")
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_launch_sources(root, family="pm-0233-unknown")


def pm0232_digest(name: str) -> str | None:
    from hermes_feishu_card.install._writer_0232 import (
        FAMILY_SOURCES as F32, LAUNCH_SOURCES as L32,
    )
    sources = {**F32, **L32}
    return sources.get(name)  # None = not tracked by the pm-0232 family


def test_v0233_every_registered_file_drift_is_rejected(tmp_path):
    source = _root()
    fixture = _fixture()
    root = _minimal_root(source, tmp_path, fixture)
    writer = fixture["writer_files"]
    launch = fixture["launch_files"]
    assert planner.verify_launch_sources(root) == "pm-0233"

    checked = 0
    for name in sorted(set(writer) | set(launch)):
        target = root / name
        original = target.read_bytes()
        target.write_bytes(original + b"\n# unowned drift\n")
        try:
            if name in writer:
                with pytest.raises(planner.ConfigBytesRefused):
                    planner.verify_family(root, "pm-0233")
            else:
                with pytest.raises(planner.ConfigBytesRefused):
                    planner.verify_launch_sources(root, family="pm-0233")
            checked += 1
        finally:
            target.write_bytes(original)
    assert checked == len(set(writer) | set(launch))


def test_v0233_family_is_mutually_exclusive_with_0232(tmp_path):
    source = _root()
    fixture = _fixture()
    root = _minimal_root(source, tmp_path, fixture)
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_family(root, "pm-0232")
    assert planner.verify_launch_sources(root) == "pm-0233"
