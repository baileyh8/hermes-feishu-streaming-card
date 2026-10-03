import copy
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hermes_feishu_card import hook_runtime, server
from hermes_feishu_card.event_auth import sign_policy_request
from hermes_feishu_card.install import patcher


SECRET = b"h" * 32
SOURCE = '''class Gateway:
    async def _run_agent_notify_long_running(self, disp, turn_ctx, _executor_task_holder):
        source, session_key, agent_holder = turn_ctx.source, turn_ctx.session_key, turn_ctx.agent_holder
        _notify_adapter = self._delivery_adapter_for(source)
        _heartbeat_msg_id = None
        while True:
            if not self._should_emit_long_running_notification(session_key, agent_holder[0], _executor_task_holder[0]):
                break
            _heartbeat_text = "⏳ Working — 3 min — iteration 2/15, clarify"
            _notify_res = None
            if _heartbeat_msg_id:
                _notify_res = await _notify_adapter.edit_message(source.chat_id, _heartbeat_msg_id, _heartbeat_text)
            if not (_notify_res and getattr(_notify_res, "success", False)):
                _notify_res = await _notify_adapter.send(source.chat_id, _heartbeat_text)
                if getattr(_notify_res, "success", False) and getattr(_notify_res, "message_id", None):
                    _heartbeat_msg_id = str(_notify_res.message_id)
'''


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(tmp_path / "state"))


class Client:
    def __init__(self, *, fails=False):
        self.sent = []
        self.fails = fails

    async def send_card(self, chat_id, card, **kwargs):
        if self.fails:
            raise OSError("fixture unavailable")
        self.sent.append(copy.deepcopy(card))
        return "om_fixture_owner"

    async def update_card_message(self, *args):
        pass


def event():
    return dict(schema_version="1", event="message.started", platform="feishu", sequence=0,
                created_at=1700000000, conversation_id="fixture-topic", chat_id="fixture-chat",
                message_id="fixture-turn", turn_id="fixture-turn", data={"profile_id": "default"})


def probe():
    return dict(schema_version="1", profile_id="default", chat_id="fixture-chat",
                conversation_id="fixture-topic", turn_id="fixture-turn", message_id="fixture-turn")


async def query(http, payload):
    body = json.dumps(payload).encode()
    response = await http.post("/delivery/policy", data=body, headers=sign_policy_request(SECRET, body))
    assert response.status == 200
    return await response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,fails,expected", [("task", False, True), ("classic", False, False), ("task", True, False)])
async def test_policy_proves_actual_exact_task_card_only(mode, fails, expected):
    client = Client(fails=fails)
    config = {"reading_preset": "task"} if mode == "task" else {}
    app = server.create_app(client, operations_transport_root_secret=SECRET, card_config=config)
    async with TestClient(TestServer(app)) as http:
        assert (await query(http, probe()))["accepted_task_card"] is False
        response = await http.post("/events", json=event())
        assert response.status == (502 if fails else 200)
        before = len(client.sent)
        result = await query(http, probe())
        assert result["accepted_task_card"] is expected
        assert len(client.sent) == before
        assert "fixture-" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("turn_id", "other-turn"), ("chat_id", "other-chat"),
    ("conversation_id", "other-topic"), ("profile_id", "other"), ("turn_id", "")])
async def test_policy_ownership_does_not_follow_aliases_or_other_routes(field, value):
    app = server.create_app(Client(), operations_transport_root_secret=SECRET,
                            card_config={"reading_preset": "task"})
    async with TestClient(TestServer(app)) as http:
        assert (await http.post("/events", json=event())).status == 200
        app[server.SESSION_ALIASES_KEY]["default:other-turn"] = "default:fixture-turn"
        assert (await query(http, dict(probe(), **{field: value}))).get("accepted_task_card", False) is False


def source():
    return SimpleNamespace(platform="feishu", profile_id="default", chat_id="fixture-chat",
        thread_id="fixture-topic", _hfc_turn_id="fixture-turn", _hfc_conversation_id="fixture-topic")


@pytest.mark.asyncio
@pytest.mark.parametrize("response,expected", [
    ({"ok": True, "disposition": "card", "accepted_task_card": True}, True),
    ({"ok": True, "disposition": "card"}, False),
    ({"ok": True, "disposition": "native", "accepted_task_card": True}, False),
    ({"ok": True, "disposition": "card", "accepted_task_card": "true"}, False),
    ({"ok": False, "accepted_task_card": True}, False), (None, False), (TimeoutError(), False),
])
async def test_producer_query_requires_fresh_exact_proof(monkeypatch, response, expected):
    calls = []
    config = SimpleNamespace(enabled=True, event_url="http://127.0.0.1:9999/events", timeout_seconds=2)
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: config)
    async def post(url, payload, timeout):
        calls.append((url, payload, timeout))
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(hook_runtime, "_post_json_response", post)
    assert await hook_runtime.suppress_task_heartbeat_async(source()) is expected
    assert await hook_runtime.suppress_task_heartbeat_async(source()) is expected
    assert len(calls) == 2  # A pinned delivery-policy decision is not ownership evidence.
    assert all(call[0].endswith("/delivery/policy") and call[1] == probe() and call[2] <= .25 for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unbound", "foreign-thread", "platform", "changed-during-query"])
async def test_producer_does_not_guess_or_reuse_changed_source(monkeypatch, change):
    value = source()
    if change == "unbound": del value._hfc_turn_id
    if change == "foreign-thread": value.thread_id = "other-topic"
    if change == "platform": value.platform = "slack"
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: SimpleNamespace(
        enabled=True, event_url="http://127.0.0.1:9999/events", timeout_seconds=1))
    async def post(*args):
        assert change == "changed-during-query"
        value._hfc_turn_id = "new-turn"
        return {"ok": True, "disposition": "card", "accepted_task_card": True}
    monkeypatch.setattr(hook_runtime, "_post_json_response", post)
    assert await hook_runtime.suppress_task_heartbeat_async(value) is False


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_heartbeat_guard_is_idempotent_and_byte_reversible(newline):
    original = SOURCE.replace("\n", newline)
    patched = patcher._apply_task_heartbeat_patch(original)
    assert patched != original
    compile(patched, "<heartbeat>", "exec")
    assert patcher._apply_task_heartbeat_patch(patched) == patched
    assert patcher.remove_patch(patched) == original
    assert patcher.remove_patch_lenient(patched) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_exact_producer_skips_both_send_and_edit_only_with_proof(monkeypatch, accepted):
    async def suppress(value): return accepted
    monkeypatch.setattr(hook_runtime, "suppress_task_heartbeat_async", suppress)
    namespace = {}
    exec(patcher._apply_task_heartbeat_patch(SOURCE), namespace)
    runner = namespace["Gateway"]()
    calls, ticks = [], []
    async def send(*args):
        calls.append("send")
        return SimpleNamespace(success=True, message_id="om_heartbeat")
    async def edit(*args):
        calls.append("edit")
        return SimpleNamespace(success=True, message_id="om_heartbeat")
    runner._delivery_adapter_for = lambda value: SimpleNamespace(send=send, edit_message=edit)
    def live(*args):
        ticks.append(True)
        return len(ticks) <= 3
    runner._should_emit_long_running_notification = live
    await runner._run_agent_notify_long_running(None, SimpleNamespace(
        source=source(), session_key="fixture-session", agent_holder=[object()]), [None])
    assert calls == ([] if accepted else ["send", "edit", "edit"])


@pytest.mark.parametrize("before,after", [
    ("_should_emit_long_running_notification(session_key, agent_holder[0], _executor_task_holder[0])",
     "_should_emit_long_running_notification(other_session, agent_holder[0], _executor_task_holder[0])"),
    ("source.chat_id, _heartbeat_text", "other_source.chat_id, _heartbeat_text"),
    ("                break", "                return"),
    ('"⏳ Working — 3 min — iteration 2/15, clarify"', '"Important notification"'),
    ('"⏳ Working — 3 min — iteration 2/15, clarify"', 'important_user_message'),
    ("        _heartbeat_msg_id = None", "        source = other_source\n        _heartbeat_msg_id = None"),
])
def test_recognized_heartbeat_producer_drift_is_refused(before, after):
    with pytest.raises(ValueError, match="heartbeat"):
        patcher._apply_task_heartbeat_patch(SOURCE.replace(before, after))


def test_unrelated_senders_and_older_unknown_layout_remain_untouched():
    other = SOURCE.replace("_run_agent_notify_long_running", "_send_answer")
    assert patcher._apply_task_heartbeat_patch(other) == other
    assert hook_runtime._hfc_classify_system_notice("⏳ Working — 3 min") is None


def test_known_pre_generic_heartbeat_template_stays_supported():
    original = SOURCE.replace('"⏳ Working — 3 min — iteration 2/15, clarify"',
                              'f"⏳ Working — {_elapsed_mins} min{_status_detail}"')
    patched = patcher._apply_task_heartbeat_patch(original)
    assert patcher.TASK_HEARTBEAT_PATCH_BEGIN in patched
    assert patcher.remove_patch(patched) == original


I18N_HEARTBEAT = ('disp._generic_status_phrase("status") if _long_running_mode == "generic" '
                  'else t("gateway.progress.working_heartbeat", minutes=_elapsed_mins, detail=_status_detail)')


def test_verified_i18n_heartbeat_template_is_idempotent_and_reversible():
    original = SOURCE.replace('"⏳ Working — 3 min — iteration 2/15, clarify"', I18N_HEARTBEAT)
    patched = patcher._apply_task_heartbeat_patch(original)
    compile(patched, "<i18n-heartbeat>", "exec")
    assert patcher.TASK_HEARTBEAT_PATCH_BEGIN in patched
    assert patcher._apply_task_heartbeat_patch(patched) == patched
    assert patcher.remove_patch(patched) == original


@pytest.mark.parametrize("before,after", [
    ("gateway.progress.working_heartbeat", "gateway.progress.important_notice"),
    ("minutes=_elapsed_mins", "elapsed=_elapsed_mins"),
    ("minutes=_elapsed_mins", "minutes=other_duration"),
    ("detail=_status_detail", "detail=important_user_message"),
    ("detail=_status_detail)", "detail=_status_detail, extra=True)"),
])
def test_i18n_heartbeat_key_and_arguments_cannot_drift(before, after):
    template = I18N_HEARTBEAT.replace(before, after)
    original = SOURCE.replace('"⏳ Working — 3 min — iteration 2/15, clarify"', template)
    with pytest.raises(ValueError, match="heartbeat message template"):
        patcher._apply_task_heartbeat_patch(original)
