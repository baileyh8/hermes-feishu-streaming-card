"""Bound only live body reasoning; preserve answers, timeline and limit fences."""
import copy
import json
import pytest
from hermes_feishu_card import render
from hermes_feishu_card.card_limits import inspect_card_limits
from hermes_feishu_card.config import load_config
from hermes_feishu_card.session import CardSession


def session():
    s = CardSession(conversation_id='c', message_id='m', chat_id='chat')
    s.thinking_text = 'OLD_HEAD ' + '思考🙂' * 17000 + ' NEW_TAIL'
    s.timeline.record_reasoning(s.thinking_text)
    s.timeline.record_tool('t', 'terminal', 'running', 'synthetic command')
    return s


def main(card):
    return ''.join(x['content'] for x in card['body']['elements'] if x.get('element_id', '').startswith('main_content'))


def test_long_reasoning_tail_keeps_tool_panel_and_unmodified_source():
    s = session(); original = copy.deepcopy(s.timeline.snapshot())
    result = render.render_card_result(s, thinking_body_tail_chars=2400)
    assert result.disposition == 'card'
    assert inspect_card_limits(result.card).safe
    assert main(result.card) == s.thinking_text[-2400:]
    assert 'synthetic command' in json.dumps(result.card, ensure_ascii=False)
    assert s.timeline.snapshot() == original
    assert len(s.thinking_text) > 50000


def test_zero_keeps_existing_long_reasoning_handoff():
    s = session()
    assert render.render_card_result(s).disposition == 'deferred_native'
    assert render.render_card_result(s, thinking_body_tail_chars=0).disposition == 'deferred_native'


@pytest.mark.parametrize('status', ['thinking', 'completed', 'failed'])
def test_answer_is_never_truncated(status):
    s = session(); s.status = status; s.answer_text = 'Full answer ' * 400
    r = render.render_card_result(s, thinking_body_tail_chars=10)
    assert main(r.card) == s.answer_text


def test_over_budget_answer_still_uses_existing_handoff():
    s = session(); s.status = 'completed'; s.answer_text = '最终答案' * 10000
    assert render.render_card_result(s, thinking_body_tail_chars=1).disposition == 'native'


def test_body_opt_out_does_not_leak_tail():
    s = session(); r = render.render_card_result(s, stream_thinking_to_body=False, thinking_body_tail_chars=12)
    assert r.disposition == 'card'
    assert 'NEW_TAIL' not in '\n'.join(x.get('content', '') for x in r.card['body']['elements'])


@pytest.mark.parametrize("width_mode", ["default", "compact", "fill"])
def test_combined_card_shrinks_body_and_keeps_auxiliary_panel(width_mode):
    s = session()
    r = render.render_card_result(s, thinking_body_tail_chars=20000, max_reasoning_chars=1500, width_mode=width_mode)
    small = render.render_card_result(s, thinking_body_tail_chars=1, max_reasoning_chars=1500, width_mode=width_mode)
    assert r.disposition == 'card' and inspect_card_limits(r.card).safe
    assert r.card['config'].get('width_mode') == (None if width_mode == 'default' else width_mode)
    assert 0 < len(main(r.card)) < 20000
    assert s.thinking_text.endswith(main(r.card))
    assert next(x for x in r.card['body']['elements'] if x.get('element_id') == 'auxiliary_timeline') == next(x for x in small.card['body']['elements'] if x.get('element_id') == 'auxiliary_timeline')


def test_other_oversized_regions_still_fail_safe():
    s = session()
    r = render.render_card_result(s, thinking_body_tail_chars=20, max_reasoning_chars=100000)
    assert r.disposition == 'deferred_native'


@pytest.mark.parametrize('value', [-1, True, 1.5, '2400'])
def test_config_rejects_invalid_tail(value, tmp_path):
    import yaml
    p = tmp_path / 'config.yaml';p.write_text(yaml.safe_dump({'card': {'thinking_body_tail_chars': value}}))
    with pytest.raises(ValueError, match='thinking_body_tail_chars'):
        load_config(p)


def test_config_accepts_opt_in_and_default(tmp_path):
    p = tmp_path / 'config.yaml';p.write_text('card:\n  thinking_body_tail_chars: 2400\n')
    assert load_config(p)['card']['thinking_body_tail_chars'] == 2400
    assert load_config(tmp_path / 'absent')['card']['thinking_body_tail_chars'] == 0
