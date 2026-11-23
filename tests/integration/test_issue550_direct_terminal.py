"""A legacy topic can reuse its message ID on a turn with no opening event."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import hook_runtime, server
from hermes_feishu_card.card_limits import inspect_card_limits
from hermes_feishu_card.feishu_client import FeishuAPIError
from hermes_feishu_card.install.patcher import _render_complete_hook_block
from hermes_feishu_card.native_handoff import NativeHandoffStore
from tests.integration.test_reopened_delivery_identity import DeduplicatingClient
from tests.integration.test_server import FakeFeishuClient, create_app, event_payload


_NATIVE = {"ok": True, "applied": False, "disposition": "native"}
_DELIVERED = {"ok": True, "applied": True, "delivery": {"outcome": "delivered"}}


def _terminal(event, sequence, created_at, **data):
    return event_payload(
        event, sequence, {"policy_new_turn": True, **data},
        message_id="om_reused", conversation_id="omt_topic",
        thread_id="omt_topic", created_at=created_at,
    )


def _app(transport, state_dir, **card_config):
    return create_app(
        transport,
        card_config={"flush_interval_ms": 0, **card_config},
        session_store_directory=state_dir,
        native_handoff_store=NativeHandoffStore(state_dir / "handoff"),
    )


async def _post(client, event):
    response = await client.post("/events", json=event)
    assert response.status == 200, await response.text()
    return await response.json()


@pytest.fixture
async def old_v1_checkpoint(tmp_path):
    transport = DeduplicatingClient()
    old_terminal = _terminal("message.completed", 7, time.time() - 10, answer="Old full answer")
    async with TestClient(TestServer(_app(transport, tmp_path))) as client:
        assert (await _post(client, old_terminal)) == _DELIVERED
        store = client.app[server.SESSION_STORE_KEY]
        path, = store.root.glob("*.json")
        envelope = json.loads(path.read_bytes())
        record = envelope["record"]
        assert record["version"] == 1
        assert record["session"]["status"] == "completed"
        # Roll back the metadata to the v1 envelope written before fingerprints
        # and explicit thread provenance were saved on the record.
        record.pop("legacy_terminal_fingerprint", None)
        record.pop("legacy_route_thread_id", None)
        body = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
        envelope["digest"] = hashlib.sha256(body).hexdigest()
        path.write_text(json.dumps(envelope, ensure_ascii=False, allow_nan=False))
    return transport, old_terminal


@pytest.mark.parametrize(("event", "data", "text", "status"), [
    ("message.completed", {"answer": "New full answer"}, "New full answer", "completed"),
    ("message.failed", {"error": "New execution error"}, "New execution error", "failed"),
])
async def test_direct_new_terminal_reopens_restored_v1_once(
    tmp_path, old_v1_checkpoint, event, data, text, status,
):
    transport, old_terminal = old_v1_checkpoint
    async with TestClient(TestServer(_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        owner = client.app[server.SESSIONS_KEY]["om_reused"]
        assert owner.restored_legacy_terminal is True
        old_card = copy.deepcopy(transport.cards["card-0"])
        old_updates = transport.updated.count("card-0")
        # The producer can be ahead of the sidecar's local clock. A replay
        # must still compare against the accepted new terminal's identity.
        new_terminal = _terminal(event, 0, time.time() + 0.5, **data)

        result = await _post(client, new_terminal)
        assert result == _DELIVERED
        assert list(transport.cards) == ["card-0", "card-1"]
        assert text in json.dumps(transport.cards["card-1"])
        assert client.app[server.SESSIONS_KEY]["om_reused"].status == status

        assert (await _post(client, new_terminal))["applied"] is True
        assert await _post(client, old_terminal) == _NATIVE
        late_unmarked = {**old_terminal, "data": {"answer": "An older unmarked terminal"}}
        assert await _post(client, late_unmarked) == _NATIVE
        assert len(transport.attempted_uuids) == 2
        assert transport.cards["card-0"] == old_card
        assert transport.updated.count("card-0") == old_updates
        assert text in json.dumps(transport.cards["card-1"])

    async with TestClient(TestServer(_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        old_updates = transport.updated.count("card-0")
        updates = list(transport.updated)
        assert (await _post(client, new_terminal))["applied"] is True
        assert await _post(client, old_terminal) == _NATIVE
        assert len(transport.attempted_uuids) == 2
        assert transport.updated == updates
        assert transport.cards["card-0"] == old_card
        assert transport.updated.count("card-0") == old_updates
        assert text in json.dumps(transport.cards["card-1"])


async def test_direct_terminal_segment_failure_hands_off_without_editing_old_card(
    tmp_path, old_v1_checkpoint, monkeypatch,
):
    transport, _ = old_v1_checkpoint
    new_terminal = _terminal("message.completed", 0, time.time(), answer=_long_answer())
    original_send = transport.send_card_delivery

    async def fail_continuation(*args, **kwargs):
        if len(transport.cards) == 2:
            raise FeishuAPIError("fixture continuation refused", retryable=False, outcome="not_sent")
        return await original_send(*args, **kwargs)

    async with TestClient(TestServer(_app(transport, tmp_path, over_limit="segmented"))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        old_card = copy.deepcopy(transport.cards["card-0"])
        updates = list(transport.updated)
        monkeypatch.setattr(transport, "send_card_delivery", fail_continuation)

        assert await _post(client, new_terminal) == _NATIVE
        assert list(transport.cards) == ["card-0", "card-1"]
        assert "完整内容已切换为 Hermes 原生消息发送" in str(transport.cards["card-1"])
        assert transport.cards["card-0"] == old_card
        assert transport.updated == [*updates, "card-1"]


def _long_answer():
    return "\n\n".join(
        f"段落 {index:03d} 包含调研原始正文及逐项论证。" * 3
        for index in range(650)
    )


@pytest.mark.parametrize("over_limit", ["segmented", "native"])
async def test_direct_terminal_uses_current_card_limit_config(tmp_path, old_v1_checkpoint, over_limit):
    transport, _ = old_v1_checkpoint
    answer = _long_answer()
    new_terminal = _terminal("message.completed", 0, time.time(), answer=answer)
    async with TestClient(TestServer(_app(transport, tmp_path, over_limit=over_limit))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert client.app[server.SESSIONS_KEY]["om_reused"].restored_legacy_terminal
        old_card = copy.deepcopy(transport.cards["card-0"])
        updates = list(transport.updated)

        result = await _post(client, new_terminal)
        if over_limit == "segmented":
            assert result == _DELIVERED
            assert len(transport.cards) >= 4
            cards = list(transport.cards.values())[1:]
            assert all(inspect_card_limits(card).safe for card in cards)
            card_body = "\n".join(
                element["content"] for card in cards for element in card["body"]["elements"]
                if element.get("tag") == "markdown" and element.get("element_id", "").startswith("main_content")
            )
            visible_blocks = re.findall(r"段落 \d{3} 包含调研原始正文及逐项论证。", card_body)
            assert visible_blocks == re.findall(r"段落 \d{3} 包含调研原始正文及逐项论证。", answer)
            for index, card in enumerate(cards[1:], start=1):
                assert card["header"]["title"]["content"].endswith(f"（续 {index}/{len(cards) - 1}）")
        else:
            assert result == _NATIVE
            assert list(transport.cards) == ["card-0"]
            assert client.app[server.FEISHU_MESSAGE_IDS_KEY]["om_reused"] == "card-0"

        attempts = list(transport.attempted_uuids)
        await _post(client, new_terminal)
        assert transport.attempted_uuids == attempts
        assert transport.updated == updates
        assert transport.cards["card-0"] == old_card
        if over_limit == "native":
            later = _terminal(
                "message.completed", 0, new_terminal["created_at"] + 1, answer="Later short answer",
            )
            assert await _post(client, later) == _DELIVERED
            assert "Later short answer" in str(transport.cards["card-1"])
            assert transport.cards["card-0"] == old_card


async def test_legacy_gateway_keeps_native_body_then_suppresses_confirmed_short_body(
    tmp_path, old_v1_checkpoint, monkeypatch,
):
    transport, _ = old_v1_checkpoint
    native = FakeFeishuClient()
    long_body = _long_answer()
    short_body = "Later short answer"
    first_at = time.time()
    replies = []
    namespace = {}
    # Run the production completion hook, with only its event transport
    # supplied by the test's legacy /events replay fixture.
    exec(
        "async def complete(source, response, created_at, message_id):\n"
        "    _response_time = 0.0\n"
        "    agent_result = {}\n"
        + "".join(_render_complete_hook_block("    ", "\n"))
        + "    return response\n",
        namespace,
    )
    source = SimpleNamespace(platform="feishu", chat_id="oc_abc", thread_id="omt_topic")
    monkeypatch.setattr(hook_runtime, "can_stage_exact_base_completion", lambda _: False)
    async with TestClient(TestServer(_app(transport, tmp_path, over_limit="native"))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        old_card = copy.deepcopy(transport.cards["card-0"])

        async def emit(values, event_name):
            payload = _terminal(event_name, 0, values["created_at"], answer=values["answer"])
            assert payload["message_id"] == values["message_id"]
            reply = await _post(client, payload)
            replies.append(reply)
            return hook_runtime._event_was_applied(reply)

        monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
        for created_at, answer in ((first_at, long_body), (first_at, long_body), (first_at + 1, short_body)):
            native_body = await namespace["complete"](source, answer, created_at, "om_reused")
            if native_body is not None:
                await native.send_text_message(source.chat_id, native_body, thread_id=source.thread_id)

        assert replies == [_NATIVE, {"ok": True, "applied": True}, _DELIVERED]
        assert native.texts == [("oc_abc", long_body, "omt_topic", None)]
        assert list(transport.cards) == ["card-0", "card-1"]
        assert transport.cards["card-0"] == old_card
        assert short_body in str(transport.cards["card-1"])


@pytest.mark.parametrize("flag", ["intentional_silence", "native_fallback"])
async def test_direct_terminal_keeps_predecessor_when_body_bypasses_card(tmp_path, old_v1_checkpoint, flag):
    transport, _ = old_v1_checkpoint
    new_terminal = _terminal("message.completed", 0, time.time(), **{flag: True})
    async with TestClient(TestServer(_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        previous = copy.deepcopy(transport.cards)
        updates = list(transport.updated)

        response = {"ok": True, "applied": False}
        assert await _post(client, new_terminal) == response
        assert await _post(client, new_terminal) == response
        assert transport.cards == previous
        assert transport.updated == updates
        assert client.app[server.FEISHU_MESSAGE_IDS_KEY]["om_reused"] == "card-0"


@pytest.mark.parametrize("outcome", ["not_sent", "unknown"])
async def test_direct_terminal_create_failure_preserves_v1_and_replay_does_not_create(
    tmp_path, old_v1_checkpoint, monkeypatch, outcome,
):
    transport, _ = old_v1_checkpoint
    new_terminal = _terminal("message.completed", 0, time.time(), answer="New full answer")
    original_send = transport.send_card_delivery
    attempted_uuids = []

    async def fail_create(*args, **kwargs):
        attempted_uuids.append(kwargs["delivery_uuid"])
        if outcome == "unknown":
            await original_send(*args, **kwargs)
        raise FeishuAPIError("fixture create failed", retryable=False, outcome=outcome)

    async with TestClient(TestServer(_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        old_card = copy.deepcopy(transport.cards["card-0"])
        prior_updates = transport.updated.count("card-0")
        monkeypatch.setattr(transport, "send_card_delivery", fail_create)

        assert await _post(client, new_terminal) == _NATIVE
        assert await _post(client, new_terminal) == _NATIVE
        owner = client.app[server.SESSION_STORE_KEY].load()[0]
        assert owner["message_id"] == "card-0"
        assert owner["session"].pending_reopen_create.start_event == "message.completed"
        assert len(attempted_uuids) == 1
        assert transport.updated.count("card-0") == prior_updates

    async with TestClient(TestServer(_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        updates = list(transport.updated)
        assert await _post(client, new_terminal) == _NATIVE
        assert len(attempted_uuids) == 1
        assert transport.updated == updates
        assert transport.cards["card-0"] == old_card
        assert client.app[server.FEISHU_MESSAGE_IDS_KEY]["om_reused"] == "card-0"

        # A genuinely newer explicit terminal still has a fresh delivery slot.
        monkeypatch.setattr(transport, "send_card_delivery", original_send)
        next_terminal = _terminal(
            "message.completed", 0, new_terminal["created_at"] + 1, answer="Next full answer",
        )
        assert await _post(client, next_terminal) == _DELIVERED
        assert "Next full answer" in str(transport.cards[transport.ids[transport.attempted_uuids[-1]]])
        assert transport.attempted_uuids[-1] not in attempted_uuids
        assert transport.updated == updates
        assert transport.cards["card-0"] == old_card
