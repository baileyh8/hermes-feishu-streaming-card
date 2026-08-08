"""Tests for card-freeze while an interaction is pending.

Feishu card updates are full replacements — any PATCH during a pending
clarify resets the multi-select dropdown and free-text input, wiping the
user's in-progress answer. Events and animations must be frozen until the
interaction completes.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import server as sidecar_server
from hermes_feishu_card.native_handoff import NativeHandoffStore


class FakeFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []

    async def send_card(self, chat_id, card, thread_id=None, reply_to_message_id=None):
        self.sent.append((chat_id, card, thread_id, reply_to_message_id))
        return f"feishu-message-{len(self.sent)}"

    async def update_card_message(self, message_id, card):
        self.updated.append((message_id, card))


def event_payload(event, sequence, data=None, *, chat_id="oc_abc", message_id="hermes-message-1"):
    return {
        "schema_version": "1",
        "event": event,
        "conversation_id": "conversation-1",
        "message_id": message_id,
        "chat_id": chat_id,
        "platform": "feishu",
        "sequence": sequence,
        "created_at": 1777017600.0 + sequence,
        "data": dict(data or {}),
    }


@pytest.fixture
async def client(tmp_path):
    feishu_client = FakeFeishuClient()
    app = sidecar_server.create_app(
        feishu_client,
        native_handoff_store=NativeHandoffStore(tmp_path / "handoff-state"),
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        yield test_client, app, feishu_client
    finally:
        await test_client.close()


async def test_streaming_events_do_not_update_card_while_interaction_pending(client):
    test_client, app, feishu_client = client

    # 1. interaction.requested -> card sent
    resp = await test_client.post("/events", json=event_payload(
        "interaction.requested", 1,
        {"interaction_id": "clarify-fz-1", "kind": "clarify", "prompt": "请选择",
         "options": [{"label": "A", "value": "A"}], "multi_select": True,
         "callback_token": "tok-fz"},
    ))
    assert resp.status == 200
    assert len(feishu_client.sent) == 1
    assert len(feishu_client.updated) == 0

    # 2. streaming events while pending -> MUST NOT update the card
    for seq, event in [(2, "thinking.delta"), (3, "tool.updated"), (4, "answer.delta")]:
        resp = await test_client.post("/events", json=event_payload(
            event, seq, {"text": "x", "tool_id": "t1", "status": "running", "answer": "y"},
        ))
        assert resp.status == 200

    assert len(feishu_client.updated) == 0, "pending interaction must freeze card updates"

    # 3. user submits via card callback -> card updates (shows 已选择)
    resp = await test_client.post("/card/actions", json={
        "schema": "2.0",
        "event": {
            "operator": {"open_id": "ou_test", "name": "测试用户"},
            "action": {
                "tag": "button", "value": {}, "name": "hfc_confirm_clarify-fz-1",
                "form_value": {"hfc_multi": ["A"], "hfc_other": ""},
            },
            "context": {"open_chat_id": "oc_abc", "open_message_id": "om_x"},
        },
    })
    assert resp.status == 200
    assert len(feishu_client.updated) >= 1, "interaction.completed must update the card"


async def test_normal_streaming_updates_still_work_without_interaction(client):
    test_client, app, feishu_client = client

    resp = await test_client.post("/events", json=event_payload(
        "message.started", 1, {},
    ))
    assert resp.status == 200
    resp = await test_client.post("/events", json=event_payload(
        "thinking.delta", 2, {"text": "思考中"},
    ))
    assert resp.status == 200
    assert len(feishu_client.updated) >= 1, "streaming updates must work without interaction"
