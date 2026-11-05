"""Pinned Hermes v0.22.7 admission cohort (#491).

The exact upstream checkout is supplied by CI. A local run can opt in with
HFC_UPSTREAM_0227_ROOT; the negative control uses a private copy and never
modifies the pinned source.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from hermes_feishu_card import admission_completion
from hermes_feishu_card.install import admission_extraction
from hermes_feishu_card.install.detect import _detect_admission_completion, detect_hermes

ENV = "HFC_UPSTREAM_0227_ROOT"
MANIFEST = json.loads(
    (Path(__file__).parents[1] / "fixtures/hermes_v0227_sources.json").read_text()
)


def _pinned_root() -> Path:
    configured = os.environ.get(ENV)
    if not configured:
        if os.environ.get("HFC_REQUIRE_0227_PIN") == "1":
            pytest.fail("required v0.22.7 pinned source missing")
        pytest.skip("v0.22.7 pinned source not supplied")
    root = Path(configured)
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert commit == MANIFEST["commit"], (commit, MANIFEST["commit"])
    assert subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain"], text=True
    ) == ""
    for name, digest in MANIFEST["files"].items():
        path = root / name
        assert path.is_file(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    return root


def _copy_sources(root: Path, target: Path) -> None:
    for name in MANIFEST["files"]:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, destination)


def test_v0227_pinned_checkout_is_supported():
    root = _pinned_root()
    detection = detect_hermes(root)
    assert detection.supported is True
    assert detection.compatibility == "full", detection.reason
    assert detection.reason == "supported"
    assert detection.capabilities["admission_completion"] is True
    assert admission_completion.CORE_HASHES_0227 in admission_completion.SOURCE_COHORTS


def test_v0227_cohort_requires_every_pinned_source(tmp_path):
    root = _pinned_root()
    base = (root / "gateway/platforms/base.py").read_text(encoding="utf-8")
    candidate = tmp_path / "exact"
    _copy_sources(root, candidate)
    assert _detect_admission_completion(candidate, base) == (True, "verified-source-cohort")

    for name in MANIFEST["files"]:
        if name.endswith("base.py"):
            continue
        changed = tmp_path / ("changed-" + name.replace("/", "_"))
        _copy_sources(root, changed)
        with (changed / name).open("ab") as stream:
            stream.write(b"\n# unowned drift\n")
        assert _detect_admission_completion(changed, base) == (
            False, "unsupported-source"
        ), name
    assert admission_extraction.BEGIN in admission_extraction.apply(base)
