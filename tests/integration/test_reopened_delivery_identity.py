"""Issue #359: real IM create UUID deduplication must not overwrite old cards."""
import copy
import json
from types import SimpleNamespace
import pytest
from aiohttp.test_utils import TestClient, TestServer
from hermes_feishu_card import server
from tests.integration.test_server import create_app, event_payload

class DeduplicatingClient:
    def __init__(self):
        self.ids = {}; self.cards = {}; self.updated = []
    async def send_card_delivery(self, chat_id, card, **kwargs):
        token = kwargs['delivery_uuid']
        if token not in self.ids:
            mid = f'card-{len(self.ids)}';self.ids[token]=mid;self.cards[mid]=copy.deepcopy(card)
        return SimpleNamespace(message_id=self.ids[token],retry_count=0)
    async def update_card_message(self, message_id, card):
        self.updated.append(message_id);self.cards[message_id]=copy.deepcopy(card)

@pytest.mark.parametrize('first_event', ['message.started','answer.delta'])
@pytest.mark.parametrize('terminal', ['message.completed','message.failed'])
async def test_reopening_twice_keeps_previous_cards_and_checkpoint(first_event, terminal, tmp_path):
    transport=DeduplicatingClient()
    app=create_app(transport,card_config={'flush_interval_ms':0},session_store_directory=tmp_path)
    async with TestClient(TestServer(app)) as client:
        async def post(name, seq, data=None):
            response=await client.post('/events',json=event_payload(name,seq,data))
            assert response.status==200,await response.text()
            return await response.json()
        await post('message.started',0)
        answer='Original substantial answer. '*50
        await post('answer.delta',1,{'text':answer})
        await post(terminal,2,{'answer':answer,'error':'expected fixture failure'} if terminal.endswith('failed') else {'answer':answer})
        old=copy.deepcopy(transport.cards); updates=list(transport.updated)
        for generation in (1,2):
            await post(first_event,0,{'policy_new_turn':True,'text':f'followup {generation}'})
            await post('message.completed',1,{'answer':f'followup {generation}'})
            assert len(transport.cards)==generation+1
            for mid,card in old.items():
                assert transport.cards[mid]==card
                assert transport.updated.count(mid)==updates.count(mid)
            old=copy.deepcopy(transport.cards);updates=list(transport.updated)
        late=await post('answer.delta',10,{'text':'late output'})
        assert late['applied'] is False
        assert transport.cards==old
        records=app[server.SESSION_STORE_KEY].load()
        active=[r for r in records if r['key']=='hermes-message-1']
        assert len(active)==1
        assert active[0]['message_id']=='card-2'
        assert active[0]['session'].answer_text=='followup 2'
        assert answer.rstrip() in json.dumps(transport.cards['card-0'])

async def test_failed_reopen_keeps_original_until_followup_can_create(tmp_path):
    from hermes_feishu_card.feishu_client import FeishuAPIError
    class FailedCreateClient(DeduplicatingClient):
        fail = False
        async def send_card_delivery(self, chat_id, card, **kwargs):
            if self.fail:
                raise FeishuAPIError('fixture refusal', retryable=False, outcome='not_sent')
            return await super().send_card_delivery(chat_id,card,**kwargs)
    transport=FailedCreateClient()
    app=create_app(transport,card_config={'flush_interval_ms':0},session_store_directory=tmp_path)
    async with TestClient(TestServer(app)) as client:
        async def post(name,seq,data=None):
            response=await client.post('/events',json=event_payload(name,seq,data))
            return response.status,await response.json()
        await post('message.started',0)
        await post('message.completed',1,{'answer':'ORIGINAL ANSWER'})
        original=copy.deepcopy(transport.cards['card-0'])
        transport.fail=True
        status,_=await post('message.started',0)
        assert status==502
        assert app[server.SESSIONS_KEY]['hermes-message-1'].answer_text=='ORIGINAL ANSWER'
        transport.fail=False
        await post('answer.delta',0,{'policy_new_turn':True,'text':'FOLLOWUP'})
        await post('message.completed',1,{'answer':'FOLLOWUP'})
        assert len(transport.cards)==2
        assert transport.cards['card-0']==original
