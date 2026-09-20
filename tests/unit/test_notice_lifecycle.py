"""Known restart notices must never broaden existing automatic-recall policy."""
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime as runtime
from hermes_feishu_card.notice_lifecycle import NoticeScope, RestartNoticeRegistry


@pytest.fixture
def runtime_route(monkeypatch):
    delivery_token = runtime._HFC_FEISHU_DELIVERY_CONTEXT.set({
        "chat_id": "oc_fixture", "profile_id": "work", "thread_id": "omt_fixture",
    })
    monkeypatch.setattr(runtime, "load_runtime_config", lambda: SimpleNamespace(
        enabled=True, event_url="http://127.0.0.1:8765/events", timeout_seconds=.1,
    ))
    posted = []
    async def post(url, payload, timeout):
        posted.append((url, payload))
        return {"ok": True}
    monkeypatch.setattr(runtime, "_post_json_ordered_response", post)
    try:
        yield posted
    finally:
        runtime._HFC_FEISHU_DELIVERY_CONTEXT.reset(delivery_token)


async def test_generated_restart_text_is_registered_without_a_timer(runtime_route):
    assert await runtime._hfc_recall_plain_text_status_notice(
        "oc_fixture", runtime._HFC_GATEWAY_ONLINE_TEXT, None,
        SimpleNamespace(success=True, message_id="om_notice"), generated_restart_notice=True,
    )
    assert runtime_route == [("http://127.0.0.1:8765/recall/schedule", {
        "message_id": "om_notice", "notice_family": "restart", "record_only": True,
        "route": {"profile_id": "work", "chat_id": "oc_fixture", "conversation_id": "omt_fixture"},
    })]


@pytest.mark.parametrize("text", [
    runtime._HFC_GATEWAY_ONLINE_TEXT,
    "⚠️ Hermes is restarting — your current task will be interrupted. "
    "Send any message after the restart and I'll try to resume where you left off.",
    "> ♻️ Gateway online — Hermes is back and ready.",
    "解释这段：♻️ Gateway online — Hermes is back and ready.",
    "♻️ Gateway online — Hermes is back and ready. 但还有故障。",
    "♻️ Gateway online — Hermes is back and ready.\nAdditional instruction",
    "✅ Background task finished — `cmd` (6s)",
    "❌ Background task failed — details must remain",
    "⏳ Gateway restarting — queued for the next turn after it comes back.",
    "✅ Approved once", "⌛ Approval timed out", "最终答案",
])
async def test_user_quotes_results_queue_and_approval_receipts_are_not_registered(runtime_route, text):
    assert not await runtime._hfc_recall_plain_text_status_notice(
        "oc_fixture", text, None, SimpleNamespace(success=True, message_id="om_notice"),
    )
    assert runtime_route == []


@pytest.mark.parametrize("result", [
    SimpleNamespace(success=False, message_id="om_notice"),
    SimpleNamespace(success=True, message_id=""),
])
async def test_restart_registration_requires_confirmed_message_id(runtime_route, result):
    assert not await runtime._hfc_recall_plain_text_status_notice(
        "oc_fixture", runtime._HFC_GATEWAY_ONLINE_TEXT, None, result,
        generated_restart_notice=True,
    )
    assert runtime_route == []


async def test_foreign_or_invalid_delivery_context_does_not_register(runtime_route):
    for context in [
        {"chat_id": "oc_other", "profile_id": "work"},
        {"chat_id": "oc_fixture", "profile_id": "work", "profile_invalid": True},
    ]:
        token = runtime._HFC_FEISHU_DELIVERY_CONTEXT.set(context)
        try:
            assert not await runtime._hfc_recall_plain_text_status_notice(
                "oc_fixture", runtime._HFC_GATEWAY_ONLINE_TEXT, None,
                SimpleNamespace(success=True, message_id="om_notice"),
                generated_restart_notice=True,
            )
        finally:
            runtime._HFC_FEISHU_DELIVERY_CONTEXT.reset(token)
    assert runtime_route == []


async def test_registration_failure_never_changes_successful_plain_notice_send(runtime_route, monkeypatch):
    sent = SimpleNamespace(success=True, message_id="om_online")
    class Adapter:
        async def _hfc_original_send(self, *args, **kwargs):
            return sent
    async def refused(*args, **kwargs):
        raise TimeoutError("fixture registration timeout")
    monkeypatch.setattr(runtime, "_post_json_ordered_response", refused)
    result = await runtime._hfc_send_plain_notice(
        Adapter(), chat_id="oc_fixture", text=runtime._HFC_GATEWAY_ONLINE_TEXT,
    )
    assert result is sent


async def test_existing_working_notice_keeps_its_fifteen_second_timer(runtime_route):
    assert await runtime._hfc_recall_plain_text_status_notice(
        "oc_fixture", "⏳ Working — 12 min — receiving stream response", None,
        SimpleNamespace(success=True, message_id="om_working"),
    )
    payload = runtime_route[0][1]
    assert payload["delay_seconds"] == 15
    assert "notice_family" not in payload
    assert "record_only" not in payload


async def test_ordinary_adapter_answer_equal_to_template_is_not_disposable(runtime_route, monkeypatch):
    sent = []
    class Adapter:
        async def _hfc_original_send(self, chat_id, content, **kwargs):
            sent.append(content)
            return SimpleNamespace(success=True, message_id="om_answer")
    async def allowed(chat_id):
        return True
    monkeypatch.setattr(runtime, "_hfc_direct_card_allowed_async", allowed)
    monkeypatch.setattr(runtime, "_hfc_take_feishu_command_result_context", lambda **kwargs: None)
    token = runtime._HFC_NATIVE_HANDOFF_CONTEXT.set(None)
    try:
        result = await runtime._hfc_send_with_native_command_result_card(
            Adapter(), "oc_fixture", runtime._HFC_GATEWAY_ONLINE_TEXT,
        )
    finally:
        runtime._HFC_NATIVE_HANDOFF_CONTEXT.reset(token)
    assert result.success is True
    assert sent == [runtime._HFC_GATEWAY_ONLINE_TEXT]
    assert runtime_route == []


def test_old_generation_cannot_discard_a_reregistered_message():
    scope = NoticeScope("work", "work:alpha", "oc_fixture", "omt_fixture")
    registry = RestartNoticeRegistry()
    assert registry.register(scope, "om_notice")
    [(message_id, old_generation)] = registry.snapshot(scope)
    registry.discard(scope, message_id, old_generation)
    assert registry.register(scope, message_id)
    registry.discard(scope, message_id, old_generation)
    assert registry.snapshot(scope)
