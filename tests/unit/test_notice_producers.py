import asyncio
from hashlib import sha256
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime as runtime
from hermes_feishu_card.notice_producers import ONLINE, RESTART, SHUTDOWN, install_notice_producers


@pytest.fixture
def wired(monkeypatch):
    registered, sent = [], []
    class Adapter:
        _app_id = "cli_fixture"
        async def _hfc_original_send(self, chat_id, content, **kwargs):
            sent.append((chat_id, content, kwargs))
            await asyncio.sleep(0)
            return SimpleNamespace(success=True, message_id=f"om_notice_{len(sent)}")
        send = runtime._hfc_send_with_native_command_result_card
    class Runner:
        _primary_profile_name = "work"
        def __init__(self):
            self.adapter = Adapter()
            self.adapters = {"feishu": self.adapter}
            self._profile_adapters = {}
        async def _send_home_channel_message(self, platform, home, transport, message, failure_fmt):
            result = await transport.adapter.send(home.chat_id, message, metadata={"thread_id":home.thread_id})
            return result.success
        async def _send_shutdown_notice(self, adapter, chat_id, msg, kind, platform_str, **send_kwargs):
            return (await adapter.send(chat_id, msg, **send_kwargs)).success
    async def schedule(message_id, **kwargs):
        registered.append((message_id, kwargs))
        return True
    async def native(chat):
        return False
    monkeypatch.setattr(runtime, "schedule_message_recall_async", schedule)
    monkeypatch.setattr(runtime, "_hfc_direct_card_allowed_async", native)
    install_notice_producers(Runner)
    first = Runner._send_home_channel_message
    install_notice_producers(Runner)
    assert Runner._send_home_channel_message is first
    return Runner(), registered, sent


async def test_native_home_notice_retains_exact_topic_profile_and_bot_proof(wired):
    runner, registered, sent = wired
    transport = SimpleNamespace(adapter=runner.adapter, is_relay=False)
    assert await runner._send_home_channel_message("feishu", SimpleNamespace(chat_id="oc_test",thread_id="omt_test"), transport, ONLINE, "failure")
    assert len(sent) == len(registered) == 1
    assert registered[0][1] == {
        "notice_family":"restart",
        "route":{"profile_id":"work", "chat_id":"oc_test", "conversation_id":"omt_test",
                 "app_id_hash":sha256(b"cli_fixture").hexdigest()},
    }
    # An ordinary answer quoting the identical wording never inherits the producer.
    assert (await runner.adapter.send("oc_test", ONLINE, metadata={"thread_id":"omt_test"})).success
    assert len(sent) == 2 and len(registered) == 1


@pytest.mark.parametrize("text", [RESTART, SHUTDOWN])
async def test_shutdown_notice_is_registered_only_after_known_producer(wired, text):
    runner, registered, _ = wired
    assert await runner._send_shutdown_notice(runner.adapter,"oc_test",text,"active chat","feishu",metadata={"thread_id":"omt_test"})
    assert registered[0][1]["route"]["conversation_id"] == "omt_test"


@pytest.mark.parametrize("damage", ["relay","foreign_adapter","ambiguous_profile","unknown_text","other_platform"])
async def test_unknown_or_ambiguous_producers_remain_plain_messages(wired, damage):
    runner, registered, sent = wired
    transport = SimpleNamespace(adapter=runner.adapter, is_relay=False)
    text, platform = ONLINE, "feishu"
    if damage == "relay":transport.is_relay=True
    elif damage == "foreign_adapter":transport.adapter=type(runner.adapter)()
    elif damage == "ambiguous_profile":runner._profile_adapters={"other":{"feishu":runner.adapter}}
    elif damage == "unknown_text":text += "\nExtra information must remain"
    else:platform="telegram"
    assert await runner._send_home_channel_message(platform,SimpleNamespace(chat_id="oc_test",thread_id=""),transport,text,"failure")
    assert len(sent) == 1 and not registered


async def test_registration_failure_keeps_native_send_success(wired, monkeypatch):
    runner, registered, sent = wired
    async def unavailable(*args, **kwargs):return False
    monkeypatch.setattr(runtime,"schedule_message_recall_async",unavailable)
    assert await runner._send_shutdown_notice(runner.adapter,"oc_test",SHUTDOWN,"home channel","feishu")
    assert len(sent) == 1


def test_unknown_signature_is_not_wrapped():
    class Runner:
        async def _send_home_channel_message(self, platform, home, transport, message, failure_fmt, extra=None):pass
    original = Runner._send_home_channel_message
    install_notice_producers(Runner)
    assert Runner._send_home_channel_message is original
