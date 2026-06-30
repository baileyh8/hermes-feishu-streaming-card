from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession, ToolState
import time


def test_render_thinking_card_has_two_state_label_and_tools():
    from hermes_feishu_card.events import SidecarEvent
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "正在分析。"
    event = SidecarEvent(
        schema_version="1", event="tool.updated",
        conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc",
        platform="feishu", sequence=0, created_at=0.0,
        data={"tool_id": "t1", "name": "search", "status": "running"},
    )
    session.apply(event)
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


def test_render_pending_interaction_as_buttons():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="interaction.requested",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "interaction_id": "approval-1",
                "kind": "approval",
                "prompt": "允许执行命令吗？",
                "description": "rm -rf /tmp/demo",
                "options": [
                    {"label": "允许一次", "value": "once", "style": "primary"},
                    {"label": "拒绝", "value": "deny", "style": "danger"},
                ],
            },
        )
    )

    card = render_card(session)

    buttons = [
        element
        for element in card["body"]["elements"]
        if element.get("tag") == "button"
    ]
    assert [item["text"]["content"] for item in buttons] == ["允许一次", "拒绝"]
    assert buttons[0]["behaviors"][0]["type"] == "callback"
    assert buttons[0]["behaviors"][0]["value"]["interaction_id"] == "approval-1"
    assert buttons[0]["behaviors"][0]["value"]["choice"] == "once"
    assert buttons[0]["behaviors"][0]["value"]["token"]
    assert "interaction_actions" not in str(card)
    assert "rm -rf /tmp/demo" in str(card)


def test_render_pending_interaction_as_text_choices_for_localhost_mode():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="interaction.requested",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "interaction_id": "clarify-1",
                "kind": "clarify",
                "prompt": "请选择处理方式",
                "options": [
                    {"label": "删除空文件", "value": "delete"},
                    {"label": "保留并补索引", "value": "keep"},
                ],
            },
        )
    )

    card = render_card(session, interaction_mode="text")

    content = str(card)
    assert not any(element.get("tag") == "button" for element in card["body"]["elements"])
    assert "1. 删除空文件" in content
    assert "2. 保留并补索引" in content
    assert "Reply with the number" in content


def test_render_completed_interaction_replaces_buttons_with_choice():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="interaction.requested",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "interaction_id": "approval-1",
                "kind": "approval",
                "prompt": "允许执行命令吗？",
                "options": [{"label": "允许一次", "value": "once"}],
            },
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="interaction.completed",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=2,
            created_at=0.0,
            data={
                "interaction_id": "approval-1",
                "choice": "once",
                "choice_label": "允许一次",
                "user_name": "Bailey",
            },
        )
    )

    card = render_card(session)

    assert not any(element.get("tag") == "button" for element in card["body"]["elements"])
    assert "已选择：允许一次" in str(card)
    assert "Bailey" in str(card)


def test_render_completed_card_shows_attachment_summary():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"
    session.attachments = [{"kind": "file", "name": "report.pdf", "summary": "report.pdf"}]

    card = render_card(session)

    assert "附件：report.pdf" in str(card)


def test_render_completed_card_places_attachment_summary_before_tools():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"
    session.attachments = [{"kind": "file", "name": "report.pdf", "summary": "report.pdf"}]

    card = render_card(session)

    element_ids = [element.get("element_id") for element in card["body"]["elements"]]
    assert element_ids.index("attachment_summary") < element_ids.index("tool_summary")


def test_render_completed_card_shows_at_most_eight_attachments():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"
    session.attachments = [
        {"kind": "file", "name": f"file-{index}.txt", "summary": f"file-{index}.txt"}
        for index in range(10)
    ]

    card = render_card(session)

    attachment_element = next(
        element
        for element in card["body"]["elements"]
        if element.get("element_id") == "attachment_summary"
    )
    assert "file-7.txt" in attachment_element["content"]
    assert "file-8.txt" not in attachment_element["content"]
    assert "file-9.txt" not in attachment_element["content"]


def test_render_completed_card_without_attachments_has_no_attachment_summary():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"

    card = render_card(session)

    element_ids = [element.get("element_id") for element in card["body"]["elements"]]
    assert "attachment_summary" not in element_ids


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


def test_render_long_table_chunks_keep_markdown_table_shape():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    rows = "\n".join(f"| {index} | {'甲' * 80} |" for index in range(80))
    session.answer_text = f"| id | content |\n| --- | --- |\n{rows}\n"
    session.status = "completed"

    card = render_card(session)

    main_elements = [
        item
        for item in card["body"]["elements"]
        if str(item.get("element_id", "")).startswith("main_content")
    ]
    assert len(main_elements) > 1
    assert all(len(item["content"]) <= 2400 for item in main_elements)
    assert all("| id | content |" in item["content"] for item in main_elements)
    assert all("| --- | --- |" in item["content"] for item in main_elements)


def test_render_long_code_block_chunks_remain_fenced():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    code = "\n".join(f"print({index!r})  # {'x' * 80}" for index in range(90))
    session.answer_text = f"```python\n{code}\n```"
    session.status = "completed"

    card = render_card(session)

    main_elements = [
        item
        for item in card["body"]["elements"]
        if str(item.get("element_id", "")).startswith("main_content")
    ]
    assert len(main_elements) > 1
    assert all(len(item["content"]) <= 2400 for item in main_elements)
    assert all(item["content"].startswith("```python\n") for item in main_elements)
    assert all(item["content"].rstrip().endswith("```") for item in main_elements)


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


def test_spinner_text_changes_over_time():
    from hermes_feishu_card.render import _spinner_text
    frames = set()
    for _ in range(20):
        frames.add(_spinner_text("生成中"))
        time.sleep(0.05)
    assert len(frames) >= 2  # 至少两个不同帧


def test_spinner_text_contains_label():
    from hermes_feishu_card.render import _spinner_text
    assert "处理中" in _spinner_text("处理中")


def test_footer_shows_spinner_not_static_for_thinking():
    from hermes_feishu_card.session import CardSession
    from hermes_feishu_card.render import _render_footer
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.status = "thinking"
    footer = _render_footer(session)
    assert footer != "生成中"  # 不再是静态文本
    assert "生成中" in footer  # label 仍然包含


def test_footer_still_static_for_failed():
    from hermes_feishu_card.session import CardSession
    from hermes_feishu_card.render import _render_footer
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.status = "failed"
    assert _render_footer(session) == "已停止"


def test_render_card_truncates_tables_over_limit():
    from hermes_feishu_card.session import CardSession
    from hermes_feishu_card.render import render_card
    session = CardSession(conversation_id="c", message_id="m", chat_id="c")
    session.answer_text = "\n\n".join(
        [f"Table {i}\n| col |\n| --- |\n| {i} |" for i in range(7)]
    )
    session.status = "completed"
    card = render_card(session)
    body_text = "".join(
        el.get("content", "") for el in card["body"]["elements"]
        if el.get("tag") == "markdown"
    )
    assert "超出部分已省略" in body_text


def test_render_answer_stays_primary_and_reasoning_moves_to_timeline():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="thinking.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "先分析约束。"},
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=2,
            created_at=0.0,
            data={"text": "这是主回答。"},
        )
    )

    card = render_card(session)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")

    assert main["content"] == "这是主回答。"
    assert timeline["tag"] == "collapsible_panel"
    assert timeline["expanded"] is False
    assert "先分析约束。" in str(timeline)


def test_render_timeline_folds_old_entries_before_answer():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    for index in range(8):
        session.apply(
            SidecarEvent(
                schema_version="1",
                event="thinking.delta",
                conversation_id="chat-1",
                message_id="msg-1",
                chat_id="oc_abc",
                platform="feishu",
                sequence=index * 2 + 1,
                created_at=0.0,
                data={"text": f"思考{index}"},
            )
        )
        session.apply(
            SidecarEvent(
                schema_version="1",
                event="tool.updated",
                conversation_id="chat-1",
                message_id="msg-1",
                chat_id="oc_abc",
                platform="feishu",
                sequence=index * 2 + 2,
                created_at=0.0,
                data={"tool_id": f"tool-{index}", "name": f"tool_{index}", "status": "completed"},
            )
        )
    session.answer_text = "最终回答不能被折叠"

    card = render_card(session, max_timeline_items=4)
    content = str(card)

    assert "最终回答不能被折叠" in content
    assert "已折叠 12 条早期思考/工具记录" in content
    assert "tool_7" in content
    assert "tool_0" not in content


def test_render_timeline_redacts_sensitive_tool_detail():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "tool_id": "tool-1",
                "name": "lark_send",
                "status": "completed",
                "detail": (
                    "FEISHU_APP_SECRET=abc123 "
                    "token=secret-token "
                    "chat_id=oc_secret"
                ),
            },
        )
    )

    card = render_card(session)
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    content = str(timeline)

    assert "FEISHU_APP_SECRET=abc123" not in content
    assert "token=secret-token" not in content
    assert "chat_id=oc_secret" not in content
    assert "lark_send" in content


def test_render_thinking_without_answer_uses_placeholder_in_main_content():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="thinking.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "这是推理文本，只该在 timeline。"},
        )
    )

    card = render_card(session)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")

    assert "正在思考" in main["content"]
    assert "这是推理文本，只该在 timeline。" not in main["content"]
    assert "这是推理文本，只该在 timeline。" in str(timeline)


def test_render_tool_summary_keeps_tool_names_when_reasoning_hidden():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="thinking.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "隐藏的思考"},
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=2,
            created_at=0.0,
            data={"tool_id": "tool-1", "name": "search", "status": "running"},
        )
    )

    card = render_card(session, show_reasoning=False)
    tool_summary = next(item for item in card["body"]["elements"] if item.get("element_id") == "tool_summary")

    assert "工具调用 1 次" in tool_summary["content"]
    assert "`search`: running" in tool_summary["content"]
    assert "auxiliary_timeline" not in str(card)


def test_render_can_hide_reasoning_timeline_when_configured():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="thinking.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "隐藏的思考"},
        )
    )
    session.answer_text = "主回答"

    card = render_card(session, show_reasoning=False)

    content = str(card)
    assert "主回答" in content
    assert "隐藏的思考" not in content
    assert "auxiliary_timeline" not in content
