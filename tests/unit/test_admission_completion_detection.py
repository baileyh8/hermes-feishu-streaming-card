"""Admission source capability is independent of ordinary Gateway compatibility."""

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from hermes_feishu_card.install import detect


@pytest.fixture(params=["5fc490e", "0224", "0225"])
def source_tree(tmp_path, request):
    manifest_path = Path(__file__).parents[1] / (
        f"fixtures/session_authority_{request.param}.json"
    )
    manifest = json.loads(manifest_path.read_text())
    archive = manifest_path.with_suffix(".json.gz").read_bytes()
    assert hashlib.sha256(archive).hexdigest() == manifest["archive_sha256"]
    files = json.loads(gzip.decompress(archive))["files"]
    for name, raw in files.items():
        assert hashlib.sha256(raw.encode()).hexdigest() == manifest["files"][name]
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw)
    return tmp_path


def test_detector_reports_verified_source_cohort_separately(source_tree):
    result = detect.detect_hermes(source_tree)
    assert result.supported
    assert result.capabilities["admission_completion"]
    assert result.capability_locations["admission_completion"] == (
        "gateway/session_ingress.py", "gateway/session_authority.py",
        "gateway/platforms/base.py",
    )
    assert "unavailable" not in result.reason


@pytest.mark.parametrize("failure", ["source", "missing", "symlink", "seam", "uninspectable"])
def test_detector_exposes_admission_limit_without_disabling_ordinary_delivery(
    source_tree, failure, monkeypatch
):
    path = source_tree / "gateway/session_ingress.py"
    if failure == "source":
        path.write_text(path.read_text() + "\n# unknown source\n")
    elif failure == "missing":
        path.unlink()
    elif failure == "symlink":
        raw = path.read_text()
        path.unlink()
        other = source_tree / "ingress.py"
        other.write_text(raw)
        path.symlink_to(other)
    elif failure == "seam":
        base = source_tree / "gateway/platforms/base.py"
        base.write_text(base.read_text().replace(
            'force_document = "[[as_document]]" in response', "force_document = False"
        ))
    else:
        original = Path.lstat
        def denied(self):
            if self == path:
                raise PermissionError("uninspectable source")
            return original(self)
        monkeypatch.setattr(Path, "lstat", denied)
    result = detect.detect_hermes(source_tree)
    assert result.supported
    assert result.compatibility == "partial"
    assert result.capabilities["admission_completion"] is False
    expected = (
        "missing-or-unverified-extraction-seam"
        if failure == "seam" else "unsupported-source"
    )
    assert result.reason == "supported; admission completion unavailable: " + expected


def test_old_hermes_without_admission_files_has_no_new_requirement(source_tree):
    for name in ("session_ingress.py", "session_authority.py"):
        (source_tree / "gateway" / name).unlink()
    result = detect.detect_hermes(source_tree)
    assert result.supported
    assert "admission_completion" not in result.capabilities
    assert result.reason == "supported"
