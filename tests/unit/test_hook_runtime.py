import math

import pytest

from hermes_feishu_card import hook_runtime


@pytest.fixture(autouse=True)
def clear_hook_env(monkeypatch):
    for name in (
        "HERMES_FEISHU_CARD_ENABLED",
        "HERMES_FEISHU_CARD_EVENT_URL",
        "HERMES_FEISHU_CARD_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)
    hook_runtime.reset_runtime_state()


def test_load_runtime_config_defaults():
    config = hook_runtime.load_runtime_config()

    assert config.enabled is True
    assert config.event_url == "http://127.0.0.1:8765/events"
    assert config.timeout_seconds == 0.8


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "OFF"])
def test_load_runtime_config_disabled_values(monkeypatch, value):
    monkeypatch.setenv("HERMES_FEISHU_CARD_ENABLED", value)

    assert hook_runtime.load_runtime_config().enabled is False


def test_load_runtime_config_custom_url_and_timeout(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://localhost:9000/events")
    monkeypatch.setenv("HERMES_FEISHU_CARD_TIMEOUT_MS", "250")

    config = hook_runtime.load_runtime_config()

    assert config.event_url == "http://localhost:9000/events"
    assert config.timeout_seconds == 0.25


@pytest.mark.parametrize("value", ["1", "49", "5001", "abc"])
def test_load_runtime_config_invalid_timeout_falls_back(monkeypatch, value):
    monkeypatch.setenv("HERMES_FEISHU_CARD_TIMEOUT_MS", value)

    assert hook_runtime.load_runtime_config().timeout_seconds == 0.8


class MessageObject:
    def __init__(self):
        self.open_chat_id = "oc_object"
        self.message_id = "msg_object"
        self.text = "对象文本"


def test_build_event_extracts_direct_fields():
    payload = hook_runtime.build_event(
        "message.started",
        {
            "chat_id": "oc_direct",
            "message_id": "msg_direct",
            "conversation_id": "conv_direct",
        },
    )

    assert payload["event"] == "message.started"
    assert payload["chat_id"] == "oc_direct"
    assert payload["message_id"] == "msg_direct"
    assert payload["conversation_id"] == "conv_direct"
    assert payload["sequence"] == 0
    assert payload["platform"] == "feishu"
    assert payload["data"] == {}


def test_build_event_extracts_nested_message_object():
    payload = hook_runtime.build_event("answer.delta", {"message": MessageObject()})

    assert payload["chat_id"] == "oc_object"
    assert payload["message_id"] == "msg_object"
    assert payload["conversation_id"] == "oc_object"
    assert payload["data"] == {"text": "对象文本"}


def test_build_event_returns_none_when_chat_id_missing():
    assert hook_runtime.build_event("message.started", {"message_id": "msg"}) is None


def test_build_event_uses_stable_message_id_fallback_with_created_at():
    local_vars = {"chat_id": "oc_abc", "created_at": 1777017600.0}

    started = hook_runtime.build_event("message.started", local_vars)
    delta = hook_runtime.build_event(
        "answer.delta", {**local_vars, "created_at": 1777017601.0}
    )
    completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017602.0}
    )

    assert started["message_id"] == delta["message_id"] == completed["message_id"]
    assert started["message_id"].startswith("hfc_")


def test_build_event_reuses_active_fallback_for_duplicate_started_before_terminal():
    local_vars = {
        "chat_id": "oc_abc",
        "conversation_id": "conv_abc",
        "created_at": 1777017600.0,
    }

    first_started = hook_runtime.build_event("message.started", local_vars)
    second_started = hook_runtime.build_event("message.started", local_vars)

    assert first_started["message_id"] == second_started["message_id"]
    assert [first_started["sequence"], second_started["sequence"]] == [0, 1]


def test_build_event_separates_fallback_started_with_different_created_at():
    local_vars = {
        "chat_id": "oc_abc",
        "conversation_id": "conv_abc",
    }

    first_started = hook_runtime.build_event(
        "message.started", {**local_vars, "created_at": 1777017600.0}
    )
    second_started = hook_runtime.build_event(
        "message.started", {**local_vars, "created_at": 1777017601.0}
    )
    first_completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017600.0}
    )
    second_completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017601.0}
    )

    assert first_started["message_id"] != second_started["message_id"]
    assert first_completed["message_id"] == first_started["message_id"]
    assert second_completed["message_id"] == second_started["message_id"]
    assert [first_started["sequence"], first_completed["sequence"]] == [0, 1]
    assert [second_started["sequence"], second_completed["sequence"]] == [0, 1]


def test_build_event_separates_untokened_fallback_started_events():
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    first_started = hook_runtime.build_event("message.started", local_vars)
    second_started = hook_runtime.build_event("message.started", local_vars)

    assert first_started["message_id"] != second_started["message_id"]
    assert [first_started["sequence"], second_started["sequence"]] == [0, 0]


def test_build_event_ignores_ambiguous_unmatched_terminal_token():
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    first_started = hook_runtime.build_event(
        "message.started", {**local_vars, "created_at": 1777017600.0}
    )
    second_started = hook_runtime.build_event(
        "message.started", {**local_vars, "created_at": 1777017601.0}
    )
    ambiguous_completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017602.0}
    )
    second_completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017601.0}
    )
    first_completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017600.0}
    )

    assert ambiguous_completed is None
    assert second_completed["message_id"] == second_started["message_id"]
    assert first_completed["message_id"] == first_started["message_id"]


def test_build_event_rotates_fallback_after_terminal_with_same_created_at():
    local_vars = {
        "chat_id": "oc_abc",
        "conversation_id": "conv_abc",
        "created_at": 1777017600.0,
    }

    first_started = hook_runtime.build_event("message.started", local_vars)
    first_completed = hook_runtime.build_event("message.completed", local_vars)
    second_started = hook_runtime.build_event("message.started", local_vars)

    assert first_started["message_id"] == first_completed["message_id"]
    assert first_started["message_id"] != second_started["message_id"]
    assert second_started["sequence"] == 0


def test_build_event_uses_stable_fallback_without_created_at(monkeypatch):
    timestamps = iter([1777017600.0, 1777017601.0, 1777017602.0])
    monkeypatch.setattr(hook_runtime.time, "time", lambda: next(timestamps))
    local_vars = {"chat_id": "oc_abc"}

    started = hook_runtime.build_event("message.started", local_vars)
    delta = hook_runtime.build_event("answer.delta", local_vars)
    completed = hook_runtime.build_event("message.completed", local_vars)

    assert started["message_id"] == delta["message_id"] == completed["message_id"]
    assert started["message_id"].startswith("hfc_")
    assert [started["sequence"], delta["sequence"], completed["sequence"]] == [0, 1, 2]


def test_build_event_rotates_fallback_after_terminal_without_created_at(monkeypatch):
    timestamps = iter([1777017600.0, 1777017601.0, 1777017602.0])
    monkeypatch.setattr(hook_runtime.time, "time", lambda: next(timestamps))
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    first_started = hook_runtime.build_event("message.started", local_vars)
    first_completed = hook_runtime.build_event("message.completed", local_vars)
    second_started = hook_runtime.build_event("message.started", local_vars)

    assert first_started["message_id"] == first_completed["message_id"]
    assert first_started["message_id"] != second_started["message_id"]
    assert first_started["message_id"].startswith("hfc_")
    assert second_started["message_id"].startswith("hfc_")
    assert second_started["sequence"] == 0


def test_build_event_creates_active_fallback_when_delta_arrives_first(monkeypatch):
    timestamps = iter([1777017600.0, 1777017601.0])
    monkeypatch.setattr(hook_runtime.time, "time", lambda: next(timestamps))
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    delta = hook_runtime.build_event("answer.delta", local_vars)
    completed = hook_runtime.build_event("message.completed", local_vars)

    assert delta["message_id"] == completed["message_id"]
    assert delta["message_id"].startswith("hfc_")
    assert [delta["sequence"], completed["sequence"]] == [0, 1]


def test_build_event_treats_invalid_created_at_as_missing_for_fallback(monkeypatch):
    timestamps = iter([1777017600.0, 1777017601.0, 1777017602.0])
    monkeypatch.setattr(hook_runtime.time, "time", lambda: next(timestamps))
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    started = hook_runtime.build_event(
        "message.started", {**local_vars, "created_at": "abc"}
    )
    delta = hook_runtime.build_event(
        "answer.delta", {**local_vars, "created_at": float("nan")}
    )
    completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": float("inf")}
    )

    assert started["message_id"] == delta["message_id"] == completed["message_id"]
    assert all(
        math.isfinite(payload["created_at"]) for payload in (started, delta, completed)
    )


@pytest.mark.parametrize("terminal_event", ["message.completed", "message.failed"])
def test_build_event_explicit_terminal_uses_and_closes_active_fallback(terminal_event):
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    first_started = hook_runtime.build_event("message.started", local_vars)
    delta = hook_runtime.build_event("answer.delta", {**local_vars, "text": "hi"})
    explicit_terminal = hook_runtime.build_event(
        terminal_event, {**local_vars, "message_id": "msg_explicit"}
    )
    next_started = hook_runtime.build_event("message.started", local_vars)

    assert (
        first_started["message_id"]
        == delta["message_id"]
        == explicit_terminal["message_id"]
    )
    assert explicit_terminal["message_id"].startswith("hfc_")
    assert [first_started["sequence"], delta["sequence"], explicit_terminal["sequence"]] == [
        0,
        1,
        2,
    ]
    assert next_started["message_id"].startswith("hfc_")
    assert first_started["message_id"] != next_started["message_id"]
    assert next_started["sequence"] == 0


class ExplodingMessageObject:
    @property
    def open_chat_id(self):
        raise RuntimeError("proxy unavailable")

    @property
    def message_id(self):
        raise RuntimeError("proxy unavailable")

    @property
    def text(self):
        raise RuntimeError("proxy unavailable")


def test_build_event_skips_message_attributes_that_raise():
    payload = hook_runtime.build_event(
        "answer.delta",
        {
            "chat_id": "oc_direct",
            "conversation_id": "conv_direct",
            "message": ExplodingMessageObject(),
        },
    )

    assert payload["chat_id"] == "oc_direct"
    assert payload["conversation_id"] == "conv_direct"
    assert payload["message_id"].startswith("hfc_")
    assert payload["data"] == {"text": ""}


def test_reset_runtime_state_clears_fallback_cache(monkeypatch):
    monkeypatch.setattr(
        hook_runtime, "_hash_fallback_message_id", lambda *_args: "hfc_first"
    )
    first = hook_runtime.build_event("message.started", {"chat_id": "oc_abc"})

    hook_runtime.reset_runtime_state()
    monkeypatch.setattr(
        hook_runtime, "_hash_fallback_message_id", lambda *_args: "hfc_second"
    )
    second = hook_runtime.build_event("message.started", {"chat_id": "oc_abc"})

    assert first["message_id"] == "hfc_first"
    assert second["message_id"] == "hfc_second"
    assert second["sequence"] == 0


def test_build_event_increments_sequence_per_message():
    local_vars = {"chat_id": "oc_abc", "message_id": "msg_seq"}

    first = hook_runtime.build_event("message.started", local_vars)
    second = hook_runtime.build_event("answer.delta", {**local_vars, "text": "hi"})

    assert first["sequence"] == 0
    assert second["sequence"] == 1
