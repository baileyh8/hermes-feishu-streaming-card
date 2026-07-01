from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.session import CardSession


def event(name, sequence, data, **overrides):
    payload = {
        "schema_version": "1",
        "event": name,
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "sequence": sequence,
        "created_at": 1777017600.0 + sequence,
        "data": data,
    }
    payload.update(overrides)
    return SidecarEvent.from_dict(payload)


def test_thinking_accumulates_and_strips_tags():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("thinking.delta", 1, {"text": "<think>先分析"}))
    assert session.apply(event("thinking.delta", 2, {"text": "</think>结束。"}))
    assert session.thinking_text == "先分析结束。"


def test_thinking_append_block_preserves_complete_interim_messages():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(event("thinking.delta", 1, {"text": "我先来讲今天的 AI。", "mode": "append_block"}))
    assert session.apply(event("thinking.delta", 2, {"text": "接着看第二个现象。", "mode": "append_block"}))

    assert session.thinking_text == "我先来讲今天的 AI。\n\n接着看第二个现象。"


def test_rejects_duplicate_and_stale_sequence():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("thinking.delta", 2, {"text": "新"}))
    assert not session.apply(event("thinking.delta", 2, {"text": "重复"}))
    assert not session.apply(event("thinking.delta", 1, {"text": "旧"}))
    assert session.thinking_text == "新"


def test_terminal_completion_applies_even_when_streaming_delta_sequence_arrived_ahead():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("answer.delta", 100, {"text": "部分答案"}))

    assert session.apply(event("message.completed", 90, {"answer": "最终答案"}))

    assert session.status == "completed"
    assert session.visible_main_text == "最终答案"
    assert session.last_sequence == 100


def test_blank_completion_preserves_streamed_answer_delta():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("answer.delta", 10, {"text": "DeepSeek 已生成答案"}))

    assert session.apply(event("message.completed", 11, {"answer": "   "}))

    assert session.status == "completed"
    assert session.visible_main_text == "DeepSeek 已生成答案"


def test_tool_updates_count_all_events():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(event("tool.updated", 1, {"tool_id": "t1", "name": "search", "status": "running"}))
    session.apply(event("tool.updated", 2, {"tool_id": "t1", "name": "search", "status": "completed"}))
    session.apply(event("tool.updated", 3, {"tool_id": "t2", "name": "fetch", "status": "completed"}))
    assert session.tool_count == 3  # 3 actual tool calls (1 unique: t1 called twice, t2 once)
    assert len(session.tools) == 2  # tools dict still deduplicates
    assert session.tools["t1"].status == "completed"


def test_completion_replaces_thinking_with_answer():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(event("thinking.delta", 1, {"text": "思考内容。"}))
    session.apply(
        event(
            "message.completed",
            2,
            {"answer": "最终答案", "tokens": {"input_tokens": 10}, "duration": 3.5},
        )
    )
    assert session.status == "completed"
    assert session.visible_main_text == "最终答案"


def test_completion_stores_model_and_context_footer_metadata():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(
        event(
            "message.completed",
            1,
            {
                "answer": "最终答案",
                "model": "MiniMax M2.7",
                "context": {"used_tokens": 182_000, "max_tokens": 204_000},
            },
        )
    )

    assert session.model == "MiniMax M2.7"
    assert session.context == {"used_tokens": 182_000, "max_tokens": 204_000}


def test_session_stores_attachment_and_delivery_metadata():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(
        event(
            "message.started",
            0,
            {"delivery_kind": "chat", "reply_to_message_id": "om_parent"},
        )
    )
    assert session.apply(
        event(
            "message.completed",
            1,
            {
                "answer": "最终答案",
                "attachments": [
                    {"kind": "file", "name": "report.pdf", "summary": "report.pdf"},
                    {"kind": "image", "summary": "missing-name.png"},
                    "bad",
                ],
            },
        )
    )

    assert session.delivery_kind == "chat"
    assert session.reply_to_message_id == "om_parent"
    assert session.attachments == [
        {"kind": "file", "name": "report.pdf", "summary": "report.pdf"}
    ]
    assert session.visible_main_text == "最终答案"


def test_session_tracks_pending_and_completed_interaction():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(
        event(
            "interaction.requested",
            1,
            {
                "interaction_id": "approval-1",
                "kind": "approval",
                "prompt": "允许执行命令吗？",
                "description": "rm -rf /tmp/demo",
                "options": [
                    {"label": "允许一次", "value": "once"},
                    {"label": "拒绝", "value": "deny", "style": "danger"},
                ],
            },
        )
    )

    assert session.active_interaction is not None
    assert session.active_interaction.interaction_id == "approval-1"
    assert session.active_interaction.status == "pending"
    assert session.active_interaction.options[0].value == "once"

    assert session.apply(
        event(
            "interaction.completed",
            2,
            {
                "interaction_id": "approval-1",
                "choice": "once",
                "choice_label": "允许一次",
                "user_name": "Bailey",
            },
        )
    )

    assert session.active_interaction is not None
    assert session.active_interaction.status == "completed"
    assert session.active_interaction.choice == "once"
    assert session.active_interaction.choice_label == "允许一次"
    assert session.active_interaction.user_name == "Bailey"


def test_answer_delta_takes_over_visible_text_before_completion():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("thinking.delta", 1, {"text": "思考内容。"}))
    assert session.visible_main_text == "思考内容。"

    assert session.apply(event("answer.delta", 2, {"text": "答案开始"}))

    assert session.status == "thinking"
    assert session.visible_main_text == "答案开始"


def test_split_think_tags_do_not_leak_across_chunks():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("thinking.delta", 1, {"text": "<thi"}))
    assert session.apply(event("tool.updated", 2, {"tool_id": "t1", "name": "search", "status": "running"}))
    assert session.apply(event("thinking.delta", 3, {"text": "nk>先分析</thi"}))
    assert session.apply(event("thinking.delta", 4, {"text": "nk>结束"}))
    assert session.thinking_text == "先分析结束"
    reasoning_entries = [item for item in session.timeline.snapshot() if item.kind == "reasoning"]
    assert reasoning_entries == []
    assert session.timeline.snapshot()[0].kind == "tool"

    assert session.apply(event("answer.delta", 5, {"text": "<thi"}))
    assert session.apply(event("answer.delta", 6, {"text": "nk>答案</thi"}))
    assert session.apply(event("answer.delta", 7, {"text": "nk>完成"}))
    assert session.answer_text == "答案完成"


def test_completed_state_rejects_later_mutations():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("thinking.delta", 1, {"text": "思考"}))
    assert session.apply(event("tool.updated", 2, {"tool_id": "t1", "name": "search", "status": "running"}))
    assert session.apply(event("message.completed", 3, {"answer": "最终答案"}))

    assert not session.apply(event("thinking.delta", 4, {"text": "更多"}))
    assert not session.apply(event("tool.updated", 5, {"tool_id": "t2", "name": "fetch", "status": "completed"}))
    assert not session.apply(event("message.completed", 6, {"answer": "覆盖答案"}))

    assert session.status == "completed"
    assert session.visible_main_text == "最终答案"
    assert session.thinking_text == "思考"
    assert session.tool_count == 1


def test_rejects_mismatched_conversation_id_and_chat_id():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert not session.apply(event("thinking.delta", 1, {"text": "错会话"}, conversation_id="chat-2"))
    assert not session.apply(event("thinking.delta", 2, {"text": "错群"}, chat_id="oc_other"))
    assert session.thinking_text == ""
    assert session.last_sequence == -1


def test_missing_or_empty_tool_id_does_not_create_tool():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("tool.updated", 1, {"name": "search", "status": "running"}))
    assert session.apply(event("tool.updated", 2, {"tool_id": "", "name": "fetch", "status": "completed"}))
    assert session.tool_count == 0
    assert session.last_sequence == 2


def test_tool_metadata_uses_defaults_for_non_strings():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("tool.updated", 1, {"tool_id": "t1", "name": None, "status": None, "detail": None}))
    assert session.tools["t1"].name == "t1"
    assert session.tools["t1"].status == "running"
    assert session.tools["t1"].detail == ""


def test_completion_bad_metadata_uses_safe_defaults():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("message.completed", 1, {"answer": "最终答案", "tokens": None, "duration": "abc"}))
    assert session.tokens == {}
    assert session.duration == 0.0


def test_failed_visible_main_text_shows_error():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    assert session.apply(event("thinking.delta", 1, {"text": "旧思考"}))
    assert session.apply(event("message.failed", 2, {"error": "失败原因"}))
    assert session.status == "failed"
    assert session.visible_main_text == "失败原因"


def test_tool_count_increments_for_same_tool_id():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    for i in range(3):
        e = event("tool.updated", i, {"tool_id": "web_search", "name": "web_search", "status": "running"})
        session.apply(e)
    assert session.tool_count == 3  # 实际调用次数
    assert len(session.tools) == 1  # tools 字典仍去重


def test_timeline_preserves_repeated_completed_tool_calls_with_same_id():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    for index in range(3):
        assert session.apply(
            event(
                "tool.updated",
                index,
                {
                    "tool_id": "execute_code",
                    "name": "execute_code",
                    "status": "completed",
                    "detail": f"第 {index + 1} 次执行",
                },
            )
        )

    entries = [item for item in session.timeline.snapshot() if item.kind == "tool"]

    assert session.tool_count == 3
    assert len(entries) == 3
    assert [item.detail for item in entries] == ["第 1 次执行", "第 2 次执行", "第 3 次执行"]


def test_timeline_updates_running_tool_until_terminal_status():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(
        event(
            "tool.updated",
            1,
            {
                "tool_id": "read",
                "name": "read_file",
                "status": "running",
                "detail": "开始读取",
            },
        )
    )
    assert session.apply(
        event(
            "tool.updated",
            2,
            {
                "tool_id": "read",
                "name": "read_file",
                "status": "completed",
                "detail": "读取完成",
            },
        )
    )

    entries = [item for item in session.timeline.snapshot() if item.kind == "tool"]

    assert len(entries) == 1
    assert entries[0].status == "completed"
    assert entries[0].detail == "读取完成"


def test_session_keeps_current_answer_visible_until_next_answer_block():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(event("answer.delta", 1, {"text": "好的，我先做分析再动手。"}))
    assert session.answer_text == "好的，我先做分析再动手。"

    assert session.apply(
        event(
            "tool.updated",
            2,
            {
                "tool_id": "terminal",
                "name": "terminal",
                "status": "running",
                "detail": "gh release view",
            },
        )
    )

    entries = session.timeline.snapshot()

    assert session.answer_text == "好的，我先做分析再动手。"
    assert [(item.kind, item.title, item.status) for item in entries] == [
        ("tool", "terminal", "running"),
    ]

    assert session.apply(event("answer.delta", 3, {"text": "我再补一轮验证。"}))
    assert session.answer_text == "我再补一轮验证。"
    entries = session.timeline.snapshot()
    assert [(item.kind, item.title, item.status) for item in entries] == [
        ("reasoning", "思考 1", "completed"),
        ("tool", "terminal", "running"),
    ]
    assert entries[0].content == "好的，我先做分析再动手。"

    assert session.apply(
        event(
            "tool.updated",
            4,
            {
                "tool_id": "readme",
                "name": "read_file",
                "status": "completed",
                "detail": "README.md",
            },
        )
    )

    entries = session.timeline.snapshot()
    assert session.answer_text == "我再补一轮验证。"
    assert [(item.kind, item.title, item.status) for item in entries] == [
        ("reasoning", "思考 1", "completed"),
        ("tool", "terminal", "running"),
        ("tool", "read_file", "completed"),
    ]

    assert session.apply(event("message.completed", 5, {"answer": "最终答案。"}))
    entries = session.timeline.snapshot()
    assert session.answer_text == "最终答案。"
    assert [(item.kind, item.title, item.status) for item in entries] == [
        ("reasoning", "思考 1", "completed"),
        ("tool", "terminal", "running"),
        ("reasoning", "思考 2", "completed"),
        ("tool", "read_file", "completed"),
    ]
    assert entries[2].content == "我再补一轮验证。"


def test_tool_count_increments_for_different_tool_ids():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    for i, tid in enumerate(["a", "b", "c"]):
        e = event("tool.updated", i, {"tool_id": tid, "name": tid, "status": "running"})
        session.apply(e)
    assert session.tool_count == 3
    assert len(session.tools) == 3


def test_session_timeline_records_pre_tool_preface_tool_answer_order():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(event("answer.delta", 1, {"text": "先看约束。"}))
    assert session.apply(event("tool.updated", 2, {"tool_id": "read", "name": "read_file", "status": "running", "detail": "README.md"}))
    assert session.apply(event("tool.updated", 3, {"tool_id": "read", "name": "read_file", "status": "completed", "detail": "README.md"}))
    assert session.apply(event("answer.delta", 4, {"text": "最终回答开始"}))

    entries = session.timeline.snapshot()
    assert [(item.kind, item.title, item.status) for item in entries] == [
        ("reasoning", "思考 1", "completed"),
        ("tool", "read_file", "completed"),
    ]
    assert entries[0].content == "先看约束。"
    assert entries[1].detail == "README.md"
    assert session.answer_text == "最终回答开始"


def test_session_archives_last_preface_on_completion_when_final_answer_arrives():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(event("answer.delta", 1, {"text": "先验证版本。"}))
    assert session.apply(event("tool.updated", 2, {"tool_id": "gh", "name": "terminal", "status": "completed"}))
    assert session.apply(event("answer.delta", 3, {"text": "还差 README，我继续查。"}))

    assert session.apply(event("message.completed", 4, {"answer": "最终答案：V3.8.1 变化如下。"}))

    entries = session.timeline.snapshot()
    assert session.status == "completed"
    assert session.answer_text == "最终答案：V3.8.1 变化如下。"
    assert [(item.kind, item.title, item.status) for item in entries] == [
        ("reasoning", "思考 1", "completed"),
        ("tool", "terminal", "completed"),
        ("reasoning", "思考 2", "completed"),
    ]
    assert entries[0].content == "先验证版本。"
    assert entries[2].content == "还差 README，我继续查。"


def test_session_strips_archived_preface_prefix_from_completed_answer():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(event("tool.updated", 1, {"tool_id": "gh", "name": "terminal", "status": "completed"}))
    assert session.apply(event("answer.delta", 2, {"text": "3. 补查结果\nREADME 和 diff 都查完了。"}))

    assert session.apply(
        event(
            "message.completed",
            3,
            {
                "answer": (
                    "3. 补查结果\nREADME 和 diff 都查完了。\n\n"
                    "---\n\n"
                    "最终总结：v3.7.0 到 v3.8.1 主要变化如下。"
                )
            },
        )
    )

    entries = session.timeline.snapshot()
    assert session.answer_text == "最终总结：v3.7.0 到 v3.8.1 主要变化如下。"
    assert entries[-1].kind == "reasoning"
    assert entries[-1].content == "3. 补查结果\nREADME 和 diff 都查完了。"


def test_session_raw_thinking_blocks_do_not_enter_timeline():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(event("thinking.delta", 1, {"text": "第一句", "mode": "append_block"}))
    assert session.apply(event("thinking.delta", 2, {"text": "第二句", "mode": "append_block"}))

    entries = session.timeline.snapshot()
    assert entries == []
    assert session.thinking_text == "第一句\n\n第二句"


def test_session_raw_thinking_replace_mode_stays_internal():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")

    assert session.apply(event("thinking.delta", 1, {"text": "我先看"}))
    assert session.apply(event("thinking.delta", 2, {"text": "我先看看今天的变更", "mode": "replace"}))

    entries = session.timeline.snapshot()
    assert entries == []
    assert session.thinking_text == "我先看看今天的变更"


def test_session_timeline_folded_count_reports_hidden_old_entries():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    for index in range(5):
        assert session.apply(event("tool.updated", index, {"tool_id": f"tool-{index}", "name": f"tool_{index}", "status": "completed"}))

    assert session.timeline.folded_count(max_items=3) == 2
    assert [item.title for item in session.timeline.snapshot(max_items=3)] == ["tool_2", "tool_3", "tool_4"]
