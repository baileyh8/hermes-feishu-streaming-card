from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.session import CardSession


def event(name, sequence, data):
    return SidecarEvent.from_dict(
        {
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
    )


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
