import asyncio
from hashlib import sha256
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime as runtime
from hermes_feishu_card.notice_producers import ONLINE, RESTART, SHUTDOWN, install_notice_producers

# The fork's own wording for the requester's restart line. Read from the module rather than retyped
# here: the emoji is U+267B + U+FE0F and the dash is U+2014, which is exactly the kind of thing a
# hand-copied literal gets subtly wrong.
_GATEWAY_ONLINE_TEXT = runtime._HFC_GATEWAY_ONLINE_TEXT


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
        async def _resolve_approval(self, approval_id, choice, user_name, *, open_id='', chat_id=''):
            from hermes_feishu_card.notice_producers import EXPIRED_APPROVAL
            await self.send(chat_id, EXPIRED_APPROVAL)
        async def _process_message_background(self,event,session_key):
            text=await self.handler(event)
            await asyncio.sleep(0)
            return await self.send(event.source.chat_id,text,metadata={'thread_id':event.source.thread_id})
        async def _dispatch_inline_reply(self,event,*,log_cmd=None):
            text=await self.handler(event)
            return await self.send(event.source.chat_id,text,metadata={'thread_id':event.source.thread_id})
    class Runner:
        _primary_profile_name = "work"
        _draining=True
        def __init__(self):
            self.adapter = Adapter()
            self.adapters = {"feishu": self.adapter}
            self._profile_adapters = {}
        async def _send_home_channel_message(self, platform, home, transport, message, failure_fmt):
            result = await transport.adapter.send(home.chat_id, message, metadata={"thread_id":home.thread_id})
            return result.success
        async def _send_shutdown_notice(self, adapter, chat_id, msg, kind, platform_str, **send_kwargs):
            return (await adapter.send(chat_id, msg, **send_kwargs)).success
        async def _send_restart_notification(self):
            from hermes_feishu_card.notice_producers import RESTARTED
            return await self.adapter.send('oc_test',RESTARTED,metadata={'thread_id':'omt_test'})
        async def _send_watcher_message(self, platform_name, chat_id, thread_id, message_text, watcher):
            return await self.adapter.send(chat_id,message_text,metadata={'thread_id':thread_id})
        async def _send_busy_drain_notice(self, event, session_key, effective_mode):
            return await self.adapter.send(event.source.chat_id,'⏳ Gateway restarting — queued for the next turn after it comes back.',metadata={'thread_id':event.source.thread_id})
        async def _hm_handle_running_session_message(self,event,source,_quick_key):
            return '⏳ Gateway is restarting and is not accepting another turn right now.'
        async def _hm_dispatch_quick_and_plugin_commands(self,event,source,command):
            return True,'⏳ Gateway is restarting and is not accepting new work right now.',command
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
    runner=Runner()
    from hermes_feishu_card.notice_producers import install_adapter_notice_producers
    install_adapter_notice_producers(runner.adapter,runner)
    return runner, registered, sent


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


async def test_restart_requester_uses_colored_emoji_and_owned_route(wired):
    runner,registered,sent=wired
    assert (await runner._send_restart_notification()).success
    # Fork contract: the fork owns this line's WORDING as well as its routing — the requester's thread
    # gets the coloured ♻ line reading 「♻️ Gateway online — Hermes is back and ready.」 (the user's own
    # wording), so the core's template never reaches Feishu unchanged. What this test is really pinning
    # — that the restart notice travels the OWNED route carrying the coloured emoji instead of a bare
    # monochrome U+267B — still holds.
    assert sent[0][1]==_GATEWAY_ONLINE_TEXT, repr(sent[0][1])
    assert registered[0][1]['notice_family']=='restart'
    assert registered[0][1]['route']['conversation_id']=='omt_test'
    from hermes_feishu_card.notice_producers import RESTARTED
    await runner.adapter.send('oc_test',RESTARTED)
    # Fork contract: the rewrite is CONTENT-based and lives in the send wrapper, which is the whole
    # point of doing it there — upstream's router only runs when the card policy accepts the chat, so a
    # policy decline used to deliver the core's bare monochrome U+267B text on no recall path at all.
    # Every path through the wrapper therefore gets the coloured line — see the registration note below
    # for what that means for a bare send.
    assert sent[-1][1]==_GATEWAY_ONLINE_TEXT, repr(sent[-1][1])
    # Fork contract, deliberate divergence from upstream's provenance model: this wrapper matches by
    # CONTENT, because provenance is not available on every path — upstream's router only runs when the
    # card policy ACCEPTS the chat, so a policy decline used to deliver the core's bare monochrome
    # U+267B text with no colour and no recall path at all. A bare send of the same legacy wording is
    # therefore rewritten AND registered as an owned restart notice: that registration is exactly how
    # the policy-declined path gets its 15s deadline.
    #
    # What upstream is really protecting still holds: a look-alike ANSWER must not be rewritten or
    # recalled. A real answer travels the card/stream path, and the match here is the WHOLE string
    # (``_hfc_restart_notice_rewrite`` strips and compares for equality), never a substring.
    assert len(registered)==2


@pytest.mark.parametrize('text,disposable',[
    ('✅ Background task finished',True),
    ('✅ Background task finished — `printf ok` (6s)',True),
    ('✅ Background task finished — `echo hi` (1h 2m)',True),
    ('✅ Background task finished\nFinal output:\nvaluable result',False),
    ('❌ Background task failed — `cmd` (exit 1)',False),
    ('✅ Background task finished but needs attention',False),
    ('✅ Background task finished — `unclosed',False),
])
async def test_background_only_known_success_one_liner_gets_timer(wired,text,disposable):
    runner,registered,sent=wired
    assert (await runner._send_watcher_message('feishu','oc_test','omt_test',text,{})).success
    assert bool(registered)==disposable
    if disposable:assert registered[0][1]['notice_family']==''
    await runner.adapter.send('oc_test',text,metadata={'thread_id':'omt_test'})
    assert len(registered)==int(disposable)


async def test_expired_approval_correction_is_retained_even_from_its_real_producer(wired):
    runner,registered,sent=wired
    await runner.adapter._resolve_approval('fixture','once','tester',chat_id='oc_test')
    assert registered==[]  # Native card may already say Approved; keep the correction.
    await runner.adapter.send('oc_test',sent[0][1])
    assert len(registered)==0
    event=SimpleNamespace(source=SimpleNamespace(platform='feishu',chat_id='oc_test',thread_id='omt_test'))
    await runner._send_busy_drain_notice(event,'session','queue')
    assert registered[-1][1]['notice_family']=='restart'
    assert registered[-1][1]['route']['conversation_id']=='omt_test'


@pytest.mark.parametrize('inline',[False,True])
@pytest.mark.parametrize('quick',[False,True])
async def test_returned_drain_reply_keeps_provenance_until_actual_base_send(wired,inline,quick):
    runner,registered,sent=wired
    event=SimpleNamespace(source=SimpleNamespace(platform='feishu',chat_id='oc_test',thread_id='omt_test'))
    async def handler(event):
        if quick:
            result=await runner._hm_dispatch_quick_and_plugin_commands(event,event.source,'fixture')
            return result[1]
        return await runner._hm_handle_running_session_message(event,event.source,'session')
    runner.adapter.handler=handler
    if inline:await runner.adapter._dispatch_inline_reply(event)
    else:await runner.adapter._process_message_background(event,'session')
    assert len(registered)==1 and registered[0][1]['notice_family']=='restart'
    # A producer call outside the exact delivery envelope cannot leak authority.
    text=await handler(event)
    await runner.adapter.send('oc_test',text,metadata={'thread_id':'omt_test'})
    assert len(registered)==1
    async def ordinary(event):return text
    runner.adapter.handler=ordinary
    await runner.adapter._process_message_background(event,'second')
    assert len(registered)==1


async def test_parallel_deliveries_do_not_share_returned_notice_authority(wired):
    runner,registered,sent=wired
    trusted=SimpleNamespace(source=SimpleNamespace(platform='feishu',chat_id='oc_test',thread_id='omt_first'))
    ordinary=SimpleNamespace(source=SimpleNamespace(platform='feishu',chat_id='oc_test',thread_id='omt_second'))
    async def handler(event):
        if event is trusted:return await runner._hm_handle_running_session_message(event,event.source,'session')
        return '⏳ Gateway is restarting and is not accepting another turn right now.'
    runner.adapter.handler=handler
    await asyncio.gather(runner.adapter._process_message_background(trusted,'first'),runner.adapter._process_message_background(ordinary,'second'))
    assert len(registered)==1
    assert registered[0][1]['route']['conversation_id']=='omt_first'
