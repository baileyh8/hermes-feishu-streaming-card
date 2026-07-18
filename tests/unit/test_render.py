from hermes_feishu_card.render import _SPINNER_FRAMES, _colored_model_label, render_card
from hermes_feishu_card.session import CardSession, InteractionState, ToolState
from hermes_feishu_card.status import StatusConfig
import pytest
import time


def test_render_thinking_card_keeps_runtime_status_only_in_footer():
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
    assert "subtitle" not in card["header"]
    content = str(card)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    assert main["content"] == "正在分析。"
    assert "正在思考" not in content
    assert "思考中" not in str(card["header"])
    assert "生成中" in content
    assert "正在分析。" in content
    assert "思考与工具 · 1 次工具调用" in content


def test_render_card_accepts_custom_header_title():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "研发助手"


def test_v4_running_card_uses_preview_title_and_public_interim_body():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "我先检查天气客户端。"
    session.latest_tool_preview = "正在读取：weather_client.py"

    card = render_card(session, title="Hermes Agent")
    main = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "main_content"
    )
    footer = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "footer"
    )

    assert card["header"]["title"]["content"] == "Hermes Agent"
    assert card["header"]["subtitle"]["content"] == "正在读取：weather_client.py"
    assert not any(
        element.get("element_id") == "runtime_summary"
        for element in card["body"]["elements"]
    )
    assert main["content"] == "我先检查天气客户端。"
    assert "gpt-" not in footer["content"]
    assert "ctx " not in footer["content"]


def test_compaction_phase_replaces_header_title_and_hides_stale_tool_summary():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "已保留的公开阶段说明"
    session.latest_tool_preview = "正在读取：weather_client.py"
    session.runtime_phase_text = "正在压缩上下文"

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "正在压缩上下文"
    assert "subtitle" not in card["header"]
    assert "正在读取：weather_client.py" not in str(card["header"])


def test_pending_interaction_has_priority_over_compaction_phase():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.runtime_phase_text = "正在压缩上下文"
    session.active_interaction = InteractionState(
        interaction_id="approval-compaction",
        kind="approval",
        prompt="允许继续执行吗？",
    )

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "允许继续执行吗？"
    assert "正在压缩上下文" not in str(card["header"])


def test_tool_activity_clears_compaction_and_restores_tool_subtitle():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.runtime_phase_text = "正在压缩上下文"
    assert session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="c",
            message_id="m",
            chat_id="oc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "tool_id": "tool-1",
                "name": "terminal",
                "status": "running",
                "detail": "pytest",
            },
        )
    )

    card = render_card(session, title="研发助手")

    assert session.runtime_phase_text == ""
    assert card["header"]["title"]["content"] == "研发助手"
    assert card["header"]["subtitle"]["content"] == "正在执行终端：pytest"


def test_completed_card_never_renders_stale_compaction_phase():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "最终答案"
    session.runtime_phase_text = "正在压缩上下文"

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "研发助手"
    assert "正在压缩上下文" not in str(card)


def test_v4_answer_delta_remains_primary_over_public_interim_text():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "公开阶段说明"
    session.answer_text = "主回答已经开始"

    card = render_card(session)
    main = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "main_content"
    )

    assert main["content"] == "主回答已经开始"
    assert "公开阶段说明" not in str(card)


def test_v4_waiting_prompt_moves_to_header_without_body_duplication():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = InteractionState(
        interaction_id="approval-1",
        kind="approval",
        prompt="允许覆盖文件吗？",
        description="目标文件：report.html",
        options=[],
    )

    card = render_card(session)
    footer = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "footer"
    )

    assert card["header"]["title"]["content"] == "允许覆盖文件吗？"
    assert "subtitle" not in card["header"]
    assert str(card).count("允许覆盖文件吗？") == 1
    assert "目标文件：report.html" in str(card)
    assert "等待" in footer["content"]
    assert "ctx " not in footer["content"]


def test_v4_completed_restores_configured_title_and_metrics():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.latest_tool_preview = "正在执行终端：pytest"
    session.status = "completed"
    session.answer_text = "最终答案"
    session.duration = 2.0
    session.model = "gpt-5.5"

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "研发助手"
    assert "正在执行终端：pytest" not in str(card["header"])
    assert "2s" in str(card)
    assert "gpt-5.5" in str(card)


def test_v4_completed_reply_card_uses_only_native_feishu_quote_header():
    session = CardSession(conversation_id="c", message_id="om_user", chat_id="oc")
    session.status = "completed"
    session.answer_text = "最终答案"
    session.reply_to_message_id = "om_user"

    card = render_card(session, title="Hermes Agent")

    assert "header" not in card
    assert "最终答案" in str(card)
    assert card["config"]["summary"]["content"] == "已完成"
    footer = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "footer"
    )
    assert footer["content"].startswith("已完成 · ")


def test_v4_failed_retains_preview_and_status_only_footer():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.latest_tool_preview = "正在读取：演示天气数据"
    session.status = "failed"
    session.answer_text = "数据源暂时不可用。"

    card = render_card(session, title="Hermes Agent")
    footer = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "footer"
    )

    assert card["header"]["title"]["content"] == "Hermes Agent"
    assert card["header"]["subtitle"]["content"] == "正在读取：演示天气数据"
    assert footer["content"] == "已停止"
    assert "ctx " not in footer["content"]


def test_v4_missing_preview_keeps_configured_title():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.thinking_text = "正在处理。"

    card = render_card(session, title="研发助手")

    assert card["header"]["title"]["content"] == "研发助手"


@pytest.mark.parametrize(
    "preview",
    [
        "```bash\nexport APP_SECRET=unsafe\n```",
        "curl https://example.test?a=1&token=unsafe",
        "deploy --password unsafe --api-key=unsafe",
    ],
)
def test_v4_runtime_header_is_single_line_bounded_and_redacted(preview):
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.latest_tool_preview = preview + ("x" * 300)

    title = render_card(session)["header"]["subtitle"]["content"]

    assert "\n" not in title
    assert "unsafe" not in title
    assert "[REDACTED]" in title
    assert len(title) <= 120


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


def test_v3818_normal_completed_card_keeps_element_order_and_configured_footer():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.status = "completed"
    session.answer_text = "最终答案"
    session.duration = 3.0
    session.model = "MiniMax M2.7"
    session.tokens = {"output_tokens": 34}

    card = render_card(session, footer_fields=["model", "duration", "output_tokens"])

    assert [element["element_id"] for element in card["body"]["elements"]] == [
        "main_content",
        "main_divider",
        "tool_summary",
        "footer",
    ]
    assert card["body"]["elements"][-1] == {
        "tag": "markdown",
        "element_id": "footer",
        "content": "MiniMax M2.7 · 3s · ↓34",
        "text_size": "x-small",
    }


def test_v3818_normal_failed_card_keeps_element_order_and_footer():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.status = "failed"
    session.answer_text = "处理失败"

    card = render_card(session, footer_fields=["model", "duration"])

    assert [element["element_id"] for element in card["body"]["elements"]] == [
        "main_content",
        "main_divider",
        "tool_summary",
        "footer",
    ]
    assert card["body"]["elements"][-1]["content"] == "已停止"


@pytest.mark.parametrize(
    ("model", "color"),
    [
        ("GPT-5.5", "blue"),
        ("claude-opus-4", "orange"),
        ("DeepSeek-V4", "indigo"),
        ("KIMI-K2", "purple"),
        ("GLM-5", "green"),
        ("Tencent/hunyuan", "teal"),
    ],
)
def test_model_footer_uses_sanitized_semantic_color(model, color):
    assert _colored_model_label(model) == f'<font color="{color}">{model}</font>'


def test_model_footer_escapes_unknown_and_malicious_model_names():
    assert _colored_model_label("MiniMax M2.7") == "MiniMax M2.7"
    escaped = _colored_model_label('<script>alert("x")</script>')
    assert escaped == "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"
    assert "<script>" not in escaped


def test_model_footer_color_preserves_layout_order_and_configured_fields():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.status = "completed"
    session.answer_text = "最终答案"
    session.duration = 3.0
    session.model = "gpt-5.5"
    session.tokens = {"output_tokens": 34}

    card = render_card(session, footer_fields=["model", "duration", "output_tokens"])

    assert [element["element_id"] for element in card["body"]["elements"]] == [
        "main_content",
        "main_divider",
        "tool_summary",
        "footer",
    ]
    assert card["body"]["elements"][-1] == {
        "tag": "markdown",
        "element_id": "footer",
        "content": '<font color="blue">gpt-5.5</font> · 3s · ↓34',
        "text_size": "x-small",
    }


def test_model_footer_color_does_not_change_non_completed_or_empty_fields():
    thinking = CardSession(conversation_id="c", message_id="m1", chat_id="oc")
    thinking.model = "gpt-5.5"
    assert render_card(thinking)["body"]["elements"][-1]["content"].endswith("生成中")

    failed = CardSession(conversation_id="c", message_id="m2", chat_id="oc")
    failed.status = "failed"
    failed.model = "gpt-5.5"
    assert render_card(failed)["body"]["elements"][-1]["content"] == "已停止"

    completed = CardSession(conversation_id="c", message_id="m3", chat_id="oc")
    completed.status = "completed"
    completed.model = "gpt-5.5"
    assert render_card(completed, footer_fields=[])["body"]["elements"][-1]["content"] == "0s"


def test_progress_handoff_changes_only_header_status_from_completed_card():
    answer = "数据收集中，数据到位后我会继续生成报告。"
    completed = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    completed.status = "completed"
    completed.display_status = "completed"
    completed.answer_text = answer
    inferred = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    inferred.status = "completed"
    inferred.answer_text = answer

    completed_card = render_card(completed)
    inferred_card = render_card(inferred)

    assert completed_card["header"]["template"] == "green"
    assert completed_card["header"]["subtitle"]["content"] == "已完成"
    assert inferred_card["header"]["template"] == "blue"
    assert "subtitle" not in inferred_card["header"]
    assert inferred_card["config"]["summary"]["content"] == "生成中"
    assert inferred_card["body"] == completed_card["body"]


def test_render_status_uses_custom_conservative_marker_pairs():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.status = "completed"
    session.answer_text = "queued now; resume later"
    config = StatusConfig(active_markers=("queued",), future_markers=("resume later",))

    card = render_card(session, status_config=config)

    assert card["header"]["template"] == "blue"
    assert card["config"]["summary"]["content"] == "生成中"


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


def test_render_card_default_json_has_no_style_or_explicit_body_text_size():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"

    card = render_card(session)
    main = next(
        item for item in card["body"]["elements"] if item["element_id"] == "main_content"
    )
    footer = next(
        item for item in card["body"]["elements"] if item["element_id"] == "footer"
    )

    assert "style" not in card["config"]
    assert "text_size" not in main
    assert footer["text_size"] == "x-small"


def test_render_scalar_text_sizes_apply_to_each_role_and_body_chunks():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    base = {
        "schema_version": "1",
        "conversation_id": "c",
        "message_id": "m",
        "chat_id": "oc",
        "platform": "feishu",
        "created_at": 0.0,
    }
    session.apply(
        SidecarEvent(
            event="answer.delta",
            sequence=1,
            data={"text": "先分析实现。"},
            **base,
        )
    )
    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=2,
            data={
                "tool_id": "terminal",
                "name": "terminal",
                "status": "completed",
                "detail": "pytest",
            },
            **base,
        )
    )
    session.apply(
        SidecarEvent(
            event="system.notice",
            sequence=3,
            data={"title": "提示", "content": "已切换上下文"},
            **base,
        )
    )
    session.apply(
        SidecarEvent(
            event="message.completed",
            sequence=4,
            data={"answer": "甲" * 2600},
            **base,
        )
    )
    session.attachments = [{"name": "report.txt"}]

    card = render_card(
        session,
        timeline_expanded=True,
        text_sizes={
            "body": "large",
            "reasoning": "medium",
            "tool": "small",
            "notice": "notation",
            "footer": "normal",
        },
    )

    main = [
        item
        for item in card["body"]["elements"]
        if str(item.get("element_id", "")).startswith("main_content")
    ]
    timeline = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    reasoning = next(item for item in timeline["elements"] if "思考" in item["content"])
    tool = next(item for item in timeline["elements"] if "terminal" in item["content"])
    notice = next(item for item in timeline["elements"] if "提示" in item["content"])
    attachment = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "attachment_summary"
    )
    footer = next(
        item for item in card["body"]["elements"] if item.get("element_id") == "footer"
    )

    assert len(main) > 1
    assert all(item["text_size"] == "large" for item in main)
    assert reasoning["text_size"] == "medium"
    assert tool["text_size"] == "small"
    assert notice["text_size"] == "notation"
    assert footer["text_size"] == "normal"
    assert "text_size" not in attachment


def test_render_independent_notice_uses_notice_text_size():
    session = CardSession(conversation_id="c", message_id="n", chat_id="oc")
    session.delivery_kind = "notice"
    session.notice_title = "通知"
    session.answer_text = "通知正文"
    session.status = "completed"

    card = render_card(session, text_sizes={"body": "large", "notice": "small"})
    main = next(
        item for item in card["body"]["elements"] if item["element_id"] == "main_content"
    )

    assert main["text_size"] == "small"


def test_render_device_text_size_emits_footer_alias():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"

    card = render_card(
        session,
        text_sizes={
            "footer": {
                "default": "x-small",
                "pc": "x-small",
                "mobile": "notation",
            }
        },
    )
    footer = next(
        item for item in card["body"]["elements"] if item["element_id"] == "footer"
    )

    assert card["config"]["style"]["text_size"] == {
        "hfc_footer": {
            "default": "x-small",
            "pc": "x-small",
            "mobile": "notation",
        }
    }
    assert footer["text_size"] == "hfc_footer"


def test_render_text_size_aliases_use_deterministic_role_order():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"
    mapping = {"default": "normal", "pc": "large", "mobile": "small"}

    card = render_card(
        session,
        text_sizes={
            "footer": mapping,
            "body": mapping,
            "reasoning": mapping,
        },
    )

    assert list(card["config"]["style"]["text_size"]) == [
        "hfc_body",
        "hfc_footer",
    ]
    assert "hfc_reasoning" not in card["config"]["style"]["text_size"]


def test_render_scalar_only_text_sizes_do_not_emit_style_aliases():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.status = "completed"
    session.answer_text = "正文"

    card = render_card(session, text_sizes={"body": "large", "footer": "small"})

    assert "style" not in card["config"]


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


def test_render_timeline_limits_reasoning_without_truncating_answer():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "思考" * 200},
        )
    )
    sequence = 2
    for index in range(6):
        session.apply(
            SidecarEvent(
                schema_version="1",
                event="tool.updated",
                conversation_id="chat-1",
                message_id="msg-1",
                chat_id="oc_abc",
                platform="feishu",
                sequence=sequence,
                created_at=0.0,
                data={
                    "tool_id": f"tool-{index}",
                    "name": "search",
                    "status": "completed",
                    "detail": "结果" * 200,
                },
            )
        )
        sequence += 1
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=sequence,
            created_at=0.0,
            data={"text": "最终回答完整保留" * 20},
        )
    )

    card = render_card(
        session,
        max_reasoning_chars=80,
        max_tool_result_chars=80,
        max_timeline_items=3,
    )

    content = str(card)
    assert "最终回答完整保留" in content
    assert "思考内容过长，已截断" in content
    assert "工具详情过长，已截断" in content
    assert "内容已折叠" not in content
    timeline = next(
        item
        for item in card["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    timeline_content = "".join(item["content"] for item in timeline["elements"])
    assert "已折叠" in timeline_content
    assert len(timeline_content) < 1000


def test_render_failed_card_shows_error_without_thinking():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "不会展示"
    session.answer_text = "处理出错"
    session.status = "failed"
    card = render_card(session)
    content = str(card)
    assert card["config"]["summary"]["content"] == "处理失败"
    assert card["header"]["template"] == "red"
    assert "subtitle" not in card["header"]
    assert card["config"]["summary"]["content"] == "处理失败"
    assert "处理出错" in content
    assert "不会展示" not in content
    assert "已停止" in content


def test_render_card_filters_think_tags_at_render_boundary():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "<think>hidden</think>可见内容"
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


def test_render_completed_card_footer_adds_configured_subscription_usage_only():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.answer_text = "最终答案"
    session.status = "completed"
    session.duration = 3
    session.subscription_usage = "5h 26% · weekly 89%"

    configured = render_card(
        session, footer_fields=["duration", "subscription_usage"]
    )
    default = render_card(session, footer_fields=["duration"])

    assert configured["body"]["elements"][-1]["content"] == (
        "3s · 5h 26% · weekly 89%"
    )
    assert default["body"]["elements"][-1]["content"] == "3s"


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


def test_render_answer_stays_primary_over_public_interim_text():
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

    assert main["content"] == "这是主回答。"
    assert "auxiliary_timeline" not in str(card)
    assert "先分析约束。" not in str(card)


def test_render_in_progress_answer_status_is_generating():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "正在生成主回答。"},
        )
    )

    card = render_card(session)

    assert "subtitle" not in card["header"]
    assert card["config"]["summary"]["content"] == "生成中"
    assert "生成中" in str(card)


def test_render_public_interim_text_in_main_content_before_answer():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.thinking_text = "这是公开的阶段性说明。"

    card = render_card(session)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")

    assert main["content"] == "这是公开的阶段性说明。"
    assert "正在思考" not in str(card)
    assert "这是公开的阶段性说明。" in str(card)


def test_render_keeps_pre_tool_answer_in_main_while_tool_runs():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "好的，我先做分析再动手。"},
        )
    )

    card = render_card(session, timeline_expanded=True)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    assert main["content"] == "好的，我先做分析再动手。"
    assert "auxiliary_timeline" not in str(card)

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
            data={
                "tool_id": "terminal",
                "name": "terminal",
                "status": "running",
                "detail": "gh release view",
            },
        )
    )

    card = render_card(session, timeline_expanded=True)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")

    assert main["content"] == "好的，我先做分析再动手。"
    assert "好的，我先做分析再动手。" not in str(timeline)
    assert "terminal" in str(timeline)


def test_render_timeline_styles_reasoning_and_tools_with_compact_hierarchy():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    base = {
        "schema_version": "1",
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "created_at": 0.0,
    }
    session.apply(SidecarEvent(event="answer.delta", sequence=1, data={"text": "先确认 release。"}, **base))
    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=2,
            data={
                "tool_id": "terminal",
                "name": "terminal",
                "status": "completed",
                "detail": "gh release list --limit 3",
            },
            **base,
        )
    )
    session.apply(SidecarEvent(event="message.completed", sequence=3, data={"answer": "最终总结。"}, **base))

    timeline = next(
        item
        for item in render_card(session, timeline_expanded=True)["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    reasoning = next(item for item in timeline["elements"] if "思考 1" in item["content"])
    tool = next(item for item in timeline["elements"] if "terminal" in item["content"])

    assert reasoning["text_size"] == "small"
    assert tool["text_size"] == "x-small"
    assert tool["content"].startswith("> ")
    assert "gh release list" in tool["content"]


def test_render_timeline_styles_system_notices_as_compact_status_lines():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    base = {
        "schema_version": "1",
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "created_at": 0.0,
    }
    session.apply(
        SidecarEvent(
            event="system.notice",
            sequence=1,
            data={
                "title": "上下文窗口提示",
                "content": "Codex gpt-5.5 caps context at 272K.",
                "notice_id": "context-cap",
            },
            **base,
        )
    )

    timeline = next(
        item
        for item in render_card(session, timeline_expanded=True)["body"]["elements"]
        if item.get("element_id") == "auxiliary_timeline"
    )
    notice = next(item for item in timeline["elements"] if "上下文窗口提示" in item["content"])

    assert notice["text_size"] == "x-small"
    assert notice["content"].startswith("> ")
    assert "Codex gpt-5.5 caps context" in notice["content"]


def test_render_independent_notice_card_uses_notice_title_and_status():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="notice-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="system.notice",
            conversation_id="chat-1",
            message_id="notice-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={
                "title": "技能加载",
                "content": "Reading skill hermes-agent",
                "notice_scope": "independent",
                "level": "info",
            },
        )
    )

    card = render_card(session)

    assert card["header"]["title"]["content"] == "技能加载"
    assert card["header"]["template"] == "blue"
    assert card["header"]["subtitle"]["content"] == "已完成"
    assert "Reading skill hermes-agent" in str(card)
    assert "生成中" not in str(card)


def test_render_rotates_each_preface_from_main_to_timeline():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    base = {
        "schema_version": "1",
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "created_at": 0.0,
    }

    session.apply(SidecarEvent(event="answer.delta", sequence=1, data={"text": "第一段验证计划。"}, **base))
    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=2,
            data={"tool_id": "release", "name": "terminal", "status": "completed"},
            **base,
        )
    )
    session.apply(SidecarEvent(event="answer.delta", sequence=3, data={"text": "第二段补充检查。"}, **base))

    card = render_card(session, timeline_expanded=True)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    assert main["content"] == "第二段补充检查。"
    assert "第一段验证计划。" in str(timeline)
    assert "第二段补充检查。" not in str(timeline)

    session.apply(
        SidecarEvent(
            event="tool.updated",
            sequence=4,
            data={"tool_id": "readme", "name": "read_file", "status": "completed"},
            **base,
        )
    )
    card = render_card(session, timeline_expanded=True)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    assert main["content"] == "第二段补充检查。"
    assert "第二段补充检查。" not in str(timeline)
    assert "read_file" in str(timeline)

    session.apply(
        SidecarEvent(
            event="message.completed",
            sequence=5,
            data={"answer": "最终总结。"},
            **base,
        )
    )

    card = render_card(session, timeline_expanded=True)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    assert main["content"] == "最终总结。"
    assert "第一段验证计划。" in str(timeline)
    assert "第二段补充检查。" in str(timeline)
    assert "思考 1" in str(timeline)
    assert "思考 2" in str(timeline)


def test_render_timeline_keeps_latest_reasoning_when_tool_burst_fills_recent_items():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "关键思考内容应该保留在展开区。"},
        )
    )
    for index in range(8):
        session.apply(
            SidecarEvent(
                schema_version="1",
                event="tool.updated",
                conversation_id="chat-1",
                message_id="msg-1",
                chat_id="oc_abc",
                platform="feishu",
                sequence=index + 2,
                created_at=0.0,
                data={
                    "tool_id": f"tool-{index}",
                    "name": f"tool_{index}",
                    "status": "completed",
                },
            )
        )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="message.completed",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=20,
            created_at=0.0,
            data={"answer": "最终回答。"},
        )
    )

    card = render_card(session, max_timeline_items=4, timeline_expanded=True)
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    timeline_content = str(timeline)

    assert "关键思考内容应该保留在展开区。" in timeline_content
    assert "tool_7" in timeline_content
    assert "tool_0" not in timeline_content


def test_render_expanded_timeline_splits_without_global_truncation():
    from hermes_feishu_card.events import SidecarEvent

    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    marker = "TIMELINE_TAIL_MARKER"
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="answer.delta",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"text": "思考" * 180 + marker},
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
            data={"tool_id": "tool-1", "name": "search", "status": "completed"},
        )
    )
    session.apply(
        SidecarEvent(
            schema_version="1",
            event="message.completed",
            conversation_id="chat-1",
            message_id="msg-1",
            chat_id="oc_abc",
            platform="feishu",
            sequence=3,
            created_at=0.0,
            data={"answer": "最终回答。"},
        )
    )

    card = render_card(session, timeline_expanded=True, max_reasoning_chars=1000)
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    timeline_content = "".join(item["content"] for item in timeline["elements"])

    assert marker in timeline_content
    assert "内容已折叠" not in timeline_content


def test_render_omits_redundant_tool_summary_when_timeline_is_visible():
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
            data={"text": "先分析。"},
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
            data={"tool_id": "tool-1", "name": "search", "status": "completed"},
        )
    )

    card = render_card(session)
    element_ids = [item.get("element_id") for item in card["body"]["elements"]]

    assert "auxiliary_timeline" in element_ids
    assert "tool_summary" not in element_ids
    assert "思考与工具 · 1 次工具调用" in str(card)


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
    assert "已折叠 4 条早期思考/工具记录" in content
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


def test_render_timeline_shows_compact_tool_arguments_duration_and_error():
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
                "name": "terminal",
                "status": "failed",
                "arguments": {"command": "gh issue list", "token": "secret-token"},
                "duration_ms": 250,
                "error": "exit 1",
            },
        )
    )

    card = render_card(session)
    timeline = next(
        item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline"
    )
    content = str(timeline)

    assert "参数:" in content
    assert "gh issue list" in content
    assert "耗时: 250ms" in content
    assert "失败: exit 1" in content
    assert "secret-token" not in content


def test_render_timeline_redacts_sensitive_tool_detail_in_json_and_repr():
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
                "name": "timeline-json",
                "status": "completed",
                "detail": '{"chat_id":"oc_secret","tenant_access_token":"token-123"}',
            },
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
            data={
                "tool_id": "tool-2",
                "name": "timeline-repr",
                "status": "completed",
                "detail": "{'app_secret': 'super-secret', 'open_id': 'ou_secret'}",
            },
        )
    )

    card = render_card(session)
    timeline = next(item for item in card["body"]["elements"] if item.get("element_id") == "auxiliary_timeline")
    content = str(timeline)

    assert "oc_secret" not in content
    assert "token-123" not in content
    assert "super-secret" not in content
    assert "ou_secret" not in content
    assert "[REDACTED]" in content


def test_render_thinking_without_answer_uses_public_interim_main_content():
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
            data={"text": "这是公开的阶段性输出。"},
        )
    )

    card = render_card(session)
    main = next(item for item in card["body"]["elements"] if item.get("element_id") == "main_content")

    assert main["content"] == "这是公开的阶段性输出。"
    assert "正在思考" not in str(card)
    assert "这是公开的阶段性输出。" in str(card)
    assert "auxiliary_timeline" not in str(card)


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
