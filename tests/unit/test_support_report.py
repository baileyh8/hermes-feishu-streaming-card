"""Support-report export boundaries using synthetic private data only."""
import copy
from hashlib import sha256
import json
import math
import os
import stat

import pytest

from hermes_feishu_card import support_report


def private_report():
    secret = "PRIVATE_SENTINEL"
    return {
        "status": "error", "created_at": 123.0,
        "config": {"loaded": False, "path": f"/synthetic/{secret}/config.yaml",
                   "contents": secret, "error": secret,
                   "server": {"host": "private.example.test", "port": 18765},
                   "app_secret": secret},
        "sidecar": {"address": "private.example.test:18765"},
        "hermes": {"checked": True, "status": "unsupported", "version": secret,
                   "root": f"/synthetic/{secret}", "reason": secret},
        "runtime_import": {"status": "failed", "message": secret,
                           "python": f"/synthetic/{secret}/python"},
        "routing": {"profile_id": secret, "bot_id": secret, "chat_id": secret,
                    "event_endpoint": f"https://private.example.test/{secret}"},
        "runtime": {"sidecar_status": "degraded", "active_sessions": 2,
                    "metrics": {"events_received": 3, secret: secret}, "logs": secret},
        "recommendations": [{"code": "config_load_failed", "severity": "error",
                             "message": secret, "next_step": secret}],
        "chat_contents": secret, "environment": {"TOKEN": secret},
        "unknown": {"nested": secret},
    }


def test_allowlisted_report_removes_raw_private_values_and_keeps_legacy_failure_codes():
    source = private_report()
    before = copy.deepcopy(source)
    report = support_report.build_support_report(source)
    text = support_report.serialize_support_report(report)
    assert source == before
    for sensitive in ("PRIVATE_SENTINEL", "/synthetic/", "private.example.test", "18765"):
        assert sensitive not in text
    diagnostics = report["diagnostics"]
    assert diagnostics["status"] == "error"
    assert diagnostics["runtime"]["active_sessions"] == 2
    assert diagnostics["runtime"]["metrics"] == {"events_received": 3}
    assert diagnostics["findings"][0]["code"] == "config_load_failed"
    assert diagnostics["recommendations"][0]["code"] == "config_load_failed"
    assert diagnostics["config"]["path_hash"]
    canonical = dict(report)
    digest = canonical.pop("report_id")
    assert digest == sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_source_timestamp_is_removed_but_raw_serialization_rejects_it(value):
    report = support_report.build_support_report({"status": "ok", "created_at": value})
    assert "created_at" not in report["diagnostics"]
    json.loads(support_report.serialize_support_report(report),
               parse_constant=lambda value: pytest.fail(f"invalid JSON constant: {value}"))
    with pytest.raises(ValueError):
        support_report.serialize_support_report({"timestamp": value})


def test_report_limit_counts_utf8_bytes_and_rejects_excess_findings_before_creating_file(tmp_path):
    limit = support_report.MAX_SUPPORT_REPORT_BYTES
    with pytest.raises(ValueError, match="export limit"):
        support_report.serialize_support_report({"padding": "界" * (limit // 3)})
    target = tmp_path / "report.json"
    source = {"findings": [{"code": "config_load_failed", "severity": "error"}] * 2000}
    with pytest.raises(ValueError, match="export limit"):
        support_report.write_support_report(target, source)
    assert not target.exists()


def test_writer_sanitizes_raw_source_and_creates_private_file(tmp_path):
    target = tmp_path / "report.json"
    source = private_report()
    support_report.write_support_report(target, source)
    assert json.loads(target.read_text()) == support_report.build_support_report(source)
    assert "PRIVATE_SENTINEL" not in target.read_text()
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling_symlink", "directory"])
def test_writer_never_replaces_existing_or_symlink_targets(tmp_path, kind):
    target, original = tmp_path / "report.json", tmp_path / "original.txt"
    if "symlink" in kind and os.name == "nt":
        pytest.skip("Windows symlink creation depends on caller privilege")
    if kind == "file":
        target.write_text("keep original")
    elif kind == "directory":
        target.mkdir()
    else:
        if kind == "symlink":
            original.write_text("keep original")
        target.symlink_to(original)
    with pytest.raises(FileExistsError):
        support_report.write_support_report(target, {"status": "ok"})
    if kind == "file":
        assert target.read_text() == "keep original"
    elif kind == "directory":
        assert target.is_dir()
    else:
        assert target.is_symlink()
        assert original.read_text() == "keep original" if original.exists() else kind == "dangling_symlink"


def test_write_failure_retains_only_private_partial_file(tmp_path, monkeypatch):
    def fail_sync(fd):
        raise OSError("synthetic fsync failure")

    target = tmp_path / "report.json"
    monkeypatch.setattr(support_report.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="synthetic fsync failure"):
        support_report.write_support_report(target, private_report())
    assert target.is_file()
    assert "PRIVATE_SENTINEL" not in target.read_text()
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_projection_keeps_fixed_safe_meaning_for_install_state_findings():
    """The shared card-safe projection must keep the fixed meaning of the
    two informational install-state codes (they carry no local identity)
    instead of collapsing everything to the generic attention notice."""
    clean = {
        "severity": "info",
        "code": "install_state_clean",
        "message": "No existing Hermes Feishu hook install state was found.",
        "next_step": "Run install --hermes-dir PATH --yes when ready to patch Hermes.",
    }
    installed = {
        "severity": "info",
        "code": "install_state_installed",
        "message": "Existing hook install state is complete and consistent.",
        "next_step": "No install-state action is required.",
    }
    drifting = {
        "severity": "warning",
        "code": "current_hash_mismatch",
        "message": f"hash mismatch at {os.sep}private{os.sep}path",
    }
    report = support_report.build_support_report({
        "status": "ok",
        "install_state": {"status": "clean"},
        "recommendations": [clean, installed, drifting],
    })
    findings = report["diagnostics"]["findings"]
    recommendations = report["diagnostics"]["recommendations"]
    assert findings[0] == {
        "code": "install_state_clean",
        "severity": "info",
        "message": "No existing Hermes Feishu hook install state was found.",
        "impact": "",
        "actions": [
            "No hook install state was found; anchors and streaming status do not prove the producer is active. Run setup with the intended Hermes root, HOME and config, then restart the gateway.",
        ],
    }
    assert findings[1]["message"] == "Existing hook install state is complete and consistent."
    assert findings[1]["actions"] == ["No install-state action is required."]
    assert findings[2]["code"] == "current_hash_mismatch"
    assert findings[2]["message"] == "Diagnostic finding requires attention."
    assert findings[2]["actions"] == []
    serialized = support_report.serialize_support_report(report)
    for private_value in ("private", "patch Hermes"):
        assert private_value not in serialized
    assert recommendations[0]["message"] == findings[0]["message"]
    assert recommendations[0]["next_step"] == findings[0]["actions"][0]


def test_projection_never_echoes_private_values_from_known_code_findings():
    # Known-code findings may arrive with arbitrary raw values attached;
    # the shared projection must emit only the fixed safe constants.
    poisoned = [
        {
            "severity": "info",
            "code": "install_state_clean",
            "message": "PRIVATE_SENTINEL message",
            "next_step": "PRIVATE_SENTINEL next_step",
        },
        {
            "severity": "info",
            "code": "install_state_installed",
            "message": "PRIVATE_SENTINEL installed",
        },
    ]
    report = support_report.build_support_report({
        "status": "ok",
        "install_state": {"status": "clean"},
        "recommendations": poisoned,
    })
    findings = report["diagnostics"]["findings"]
    assert findings[0]["message"] == "No existing Hermes Feishu hook install state was found."
    assert all("PRIVATE_SENTINEL" not in part for part in findings[0]["actions"])
    assert findings[1]["message"] == "Existing hook install state is complete and consistent."
    assert "PRIVATE_SENTINEL" not in support_report.serialize_support_report(report)
