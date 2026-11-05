"""Convergent managed PM upgrades (#494)."""
import json
from pathlib import Path
from types import SimpleNamespace

from hermes_feishu_card import cli
from hermes_feishu_card.install import pm_dependency


class _FakeDistribution:
    def __init__(self, payload):
        self._payload = payload

    def read_text(self, name):
        return self._payload if name == "direct_url.json" else None


def test_vcs_install_derives_exact_commit_spec(monkeypatch):
    payload = json.dumps({
        "url": "https://github.com/baileyh8/hermes-feishu-streaming-card.git",
        "vcs_info": {"vcs": "git", "commit_id": "a" * 40,
                     "requested_revision": "v5.0.9"},
    })
    monkeypatch.setattr(
        cli.importlib_metadata, "distribution",
        lambda name: _FakeDistribution(payload),
    )
    monkeypatch.setenv("HFC_INSTALL_SPEC", "")
    assert cli._installed_distribution_spec() == (
        "git+https://github.com/baileyh8/hermes-feishu-streaming-card.git@" + "a" * 40
    )


def test_index_wheel_or_missing_metadata_needs_explicit_spec(monkeypatch):
    monkeypatch.setattr(
        cli.importlib_metadata, "distribution",
        lambda name: _FakeDistribution(None),
    )
    assert cli._installed_distribution_spec() is None
    monkeypatch.setattr(
        cli.importlib_metadata, "distribution",
        lambda name: _FakeDistribution("not-json"),
    )
    assert cli._installed_distribution_spec() is None
    monkeypatch.setattr(
        cli.importlib_metadata, "distribution",
        lambda name: _FakeDistribution(json.dumps({"url": "https://pypi.example/x.whl"})),
    )
    assert cli._installed_distribution_spec() is None


def _fake_pm(tmp_path, owner, *, verified=True):
    return SimpleNamespace(
        NAME="hermes-feishu-card",
        _call=lambda *a, **k: {"home": str(tmp_path)},
        _owned=lambda member: owner,
        _install_spec_digest=lambda spec: "digest-of-" + str(spec),
        verify_installed=lambda member, python: verified,
    )


def _owner(version=None, digest=None):
    return {
        "version": version or cli.PACKAGE_VERSION,
        "spec_sha256": digest or "digest-of-spec",
    }


def test_repeat_install_skips_pm_admission(monkeypatch, tmp_path):
    detection = SimpleNamespace(root=tmp_path)
    runtime = tmp_path / "bin/python"
    monkeypatch.setattr(
        cli, "_check_runtime_hook_import",
        lambda python: {"status": "ok", "version": cli.PACKAGE_VERSION},
    )
    monkeypatch.setattr(cli, "_runtime_report_uses_installed_package", lambda *a: True)
    fake = _fake_pm(tmp_path, _owner())
    assert cli._pm_runtime_already_registered(
        detection, runtime, None, "spec", fake
    ) is True

    monkeypatch.setattr(cli, "_detect_hermes_runtime_python", lambda *a, **k: runtime)
    monkeypatch.setattr(pm_dependency, "available", lambda root: True)
    monkeypatch.setattr(cli, "_runtime_install_spec", lambda: "spec")
    monkeypatch.setattr(cli, "_pm_runtime_already_registered", lambda *a, **k: True)
    called = []
    monkeypatch.setattr(
        pm_dependency, "ensure",
        lambda *a, **k: called.append("ensure") or None,
    )
    assert cli._ensure_hermes_runtime_package(detection) is None
    assert called == []


def test_mismatched_version_digest_or_wheel_falls_through(monkeypatch, tmp_path):
    detection = SimpleNamespace(root=tmp_path)
    runtime = tmp_path / "bin/python"
    monkeypatch.setattr(
        cli, "_check_runtime_hook_import",
        lambda python: {"status": "ok", "version": cli.PACKAGE_VERSION},
    )
    monkeypatch.setattr(cli, "_runtime_report_uses_installed_package", lambda *a: True)
    assert cli._pm_runtime_already_registered(
        detection, runtime, None, "spec",
        _fake_pm(tmp_path, _owner(version="5.0.7")),
    ) is False
    assert cli._pm_runtime_already_registered(
        detection, runtime, None, "spec",
        _fake_pm(tmp_path, _owner(digest="other")),
    ) is False
    assert cli._pm_runtime_already_registered(
        detection, runtime, None, "spec",
        _fake_pm(tmp_path, _owner(), verified=False),
    ) is False
    assert cli._pm_runtime_already_registered(
        detection, runtime, None, "spec", _fake_pm(tmp_path, None),
    ) is False
