import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import server as sidecar_server
from hermes_feishu_card.server import FEISHU_MESSAGE_IDS_KEY, create_app


class FakeFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.fail_send = False
        self.update_failures_remaining = 0
        self.update_delay = 0.0

    async def send_card(self, chat_id, card):
        if self.fail_send:
            raise RuntimeError("send unavailable")
        self.sent.append((chat_id, card))
        return f"feishu-message-{len(self.sent)}"

    async def update_card_message(self, message_id, card):
        if self.update_delay:
            await asyncio.sleep(self.update_delay)
        if self.update_failures_remaining > 0:
            self.update_failures_remaining -= 1
            raise RuntimeError("update unavailable")
        self.updated.append((message_id, card))


def event_payload(
    event,
    sequence,
    data=None,
    *,
    conversation_id="conversation-1",
    message_id="hermes-message-1",
    chat_id="oc_abc",
):
    return {
        "schema_version": "1",
        "event": event,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "chat_id": chat_id,
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


async def test_card_config_customizes_header_title():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client, card_config={"title": "研发助手"})
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload("message.started", 0),
        )
    finally:
        await test_client.close()

    assert response.status == 200
    assert feishu_client.sent[0][1]["header"]["title"]["content"] == "研发助手"


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


async def test_parallel_message_sessions_update_their_own_feishu_cards(client):
    test_client, feishu_client = client

    msg1 = {"conversation_id": "conversation-1", "message_id": "hermes-message-1"}
    msg2 = {"conversation_id": "conversation-2", "message_id": "hermes-message-2"}

    started1 = await test_client.post(
        "/events",
        json=event_payload("message.started", 0, **msg1),
    )
    delta1 = await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "第一条"}, **msg1),
    )
    started2 = await test_client.post(
        "/events",
        json=event_payload("message.started", 0, **msg2),
    )
    tool2 = await test_client.post(
        "/events",
        json=event_payload(
            "tool.updated",
            1,
            {
                "tool_id": "tool-2",
                "name": "search",
                "status": "running",
                "detail": "第二条工具",
            },
            **msg2,
        ),
    )
    completed1 = await test_client.post(
        "/events",
        json=event_payload("message.completed", 2, {"answer": "第一条完成"}, **msg1),
    )
    updates_before_late = len(feishu_client.updated)
    late1 = await test_client.post(
        "/events",
        json=event_payload("tool.updated", 3, {"tool_id": "late"}, **msg1),
    )

    assert started1.status == 200
    assert delta1.status == 200
    assert started2.status == 200
    assert tool2.status == 200
    assert completed1.status == 200
    assert late1.status == 200
    assert await late1.json() == {"ok": True, "applied": False}
    assert len(feishu_client.updated) == updates_before_late

    assert [item[0] for item in feishu_client.sent] == ["oc_abc", "oc_abc"]
    updates_by_message = {}
    for feishu_message_id, card in feishu_client.updated:
        updates_by_message.setdefault(feishu_message_id, []).append(str(card))
    assert set(updates_by_message) == {"feishu-message-1", "feishu-message-2"}
    assert any("第一条完成" in card for card in updates_by_message["feishu-message-1"])
    assert any("`search`: running" in card for card in updates_by_message["feishu-message-2"])


async def test_streaming_deltas_are_throttled_but_terminal_event_updates(client):
    test_client, feishu_client = client

    started = await test_client.post("/events", json=event_payload("message.started", 0))
    first_delta = await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "第一段"}),
    )
    second_delta = await test_client.post(
        "/events",
        json=event_payload("answer.delta", 2, {"text": "第二段"}),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 3, {"answer": "最终答案"}),
    )

    assert started.status == 200
    assert first_delta.status == 200
    assert second_delta.status == 200
    assert await second_delta.json() == {"ok": True, "applied": True}
    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}

    assert len(feishu_client.sent) == 1
    assert len(feishu_client.updated) == 2
    assert "第一段" in str(feishu_client.updated[0][1])
    assert "第二段" not in str(feishu_client.updated[0][1])
    assert "最终答案" in str(feishu_client.updated[-1][1])
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 4
    assert metrics["events_applied"] == 4
    assert metrics["events_rejected"] == 0
    assert metrics["feishu_update_attempts"] == 2
    assert metrics["feishu_update_successes"] == 2


async def test_terminal_event_with_stale_sequence_still_finalizes_card(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 100, {"text": "部分答案"}),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 90, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    assert "最终答案" in str(feishu_client.updated[-1][1])
    assert "已完成" in str(feishu_client.updated[-1][1])


async def test_concurrent_streaming_deltas_share_message_update_window(client):
    test_client, feishu_client = client
    feishu_client.update_delay = 0.05

    started = await test_client.post("/events", json=event_payload("message.started", 0))
    first_delta, second_delta = await asyncio.gather(
        test_client.post(
            "/events",
            json=event_payload("answer.delta", 1, {"text": "第一段"}),
        ),
        test_client.post(
            "/events",
            json=event_payload("answer.delta", 2, {"text": "第二段"}),
        ),
    )

    assert started.status == 200
    assert first_delta.status == 200
    assert second_delta.status == 200
    assert await first_delta.json() == {"ok": True, "applied": True}
    assert await second_delta.json() == {"ok": True, "applied": True}
    assert len(feishu_client.updated) == 1
    assert "第一段" in str(feishu_client.updated[0][1])
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 3
    assert metrics["events_applied"] == 3
    assert metrics["feishu_update_attempts"] == 1
    assert metrics["feishu_update_successes"] == 1


async def test_terminal_event_waits_for_update_window(client, monkeypatch):
    test_client, feishu_client = client
    now = [100.0]
    sleeps = []
    monkeypatch.setattr(sidecar_server.time, "monotonic", lambda: now[0])

    async def fake_sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr(sidecar_server.asyncio, "sleep", fake_sleep)

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post("/events", json=event_payload("answer.delta", 1, {"text": "片段"}))
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 2, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    assert sleeps == [sidecar_server.UPDATE_MIN_INTERVAL_SECONDS]
    assert len(feishu_client.updated) == 2
    assert "最终答案" in str(feishu_client.updated[-1][1])


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


async def test_terminal_update_failure_still_accepts_event_to_prevent_native_fallback(client):
    test_client, feishu_client = client
    feishu_client.update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS

    await test_client.post("/events", json=event_payload("message.started", 0))
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_applied"] == 2
    assert metrics["events_rejected"] == 0
    assert metrics["feishu_update_attempts"] == sidecar_server.UPDATE_MAX_ATTEMPTS
    assert metrics["feishu_update_failures"] == sidecar_server.UPDATE_MAX_ATTEMPTS


async def test_terminal_update_failure_is_retried_in_background(client, monkeypatch):
    test_client, feishu_client = client
    feishu_client.update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(sidecar_server.asyncio, "sleep", fake_sleep)

    await test_client.post("/events", json=event_payload("message.started", 0))
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    for _ in range(10):
        if feishu_client.updated:
            break
        await asyncio.sleep(0)

    assert len(feishu_client.updated) == 1
    assert "最终答案" in str(feishu_client.updated[-1][1])


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
