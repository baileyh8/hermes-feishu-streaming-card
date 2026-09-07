"""Zero events does not prove the Gateway hook was never executed."""
import asyncio
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer
import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.operations_transport import ensure_transport_root_secret, read_transport_root_secret
from hermes_feishu_card.runtime_control import RuntimeControlEmitter, RUNTIME_HOOK_GENERATION
from hermes_feishu_card.server import create_app


@pytest.mark.parametrize("wrong_key", [False, True])
async def test_live_policy_rejection_and_missing_heartbeat_with_executed_hook(tmp_path, monkeypatch, wrong_key):
    hook_runtime.reset_runtime_state()
    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(tmp_path / "gateway-state"))
    monkeypatch.setenv("HERMES_FEISHU_CARD_ENABLED", "1")
    monkeypatch.delenv("HERMES_FEISHU_CARD_PROFILE_ID", raising=False)
    if wrong_key:
        ensure_transport_root_secret()
    app = create_app(object(), operations_transport_root_secret=b"s" * 32, integrity_mode="notify")
    async with TestClient(TestServer(app)) as client:
        event_url = str(client.make_url("/events"))
        monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", event_url)
        # Run one real authenticated control attempt deterministically, without
        # a daemon worker race. Both policy and control use the actual key reader.
        control = RuntimeControlEmitter(event_url=event_url, hook_generation=RUNTIME_HOOK_GENERATION,
                                        package_version="test", secret_reader=read_transport_root_secret)
        assert await asyncio.to_thread(control.emit_once, "runtime.hello") is False
        startup_calls = []
        monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *a: startup_calls.append(True) or True)
        source = SimpleNamespace(platform="feishu", profile="secretary", chat_id="test-chat")
        event = SimpleNamespace(source=source, message_id="test-message")
        assert await hook_runtime.emit_from_hermes_locals_async({"source": source, "event": event}) is False
        health = await (await client.get("/health")).json()
    hook_runtime.reset_runtime_state()
    assert startup_calls == [True]
    assert health["metrics"]["events_received"] == 0
    assert health["metrics"]["policy_auth_rejections"] == 1
    assert health["metrics"]["policy_queries"] == 0
    assert health["metrics"]["runtime_control_auth_rejections"] == int(wrong_key)
    assert health["readiness"]["runtime_seen"] is False
