from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.install import patcher


@pytest.mark.parametrize("accepted", [True, False])
def test_generated_status_callback_suppresses_only_accepted_failure_notice(monkeypatch, accepted):
    original = (Path(__file__).parents[1] / "fixtures/hermes_decomposed/gateway/run_turn_runner.py").read_text()
    namespace = {}
    patched = patcher.apply_gateway_fragment(original, "gateway/run_turn_runner.py")
    exec(patched, namespace)
    assert patcher.remove_patch(patched) == original
    native, events = [], []
    def emit(local_vars):
        events.append(("system.notice", local_vars))
        return accepted
    monkeypatch.setattr(hook_runtime, "_emit_status_notice_confirmed", emit)
    ctx = SimpleNamespace(source=SimpleNamespace(platform="feishu"), event_message_id="om_test",
                          _status_chat_id="oc_test", _loop_for_step=None,
                          _run_still_current=lambda: True,
                          status_queue=SimpleNamespace(put=native.append))
    runner = namespace["TurnRunner"](SimpleNamespace(), ctx)
    message = "❌ API failed after 3 retries — HTTP 503 PRIVATE_PROVIDER_DETAIL"
    runner._status_callback_sync("lifecycle", message)
    assert len(events) == 1 and events[0][0] == "system.notice"
    assert events[0][1]["_hfc_notice_kind"] == "provider-failure"
    assert "PRIVATE_PROVIDER_DETAIL" not in events[0][1]["content"]
    assert native == ([] if accepted else [("lifecycle", message)])


@pytest.mark.parametrize("event_type,message", [
    ("context", "❌ API failed after 3 retries — HTTP 503"),
    ("lifecycle", "解释这条消息：❌ API failed after 3 retries — HTTP 503"),
    ("lifecycle", "❌ API failed after 3 retries — HTTP 503\nExtra user instruction"),
    ("lifecycle", "⚠️ Max retries (3) exhausted — trying fallback..."),
])
def test_unknown_or_nonterminal_provider_notices_keep_native_path(monkeypatch, event_type, message):
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_threadsafe",
                        lambda *a, **k: pytest.fail("Unexpected ownership"))
    assert not hook_runtime.handle_status_from_hermes_locals(
        {"source":SimpleNamespace(platform="feishu")}, event_type=event_type, message=message)


def test_prior_status_hook_remains_strictly_removable():
    block = patcher._render_turn_context_hook_block(patcher._render_v464_status_hook_block, "    ", "\n")
    source = "def status(ctx):\n" + "".join(block) + "    return None\n"
    assert patcher.remove_patch(source) == "def status(ctx):\n    return None\n"
    with pytest.raises(ValueError):
        patcher.remove_patch(source.replace("event_type=event_type", "event_type='custom'"))


@pytest.mark.asyncio
@pytest.mark.parametrize("response,expected", [
    ({"ok": True, "applied": True}, True),
    ({"ok": True, "applied": False}, False),
    ({"ok": True}, False), ({}, False), (None, False),
])
async def test_failure_notice_waits_for_explicit_http_ack(monkeypatch, response, expected):
    import asyncio
    from aiohttp import web
    received = []
    async def receive(request):
        received.append(await request.json())
        return web.json_response(response)
    app = web.Application()
    app.router.add_post("/events", receive)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", f"http://127.0.0.1:{port}/events")
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda config: None)
    async def policy(config, local_vars, name):
        return hook_runtime._PolicyGateResult(True, None)
    monkeypatch.setattr(hook_runtime, "_policy_gate_async", policy)
    local_vars = {"source": SimpleNamespace(platform="feishu", chat_id="oc_fixture"),
                  "message_id":"om_fixture", "_hfc_loop":asyncio.get_running_loop()}
    try:
        # Same-loop calls fail open instead of deadlocking the delivery loop.
        assert not hook_runtime._emit_status_notice_confirmed(local_vars)
        result = await asyncio.to_thread(hook_runtime.handle_status_from_hermes_locals,
            local_vars, event_type="lifecycle", message="❌ API failed after 3 retries — HTTP 503")
        assert result is expected
        assert len(received) == 1
        assert received[0]["data"]["notice_kind"] == "provider-failure"
    finally:
        await runner.cleanup()
