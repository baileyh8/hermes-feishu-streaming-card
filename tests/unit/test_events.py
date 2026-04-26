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


@pytest.mark.parametrize("event", [[], "", {}, "   "])
def test_rejects_invalid_event_name_type(event):
    with pytest.raises(EventValidationError, match="event"):
        SidecarEvent.from_dict(valid_payload(event=event))


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


@pytest.mark.parametrize("sequence", [True, -1, "2"])
def test_rejects_invalid_sequence(sequence):
    payload = valid_payload(sequence=sequence)
    with pytest.raises(EventValidationError, match="sequence"):
        SidecarEvent.from_dict(payload)


def test_rejects_invalid_created_at():
    payload = valid_payload()
    payload["created_at"] = "abc"
    with pytest.raises(EventValidationError, match="created_at"):
        SidecarEvent.from_dict(payload)


@pytest.mark.parametrize("created_at", [float("nan"), float("inf"), float("-inf")])
def test_rejects_non_finite_created_at(created_at):
    payload = valid_payload()
    payload["created_at"] = created_at
    with pytest.raises(EventValidationError, match="created_at"):
        SidecarEvent.from_dict(payload)


def test_rejects_non_object_data():
    payload = valid_payload()
    payload["data"] = "not-an-object"
    with pytest.raises(EventValidationError, match="data"):
        SidecarEvent.from_dict(payload)


def test_rejects_non_object_payload():
    with pytest.raises(EventValidationError, match="payload must be an object"):
        SidecarEvent.from_dict("not-an-object")


@pytest.mark.parametrize("field", ["conversation_id", "message_id", "chat_id"])
@pytest.mark.parametrize("value", [None, "", "   ", 123])
def test_rejects_invalid_id_fields(field, value):
    payload = valid_payload()
    payload[field] = value
    with pytest.raises(EventValidationError, match=field):
        SidecarEvent.from_dict(payload)


def test_allows_extra_fields():
    payload = valid_payload()
    payload["extra"] = "ignored"
    event = SidecarEvent.from_dict(payload)
    assert event.event == "thinking.delta"
