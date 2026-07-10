import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.bots import RouteResult
from hermes_feishu_card import flush as flush_module
from hermes_feishu_card import server as sidecar_server
from hermes_feishu_card.server import FEISHU_MESSAGE_IDS_KEY, create_app


_REAL_ASYNCIO_SLEEP = asyncio.sleep


class FakeFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.fail_send = False
        self.send_delay = 0.0
        self.update_failures_remaining = 0
        self.update_error_message = "update unavailable"
        self.update_delay = 0.0

    async def send_card(self, chat_id, card, thread_id=None, reply_to_message_id=None):
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
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
        "update_scheduled": 0,
        "update_coalesced": 0,
        "update_queue_peak": 0,
        "terminal_drains": 0,
        "terminal_drain_timeouts": 0,
        "terminal_drain_latency_ms": 0,
        "feishu_update_latency_ms": 0,
        "cron_cards_sent": 0,
        "cron_fallbacks": 0,
    }
    assert body["reply_index"] == {"entries": 0, "last_lookup": {}}
    assert body["cron"] == {"cards_sent": 0, "fallbacks": 0}
    assert body["profile_diagnostics"] == {}


async def test_hfc_help_command_sends_read_only_diagnostic_card(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/commands",
        json={
            "command": "help",
            "chat_id": "oc_secret_chat",
            "message_id": "om_secret_message",
            "thread_id": "omt_secret_thread",
        },
    )

    assert response.status == 200
    body = await response.json()
    assert body == {"ok": True, "handled": True, "command": "help"}
    assert len(feishu_client.sent) == 1
    chat_id, card, thread_id, reply_to_message_id = feishu_client.sent[0]
    assert chat_id == "oc_secret_chat"
    assert thread_id == "omt_secret_thread"
    assert reply_to_message_id == "om_secret_message"
    content = str(card)
    assert "/hfc status" in content
    assert "/hfc doctor" in content
    assert "/hfc monitor" in content
    assert "oc_secret_chat" not in content
    assert "om_secret_message" not in content
    assert "omt_secret_thread" not in content


async def test_hfc_command_request_returns_before_slow_feishu_send():
    class BlockingFeishuClient(FakeFeishuClient):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send_card(self, chat_id, card, thread_id=None, reply_to_message_id=None):
            self.started.set()
            await self.release.wait()
            return await super().send_card(
                chat_id,
                card,
                thread_id=thread_id,
                reply_to_message_id=reply_to_message_id,
            )

    feishu_client = BlockingFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    post_task = asyncio.create_task(
        test_client.post(
            "/commands",
            json={
                "command": "status",
                "chat_id": "oc_secret_chat",
                "message_id": "om_secret_message",
            },
        )
    )
    try:
        await asyncio.wait_for(feishu_client.started.wait(), timeout=1.0)
        try:
            response = await asyncio.wait_for(asyncio.shield(post_task), timeout=0.05)
        except asyncio.TimeoutError as exc:
            raise AssertionError("/commands waited for Feishu card delivery") from exc
        body = await response.json()
        assert response.status == 200
        assert body == {"ok": True, "handled": True, "command": "status"}
        assert feishu_client.sent == []

        feishu_client.release.set()
        for _ in range(80):
            if feishu_client.sent:
                break
            await _REAL_ASYNCIO_SLEEP(0.01)
        assert len(feishu_client.sent) == 1
    finally:
        feishu_client.release.set()
        await post_task
        await test_client.close()


async def test_hfc_status_group_unbound_shows_binding_hint_and_slash_guidance():
    factory = FakeFeishuClientFactory()

    def bot_router(event):
        assert event.data["chat_type"] == "group"
        return RouteResult(
            "default",
            "bindings.fallback_bot",
            metadata={
                "group": {
                    "is_group": True,
                    "enabled": True,
                    "chat_bound": False,
                    "chat_allowed": True,
                    "require_mention": True,
                }
            },
        )

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/commands",
            json={
                "command": "status",
                "chat_id": "oc_group",
                "message_id": "om_group_status",
                "data": {"chat_type": "group"},
            },
        )
    finally:
        await test_client.close()

    assert response.status == 200
    assert len(factory.clients["default"].sent) == 1
    card = factory.clients["default"].sent[0][1]
    content = str(card)
    assert "当前群未绑定" in content
    assert "bots bind-chat oc_group default" in content
    assert "群内 slash command" in content
    assert "Hermes @/白名单" in content


async def test_hfc_monitor_command_reports_safe_metrics(client):
    test_client, feishu_client = client
    metrics = test_client.app[sidecar_server.METRICS_KEY]
    metrics.events_received = 3
    metrics.update_coalesced = 2
    metrics.update_queue_peak = 4

    response = await test_client.post(
        "/commands",
        json={
            "command": "monitor",
            "chat_id": "oc_monitor_secret",
            "message_id": "om_monitor_secret",
        },
    )

    assert response.status == 200
    assert (await response.json()) == {
        "ok": True,
        "handled": True,
        "command": "monitor",
    }
    assert len(feishu_client.sent) == 1
    content = str(feishu_client.sent[0][1])
    assert "events_received: 3" in content
    assert "update_coalesced: 2" in content
    assert "update_queue_peak: 4" in content
    assert "active_sessions: 0" in content
    assert "oc_monitor_secret" not in content
    assert "om_monitor_secret" not in content


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
    assert "hermes-message-1" not in body["sessions"]
    assert len(body["sessions"]) == 1
    session_snapshot = next(iter(body["sessions"].values()))
    assert session_snapshot["status"] == "completed"
    assert session_snapshot["answer_chars"] > 0
    assert body["metrics"]["feishu_update_attempts"] == 1


async def test_card_config_controls_timeline_rendering():
    feishu_client = FakeFeishuClient()
    app = create_app(
        feishu_client,
        card_config={
            "timeline_expanded": True,
            "show_reasoning": True,
            "max_timeline_items": 1,
            "max_reasoning_chars": 20,
            "max_tool_result_chars": 20,
        },
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))
        await test_client.post(
            "/events",
            json=event_payload(
                "answer.delta", 1, {"text": "第一段很长很长很长很长很长"}
            ),
        )
        await wait_for_card_update(feishu_client, "生成中")
        await _REAL_ASYNCIO_SLEEP(0.25)
        await test_client.post(
            "/events",
            json=event_payload(
                "tool.updated",
                2,
                {
                    "tool_id": "read",
                    "name": "read_file",
                    "status": "completed",
                    "detail": "abcdefghijklmnopqrstuvwxyz1234567890",
                },
            ),
        )
        await wait_for_card_update(feishu_client, "read_file")
        await test_client.post(
            "/events",
            json=event_payload("message.completed", 3, {"answer": "最终回答"}),
        )
        await wait_for_card_update(feishu_client, "最终回答")
    finally:
        await test_client.close()

    card = feishu_client.updated[-1][1]
    timeline = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    assert timeline["expanded"] is True
    assert "已折叠 1 条早期思考/工具记录" in str(timeline)
    assert "工具详情过长，已截断" in str(timeline)
    assert "内容已折叠" not in str(timeline)


@pytest.mark.parametrize(
    ("card_config", "expect_timeline", "expected_expanded", "thought_text"),
    [
        ({"show_reasoning": 0}, False, None, "数值 0 应该隐藏"),
        ({"show_reasoning": "0"}, False, None, "字符串 0 应该隐藏"),
        (
            {"show_reasoning": True, "timeline_expanded": 1},
            True,
            True,
            "数值 1 应该展开",
        ),
        (
            {"show_reasoning": True, "timeline_expanded": "1"},
            True,
            True,
            "字符串 1 应该展开",
        ),
    ],
)
async def test_card_config_string_booleans_control_timeline_rendering(
    card_config, expect_timeline, expected_expanded, thought_text
):
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client, card_config=card_config)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))
        await test_client.post(
            "/events",
            json=event_payload("answer.delta", 1, {"text": thought_text}),
        )
        await test_client.post(
            "/events",
            json=event_payload(
                "tool.updated",
                2,
                {"tool_id": "config-tool", "name": "config_tool", "status": "completed"},
            ),
        )
        await wait_for_card_update(feishu_client, "config_tool")
        await test_client.post(
            "/events",
            json=event_payload("message.completed", 3, {"answer": "最终回答"}),
        )
        await wait_for_card_update(feishu_client, "最终回答")
    finally:
        await test_client.close()

    card = feishu_client.updated[-1][1]
    content = str(card)
    if not expect_timeline:
        assert "auxiliary_timeline" not in content
        assert thought_text not in content
        return

    timeline = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    assert timeline["expanded"] is expected_expanded
    assert thought_text in str(timeline)


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


async def test_topic_stream_event_with_reply_anchor_updates_existing_card(client):
    test_client, feishu_client = client

    started = await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )

    assert started.status == 200
    assert await started.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert feishu_client.sent[0][2] == "omt_topic"
    assert feishu_client.sent[0][3] == "om_topic_user"

    tool = await test_client.post(
        "/events",
        json=event_payload(
            "tool.updated",
            1,
            {
                "reply_to_message_id": "om_topic_user",
                "tool_id": "terminal",
                "name": "terminal",
                "status": "running",
                "detail": "brew install ripgrep",
            },
            conversation_id="omt_topic",
            message_id="om_topic_stream_reply",
            thread_id="omt_topic",
        ),
    )

    assert tool.status == 200
    assert await tool.json() == {"ok": True, "applied": True}
    message_id, card = await wait_for_card_update(feishu_client, "brew install ripgrep")
    assert message_id == "feishu-message-1"
    assert "terminal" in str(card)
    assert len(feishu_client.sent) == 1


async def test_topic_system_notice_with_reply_anchor_updates_existing_card(client):
    test_client, feishu_client = client

    await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )

    notice = await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            1,
            {
                "reply_to_message_id": "om_topic_user",
                "title": "上下文窗口提示",
                "content": (
                    "ℹ️ Codex gpt-5.5 caps context at 272K, "
                    "so auto-compaction was raised to 85%."
                ),
                "level": "info",
                "notice_kind": "context-cap",
                "notice_id": "context-cap",
                "notice_scope": "session",
            },
            conversation_id="omt_topic",
            message_id="om_topic_stream_reply",
            thread_id="omt_topic",
        ),
    )

    assert notice.status == 200
    assert await notice.json() == {"ok": True, "applied": True}
    message_id, card = await wait_for_card_update(feishu_client, "auto-compaction")
    assert message_id == "feishu-message-1"
    assert "上下文窗口提示" in str(card)
    assert len(feishu_client.sent) == 1


async def test_topic_second_message_reusing_message_id_sends_new_card(client):
    """Feishu topic groups reuse the same message_id across turns. A second
    message.started on the same (already-completed) key must send a NEW card,
    not be ignored, so the second turn is not left card-less."""
    test_client, feishu_client = client

    # First turn: started -> answer -> completed on message_id om_topic_user.
    await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    await test_client.post(
        "/events",
        json=event_payload(
            "answer.delta",
            1,
            {"reply_to_message_id": "om_topic_user", "text": "first answer"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            2,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    assert completed.status == 200
    assert len(feishu_client.sent) == 1

    # Second turn in the SAME thread reuses the SAME message_id.
    started2 = await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    assert started2.status == 200
    assert await started2.json() == {"ok": True, "applied": True}
    # A brand-new card must be sent for the second turn.
    assert len(feishu_client.sent) == 2

    # And the second turn's content must render on the new card.
    await test_client.post(
        "/events",
        json=event_payload(
            "answer.delta",
            1,
            {"reply_to_message_id": "om_topic_user", "text": "second answer"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    _mid, card = await wait_for_card_update(feishu_client, "second answer")
    assert "second answer" in str(card)


async def test_topic_second_message_started_while_active_is_ignored(client):
    """A duplicate message.started while the session is still streaming must
    still be ignored (no spurious second card)."""
    test_client, feishu_client = client

    await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    assert len(feishu_client.sent) == 1

    # Session still active (no completed) -> a second started is a duplicate.
    dup = await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    assert dup.status == 200
    assert await dup.json() == {"ok": True, "applied": False}
    assert len(feishu_client.sent) == 1


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


async def test_native_text_fallback_interaction_is_not_applied_in_text_mode():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client, card_config={"interaction_mode": "text"})
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        requested = await test_client.post(
            "/events",
            json=event_payload(
                "interaction.requested",
                1,
                {
                    "interaction_id": "slash-new-1",
                    "kind": "slash_confirm",
                    "prompt": "Confirm /new",
                    "fallback_policy": "native_text",
                    "options": [
                        {"label": "Approve Once", "value": "once"},
                        {"label": "Always Approve", "value": "always"},
                        {"label": "Cancel", "value": "cancel"},
                    ],
                },
            ),
        )

        assert requested.status == 200
        assert await requested.json() == {
            "ok": True,
            "applied": False,
            "interaction_mode": "text",
        }
        assert feishu_client.sent == []
        assert feishu_client.updated == []
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
    assert body["chat_id_hash"] == sidecar_server._diagnostic_id_hash("oc_abc")
    assert body["message_id_hash"] == sidecar_server._diagnostic_id_hash(
        "feishu-message-1"
    )
    assert body["source_message_id_hash"] == sidecar_server._diagnostic_id_hash(
        "hermes-message-1"
    )
    assert "chat_id" not in body
    assert "message_id" not in body
    assert "source_message_id" not in body
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


@pytest.mark.parametrize(
    ("event_name", "data", "expected_text"),
    [
        ("answer.delta", {"text": "提前到达的回答"}, "提前到达的回答"),
        ("thinking.delta", {"text": "提前到达的思考"}, "Hermes Agent"),
        (
            "tool.updated",
            {
                "tool_id": "tool-1",
                "name": "search",
                "status": "running",
                "detail": "提前到达的工具",
            },
            "search",
        ),
        ("message.completed", {"answer": "提前完成的回答"}, "提前完成的回答"),
    ],
)
async def test_message_event_without_started_creates_initial_card(
    client, event_name, data, expected_text
):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload(event_name, 1, data),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert expected_text in str(feishu_client.sent[0][1])
    assert feishu_client.updated == []
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 1
    assert metrics["events_applied"] == 1
    assert metrics["events_ignored"] == 0


async def test_independent_system_notice_without_started_sends_notice_card(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            1,
            {
                "title": "上下文窗口提示",
                "content": "Codex gpt-5.5 caps context at 272K.",
                "notice_scope": "independent",
                "level": "info",
            },
            message_id="notice_context_cap",
        ),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    card = feishu_client.sent[0][1]
    assert card["header"]["title"]["content"] == "上下文窗口提示"
    assert card["header"]["template"] == "blue"
    assert "Codex gpt-5.5 caps context at 272K." in str(card)
    assert "生成中" not in str(card)
    assert feishu_client.updated == []


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


async def test_cron_completed_event_with_thread_id_sends_card_to_thread(client):
    """Cron event with thread_id should pass thread_id to send_card."""
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            0,
            {"answer": "Cron in thread", "delivery_kind": "cron"},
            message_id="cron_thread_1",
            chat_id="oc_topic_group",
            thread_id="omt_target_thread",
            conversation_id="omt_target_thread",
        ),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    # FakeFeishuClient.send_card stores (chat_id, card, thread_id, reply_to_message_id)
    chat_id, card, thread_id, reply_to = feishu_client.sent[0]
    assert chat_id == "oc_topic_group"
    assert thread_id == "omt_target_thread"
    assert "Cron in thread" in str(card)


async def test_cron_completed_event_without_thread_id_sends_card_to_chat(client):
    """Cron event without thread_id should send card to chat_id directly."""
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            0,
            {"answer": "Cron no thread", "delivery_kind": "cron"},
            message_id="cron_no_thread_1",
            chat_id="oc_dm_chat",
            thread_id="",
            conversation_id="cron_no_thread_1",
        ),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    chat_id, card, thread_id, reply_to = feishu_client.sent[0]
    assert chat_id == "oc_dm_chat"
    assert thread_id is None  # _thread_id_for_event returns None for non-omt_ ids


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
    assert "后续增量" not in str(feishu_client.updated[0][1])
    assert "生成中" in str(feishu_client.updated[0][1])


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
    assert any("`search` · running" in card for card in updates_by_message["feishu-message-2"])


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
    assert len(feishu_client.updated) == 3
    assert "第一段" in str(feishu_client.updated[0][1])
    assert "最终答案" in str(feishu_client.updated[-1][1])
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 4
    assert metrics["events_applied"] == 4
    assert metrics["events_rejected"] == 0
    assert metrics["feishu_update_attempts"] == 3
    assert metrics["feishu_update_successes"] == 3
    assert metrics["terminal_drains"] == 1


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


async def test_burst_updates_are_coalesced_and_reported_in_health(client, monkeypatch):
    test_client, feishu_client = client
    feishu_client.update_delay = 0.03
    monkeypatch.setattr(sidecar_server, "UPDATE_MIN_INTERVAL_SECONDS", 0)

    await test_client.post("/events", json=event_payload("message.started", 0))
    responses = await asyncio.gather(
        *[
            test_client.post(
                "/events",
                json=event_payload("answer.delta", index, {"text": f"片段{index}"}),
            )
            for index in range(1, 25)
        ]
    )

    assert all(response.status == 200 for response in responses)
    await wait_for_card_update(feishu_client, "片段24")
    health = await test_client.get("/health")
    body = await health.json()
    assert body["metrics"]["update_coalesced"] > 0
    assert body["metrics"]["update_queue_peak"] == 1
    assert body["metrics"]["feishu_update_attempts"] < 24


async def test_terminal_event_ack_does_not_wait_for_slow_card_patch(client, monkeypatch):
    del monkeypatch
    feishu_client = FakeFeishuClient()
    feishu_client.update_delay = 0.25
    app = create_app(
        feishu_client,
        card_config={"flush_interval_ms": 0, "final_drain_timeout_ms": 120},
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))

        started_at = asyncio.get_running_loop().time()
        completed = await test_client.post(
            "/events",
            json=event_payload("message.completed", 1, {"answer": "最终答案"}),
        )
        elapsed = asyncio.get_running_loop().time() - started_at

        assert completed.status == 200
        assert await completed.json() == {"ok": True, "applied": True}
        assert elapsed < 0.12
        assert elapsed < feishu_client.update_delay
        assert feishu_client.updated == []

        for _ in range(40):
            if feishu_client.updated:
                break
            await asyncio.sleep(0.01)
        assert "最终答案" in str(feishu_client.updated[-1][1])
    finally:
        await test_client.close()


async def test_terminal_event_drains_latest_pending_content_before_final_card(client, monkeypatch):
    test_client, feishu_client = client
    feishu_client.update_delay = 0.04
    monkeypatch.setattr(sidecar_server, "UPDATE_MIN_INTERVAL_SECONDS", 0)

    await test_client.post("/events", json=event_payload("message.started", 0))
    await asyncio.gather(
        *[
            test_client.post(
                "/events",
                json=event_payload("answer.delta", index, {"text": f"片段{index}"}),
            )
            for index in range(1, 15)
        ]
    )
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 15, {"answer": ""}),
    )

    assert completed.status == 200
    await wait_for_card_update(feishu_client, "片段14")
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["terminal_drains"] == 1
    assert metrics["terminal_drain_timeouts"] == 0
    assert "片段14" in str(feishu_client.updated[-1][1])


async def test_terminal_event_does_not_wait_for_update_window_without_pending_flush(
    client, monkeypatch
):
    test_client, feishu_client = client
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(flush_module.asyncio, "sleep", fake_sleep)

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post("/events", json=event_payload("answer.delta", 1, {"text": "片段"}))
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 2, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    await wait_for_card_update(feishu_client, "最终答案")
    assert sleeps == []
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
    assert diagnostics["last_attachment_event"]["event"] == "message.completed"
    assert diagnostics["last_attachment_event"]["attachment_count"] == 1
    assert diagnostics["last_attachment_event"]["native_delivery"] == "allowed"
    assert diagnostics["last_attachment_event"]["message_id_hash"]
    assert "message_id" not in diagnostics["last_attachment_event"]


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


async def test_terminal_retry_backoff_does_not_inflate_patch_latency_metric(
    client, monkeypatch
):
    test_client, feishu_client = client
    feishu_client.update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS
    now = [100.0]

    async def fake_sleep(delay):
        now[0] += delay

    monkeypatch.setattr(sidecar_server.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(sidecar_server.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(flush_module.time, "monotonic", lambda: now[0])

    await test_client.post("/events", json=event_payload("message.started", 0))
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    await wait_for_card_update(feishu_client, "最终答案")
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["feishu_update_attempts"] == sidecar_server.UPDATE_MAX_ATTEMPTS + 1
    assert metrics["feishu_update_failures"] == sidecar_server.UPDATE_MAX_ATTEMPTS
    assert metrics["feishu_update_latency_ms"] < 1000


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
    assert routing["last_route"]["bot_id"] == "sales"
    assert routing["last_route"]["reason"] == "bindings.chats"
    assert routing["last_route"]["chat_id_hash"]
    assert routing["last_route"]["message_id_hash"]
    assert routing["last_route_error"] == ""
    assert "chat_id" not in routing["last_route"]
    assert "message_id" not in routing["last_route"]
    assert "registry-secret" not in str(body)
    assert "oc_sales" not in str(body)
    assert "hermes-message-1" not in str(body)
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
    assert routing["last_route"]["profile_id"] == "work"
    assert routing["last_route"]["bot_id"] == "sales"
    assert routing["last_route"]["reason"] == "bindings.chats"
    assert routing["last_route"]["chat_id_hash"]
    assert routing["last_route"]["message_id_hash"]
    assert routing["profiles"]["work"]["bot_count"] == 2
    assert routing["profiles"]["work"]["chat_binding_count"] == 1
    assert routing["profiles"]["work"]["last_route"]["bot_id"] == "sales"
    assert routing["profiles"]["work"]["last_route"]["chat_id_hash"]
    assert routing["profiles"]["work"]["last_route"]["message_id_hash"]
    assert routing["profiles"]["work"]["last_route_error"] == ""
    assert "chat_id" not in routing["profiles"]["work"]["last_route"]
    assert "message_id" not in routing["profiles"]["work"]["last_route"]
    assert "registry-secret" not in str(routing)
    assert "oc_sales" not in str(body)
    assert "hermes-message-1" not in str(body)
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
