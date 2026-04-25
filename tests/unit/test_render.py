from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession, ToolState


def test_render_thinking_card_has_two_state_label_and_tools():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "正在分析。"
    session.tools["t1"] = ToolState(tool_id="t1", name="search", status="running")
    card = render_card(session)
    assert card["schema"] == "2.0"
    assert card["header"]["subtitle"]["content"] == "思考中"
    content = str(card)
    assert "正在分析。" in content
    assert "工具调用 1 次" in content


def test_render_completed_card_replaces_thinking():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "不会展示"
    session.answer_text = "最终答案"
    session.status = "completed"
    card = render_card(session)
    content = str(card)
    assert card["header"]["subtitle"]["content"] == "已完成"
    assert "最终答案" in content
    assert "不会展示" not in content
