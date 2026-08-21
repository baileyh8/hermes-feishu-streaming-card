from __future__ import annotations

from hermes_feishu_card.render import render_card, render_card_result
from hermes_feishu_card.session import CardSession, InteractionState

SENDER_OU = "ou_abc123DEF_xyz"
EXPECTED_MENTION = f'<at id="{SENDER_OU}"></at> 请选择一个选项'


def _approval_session(*, sender_open_id: str = SENDER_OU, kind: str = "approval") -> CardSession:
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.sender_open_id = sender_open_id
    session.active_interaction = InteractionState(
        interaction_id="approval-1",
        kind=kind,
        prompt="需要授权后继续执行",
        description="```\nrm -rf /\n```",
    )
    return session


def _body_elements(card: dict) -> list[dict]:
    if "body" in card:
        return card["body"]["elements"]
    return card["elements"]


def _mention_elements(card: dict) -> list[dict]:
    return [
        element
        for element in _body_elements(card)
        if isinstance(element, dict)
        and element.get("tag") == "markdown"
        and "<at id=" in str(element.get("content", ""))
    ]


def test_approval_legacy_callback_card_contains_mention():
    """Default callback-mode approval card embeds the @ mention inside the card."""
    session = _approval_session()

    card = render_card(session, title="研发助手")

    mentions = _mention_elements(card)
    assert len(mentions) == 1
    assert mentions[0]["content"] == EXPECTED_MENTION


def test_approval_legacy_callback_card_title_prefix():
    session = _approval_session()

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "待审批：需要授权后继续执行"


def test_approval_mention_absent_when_config_disabled():
    session = _approval_session()

    card = render_card(session, title="研发助手", mentions_enabled=False)

    assert _mention_elements(card) == []


def test_approval_mention_absent_without_sender_open_id():
    session = _approval_session(sender_open_id="")

    card = render_card(session, title="研发助手")

    assert _mention_elements(card) == []


def test_approval_mention_absent_with_invalid_sender_open_id():
    session = _approval_session(sender_open_id="user_1234")

    card = render_card(session, title="研发助手")

    assert _mention_elements(card) == []


def test_approval_mention_scoped_to_approval_kind_only():
    """kind outside approval/clarify (e.g. slash) gets no mention."""
    session = _approval_session(kind="slash")

    card = render_card(session, title="研发助手")

    assert _mention_elements(card) == []


def test_approval_mention_absent_after_completion():
    session = _approval_session()
    interaction = session.active_interaction
    assert interaction is not None
    interaction.status = "completed"
    interaction.choice = "once"
    interaction.choice_label = "允许一次"

    card = render_card(session, title="研发助手")

    assert _mention_elements(card) == []


def test_approval_mention_present_in_text_mode_interaction_elements():
    """text interaction mode also renders the mention (non-legacy path)."""
    session = _approval_session()
    session.answer_text = "需要授权"

    card = render_card(session, title="研发助手", interaction_mode="text")

    mentions = _mention_elements(card)
    assert len(mentions) == 1
    assert mentions[0]["content"] == EXPECTED_MENTION


def test_render_card_result_legacy_disposition_contains_mention():
    session = _approval_session()

    result = render_card_result(session, title="研发助手")

    assert result.disposition == "card"
    mentions = _mention_elements(result.card)
    assert len(mentions) == 1
    assert mentions[0]["content"] == EXPECTED_MENTION
