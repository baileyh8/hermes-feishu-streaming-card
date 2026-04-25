import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.server import create_app


class FakeFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []

    async def send_card(self, chat_id, card):
        self.sent.append((chat_id, card))
        return f"feishu-message-{len(self.sent)}"

    async def update_card_message(self, message_id, card):
        self.updated.append((message_id, card))


def event_payload(event, sequence, data=None):
    return {
        "schema_version": "1",
        "event": event,
        "conversation_id": "conversation-1",
        "message_id": "hermes-message-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "sequence": sequence,
        "created_at": 1777017600.0 + sequence,
        "data": data or {},
    }


@pytest.fixture
async def client():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        yield test_client, feishu_client
    finally:
        await test_client.close()


async def test_health_reports_healthy_status_and_active_sessions(client):
    test_client, _ = client

    response = await test_client.get("/health")

    assert response.status == 200
    body = await response.json()
    assert body["status"] == "healthy"
    assert body["active_sessions"] == 0


async def test_event_lifecycle_sends_then_updates_final_card(client):
    test_client, feishu_client = client

    started = await test_client.post(
        "/events",
        json=event_payload("message.started", 0),
    )
    thinking = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 1, {"text": "先分析"}),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 2, {"answer": "最终答案"}),
    )

    assert started.status == 200
    assert await started.json() == {"ok": True, "applied": True}
    assert thinking.status == 200
    assert (await thinking.json())["applied"] is True
    assert completed.status == 200
    assert (await completed.json())["applied"] is True

    assert len(feishu_client.sent) == 1
    assert feishu_client.sent[0][0] == "oc_abc"
    assert len(feishu_client.updated) >= 1
    assert all(message_id == "feishu-message-1" for message_id, _ in feishu_client.updated)
    assert "最终答案" in str(feishu_client.updated[-1][1])


async def test_invalid_event_returns_400_json(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload("bad.event", 1),
    )

    assert response.status == 400
    body = await response.json()
    assert body["ok"] is False
    assert "error" in body
    assert feishu_client.sent == []
    assert feishu_client.updated == []
