import pytest
from hermes_feishu_card import hook_runtime
from hermes_feishu_card.render import render_card, render_card_result
from hermes_feishu_card.session import CardSession, InteractionState, InteractionOption


@pytest.mark.parametrize("kind", ["approval", "clarify"])
def test_short_buttons_keep_full_option_explanations_and_callback_identity(kind):
    label = "完整说明" * 100 + "最后不能丢失"
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc_test")
    session.active_interaction = InteractionState(
        interaction_id="i", kind=kind, prompt="选择", callback_token="secret-test-token",
        options=[InteractionOption(label, "original-value", "danger"), InteractionOption("继续", "next")],
    )
    card = render_card(session)
    elements = card["elements"]
    body = "\n".join(e.get("content", "") for e in elements if e.get("tag") == "markdown")
    buttons = [b for e in elements if e.get("tag") == "action" for b in e["actions"]]
    buttons += [b for e in elements if e.get("tag") == "column_set"
                for c in e["columns"] for b in c["elements"]]
    assert "1. " + label in body
    assert "2. 继续" in body
    assert [b["text"]["content"] for b in buttons] == ["1", "2"]
    assert buttons[0]["type"] == "danger"
    assert buttons[0]["value"]["choice"] == "original-value"
    assert buttons[0]["value"]["choice_label"] == label
    assert buttons[0]["value"]["token"] == "secret-test-token"


@pytest.mark.parametrize("count", [2, 4, 5, 8])
def test_non_approval_choices_use_compact_rows_and_wrap_in_fours(count):
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc_test")
    session.active_interaction = InteractionState(
        interaction_id="i", kind="clarify", prompt="选择", callback_token="secret-test-token",
        options=[InteractionOption(f"选项{n + 1}", f"value-{n + 1}") for n in range(count)],
    )
    elements = render_card(session, interaction_mode="callback")["elements"]

    # No legacy `action` container: on mobile it stretches every button across the row.
    assert not any(element.get("tag") == "action" for element in elements)
    rows = [element for element in elements if element.get("tag") == "column_set"]
    assert rows, "choices must render a compact button row"

    buttons = [column["elements"][0] for row in rows for column in row["columns"]]
    # Order, callback value and the auto width are what the click depends on.
    assert [button["text"]["content"] for button in buttons] == [
        str(n + 1) for n in range(count)
    ]
    assert [button["value"]["choice"] for button in buttons] == [
        f"value-{n + 1}" for n in range(count)
    ]
    assert all(button["value"]["token"] == "secret-test-token" for button in buttons)
    assert all(button.get("width") == "default" for button in buttons)
    assert all(row.get("flex_mode") == "flow" for row in rows)
    # Four per line, longer lists wrap instead of being squeezed.
    assert [len(row["columns"]) for row in rows] == [
        min(4, count - offset) for offset in range(0, count, 4)
    ]


def test_compact_rows_keep_custom_input_and_long_chinese_labels_in_the_body():
    label = "这是一个非常长的中文选项标签" * 20 + "最后不能丢失"
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc_test")
    session.active_interaction = InteractionState(
        interaction_id="i", kind="clarify", prompt="选择", allow_custom_input=True,
        options=[InteractionOption(label, "value-long"), InteractionOption("继续", "next")],
    )
    card = render_card(session)
    elements = card["elements"]
    body = "\n".join(e.get("content", "") for e in elements if e.get("tag") == "markdown")

    # The button stays a sequence number; the label lives in the body where it can wrap.
    assert "1. " + label in body
    assert "2. 继续" in body
    buttons = [
        column["elements"][0]
        for element in elements if element.get("tag") == "column_set"
        for column in element["columns"]
    ]
    assert [button["text"]["content"] for button in buttons] == ["1", "2"]
    # A custom answer still has an input of its own next to the compact buttons.
    assert any(element.get("tag") == "form" for element in elements)


@pytest.mark.parametrize("status", ["failed", "timeout"])
def test_accepted_approval_expiry_denies_without_reopening_native_approval(monkeypatch, status):
    monkeypatch.setattr(hook_runtime, "request_interaction_from_hermes_locals", lambda *a, **k: {"status": status})
    assert hook_runtime.request_approval_choice_from_hermes_locals({}, {"command": "echo test"}, interaction_id="i") == "deny"


def test_unaccepted_approval_keeps_native_fallback(monkeypatch):
    monkeypatch.setattr(hook_runtime, "request_interaction_from_hermes_locals", lambda *a, **k: None)
    assert hook_runtime.request_approval_choice_from_hermes_locals({}, {"command": "echo test"}, interaction_id="i") is None


def test_answer_preserves_explicit_open_id_mention_without_guessing_display_name():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc_test")
    session.answer_text = '@显示名称 <at id="ou_test_user"></at> 正文'
    card = render_card(session)
    body = "\n".join(e.get("content", "") for e in card["body"]["elements"])
    assert session.answer_text in body
    assert body.count("<at ") == 1


@pytest.mark.parametrize("multi_select", [False, True])
def test_option_body_escapes_markup_and_keeps_multiline_details(multi_select):
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc_test")
    label = '[隐藏](https://example.test) <at id="all"></at>\n第二行'
    session.active_interaction = InteractionState(
        interaction_id="i", kind="clarify", prompt="选择", multi_select=multi_select,
        options=[InteractionOption(label, "original")],
    )
    card = render_card(session)
    body = "\n".join(e.get("content", "") for e in card["elements"] if e.get("tag") == "markdown")
    assert "<at" not in body
    assert "&lt;at" in body
    assert r"\[隐藏\]\(https://example\.test\)" in body
    assert "第二行" in body


def test_oversized_option_body_falls_back_without_truncating_decision():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc_test")
    session.active_interaction = InteractionState(
        interaction_id="i", kind="approval", prompt="选择",
        options=[InteractionOption("长说明" * 10000, "once")],
    )
    assert render_card_result(session).disposition == "deferred_native"
