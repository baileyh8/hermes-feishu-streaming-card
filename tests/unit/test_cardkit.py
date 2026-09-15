import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

from hermes_feishu_card import cardkit
from hermes_feishu_card.feishu_client import FeishuAPIError, FeishuClient, FeishuClientConfig


def card(text='Loading', streaming=True):
    return {'schema': '2.0', 'config': {'streaming_mode': streaming, 'update_multi': True},
            'body': {'elements': [{'tag': 'markdown', 'element_id': 'main_content', 'content': text},
                                  {'tag': 'markdown', 'element_id': 'footer', 'content': 'Working'}]}}


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(cardkit, 'MIN_MUTATION_INTERVAL', 0)
    client = FeishuClient(FeishuClientConfig('fixture-app', 'fixture-secret'))
    calls = []

    async def token():
        return 'fixture-token'

    async def request(method, path, **kwargs):
        calls.append((method, path, deepcopy(kwargs.get('json_body'))))
        if path == '/cardkit/v1/cards':
            return {'code': 0, 'data': {'card_id': 'card_fixture'}}
        if path.startswith('/im/'):
            return {'code': 0, 'data': {'message_id': 'om_fixture'}}
        return {'code': 0}

    monkeypatch.setattr(client, '_tenant_token', token)
    monkeypatch.setattr(client, '_request_json', request)
    return client, calls


def test_client_built_without_event_loop_can_stream_on_running_loop(monkeypatch):
    # CLI/maintenance builds the client before asyncio.run; no loop exists in
    # this worker, including on Python 3.9 where Lock binds eagerly.
    with ThreadPoolExecutor(max_workers=1) as pool:
        client = pool.submit(
            FeishuClient, FeishuClientConfig('fixture-app', 'fixture-secret')
        ).result()
    calls = []

    async def token():
        return 'fixture-token'

    async def request(method, path, **kwargs):
        calls.append(path)
        return {'code': 0, 'data': {'card_id': 'card_fixture', 'message_id': 'om_fixture'}}

    monkeypatch.setattr(client, '_tenant_token', token)
    monkeypatch.setattr(client, '_request_json', request)
    result = asyncio.run(client.send_card('oc_fixture', card(), delivery_uuid='sync-built'))
    assert result == 'om_fixture'
    assert calls == ['/cardkit/v1/cards', '/im/v1/messages']


@pytest.mark.asyncio
async def test_entity_create_and_uuid_bound_topic_reply(transport):
    client, calls = transport
    result = await client.send_card_delivery('oc_group', card(), thread_id='omt_topic',
                                            reply_to_message_id='om_user', delivery_uuid='turn-1')
    assert result.message_id == 'om_fixture'
    assert calls[0][:2] == ('POST', '/cardkit/v1/cards')
    assert json.loads(calls[0][2]['data'])['config']['streaming_mode'] is True
    assert calls[1][1] == '/im/v1/messages/om_user/reply'
    assert json.loads(calls[1][2]['content']) == {'type': 'card', 'data': {'card_id': 'card_fixture'}}
    assert calls[1][2]['reply_in_thread'] is True
    assert calls[1][2]['uuid'] == 'turn-1'


@pytest.mark.asyncio
async def test_cumulative_text_updates_use_element_api_not_message_patch(transport):
    client, calls = transport
    await client.send_card('oc_group', card(), delivery_uuid='turn-1')
    await client.update_card_message('om_fixture', card('hello'))
    updated = card('hello world'); updated['body']['elements'][1]['content'] = 'Working 2s'
    await client.update_card_message('om_fixture', updated)
    mutations = calls[2:]
    assert [c[1] for c in mutations] == ['/cardkit/v1/cards/card_fixture/elements/main_content/content'] * 2
    assert [c[2]['content'] for c in mutations] == ['hello', 'hello world']
    assert [c[2]['sequence'] for c in mutations] == [1, 2]
    assert mutations[0][2]['uuid'] != mutations[1][2]['uuid']


@pytest.mark.asyncio
async def test_terminal_explicitly_closes_then_publishes_full_card(transport):
    client, calls = transport
    await client.send_card('oc_group', card(), delivery_uuid='turn-1')
    final = card('Complete answer tail', streaming=False)
    await client.update_card_message('om_fixture', final)
    assert calls[2][:2] == ('PATCH', '/cardkit/v1/cards/card_fixture/settings')
    assert json.loads(calls[2][2]['settings'])['config']['streaming_mode'] is False
    assert calls[3][:2] == ('PUT', '/cardkit/v1/cards/card_fixture')
    assert json.loads(calls[3][2]['card']['data']) == final
    assert [c[2]['sequence'] for c in calls[2:]] == [1, 2]


@pytest.mark.asyncio
async def test_structure_changes_use_entity_update_not_im_patch(transport):
    client, calls = transport
    await client.send_card('oc_group', card(), delivery_uuid='turn-1')
    changed = card('hello'); changed['body']['elements'].append({'tag': 'hr', 'element_id': 'new_section'})
    await client.update_card_message('om_fixture', changed)
    assert calls[-1][:2] == ('PUT', '/cardkit/v1/cards/card_fixture')


@pytest.mark.asyncio
async def test_retry_with_same_delivery_uuid_reuses_entity(transport):
    client, calls = transport
    for _ in range(2):
        await client.send_card('oc_group', card(), delivery_uuid='same-delivery')
    assert sum(path == '/cardkit/v1/cards' for _, path, _ in calls) == 1
    references = [body['content'] for _, path, body in calls if path.startswith('/im/')]
    assert len(set(references)) == 1


@pytest.mark.asyncio
async def test_entity_creation_failure_does_not_send_another_message(transport, monkeypatch):
    client, calls = transport
    async def failed(*args, **kwargs):
        raise FeishuAPIError('missing permission', api_code=99991672)
    monkeypatch.setattr(client, '_request_json', failed)
    with pytest.raises(FeishuAPIError):
        await client.send_card('oc_group', card(), delivery_uuid='turn-1')
    assert not client.cardkit.entities
    assert calls == []


@pytest.mark.asyncio
async def test_failed_terminal_update_can_be_retried_without_reopening_stream(transport, monkeypatch):
    client, calls = transport
    await client.send_card('oc_group', card(), delivery_uuid='turn-1')
    original = client._request_json
    failures = [True]
    async def request(method, path, **kwargs):
        if method == 'PUT' and path == '/cardkit/v1/cards/card_fixture' and failures:
            failures.pop(); raise FeishuAPIError('temporary failure', retryable=True)
        return await original(method, path, **kwargs)
    monkeypatch.setattr(client, '_request_json', request)
    with pytest.raises(FeishuAPIError):
        await client.update_card_message('om_fixture', card('final', False))
    await client.update_card_message('om_fixture', card('final', False))
    assert sum(path.endswith('/settings') for _, path, _ in calls) == 1
    assert calls[-1][2]['sequence'] == 3
    assert client.cardkit.entities['om_fixture'].streaming is False


@pytest.mark.asyncio
async def test_legacy_interactions_keep_existing_message_transport(transport):
    client, calls = transport
    legacy = {'config': {'wide_screen_mode': True}, 'elements': []}
    await client.send_card('oc_group', legacy, delivery_uuid='interaction')
    await client.update_card_message('om_fixture', legacy)
    assert [method for method, _, _ in calls] == ['POST', 'PATCH']
    assert all(path.startswith('/im/') for _, path, _ in calls)


@pytest.mark.asyncio
async def test_card_limit_rejected_before_creating_entity(transport):
    from hermes_feishu_card.card_limits import CardLimitExceeded
    client, calls = transport
    with pytest.raises(CardLimitExceeded):
        await client.send_card('oc_group', card('界' * 30000), delivery_uuid='turn-1')
    assert calls == []

@pytest.mark.asyncio
async def test_stream_lifetime_closes_and_never_reopens(transport):
    client, calls = transport
    await client.send_card('oc_group', card(), delivery_uuid='turn-1')
    client.cardkit.entities['om_fixture'].created_at -= cardkit.STREAM_LIFETIME_SECONDS + 1
    await client.update_card_message('om_fixture', card('long running'))
    await client.update_card_message('om_fixture', card('still running'))
    assert sum(path.endswith('/settings') for _, path, _ in calls) == 1
    assert json.loads(calls[-1][2]['card']['data'])['config']['streaming_mode'] is False


@pytest.mark.asyncio
async def test_concurrent_mutations_have_unique_ordered_sequences(transport):
    client, calls = transport
    await client.send_card('oc_group', card(), delivery_uuid='turn-1')
    await asyncio.gather(*(client.update_card_message('om_fixture', card(str(i))) for i in range(12)))
    sequences = [body['sequence'] for _, _, body in calls if 'sequence' in body]
    assert sequences == list(range(1, 13))
    assert client.cardkit.entities['om_fixture'].card['body']['elements'][0]['content'] == '11'


@pytest.mark.asyncio
async def test_http_contract_authentication_and_terminal_delivery(monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    monkeypatch.setattr(cardkit, 'MIN_MUTATION_INTERVAL', 0)
    requests = []

    async def endpoint(request):
        body = await request.json()
        requests.append((request.method, request.path, body))
        if request.path == '/auth/v3/tenant_access_token/internal':
            assert body == {'app_id': 'fixture-app', 'app_secret': 'fixture-secret'}
            return web.json_response({'code': 0, 'tenant_access_token': 'fixture-token', 'expire': 7200})
        assert request.headers['Authorization'] == 'Bearer fixture-token'
        if request.path == '/cardkit/v1/cards':
            return web.json_response({'code': 0, 'data': {'card_id': 'card_http'}})
        if request.path == '/im/v1/messages/om_user/reply':
            assert body['reply_in_thread'] is True
            assert json.loads(body['content'])['data']['card_id'] == 'card_http'
            return web.json_response({'code': 0, 'data': {'message_id': 'om_http'}})
        return web.json_response({'code': 0})

    app = web.Application()
    app.router.add_route('*', '/{path:.*}', endpoint)
    async with TestServer(app) as server:
        client = FeishuClient(FeishuClientConfig('fixture-app', 'fixture-secret', str(server.make_url('/'))))
        await client.send_card('oc_group', card(), reply_to_message_id='om_user',
                               reply_in_thread=True, delivery_uuid='http-delivery')
        await client.update_card_message('om_http', card('streamed answer'))
        await client.update_card_message('om_http', card('complete answer', False))
    assert [(m, p) for m, p, _ in requests][-3:] == [
        ('PUT', '/cardkit/v1/cards/card_http/elements/main_content/content'),
        ('PATCH', '/cardkit/v1/cards/card_http/settings'),
        ('PUT', '/cardkit/v1/cards/card_http'),
    ]


@pytest.mark.asyncio
async def test_retry_changed_spinner_reuses_original_visible_entity(transport):
    client, calls = transport
    await client.send_card('oc_group', card('spinner 1'), delivery_uuid='same-turn')
    await client.send_card('oc_group', card('spinner 2'), delivery_uuid='same-turn')
    assert sum(path == '/cardkit/v1/cards' for _, path, _ in calls) == 1
    await client.update_card_message('om_fixture', card('finished', False))
    assert calls[-1][1] == '/cardkit/v1/cards/card_fixture'


@pytest.mark.asyncio
async def test_empty_content_uses_full_update_instead_of_invalid_text_api(transport):
    client, calls = transport
    await client.send_card('oc_group', card('prior'), delivery_uuid='turn-1')
    await client.update_card_message('om_fixture', card(''))
    assert calls[-1][:2] == ('PUT', '/cardkit/v1/cards/card_fixture')


@pytest.mark.asyncio
async def test_long_element_identifier_uses_full_card_contract(transport):
    client, calls = transport
    initial = card('prior')
    initial['body']['elements'][0]['element_id'] = 'reasoning_entry_identifier_too_long'
    await client.send_card('oc_group', initial, delivery_uuid='turn-1')
    changed = deepcopy(initial)
    changed['body']['elements'][0]['content'] = 'next'
    await client.update_card_message('om_fixture', changed)
    assert calls[-1][:2] == ('PUT', '/cardkit/v1/cards/card_fixture')


@pytest.mark.asyncio
async def test_lost_create_response_is_not_an_unknown_im_delivery(transport, monkeypatch):
    client, calls = transport
    async def lost_create(*args, **kwargs):
        raise FeishuAPIError('response lost', retryable=True, outcome='unknown')
    monkeypatch.setattr(client, '_request_json', lost_create)
    with pytest.raises(FeishuAPIError) as error:
        await client.send_card('oc_group', card(), delivery_uuid='turn-1')
    assert error.value.outcome == 'not_sent'
    assert not client.cardkit.entities
