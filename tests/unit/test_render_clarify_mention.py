from __future__ import annotations

from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession, InteractionState, InteractionOption


def _clarify_session(
    open_id: str = "ou_clarify_123",
    kind: str = "clarify",
    status: str = "pending",
    multi_select: bool = False,
) -> CardSession:
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.sender_open_id = open_id
    session.active_interaction = InteractionState(
        interaction_id="clarify-1",
        kind=kind,
        prompt="选哪个？",
        options=[
            InteractionOption(label="A", value="A"),
            InteractionOption(label="B", value="B"),
        ],
        status=status,
        multi_select=multi_select,
    )
    return session


def _card_elements(card: dict) -> list[dict]:
    if isinstance(card.get("body"), dict):
        return card["body"].get("elements", [])
    return card.get("elements", [])


def _card_text(card: dict) -> str:
    return "\n".join(str(element) for element in _card_elements(card))


def test_clarify_pending_legacy_card_includes_mention():
    # Default interaction_mode == "callback" -> legacy card rail is the
    # production clarify card path.
    session = _clarify_session()

    card = render_card(session)

    elements = _card_elements(card)
    assert elements[0] == {
        "tag": "markdown",
        "content": '<at id="ou_clarify_123"></at> 请选择一个选项',
    }


def test_clarify_mention_disabled_by_config_flag():
    session = _clarify_session()

    card = render_card(session, mentions_enabled=False)

    assert "<at" not in _card_text(card)


def test_clarify_mention_absent_without_sender_open_id():
    session = _clarify_session(open_id="")

    card = render_card(session)

    assert "<at" not in _card_text(card)


def test_clarify_mention_absent_invalid_open_id():
    session = _clarify_session(open_id="om_invalid")

    card = render_card(session)

    assert "<at" not in _card_text(card)


def test_clarify_mention_absent_for_non_clarify_kind():
    # kind=approval is handled by the sibling task through the same shared
    # helper; use a kind excluded from both to prove the kind gate works.
    session = _clarify_session(kind="slash_confirm")

    card = render_card(session)

    assert "<at" not in _card_text(card)


def test_clarify_mention_absent_when_completed():
    session = _clarify_session(status="completed")
    interaction = session.active_interaction
    assert interaction is not None
    interaction.choice = "A"
    interaction.choice_label = "A"

    card = render_card(session)

    assert "<at" not in _card_text(card)


def test_clarify_mention_in_text_mode_card_uses_v2_element():
    session = _clarify_session()

    card = render_card(session, interaction_mode="text")

    elements = _card_elements(card)
    mention_elements = [
        element
        for element in elements
        if element.get("element_id") == "interaction_hint"
    ]
    assert len(mention_elements) == 1
    assert (
        mention_elements[0]["content"]
        == '<at id="ou_clarify_123"></at> 请选择一个选项'
    )


def test_clarify_mention_multiselect_legacy_precedes_form():
    session = _clarify_session(multi_select=True)

    card = render_card(session)

    elements = _card_elements(card)
    assert (
        elements[0]["content"]
        == '<at id="ou_clarify_123"></at> 请选择（可多选）'
    )
    assert any(
        isinstance(element, dict) and element.get("tag") == "form"
        for element in elements
    )
