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
        self.fail = False

    async def send_card(self, chat_id, card, **kwargs):
        if self.fail:
            raise RuntimeError("fixture send unavailable")
        mid = f"om_segment_{len(self.sent) + 1}"
        self.sent.append((mid, copy.deepcopy(card), kwargs))
        return mid

    async def update_card_message(self, mid, card):
        original = next(c for m, c, _ in self.sent if m == mid)
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
        assert '已选择' in str(retired) and 'Choose fixture' in str(retired)
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
