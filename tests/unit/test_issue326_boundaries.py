import ast
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime as runtime
from hermes_feishu_card.install import patcher


@pytest.mark.asyncio
async def test_plain_status_recall_retains_task_profile_and_thread(monkeypatch):
    monkeypatch.delenv("HERMES_FEISHU_CARD_PROFILE_ID", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    calls = []

    async def schedule(message_id, **kwargs):
        calls.append((message_id, kwargs))
        return True

    monkeypatch.setattr(runtime, "schedule_message_recall_async", schedule)
    token = runtime._HFC_FEISHU_DELIVERY_CONTEXT.set({
        "chat_id": "chat_fixture", "profile_id": "secondary",
        "thread_id": "thread_fixture", "profile_invalid": False,
    })
    try:
        assert await runtime._hfc_recall_plain_text_status_notice(
            "chat_fixture", "⏳ Compressing context", None,
            SimpleNamespace(success=True, message_id="notice_fixture"),
        )
    finally:
        runtime._HFC_FEISHU_DELIVERY_CONTEXT.reset(token)
    assert calls[0][1]["route"] == {
        "profile_id": "secondary", "chat_id": "chat_fixture",
        "conversation_id": "thread_fixture",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [
    {"chat_id": "another_chat", "profile_id": "secondary"},
    {"chat_id": "chat_fixture", "profile_id": "secondary", "profile_invalid": True},
])
async def test_plain_status_does_not_recall_with_foreign_or_invalid_context(monkeypatch, context):
    async def forbidden(*args, **kwargs):
        pytest.fail("invalid route must never reach the recall endpoint")
    monkeypatch.setattr(runtime, "schedule_message_recall_async", forbidden)
    token = runtime._HFC_FEISHU_DELIVERY_CONTEXT.set(context)
    try:
        assert not await runtime._hfc_recall_plain_text_status_notice(
            "chat_fixture", "⏳ Compressing context", None,
            SimpleNamespace(success=True, message_id="notice_fixture"),
        )
    finally:
        runtime._HFC_FEISHU_DELIVERY_CONTEXT.reset(token)


@pytest.mark.parametrize("text", [
    "⏳ Here is the schedule you requested: tomorrow at 9am.",
    "⏳ Queued for the next turn. I'll respond once the current task finishes.",
    "⏳ Another Hermes process kept this session busy too long. Your message was not processed.",
    "⏳ Gateway is restarting and is not accepting new work right now.",
])
def test_hourglass_prefix_alone_never_authorizes_recall(text):
    assert runtime._transient_notice_recall_seconds(text) is None


@pytest.mark.asyncio
async def test_restart_send_failure_keeps_native_fallback():
    class Adapter:
        async def _hfc_original_send(self, *args, **kwargs):
            return SimpleNamespace(success=False, error="temporary transport failure")
    result = await runtime._hfc_send_system_notice_card(
        Adapter(), chat_id="chat_fixture",
        content="♻️ Gateway restarted successfully. Your session continues.",
    )
    assert result.success is False
    assert result.error == "delivery_disposition=native"


@pytest.mark.parametrize("body", [
    "    if enabled:\n        agent.tool_start_callback = callback\n",
    "    def later():\n        agent.tool_start_callback = callback\n",
])
def test_unknown_conditional_only_tool_anchor_is_rejected(body):
    source = "def wire(agent, callback, enabled):\n" + body
    node = ast.parse(source).body[0]
    assert patcher._last_stable_tool_lifecycle_assignment_location(
        node, source.splitlines(keepends=True)
    ) is None
