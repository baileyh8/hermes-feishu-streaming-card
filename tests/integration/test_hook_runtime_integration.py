import asyncio
import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import hook_runtime
from hermes_feishu_card import server as sidecar_server
from hermes_feishu_card.diagnostics import DiagnosticFinding, DiagnosticReport
from hermes_feishu_card.feishu_client import FeishuAPIError
from hermes_feishu_card.server import create_app
from hermes_feishu_card.operations_transport import ensure_transport_root_secret


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"


@pytest.fixture(autouse=True)
def reset_hook_runtime_state():
    hook_runtime.reset_runtime_state()
    yield
    hook_runtime.reset_runtime_state()


def _operation_token(operation_id="operation-1"):
    payload = json.dumps(
        {
            "operation_id": operation_id,
            "action": "repair",
            "report_fingerprint": "report-1",
            "expires_at": 9999999999,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=") + ".signature"


class Message:
    chat_id = "oc_fixture"
    message_id = "msg_fixture"
    text = "fixture answer"


class Hooks:
    def __init__(self):
        self.events = []

    def emit(self, name, data):
        self.events.append((name, data))


def copy_hermes(tmp_path):
    hermes_dir = tmp_path / "hermes"
    shutil.copytree(FIXTURE, hermes_dir)
    return hermes_dir


def run_cli(*args):
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, "-m", "hermes_feishu_card.cli", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def load_run_py(path):
    spec = importlib.util.spec_from_file_location("fixture_run", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def wait_for_event_count(received, expected_count, event, timeout=1):
    if len(received) >= expected_count:
        return
    await asyncio.wait_for(event.wait(), timeout=timeout)
    assert len(received) >= expected_count


async def test_installed_hook_preserves_handler_return_when_sender_fails(
    tmp_path, monkeypatch
):
    hermes_dir = copy_hermes(tmp_path)
    sender_called = asyncio.Event()

    async def failing_post_json(url, payload, timeout):
        sender_called.set()
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(hook_runtime, "_post_json", failing_post_json)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    install = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
    assert install.returncode == 0, install.stderr
    module = load_run_py(hermes_dir / "gateway" / "run.py")
    hooks = Hooks()

    result = await module._handle_message_with_agent(Message(), hooks)

    assert result == "fixture answer"
    await asyncio.wait_for(sender_called.wait(), timeout=1)
    assert len(hooks.events) == 1
    assert hooks.events[0][0] == "agent:end"
    assert hooks.events[0][1]["message"].chat_id == "oc_fixture"


async def test_installed_hook_posts_started_event_to_mock_sidecar(tmp_path, monkeypatch):
    received = []
    received_event = asyncio.Event()

    async def events(request):
        received.append(await request.json())
        received_event.set()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/events", events)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        hermes_dir = copy_hermes(tmp_path)
        monkeypatch.setenv(
            "HERMES_FEISHU_CARD_EVENT_URL",
            str(client.make_url("/events")),
        )
        install = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
        assert install.returncode == 0, install.stderr
        module = load_run_py(hermes_dir / "gateway" / "run.py")

        result = await module._handle_message_with_agent(Message(), Hooks())

        assert result in (None, "fixture answer")
        await asyncio.wait_for(received_event.wait(), timeout=1)
        assert received
        assert received[0]["event"] == "message.started"
        assert received[0]["chat_id"] == "oc_fixture"
        assert received[0]["message_id"] == "msg_fixture"
    finally:
        await client.close()


async def test_installed_hook_forwards_streaming_tool_and_completion_events(
    tmp_path, monkeypatch
):
    received = []
    received_count = asyncio.Event()

    async def events(request):
        received.append(await request.json())
        if len(received) >= 5:
            received_count.set()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/events", events)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        hermes_dir = copy_hermes(tmp_path)
        monkeypatch.setenv(
            "HERMES_FEISHU_CARD_EVENT_URL",
            str(client.make_url("/events")),
        )
        hook_runtime.reset_runtime_state()
        install = run_cli("install", "--hermes-dir", str(hermes_dir), "--yes")
        assert install.returncode == 0, install.stderr
        module = load_run_py(hermes_dir / "gateway" / "run.py")

        result = await module._handle_message_with_agent(Message(), Hooks())

        assert result in (None, "fixture answer")
        await wait_for_event_count(received, 5, received_count)
        assert [item["event"] for item in received] == [
            "message.started",
            "thinking.delta",
            "tool.updated",
            "answer.delta",
            "message.completed",
        ]
        assert {item["chat_id"] for item in received} == {"oc_fixture"}
        assert {item["message_id"] for item in received} == {"msg_fixture"}
        assert received[1]["data"] == {
            "profile_id": "default",
            "profile_source": "fallback_default",
            "text": "thinking fixture delta",
            "mode": "append_block",
        }
        assert received[2]["data"] == {
            "profile_id": "default",
            "profile_source": "fallback_default",
            "tool_id": "fixture_tool",
            "name": "fixture_tool",
            "status": "running",
            "detail": "fixture tool preview",
            "arguments": {"query": "fixture"},
        }
        assert received[3]["data"] == {
            "profile_id": "default",
            "profile_source": "fallback_default",
            "text": "answer fixture delta",
        }
        assert received[4]["data"] == {
            "profile_id": "default",
            "profile_source": "fallback_default",
            "answer": "fixture answer",
            "duration": 0.25,
            "model": "Unknown",
            "tokens": {"input_tokens": 7, "output_tokens": 11},
            "context": {"used_tokens": 0, "max_tokens": 0},
            "attachments": [],
            "native_delivery": "allowed",
        }
    finally:
        await client.close()


class _CallbackCard:
    def __init__(self):
        self.type = None
        self.data = None


class _CallbackResponse:
    def __init__(self):
        self.card = None


def _card_action_data(action, *, open_id="ou_operator", chat_id="oc_group"):
    return SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(value=action),
            context=SimpleNamespace(open_chat_id=chat_id),
            operator=SimpleNamespace(open_id=open_id, user_id="user-1"),
        )
    )


def _installed_action_adapter(*, allowed=True):
    class Adapter:
        name = "feishu"

        def __init__(self):
            self.allowed = []
            self.native_actions = []
            self.gray_messages = []

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            self.allowed.append((sender_id.open_id, chat_id, is_bot))
            return allowed

        def _on_card_action_trigger(self, data):
            self.native_actions.append(data)
            return "native-fallback"

        async def _handle_card_action_event(self, data):
            self.gray_messages.append(data)

    Adapter.__module__ = hook_runtime.__name__
    adapter = Adapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True
    return adapter


class _OperationsFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []

    async def send_card(
        self, chat_id, card, thread_id=None, reply_to_message_id=None
    ):
        self.sent.append((chat_id, card, thread_id, reply_to_message_id))
        return f"operations-message-{len(self.sent)}"

    async def update_card_message(self, message_id, card):
        self.updated.append((message_id, card))


class _NoticeFailureClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.delivery_calls = []

    async def send_card_delivery(self, *args, **kwargs):
        self.delivery_calls.append((args, kwargs))
        raise FeishuAPIError(
            "safe failure",
            status_code=400 if self.outcome == "not_sent" else 503,
            api_code=9499,
            retryable=self.outcome == "unknown",
            outcome=self.outcome,
            retry_count=0 if self.outcome == "not_sent" else 2,
        )

    async def update_card_message(self, message_id, card):
        return None


@pytest.mark.parametrize(
    ("outcome", "expected_content", "metric_name"),
    [
        (
            "not_sent",
            "ℹ️ Codex gpt-5.5 caps context at 272K, so auto-compaction was raised to 85%.",
            "notice_native_fallbacks",
        ),
        (
            "unknown",
            "⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。",
            "notice_uncertain_warnings",
        ),
    ],
)
async def test_installed_notice_hook_uses_sidecar_delivery_outcome_for_topic_fallback(
    monkeypatch,
    outcome,
    expected_content,
    metric_name,
):
    feishu_client = _NoticeFailureClient(outcome)
    app = create_app(feishu_client)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    try:
        monkeypatch.setenv(
            "HERMES_FEISHU_CARD_EVENT_URL",
            str(test_client.make_url("/events")),
        )

        class Adapter:
            name = "feishu"

            def __init__(self):
                self._client = object()
                self.native_calls = []

            async def send(self, chat_id, content, reply_to=None, metadata=None):
                self.native_calls.append((chat_id, content, reply_to, metadata))
                return SimpleNamespace(
                    success=True,
                    message_id="om_native_fallback",
                    error="",
                )

        adapter = Adapter()
        runner = SimpleNamespace(adapters={"feishu": adapter})
        event = SimpleNamespace(
            source=SimpleNamespace(
                platform="feishu",
                chat_id="oc_topic",
                message_id="om_topic_user",
                thread_id="omt_topic",
            ),
            message_id="om_topic_user",
            thread_id="omt_topic",
            get_command=lambda: "",
        )
        assert hook_runtime.install_feishu_command_card_adapter_methods(
            runner,
            event=event,
        )

        original_content = (
            "ℹ️ Codex gpt-5.5 caps context at 272K, "
            "so auto-compaction was raised to 85%."
        )
        result = await adapter.send(
            "oc_topic",
            original_content,
            reply_to="om_topic_user",
            metadata={"thread_id": "omt_topic"},
        )
        health = await (await test_client.get("/health")).json()
    finally:
        await test_client.close()

    assert result.success is True
    assert adapter.native_calls == [
        (
            "oc_topic",
            expected_content,
            "om_topic_user",
            {"thread_id": "omt_topic"},
        )
    ]
    assert len(feishu_client.delivery_calls) == 1
    delivery_kwargs = feishu_client.delivery_calls[0][1]
    assert delivery_kwargs["reply_to_message_id"] == "om_topic_user"
    assert delivery_kwargs["thread_id"] == "omt_topic"
    assert delivery_kwargs["delivery_uuid"].startswith("hfc_")
    metrics = health["metrics"]
    assert metrics[metric_name] == 1
    assert metrics["feishu_send_retries"] == (0 if outcome == "not_sent" else 2)


async def test_hook_event_proof_is_accepted_by_authenticated_sidecar(
    tmp_path,
    monkeypatch,
):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(state_dir))
    root_secret = ensure_transport_root_secret(state_dir)
    app = create_app(
        _OperationsFeishuClient(),
        operations_transport_root_secret=root_secret,
        event_auth_required=True,
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    payload = {
        "schema_version": "1",
        "event": "message.started",
        "conversation_id": "conversation-authenticated",
        "message_id": "message-authenticated",
        "chat_id": "oc_authenticated",
        "platform": "feishu",
        "sequence": 0,
        "created_at": 1777017600.0,
        "data": {},
    }
    try:
        await hook_runtime._post_json(
            str(test_client.make_url("/events")),
            payload,
            0.8,
        )
        health = await (await test_client.get("/health")).json()
    finally:
        await test_client.close()

    assert health["event_auth_required"] is True
    assert health["metrics"]["events_received"] == 1
    assert health["metrics"]["events_applied"] == 1
    assert health["metrics"]["event_auth_rejections"] == 0


def _operations_report():
    return DiagnosticReport(
        status="warning",
        created_at=100.0,
        config={"loaded": True},
        hermes={"status": "supported"},
        streaming={"status": "enabled"},
        install_state={
            "status": "incomplete",
            "recovery_executable": True,
            "recovery_fingerprint": "recovery-integration",
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
    )


def _operations_button(card, label):
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


async def _wait_for_sent_card(client, count):
    for _ in range(100):
        if len(client.sent) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("operations card was not sent")


async def _establish_operations_card(client, *, chat_id, chat_type, operator, index):
    handled = await asyncio.to_thread(
        hook_runtime.handle_hfc_command_from_hermes_locals,
        {
            "platform": "feishu",
            "chat_id": chat_id,
            "message_id": f"om_doctor_{index}",
            "text": "/hfc doctor",
            "chat_type": chat_type,
            "operator_open_id": operator,
        },
    )
    assert handled is True
    await _wait_for_sent_card(client, index)
    return client.sent[index - 1][1]


async def _click_operations(adapter, value, *, chat_id, operator):
    return await asyncio.to_thread(
        adapter._on_card_action_trigger,
        _card_action_data(value, open_id=operator, chat_id=chat_id),
    )


async def _wait_for_operations_dispatch():
    await asyncio.wait_for(
        asyncio.to_thread(hook_runtime._OPERATIONS_ACTION_DISPATCHER.wait),
        timeout=2.0,
    )


async def _click_operations_and_wait_for_card(
    adapter, value, *, chat_id, operator, feishu_client
):
    update_count = len(feishu_client.updated)
    response = await _click_operations(
        adapter, value, chat_id=chat_id, operator=operator
    )
    await _wait_for_operations_dispatch()
    await _wait_for(lambda: len(feishu_client.updated) > update_count)
    return response, feishu_client.updated[-1][1]


async def test_installed_ws_hook_uses_real_http_for_operator_and_auth_matrix(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(hook_runtime, "CallBackCard", _CallbackCard, raising=False)
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", _CallbackResponse, raising=False
    )
    report = _operations_report()
    detection = SimpleNamespace(root=Path("/private/hermes"))
    recovery_calls = []
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, detection),
    )
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args: recovery_calls.append(args) or SimpleNamespace(status="repaired"),
    )
    monkeypatch.setattr(sidecar_server.shutil, "which", lambda command: None)

    state_dir = tmp_path / "state"
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(state_dir))
    root_secret = ensure_transport_root_secret(state_dir)
    feishu_client = _OperationsFeishuClient()
    app = create_app(
        feishu_client,
        operations_transport_root_secret=root_secret,
    )
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    hook_runtime.reset_runtime_state()
    monkeypatch.setenv(
        "HERMES_FEISHU_CARD_EVENT_URL",
        str(test_client.make_url("/events")),
    )
    monkeypatch.setenv("HERMES_FEISHU_CARD_PROFILE_ID", "work")
    adapter = _installed_action_adapter()
    try:
        group_card = await _establish_operations_card(
            feishu_client,
            chat_id="oc_group",
            chat_type="group",
            operator="ou_owner",
            index=1,
        )
        monkeypatch.setenv("HERMES_FEISHU_CARD_PROFILE_ID", "default")
        repair = _operations_button(group_card, "安全修复")
        operation_id = hook_runtime._operation_id_from_token(repair["token"])
        authentic_secret = hook_runtime._transport_secret_for_token(repair["token"])
        assert authentic_secret is not None

        hook_runtime._remember_operation_transport(
            operation_id, "forged-process-secret"
        )
        rejected_auth = await _click_operations(
            adapter, repair, chat_id="oc_group", operator="ou_owner"
        )
        assert rejected_auth.card is None
        await _wait_for_operations_dispatch()
        hook_runtime._remember_operation_transport(operation_id, authentic_secret)

        owner_response, owner_card = await _click_operations_and_wait_for_card(
            adapter,
            repair,
            chat_id="oc_group",
            operator="ou_owner",
            feishu_client=feishu_client,
        )
        assert owner_response.card is None
        confirm = _operations_button(owner_card, "确认修复")
        other_response, other_card = await _click_operations_and_wait_for_card(
            adapter,
            confirm,
            chat_id="oc_group",
            operator="ou_other",
            feishu_client=feishu_client,
        )
        assert other_response.card is None
        assert _operations_button(other_card, "确认修复")
        group_update_start = len(feishu_client.updated)
        completed_group, _executing_group_card = await _click_operations_and_wait_for_card(
            adapter,
            confirm,
            chat_id="oc_group",
            operator="ou_owner",
            feishu_client=feishu_client,
        )
        assert completed_group.card is None
        assert any(
            "正在安全修复" in str(card)
            for _message_id, card in feishu_client.updated[group_update_start:]
        )
        await _wait_for(
            lambda: any(
                "安全修复已完成" in str(card)
                for _message_id, card in feishu_client.updated
            )
        )

        private_card = await _establish_operations_card(
            feishu_client,
            chat_id="oc_private",
            chat_type="private",
            operator="ou_first",
            index=2,
        )
        private_repair = _operations_button(private_card, "安全修复")
        private_response, private_confirm_card = await _click_operations_and_wait_for_card(
            adapter,
            private_repair,
            chat_id="oc_private",
            operator="ou_first",
            feishu_client=feishu_client,
        )
        assert private_response.card is None
        private_confirm = _operations_button(private_confirm_card, "确认修复")
        private_update_start = len(feishu_client.updated)
        completed_private, _executing_private_card = await _click_operations_and_wait_for_card(
            adapter,
            private_confirm,
            chat_id="oc_private",
            operator="ou_second",
            feishu_client=feishu_client,
        )
        assert completed_private.card is None
        assert any(
            "正在安全修复" in str(card)
            for _message_id, card in feishu_client.updated[private_update_start:]
        )
        await _wait_for(
            lambda: sum(
                "安全修复已完成" in str(card)
                for _message_id, card in feishu_client.updated
            )
            == 2
        )

        await adapter._handle_card_action_event(
            _card_action_data(repair, open_id="ou_owner", chat_id="oc_group")
        )
    finally:
        await test_client.close()

    assert len(recovery_calls) == 2
    assert adapter.native_actions == []
    assert adapter.gray_messages == []


async def test_ws_operations_recheck_returns_in_progress_card_and_keeps_successor_transport(
    monkeypatch,
    tmp_path,
):
    report = _operations_report()
    recheck_started = threading.Event()
    release_recheck = threading.Event()
    report_builds = 0
    report_builds_lock = threading.Lock()

    def blocked_report(*args, **kwargs):
        nonlocal report_builds
        with report_builds_lock:
            report_builds += 1
            is_recheck_build = report_builds == 2
        if is_recheck_build:
            recheck_started.set()
            release_recheck.wait(2.0)
        return report, SimpleNamespace(root=Path("/private/hermes"))

    monkeypatch.setattr(sidecar_server, "_build_operations_report_sync", blocked_report)
    monkeypatch.setattr(hook_runtime, "CallBackCard", _CallbackCard, raising=False)
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", _CallbackResponse, raising=False
    )
    hook_runtime.reset_runtime_state()
    state_dir = tmp_path / "state"
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(state_dir))
    root_secret = ensure_transport_root_secret(state_dir)
    feishu_client = _OperationsFeishuClient()
    app = create_app(feishu_client, operations_transport_root_secret=root_secret)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        monkeypatch.setenv(
            "HERMES_FEISHU_CARD_EVENT_URL", str(client.make_url("/events"))
        )
        adapter = _installed_action_adapter()
        initial_card = await _establish_operations_card(
            feishu_client,
            chat_id="oc_private",
            chat_type="private",
            operator="ou_owner",
            index=1,
        )
        recheck = _operations_button(initial_card, "重新检测")
        original_secret = hook_runtime._transport_secret_for_token(recheck["token"])

        response = await _click_operations(
            adapter, recheck, chat_id="oc_private", operator="ou_owner"
        )
        await _wait_for_operations_dispatch()
        successor_id = next(
            operation_id
            for operation_id, operation in app[
                sidecar_server.OPERATIONS_STORE_KEY
            ]._records.items()
            if operation.state == "preparing"
        )
        await asyncio.wait_for(asyncio.to_thread(recheck_started.wait), timeout=1.0)
        release_recheck.set()
        await _wait_for(
            lambda: any(
                "诊断摘要" in str(card)
                for _message_id, card in feishu_client.updated
            )
        )
        patched_recheck = _operations_button(feishu_client.updated[-1][1], "重新检测")
        patched_update_start = len(feishu_client.updated)
        patched_response = await _click_operations(
            adapter, patched_recheck, chat_id="oc_private", operator="ou_owner"
        )
        await _wait_for_operations_dispatch()
        await _wait_for(lambda: len(feishu_client.updated) > patched_update_start)
    finally:
        release_recheck.set()
        await client.close()

    assert response.card is None
    assert patched_response.card is None
    assert any(
        "正在重新检测" in str(card)
        for _message_id, card in feishu_client.updated[patched_update_start:]
    )
    assert hook_runtime._operation_transport_context(successor_id) == (
        original_secret,
        "default",
    )


@pytest.mark.parametrize(
    "operation_action",
    [
        "details",
        "recheck",
        "repair",
        "confirm_repair",
        "cancel",
        "restart",
        "confirm_restart",
        "dismiss",
    ],
)
def test_installed_ws_operations_actions_all_require_admission(
    monkeypatch, operation_action
):
    monkeypatch.setattr(hook_runtime, "CallBackCard", _CallbackCard, raising=False)
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", _CallbackResponse, raising=False
    )
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )
    posted = []
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_sync_response",
        lambda url, payload, timeout: posted.append((url, payload, timeout))
        or {"ok": True, "card": {"schema": "2.0"}},
    )
    token = _operation_token()
    hook_runtime._remember_operation_transport("operation-1", "process-local-secret")
    adapter = _installed_action_adapter()

    response = adapter._on_card_action_trigger(
        _card_action_data(
            {
                "hfc_action": "operations.select",
                "operation_action": operation_action,
                "token": token,
                "profile_scope": "opaque-scope",
            }
        )
    )
    hook_runtime._OPERATIONS_ACTION_DISPATCHER.wait()

    assert adapter.allowed == [("ou_operator", "oc_group", False)]
    assert adapter.native_actions == []
    assert posted[0][1]["event"]["action"]["value"]["operation_action"] == operation_action
    assert response.card is None


@pytest.mark.parametrize(
    ("allowed", "open_id"),
    [(False, "ou_denied"), (True, "")],
)
def test_installed_ws_rejected_operations_are_claimed_without_gray_fallback(
    monkeypatch, allowed, open_id
):
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", _CallbackResponse, raising=False
    )
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_sync_response",
        lambda *args: pytest.fail("rejected operation must not be forwarded"),
    )
    adapter = _installed_action_adapter(allowed=allowed)

    response = adapter._on_card_action_trigger(
        _card_action_data(
            {
                "hfc_action": "operations.select",
                "operation_action": "repair",
                "token": "opaque-token",
            },
            open_id=open_id,
        )
    )

    assert adapter.native_actions == []
    assert response.card is None


async def test_installed_ws_background_operations_suppress_native_gray_message():
    adapter = _installed_action_adapter()

    await adapter._handle_card_action_event(
        _card_action_data(
            {
                "hfc_action": "operations.select",
                "operation_action": "repair",
                "token": "opaque-token",
            }
        )
    )

    assert adapter.gray_messages == []


def test_installed_ws_unknown_action_keeps_native_fallback():
    adapter = _installed_action_adapter()
    data = _card_action_data({"hfc_action": "future.namespace"})

    response = adapter._on_card_action_trigger(data)

    assert response == "native-fallback"
    assert adapter.native_actions == [data]


def test_installed_ws_interaction_select_behavior_is_unchanged(monkeypatch):
    monkeypatch.setattr(hook_runtime, "CallBackCard", _CallbackCard, raising=False)
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", _CallbackResponse, raising=False
    )
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )
    posted = []
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_sync_response",
        lambda url, payload, timeout: posted.append(payload)
        or {"ok": True, "card": {"schema": "2.0"}},
    )
    adapter = _installed_action_adapter(allowed=False)

    response = adapter._on_card_action_trigger(
        _card_action_data(
            {
                "hfc_action": "interaction.select",
                "interaction_id": "interaction-1",
                "choice": "approve",
                "choice_label": "Approve",
                "token": "interaction-token",
            }
        )
    )

    assert adapter.allowed == []
    assert adapter.native_actions == []
    assert posted[0]["event"]["action"]["value"]["hfc_action"] == "interaction.select"
    assert response.card.type == "raw"


class _OperationsFeishuClient:
    def __init__(self):
        self.sent = []
        self.updated = []

    async def send_card(
        self, chat_id, card, thread_id=None, reply_to_message_id=None
    ):
        self.sent.append((chat_id, card, thread_id, reply_to_message_id))
        return f"message-{len(self.sent)}"

    async def update_card_message(self, message_id, card):
        self.updated.append((message_id, card))


def _operations_report():
    return DiagnosticReport(
        status="warning",
        created_at=100.0,
        config={"loaded": True},
        hermes={"status": "supported"},
        streaming={"status": "enabled"},
        install_state={
            "status": "incomplete",
            "recovery_executable": True,
            "recovery_fingerprint": "recovery-real-http",
        },
        routing={"profile_id": "default"},
        runtime={},
        findings=(
            DiagnosticFinding(
                code="repairable",
                severity="warning",
                message="Repair is available.",
            ),
        ),
    )


def _operations_button(card, label):
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


async def _wait_for(predicate, attempts=100):
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached")


async def test_ws_hook_to_real_local_actions_enforces_transport_scope_ownership_expiry_once(
    monkeypatch,
    tmp_path,
):
    report = _operations_report()
    detection = SimpleNamespace(root=Path("/private/hermes"))
    recovery_calls = []
    feishu_client = _OperationsFeishuClient()
    monkeypatch.setattr(
        sidecar_server,
        "_build_operations_report_sync",
        lambda *args, **kwargs: (report, detection),
    )
    monkeypatch.setattr(
        sidecar_server,
        "execute_recovery",
        lambda *args: recovery_calls.append(args) or SimpleNamespace(status="repaired"),
    )
    monkeypatch.setattr(sidecar_server.shutil, "which", lambda command: None)
    monkeypatch.setattr(hook_runtime, "CallBackCard", _CallbackCard, raising=False)
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", _CallbackResponse, raising=False
    )
    hook_runtime.reset_runtime_state()
    state_dir = tmp_path / "state"
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(state_dir))
    root_secret = ensure_transport_root_secret(state_dir)
    app = sidecar_server.create_app(
        feishu_client,
        operations_transport_root_secret=root_secret,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        monkeypatch.setenv(
            "HERMES_FEISHU_CARD_EVENT_URL", str(client.make_url("/events"))
        )
        command_locals = {
            "source": SimpleNamespace(platform="feishu", chat_id="oc_group"),
            "event": SimpleNamespace(
                text="/hfc doctor",
                message_id="om_doctor",
                chat_type="group",
                operator=SimpleNamespace(open_id="ou_owner"),
            ),
            "message_id": "om_doctor",
        }
        assert await asyncio.to_thread(
            hook_runtime.handle_hfc_command_from_hermes_locals, command_locals
        )
        await _wait_for(lambda: len(feishu_client.sent) == 1)
        initial_card = feishu_client.sent[0][1]
        repair = _operations_button(initial_card, "安全修复")
        secret = hook_runtime._transport_secret_for_token(repair["token"])
        assert secret
        serialized_card = str(initial_card)
        assert secret.hex() not in serialized_card
        assert base64.urlsafe_b64encode(secret).decode("ascii") not in serialized_card
        assert "transport_id" not in str(initial_card)

        unsigned = {
            "event": {
                "action": {"value": repair},
                "context": {"open_chat_id": "oc_group"},
                "operator": {"open_id": "ou_owner"},
            }
        }
        unsigned_response = await client.post("/card/actions", json=unsigned)
        assert unsigned_response.status == 403

        adapter = _installed_action_adapter()
        wrong_scope = await asyncio.to_thread(
            adapter._on_card_action_trigger,
            _card_action_data(repair, open_id="ou_owner", chat_id="oc_other"),
        )
        assert wrong_scope.card is None
        await _wait_for_operations_dispatch()

        wrong_owner, wrong_owner_card = await _click_operations_and_wait_for_card(
            adapter,
            repair,
            chat_id="oc_group",
            operator="ou_other",
            feishu_client=feishu_client,
        )
        assert wrong_owner.card is None
        assert "安全修复" in str(wrong_owner_card)

        first, confirmation_card = await _click_operations_and_wait_for_card(
            adapter,
            repair,
            chat_id="oc_group",
            operator="ou_owner",
            feishu_client=feishu_client,
        )
        assert first.card is None
        confirm = _operations_button(confirmation_card, "确认修复")
        duplicate_update_start = len(feishu_client.updated)
        duplicate_results = await asyncio.gather(
            *[
                asyncio.to_thread(
                    adapter._on_card_action_trigger,
                    _card_action_data(confirm, open_id="ou_owner"),
                )
                for _ in range(2)
            ]
        )
        await _wait_for_operations_dispatch()
        await _wait_for(lambda: len(recovery_calls) == 1)
        assert len(recovery_calls) == 1
        assert all(item.card is None for item in duplicate_results)
        await _wait_for(
            lambda: any(
                "正在安全修复" in str(card)
                for _message_id, card in feishu_client.updated[
                    duplicate_update_start:
                ]
            )
        )
        await _wait_for(
            lambda: any(
                "安全修复已完成" in str(card)
                for _message_id, card in feishu_client.updated
            )
        )

        command_locals["message_id"] = "om_doctor_expiry"
        command_locals["event"] = SimpleNamespace(
            text="/hfc doctor",
            message_id="om_doctor_expiry",
            chat_type="group",
            operator=SimpleNamespace(open_id="ou_owner"),
        )
        assert await asyncio.to_thread(
            hook_runtime.handle_hfc_command_from_hermes_locals, command_locals
        )
        await _wait_for(lambda: len(feishu_client.sent) == 2)
        details = _operations_button(feishu_client.sent[1][1], "查看诊断")
        store = app[sidecar_server.OPERATIONS_STORE_KEY]
        _claims, record = store.inspect(
            details["token"],
            callback_chat_id="oc_group",
            callback_profile_scope=details["profile_scope"],
            allow_expired=True,
        )
        record.expires_at = 0.0
        expired, expired_card = await _click_operations_and_wait_for_card(
            adapter,
            details,
            chat_id="oc_group",
            operator="ou_owner",
            feishu_client=feishu_client,
        )
        assert expired.card is None
        assert "诊断已过期" in str(expired_card)
        assert _operations_button(expired_card, "重新检测")
    finally:
        await client.close()
