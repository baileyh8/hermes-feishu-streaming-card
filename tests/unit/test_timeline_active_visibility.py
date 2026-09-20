"""Issue #340: the panel must retain the live body tool and its predecessor."""
import copy
import json
import pytest
from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession, ToolState


def fixture():
    session = CardSession(conversation_id='chat', message_id='turn', chat_id='test')
    for number in range(1, 37):
        status = 'running' if number in {25, 36} else 'completed'
        tool_id = f'call-{number}'
        session.tools[tool_id] = ToolState(tool_id, f'work-{number}', status, ordinal=number, started_at=float(number))
        session.timeline.record_tool(tool_id, f'work-{number}', status, f'detail-{number}')
        if number % 4 == 0:session.timeline.record_reasoning(f'thought-{number}')
    session._tool_call_count = 36
    return session


def panel(card):
    return next(e for e in card['body']['elements'] if e.get('element_id')=='auxiliary_timeline')


@pytest.mark.parametrize('order',['newest_first','chronological'])
@pytest.mark.parametrize('per_group',[0,2])
def test_panel_keeps_old_running_call_and_its_predecessor_within_budget(order,per_group):
    s=fixture();before=copy.deepcopy(s)
    result=render_card(s,max_timeline_items=12,timeline_order=order,timeline_tools_per_reasoning=per_group)
    p=panel(result);text=json.dumps(p,ensure_ascii=False)
    for n in (24,25,35,36):
        assert f'work-{n}' in text and f'#{n}' in text
    assert '执行中' in text
    assert len([e for e in p['elements'] if 'folded' not in e.get('element_id','')])<=12
    assert s.tools==before.tools and s.timeline==before.timeline and s.tool_count==36


@pytest.mark.parametrize('terminal',['completed','failed'])
def test_terminal_panel_marks_unfinished_calls_interrupted_without_mutating_history(terminal):
    s=fixture();s.status=terminal;before=copy.deepcopy(s.timeline)
    text=json.dumps(panel(render_card(s,max_timeline_items=60)),ensure_ascii=False)
    assert '执行中' not in text
    assert '已中断' in text
    assert s.timeline==before


def test_single_slot_prioritizes_current_running_work_over_later_reasoning():
    s=fixture()
    text=json.dumps(panel(render_card(s,max_timeline_items=1)),ensure_ascii=False)
    assert 'work-36' in text and '#36' in text


def test_retained_old_tool_keeps_its_own_reasoning_inside_the_global_budget():
    s=fixture()
    p=panel(render_card(s,max_timeline_items=12,timeline_order='chronological'))
    text=json.dumps(p,ensure_ascii=False)
    assert 'thought-24' in text and 'thought-20' in text
    assert text.index('thought-20')<text.index('work-24')<text.index('thought-24')<text.index('work-25')
    assert text.index('thought-32')<text.index('work-35')<text.index('work-36')
    assert len([e for e in p['elements'] if 'folded' not in e.get('element_id','')])<=12
