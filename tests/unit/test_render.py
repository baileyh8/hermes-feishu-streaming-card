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


def test_render_failed_card_shows_error_without_thinking():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "不会展示"
    session.answer_text = "处理出错"
    session.status = "failed"
    card = render_card(session)
    content = str(card)
    assert card["config"]["summary"]["content"] == "处理失败"
    assert card["header"]["template"] == "red"
    assert card["header"]["subtitle"]["content"] == "处理失败"
    assert "处理出错" in content
    assert "不会展示" not in content
    assert "已停止" in content


def test_render_card_filters_think_tags_at_render_boundary():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "<think>hidden</think>可见内容"
    card = render_card(session)
    content = str(card)
    assert "<think>" not in content
    assert "</think>" not in content
    assert "hidden可见内容" in content


def test_render_completed_card_handles_empty_tokens_and_non_numeric_duration():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.duration = "bad"
    card = render_card(session)
    content = str(card)
    assert "耗时 0.0s" in content
    assert "输入 0" in content
    assert "输出 0" in content


def test_render_completed_card_handles_missing_token_stats():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.tokens = None
    card = render_card(session)
    content = str(card)
    assert "输入 0" in content
    assert "输出 0" in content
