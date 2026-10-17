"""Interaction receipts keep their dialect; new output continues below them."""
import asyncio
import copy
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.server import create_app, SESSIONS_KEY, FEISHU_MESSAGE_IDS_KEY


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(tmp_path / "state"))


class Client:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.cross_dialect = []
        self.fail = False

    async def send_card(self, chat_id, card, **kwargs):
        if self.fail:
            raise RuntimeError("fixture send unavailable")
        mid = f"om_segment_{len(self.sent) + 1}"
        self.sent.append((mid, copy.deepcopy(card), kwargs))
        return mid

    async def update_card_message(self, mid, card):
        original = next(c for m, c, _ in self.sent if m == mid)
        if original.get("schema") != card.get("schema"):
            self.cross_dialect.append(mid)
        assert original.get("schema") == card.get("schema"), "cross-dialect PATCH"
        self.updated.append((mid, copy.deepcopy(card)))


def event(kind, sequence, data=None):
    return dict(schema_version="1", event=kind, sequence=sequence,
                conversation_id="topic_fixture", message_id="source_fixture",
                turn_id="turn_fixture", chat_id="chat_fixture", platform="feishu",
                created_at=time.time(), data=data or {})


async def post(http, kind, seq, data=None):
    response = await http.post("/events", json=event(kind, seq, data))
    assert response.status == 200, await response.text()
    return await response.json()


async def interact(http, seq, identifier, kind="clarify"):
    await post(http, "interaction.requested", seq, {
        "interaction_id": identifier, "kind": kind, "prompt": "Choose fixture",
        "options": [{"label": "Continue", "value": "once"}],
        "reply_to_message_id": "om_fixture_anchor", "reply_in_thread": True,
    })
    # Text delivery and native completion use the same identity-scoped transition.
    await post(http, "interaction.completed", seq + 1, {
        "interaction_id": identifier, "choice": "once", "choice_label": "Continue",
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["clarify", "approval"])
async def test_continuation_keeps_turn_history_and_updates_new_v2_owner(kind):
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0})
    async with TestClient(TestServer(app)) as http:
        await post(http, "message.started", 0)
        await post(http, "answer.delta", 1, {"text": "BEFORE_CHOICE"})
        await post(http, "tool.updated", 2, {"tool_id": "before", "name": "read", "status": "completed"})
        await interact(http, 3, "question_one", kind)
        assert len(client.sent) == 2, "no empty card merely for choosing"
        await post(http, "answer.delta", 5, {"text": "AFTER_CHOICE"})
        await asyncio.sleep(.02)
        assert len(client.sent) == 3
        session = app[SESSIONS_KEY]["turn_fixture"]
        assert session.message_id == "source_fixture"
        assert session.tool_count == 1 and "before" in session.tools
        assert "BEFORE_CHOICE" in session.answer_text or "BEFORE_CHOICE" in str(session.timeline)
        mid, card, route = client.sent[-1]
        assert card.get("schema") == "2.0"
        assert "AFTER_CHOICE" in str(card)
        assert "BEFORE_CHOICE" not in str(card.get("body", {}).get("elements", [])[:1])
        assert route.get("reply_to_message_id") == "om_fixture_anchor"
        assert app[FEISHU_MESSAGE_IDS_KEY]["turn_fixture"] == mid
        await post(http, "tool.updated", 6, {"tool_id": "after", "name": "write", "status": "completed"})
        await post(http, "message.completed", 7, {"answer": "FINAL_ANSWER"})
        await post(http, "message.completed", 7, {"answer": "FINAL_ANSWER"})
        await asyncio.sleep(.02)
        assert len(client.sent) == 3
        assert client.updated[-1][0] == mid
        assert "FINAL_ANSWER" in str(client.updated[-1][1])
        assert session.tool_count == 2


@pytest.mark.asyncio
async def test_batch_questions_do_not_create_empty_continuations_or_accept_old_question():
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0})
    async with TestClient(TestServer(app)) as http:
        await post(http, "message.started", 0)
        for seq in (1, 3, 5):
            await interact(http, seq, f"question_{seq}")
        assert len(client.sent) == 4
        await post(http, "interaction.completed", 7, {"interaction_id": "question_1", "choice": "stale"})
        await post(http, "answer.delta", 8, {"text": "AFTER_THREE_QUESTIONS"})
        await asyncio.sleep(.02)
        assert len(client.sent) == 5
        assert "AFTER_THREE_QUESTIONS" in str(client.sent[-1][1])
        assert app[SESSIONS_KEY]["turn_fixture"].active_interaction.choice == "once"


@pytest.mark.asyncio
async def test_continuation_send_failure_keeps_original_content_and_does_not_retry_each_delta():
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0})
    async with TestClient(TestServer(app)) as http:
        await post(http, "message.started", 0)
        await post(http, "answer.delta", 1, {"text": "KEEP_BEFORE"})
        await interact(http, 2, "question_one")
        client.fail = True
        await post(http, "answer.delta", 4, {"text": "KEEP_AFTER"})
        await post(http, "answer.delta", 5, {"text": "KEEP_TAIL"})
        await post(http, "message.failed", 6, {"error": "FIXTURE_FAILURE"})
        await asyncio.sleep(.02)
        assert app[FEISHU_MESSAGE_IDS_KEY]["turn_fixture"] == "om_segment_1"
        body = str(client.updated[-1][1])
        assert all(x in body for x in ("KEEP_BEFORE", "KEEP_AFTER", "KEEP_TAIL", "FIXTURE_FAILURE"))
        assert "续答" in body


@pytest.mark.asyncio
async def test_restart_preserves_continuation_owner_and_does_not_replay_selection(tmp_path):
    client = Client()
    def app():
        return create_app(client, card_config={"flush_interval_ms": 0}, session_store_directory=tmp_path)
    async with TestClient(TestServer(app())) as http:
        await post(http, "message.started", 0)
        await interact(http, 1, "question_one")
        await post(http, "answer.delta", 3, {"text": "PARTIAL_AFTER_CHOICE"})
    assert len(client.sent) == 3
    async with TestClient(TestServer(app())) as http:
        await post(http, "message.completed", 4, {"answer": "COMPLETE_AFTER_RESTART"})
        await asyncio.sleep(.02)
        assert len(client.sent) == 3
        assert client.updated[-1][0] == "om_segment_3"
        assert "COMPLETE_AFTER_RESTART" in str(client.updated[-1][1])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["callback", "text"])
@pytest.mark.parametrize("start_first", [False, True])
async def test_real_callback_or_text_completion_then_stream_and_terminal(mode, start_first):
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0, "interaction_mode": mode})
    async with TestClient(TestServer(app)) as http:
        if start_first:
            await post(http, "message.started", 0)
        await post(http, "interaction.requested", 1, {
            "interaction_id": "first_question", "kind": "clarify", "prompt": "Select",
            "options": [{"label": "A", "value": "a"}],
        })
        if mode == "callback":
            interaction = app[SESSIONS_KEY]["turn_fixture"].active_interaction
            payload = {"event": {"context": {"open_chat_id": "chat_fixture"},
                "operator": {"open_id": "ou_fixture"},
                "action": {"value": {"hfc_action": "interaction.select", "interaction_id": "first_question",
                    "token": interaction.callback_token, "choice": "a", "choice_label": "A"}}}}
            assert (await http.post("/card/actions", json=payload)).status == 200
            assert (await http.post("/card/actions", json=payload)).status == 409
        else:
            await post(http, "interaction.completed", 2, {"interaction_id": "first_question", "choice": "a"})
        before = len(client.sent)
        await post(http, "answer.delta", 3, {"text": "REAL_CONTINUATION"})
        assert len(client.sent) == before + 1
        new_mid = client.sent[-1][0]
        await post(http, "message.completed", 4, {"answer": "REAL_TERMINAL"})
        await asyncio.sleep(.02)
        assert client.updated[-1][0] == new_mid
        assert "REAL_TERMINAL" in str(client.updated[-1][1])


@pytest.mark.asyncio
async def test_native_runtime_selection_reaches_new_owner_without_consuming_transport_sequence():
    from hermes_feishu_card.runtime_interaction_transport import RuntimeInteractionListener
    secret = b"s" * 32
    resolved = []
    listener = RuntimeInteractionListener(secret, lambda payload: resolved.append(payload) or True)
    listener.start()
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0}, operations_transport_root_secret=secret)
    try:
        async with TestClient(TestServer(app)) as http:
            await post(http, "message.started", 0)
            payload = event("interaction.requested", 1, {
                "interaction_id": "native_question", "kind": "approval", "prompt": "Allow?",
                "allow_custom_input": False, "options": [{"label": "Once", "value": "once"}],
                "_hfc_runtime_admission": {"protocol": "hfc-runtime-interaction-v1", "runtime_id": "a" * 64,
                    "resolve_url": listener.resolve_url, "interaction_key": "b" * 64,
                    "token": "c" * 64, "expires_at": time.time() + 20},
            })
            payload.update(event_id="patch:turn_fixture:interaction:1", producer="patch", phase="started")
            response = await http.post("/events", json=payload)
            assert response.status == 200, await response.text()
            interaction = app[SESSIONS_KEY]["turn_fixture"].active_interaction
            action = {"event": {"context": {"open_chat_id": "chat_fixture", "profile_id": "default"},
                "operator": {"open_id": "ou_fixture"}, "action": {"value": {
                    "hfc_action": "interaction.select", "interaction_id": interaction.interaction_id,
                    "token": interaction.callback_token, "choice": "once"}}}}
            response = await http.post("/card/actions", json=action)
            assert response.status == 200, await response.text()
            assert len(resolved) == 1
            await post(http, "answer.delta", 2, {"text": "NATIVE_RESUMED"})
            assert len(client.sent) == 3
            assert "NATIVE_RESUMED" in str(client.sent[-1][1])
    finally:
        listener.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["callback", "text"])
async def test_second_question_failure_updates_its_receipt_and_keeps_answer(mode):
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0, "interaction_mode": mode})
    async with TestClient(TestServer(app)) as http:
        await post(http, "message.started", 0)
        await interact(http, 1, "question_one")
        await post(http, "answer.delta", 3, {"text": "KEEP_CONTINUATION"})
        await post(http, "interaction.requested", 4, {
            "interaction_id": "question_two", "kind": "clarify", "prompt": "Second question",
            "options": [{"label": "B", "value": "b"}],
            "reply_to_message_id": "om_fixture_anchor", "reply_in_thread": True,
        })
        receipt_id = client.sent[-1][0]
        await post(http, "interaction.failed", 5, {
            "interaction_id": "question_two", "error": "SECOND_QUESTION_FAILED",
        })
        await asyncio.sleep(.02)
        receipts = [card for mid, card in client.updated if mid == receipt_id]
        assert receipts and "SECOND_QUESTION_FAILED" in str(receipts[-1])
        assert "interaction.select" not in str(receipts[-1])
        assert "KEEP_CONTINUATION" in app[SESSIONS_KEY]["turn_fixture"].answer_text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["clarify", "approval"])
async def test_expired_interaction_continues_below_the_native_fallback(kind):
    # An unanswered card expires and Hermes re-asks natively below it; the agent's
    # work after that reply must not land in the card above the native question.
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0})
    async with TestClient(TestServer(app)) as http:
        await post(http, "message.started", 0)
        await post(http, "answer.delta", 1, {"text": "BEFORE_QUESTION"})
        await post(http, "interaction.requested", 2, {
            "interaction_id": "expired_question", "kind": kind, "prompt": "Choose fixture",
            "options": [{"label": "Continue", "value": "once"}],
            "reply_to_message_id": "om_fixture_anchor", "reply_in_thread": True,
        })
        before = len(client.sent)
        expired = {"interaction_id": "expired_question", "error": "交互已过期"}
        await post(http, "interaction.failed", 3, expired)
        await post(http, "interaction.failed", 3, expired)
        # A click that lands after expiry cannot reopen the question.
        await post(http, "interaction.completed", 4, {
            "interaction_id": "expired_question", "choice": "once", "choice_label": "Continue",
        })
        await asyncio.sleep(.02)
        assert len(client.sent) == before, "no empty card merely for expiring"
        session = app[SESSIONS_KEY]["turn_fixture"]
        assert session.active_interaction.status == "failed"
        generation = session.display_segment["generation"]
        await post(http, "answer.delta", 5, {"text": "AFTER_NATIVE_REPLY"})
        await post(http, "answer.delta", 6, {"text": "MORE_AFTER_REPLY"})
        await asyncio.sleep(.02)
        assert len(client.sent) == before + 1
        assert session.display_segment["generation"] == generation + 1
        mid, card, route = client.sent[-1]
        assert "AFTER_NATIVE_REPLY" in str(card)
        assert "BEFORE_QUESTION" not in str(card.get("body", {}).get("elements", [])[:1])
        assert route.get("reply_to_message_id") == "om_fixture_anchor"
        assert app[FEISHU_MESSAGE_IDS_KEY]["turn_fixture"] == mid
        await post(http, "message.completed", 7, {"answer": "FINAL_ANSWER"})
        await asyncio.sleep(.02)
        assert len(client.sent) == before + 1
        assert client.updated[-1][0] == mid
        assert "FINAL_ANSWER" in str(client.updated[-1][1])
        assert not client.cross_dialect


@pytest.mark.asyncio
async def test_terminal_closing_an_open_interaction_creates_no_continuation():
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0})
    async with TestClient(TestServer(app)) as http:
        await post(http, "message.started", 0)
        await post(http, "interaction.requested", 1, {
            "interaction_id": "open_question", "kind": "clarify", "prompt": "Choose fixture",
            "options": [{"label": "Continue", "value": "once"}],
            "reply_to_message_id": "om_fixture_anchor", "reply_in_thread": True,
        })
        before = len(client.sent)
        await post(http, "message.completed", 2, {"answer": "FINAL_ANSWER"})
        await asyncio.sleep(.02)
        assert len(client.sent) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', [False, True])
async def test_confirmed_handoff_freezes_old_card_without_claiming_turn_success(terminal):
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0})
    async with TestClient(TestServer(app)) as http:
        await post(http, 'message.started', 0)
        await post(http, 'answer.delta', 1, {'text':'BEFORE_SELECTION'})
        await post(http, 'tool.updated', 2, {'tool_id':'live', 'name':'clarify', 'status':'running'})
        await post(http, 'subagent.updated', 3, {'child_id':'worker', 'name':'Research', 'status':'running'})
        await interact(http, 4, 'question_one')
        old_id = app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture']
        await post(http, 'message.failed' if terminal else 'answer.delta', 6,
                   {'error':'AFTER_SELECTION_FAILURE'} if terminal else {'text':'AFTER_SELECTION'})
        await asyncio.sleep(.03)
        retired = next(card for mid, card in reversed(client.updated) if mid == old_id)
        assert retired['header']['template'] == 'blue'
        assert '本段已转入续答' in retired['header']['subtitle']['content']
        footer = next(e for e in retired['body']['elements'] if e.get('element_id') == 'footer')
        assert '下方新卡' in footer['content'] and '已完成' not in footer['content']
        assert 'BEFORE_SELECTION' in str(retired) and 'AFTER_SELECTION' not in str(retired)
        assert ('已选择' in str(retired)) is (not terminal)
        assert ('Choose fixture' in str(retired)) is (not terminal)
        # A terminal cleanup can retire this duplicate only after an explicit
        # full PATCH of the separate interaction receipt, in its own dialect.
        if terminal:
            receipt = next(card for mid, card in reversed(client.updated) if mid == client.sent[1][0])
            assert '已选择' in str(receipt) and 'Choose fixture' in str(receipt)
        assert '执行中' not in str(retired) and '已中断' not in str(retired)
        assert retired['config']['streaming_mode'] is False
        session = app[SESSIONS_KEY]['turn_fixture']
        assert session.tools['live'].status == 'running'
        assert 'display_handoff' not in str(session.timeline)
        if terminal:
            assert session.status == 'failed'
        else:
            assert session.status not in {'completed', 'failed'}
        assert app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture'] != old_id


@pytest.mark.asyncio
@pytest.mark.parametrize("show_reasoning", [True, False])
async def test_task_handoff_has_one_honest_note_and_retains_tool_details(show_reasoning):
    client = Client()
    app = create_app(client, card_config={
        "flush_interval_ms": 0, "reading_preset": "task", "show_reasoning": show_reasoning,
    })
    async with TestClient(TestServer(app)) as http:
        await post(http, "message.started", 0)
        await post(http, "tool.updated", 1, {
            "tool_id": "live", "name": "terminal", "status": "running",
            "detail": 'fixture.py\n参数: {"command": "fixture.py", "timeout": 120}',
        })
        await post(http, "interaction.requested", 2, {
            "interaction_id": "question_one", "kind": "clarify", "prompt": "Choose fixture",
            "options": [{"label": "Continue", "value": "once"}],
        })
        old_id = client.sent[0][0]
        retired = next(card for mid, card in reversed(client.updated) if mid == old_id)
        assert retired["header"]["template"] == "default"
        assert "已转入交互卡片" in str(retired["header"])
        assert "执行中" not in str(retired) and "已中断" not in str(retired)
        assert "fixture.py" in str(retired) and "120" in str(retired)
        assert not any(e.get("element_id") in {"footer", "task_observation", "main_divider"}
                       for e in retired["body"]["elements"])
        if show_reasoning:
            assert not any(e.get("element_id", "").startswith("tool_activity_")
                           for e in retired["body"]["elements"])
        session = app[SESSIONS_KEY]["turn_fixture"]
        assert session.tools["live"].status == "running"
        assert session.active_interaction.status == "pending"
        await post(http, "interaction.completed", 3, {"interaction_id": "question_one", "choice": "once"})
        await post(http, "answer.delta", 4, {"text": "NEW_RESULT"})
        await asyncio.sleep(.03)
        retired = next(card for mid, card in reversed(client.updated) if mid == old_id)
        assert retired["header"]["template"] == "blue"
        assert retired["header"]["title"]["content"] == "已转入续答"
        assert str(retired["body"]).count("本段已转入续答") == 0
        assert "执行中" not in str(retired) and "已中断" not in str(retired)
        assert session.tools["live"].status == "running"


@pytest.mark.asyncio
async def test_task_paused_approval_does_not_duplicate_scope_on_predecessor():
    from hermes_feishu_card.server import _expire_pending_interactions
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0, "reading_preset": "task"})
    async with TestClient(TestServer(app)) as http:
        await post(http, "message.started", 0)
        await post(http, "interaction.requested", 1, {
            "interaction_id": "approval_one", "kind": "approval", "prompt": "Confirm fixture",
            "description": "EXACT_APPROVAL_SCOPE", "pause_on_timeout": True, "timeout_seconds": 1,
            "options": [{"label": "允许一次", "value": "once"}, {"label": "拒绝", "value": "deny"}],
        })
        session = app[SESSIONS_KEY]["turn_fixture"]
        interaction = session.active_interaction
        await _expire_pending_interactions(app, now=interaction.expires_at + 1)
        await asyncio.sleep(.03)
        assert interaction.status == "paused" and not interaction.choice
        predecessor = next(card for mid, card in reversed(client.updated) if mid == client.sent[0][0])
        approval = next(card for mid, card in reversed(client.updated) if mid == interaction.feishu_message_id)
        assert "EXACT_APPROVAL_SCOPE" not in str(predecessor)
        assert "继续审批" in str(predecessor) and "执行中" not in str(predecessor)
        assert "EXACT_APPROVAL_SCOPE" in str(approval) and "查看并继续审批" in str(approval)
        assert "schema" not in approval and predecessor["schema"] == "2.0"


@pytest.mark.asyncio
async def test_task_paused_second_approval_keeps_first_legacy_owner_dialect():
    from hermes_feishu_card.server import _expire_pending_interactions
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0, "reading_preset": "task"})
    async with TestClient(TestServer(app)) as http:
        # No message.started: the first interaction itself owns the legacy card.
        await interact(http, 0, "first_question")
        owner_id = app[FEISHU_MESSAGE_IDS_KEY]["turn_fixture"]
        await post(http, "interaction.requested", 2, {
            "interaction_id": "approval_two", "kind": "approval", "prompt": "Confirm fixture",
            "description": "EXACT_APPROVAL_SCOPE", "pause_on_timeout": True, "timeout_seconds": 1,
            "reply_to_message_id": "om_fixture_anchor", "reply_in_thread": True,
            "options": [{"label": "允许一次", "value": "once"}, {"label": "拒绝", "value": "deny"}],
        })
        session = app[SESSIONS_KEY]["turn_fixture"]
        interaction = session.active_interaction
        assert interaction.feishu_message_id != owner_id
        await _expire_pending_interactions(app, now=interaction.expires_at + 1)
        await asyncio.sleep(.03)

        assert not client.cross_dialect
        assert app[FEISHU_MESSAGE_IDS_KEY]["turn_fixture"] == owner_id
        assert interaction.status == "paused" and not interaction.choice
        predecessor = next(card for mid, card in reversed(client.updated) if mid == owner_id)
        approval = next(card for mid, card in reversed(client.updated) if mid == interaction.feishu_message_id)
        assert "schema" not in predecessor and "schema" not in approval
        assert "Choose fixture" in str(predecessor) and "已选择" in str(predecessor)
        assert "EXACT_APPROVAL_SCOPE" in str(approval) and "查看并继续审批" in str(approval)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["message.completed", "message.failed"])
@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("start_first", [False, True])
async def test_terminal_closes_unresolved_approval_and_rejects_late_choice(terminal, paused, start_first):
    from hermes_feishu_card.server import _expire_pending_interactions
    client = Client()
    app = create_app(client, card_config={"flush_interval_ms": 0, "reading_preset": "task"})
    async with TestClient(TestServer(app)) as http:
        if start_first:
            await post(http, "message.started", 0)
        await post(http, "interaction.requested", 1, {
            "interaction_id": "terminal_approval", "kind": "approval", "prompt": "Confirm fixture",
            "description": "KEEP_EXACT_SCOPE", "pause_on_timeout": True, "timeout_seconds": 300,
            "options": [{"label": "允许一次", "value": "once"}, {"label": "拒绝", "value": "deny"}],
        })
        session = app[SESSIONS_KEY]["turn_fixture"]
        interaction = session.active_interaction
        token = interaction.callback_token
        receipt_id = interaction.feishu_message_id
        if paused:
            await _expire_pending_interactions(app, now=interaction.expires_at + 1)
            await asyncio.sleep(.02)
        await post(http, terminal, 2, {"answer": "ACTUAL_TERMINAL_RESULT", "error": "ACTUAL_TERMINAL_RESULT"})
        await asyncio.sleep(.03)
        assert interaction.status == "failed" and not interaction.choice
        result = await (await http.get("/interactions/terminal_approval")).json()
        assert result["status"] == "failed" and not result["choice"]
        receipt = next(card for mid, card in reversed(client.updated) if mid == receipt_id)
        assert "KEEP_EXACT_SCOPE" in str(receipt)
        assert "查看并继续审批" not in str(receipt) and "hfc_action" not in str(receipt)
        owner = next(card for mid, card in reversed(client.updated)
                     if mid == app[FEISHU_MESSAGE_IDS_KEY]["turn_fixture"])
        assert "ACTUAL_TERMINAL_RESULT" in str(owner)
        assert "等待选择" not in str(owner) and "分钟后过期" not in str(owner)
        payload = {"event": {"context": {"open_chat_id": "chat_fixture"},
            "operator": {"open_id": "ou_fixture"}, "action": {"value": {
                "hfc_action": "interaction.select", "interaction_id": "terminal_approval",
                "token": token, "choice": "once"}}}}
        assert (await http.post("/card/actions", json=payload)).status in {404, 409}
        assert not interaction.choice and not client.cross_dialect


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["message.completed", "message.failed"])
async def test_terminal_ack_and_answer_do_not_wait_for_receipt_or_repeat_its_patch(terminal):
    class BlockingReceiptClient(Client):
        def __init__(self):
            super().__init__()
            self.receipt_id = ""
            self.receipt_started = asyncio.Event()
            self.release_receipt = asyncio.Event()
            self.receipt_updates = 0

        async def update_card_message(self, mid, card):
            if mid == self.receipt_id:
                self.receipt_updates += 1
                self.receipt_started.set()
                await self.release_receipt.wait()
            await super().update_card_message(mid, card)

    client = BlockingReceiptClient()
    app = create_app(client, card_config={"flush_interval_ms": 0, "reading_preset": "task"})
    async with TestClient(TestServer(app)) as http:
        await post(http, "message.started", 0)
        await post(http, "interaction.requested", 1, {
            "interaction_id": "slow_receipt", "kind": "approval", "prompt": "Confirm fixture",
            "description": "KEEP_EXACT_SCOPE", "timeout_seconds": 300,
            "options": [{"label": "允许一次", "value": "once"}],
        })
        session = app[SESSIONS_KEY]["turn_fixture"]
        client.receipt_id = session.active_interaction.feishu_message_id
        owner_id = app[FEISHU_MESSAGE_IDS_KEY]["turn_fixture"]
        try:
            # Receipt PATCH remains blocked. Neither the ACK nor the actual
            # final answer may depend on this optional display cleanup.
            result = await asyncio.wait_for(post(http, terminal, 2, {
                "answer": "FINAL_WITHOUT_RECEIPT", "error": "FINAL_WITHOUT_RECEIPT",
            }), timeout=.2)
            assert result == {"ok": True, "applied": True}
            await asyncio.wait_for(client.receipt_started.wait(), timeout=.5)
            assert any(mid == owner_id and "FINAL_WITHOUT_RECEIPT" in str(card)
                       for mid, card in client.updated)
            assert session.active_interaction.status == "failed"
            await asyncio.wait_for(post(http, terminal, 2, {
                "answer": "DUPLICATE_MUST_NOT_REPLACE", "error": "DUPLICATE_MUST_NOT_REPLACE",
            }), timeout=.2)
            assert client.receipt_updates == 1
        finally:
            client.release_receipt.set()
        for _ in range(50):
            if any(mid == client.receipt_id for mid, _ in client.updated):
                break
            await asyncio.sleep(.01)
        receipt = next(card for mid, card in reversed(client.updated) if mid == client.receipt_id)
        assert "KEEP_EXACT_SCOPE" in str(receipt) and "hfc_action" not in str(receipt)
        assert not client.cross_dialect


@pytest.mark.asyncio
async def test_failed_predecessor_patch_keeps_new_owner_and_terminal_delivery():
    from hermes_feishu_card.server import DIAGNOSTICS_KEY
    class FailingOldUpdate(Client):
        async def update_card_message(self, mid, card):
            if '本段已转入续答' in card.get('header', {}).get('subtitle', {}).get('content', ''):
                raise RuntimeError('fixture old message unavailable')
            return await super().update_card_message(mid, card)
    client = FailingOldUpdate()
    app = create_app(client, card_config={'flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http, 'message.started', 0)
        await interact(http, 1, 'question_one')
        await post(http, 'answer.delta', 3, {'text':'NEW_OWNER_CONTENT'})
        owner = app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture']
        await post(http, 'message.completed', 4, {'answer':'FINAL_NEW_OWNER'})
        assert owner == 'om_segment_3'
        assert 'FINAL_NEW_OWNER' in str(client.updated[-1][1])
        assert app[DIAGNOSTICS_KEY]['last_continuation_predecessor'] == 'update_failed'


@pytest.mark.asyncio
@pytest.mark.parametrize('start_first', [True, False])
@pytest.mark.parametrize('mode', ['text', 'callback'])
async def test_resolved_receipts_keep_dialect_choices_and_live_turn(start_first, mode):
    client = Client()
    app = create_app(client, card_config={'interaction_mode':mode, 'flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        if start_first:
            await post(http, 'message.started', 0)
            await post(http, 'tool.updated', 1, {'tool_id':'live', 'name':'clarify', 'status':'running'})
        receipt_ids = []
        for seq in (2, 4):
            await interact(http, seq, f'question_{seq}')
            receipt_ids.append(app[SESSIONS_KEY]['turn_fixture'].active_interaction.feishu_message_id)
            await asyncio.sleep(.02)
            for mid in receipt_ids:
                card = next(c for m, c in reversed(client.updated) if m == mid)
                if mode == 'text':
                    assert card['schema'] == '2.0'
                    assert '交互结果已记录' in str(card)
                else:
                    assert card.get('schema') is None
                    assert 'interaction.select' not in str(card)
                assert not client.cross_dialect
                assert 'Choose fixture' in str(card) and '已选择' in str(card)
                assert '执行中' not in str(card) and '已中断' not in str(card)
                assert '本轮回复结束' not in str(card)
            assert app[SESSIONS_KEY]['turn_fixture'].status not in {'completed','failed'}
        await post(http, 'message.completed', 6, {'answer':'FINAL_AFTER_TWO'})
        assert 'FINAL_AFTER_TWO' in str(client.updated[-1][1])


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['callback', 'text'])
@pytest.mark.parametrize('legacy_owner', [False, True])
async def test_failed_receipt_render_is_read_only_before_owner_policy_selection(monkeypatch, mode, legacy_owner):
    """A pre-render snapshot is valid only while the real renderer preserves it."""
    from hermes_feishu_card import server as sidecar
    from hermes_feishu_card.owner_display_policy import OwnerRefreshState
    client = Client()
    app = create_app(client, card_config={'flush_interval_ms': 0, 'interaction_mode': mode})
    trace = []
    original_render = sidecar._render_session_card_for_app
    original_source = OwnerRefreshState.card_source

    def stored_state(session):
        return copy.deepcopy({
            key: vars(value) if key in {'thinking_normalizer', 'answer_normalizer'} else value
            for key, value in vars(session).items()
        })

    def render(app, session, **kwargs):
        before = stored_state(session)
        interaction = session.active_interaction
        tracked = interaction is not None and interaction.status == 'failed' and bool(interaction.receipt_fingerprint)
        card = original_render(app, session, **kwargs)
        if tracked:
            assert stored_state(session) == before, 'failed receipt render changed the accepted owner'
            trace.append('failed-render')
        return card

    def select(snapshot, supplier):
        if snapshot.refresh_failed_receipt_first:
            assert trace and trace[-1] == 'failed-render'
            owner = app[SESSIONS_KEY]['turn_fixture']
            interaction = owner.active_interaction
            assert snapshot.owner_message_id == app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture']
            assert snapshot.interaction_status == interaction.status
            assert snapshot.interaction_message_id == interaction.feishu_message_id
            assert snapshot.has_legacy_owner_receipt == bool(owner.legacy_owner_receipt)
            assert snapshot.has_receipt_fingerprint == bool(interaction.receipt_fingerprint)
            trace.append('policy-selection')
        return original_source(snapshot, supplier)

    monkeypatch.setattr(sidecar, '_render_session_card_for_app', render)
    monkeypatch.setattr(OwnerRefreshState, 'card_source', select)
    async with TestClient(TestServer(app)) as http:
        if legacy_owner:
            await interact(http, 1, 'first-owner')
            sequence = 3
        else:
            await post(http, 'message.started', 0)
            sequence = 1
        await post(http, 'interaction.requested', sequence, {
            'interaction_id': 'failed-receipt', 'kind': 'clarify', 'prompt': 'Choose fixture',
            'options': [{'label': 'Continue', 'value': 'once'}],
            'reply_to_message_id': 'om_fixture_anchor', 'reply_in_thread': True,
        })
        owner = app[SESSIONS_KEY]['turn_fixture']
        original_owner_id = app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture']
        receipt_id = owner.active_interaction.feishu_message_id
        assert receipt_id != original_owner_id
        await post(http, 'interaction.failed', sequence + 1, {
            'interaction_id': 'failed-receipt', 'error': 'synthetic failure',
        })
        for _ in range(100):
            if 'policy-selection' in trace:
                break
            await asyncio.sleep(.01)
        assert 'policy-selection' in trace
        assert app[SESSIONS_KEY]['turn_fixture'] is owner
        assert app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture'] == original_owner_id
        assert owner.active_interaction.status == 'failed' and owner.active_interaction.receipt_fingerprint
        assert not client.cross_dialect


class NavigationClient(Client):
    def __init__(self, *, include_link=True, fail_new=False, fail_old_patch=False):
        super().__init__()
        self.include_link = include_link
        self.fail_new = fail_new
        self.fail_old_patch = fail_old_patch

    async def send_card_delivery(self, chat_id, card, **kwargs):
        from hermes_feishu_card.feishu_client import FeishuAPIError, FeishuSendResult
        from hermes_feishu_card.message_navigation import navigation_from_response
        if self.fail_new and len(self.sent) >= 2:
            raise FeishuAPIError('synthetic ambiguous create', outcome='unknown')
        mid = await self.send_card(chat_id, card, **kwargs)
        nav = None
        if self.include_link and len(self.sent) >= 3:
            thread = 'omt_navigation_fixture'
            link = ('https://applink.feishu.cn/client/thread/open?'
                    f'openchatid={chat_id}&openthreadid={thread}&thread_position=12')
            nav = navigation_from_response({'message_id':mid,'chat_id':chat_id,'thread_id':thread,
                'message_app_link':link,'thread_message_position':'12'},
                api_base_url='https://open.feishu.cn/open-apis',chat_id=chat_id,
                thread_id=kwargs.get('thread_id'),reply_in_thread=kwargs.get('reply_in_thread',False))
        return FeishuSendResult(mid, navigation=nav)

    async def update_card_message(self, mid, card):
        if self.fail_old_patch and any(e.get('element_id')=='continuation_link'
                                      for e in card.get('body',{}).get('elements',[])):
            raise RuntimeError('synthetic predecessor patch failure')
        return await super().update_card_message(mid,card)


def continuation_buttons(card):
    return [e for e in card.get('body',{}).get('elements',[])
            if e.get('element_id')=='continuation_link']


@pytest.mark.asyncio
@pytest.mark.parametrize('include_link',[False,True])
async def test_confirmed_continuation_navigation_is_optional_and_checkpoint_free(tmp_path,include_link):
    import json
    client=NavigationClient(include_link=include_link)
    app=create_app(client,card_config={'flush_interval_ms':0},session_store_directory=tmp_path)
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await post(http,'answer.delta',1,{'text':'KEEP_PREVIOUS_CONTENT'})
        await interact(http,2,'navigation_question')
        await post(http,'answer.delta',4,{'text':'KEEP_NEW_CONTENT'})
        await asyncio.sleep(.02)
        assert app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture']=='om_segment_3'
        retired=next(c for mid,c in reversed(client.updated) if mid=='om_segment_1')
        buttons=continuation_buttons(retired)
        assert len(buttons)==int(include_link)
        if include_link:
            assert buttons[0]['behaviors'][0]['type']=='open_url'
            assert 'hfc_action' not in str(buttons[0])
        assert 'KEEP_PREVIOUS_CONTENT' in str(retired)
        assert not client.cross_dialect
        await post(http,'message.completed',5,{'answer':'FINAL_NEW_CONTENT'})
        assert client.updated[-1][0]=='om_segment_3'
        assert 'FINAL_NEW_CONTENT' in str(client.updated[-1][1])
    checkpoints=list(tmp_path.rglob('*.json'))
    assert checkpoints
    for path in checkpoints:
        assert 'continuation_link' not in path.read_text()
        assert 'applink.feishu.cn' not in path.read_text()
    # Navigation is delivery-time metadata, never a new CardSession registry.
    from dataclasses import fields
    from hermes_feishu_card.session import CardSession
    assert not any('navigation' in f.name for f in fields(CardSession))


@pytest.mark.asyncio
async def test_unknown_continuation_create_never_installs_navigation_or_owner():
    client=NavigationClient(fail_new=True)
    app=create_app(client,card_config={'flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await post(http,'answer.delta',1,{'text':'KEEP_BEFORE'})
        await interact(http,2,'unknown_navigation')
        await post(http,'answer.delta',4,{'text':'KEEP_AFTER'})
        await post(http,'answer.delta',5,{'text':'KEEP_TAIL'})
        assert app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture']=='om_segment_1'
        assert len(client.sent)==2
        assert not any(continuation_buttons(c) for _,c in client.updated)
        assert 'KEEP_AFTER' in str(client.updated[-1][1])


@pytest.mark.asyncio
async def test_navigation_predecessor_patch_failure_does_not_undo_new_owner():
    client=NavigationClient(fail_old_patch=True)
    app=create_app(client,card_config={'flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await interact(http,1,'failed_navigation_patch')
        await post(http,'answer.delta',3,{'text':'NEW_OWNER_BODY'})
        assert app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture']=='om_segment_3'
        await post(http,'message.completed',4,{'answer':'FINAL_STILL_DELIVERED'})
        assert client.updated[-1][0]=='om_segment_3'
        assert 'FINAL_STILL_DELIVERED' in str(client.updated[-1][1])


@pytest.mark.asyncio
async def test_old_string_send_interface_remains_navigation_free():
    client=Client()
    app=create_app(client,card_config={'flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await interact(http,1,'legacy_string_send')
        await post(http,'answer.delta',3,{'text':'NEW_BODY'})
        assert app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture']=='om_segment_3'
        assert not any(continuation_buttons(c) for _,c in client.updated)


@pytest.mark.asyncio
async def test_receipt_pruning_preserves_navigation_without_restoring_controls(monkeypatch):
    from hermes_feishu_card import server
    from hermes_feishu_card.session import CardSession, InteractionState
    from hermes_feishu_card.message_navigation import MessageNavigation
    owner=CardSession('fixture','turn_fixture','chat_fixture')
    snapshot=copy.deepcopy(owner)
    snapshot.active_interaction=InteractionState('receipt_fixture','approval','KEEP_SCOPE',
        status='completed',feishu_message_id='om_receipt_fixture')
    app=create_app(Client())
    app[SESSIONS_KEY]['turn_fixture']=owner
    app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture']='om_new_fixture'
    rendered={'schema':'2.0','body':{'elements':[
        {'tag':'markdown','element_id':'main_content','content':'KEEP_PREVIOUS_BODY'},
        {'tag':'markdown','element_id':'interaction_review_scope','content':'KEEP_SCOPE'},
        {'tag':'markdown','element_id':'interaction_result','content':'COMPLETED_RECEIPT'}]}}
    monkeypatch.setattr(server,'_render_static_display_card',lambda *args,**kwargs:copy.deepcopy(rendered))
    updated=[]
    async def update(app,mid,card,bot_id):
        updated.append(copy.deepcopy(card))
        return True
    monkeypatch.setattr(server,'_update_card_for_app',update)
    link='https://applink.feishu.cn/client/chat/open?openChatId=chat_fixture&position=12'
    nav=MessageNavigation('om_new_fixture','chat_fixture',link)
    assert await server._retire_continuation_predecessor(app,'turn_fixture','om_old_fixture',snapshot,
                                                      bot_id=None,navigation=nav)
    assert len(continuation_buttons(updated[-1]))==1
    assert len(owner.approval_retirements)==1
    cleaned=owner.approval_retirements[0]['card']
    assert len(continuation_buttons(cleaned))==1
    assert 'KEEP_PREVIOUS_BODY' in str(cleaned)
    assert 'interaction_review_scope' not in str(cleaned)
    assert 'interaction_result' not in str(cleaned)
    assert not owner.approval_retirements[0]['interaction'].callback_token


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_continuation_queries_only_confirmed_new_owner(failure):
    from hermes_feishu_card.message_navigation import MessageNavigation

    class LookupClient(NavigationClient):
        def __init__(self):
            super().__init__(include_link=False)
            self.lookups = []

        async def lookup_message_navigation(self, mid, chat, **route):
            self.lookups.append((mid, chat, route))
            assert len(self.sent) == 3
            if failure:
                raise RuntimeError('synthetic lookup failure')
            return MessageNavigation(mid, chat,
                'https://applink.feishu.cn/client/thread/open?openchatid=chat_fixture&openthreadid=omt_fixture&thread_position=12')

    client = LookupClient()
    app = create_app(client, card_config={'flush_interval_ms': 0})
    async with TestClient(TestServer(app)) as http:
        await post(http, 'message.started', 0)
        await post(http, 'answer.delta', 1, {'text': 'KEEP_PREVIOUS'})
        await interact(http, 2, 'lookup_question')
        assert not client.lookups
        await post(http, 'answer.delta', 4, {'text': 'KEEP_NEW'})
        await asyncio.sleep(.02)
        assert len(client.lookups) == 1
        mid, chat, route = client.lookups[0]
        assert mid == app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture'] == 'om_segment_3'
        assert chat == 'chat_fixture' and route['reply_in_thread'] is True
        retired = next(c for m, c in reversed(client.updated) if m == 'om_segment_1')
        assert len(continuation_buttons(retired)) == int(not failure)
        assert 'KEEP_PREVIOUS' in str(retired)
        assert len(client.sent) == 3


@pytest.mark.asyncio
async def test_navigation_lookup_owner_change_does_not_patch_predecessor(monkeypatch):
    from hermes_feishu_card import server
    from hermes_feishu_card.session import CardSession
    client = Client()
    app = create_app(client)
    owner = CardSession('fixture', 'turn_fixture', 'chat_fixture')
    app[SESSIONS_KEY]['turn_fixture'] = owner
    app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture'] = 'om_new_fixture'
    async def lookup(*args, **kwargs):
        app[FEISHU_MESSAGE_IDS_KEY]['turn_fixture'] = 'om_replaced_fixture'
        return None
    monkeypatch.setattr(client, 'lookup_message_navigation', lookup, raising=False)
    monkeypatch.setattr(server, '_render_static_display_card', lambda *args, **kwargs:
        {'schema': '2.0', 'body': {'elements': [{'tag': 'markdown', 'content': 'KEEP'}]}})
    assert not await server._retire_continuation_predecessor(
        app, 'turn_fixture', 'om_old_fixture', copy.deepcopy(owner), bot_id=None)
    assert not client.updated
