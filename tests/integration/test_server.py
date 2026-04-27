import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.server import FEISHU_MESSAGE_IDS_KEY, create_app


class FakeFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.fail_send = False
        self.update_failures_remaining = 0

    async def send_card(self, chat_id, card):
        if self.fail_send:
            raise RuntimeError("send unavailable")
        self.sent.append((chat_id, card))
        return f"feishu-message-{len(self.sent)}"

    async def update_card_message(self, message_id, card):
        if self.update_failures_remaining > 0:
            self.update_failures_remaining -= 1
            raise RuntimeError("update unavailable")
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
    assert body["metrics"] == {
        "events_received": 0,
        "events_applied": 0,
        "events_ignored": 0,
        "events_rejected": 0,
        "feishu_send_attempts": 0,
        "feishu_send_successes": 0,
        "feishu_send_failures": 0,
        "feishu_update_attempts": 0,
        "feishu_update_successes": 0,
        "feishu_update_failures": 0,
        "feishu_update_retries": 0,
    }


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
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 3
    assert metrics["events_applied"] == 3
    assert metrics["events_ignored"] == 0
    assert metrics["events_rejected"] == 0
    assert metrics["feishu_send_attempts"] == 1
    assert metrics["feishu_send_successes"] == 1
    assert metrics["feishu_send_failures"] == 0
    assert metrics["feishu_update_attempts"] == 2
    assert metrics["feishu_update_successes"] == 2
    assert metrics["feishu_update_failures"] == 0
    assert metrics["feishu_update_retries"] == 0


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
    health = await test_client.get("/health")
    assert (await health.json())["metrics"]["events_rejected"] == 1


async def test_malformed_json_returns_400_json(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        data="{bad json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 400
    body = await response.json()
    assert body["ok"] is False
    assert "error" in body
    assert feishu_client.sent == []
    assert feishu_client.updated == []


async def test_non_object_json_payload_returns_400_json(client):
    test_client, feishu_client = client

    response = await test_client.post("/events", json=["not", "an", "object"])

    assert response.status == 400
    body = await response.json()
    assert body["ok"] is False
    assert "error" in body
    assert feishu_client.sent == []
    assert feishu_client.updated == []


async def test_event_before_started_is_not_applied(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 1, {"text": "提前到达"}),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": False}
    assert feishu_client.sent == []
    assert feishu_client.updated == []
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 1
    assert metrics["events_applied"] == 0
    assert metrics["events_ignored"] == 1


async def test_duplicate_started_does_not_send_again(client):
    test_client, feishu_client = client

    first = await test_client.post("/events", json=event_payload("message.started", 0))
    duplicate = await test_client.post("/events", json=event_payload("message.started", 0))

    assert first.status == 200
    assert await first.json() == {"ok": True, "applied": True}
    assert duplicate.status == 200
    assert await duplicate.json() == {"ok": True, "applied": False}
    assert len(feishu_client.sent) == 1
    assert feishu_client.updated == []


async def test_replayed_started_with_higher_sequence_does_not_block_later_delta(client):
    test_client, feishu_client = client

    first = await test_client.post("/events", json=event_payload("message.started", 0))
    replayed = await test_client.post("/events", json=event_payload("message.started", 5))
    thinking = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 1, {"text": "后续增量"}),
    )

    assert first.status == 200
    assert await first.json() == {"ok": True, "applied": True}
    assert replayed.status == 200
    assert await replayed.json() == {"ok": True, "applied": False}
    assert thinking.status == 200
    assert await thinking.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert len(feishu_client.updated) == 1
    assert "后续增量" in str(feishu_client.updated[0][1])


async def test_delta_after_completed_does_not_update_again(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )
    updates_after_completed = len(feishu_client.updated)

    response = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 2, {"text": "迟到增量"}),
    )

    assert response.status == 200
    assert (await response.json())["applied"] is False
    assert len(feishu_client.updated) == updates_after_completed


async def test_missing_feishu_message_id_returns_conflict_without_update(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    test_client.app[FEISHU_MESSAGE_IDS_KEY].pop("hermes-message-1")

    response = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 1, {"text": "需要更新"}),
    )

    assert response.status == 409
    body = await response.json()
    assert body == {"ok": False, "error": "feishu_message_id missing"}
    assert feishu_client.updated == []
    health = await test_client.get("/health")
    assert (await health.json())["metrics"]["events_rejected"] == 1


async def test_update_retries_once_and_reports_retry_metrics(client):
    test_client, feishu_client = client
    feishu_client.update_failures_remaining = 1

    started = await test_client.post("/events", json=event_payload("message.started", 0))
    thinking = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 1, {"text": "需要重试"}),
    )

    assert started.status == 200
    assert thinking.status == 200
    assert (await thinking.json()) == {"ok": True, "applied": True}
    assert len(feishu_client.updated) == 1
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["feishu_update_attempts"] == 2
    assert metrics["feishu_update_successes"] == 1
    assert metrics["feishu_update_failures"] == 1
    assert metrics["feishu_update_retries"] == 1


async def test_send_failure_returns_json_error_and_allows_started_retry(client):
    test_client, feishu_client = client
    feishu_client.fail_send = True

    failed = await test_client.post("/events", json=event_payload("message.started", 0))

    assert failed.status == 502
    failed_body = await failed.json()
    assert failed_body == {"ok": False, "error": "feishu send failed"}
    assert feishu_client.sent == []
    health_after_failure = await test_client.get("/health")
    failure_body = await health_after_failure.json()
    assert failure_body["active_sessions"] == 0
    assert failure_body["metrics"]["feishu_send_attempts"] == 1
    assert failure_body["metrics"]["feishu_send_failures"] == 1

    feishu_client.fail_send = False
    retried = await test_client.post("/events", json=event_payload("message.started", 0))

    assert retried.status == 200
    assert await retried.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
