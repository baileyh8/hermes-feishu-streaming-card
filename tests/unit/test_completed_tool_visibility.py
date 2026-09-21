import pytest

from hermes_feishu_card import render
from hermes_feishu_card.config import DEFAULT_CONFIG
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession


def session_with_tool():
    session = CardSession(conversation_id='fixture', message_id='fixture', chat_id='fixture')
    session.apply(SidecarEvent.from_dict({
        'schema_version': '1', 'event': 'tool.updated', 'platform': 'feishu',
        'conversation_id': 'fixture', 'message_id': 'fixture', 'chat_id': 'fixture',
        'sequence': 1, 'created_at': 10.0,
        'data': {'tool_id': 'fixture-tool', 'name': 'terminal', 'status': 'completed', 'detail': 'pytest -q'},
    }))
    session.answer_text = 'Preserved answer'
    return session


def tool_rows(card):
    return [e for e in card['body']['elements'] if e.get('element_id', '').startswith('tool_activity_')]


@pytest.mark.parametrize('status', ['completed', 'failed'])
@pytest.mark.parametrize('reasoning', [True, False])
def test_terminal_tool_visibility_changes_only_content_tool_rows(status, reasoning):
    session = session_with_tool()
    session.status = status
    shown = render_card(session, show_reasoning=reasoning)
    hidden = render_card(session, show_reasoning=reasoning, hide_completed_tool_activity=True)
    assert tool_rows(shown)
    if status == "completed":
        assert not tool_rows(hidden)
    else:
        # Contract change (v4.6.6): a finished turn now hides only the SUCCESSFUL rows, on completed and
        # failed alike, so this completed fixture row goes in both cases — v4.6.6 moved the filtering
        # into the renderer, where it keeps every non-successful row.
        #
        # What the fork actually insists on is unchanged and is stronger than "a failed turn keeps
        # everything": a row that did NOT succeed must survive, because it carries the 已中断 pill —
        # WHERE the run stopped, the one thing a reader opens a failed card for. That is pinned by
        # `test_terminal_compaction_preserves_unsuccessful_tool_evidence` below (it runs for
        # failed/error/cancelled/interrupted/running) and by upstream's own
        # test_timeline_active_visibility::test_terminal_compaction_keeps_old_interrupted_body_tools_in_the_panel.
        assert not tool_rows(hidden)
    assert hidden['header'] == shown['header']
    assert hidden['body']['elements'] == [
        e for e in shown['body']['elements'] if e not in tool_rows(shown)
    ]
    assert "工具 #1" in hidden["header"]["title"]["content"]
    assert 'fixture-tool' in session.tools


def test_switch_preserves_live_progress_and_default(monkeypatch):
    session = session_with_tool()
    # The footer shows a spinner whose FRAME is a function of the wall clock
    # (`_SPINNER_FRAMES[int(_time.time() * 8) % 10]`), so two independent render_card calls can
    # disagree on that one character and nothing else — which they did, intermittently, on the
    # whole-card comparisons below. Pin the frame rather than the clock (freezing `_time` would also
    # skew every elapsed-time reading) and rather than weakening the comparisons: the two cards must
    # still match in every other respect, which is the point.
    monkeypatch.setattr(render, "_spinner_frame", lambda: "⠋")
    # Default True in the CONFIG, against upstream's False — the user's call: a finished card reads as
    # answer + footer, and a deployment that wants the rows back sets it false. The render_card
    # parameter still defaults to False; the server passes the config value in explicitly.
    assert DEFAULT_CONFIG['card']['hide_completed_tool_activity'] is True
    # A RUNNING turn keeps its rows whatever the switch says: they are the live progress line, and
    # the 思考过程 panel below does not exist yet.
    assert render_card(session) == render_card(session, hide_completed_tool_activity=True)
    assert tool_rows(render_card(session))
    shown = render_card(session)
    hidden = render_card(session, hide_completed_tool_activity=True)
    assert tool_rows(shown) == tool_rows(hidden)
    # A finished one does not, once the switch is on.
    session.status = "completed"
    assert tool_rows(render_card(session))
    assert not tool_rows(render_card(session, hide_completed_tool_activity=True))
    session.tools.clear()
    assert render_card(session) == render_card(session, hide_completed_tool_activity=True)


@pytest.mark.parametrize('status',['failed','error','cancelled','interrupted','running'])
@pytest.mark.parametrize('flag',['hide_completed_tool_activity','hide_successful_tool_activity'])
def test_terminal_compaction_preserves_unsuccessful_tool_evidence(status,flag):
    session=session_with_tool()
    session.tools['fixture-tool'].status=status
    session.status='completed'
    card=render_card(session,**{flag:True})
    assert tool_rows(card)
    assert '已完成' not in tool_rows(card)[0]['content']
