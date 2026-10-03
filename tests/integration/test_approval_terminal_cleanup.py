"""#337/#339: compact duplicates only after a complete receipt is delivered."""
import asyncio
import copy
import time
import pytest
from aiohttp.test_utils import TestClient, TestServer
from hermes_feishu_card.server import create_app, SESSIONS_KEY


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_FEISHU_CARD_STATE_DIR', str(tmp_path/'state'))


class Client:
    def __init__(self):
        self.cards={};self.updates=[];self.fail_receipt=False
    async def send_card(self, chat_id, card, **kwargs):
        mid=f'card-{len(self.cards)+1}';self.cards[mid]=copy.deepcopy(card);return mid
    async def update_card_message(self, mid, card):
        assert self.cards[mid].get('schema')==card.get('schema')
        if self.fail_receipt and mid=='card-2':raise RuntimeError('fixture receipt unavailable')
        self.cards[mid]=copy.deepcopy(card);self.updates.append((mid,copy.deepcopy(card)))


async def post(http, name, seq, data=None):
    r=await http.post('/events',json=dict(schema_version='1',event=name,sequence=seq,conversation_id='scope',
        message_id='source',turn_id='turn',chat_id='chat',platform='feishu',created_at=time.time(),data=data or {}))
    assert r.status==200,await r.text()
    return await r.json()


async def chosen(http, seq, identifier='approval', kind='approval'):
    await post(http,'interaction.requested',seq,dict(interaction_id=identifier,kind=kind,prompt='Fixture operation?',
        description='Run harmless fixture command',options=[{'label':'Allow once','value':'once'}]))
    await post(http,'interaction.completed',seq+1,dict(interaction_id=identifier,choice='once',choice_label='Allow once'))


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['callback','text'])
@pytest.mark.parametrize('terminal',['message.completed','message.failed'])
@pytest.mark.parametrize('kind',['approval','clarify'])
async def test_terminal_retires_duplicate_while_independent_receipt_keeps_scope(mode,terminal,kind):
    c=Client();app=create_app(c,card_config={'interaction_mode':mode,'flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await chosen(http,1,kind=kind)
        await post(http,'answer.delta',3,{'text':'CONTINUED'})
        await asyncio.sleep(.03)
        assert '已选择' in str(c.cards['card-1']), 'keep scope throughout the running turn'
        await post(http,terminal,4,{'answer':'FINAL','error':'fixture failure'})
        await asyncio.sleep(.03)
        assert '已选择' not in str(c.cards['card-1'])
        assert 'Fixture operation?' not in str(c.cards['card-1'])
        receipt=str(c.cards['card-2'])
        assert 'Fixture operation?' in receipt and 'Run harmless fixture command' in receipt
        assert 'Allow once' in receipt and '已选择' in receipt
        assert 'CONTINUED' not in str(c.cards['card-1'])
        assert '本段已转入续答' in str(c.cards['card-1'])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['approval','clarify'])
async def test_failed_receipt_update_preserves_the_duplicate_and_answer(kind):
    c=Client();app=create_app(c,card_config={'flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await chosen(http,1,kind=kind)
        await post(http,'answer.delta',3,{'text':'CONTINUED'})
        c.fail_receipt=True
        await post(http,'message.completed',4,{'answer':'FINAL'})
        await asyncio.sleep(.03)
        assert '已选择' in str(c.cards['card-1'])
        assert 'FINAL' in str(c.cards['card-3'])


@pytest.mark.asyncio
async def test_unknown_receipt_kinds_are_not_cleanup_targets():
    c=Client();app=create_app(c,card_config={'flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await chosen(http,1,kind='choice')
        await post(http,'answer.delta',3,{'text':'CONTINUED'})
        await post(http,'message.completed',4,{'answer':'FINAL'})
        await asyncio.sleep(.03)
        assert '已选择' in str(c.cards['card-1'])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['approval','clarify'])
async def test_consecutive_receipts_keep_both_and_remove_only_duplicates(kind):
    c=Client();app=create_app(c,card_config={'flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await chosen(http,1,'first',kind=kind)
        await post(http,'answer.delta',3,{'text':'CONTINUED'})
        await chosen(http,4,'second',kind=kind)
        await post(http,'answer.delta',6,{'text':'SECOND'})
        await post(http,'message.completed',7,{'answer':'FINAL'})
        await asyncio.sleep(.03)
        assert all('已选择' not in str(c.cards[mid]) for mid in ['card-1','card-3'])
        assert all('Run harmless fixture command' in str(c.cards[mid]) and
                   'Allow once' in str(c.cards[mid]) for mid in ['card-2','card-4'])
        assert 'FINAL' in str(c.cards['card-5'])


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['sole_owner','pending','oversize','replaced'])
@pytest.mark.parametrize('kind',['approval','clarify'])
async def test_unproven_receipts_cannot_authorize_cleanup(damage,monkeypatch,kind):
    from hermes_feishu_card import server
    c=Client();app=create_app(c,card_config={'flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await chosen(http,1,kind=kind)
        await post(http,'answer.delta',3,{'text':'CONTINUED'})
        s=next(iter(app[SESSIONS_KEY].values()))
        key=next(iter(app[SESSIONS_KEY]))
        s.status='completed'
        if damage=='sole_owner':
            for i in [s.active_interaction,s.approval_retirements[0]['interaction']]:
                i.feishu_message_id=app[server.FEISHU_MESSAGE_IDS_KEY][key]
        elif damage=='pending':
            for i in [s.active_interaction,s.approval_retirements[0]['interaction']]:i.status='pending'
        elif damage=='oversize':
            monkeypatch.setattr(server,'_render_interaction_callback_card_for_app',lambda *a,**k: {'elements':[{'tag':'markdown','content':'x'*100000}]})
        else:app[SESSIONS_KEY][key]=copy.deepcopy(s)
        await server._settle_approval_displays(app,key,s)
        assert '已选择' in str(c.cards['card-1'])
        assert not s.active_interaction.receipt_fingerprint


@pytest.mark.parametrize('kind',['approval','clarify'])
def test_receipt_proof_is_bound_to_scope_and_choice_and_never_restored(tmp_path,kind):
    from hermes_feishu_card.session import CardSession, InteractionState
    from hermes_feishu_card.approval_receipts import approval_receipt_fingerprint,has_confirmed_approval_receipt
    from hermes_feishu_card.session_store import SessionStore
    s=CardSession('scope','source','chat')
    i=InteractionState('approval',kind,'Operation?',status='completed',feishu_message_id='receipt',choice='once')
    s.active_interaction=i
    i.receipt_fingerprint=approval_receipt_fingerprint(s,i)
    assert has_confirmed_approval_receipt(s)
    for field,value in [('prompt','Changed scope'),('choice','deny'),('feishu_message_id','other'),
                        ('multi_select',True),('allow_custom_input',True)]:
        old=getattr(i,field);setattr(i,field,value)
        assert not has_confirmed_approval_receipt(s)
        setattr(i,field,old)
    s.approval_retirements=[{'sentinel':'PRIVATE_RETIREMENT','interaction':i}]
    store=SessionStore(tmp_path)
    store.save('turn',s,'owner',None,'',{},'fixture_client')
    raw=next(store.root.glob('*.json')).read_text()
    assert 'PRIVATE_RETIREMENT' not in raw and i.receipt_fingerprint not in raw
    restored=store.load()[0]['session']
    assert restored.approval_retirements==[] and not has_confirmed_approval_receipt(restored)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['callback','text'])
async def test_clarify_multiselect_custom_receipt_keeps_exact_decision_after_cleanup(mode):
    c=Client();app=create_app(c,card_config={'interaction_mode':mode,'reading_preset':'task','flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await post(http,'interaction.requested',1,dict(interaction_id='clarify',kind='clarify',
            prompt='哪些内容需要保留？',description='KEEP_FULL_SCOPE',multi_select=True,allow_custom_input=True,
            options=[{'label':'完整答案','value':'answer'},{'label':'工具参数','value':'tools'},
                     {'label':'可读布局','value':'layout'}]))
        session=app[SESSIONS_KEY]['turn']
        token=session.active_interaction.callback_token
        choice='["answer","tools","字号与间距"]'
        label='完整答案、工具参数、[自定义] 字号与间距'
        await post(http,'interaction.completed',2,dict(interaction_id='clarify',choice=choice,choice_label=label))
        await post(http,'answer.delta',3,{'text':'CONTINUED'})
        await post(http,'message.completed',4,{'answer':'FINAL_EXACT_ANSWER'})
        await asyncio.sleep(.03)
        assert '哪些内容需要保留' not in str(c.cards['card-1']) and '已选择' not in str(c.cards['card-1'])
        receipt=str(c.cards['card-2'])
        assert 'KEEP_FULL_SCOPE' in receipt and '可读布局' in receipt and label in receipt
        assert 'FINAL_EXACT_ANSWER' in str(c.cards['card-3'])
        assert session.active_interaction.choice==choice and session.active_interaction.choice_label==label
        assert session.active_interaction.callback_token==token
        assert len(c.cards)==3


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal',['message.completed','message.failed'])
async def test_missing_text_receipt_render_keeps_full_owner_scope_and_answer(monkeypatch,terminal):
    from hermes_feishu_card import server
    c=Client();app=create_app(c,card_config={'interaction_mode':'text','flush_interval_ms':0})
    async with TestClient(TestServer(app)) as http:
        await post(http,'message.started',0)
        await post(http,'interaction.requested',1,dict(interaction_id='clarify',kind='clarify',
            prompt='QUESTION_SCOPE',description='END_SCOPE',options=[{'label':'A','value':'a'}]))
        monkeypatch.setattr(server,'_render_static_display_card',lambda *args,**kwargs:None)
        c.updates.clear()
        await post(http,terminal,2,{'answer':'FINAL_EXACT','error':'FINAL_EXACT'})
        await asyncio.sleep(.03)
        assert not any(mid=='card-2' for mid,_ in c.updates)
        assert app[server.DIAGNOSTICS_KEY]['last_interaction_receipt_update']=='failed'
        assert not app[SESSIONS_KEY]['turn'].active_interaction.receipt_fingerprint
        assert 'FINAL_EXACT' in str(c.cards['card-1']) and 'END_SCOPE' in str(c.cards['card-1'])
        assert len(c.cards)==2
