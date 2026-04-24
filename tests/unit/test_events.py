import pytest

from hermes_feishu_card.events import EventValidationError, SidecarEvent


def valid_payload(event="thinking.delta", sequence=2):
    return {
        "schema_version": "1",
        "event": event,
        "conversation_id": "chat-1",
        "message_id": "msg-1",
        "chat_id": "oc_abc",
        "platform": "feishu",
        "sequence": sequence,
        "created_at": 1777017600.0,
        "data": {"text": "我在分析。"},
    }


def test_parses_valid_event():
    event = SidecarEvent.from_dict(valid_payload())
    assert event.event == "thinking.delta"
    assert event.sequence == 2


def test_rejects_unknown_event_name():
    with pytest.raises(EventValidationError, match="unknown event"):
        SidecarEvent.from_dict(valid_payload(event="bad.event"))


def test_rejects_missing_chat_id():
    payload = valid_payload()
    del payload["chat_id"]
    with pytest.raises(EventValidationError, match="chat_id"):
        SidecarEvent.from_dict(payload)


def test_rejects_non_feishu_platform():
    payload = valid_payload()
    payload["platform"] = "slack"
    with pytest.raises(EventValidationError, match="platform"):
        SidecarEvent.from_dict(payload)
