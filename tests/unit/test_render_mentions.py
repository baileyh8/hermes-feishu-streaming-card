from __future__ import annotations

from hermes_feishu_card.render import render_card, render_card_result
from hermes_feishu_card.session import CardSession, InteractionState


def _pending_approval_session() -> CardSession:
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = InteractionState(
        interaction_id="approval-1",
        kind="approval",
        prompt="允许继续执行吗？",
    )
    return session


def test_render_accepts_mentions_enabled_kwarg_with_default():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.answer_text = "答案"

    card = render_card(session, mentions_enabled=True)

    assert card["header"]["title"]["content"] == "Hermes Agent"


def test_render_mentions_enabled_false_keeps_pending_approval_card():
    session = _pending_approval_session()

    card = render_card(session, title="研发助手", mentions_enabled=False)

    assert card["header"]["title"]["content"] == "待审批：允许继续执行吗？"


def test_render_mentions_enabled_true_keeps_pending_approval_card():
    session = _pending_approval_session()

    card = render_card(session, title="研发助手", mentions_enabled=True)

    assert card["header"]["title"]["content"] == "待审批：允许继续执行吗？"


def test_render_mentions_enabled_false_completed_interaction_still_renders():
    session = _pending_approval_session()
    interaction = session.active_interaction
    assert interaction is not None
    interaction.status = "completed"
    interaction.choice = "1"
    interaction.choice_label = "继续执行"

    card = render_card(session, mentions_enabled=False)

    assert "继续执行" in result_text(card)


def test_render_card_result_plumbs_mentions_enabled_to_legacy_callback_card():
    session = _pending_approval_session()

    result = render_card_result(session, mentions_enabled=False)

    assert result.disposition == "card"
    assert result.card["header"]["title"]["content"] == "待审批：允许继续执行吗？"


def result_text(card: dict) -> str:
    if "body" in card:
        elements = card["body"]["elements"]
    else:
        elements = card["elements"]
    parts = []
    for element in elements:
        parts.append(str(element))
    return "\n".join(parts)
