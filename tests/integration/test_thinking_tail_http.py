import pytest
from aiohttp.test_utils import TestClient, TestServer
from hermes_feishu_card.card_limits import inspect_card_limits
from tests.integration.test_server import FakeFeishuClient, create_app, event_payload, wait_for_card_update


@pytest.mark.parametrize('width_mode', ['default', 'compact', 'fill'])
@pytest.mark.parametrize('streaming', [False, True])
async def test_live_tail_preserves_one_card_and_full_final_answer(streaming, width_mode):
    client = FakeFeishuClient()
    app = create_app(client, card_config={'width_mode': width_mode, 'thinking_body_tail_chars': 2400,
                      'stream_thinking_to_body': True, 'streaming_mode': streaming, 'flush_interval_ms': 0})
    http = TestClient(TestServer(app)); await http.start_server()
    try:
        assert (await http.post('/events', json=event_payload('message.started', 0))).status == 200
        thought = 'OLD_HEAD ' + '思考🙂' * 17000 + ' NEW_TAIL'
        assert (await http.post('/events', json=event_payload('thinking.delta', 1, {'text': thought}))).status == 200
        await wait_for_card_update(client, 'NEW_TAIL')
        card = client.updated[-1][1]
        assert card['config'].get('width_mode') == (None if width_mode == 'default' else width_mode)
        primary = ''.join(x.get('content', '') for x in card['body']['elements'] if x.get('element_id', '').startswith('main_content'))
        assert primary == thought[-2400:] and inspect_card_limits(card).safe
        assert len(client.sent) == 1
        answer = 'Complete answer, never windowed. ' * 160
        assert (await http.post('/events', json=event_payload('message.completed', 2, {'answer': answer}))).status == 200
        await wait_for_card_update(client, 'Complete answer')
        card = client.updated[-1][1]
        assert card['config'].get('width_mode') == (None if width_mode == 'default' else width_mode)
        primary = ''.join(x.get('content', '') for x in card['body']['elements'] if x.get('element_id', '').startswith('main_content'))
        assert primary == answer.strip()
        assert len(client.sent) == 1 and inspect_card_limits(card).safe
        assert (await http.post('/events', json=event_payload('thinking.delta', 3, {'text': 'LATE_THOUGHT'}))).status == 200
        assert 'LATE_THOUGHT' not in str(client.updated[-1][1])
    finally:
        await http.close()


@pytest.mark.parametrize('width_mode', ['compact', 'fill'])
def test_legacy_owner_keeps_bounded_primary_and_receipt(width_mode):
    from hermes_feishu_card import server
    from hermes_feishu_card.session import CardSession
    from hermes_feishu_card.legacy_owner import static_legacy_receipt
    s = CardSession('c', 'm', 'chat')
    s.thinking_text = 'OLD ' + '思考' * 25000 + ' NEW_TAIL'
    s.legacy_owner_receipt = static_legacy_receipt({'elements': [{'tag': 'markdown', 'content': 'Approved scope receipt'}]})
    app = create_app(FakeFeishuClient(), card_config={'width_mode': width_mode, 'thinking_body_tail_chars': 2400})
    app[server.SESSION_CARD_CONFIGS_KEY]['m'] = {'width_mode': width_mode, 'thinking_body_tail_chars': 2400}
    result = server._render_session_card_result_for_app(app, s, session_key='m')
    result = server._existing_owner_render_result(app, s, result, session_key='m')
    assert result.disposition == 'card' and inspect_card_limits(result.card).safe
    assert 'NEW_TAIL' in str(result.card) and 'Approved scope receipt' in str(result.card)
    assert 'OLD ' not in str(result.card)
    assert 'schema' not in result.card and 'width_mode' not in result.card['config']
