from types import SimpleNamespace
import pytest
from hermes_feishu_card import hook_runtime
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession


def session():
    return CardSession(conversation_id='fixture', message_id='fixture', chat_id='fixture')


def apply(s, kind, sequence, data, at=None):
    return s.apply(SidecarEvent.from_dict(dict(schema_version='1', platform='feishu', event=kind,
        conversation_id='fixture', message_id='fixture', chat_id='fixture', sequence=sequence,
        created_at=float(sequence if at is None else at), data=data)))


def rows(s):
    return [e['content'] for e in render_card(s)['body']['elements'] if e.get('element_id','').startswith('tool_activity_')]


def test_parallel_tool_windows_keep_each_running_predecessor_in_call_order():
    s=session()
    for n in range(1,7):
        apply(s,'tool.updated',n,{'tool_id':str(n),'name':'tool'+str(n),'status':'running' if n in [3,6] else 'completed'})
    content='\n'.join(rows(s))
    assert len(rows(s))==4
    for n in [2,3,5,6]:assert 'tool'+str(n) in content
    for n in [1,4]:assert 'tool'+str(n) not in content
    assert [content.index('tool'+str(n)) for n in [2,3,5,6]]==sorted(content.index('tool'+str(n)) for n in [2,3,5,6])


@pytest.mark.parametrize('status',['completed','failed','cancelled'])
@pytest.mark.parametrize('measured',[True,False])
def test_terminal_tool_row_retains_measured_or_derived_duration(status,measured):
    s=session()
    apply(s,'tool.updated',1,{'tool_id':'t','name':'terminal','status':'running'},at=10)
    data={'tool_id':'t','name':'terminal','status':status}
    if measured:data['duration_ms']=7000
    apply(s,'tool.updated',2,data,at=17)
    assert s.tools['t'].duration_ms==7000
    assert '7s' in rows(s)[0]


@pytest.mark.parametrize('bad',[float('inf'),float('nan'),-5,'invalid',True])
def test_invalid_tool_duration_does_not_break_rendering(bad):
    s=session();apply(s,'tool.updated',1,{'tool_id':'t','name':'terminal','status':'completed','duration_ms':bad})
    assert s.tools['t'].duration_ms is None
    assert rows(s)


def test_interruption_metrics_reach_failed_card_and_partial_text_survives():
    source=SimpleNamespace(platform='feishu',chat_id='fixture',thread_id='fixture')
    result={'_hfc_turn_seconds':42.5,'model':'fixture-model','input_tokens':1234,'output_tokens':567,'last_prompt_tokens':3000,'context_length':10000}
    local=hook_runtime.interrupted_turn_locals(source,'fixture',result)
    payload=hook_runtime.build_event('message.failed',local)
    s=session();s.answer_text='Partial answer'
    apply(s,'message.failed',2,payload['data'])
    assert s.model=='fixture-model' and s.duration==42.5
    assert s.tokens=={'input_tokens':1234,'output_tokens':567}
    assert s.context=={'used_tokens':3000,'max_tokens':10000}
    assert 'Partial answer' in s.answer_text and '用户已打断' in s.answer_text
    card=str(render_card(s));assert 'fixture-model' in card and '42s' in card
    assert not apply(s,'message.failed',3,{'error':'late','model':'wrong'})
    assert s.model=='fixture-model'


@pytest.mark.parametrize('bad',[None,0,-1,float('inf'),float('nan'),True,'invalid'])
def test_failure_placeholders_do_not_erase_known_metrics(bad):
    s=session();s.model='known';s.duration=12;s.tokens={'input_tokens':10,'output_tokens':20};s.context={'used_tokens':30,'max_tokens':100}
    apply(s,'message.failed',1,{'error':'stop','model':'Unknown','duration':bad,'tokens':{'input_tokens':bad,'output_tokens':0},'context':{'used_tokens':0,'max_tokens':bad}})
    assert s.model=='known' and s.duration==12
    assert s.tokens=={'input_tokens':10,'output_tokens':20}
    assert s.context=={'used_tokens':30,'max_tokens':100}


def test_tool_duration_survives_private_checkpoint_and_legacy_record(tmp_path):
    import hashlib
    import json
    from hermes_feishu_card.session_store import SessionStore
    store=SessionStore(tmp_path);s=session()
    apply(s,'tool.updated',1,{'tool_id':'t','name':'terminal','status':'completed','duration_ms':7000})
    store.save('fixture',s,'om_fixture',None,'',{},'fixture_client')
    restored=store.load()[0]['session']
    assert restored.tools['t'].duration_ms==7000 and '7s' in rows(restored)[0]
    path=next(store.root.glob('*.json'));data=json.loads(path.read_text())
    del data['record']['session']['tools']['t']['duration_ms']
    encoded=json.dumps(data['record'],ensure_ascii=False,allow_nan=False,sort_keys=True).encode()
    data['digest']=hashlib.sha256(encoded).hexdigest();path.write_text(json.dumps(data))
    restored=store.load()[0]['session']
    assert restored.tools['t'].duration_ms is None and rows(restored)


@pytest.mark.asyncio
async def test_actual_queued_interruption_block_delivers_metrics_then_next_turn(monkeypatch):
    from hermes_feishu_card.install import patcher
    seen=[]
    async def emit(local_vars, *, event_name):
        seen.append(hook_runtime.build_event(event_name,local_vars));return True
    monkeypatch.setattr(hook_runtime,'emit_from_hermes_locals_async',emit)
    source=SimpleNamespace(platform='feishu',chat_id='fixture',thread_id='fixture',_hfc_turn_id='om_old')
    event=SimpleNamespace(source=source,message_id='om_new',reply_to_message_id='om_root')
    context=SimpleNamespace(source=source,event_message_id='om_old')
    block=''.join(patcher._render_queued_followup_hook_block('    ','\n'))
    namespace={}
    exec('async def run(source,next_source,pending_event,turn_ctx,result):\n'+block+'    return next_source\n',namespace)
    result={'interrupted':True,'_hfc_turn_seconds':7,'model':'fixture-model','input_tokens':100,'output_tokens':50}
    next_source=await namespace['run'](source,source,event,context,result)
    assert [p['event'] for p in seen]==['message.failed','message.started']
    assert seen[0]['turn_id']=='om_old' and seen[0]['data']['duration']==7
    assert seen[0]['data']['model']=='fixture-model'
    assert seen[0]['data']['tokens']=={'input_tokens':100,'output_tokens':50}
    assert next_source is not source and next_source._hfc_turn_id=='om_new'
    assert source._hfc_turn_id=='om_old'
