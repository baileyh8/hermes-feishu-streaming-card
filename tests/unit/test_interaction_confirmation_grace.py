"""PR #342: lost POST response, initially absent card, then visible interaction."""
from types import SimpleNamespace
import time
import pytest
from hermes_feishu_card import hook_runtime as runtime


@pytest.fixture(autouse=True)
def card_policy(monkeypatch):
    runtime.reset_runtime_state()
    monkeypatch.setenv('HERMES_FEISHU_CARD_ENABLED','true')
    monkeypatch.setattr(runtime,'_fetch_delivery_policy_sync',lambda *a,**k:{'ok':True,'disposition':'card','ttl_ms':1000})
    yield
    runtime.reset_runtime_state()


def test_late_confirmation_keeps_one_post_and_returns_the_actual_choice(monkeypatch):
    runtime.reset_runtime_state()
    monkeypatch.setenv('HERMES_FEISHU_CARD_EVENT_URL','http://sidecar.test/events')
    responses=iter([None,{'ok':True,'status':'pending','interaction_id':'late'},
                    {'ok':True,'status':'completed','interaction_id':'late','choice':'A'}])
    posts=[]
    def post(*args):posts.append(args);return runtime._POST_FAILED
    monkeypatch.setattr(runtime,'_post_interaction_event',post)
    monkeypatch.setattr(runtime,'_get_json_sync',lambda *a,**k:next(responses))
    result=runtime.request_interaction_from_hermes_locals(
        {'chat_id':'fixture-chat','message_id':'fixture-source'},kind='clarify',
        interaction_id='late',prompt='Fixture?',options=[{'label':'A','value':'A'}],
        timeout_seconds=1,poll_interval_seconds=0)
    assert result and result['choice']=='A'
    assert len(posts)==1


def test_slow_read_cannot_exceed_the_confirmation_deadline(monkeypatch):
    clock=[0.0];timeouts=[]
    def sleep(seconds):clock[0]+=seconds
    def get(url,timeout):
        timeouts.append(timeout);clock[0]+=timeout;raise TimeoutError('fixture')
    monkeypatch.setattr(runtime,'time',SimpleNamespace(monotonic=lambda:clock[0],sleep=sleep,time=time.time))
    monkeypatch.setattr(runtime,'_get_json_sync',get)
    config=SimpleNamespace(event_url='http://sidecar.test/events',timeout_seconds=30)
    assert not runtime._wait_for_interaction_card_confirmation(config,'fixture',grace_seconds=100)
    assert timeouts and clock[0]<=5.000001


@pytest.mark.parametrize('response',[{'ok':False},{'ok':True,'applied':False}])
def test_explicit_rejection_never_waits_or_replays(monkeypatch,response):
    runtime.reset_runtime_state()
    monkeypatch.setenv('HERMES_FEISHU_CARD_EVENT_URL','http://sidecar.test/events')
    posts=[]
    def post(*args):posts.append(args);return response
    def unexpected(*args,**kwargs):pytest.fail('explicit rejection must not poll')
    monkeypatch.setattr(runtime,'_post_interaction_event',post)
    monkeypatch.setattr(runtime,'_get_json_sync',unexpected)
    assert runtime.request_interaction_from_hermes_locals(
        {'chat_id':'fixture-chat','message_id':'fixture-source'},kind='clarify',
        interaction_id='rejected',prompt='Fixture?',options=[{'label':'A','value':'A'}]) is None
    assert len(posts)==1
