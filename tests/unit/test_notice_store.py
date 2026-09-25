import json
import os

import pytest

from hermes_feishu_card.notice_lifecycle import NoticeScope, RestartNoticeRegistry


SCOPE = NoticeScope("work", "work:alpha", "oc_test", "omt_test")


def registry(directory, **kwargs):
    return RestartNoticeRegistry(directory=directory, identity_for_scope=lambda scope: "a" * 64, **kwargs)


def test_restored_ownership_keeps_exact_scope_retry_and_generation(tmp_path):
    wall = [1000.0]
    ticks = [50.0]
    first = registry(tmp_path, wall_clock=lambda: wall[0], clock=lambda: ticks[0])
    assert first.register(SCOPE, "om_first")
    [(mid, generation)] = first.snapshot(SCOPE)
    first.defer(SCOPE, mid, generation)
    wall[0] += 10
    ticks[0] = 1  # A fresh process has a different monotonic origin.
    second = registry(tmp_path, wall_clock=lambda: wall[0], clock=lambda: ticks[0])
    assert second.snapshot(SCOPE) == ((mid, generation),)
    assert not second.snapshot(NoticeScope("work", "work:alpha", "oc_test", ""))
    assert not second.ready(SCOPE, mid, generation)
    ticks[0] += 21
    assert second.ready(SCOPE, mid, generation)
    second.discard(SCOPE, mid, generation)
    third = registry(tmp_path, wall_clock=lambda: wall[0], clock=lambda: ticks[0])
    assert not third.snapshot(SCOPE)
    assert third.register(SCOPE, mid)
    assert third.snapshot(SCOPE)[0][1] > generation
    third.discard(SCOPE, mid, generation)
    assert third.snapshot(SCOPE)
    payload = (tmp_path / "restart-notices-v1/owned.json").read_text()
    assert not any(key in payload for key in ("answer", "callback", "token", "secret"))
    if os.name != "nt":
        assert (tmp_path / "restart-notices-v1").stat().st_mode & 0o777 == 0o700
        assert (tmp_path / "restart-notices-v1/owned.json").stat().st_mode & 0o777 == 0o600


def test_changed_application_or_policy_never_restores_ownership(tmp_path):
    assert registry(tmp_path).register(SCOPE, "om_notice")
    for identity in ("b" * 64, None):
        changed = RestartNoticeRegistry(directory=tmp_path, identity_for_scope=lambda scope: identity)
        assert not changed.snapshot(SCOPE)


def test_corrupt_ledger_is_not_overwritten_or_partially_restored(tmp_path):
    first = registry(tmp_path)
    assert first.register(SCOPE, "om_notice")
    path = tmp_path / "restart-notices-v1/owned.json"
    body = json.loads(path.read_text())
    body["record"]["notices"][0]["message_id"] = "om_edited"
    path.write_text(json.dumps(body))
    before = path.read_bytes()
    restored = registry(tmp_path)
    assert restored.persistence_state == "unavailable"
    assert not restored.snapshot(SCOPE)
    assert not restored.register(SCOPE, "om_other")
    assert path.read_bytes() == before


def test_failed_write_never_acknowledges_durable_registration(tmp_path, monkeypatch):
    from hermes_feishu_card import native_handoff
    current = registry(tmp_path)
    write = native_handoff._atomic_write_private
    def fail(*args):
        raise OSError("fixture full disk")
    monkeypatch.setattr(native_handoff, "_atomic_write_private", fail)
    assert not current.register(SCOPE, "om_notice")
    assert not current.snapshot(SCOPE)
    assert current.persistence_state == "unavailable"
    monkeypatch.setattr(native_handoff, "_atomic_write_private", write)
    assert current.register(SCOPE, "om_notice")
    assert current.persistence_state == "ready"
    assert registry(tmp_path).snapshot(SCOPE)


def test_expired_ownership_releases_capacity_without_deleting_messages(tmp_path):
    now = [1000.0]
    first = registry(tmp_path, wall_clock=lambda: now[0], max_members=1)
    assert first.register(SCOPE, "om_old")
    assert not first.register(SCOPE, "om_full")
    now[0] += 7 * 24 * 3600 + 1
    assert not registry(tmp_path, wall_clock=lambda: now[0]).snapshot(SCOPE)
    assert first.register(SCOPE, "om_new")
    assert [mid for mid, _ in first.snapshot(SCOPE)] == ["om_new"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink permission contract")
def test_notice_store_refuses_symlink_without_writing_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "restart-notices-v1").symlink_to(target, target_is_directory=True)
    current = registry(tmp_path)
    assert current.persistence_state == "unavailable"
    assert not current.register(SCOPE, "om_notice")
    assert not list(target.iterdir())
