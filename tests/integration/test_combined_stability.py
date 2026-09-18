import asyncio
import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from hermes_feishu_card import hook_runtime as runtime
from hermes_feishu_card.server import create_app, SESSIONS_KEY
from hermes_feishu_card.bots import RouteResult

class Client:
    def __init__(self): self.sent=[]; self.updated=[]; self.deleted=[]
    async def send_card(self, chat_id, card, **kwargs):
        mid=f'om_fixture_{len(self.sent)+1}'; self.sent.append((mid,card)); return mid
    async def update_card_message(self, mid, card): self.updated.append((mid,card))
    async def delete_message(self, mid): self.deleted.append(mid); return True

class Factory:
    def __init__(self, client): self.client=client
    def get_client(self, bot_id):
        assert bot_id=='default'
        return self.client

@pytest.mark.asyncio
async def test_custom_profile_notice_recall_uses_same_route():
    fake=Client()
    app=create_app({'work':Factory(fake)},bot_router=lambda e:RouteResult('default','fixture'))
    async with TestClient(TestServer(app)) as http:
        r=await http.post('/recall/schedule',json={'message_id':'om_ack','delay_seconds':0,'route':{'profile_id':'work','chat_id':'oc_fixture'}})
        assert r.status==200
        await asyncio.sleep(.03)
        assert fake.deleted==['om_ack']

@pytest.mark.asyncio
async def test_terminal_request_itself_is_inside_total_budget(monkeypatch):
    monkeypatch.setattr(runtime,'TERMINAL_DELIVERY_RETRY_BUDGET_SECONDS',.03)
    async def slow(*args):
        await asyncio.sleep(.2)
        return {'ok':True,'applied':True}
    monkeypatch.setattr(runtime,'_post_json_ordered_response',slow)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(runtime._post_terminal_with_retry(SimpleNamespace(event_url='http://fixture',timeout_seconds=10),{},'message.completed'),.1)
    # An outer timeout alone is not proof: the internal budget must cancel the attempt.
    started=time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await runtime._post_terminal_with_retry(SimpleNamespace(event_url='http://fixture',timeout_seconds=10),{},'message.completed')
    assert time.monotonic()-started<.12

@pytest.mark.asyncio
async def test_reasoning_callback_rebinds_and_does_not_touch_answer(monkeypatch):
    seen=[]
    def emit(data, *, event_name): seen.append((event_name,data['text'])); return True
    monkeypatch.setattr(runtime,'emit_from_hermes_locals_threadsafe',emit)
    source=SimpleNamespace(platform='feishu',chat_id='oc_fixture',_hfc_turn_id='turn1')
    agent=SimpleNamespace(reasoning_callback=None)
    runtime.bind_agent_reasoning(agent,source,'turn1',asyncio.get_running_loop(),lambda:True)
    old=agent.reasoning_callback
    old('reason A');old('reason B')
    runtime.bind_agent_reasoning(agent,source,'turn2',asyncio.get_running_loop(),lambda:True)
    old('stale')
    agent.reasoning_callback('reason C')
    assert seen==[('thinking.delta','reason A'),('thinking.delta','reason B'),('thinking.delta','reason C')]


def event(name,seq,data=None,chat='oc_fixture'):
    return dict(schema_version='1',event=name,conversation_id='conv_fixture',message_id='turn_fixture',turn_id='turn_fixture',chat_id=chat,platform='feishu',sequence=seq,created_at=time.time(),data=data or {})

@pytest.mark.asyncio
async def test_restart_terminal_updates_original_card_and_preserves_partial(tmp_path):
    fake=Client()
    def app(): return create_app(fake,card_config={'flush_interval_ms':0},session_store_directory=tmp_path)
    async with TestClient(TestServer(app())) as http:
        for e in [event('message.started',0),event('answer.delta',1,{'text':'PARTIAL_ANSWER'})]:
            r=await http.post('/events',json=e);assert r.status==200
    assert len(fake.sent)==1
    async with TestClient(TestServer(app())) as http:
        e=event('message.completed',2,{'answer':'PROVIDER_ERROR','turn_outcome':'failed'})
        for _ in range(2):
            r=await http.post('/events',json=e);assert r.status==200
        await asyncio.sleep(.05)
        s=next(iter(http.app[SESSIONS_KEY].values()))
        assert s.status=='failed' and 'PARTIAL_ANSWER' in s.answer_text
        assert len(fake.sent)==1
        assert fake.updated[-1][0]=='om_fixture_1'

@pytest.mark.asyncio
@pytest.mark.parametrize('profile,expected', [('missing',409), ('default',409), ('work',200)])
async def test_recall_never_guesses_between_profiles(profile,expected):
    work,other=Client(),Client()
    app=create_app({'work':Factory(work),'other':Factory(other)},bot_router=lambda e:RouteResult('default','fixture'))
    async with TestClient(TestServer(app)) as http:
        r=await http.post('/recall/schedule',json={'message_id':'om_ack','delay_seconds':0,
                         'route':{'profile_id':profile,'chat_id':'oc_fixture'}})
        assert r.status==expected
        await asyncio.sleep(.01)
        assert other.deleted==[]
        assert work.deleted==(['om_ack'] if expected==200 else [])

@pytest.mark.asyncio
async def test_recall_runtime_preserves_profile_and_chat(monkeypatch):
    calls=[]
    async def schedule(mid,**kw): calls.append((mid,kw));return True
    monkeypatch.delenv('HERMES_FEISHU_CARD_PROFILE_ID',raising=False)
    monkeypatch.setattr(runtime,'schedule_message_recall_async',schedule)
    source=SimpleNamespace(platform='feishu',profile_id='work',chat_id='chat_fixture',thread_id='topic_fixture')
    # The adapter also carries answers. Use the real Hermes heartbeat template;
    # an arbitrary string after the status prefix must not authorize deletion.
    assert runtime._transient_notice_recall_seconds('⏳ Working — testing') is None
    assert await runtime.recall_transient_thread_notice_async(source,'⏳ Working — 2 min',SimpleNamespace(success=True,message_id='om_ack'))
    assert calls[0][1]['route']==dict(profile_id='work',chat_id='chat_fixture',conversation_id='topic_fixture')

@pytest.mark.asyncio
async def test_generated_context_hook_binds_real_reasoning_channel(monkeypatch):
    from hermes_feishu_card.install import patcher
    from hermes_feishu_card.events import SidecarEvent
    from hermes_feishu_card.session import CardSession
    seen=[]
    def emit(lv,*,event_name):
        seen.append(runtime.build_event(event_name,lv));return True
    monkeypatch.setattr(runtime,'emit_from_hermes_locals_threadsafe',emit)
    source=SimpleNamespace(platform='feishu',chat_id='oc_reasoning',_hfc_turn_id='om_reasoning')
    ctx=SimpleNamespace(source=source,event_message_id='om_reasoning',_loop_for_step=asyncio.get_running_loop(),_run_still_current=lambda:True)
    agent=SimpleNamespace(reasoning_callback=None,tool_progress_callback=None,tool_start_callback=None,tool_complete_callback=None)
    block=''.join(patcher._render_turn_context_hook_block(patcher._render_stable_tool_lifecycle_hook_block,'    ','\n'))
    ns={};exec('def wire(agent,ctx):\n'+block,ns);ns['wire'](agent,ctx)
    assert callable(agent.reasoning_callback)
    for token in ['分析','这段代码']:
        agent.reasoning_callback(token)
    assert len(seen)==2
    first=SidecarEvent.from_dict(seen[0]);session=CardSession(first.conversation_id,first.message_id,first.chat_id)
    for data in seen:session.apply(SidecarEvent.from_dict(data))
    assert session.thinking_text=='分析这段代码' and session.answer_text==''
    ctx._run_still_current=lambda:False
    # The generated callback receives the actual bound predicate, as other HFC callbacks do.
    runtime.bind_agent_reasoning(agent,source,'om_reasoning',ctx._loop_for_step,lambda:False)
    agent.reasoning_callback('stale')
    assert len(seen)==2

@pytest.mark.asyncio
async def test_reasoning_failure_falls_back_and_nonfeishu_unbinds(monkeypatch):
    original=[];agent=SimpleNamespace(reasoning_callback=original.append)
    monkeypatch.setattr(runtime,'emit_from_hermes_locals_threadsafe',lambda *a,**kw:False)
    source=SimpleNamespace(platform='feishu',chat_id='fixture')
    runtime.bind_agent_reasoning(agent,source,'turn',asyncio.get_running_loop(),lambda:True)
    old=agent.reasoning_callback;old('fallback')
    assert original==['fallback']
    assert not runtime.bind_agent_reasoning(agent,SimpleNamespace(platform='telegram'),'turn2',asyncio.get_running_loop(),lambda:True)
    old('stale');agent.reasoning_callback('native')
    assert original==['fallback','native']

@pytest.mark.asyncio
async def test_restarted_card_is_not_mutated_by_wrong_chat(tmp_path):
    fake=Client()
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        await http.post('/events',json=event('message.started',0))
        await http.post('/events',json=event('answer.delta',1,{'text':'original'}))
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        r=await http.post('/events',json=event('message.completed',2,{'answer':'foreign'},chat='other_chat'))
        assert not (await r.json()).get('applied')
        assert all('foreign' not in str(c) for _,c in fake.updated)
        assert len(fake.sent)==1

@pytest.mark.asyncio
async def test_restart_revokes_old_approval_and_keeps_original_card(tmp_path):
    from hermes_feishu_card.server import INTERACTION_RESULTS_KEY
    fake=Client()
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        await http.post('/events',json=event('message.started',0))
        r=await http.post('/events',json=event('interaction.requested',1,{
            'interaction_id':'approval_fixture','kind':'approval','prompt':'Approve fixture?',
            'options':[{'label':'Allow','value':'yes'}],'timeout_seconds':30}))
        assert r.status==200
        old=next(iter(http.app[SESSIONS_KEY].values())).active_interaction
        assert old and old.callback_token
    count=len(fake.sent)
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        s=next(iter(http.app[SESSIONS_KEY].values()))
        assert s.active_interaction is None and s.status=='failed'
        assert '原授权已失效' in s.answer_text
        assert not http.app[INTERACTION_RESULTS_KEY]
        assert len(fake.sent)==count

@pytest.mark.asyncio
async def test_checkpoint_io_failure_keeps_delivery_working(tmp_path,monkeypatch):
    from hermes_feishu_card.session_store import SessionStore
    from hermes_feishu_card.server import DIAGNOSTICS_KEY
    def fail(*a,**k):raise OSError('private detail')
    monkeypatch.setattr(SessionStore,'save',fail)
    fake=Client()
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        r=await http.post('/events',json=event('message.completed',0,{'answer':'final'}))
        assert (await r.json())['applied']
        assert len(fake.sent)==1
        assert http.app[DIAGNOSTICS_KEY]['card_checkpoint_state']=='unavailable'

@pytest.mark.asyncio
async def test_restart_restores_completed_card_without_second_send(tmp_path):
    fake=Client()
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        await http.post('/events',json=event('message.completed',0,{'answer':'COMPLETE'}))
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        r=await http.post('/events',json=event('message.completed',0,{'answer':'COMPLETE'}))
        assert (await r.json())['applied']
        assert len(fake.sent)==1
        s=next(iter(http.app[SESSIONS_KEY].values()));assert s.answer_text=='COMPLETE'

@pytest.mark.asyncio
async def test_boot_resume_waits_before_original_handler_without_pinning_native(monkeypatch):
    from urllib.error import URLError
    order=[]
    monkeypatch.setattr(runtime,'load_runtime_config',lambda:SimpleNamespace(enabled=True,event_url='http://fixture/events',timeout_seconds=.05))
    monkeypatch.setattr(runtime,'TERMINAL_DELIVERY_RETRY_DELAYS',(.001,.001))
    async def post(*args):
        order.append('probe')
        if order.count('probe')==1:raise URLError('offline')
        return {'ok':True,'disposition':'card','ttl_ms':100}
    monkeypatch.setattr(runtime,'_post_json_ordered_response',post)
    class Runner:
        async def _run_startup_resume_event(self,adapter,event,session_key):
            order.append('original');return 'original-result'
    assert runtime._install_startup_resume_wait(Runner)
    wrapped=Runner._run_startup_resume_event
    assert runtime._install_startup_resume_wait(Runner)
    assert Runner._run_startup_resume_event is wrapped
    result=await Runner()._run_startup_resume_event(None,SimpleNamespace(source=SimpleNamespace(platform='feishu',chat_id='fixture')),'session')
    assert result=='original-result' and order==['probe','probe','original']

@pytest.mark.asyncio
async def test_boot_resume_native_policy_is_not_overridden(monkeypatch):
    calls=[]
    monkeypatch.setattr(runtime,'load_runtime_config',lambda:SimpleNamespace(enabled=True,event_url='http://fixture/events',timeout_seconds=.05))
    async def post(*args):calls.append('query');return {'ok':True,'disposition':'native','ttl_ms':100}
    monkeypatch.setattr(runtime,'_post_json_ordered_response',post)
    await runtime._wait_for_startup_card_policy(SimpleNamespace(platform='feishu',chat_id='fixture'))
    assert calls==['query']
    await runtime._wait_for_startup_card_policy(SimpleNamespace(platform='telegram',chat_id='fixture'))
    assert calls==['query']


def test_boot_resume_signature_drift_is_rejected():
    class Runner:
        async def _run_startup_resume_event(self,adapter,event,session_key,unknown): pass
    old=Runner._run_startup_resume_event
    assert not runtime._install_startup_resume_wait(Runner)
    assert Runner._run_startup_resume_event is old

@pytest.mark.asyncio
async def test_reasoning_stream_and_final_snapshot_are_not_double_counted(monkeypatch):
    seen=[]
    monkeypatch.setattr(runtime,'emit_from_hermes_locals_threadsafe',lambda d,**kw:seen.append(d['text']) or True)
    class Agent:
        reasoning_callback=None
        def _fire_reasoning_delta(self,text):
            if self.reasoning_callback:self.reasoning_callback(text)
    agent=Agent();source=SimpleNamespace(platform='feishu',chat_id='fixture')
    runtime.bind_agent_reasoning(agent,source,'turn',asyncio.get_running_loop(),lambda:True)
    agent._fire_reasoning_delta('哈');agent._fire_reasoning_delta('哈')
    # Hermes' non-streaming post-response fallback can repeat the whole reasoning.
    agent.reasoning_callback('哈哈')
    agent._fire_reasoning_delta('next');agent.reasoning_callback('next')
    assert seen==['哈','哈','next']

@pytest.mark.asyncio
async def test_checkpoint_same_turn_id_is_isolated_by_profile(tmp_path):
    a,b=Client(),Client()
    def make():return create_app({'work':Factory(a),'other':Factory(b)},bot_router=lambda e:RouteResult('default','fixture'),session_store_directory=tmp_path)
    async with TestClient(TestServer(make())) as http:
        for profile in ['work','other']:
            r=await http.post('/events',json=event('message.started',0,{'profile_id':profile}))
            assert (await r.json())['applied']
            r=await http.post('/events',json=event('answer.delta',1,{'profile_id':profile,'text':profile}))
            assert (await r.json())['applied']
    async with TestClient(TestServer(make())) as http:
        r=await http.post('/events',json=event('message.completed',2,{'profile_id':'work','answer':'WORK_FINAL'}))
        assert (await r.json())['applied']
        sessions=http.app[SESSIONS_KEY]
        assert sessions['work:turn_fixture'].answer_text=='WORK_FINAL'
        assert sessions['other:turn_fixture'].answer_text=='other'
        assert sessions['other:turn_fixture'].status!='completed'
        assert len(a.sent)==len(b.sent)==1

@pytest.mark.asyncio
async def test_changed_app_identity_does_not_restore_old_card(tmp_path):
    fake=Client();fake.config=SimpleNamespace(app_id='app_old',base_url='https://fixture')
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        await http.post('/events',json=event('message.started',0))
    fake.config.app_id='app_new';updates=len(fake.updated)
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        await asyncio.sleep(.01)
        assert not http.app[SESSIONS_KEY]
        assert len(fake.updated)==updates

@pytest.mark.asyncio
async def test_restart_keeps_canonical_turn_distinct_from_transport_message(tmp_path):
    fake=Client()
    def ev(name,n,data):
        e=event(name,n,data);e['message_id']='internal_transport';e['turn_id']='canonical_turn';return e
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        await http.post('/events',json=ev('message.started',0,{}))
        await http.post('/events',json=ev('answer.delta',1,{'text':'CANONICAL_PARTIAL'}))
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        assert 'canonical_turn' in http.app[SESSIONS_KEY]
        r=await http.post('/events',json=ev('message.completed',2,{'answer':'failure','turn_outcome':'failed'}))
        assert (await r.json())['applied']
        assert len(fake.sent)==1
        assert 'CANONICAL_PARTIAL' in http.app[SESSIONS_KEY]['canonical_turn'].answer_text

@pytest.mark.asyncio
async def test_restart_does_not_refill_original_after_terminal_recovery_send(tmp_path,monkeypatch):
    from hermes_feishu_card import server
    from hermes_feishu_card.session_store import SessionStore
    class Failing(Client):
        fail=True
        async def update_card_message(self,mid,card):
            if self.fail:raise RuntimeError('fixture failed update')
            await super().update_card_message(mid,card)
    fake=Failing()
    monkeypatch.setattr(server,'UPDATE_MAX_ATTEMPTS',1)
    async def exhausted(*a,**kw):return False
    monkeypatch.setattr(server,'_retry_terminal_update',exhausted)
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path,card_config={'flush_interval_ms':0}))) as http:
        await http.post('/events',json=event('message.started',0))
        await http.post('/events',json=event('message.completed',1,{'answer':'FINAL_RECOVERY'}))
        for _ in range(100):
            if next(iter(http.app[SESSIONS_KEY].values())).terminal_delivery_state=='recovered':break
            await asyncio.sleep(.01)
        else:raise AssertionError('recovery did not finish')
    assert len(fake.sent)==2
    assert SessionStore(tmp_path).load()[0]['session'].terminal_delivery_state=='recovered'
    fake.fail=False;updates=len(fake.updated)
    async with TestClient(TestServer(create_app(fake,session_store_directory=tmp_path))) as http:
        await asyncio.sleep(.02)
        await http.post('/events',json=event('message.completed',1,{'answer':'FINAL_RECOVERY'}))
        assert len(fake.sent)==2 and len(fake.updated)==updates
