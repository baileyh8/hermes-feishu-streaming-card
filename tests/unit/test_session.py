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


def test_tool_updates_count_unique_events():
    session = CardSession(conversation_id="chat-1", message_id="msg-1", chat_id="oc_abc")
    session.apply(event("tool.updated", 1, {"tool_id": "t1", "name": "search", "status": "running"}))
    session.apply(event("tool.updated", 2, {"tool_id": "t1", "name": "search", "status": "completed"}))
    session.apply(event("tool.updated", 3, {"tool_id": "t2", "name": "fetch", "status": "completed"}))
    assert session.tool_count == 2
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
    assert session.apply(event("thinking.delta", 2, {"text": "nk>先分析</thi"}))
    assert session.apply(event("thinking.delta", 3, {"text": "nk>结束"}))
    assert session.thinking_text == "先分析结束"

    assert session.apply(event("answer.delta", 4, {"text": "<thi"}))
    assert session.apply(event("answer.delta", 5, {"text": "nk>答案</thi"}))
    assert session.apply(event("answer.delta", 6, {"text": "nk>完成"}))
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
