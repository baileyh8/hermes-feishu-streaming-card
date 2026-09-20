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


@pytest.mark.parametrize('reasoning', [True, False])
def test_terminal_tool_visibility_changes_only_content_tool_rows(reasoning):
    """A COMPLETED turn drops its content-area tool rows when the switch is on.

    Maintainer note (contract change): the switch was upstreamed in v4.6.2 covering both `completed`
    and `failed`; this PR narrows it to COMPLETED on purpose. A failed card's rows carry the 已中断
    pill — WHERE the run stopped — which is the one thing a reader opens a failed card for. Hiding
    them deletes the diagnostic along with the noise. The `failed` arm therefore asserts the rows are
    KEPT; see ``test_a_failed_turn_keeps_the_rows_that_name_where_it_stopped``.
    """
    session = session_with_tool()
    session.status = 'completed'
    # Explicit False: the default is True (see the note on the default test below), so "shown" has to
    # ask for the rows back rather than rely on what render_card does with no argument.
    shown = render_card(session, show_reasoning=reasoning, hide_completed_tool_activity=False)
    hidden = render_card(session, show_reasoning=reasoning, hide_completed_tool_activity=True)
    assert tool_rows(shown)
    assert not tool_rows(hidden)
    assert hidden['header'] == shown['header']
    assert hidden['body']['elements'] == [
        e for e in shown['body']['elements'] if e not in tool_rows(shown)
    ]
    assert "工具 #1" in hidden["header"]["title"]["content"]
    assert 'fixture-tool' in session.tools


@pytest.mark.parametrize('reasoning', [True, False])
def test_a_failed_turn_keeps_the_rows_that_name_where_it_stopped(reasoning):
    """The deliberate difference from the v4.6.2 upstream behaviour: failed is not hidden.

    This is the requirement behind issue #328 — 「如果整个卡已经完成，那么正文里面最近的工具行也的确
    可以关闭展示了」. The word is 完成 (finished successfully): the complaint is about clutter AFTER a
    turn that worked, not about a card that died. A stopped run's last row answers "how far did it
    get", so the switch must not be the thing that erases it.
    """
    session = session_with_tool()
    session.status = 'failed'
    shown = render_card(session, show_reasoning=reasoning)
    with_switch = render_card(session, show_reasoning=reasoning, hide_completed_tool_activity=True)
    assert tool_rows(shown)
    assert tool_rows(with_switch), "a failed turn keeps the rows that name where it stopped"


def test_switch_preserves_live_progress_and_default():
    """The default hides a finished turn's rows, and never touches a running one.

    Maintainer note (contract change): the default is True, not the upstream False. The switch exists
    because a finished card restates rows the 思考过程 panel already holds; the deployment that wants
    them back sets it false — one line, on purpose. Defaulting to False would ship the feature OFF and
    make every deployment that wants it opt in, which is backwards for the very clutter #328 reports.
    """
    session = session_with_tool()
    assert DEFAULT_CONFIG['card']['hide_completed_tool_activity'] is True
    # A running turn is untouched either way — the rows ARE its progress.
    assert session.status not in {'completed', 'failed'}
    assert render_card(session) == render_card(session, hide_completed_tool_activity=True)
    assert tool_rows(render_card(session))
    session.tools.clear()
    assert render_card(session) == render_card(session, hide_completed_tool_activity=True)
