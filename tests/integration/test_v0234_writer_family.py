"""Exact Hermes v0.23.4 writer and launch source contracts (#553).

The exact upstream checkout is supplied by CI. A local run can opt in with
'HFC_CONFIG_WRITER_0234_ROOT'; every negative control uses a private copy.
Drift baseline vs pm-0233: 119 unique tracked files (117 writer + 5 launch),
96 unchanged, 22 changed, 1 newly bound (hermes_cli/profiles_run_elsewhere.py).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from hermes_feishu_card.install import config_backend_0226 as backend
from hermes_feishu_card.install import plugin_config_bytes as planner


COMMIT = "8fc0374447313e276f6b9a2764bfc750190c966a"
ENV = "HFC_CONFIG_WRITER_0234_ROOT"
FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "hermes_v0234_writer_family.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _root() -> Path:
    configured = os.environ.get(ENV)
    if not configured:
        if os.environ.get("HFC_REQUIRE_CONFIG_WRITER_CONTRACT") == "1":
            pytest.fail("required v0.23.4 writer source missing")
        pytest.skip("v0.23.4 writer source not supplied")
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
    assert status == "", "pinned v0.23.4 checkout must be clean"
    return root


def _minimal_root(source: Path, tmp_path: Path, fixture: dict) -> Path:
    root = tmp_path / "hermes"
    for name in sorted(set(fixture["writer_files"]) | set(fixture["launch_files"])):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    return root


def pm0233_digest(name: str) -> str | None:
    from hermes_feishu_card.install._writer_0233 import (
        FAMILY_SOURCES as F33, LAUNCH_SOURCES as L33,
    )
    sources = {**F33, **L33}
    return sources.get(name)


def test_v0234_writer_and_launch_sources_are_exact():
    root = _root()
    fixture = _fixture()
    assert fixture["commit"] == COMMIT
    assert fixture["tag"] == "v0.23.4"
    assert planner.FAMILIES["pm-0234"] == fixture["writer_files"]
    assert planner.LAUNCH_SOURCES["pm-0234"] == fixture["launch_files"]
    assert "utils.py" not in planner.FAMILIES["pm-0234"]
    assert "pm-0234" in planner.BOOT_GATE_FAMILIES
    drifted = {
        name for name, digest in fixture["writer_files"].items()
        if pm0233_digest(name) != digest
    } | {
        name for name, digest in fixture["launch_files"].items()
        if pm0233_digest(name) != digest
    }
    assert drifted == set(fixture["changed_from_pm0233"]) | set(fixture["new_bound_sources"])
    assert set(fixture["new_bound_sources"]) == {"hermes_cli/profiles_run_elsewhere.py"}

    planner.verify_family(root, "pm-0234")
    assert planner.verify_launch_sources(root) == "pm-0234"
    for name, digest in fixture["writer_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    for name, digest in fixture["launch_files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    for old in ("pm-0226", "pm-0227", "pm-0230", "pm-0231", "pm-0232", "pm-0233"):
        with pytest.raises(planner.ConfigBytesRefused):
            planner.verify_family(root, old)
        with pytest.raises(planner.ConfigBytesRefused):
            planner.verify_launch_sources(root, family=old)
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_family(root, "pm-0234-unknown")
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_launch_sources(root, family="pm-0234-unknown")


def test_v0234_every_registered_file_drift_is_rejected(tmp_path):
    source = _root()
    fixture = _fixture()
    root = _minimal_root(source, tmp_path, fixture)
    writer = fixture["writer_files"]
    launch = fixture["launch_files"]
    assert planner.verify_launch_sources(root) == "pm-0234"

    checked = 0
    for name in sorted(set(writer) | set(launch)):
        target = root / name
        original = target.read_bytes()
        target.write_bytes(original + b"\n# unowned drift\n")
        try:
            if name in writer:
                with pytest.raises(planner.ConfigBytesRefused):
                    planner.verify_family(root, "pm-0234")
            else:
                with pytest.raises(planner.ConfigBytesRefused):
                    planner.verify_launch_sources(root, family="pm-0234")
            checked += 1
        finally:
            target.write_bytes(original)
    assert checked == len(set(writer) | set(launch))


def test_v0234_family_is_mutually_exclusive_with_0233(tmp_path):
    source = _root()
    fixture = _fixture()
    root = _minimal_root(source, tmp_path, fixture)
    with pytest.raises(planner.ConfigBytesRefused):
        planner.verify_family(root, "pm-0233")
    assert planner.verify_launch_sources(root) == "pm-0234"


def test_v0234_profiles_run_elsewhere_fails_closed_for_marked_profiles_only(tmp_path):
    """Marked profiles fail closed during file-backend proof; unrelated markers do not block."""
    source = _root()
    fixture = _fixture()
    root = _minimal_root(source, tmp_path, fixture)
    python = Path(sys.executable)

    # Marked default profile refuses.
    home_default = tmp_path / "home-default"
    home_default.mkdir(mode=0o700)
    (home_default / "config.yaml").write_text("_config_version: 54\nplugins:\n  enabled: []\n")
    (home_default / "profiles_run_elsewhere").mkdir(mode=0o700)
    (home_default / "profiles_run_elsewhere" / "default.json").write_text(
        '{"plugin": "sandbox", "since": "2026-11-24"}\n'
    )
    with pytest.raises(backend.BackendRefused):
        backend.verify_file_backend(root, python, home_default, sources=planner.FAMILIES["pm-0234"])

    # Marked named profile refuses.
    named_root = tmp_path / "named-root"
    home_named = named_root / "profiles" / "work"
    home_named.mkdir(parents=True, mode=0o700)
    (home_named / "config.yaml").write_text("_config_version: 54\nplugins:\n  enabled: []\n")
    (named_root / "profiles_run_elsewhere").mkdir(mode=0o700)
    (named_root / "profiles_run_elsewhere" / "work.json").write_text(
        '{"plugin": "sandbox", "since": "2026-11-24"}\n'
    )
    with pytest.raises(backend.BackendRefused):
        backend.verify_file_backend(root, python, home_named, sources=planner.FAMILIES["pm-0234"])

    # Unrelated marked profile allows default and another named profile.
    home_other_default = tmp_path / "home-other-default"
    home_other_default.mkdir(mode=0o700)
    (home_other_default / "config.yaml").write_text("_config_version: 54\nplugins:\n  enabled: []\n")
    (home_other_default / "profiles_run_elsewhere").mkdir(mode=0o700)
    (home_other_default / "profiles_run_elsewhere" / "other.json").write_text(
        '{"plugin": "sandbox", "since": "2026-11-24"}\n'
    )
    backend.verify_file_backend(root, python, home_other_default, sources=planner.FAMILIES["pm-0234"])

    unrelated_root = tmp_path / "unrelated-root"
    home_main = unrelated_root / "profiles" / "main"
    home_main.mkdir(parents=True, mode=0o700)
    (home_main / "config.yaml").write_text("_config_version: 54\nplugins:\n  enabled: []\n")
    (unrelated_root / "profiles_run_elsewhere").mkdir(mode=0o700)
    (unrelated_root / "profiles_run_elsewhere" / "other.json").write_text(
        '{"plugin": "sandbox", "since": "2026-11-24"}\n'
    )
    backend.verify_file_backend(root, python, home_main, sources=planner.FAMILIES["pm-0234"])
