"""Exact Hermes v0.23.1 admission source contracts (#520).

The exact upstream checkout is supplied by CI. A local run can opt in with
'HFC_UPSTREAM_0231_ROOT'; every negative control uses a private copy.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from hermes_feishu_card import admission_completion as ac
from hermes_feishu_card import admission_topology_0226 as topology
from hermes_feishu_card.install.detect import (
    _detect_admission_completion,
    detect_hermes,
)

ENV = "HFC_UPSTREAM_0231_ROOT"
REQUIRE_ENV = "HFC_REQUIRE_0231_PIN"
MANIFEST = json.loads(
    (Path(__file__).parents[1] / "fixtures/hermes_v0231_sources.json").read_text()
)


def _pinned_root() -> Path:
    configured = os.environ.get(ENV)
    if not configured:
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail("required v0.23.1 pinned source missing")
        pytest.skip("v0.23.1 pinned source not supplied")
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


def _copy_sources(root: Path, target: Path, names=None) -> None:
    for name in names or MANIFEST["files"]:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, destination)


def test_v0231_pinned_checkout_is_supported():
    root = _pinned_root()
    detection = detect_hermes(root)
    assert detection.supported is True
    assert detection.compatibility == "full", detection.reason
    assert detection.reason == "supported"
    assert detection.capabilities["message_handler"] is True
    assert detection.capabilities["completion_return"] is True
    assert detection.capabilities["admission_completion"] is True
    assert ac.SOURCE_COHORTS[-1] == topology.CORE_HASHES_0231
    assert topology.source_bytes_0231(root) is not None


def test_v0231_cohort_requires_every_pinned_source(tmp_path):
    root = _pinned_root()
    base = (root / "gateway/platforms/base.py").read_text(encoding="utf-8")
    candidate = tmp_path / "exact"
    _copy_sources(root, candidate)
    assert _detect_admission_completion(candidate, base) == (
        True,
        "verified-source-cohort",
    )
    for name in MANIFEST["files"]:
        changed = tmp_path / ("changed-" + name.replace("/", "_"))
        _copy_sources(root, changed)
        with (changed / name).open("ab") as stream:
            stream.write(b"\n# unowned drift\n")
        assert _detect_admission_completion(changed, base) == (
            False,
            "unsupported-source",
        ), name


def test_v0231_topology_whole_set_rejects_0230_files(tmp_path):
    """The 0231 whole-file set must not accept drifted 0230 bytes anywhere."""
    root = _pinned_root()
    candidate = tmp_path / "mixed-0230"
    _copy_sources(root, candidate)
    (candidate / "gateway/session_ingress.py").write_bytes(
        b"# 0230-generation bytes must not satisfy the 0231 whole-set\n"
    )
    base = (root / "gateway/platforms/base.py").read_text(encoding="utf-8")
    assert _detect_admission_completion(candidate, base) == (
        False,
        "unsupported-source",
    )
    assert topology.source_bytes_0231(candidate) is None


def test_v0231_unknown_main_bytes_are_rejected(tmp_path):
    """Unknown main builds (slash-confirm drift) keep failing the whole set."""
    root = _pinned_root()
    candidate = tmp_path / "unknown-main"
    _copy_sources(root, candidate)
    source = (candidate / "gateway/run_busy.py").read_text(encoding="utf-8")
    drifted = source.replace(
        "register(session_key, confirm_id, command, handler)",
        "register(session_key, confirm_id, command, handler, "
        "authority=prompt_authority())",
    )
    assert drifted != source
    (candidate / "gateway/run_busy.py").write_text(drifted, encoding="utf-8")
    base = (root / "gateway/platforms/base.py").read_text(encoding="utf-8")
    assert _detect_admission_completion(candidate, base) == (
        False,
        "unsupported-source",
    )
