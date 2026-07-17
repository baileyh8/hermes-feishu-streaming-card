import asyncio
import base64
import json
import logging
from pathlib import Path
import secrets
import threading
import time
from types import SimpleNamespace
import warnings

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card.bots import RouteResult
from hermes_feishu_card import flush as flush_module
from hermes_feishu_card import server as sidecar_server
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.event_auth import sign_event_request
from hermes_feishu_card.diagnostics import DiagnosticFinding, DiagnosticReport
from hermes_feishu_card.flush import FlushController
from hermes_feishu_card.lifecycle import (
    cleanup_closed_controller,
    cleanup_orphan_message_lock,
    cleanup_runtime_state,
)
from hermes_feishu_card.operations import OperationStore, sign_transport_proof
from hermes_feishu_card.operations_transport import (
    derive_operation_transport_secret,
    sign_command_transport_proof,
)
from hermes_feishu_card.session import CardSession, InteractionState
from hermes_feishu_card.server import (
    CARD_SUMMARIES_KEY,
    CARD_SUMMARY_SESSION_KEYS_KEY,
    CLEANUP_TASK_KEY,
    DIAGNOSTICS_KEY,
    FEISHU_MESSAGE_IDS_KEY,
    FLUSH_CONTROLLERS_KEY,
    INTERACTION_RESULTS_KEY,
    INTERACTION_RESULT_SESSION_KEYS_KEY,
    MESSAGE_BOT_IDS_KEY,
    MESSAGE_LOCKS_KEY,
    MESSAGE_LOCK_USERS_KEY,
    METRICS_KEY,
    RUNTIME_CLEANUP_INTERVAL_SECONDS,
    SESSION_ALIASES_KEY,
    SESSION_CARD_CONFIGS_KEY,
    SESSIONS_KEY,
    create_app as _create_app,
)


_REAL_ASYNCIO_SLEEP = asyncio.sleep
TRANSPORT_SECRET = "test-adapter-process-local-secret"
TRANSPORT_ROOT_SECRET = b"r" * 32


def create_app(*args, **kwargs):
    kwargs.setdefault(
        "operations_transport_root_secret",
        TRANSPORT_ROOT_SECRET,
    )
    return _create_app(*args, **kwargs)


def signed_operations_command(payload):
    signed = dict(payload)
    signed.pop("adapter_transport_secret", None)
    signed["adapter_command_proof"] = sign_command_transport_proof(
        TRANSPORT_ROOT_SECRET,
        signed,
        timestamp=int(sidecar_server.time.time()),
        nonce=secrets.token_urlsafe(18),
    )
    return signed


async def test_after_eof_runs_once_after_connection_reset_and_preserves_eof_error(
    monkeypatch,
):
    callbacks = []

    async def failed_write_eof(self, data=b""):
        raise ConnectionResetError("client disconnected")

    def failed_after_eof():
        callbacks.append(True)
        raise RuntimeError("schedule failed")

    monkeypatch.setattr(sidecar_server.web.Response, "write_eof", failed_write_eof)
    response = sidecar_server._AfterEofJsonResponse({}, failed_after_eof)

    with pytest.raises(ConnectionResetError, match="client disconnected"):
        await response.write_eof()
    with pytest.raises(ConnectionResetError, match="client disconnected"):
        await response.write_eof()

    assert callbacks == [True]


async def test_after_eof_runs_once_on_successful_repeated_write_eof(monkeypatch):
    callbacks = []
    writes = []

    async def successful_write_eof(self, data=b""):
        writes.append(data)

    monkeypatch.setattr(sidecar_server.web.Response, "write_eof", successful_write_eof)
    response = sidecar_server._AfterEofJsonResponse({}, lambda: callbacks.append(True))

    await response.write_eof()
    await response.write_eof(b"again")

    assert callbacks == [True]
    assert writes == [b"", b"again"]


class FakeFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []
        self.fail_send = False
        self.send_delay = 0.0
        self.update_failures_remaining = 0
        self.update_error_message = "update unavailable"
        self.update_delay = 0.0

    async def send_card(self, chat_id, card, thread_id=None, reply_to_message_id=None):
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        if self.fail_send:
            raise RuntimeError("send unavailable")
        self.sent.append((chat_id, card, thread_id, reply_to_message_id))
        return f"feishu-message-{len(self.sent)}"

    async def update_card_message(self, message_id, card):
        if self.update_delay:
            await asyncio.sleep(self.update_delay)
        if self.update_failures_remaining > 0:
            self.update_failures_remaining -= 1
            raise RuntimeError(self.update_error_message)
        self.updated.append((message_id, card))


class ReorderingFeishuClient(FakeFeishuClient):
    def __init__(self):
        super().__init__()
        self.update_calls = 0

    async def update_card_message(self, message_id, card):
        self.update_calls += 1
        update_call = self.update_calls
        if update_call == 1:
            await asyncio.sleep(0.05)
        self.updated.append((message_id, card))


class ControlledOperationsUpdateClient(FakeFeishuClient):
    def __init__(self):
        super().__init__()
        self.update_started = asyncio.Event()
        self.release_update = asyncio.Event()
        self.block_first_update = True

    async def update_card_message(self, message_id, card):
        if self.block_first_update:
            self.block_first_update = False
            self.update_started.set()
            await self.release_update.wait()
        if self.update_failures_remaining > 0:
            self.update_failures_remaining -= 1
            raise RuntimeError(self.update_error_message)
        self.updated.append((message_id, card))


class FakeBotRegistry:
    def safe_diagnostics(self):
        return {
            "default_bot": "default",
            "bot_count": 2,
            "chat_binding_count": 1,
            "secret": "registry-secret",
        }


class FakeFeishuClientFactory:
    def __init__(self, cards=None, profile_card=None):
        self.registry = FakeBotRegistry()
        self.clients = {
            "default": FakeFeishuClient(),
            "sales": FakeFeishuClient(),
        }
        self.cards = cards or {}
        self.profile_card = profile_card or {}

    def get_client(self, bot_id):
        return self.clients[bot_id]

    def card_config_for_bot(self, bot_id, base_card=None, profile_card=None):
        card = dict(base_card or {})
        card.update(profile_card or self.profile_card)
        card.update(self.cards.get(bot_id, {}))
        return card


def operations_report(
    *,
    report_marker="report-a",
    recovery_fingerprint="recovery-a",
    executable=True,
    profile_source="",
):
    return DiagnosticReport(
        status="warning",
        created_at=100.0,
        config={"path": "/private/config.yaml", "marker": report_marker},
        hermes={"root": "/private/hermes", "status": "supported"},
        streaming={"status": "enabled"},
        install_state={
            "status": "incomplete",
            "recovery_executable": executable,
            "recovery_fingerprint": recovery_fingerprint,
        },
        routing={"profile_id": "default", "profile_source": profile_source},
        runtime={},
        findings=(
            DiagnosticFinding(
                code="owned_incomplete",
                severity="warning",
                message="Hook state needs repair.",
            ),
        ),
    )


async def test_operations_callbacks_keep_full_recovery_fingerprint_internal(monkeypatch):
    feishu_client = FakeFeishuClient()
    full_fingerprint = "a" * 64
    report = DiagnosticReport(
        status="warning",
        created_at=100.0,
        config={"path": "/private/config.yaml", "marker": "full-fingerprint"},
        hermes={"root": "/private/hermes", "status": "supported"},
        streaming={"status": "enabled"},
        install_state={
            "status": "incomplete",
            "recovery_executable": True,
            "recovery_fingerprint": full_fingerprint[:12],
        },
        routing={"profile_id": "default"},
        runtime={},
        findings=(
            DiagnosticFinding(
                code="owned_incomplete",
                severity="warning",
                message="Hook state needs repair.",
            ),
        ),
        internal_recovery_fingerprint=full_fingerprint,
    )
    calls = []
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, SimpleNamespace(root=Path("/private/hermes"))),
    )
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args: calls.append(args) or SimpleNamespace(status="repaired"),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_private",
                "message_id": "om_doctor",
                "chat_type": "private",
                "operator": "ou_owner",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        card = feishu_client.sent[0][1]
        details = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                operations_button(card, "查看诊断"), chat_id="oc_private"
            ),
        )
        details_body = await details.json()
        repair = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                operations_button(details_body["card"], "安全修复"),
                chat_id="oc_private",
            ),
        )
        confirm = operations_button((await repair.json())["card"], "确认修复")
        completed = await test_client.post(
            "/card/actions", json=operations_action_payload(confirm, chat_id="oc_private")
        )
        completed_body = await completed.json()
    finally:
        await test_client.close()

    assert details_body["ok"] is True
    assert completed_body["ok"] is True
    assert calls == [(SimpleNamespace(root=Path("/private/hermes")), full_fingerprint)]


def operations_button(card, label):
    def find_button(elements):
        for element in elements:
            if element.get("tag") == "button":
                text = element.get("text")
                behaviors = element.get("behaviors")
                if (
                    isinstance(text, dict)
                    and text.get("content") == label
                    and isinstance(behaviors, list)
                    and behaviors
                    and isinstance(behaviors[0], dict)
                    and isinstance(behaviors[0].get("value"), dict)
                ):
                    return behaviors[0]["value"]
            if element.get("tag") == "column_set":
                for column in element.get("columns", []):
                    value = find_button(column.get("elements", []))
                    if value is not None:
                        return value
            for button in element.get("actions", []):
                if button["text"]["content"] == label:
                    return button["value"]
        return None

    value = find_button(card["body"]["elements"])
    if value is not None:
        return value
    raise AssertionError(f"missing operations button: {label}")


def operations_action_payload(
    value,
    *,
    chat_id="oc_group",
    operator="ou_owner",
    profile_id="",
    proof_profile_id="",
    transport_secret=None,
):
    context = {"open_chat_id": chat_id}
    if profile_id:
        context["profile_id"] = profile_id
    proof_profile_id = proof_profile_id or profile_id or "default"
    timestamp = int(sidecar_server.time.time())
    token = str(value.get("token") or "")
    encoded = token.split(".", 1)[0]
    claims = json.loads(
        base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    )
    proof = sign_transport_proof(
        transport_secret
        or derive_operation_transport_secret(
            TRANSPORT_ROOT_SECRET,
            claims["operation_id"],
        ),
        token=token,
        action=str(value.get("operation_action") or ""),
        callback_chat_id=chat_id,
        callback_profile_id=proof_profile_id,
        callback_profile_scope=str(value.get("profile_scope") or ""),
        operator_open_id=operator,
        timestamp=timestamp,
    )
    return {
        "adapter_transport_proof": {
            "timestamp": timestamp,
            "signature": proof,
        },
        "event": {
            "action": {"value": value},
            "context": context,
            "operator": {"open_id": operator} if operator else {},
        }
    }


def event_payload(
    event,
    sequence,
    data=None,
    *,
    conversation_id="conversation-1",
    message_id="hermes-message-1",
    chat_id="oc_abc",
    thread_id="",
):
    payload = {
        "schema_version": "1",
        "event": event,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "chat_id": chat_id,
        "platform": "feishu",
        "sequence": sequence,
        "created_at": 1777017600.0 + sequence,
        "data": data or {},
    }
    if thread_id:
        payload["thread_id"] = thread_id
    return payload


@pytest.fixture
async def client():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        yield test_client, feishu_client
    finally:
        await test_client.close()


async def test_event_auth_required_rejects_unsigned_and_reports_bounded_health():
    app = create_app(FakeFeishuClient(), event_auth_required=True)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload("message.started", 0),
        )
        response_body = await response.json()
        health = await (await test_client.get("/health")).json()
    finally:
        await test_client.close()

    assert response.status == 401
    assert response_body == {
        "ok": False,
        "error": "event authentication failed",
    }
    assert health["event_auth_required"] is True
    assert health["metrics"]["events_received"] == 0
    assert health["metrics"]["events_rejected"] == 1
    assert health["metrics"]["event_auth_rejections"] == 1
    assert "signature" not in json.dumps(health).lower()


async def test_event_auth_required_accepts_valid_body_once_and_rejects_replay():
    app = create_app(FakeFeishuClient(), event_auth_required=True)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    payload = event_payload("message.started", 0)
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        **sign_event_request(
            TRANSPORT_ROOT_SECRET,
            body,
            timestamp=int(sidecar_server.time.time()),
            nonce="nonce-1234567890",
        ),
    }
    try:
        accepted = await test_client.post("/events", data=body, headers=headers)
        accepted_body = await accepted.json()
        replayed = await test_client.post("/events", data=body, headers=headers)
    finally:
        await test_client.close()

    assert accepted.status == 200
    assert accepted_body == {"ok": True, "applied": True}
    assert replayed.status == 401


async def test_event_auth_required_rejects_wrong_signature_without_echoing_it():
    app = create_app(FakeFeishuClient(), event_auth_required=True)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    payload = event_payload("message.started", 0)
    body = json.dumps(payload).encode("utf-8")
    wrong_signature = "f" * 64
    headers = {
        "Content-Type": "application/json",
        **sign_event_request(
            TRANSPORT_ROOT_SECRET,
            body,
            timestamp=int(sidecar_server.time.time()),
            nonce="nonce-1234567890",
        ),
        "X-HFC-Event-Signature": wrong_signature,
    }
    try:
        response = await test_client.post("/events", data=body, headers=headers)
        response_text = await response.text()
    finally:
        await test_client.close()

    assert response.status == 401
    assert wrong_signature not in response_text


def test_create_app_refuses_required_event_auth_without_private_root_secret():
    with pytest.raises(ValueError, match="event authentication"):
        _create_app(
            FakeFeishuClient(),
            operations_transport_root_secret=None,
            event_auth_required=True,
        )


async def wait_for_card_update(feishu_client, expected_text, attempts=80):
    for _ in range(attempts):
        for message_id, card in reversed(feishu_client.updated):
            if expected_text in str(card):
                return message_id, card
        await _REAL_ASYNCIO_SLEEP(0.01)
    raise AssertionError(f"card update containing {expected_text!r} was not observed")


async def test_new_turn_abandons_interrupted_session_in_same_conversation(client):
    test_client, _feishu_client = client
    first = {
        "conversation_id": "conversation-interrupt",
        "message_id": "message-interrupted",
    }
    second = {
        "conversation_id": "conversation-interrupt",
        "message_id": "message-follow-up",
    }

    await test_client.post(
        "/events",
        json=event_payload("message.started", 0, **first),
    )
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "partial"}, **first),
    )
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 0, {"text": "follow-up"}, **second),
    )

    assert test_client.app[SESSIONS_KEY]["message-interrupted"].status == "completed"
    assert test_client.app[SESSIONS_KEY]["message-follow-up"].status == "thinking"


async def test_interrupted_terminal_update_cannot_be_overwritten_by_stale_delta():
    feishu_client = ReorderingFeishuClient()
    app = create_app(feishu_client, card_config={"flush_interval_ms": 0})
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    first = {
        "conversation_id": "conversation-race",
        "message_id": "message-race-old",
    }
    second = {
        "conversation_id": "conversation-race",
        "message_id": "message-race-new",
    }
    try:
        await test_client.post(
            "/events",
            json=event_payload("message.started", 0, **first),
        )
        await test_client.post(
            "/events",
            json=event_payload("answer.delta", 1, {"text": "stale delta"}, **first),
        )
        await _wait_until(lambda: feishu_client.update_calls == 1)

        await test_client.post(
            "/events",
            json=event_payload("message.started", 0, **second),
        )
        await _wait_until(
            lambda: len(
                [
                    card
                    for message_id, card in feishu_client.updated
                    if message_id == "feishu-message-1"
                ]
            )
            >= 2
        )
    finally:
        await test_client.close()

    old_card_updates = [
        card
        for message_id, card in feishu_client.updated
        if message_id == "feishu-message-1"
    ]
    assert "已完成" in str(old_card_updates[-1])


async def test_interrupted_session_log_does_not_expose_chat_id(client, caplog):
    test_client, _feishu_client = client
    chat_id = "oc_sensitive_interrupt_chat"

    with caplog.at_level(logging.INFO, logger=sidecar_server.__name__):
        await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                conversation_id="conversation-log",
                message_id="message-log-old",
                chat_id=chat_id,
            ),
        )
        await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                conversation_id="conversation-log",
                message_id="message-log-new",
                chat_id=chat_id,
            ),
        )

    assert chat_id not in caplog.text
    assert sidecar_server._diagnostic_id_hash(chat_id) in caplog.text


async def _wait_until(predicate, attempts=80):
    for _ in range(attempts):
        if predicate():
            return
        await _REAL_ASYNCIO_SLEEP(0.01)
    raise AssertionError("condition was not observed")


async def wait_for_metric(test_client, metric_name, expected_value, attempts=80):
    body = None
    for _ in range(attempts):
        health = await test_client.get("/health")
        body = await health.json()
        if body["metrics"][metric_name] == expected_value:
            return body["metrics"]
        await _REAL_ASYNCIO_SLEEP(0.01)
    raise AssertionError(
        f"metric {metric_name!r} did not become {expected_value!r}: {body}"
    )


def test_create_app_does_not_require_a_current_event_loop():
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None
    try:
        asyncio.set_event_loop(None)
        app = create_app(FakeFeishuClient())
    finally:
        asyncio.set_event_loop(previous_loop)

    assert app[sidecar_server.OPERATIONS_DIAGNOSTIC_SEMAPHORE_KEY]["value"] is None
    assert app[sidecar_server.OPERATIONS_PUBLISH_LOCKS_GUARD_KEY]["value"] is None


async def test_health_reports_healthy_status_and_active_sessions(client):
    test_client, _ = client

    response = await test_client.get("/health")

    assert response.status == 200
    body = await response.json()
    assert body["status"] == "healthy"
    assert body["event_auth_required"] is False
    assert body["active_sessions"] == 0
    assert body["metrics"] == {
        "events_received": 0,
        "events_applied": 0,
        "events_ignored": 0,
        "events_rejected": 0,
        "event_auth_rejections": 0,
        "feishu_send_attempts": 0,
        "feishu_send_successes": 0,
        "feishu_send_failures": 0,
        "feishu_update_attempts": 0,
        "feishu_update_successes": 0,
        "feishu_update_failures": 0,
        "feishu_update_retries": 0,
        "update_scheduled": 0,
        "update_coalesced": 0,
        "update_queue_peak": 0,
        "terminal_drains": 0,
        "terminal_drain_timeouts": 0,
        "terminal_drain_latency_ms": 0,
        "feishu_update_latency_ms": 0,
        "cron_cards_sent": 0,
        "cron_fallbacks": 0,
        "recovery_plans_available": 0,
        "recovery_attempts": 0,
        "recovery_successes": 0,
        "recovery_refusals": 0,
        "profile_mismatches": 0,
        "sessions_collected": 0,
        "zombie_sessions_collected": 0,
        "flush_controllers_collected": 0,
    }
    assert body["reply_index"] == {"entries": 0, "last_lookup": {}}
    assert body["cron"] == {"cards_sent": 0, "fallbacks": 0}
    assert body["profile_diagnostics"] == {}


def test_cleanup_runtime_state_removes_related_state_and_counts_once():
    app = create_app(FakeFeishuClient())
    session_key = "profile:om_terminal"
    alias_key = "profile:om_reply"
    session = CardSession("oc_1", "om_terminal", "oc_1")
    session.status = "completed"
    session.updated_at = 100.0
    session.answer_text = "final answer"
    session.active_interaction = InteractionState(
        interaction_id="approval-old",
        kind="approval",
        prompt="Old approval",
        status="completed",
    )
    controller = FlushController(interval_seconds=0.2, metrics=app[METRICS_KEY])
    controller.close()
    app[SESSIONS_KEY][session_key] = session
    app[SESSION_ALIASES_KEY][alias_key] = session_key
    app[MESSAGE_LOCKS_KEY][session_key] = SimpleNamespace(locked=lambda: False)
    app[MESSAGE_LOCKS_KEY][alias_key] = SimpleNamespace(locked=lambda: False)
    app[FEISHU_MESSAGE_IDS_KEY][session_key] = "om_card"
    app[MESSAGE_BOT_IDS_KEY][session_key] = "profile:default"
    app[SESSION_CARD_CONFIGS_KEY][session_key] = {"title": "Card"}
    app[FLUSH_CONTROLLERS_KEY][session_key] = controller
    sidecar_server._store_interaction_result(app, session)
    session.active_interaction = InteractionState(
        interaction_id="approval-latest",
        kind="approval",
        prompt="Latest approval",
        status="completed",
    )
    sidecar_server._store_interaction_result(app, session)
    sidecar_server._store_card_summary(
        app,
        SidecarEvent.from_dict(event_payload("message.completed", 1)),
        session,
        "om_card_previous_turn",
    )
    sidecar_server._store_card_summary(
        app,
        SidecarEvent.from_dict(event_payload("message.completed", 1)),
        session,
        "om_card",
    )

    result = cleanup_runtime_state(app, now=3700.0)

    assert result.session_keys == (session_key,)
    assert result.reasons == ("terminal_retention_expired",)
    assert result.controllers_collected == 1
    for state in (
        app[SESSIONS_KEY],
        app[SESSION_ALIASES_KEY],
        app[MESSAGE_LOCKS_KEY],
        app[FEISHU_MESSAGE_IDS_KEY],
        app[MESSAGE_BOT_IDS_KEY],
        app[SESSION_CARD_CONFIGS_KEY],
        app[FLUSH_CONTROLLERS_KEY],
    ):
        assert session_key not in state
        assert alias_key not in state
    assert app[CARD_SUMMARIES_KEY] == {}
    assert app[CARD_SUMMARY_SESSION_KEYS_KEY] == {}
    assert app[INTERACTION_RESULTS_KEY] == {}
    assert app[INTERACTION_RESULT_SESSION_KEYS_KEY] == {}
    assert app[METRICS_KEY].sessions_collected == 1
    assert app[METRICS_KEY].zombie_sessions_collected == 0
    assert app[METRICS_KEY].flush_controllers_collected == 1
    assert session_key not in str(app[DIAGNOSTICS_KEY]["cleanup_history"])
    assert app[DIAGNOSTICS_KEY]["cleanup_history"][-1]["session_key_hash"]

    assert cleanup_runtime_state(app, now=9999.0).session_keys == ()
    assert app[METRICS_KEY].sessions_collected == 1
    assert app[METRICS_KEY].flush_controllers_collected == 1


async def test_cleanup_reassigned_alias_keeps_new_session_routable():
    app = create_app(FakeFeishuClient())
    old_key = "om_old"
    new_key = "om_new"
    old = CardSession("conversation-1", old_key, "oc_abc")
    old.status = "completed"
    old.updated_at = 100.0
    active = CardSession("conversation-1", new_key, "oc_abc")
    active.updated_at = 3700.0
    active.answer_text = "active"
    app[SESSIONS_KEY][old_key] = old
    app[SESSIONS_KEY][new_key] = active
    app[FEISHU_MESSAGE_IDS_KEY][new_key] = "om_active_card"
    app[MESSAGE_BOT_IDS_KEY][new_key] = "profile:default"
    app[SESSION_CARD_CONFIGS_KEY][new_key] = {"title": "Card"}

    class ReassignedAliases(dict):
        def __init__(self):
            super().__init__({"om_reply": new_key})
            self._first_items_call = True

        def items(self):
            if self._first_items_call:
                self._first_items_call = False
                return (("om_reply", old_key),)
            return super().items()

    app[SESSION_ALIASES_KEY] = ReassignedAliases()
    cleanup_runtime_state(app, now=3700.0)

    assert old_key not in app[SESSIONS_KEY]
    assert app[SESSION_ALIASES_KEY]["om_reply"] == new_key
    assert app[FEISHU_MESSAGE_IDS_KEY][new_key] == "om_active_card"
    assert app[MESSAGE_BOT_IDS_KEY][new_key] == "profile:default"
    assert app[SESSION_CARD_CONFIGS_KEY][new_key] == {"title": "Card"}

    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload(
                "answer.delta",
                1,
                {"text": "fresh", "reply_to_message_id": "om_reply"},
                message_id="om_followup",
            ),
        )
        body = await response.json()
    finally:
        await test_client.close()

    assert response.status == 200
    assert body == {"ok": True, "applied": True}
    assert app[SESSIONS_KEY][new_key] is active
    assert active.answer_text == "activefresh"


async def test_cleanup_runtime_state_keeps_active_interactions_and_inflight_aliases():
    app = create_app(FakeFeishuClient())
    interaction_key = "om_interaction"
    inflight_key = "om_inflight"
    inflight_alias = "om_inflight_reply"
    interaction = CardSession("oc_1", interaction_key, "oc_1")
    interaction.updated_at = 0.0
    interaction.active_interaction = InteractionState(
        interaction_id="approval-1",
        kind="approval",
        prompt="允许吗？",
    )
    inflight = CardSession("oc_1", inflight_key, "oc_1")
    inflight.status = "failed"
    inflight.updated_at = 0.0
    alias_lock = asyncio.Lock()
    await alias_lock.acquire()
    app[SESSIONS_KEY].update(
        {interaction_key: interaction, inflight_key: inflight}
    )
    app[SESSION_ALIASES_KEY][inflight_alias] = inflight_key
    app[MESSAGE_LOCKS_KEY][inflight_alias] = alias_lock
    sidecar_server._store_interaction_result(app, interaction)
    inflight.answer_text = "in flight"
    app[FEISHU_MESSAGE_IDS_KEY][inflight_key] = "om_inflight_card"
    sidecar_server._store_card_summary(
        app,
        SidecarEvent.from_dict(
            event_payload(
                "message.failed",
                1,
                message_id=inflight_key,
            )
        ),
        inflight,
        "om_inflight_card",
    )

    try:
        result = cleanup_runtime_state(app, now=5000.0)
    finally:
        alias_lock.release()

    assert result.session_keys == ()
    assert set(app[SESSIONS_KEY]) == {interaction_key, inflight_key}
    assert app[INTERACTION_RESULTS_KEY]["approval-1"]["status"] == "pending"
    assert app[CARD_SUMMARIES_KEY]["om_inflight_card"]["summary"] == "in flight"
    assert app[INTERACTION_RESULT_SESSION_KEYS_KEY]["approval-1"] == interaction_key
    assert app[CARD_SUMMARY_SESSION_KEYS_KEY]["om_inflight_card"] == inflight_key
    assert app[METRICS_KEY].sessions_collected == 0


def test_cleanup_history_is_hashed_and_bounded_to_fifty_entries():
    app = create_app(FakeFeishuClient())
    raw_keys = []
    for index in range(55):
        session_key = f"private-session-{index}"
        raw_keys.append(session_key)
        session = CardSession("oc_1", session_key, "oc_1")
        session.status = "failed"
        session.updated_at = 0.0
        app[SESSIONS_KEY][session_key] = session

    result = cleanup_runtime_state(app, now=3600.0)

    history = app[DIAGNOSTICS_KEY]["cleanup_history"]
    assert len(result.session_keys) == 55
    assert len(history) == 50
    assert app[METRICS_KEY].sessions_collected == 55
    assert not any(raw_key in str(history) for raw_key in raw_keys)


def test_closed_controller_cleanup_requires_same_instance():
    app = create_app(FakeFeishuClient())
    session_key = "om_reused"
    old = FlushController(interval_seconds=0.2, metrics=app[METRICS_KEY])
    replacement = FlushController(interval_seconds=0.2, metrics=app[METRICS_KEY])
    old.close()
    app[FLUSH_CONTROLLERS_KEY][session_key] = replacement

    assert not cleanup_closed_controller(app, session_key, old, now=100.0)
    assert app[FLUSH_CONTROLLERS_KEY][session_key] is replacement
    assert app[METRICS_KEY].flush_controllers_collected == 0


def test_orphan_message_lock_cleanup_requires_same_instance_and_no_users():
    app = create_app(FakeFeishuClient())
    lock_key = "om_failed"
    old = SimpleNamespace(locked=lambda: False)
    replacement = SimpleNamespace(locked=lambda: False)
    app[MESSAGE_LOCKS_KEY][lock_key] = replacement

    assert not cleanup_orphan_message_lock(app, lock_key, old)
    app[MESSAGE_LOCK_USERS_KEY][lock_key] = 1
    assert not cleanup_orphan_message_lock(app, lock_key, replacement)
    assert app[MESSAGE_LOCKS_KEY][lock_key] is replacement

    app[MESSAGE_LOCK_USERS_KEY].pop(lock_key)
    assert cleanup_orphan_message_lock(app, lock_key, replacement)
    assert lock_key not in app[MESSAGE_LOCKS_KEY]


async def test_terminal_update_removes_its_closed_controller(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )
    await wait_for_card_update(feishu_client, "最终答案")
    for _ in range(20):
        if "hermes-message-1" not in test_client.app[FLUSH_CONTROLLERS_KEY]:
            break
        await _REAL_ASYNCIO_SLEEP(0)

    assert "hermes-message-1" not in test_client.app[FLUSH_CONTROLLERS_KEY]
    assert test_client.app[METRICS_KEY].flush_controllers_collected == 1


async def test_terminal_update_fetches_configured_subscription_usage_once(monkeypatch):
    feishu_client = FakeFeishuClient()
    calls = []

    async def fake_fetch(hermes_root):
        calls.append(hermes_root)
        return "5h 26% · weekly 89%"

    monkeypatch.setattr(sidecar_server, "fetch_codex_subscription_usage", fake_fetch)
    app = create_app(
        feishu_client,
        card_config={
            "flush_interval_ms": 0,
            "footer_fields": ["duration", "subscription_usage"],
        },
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))
        await test_client.post(
            "/events",
            json=event_payload("message.completed", 1, {"answer": "最终答案"}),
        )
        _message_id, card = await wait_for_card_update(
            feishu_client, "5h 26% · weekly 89%"
        )
    finally:
        await test_client.close()

    assert len(calls) == 1
    assert "最终答案" in str(card)


async def test_terminal_update_does_not_fetch_unconfigured_subscription_usage(
    client, monkeypatch
):
    test_client, feishu_client = client

    async def unexpected_fetch(_hermes_root):
        raise AssertionError("subscription usage should remain opt-in")

    monkeypatch.setattr(
        sidecar_server, "fetch_codex_subscription_usage", unexpected_fetch
    )
    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )

    _message_id, card = await wait_for_card_update(feishu_client, "最终答案")

    assert "weekly" not in str(card)


async def test_terminal_task_exception_still_removes_closed_controller_once(caplog):
    app = create_app(FakeFeishuClient())
    session_key = "om_terminal_error"
    controller = FlushController(interval_seconds=0.2, metrics=app[METRICS_KEY])
    controller.close()
    app[FLUSH_CONTROLLERS_KEY][session_key] = controller

    async def fail_update():
        raise RuntimeError("terminal update failed")

    task = asyncio.create_task(fail_update())
    with pytest.raises(RuntimeError, match="terminal update failed"):
        await task

    sidecar_server._post_terminal_cleanup(app, session_key, controller, task)
    sidecar_server._post_terminal_cleanup(app, session_key, controller, task)

    assert session_key not in app[FLUSH_CONTROLLERS_KEY]
    assert app[METRICS_KEY].flush_controllers_collected == 1
    assert "terminal card update task failed" in caplog.text


async def test_terminal_task_cancel_preserves_replacement_controller():
    app = create_app(FakeFeishuClient())
    session_key = "om_terminal_reused"
    old = FlushController(interval_seconds=0.2, metrics=app[METRICS_KEY])
    replacement = FlushController(interval_seconds=0.2, metrics=app[METRICS_KEY])
    old.close()
    app[FLUSH_CONTROLLERS_KEY][session_key] = replacement

    task = asyncio.create_task(asyncio.sleep(60))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    sidecar_server._post_terminal_cleanup(app, session_key, old, task)

    assert app[FLUSH_CONTROLLERS_KEY][session_key] is replacement
    assert app[METRICS_KEY].flush_controllers_collected == 0


async def test_runtime_cleanup_starts_one_sixty_second_task_and_cancels_it(monkeypatch):
    delays = []
    second_sleep_started = asyncio.Event()

    async def fake_cleanup_sleep(delay):
        delays.append(delay)
        if len(delays) == 1:
            return
        second_sleep_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(sidecar_server, "_cleanup_sleep", fake_cleanup_sleep)
    app = create_app(FakeFeishuClient())
    zombie = CardSession("oc_1", "om_zombie", "oc_1")
    zombie.updated_at = 0.0
    app[SESSIONS_KEY]["om_zombie"] = zombie
    test_client = TestClient(TestServer(app))

    await test_client.start_server()
    task = app[CLEANUP_TASK_KEY]
    await asyncio.wait_for(second_sleep_started.wait(), timeout=1.0)
    try:
        assert delays == [
            RUNTIME_CLEANUP_INTERVAL_SECONDS,
            RUNTIME_CLEANUP_INTERVAL_SECONDS,
        ]
        assert RUNTIME_CLEANUP_INTERVAL_SECONDS == 60.0
        assert "om_zombie" not in app[SESSIONS_KEY]
        assert app[METRICS_KEY].zombie_sessions_collected == 1
        assert not task.done()
    finally:
        await test_client.close()

    assert task.cancelled()


async def test_hfc_help_command_sends_read_only_diagnostic_card(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/commands",
        json={
            "command": "help",
            "chat_id": "oc_secret_chat",
            "message_id": "om_secret_message",
            "thread_id": "omt_secret_thread",
        },
    )

    assert response.status == 200
    body = await response.json()
    assert body == {"ok": True, "handled": True, "command": "help"}
    assert len(feishu_client.sent) == 1
    chat_id, card, thread_id, reply_to_message_id = feishu_client.sent[0]
    assert chat_id == "oc_secret_chat"
    assert thread_id == "omt_secret_thread"
    assert reply_to_message_id == "om_secret_message"
    content = str(card)
    assert "/hfc status" in content
    assert "/hfc doctor" in content
    assert "/hfc monitor" in content
    assert "oc_secret_chat" not in content
    assert "om_secret_message" not in content
    assert "omt_secret_thread" not in content


async def test_hfc_command_request_returns_before_slow_feishu_send():
    class BlockingFeishuClient(FakeFeishuClient):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send_card(self, chat_id, card, thread_id=None, reply_to_message_id=None):
            self.started.set()
            await self.release.wait()
            return await super().send_card(
                chat_id,
                card,
                thread_id=thread_id,
                reply_to_message_id=reply_to_message_id,
            )

    feishu_client = BlockingFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    post_task = asyncio.create_task(
        test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "status",
                "chat_id": "oc_secret_chat",
                "message_id": "om_secret_message",
            }),
        )
    )
    try:
        await asyncio.wait_for(feishu_client.started.wait(), timeout=1.0)
        try:
            response = await asyncio.wait_for(asyncio.shield(post_task), timeout=0.05)
        except asyncio.TimeoutError as exc:
            raise AssertionError("/commands waited for Feishu card delivery") from exc
        body = await response.json()
        assert response.status == 200
        assert body == {"ok": True, "handled": True, "command": "status"}
        assert feishu_client.sent == []

        feishu_client.release.set()
        for _ in range(80):
            if feishu_client.sent:
                break
            await _REAL_ASYNCIO_SLEEP(0.01)
        assert len(feishu_client.sent) == 1
    finally:
        feishu_client.release.set()
        await post_task
        await test_client.close()


async def test_hfc_status_group_unbound_shows_binding_hint_and_slash_guidance():
    factory = FakeFeishuClientFactory()

    def bot_router(event):
        assert event.data["chat_type"] == "group"
        return RouteResult(
            "default",
            "bindings.fallback_bot",
            metadata={
                "group": {
                    "is_group": True,
                    "enabled": True,
                    "chat_bound": False,
                    "chat_allowed": True,
                    "require_mention": True,
                }
            },
        )

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "status",
                "chat_id": "oc_group",
                "message_id": "om_group_status",
                "data": {"chat_type": "group"},
            }),
        )
    finally:
        await test_client.close()

    assert response.status == 200
    assert len(factory.clients["default"].sent) == 1
    card = factory.clients["default"].sent[0][1]
    content = str(card)
    assert "当前群未绑定" in content
    assert "bots bind-chat oc_group default" in content
    assert "群内 slash command" in content
    assert "Hermes @/白名单" in content


async def test_hfc_monitor_command_reports_safe_metrics(client):
    test_client, feishu_client = client
    metrics = test_client.app[sidecar_server.METRICS_KEY]
    metrics.events_received = 3
    metrics.update_coalesced = 2
    metrics.update_queue_peak = 4

    response = await test_client.post(
        "/commands",
        json={
            "command": "monitor",
            "chat_id": "oc_monitor_secret",
            "message_id": "om_monitor_secret",
        },
    )

    assert response.status == 200
    assert (await response.json()) == {
        "ok": True,
        "handled": True,
        "command": "monitor",
    }
    assert len(feishu_client.sent) == 1
    content = str(feishu_client.sent[0][1])
    assert "events_received: 3" in content
    assert "update_coalesced: 2" in content
    assert "update_queue_peak: 4" in content
    assert "active_sessions: 0" in content
    assert "oc_monitor_secret" not in content
    assert "om_monitor_secret" not in content


async def test_hfc_doctor_sends_group_owned_operations_card(monkeypatch):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    detection = SimpleNamespace(root=Path("/private/hermes"))
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, detection),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_group",
                "message_id": "om_doctor",
                "chat_type": "group",
                "operator": "ou_initiator",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        card = feishu_client.sent[0][1]
        repair = operations_button(card, "安全修复")
        rejected = await test_client.post(
            "/card/actions",
            json=operations_action_payload(repair, operator="ou_other"),
        )
        body = await rejected.json()
        accepted = await test_client.post(
            "/card/actions",
            json=operations_action_payload(repair, operator="ou_initiator"),
        )
        confirm = operations_button((await accepted.json())["card"], "确认修复")
        rejected_confirm = await test_client.post(
            "/card/actions",
            json=operations_action_payload(confirm, operator="ou_other"),
        )
        rejected_confirm_body = await rejected_confirm.json()
    finally:
        await test_client.close()

    assert response.status == 200
    assert rejected.status == 200
    assert body["ok"] is False
    assert rejected_confirm_body["ok"] is False
    assert operations_button(rejected_confirm_body["card"], "确认修复")
    assert "ou_initiator" not in str(card)
    assert "oc_group" not in str(card)
    assert operations_button(body["card"], "安全修复")


async def test_hfc_doctor_acknowledges_before_slow_diagnosis_and_binds_operation(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    detection = SimpleNamespace(root=Path("/private/hermes"))

    def slow_report(*args, **kwargs):
        time.sleep(1.0)
        return report, detection

    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", slow_report)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        started = time.monotonic()
        response = await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_slow_doctor",
                    "chat_type": "private",
                }
            ),
        )
        elapsed = time.monotonic() - started
        body = await response.json()
        operation_id = body["operation_id"]
        store = app[sidecar_server.OPERATIONS_STORE_KEY]
        record = store._records[operation_id]

        assert elapsed < 0.8
        assert record.state == "preparing"
        assert operation_id in store._transport_secrets

        await _wait_until(lambda: bool(feishu_client.sent), attempts=160)
    finally:
        await test_client.close()

    assert response.status == 200
    assert body["ok"] is True
    assert body["handled"] is True
    assert feishu_client.sent[0][0] == "oc_private"


async def test_hfc_doctor_reuses_preparing_operation_and_starts_one_diagnosis(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    calls = []

    def build_report(*args, **kwargs):
        calls.append(True)
        time.sleep(0.05)
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", build_report)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        payload = {
            "command": "doctor",
            "chat_id": "oc_private",
            "message_id": "om_duplicate_doctor",
            "chat_type": "private",
        }
        first = await test_client.post(
            "/commands", json=signed_operations_command(payload)
        )
        second = await test_client.post(
            "/commands", json=signed_operations_command(payload)
        )
        first_body = await first.json()
        second_body = await second.json()
        await _wait_until(lambda: bool(feishu_client.sent))
    finally:
        await test_client.close()

    assert first_body["operation_id"] == second_body["operation_id"]
    assert calls == [True]
    assert len(feishu_client.sent) == 1


async def test_hfc_doctor_marks_background_failure_without_exception_details(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()

    def fail_report(*args, **kwargs):
        raise RuntimeError("private-token-should-not-appear")

    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", fail_report)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_failing_doctor",
                    "chat_type": "private",
                }
            ),
        )
        operation_id = (await response.json())["operation_id"]
        await _wait_until(lambda: bool(feishu_client.sent))
        record = app[sidecar_server.OPERATIONS_STORE_KEY]._records[operation_id]
    finally:
        await test_client.close()

    assert record.state == "failed"
    assert "private-token-should-not-appear" not in str(feishu_client.sent)
    assert "诊断暂时不可用" in str(feishu_client.sent[0][1])


async def test_hfc_doctor_timeout_marks_failed_and_releases_store_capacity(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    started = threading.Event()
    release = threading.Event()
    first_finished = threading.Event()
    calls = []

    def blocked_report(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            started.set()
            release.wait(1.0)
            first_finished.set()
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "OPERATIONS_DIAGNOSTIC_TIMEOUT_SECONDS", 0.02, raising=False)
    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", blocked_report)
    app = create_app(feishu_client)
    app[sidecar_server.OPERATIONS_STORE_KEY] = OperationStore(
        secret=b"store", max_records=1
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        first = await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_timeout_first",
                    "chat_type": "private",
                }
            ),
        )
        first_operation_id = (await first.json())["operation_id"]
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1.0)
        await _wait_until(lambda: bool(feishu_client.sent), attempts=40)
        failed = app[sidecar_server.OPERATIONS_STORE_KEY]._records[first_operation_id]
        second = await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_timeout_second",
                    "chat_type": "private",
                }
            ),
        )
        second_operation_id = (await second.json())["operation_id"]
        await _wait_until(lambda: len(feishu_client.sent) == 2)
        release.set()
        await asyncio.wait_for(asyncio.to_thread(first_finished.wait), timeout=1.0)
        await _wait_until(
            lambda: not app[sidecar_server.OPERATIONS_DIAGNOSTIC_TASKS_KEY]
        )
    finally:
        release.set()
        await test_client.close()

    assert failed.state == "failed"
    assert "诊断暂时不可用" in str(feishu_client.sent[0][1])
    assert second.status == 200
    assert len(feishu_client.sent) == 2
    assert set(app[sidecar_server.OPERATIONS_STORE_KEY]._records) == {
        second_operation_id
    }


async def test_hfc_doctor_diagnostics_do_not_queue_beyond_executor_workers(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def blocked_report(*args, **kwargs):
        calls.append(True)
        if len(calls) == sidecar_server.MAX_CONCURRENT_OPERATION_DIAGNOSTICS:
            started.set()
        release.wait(1.0)
        return operations_report(), SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "OPERATIONS_DIAGNOSTIC_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", blocked_report)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        executor = app[sidecar_server.OPERATIONS_DIAGNOSTIC_EXECUTOR_KEY]
        responses = await asyncio.gather(
            *[
                test_client.post(
                    "/commands",
                    json=signed_operations_command(
                        {
                            "command": "doctor",
                            "chat_id": "oc_private",
                            "message_id": f"om_bounded_{index}",
                            "chat_type": "private",
                        }
                    ),
                )
                for index in range(
                    sidecar_server.MAX_CONCURRENT_OPERATION_DIAGNOSTICS + 1
                )
            ]
        )
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1.0)
        await _wait_until(
            lambda: len(feishu_client.sent)
            == sidecar_server.MAX_CONCURRENT_OPERATION_DIAGNOSTICS + 1,
            attempts=80,
        )
    finally:
        release.set()
        await test_client.close()

    assert all(response.status == 200 for response in responses)
    assert executor._max_workers == sidecar_server.MAX_CONCURRENT_OPERATION_DIAGNOSTICS
    assert len(calls) == sidecar_server.MAX_CONCURRENT_OPERATION_DIAGNOSTICS


async def test_hfc_doctor_timeout_includes_waiting_for_diagnostic_slot(monkeypatch):
    feishu_client = FakeFeishuClient()
    monkeypatch.setattr(sidecar_server, "OPERATIONS_DIAGNOSTIC_TIMEOUT_SECONDS", 0.02)
    app = create_app(feishu_client)
    semaphore = sidecar_server._operations_diagnostic_semaphore(app)
    for _ in range(sidecar_server.MAX_CONCURRENT_OPERATION_DIAGNOSTICS):
        await semaphore.acquire()
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_waiting_timeout",
                    "chat_type": "private",
                }
            ),
        )
        operation_id = (await response.json())["operation_id"]
        tasks = app[sidecar_server.OPERATIONS_DIAGNOSTIC_TASKS_KEY]
        await _wait_until(lambda: bool(tasks), attempts=200)
        await asyncio.wait_for(asyncio.shield(next(iter(tasks))), timeout=2.0)
        record = app[sidecar_server.OPERATIONS_STORE_KEY]._records[operation_id]
    finally:
        for _ in range(sidecar_server.MAX_CONCURRENT_OPERATION_DIAGNOSTICS):
            semaphore.release()
        await test_client.close()

    assert response.status == 200
    assert record.state == "failed"


async def test_recheck_returns_immediately_when_diagnostic_executor_is_busy(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, SimpleNamespace(root=Path("/private/hermes"))),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_busy_action",
                    "chat_type": "private",
                }
            ),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        recheck = operations_button(feishu_client.sent[0][1], "重新检测")
        futures = app[sidecar_server.OPERATIONS_DIAGNOSTIC_FUTURES_KEY]
        futures.update(
            range(sidecar_server.MAX_CONCURRENT_OPERATION_DIAGNOSTICS)
        )
        response = await test_client.post(
            "/card/actions",
            json=operations_action_payload(recheck, chat_id="oc_private"),
        )
        body = await response.json()
        await wait_for_card_update(feishu_client, "诊断暂时不可用")
        record = app[sidecar_server.OPERATIONS_STORE_KEY].current_successor(
            body["operation_id"]
        )
    finally:
        futures.clear()
        await test_client.close()

    assert response.status == 200
    assert "正在重新检测" in str(body["card"])
    assert record is not None
    assert record.state == "failed"
    assert operations_button(feishu_client.updated[-1][1], "重新检测")


async def test_recheck_callback_returns_preparing_card_before_blocked_report_and_patches_same_card(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    recheck_started = threading.Event()
    release_recheck = threading.Event()
    calls = []

    def blocked_report(*args, **kwargs):
        calls.append(args[3])
        if args[3] == "fallback_default":
            recheck_started.set()
            release_recheck.wait(2.0)
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", blocked_report)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_fast_recheck",
                    "chat_type": "private",
                }
            ),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        recheck = operations_button(feishu_client.sent[0][1], "重新检测")
        original_id = next(iter(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]))
        started = time.monotonic()
        first = await asyncio.wait_for(
            test_client.post(
                "/card/actions",
                json=operations_action_payload(recheck, chat_id="oc_private"),
            ),
            timeout=0.5,
        )
        elapsed = time.monotonic() - started
        first_body = await first.json()
        await asyncio.wait_for(asyncio.to_thread(recheck_started.wait), timeout=1.0)
        repeated = await test_client.post(
            "/card/actions",
            json=operations_action_payload(recheck, chat_id="oc_private"),
        )
        repeated_body = await repeated.json()
        release_recheck.set()
        await wait_for_card_update(feishu_client, "诊断摘要")
    finally:
        release_recheck.set()
        await test_client.close()

    assert elapsed < 0.5
    assert "正在重新检测" in str(first_body["card"])
    assert first_body["operation_id"] == repeated_body["operation_id"]
    assert first_body["operation_id"] != original_id
    assert calls == ["", "fallback_default"]
    assert len(feishu_client.sent) == 1
    assert len(feishu_client.updated) == 3
    assert "正在重新检测" in str(feishu_client.updated[0][1])
    assert "诊断摘要" in str(feishu_client.updated[-1][1])


async def test_same_fingerprint_slow_recheck_patch_keeps_one_worker_and_links_old_token(
    monkeypatch,
):
    feishu_client = ControlledOperationsUpdateClient()
    feishu_client.update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS
    report = operations_report()
    calls = []
    unexpected_second_recheck = asyncio.Event()

    def build_report(*args, **_kwargs):
        profile_source = args[3]
        calls.append(profile_source)
        if profile_source == "fallback_default" and calls.count("fallback_default") > 1:
            unexpected_second_recheck.set()
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", build_report)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_slow_recheck",
                    "chat_type": "private",
                }
            ),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        recheck = operations_button(feishu_client.sent[0][1], "重新检测")
        first = await test_client.post(
            "/card/actions",
            json=operations_action_payload(recheck, chat_id="oc_private"),
        )
        first_body = await first.json()
        preparing_id = first_body["operation_id"]
        old_preparing_recheck = operations_button(first_body["card"], "重新检测")
        active_transport_secret = app[
            sidecar_server.OPERATIONS_STORE_KEY
        ]._transport_secrets[preparing_id]
        await asyncio.wait_for(feishu_client.update_started.wait(), timeout=1.0)

        repeated = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                old_preparing_recheck,
                chat_id="oc_private",
                transport_secret=active_transport_secret,
            ),
        )
        repeated_body = await repeated.json()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(unexpected_second_recheck.wait(), timeout=0.1)

        feishu_client.release_update.set()
        await _wait_until(
            lambda: not app[sidecar_server.OPERATIONS_DIAGNOSTIC_TASKS_KEY]
        )
        after_failure = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                old_preparing_recheck,
                chat_id="oc_private",
                transport_secret=active_transport_secret,
            ),
        )
        after_failure_body = await after_failure.json()
    finally:
        feishu_client.release_update.set()
        await test_client.close()

    assert first.status == 200
    assert repeated.status == 200
    assert after_failure.status == 200
    assert calls == ["", "fallback_default"]
    store = app[sidecar_server.OPERATIONS_STORE_KEY]

    def record_for(operation_id):
        return store._records.get(operation_id) or store._recheck_predecessors.get(
            operation_id
        )

    preparing = record_for(preparing_id)
    repeated_record = record_for(repeated_body["operation_id"])
    after_failure_record = record_for(after_failure_body["operation_id"])
    assert preparing is not None
    assert repeated_record is not None
    assert after_failure_record is not None
    assert repeated_record.transport_lineage_id == preparing.transport_lineage_id
    assert after_failure_record.transport_lineage_id == preparing.transport_lineage_id
    assert "诊断摘要" in str(after_failure_body["card"])


async def test_snapshot_actions_never_rebuild_a_report(monkeypatch):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    calls = []

    def build_report(*args, **kwargs):
        calls.append(True)
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", build_report)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_snapshot_actions",
                    "chat_type": "private",
                }
            ),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        card = feishu_client.sent[0][1]
        details = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                operations_button(card, "查看诊断"), chat_id="oc_private"
            ),
        )
        repair = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                operations_button((await details.json())["card"], "安全修复"),
                chat_id="oc_private",
            ),
        )
        cancelled = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                operations_button((await repair.json())["card"], "取消"),
                chat_id="oc_private",
            ),
        )
        dismissed = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                operations_button((await cancelled.json())["card"], "暂不处理"),
                chat_id="oc_private",
            ),
        )
        assert (await dismissed.json())["ok"] is True
    finally:
        await test_client.close()

    assert calls == [True]


async def test_confirm_repair_returns_executing_card_before_fresh_evidence_and_runs_once(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    fresh_started = threading.Event()
    release_fresh = threading.Event()
    executions = []
    calls = []

    def blocked_report(*args, **kwargs):
        calls.append(args[3])
        if args[3] == "fallback_default":
            fresh_started.set()
            release_fresh.wait(2.0)
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", blocked_report)
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args: executions.append(args) or SimpleNamespace(status="repaired"),
    )
    monkeypatch.setattr(sidecar_server.shutil, "which", lambda _command: None)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_fast_confirm",
                    "chat_type": "private",
                }
            ),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        repair = operations_button(feishu_client.sent[0][1], "安全修复")
        confirmation = await test_client.post(
            "/card/actions",
            json=operations_action_payload(repair, chat_id="oc_private"),
        )
        confirm = operations_button((await confirmation.json())["card"], "确认修复")
        started = time.monotonic()
        first = await asyncio.wait_for(
            test_client.post(
                "/card/actions",
                json=operations_action_payload(confirm, chat_id="oc_private"),
            ),
            timeout=0.5,
        )
        elapsed = time.monotonic() - started
        first_body = await first.json()
        await asyncio.wait_for(asyncio.to_thread(fresh_started.wait), timeout=1.0)
        repeated = await test_client.post(
            "/card/actions",
            json=operations_action_payload(confirm, chat_id="oc_private"),
        )
        repeated_body = await repeated.json()
        release_fresh.set()
        await wait_for_card_update(feishu_client, "安全修复已完成")
    finally:
        release_fresh.set()
        await test_client.close()

    assert elapsed < 0.5
    assert "正在安全修复" in str(first_body["card"])
    assert first_body["operation_id"] == repeated_body["operation_id"]
    assert calls == ["", "fallback_default", "fallback_default"]
    assert len(executions) == 1
    assert len(feishu_client.sent) == 1
    assert len(feishu_client.updated) == 4
    assert "确认安全修复" in str(feishu_client.updated[0][1])
    assert any("正在安全修复" in str(card) for _message_id, card in feishu_client.updated)
    assert "安全修复已完成" in str(feishu_client.updated[-1][1])
    assert not any(
        record.state == "executing"
        for record in app[sidecar_server.OPERATIONS_STORE_KEY]._records.values()
    )


async def test_repair_confirmation_step_returns_from_snapshot_without_diagnosis(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    diagnostic_started = threading.Event()
    diagnostic_release = threading.Event()

    def blocked_report(*args, **kwargs):
        if args[3] == "callback":
            diagnostic_started.set()
            diagnostic_release.wait(2.0)
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "OPERATIONS_DIAGNOSTIC_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", blocked_report)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_timeout_action",
                    "chat_type": "private",
                }
            ),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        repair = operations_button(feishu_client.sent[0][1], "安全修复")
        started = time.monotonic()
        response = await asyncio.wait_for(
            test_client.post(
                "/card/actions",
                json=operations_action_payload(repair, chat_id="oc_private"),
            ),
            timeout=0.5,
        )
        elapsed = time.monotonic() - started
        body = await response.json()
        await asyncio.sleep(0.05)
        record = app[sidecar_server.OPERATIONS_STORE_KEY]._records[
            json.loads(
                base64.urlsafe_b64decode(
                    str(repair["token"]).split(".", 1)[0]
                    + "=" * (-len(str(repair["token"]).split(".", 1)[0]) % 4)
                )
            )["operation_id"]
        ]
    finally:
        diagnostic_release.set()
        await test_client.close()

    assert response.status == 200
    assert elapsed < 0.5
    assert not diagnostic_started.is_set()
    assert "确认安全修复" in str(body["card"])
    assert record.state == "confirm_repair"


async def test_late_repair_worker_cannot_reclaim_delivery_or_inflight_capacity():
    feishu_client = FakeFeishuClient()
    report = operations_report()
    app = create_app(feishu_client)
    store = OperationStore(secret=b"store", max_records=2)
    active = store.create(
        chat_id="oc_active",
        profile_id="default",
        report_fingerprint="active-report",
        recovery_fingerprint="active-recovery",
        group=False,
        transport_secret=b"a" * 32,
    )
    active.state = "executing"
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=report.fingerprint,
        recovery_fingerprint=report.recovery_fingerprint,
        group=False,
        transport_secret=b"b" * 32,
    )
    operation.report = report
    operation.state = "executing"
    app[sidecar_server.OPERATIONS_STORE_KEY] = store
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )

    await sidecar_server._finish_operations_repair(
        app,
        operation,
        report,
        state="repaired",
        result={"message": "已完成安全修复并重新检测。"},
    )
    await sidecar_server._finish_operations_repair(
        app,
        operation,
        report,
        state="repaired",
        result={"message": "late worker"},
    )

    assert len(feishu_client.updated) == 1
    assert len(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]) == 1
    assert operation.operation_id not in app[sidecar_server.OPERATIONS_DELIVERIES_KEY]
    assert set(app[sidecar_server.OPERATIONS_STORE_KEY]._records) != {
        active.operation_id,
        operation.operation_id,
    }
    assert all(
        record.state != "executing" or record.operation_id == active.operation_id
        for record in app[sidecar_server.OPERATIONS_STORE_KEY]._records.values()
    )


async def test_delayed_old_confirmation_returns_current_successor_without_delivery_reversal():
    feishu_client = FakeFeishuClient()
    report = operations_report()
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=report.fingerprint,
        recovery_fingerprint=report.recovery_fingerprint,
        group=False,
        transport_secret=b"c" * 32,
    )
    operation.report = report
    operation.state = "executing"
    old_value = {
        "hfc_action": "operations.select",
        "operation_action": "confirm_repair",
        "token": store.token(operation, "confirm_repair"),
        "profile_scope": store.scope_fingerprint(operation),
    }
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )
    await sidecar_server._finish_operations_repair(
        app,
        operation,
        report,
        state="repaired",
        result={"message": "已完成安全修复并重新检测。", "restart_available": False},
    )
    successor_id = next(iter(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]))
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                old_value,
                chat_id="oc_private",
                transport_secret=b"c" * 32,
            ),
        )
        body = await response.json()
    finally:
        await test_client.close()

    assert body["ok"] is True, body
    assert body["operation_id"] == successor_id, body
    assert set(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]) == {successor_id}
    assert "安全修复已完成" in str(body["card"])


async def test_operations_patch_failure_is_recorded_on_the_completed_card():
    feishu_client = FakeFeishuClient()
    feishu_client.update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS
    report = operations_report()
    app = create_app(feishu_client)
    operation = app[sidecar_server.OPERATIONS_STORE_KEY].create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=report.fingerprint,
        recovery_fingerprint=report.recovery_fingerprint,
        group=False,
        transport_secret=b"p" * 32,
    )
    operation.report = report
    operation.state = "failed"
    operation.result = {"message": "诊断暂时不可用，请稍后重新检测。"}
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )

    updated = await sidecar_server._publish_operations_card(app, report, operation)

    assert updated is False
    assert operation.result["delivery_error"] == "card update unavailable"
    assert app[DIAGNOSTICS_KEY]["last_update_error"] == "bot_id= RuntimeError"
    assert feishu_client.updated == []


async def test_stale_operations_publisher_republishes_the_current_delivery_owner():
    feishu_client = ControlledOperationsUpdateClient()
    old_report = operations_report(report_marker="old-owner")
    current_report = operations_report(report_marker="current-owner")
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    old_operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=old_report.fingerprint,
        recovery_fingerprint=old_report.recovery_fingerprint,
        group=False,
        transport_secret=b"o" * 32,
    )
    old_operation.report = old_report
    old_operation.state = "failed"
    old_operation.result = {"message": "old publisher"}
    sidecar_server._store_operation_delivery(
        app,
        old_operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )

    old_publisher = asyncio.create_task(
        sidecar_server._publish_operations_card(app, old_report, old_operation)
    )
    await asyncio.wait_for(feishu_client.update_started.wait(), timeout=1.0)
    current_operation = sidecar_server._successor_operation(
        app,
        old_operation,
        current_report,
        state="failed",
        result={"message": "current publisher"},
    )
    current_publisher = asyncio.create_task(
        sidecar_server._publish_operations_card(
            app, current_report, current_operation
        )
    )
    feishu_client.release_update.set()
    await asyncio.gather(old_publisher, current_publisher)

    assert "current publisher" in str(feishu_client.updated[-1][1])
    assert len(feishu_client.updated) == 3
    assert set(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]) == {
        current_operation.operation_id
    }
    assert app[sidecar_server.OPERATIONS_PUBLISH_LOCKS_KEY] == {}


async def test_mutation_cleanup_snapshots_three_futures_and_waits_for_running_workers():
    app = create_app(FakeFeishuClient())
    started = threading.Event()
    release = threading.Event()
    running = [0]

    def blocked_mutation():
        running[0] += 1
        if running[0] == 2:
            started.set()
        release.wait(2.0)

    tasks = [
        asyncio.create_task(sidecar_server._run_operations_mutation(app, blocked_mutation))
        for _ in range(3)
    ]
    for task in tasks:
        sidecar_server._track_operations_task(app, task)
    await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1.0)
    cleanup = asyncio.create_task(sidecar_server._stop_operations_diagnostics(app))
    await asyncio.sleep(0.02)
    assert cleanup.done() is False
    release.set()
    await cleanup
    await asyncio.gather(*tasks, return_exceptions=True)

    assert running[0] == 2
    assert app[sidecar_server.OPERATIONS_MUTATION_FUTURES_KEY] == set()
    with pytest.raises(RuntimeError):
        app[sidecar_server.OPERATIONS_MUTATION_EXECUTOR_KEY].submit(lambda: None)


@pytest.mark.parametrize("state", ("preparing", "executing", "restarting"))
async def test_inflight_recheck_returns_current_card_after_all_patch_attempts_fail(state):
    feishu_client = FakeFeishuClient()
    feishu_client.update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS
    report = operations_report()
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=report.fingerprint,
        recovery_fingerprint=report.recovery_fingerprint,
        group=False,
        transport_secret=b"p" * 32,
    )
    operation.report = report
    operation.state = state
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )
    assert await sidecar_server._publish_operations_card(app, report, operation) is False
    card = sidecar_server._render_operations_for_app(app, report, operation)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                operations_button(card, "重新检测"),
                chat_id="oc_private",
                transport_secret=b"p" * 32,
            ),
        )
        body = await response.json()
    finally:
        await test_client.close()

    assert body["ok"] is True
    assert body["operation_id"] == operation.operation_id
    assert operation.state == state
    assert set(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]) == {operation.operation_id}
    assert "正在" in str(body["card"])


async def test_repair_timeout_renders_failed_diagnostic_successor(monkeypatch):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    diagnostic_started = threading.Event()
    diagnostic_release = threading.Event()

    def blocked_report(*args, **kwargs):
        if args[3] == "fallback_default":
            diagnostic_started.set()
            diagnostic_release.wait(0.5)
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "OPERATIONS_DIAGNOSTIC_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", blocked_report)
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args, **kwargs: SimpleNamespace(status="repaired"),
    )
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=report.fingerprint,
        recovery_fingerprint=report.recovery_fingerprint,
        group=False,
        transport_secret=b"s" * 32,
    )
    operation.report = report
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        repair = operations_button(
            sidecar_server._render_operations_for_app(app, report, operation),
            "安全修复",
        )
        confirmation = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                repair, chat_id="oc_private", transport_secret=b"s" * 32
            ),
        )
        confirm_repair = operations_button((await confirmation.json())["card"], "确认修复")
        response = await asyncio.wait_for(
            test_client.post(
                "/card/actions",
                json=operations_action_payload(
                    confirm_repair,
                    chat_id="oc_private",
                    transport_secret=b"s" * 32,
                ),
            ),
            timeout=0.5,
        )
        body = await response.json()
        await asyncio.wait_for(asyncio.to_thread(diagnostic_started.wait), timeout=1.0)
        await wait_for_card_update(feishu_client, "诊断暂时不可用")
        successor = next(
            record
            for record in app[sidecar_server.OPERATIONS_STORE_KEY]._records.values()
            if record.state == "failed"
        )
        diagnostic_release.set()
    finally:
        diagnostic_release.set()
        await test_client.close()

    assert response.status == 200
    assert "正在安全修复" in str(body["card"])
    assert successor.state == "failed"
    assert operations_button(feishu_client.updated[-1][1], "重新检测")


async def test_restart_timeout_updates_recheckable_failure_without_restarting(monkeypatch):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    diagnostic_started = threading.Event()
    diagnostic_release = threading.Event()

    def blocked_report(*args, **kwargs):
        diagnostic_started.set()
        diagnostic_release.wait(2.0)
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "OPERATIONS_DIAGNOSTIC_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", blocked_report)
    restart_calls = []
    monkeypatch.setattr(
        sidecar_server.subprocess,
        "run",
        lambda *args, **kwargs: restart_calls.append((args, kwargs))
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=report.fingerprint,
        recovery_fingerprint=report.recovery_fingerprint,
        group=False,
        transport_secret=b"s" * 32,
    )
    operation.state = "restarting"
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )
    try:
        await sidecar_server._run_operations_restart(app, operation)
        await asyncio.wait_for(asyncio.to_thread(diagnostic_started.wait), timeout=1.0)
        successor = next(
            record
            for record in store._records.values()
            if record.operation_id != operation.operation_id
        )
    finally:
        diagnostic_release.set()
        await sidecar_server._stop_operations_diagnostics(app)

    assert successor.state == "restart_failed"
    assert restart_calls == []
    assert "重启前的诊断暂时不可用" in str(successor.result)
    assert len(feishu_client.updated) == 1
    assert operations_button(feishu_client.updated[0][1], "重新检测")


async def test_cleanup_cancels_scheduled_restart_before_diagnostic_executor_shutdown(
    monkeypatch,
    caplog,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    restart_calls = []
    diagnostic_started = threading.Event()
    diagnostic_release = threading.Event()

    def blocked_report(*args, **kwargs):
        diagnostic_started.set()
        diagnostic_release.wait(2.0)
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "RESTART_CALLBACK_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", blocked_report)
    monkeypatch.setattr(
        sidecar_server.subprocess,
        "run",
        lambda *args, **kwargs: restart_calls.append((args, kwargs))
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=report.fingerprint,
        recovery_fingerprint=report.recovery_fingerprint,
        group=False,
        transport_secret=b"s" * 32,
    )
    operation.state = "restarting"

    sidecar_server._schedule_operations_restart(app, operation)
    try:
        await asyncio.wait_for(asyncio.to_thread(diagnostic_started.wait), timeout=1.0)
        assert len(app[sidecar_server.OPERATIONS_DIAGNOSTIC_TASKS_KEY]) == 1

        await sidecar_server._stop_operations_diagnostics(app)
        await _REAL_ASYNCIO_SLEEP(0.05)
    finally:
        diagnostic_release.set()

    assert not app[sidecar_server.OPERATIONS_DIAGNOSTIC_TASKS_KEY]
    assert restart_calls == []
    assert "cannot schedule new futures after shutdown" not in caplog.text


async def test_hfc_doctor_shutdown_cancels_tracked_diagnosis(monkeypatch):
    feishu_client = FakeFeishuClient()
    started = asyncio.Event()

    async def blocked_report(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(sidecar_server, "_build_operations_report", blocked_report)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    await test_client.post(
        "/commands",
        json=signed_operations_command(
            {
                "command": "doctor",
                "chat_id": "oc_private",
                "message_id": "om_shutdown_doctor",
                "chat_type": "private",
            }
        ),
    )
    await started.wait()
    tasks = app[sidecar_server.OPERATIONS_DIAGNOSTIC_TASKS_KEY]
    assert len(tasks) == 1

    await test_client.close()

    assert not tasks


async def test_http_operations_reject_forged_operator_without_valid_adapter_proof(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (
            report,
            SimpleNamespace(root=Path("/private/hermes")),
        ),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_group",
                "message_id": "om_doctor",
                "chat_type": "group",
                "operator": "ou_owner",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        repair = operations_button(feishu_client.sent[0][1], "安全修复")
        forged = operations_action_payload(repair, operator="ou_owner")
        forged["adapter_transport_proof"]["signature"] = "0" * 64

        response = await test_client.post("/card/actions", json=forged)
        body = await response.json()
    finally:
        await test_client.close()

    assert response.status == 403
    assert body == {"ok": False, "error": "operation rejected"}


async def test_doctor_rejects_caller_chosen_secret_and_forged_operator(monkeypatch):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (
            report,
            SimpleNamespace(root=Path("/private/hermes")),
        ),
    )
    app = create_app(
        feishu_client,
        operations_transport_root_secret=b"r" * 32,
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/commands",
            json={
                "command": "doctor",
                "chat_id": "oc_victim",
                "message_id": "om_forged",
                "profile_id": "work",
                "chat_type": "group",
                "operator": "ou_victim",
                "adapter_transport_secret": "attacker-chosen-secret",
            },
        )
        body = await response.json()
    finally:
        await test_client.close()

    assert response.status == 403
    assert body == {"ok": False, "error": "command authentication rejected"}
    assert feishu_client.sent == []


async def test_hfc_doctor_returns_overload_without_evicting_inflight(monkeypatch):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (
            report,
            SimpleNamespace(root=Path("/private/hermes")),
        ),
    )
    app = create_app(feishu_client)
    store = OperationStore(secret=b"store", max_records=1)
    active = store.create(
        chat_id="oc_active",
        profile_id="default",
        report_fingerprint="active-report",
        recovery_fingerprint="active-recovery",
        group=False,
        transport_secret=TRANSPORT_SECRET.encode("utf-8"),
    )
    active.state = "executing"
    app[sidecar_server.OPERATIONS_STORE_KEY] = store
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_new",
                "message_id": "om_new",
                "chat_type": "private",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        body = await response.json()
    finally:
        await test_client.close()

    assert response.status == 503
    assert body == {"ok": False, "error": "operations overloaded"}
    assert store.complete(
        active.operation_id,
        expected_state="executing",
        state="repaired",
        result={},
    ).state == "repaired"
    assert feishu_client.sent == []


async def test_group_operations_first_claim_missing_identity_and_read_only_matrix(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    detection = SimpleNamespace(root=Path("/private/hermes"))
    calls = []
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, detection),
    )
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args: calls.append(args) or SimpleNamespace(status="repaired"),
    )
    monkeypatch.setattr(sidecar_server.shutil, "which", lambda command: None)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_group",
                "message_id": "om_doctor",
                "chat_type": "group",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        card = feishu_client.sent[0][1]

        details = operations_button(card, "查看诊断")
        read_only = await test_client.post(
            "/card/actions",
            json=operations_action_payload(details, operator="ou_reader"),
        )
        assert (await read_only.json())["ok"] is True

        repair = operations_button(card, "安全修复")
        missing_identity = await test_client.post(
            "/card/actions",
            json=operations_action_payload(repair, operator=""),
        )
        assert (await missing_identity.json())["ok"] is False

        claimed = await test_client.post(
            "/card/actions",
            json=operations_action_payload(repair, operator="ou_claimant"),
        )
        confirm = operations_button((await claimed.json())["card"], "确认修复")
        changed_operator = await test_client.post(
            "/card/actions",
            json=operations_action_payload(confirm, operator="ou_other"),
        )
        assert (await changed_operator.json())["ok"] is False

        completed = await test_client.post(
            "/card/actions",
            json=operations_action_payload(confirm, operator="ou_claimant"),
        )
        completed_body = await completed.json()
        await _wait_until(lambda: len(calls) == 1)
    finally:
        await test_client.close()

    assert completed_body["ok"] is True
    assert len(calls) == 1
    assert calls[0][1] == "recovery-a"


async def test_concurrent_recheck_returns_one_successor_and_moves_delivery_once(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (
            report,
            SimpleNamespace(root=Path("/private/hermes")),
        ),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_private",
                "message_id": "om_doctor",
                "chat_type": "private",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        recheck = operations_button(feishu_client.sent[0][1], "重新检测")
        original_id = next(iter(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]))
        payload = operations_action_payload(
            recheck,
            chat_id="oc_private",
            operator="ou_owner",
        )
        responses = await asyncio.gather(
            *[
                test_client.post("/card/actions", json=payload)
                for _ in range(8)
            ]
        )
        bodies = [await response.json() for response in responses]
    finally:
        await test_client.close()

    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    current = store.current_successor(original_id)
    deliveries = app[sidecar_server.OPERATIONS_DELIVERIES_KEY]
    assert current is not None
    assert all(
        store.current_successor(body["operation_id"]) is current for body in bodies
    )
    assert original_id not in deliveries
    assert set(deliveries) == {current.operation_id}
    assert all(body["ok"] is True for body in bodies)


async def test_changed_recheck_creates_one_fresh_successor_and_moves_delivery_once(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    reports = [
        operations_report(report_marker="report-a", recovery_fingerprint="recovery-a"),
        operations_report(report_marker="report-b", recovery_fingerprint="recovery-b"),
    ]
    detection = SimpleNamespace(root=Path("/private/hermes"))
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (reports.pop(0) if reports else operations_report(
            report_marker="report-b", recovery_fingerprint="recovery-b"
        ), detection),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_private",
                "message_id": "om_doctor",
                "chat_type": "private",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        recheck = operations_button(feishu_client.sent[0][1], "重新检测")
        original_id = next(iter(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]))
        payload = operations_action_payload(
            recheck, chat_id="oc_private", operator="ou_owner"
        )
        first = await test_client.post("/card/actions", json=payload)
        second = await test_client.post("/card/actions", json=payload)
        first_body = await first.json()
        second_body = await second.json()
    finally:
        await test_client.close()

    assert first_body["ok"] is True
    assert second_body["ok"] is True
    assert first_body["operation_id"] != original_id
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    successor = store.current_successor(first_body["operation_id"])
    assert successor is not None
    assert store.current_successor(second_body["operation_id"]) is successor
    assert successor.recovery_fingerprint == "recovery-b"
    assert original_id not in app[sidecar_server.OPERATIONS_DELIVERIES_KEY]
    assert set(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]) == {
        successor.operation_id
    }


async def test_full_capacity_repeated_recheck_returns_same_successor_and_moves_delivery_once(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (
            report,
            SimpleNamespace(root=Path("/private/hermes")),
        ),
    )
    app = create_app(feishu_client)
    store = OperationStore(secret=b"store", max_records=2)
    active = store.create(
        chat_id="oc_active",
        profile_id="default",
        report_fingerprint="active-report",
        recovery_fingerprint="active-recovery",
        group=False,
        transport_secret=TRANSPORT_SECRET.encode("utf-8"),
    )
    active.state = "executing"
    app[sidecar_server.OPERATIONS_STORE_KEY] = store
    transfer_calls = []
    original_transfer = sidecar_server._transfer_operation_delivery

    def track_delivery_transfer(*args):
        transfer_calls.append(args[1:])
        return original_transfer(*args)

    monkeypatch.setattr(
        sidecar_server, "_transfer_operation_delivery", track_delivery_transfer
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_private",
                "message_id": "om_doctor",
                "chat_type": "private",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        recheck = operations_button(feishu_client.sent[0][1], "重新检测")
        original_id = next(iter(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]))
        payload = operations_action_payload(
            recheck,
            chat_id="oc_private",
            operator="ou_owner",
        )
        first_response = await test_client.post("/card/actions", json=payload)
        first_body = await first_response.json()
        repeated_response = await test_client.post("/card/actions", json=payload)
        repeated_body = await repeated_response.json()
    finally:
        await test_client.close()

    assert first_body["ok"] is True
    assert repeated_body["ok"] is True
    current = store.current_successor(first_body["operation_id"])
    assert current is not None
    assert store.current_successor(repeated_body["operation_id"]) is current
    assert "正在重新检测" in str(first_body["card"])
    assert any(
        marker in str(repeated_body["card"])
        for marker in ("正在重新检测", "诊断摘要")
    )
    assert transfer_calls == [
        (original_id, first_body["operation_id"]),
        (first_body["operation_id"], current.operation_id),
    ]
    assert original_id not in app[sidecar_server.OPERATIONS_DELIVERIES_KEY]
    assert set(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]) == {
        current.operation_id
    }


async def test_private_repair_is_exactly_once_under_concurrent_confirmation(monkeypatch):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    detection = SimpleNamespace(root=Path("/private/hermes"))
    calls = []

    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, detection),
    )

    def fake_execute(current_detection, expected_fingerprint=None):
        calls.append((current_detection, expected_fingerprint))
        return SimpleNamespace(status="repaired", message="Verified recovery completed.")

    monkeypatch.setattr(sidecar_server, "execute_recovery", fake_execute)
    monkeypatch.setattr(sidecar_server.shutil, "which", lambda command: None)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_private",
                "message_id": "om_doctor",
                "chat_type": "private",
                "operator": "ou_first",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        repair = operations_button(feishu_client.sent[0][1], "安全修复")
        first = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                repair, chat_id="oc_private", operator="ou_first"
            ),
        )
        confirm = operations_button((await first.json())["card"], "确认修复")
        responses = await asyncio.gather(
            *[
                test_client.post(
                    "/card/actions",
                    json=operations_action_payload(
                        confirm, chat_id="oc_private", operator=f"ou_{index}"
                    ),
                )
                for index in range(8)
            ]
        )
        bodies = [await response.json() for response in responses]
        await wait_for_card_update(feishu_client, "安全修复已完成")
    finally:
        await test_client.close()

    assert len(calls) == 1
    assert calls[0][1] == "recovery-a"
    assert app[METRICS_KEY].recovery_attempts == 1
    assert app[METRICS_KEY].recovery_successes == 1
    assert all(response.status == 200 for response in responses)
    assert all("正在安全修复" in str(body["card"]) for body in bodies)
    assert "安全修复已完成" in str(feishu_client.updated[-1][1])


async def test_changed_recovery_fingerprint_refuses_confirm_without_execution(monkeypatch):
    feishu_client = FakeFeishuClient()
    current = [operations_report(recovery_fingerprint="recovery-a")]
    detection = SimpleNamespace(root=Path("/private/hermes"))
    calls = []
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (current[0], detection),
    )
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_group",
                "message_id": "om_doctor",
                "chat_type": "group",
                "operator": "ou_owner",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        repair = operations_button(feishu_client.sent[0][1], "安全修复")
        first = await test_client.post(
            "/card/actions", json=operations_action_payload(repair)
        )
        confirm = operations_button((await first.json())["card"], "确认修复")
        current[0] = operations_report(
            report_marker="report-b",
            recovery_fingerprint="recovery-b",
        )
        rejected = await test_client.post(
            "/card/actions", json=operations_action_payload(confirm)
        )
        body = await rejected.json()
        await wait_for_card_update(feishu_client, "诊断状态已变化")
    finally:
        await test_client.close()

    assert rejected.status == 200
    assert body["ok"] is True
    assert "正在安全修复" in str(body["card"])
    assert operations_button(feishu_client.updated[-1][1], "重新检测")
    assert calls == []


async def test_repair_refuses_changed_report_fingerprint_before_mutation(monkeypatch):
    feishu_client = FakeFeishuClient()
    claimed = operations_report(report_marker="report-a", recovery_fingerprint="recovery-a")
    fresh = operations_report(recovery_fingerprint="recovery-a", executable=False)
    assert fresh.fingerprint != claimed.fingerprint
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=claimed.fingerprint,
        recovery_fingerprint=claimed.recovery_fingerprint,
        group=False,
        transport_secret=b"r" * 32,
    )
    operation.report = claimed
    operation.state = "executing"
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )
    calls = []

    async def fresh_report(*_args, **_kwargs):
        return fresh, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_bounded_operations_report", fresh_report)
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args: calls.append(args) or SimpleNamespace(status="repaired"),
    )

    await sidecar_server._run_operations_repair(app, operation)

    successor = next(iter(store._records.values()))
    assert calls == []
    assert successor.state == "failed"
    assert "诊断状态已变化" in str(successor.result)


async def test_successful_repair_builds_successor_from_post_mutation_report(monkeypatch):
    feishu_client = FakeFeishuClient()
    claimed = operations_report(recovery_fingerprint="recovery-a")
    repaired = operations_report(recovery_fingerprint="recovery-clean", executable=False)
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=claimed.fingerprint,
        recovery_fingerprint=claimed.recovery_fingerprint,
        group=False,
        transport_secret=b"r" * 32,
    )
    operation.report = claimed
    operation.state = "executing"
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )
    calls = []

    async def reports(_app, *, profile_id, profile_source, **_kwargs):
        calls.append(profile_source)
        report = claimed if len(calls) == 1 else repaired
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_bounded_operations_report", reports)
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args: SimpleNamespace(status="repaired"),
    )
    monkeypatch.setattr(sidecar_server.shutil, "which", lambda _command: "/usr/bin/hermes")

    await sidecar_server._run_operations_repair(app, operation)

    successor = next(iter(store._records.values()))
    assert calls == ["fallback_default", "fallback_default"]
    assert successor.report is repaired
    assert successor.report_fingerprint == repaired.fingerprint
    assert successor.recovery_fingerprint == repaired.recovery_fingerprint
    assert successor.result["restart_available"] is True


@pytest.mark.parametrize("profile_source", ["fallback_default", "sanitized_locals", "sanitized_hermes_home"])
async def test_background_operations_reuse_snapshot_profile_source_for_fingerprint(monkeypatch, profile_source):
    feishu_client = FakeFeishuClient()
    claimed = operations_report(profile_source=profile_source)
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private", profile_id="default",
        report_fingerprint=claimed.fingerprint,
        recovery_fingerprint=claimed.recovery_fingerprint,
        group=False, transport_secret=b"r" * 32,
    )
    operation.report = claimed
    operation.state = "executing"
    sidecar_server._store_operation_delivery(
        app, operation.operation_id, {"message_id": "feishu-message", "bot_id": None}
    )
    sources = []
    executed = []

    async def reports(_app, *, profile_source, **_kwargs):
        sources.append(profile_source)
        return claimed, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_bounded_operations_report", reports)
    monkeypatch.setattr(
        sidecar_server, "execute_recovery",
        lambda *args: executed.append(args) or SimpleNamespace(status="repaired"),
    )
    monkeypatch.setattr(sidecar_server.shutil, "which", lambda _command: None)

    await sidecar_server._run_operations_repair(app, operation)

    recheck = store.create(
        chat_id="oc_private", profile_id="default",
        report_fingerprint=claimed.fingerprint,
        recovery_fingerprint=claimed.recovery_fingerprint,
        group=False, transport_secret=b"s" * 32,
    )
    recheck.report = claimed
    recheck.state = "preparing"
    sidecar_server._store_operation_delivery(
        app, recheck.operation_id, {"message_id": "recheck-message", "bot_id": None}
    )
    await sidecar_server._run_operations_recheck(app, recheck)

    restart = store.create(
        chat_id="oc_private", profile_id="default",
        report_fingerprint=claimed.fingerprint,
        recovery_fingerprint=claimed.recovery_fingerprint,
        group=False, transport_secret=b"t" * 32,
    )
    restart.report = claimed
    restart.state = "restarting"
    sidecar_server._store_operation_delivery(
        app, restart.operation_id, {"message_id": "restart-message", "bot_id": None}
    )
    await sidecar_server._run_operations_restart(app, restart)

    assert sources == [profile_source] * 4
    assert len(executed) == 1
    assert next(iter(store._records.values())).state == "repaired"


async def test_repair_rejects_actual_profile_source_change(monkeypatch):
    feishu_client = FakeFeishuClient()
    claimed = operations_report(profile_source="fallback_default")
    changed = operations_report(profile_source="env")
    assert changed.fingerprint != claimed.fingerprint
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private", profile_id="default",
        report_fingerprint=claimed.fingerprint,
        recovery_fingerprint=claimed.recovery_fingerprint,
        group=False, transport_secret=b"u" * 32,
    )
    operation.report = claimed
    operation.state = "executing"
    sidecar_server._store_operation_delivery(
        app, operation.operation_id, {"message_id": "source-message", "bot_id": None}
    )
    executed = []

    async def reports(*_args, **_kwargs):
        return changed, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_bounded_operations_report", reports)
    monkeypatch.setattr(sidecar_server, "execute_recovery", lambda *args: executed.append(args))

    await sidecar_server._run_operations_repair(app, operation)

    assert executed == []
    assert next(iter(store._records.values())).state == "failed"


async def test_successful_repair_without_post_diagnosis_keeps_recheckable_fallback(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    claimed = operations_report(recovery_fingerprint="recovery-a")
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=claimed.fingerprint,
        recovery_fingerprint=claimed.recovery_fingerprint,
        group=False,
        transport_secret=b"r" * 32,
    )
    operation.report = claimed
    operation.state = "executing"
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )
    calls = []

    async def reports(_app, *, profile_id, profile_source, **_kwargs):
        calls.append(profile_source)
        if len(calls) > 1:
            raise asyncio.TimeoutError
        return claimed, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_bounded_operations_report", reports)
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args: SimpleNamespace(status="repaired"),
    )
    monkeypatch.setattr(sidecar_server.shutil, "which", lambda _command: "/usr/bin/hermes")

    await sidecar_server._run_operations_repair(app, operation)

    successor = next(iter(store._records.values()))
    assert calls == ["fallback_default", "fallback_default"]
    assert successor.state == "repaired"
    assert successor.report.status == "error"
    assert successor.result["restart_available"] is False
    assert "重新检测暂时不可用" in str(successor.result)
    assert operations_button(feishu_client.updated[-1][1], "重新检测")
    assert "重启 Gateway" not in str(feishu_client.updated[-1][1])


async def test_repair_rejects_synthetic_post_repair_failure_report(monkeypatch):
    feishu_client = FakeFeishuClient()
    claimed = operations_report(recovery_fingerprint="recovery-a")
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=claimed.fingerprint,
        recovery_fingerprint=claimed.recovery_fingerprint,
        group=False,
        transport_secret=b"r" * 32,
    )
    operation.report = claimed
    operation.state = "executing"
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )
    calls = []

    def swallowed_build(*args):
        profile_source = args[3]
        calls.append(profile_source)
        report = (
            sidecar_server._failed_operations_report("default")
            if len(calls) > 1
            else claimed
        )
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(
        sidecar_server, "_build_operations_report_sync", swallowed_build
    )
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args: SimpleNamespace(status="repaired"),
    )
    monkeypatch.setattr(sidecar_server.shutil, "which", lambda _command: "/usr/bin/hermes")

    await sidecar_server._run_operations_repair(app, operation)

    successor = next(iter(store._records.values()))
    assert calls == ["fallback_default", "fallback_default"]
    assert successor.report.status == "error"
    assert successor.result["restart_available"] is False
    assert "重新检测暂时不可用" in str(successor.result)
    assert operations_button(feishu_client.updated[-1][1], "重新检测")
    assert "重启 Gateway" not in str(feishu_client.updated[-1][1])


async def test_restart_refuses_changed_report_fingerprint_before_mutation(monkeypatch):
    feishu_client = FakeFeishuClient()
    claimed = operations_report(report_marker="report-a", recovery_fingerprint="recovery-a")
    fresh = operations_report(recovery_fingerprint="recovery-a", executable=False)
    assert fresh.fingerprint != claimed.fingerprint
    app = create_app(feishu_client)
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation = store.create(
        chat_id="oc_private",
        profile_id="default",
        report_fingerprint=claimed.fingerprint,
        recovery_fingerprint=claimed.recovery_fingerprint,
        group=False,
        transport_secret=b"r" * 32,
    )
    operation.report = claimed
    operation.state = "restarting"
    sidecar_server._store_operation_delivery(
        app,
        operation.operation_id,
        {"message_id": "feishu-message", "bot_id": None},
    )
    calls = []

    async def fresh_report(*_args, **_kwargs):
        return fresh, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_bounded_operations_report", fresh_report)
    monkeypatch.setattr(sidecar_server.shutil, "which", lambda _command: "/usr/bin/hermes")
    monkeypatch.setattr(sidecar_server, "RESTART_CALLBACK_GRACE_SECONDS", 0)
    monkeypatch.setattr(
        sidecar_server.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    await sidecar_server._run_operations_restart(app, operation)

    successor = next(iter(store._records.values()))
    assert calls == []
    assert successor.state == "restart_failed"
    assert "诊断状态已变化" in str(successor.result)


async def test_http_operations_reject_tampered_and_profile_mismatched_tokens(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, SimpleNamespace(root=Path("/private/hermes"))),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_private",
                "message_id": "om_doctor",
                "profile_id": "default",
                "chat_type": "private",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        details = operations_button(feishu_client.sent[0][1], "查看诊断")
        tampered = dict(details)
        tampered["token"] = details["token"][:-1] + (
            "0" if details["token"][-1] != "0" else "1"
        )
        tampered_response = await test_client.post(
            "/card/actions",
            json=operations_action_payload(tampered, chat_id="oc_private"),
        )
        mismatched_response = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                details,
                chat_id="oc_private",
                profile_id="sales",
                proof_profile_id="default",
            ),
        )
        chat_mismatched_response = await test_client.post(
            "/card/actions",
            json=operations_action_payload(details, chat_id="oc_other"),
        )
        tampered_body = await tampered_response.json()
        mismatched_body = await mismatched_response.json()
        chat_mismatched_body = await chat_mismatched_response.json()
    finally:
        await test_client.close()

    assert tampered_response.status == 200
    assert tampered_body == {
        "ok": False,
        "error": "operation rejected",
    }
    assert mismatched_response.status == 200
    assert mismatched_body["ok"] is True
    assert "card" in mismatched_body
    assert chat_mismatched_body == {
        "ok": False,
        "error": "operation rejected",
    }


async def test_http_operations_reject_unsigned_forged_operator(monkeypatch):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, SimpleNamespace(root=Path("/private/hermes"))),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_group",
                "message_id": "om_doctor",
                "chat_type": "group",
                "operator": "ou_owner",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        repair = operations_button(feishu_client.sent[0][1], "安全修复")

        forged_payload = operations_action_payload(repair, operator="ou_owner")
        forged_payload.pop("adapter_transport_proof")
        forged = await test_client.post(
            "/card/actions",
            json=forged_payload,
        )
        body = await forged.json()
    finally:
        await test_client.close()

    assert forged.status == 403
    assert body == {"ok": False, "error": "operation rejected"}


async def test_http_stale_operation_renders_recheck_only_expired_card(monkeypatch):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, SimpleNamespace(root=Path("/private/hermes"))),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_private",
                "message_id": "om_doctor",
                "profile_id": "default",
                "chat_type": "private",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        card = feishu_client.sent[0][1]
        repair = operations_button(card, "安全修复")
        store = app[sidecar_server.OPERATIONS_STORE_KEY]
        _claims, record = store.inspect(
            repair["token"],
            callback_chat_id="oc_private",
            callback_profile_scope=repair["profile_scope"],
            allow_expired=True,
        )
        record.expires_at = 0.0

        stale_response = await test_client.post(
            "/card/actions",
            json=operations_action_payload(repair, chat_id="oc_private"),
        )
        stale_body = await stale_response.json()
    finally:
        await test_client.close()

    assert stale_response.status == 200
    assert stale_body["ok"] is False
    assert "诊断已过期" in str(stale_body["card"])
    recheck = operations_button(stale_body["card"], "重新检测")
    assert recheck["token"] != repair["token"]
    assert "安全修复" not in str(stale_body["card"])
    assert "oc_private" not in str(stale_body)
    assert "profile_id" not in str(stale_body)


def test_operation_delivery_index_is_bounded():
    app = create_app(FakeFeishuClient())

    for index in range(sidecar_server.MAX_OPERATION_DELIVERIES + 5):
        sidecar_server._store_operation_delivery(
            app,
            f"operation-{index}",
            {"message_id": f"message-{index}"},
        )

    assert len(app[sidecar_server.OPERATIONS_DELIVERIES_KEY]) == 200
    assert "operation-0" not in app[sidecar_server.OPERATIONS_DELIVERIES_KEY]


async def test_confirmed_restart_returns_callback_before_sanitized_background_result(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    detection = SimpleNamespace(root=Path("/private/hermes"))
    restart_started = threading.Event()
    restart_release = threading.Event()
    restart_calls = []
    ordering = []
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, detection),
    )
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args, **kwargs: SimpleNamespace(status="repaired"),
    )
    monkeypatch.setattr(
        sidecar_server.shutil, "which", lambda command: "/usr/local/bin/hermes"
    )

    def fake_run(*args, **kwargs):
        ordering.append("restart_started")
        restart_calls.append((args, kwargs))
        restart_started.set()
        restart_release.wait(timeout=0.5)
        return SimpleNamespace(
            returncode=1,
            stdout=(
                'Authorization: Bearer bearer-secret '
                '{"password":"json-secret","token":"json-token"} '
                "open_id=ou_secret chat_id=oc_secret message_id=om_secret "
                "user_id=user_secret /private/hermes/runtime.log"
            ),
            stderr="restart failed secret=plain-secret",
        )

    monkeypatch.setattr(sidecar_server.subprocess, "run", fake_run)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command({
                "command": "doctor",
                "chat_id": "oc_private",
                "message_id": "om_doctor",
                "chat_type": "private",
                "operator": "ou_first",
                "adapter_transport_secret": TRANSPORT_SECRET,
            }),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        repair = operations_button(feishu_client.sent[0][1], "安全修复")
        repair_token = str(repair["token"])
        repair_encoded = repair_token.split(".", 1)[0]
        repair_claims = json.loads(
            base64.urlsafe_b64decode(
                repair_encoded + "=" * (-len(repair_encoded) % 4)
            )
        )
        transport_secret = derive_operation_transport_secret(
            TRANSPORT_ROOT_SECRET,
            repair_claims["operation_id"],
        )
        confirm_card = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                repair,
                chat_id="oc_private",
                operator="ou_first",
                transport_secret=transport_secret,
            ),
        )
        confirm_repair = operations_button(
            (await confirm_card.json())["card"], "确认修复"
        )
        repaired = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                confirm_repair,
                chat_id="oc_private",
                operator="ou_second",
                transport_secret=transport_secret,
            ),
        )
        assert "正在安全修复" in str((await repaired.json())["card"])
        await wait_for_card_update(feishu_client, "安全修复已完成")
        restart = operations_button(feishu_client.updated[-1][1], "重启 Gateway")
        confirm_restart_card = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                restart,
                chat_id="oc_private",
                operator="ou_second",
                transport_secret=transport_secret,
            ),
        )
        confirm_restart = operations_button(
            (await confirm_restart_card.json())["card"], "确认重启"
        )
        callback = await asyncio.wait_for(
            test_client.post(
                "/card/actions",
                json=operations_action_payload(
                    confirm_restart,
                    chat_id="oc_private",
                    operator="ou_third",
                    transport_secret=transport_secret,
                ),
            ),
            timeout=0.5,
        )
        callback_body = await callback.json()
        ordering.append("client_received_response")
        assert callback.status == 200
        assert "正在重启 Gateway" in str(callback_body["card"])
        await _wait_until(restart_started.is_set, attempts=150)

        restart_release.set()
        await wait_for_card_update(feishu_client, "修复完成，重启失败")
    finally:
        restart_release.set()
        await test_client.close()

    assert len(restart_calls) == 1
    assert ordering[:2] == ["client_received_response", "restart_started"]
    args, kwargs = restart_calls[0]
    assert args == (["/usr/local/bin/hermes", "gateway", "restart"],)
    assert kwargs == {
        "cwd": Path("/private/hermes"),
        "check": False,
        "capture_output": True,
        "text": True,
        "timeout": 30,
    }
    updated = str(feishu_client.updated[-1][1])
    stored_results = [
        record.result
        for record in app[sidecar_server.OPERATIONS_STORE_KEY]._records.values()
        if record.result
    ]
    serialized = str(stored_results)
    for sensitive in (
        "bearer-secret",
        "json-secret",
        "json-token",
        "ou_secret",
        "oc_secret",
        "om_secret",
        "user_secret",
        "plain-secret",
        "/private/hermes",
    ):
        assert sensitive not in serialized
        assert sensitive not in updated


async def test_operations_action_patches_same_card_to_confirmation_state_after_response(
    monkeypatch,
):
    feishu_client = FakeFeishuClient()
    report = operations_report()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (
            report,
            SimpleNamespace(root=Path("/private/hermes")),
        ),
    )
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        await test_client.post(
            "/commands",
            json=signed_operations_command(
                {
                    "command": "doctor",
                    "chat_id": "oc_private",
                    "message_id": "om_doctor",
                    "chat_type": "private",
                    "adapter_transport_secret": TRANSPORT_SECRET,
                }
            ),
        )
        await _wait_until(lambda: bool(feishu_client.sent))
        repair = operations_button(feishu_client.sent[0][1], "安全修复")
        transport_secret = derive_operation_transport_secret(
            TRANSPORT_ROOT_SECRET,
            json.loads(
                base64.urlsafe_b64decode(
                    str(repair["token"]).split(".", 1)[0]
                    + "=" * (-len(str(repair["token"]).split(".", 1)[0]) % 4)
                )
            )["operation_id"],
        )

        response = await test_client.post(
            "/card/actions",
            json=operations_action_payload(
                repair,
                chat_id="oc_private",
                transport_secret=transport_secret,
            ),
        )
        body = await response.json()
        await wait_for_card_update(feishu_client, "确认安全修复")
    finally:
        await test_client.close()

    assert response.status == 200
    assert "确认安全修复" in str(body["card"])
    assert len(feishu_client.sent) == 1
    assert feishu_client.updated[0][0] == "feishu-message-1"


async def test_operations_transition_starts_follow_up_while_card_patch_is_blocked(
    monkeypatch,
):
    app = create_app(FakeFeishuClient())
    report = operations_report()
    store = app[sidecar_server.OPERATIONS_STORE_KEY]
    operation, _created = store.prepare(
        chat_id="oc_private",
        profile_id="default",
        group=False,
        initiator_open_id="ou_owner",
        operation_id="operation-transition",
        transport_secret=b"transition-secret-value",
        idempotency_key="transition-test",
    )
    patch_started = asyncio.Event()
    release_patch = asyncio.Event()
    follow_up_started = asyncio.Event()

    async def blocked_publish(*_args):
        patch_started.set()
        await release_patch.wait()
        return True

    monkeypatch.setattr(sidecar_server, "_publish_operations_card", blocked_publish)

    def schedule_follow_up():
        follow_up_started.set()

    sidecar_server._schedule_operations_transition(
        app, report, operation, schedule_follow_up
    )
    await asyncio.wait_for(patch_started.wait(), timeout=0.5)
    await asyncio.wait_for(follow_up_started.wait(), timeout=0.5)
    release_patch.set()
    await _wait_until(
        lambda: not app[sidecar_server.OPERATIONS_DIAGNOSTIC_TASKS_KEY]
    )
    await sidecar_server._stop_operations_diagnostics(app)


async def test_operations_response_always_schedules_same_card_publish(monkeypatch):
    app = create_app(FakeFeishuClient())
    report = operations_report()
    operation, _created = app[sidecar_server.OPERATIONS_STORE_KEY].prepare(
        chat_id="oc_private",
        profile_id="default",
        group=False,
        initiator_open_id="ou_owner",
        operation_id="operation-response",
        transport_secret=b"response-secret-value",
        idempotency_key="response-test",
    )
    scheduled = []

    async def successful_write_eof(self, data=b""):
        return None

    def capture_schedule(captured_app, captured_report, captured_operation, follow_up=None):
        scheduled.append(
            (captured_app, captured_report, captured_operation, follow_up)
        )

    monkeypatch.setattr(sidecar_server.web.Response, "write_eof", successful_write_eof)
    monkeypatch.setattr(
        sidecar_server, "_schedule_operations_transition", capture_schedule
    )

    response = sidecar_server._operations_response(app, report, operation)
    await response.write_eof()

    assert scheduled == [(app, report, operation, None)]
    await sidecar_server._stop_operations_diagnostics(app)


@pytest.mark.parametrize(
    "output",
    [
        "Authorization: Bearer secret.jwt.value",
        '{"token":"secret","open_id":"ou_private"}',
        "token=secret-value",
        "oc_private ou_private profile=finance",
        "failed (/Users/bailey/Private Folder/runtime.log)",
        "gateway restarted; token=secret",
    ],
)
def test_restart_output_sanitization_is_fail_closed_for_unknown_output(output):
    assert sidecar_server._restart_output_status(output) == "suppressed"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("", "empty"),
        ("gateway restart completed", "reported_success"),
        ("restart successful", "reported_success"),
    ],
)
def test_restart_output_sanitization_retains_only_explicit_safe_statuses(
    output, expected
):
    assert sidecar_server._restart_output_status(output) == expected


async def test_health_reports_profile_diagnostics_for_profile_events():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                {"profile_id": "work", "profile_source": "env"},
            ),
        )
        assert response.status == 200
        health = await test_client.get("/health")
        body = await health.json()
    finally:
        await test_client.close()

    assert body["profile_diagnostics"]["work"]["events"] == 1
    assert body["profile_diagnostics"]["work"]["last_profile_source"] == "env"


async def test_profiled_hook_like_started_and_delta_apply_to_same_session():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    profile_data = {"profile_id": "default", "profile_source": "fallback_default"}
    await test_client.start_server()
    try:
        started = await test_client.post(
            "/events",
            json=event_payload("message.started", 0, profile_data),
        )
        delta = await test_client.post(
            "/events",
            json=event_payload("answer.delta", 1, {"text": "hello", **profile_data}),
        )
        started_body = await started.json()
        delta_body = await delta.json()
    finally:
        await test_client.close()

    assert started.status == 200
    assert started_body == {"ok": True, "applied": True}
    assert delta.status == 200
    assert delta_body == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert len(feishu_client.updated) == 1


async def test_profile_diagnostics_sanitizes_invalid_profile_keys():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                {"profile_id": "bad:profile/path", "profile_source": "env"},
            ),
        )
        assert response.status == 200
        health = await test_client.get("/health")
        body = await health.json()
    finally:
        await test_client.close()

    assert "bad:profile/path" not in body["profile_diagnostics"]
    assert body["profile_diagnostics"]["default"]["events"] == 1


async def test_event_lifecycle_sends_then_updates_final_card(client):
    test_client, feishu_client = client

    started = await test_client.post(
        "/events",
        json=event_payload("message.started", 0),
    )
    thinking = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 1, {"text": "先分析"}),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 2, {"answer": "最终答案"}),
    )

    assert started.status == 200
    assert await started.json() == {"ok": True, "applied": True}
    assert thinking.status == 200
    assert (await thinking.json())["applied"] is True
    assert completed.status == 200
    assert (await completed.json())["applied"] is True

    await wait_for_card_update(feishu_client, "最终答案")
    assert len(feishu_client.sent) == 1
    assert feishu_client.sent[0][0] == "oc_abc"
    assert len(feishu_client.updated) >= 1
    assert all(message_id == "feishu-message-1" for message_id, _ in feishu_client.updated)
    assert "最终答案" in str(feishu_client.updated[-1][1])
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 3
    assert metrics["events_applied"] == 3
    assert metrics["events_ignored"] == 0
    assert metrics["events_rejected"] == 0
    assert metrics["feishu_send_attempts"] == 1
    assert metrics["feishu_send_successes"] == 1
    assert metrics["feishu_send_failures"] == 0
    assert metrics["feishu_update_attempts"] == 2
    assert metrics["feishu_update_successes"] == 2
    assert metrics["feishu_update_failures"] == 0
    assert metrics["feishu_update_retries"] == 0


async def test_v4_runtime_header_and_interim_body_share_one_card(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post(
        "/events",
        json=event_payload(
            "thinking.delta",
            1,
            {"text": "我先检查天气客户端。", "mode": "append_block"},
        ),
    )
    await test_client.post(
        "/events",
        json=event_payload(
            "tool.updated",
            2,
            {
                "tool_id": "read",
                "name": "read_file",
                "status": "running",
                "detail": "读取 weather_client.py",
            },
        ),
    )

    _, running = await wait_for_card_update(feishu_client, "正在读取：weather_client.py")
    assert running["header"]["title"]["content"] == "Hermes Agent"
    assert running["header"]["subtitle"]["content"] == "正在读取：weather_client.py"
    assert "我先检查天气客户端。" in str(running)
    assert all(
        message_id == "feishu-message-1"
        for message_id, _card in feishu_client.updated
    )

    await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            3,
            {
                "answer": "广州今天有短时阵雨。",
                "duration": 3.0,
                "model": "gpt-5.5",
                "tokens": {"input_tokens": 100, "output_tokens": 20},
                "context": {"used_tokens": 120, "max_tokens": 272000},
            },
        ),
    )

    _, completed = await wait_for_card_update(
        feishu_client,
        "广州今天有短时阵雨。",
    )
    assert completed["header"]["title"]["content"] == "Hermes Agent"
    assert "正在读取：weather_client.py" not in str(completed["header"])
    assert "gpt-5.5" in str(completed)


async def test_v4_interaction_restores_cached_preview_on_same_card(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post(
        "/events",
        json=event_payload(
            "tool.updated",
            1,
            {
                "tool_id": "read",
                "name": "read_file",
                "status": "running",
                "detail": "读取 weather_client.py",
            },
        ),
    )
    await test_client.post(
        "/events",
        json=event_payload(
            "interaction.requested",
            2,
            {
                "interaction_id": "approval-v4",
                "kind": "approval",
                "prompt": "允许读取精确位置吗？",
                "description": "仅用于本次查询。",
                "options": [
                    {
                        "label": "允许一次",
                        "value": "once",
                        "style": "primary",
                    }
                ],
            },
        ),
    )

    _, waiting = await wait_for_card_update(feishu_client, "允许读取精确位置吗？")
    assert waiting["header"]["title"]["content"] == "允许读取精确位置吗？"
    button = next(
        item for item in waiting["body"]["elements"] if item.get("tag") == "button"
    )
    action_value = button["behaviors"][0]["value"]

    response = await test_client.post(
        "/card/actions",
        json={
            "event": {
                "operator": {"open_id": "ou_bailey", "name": "Bailey"},
                "context": {"open_chat_id": "oc_abc"},
                "action": {"value": action_value},
            }
        },
    )

    assert response.status == 200
    _, resumed = await wait_for_card_update(feishu_client, "已选择：允许一次")
    assert resumed["header"]["title"]["content"] == "Hermes Agent"
    assert resumed["header"]["subtitle"]["content"] == "正在读取：weather_client.py"
    assert all(
        message_id == "feishu-message-1"
        for message_id, _card in feishu_client.updated
    )


async def test_v4_preview_burst_coalesces_and_late_preview_cannot_reopen_card(
    client,
):
    test_client, feishu_client = client
    feishu_client.update_delay = 0.03

    await test_client.post("/events", json=event_payload("message.started", 0))
    responses = []
    for index in range(1, 16):
        responses.append(
            await test_client.post(
                "/events",
                json=event_payload(
                    "tool.updated",
                    index,
                    {
                        "tool_id": f"tool-{index}",
                        "name": "read_file",
                        "status": "running",
                        "detail": f"读取 file-{index}.py",
                    },
                ),
            )
        )
    assert all(response.status == 200 for response in responses)

    _, running = await wait_for_card_update(feishu_client, "正在读取：file-15.py")
    assert running["header"]["title"]["content"] == "Hermes Agent"
    assert running["header"]["subtitle"]["content"] == "正在读取：file-15.py"

    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 16, {"answer": "完成"}),
    )
    assert await completed.json() == {"ok": True, "applied": True}
    await wait_for_card_update(feishu_client, "完成")
    updates_before_late = len(feishu_client.updated)

    late = await test_client.post(
        "/events",
        json=event_payload(
            "tool.updated",
            17,
            {
                "tool_id": "late",
                "name": "terminal",
                "status": "running",
                "detail": "迟到命令",
            },
        ),
    )
    assert await late.json() == {"ok": True, "applied": False}
    assert len(feishu_client.updated) == updates_before_late

    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["update_coalesced"] > 0
    assert metrics["update_queue_peak"] == 1


async def test_completed_without_deltas_updates_started_card(client):
    test_client, feishu_client = client

    started = await test_client.post(
        "/events",
        json=event_payload("message.started", 0),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            1,
            {"answer": "DeepSeek 一次性返回的最终答案"},
        ),
    )

    assert started.status == 200
    assert await started.json() == {"ok": True, "applied": True}
    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    await wait_for_card_update(feishu_client, "DeepSeek 一次性返回的最终答案")
    assert len(feishu_client.updated) == 1
    assert "DeepSeek 一次性返回的最终答案" in str(feishu_client.updated[-1][1])

    health = await test_client.get("/health")
    body = await health.json()
    assert "hermes-message-1" not in body["sessions"]
    assert len(body["sessions"]) == 1
    session_snapshot = next(iter(body["sessions"].values()))
    assert session_snapshot["status"] == "completed"
    assert session_snapshot["answer_chars"] > 0
    assert body["metrics"]["feishu_update_attempts"] == 1


async def test_card_config_controls_timeline_rendering():
    feishu_client = FakeFeishuClient()
    app = create_app(
        feishu_client,
        card_config={
            "timeline_expanded": True,
            "show_reasoning": True,
            "max_timeline_items": 1,
            "max_reasoning_chars": 20,
            "max_tool_result_chars": 20,
        },
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))
        await test_client.post(
            "/events",
            json=event_payload(
                "answer.delta", 1, {"text": "第一段很长很长很长很长很长"}
            ),
        )
        await wait_for_card_update(feishu_client, "生成中")
        await _REAL_ASYNCIO_SLEEP(0.25)
        await test_client.post(
            "/events",
            json=event_payload(
                "tool.updated",
                2,
                {
                    "tool_id": "read",
                    "name": "read_file",
                    "status": "completed",
                    "detail": "abcdefghijklmnopqrstuvwxyz1234567890",
                },
            ),
        )
        await wait_for_card_update(feishu_client, "read_file")
        await test_client.post(
            "/events",
            json=event_payload("message.completed", 3, {"answer": "最终回答"}),
        )
        await wait_for_card_update(feishu_client, "最终回答")
    finally:
        await test_client.close()

    card = feishu_client.updated[-1][1]
    timeline = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    assert timeline["expanded"] is True
    assert "已折叠 1 条早期思考/工具记录" in str(timeline)
    assert "工具详情过长，已截断" in str(timeline)
    assert "内容已折叠" not in str(timeline)


@pytest.mark.parametrize(
    ("card_config", "expect_timeline", "expected_expanded", "thought_text"),
    [
        ({"show_reasoning": 0}, False, None, "数值 0 应该隐藏"),
        ({"show_reasoning": "0"}, False, None, "字符串 0 应该隐藏"),
        (
            {"show_reasoning": True, "timeline_expanded": 1},
            True,
            True,
            "数值 1 应该展开",
        ),
        (
            {"show_reasoning": True, "timeline_expanded": "1"},
            True,
            True,
            "字符串 1 应该展开",
        ),
    ],
)
async def test_card_config_string_booleans_control_timeline_rendering(
    card_config, expect_timeline, expected_expanded, thought_text
):
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client, card_config=card_config)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))
        await test_client.post(
            "/events",
            json=event_payload("answer.delta", 1, {"text": thought_text}),
        )
        await test_client.post(
            "/events",
            json=event_payload(
                "tool.updated",
                2,
                {"tool_id": "config-tool", "name": "config_tool", "status": "completed"},
            ),
        )
        await wait_for_card_update(feishu_client, "config_tool")
        await test_client.post(
            "/events",
            json=event_payload("message.completed", 3, {"answer": "最终回答"}),
        )
        await wait_for_card_update(feishu_client, "最终回答")
    finally:
        await test_client.close()

    card = feishu_client.updated[-1][1]
    content = str(card)
    if not expect_timeline:
        assert "auxiliary_timeline" not in content
        assert thought_text not in content
        return

    timeline = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    assert timeline["expanded"] is expected_expanded
    assert thought_text in str(timeline)


async def test_card_config_custom_status_markers_reach_renderer():
    feishu_client = FakeFeishuClient()
    app = create_app(
        feishu_client,
        card_config={
            "status": {
                "active_markers": ["queued"],
                "future_markers": ["resume later"],
            }
        },
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload(
                "message.completed",
                1,
                {"answer": "Queued now; resume later."},
            ),
        )
    finally:
        await test_client.close()

    assert response.status == 200
    card = feishu_client.sent[0][1]
    assert card["header"]["template"] == "blue"
    assert "subtitle" not in card["header"]
    assert card["config"]["summary"]["content"] == "生成中"
    session = next(iter(app[SESSIONS_KEY].values()))
    assert session.display_status == ""
    assert session.display_status_source == "inferred"


async def test_message_started_sends_card_as_thread_reply(client):
    test_client, feishu_client = client

    started = await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_user_message"},
            conversation_id="conversation-1",
            message_id="om_user_message",
            thread_id="omt_thread",
        ),
    )

    assert started.status == 200
    assert await started.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert feishu_client.sent[0][0] == "oc_abc"
    assert feishu_client.sent[0][2] == "omt_thread"
    assert feishu_client.sent[0][3] == "om_user_message"


async def test_message_started_uses_user_message_as_normal_chat_reply_anchor(client):
    test_client, feishu_client = client

    started = await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            conversation_id="conversation-1",
            message_id="om_user_message",
        ),
    )

    assert started.status == 200
    assert await started.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert feishu_client.sent[0][2] is None
    assert feishu_client.sent[0][3] == "om_user_message"


async def test_topic_stream_event_with_reply_anchor_updates_existing_card(client):
    test_client, feishu_client = client

    started = await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )

    assert started.status == 200
    assert await started.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert feishu_client.sent[0][2] == "omt_topic"
    assert feishu_client.sent[0][3] == "om_topic_user"

    tool = await test_client.post(
        "/events",
        json=event_payload(
            "tool.updated",
            1,
            {
                "reply_to_message_id": "om_topic_user",
                "tool_id": "terminal",
                "name": "terminal",
                "status": "running",
                "detail": "brew install ripgrep",
            },
            conversation_id="omt_topic",
            message_id="om_topic_stream_reply",
            thread_id="omt_topic",
        ),
    )

    assert tool.status == 200
    assert await tool.json() == {"ok": True, "applied": True}
    message_id, card = await wait_for_card_update(feishu_client, "brew install ripgrep")
    assert message_id == "feishu-message-1"
    assert "terminal" in str(card)
    assert len(feishu_client.sent) == 1


async def test_topic_system_notice_with_reply_anchor_updates_existing_card(client):
    test_client, feishu_client = client

    await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )

    notice = await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            1,
            {
                "reply_to_message_id": "om_topic_user",
                "title": "上下文窗口提示",
                "content": (
                    "ℹ️ Codex gpt-5.5 caps context at 272K, "
                    "so auto-compaction was raised to 85%."
                ),
                "level": "info",
                "notice_kind": "context-cap",
                "notice_id": "context-cap",
                "notice_scope": "session",
            },
            conversation_id="omt_topic",
            message_id="om_topic_stream_reply",
            thread_id="omt_topic",
        ),
    )

    assert notice.status == 200
    assert await notice.json() == {"ok": True, "applied": True}
    message_id, card = await wait_for_card_update(feishu_client, "auto-compaction")
    assert message_id == "feishu-message-1"
    assert "上下文窗口提示" in str(card)
    assert len(feishu_client.sent) == 1


async def test_topic_second_message_reusing_message_id_sends_new_card(client):
    """Feishu topic groups reuse the same message_id across turns. A second
    message.started on the same (already-completed) key must send a NEW card,
    not be ignored, so the second turn is not left card-less."""
    test_client, feishu_client = client

    # First turn: started -> answer -> completed on message_id om_topic_user.
    await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    await test_client.post(
        "/events",
        json=event_payload(
            "answer.delta",
            1,
            {"reply_to_message_id": "om_topic_user", "text": "first answer"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            2,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    assert completed.status == 200
    assert len(feishu_client.sent) == 1

    # Second turn in the SAME thread reuses the SAME message_id.
    started2 = await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    assert started2.status == 200
    assert await started2.json() == {"ok": True, "applied": True}
    # A brand-new card must be sent for the second turn.
    assert len(feishu_client.sent) == 2

    # And the second turn's content must render on the new card.
    await test_client.post(
        "/events",
        json=event_payload(
            "answer.delta",
            1,
            {"reply_to_message_id": "om_topic_user", "text": "second answer"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    _mid, card = await wait_for_card_update(feishu_client, "second answer")
    assert "second answer" in str(card)


async def test_topic_second_message_started_while_active_is_ignored(client):
    """A duplicate message.started while the session is still streaming must
    still be ignored (no spurious second card)."""
    test_client, feishu_client = client

    await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    assert len(feishu_client.sent) == 1

    # Session still active (no completed) -> a second started is a duplicate.
    dup = await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": "om_topic_user"},
            conversation_id="omt_topic",
            message_id="om_topic_user",
            thread_id="omt_topic",
        ),
    )
    assert dup.status == 200
    assert await dup.json() == {"ok": True, "applied": False}
    assert len(feishu_client.sent) == 1


async def test_interaction_request_renders_buttons_and_callback_resolves(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    requested = await test_client.post(
        "/events",
        json=event_payload(
            "interaction.requested",
            1,
            {
                "interaction_id": "approval-1",
                "kind": "approval",
                "prompt": "允许执行命令吗？",
                "description": "rm -rf /tmp/demo",
                "options": [
                    {"label": "允许一次", "value": "once", "style": "primary"},
                    {"label": "拒绝", "value": "deny", "style": "danger"},
                ],
            },
        ),
    )

    assert requested.status == 200
    assert (await requested.json()) == {
        "ok": True,
        "applied": True,
        "interaction_mode": "callback",
    }
    interaction_card = feishu_client.updated[-1][1]
    button = next(
        element
        for element in interaction_card["body"]["elements"]
        if element.get("tag") == "button"
    )
    action_value = button["behaviors"][0]["value"]

    callback = await test_client.post(
        "/card/actions",
        json={
            "event": {
                "operator": {"open_id": "ou_bailey", "name": "Bailey"},
                "context": {"open_chat_id": "oc_abc"},
                "action": {"value": action_value},
            }
        },
    )
    result = await test_client.get("/interactions/approval-1")

    assert callback.status == 200
    callback_body = await callback.json()
    assert callback_body["ok"] is True
    assert callback_body["toast"]["type"] == "success"
    assert result.status == 200
    assert await result.json() == {
        "ok": True,
        "status": "completed",
        "choice": "once",
        "choice_label": "允许一次",
        "interaction_id": "approval-1",
    }
    assert "已选择：允许一次" in str(feishu_client.updated[-1][1])


def test_interaction_operator_name_never_falls_back_to_feishu_ids():
    assert sidecar_server._extract_operator_name(
        {"event": {"operator": {"open_id": "ou_private", "user_id": "on_private"}}}
    ) == ""
    assert sidecar_server._extract_operator_name(
        {"event": {"operator": {"open_id": "ou_private", "name": "Bailey"}}}
    ) == "Bailey"


async def test_interaction_request_uses_text_fallback_when_configured():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client, card_config={"interaction_mode": "text"})
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))
        requested = await test_client.post(
            "/events",
            json=event_payload(
                "interaction.requested",
                1,
                {
                    "interaction_id": "clarify-1",
                    "kind": "clarify",
                    "prompt": "请选择处理方式",
                    "options": [
                        {"label": "删除空文件", "value": "delete"},
                        {"label": "保留并补索引", "value": "keep"},
                    ],
                },
            ),
        )

        assert requested.status == 200
        assert (await requested.json())["interaction_mode"] == "text"
        interaction_card = feishu_client.updated[-1][1]
        content = str(interaction_card)
        assert not any(
            element.get("tag") == "button"
            for element in interaction_card["body"]["elements"]
        )
        assert "1. 删除空文件" in content
        assert "2. 保留并补索引" in content
    finally:
        await test_client.close()


async def test_native_text_fallback_interaction_is_not_applied_in_text_mode():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client, card_config={"interaction_mode": "text"})
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        requested = await test_client.post(
            "/events",
            json=event_payload(
                "interaction.requested",
                1,
                {
                    "interaction_id": "slash-new-1",
                    "kind": "slash_confirm",
                    "prompt": "Confirm /new",
                    "fallback_policy": "native_text",
                    "options": [
                        {"label": "Approve Once", "value": "once"},
                        {"label": "Always Approve", "value": "always"},
                        {"label": "Cancel", "value": "cancel"},
                    ],
                },
            ),
        )

        assert requested.status == 200
        assert await requested.json() == {
            "ok": True,
            "applied": False,
            "interaction_mode": "text",
        }
        assert feishu_client.sent == []
        assert feishu_client.updated == []
    finally:
        await test_client.close()


async def test_completed_card_summary_can_be_looked_up_by_feishu_message_id(client):
    test_client, _ = client
    long_answer = "最终答案" * 1000

    missing = await test_client.get("/messages/feishu-message-1/summary")
    await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"profile_id": "work"},
        ),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            1,
            {"answer": long_answer, "profile_id": "work"},
        ),
    )
    found = await test_client.get("/messages/feishu-message-1/summary")

    assert missing.status == 404
    assert await missing.json() == {"ok": False, "error": "not found"}
    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    assert found.status == 200
    body = await found.json()
    assert body["ok"] is True
    assert body["profile_id"] == "work"
    assert body["chat_id_hash"] == sidecar_server._diagnostic_id_hash("oc_abc")
    assert body["message_id_hash"] == sidecar_server._diagnostic_id_hash(
        "feishu-message-1"
    )
    assert body["source_message_id_hash"] == sidecar_server._diagnostic_id_hash(
        "hermes-message-1"
    )
    assert "chat_id" not in body
    assert "message_id" not in body
    assert "source_message_id" not in body
    assert body["summary"] == long_answer[:4000]
    assert len(body["summary"]) == 4000


async def test_blank_completed_card_summary_is_not_indexed(client):
    test_client, _ = client

    await test_client.post(
        "/events",
        json=event_payload("message.started", 0),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "   "}),
    )
    found = await test_client.get("/messages/feishu-message-1/summary")

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    assert found.status == 404
    assert await found.json() == {"ok": False, "error": "not found"}


async def test_card_config_customizes_header_title():
    feishu_client = FakeFeishuClient()
    app = create_app(feishu_client, card_config={"title": "研发助手"})
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload("message.started", 0),
        )
    finally:
        await test_client.close()

    assert response.status == 200
    assert feishu_client.sent[0][1]["header"]["title"]["content"] == "研发助手"


async def test_invalid_event_returns_400_json(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload("bad.event", 1),
    )

    assert response.status == 400
    body = await response.json()
    assert body["ok"] is False
    assert "error" in body
    assert feishu_client.sent == []
    assert feishu_client.updated == []
    health = await test_client.get("/health")
    assert (await health.json())["metrics"]["events_rejected"] == 1


async def test_malformed_json_returns_400_json(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        data="{bad json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 400
    body = await response.json()
    assert body["ok"] is False
    assert "error" in body
    assert feishu_client.sent == []
    assert feishu_client.updated == []


async def test_non_object_json_payload_returns_400_json(client):
    test_client, feishu_client = client

    response = await test_client.post("/events", json=["not", "an", "object"])

    assert response.status == 400
    body = await response.json()
    assert body["ok"] is False
    assert "error" in body
    assert feishu_client.sent == []
    assert feishu_client.updated == []


@pytest.mark.parametrize(
    ("event_name", "data", "expected_text"),
    [
        ("answer.delta", {"text": "提前到达的回答"}, "提前到达的回答"),
        ("thinking.delta", {"text": "提前到达的思考"}, "Hermes Agent"),
        (
            "tool.updated",
            {
                "tool_id": "tool-1",
                "name": "search",
                "status": "running",
                "detail": "提前到达的工具",
            },
            "search",
        ),
        ("message.completed", {"answer": "提前完成的回答"}, "提前完成的回答"),
    ],
)
async def test_message_event_without_started_creates_initial_card(
    client, event_name, data, expected_text
):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload(event_name, 1, data),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert expected_text in str(feishu_client.sent[0][1])
    assert feishu_client.updated == []
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 1
    assert metrics["events_applied"] == 1
    assert metrics["events_ignored"] == 0


async def test_independent_system_notice_without_started_sends_notice_card(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            1,
            {
                "title": "上下文窗口提示",
                "content": "Codex gpt-5.5 caps context at 272K.",
                "notice_scope": "independent",
                "level": "info",
            },
            message_id="notice_context_cap",
        ),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    card = feishu_client.sent[0][1]
    assert card["header"]["title"]["content"] == "上下文窗口提示"
    assert card["header"]["template"] == "blue"
    assert "Codex gpt-5.5 caps context at 272K." in str(card)
    assert "生成中" not in str(card)
    assert feishu_client.updated == []


async def test_orphaned_self_improvement_notice_does_not_claim_next_turn(client):
    test_client, feishu_client = client
    conversation_id = "omt_self_improvement"
    reply_anchor = "om_topic_root"
    notice_data = {
        "reply_to_message_id": reply_anchor,
        "title": "自我改进",
        "content": "💾 Self-improvement review: Memory updated",
        "level": "info",
        "notice_kind": "self-improvement",
        "notice_id": "self-improvement:review-1",
    }

    orphaned = await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            1,
            {**notice_data, "notice_scope": "session"},
            conversation_id=conversation_id,
            message_id=reply_anchor,
            thread_id=conversation_id,
        ),
    )

    assert orphaned.status == 200
    assert await orphaned.json() == {"ok": True, "applied": False}
    assert reply_anchor not in test_client.app[SESSIONS_KEY]
    assert reply_anchor not in test_client.app[SESSION_ALIASES_KEY]
    assert feishu_client.sent == []

    independent = await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            2,
            {**notice_data, "notice_scope": "independent"},
            conversation_id=conversation_id,
            message_id="notice_self_improvement",
            thread_id=conversation_id,
        ),
    )

    assert independent.status == 200
    assert await independent.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert "生成中" not in str(feishu_client.sent[0][1])
    assert test_client.app[SESSIONS_KEY]["notice_self_improvement"].status == "completed"

    started = await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            {"reply_to_message_id": reply_anchor},
            conversation_id=conversation_id,
            message_id=reply_anchor,
            thread_id=conversation_id,
        ),
    )
    assert started.status == 200
    assert await started.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 2

    followup = await test_client.post(
        "/events",
        json=event_payload(
            "answer.delta",
            1,
            {
                "reply_to_message_id": reply_anchor,
                "text": "后续对话应更新自己的卡片",
            },
            conversation_id=conversation_id,
            message_id="om_followup_delta",
            thread_id=conversation_id,
        ),
    )
    assert followup.status == 200
    assert await followup.json() == {"ok": True, "applied": True}
    updated_message_id, _card = await wait_for_card_update(
        feishu_client,
        "后续对话应更新自己的卡片",
    )
    assert updated_message_id == "feishu-message-2"
    assert test_client.app[SESSIONS_KEY]["notice_self_improvement"].status == "completed"
    assert test_client.app[SESSIONS_KEY][reply_anchor].status == "thinking"


async def test_independent_background_process_notice_updates_same_card(client):
    test_client, feishu_client = client
    message_id = "notice_background_process_proc_109e6dc419af"

    running_response = await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            100,
            {
                "title": "后台进程运行中",
                "content": "Updating files: 76%",
                "notice_scope": "independent",
                "notice_kind": "background-process",
                "notice_id": "background-process:proc_109e6dc419af",
                "notice_terminal": False,
                "level": "info",
            },
            message_id=message_id,
        ),
    )

    assert running_response.status == 200
    assert await running_response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert feishu_client.sent[0][1]["header"]["title"]["content"] == "后台进程运行中"
    assert feishu_client.updated == []

    completed_response = await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            1,
            {
                "title": "后台进程已完成",
                "content": "Updating files: 100%, done.",
                "notice_scope": "independent",
                "notice_kind": "background-process",
                "notice_id": "background-process:proc_109e6dc419af",
                "notice_terminal": True,
                "level": "success",
            },
            message_id=message_id,
        ),
    )

    assert completed_response.status == 200
    assert await completed_response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert len(feishu_client.updated) == 1
    card = feishu_client.updated[0][1]
    assert card["header"]["title"]["content"] == "后台进程已完成"
    assert card["header"]["template"] == "green"
    assert "Updating files: 100%, done." in str(card)


async def test_independent_background_notices_do_not_abandon_active_cards(client):
    test_client, _feishu_client = client
    conversation_id = "conversation-background-concurrency"

    await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            conversation_id=conversation_id,
            message_id="main-turn-1",
        ),
    )
    await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            100,
            {
                "title": "后台进程运行中",
                "content": "process one: 50%",
                "notice_scope": "independent",
                "notice_kind": "background-process",
                "notice_id": "background-process:proc_111111111111",
                "notice_terminal": False,
                "level": "info",
            },
            conversation_id=conversation_id,
            message_id="notice-process-one",
        ),
    )
    await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            100,
            {
                "title": "后台进程运行中",
                "content": "process two: 50%",
                "notice_scope": "independent",
                "notice_kind": "background-process",
                "notice_id": "background-process:proc_222222222222",
                "notice_terminal": False,
                "level": "info",
            },
            conversation_id=conversation_id,
            message_id="notice-process-two",
        ),
    )

    sessions = test_client.app[SESSIONS_KEY]
    assert sessions["main-turn-1"].status == "thinking"
    assert sessions["notice-process-one"].status == "running"
    assert sessions["notice-process-two"].status == "running"

    await test_client.post(
        "/events",
        json=event_payload(
            "message.started",
            0,
            conversation_id=conversation_id,
            message_id="main-turn-2",
        ),
    )

    assert sessions["main-turn-1"].status == "completed"
    assert sessions["notice-process-one"].status == "running"
    assert sessions["notice-process-two"].status == "running"


async def test_background_notice_terminal_update_retries_and_cleans_controller(
    client, monkeypatch
):
    test_client, feishu_client = client
    message_id = "notice-background-terminal-retry"

    await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            100,
            {
                "title": "后台进程运行中",
                "content": "Updating files: 76%",
                "notice_scope": "independent",
                "notice_kind": "background-process",
                "notice_id": "background-process:proc_333333333333",
                "notice_terminal": False,
                "level": "info",
            },
            message_id=message_id,
        ),
    )
    feishu_client.update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(sidecar_server.asyncio, "sleep", fake_sleep)
    completed = await test_client.post(
        "/events",
        json=event_payload(
            "system.notice",
            1,
            {
                "title": "后台进程已完成",
                "content": "Updating files: 100%, done.",
                "notice_scope": "independent",
                "notice_kind": "background-process",
                "notice_id": "background-process:proc_333333333333",
                "notice_terminal": True,
                "level": "success",
            },
            message_id=message_id,
        ),
    )

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    await wait_for_card_update(feishu_client, "Updating files: 100%, done.")
    for _ in range(20):
        if message_id not in test_client.app[FLUSH_CONTROLLERS_KEY]:
            break
        await _REAL_ASYNCIO_SLEEP(0)

    metrics = test_client.app[METRICS_KEY]
    assert metrics.feishu_update_attempts == sidecar_server.UPDATE_MAX_ATTEMPTS + 1
    assert metrics.feishu_update_failures == sidecar_server.UPDATE_MAX_ATTEMPTS
    assert message_id not in test_client.app[FLUSH_CONTROLLERS_KEY]
    assert metrics.flush_controllers_collected == 1


async def test_cron_completed_event_sends_completed_card_without_started(client):
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            0,
            {"answer": "定时结果", "delivery_kind": "cron"},
            message_id="cron_1",
        ),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert "定时结果" in str(feishu_client.sent[0][1])
    assert feishu_client.updated == []
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 1
    assert metrics["events_applied"] == 1
    assert metrics["events_ignored"] == 0
    assert metrics["cron_cards_sent"] == 1


async def test_cron_completed_event_with_thread_id_sends_card_to_thread(client):
    """Cron event with thread_id should pass thread_id to send_card."""
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            0,
            {"answer": "Cron in thread", "delivery_kind": "cron"},
            message_id="cron_thread_1",
            chat_id="oc_topic_group",
            thread_id="omt_target_thread",
            conversation_id="omt_target_thread",
        ),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    # FakeFeishuClient.send_card stores (chat_id, card, thread_id, reply_to_message_id)
    chat_id, card, thread_id, reply_to = feishu_client.sent[0]
    assert chat_id == "oc_topic_group"
    assert thread_id == "omt_target_thread"
    assert "Cron in thread" in str(card)


async def test_cron_completed_event_without_thread_id_sends_card_to_chat(client):
    """Cron event without thread_id should send card to chat_id directly."""
    test_client, feishu_client = client

    response = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            0,
            {"answer": "Cron no thread", "delivery_kind": "cron"},
            message_id="cron_no_thread_1",
            chat_id="oc_dm_chat",
            thread_id="",
            conversation_id="cron_no_thread_1",
        ),
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    chat_id, card, thread_id, reply_to = feishu_client.sent[0]
    assert chat_id == "oc_dm_chat"
    assert thread_id is None  # _thread_id_for_event returns None for non-omt_ ids


async def test_duplicate_started_does_not_send_again(client):
    test_client, feishu_client = client

    first = await test_client.post("/events", json=event_payload("message.started", 0))
    duplicate = await test_client.post("/events", json=event_payload("message.started", 0))

    assert first.status == 200
    assert await first.json() == {"ok": True, "applied": True}
    assert duplicate.status == 200
    assert await duplicate.json() == {"ok": True, "applied": False}
    assert len(feishu_client.sent) == 1
    assert feishu_client.updated == []


async def test_replayed_started_with_higher_sequence_does_not_block_later_delta(client):
    test_client, feishu_client = client

    first = await test_client.post("/events", json=event_payload("message.started", 0))
    replayed = await test_client.post("/events", json=event_payload("message.started", 5))
    thinking = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 1, {"text": "后续增量"}),
    )

    assert first.status == 200
    assert await first.json() == {"ok": True, "applied": True}
    assert replayed.status == 200
    assert await replayed.json() == {"ok": True, "applied": False}
    assert thinking.status == 200
    assert await thinking.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1
    assert len(feishu_client.updated) == 1
    assert "后续增量" in str(feishu_client.updated[0][1])
    assert "生成中" in str(feishu_client.updated[0][1])


async def test_delta_after_completed_does_not_update_again(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )
    updates_after_completed = len(feishu_client.updated)

    response = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 2, {"text": "迟到增量"}),
    )

    assert response.status == 200
    assert (await response.json())["applied"] is False
    assert len(feishu_client.updated) == updates_after_completed


async def test_parallel_message_sessions_update_their_own_feishu_cards(client):
    test_client, feishu_client = client

    msg1 = {"conversation_id": "conversation-1", "message_id": "hermes-message-1"}
    msg2 = {"conversation_id": "conversation-2", "message_id": "hermes-message-2"}

    started1 = await test_client.post(
        "/events",
        json=event_payload("message.started", 0, **msg1),
    )
    delta1 = await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "第一条"}, **msg1),
    )
    started2 = await test_client.post(
        "/events",
        json=event_payload("message.started", 0, **msg2),
    )
    tool2 = await test_client.post(
        "/events",
        json=event_payload(
            "tool.updated",
            1,
            {
                "tool_id": "tool-2",
                "name": "search",
                "status": "running",
                "detail": "第二条工具",
            },
            **msg2,
        ),
    )
    completed1 = await test_client.post(
        "/events",
        json=event_payload("message.completed", 2, {"answer": "第一条完成"}, **msg1),
    )
    await wait_for_card_update(feishu_client, "第一条完成")
    updates_before_late = len(feishu_client.updated)
    late1 = await test_client.post(
        "/events",
        json=event_payload("tool.updated", 3, {"tool_id": "late"}, **msg1),
    )

    assert started1.status == 200
    assert delta1.status == 200
    assert started2.status == 200
    assert tool2.status == 200
    assert completed1.status == 200
    assert late1.status == 200
    assert await late1.json() == {"ok": True, "applied": False}
    assert len(feishu_client.updated) == updates_before_late

    assert [item[0] for item in feishu_client.sent] == ["oc_abc", "oc_abc"]
    updates_by_message = {}
    for feishu_message_id, card in feishu_client.updated:
        updates_by_message.setdefault(feishu_message_id, []).append(str(card))
    assert set(updates_by_message) == {"feishu-message-1", "feishu-message-2"}
    assert any("第一条完成" in card for card in updates_by_message["feishu-message-1"])
    assert any("`search` · running" in card for card in updates_by_message["feishu-message-2"])


async def test_streaming_deltas_are_throttled_but_terminal_event_updates(client):
    test_client, feishu_client = client

    started = await test_client.post("/events", json=event_payload("message.started", 0))
    first_delta = await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "第一段"}),
    )
    second_delta = await test_client.post(
        "/events",
        json=event_payload("answer.delta", 2, {"text": "第二段"}),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 3, {"answer": "最终答案"}),
    )

    assert started.status == 200
    assert first_delta.status == 200
    assert second_delta.status == 200
    assert await second_delta.json() == {"ok": True, "applied": True}
    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}

    assert len(feishu_client.sent) == 1
    await wait_for_card_update(feishu_client, "最终答案")
    assert len(feishu_client.updated) == 3
    assert "第一段" in str(feishu_client.updated[0][1])
    assert "最终答案" in str(feishu_client.updated[-1][1])
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 4
    assert metrics["events_applied"] == 4
    assert metrics["events_rejected"] == 0
    assert metrics["feishu_update_attempts"] == 3
    assert metrics["feishu_update_successes"] == 3
    assert metrics["terminal_drains"] == 1


async def test_terminal_event_with_stale_sequence_still_finalizes_card(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 100, {"text": "部分答案"}),
    )
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 90, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    await wait_for_card_update(feishu_client, "最终答案")
    assert "最终答案" in str(feishu_client.updated[-1][1])
    assert "已完成" in str(feishu_client.updated[-1][1])


async def test_concurrent_streaming_deltas_share_message_update_window(client):
    test_client, feishu_client = client
    feishu_client.update_delay = 0.05

    started = await test_client.post("/events", json=event_payload("message.started", 0))
    first_delta, second_delta = await asyncio.gather(
        test_client.post(
            "/events",
            json=event_payload("answer.delta", 1, {"text": "第一段"}),
        ),
        test_client.post(
            "/events",
            json=event_payload("answer.delta", 2, {"text": "第二段"}),
        ),
    )

    assert started.status == 200
    assert first_delta.status == 200
    assert second_delta.status == 200
    assert await first_delta.json() == {"ok": True, "applied": True}
    assert await second_delta.json() == {"ok": True, "applied": True}
    for _ in range(20):
        if feishu_client.updated:
            break
        await asyncio.sleep(0.01)
    assert len(feishu_client.updated) == 1
    assert "第一段" in str(feishu_client.updated[0][1])
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 3
    assert metrics["events_applied"] == 3
    assert metrics["feishu_update_attempts"] == 1
    assert metrics["feishu_update_successes"] == 1


async def test_concurrent_card_updates_preserve_newer_content(monkeypatch):
    feishu_client = ReorderingFeishuClient()
    app = create_app(feishu_client)
    server = TestServer(app)
    test_client = TestClient(server)
    monkeypatch.setattr(sidecar_server, "UPDATE_MIN_INTERVAL_SECONDS", 0)
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))
        first_delta, second_delta = await asyncio.gather(
            test_client.post(
                "/events",
                json=event_payload("answer.delta", 1, {"text": "第一段"}),
            ),
            test_client.post(
                "/events",
                json=event_payload("answer.delta", 2, {"text": "第二段"}),
            ),
        )

        assert first_delta.status == 200
        assert second_delta.status == 200
        for _ in range(20):
            if len(feishu_client.updated) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(feishu_client.updated) == 2
        assert "第一段第二段" in str(feishu_client.updated[-1][1])
    finally:
        await test_client.close()


async def test_terminal_update_is_not_blocked_by_streaming_update_backlog(client, monkeypatch):
    test_client, feishu_client = client
    feishu_client.update_delay = 0.05
    monkeypatch.setattr(sidecar_server, "UPDATE_MIN_INTERVAL_SECONDS", 0)

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "片段0"}),
    )
    deltas = await asyncio.gather(
        *[
            test_client.post(
                "/events",
                json=event_payload("answer.delta", sequence, {"text": f"片段{sequence}"}),
            )
            for sequence in range(2, 12)
        ]
    )

    started_at = asyncio.get_running_loop().time()
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 12, {"answer": "最终答案"}),
    )
    elapsed = asyncio.get_running_loop().time() - started_at

    assert all(response.status == 200 for response in deltas)
    assert completed.status == 200
    assert elapsed < 0.5
    await wait_for_card_update(feishu_client, "最终答案")
    assert "最终答案" in str(feishu_client.updated[-1][1])
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["events_received"] == 13
    assert metrics["events_applied"] == 13
    assert metrics["feishu_update_attempts"] <= 3
    assert metrics["feishu_update_failures"] == 0


async def test_burst_updates_are_coalesced_and_reported_in_health(client, monkeypatch):
    test_client, feishu_client = client
    feishu_client.update_delay = 0.03
    monkeypatch.setattr(sidecar_server, "UPDATE_MIN_INTERVAL_SECONDS", 0)

    await test_client.post("/events", json=event_payload("message.started", 0))
    responses = await asyncio.gather(
        *[
            test_client.post(
                "/events",
                json=event_payload("answer.delta", index, {"text": f"片段{index}"}),
            )
            for index in range(1, 25)
        ]
    )

    assert all(response.status == 200 for response in responses)
    await wait_for_card_update(feishu_client, "片段24")
    health = await test_client.get("/health")
    body = await health.json()
    assert body["metrics"]["update_coalesced"] > 0
    assert body["metrics"]["update_queue_peak"] == 1
    assert body["metrics"]["feishu_update_attempts"] < 24


async def test_terminal_event_ack_does_not_wait_for_slow_card_patch(client, monkeypatch):
    del monkeypatch
    feishu_client = FakeFeishuClient()
    feishu_client.update_delay = 0.25
    app = create_app(
        feishu_client,
        card_config={"flush_interval_ms": 0, "final_drain_timeout_ms": 120},
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post("/events", json=event_payload("message.started", 0))

        started_at = asyncio.get_running_loop().time()
        completed = await test_client.post(
            "/events",
            json=event_payload("message.completed", 1, {"answer": "最终答案"}),
        )
        elapsed = asyncio.get_running_loop().time() - started_at

        assert completed.status == 200
        assert await completed.json() == {"ok": True, "applied": True}
        assert elapsed < 0.12
        assert elapsed < feishu_client.update_delay
        assert feishu_client.updated == []

        for _ in range(40):
            if feishu_client.updated:
                break
            await asyncio.sleep(0.01)
        assert "最终答案" in str(feishu_client.updated[-1][1])
    finally:
        await test_client.close()


async def test_terminal_event_drains_latest_pending_content_before_final_card(client, monkeypatch):
    test_client, feishu_client = client
    feishu_client.update_delay = 0.04
    monkeypatch.setattr(sidecar_server, "UPDATE_MIN_INTERVAL_SECONDS", 0)

    await test_client.post("/events", json=event_payload("message.started", 0))
    await asyncio.gather(
        *[
            test_client.post(
                "/events",
                json=event_payload("answer.delta", index, {"text": f"片段{index}"}),
            )
            for index in range(1, 15)
        ]
    )
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 15, {"answer": ""}),
    )

    assert completed.status == 200
    await wait_for_card_update(feishu_client, "片段14")
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["terminal_drains"] == 1
    assert metrics["terminal_drain_timeouts"] == 0
    assert "片段14" in str(feishu_client.updated[-1][1])


async def test_terminal_event_does_not_wait_for_update_window_without_pending_flush(
    client, monkeypatch
):
    test_client, feishu_client = client
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(flush_module.asyncio, "sleep", fake_sleep)

    await test_client.post("/events", json=event_payload("message.started", 0))
    await test_client.post("/events", json=event_payload("answer.delta", 1, {"text": "片段"}))
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 2, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    await wait_for_card_update(feishu_client, "最终答案")
    assert sleeps == []
    assert len(feishu_client.updated) == 2
    assert "最终答案" in str(feishu_client.updated[-1][1])


async def test_missing_feishu_message_id_returns_conflict_without_update(client):
    test_client, feishu_client = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    test_client.app[FEISHU_MESSAGE_IDS_KEY].pop("hermes-message-1")

    response = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 1, {"text": "需要更新"}),
    )

    assert response.status == 409
    body = await response.json()
    assert body == {"ok": False, "error": "feishu_message_id missing"}
    assert feishu_client.updated == []
    health = await test_client.get("/health")
    assert (await health.json())["metrics"]["events_rejected"] == 1


async def test_update_retries_once_and_reports_retry_metrics(client):
    test_client, feishu_client = client
    feishu_client.update_failures_remaining = 1

    started = await test_client.post("/events", json=event_payload("message.started", 0))
    thinking = await test_client.post(
        "/events",
        json=event_payload("thinking.delta", 1, {"text": "需要重试"}),
    )

    assert started.status == 200
    assert thinking.status == 200
    assert (await thinking.json()) == {"ok": True, "applied": True}
    assert len(feishu_client.updated) == 1
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["feishu_update_attempts"] == 2
    assert metrics["feishu_update_successes"] == 1
    assert metrics["feishu_update_failures"] == 1
    assert metrics["feishu_update_retries"] == 1


async def test_terminal_update_failure_still_accepts_event_to_prevent_native_fallback(client):
    test_client, feishu_client = client
    feishu_client.update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS

    await test_client.post("/events", json=event_payload("message.started", 0))
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    assert await completed.json() == {"ok": True, "applied": True}
    metrics = await wait_for_metric(
        test_client,
        "feishu_update_attempts",
        sidecar_server.UPDATE_MAX_ATTEMPTS + 1,
        attempts=180,
    )
    assert metrics["events_applied"] == 2
    assert metrics["events_rejected"] == 0
    assert metrics["feishu_update_attempts"] == 4  # 3 from _update_card + 1 from _retry_terminal
    assert metrics["feishu_update_failures"] == sidecar_server.UPDATE_MAX_ATTEMPTS


async def test_health_reports_last_attachment_event_for_native_delivery(client):
    test_client, _ = client

    await test_client.post("/events", json=event_payload("message.started", 0))
    completed = await test_client.post(
        "/events",
        json=event_payload(
            "message.completed",
            1,
            {
                "answer": "最终答案",
                "attachments": [
                    {"kind": "image", "name": "cover.png", "summary": "cover.png"}
                ],
                "native_delivery": "required",
            },
        ),
    )

    assert completed.status == 200
    health = await test_client.get("/health")
    diagnostics = (await health.json())["diagnostics"]
    assert diagnostics["last_attachment_event"]["event"] == "message.completed"
    assert diagnostics["last_attachment_event"]["attachment_count"] == 1
    assert diagnostics["last_attachment_event"]["native_delivery"] == "required"
    assert diagnostics["last_attachment_event"]["message_id_hash"]
    assert "message_id" not in diagnostics["last_attachment_event"]


async def test_terminal_update_failure_is_retried_in_background(client, monkeypatch):
    test_client, feishu_client = client
    feishu_client.update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(sidecar_server.asyncio, "sleep", fake_sleep)

    await test_client.post("/events", json=event_payload("message.started", 0))
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    for _ in range(10):
        if feishu_client.updated:
            break
        await asyncio.sleep(0)

    assert len(feishu_client.updated) == 1
    assert "最终答案" in str(feishu_client.updated[-1][1])


async def test_terminal_retry_backoff_does_not_inflate_patch_latency_metric(
    client, monkeypatch
):
    test_client, feishu_client = client
    feishu_client.update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS
    now = [100.0]

    async def fake_sleep(delay):
        now[0] += delay

    monkeypatch.setattr(sidecar_server.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(sidecar_server.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(flush_module.time, "monotonic", lambda: now[0])

    await test_client.post("/events", json=event_payload("message.started", 0))
    completed = await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "最终答案"}),
    )

    assert completed.status == 200
    await wait_for_card_update(feishu_client, "最终答案")
    health = await test_client.get("/health")
    metrics = (await health.json())["metrics"]
    assert metrics["feishu_update_attempts"] == sidecar_server.UPDATE_MAX_ATTEMPTS + 1
    assert metrics["feishu_update_failures"] == sidecar_server.UPDATE_MAX_ATTEMPTS
    assert metrics["feishu_update_latency_ms"] < 1000


async def test_send_failure_returns_json_error_and_allows_started_retry(client):
    test_client, feishu_client = client
    feishu_client.fail_send = True

    failed = await test_client.post("/events", json=event_payload("message.started", 0))

    assert failed.status == 502
    failed_body = await failed.json()
    assert failed_body == {"ok": False, "error": "feishu send failed"}
    assert feishu_client.sent == []
    health_after_failure = await test_client.get("/health")
    failure_body = await health_after_failure.json()
    assert failure_body["active_sessions"] == 0
    assert failure_body["metrics"]["feishu_send_attempts"] == 1
    assert failure_body["metrics"]["feishu_send_failures"] == 1

    feishu_client.fail_send = False
    retried = await test_client.post("/events", json=event_payload("message.started", 0))

    assert retried.status == 200
    assert await retried.json() == {"ok": True, "applied": True}
    assert len(feishu_client.sent) == 1


@pytest.mark.parametrize("failure_kind", ["send", "route"])
@pytest.mark.parametrize("replacement", [False, True])
async def test_repeated_failed_messages_do_not_retain_runtime_state(
    failure_kind, replacement
):
    if failure_kind == "send":
        boundary = FakeFeishuClient()
        boundary.fail_send = True
        app = create_app(boundary)
    else:
        boundary = FakeFeishuClientFactory()

        def fail_route(event):
            raise RuntimeError("route unavailable")

        app = create_app(boundary, bot_router=fail_route)

    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        for index in range(12):
            session_key = f"om_failed_{index}"
            reply_alias = f"om_reply_{index}"
            parent_alias = f"om_parent_{index}"
            if replacement:
                old_session = CardSession("oc_abc", session_key, "oc_abc")
                old_session.status = "completed"
                old_session.answer_text = f"old answer {index}"
                old_session.active_interaction = InteractionState(
                    interaction_id=f"approval-{index}",
                    kind="approval",
                    prompt="Old approval",
                    status="completed",
                )
                app[SESSIONS_KEY][session_key] = old_session
                app[FEISHU_MESSAGE_IDS_KEY][session_key] = f"om_card_{index}"
                app[MESSAGE_BOT_IDS_KEY][session_key] = "default"
                app[SESSION_CARD_CONFIGS_KEY][session_key] = {"title": "Old"}
                app[SESSION_ALIASES_KEY][f"om_old_alias_{index}"] = session_key
                sidecar_server._store_interaction_result(app, old_session)
                sidecar_server._store_card_summary(
                    app,
                    SidecarEvent.from_dict(
                        event_payload(
                            "message.completed",
                            1,
                            {"answer": old_session.answer_text},
                            message_id=session_key,
                        )
                    ),
                    old_session,
                    f"om_card_{index}",
                )

            response = await test_client.post(
                "/events",
                json=event_payload(
                    "message.started",
                    0,
                    {
                        "reply_to_message_id": reply_alias,
                        "parent_message_id": parent_alias,
                    },
                    message_id=session_key,
                    thread_id=f"omt_topic_{index}",
                ),
            )
            assert response.status == 502

        assert app[SESSIONS_KEY] == {}
        assert app[SESSION_ALIASES_KEY] == {}
        assert app[MESSAGE_LOCKS_KEY] == {}
        assert app[MESSAGE_LOCK_USERS_KEY] == {}
        assert app[FEISHU_MESSAGE_IDS_KEY] == {}
        assert app[MESSAGE_BOT_IDS_KEY] == {}
        assert app[SESSION_CARD_CONFIGS_KEY] == {}
        assert app[FLUSH_CONTROLLERS_KEY] == {}
        assert app[CARD_SUMMARIES_KEY] == {}
        assert app[CARD_SUMMARY_SESSION_KEYS_KEY] == {}
        assert app[INTERACTION_RESULTS_KEY] == {}
        assert app[INTERACTION_RESULT_SESSION_KEYS_KEY] == {}
    finally:
        await test_client.close()


def test_failed_session_cleanup_preserves_reassigned_aliases_and_owners():
    app = create_app(FakeFeishuClient())
    failed_key = "om_failed"
    active_key = "om_active"
    active_session = CardSession("oc_abc", active_key, "oc_abc")
    app[SESSIONS_KEY][active_key] = active_session
    app[SESSION_ALIASES_KEY]["om_reply"] = active_key
    app[CARD_SUMMARIES_KEY]["om_card"] = {"summary": "active"}
    app[CARD_SUMMARY_SESSION_KEYS_KEY]["om_card"] = active_key
    app[INTERACTION_RESULTS_KEY]["approval-active"] = {"status": "pending"}
    app[INTERACTION_RESULT_SESSION_KEYS_KEY]["approval-active"] = active_key

    sidecar_server._cleanup_failed_session_state(app, failed_key)

    assert app[SESSION_ALIASES_KEY] == {"om_reply": active_key}
    assert app[CARD_SUMMARIES_KEY] == {"om_card": {"summary": "active"}}
    assert app[CARD_SUMMARY_SESSION_KEYS_KEY] == {"om_card": active_key}
    assert app[INTERACTION_RESULTS_KEY] == {
        "approval-active": {"status": "pending"}
    }
    assert app[INTERACTION_RESULT_SESSION_KEYS_KEY] == {
        "approval-active": active_key
    }

    app[SESSIONS_KEY][failed_key] = CardSession("oc_abc", failed_key, "oc_abc")
    app[SESSION_ALIASES_KEY]["om_failed_reply"] = failed_key
    app[CARD_SUMMARIES_KEY]["om_failed_card"] = {"summary": "current"}
    app[CARD_SUMMARY_SESSION_KEYS_KEY]["om_failed_card"] = failed_key
    app[INTERACTION_RESULTS_KEY]["approval-current"] = {"status": "pending"}
    app[INTERACTION_RESULT_SESSION_KEYS_KEY]["approval-current"] = failed_key

    sidecar_server._cleanup_failed_session_state(app, failed_key)

    assert app[SESSION_ALIASES_KEY]["om_failed_reply"] == failed_key
    assert app[CARD_SUMMARIES_KEY]["om_failed_card"] == {"summary": "current"}
    assert app[CARD_SUMMARY_SESSION_KEYS_KEY]["om_failed_card"] == failed_key
    assert app[INTERACTION_RESULTS_KEY]["approval-current"] == {"status": "pending"}
    assert app[INTERACTION_RESULT_SESSION_KEYS_KEY]["approval-current"] == failed_key


async def test_started_routes_card_to_bound_bot_client():
    factory = FakeFeishuClientFactory()

    def bot_router(event):
        assert event.chat_id == "oc_sales"
        return RouteResult("sales", "bindings.chats")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        status = response.status
        body = await response.json()
    finally:
        await test_client.close()

    assert status == 200
    assert body == {"ok": True, "applied": True}
    assert factory.clients["default"].sent == []
    assert len(factory.clients["sales"].sent) == 1
    assert factory.clients["sales"].sent[0][0] == "oc_sales"


async def test_started_card_title_uses_bot_over_profile_and_global():
    factory = FakeFeishuClientFactory(
        cards={"sales": {"title": "Sales Bot"}},
        profile_card={"title": "Profile"},
    )

    def bot_router(event):
        return RouteResult("sales", "bindings.chats")

    app = create_app(
        factory,
        card_config={"title": "Global"},
        bot_router=bot_router,
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                chat_id="oc_sales",
            ),
        )
    finally:
        await test_client.close()

    assert response.status == 200
    sent_card = factory.clients["sales"].sent[0][1]
    assert sent_card["header"]["title"]["content"] == "Sales Bot"


@pytest.mark.parametrize("profile_id", ["bad:profile/path", "", "x" * 65])
async def test_invalid_profile_routes_with_default_factory_and_health_key(profile_id):
    factory = FakeFeishuClientFactory()

    def bot_router(event):
        return RouteResult("sales", "bindings.chats")

    app = create_app({"default": factory, "sales": factory}, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                {"profile_id": profile_id, "profile_source": "env"},
                chat_id="oc_sales",
            ),
        )
        status = response.status
        body = await response.json()
        health = await test_client.get("/health")
        health_body = await health.json()
    finally:
        await test_client.close()

    assert status == 200
    assert body == {"ok": True, "applied": True}
    assert factory.clients["default"].sent == []
    assert len(factory.clients["sales"].sent) == 1
    assert profile_id not in health_body["profile_diagnostics"]
    assert health_body["profile_diagnostics"]["default"]["events"] == 1
    assert health_body["routing"]["last_route_error"] == ""


async def test_update_reuses_original_bot_without_rerouting():
    factory = FakeFeishuClientFactory()
    route_calls = 0

    def bot_router(event):
        nonlocal route_calls
        route_calls += 1
        if route_calls == 1:
            return ("sales", "bindings.chats")
        raise AssertionError("updates must not reroute")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        started = await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        completed = await test_client.post(
            "/events",
            json=event_payload(
                "message.completed",
                1,
                {"answer": "成交"},
                chat_id="oc_sales",
            ),
        )
        await wait_for_card_update(factory.clients["sales"], "成交")
    finally:
        await test_client.close()

    assert started.status == 200
    assert completed.status == 200
    assert route_calls == 1
    assert factory.clients["default"].updated == []
    assert len(factory.clients["sales"].updated) == 1
    assert factory.clients["sales"].updated[0][0] == "feishu-message-1"


async def test_health_reports_safe_routing_diagnostics_without_secrets():
    factory = FakeFeishuClientFactory()

    def bot_router(event):
        return ("sales", "bindings.chats")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        response = await test_client.get("/health")
        status = response.status
        body = await response.json()
    finally:
        await test_client.close()

    assert status == 200
    routing = body["routing"]
    assert routing["default_bot"] == "default"
    assert routing["bot_count"] == 2
    assert routing["chat_binding_count"] == 1
    assert routing["last_route"]["bot_id"] == "sales"
    assert routing["last_route"]["reason"] == "bindings.chats"
    assert routing["last_route"]["chat_id_hash"]
    assert routing["last_route"]["message_id_hash"]
    assert routing["last_route_error"] == ""
    assert "chat_id" not in routing["last_route"]
    assert "message_id" not in routing["last_route"]
    assert "registry-secret" not in str(body)
    assert "oc_sales" not in str(body)
    assert "hermes-message-1" not in str(body)
    assert "secret" not in routing


async def test_health_routing_groups_multi_profile_diagnostics_without_secrets():
    factories = {
        "default": FakeFeishuClientFactory(),
        "work": FakeFeishuClientFactory(),
    }

    def bot_router(event):
        return ("sales", "bindings.chats")

    app = create_app(factories, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post(
            "/events",
            json=event_payload(
                "message.started",
                0,
                {"profile_id": "work", "profile_source": "env"},
                chat_id="oc_sales",
            ),
        )
        response = await test_client.get("/health")
        body = await response.json()
    finally:
        await test_client.close()

    routing = body["routing"]
    assert routing["profile_count"] == 2
    assert routing["bot_count"] == 4
    assert routing["chat_binding_count"] == 2
    assert routing["last_route"]["profile_id"] == "work"
    assert routing["last_route"]["bot_id"] == "sales"
    assert routing["last_route"]["reason"] == "bindings.chats"
    assert routing["last_route"]["chat_id_hash"]
    assert routing["last_route"]["message_id_hash"]
    assert routing["profiles"]["work"]["bot_count"] == 2
    assert routing["profiles"]["work"]["chat_binding_count"] == 1
    assert routing["profiles"]["work"]["last_route"]["bot_id"] == "sales"
    assert routing["profiles"]["work"]["last_route"]["chat_id_hash"]
    assert routing["profiles"]["work"]["last_route"]["message_id_hash"]
    assert routing["profiles"]["work"]["last_route_error"] == ""
    assert "chat_id" not in routing["profiles"]["work"]["last_route"]
    assert "message_id" not in routing["profiles"]["work"]["last_route"]
    assert "registry-secret" not in str(routing)
    assert "oc_sales" not in str(body)
    assert "hermes-message-1" not in str(body)
    assert "secret" not in routing["profiles"]["work"]


async def test_route_failure_returns_502_and_reports_safe_error():
    factory = FakeFeishuClientFactory()

    def bot_router(event):
        raise RuntimeError("cannot route with app_secret=super-secret")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        failed = await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        failed_status = failed.status
        failed_body = await failed.json()
        health = await test_client.get("/health")
        health_body = await health.json()
    finally:
        await test_client.close()

    assert failed_status == 502
    assert failed_body == {"ok": False, "error": "bot route failed"}
    assert factory.clients["default"].sent == []
    assert factory.clients["sales"].sent == []
    assert health_body["active_sessions"] == 0
    assert health_body["metrics"]["events_rejected"] == 1
    assert health_body["diagnostics"]["last_route_error"] == "RuntimeError"
    assert health_body["routing"]["last_route_error"] == "RuntimeError"
    assert "super-secret" not in str(health_body)


async def test_routed_update_failure_reports_safe_bot_id():
    factory = FakeFeishuClientFactory()
    factory.clients["sales"].update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS

    def bot_router(event):
        return ("sales", "bindings.chats")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        started = await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        updated = await test_client.post(
            "/events",
            json=event_payload(
                "thinking.delta",
                1,
                {"text": "需要更新"},
                chat_id="oc_sales",
            ),
        )
        health = await test_client.get("/health")
        health_body = await health.json()
    finally:
        await test_client.close()

    assert started.status == 200
    assert updated.status == 200
    # Lock optimization: non-terminal update failures no longer return 502
    assert health_body["diagnostics"]["last_update_error"] == "bot_id=sales RuntimeError"
    assert "secret" not in health_body["diagnostics"]["last_update_error"].lower()
    assert "token" not in health_body["diagnostics"]["last_update_error"].lower()


async def test_routed_update_failure_redacts_sensitive_exception_text():
    factory = FakeFeishuClientFactory()
    factory.clients["sales"].update_failures_remaining = sidecar_server.UPDATE_MAX_ATTEMPTS
    factory.clients["sales"].update_error_message = (
        "Authorization Bearer tenant-token-secret app_secret=super-secret"
    )

    def bot_router(event):
        return ("sales", "bindings.chats")

    app = create_app(factory, bot_router=bot_router)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        await test_client.post(
            "/events",
            json=event_payload("message.started", 0, chat_id="oc_sales"),
        )
        updated = await test_client.post(
            "/events",
            json=event_payload(
                "thinking.delta",
                1,
                {"text": "需要更新"},
                chat_id="oc_sales",
            ),
        )
        health = await test_client.get("/health")
        health_body = await health.json()
    finally:
        await test_client.close()

    last_update_error = health_body["diagnostics"]["last_update_error"]
    assert updated.status == 200
    # Lock optimization: non-terminal update failures no longer return 502
    assert "bot_id=sales" in last_update_error
    assert "RuntimeError" in last_update_error
    assert "tenant-token-secret" not in last_update_error
    assert "super-secret" not in last_update_error
    assert "Authorization" not in last_update_error
    assert "Bearer" not in last_update_error


async def test_session_key_uses_profile_id():
    from hermes_feishu_card.server import _session_key
    from hermes_feishu_card.events import SidecarEvent
    event = SidecarEvent(
        schema_version="1", event="message.started",
        conversation_id="c", message_id="m", chat_id="c",
        platform="feishu", sequence=0, created_at=0.0,
        data={"profile_id": "work"},
    )
    assert _session_key(event) == "work:m"


async def test_session_key_no_profile_is_just_message_id():
    from hermes_feishu_card.server import _session_key
    from hermes_feishu_card.events import SidecarEvent
    event = SidecarEvent(
        schema_version="1", event="message.started",
        conversation_id="c", message_id="m", chat_id="c",
        platform="feishu", sequence=0, created_at=0.0,
        data={},
    )
    assert _session_key(event) == "m"


async def test_session_key_explicit_empty_profile_uses_default_composite_key():
    from hermes_feishu_card.server import _session_key
    from hermes_feishu_card.events import SidecarEvent
    event = SidecarEvent(
        schema_version="1", event="message.started",
        conversation_id="c", message_id="m", chat_id="c",
        platform="feishu", sequence=0, created_at=0.0,
        data={"profile_id": ""},
    )
    assert _session_key(event) == "default:m"


# --- Tests for _abandon_stale_sessions_for_chat (interrupt scenario, #92) ---


async def test_interrupt_abandons_stale_session_via_session_creating_event(client):
    """When a new session is created via SESSION_CREATING_EVENTS (e.g. answer.delta
    after an interrupt with no message.started), the old active session for the
    same chat+conversation should be marked completed and its card updated."""
    test_client, feishu_client = client

    # First turn: message.started + some streaming
    msg1 = {"conversation_id": "conv-1", "message_id": "msg-turn-1"}
    await test_client.post("/events", json=event_payload("message.started", 0, **msg1))
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "正在回答第一个问题"}, **msg1),
    )
    await wait_for_card_update(feishu_client, "正在回答第一个问题")

    # Interrupt: a new turn arrives via SESSION_CREATING_EVENTS (answer.delta with
    # a different message_id but same conversation_id — simulates the gateway
    # interrupt fallback path where event_message_id changes).
    msg2 = {"conversation_id": "conv-1", "message_id": "msg-turn-2"}
    resp = await test_client.post(
        "/events",
        json=event_payload("answer.delta", 0, {"text": "新的回答"}, **msg2),
    )
    assert resp.status == 200

    # The old card should have been updated to completed state.
    # Two cards were sent (one for each session).
    assert len(feishu_client.sent) == 2

    # The old card (feishu-message-1) should have a final update showing completed
    updates_for_old = [
        card for mid, card in feishu_client.updated if mid == "feishu-message-1"
    ]
    # At least 2 updates: the streaming delta + the abandon completion
    assert len(updates_for_old) >= 2
    # The last update should contain the completed marker (subtitle)
    last_card = str(updates_for_old[-1])
    assert "已完成" in last_card


async def test_interrupt_abandons_stale_session_via_message_started(client):
    """When a new message.started arrives with a different message_id but same
    conversation, the old active session should be abandoned."""
    test_client, feishu_client = client

    msg1 = {"conversation_id": "conv-1", "message_id": "msg-first"}
    await test_client.post("/events", json=event_payload("message.started", 0, **msg1))
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "第一轮内容"}, **msg1),
    )
    await wait_for_card_update(feishu_client, "第一轮内容")

    # New turn with different message_id, same conversation
    msg2 = {"conversation_id": "conv-1", "message_id": "msg-second"}
    await test_client.post("/events", json=event_payload("message.started", 0, **msg2))

    # Two cards sent (old + new)
    assert len(feishu_client.sent) == 2

    # Old card should have been updated with completed state
    updates_for_old = [
        card for mid, card in feishu_client.updated if mid == "feishu-message-1"
    ]
    assert len(updates_for_old) >= 2
    assert "已完成" in str(updates_for_old[-1])


async def test_interrupt_does_not_abandon_different_conversation(client):
    """Sessions in different conversations (e.g. different topic group threads)
    should NOT be abandoned when a new session is created."""
    test_client, feishu_client = client

    # Two different conversations in the same chat
    msg1 = {"conversation_id": "thread-A", "message_id": "msg-thread-a"}
    msg2 = {"conversation_id": "thread-B", "message_id": "msg-thread-b"}

    await test_client.post("/events", json=event_payload("message.started", 0, **msg1))
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "Thread A 内容"}, **msg1),
    )
    await wait_for_card_update(feishu_client, "Thread A 内容")
    updates_before = len(feishu_client.updated)

    # New session in a DIFFERENT conversation — should NOT abandon thread-A
    await test_client.post("/events", json=event_payload("message.started", 0, **msg2))

    # Give any background tasks a chance to run
    await asyncio.sleep(0.05)

    # Old card (feishu-message-1) should NOT have gotten an abandon update
    updates_after = feishu_client.updated[updates_before:]
    old_card_updates = [card for mid, card in updates_after if mid == "feishu-message-1"]
    assert len(old_card_updates) == 0, (
        "Session in different conversation should not be abandoned"
    )

async def test_terminal_event_on_abandoned_session_returns_applied_true(client):
    """When message.completed arrives for a session that was already abandoned
    (status=completed), the sidecar should return applied=True so the gateway
    hook suppresses the native plain-text delivery."""
    test_client, feishu_client = client

    msg1 = {"conversation_id": "conv-1", "message_id": "msg-original"}
    await test_client.post("/events", json=event_payload("message.started", 0, **msg1))
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 1, {"text": "部分回答"}, **msg1),
    )
    await wait_for_card_update(feishu_client, "部分回答")

    # Trigger abandon by creating a new session in the same conversation
    msg2 = {"conversation_id": "conv-1", "message_id": "msg-followup"}
    await test_client.post(
        "/events",
        json=event_payload("answer.delta", 0, {"text": "新回答"}, **msg2),
    )
    await wait_for_card_update(feishu_client, "已完成")
    old_updates_before_late_terminal = len(
        [
            card
            for message_id, card in feishu_client.updated
            if message_id == "feishu-message-1"
        ]
    )

    # Now send message.completed for the OLD session — should report applied=True
    response = await test_client.post(
        "/events",
        json=event_payload("message.completed", 2, {"answer": "最终答案"}, **msg1),
    )
    result = await response.json()
    await asyncio.sleep(0.05)
    assert result["ok"] is True
    assert result["applied"] is True
    old_updates_after_late_terminal = len(
        [
            card
            for message_id, card in feishu_client.updated
            if message_id == "feishu-message-1"
        ]
    )
    assert old_updates_after_late_terminal == old_updates_before_late_terminal


async def test_interrupt_abandon_does_not_affect_completed_sessions(client):
    """Sessions that are already completed should not be re-abandoned or cause
    extra card updates when a new session is created."""
    test_client, feishu_client = client

    msg1 = {"conversation_id": "conv-1", "message_id": "msg-done"}
    await test_client.post("/events", json=event_payload("message.started", 0, **msg1))
    await test_client.post(
        "/events",
        json=event_payload("message.completed", 1, {"answer": "完成了"}, **msg1),
    )
    await wait_for_card_update(feishu_client, "完成了")
    updates_after_complete = len(feishu_client.updated)

    # New session in same conversation — should not touch the completed one
    msg2 = {"conversation_id": "conv-1", "message_id": "msg-new-turn"}
    await test_client.post("/events", json=event_payload("message.started", 0, **msg2))

    # No extra card updates should happen for the old completed session
    await asyncio.sleep(0.05)
    new_updates = feishu_client.updated[updates_after_complete:]
    for mid, _ in new_updates:
        assert mid != "feishu-message-1", (
            "Already-completed session should not get extra updates on abandon"
        )
