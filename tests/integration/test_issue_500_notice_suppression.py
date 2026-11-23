"""Integration regressions for #500: per-chat/chat-type system notice suppression."""
from __future__ import annotations

from types import SimpleNamespace
import time
import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.server import create_app


class _RecordingFeishuClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.updated: list[dict] = []

    async def send_card(self, chat_id: str, card: dict, **kwargs):
        self.sent.append({"chat_id": chat_id, "card": card, **kwargs})
        return f"om_card_{len(self.sent)}"

    async def update_card(self, message_id: str, card: dict, **kwargs):
        self.updated.append({"message_id": message_id, "card": card, **kwargs})
        return True


def test_hook_runtime_classifies_chinese_self_improvement_and_carries_chat_type():
    en = hook_runtime._hfc_classify_system_notice("💾 Self-improvement review: saved skill foo")
    zh = hook_runtime._hfc_classify_system_notice("💾 自我改进回顾：已更新技能 foo")
    assert en is not None and en["notice_kind"] == "self-improvement"
    assert zh is not None and zh["notice_kind"] == "self-improvement"

    src = SimpleNamespace(platform="feishu", chat_id="oc_grp_1", chat_type="group", thread_id="omt_1", message_id="om_1")
    ctx = hook_runtime._hfc_notice_context_from_source(src)
    assert ctx is not None
    assert ctx["chat_type"] == "group"

    payload = hook_runtime._hfc_build_system_notice_payload(
        chat_id="oc_grp_1",
        content="💾 自我改进回顾：已更新技能 foo",
        reply_to="om_1",
        metadata=None,
        context=ctx,
        notice=zh,
        notice_scope="independent",
        message_id="notice_self_imp_1",
    )
    assert payload["data"]["chat_type"] == "group"
    assert payload["data"]["notice_kind"] == "self-improvement"


@pytest.mark.asyncio
async def test_group_suppresses_self_improvement_without_feishu_or_native_fallback(monkeypatch):
    feishu = _RecordingFeishuClient()
    app = create_app(
        feishu,
        card_config={
            "chat_overrides": {
                "group": {"suppressed_notice_kinds": ["self-improvement"]},
                "p2p": {},
                "chats": {
                    "oc_group_allow": {"suppressed_notice_kinds": []},
                },
            }
        },
    )
    async with TestClient(TestServer(app)) as client:
        event_url = str(client.make_url("/events"))
        monkeypatch.setattr(
            hook_runtime,
            "load_runtime_config",
            lambda: SimpleNamespace(enabled=True, event_url=event_url, timeout_seconds=5.0),
        )
        async def _allow_card(_chat_id: str) -> bool:
            return True
        monkeypatch.setattr(hook_runtime, "_hfc_direct_card_allowed_async", _allow_card)

        # 1. Group chat: self-improvement notice is suppressed (no card sent, hook reports success so native text is NOT sent)
        token = hook_runtime._HFC_FEISHU_NOTICE_CONTEXT.set({
            "chat_id": "oc_group_1",
            "chat_type": "group",
            "conversation_id": "oc_group_1",
            "thread_id": "",
            "message_id": "",
            "profile_id": "default",
        })
        try:
            res_group = await hook_runtime._hfc_send_system_notice_card(
                SimpleNamespace(),
                chat_id="oc_group_1",
                content="💾 Self-improvement review: saved memory",
            )
        finally:
            hook_runtime._HFC_FEISHU_NOTICE_CONTEXT.reset(token)
        assert res_group.success is True
        assert feishu.sent == []
        assert feishu.updated == []

        # 2. P2P chat: self-improvement notice is NOT suppressed -> card IS sent
        token = hook_runtime._HFC_FEISHU_NOTICE_CONTEXT.set({
            "chat_id": "oc_p2p_1",
            "chat_type": "p2p",
            "conversation_id": "oc_p2p_1",
            "thread_id": "",
            "message_id": "",
            "profile_id": "default",
        })
        try:
            res_p2p = await hook_runtime._hfc_send_system_notice_card(
                SimpleNamespace(),
                chat_id="oc_p2p_1",
                content="💾 自我改进回顾：已更新技能",
            )
        finally:
            hook_runtime._HFC_FEISHU_NOTICE_CONTEXT.reset(token)
        assert res_p2p.success is True
        assert len(feishu.sent) == 1
        assert feishu.sent[0]["chat_id"] == "oc_p2p_1"

        # 3. Group chat with explicit chat override []: self-improvement IS sent
        token = hook_runtime._HFC_FEISHU_NOTICE_CONTEXT.set({
            "chat_id": "oc_group_allow",
            "chat_type": "group",
            "conversation_id": "oc_group_allow",
            "thread_id": "",
            "message_id": "",
            "profile_id": "default",
        })
        try:
            res_allow = await hook_runtime._hfc_send_system_notice_card(
                SimpleNamespace(),
                chat_id="oc_group_allow",
                content="💾 Self-improvement review: allowed here",
            )
        finally:
            hook_runtime._HFC_FEISHU_NOTICE_CONTEXT.reset(token)
        assert res_allow.success is True
        assert len(feishu.sent) == 2
        assert feishu.sent[1]["chat_id"] == "oc_group_allow"

        # 4. Unsuppressed notice kind in group chat (skill-loading) still sends normally
        token = hook_runtime._HFC_FEISHU_NOTICE_CONTEXT.set({
            "chat_id": "oc_group_1",
            "chat_type": "group",
            "conversation_id": "oc_group_1",
            "thread_id": "",
            "message_id": "",
            "profile_id": "default",
        })
        try:
            res_other = await hook_runtime._hfc_send_system_notice_card(
                SimpleNamespace(),
                chat_id="oc_group_1",
                content="Reading skill: foo",
            )
        finally:
            hook_runtime._HFC_FEISHU_NOTICE_CONTEXT.reset(token)
        assert res_other.success is True
        assert len(feishu.sent) == 3
        assert feishu.sent[2]["chat_id"] == "oc_group_1"

        # 5. Detached background notice (no chat_type in payload) falls back to observed session chat_type
        now = time.time()
        started = await client.post("/events", json={
            "schema_version": "1",
            "event": "message.started",
            "message_id": "om_grp_turn_1",
            "chat_id": "oc_group_detached",
            "conversation_id": "oc_group_detached",
            "platform": "feishu",
            "sequence": 1,
            "created_at": now,
            "data": {"chat_type": "group"},
        })
        assert started.status == 200, await started.text()
        sent_before = len(feishu.sent)
        detached = await client.post("/events", json={
            "schema_version": "1",
            "event": "system.notice",
            "message_id": "notice_detached_si_1",
            "chat_id": "oc_group_detached",
            "conversation_id": "oc_group_detached",
            "platform": "feishu",
            "sequence": 1,
            "created_at": now + 1.0,
            "data": {
                "delivery_kind": "notice",
                "notice_scope": "independent",
                "notice_kind": "self-improvement",
                "notice_id": "si_1",
                "title": "自我改进",
                "content": "💾 Self-improvement review: detached",
            },
        })
        assert detached.status == 200
        body = await detached.json()
        assert body["ok"] is True
        assert body["applied"] is True
        assert body["suppressed"] is True
        assert body["delivery"]["outcome"] == "accepted"
        assert len(feishu.sent) == sent_before
