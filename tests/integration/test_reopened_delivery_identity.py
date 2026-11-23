"""Issue #359: real IM create UUID deduplication must not overwrite old cards."""
import asyncio
import copy
import hashlib
import json
import time
from contextlib import AsyncExitStack
from types import SimpleNamespace
import pytest
from aiohttp.test_utils import TestClient, TestServer
from hermes_feishu_card import server
from hermes_feishu_card.feishu_client import FeishuAPIError
from hermes_feishu_card.native_handoff import NativeHandoffStore
from tests.integration.test_server import create_app, event_payload, wait_for_condition
from tests.integration.test_server_segmented import oversize_answer

class DeduplicatingClient:
    def __init__(self):
        self.ids = {}; self.cards = {}; self.updated = []
        self.attempted_uuids = []
    async def send_card_delivery(self, chat_id, card, **kwargs):
        token = kwargs['delivery_uuid']
        self.attempted_uuids.append(token)
        if token not in self.ids:
            mid = f'card-{len(self.ids)}';self.ids[token]=mid;self.cards[mid]=copy.deepcopy(card)
        return SimpleNamespace(message_id=self.ids[token],retry_count=0)
    async def update_card_message(self, message_id, card):
        self.updated.append(message_id);self.cards[message_id]=copy.deepcopy(card)

@pytest.mark.parametrize('first_event', ['message.started','answer.delta'])
@pytest.mark.parametrize('terminal', ['message.completed','message.failed'])
async def test_reopening_twice_keeps_previous_cards_and_checkpoint(first_event, terminal, tmp_path):
    transport=DeduplicatingClient()
    app=create_app(transport,card_config={'flush_interval_ms':0},session_store_directory=tmp_path)
    async with TestClient(TestServer(app)) as client:
        async def post(name, seq, data=None):
            response=await client.post('/events',json=event_payload(name,seq,data))
            assert response.status==200,await response.text()
            return await response.json()
        await post('message.started',0)
        answer='Original substantial answer. '*50
        await post('answer.delta',1,{'text':answer})
        await post(terminal,2,{'answer':answer,'error':'expected fixture failure'} if terminal.endswith('failed') else {'answer':answer})
        old=copy.deepcopy(transport.cards); updates=list(transport.updated)
        for generation in (1,2):
            await post(first_event,0,{'policy_new_turn':True,'text':f'followup {generation}'})
            await post('message.completed',1,{'answer':f'followup {generation}'})
            assert len(transport.cards)==generation+1
            for mid,card in old.items():
                assert transport.cards[mid]==card
                assert transport.updated.count(mid)==updates.count(mid)
            old=copy.deepcopy(transport.cards);updates=list(transport.updated)
        late=await post('answer.delta',10,{'text':'late output'})
        assert late['applied'] is False
        assert transport.cards==old
        records=app[server.SESSION_STORE_KEY].load()
        active=[r for r in records if r['key']=='hermes-message-1']
        assert len(active)==1
        assert active[0]['message_id']=='card-2'
        assert active[0]['session'].answer_text=='followup 2'
        assert answer.rstrip() in json.dumps(transport.cards['card-0'])

@pytest.mark.parametrize("policy_marker", [False, True])
async def test_failed_reopen_keeps_original_until_followup_can_create(tmp_path, policy_marker):
    class FailedCreateClient(DeduplicatingClient):
        fail = False
        async def send_card_delivery(self, chat_id, card, **kwargs):
            if self.fail:
                raise FeishuAPIError('fixture refusal', retryable=False, outcome='not_sent')
            return await super().send_card_delivery(chat_id,card,**kwargs)
    transport=FailedCreateClient()
    app=create_app(transport,card_config={'flush_interval_ms':0},session_store_directory=tmp_path)
    async with TestClient(TestServer(app)) as client:
        async def post(name,seq,data=None):
            response=await client.post('/events',json=event_payload(name,seq,data))
            return response.status,await response.json()
        await post('message.started',0)
        await post('message.completed',1,{'answer':'ORIGINAL ANSWER'})
        original=copy.deepcopy(transport.cards['card-0'])
        transport.fail=True
        status,_=await post('message.started',0)
        assert status==502
        assert app[server.SESSIONS_KEY]['hermes-message-1'].answer_text=='ORIGINAL ANSWER'
        transport.fail=False
        await post('answer.delta',0,{'policy_new_turn':policy_marker,'text':'FOLLOWUP'})
        await post('message.completed',1,{'answer':'FOLLOWUP'})
        assert len(transport.cards)==2
        assert transport.cards['card-0']==original


def _topic_event(name, sequence, turn, **data):
    return event_payload(
        name,
        sequence,
        {"reply_to_message_id": "om_topic_anchor", "reply_in_thread": True, **data},
        thread_id="omt_topic",
        created_at=1777017600.0 + 10 * turn + sequence,
    )


def _long_topic_terminal(turn):
    marker = ("一轮要点", "二轮要点")[turn - 1]
    answer = oversize_answer().replace("结论要点", marker)
    return _topic_event("message.completed", 1, turn, answer=answer)


async def _post_event(client, payload, *, status=200):
    response = await client.post("/events", json=payload)
    assert response.status == status, await response.text()
    return await response.json()


def _segmented_app(transport, tmp_path):
    return create_app(
        transport,
        card_config={"over_limit": "segmented", "flush_interval_ms": 0},
        native_handoff_store=NativeHandoffStore(tmp_path / "handoff"),
        session_store_directory=tmp_path,
    )


async def test_recovered_terminal_sends_one_completion_notice_after_body_ack(tmp_path, monkeypatch):
    transport = DeduplicatingClient()
    notices = []
    sending, release = asyncio.Event(), asyncio.Event()

    async def send_notice(chat_id, text, **placement):
        notices.append((chat_id, text, placement))
        if len(notices) == 2:
            sending.set()
            await release.wait()
        return "om_notice"

    transport.send_text_message = send_notice

    def app():
        return create_app(transport, session_store_directory=tmp_path, card_config={
            "flush_interval_ms": 0, "completion_notify": {"enabled": True, "placement": "message"},
        })

    create = transport.send_card_delivery

    async def lose_ack(*args, **kwargs):
        await create(*args, **kwargs)
        raise FeishuAPIError("lost ACK", outcome="unknown")

    terminal = _topic_event("message.completed", 1, 2, answer="Recovered final")
    terminal.pop("thread_id")
    terminal["data"].pop("reply_to_message_id")
    terminal["data"].pop("reply_in_thread")
    async with TestClient(TestServer(app())) as client:
        await _post_event(client, _topic_event("message.started", 0, 1))
        await _post_event(client, _topic_event("message.completed", 1, 1, answer="First final"))
        await wait_for_condition(lambda: len(notices) == 1)
        with monkeypatch.context() as patch:
            patch.setattr(transport, "send_card_delivery", lose_ack)
            await _post_event(client, _topic_event("message.started", 0, 2), status=502)
        assert len(notices) == 1

    async with TestClient(TestServer(app())) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        try:
            assert (await asyncio.wait_for(_post_event(client, terminal), 2))["applied"]
            assert "Recovered final" in str(transport.cards["card-1"])
            await asyncio.wait_for(sending.wait(), 1)
            assert (await _post_event(client, terminal))["applied"]
        finally:
            release.set()
        await wait_for_condition(lambda: client.app[server.SESSION_STORE_KEY].load()[0][
            "session"].completion_notify_state == "sent")

    async with TestClient(TestServer(app())) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert (await _post_event(client, terminal))["applied"]
    assert len(notices) == 2
    assert notices[1][2] == {
        "thread_id": "omt_topic", "reply_to_message_id": "om_topic_anchor", "reply_in_thread": True,
    }


@pytest.mark.parametrize("reopen_event", ["message.started", "answer.delta"])
@pytest.mark.parametrize("cardless_first", [False, True], ids=["live", "cardless"])
async def test_reopened_long_answer_sends_new_continuations(tmp_path, reopen_event, cardless_first):
    transport = DeduplicatingClient()
    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        if not cardless_first:
            await _post_event(client, _topic_event("message.started", 0, 1))
        first = _long_topic_terminal(1)
        assert (await _post_event(client, first))["applied"] is True
        assert len(transport.cards) == 4
        first_uuids = list(transport.attempted_uuids)
        old_cards = copy.deepcopy(transport.cards)
        old_updates = list(transport.updated)
        assert (await _post_event(client, first))["applied"] is True
        assert transport.attempted_uuids == first_uuids

        second_start = _topic_event(reopen_event, 0, 2, policy_new_turn=True, text="第二轮")
        assert (await _post_event(client, second_start))["applied"] is True
        second = _long_topic_terminal(2)
        assert (await _post_event(client, second))["applied"] is True
        assert len(transport.cards) == 8
        second_uuids = transport.attempted_uuids[len(first_uuids):]
        assert len(second_uuids) == 4
        assert set(first_uuids).isdisjoint(second_uuids)
        assert all("一轮要点" in str(card) for card in old_cards.values())
        for old_id, old_card in old_cards.items():
            assert transport.cards[old_id] == old_card
            assert transport.updated.count(old_id) == old_updates.count(old_id)
        for index, token in enumerate(second_uuids[1:], start=1):
            card = transport.cards[transport.ids[token]]
            assert "二轮要点" in str(card)
            assert card["header"]["title"]["content"].endswith(f"（续 {index}/3）")

        updates_before_replay = list(transport.updated)
        assert (await _post_event(client, second))["applied"] is True
        assert transport.attempted_uuids == first_uuids + second_uuids
        assert transport.updated == updates_before_replay


async def test_failed_reopen_preserves_segmented_owner_for_retry_after_restart(tmp_path, monkeypatch):
    transport = DeduplicatingClient()
    first = _long_topic_terminal(1)
    second_start = _topic_event("message.started", 0, 2)

    async def refuse_create(*args, **kwargs):
        raise FeishuAPIError("fixture refusal", outcome="not_sent")

    app = _segmented_app(transport, tmp_path)
    async with TestClient(TestServer(app)) as client:
        assert (await _post_event(client, first))["applied"] is True
        old_cards = copy.deepcopy(transport.cards)
        old_updates = list(transport.updated)
        with monkeypatch.context() as patch:
            patch.setattr(transport, "send_card_delivery", refuse_create)
            result = await _post_event(client, second_start, status=502)
        assert result["delivery"]["outcome"] == "not_sent"
        assert app[server.FEISHU_MESSAGE_IDS_KEY]["hermes-message-1"] == "card-0"
        assert app[server.SESSIONS_KEY]["hermes-message-1"].terminal_disposition == "segmented"

    app = _segmented_app(transport, tmp_path)
    async with TestClient(TestServer(app)) as client:
        await app[server.SESSION_RESTORE_TASK_KEY]
        assert app[server.FEISHU_MESSAGE_IDS_KEY]["hermes-message-1"] == "card-0"
        assert (await _post_event(client, first))["applied"] is True
        assert len(transport.attempted_uuids) == len(old_cards) == 4
        assert transport.updated == old_updates

        assert (await _post_event(client, second_start))["applied"] is True
        second = _long_topic_terminal(2)
        assert (await _post_event(client, second))["applied"] is True
        assert len(transport.cards) == 8
        for old_id, old_card in old_cards.items():
            assert transport.cards[old_id] == old_card
            assert transport.updated.count(old_id) == old_updates.count(old_id)


@pytest.mark.parametrize("reopened", [False, True], ids=["cardless", "reopened"])
async def test_interrupted_segment_delivery_retries_same_slots_after_restart(tmp_path, monkeypatch, reopened):
    transport = DeduplicatingClient()
    segment_accepted = asyncio.Event()
    send_cancelled = asyncio.Event()
    send_card = transport.send_card_delivery

    async def hold_first_segment_response(chat_id, card, **kwargs):
        result = await send_card(chat_id, card, **kwargs)
        title = card.get("header", {}).get("title", {}).get("content", "")
        if title.endswith("（续 1/3）"):
            segment_accepted.set()
            try:
                await asyncio.Future()
            finally:
                send_cancelled.set()
        return result

    app = _segmented_app(transport, tmp_path)
    async with TestClient(TestServer(app)) as client:
        if reopened:
            assert (await _post_event(client, _long_topic_terminal(1)))["applied"] is True
            await _post_event(client, _topic_event("message.started", 0, 2))
            # The existing v1 active checkpoint already has the confirmed main ID.
            assert app[server.SESSION_STORE_KEY].load()[0]["message_id"] == "card-4"

        terminal = _long_topic_terminal(2)
        attempts_before = len(transport.attempted_uuids)
        with monkeypatch.context() as patch:
            patch.setattr(transport, "send_card_delivery", hold_first_segment_response)
            post = asyncio.create_task(_post_event(client, terminal))
            try:
                await asyncio.wait_for(segment_accepted.wait(), timeout=5)
            finally:
                post.cancel()
                await asyncio.gather(post, return_exceptions=True)
            await asyncio.wait_for(send_cancelled.wait(), timeout=5)

    interrupted_uuids = transport.attempted_uuids[attempts_before:]
    assert len(interrupted_uuids) == (1 if reopened else 2)
    accepted_card = transport.ids[interrupted_uuids[-1]]
    assert "二轮要点" in str(transport.cards[accepted_card])
    attempts_before = len(transport.attempted_uuids)

    app = _segmented_app(transport, tmp_path)
    async with TestClient(TestServer(app)) as client:
        await app[server.SESSION_RESTORE_TASK_KEY]
        assert app[server.DIAGNOSTICS_KEY]["card_checkpoints_restored"] == int(reopened)
        assert (await _post_event(client, terminal))["applied"] is True
        retried = transport.attempted_uuids[attempts_before:]
        assert retried[:len(interrupted_uuids)] == interrupted_uuids
        assert len(retried) == len(interrupted_uuids) + 2
        assert len(transport.cards) == (8 if reopened else 4)
        for token in retried[-3:]:
            assert "二轮要点" in str(transport.cards[transport.ids[token]])


async def test_replayed_alias_terminal_while_segmented_lead_is_in_flight(tmp_path, monkeypatch):
    transport = DeduplicatingClient()
    entered, release = asyncio.Event(), asyncio.Event()
    patch = transport.update_card_message

    async def held(message_id, card):
        if not entered.is_set() and "一轮要点" in str(card):
            entered.set()
            await release.wait()
        await patch(message_id, card)

    app = _segmented_app(transport, tmp_path)
    async with TestClient(TestServer(app)) as client:
        await _post_event(client, _topic_event("message.started", 0, 1))
        terminal = {**_long_topic_terminal(1), "message_id": "om_alias"}
        monkeypatch.setattr(transport, "update_card_message", held)
        first = asyncio.create_task(_post_event(client, terminal))
        await asyncio.wait_for(entered.wait(), 3)
        parsed = asyncio.Event()
        parse = server.SidecarEvent.from_dict

        def observe_parse(payload):
            event = parse(payload)
            parsed.set()
            return event

        monkeypatch.setattr(server.SidecarEvent, "from_dict", staticmethod(observe_parse))
        retry = asyncio.create_task(_post_event(client, terminal))
        try:
            await asyncio.wait_for(parsed.wait(), 2)
        finally:
            release.set()
        assert (await first)["applied"] is True
        assert (await retry)["applied"] is True
        assert len(transport.cards) == 4


async def test_old_quote_terminal_after_rebind_keeps_new_turn_writable(tmp_path):
    transport = DeduplicatingClient()
    app = _segmented_app(transport, tmp_path)
    anchor = {"reply_to_message_id": "om_shared"}
    async with TestClient(TestServer(app)) as client:
        await _post_event(client, event_payload("message.started", 0, anchor, message_id="om_a"))
        first = event_payload("message.completed", 1, {**anchor, "answer": "ALPHA"}, message_id="om_shared")
        assert (await _post_event(client, first))["applied"]
        await _post_event(client, event_payload("message.started", 0, anchor, message_id="om_b"))
        assert (await _post_event(client, first))["applied"]
        second = event_payload("message.completed", 1, {**anchor, "answer": "BETA"}, message_id="om_b")
        assert (await _post_event(client, second))["applied"]
        assert app[server.SESSIONS_KEY]["om_b"].answer_text == "BETA"
        assert len(transport.cards) == 2


async def test_same_reopen_request_reuses_accepted_create_after_lost_response(tmp_path, monkeypatch):
    transport = DeduplicatingClient()
    send_card = transport.send_card_delivery
    first = _long_topic_terminal(1)
    reopen = _topic_event("message.started", 0, 2)
    async def lose_response(*args, **kwargs):
        await send_card(*args, **kwargs)
        raise FeishuAPIError("lost response", outcome="unknown")
    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        assert (await _post_event(client, first))["applied"]
        with monkeypatch.context() as scope:
            scope.setattr(transport, "send_card_delivery", lose_response)
            assert (await _post_event(client, reopen, status=502))["delivery"]["outcome"] == "unknown"
    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert (await _post_event(client, reopen))["applied"]
        assert len(transport.cards) == 5


async def test_refused_reopen_restores_only_the_previous_display_aliases(tmp_path, monkeypatch):
    transport = DeduplicatingClient()
    primary = _topic_event("message.started", 0, 1)
    terminal = {**_long_topic_terminal(1), "message_id": "om_legacy"}
    reopen = _topic_event("message.started", 0, 2)

    async def refuse(*args, **kwargs):
        raise FeishuAPIError("declined", outcome="not_sent")

    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        assert (await _post_event(client, primary))["applied"]
        assert (await _post_event(client, terminal))["applied"]
        before = dict(client.app[server.SESSION_ALIASES_KEY])
        monkeypatch.setattr(transport, "send_card_delivery", refuse)
        assert (await _post_event(client, reopen, status=502))["delivery"]["outcome"] == "not_sent"
        assert client.app[server.SESSION_ALIASES_KEY] == before
    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert (await _post_event(client, terminal))["applied"]
        assert len(transport.cards) == 4


@pytest.mark.parametrize("restart", [False, True], ids=["live", "restart"])
@pytest.mark.parametrize("policy_marker", [False, True], ids=["ordinary-delta", "policy-new-turn"])
async def test_lost_reopen_ack_recovers_on_different_event(tmp_path, monkeypatch, restart, policy_marker):
    transport = DeduplicatingClient()
    send = transport.send_card_delivery

    async def lose_ack(*args, **kwargs):
        await send(*args, **kwargs)
        raise FeishuAPIError("fixture lost ACK", outcome="unknown")

    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(TestClient(TestServer(_segmented_app(transport, tmp_path))))
        assert (await _post_event(client, _long_topic_terminal(1)))["applied"] is True
        previous = copy.deepcopy(transport.cards)
        with monkeypatch.context() as scope:
            scope.setattr(transport, "send_card_delivery", lose_ack)
            result = await _post_event(client, _topic_event("message.started", 0, 2), status=502)
        assert result["delivery"]["outcome"] == "unknown"
        assert client.app[server.FEISHU_MESSAGE_IDS_KEY]["hermes-message-1"] == "card-0"
        reopen_uuid = transport.attempted_uuids[-1]

        if restart:
            await stack.aclose()
            client = await stack.enter_async_context(TestClient(TestServer(_segmented_app(transport, tmp_path))))
            await client.app[server.SESSION_RESTORE_TASK_KEY]

        # The later hook may omit placement metadata. Recovery must keep the
        # placement/UUID of the first create and update the actual message.
        delta = _topic_event("answer.delta", 1, 2, text="SECOND BODY", policy_new_turn=policy_marker)
        delta.pop("thread_id")
        delta["data"].pop("reply_to_message_id")
        delta["data"].pop("reply_in_thread")
        assert (await _post_event(client, delta))["applied"] is True
        assert transport.attempted_uuids[4:] == [reopen_uuid, reopen_uuid]
        assert (await _post_event(client, _topic_event("message.completed", 2, 2)))["applied"] is True
        await wait_for_condition(lambda: "SECOND BODY" in str(transport.cards["card-4"]))
        assert len(transport.cards) == 5
        assert all(transport.cards[mid] == card for mid, card in previous.items())


@pytest.mark.parametrize("restart", [False, True], ids=["live", "restart"])
@pytest.mark.parametrize("outcome", ["unknown", "not_sent"])
async def test_unconfirmed_reopen_terminal_leaves_body_to_native(tmp_path, monkeypatch, restart, outcome):
    transport = DeduplicatingClient()
    send = transport.send_card_delivery

    async def lose_ack(*args, **kwargs):
        await send(*args, **kwargs)
        raise FeishuAPIError("fixture lost ACK", outcome="unknown")

    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(TestClient(TestServer(_segmented_app(transport, tmp_path))))
        assert (await _post_event(client, _long_topic_terminal(1)))["applied"] is True
        previous = copy.deepcopy(transport.cards)
        with monkeypatch.context() as scope:
            scope.setattr(transport, "send_card_delivery", lose_ack)
            assert (await _post_event(client, _topic_event("message.started", 0, 2), status=502))["delivery"]["outcome"] == "unknown"
        reopen_uuid = transport.attempted_uuids[-1]

        if restart:
            await stack.aclose()
            client = await stack.enter_async_context(TestClient(TestServer(_segmented_app(transport, tmp_path))))
            await client.app[server.SESSION_RESTORE_TASK_KEY]

        async def refuse_or_lose_ack(chat_id, card, **kwargs):
            transport.attempted_uuids.append(kwargs["delivery_uuid"])
            raise FeishuAPIError("fixture cannot confirm create", outcome=outcome)

        monkeypatch.setattr(transport, "send_card_delivery", refuse_or_lose_ack)
        terminal = _topic_event("message.completed", 2, 2, answer="BODY FOR NATIVE")
        for _ in range(2):
            result = await _post_event(client, terminal)
            assert result["applied"] is False
            assert result["disposition"] == "native"
        if restart:
            await stack.aclose()
            client = await stack.enter_async_context(TestClient(TestServer(_segmented_app(transport, tmp_path))))
            await client.app[server.SESSION_RESTORE_TASK_KEY]
            assert (await _post_event(client, terminal))["disposition"] == "native"
        assert transport.attempted_uuids[4:] == [reopen_uuid, reopen_uuid]
        assert len(transport.cards) == 5
        assert all(transport.cards[mid] == card for mid, card in previous.items())


@pytest.mark.parametrize("patch_succeeds", [True, False], ids=["confirmed-body", "unconfirmed-body"])
async def test_terminal_after_restarted_reopen_requires_body_ack(tmp_path, monkeypatch, patch_succeeds):
    transport = DeduplicatingClient()
    send = transport.send_card_delivery

    async def lose_ack(*args, **kwargs):
        await send(*args, **kwargs)
        raise FeishuAPIError("fixture lost ACK", outcome="unknown")

    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        assert (await _post_event(client, _long_topic_terminal(1)))["applied"] is True
        previous = copy.deepcopy(transport.cards)
        with monkeypatch.context() as scope:
            scope.setattr(transport, "send_card_delivery", lose_ack)
            result = await _post_event(client, _topic_event("message.started", 0, 2), status=502)
            assert result["delivery"]["outcome"] == "unknown"
    reopen_uuid = transport.attempted_uuids[-1]
    reopened_message = transport.ids[reopen_uuid]

    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        if not patch_succeeds:
            async def reject_patch(_message_id, _card):
                raise FeishuAPIError("fixture PATCH declined", outcome="not_sent")

            monkeypatch.setattr(transport, "update_card_message", reject_patch)
        terminal = _topic_event("message.completed", 1, 2, answer="TERMINAL BODY")
        result = await _post_event(client, terminal)
        assert result["applied"] is patch_succeeds
        assert ("TERMINAL BODY" in str(transport.cards[reopened_message])) is patch_succeeds
        if not patch_succeeds:
            assert result["disposition"] == "native"
            assert (await _post_event(client, terminal))["applied"] is False
        assert transport.attempted_uuids[4:] == [reopen_uuid, reopen_uuid]
        assert all(transport.cards[mid] == card for mid, card in previous.items())


async def test_first_reopen_delta_keeps_partial_answer_across_restart(tmp_path, monkeypatch):
    transport = DeduplicatingClient()
    send = transport.send_card_delivery

    async def lose_ack(*args, **kwargs):
        await send(*args, **kwargs)
        raise FeishuAPIError("fixture lost ACK", outcome="unknown")

    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        assert (await _post_event(client, _long_topic_terminal(1)))["applied"] is True
        with monkeypatch.context() as scope:
            scope.setattr(transport, "send_card_delivery", lose_ack)
            delta = _topic_event("answer.delta", 0, 2, policy_new_turn=True, text="PARTIAL CONTENT")
            assert (await _post_event(client, delta, status=502))["delivery"]["outcome"] == "unknown"
    reopen_uuid = transport.attempted_uuids[-1]

    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        terminal = _topic_event("message.failed", 1, 2, error="EXPECTED FAILURE")
        assert (await _post_event(client, terminal))["applied"] is True
        assert transport.attempted_uuids[4:] == [reopen_uuid, reopen_uuid]
        visible = str(transport.cards[transport.ids[reopen_uuid]])
        assert "PARTIAL CONTENT" in visible and "EXPECTED FAILURE" in visible


async def test_cancelled_reopen_wait_preserves_reservation_before_restart(tmp_path, monkeypatch):
    transport = DeduplicatingClient()
    send = transport.send_card_delivery
    accepted, cancelled = asyncio.Event(), asyncio.Event()

    async def hold_ack(*args, **kwargs):
        await send(*args, **kwargs)
        accepted.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        assert (await _post_event(client, _long_topic_terminal(1)))["applied"]
        with monkeypatch.context() as scope:
            scope.setattr(transport, "send_card_delivery", hold_ack)
            request = asyncio.create_task(_post_event(client, _topic_event("message.started", 0, 2)))
            try:
                await asyncio.wait_for(accepted.wait(), timeout=5)
            finally:
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            await asyncio.wait_for(cancelled.wait(), timeout=5)
    reopen_uuid = transport.attempted_uuids[-1]

    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        terminal = _topic_event("message.completed", 1, 2, answer="RECOVERED AFTER CANCELLATION")
        assert (await _post_event(client, terminal))["applied"] is True
        assert transport.attempted_uuids[4:] == [reopen_uuid, reopen_uuid]
        assert "RECOVERED AFTER CANCELLATION" in str(transport.cards[transport.ids[reopen_uuid]])


async def test_uncertain_cardkit_reopen_does_not_create_another_entity(tmp_path, monkeypatch):
    transport = DeduplicatingClient()
    app = create_app(
        transport, card_config={"streaming_mode": True, "flush_interval_ms": 0},
        session_store_directory=tmp_path,
    )
    send = transport.send_card_delivery

    async def lose_ack(*args, **kwargs):
        assert args[1]["config"]["streaming_mode"] is True
        await send(*args, **kwargs)
        raise FeishuAPIError("fixture lost CardKit IM ACK", outcome="unknown")

    async with TestClient(TestServer(app)) as client:
        assert (await _post_event(client, _topic_event("message.completed", 1, 1, answer="PREDECESSOR")))["applied"]
        with monkeypatch.context() as scope:
            scope.setattr(transport, "send_card_delivery", lose_ack)
            result = await _post_event(client, _topic_event("message.started", 0, 2), status=502)
            assert result["delivery"]["outcome"] == "unknown"
        terminal = _topic_event("message.completed", 1, 2, answer="NATIVE BODY")
        assert (await _post_event(client, terminal)) == {"ok": True, "applied": False, "disposition": "native"}
        assert len(transport.attempted_uuids) == 2
        assert len(transport.cards) == 2


async def test_pre_fingerprint_checkpoint_avoids_duplicate_quote_card_without_hiding_new_terminal(tmp_path):
    transport = DeduplicatingClient()
    quote = {"reply_to_message_id": "om_quote"}
    first = event_payload("message.started", 0, quote, message_id="om_original", created_at=time.time())
    old_terminal = event_payload(
        "message.completed", 1, {**quote, "answer": "SAME TEXT"},
        message_id="om_quote", created_at=time.time(),
    )
    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        assert (await _post_event(client, first))["applied"] is True
        assert (await _post_event(client, old_terminal))["applied"] is True
        store = client.app[server.SESSION_STORE_KEY]
        await wait_for_condition(lambda: store.load()[0]["session"].terminal_delivery_state == "delivered")
        path = next(store.root.glob("*.json"))
        envelope = json.loads(path.read_bytes())
        assert envelope["record"]["aliases"]["om_quote"] == "om_original"
        # a2db3ee1 wrote the same v1 display envelope without these fields.
        for field in ("legacy_terminal_fingerprint", "legacy_route_thread_id"):
            envelope["record"].pop(field, None)
        body = json.dumps(envelope["record"], ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
        envelope["digest"] = hashlib.sha256(body).hexdigest()
        path.write_text(json.dumps(envelope, ensure_ascii=False, allow_nan=False))

    async with TestClient(TestServer(_segmented_app(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        restored = client.app[server.SESSIONS_KEY]["om_original"]
        assert not restored.legacy_terminal_fingerprint
        result = await _post_event(client, old_terminal)
        assert result["applied"] is False
        assert result["disposition"] == "native"
        assert len(transport.cards) == 1
        assert not restored.legacy_terminal_fingerprint

        new_terminal = {**old_terminal, "created_at": time.time()}
        assert (await _post_event(client, new_terminal))["applied"] is True
        assert len(transport.cards) == 2
