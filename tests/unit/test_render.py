from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession, ToolState


def test_render_thinking_card_has_two_state_label_and_tools():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "正在分析。"
    session.tools["t1"] = ToolState(tool_id="t1", name="search", status="running")
    card = render_card(session)
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "Hermes Agent"
    assert card["header"]["subtitle"]["content"] == "思考中"
    content = str(card)
    assert "正在分析。" in content
    assert "工具调用 1 次" in content


def test_render_card_accepts_custom_header_title():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "研发助手"


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


def test_render_long_main_content_splits_markdown_elements_without_truncating():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "甲" * 2600 + "乙" * 2600
    session.status = "completed"

    card = render_card(session)

    main_elements = [
        item
        for item in card["body"]["elements"]
        if str(item.get("element_id", "")).startswith("main_content")
    ]
    assert len(main_elements) == 3
    assert all(len(item["content"]) <= 2400 for item in main_elements)
    assert "".join(item["content"] for item in main_elements) == session.answer_text


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
    assert "0s" in content
    assert "Unknown" in content
    assert "↑0" in content
    assert "↓0" in content
    assert "ctx 0/0 0%" in content


def test_render_completed_card_handles_missing_token_stats():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.tokens = None
    card = render_card(session)
    content = str(card)
    assert "↑0" in content
    assert "↓0" in content


def test_render_completed_card_footer_uses_compact_metrics_format():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.duration = 92
    session.model = "MiniMax M2.7"
    session.tokens = {"input_tokens": 1_100_000, "output_tokens": 2_200}
    session.context = {"used_tokens": 182_000, "max_tokens": 204_000}

    card = render_card(session)

    assert "1m32s · MiniMax M2.7 · ↑1.1m · ↓2.2k · ctx 182k/204k 89%" in str(card)


def test_render_completed_card_footer_respects_configured_fields_and_order():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.duration = 92
    session.model = "MiniMax M2.7"
    session.tokens = {"input_tokens": 1_100_000, "output_tokens": 2_200}
    session.context = {"used_tokens": 182_000, "max_tokens": 204_000}

    card = render_card(session, footer_fields=["model", "duration", "context"])

    content = str(card)
    assert "MiniMax M2.7 · 1m32s · ctx 182k/204k 89%" in content
    assert "↑1.1m" not in content
    assert "↓2.2k" not in content
