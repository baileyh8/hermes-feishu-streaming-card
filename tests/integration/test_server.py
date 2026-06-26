import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.bots import RouteResult
from hermes_feishu_card import server as sidecar_server
from hermes_feishu_card.server import FEISHU_MESSAGE_IDS_KEY, create_app


_REAL_ASYNCIO_SLEEP = asyncio.sleep


class FakeFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.fail_send = False
        self.update_failures_remaining = 0
        self.update_error_message = "update unavailable"
        self.update_delay = 0.0

    async def send_card(self, chat_id, card, thread_id=None, reply_to_message_id=None):
        if self.fail_send:
            raise RuntimeError("send unavailable")
        self.sent.append((chat_id, card, thread_id, reply_to_message_id))
        return f"feishu-message-{len(self.sent)}"

    async def update_card_message(self, message_id, card):
        if self.update_delay:
            await asyncio.sleep(self.update_delay)
        if self.update_failures_remaining > 0:
            self.update_failures_remaining -= 1
            raise RuntimeError(self.update_error_message)
        self.updated.append((message_id, card))


class ReorderingFeishuClient(FakeFeishuClient):
    def __init__(self):
        super().__init__()
        self.update_calls = 0

    async def update_card_message(self, message_id, card):
        self.update_calls += 1
        update_call = self.update_calls
        if update_call == 1:
            await asyncio.sleep(0.05)
        self.updated.append((message_id, card))


class FakeBotRegistry:
    def safe_diagnostics(self):
        return {
            "default_bot": "default",
            "bot_count": 2,
            "chat_binding_count": 1,
            "secret": "registry-secret",
        }


class FakeFeishuClientFactory:
    def __init__(self, cards=None, profile_card=None):
        self.registry = FakeBotRegistry()
        self.clients = {
            "default": FakeFeishuClient(),
            "sales": FakeFeishuClient(),
        }
        self.cards = cards or {}
        self.profile_card = profile_card or {}

    def get_client(self, bot_id):
        return self.clients[bot_id]

    def card_config_for_bot(self, bot_id, base_card=None, profile_card=None):
        card = dict(base_card or {})
        card.update(profile_card or self.profile_card)
        card.update(self.cards.get(bot_id, {}))
        return card


def event_payload(
    event,
    sequence,
    data=None,
    *,
    conversation_id="conversation-1",
    message_id="hermes-message-1",
    chat_id="oc_abc",
    thread_id="",
):
    payload = {
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
    if thread_id:
        payload["thread_id"] = thread_id
    return payload


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


async def wait_for_card_update(feishu_client, expected_text, attempts=80):
    for _ in range(attempts):
        for message_id, card in reversed(feishu_client.updated):
            if expected_text in str(card):
                return message_id, card
        await _REAL_ASYNCIO_SLEEP(0.01)
    raise AssertionError(f"card update containing {expected_text!r} was not observed")


async def wait_for_metric(test_client, metric_name, expected_value, attempts=80):
    body = None
    for _ in range(attempts):
        health = await test_client.get("/health")
        body = await health.json()
        if body["metrics"][metric_name] == expected_value:
            return body["metrics"]
        await _REAL_ASYNCIO_SLEEP(0.01)
    raise AssertionError(
        f"metric {metric_name!r} did not become {expected_value!r}: {body}"
    )


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
        "cron_cards_sent": 0,
        "cron_fallbacks": 0,
    }
    assert body["reply_index"] == {"entries": 0, "last_lookup": {}}
    assert body["cron"] == {"cards_sent": 0, "fallbacks": 0}
    assert body["profile_diagnostics"] == {}


async def test_health_reports_profile_diagnostics_for_profile_events():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                {"profile_id": "work", "profile_source": "env"},
            ),
        )
        assert response.status == 200
        health = await test_client.get("/health")
        body = await health.json()
    finally:
        await test_client.close()

    assert body["profile_diagnostics"]["work"]["events"] == 1
    assert body["profile_diagnostics"]["work"]["last_profile_source"] == "env"


async def test_profiled_hook_like_started_and_delta_apply_to_same_session():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    profile_data = {"profile_id": "default", "profile_source": "fallback_default"}
    await test_client.start_server()
    try:
        started = await test_client.post(
            "/events",
            json=event_payload("message.started", 0, profile_data),
        )
        delta = await test_client.post(
            "/events",
            json=event_payload("answer.delta", 1, {"text": "hello", **profile_data}),
        )
        started_body = await started.json()
        delta_body = await delta.json()
    finally:
        await test_client.close()

    assert started.status == 200
    assert started_body == {"ok": True, "applied": True}
    assert delta.status == 200
    assert delta_body == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert len(feishu_client.updated) == 1


async def test_profile_diagnostics_sanitizes_invalid_profile_keys():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                {"profile_id": "bad:profile/path", "profile_source": "env"},
            ),
        )
        assert response.status == 200
        health = await test_client.get("/health")
        body = await health.json()
    finally:
        await test_client.close()

    assert "bad:profile/path" not in body["profile_diagnostics"]
    assert body["profile_diagnostics"]["default"]["events"] == 1


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

    await wait_for_card_update(feishu_client, "最终答案")
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


async def test_completed_without_deltas_updates_started_card(client):
    test_client, feishu_client = client

    started = await test_client.post(
        "/events",
        json=event_payload("message.started", 0),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            1,
            {"answer": "DeepSeek 一次性返回的最终答案"},
        ),
    )

    assert started.status == 200
    assert await started.json() == {"ok": True, "applied": True}
    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    await wait_for_card_update(feishu_client, "DeepSeek 一次性返回的最终答案")
    assert len(feishu_client.updated) == 1
    assert "DeepSeek 一次性返回的最终答案" in str(feishu_client.updated[-1][1])

    health = await test_client.get("/health")
    body = await health.json()
    assert body["sessions"]["hermes-message-1"]["status"] == "completed"
    assert body["sessions"]["hermes-message-1"]["answer_chars"] > 0
    assert body["metrics"]["feishu_update_attempts"] == 1


async def test_message_started_sends_card_as_thread_reply(client):
    test_client, feishu_client = client

    started = await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_user_message"},
            conversation_id="conversation-1",
            message_id="om_user_message",
            thread_id="omt_thread",
        ),
    )

    assert started.status == 200
    assert await started.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert feishu_client.sent[0][0] == "oc_abc"
    assert feishu_client.sent[0][2] == "omt_thread"
    assert feishu_client.sent[0][3] == "om_user_message"


async def test_interaction_request_renders_buttons_and_callback_resolves(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    requested = await test_client.post(
        "/events",
        json=event_payload(
            "interaction.requested",
            1,
            {
                "interaction_id": "approval-1",
                "kind": "approval",
                "prompt": "允许执行命令吗？",
                "description": "rm -rf /tmp/demo",
                "options": [
                    {"label": "允许一次", "value": "once", "style": "primary"},
                    {"label": "拒绝", "value": "deny", "style": "danger"},
                ],
            },
        ),
    )

    assert requested.status == 200
    assert (await requested.json()) == {
        "ok": True,
        "applied": True,
        "interaction_mode": "callback",
    }
    interaction_card = feishu_client.updated[-1][1]
    button = next(
        element
        for element in interaction_card["body"]["elements"]
        if element.get("tag") == "button"
    )
    action_value = button["behaviors"][0]["value"]

    callback = await test_client.post(
        "/card/actions",
        json={
            "event": {
                "operator": {"open_id": "ou_bailey", "name": "Bailey"},
                "context": {"open_chat_id": "oc_abc"},
                "action": {"value": action_value},
            }
        },
    )
    result = await test_client.get("/interactions/approval-1")

    assert callback.status == 200
    callback_body = await callback.json()
    assert callback_body["ok"] is True
    assert callback_body["toast"]["type"] == "success"
    assert result.status == 200
    assert await result.json() == {
        "ok": True,
        "status": "completed",
        "choice": "once",
        "choice_label": "允许一次",
        "interaction_id": "approval-1",
    }
    assert "已选择：允许一次" in str(feishu_client.updated[-1][1])


async def test_interaction_request_uses_text_fallback_when_configured():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client, card_config={"interaction_mode": "text"})
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))
        requested = await test_client.post(
            "/events",
            json=event_payload(
                "interaction.requested",
                1,
                {
                    "interaction_id": "clarify-1",
                    "kind": "clarify",
                    "prompt": "请选择处理方式",
                    "options": [
                        {"label": "删除空文件", "value": "delete"},
                        {"label": "保留并补索引", "value": "keep"},
                    ],
                },
            ),
        )

        assert requested.status == 200
        assert (await requested.json())["interaction_mode"] == "text"
        interaction_card = feishu_client.updated[-1][1]
        content = str(interaction_card)
        assert not any(
            element.get("tag") == "button"
            for element in interaction_card["body"]["elements"]
        )
        assert "1. 删除空文件" in content
        assert "2. 保留并补索引" in content
    finally:
        await test_client.close()


async def test_completed_card_summary_can_be_looked_up_by_feishu_message_id(client):
    test_client, _ = client
    long_answer = "最终答案" * 1000

    missing = await test_client.get("/messages/feishu-message-1/summary")
    await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"profile_id": "work"},
        ),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            1,
            {"answer": long_answer, "profile_id": "work"},
        ),
    )
    found = await test_client.get("/messages/feishu-message-1/summary")

    assert missing.status == 404
    assert await missing.json() == {"ok": False, "error": "not found"}
    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    assert found.status == 200
    body = await found.json()
    assert body["ok"] is True
    assert body["profile_id"] == "work"
    assert body["chat_id"] == "oc_abc"
    assert body["message_id"] == "feishu-message-1"
    assert body["summary"] == long_answer[:4000]
    assert len(body["summary"]) == 4000


async def test_blank_completed_card_summary_is_not_indexed(client):
    test_client, _ = client

    await test_client.post(
        "/events",
        json=event_payload("message.started", 0),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "   "}),
    )
    found = await test_client.get("/messages/feishu-message-1/summary")

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    assert found.status == 404
    assert await found.json() == {"ok": False, "error": "not found"}


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


async def test_cron_completed_event_sends_completed_card_without_started(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            0,
            {"answer": "定时结果", "delivery_kind": "cron"},
            message_id="cron_1",
        ),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert "定时结果" in str(feishu_client.sent[0][1])
    assert feishu_client.updated == []
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 1
    assert metrics["events_applied"] == 1
    assert metrics["events_ignored"] == 0
    assert metrics["cron_cards_sent"] == 1


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
    await wait_for_card_update(feishu_client, "第一条完成")
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
    await wait_for_card_update(feishu_client, "最终答案")
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
    await wait_for_card_update(feishu_client, "最终答案")
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
    for _ in range(20):
        if feishu_client.updated:
            break
        await asyncio.sleep(0.01)
    assert len(feishu_client.updated) == 1
    assert "第一段" in str(feishu_client.updated[0][1])
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 3
    assert metrics["events_applied"] == 3
    assert metrics["feishu_update_attempts"] == 1
    assert metrics["feishu_update_successes"] == 1


async def test_concurrent_card_updates_preserve_newer_content(monkeypatch):
    feishu_client = ReorderingFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    monkeypatch.setattr(sidecar_server, "UPDATE_MIN_INTERVAL_SECONDS", 0)
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))
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

        assert first_delta.status == 200
        assert second_delta.status == 200
        for _ in range(20):
            if len(feishu_client.updated) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(feishu_client.updated) == 2
        assert "第一段第二段" in str(feishu_client.updated[-1][1])
    finally:
        await test_client.close()


async def test_terminal_update_is_not_blocked_by_streaming_update_backlog(client, monkeypatch):
    test_client, feishu_client = client
    feishu_client.update_delay = 0.05
    monkeypatch.setattr(sidecar_server, "UPDATE_MIN_INTERVAL_SECONDS", 0)

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "片段0"}),
    )
    deltas = await asyncio.gather(
        *[
            test_client.post(
                "/events",
                json=event_payload("answer.delta", sequence, {"text": f"片段{sequence}"}),
            )
            for sequence in range(2, 12)
        ]
    )

    started_at = asyncio.get_running_loop().time()
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 12, {"answer": "最终答案"}),
    )
    elapsed = asyncio.get_running_loop().time() - started_at

    assert all(response.status == 200 for response in deltas)
    assert completed.status == 200
    assert elapsed < 0.5
    await wait_for_card_update(feishu_client, "最终答案")
    assert "最终答案" in str(feishu_client.updated[-1][1])
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 13
    assert metrics["events_applied"] == 13
    assert metrics["feishu_update_attempts"] <= 3
    assert metrics["feishu_update_failures"] == 0


async def test_terminal_event_ack_does_not_wait_for_slow_card_patch(client, monkeypatch):
    test_client, feishu_client = client
    feishu_client.update_delay = 0.25
    monkeypatch.setattr(sidecar_server, "UPDATE_MIN_INTERVAL_SECONDS", 0)

    await test_client.post("/events", json=event_payload("message.started", 0))

    started_at = asyncio.get_running_loop().time()
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )
    elapsed = asyncio.get_running_loop().time() - started_at

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    assert elapsed < 0.1
    assert feishu_client.updated == []

    for _ in range(40):
        if feishu_client.updated:
            break
        await asyncio.sleep(0.01)
    assert "最终答案" in str(feishu_client.updated[-1][1])


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
    await wait_for_card_update(feishu_client, "最终答案")
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
    metrics = await wait_for_metric(
        test_client,
        "feishu_update_attempts",
        sidecar_server.UPDATE_MAX_ATTEMPTS + 1,
        attempts=180,
    )
    assert metrics["events_applied"] == 2
    assert metrics["events_rejected"] == 0
    assert metrics["feishu_update_attempts"] == 4  # 3 from _update_card + 1 from _retry_terminal
    assert metrics["feishu_update_failures"] == sidecar_server.UPDATE_MAX_ATTEMPTS


async def test_health_reports_last_attachment_event_for_native_delivery(client):
    test_client, _ = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    completed = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            1,
            {
                "answer": "最终答案",
                "attachments": [
                    {"kind": "image", "name": "cover.png", "summary": "cover.png"}
                ],
            },
        ),
    )

    assert completed.status == 200
    health = await test_client.get("/health")
    diagnostics = (await health.json())["diagnostics"]
    assert diagnostics["last_attachment_event"] == {
        "message_id": "hermes-message-1",
        "event": "message.completed",
        "attachment_count": 1,
        "native_delivery": "allowed",
    }


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


async def test_started_routes_card_to_bound_bot_client():
    factory = FakeFeishuClientFactory()

    def bot_router(event):
        assert event.chat_id == "oc_sales"
        return RouteResult("sales", "bindings.chats")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        status = response.status
        body = await response.json()
    finally:
        await test_client.close()

    assert status == 200
    assert body == {"ok": True, "applied": True}
    assert factory.clients["default"].sent == []
    assert len(factory.clients["sales"].sent) == 1
    assert factory.clients["sales"].sent[0][0] == "oc_sales"


async def test_started_card_title_uses_bot_over_profile_and_global():
    factory = FakeFeishuClientFactory(
        cards={"sales": {"title": "Sales Bot"}},
        profile_card={"title": "Profile"},
    )

    def bot_router(event):
        return RouteResult("sales", "bindings.chats")

    app = create_app(
        factory,
        card_config={"title": "Global"},
        bot_router=bot_router,
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                chat_id="oc_sales",
            ),
        )
    finally:
        await test_client.close()

    assert response.status == 200
    sent_card = factory.clients["sales"].sent[0][1]
    assert sent_card["header"]["title"]["content"] == "Sales Bot"


@pytest.mark.parametrize("profile_id", ["bad:profile/path", "", "x" * 65])
async def test_invalid_profile_routes_with_default_factory_and_health_key(profile_id):
    factory = FakeFeishuClientFactory()

    def bot_router(event):
        return RouteResult("sales", "bindings.chats")

    app = create_app({"default": factory, "sales": factory}, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                {"profile_id": profile_id, "profile_source": "env"},
                chat_id="oc_sales",
            ),
        )
        status = response.status
        body = await response.json()
        health = await test_client.get("/health")
        health_body = await health.json()
    finally:
        await test_client.close()

    assert status == 200
    assert body == {"ok": True, "applied": True}
    assert factory.clients["default"].sent == []
    assert len(factory.clients["sales"].sent) == 1
    assert profile_id not in health_body["profile_diagnostics"]
    assert health_body["profile_diagnostics"]["default"]["events"] == 1
    assert health_body["routing"]["last_route_error"] == ""


async def test_update_reuses_original_bot_without_rerouting():
    factory = FakeFeishuClientFactory()
    route_calls = 0

    def bot_router(event):
        nonlocal route_calls
        route_calls += 1
        if route_calls == 1:
            return ("sales", "bindings.chats")
        raise AssertionError("updates must not reroute")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        started = await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        completed = await test_client.post(
            "/events",
            json=event_payload(
                "message.completed",
                1,
                {"answer": "成交"},
                chat_id="oc_sales",
            ),
        )
        await wait_for_card_update(factory.clients["sales"], "成交")
    finally:
        await test_client.close()

    assert started.status == 200
    assert completed.status == 200
    assert route_calls == 1
    assert factory.clients["default"].updated == []
    assert len(factory.clients["sales"].updated) == 1
    assert factory.clients["sales"].updated[0][0] == "feishu-message-1"


async def test_health_reports_safe_routing_diagnostics_without_secrets():
    factory = FakeFeishuClientFactory()

    def bot_router(event):
        return ("sales", "bindings.chats")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        response = await test_client.get("/health")
        status = response.status
        body = await response.json()
    finally:
        await test_client.close()

    assert status == 200
    routing = body["routing"]
    assert routing["default_bot"] == "default"
    assert routing["bot_count"] == 2
    assert routing["chat_binding_count"] == 1
    assert routing["last_route"] == {
        "message_id": "hermes-message-1",
        "chat_id": "oc_sales",
        "bot_id": "sales",
        "reason": "bindings.chats",
    }
    assert routing["last_route_error"] == ""
    assert "registry-secret" not in str(body)
    assert "secret" not in routing


async def test_health_routing_groups_multi_profile_diagnostics_without_secrets():
    factories = {
        "default": FakeFeishuClientFactory(),
        "work": FakeFeishuClientFactory(),
    }

    def bot_router(event):
        return ("sales", "bindings.chats")

    app = create_app(factories, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                {"profile_id": "work", "profile_source": "env"},
                chat_id="oc_sales",
            ),
        )
        response = await test_client.get("/health")
        body = await response.json()
    finally:
        await test_client.close()

    routing = body["routing"]
    assert routing["profile_count"] == 2
    assert routing["bot_count"] == 4
    assert routing["chat_binding_count"] == 2
    assert routing["last_route"] == {
        "message_id": "hermes-message-1",
        "chat_id": "oc_sales",
        "profile_id": "work",
        "bot_id": "sales",
        "reason": "bindings.chats",
    }
    assert routing["profiles"]["work"]["bot_count"] == 2
    assert routing["profiles"]["work"]["chat_binding_count"] == 1
    assert routing["profiles"]["work"]["last_route"]["bot_id"] == "sales"
    assert routing["profiles"]["work"]["last_route_error"] == ""
    assert "registry-secret" not in str(routing)
    assert "secret" not in routing["profiles"]["work"]


async def test_route_failure_returns_502_and_reports_safe_error():
    factory = FakeFeishuClientFactory()

    def bot_router(event):
        raise RuntimeError("cannot route with app_secret=super-secret")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        failed = await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        failed_status = failed.status
        failed_body = await failed.json()
        health = await test_client.get("/health")
        health_body = await health.json()
    finally:
        await test_client.close()

    assert failed_status == 502
    assert failed_body == {"ok": False, "error": "bot route failed"}
    assert factory.clients["default"].sent == []
    assert factory.clients["sales"].sent == []
    assert health_body["active_sessions"] == 0
    assert health_body["metrics"]["events_rejected"] == 1
    assert health_body["diagnostics"]["last_route_error"] == "RuntimeError"
    assert health_body["routing"]["last_route_error"] == "RuntimeError"
    assert "super-secret" not in str(health_body)


async def test_routed_update_failure_reports_safe_bot_id():
    factory = FakeFeishuClientFactory()
    factory.clients["sales"].update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS

    def bot_router(event):
        return ("sales", "bindings.chats")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        started = await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        updated = await test_client.post(
            "/events",
            json=event_payload(
                "thinking.delta",
                1,
                {"text": "需要更新"},
                chat_id="oc_sales",
            ),
        )
        health = await test_client.get("/health")
        health_body = await health.json()
    finally:
        await test_client.close()

    assert started.status == 200
    assert updated.status == 200
    # Lock optimization: non-terminal update failures no longer return 502
    assert health_body["diagnostics"]["last_update_error"] == "bot_id=sales RuntimeError"
    assert "secret" not in health_body["diagnostics"]["last_update_error"].lower()
    assert "token" not in health_body["diagnostics"]["last_update_error"].lower()


async def test_routed_update_failure_redacts_sensitive_exception_text():
    factory = FakeFeishuClientFactory()
    factory.clients["sales"].update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS
    factory.clients["sales"].update_error_message = (
        "Authorization Bearer tenant-token-secret app_secret=super-secret"
    )

    def bot_router(event):
        return ("sales", "bindings.chats")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        updated = await test_client.post(
            "/events",
            json=event_payload(
                "thinking.delta",
                1,
                {"text": "需要更新"},
                chat_id="oc_sales",
            ),
        )
        health = await test_client.get("/health")
        health_body = await health.json()
    finally:
        await test_client.close()

    last_update_error = health_body["diagnostics"]["last_update_error"]
    assert updated.status == 200
    # Lock optimization: non-terminal update failures no longer return 502
    assert "bot_id=sales" in last_update_error
    assert "RuntimeError" in last_update_error
    assert "tenant-token-secret" not in last_update_error
    assert "super-secret" not in last_update_error
    assert "Authorization" not in last_update_error
    assert "Bearer" not in last_update_error


async def test_session_key_uses_profile_id():
    from hermes_feishu_card.server import _session_key
    from hermes_feishu_card.events import SidecarEvent
    event = SidecarEvent(
        schema_version="1", event="message.started",
        conversation_id="c", message_id="m", chat_id="c",
        platform="feishu", sequence=0, created_at=0.0,
        data={"profile_id": "work"},
    )
    assert _session_key(event) == "work:m"


async def test_session_key_no_profile_is_just_message_id():
    from hermes_feishu_card.server import _session_key
    from hermes_feishu_card.events import SidecarEvent
    event = SidecarEvent(
        schema_version="1", event="message.started",
        conversation_id="c", message_id="m", chat_id="c",
        platform="feishu", sequence=0, created_at=0.0,
        data={},
    )
    assert _session_key(event) == "m"


async def test_session_key_explicit_empty_profile_uses_default_composite_key():
    from hermes_feishu_card.server import _session_key
    from hermes_feishu_card.events import SidecarEvent
    event = SidecarEvent(
        schema_version="1", event="message.started",
        conversation_id="c", message_id="m", chat_id="c",
        platform="feishu", sequence=0, created_at=0.0,
        data={"profile_id": ""},
    )
    assert _session_key(event) == "default:m"
