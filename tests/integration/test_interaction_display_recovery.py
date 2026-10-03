"""A restart closes the actual auxiliary card without restoring its authority."""
import asyncio
import copy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import server
from hermes_feishu_card.bots import RouteResult


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(tmp_path / "state"))


class Client:
    def __init__(self):
        self.sent, self.updated = [], []
        self.config = SimpleNamespace(app_id="fixture_app", base_url="https://fixture")
        self.fail_id = ""

    async def send_card(self, chat_id, card, **kwargs):
        mid = f"om_fixture_{len(self.sent) + 1}"
        self.sent.append((mid, copy.deepcopy(card)))
        return mid

    async def update_card_message(self, mid, card):
        if mid == self.fail_id:
            raise OSError("private fixture failure")
        original = next(card for identifier, card in self.sent if identifier == mid)
        assert original.get("schema") == card.get("schema"), "cross-dialect PATCH"
        self.updated.append((mid, copy.deepcopy(card)))


def event(kind, seq, data=None):
    return dict(schema_version="1", event=kind, sequence=seq, platform="feishu",
                conversation_id="topic_fixture", message_id="source_fixture",
                turn_id="turn_fixture", chat_id="chat_fixture", created_at=time.time(), data=data or {})


def make_app(client, root, mode="callback", **kwargs):
    return server.create_app(client, session_store_directory=root,
        card_config={"flush_interval_ms": 0, "reading_preset": "task", "interaction_mode": mode}, **kwargs)


async def pending(http, *, kind="approval", pause=False, profile=""):
    data = {"profile_id": profile} if profile else {}
    assert (await http.post("/events", json=event("message.started", 0, data))).status == 200
    response = await http.post("/events", json=event("interaction.requested", 1, dict(data,
        interaction_id="fixture_approval", kind=kind, prompt="Choose exact fixture",
        description="KEEP_SCOPE " + "x" * 4500 + " END_SCOPE --api-key SECRET_FIXTURE_VALUE",
        timeout_seconds=300, pause_on_timeout=pause,
        options=[{"label": "允许一次", "value": "once"}, {"label": "拒绝", "value": "deny"}])))
    assert response.status == 200 and (await response.json())["applied"]
    session = next(iter(http.app[server.SESSIONS_KEY].values()))
    interaction = session.active_interaction
    if pause:
        await server._expire_pending_interactions(http.app, now=interaction.expires_at + 1)
        await asyncio.sleep(.01)
    return interaction.feishu_message_id, interaction.callback_token


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["callback", "text"])
@pytest.mark.parametrize("pause", [False, True])
async def test_restart_retires_actual_auxiliary_preserving_scope_and_dialect(tmp_path, mode, pause):
    fake = Client()
    async with TestClient(TestServer(make_app(fake, tmp_path, mode))) as http:
        auxiliary, token = await pending(http, pause=pause)
        assert auxiliary != http.app[server.FEISHU_MESSAGE_IDS_KEY]["turn_fixture"]
    raw = next((tmp_path / "card-checkpoints-v1").glob("*.json")).read_text()
    assert token not in raw and "SECRET_FIXTURE_VALUE" not in raw
    assert "callback_token" not in raw and "runtime_admission" not in raw and "last_waiter_poll_at" not in raw
    fake.updated.clear()
    async with TestClient(TestServer(make_app(fake, tmp_path, mode))) as http:
        await http.app[server.SESSION_RESTORE_TASK_KEY]
        receipt = next(card for mid, card in fake.updated if mid == auxiliary)
        assert "KEEP_SCOPE" in str(receipt) and "x" * 4500 in str(receipt) and "END_SCOPE" in str(receipt)
        assert "失效" in str(receipt) and "查看并继续审批" not in str(receipt)
        assert receipt["header"]["template"] == "red"
        assert "hfc_action" not in str(receipt) and '"tag": "button"' not in json.dumps(receipt)
        session = http.app[server.SESSIONS_KEY]["turn_fixture"]
        assert session.active_interaction is None and session.status == "failed"
        assert not http.app[server.INTERACTION_RESULTS_KEY]
        assert (await http.get("/interactions/fixture_approval")).status == 404
        old_action = {"event": {"context": {"open_chat_id": "chat_fixture"},
            "operator": {"open_id": "ou_fixture"}, "action": {"value": {
                "hfc_action": "interaction.select", "interaction_id": "fixture_approval",
                "token": token, "choice": "once"}}}}
        assert (await http.post("/card/actions", json=old_action)).status == 404
        assert len(fake.sent) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["callback", "text"])
@pytest.mark.parametrize("kind", ["approval", "clarify"])
async def test_regular_expiry_retires_actual_auxiliary_once(tmp_path, mode, kind):
    fake = Client()
    async with TestClient(TestServer(make_app(fake, tmp_path, mode))) as http:
        auxiliary, _ = await pending(http, kind=kind)
        session = http.app[server.SESSIONS_KEY]["turn_fixture"]
        now = session.active_interaction.expires_at + 1
        fake.updated.clear()
        assert await server._expire_pending_interactions(http.app, now=now) == 1
        receipt = next(card for mid, card in fake.updated if mid == auxiliary)
        assert "交互已过期" in str(receipt) and "END_SCOPE" in str(receipt)
        assert "hfc_action" not in str(receipt)
        owner_id = http.app[server.FEISHU_MESSAGE_IDS_KEY]["turn_fixture"]
        owner = next(card for mid, card in reversed(fake.updated) if mid == owner_id)
        assert "KEEP_SCOPE" not in str(owner), "the confirmed auxiliary retains the complete scope"
        count = len(fake.updated)
        assert await server._expire_pending_interactions(http.app, now=now + 1) == 0
        assert len(fake.updated) == count and len(fake.sent) == 2


@pytest.mark.asyncio
async def test_old_checkpoint_without_auxiliary_does_not_guess_message_id(tmp_path):
    fake = Client()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        auxiliary, _ = await pending(http)
    path = next((tmp_path / "card-checkpoints-v1").glob("*.json"))
    envelope = json.loads(path.read_text())
    envelope["record"].pop("auxiliary_receipt", None)
    envelope["digest"] = hashlib.sha256(json.dumps(envelope["record"], ensure_ascii=False,
        allow_nan=False, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(envelope))
    fake.updated.clear()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        await http.app[server.SESSION_RESTORE_TASK_KEY]
        assert http.app[server.SESSIONS_KEY]["turn_fixture"].active_interaction is None
        assert all(mid != auxiliary for mid, _ in fake.updated)


@pytest.mark.asyncio
async def test_auxiliary_restore_failure_keeps_owner_and_never_sends_duplicate(tmp_path):
    fake = Client()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        auxiliary, _ = await pending(http)
    fake.fail_id = auxiliary
    fake.updated.clear()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        await http.app[server.SESSION_RESTORE_TASK_KEY]
        assert any(mid != auxiliary for mid, _ in fake.updated)
        assert http.app[server.DIAGNOSTICS_KEY]["card_restore_update"] == "failed"
        assert len(fake.sent) == 2


@pytest.mark.asyncio
async def test_changed_client_identity_blocks_auxiliary_restore(tmp_path):
    fake = Client()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        await pending(http)
    fake.config.app_id = "different_app"
    fake.updated.clear()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        await http.app[server.SESSION_RESTORE_TASK_KEY]
        assert not http.app[server.SESSIONS_KEY] and not fake.updated


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_recovery", ["recovered", "unknown"])
async def test_auxiliary_is_recovered_even_when_owner_must_not_be_refilled(tmp_path, terminal_recovery):
    fake = Client()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        auxiliary, _ = await pending(http)
        session = http.app[server.SESSIONS_KEY]["turn_fixture"]
        session.terminal_delivery_state = terminal_recovery
        server._checkpoint_session(http.app, "turn_fixture", "")
    fake.updated.clear()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        await http.app[server.SESSION_RESTORE_TASK_KEY]
        assert [mid for mid, _ in fake.updated] == [auxiliary]


class Factory:
    def __init__(self, client):
        self.client = client

    def get_client(self, bot_id):
        return self.client


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["profile", "bot"])
async def test_auxiliary_restore_rejects_changed_profile_or_bot(tmp_path, change):
    fake = Client()
    clients = {"work": Factory(fake)}
    async with TestClient(TestServer(make_app(clients, tmp_path,
            bot_router=lambda event: RouteResult("default", "fixture")))) as http:
        await pending(http, profile="work")
    fake.updated.clear()
    if change == "profile":
        clients = {"other": Factory(fake)}
    bot = "other" if change == "bot" else "default"
    async with TestClient(TestServer(make_app(clients, tmp_path,
            bot_router=lambda event: RouteResult(bot, "fixture")))) as http:
        await http.app[server.SESSION_RESTORE_TASK_KEY]
        assert not http.app[server.SESSIONS_KEY] and not fake.updated


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "update", "capacity"])
async def test_failed_interaction_compacts_owner_only_after_full_receipt_ack(tmp_path, monkeypatch, failure):
    fake = Client()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        auxiliary, _ = await pending(http)
        if failure == "update":
            fake.fail_id = auxiliary
        elif failure == "capacity":
            original = server._render_interaction_callback_card_for_app
            def oversized(*args, **kwargs):
                card = original(*args, **kwargs)
                card["elements"].append({"tag": "markdown", "content": "x" * 28000})
                return card
            monkeypatch.setattr(server, "_render_interaction_callback_card_for_app", oversized)
        fake.updated.clear()
        response = await http.post("/events", json=event("interaction.failed", 2, {
            "interaction_id": "fixture_approval", "error": "Task stopped, approval invalid"}))
        assert response.status == 200
        await asyncio.sleep(.03)
        owner_id = http.app[server.FEISHU_MESSAGE_IDS_KEY]["turn_fixture"]
        owner = next(card for mid, card in reversed(fake.updated) if mid == owner_id)
        assert ("KEEP_SCOPE" in str(owner)) is (failure != "none")
        session = http.app[server.SESSIONS_KEY]["turn_fixture"]
        assert bool(session.active_interaction.receipt_fingerprint) is (failure == "none")
        assert not session.active_interaction.choice
        if failure == "none":
            receipt = next(card for mid, card in fake.updated if mid == auxiliary)
            assert "KEEP_SCOPE" in str(receipt) and "END_SCOPE" in str(receipt)
            assert "Task stopped, approval invalid" in str(receipt)
        assert len(fake.sent) == 2


@pytest.mark.asyncio
async def test_new_state_during_restore_cannot_refresh_stale_auxiliary(tmp_path):
    fake = Client()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        auxiliary, _ = await pending(http)
    original = fake.update_card_message
    app = make_app(fake, tmp_path)
    async def change_state(mid, card):
        await original(mid, card)
        app[server.SESSIONS_KEY]["turn_fixture"].updated_at += 1
    fake.update_card_message = change_state
    fake.updated.clear()
    async with TestClient(TestServer(app)) as http:
        await http.app[server.SESSION_RESTORE_TASK_KEY]
        assert all(mid != auxiliary for mid, _ in fake.updated)


@pytest.mark.asyncio
async def test_checkpoint_capacity_failure_keeps_actual_delivery_and_diagnostics(tmp_path, monkeypatch):
    fake = Client()
    app = make_app(fake, tmp_path)
    original = server._checkpoint_auxiliary_receipt
    def fail(*args, **kwargs):
        value = original(*args, **kwargs)
        if value is not None:
            raise ValueError("fixture capacity")
        return value
    monkeypatch.setattr(server, "_checkpoint_auxiliary_receipt", fail)
    async with TestClient(TestServer(app)) as http:
        auxiliary, token = await pending(http)
        assert auxiliary and token and len(fake.sent) == 2
        assert app[server.DIAGNOSTICS_KEY]["card_checkpoint_state"] == "unavailable"
        assert app[server.SESSIONS_KEY]["turn_fixture"].active_interaction.status == "pending"


@pytest.mark.asyncio
async def test_completed_auxiliary_restores_decision_without_reopening_authority(tmp_path):
    fake = Client()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        auxiliary, _ = await pending(http)
        assert (await http.post("/events", json=event("interaction.completed", 2, {
            "interaction_id": "fixture_approval", "choice": "once", "choice_label": "允许一次"}))).status == 200
    fake.updated.clear()
    async with TestClient(TestServer(make_app(fake, tmp_path))) as http:
        await http.app[server.SESSION_RESTORE_TASK_KEY]
        receipt = next(card for mid, card in fake.updated if mid == auxiliary)
        assert receipt["header"]["template"] == "green"
        assert "已选择" in str(receipt) and "END_SCOPE" in str(receipt)
        assert not http.app[server.INTERACTION_RESULTS_KEY]
        assert http.app[server.SESSIONS_KEY]["turn_fixture"].active_interaction is None


def test_failed_receipt_proof_changes_with_expiry_reason():
    from hermes_feishu_card.approval_receipts import approval_receipt_fingerprint, has_confirmed_approval_receipt
    from hermes_feishu_card.session import CardSession, InteractionState
    session = CardSession("conversation", "source", "chat")
    interaction = InteractionState("fixture", "approval", "Scope", status="failed",
                                   feishu_message_id="om_auxiliary", error="expired")
    session.active_interaction = interaction
    interaction.receipt_fingerprint = approval_receipt_fingerprint(session, interaction)
    assert has_confirmed_approval_receipt(session)
    interaction.error = "different failure"
    assert not has_confirmed_approval_receipt(session)


@pytest.mark.asyncio
async def test_failed_receipt_ack_never_refills_owner_after_terminal_recovery(tmp_path, monkeypatch):
    fake = Client()
    app = make_app(fake, tmp_path)
    async with TestClient(TestServer(app)) as http:
        auxiliary, _ = await pending(http)
        owner = app[server.FEISHU_MESSAGE_IDS_KEY]["turn_fixture"]
        original = fake.update_card_message
        owner_writable = False
        async def changing_connection(mid, card):
            nonlocal owner_writable
            if mid == owner and not owner_writable:
                raise OSError("fixture owner unavailable")
            await original(mid, card)
            if mid == auxiliary:
                owner_writable = True
        fake.update_card_message = changing_connection
        async def no_delay(*args, **kwargs):
            return False
        monkeypatch.setattr(server, "_retry_terminal_update", no_delay)
        response = await http.post("/events", json=event("message.failed", 2, {"error": "RECOVERED_FINAL"}))
        assert response.status == 200
        for _ in range(100):
            await asyncio.sleep(.01)
            if owner_writable:
                break
        assert owner_writable
        assert app[server.SESSIONS_KEY]["turn_fixture"].terminal_delivery_state == "recovered"
        assert len(fake.sent) == 3 and "RECOVERED_FINAL" in str(fake.sent[-1][1])
        assert all("RECOVERED_FINAL" not in str(card) for mid, card in fake.updated if mid == owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["message.failed", "message.completed"])
async def test_unrenderable_text_receipt_keeps_full_terminal_without_fallback(tmp_path, monkeypatch, terminal):
    fake = Client()
    app = make_app(fake, tmp_path, "text")
    async with TestClient(TestServer(app)) as http:
        auxiliary, _ = await pending(http)
        owner = app[server.FEISHU_MESSAGE_IDS_KEY]["turn_fixture"]
        original = server._render_static_display_card

        def unavailable_receipt(*args, **kwargs):
            if kwargs.get("note") == "交互结果已记录，后续进展以回复卡为准":
                return None
            return original(*args, **kwargs)

        monkeypatch.setattr(server, "_render_static_display_card", unavailable_receipt)
        fake.updated.clear()
        data = {"error" if terminal == "message.failed" else "answer": "FULL_TERMINAL_RETAINED"}
        response = await http.post("/events", json=event(terminal, 2, data))
        assert response.status == 200
        for _ in range(100):
            await asyncio.sleep(.01)
            if app[server.DIAGNOSTICS_KEY].get("last_interaction_receipt_update") == "failed":
                break

        session = app[server.SESSIONS_KEY]["turn_fixture"]
        assert app[server.DIAGNOSTICS_KEY]["last_interaction_receipt_update"] == "failed"
        assert session.terminal_delivery_state == "delivered"
        assert not session.active_interaction.receipt_fingerprint
        assert all(mid != auxiliary for mid, _ in fake.updated)
        final = next(card for mid, card in reversed(fake.updated) if mid == owner)
        assert "FULL_TERMINAL_RETAINED" in str(final)
        assert "KEEP_SCOPE" in str(final) and "x" * 4500 in str(final) and "END_SCOPE" in str(final)
        assert len(fake.sent) == 2
