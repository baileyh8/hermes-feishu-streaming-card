"""Legacy terminal aliases keep display identity across a sidecar restart."""
import asyncio
import copy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import server
from hermes_feishu_card.feishu_client import FeishuAPIError
from hermes_feishu_card.native_handoff import NativeHandoffStore
from tests.integration.test_server import create_app, event_payload, wait_for_condition


ANSWER = "Complete terminal body from Hermes"
OWNER = "om_original"
ANCHOR = "om_quote"
ACCEPTED = {"ok": True, "applied": True}


class TerminalClient:
    def __init__(self):
        self.cards = {}
        self.ids_by_uuid = {}
        self.sends = []
        self.patches = []
        self.patch_fails = False
        self.lose_recovery_ack = ""
        self.pause_at = ""
        self.paused = asyncio.Event()
        self.paused_task = None

    async def _pause(self, phase):
        if self.pause_at == phase:
            self.paused_task = asyncio.current_task()
            self.paused.set()
            await asyncio.Event().wait()

    async def send_card_delivery(self, chat_id, card, *, delivery_uuid, **kwargs):
        self.sends.append((delivery_uuid, kwargs))
        if self.lose_recovery_ack == "before":
            raise FeishuAPIError("fixture write unconfirmed", outcome="unknown", retryable=False)
        if delivery_uuid not in self.ids_by_uuid:
            message_id = f"om_card_{len(self.cards)}"
            self.ids_by_uuid[delivery_uuid] = message_id
            self.cards[message_id] = copy.deepcopy(card)
        await self._pause("recovery")
        if self.lose_recovery_ack == "after":
            raise FeishuAPIError("fixture ACK lost", outcome="unknown", retryable=False)
        return SimpleNamespace(message_id=self.ids_by_uuid[delivery_uuid], retry_count=0)

    async def update_card_message(self, message_id, card):
        self.patches.append(message_id)
        await self._pause("patch")
        if self.patch_fails:
            raise FeishuAPIError("fixture PATCH refused", retryable=False)
        self.cards[message_id] = copy.deepcopy(card)


def _app(transport, path):
    return create_app(
        transport, card_config={"flush_interval_ms": 0}, session_store_directory=path,
        native_handoff_store=NativeHandoffStore(path / "handoff"),
    )


def _event(name, sequence, **data):
    return event_payload(
        name, sequence, {"reply_to_message_id": ANCHOR, "reply_in_thread": True, **data},
        message_id=OWNER if name == "message.started" else ANCHOR,
        conversation_id="omt_topic", thread_id="omt_topic", created_at=time.time(),
    )


async def _post(http, event):
    response = await http.post("/events", json=event)
    assert response.status == 200, await response.text()
    return await response.json()


def _wait_checkpoint(app, state):
    store = app[server.SESSION_STORE_KEY]
    return wait_for_condition(lambda: any(
        record["key"] == OWNER and record["session"].terminal_delivery_state == state
        for record in store.load()
    ))


def _edit_saved_checkpoint(app, edit):
    path = next(app[server.SESSION_STORE_KEY].root.glob("*.json"))
    envelope = json.loads(path.read_bytes())
    record = envelope["record"]
    edit(record)
    envelope["digest"] = hashlib.sha256(json.dumps(
        record, ensure_ascii=False, allow_nan=False, sort_keys=True,
    ).encode()).hexdigest()
    path.write_text(json.dumps(envelope, ensure_ascii=False))


def _old_checkpoint_format(app, terminal, version):
    def edit(record):
        if version == "c4596f0a":
            record["legacy_terminal_fingerprint"] = server._event_id_fingerprint(
                server.SidecarEvent.from_dict(terminal),
            )
        else:
            record.pop("legacy_terminal_fingerprint", None)
            record.pop("legacy_route_thread_id", None)

    _edit_saved_checkpoint(app, edit)


@pytest.fixture
def no_patch_retries(monkeypatch):
    async def exhausted(*args):
        return False

    monkeypatch.setattr(server, "_retry_terminal_update", exhausted)


@pytest.mark.parametrize("recovery", ["delivered", "unknown"])
async def test_generation_receipt_waits_for_confirmed_body(tmp_path, no_patch_retries, recovery):
    transport = TerminalClient()
    start = _event("message.started", 0)
    terminal = _event("message.completed", 1, answer=ANSWER, native_handoff={"generation": "a" * 32})
    terminal["message_id"] = start["message_id"]
    fingerprint = server._legacy_terminal_fingerprint(server.SidecarEvent.from_dict(terminal))
    async with TestClient(TestServer(_app(transport, tmp_path))) as http:
        assert (await _post(http, start))["applied"]
        transport.patch_fails = True
        transport.lose_recovery_ack = "before" if recovery == "unknown" else ""
        assert await _post(http, terminal) == ACCEPTED
        owner = http.app[server.SESSIONS_KEY][OWNER]
        state = "unknown" if recovery == "unknown" else "recovered"
        await _wait_checkpoint(http.app, state)
        confirmed = recovery == "delivered"
        assert (fingerprint in dict(owner.confirmed_legacy_terminal_receipts)) == confirmed
        assert (http.app[server.EVENT_ID_FENCE_KEY].replay_confirmed_legacy_terminal(fingerprint)
                is not None) == confirmed


@pytest.mark.parametrize("checkpoint", ["current", "c4596f0a", "a2db3ee1"])
@pytest.mark.parametrize("ack_lost", ["before", "after"])
async def test_exact_unknown_terminal_replays_without_duplicate_body(tmp_path, no_patch_retries, checkpoint, ack_lost):
    transport = TerminalClient()
    start = _event("message.started", 0)
    terminal = (
        _event("message.completed", 1, answer=ANSWER) if ack_lost == "before"
        else _event("message.failed", 1, error=ANSWER)
    )
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        assert (await _post(http, start))["applied"] is True
        transport.patch_fails = True
        transport.lose_recovery_ack = ack_lost
        assert await _post(http, terminal) == ACCEPTED
        await _wait_checkpoint(app, "unknown")
        sends, patches = list(transport.sends), list(transport.patches)
        assert len(sends) == 2
        assert await _post(http, terminal) == ACCEPTED
        assert (transport.sends, transport.patches) == (sends, patches)
        if checkpoint != "current":
            _old_checkpoint_format(app, terminal, checkpoint)

    transport.patch_fails = False
    transport.lose_recovery_ack = ""
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await app[server.SESSION_RESTORE_TASK_KEY]
        assert app[server.DIAGNOSTICS_KEY]["card_checkpoints_restored"] == 1
        responses = await asyncio.gather(_post(http, terminal), _post(http, terminal))
        assert responses == [ACCEPTED, ACCEPTED]
        await _wait_checkpoint(app, "recovered")
        assert transport.patches == patches
        assert transport.sends == [*sends, sends[-1]]
        assert len(transport.cards) == 2
        assert sum(ANSWER in str(card) for card in transport.cards.values()) == 1

    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await app[server.SESSION_RESTORE_TASK_KEY]
        assert await _post(http, terminal) == ACCEPTED
        assert (transport.sends, transport.patches) == ([*sends, sends[-1]], patches)


@pytest.mark.parametrize("phase, checkpoint_state", [("patch", "pending"), ("recovery", "recovering")])
async def test_interrupted_terminal_replays_the_pending_write(tmp_path, no_patch_retries, phase, checkpoint_state):
    transport = TerminalClient()
    start = _event("message.started", 0)
    terminal = _event("message.completed", 1, answer=ANSWER)
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        assert (await _post(http, start))["applied"] is True
        transport.patch_fails = phase == "recovery"
        transport.pause_at = phase
        assert await _post(http, terminal) == ACCEPTED
        await asyncio.wait_for(transport.paused.wait(), 2)
        await _wait_checkpoint(app, checkpoint_state)
        sends, patches = list(transport.sends), list(transport.patches)
        assert await _post(http, terminal) == ACCEPTED
        assert (transport.sends, transport.patches) == (sends, patches)
        task = transport.paused_task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    transport.pause_at = ""
    transport.patch_fails = False
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await app[server.SESSION_RESTORE_TASK_KEY]
        assert (transport.sends, transport.patches) == (sends, patches)
        assert await _post(http, terminal) == ACCEPTED
        await _wait_checkpoint(app, "delivered" if phase == "patch" else "recovered")
        if phase == "patch":
            assert (transport.sends, transport.patches) == (sends, [*patches, "om_card_0"])
        else:
            assert (transport.sends, transport.patches) == ([*sends, sends[-1]], patches)
        assert sum(ANSWER in str(card) for card in transport.cards.values()) == 1


async def test_replayed_terminal_does_not_rebind_the_new_owner_quote(tmp_path, no_patch_retries):
    transport = TerminalClient()
    terminal = _event("message.completed", 1, answer=ANSWER)
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await _post(http, _event("message.started", 0))
        transport.patch_fails = True
        transport.lose_recovery_ack = "before"
        assert await _post(http, terminal) == ACCEPTED
        await _wait_checkpoint(app, "unknown")

    transport.patch_fails = False
    transport.lose_recovery_ack = ""
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await app[server.SESSION_RESTORE_TASK_KEY]
        await _post(http, {**_event("message.started", 0), "message_id": "om_second"})
        assert app[server.SESSION_ALIASES_KEY][ANCHOR] == "om_second"
        assert await _post(http, terminal) == ACCEPTED
        await _wait_checkpoint(app, "recovered")
        assert app[server.SESSION_ALIASES_KEY][ANCHOR] == "om_second"
        # Retained owner identity must survive eviction of the bounded HTTP fence.
        app[server.EVENT_ID_FENCE_KEY].entries.clear()
        assert await _post(http, terminal) == ACCEPTED
        assert app[server.SESSIONS_KEY]["om_second"].status == "thinking"
        await _post(http, _event("message.completed", 1, answer="The next answer"))
        await wait_for_condition(lambda: "The next answer" in str(transport.cards["om_card_1"]))
        assert len(transport.cards) == 3
        assert sum(ANSWER in str(card) for card in transport.cards.values()) == 1


async def test_expired_recovery_uuid_reports_unknown_without_a_second_delivery(tmp_path, no_patch_retries):
    transport = TerminalClient()
    terminal = _event("message.completed", 1, answer=ANSWER)
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await _post(http, _event("message.started", 0))
        transport.patch_fails = True
        transport.lose_recovery_ack = "after"
        await _post(http, terminal)
        await _wait_checkpoint(app, "unknown")
        _edit_saved_checkpoint(app, lambda record: record["session"].update(updated_at=time.time() - 3601))

    transport.patch_fails = False
    transport.lose_recovery_ack = ""
    sends, patches = list(transport.sends), list(transport.patches)
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await app[server.SESSION_RESTORE_TASK_KEY]
        result = {**ACCEPTED, "delivery": {"outcome": "unknown"}}
        assert await _post(http, terminal) == result
        assert await _post(http, terminal) == result
        assert (transport.sends, transport.patches) == (sends, patches)
        assert sum(ANSWER in str(card) for card in transport.cards.values()) == 1


async def test_native_handoff_ledger_takes_precedence_over_unconfirmed_card(tmp_path, no_patch_retries):
    transport = TerminalClient()
    terminal = _event("message.completed", 1, answer=ANSWER)
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await _post(http, _event("message.started", 0))
        transport.patch_fails = True
        transport.lose_recovery_ack = "before"
        await _post(http, terminal)
        await _wait_checkpoint(app, "unknown")
        event = server.SidecarEvent.from_dict(terminal)
        ledger = app[server.NATIVE_HANDOFF_STORE_KEY]
        _, created = ledger.begin_no_card(server._native_handoff_identity(event), event_created_at=event.created_at)
        assert created

    transport.patch_fails = False
    transport.lose_recovery_ack = ""
    sends, patches = list(transport.sends), list(transport.patches)
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await app[server.SESSION_RESTORE_TASK_KEY]
        assert await _post(http, terminal) == ACCEPTED
        assert app[server.DIAGNOSTICS_KEY]["last_terminal_event"]["disposition"] == "native_deduplicated"
        assert (transport.sends, transport.patches) == (sends, patches)


async def test_confirmed_patch_is_checkpointed_before_receipt_cleanup(tmp_path, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pause_receipts(*args):
        entered.set()
        await release.wait()

    monkeypatch.setattr(server, "_settle_approval_displays", pause_receipts)
    transport = TerminalClient()
    terminal = _event("message.completed", 1, answer=ANSWER)
    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await _post(http, _event("message.started", 0))
        assert await _post(http, terminal) == ACCEPTED
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await _wait_checkpoint(app, "delivered")
            assert await _post(http, terminal) == ACCEPTED
        finally:
            release.set()

    app = _app(transport, tmp_path)
    async with TestClient(TestServer(app)) as http:
        await app[server.SESSION_RESTORE_TASK_KEY]
        sends, patches = list(transport.sends), list(transport.patches)
        assert await _post(http, terminal) == ACCEPTED
        assert (transport.sends, transport.patches) == (sends, patches)
        assert len(transport.cards) == 1
        assert ANSWER in str(transport.cards["om_card_0"])
