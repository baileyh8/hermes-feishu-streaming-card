import asyncio
import json
import math
import threading
from urllib import error

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


class SourceObject:
    platform = "feishu"
    chat_id = "oc_source"
    thread_id = "thread_source"


class GatewayEventObject:
    def __init__(self, message_id: str):
        self.message_id = message_id


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


def test_build_event_extracts_gateway_source_object():
    payload = hook_runtime.build_event(
        "message.started",
        {
            "source": SourceObject(),
            "session_id": "session_source",
        },
    )

    assert payload["event"] == "message.started"
    assert payload["chat_id"] == "oc_source"
    assert payload["conversation_id"] == "session_source"
    assert payload["message_id"].startswith("hfc_")


def test_build_event_uses_gateway_event_message_id_for_card_lifecycle():
    first = {
        "source": SourceObject(),
        "event": GatewayEventObject("om_first"),
        "session_id": "session_source",
    }
    second = {
        "source": SourceObject(),
        "event": GatewayEventObject("om_second"),
        "session_id": "session_source",
    }

    first_started = hook_runtime.build_event("message.started", first)
    first_completed = hook_runtime.build_event(
        "message.completed", {**first, "answer": "first answer"}
    )
    second_started = hook_runtime.build_event("message.started", second)
    second_completed = hook_runtime.build_event(
        "message.completed", {**second, "answer": "second answer"}
    )

    assert first_started["message_id"] == "om_first"
    assert first_completed["message_id"] == "om_first"
    assert second_started["message_id"] == "om_second"
    assert second_completed["message_id"] == "om_second"


def test_build_event_explicit_started_keeps_active_fallback_identity():
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    fallback_started = hook_runtime.build_event("message.started", local_vars)
    explicit_started = hook_runtime.build_event(
        "message.started", {**local_vars, "message_id": "msg_real"}
    )
    explicit_delta = hook_runtime.build_event(
        "answer.delta", {**local_vars, "message_id": "msg_real", "text": "hi"}
    )
    explicit_completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "message_id": "msg_real"}
    )

    assert fallback_started["message_id"].startswith("hfc_")
    assert explicit_started["message_id"] == fallback_started["message_id"]
    assert explicit_delta["message_id"] == fallback_started["message_id"]
    assert explicit_completed["message_id"] == fallback_started["message_id"]
    assert [
        fallback_started["sequence"],
        explicit_started["sequence"],
        explicit_delta["sequence"],
        explicit_completed["sequence"],
    ] == [
        0,
        1,
        2,
        3,
    ]


def test_build_event_extracts_nested_message_object():
    payload = hook_runtime.build_event("answer.delta", {"message": MessageObject()})

    assert payload["chat_id"] == "oc_object"
    assert payload["message_id"] == "msg_object"
    assert payload["conversation_id"] == "oc_object"
    assert payload["data"] == {"text": "对象文本"}


def test_build_completed_event_preserves_duration_and_tokens():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_abc",
            "message_id": "msg_1",
            "answer": "最终答案",
            "duration": 2.75,
            "model": "MiniMax M2.7",
            "tokens": {"input_tokens": 12, "output_tokens": 34},
            "context": {"used_tokens": 182_000, "max_tokens": 204_000},
        },
    )

    assert payload["data"] == {
        "answer": "最终答案",
        "duration": 2.75,
        "model": "MiniMax M2.7",
        "tokens": {"input_tokens": 12, "output_tokens": 34},
        "context": {"used_tokens": 182_000, "max_tokens": 204_000},
    }


def test_build_completed_event_uses_agent_result_token_fallbacks():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_abc",
            "message_id": "msg_1",
            "answer": "中文答案",
            "response_time": 1.25,
            "tokens": {"input_tokens": 0, "output_tokens": 0},
            "agent_result": {"last_prompt_tokens": 99},
        },
    )

    assert payload["data"]["duration"] == 1.25
    assert payload["data"]["tokens"]["input_tokens"] == 99
    assert payload["data"]["tokens"]["output_tokens"] > 0


def test_build_completed_event_sanitizes_cumulative_token_counts():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_abc",
            "message_id": "msg_1",
            "answer": "我来为您撰写",
            "tokens": {"input_tokens": 279_000, "output_tokens": 17_300},
            "agent_result": {"last_prompt_tokens": 35_400},
        },
    )

    assert payload["data"]["tokens"] == {
        "input_tokens": 35_400,
        "output_tokens": 6,
    }


def test_build_event_returns_none_when_chat_id_missing():
    assert hook_runtime.build_event("message.started", {"message_id": "msg"}) is None


def test_build_event_uses_stable_message_id_fallback_with_created_at():
    local_vars = {"chat_id": "oc_abc", "created_at": 1777017600.0}

    started = hook_runtime.build_event("message.started", local_vars)
    delta = hook_runtime.build_event(
        "answer.delta", {**local_vars, "created_at": 1777017601.0}
    )
    completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017600.0}
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


def test_build_event_ignores_ambiguous_untokened_terminal():
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    first_started = hook_runtime.build_event(
        "message.started", {**local_vars, "created_at": 1777017600.0}
    )
    second_started = hook_runtime.build_event(
        "message.started", {**local_vars, "created_at": 1777017601.0}
    )
    ambiguous_completed = hook_runtime.build_event("message.completed", local_vars)
    first_completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017600.0}
    )
    second_completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017601.0}
    )

    assert ambiguous_completed is None
    assert first_completed["message_id"] == first_started["message_id"]
    assert second_completed["message_id"] == second_started["message_id"]


def test_build_event_ignores_unmatched_terminal_token_with_single_active_fallback():
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    started = hook_runtime.build_event(
        "message.started", {**local_vars, "created_at": 1777017600.0}
    )
    mismatched_completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017601.0}
    )
    matched_completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017600.0}
    )

    assert mismatched_completed is None
    assert matched_completed["message_id"] == started["message_id"]


def test_build_event_ignores_explicit_terminal_with_unmatched_token():
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    started = hook_runtime.build_event(
        "message.started", {**local_vars, "created_at": 1777017600.0}
    )
    explicit_terminal = hook_runtime.build_event(
        "message.completed",
        {**local_vars, "message_id": "msg_explicit", "created_at": 1777017601.0},
    )
    delta = hook_runtime.build_event(
        "answer.delta", {**local_vars, "created_at": 1777017600.0, "text": "still active"}
    )
    completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "created_at": 1777017600.0}
    )

    assert explicit_terminal is None
    assert delta["message_id"] == started["message_id"]
    assert completed["message_id"] == started["message_id"]


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
def test_build_event_explicit_terminal_closes_active_fallback(terminal_event):
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    first_started = hook_runtime.build_event("message.started", local_vars)
    delta = hook_runtime.build_event("answer.delta", {**local_vars, "text": "hi"})
    explicit_terminal = hook_runtime.build_event(
        terminal_event, {**local_vars, "message_id": "msg_explicit"}
    )
    next_started = hook_runtime.build_event("message.started", local_vars)

    assert first_started["message_id"] == delta["message_id"]
    assert explicit_terminal["message_id"] == first_started["message_id"]
    assert [first_started["sequence"], delta["sequence"], explicit_terminal["sequence"]] == [
        0,
        1,
        2,
    ]
    assert next_started["message_id"].startswith("hfc_")
    assert first_started["message_id"] != next_started["message_id"]
    assert next_started["sequence"] == 0


def test_build_event_explicit_delta_uses_active_fallback_state():
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    first_started = hook_runtime.build_event("message.started", local_vars)
    explicit_delta = hook_runtime.build_event(
        "answer.delta", {**local_vars, "message_id": "msg_explicit", "text": "hi"}
    )
    completed = hook_runtime.build_event("message.completed", local_vars)

    assert first_started["message_id"] == explicit_delta["message_id"]
    assert completed["message_id"] == first_started["message_id"]
    assert [first_started["sequence"], explicit_delta["sequence"], completed["sequence"]] == [
        0,
        1,
        2,
    ]


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


class SenderProbe:
    def __init__(self):
        self.payloads = []
        self.raise_error = False

    async def __call__(self, url, payload, timeout):
        self.payloads.append((url, payload, timeout))
        if self.raise_error:
            raise RuntimeError("network failed")


async def drain_tasks():
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_schedules_sender(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    result = hook_runtime.emit_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.started",
    )
    await drain_tasks()

    assert result is True
    assert len(sender.payloads) == 1
    url, payload, timeout = sender.payloads[0]
    assert url == "http://sidecar.test/events"
    assert payload["event"] == "message.started"
    assert payload["message_id"] == "msg_1"
    assert timeout == 0.8


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_threadsafe_schedules_on_running_loop(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    result = hook_runtime.emit_from_hermes_locals_threadsafe(
        {"chat_id": "oc_abc", "message_id": "msg_1", "text": "hello"},
        event_name="answer.delta",
    )
    await drain_tasks()

    assert result is True
    assert len(sender.payloads) == 1
    url, payload, timeout = sender.payloads[0]
    assert url == "http://sidecar.test/events"
    assert payload["event"] == "answer.delta"
    assert payload["message_id"] == "msg_1"
    assert payload["data"] == {"text": "hello"}
    assert timeout == 0.8


def test_emit_from_hermes_locals_threadsafe_uses_explicit_loop_from_sync_call(
    monkeypatch,
):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run_loop():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run_loop)
    thread.start()
    ready.wait(timeout=1)
    try:
        result = hook_runtime.emit_from_hermes_locals_threadsafe(
            {
                "_hfc_loop": loop,
                "chat_id": "oc_abc",
                "message_id": "msg_1",
                "tool_id": "tool_1",
                "name": "search",
                "status": "completed",
                "detail": "done",
            },
            event_name="tool.updated",
        )
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(timeout=1)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1)
        loop.close()

    assert result is True
    assert len(sender.payloads) == 1
    _url, payload, _timeout = sender.payloads[0]
    assert payload["event"] == "tool.updated"
    assert payload["data"] == {
        "tool_id": "tool_1",
        "name": "search",
        "status": "completed",
        "detail": "done",
    }


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_threadsafe_missing_chat_id_does_not_send(
    monkeypatch,
):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)

    result = hook_runtime.emit_from_hermes_locals_threadsafe(
        {"message_id": "msg_1"},
        event_name="message.started",
    )
    await drain_tasks()

    assert result is False
    assert sender.payloads == []


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_threadsafe_sender_error_is_swallowed(
    monkeypatch,
):
    sender = SenderProbe()
    sender.raise_error = True
    monkeypatch.setattr(hook_runtime, "_post_json", sender)

    result = hook_runtime.emit_from_hermes_locals_threadsafe(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.started",
    )
    await drain_tasks()

    assert result is True
    assert len(sender.payloads) == 1


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_disabled_does_not_send(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_ENABLED", "0")

    result = hook_runtime.emit_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.started",
    )
    await drain_tasks()

    assert result is False
    assert sender.payloads == []


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_build_event_none_does_not_send(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)

    result = hook_runtime.emit_from_hermes_locals(
        {"message_id": "msg_1"},
        event_name="message.started",
    )
    await drain_tasks()

    assert result is False
    assert sender.payloads == []


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_sender_error_is_swallowed(monkeypatch):
    sender = SenderProbe()
    sender.raise_error = True
    monkeypatch.setattr(hook_runtime, "_post_json", sender)

    result = hook_runtime.emit_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.started",
    )
    await drain_tasks()

    assert result is True
    assert len(sender.payloads) == 1


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_async_reports_sender_success(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    result = await hook_runtime.emit_from_hermes_locals_async(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.completed",
    )

    assert result is True
    assert len(sender.payloads) == 1
    url, payload, timeout = sender.payloads[0]
    assert url == "http://sidecar.test/events"
    assert payload["event"] == "message.completed"
    assert payload["message_id"] == "msg_1"
    assert timeout == hook_runtime.TERMINAL_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_async_reports_sender_failure(monkeypatch):
    sender = SenderProbe()
    sender.raise_error = True
    monkeypatch.setattr(hook_runtime, "_post_json", sender)

    result = await hook_runtime.emit_from_hermes_locals_async(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.completed",
    )

    assert result is False
    assert len(sender.payloads) == 1


def test_emit_from_hermes_locals_without_running_loop_fails_open(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)

    result = hook_runtime.emit_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.started",
    )

    assert result is False
    assert sender.payloads == []


@pytest.mark.asyncio
async def test_post_json_constructs_json_post_and_timeout(monkeypatch):
    opened = {}

    def fake_open_request(req, timeout):
        opened["url"] = req.full_url
        opened["method"] = req.get_method()
        opened["headers"] = dict(req.header_items())
        opened["body"] = req.data
        opened["timeout"] = timeout

    monkeypatch.setattr(hook_runtime, "_open_request", fake_open_request)

    await hook_runtime._post_json(
        "http://sidecar.test/events",
        {"event": "message.started", "data": {"text": "对象文本"}},
        0.25,
    )

    assert opened["url"] == "http://sidecar.test/events"
    assert opened["method"] == "POST"
    assert opened["headers"]["Content-type"] == "application/json"
    assert json.loads(opened["body"].decode("utf-8")) == {
        "event": "message.started",
        "data": {"text": "对象文本"},
    }
    assert opened["timeout"] == 0.25


@pytest.mark.asyncio
async def test_post_json_propagates_http_errors_from_open_request(monkeypatch):
    def fake_open_request(_req, _timeout):
        raise error.HTTPError("http://sidecar.test/events", 500, "boom", {}, None)

    monkeypatch.setattr(hook_runtime, "_open_request", fake_open_request)

    with pytest.raises(error.HTTPError):
        await hook_runtime._post_json(
            "http://sidecar.test/events",
            {"event": "message.started"},
            0.8,
        )
