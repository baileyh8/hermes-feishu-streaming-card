"""Legacy terminal replays must retain exact delivery evidence across replacement."""
import copy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import server
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.feishu_client import FeishuAPIError
from hermes_feishu_card.native_handoff import NativeHandoffStore
from hermes_feishu_card.session import (
    LEGACY_TERMINAL_RECEIPT_TTL_SECONDS,
    MAX_CONFIRMED_LEGACY_TERMINAL_RECEIPTS,
)
from tests.integration.test_reopened_delivery_identity import DeduplicatingClient, _post_event
from tests.integration.test_server import create_app, event_payload, wait_for_condition
from tests.integration.test_server_segmented import oversize_answer


DELIVERED = {"ok": True, "applied": True, "delivery": {"outcome": "delivered"}}
NATIVE = {"ok": True, "applied": False, "disposition": "native"}


class RecordingTransport(DeduplicatingClient):
    def __init__(self):
        super().__init__()
        self.notices = []

    async def send_text_message(self, chat_id, text, **placement):
        self.notices.append((chat_id, text, placement))
        return "om_fixture_notice"

    def io_counts(self):
        return len(self.attempted_uuids), len(self.updated), len(self.notices)


def _edit_checkpoint(store, change):
    path, = store.root.glob("*.json")
    envelope = json.loads(path.read_bytes())
    record = envelope["record"]
    change(record)
    encoded = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
    envelope["digest"] = hashlib.sha256(encoded).hexdigest()
    path.write_text(json.dumps(envelope, ensure_ascii=False, allow_nan=False))


def _remove_checkpoint_fields(store, *fields):
    def remove(record):
        for key in fields:
            record.pop(key, None)

    _edit_checkpoint(store, remove)


def _event(name, sequence, created_at, *, quote=True, **data):
    return event_payload(
        name,
        sequence,
        {**({"reply_to_message_id": "om_quote"} if quote else {}), **data},
        message_id="om_legacy",
        created_at=created_at,
    )


def _terminal(created_at, answer, **data):
    return _event("message.completed", 1, created_at, policy_new_turn=True, answer=answer, **data)


def _fingerprint(payload):
    return server._legacy_terminal_fingerprint(SidecarEvent.from_dict(payload))


def _with_store(transport, directory, **card_options):
    return create_app(
        transport, card_config={"flush_interval_ms": 0, **card_options}, session_store_directory=directory,
        native_handoff_store=NativeHandoffStore(directory / "handoff"),
    )


@pytest.mark.parametrize("segmented", [False, True], ids=["patch", "segmented"])
async def test_confirmed_update_survives_replacement_and_restart(tmp_path, segmented):
    transport = RecordingTransport()
    start = time.time()
    old = _event("message.completed", 1, start, answer=oversize_answer() if segmented else "First answer",
                 native_handoff={"generation": "a" * 32})
    new = _terminal(start + 1, "Second answer", native_handoff={"generation": "b" * 32})

    def app():
        return _with_store(transport, tmp_path, over_limit="segmented")

    async with TestClient(TestServer(app())) as client:
        assert (await _post_event(client, _event("message.started", 0, start)))["applied"]
        assert (await _post_event(client, old))["applied"]
        owner = client.app[server.SESSIONS_KEY][old["message_id"]]
        await wait_for_condition(lambda: owner.terminal_card_confirmed)
        assert await _post_event(client, new) == DELIVERED
        writes = transport.io_counts()
        assert await _post_event(client, old) == DELIVERED
        assert transport.io_counts() == writes

    async with TestClient(TestServer(app())) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        writes = transport.io_counts()
        assert await _post_event(client, old) == DELIVERED
        assert await _post_event(client, new) == DELIVERED
        assert transport.io_counts() == writes


async def test_partly_delivered_segmented_update_does_not_confirm_full_body(tmp_path, monkeypatch):
    transport = RecordingTransport()
    start = time.time()
    old = _event("message.completed", 1, start, answer=oversize_answer(),
                 native_handoff={"generation": "a" * 32})

    async def refuse_continuation(*args, **kwargs):
        raise FeishuAPIError("continuation refused", outcome="not_sent")

    async with TestClient(TestServer(_with_store(transport, tmp_path, over_limit="segmented"))) as client:
        assert (await _post_event(client, _event("message.started", 0, start)))["applied"]
        monkeypatch.setattr(transport, "send_card_delivery", refuse_continuation)
        assert await _post_event(client, old) == NATIVE
        owner = client.app[server.SESSIONS_KEY][old["message_id"]]
        assert _fingerprint(old) not in dict(owner.confirmed_legacy_terminal_receipts)
        assert client.app[server.EVENT_ID_FENCE_KEY].replay_confirmed_legacy_terminal(_fingerprint(old)) is None


@pytest.mark.parametrize(("quote", "handoff"), [(True, False), (False, False), (True, True)])
async def test_restarted_earlier_marked_terminal_uses_exact_delivery_receipt(tmp_path, quote, handoff):
    transport = RecordingTransport()
    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        assert (await _post_event(client, _event("message.started", 0, time.time())))["applied"]
        assert (await _post_event(
            client, _event("message.completed", 1, time.time(), answer="Legacy predecessor"),
        ))["applied"]
    _remove_checkpoint_fields(
        client.app[server.SESSION_STORE_KEY],
        "legacy_terminal_fingerprint", "legacy_route_thread_id", "confirmed_legacy_terminal_receipts",
    )

    started_at = time.time() + 1
    earlier = _terminal(started_at, "Earlier body", quote=quote)
    latest = _terminal(started_at + 1, "Latest body", quote=quote)
    if handoff:
        earlier["data"]["native_handoff"] = {"generation": "a" * 32}
        latest["data"]["native_handoff"] = {"generation": "b" * 32}

    def app():
        return _with_store(transport, tmp_path, completion_notify={"enabled": True, "placement": "message"})

    async with TestClient(TestServer(app())) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert await _post_event(client, earlier) == DELIVERED
        await wait_for_condition(lambda: len(transport.notices) == 1)
        assert await _post_event(client, latest) == DELIVERED
        await wait_for_condition(lambda: len(transport.notices) == 2)
        assert "Earlier body" in json.dumps(transport.cards["card-1"])
        assert "Latest body" in json.dumps(transport.cards["card-2"])
        assert await _post_event(client, earlier) == DELIVERED

    async with TestClient(TestServer(app())) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        cards = copy.deepcopy(transport.cards)
        io_counts = transport.io_counts()
        assert await _post_event(client, earlier) == DELIVERED
        assert await _post_event(client, latest) == DELIVERED
        assert transport.io_counts() == io_counts
        assert transport.cards == cards


@pytest.mark.parametrize("turn_id", ["", "fixture-explicit-turn"], ids=["legacy-start", "explicit-turn-id"])
async def test_history_survives_multiple_rollovers_and_an_explicit_new_turn(tmp_path, turn_id):
    transport = RecordingTransport()
    start = time.time() + 1
    earlier = [
        _terminal(start, "First confirmed body"),
        _event("message.failed", 1, start + 1, policy_new_turn=True, error="Visible error body"),
        _terminal(start + 2, "Third confirmed body"),
    ]
    explicit = _event("message.started", 0, start + 3)
    if turn_id:
        explicit["turn_id"] = turn_id
    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        for payload in earlier:
            assert await _post_event(client, payload) == DELIVERED
        assert "Visible error body" in str(transport.cards["card-1"])
        assert await _post_event(client, explicit) == DELIVERED

    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        owner = client.app[server.SESSIONS_KEY][turn_id or explicit["message_id"]]
        cards = copy.deepcopy(transport.cards)
        io_counts = transport.io_counts()
        for payload in reversed(earlier):
            assert await _post_event(client, payload) == DELIVERED
        assert owner.status == "thinking"
        assert transport.io_counts() == io_counts
        assert transport.cards == cards


@pytest.mark.parametrize("scope", ["chat", "thread", "profile", "requester"])
async def test_historical_receipt_requires_the_exact_event_scope(tmp_path, scope):
    transport = RecordingTransport()
    start = time.time() + 1
    sender = {"chat_type": "group", "sender_open_id": "ou_fixture_first"}
    earlier = _terminal(start, "Earlier scoped body", **sender)
    latest = _terminal(start + 1, "Latest scoped body", **sender)
    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        for payload in (earlier, latest):
            assert await _post_event(client, payload) == DELIVERED

    changed = copy.deepcopy(earlier)
    if scope == "chat":
        changed["chat_id"] = "oc_fixture_other"
    elif scope == "thread":
        changed["thread_id"] = "omt_fixture_other"
    elif scope == "profile":
        changed["data"]["profile_id"] = "work"
    else:
        changed["data"]["sender_open_id"] = "ou_fixture_other"

    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        cards = copy.deepcopy(transport.cards)
        io_counts = transport.io_counts()
        assert await _post_event(client, earlier) == DELIVERED
        assert transport.io_counts() == io_counts
        # The same message ID and body from another route is a new delivery.
        assert await _post_event(client, changed) == DELIVERED
        assert len(transport.attempted_uuids) == io_counts[0] + 1
        assert all(transport.cards[key] == card for key, card in cards.items())


async def test_receipt_restore_requires_the_same_feishu_application(tmp_path):
    transport = RecordingTransport()
    transport.config = SimpleNamespace(app_id="cli_fixture_first")
    start = time.time() + 1
    earlier = _terminal(start, "Earlier application body")
    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        for payload in (earlier, _terminal(start + 1, "Latest application body")):
            assert await _post_event(client, payload) == DELIVERED

    different_transport = RecordingTransport()
    different_transport.config = SimpleNamespace(app_id="cli_fixture_other")
    async with TestClient(TestServer(_with_store(different_transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert await _post_event(client, earlier) == DELIVERED
        assert different_transport.io_counts() == (1, 0, 0)


@pytest.mark.parametrize("damage", ["missing-client-identity", "keyed-load-error"])
async def test_unavailable_checkpoint_history_keeps_the_confirmed_create_response(tmp_path, monkeypatch, damage):
    transport = RecordingTransport()
    start = time.time() + 1
    earlier = _terminal(start, "Earlier body")
    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        assert await _post_event(client, earlier) == DELIVERED
    store = client.app[server.SESSION_STORE_KEY]
    _remove_checkpoint_fields(store, "client_identity")

    replacement = RecordingTransport()
    async with TestClient(TestServer(_with_store(replacement, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert not client.app[server.SESSIONS_KEY]
        if damage == "keyed-load-error":
            store = client.app[server.SESSION_STORE_KEY]
            original_load = store.load

            def load(*, key=None):
                if key is not None:
                    raise OSError("fixture keyed read unavailable")
                return original_load()

            monkeypatch.setattr(store, "load", load)
        new = _terminal(start + 1, "New confirmed body")
        assert await _post_event(client, new) == DELIVERED
        assert replacement.io_counts() == (1, 0, 0)
        assert "New confirmed body" in str(replacement.cards["card-0"])
        record, = client.app[server.SESSION_STORE_KEY].load()
        assert [item[0] for item in record["session"].confirmed_legacy_terminal_receipts] == [_fingerprint(new)]


async def test_earlier_receipt_expiry_stays_bound_to_the_first_confirmation(tmp_path, monkeypatch):
    transport = RecordingTransport()
    now = time.time()
    monkeypatch.setattr(server.time, "time", lambda: now)
    earlier = _terminal(now + 1, "Expiring body")
    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        assert await _post_event(client, earlier) == DELIVERED
        now += LEGACY_TERMINAL_RECEIPT_TTL_SECONDS - 10
        latest = _terminal(now + 1, "Still current body")
        assert await _post_event(client, latest) == DELIVERED

    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert await _post_event(client, earlier) == DELIVERED
        fence = client.app[server.EVENT_ID_FENCE_KEY]
        receipt = fence.entries[("legacy_terminal", _fingerprint(earlier))]
        assert 0 < receipt.expires_at - fence.now() <= 10

    now += 11
    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        io_counts = transport.io_counts()
        assert await _post_event(client, earlier) == NATIVE
        assert await _post_event(client, latest) == DELIVERED
        assert transport.io_counts() == io_counts


async def test_checkpoint_retains_only_the_most_recent_confirmed_receipts(tmp_path):
    transport = RecordingTransport()
    start = time.time() + 1
    payloads = [
        _terminal(start + index, f"Visible body {index}")
        for index in range(MAX_CONFIRMED_LEGACY_TERMINAL_RECEIPTS + 2)
    ]
    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        for payload in payloads:
            assert await _post_event(client, payload) == DELIVERED
        record, = client.app[server.SESSION_STORE_KEY].load()
        history = record["session"].confirmed_legacy_terminal_receipts
        assert [fingerprint for fingerprint, _ in history] == [_fingerprint(item) for item in payloads[2:]]

    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        io_counts = transport.io_counts()
        assert await _post_event(client, payloads[0]) == NATIVE
        assert await _post_event(client, payloads[2]) == DELIVERED
        assert await _post_event(client, payloads[-1]) == DELIVERED
        assert transport.io_counts() == io_counts


@pytest.mark.parametrize("old_delivery", ["delivered", "unknown"])
async def test_old_checkpoint_requires_exact_confirmed_delivery_to_seed_history(tmp_path, old_delivery):
    transport = RecordingTransport()
    start = time.time() + 1
    earlier = _terminal(start + 1, "Earlier marked body")
    latest = _terminal(start + 2, "Current marked body")
    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        assert await _post_event(client, _terminal(start, "Predecessor")) == DELIVERED
        assert await _post_event(client, earlier) == DELIVERED

    def as_old_record(record):
        record.pop("confirmed_legacy_terminal_receipts")
        assert record["legacy_terminal_fingerprint"] == _fingerprint(earlier)
        assert record["legacy_terminal_start_at"] == earlier["created_at"]
        record["session"]["terminal_delivery_state"] = old_delivery

    _edit_checkpoint(client.app[server.SESSION_STORE_KEY], as_old_record)
    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        assert await _post_event(client, latest) == DELIVERED

    async with TestClient(TestServer(_with_store(transport, tmp_path))) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        io_counts = transport.io_counts()
        assert await _post_event(client, earlier) == (DELIVERED if old_delivery == "delivered" else NATIVE)
        assert transport.io_counts() == io_counts


@pytest.mark.parametrize("outcome", ["segmented", "partial", "native", "unknown", "not_sent"])
async def test_only_full_terminal_delivery_records_an_exact_receipt(tmp_path, monkeypatch, outcome):
    transport = RecordingTransport()
    start = time.time() + 1
    earlier = _terminal(start, "First confirmed body")
    middle = _terminal(start + 1, oversize_answer() if outcome in {"segmented", "partial", "native"} else "Middle body")
    latest = _terminal(start + 2, "Latest confirmed body")
    send = transport.send_card_delivery

    async def interrupt(*args, **kwargs):
        if outcome == "partial" and len(transport.cards) == 2:
            raise FeishuAPIError("fixture continuation refused", outcome="not_sent")
        if outcome in {"unknown", "not_sent"}:
            if outcome == "unknown":
                await send(*args, **kwargs)
            raise FeishuAPIError("fixture create unconfirmed", outcome=outcome)
        return await send(*args, **kwargs)

    def app():
        return _with_store(transport, tmp_path, over_limit="native" if outcome == "native" else "segmented")

    async with TestClient(TestServer(app())) as client:
        assert await _post_event(client, earlier) == DELIVERED
        with monkeypatch.context() as patch:
            patch.setattr(transport, "send_card_delivery", interrupt)
            assert await _post_event(client, middle) == (DELIVERED if outcome == "segmented" else NATIVE)
        record, = client.app[server.SESSION_STORE_KEY].load()
        history = record["session"].confirmed_legacy_terminal_receipts
        expected = (earlier, middle) if outcome == "segmented" else (earlier,)
        assert tuple(fingerprint for fingerprint, _ in history) == tuple(map(_fingerprint, expected))
        if outcome == "partial":
            assert record["legacy_terminal_receipts_only"] is True
            assert not record["session"].answer_text

    async with TestClient(TestServer(app())) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        if outcome == "partial":
            assert earlier["message_id"] not in client.app[server.SESSIONS_KEY]
        io_counts = transport.io_counts()
        assert await _post_event(client, earlier) == DELIVERED
        assert transport.io_counts() == io_counts
        middle_replay = await _post_event(client, middle)
        assert (middle_replay == DELIVERED) is (outcome == "segmented")
        assert await _post_event(client, latest) == DELIVERED

    async with TestClient(TestServer(app())) as client:
        await client.app[server.SESSION_RESTORE_TASK_KEY]
        io_counts = transport.io_counts()
        assert await _post_event(client, earlier) == DELIVERED
        assert await _post_event(client, latest) == DELIVERED
        assert transport.io_counts() == io_counts
