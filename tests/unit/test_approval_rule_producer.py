"""Native approval metadata is optional display data, never a new grant."""
import pytest

from hermes_feishu_card import hook_runtime as h


@pytest.fixture(autouse=True)
def isolated_runtime():
    h.reset_runtime_state()
    yield
    h.reset_runtime_state()


@pytest.mark.parametrize(
    "metadata,expected",
    [
        ({"pattern_keys": ["recursive delete", "recursive delete", "future native rule"]},
         ["recursive delete", "future native rule"]),
        ({"pattern_key": "recursive delete"}, ["recursive delete"]),
        ({"pattern_keys": [None, "", "x" * 121, "recursive delete"]},
         ["recursive delete"]),
        ({"pattern_keys": {"malformed": True}}, []),
    ],
)
def test_native_approval_keys_reach_event_without_changing_command_or_choices(
    monkeypatch, metadata, expected
):
    calls = []

    def request(values, **kwargs):
        calls.append((values, kwargs, h.build_interaction_event(values, **kwargs)))
        return {"status": "completed", "choice": "once"}

    monkeypatch.setattr(h, "request_interaction_from_hermes_locals", request)
    command = "echo harmless && rm -rf /tmp/hfc-demo END_OF_ORIGINAL_COMMAND"
    inputs = {"chat_id": "fixture-chat", "event_message_id": "fixture-message"}
    choice = h.request_approval_choice_from_hermes_locals(
        inputs,
        {"command": command, "description": "native description", "smart_denied": True,
         **metadata},
        interaction_id="fixture-approval",
    )
    values, kwargs, payload = calls[0]
    assert choice == "once"
    assert payload is not None
    assert payload["data"].get("approval_rule_keys", []) == expected
    assert payload["data"]["interaction_id"] == "fixture-approval"
    assert "END\\_OF\\_ORIGINAL\\_COMMAND" in kwargs["description"]
    assert [item["value"] for item in kwargs["options"]] == ["once", "deny"]
    assert kwargs["allow_custom_input"] is False
    assert inputs == {"chat_id": "fixture-chat", "event_message_id": "fixture-message"}


def test_rule_metadata_is_bounded_and_does_not_cross_into_clarify():
    values = {"chat_id": "fixture-chat", "event_message_id": "fixture-message",
              "_hfc_approval_rule_keys": [f"native-{i}" for i in range(40)]}
    approval = h.build_interaction_event(
        values, kind="approval", interaction_id="approval", prompt="approval"
    )
    clarify = h.build_interaction_event(
        values, kind="clarify", interaction_id="clarify", prompt="question"
    )
    assert approval is not None and clarify is not None
    assert approval["data"]["approval_rule_keys"] == [f"native-{i}" for i in range(8)]
    assert "approval_rule_keys" not in clarify["data"]


def test_optional_metadata_exception_does_not_lose_original_approval(monkeypatch):
    from hermes_feishu_card import approval_rule_hints

    def broken_metadata(_raw):
        raise ValueError("display metadata unavailable")

    monkeypatch.setattr(approval_rule_hints, "bounded_native_rule_keys", broken_metadata)
    payloads = []

    def request(values, **kwargs):
        payloads.append(h.build_interaction_event(values, **kwargs))
        return {"status": "completed", "choice": "deny"}

    monkeypatch.setattr(h, "request_interaction_from_hermes_locals", request)
    choice = h.request_approval_choice_from_hermes_locals(
        {"chat_id": "fixture-chat", "event_message_id": "fixture-message"},
        {"command": "original command", "pattern_key": "recursive delete"},
        interaction_id="original-approval",
    )
    assert choice == "deny"
    assert payloads[0]["data"]["interaction_id"] == "original-approval"
    assert "approval_rule_keys" not in payloads[0]["data"]


def test_previous_approval_keys_are_not_reused(monkeypatch):
    calls = []
    monkeypatch.setattr(
        h, "request_interaction_from_hermes_locals",
        lambda values, **kwargs: calls.append(h.build_interaction_event(values, **kwargs)),
    )
    h.request_approval_choice_from_hermes_locals(
        {"chat_id": "fixture-chat", "event_message_id": "new-message",
         "_hfc_approval_rule_keys": ["recursive delete"]},
        {"command": "different command"}, interaction_id="new-approval",
    )
    assert "approval_rule_keys" not in calls[0]["data"]
