import asyncio
import copy
import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.server import create_app, SESSIONS_KEY, FEISHU_MESSAGE_IDS_KEY
from hermes_feishu_card.card_limits import inspect_card_limits


@pytest.fixture(autouse=True)
def isolated_handoff_store(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(tmp_path / "handoff"))


class Client:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.cross_dialect = []
        self.fail_create = False

    async def send_card(self, chat_id, card, **kwargs):
        if self.fail_create:
            raise RuntimeError("fixture create unavailable")
        mid = f"legacy-owner-{len(self.sent)}"
        self.sent.append((mid, copy.deepcopy(card)))
        return mid

    async def update_card_message(self, mid, card):
        original = next(c for m, c in self.sent if m == mid)
        if original.get("schema") != card.get("schema"):
            self.cross_dialect.append((mid, card.get("schema")))
            raise RuntimeError("cross dialect rejected")
        self.updated.append((mid, copy.deepcopy(card)))


def event(kind, seq, data=None):
    return dict(schema_version="1", event=kind, sequence=seq, conversation_id="fixture", message_id="source", turn_id="turn", chat_id="fixture-chat", platform="feishu", created_at=time.time(), data=data or {})


async def post(http, kind, seq, data=None):
    response = await http.post("/events", json=event(kind, seq, data))
    assert response.status == 200, await response.text()
    return await response.json()


async def request_first(http, app, *, complete=True, description=""):
    await post(http, "interaction.requested", 1, {"interaction_id": "q1", "kind": "clarify", "prompt": "ORIGINAL_QUESTION", "description": description, "options": [{"label": "ORIGINAL_CHOICE", "value": "a"}]})
    token = app[SESSIONS_KEY]["turn"].active_interaction.callback_token
    if complete:
        await post(http, "interaction.completed", 2, {"interaction_id": "q1", "choice": "a", "choice_label": "ORIGINAL_CHOICE"})
    return token


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_first_legacy_owner_retains_receipt_and_content_when_continuation_create_fails(terminal):
    client = Client()
    app = create_app(client, card_config={"interaction_mode": "callback", "flush_interval_ms": 0})
    async with TestClient(TestServer(app)) as http:
        await request_first(http, app)
        client.fail_create = True
        await post(http, "answer.delta", 3, {"text": "PRESERVED_PARTIAL"})
        await asyncio.sleep(.03)
        assert not client.cross_dialect
        assert "PRESERVED_PARTIAL" in str(client.updated[-1][1])
        data = {"answer": "PRESERVED_PARTIAL FINAL_RESULT"} if terminal == "completed" else {"error": "FAILURE_REASON"}
        await post(http, f"message.{terminal}", 4, data)
        assert not client.cross_dialect
        card = client.updated[-1][1]
        assert card.get("schema") is None
        assert all(text in str(card) for text in ["ORIGINAL_QUESTION", "ORIGINAL_CHOICE", "PRESERVED_PARTIAL", "FINAL_RESULT" if terminal == "completed" else "FAILURE_REASON"])
        assert inspect_card_limits(card).safe
        assert len(client.sent) == 1
        assert app[SESSIONS_KEY]["turn"].terminal_delivery_state == "delivered"


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [True, False])
async def test_legacy_owner_restart_keeps_static_receipt_without_callback_tokens(tmp_path, complete):
    client = Client()
    def make_app():
        return create_app(client, card_config={"interaction_mode": "callback", "flush_interval_ms": 0}, session_store_directory=tmp_path)
    first = make_app()
    async with TestClient(TestServer(first)) as http:
        old_token = await request_first(http, first, complete=complete)
    snapshots = "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert old_token not in snapshots
    second = make_app()
    async with TestClient(TestServer(second)) as http:
        await asyncio.sleep(.05)
        assert not client.cross_dialect
        card = client.updated[-1][1]
        assert "ORIGINAL_QUESTION" in str(card)
        assert "连接已重建" in str(card)
        assert "interaction.select" not in str(card)
        assert old_token not in str(card)
        assert second[SESSIONS_KEY]["turn"].active_interaction is None
        if complete:
            await post(http, "answer.delta", 3, {"text": "NEW_V2_CONTINUATION"})
            assert len(client.sent) == 2
            assert client.sent[-1][1].get("schema") == "2.0"
            assert "NEW_V2_CONTINUATION" in str(client.sent[-1][1])
            assert second[FEISHU_MESSAGE_IDS_KEY]["turn"] == client.sent[-1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("description,answer", [("", "大段结果\n" * 2400), ("scope " * 2500, "result " * 2200)], ids=["oversized-answer", "receipt-pushes-over-limit"])
async def test_failed_continuation_legacy_owner_uses_native_handoff_for_large_final_answer(description, answer):
    client = Client()
    app = create_app(client, card_config={"interaction_mode": "callback", "flush_interval_ms": 0})
    async with TestClient(TestServer(app)) as http:
        await request_first(http, app, description=description)
        client.fail_create = True
        await post(http, "answer.delta", 3, {"text": "SHORT_PARTIAL"})
        result = await post(http, "message.completed", 4, {"answer": answer})
        assert result["applied"] is False and result["disposition"] == "native"
        assert not client.cross_dialect
        assert "ORIGINAL_QUESTION" in str(client.updated[-1][1])
        assert "ORIGINAL_CHOICE" in str(client.updated[-1][1])
        assert inspect_card_limits(client.updated[-1][1]).safe
