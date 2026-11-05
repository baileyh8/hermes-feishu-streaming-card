"""Managed PM install spec derivation (#494)."""
import json

from hermes_feishu_card import cli


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
