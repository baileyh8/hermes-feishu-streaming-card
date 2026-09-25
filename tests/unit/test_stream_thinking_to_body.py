import json

import pytest

from hermes_feishu_card.config import DEFAULT_CONFIG
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.render import render_card_result
from hermes_feishu_card.session import CardSession


def apply(session, kind, sequence, data):
    return session.apply(SidecarEvent.from_dict(dict(
        schema_version='1', platform='feishu', event=kind, sequence=sequence,
        conversation_id='fixture', message_id='fixture', chat_id='fixture',
        created_at=float(sequence), data=data,
    )))


def thought_session(text='live reasoning marker'):
    session = CardSession(conversation_id='fixture', message_id='fixture', chat_id='fixture')
    apply(session, 'thinking.delta', 1, {'text': text})
    return session


def body_text(card):
    return '\n'.join(e.get('content', '') for e in card['body']['elements'])


def panel(card):
    return next(e for e in card['body']['elements'] if e.get('element_id') == 'auxiliary_timeline')


def test_default_preserves_live_body_and_session_storage():
    session = thought_session()
    assert DEFAULT_CONFIG['card']['stream_thinking_to_body'] is True
    assert 'live reasoning marker' in body_text(render_card_result(session).card)
    assert session.timeline.snapshot() == []


@pytest.mark.parametrize('reasoning_format', ['panel', 'code'])
@pytest.mark.parametrize('with_tool', [False, True])
def test_opt_out_moves_live_thinking_to_bounded_panel_only(reasoning_format, with_tool):
    session = thought_session()
    if with_tool:
        apply(session, 'tool.updated', 2, {'tool_id': 't1', 'name': 'terminal', 'status': 'running'})
    result = render_card_result(session, stream_thinking_to_body=False, reasoning_format=reasoning_format)
    assert result.disposition == 'card'
    assert 'live reasoning marker' not in body_text(result.card)
    assert 'live reasoning marker' in str(panel(result.card))
    assert panel(result.card)['expanded'] is False
    if with_tool:
        assert any(e.get('element_id', '').startswith('tool_activity_') for e in result.card['body']['elements'])
    else:
        assert '正在加载上下文' in body_text(result.card)
    assert session.thinking_text == 'live reasoning marker'
    assert not any(e.kind == 'reasoning' for e in session.timeline.snapshot())


def test_opt_out_honors_show_reasoning_without_losing_answer():
    session = thought_session()
    card = render_card_result(session, stream_thinking_to_body=False, show_reasoning=False).card
    assert 'live reasoning marker' not in str(card)
    apply(session, 'answer.delta', 2, {'text': 'Answer starts here'})
    card = render_card_result(session, stream_thinking_to_body=False).card
    assert 'Answer starts here' in body_text(card)
    assert 'live reasoning marker' in str(panel(card))


@pytest.mark.parametrize('terminal', ['message.completed', 'message.failed'])
def test_opt_out_does_not_change_terminal_or_failure_content(terminal):
    session = thought_session()
    apply(session, terminal, 2, {'answer': 'Final answer', 'error': 'Stopped'})
    assert render_card_result(session).card == render_card_result(session, stream_thinking_to_body=False).card
    if terminal == 'message.failed':
        assert 'live reasoning marker' in body_text(render_card_result(session).card)
    assert not apply(session, 'thinking.delta', 3, {'text': 'late reasoning'})


def test_long_live_reasoning_no_longer_forces_native_handoff_at_default_panel_budget():
    session = thought_session('推理细节' * 9000)
    assert render_card_result(session).disposition == 'deferred_native'
    result = render_card_result(session, stream_thinking_to_body=False)
    assert result.disposition == 'card'
    assert result.inspection.safe
    assert '已截断' in str(panel(result.card))
    assert '推理细节' not in body_text(result.card)
    # User-configured huge panels and genuine huge answers remain subject to the global card gate.
    assert render_card_result(session, stream_thinking_to_body=False, max_reasoning_chars=100000).disposition == 'deferred_native'
    apply(session, 'answer.delta', 2, {'text': '答案' * 20000})
    assert render_card_result(session, stream_thinking_to_body=False).disposition == 'deferred_native'


def test_opt_out_respects_approval_and_restored_display_state(tmp_path):
    from hermes_feishu_card.session import InteractionState
    from hermes_feishu_card.session_store import SessionStore
    session=thought_session()
    store=SessionStore(tmp_path)
    store.save('fixture',session,'om_fixture',None,'',{},'fixture_client')
    restored=store.load()[0]['session']
    assert 'live reasoning marker' in str(panel(render_card_result(restored,stream_thinking_to_body=False).card))
    restored.active_interaction=InteractionState('fixture','approval','Approve this command?',callback_token='fixture-token')
    card=render_card_result(restored,stream_thinking_to_body=False).card
    assert 'live reasoning marker' not in str(card)
    assert 'Approve this command?' in str(card)
