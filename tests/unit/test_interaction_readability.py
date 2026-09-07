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
    assert "1. " + label in body
    assert "2. 继续" in body
    assert [b["text"]["content"] for b in buttons] == ["1", "2"]
    assert buttons[0]["type"] == "danger"
    assert buttons[0]["value"]["choice"] == "original-value"
    assert buttons[0]["value"]["choice_label"] == label
    assert buttons[0]["value"]["token"] == "secret-test-token"


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
