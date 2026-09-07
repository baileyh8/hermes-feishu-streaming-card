"""Install real, hash-bound Hermes sources, without importing or starting Hermes.

CI supplies both exact upstream checkouts. Local runs may opt in with
HFC_UPSTREAM_STABLE_ROOT / HFC_UPSTREAM_MAIN_ROOT; no network runs inside pytest.
"""
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from hermes_feishu_card import cli
from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.integrity import plan_integrity_repair


SOURCES = json.loads(
    (Path(__file__).parents[1] / "fixtures/hermes_upstream_sources.json").read_text()
)


@pytest.mark.parametrize("baseline", ["stable", "main"])
def test_pinned_upstream_install_repeat_doctor_restore(baseline, tmp_path, monkeypatch):
    configured = os.environ.get(f"HFC_UPSTREAM_{baseline.upper()}_ROOT")
    if not configured:
        pytest.skip(f"exact {baseline} Hermes source checkout not supplied")
    source = Path(configured)
    target = tmp_path / "hermes"
    target.mkdir()
    expected = SOURCES[baseline]
    originals = {}
    for name, digest in expected["sha256"].items():
        raw = (source / name).read_bytes()
        assert sha256(raw).hexdigest() == digest, (
            f"{baseline} {name} does not match upstream {expected['commit']}"
        )
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        originals[name] = raw

    # Deliberately source-only: official Docker images need not contain .git.
    # Only package/SDK installation is skipped; detection, patching, ownership,
    # CLI dispatch, repeat installation and restore execute unchanged.
    monkeypatch.setattr(cli, "_ensure_hermes_runtime_package", lambda detection: None)
    monkeypatch.setattr(cli, "_ensure_hermes_feishu_sdk", lambda detection: None)
    detection = detect_hermes(target)
    assert detection.supported, detection.reason
    assert detection.version == "0.21.0"
    assert detection.decomposed == (baseline == "main")
    assert cli.main(["install", "--hermes-dir", str(target), "--yes"]) == 0
    installed = {name: (target / name).read_bytes() for name in originals}
    assert installed != originals
    for name, raw in installed.items():
        if name.endswith(".py"):
            compile(raw, name, "exec")
    assert cli.main(["install", "--hermes-dir", str(target), "--yes"]) == 0
    assert installed == {name: (target / name).read_bytes() for name in originals}
    detection = detect_hermes(target)
    assert detection.supported, detection.reason
    assert cli._diagnose_install_state(detection)["status"] == "installed"
    integrity = plan_integrity_repair(detection)
    assert integrity.reason == "recovery_not_required", integrity
    assert not integrity.executable
    assert cli.main(["uninstall", "--hermes-dir", str(target), "--yes"]) == 0
    assert originals == {name: (target / name).read_bytes() for name in originals}
    assert not list(target.rglob("*.hermes_feishu_card.bak"))
    assert not (target / ".hermes_feishu_card_manifest").exists()
