import copy
import json

import pytest

from hermes_feishu_card.config import load_config
from hermes_feishu_card.reading import explain_reading_config
from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession


def timeline_session():
    session = CardSession(conversation_id="chat", message_id="message", chat_id="oc_test")
    for group in (1, 2):
        session.timeline.record_reasoning(f"reasoning-{group}")
        for index in range(4):
            session.timeline.record_tool(f"{group}-{index}", f"success-{group}-{index}", "completed")
        session.timeline.record_tool(f"failed-{group}", f"failed-{group}", "failed", "failure details")
        session.timeline.record_tool(f"running-{group}", f"running-{group}", "running")
    session._tool_call_count = 12
    return session


def panel_text(card):
    return json.dumps(next(e for e in card["body"]["elements"] if e.get("element_id") == "auxiliary_timeline"), ensure_ascii=False)


def test_optional_panel_order_keeps_default_and_chronological_body():
    session = timeline_session()
    default = panel_text(render_card(session, max_timeline_items=30))
    chronological = panel_text(render_card(session, max_timeline_items=30, timeline_order="chronological"))
    # Fork contract: the fork's DEFAULT is chronological, not upstream's newest_first. Upstream ships
    # the knob defaulting to the old order and asks the reader to opt in; the user's call is that the
    # convenient order has to be what you get with no configuration (「要争默认值。使用者便利第一位。
    # 不应该给使用者增加麻烦」), and the panel reading top-to-bottom in the order the turn happened is
    # the behaviour this fork requested first (「正文的思考应该正序」/「Timeline 的工具正序一下」).
    assert default.index("reasoning-1") < default.index("reasoning-2")
    newest_first = panel_text(render_card(session, max_timeline_items=30, timeline_order="newest_first"))
    assert newest_first.index("reasoning-2") < newest_first.index("reasoning-1")
    assert chronological.index("reasoning-1") < chronological.index("reasoning-2")
    for order in ("chronological", "newest_first"):
        card = render_card(session, max_timeline_items=30, timeline_order=order, reasoning_format="code")
        body = json.dumps(card, ensure_ascii=False)
        assert body.index("reasoning-1") < body.index("reasoning-2")


def test_last_two_successes_per_reasoning_retains_failures_counts_and_history():
    session = timeline_session()
    before = copy.deepcopy(session.timeline)
    card = render_card(session, max_timeline_items=30, timeline_tools_per_reasoning=2)
    text = panel_text(card)
    for group in (1, 2):
        assert f"success-{group}-0" not in text and f"success-{group}-1" not in text
        assert f"success-{group}-2" in text and f"success-{group}-3" in text
        assert f"failed-{group}" in text and f"running-{group}" in text
    assert "已折叠 4" in text
    assert session.tool_count == 12 and session.timeline == before


def test_timeline_config_profiles_and_readonly_explanation(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("card:\n  timeline_order: CHRONOLOGICAL\n  timeline_tools_per_reasoning: 2\n"
                    "profiles:\n  work:\n    card:\n      timeline_order: newest_first\n")
    config = load_config(path)
    assert config["card"]["timeline_order"] == "chronological"
    assert config["profiles"]["work"]["card"]["timeline_order"] == "newest_first"
    report = explain_reading_config({"card":{"timeline_tools_per_reasoning":2}})
    assert "timeline_tools_per_reasoning" in json.dumps(report)


@pytest.mark.parametrize("field,value", [("timeline_order", "random"), ("timeline_tools_per_reasoning", "true"),
                                         ("timeline_tools_per_reasoning", "-1"), ("timeline_tools_per_reasoning", "101")])
def test_invalid_timeline_options_are_rejected(tmp_path, field, value):
    path = tmp_path / "config.yaml"
    path.write_text(f"card:\n  {field}: {value}\n")
    with pytest.raises(ValueError, match=field):load_config(path)
