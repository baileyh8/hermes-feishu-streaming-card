"""A media/card send into a topic must reply INSIDE the topic, not start a new one.

Feishu's ``message.create`` accepts ``chat_id`` but not ``thread_id``: an unanchored create in a
topic group is a *new topic* in the UI, which is how an image ended up in the main message flow
while the text of the same reply landed correctly. The hook rewrites the underlying
``_send_raw_message``, so anything it does to the topic binding silently overrides the adapter.

Contract asserted here: when a topic binding exists but no reply anchor was supplied, resolve an
anchor inside the topic and reply to it; only fall back to the old unanchored behaviour when no
anchor can be resolved.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime

THREAD_ID = "omt_19ccc776d08fdcb7"
IN_TOPIC_ANCHOR = "om_x100b65a0b532e4acb1b4060ff134af7"


class _FakeAdapter:
    """Minimal adapter double: records what the wrapped send receives."""

    def __init__(self, *, anchor: str = IN_TOPIC_ANCHOR, raises: bool = False) -> None:
        self._anchor = anchor
        self._raises = raises
        self.fetch_calls: list[str] = []
        self.sent: list[dict] = []
        self.routes: list[str] = []

    async def _fetch_last_message_in_thread(self, thread_id: str) -> str:
        self.fetch_calls.append(thread_id)
        if self._raises:
            raise RuntimeError("feishu list failed")
        return self._anchor


@pytest.fixture
def adapter(monkeypatch):
    """Install a wrapped-send double on the class, the way the hook does at runtime."""

    def _install(fake: _FakeAdapter) -> _FakeAdapter:
        async def _original(self, **kwargs):
            self.sent.append(kwargs)
            self.routes.append(hook_runtime._HFC_NATIVE_HANDOFF_ROUTE.get())
            return SimpleNamespace(success=True, message_id="om_sent")

        monkeypatch.setattr(
            _FakeAdapter, "_hfc_original_send_raw_message", _original, raising=False
        )
        return fake

    return _install


def _send(fake: _FakeAdapter, **overrides):
    kwargs = {
        "chat_id": "oc_b73b4bf89045f34ff0c501a3d4f1d911",
        "msg_type": "image",
        "payload": '{"image_key":"img_v3_test"}',
        "reply_to": None,
        "metadata": {"thread_id": THREAD_ID},
    }
    kwargs.update(overrides)
    return hook_runtime._hfc_send_raw_message_with_native_handoff_route(fake, **kwargs)


async def test_unanchored_topic_send_replies_inside_the_topic(adapter):
    """The regression: the topic binding used to be stripped, so the send became a new topic."""
    fake = adapter(_FakeAdapter())
    await _send(fake)

    assert fake.fetch_calls == [THREAD_ID]
    assert fake.sent[0]["reply_to"] == IN_TOPIC_ANCHOR
    # The topic binding must survive: the adapter derives reply_in_thread from it.
    assert fake.sent[0]["metadata"]["thread_id"] == THREAD_ID


async def test_metadata_anchor_is_used_without_an_extra_api_call(adapter):
    fake = adapter(_FakeAdapter())
    await _send(fake, metadata={"thread_id": THREAD_ID, "reply_to_message_id": "om_x_known"})

    assert fake.sent[0]["reply_to"] == "om_x_known"
    assert fake.fetch_calls == []


async def test_an_explicit_reply_target_is_never_overridden(adapter):
    fake = adapter(_FakeAdapter())
    await _send(fake, reply_to="om_x_explicit")

    assert fake.sent[0]["reply_to"] == "om_x_explicit"
    assert fake.fetch_calls == []


async def test_unresolvable_anchor_keeps_the_previous_fallback(adapter):
    """Fallback path stays covered: no anchor reachable -> old unanchored behaviour."""
    fake = adapter(_FakeAdapter(anchor=""))
    await _send(fake)

    assert fake.fetch_calls == [THREAD_ID]
    assert not fake.sent[0].get("reply_to")
    assert "thread_id" not in fake.sent[0]["metadata"]


async def test_anchor_lookup_failure_still_sends(adapter):
    """A broken lookup must degrade, never block the delivery."""
    fake = adapter(_FakeAdapter(raises=True))
    await _send(fake)

    assert len(fake.sent) == 1
    assert not fake.sent[0].get("reply_to")


async def test_a_send_without_a_topic_is_left_alone(adapter):
    fake = adapter(_FakeAdapter())
    await _send(fake, metadata={})

    assert fake.fetch_calls == []
    assert not fake.sent[0].get("reply_to")


async def test_native_handoff_still_reports_a_thread_reply_route(adapter, monkeypatch):
    """UUID/route derivation must see the real route, or chunk identity drifts."""
    fake = adapter(_FakeAdapter())
    token = hook_runtime._HFC_NATIVE_HANDOFF_SEND_TRACKER.set(
        {"descriptor": "test", "next_ordinal": 0, "fallback_ordinal": None,
         "required": {}, "failures": {}}
    )
    try:
        await _send(fake)
    finally:
        hook_runtime._HFC_NATIVE_HANDOFF_SEND_TRACKER.reset(token)

    assert fake.routes == ["thread-reply"]
