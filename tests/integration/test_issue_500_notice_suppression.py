"""Integration regressions for #500: per-chat/chat-type system notice suppression."""
from __future__ import annotations

import asyncio
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.delivery import FeishuAPIError
from hermes_feishu_card.delivery_policy import ChatDeliveryPolicy
from hermes_feishu_card.server import create_app


class _RecordingFeishuClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.updated: list[dict] = []
        self.card_updated = asyncio.Event()
        self.card_attempted = asyncio.Event()
        self.outcome = "delivered"

    async def send_card(self, chat_id: str, card: dict, **kwargs):
        self.sent.append({"chat_id": chat_id, "card": card, **kwargs})
        self.card_attempted.set()
        if self.outcome != "delivered":
            raise FeishuAPIError("test notice rejection", outcome=self.outcome)
        return f"om_card_{len(self.sent)}"

    async def update_card_message(self, message_id: str, card: dict, **kwargs):
        self.updated.append({"message_id": message_id, "card": card, **kwargs})
        self.card_updated.set()


_SELF_IMPROVEMENT_EN = "💾 Self-improvement review: Saved memory."
_SELF_IMPROVEMENT_ZH = "💾 自我改进回顾：已更新技能 foo"
_SELF_IMPROVEMENT_ZH_HANT = "💾 自我改進檢閱：已更新技能 foo"


@pytest.fixture
async def notice_path(monkeypatch):
    hook_runtime.reset_runtime_state()
    feishu = _RecordingFeishuClient()
    notices = []
    root_secret = b"n" * 32

    @web.middleware
    async def record_notices(request, handler):
        if request.path == "/events":
            payload = await request.json()
            if payload.get("event") == "system.notice":
                notices.append(payload)
        return await handler(request)

    app = create_app(
        feishu,
        operations_transport_root_secret=root_secret,
        delivery_policy=ChatDeliveryPolicy(
            profile_native_chats={"default": ("oc_group_native",), "research": ()},
            profile_card_chats={"default": (), "research": ()},
            profile_default_delivery={"research": {"group": "native", "p2p": "card"}},
        ),
        card_config={
            "chat_overrides": {
                "group": {"suppressed_notice_kinds": ["self-improvement", "background-task"]},
                "chats": {
                    "oc_group_1": {"suppressed_notice_kinds": ["self-improvement", "background-task"]},
                    "oc_group_allow": {"suppressed_notice_kinds": []},
                },
            }
        },
    )
    app.middlewares.append(record_notices)

    class FeishuAdapter:
        name = "feishu"

        def __init__(self):
            self.sent = []
            self.edited = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_send")

        async def edit_message(self, chat_id, message_id, content, *, finalize=False):
            self.edited.append((chat_id, message_id, content, finalize))
            return SimpleNamespace(success=True, message_id=message_id)

    try:
        async with TestClient(TestServer(app)) as client:
            monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", str(client.make_url("/events")))
            monkeypatch.setattr(hook_runtime, "read_transport_root_secret", lambda: root_secret)
            monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda: None)
            adapter = FeishuAdapter()
            yield SimpleNamespace(adapter=adapter, feishu=feishu, notices=notices, client=client)
    finally:
        hook_runtime.reset_runtime_state()


@pytest.fixture
def review_gateway(monkeypatch, notice_path):
    scheduled = []

    def module(name, source, **globals_):
        result = ModuleType(name)
        result.__dict__.update(globals_)
        monkeypatch.setitem(sys.modules, name, result)
        exec(compile(source, name, "exec"), result.__dict__)
        return result

    scheduler = module("agent.async_utils", """
def safe_schedule_threadsafe(coro, loop, *, logger=None, log_message='', log_level=0):
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    scheduled.append(future)
    return future
""", asyncio=asyncio, scheduled=scheduled)
    gateway = module("gateway.run", """
class GatewayRunner:
    async def _deliver_platform_notice(self, source, content):
        return await self.adapters['feishu'].send(source.chat_id, content)

def old_review(ctx):
    def _deliver_bg_review_message(message):
        return safe_schedule_threadsafe(
            ctx._status_adapter.send(ctx.source.chat_id, message, metadata=ctx.metadata), ctx.loop,
            log_message='background_review_callback scheduling error',
        )
    return _deliver_bg_review_message
    """, safe_schedule_threadsafe=scheduler.safe_schedule_threadsafe)
    progress = module("gateway.run_turn_progress", """
class GatewayTurnProgressMixin:
    def _schedule(self, coro, log_message, loop=None):
        return gateway.safe_schedule_threadsafe(coro, self._ctx.loop, log_message=log_message)

    def _send_status_text(self, text, metadata, log_message):
        ctx = self._ctx
        return self._schedule(ctx._status_adapter.send(ctx.source.chat_id, text, metadata=metadata), log_message)

    async def _send_progress_text(self, st, text):
        return await st.adapter.send(self._ctx.source.chat_id, text, metadata=self._ctx.metadata)

    async def _edit_progress_message(self, st, message_id, content):
        return await st.adapter.edit_message(self._ctx.source.chat_id, message_id, content)
""", gateway=gateway)
    turn = module("gateway.run_turn_runner", """
class TurnRunner(GatewayTurnProgressMixin):
    def make_review(self):
        ctx = self._ctx
        pending = []

        def deliver(message):
            self._send_status_text(message, ctx.metadata, 'background_review_callback scheduling error')

        def release():
            for message in pending:
                deliver(message)
            pending.clear()

        return pending.append, release
""", GatewayTurnProgressMixin=progress.GatewayTurnProgressMixin)
    cold = module("gateway.session_cold_review", """
def cold_review(adapter, source, metadata, loop):
    def deliver(message):
        return scheduler.safe_schedule_threadsafe(
            adapter.send(source.chat_id, str(message), metadata=metadata), loop,
            log_message='cold /refine summary delivery scheduling failed',
        )
    return deliver
""", scheduler=scheduler)
    runner = gateway.GatewayRunner()
    runner.adapters = {"feishu": notice_path.adapter}
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    installed = gateway.safe_schedule_threadsafe
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    assert gateway.safe_schedule_threadsafe is scheduler.safe_schedule_threadsafe is installed
    return SimpleNamespace(gateway=gateway, runner=runner, turn=turn.TurnRunner(),
                           cold=cold, scheduled=scheduled)


@pytest.mark.parametrize(
    "producer,chat_id,chat_type,content,expected",
    [
        ("resident", "oc_group_policy", "group", _SELF_IMPROVEMENT_EN, "suppressed"),
        ("resident", "oc_group_policy", "group", _SELF_IMPROVEMENT_ZH, "suppressed"),
        ("resident", "oc_group_policy", "group", _SELF_IMPROVEMENT_ZH_HANT, "suppressed"),
        ("cold", "oc_group_policy", "group", _SELF_IMPROVEMENT_ZH, "suppressed"),
        ("cold", "oc_group_policy", "group", _SELF_IMPROVEMENT_ZH_HANT, "suppressed"),
        ("legacy", "oc_group_policy", "group", _SELF_IMPROVEMENT_EN, "suppressed"),
        ("resident", "oc_review_p2p", "p2p", _SELF_IMPROVEMENT_ZH, "card"),
        ("resident", "oc_group_allow", "group", _SELF_IMPROVEMENT_EN, "card"),
        ("resident", "oc_group_native", "group", _SELF_IMPROVEMENT_ZH, "native"),
        ("cold", "oc_group_research", "group", _SELF_IMPROVEMENT_EN, "native"),
        ("resident", "oc_group_policy", "group", "说明自我改进回顾的写法。", "native"),
    ],
)
async def test_review_producer_uses_its_own_chat_policy(
    notice_path, review_gateway, producer, chat_id, chat_type, content, expected,
):
    adapter = notice_path.adapter
    source = SimpleNamespace(platform="feishu", chat_id=chat_id, chat_type=chat_type, thread_id="omt_review")
    if chat_id == "oc_group_research":
        source.profile = "research"
    metadata = {"thread_id": source.thread_id, "_interim_send": True}
    loop = asyncio.get_running_loop()
    ctx = SimpleNamespace(source=source, _status_adapter=adapter, metadata=metadata, loop=loop)
    if producer == "resident":
        review_gateway.turn._ctx = ctx
        send, release = review_gateway.turn.make_review()
        await asyncio.to_thread(send, content)
        assert review_gateway.scheduled == []
        release()
    elif producer == "cold":
        send = review_gateway.cold.cold_review(adapter, source, metadata, loop)
        await asyncio.to_thread(send, content)
    else:
        await asyncio.to_thread(review_gateway.gateway.old_review(ctx), content)

    assert len(review_gateway.scheduled) == 1
    result = await asyncio.wrap_future(review_gateway.scheduled[0])
    assert result.success is True
    if expected in {"suppressed", "card"}:
        [notice] = notice_path.notices
        assert notice["data"]["chat_type"] == chat_type
        assert notice["data"]["notice_kind"] == "self-improvement"
        assert notice["data"]["content"] == content
        assert notice["thread_id"] == source.thread_id
        assert adapter.sent == []
    else:
        assert adapter.sent == [(chat_id, content, None, metadata)]
        assert notice_path.notices == []
    assert len(notice_path.feishu.sent) == (1 if expected == "card" else 0)
    assert notice_path.feishu.updated == []


async def test_review_scheduler_does_not_claim_user_text_or_interim_output(notice_path, review_gateway):
    adapter = notice_path.adapter
    chat_id = "oc_group_1"
    metadata = {"_interim_send": True, "notice_kind": "self-improvement"}
    # The genuine diagnostic label and interim metadata do not confer provenance.
    result = await asyncio.wrap_future(review_gateway.gateway.safe_schedule_threadsafe(
        adapter.send(chat_id, _SELF_IMPROVEMENT_EN, metadata=metadata), asyncio.get_running_loop(),
        log_message="background_review_callback scheduling error",
    ))
    assert result.message_id == "om_native_send"
    assert adapter.sent == [(chat_id, _SELF_IMPROVEMENT_EN, None, metadata)]
    assert notice_path.notices == []


@pytest.mark.parametrize("content,suppressed", [
    (_SELF_IMPROVEMENT_ZH, True),
    ("Unrecognized review summary.", False),
])
async def test_cold_review_retains_notice_policy_with_refine_command_active(
    monkeypatch, notice_path, review_gateway, content, suppressed,
):
    source = SimpleNamespace(platform="feishu", chat_id="oc_group_policy", chat_type="group")
    event = SimpleNamespace(source=source, text="/refine", get_command=lambda: "refine")
    assert hook_runtime.install_feishu_command_card_adapter_methods(review_gateway.runner, event=event)
    command_feedback = []

    async def record_feedback(adapter, *, content, **kwargs):
        command_feedback.append(content)
        return SimpleNamespace(success=True, message_id="om_command_feedback")

    monkeypatch.setattr(hook_runtime, "_hfc_send_native_command_result_card", record_feedback)
    metadata = {"_interim_send": True}
    send_review = review_gateway.cold.cold_review(notice_path.adapter, source, metadata, asyncio.get_running_loop())
    await asyncio.to_thread(send_review, content)
    result = await asyncio.wrap_future(review_gateway.scheduled[0])
    assert result.success is True
    if suppressed:
        assert result.message_id.startswith("notice_")
        assert notice_path.adapter.sent == []
        assert [event["data"]["notice_kind"] for event in notice_path.notices] == ["self-improvement"]
    else:
        assert notice_path.adapter.sent == [(source.chat_id, content, None, metadata)]
        assert notice_path.notices == []
    assert command_feedback == notice_path.feishu.sent == []

    result = await notice_path.adapter.send(source.chat_id, "Refine started.")
    assert result.message_id == "om_command_feedback"
    assert command_feedback == ["Refine started."]


async def test_pure_skill_progress_keeps_its_notice_and_edit_path(notice_path, review_gateway):
    adapter = notice_path.adapter
    source = SimpleNamespace(platform="feishu", chat_id="oc_group_1", chat_type="group", message_id="om_skill")
    started = await notice_path.client.post("/events", json=hook_runtime.build_event(
        "message.started", {"source": source, "message_id": source.message_id},
    ))
    assert (await started.json())["applied"] is True
    review_gateway.turn._ctx = SimpleNamespace(source=source, metadata=None)
    state = SimpleNamespace(adapter=adapter)
    result = await review_gateway.turn._send_progress_text(state, "📚 Reading skill foo")
    assert result.success is True
    await asyncio.wait_for(notice_path.feishu.card_updated.wait(), timeout=2)
    notice_path.feishu.card_updated.clear()
    edited = await review_gateway.turn._edit_progress_message(state, result.message_id, "📚 Reading skill bar")
    assert edited.success is True
    await asyncio.wait_for(notice_path.feishu.card_updated.wait(), timeout=2)
    assert [event["data"]["notice_kind"] for event in notice_path.notices] == ["skill-loading", "skill-loading"]
    assert len(notice_path.feishu.sent) == 1
    assert [patch["message_id"] for patch in notice_path.feishu.updated] == ["om_card_1", "om_card_1"]
    assert adapter.sent == adapter.edited == []


@pytest.mark.parametrize("entry", ["send", "edit"])
@pytest.mark.parametrize(
    "content",
    [
        "  本周例会讨论了自我改进回顾的写法。\n",
        "We discussed how to write a self-improvement review at our meeting.",
        f"> {_SELF_IMPROVEMENT_EN}",
        _SELF_IMPROVEMENT_EN,
        _SELF_IMPROVEMENT_ZH,
        _SELF_IMPROVEMENT_ZH_HANT,
        "After reading skill notes, we discussed context compression.",
        "The sample says the session automatically reset; actual sessions may differ.",
        "If it caps context, explain how auto-compaction affects the output.",
        "The team compared context compression models.",
        "📚 Reading skill foo",
        "ℹ️ 上下文压缩已推迟 — 摘要仍在生成中。本回合将不压缩继续。",
        "ℹ️ Codex gpt-5.5 caps context at 272K, so auto-compaction was raised to 85%.",
        "Session automatically reset",
    ],
    ids=["zh-body", "en-body", "quoted", "verbatim-en", "verbatim-zh",
         "verbatim-zh-hant", "skill", "reset", "cap", "compression", "verbatim-skill", "verbatim-compression",
         "verbatim-cap", "verbatim-reset"],
)
async def test_ordinary_adapter_text_is_delivered_once(entry, content, notice_path):
    adapter = notice_path.adapter
    source = SimpleNamespace(
        platform="feishu", chat_id="oc_group_1", chat_type="group", thread_id="omt_topic",
    )
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter}),
        event=SimpleNamespace(source=source, message_id="om_inbound"),
    )
    if entry == "send":
        metadata = {"thread_id": "omt_topic"}
        result = await adapter.send(source.chat_id, content, reply_to="om_inbound", metadata=metadata)
        expected_sent = [(source.chat_id, content, "om_inbound", metadata)]
        expected_edited = []
    else:
        result = await adapter.edit_message(source.chat_id, "om_outbound", content, finalize=True)
        expected_sent = []
        expected_edited = [(source.chat_id, "om_outbound", content, True)]

    assert result.success is True
    assert adapter.sent == expected_sent
    assert adapter.edited == expected_edited
    assert notice_path.notices == []
    assert notice_path.feishu.sent == notice_path.feishu.updated == []


@pytest.mark.parametrize("chat_id,content", [
    ("oc_group_1", "本周例会讨论了自我改进回顾的写法。"),
    ("oc_group_allow", _SELF_IMPROVEMENT_EN),
])
async def test_exact_chat_suppression_cannot_claim_detached_ordinary_body(notice_path, chat_id, content):
    adapter = notice_path.adapter
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter}),
    )
    result = await adapter.send(chat_id, content)

    assert result.success is True
    assert adapter.sent == [(chat_id, content, None, None)]
    assert notice_path.notices == []
    assert notice_path.feishu.sent == notice_path.feishu.updated == []


async def test_detached_review_uses_observed_group_type(notice_path):
    client, feishu = notice_path.client, notice_path.feishu
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


@pytest.mark.parametrize("outcome", ["not_sent", "unknown", "delivered"])
async def test_platform_notice_awaits_independent_card_before_native_fallback(notice_path, outcome):
    adapter = notice_path.adapter
    notice_path.feishu.outcome = outcome

    class Gateway:
        adapters = {"feishu": adapter}

        async def _deliver_platform_notice(self, source, content):
            # Older managed Hermes source can still contain the synchronous hook.
            if hook_runtime.handle_platform_notice_from_hermes(self, source, content):
                return
            return await adapter.send(source.chat_id, content, metadata={"thread_id": source.thread_id})

    gateway = Gateway()
    assert hook_runtime.install_feishu_command_card_adapter_methods(gateway)
    source = SimpleNamespace(platform="feishu", chat_id="oc_group_1", chat_type="group", thread_id="omt_notice")
    content = "📬 No home channel is set for Feishu. Type /sethome to make this chat your home channel."
    assert await hook_runtime._hfc_direct_card_allowed_async(
        source.chat_id, notice_context=hook_runtime._hfc_notice_context_from_source(source),
    )
    result = await gateway._deliver_platform_notice(source, content)
    await asyncio.wait_for(notice_path.feishu.card_attempted.wait(), timeout=2)

    if outcome == "not_sent":
        assert adapter.sent == [(source.chat_id, content, None, {"thread_id": source.thread_id})]
        assert result.message_id == "om_native_send"
    elif outcome == "unknown":
        assert adapter.sent == [(source.chat_id, hook_runtime._NOTICE_UNCERTAIN_WARNING, None, {
            "thread_id": source.thread_id,
        })]
        assert result.message_id == "om_native_send"
    else:
        assert adapter.sent == []
    assert result.success is True
    [event] = notice_path.notices
    assert event["data"]["notice_scope"] == "independent"
    assert len(notice_path.feishu.sent) == 1
    assert notice_path.feishu.updated == []
