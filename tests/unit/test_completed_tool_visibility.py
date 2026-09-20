import pytest

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
        # Contract difference from upstream, on purpose: a FAILED turn keeps its rows, because they
        # carry the 已中断 pill — WHERE the run stopped, the one thing a reader opens a failed card
        # for. Upstream hides them here; this fork does not.
        assert tool_rows(hidden)
    assert hidden['header'] == shown['header']
    assert hidden['body']['elements'] == [
        e for e in shown['body']['elements'] if e not in tool_rows(shown) or status == "failed"
    ]
    assert "工具 #1" in hidden["header"]["title"]["content"]
    assert 'fixture-tool' in session.tools


def test_switch_preserves_live_progress_and_default():
    session = session_with_tool()
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
