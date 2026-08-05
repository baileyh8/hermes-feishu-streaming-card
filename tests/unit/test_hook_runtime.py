import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import inspect
import math
import re
import sys
import threading
import time
import types
from types import SimpleNamespace
from urllib import error

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.event_auth import EventProofVerifier, PolicyProofVerifier


_NATIVE_ACK_PLAN_RE = re.compile(r"invalid post payload", re.IGNORECASE)
_NATIVE_ACK_PLAN_CONSTANT = ("text", "post", 5)


def _operation_token(operation_id="operation-1"):
    payload = json.dumps(
        {
            "operation_id": operation_id,
            "action": "repair",
            "report_fingerprint": "report-1",
            "expires_at": 9999999999,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=") + ".signature"


@pytest.fixture(autouse=True)
def clear_hook_env(monkeypatch):
    for name in (
        "HERMES_FEISHU_CARD_ENABLED",
        "HERMES_FEISHU_CARD_EVENT_URL",
        "HERMES_FEISHU_CARD_TIMEOUT_MS",
        "HERMES_FEISHU_CARD_PROFILE_ID",
        "HERMES_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    hook_runtime.reset_runtime_state()

    helpers_module = types.ModuleType("gateway.platforms.helpers")

    def strip_markdown(text):
        return str(text).strip()

    strip_markdown.__module__ = helpers_module.__name__
    helpers_module.strip_markdown = strip_markdown
    monkeypatch.setitem(sys.modules, helpers_module.__name__, helpers_module)

    def card_policy(*_args, **_kwargs):
        return {"ok": True, "disposition": "card", "ttl_ms": 1000}

    monkeypatch.setattr(hook_runtime, "_fetch_delivery_policy_sync", card_policy)


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


def test_sync_policy_gate_cleans_native_state_when_identity_is_missing():
    hook_runtime._HFC_NATIVE_MEDIA_TEXT_SUPPRESSION.set(
        hook_runtime._NativeMediaTextSuppression("oc_stale", "stale answer")
    )

    result = hook_runtime._policy_gate_sync(
        hook_runtime.load_runtime_config(),
        {"platform": "telegram", "message_id": "om_missing"},
        "message.started",
    )

    assert result.card is False
    assert result.identity is None
    assert hook_runtime._HFC_NATIVE_MEDIA_TEXT_SUPPRESSION.get() is None


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


class TelegramSourceObject:
    platform = "telegram"
    chat_id = "telegram_chat"


class GatewayEventObject:
    def __init__(self, message_id: str):
        self.message_id = message_id


def test_status_from_hermes_emits_compaction_notice_with_topic_context(monkeypatch):
    posted = []

    def capture(local_vars, event_name="message.started"):
        payload = hook_runtime.build_event(event_name, local_vars)
        posted.append(payload)
        return payload is not None

    monkeypatch.setattr(
        hook_runtime,
        "emit_from_hermes_locals_threadsafe",
        capture,
    )
    local_vars = {
        "source": SourceObject(),
        "message_id": "om_compaction",
        "thread_id": "omt_compaction_topic",
        "reply_to_message_id": "om_topic_user",
        "_run_still_current": lambda: True,
    }

    handled = hook_runtime.handle_status_from_hermes_locals(
        local_vars,
        event_type="context",
        message=(
            "🗜️ Compacting context — summarizing earlier conversation "
            "so I can continue..."
        ),
    )

    assert handled is True
    assert len(posted) == 1
    payload = posted[0]
    assert payload["event"] == "system.notice"
    assert payload["conversation_id"] == "omt_compaction_topic"
    assert payload["thread_id"] == "omt_compaction_topic"
    assert payload["data"]["reply_to_message_id"] == "om_topic_user"
    assert payload["data"]["notice_kind"] == "context-compaction"
    assert payload["data"]["notice_id"] == "context-compaction:active"
    assert payload["data"]["notice_scope"] == "session"
    assert payload["data"]["phase"] == "started"
    assert payload["data"]["title"] == "正在压缩上下文"
    assert payload["data"]["level"] == "info"
    assert payload["data"]["content"] == "正在总结较早的对话，完成后会继续当前任务。"
    assert payload["data"]["create_session"] is True
    assert payload["data"]["display_status"] == "in_progress"


@pytest.mark.parametrize(
    "message",
    [
        "compression failed",
        "compressing files",
        "context pressure is high",
        "provider status: waiting",
        "",
    ],
)
def test_status_from_hermes_ignores_non_compaction_messages(monkeypatch, message):
    posted = []
    monkeypatch.setattr(
        hook_runtime,
        "emit_from_hermes_locals_threadsafe",
        lambda *args, **kwargs: posted.append((args, kwargs)) or True,
    )

    handled = hook_runtime.handle_status_from_hermes_locals(
        {
            "source": SourceObject(),
            "message_id": "om_compaction_non_match",
            "_run_still_current": lambda: True,
        },
        event_type="context",
        message=message,
    )

    assert handled is False
    assert posted == []


@pytest.mark.parametrize(
    "local_vars",
    [
        {
            "source": TelegramSourceObject(),
            "message_id": "telegram-message",
            "_run_still_current": lambda: True,
        },
        {
            "source": SourceObject(),
            "message_id": "om_stale_compaction",
            "_run_still_current": lambda: False,
        },
    ],
)
def test_status_from_hermes_ignores_non_feishu_or_stale_run(monkeypatch, local_vars):
    posted = []
    monkeypatch.setattr(
        hook_runtime,
        "emit_from_hermes_locals_threadsafe",
        lambda *args, **kwargs: posted.append((args, kwargs)) or True,
    )

    handled = hook_runtime.handle_status_from_hermes_locals(
        local_vars,
        event_type="context",
        message="Compacting context",
    )

    assert handled is False
    assert posted == []


def test_compaction_status_and_next_delta_use_increasing_sequence(monkeypatch):
    posted = []

    def capture(local_vars, event_name="message.started"):
        payload = hook_runtime.build_event(event_name, local_vars)
        posted.append(payload)
        return payload is not None

    monkeypatch.setattr(
        hook_runtime,
        "emit_from_hermes_locals_threadsafe",
        capture,
    )
    local_vars = {
        "source": SourceObject(),
        "message_id": "om_compaction_order",
        "_run_still_current": lambda: True,
    }

    assert hook_runtime.handle_status_from_hermes_locals(
        local_vars,
        event_type="context",
        message="COMPACTING   CONTEXT",
    )
    assert capture(
        {**local_vars, "text": "continued output"},
        event_name="answer.delta",
    )

    assert [payload["event"] for payload in posted] == [
        "system.notice",
        "answer.delta",
    ]
    assert [payload["sequence"] for payload in posted] == [0, 1]


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
    assert payload["data"]["profile_id"] == "default"
    assert payload["data"]["profile_source"] == "fallback_default"
    assert len(payload["data"]["native_handoff"]["generation"]) == 32


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("reply_to_message_id", "om_reply"),
        ("quote_message_id", "om_quote"),
        ("parent_message_id", "om_parent"),
    ],
)
def test_build_started_event_extracts_reply_context_from_local_vars(field, expected):
    payload = hook_runtime.build_event(
        "message.started",
        {
            "chat_id": "oc_direct",
            "message_id": "msg_direct",
            field: expected,
        },
    )

    assert payload["data"]["reply_to_message_id"] == expected


def test_build_started_event_extracts_reply_context_from_message_and_event_objects():
    class ReplyMessageObject:
        chat_id = "oc_message"
        message_id = "msg_message"
        parent_message_id = "om_parent"

    class QuoteEventObject:
        quote_message_id = "om_quote"

    payload = hook_runtime.build_event(
        "message.started",
        {
            "message": ReplyMessageObject(),
            "event": QuoteEventObject(),
        },
    )

    assert payload["data"]["reply_to_message_id"] == "om_parent"


def test_build_started_event_preserves_canonical_reply_priority_across_sources():
    class ReplyMessageObject:
        chat_id = "oc_message"
        message_id = "msg_message"
        reply_to_message_id = "om_message_reply"

    payload = hook_runtime.build_event(
        "message.started",
        {
            "message": ReplyMessageObject(),
            "quote_message_id": "om_quote",
        },
    )

    assert payload["data"]["reply_to_message_id"] == "om_quote"


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


def test_build_event_carries_feishu_thread_id_from_source():
    class ThreadSourceObject:
        platform = "feishu"
        chat_id = "oc_source"
        thread_id = "omt_thread"

    payload = hook_runtime.build_event(
        "message.started",
        {
            "source": ThreadSourceObject(),
            "session_id": "agent:main:feishu:dm:oc_source:omt_thread",
            "message_id": "om_user_message",
        },
    )

    assert payload["chat_id"] == "oc_source"
    assert payload["thread_id"] == "omt_thread"


def test_build_stream_event_carries_topic_reply_anchor_from_source_message_id():
    class TopicSourceObject:
        platform = "feishu"
        chat_id = "oc_source"
        thread_id = "omt_thread"
        message_id = "om_topic_user"

    payload = hook_runtime.build_event(
        "tool.updated",
        {
            "source": TopicSourceObject(),
            "session_id": "agent:main:feishu:dm:oc_source:omt_thread",
            "message_id": "om_topic_stream_reply",
            "tool_id": "terminal",
            "name": "terminal",
            "status": "running",
            "detail": "brew install ripgrep",
        },
    )

    assert payload["message_id"] == "om_topic_stream_reply"
    assert payload["thread_id"] == "omt_thread"
    assert payload["data"]["reply_to_message_id"] == "om_topic_user"


def test_build_tool_event_carries_arguments_duration_and_error():
    payload = hook_runtime.build_event(
        "tool.updated",
        {
            "platform": "feishu",
            "chat_id": "oc_group",
            "message_id": "om_tool",
            "tool_id": "tool-1",
            "name": "terminal",
            "status": "failed",
            "arguments": {"command": "date"},
            "duration_ms": 250,
            "error": "exit 1",
        },
    )

    assert payload["data"]["arguments"] == {"command": "date"}
    assert payload["data"]["duration_ms"] == 250
    assert payload["data"]["error"] == "exit 1"


def test_build_tool_event_extracts_duration_from_progress_callback_kwargs():
    payload = hook_runtime.build_event(
        "tool.updated",
        {
            "source": SourceObject(),
            "message_id": "om_tool_duration",
            "tool_id": "web_search",
            "name": "web_search",
            "status": "completed",
            "kwargs": {"duration": 1.75},
        },
    )

    assert payload is not None
    assert payload["data"]["duration_ms"] == 1750


def test_build_event_ignores_non_feishu_platforms():
    assert (
        hook_runtime.build_event(
            "message.started",
            {
                "source": TelegramSourceObject(),
                "message_id": "tg_message",
                "conversation_id": "tg_conversation",
            },
        )
        is None
    )


def test_handle_hfc_command_posts_command_without_building_normal_event(monkeypatch):
    posted = []
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    def fake_post(url, payload, timeout):
        posted.append((url, payload, timeout))
        return True

    monkeypatch.setattr(hook_runtime, "_post_json_sync", fake_post)

    handled = hook_runtime.handle_hfc_command_from_hermes_locals(
        {
            "source": SourceObject(),
            "message_id": "om_command",
            "text": "/hfc monitor",
        }
    )

    assert handled is True
    url, payload, timeout = posted[0]
    assert url == "http://sidecar.test/commands"
    assert timeout == 0.8
    assert payload["command"] == "monitor"
    assert payload["chat_id"] == "oc_source"
    assert payload["message_id"] == "om_command"
    assert payload["thread_id"] == ""


def test_handle_hfc_command_reads_gateway_event_text(monkeypatch):
    posted = []
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    class HfcEventObject:
        text = "/hfc status"
        message_id = "om_event_command"

    def fake_post(url, payload, timeout):
        posted.append((url, payload, timeout))
        return True

    monkeypatch.setattr(hook_runtime, "_post_json_sync", fake_post)

    handled = hook_runtime.handle_hfc_command_from_hermes_locals(
        {
            "source": SourceObject(),
            "event": HfcEventObject(),
            "message_id": "om_event_command",
        }
    )

    assert handled is True
    assert posted[0][1]["command"] == "status"
    assert posted[0][1]["message_id"] == "om_event_command"


def test_handle_hfc_command_forwards_chat_type_and_operator(monkeypatch):
    posted = []
    root_secret = b"r" * 32
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    monkeypatch.setattr(
        hook_runtime,
        "read_transport_root_secret",
        lambda: root_secret,
    )

    class HfcEventObject:
        text = "/hfc doctor"
        message_id = "om_event_command"
        chat_type = "group"
        operator = SimpleNamespace(open_id="ou_initiator")

    monkeypatch.setattr(
        hook_runtime,
        "_post_json_sync_response",
        lambda url, payload, timeout: posted.append(payload)
        or {"ok": True, "operation_id": "operation-1"},
    )

    handled = hook_runtime.handle_hfc_command_from_hermes_locals(
        {
            "source": SourceObject(),
            "event": HfcEventObject(),
            "message_id": "om_event_command",
        }
    )

    assert handled is True
    assert posted[0]["chat_type"] == "group"
    assert posted[0]["operator"] == "ou_initiator"
    assert "adapter_transport_secret" not in posted[0]
    assert posted[0]["adapter_command_proof"]["signature"]
    assert hook_runtime._transport_secret_for_token(
        _operation_token()
    ) == hook_runtime.derive_operation_transport_secret(root_secret, "operation-1")


def test_handle_hfc_command_ignores_regular_messages(monkeypatch):
    posted = []
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_sync",
        lambda *args: posted.append(args),
    )

    handled = hook_runtime.handle_hfc_command_from_hermes_locals(
        {
            "source": SourceObject(),
            "message_id": "om_normal",
            "text": "hello /hfc status",
        }
    )

    assert handled is False
    assert posted == []


@pytest.mark.asyncio
async def test_async_emit_does_not_post_non_feishu_events(monkeypatch):
    posted = []

    async def fake_post(url, payload, timeout):
        posted.append(payload)

    monkeypatch.setattr(hook_runtime, "_post_json_ordered", fake_post)

    delivered = await hook_runtime.emit_from_hermes_locals_async(
        {
            "source": TelegramSourceObject(),
            "message_id": "tg_message",
            "conversation_id": "tg_conversation",
        },
        event_name="message.completed",
    )

    assert delivered is False
    assert posted == []


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


def test_build_event_uses_event_message_id_from_hermes_run_agent_started_hook():
    payload = hook_runtime.build_event(
        "message.started",
        {
            "source": SourceObject(),
            "event_message_id": "om_hermes_20260507",
            "session_id": "session_source",
        },
    )

    assert payload["message_id"] == "om_hermes_20260507"


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
    assert payload["data"] == {
        "profile_id": "default",
        "profile_source": "fallback_default",
        "text": "对象文本",
    }


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

    native_handoff = payload["data"].pop("native_handoff")
    assert len(native_handoff["generation"]) == 32
    assert set(native_handoff) == {"generation"}
    assert payload["data"] == {
        "profile_id": "default",
        "profile_source": "fallback_default",
        "answer": "最终答案",
        "attachments": [],
        "native_delivery": "allowed",
        "duration": 2.75,
        "model": "MiniMax M2.7",
        "tokens": {"input_tokens": 12, "output_tokens": 34},
        "context": {"used_tokens": 182_000, "max_tokens": 204_000},
    }


@pytest.mark.parametrize(
    ("event_name", "display_status"),
    [
        ("message.completed", "in_progress"),
        ("message.failed", "failed"),
    ],
)
def test_terminal_event_carries_exact_explicit_display_status(event_name, display_status):
    payload = hook_runtime.build_event(
        event_name,
        {
            "chat_id": "oc_abc",
            "message_id": "msg_status",
            "answer": "最终答案",
            "error": "处理失败",
            "display_status": display_status,
        },
    )

    assert payload["data"]["display_status"] == display_status


@pytest.mark.parametrize("display_status", ["running", "COMPLETED", " completed "])
def test_terminal_event_omits_invalid_explicit_display_status(display_status):
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_abc",
            "message_id": "msg_status",
            "answer": "最终答案",
            "display_status": display_status,
        },
    )

    assert "display_status" not in payload["data"]


def test_build_interaction_event_reuses_active_card_message_id():
    local_vars = {
        "chat_id": "oc_abc",
        "conversation_id": "conv_abc",
        "event_message_id": "om_hermes_20260516",
    }

    started = hook_runtime.build_event("message.started", local_vars)
    interaction = hook_runtime.build_interaction_event(
        local_vars,
        kind="approval",
        interaction_id="approval-1",
        prompt="允许执行命令吗？",
        description="rm -rf /tmp/demo",
        options=[
            {"label": "允许一次", "value": "once"},
            {"label": "拒绝", "value": "deny"},
        ],
    )

    assert interaction["event"] == "interaction.requested"
    assert interaction["message_id"] == started["message_id"]
    assert interaction["data"]["interaction_id"] == "approval-1"
    assert interaction["data"]["kind"] == "approval"
    assert interaction["data"]["prompt"] == "允许执行命令吗？"
    assert interaction["data"]["options"][0]["value"] == "once"


def test_request_interaction_posts_event_and_polls_until_completed(monkeypatch):
    posted = []
    polls = iter(
        [
            {"ok": True, "status": "pending", "interaction_id": "approval-1"},
            {
                "ok": True,
                "status": "completed",
                "interaction_id": "approval-1",
                "choice": "once",
                "choice_label": "允许一次",
            },
        ]
    )
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    def fake_post(local_vars, url, payload, timeout):
        posted.append((local_vars, url, payload, timeout))
        return {"ok": True, "applied": True}

    def fake_get(url, timeout):
        assert url == "http://sidecar.test/interactions/approval-1"
        return next(polls)

    monkeypatch.setattr(hook_runtime, "_post_interaction_event", fake_post)
    monkeypatch.setattr(hook_runtime, "_get_json_sync", fake_get)

    result = hook_runtime.request_interaction_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        kind="approval",
        interaction_id="approval-1",
        prompt="允许执行命令吗？",
        options=[{"label": "允许一次", "value": "once"}],
        timeout_seconds=1,
        poll_interval_seconds=0,
    )

    assert result == {
        "ok": True,
        "status": "completed",
        "interaction_id": "approval-1",
        "choice": "once",
        "choice_label": "允许一次",
    }
    assert posted[0][1] == "http://sidecar.test/events"
    assert posted[0][2]["event"] == "interaction.requested"


def test_request_slash_confirm_async_posts_event_and_polls_until_completed(monkeypatch):
    posted = []
    polls = iter(
        [
            {"ok": True, "status": "pending", "interaction_id": "slash-new-1"},
            {
                "ok": True,
                "status": "completed",
                "interaction_id": "slash-new-1",
                "choice": "once",
                "choice_label": "允许一次",
            },
        ]
    )
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    async def fake_post(url, payload, timeout):
        posted.append((url, payload, timeout))
        return {"ok": True, "applied": True, "interaction_mode": "card"}

    async def fake_get(url, timeout):
        assert url == "http://sidecar.test/interactions/slash-new-1"
        return next(polls)

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post)
    monkeypatch.setattr(hook_runtime, "_get_json", fake_get)

    async def run():
        return await hook_runtime.request_slash_confirm_from_hermes_locals_async(
            {
                "chat_id": "oc_abc",
                "message_id": "msg_1",
                "conversation_id": "conv_abc",
            },
            command="new",
            title="Confirm /new",
            message="This starts a fresh session.",
            interaction_id="slash-new-1",
            timeout_seconds=1,
            poll_interval_seconds=0,
        )

    result = asyncio.run(run())

    assert result == "once"
    assert posted[0][0] == "http://sidecar.test/events"
    payload = posted[0][1]
    assert payload["event"] == "interaction.requested"
    assert payload["data"]["kind"] == "slash_confirm"
    assert payload["data"]["fallback_policy"] == "native_text"
    assert payload["data"]["interaction_id"] == "slash-new-1"
    assert payload["data"]["prompt"] == "Confirm /new"
    assert payload["data"]["description"] == "This starts a fresh session."
    assert [option["value"] for option in payload["data"]["options"]] == [
        "once",
        "always",
        "cancel",
    ]
    assert payload["data"]["options"][0]["label"] == "允许一次"
    assert payload["data"]["options"][2]["style"] == "danger"


def test_request_slash_confirm_async_skips_sidecar_when_native_feishu_card_available(monkeypatch):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()

        async def _feishu_send_with_retry(self, **kwargs):
            raise AssertionError("native send happens later in Hermes")

    posted = []
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    async def fake_post(url, payload, timeout):
        posted.append((url, payload, timeout))
        return {"ok": False}

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post)

    async def run():
        return await hook_runtime.request_slash_confirm_from_hermes_locals_async(
            {
                "self": SimpleNamespace(adapters={"feishu": DummyFeishuAdapter()}),
                "source": SimpleNamespace(platform="feishu", chat_id="oc_abc"),
                "chat_id": "oc_abc",
                "conversation_id": "feishu:oc_abc",
                "message_id": "om_cmd",
            },
            command="/new",
            title="Confirm /new",
            message="This starts a fresh session.",
            interaction_id="slash_native",
        )

    assert asyncio.run(run()) is None
    assert posted == []


def test_install_feishu_command_card_methods_adds_native_slash_confirm():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.sent = None

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent = kwargs
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id="om_slash_card"),
            )

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})

    installed = hook_runtime.install_feishu_command_card_adapter_methods(runner)

    async def run():
        return await adapter.send_slash_confirm(
            chat_id="oc_abc",
            title="/new",
            message=(
                "⚠️ **Confirm /new**\n\n"
                "This starts a fresh session and discards history.\n\n"
                "Choose:\n"
                "• **Approve Once** — proceed this time only"
            ),
            session_key="feishu:oc_abc",
            confirm_id="cf-1",
            metadata={"reply_to_message_id": "om_user_cmd"},
        )

    result = asyncio.run(run())

    assert installed is True
    assert result.success is True
    assert result.message_id == "om_slash_card"
    assert adapter.sent["chat_id"] == "oc_abc"
    assert adapter.sent["msg_type"] == "interactive"
    assert adapter.sent["reply_to"] == "om_user_cmd"

    card = json.loads(adapter.sent["payload"])
    assert card["config"] == {"wide_screen_mode": True, "update_multi": True}
    assert card["header"]["template"] == "orange"
    assert card["header"]["title"]["content"] == "/new"
    assert "This starts a fresh session" in card["elements"][0]["content"]
    actions = card["elements"][1]["actions"]
    assert [action["value"]["hfc_choice"] for action in actions] == [
        "once",
        "always",
        "cancel",
    ]
    assert actions[0]["value"]["hfc_action"] == "slash_confirm"
    assert adapter._hfc_slash_confirm_state["cf-1"] == {
        "session_key": "feishu:oc_abc",
        "chat_id": "oc_abc",
        "message_id": "om_slash_card",
    }


def test_native_slash_confirm_tracks_send_result_message_id():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()

        async def _feishu_send_with_retry(self, **kwargs):
            return SimpleNamespace(success=True, message_id="om_direct_slash_card")

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True

    async def run():
        return await adapter.send_slash_confirm(
            chat_id="oc_abc",
            title="/new",
            message="This starts a fresh session.",
            session_key="feishu:oc_abc",
            confirm_id="cf-direct",
            metadata=None,
        )

    result = asyncio.run(run())

    assert result.success is True
    assert result.message_id == "om_direct_slash_card"
    assert adapter._hfc_slash_confirm_state["cf-direct"]["message_id"] == "om_direct_slash_card"


@pytest.mark.parametrize(
    ("text", "raw_command", "expected_command"),
    [
        ("/status", "status", "status"),
        ("/update", "update", "update"),
        ("/deploy-preview now", "deploy-preview", "deploy-preview"),
        ("/does-not-exist", "does-not-exist", "does-not-exist"),
    ],
)
def test_all_feishu_slash_commands_create_feedback_context(
    text,
    raw_command,
    expected_command,
):
    event = SimpleNamespace(
        source=SimpleNamespace(
            platform="feishu",
            chat_id="oc_abc",
            thread_id="omt_topic",
        ),
        text=text,
        message_id="om_user_command",
        get_command=lambda: raw_command,
    )

    context = hook_runtime._hfc_command_result_context_from_event(event)

    assert context is not None
    assert context["command"] == expected_command
    assert context["raw_command"] == raw_command
    assert context["chat_id"] == "oc_abc"
    assert context["reply_to_message_id"] == "om_user_command"
    assert context["thread_id"] == "omt_topic"
    assert context["card_message_id"] == ""
    assert context["expires_at"] > time.monotonic()


def test_feishu_command_alias_uses_hermes_canonical_name(monkeypatch):
    commands_module = types.ModuleType("hermes_cli.commands")
    commands_module.resolve_command = lambda command: (
        SimpleNamespace(name="compress") if command == "compact" else None
    )
    package = types.ModuleType("hermes_cli")
    package.commands = commands_module
    monkeypatch.setitem(sys.modules, "hermes_cli", package)
    monkeypatch.setitem(sys.modules, "hermes_cli.commands", commands_module)
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="/compact",
        message_id="om_user_compact",
        get_command=lambda: "compact",
    )

    context = hook_runtime._hfc_command_result_context_from_event(event)

    assert context is not None
    assert context["raw_command"] == "compact"
    assert context["command"] == "compress"


@pytest.mark.parametrize(
    "event",
    [
        SimpleNamespace(
            source=SimpleNamespace(platform="telegram", chat_id="tg_abc"),
            text="/status",
            get_command=lambda: "status",
        ),
        SimpleNamespace(
            source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
            text="ordinary chat",
            get_command=lambda: "",
        ),
        SimpleNamespace(
            source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
            text="/",
            get_command=lambda: "",
        ),
    ],
)
def test_non_feishu_or_non_command_event_has_no_feedback_context(event):
    assert hook_runtime._hfc_command_result_context_from_event(event) is None


def test_command_feedback_context_rejects_empty_mismatch_and_expired_content():
    base = {
        "command": "status",
        "raw_command": "status",
        "chat_id": "oc_expected",
        "reply_to_message_id": "om_user",
        "thread_id": "",
        "card_message_id": "",
        "expires_at": time.monotonic() + 60,
    }

    hook_runtime._HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(dict(base))
    assert (
        hook_runtime._hfc_take_feishu_command_result_context(
            chat_id="oc_expected",
            content="   ",
        )
        is None
    )

    hook_runtime._HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(dict(base))
    assert (
        hook_runtime._hfc_take_feishu_command_result_context(
            chat_id="oc_other",
            content="status output",
        )
        is None
    )

    expired = dict(base)
    expired["expires_at"] = time.monotonic() - 1
    hook_runtime._HFC_FEISHU_COMMAND_RESULT_CONTEXT.set(expired)
    assert (
        hook_runtime._hfc_take_feishu_command_result_context(
            chat_id="oc_expected",
            content="status output",
        )
        is None
    )


def test_native_feishu_direct_new_result_is_sent_as_command_card():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.sent = None
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_text")

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent = kwargs
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id="om_new_result_card"),
            )

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="/new",
        message_id="om_user_new",
        get_command=lambda: "new",
    )

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event) is True

    async def run():
        return await adapter.send(
            "oc_abc",
            "✨ Session reset! Starting fresh.",
            reply_to="om_user_new",
            metadata={"reply_to_message_id": "om_user_new"},
        )

    result = asyncio.run(run())

    assert result.success is True
    assert result.message_id == "om_new_result_card"
    assert adapter.text_sent == []
    assert adapter.sent["msg_type"] == "interactive"
    assert adapter.sent["reply_to"] == "om_user_new"
    card = json.loads(adapter.sent["payload"])
    assert card["header"]["title"]["content"] == "会话已重置"
    assert card["header"]["template"] == "green"
    assert "Session reset" in card["elements"][0]["content"]


def test_native_feishu_command_feedback_updates_same_card(monkeypatch):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.sent = []
            self.updated = []
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append(content)
            return SimpleNamespace(success=True, message_id=f"om_text_{len(self.text_sent)}")

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent.append(kwargs)
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id=f"om_card_{len(self.sent)}"),
            )

    adapter = DummyFeishuAdapter()

    async def update_card(_adapter, message_id, card):
        adapter.updated.append((message_id, card))
        return True

    monkeypatch.setattr(hook_runtime, "_hfc_update_native_command_card", update_card)
    runner = SimpleNamespace(adapters={"feishu": adapter})
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="/new",
        message_id="om_user_new",
        get_command=lambda: "new",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)

    async def run():
        first = await adapter.send("oc_abc", "Session reset.", reply_to="om_user_new")
        second = await adapter.send("oc_abc", "ordinary follow-up", reply_to="om_user_new")
        return first, second

    first, second = asyncio.run(run())

    assert first.message_id == "om_card_1"
    assert second.message_id == "om_card_1"
    assert len(adapter.sent) == 1
    assert adapter.updated[0][0] == "om_card_1"
    assert adapter.updated[0][1]["elements"][0]["content"] == "ordinary follow-up"
    assert adapter.text_sent == []


def test_concurrent_command_feedback_creates_one_card(monkeypatch):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.created = []
            self.updated = []
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append(content)
            return SimpleNamespace(success=True, message_id="om_text")

        async def _feishu_send_with_retry(self, **kwargs):
            self.created.append(kwargs)
            await asyncio.sleep(0)
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id="om_command_card"),
            )

    adapter = DummyFeishuAdapter()

    async def update_card(_adapter, message_id, card):
        adapter.updated.append((message_id, card))
        return True

    monkeypatch.setattr(hook_runtime, "_hfc_update_native_command_card", update_card)
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="/status",
        message_id="om_user_status",
        get_command=lambda: "status",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter}),
        event=event,
    )

    async def run():
        return await asyncio.gather(
            adapter.send("oc_abc", "first"),
            adapter.send("oc_abc", "second"),
        )

    results = asyncio.run(run())

    assert [result.message_id for result in results] == [
        "om_command_card",
        "om_command_card",
    ]
    assert len(adapter.created) == 1
    assert len(adapter.updated) == 1
    assert adapter.text_sent == []


def test_command_feedback_patch_failure_falls_back_to_exact_native_text(monkeypatch):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_fallback")

        async def _feishu_send_with_retry(self, **kwargs):
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id="om_command_card"),
            )

    async def failed_update(_adapter, _message_id, _card):
        return False

    monkeypatch.setattr(hook_runtime, "_hfc_update_native_command_card", failed_update)
    adapter = DummyFeishuAdapter()
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="/status",
        message_id="om_user_status",
        get_command=lambda: "status",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter}),
        event=event,
    )

    async def run():
        await adapter.send("oc_abc", "first")
        return await adapter.send(
            "oc_abc",
            "exact second feedback",
            reply_to="om_user_status",
            metadata={"thread_id": "omt_topic"},
        )

    result = asyncio.run(run())

    assert result.message_id == "om_native_fallback"
    assert adapter.text_sent == [
        (
            "oc_abc",
            "exact second feedback",
            "om_user_status",
            {"thread_id": "omt_topic"},
        )
    ]


def test_command_feedback_create_failure_falls_back_to_exact_native_text():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_fallback")

        async def _feishu_send_with_retry(self, **kwargs):
            return SimpleNamespace(success=False, error="card create failed")

    adapter = DummyFeishuAdapter()
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="/usage",
        message_id="om_user_usage",
        get_command=lambda: "usage",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter}),
        event=event,
    )

    result = asyncio.run(
        adapter.send(
            "oc_abc",
            "exact usage feedback",
            reply_to="om_user_usage",
            metadata={"thread_id": "omt_topic"},
        )
    )

    assert result.message_id == "om_native_fallback"
    assert adapter.text_sent == [
        (
            "oc_abc",
            "exact usage feedback",
            "om_user_usage",
            {"thread_id": "omt_topic"},
        )
    ]


def test_command_feedback_long_markdown_is_split_without_data_loss():
    content = ("paragraph\n\n" * 700).strip()

    card = hook_runtime._hfc_command_result_card(
        title="/commands",
        content=content,
    )

    assert len(card["elements"]) > 1
    assert "".join(element["content"] for element in card["elements"]) == content


@pytest.mark.asyncio
async def test_v400_hook_runtime_suppresses_matching_native_media_text_after_card_delivery(
    monkeypatch,
):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []
            self.media_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content))
            return SimpleNamespace(success=True, message_id="om_native_text")

        async def send_multiple_images(self, chat_id, images, metadata=None):
            self.media_sent.append((chat_id, images))
            return SimpleNamespace(success=True, message_id="om_native_image")

    async def applied(_url, _payload, _timeout):
        return {"ok": True, "applied": True}

    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    monkeypatch.setattr(hook_runtime, "_post_json_response", applied)
    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    hook_runtime.install_feishu_command_card_adapter_methods(runner)

    delivered = await hook_runtime.emit_from_hermes_locals_async(
        {
            "source": SourceObject(),
            "message_id": "om_media_turn",
            "answer": "已生成图片\nMEDIA:/tmp/result.png",
        },
        event_name="message.completed",
    )
    unrelated = await adapter.send("oc_source", "另一条正常消息")
    duplicate = await adapter.send("oc_source", "已生成图片")
    media = await adapter.send_multiple_images(
        "oc_source", [("file:///tmp/result.png", "")]
    )
    repeated_later = await adapter.send("oc_source", "已生成图片")

    assert delivered is True
    assert unrelated.message_id == "om_native_text"
    assert duplicate.success is True
    assert duplicate.message_id == "media_text_suppressed"
    assert media.message_id == "om_native_image"
    assert repeated_later.message_id == "om_native_text"
    assert adapter.text_sent == [
        ("oc_source", "另一条正常消息"),
        ("oc_source", "已生成图片"),
    ]
    assert adapter.media_sent == [
        ("oc_source", [("file:///tmp/result.png", "")])
    ]


@pytest.mark.asyncio
async def test_v4021_hook_runtime_keeps_image_delivery_and_accepted_notice_in_same_turn(
    monkeypatch,
):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []
            self.media_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_text")

        async def send_multiple_images(self, chat_id, images, metadata=None):
            self.media_sent.append((chat_id, images, metadata))
            return SimpleNamespace(success=True, message_id="om_native_image")

    async def fake_post_json_ordered_response(_url, payload, _timeout):
        if payload["event"] == "message.completed":
            return {"ok": True, "applied": True}
        if payload["event"] == "system.notice":
            return {
                "ok": True,
                "applied": True,
                "delivery": {"outcome": "accepted"},
            }
        raise AssertionError(f"unexpected event: {payload['event']}")

    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )
    adapter = DummyFeishuAdapter()
    hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter})
    )

    delivered = await hook_runtime.emit_from_hermes_locals_async(
        {
            "source": SourceObject(),
            "message_id": "om_media_turn",
            "answer": "已生成图片\nMEDIA:/tmp/result.png",
        },
        event_name="message.completed",
    )
    duplicate = await adapter.send("oc_source", "已生成图片")
    media = await adapter.send_multiple_images(
        "oc_source", [("file:///tmp/result.png", "")]
    )

    token = hook_runtime._HFC_FEISHU_NOTICE_CONTEXT.set(
        {
            "chat_id": "oc_source",
            "message_id": "om_media_turn",
            "conversation_id": "oc_source",
            "thread_id": "",
        }
    )
    try:
        notice_result = await adapter.send(
            "oc_source",
            '✅ Background task complete\nPrompt: "Generate image"\n\nImage ready.',
        )
    finally:
        hook_runtime._HFC_FEISHU_NOTICE_CONTEXT.reset(token)

    assert delivered is True
    assert duplicate.message_id == "media_text_suppressed"
    assert media.message_id == "om_native_image"
    assert notice_result.message_id == "om_media_turn"
    assert len(adapter.media_sent) == 1
    assert adapter.media_sent == [
        ("oc_source", [("file:///tmp/result.png", "")], None)
    ]
    assert all(
        content != hook_runtime._NOTICE_UNCERTAIN_WARNING
        for _chat_id, content, _reply_to, _metadata in adapter.text_sent
    )
    assert adapter.text_sent == []


@pytest.mark.asyncio
async def test_v400_hook_runtime_keeps_native_media_text_when_card_delivery_fails(
    monkeypatch,
):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content))
            return SimpleNamespace(success=True, message_id="om_native_text")

    async def rejected(_url, _payload, _timeout):
        return {"ok": True, "applied": False, "disposition": "native"}

    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    monkeypatch.setattr(hook_runtime, "_post_json_response", rejected)
    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    hook_runtime.install_feishu_command_card_adapter_methods(runner)

    delivered = await hook_runtime.emit_from_hermes_locals_async(
        {
            "source": SourceObject(),
            "message_id": "om_media_fail_open",
            "answer": "已生成图片\nMEDIA:/tmp/result.png",
        },
        event_name="message.completed",
    )
    result = await adapter.send("oc_source", "已生成图片")

    assert delivered is False
    assert result.message_id == "om_native_text"
    assert adapter.text_sent == [("oc_source", "已生成图片")]


def test_native_feishu_update_command_result_uses_card():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.sent = None
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_plain_update")

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent = kwargs
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id="om_unexpected_card"),
            )

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="/update",
        message_id="om_user_update",
        get_command=lambda: "update",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)

    async def run():
        return await adapter.send("oc_abc", "Update started.", reply_to="om_user_update")

    result = asyncio.run(run())

    assert result.success is True
    assert result.message_id == "om_unexpected_card"
    assert adapter.sent is not None
    assert adapter.text_sent == []
    card = json.loads(adapter.sent["payload"])
    assert card["header"]["title"]["content"] == "/update"
    assert card["elements"][0]["content"] == "Update started."


@pytest.mark.parametrize(
    "terminal_feedback",
    [
        "🗜️ Compressed: 57 → 13 messages\nApprox request size: ~47,319 → ~12,910 tokens",
        "No changes from compression.\nApprox request size: ~12,910 tokens (unchanged)",
        "⚠️ Compression aborted. No messages were dropped.",
    ],
)
def test_manual_compress_updates_running_card_with_original_result(
    monkeypatch,
    terminal_feedback,
):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.created = []
            self.updated = []
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append(content)
            return SimpleNamespace(success=True, message_id="om_native")

        async def _feishu_send_with_retry(self, **kwargs):
            self.created.append(kwargs)
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id="om_compress_card"),
            )

    class DummyRunner:
        def __init__(self, adapter):
            self.adapters = {"feishu": adapter}
            self.original_calls = 0

        async def _handle_compress_command(self, event):
            self.original_calls += 1
            return terminal_feedback

    adapter = DummyFeishuAdapter()

    async def update_card(_adapter, message_id, card):
        adapter.updated.append((message_id, card))
        return True

    monkeypatch.setattr(hook_runtime, "_hfc_update_native_command_card", update_card)
    runner = DummyRunner(adapter)
    event = SimpleNamespace(
        source=SimpleNamespace(
            platform="feishu",
            chat_id="oc_abc",
            thread_id="omt_topic",
        ),
        text="/compress",
        message_id="om_user_compress",
        get_command=lambda: "compress",
    )

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)
    result = asyncio.run(runner._handle_compress_command(event))

    assert result is None
    assert runner.original_calls == 1
    assert len(adapter.created) == 1
    created_card = json.loads(adapter.created[0]["payload"])
    assert created_card["header"]["title"]["content"] == "上下文压缩"
    assert created_card["header"]["template"] == "blue"
    assert created_card["elements"][0]["content"] == "⏳ 正在压缩上下文…"
    assert adapter.created[0]["reply_to"] == "om_user_compress"
    assert adapter.created[0]["metadata"] == {"thread_id": "omt_topic"}
    assert adapter.updated[0][0] == "om_compress_card"
    updated_card = adapter.updated[0][1]
    assert "".join(element["content"] for element in updated_card["elements"]) == terminal_feedback
    assert adapter.text_sent == []


def test_manual_compress_begin_failure_returns_original_feedback():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            return SimpleNamespace(success=True, message_id="om_native")

        async def _feishu_send_with_retry(self, **kwargs):
            return SimpleNamespace(success=False, error="create failed")

    class DummyRunner:
        def __init__(self):
            self.adapters = {"feishu": DummyFeishuAdapter()}
            self.original_calls = 0

        async def _handle_compress_command(self, event):
            self.original_calls += 1
            return "original compression feedback"

    runner = DummyRunner()
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="/compress",
        message_id="om_user_compress",
        get_command=lambda: "compress",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)

    result = asyncio.run(runner._handle_compress_command(event))

    assert result == "original compression feedback"
    assert runner.original_calls == 1


def test_manual_compress_terminal_patch_failure_returns_original_feedback(monkeypatch):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            return SimpleNamespace(success=True, message_id="om_native")

        async def _feishu_send_with_retry(self, **kwargs):
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id="om_compress_card"),
            )

    class DummyRunner:
        def __init__(self):
            self.adapters = {"feishu": DummyFeishuAdapter()}

        async def _handle_compress_command(self, event):
            return "terminal compression feedback"

    async def failed_update(_adapter, _message_id, _card):
        return False

    monkeypatch.setattr(hook_runtime, "_hfc_update_native_command_card", failed_update)
    runner = DummyRunner()
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="/compact",
        message_id="om_user_compact",
        get_command=lambda: "compact",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)

    result = asyncio.run(runner._handle_compress_command(event))

    assert result == "terminal compression feedback"


def test_compress_handler_wrapper_is_idempotent_and_non_feishu_bypasses_card():
    class DummyTelegramAdapter:
        name = "telegram"

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            return SimpleNamespace(success=True, message_id="tg_native")

    class DummyRunner:
        def __init__(self):
            self.adapters = {"telegram": DummyTelegramAdapter()}
            self.original_calls = 0

        async def _handle_compress_command(self, event):
            self.original_calls += 1
            return "telegram compression feedback"

    runner = DummyRunner()
    event = SimpleNamespace(
        source=SimpleNamespace(platform="telegram", chat_id="tg_abc"),
        text="/compress",
        message_id="tg_user",
        get_command=lambda: "compress",
    )

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event) is False
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event) is False
    result = asyncio.run(runner._handle_compress_command(event))

    assert result == "telegram compression feedback"
    assert runner.original_calls == 1


@pytest.mark.parametrize(
    ("platform", "chat_type", "args", "command"),
    [
        ("telegram", "private", "", "update"),
        ("feishu", "group", "", "update"),
        ("feishu", "private", "--yes", "update"),
        ("feishu", "private", "", "upgrade"),
    ],
)
def test_update_command_wrapper_preserves_original_outside_exact_private_bare_command(
    platform,
    chat_type,
    args,
    command,
    monkeypatch,
):
    requests = []

    async def request_update(runner, event):
        requests.append((runner, event))
        return True

    monkeypatch.setattr(hook_runtime, "_hfc_request_update_command", request_update)

    class DummyRunner:
        adapters = {}

        def __init__(self):
            self.original_calls = 0

        async def _handle_update_command(self, event):
            self.original_calls += 1
            return "original update"

    runner = DummyRunner()
    event = SimpleNamespace(
        source=SimpleNamespace(
            platform=platform,
            chat_type=chat_type,
            chat_id="oc_private",
        ),
        get_command=lambda: command,
        get_command_args=lambda: args,
    )

    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)
    result = asyncio.run(runner._handle_update_command(event))

    assert result == "original update"
    assert runner.original_calls == 1
    assert requests == []


def test_update_command_wrapper_routes_exact_private_bare_command_and_fails_closed(
    monkeypatch,
):
    outcomes = iter([True, False])
    requests = []

    async def request_update(runner, event):
        requests.append((runner, event))
        return next(outcomes)

    monkeypatch.setattr(hook_runtime, "_hfc_request_update_command", request_update)

    class DummyRunner:
        adapters = {}

        def __init__(self):
            self.original_calls = 0

        async def _handle_update_command(self, event):
            self.original_calls += 1
            return "original update"

    runner = DummyRunner()
    event = SimpleNamespace(
        source=SimpleNamespace(
            platform="feishu",
            chat_type="private",
            chat_id="oc_private",
        ),
        sender_id=SimpleNamespace(open_id="ou_owner"),
        get_command=lambda: "update",
        get_command_args=lambda: "",
    )

    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)
    assert asyncio.run(runner._handle_update_command(event)) is None
    unavailable = asyncio.run(runner._handle_update_command(event))

    assert "暂不可用" in unavailable
    assert runner.original_calls == 0
    assert len(requests) == 2


def test_update_command_wrapper_requires_private_operator_identity(monkeypatch):
    requests = []

    async def request_update(runner, event):
        requests.append((runner, event))
        return True

    monkeypatch.setattr(hook_runtime, "_hfc_request_update_command", request_update)

    class DummyRunner:
        adapters = {}

        def __init__(self):
            self.original_calls = 0

        async def _handle_update_command(self, event):
            self.original_calls += 1
            return "original update"

    runner = DummyRunner()
    event = SimpleNamespace(
        source=SimpleNamespace(
            platform="feishu",
            chat_type="private",
            chat_id="oc_private",
        ),
        get_command=lambda: "update",
        get_command_args=lambda: "",
    )

    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)
    result = asyncio.run(runner._handle_update_command(event))

    assert "无法确认操作者" in result
    assert runner.original_calls == 0
    assert requests == []


def test_maintenance_admission_fence_blocks_new_gateway_work(
    tmp_path,
    monkeypatch,
):
    from hermes_feishu_card.maintenance_store import (
        maintenance_paths,
        release_drain_lease,
        reserve_drain_lease,
    )

    monkeypatch.setenv("HERMES_FEISHU_CARD_STATE_DIR", str(tmp_path / "state"))
    paths = maintenance_paths()
    reserve_drain_lease(paths, owner_id="job-1")

    class Adapter:
        def __init__(self):
            self.sent = []

        async def send(self, chat_id, content, **kwargs):
            self.sent.append((chat_id, content, kwargs))

    adapter = Adapter()
    source = SimpleNamespace(chat_id="oc_private", platform="feishu")

    class Runner:
        adapters = {"feishu": adapter}
        _profile_adapters = {}
        _running_agents = {"active": object()}
        _external_drain_active = True

        def _active_work_count(self):
            return 3

        def _adapter_for_source(self, current_source):
            assert current_source is source
            return adapter

    runner = Runner()
    local_vars = {"self": runner, "event": SimpleNamespace(source=source), "source": source}

    assert asyncio.run(
        hook_runtime.maintenance_admission_from_hermes_locals(local_vars)
    ) is True
    assert adapter.sent and "维护升级" in adapter.sent[0][1]
    assert hook_runtime.gateway_active_session_count() == 3
    assert hook_runtime.gateway_external_drain_active() is True
    assert hook_runtime.gateway_active_work_snapshot() == (3, True)

    assert release_drain_lease(paths, owner_id="job-1") is True
    assert asyncio.run(
        hook_runtime.maintenance_admission_from_hermes_locals(local_vars)
    ) is False


def test_command_adapter_install_registers_gateway_runner_before_heartbeat(
    monkeypatch,
):
    snapshots = []

    monkeypatch.setattr(
        hook_runtime,
        "_ensure_runtime_control_started",
        lambda: snapshots.append(hook_runtime.gateway_active_work_snapshot()) or True,
    )

    class Runner:
        adapters = {}

        def _active_work_count(self):
            return 0

    runner = Runner()

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is False
    assert snapshots == [(0, True)]


def test_gateway_drain_home_requires_runtime_home_to_match_checkout(
    tmp_path,
    monkeypatch,
):
    gateway_source = tmp_path / "home" / "hermes-agent" / "gateway" / "run.py"
    gateway_source.parent.mkdir(parents=True)
    gateway_source.write_text("# fixture\n", encoding="utf-8")
    monkeypatch.setitem(
        sys.modules,
        "gateway.run",
        SimpleNamespace(__file__=str(gateway_source)),
    )
    current_home = [gateway_source.parents[2]]
    monkeypatch.setattr(
        hook_runtime.importlib,
        "import_module",
        lambda name: SimpleNamespace(
            get_process_hermes_home=lambda: current_home[0]
        ),
    )

    assert hook_runtime.gateway_drain_home_verified() is True
    current_home[0] = tmp_path / "home" / "profiles" / "secondary"
    assert hook_runtime.gateway_drain_home_verified() is False


def test_manual_compress_original_exception_is_not_swallowed():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            return SimpleNamespace(success=True, message_id="om_native")

        async def _feishu_send_with_retry(self, **kwargs):
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id="om_compress_card"),
            )

    class DummyRunner:
        def __init__(self):
            self.adapters = {"feishu": DummyFeishuAdapter()}

        async def _handle_compress_command(self, event):
            raise RuntimeError("compress exploded")

    runner = DummyRunner()
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="/compress",
        message_id="om_user_compress",
        get_command=lambda: "compress",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)

    with pytest.raises(RuntimeError, match="compress exploded"):
        asyncio.run(runner._handle_compress_command(event))


def test_native_feishu_system_notice_send_posts_sidecar_and_suppresses_text(monkeypatch):
    posted = []

    async def fake_post_json_ordered_response(url, payload, timeout):
        posted.append((url, payload, timeout))
        return {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered"},
        }

    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:8765/events")
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_text")

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="查一下广州明天天气",
        message_id="om_user_weather",
        get_command=lambda: "",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)

    async def run():
        return await adapter.send(
            "oc_abc",
            "ℹ️ Codex gpt-5.5 caps context at 272K, so auto-compaction was raised to 85%.",
            reply_to="om_user_weather",
            metadata={"reply_to_message_id": "om_user_weather"},
        )

    result = asyncio.run(run())

    assert result.success is True
    assert result.message_id == "om_user_weather"
    assert adapter.text_sent == []
    assert len(posted) == 1
    payload = posted[0][1]
    assert payload["event"] == "system.notice"
    assert payload["message_id"] == "om_user_weather"
    assert payload["data"]["title"] == "上下文窗口提示"
    assert payload["data"]["notice_scope"] == "session"
    assert "auto-compaction" in payload["data"]["content"]
    assert posted[0][2] == hook_runtime.TERMINAL_TIMEOUT_SECONDS


def test_native_feishu_system_notice_send_warns_when_card_times_out(monkeypatch):
    attempts = []

    async def fake_post_json_ordered_response(url, payload, timeout):
        attempts.append((url, payload, timeout))
        raise TimeoutError("timed out")

    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:8765/events")
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_text")

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="查一下广州明天天气",
        message_id="om_user_weather",
        get_command=lambda: "",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)

    async def run():
        return await adapter.send(
            "oc_abc",
            "ℹ Codex gpt-5.5 caps context at 272K, so auto-compaction was raised to 85%.",
            reply_to="om_user_weather",
            metadata={"reply_to_message_id": "om_user_weather"},
        )

    result = asyncio.run(run())

    assert result.success is True
    assert result.message_id == "om_native_text"
    assert len(attempts) == 1
    assert adapter.text_sent == [
        (
            "oc_abc",
            "⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。",
            "om_user_weather",
            {"reply_to_message_id": "om_user_weather"},
        )
    ]


@pytest.mark.parametrize(
    ("error", "expected_content"),
    [
        ("delivery_outcome=not_sent", "原始系统通知"),
        (
            "delivery_outcome=unknown",
            "⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。",
        ),
        (
            "sidecar response invalid",
            "⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。",
        ),
    ],
)
def test_system_notice_delivery_outcome_selects_safe_native_fallback(
    monkeypatch,
    error,
    expected_content,
):
    calls = []

    class Adapter:
        pass

    async def original(self, chat_id, content, reply_to=None, metadata=None):
        calls.append((chat_id, content, reply_to, metadata))
        return SimpleNamespace(success=True, message_id="native-1", error="")

    async def failed_notice(self, **kwargs):
        return SimpleNamespace(success=False, message_id="", error=error)

    Adapter._hfc_original_send = original
    monkeypatch.setattr(hook_runtime, "_hfc_send_system_notice_card", failed_notice)
    monkeypatch.setattr(
        hook_runtime,
        "_hfc_classify_system_notice",
        lambda content: {"notice_kind": "system"},
    )

    result = asyncio.run(
        hook_runtime._hfc_send_with_native_command_result_card(
            Adapter(),
            "oc_test",
            "原始系统通知",
            reply_to="om_test",
            metadata={"thread_id": "omt_test"},
        )
    )

    assert result.success is True
    assert calls == [
        ("oc_test", expected_content, "om_test", {"thread_id": "omt_test"})
    ]


def test_system_notice_accepts_queued_existing_card_update():
    result = {
        "ok": True,
        "applied": True,
        "delivery": {"outcome": "accepted"},
    }

    assert hook_runtime._hfc_notice_delivery_outcome(result) == "accepted"
    assert hook_runtime._hfc_notice_post_applied(result) is True


def test_system_notice_rejects_accepted_outcome_without_applied_ack():
    result = {
        "ok": True,
        "delivery": {"outcome": "accepted"},
    }

    assert hook_runtime._hfc_notice_post_applied(result) is False


def test_system_notice_delivered_suppresses_native_fallback(monkeypatch):
    calls = []

    class Adapter:
        pass

    async def original(self, chat_id, content, reply_to=None, metadata=None):
        calls.append(content)
        return SimpleNamespace(success=True, message_id="native-1", error="")

    async def delivered_notice(self, **kwargs):
        return SimpleNamespace(success=True, message_id="card-1", error="")

    Adapter._hfc_original_send = original
    monkeypatch.setattr(hook_runtime, "_hfc_send_system_notice_card", delivered_notice)
    monkeypatch.setattr(
        hook_runtime,
        "_hfc_classify_system_notice",
        lambda content: {"notice_kind": "system"},
    )

    result = asyncio.run(
        hook_runtime._hfc_send_with_native_command_result_card(
            Adapter(),
            "oc_test",
            "原始系统通知",
        )
    )

    assert result.success is True
    assert result.message_id == "card-1"
    assert calls == []


def _install_background_notice_probe(
    monkeypatch,
    *,
    post_result=None,
    post_error=None,
    event=None,
):
    posted = []

    async def fake_post_json_ordered_response(url, payload, timeout):
        posted.append((url, payload, timeout))
        if post_error is not None:
            raise post_error
        if post_result is not None:
            return post_result
        return {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered"},
        }

    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_text")

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert (
        hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)
        is True
    )
    return adapter, posted


def test_background_process_notice_classification_and_stable_id():
    running = hook_runtime._hfc_classify_system_notice(
        "[Background process proc_109e6dc419af is still running~ "
        "New output:\nUpdating files: 76%]"
    )
    completed = hook_runtime._hfc_classify_system_notice(
        "[Background process proc_109e6dc419af finished with exit code 0~ "
        "Here's the final output:\nCloning into 'skills'...\n]"
    )
    failed = hook_runtime._hfc_classify_system_notice(
        "[Background process proc_109e6dc419af finished with exit code 17~ "
        "Here's the final output:\nReading skill failed\n]"
    )
    unknown = hook_runtime._hfc_classify_system_notice(
        "[Background process proc_109e6dc419af finished with exit code None~ "
        "Here's the final output:\n\n]"
    )
    killed = hook_runtime._hfc_classify_system_notice(
        "[Background process proc_109e6dc419af finished with exit code -9~ "
        "Here's the final output:\nkilled\n]"
    )
    another = hook_runtime._hfc_classify_system_notice(
        "[Background process proc_aaaaaaaaaaaa finished with exit code 0~ "
        "Here's the final output:\ndone\n]"
    )

    assert running == {
        "title": "后台进程运行中",
        "level": "info",
        "notice_kind": "background-process",
        "notice_id": "background-process:proc_109e6dc419af",
        "notice_terminal": False,
    }
    assert completed == {
        "title": "后台进程已完成",
        "level": "success",
        "notice_kind": "background-process",
        "notice_id": "background-process:proc_109e6dc419af",
        "notice_terminal": True,
    }
    assert failed == {
        "title": "后台进程失败",
        "level": "error",
        "notice_kind": "background-process",
        "notice_id": "background-process:proc_109e6dc419af",
        "notice_terminal": True,
    }
    assert unknown == {
        "title": "后台进程已结束",
        "level": "warning",
        "notice_kind": "background-process",
        "notice_id": "background-process:proc_109e6dc419af",
        "notice_terminal": True,
    }
    assert killed == failed
    assert another is not None
    assert another["notice_id"] == "background-process:proc_aaaaaaaaaaaa"
    independent_ids = {
        hook_runtime._hfc_independent_notice_message_id(
            "oc_abc",
            content,
            notice,
        )
        for content, notice in (
            ("running", running),
            ("completed", completed),
            ("failed", failed),
            ("unknown", unknown),
            ("killed", killed),
        )
    }
    assert len(independent_ids) == 1


def test_long_running_heartbeat_notice_is_non_terminal():
    notice = hook_runtime._hfc_classify_system_notice(
        "⏳ Working — 6 min — iteration 10/90, "
        "waiting for provider response (streaming)"
    )

    assert notice == {
        "title": "运行中",
        "level": "info",
        "notice_kind": "heartbeat",
        "notice_id": "heartbeat",
        "notice_terminal": False,
    }


def test_long_running_heartbeat_reuses_independent_message_id_per_anchor():
    first = "⏳ Working — 6 min — iteration 10/90, terminal"
    second = "⏳ Working — 9 min — iteration 14/90, terminal"
    first_notice = hook_runtime._hfc_classify_system_notice(first)
    second_notice = hook_runtime._hfc_classify_system_notice(second)

    assert first_notice is not None
    assert second_notice is not None
    first_message_id = hook_runtime._hfc_independent_notice_message_id(
        "oc_abc", first, first_notice, anchor="om_task_1"
    )
    assert first_message_id == hook_runtime._hfc_independent_notice_message_id(
        "oc_abc", second, second_notice, anchor="om_task_1"
    )
    assert first_message_id != hook_runtime._hfc_independent_notice_message_id(
        "oc_abc", second, second_notice, anchor="om_task_2"
    )


@pytest.mark.parametrize(
    (
        "content",
        "expected_title",
        "expected_level",
        "expected_kind",
        "expected_terminal",
    ),
    [
        (
            "[Background process proc_111111111111 is still running~ "
            "New output:\nUpdating files: 76%]",
            "后台进程运行中",
            "info",
            "background-process",
            False,
        ),
        (
            "[Background process proc_222222222222 finished with exit code 0~ "
            "Here's the final output:\nCloning into 'skills'...\n]",
            "后台进程已完成",
            "success",
            "background-process",
            True,
        ),
        (
            "[Background process proc_333333333333 finished with exit code 9~ "
            "Here's the final output:\nfatal: repository not found\n]",
            "后台进程失败",
            "error",
            "background-process",
            True,
        ),
        (
            '✅ Background task complete\nPrompt: "Clone repositories"\n\nDone.',
            "后台任务已完成",
            "success",
            "background-task",
            True,
        ),
        (
            "❌ Background task bg_123456_abcdef failed: provider timeout",
            "后台任务失败",
            "error",
            "background-task",
            True,
        ),
    ],
)
def test_background_direct_send_uses_system_notice_card(
    monkeypatch,
    content,
    expected_title,
    expected_level,
    expected_kind,
    expected_terminal,
):
    adapter, posted = _install_background_notice_probe(monkeypatch)

    async def run():
        return await adapter.send(
            "oc_topic",
            content,
            metadata={"thread_id": "omt_topic"},
        )

    result = asyncio.run(run())

    assert result.success is True
    assert adapter.text_sent == []
    assert len(posted) == 1
    _, payload, timeout = posted[0]
    assert timeout == hook_runtime.TERMINAL_TIMEOUT_SECONDS
    assert result.message_id == payload["message_id"]
    assert payload["event"] == "system.notice"
    assert payload["conversation_id"] == "omt_topic"
    assert payload["thread_id"] == "omt_topic"
    assert payload["data"]["notice_scope"] == "independent"
    assert payload["data"]["title"] == expected_title
    assert payload["data"]["level"] == expected_level
    assert payload["data"]["notice_kind"] == expected_kind
    assert payload["data"]["notice_terminal"] is expected_terminal
    assert payload["data"]["notice_id"]
    assert payload["data"]["content"] == content


def test_background_task_started_notice_reuses_anchored_card_without_native_text(
    monkeypatch,
):
    event = SimpleNamespace(
        source=SimpleNamespace(
            platform="feishu",
            chat_id="oc_topic",
            message_id="om_background_request",
            thread_id="omt_topic",
        ),
        message_id="om_background_request",
        thread_id="omt_topic",
    )
    adapter, posted = _install_background_notice_probe(monkeypatch, event=event)
    started = (
        '🔄 Background task started: "Check the release"\n'
        "Task ID: bg_123456_abcdef\n"
        "You can keep chatting — results will appear when done."
    )
    completed = (
        '✅ Background task complete\nPrompt: "Check the release"\n\n'
        "V4.0.6 background private ok"
    )

    async def run():
        first = await adapter.send(
            "oc_topic",
            started,
            metadata={"thread_id": "omt_topic"},
        )
        second = await adapter.send(
            "oc_topic",
            completed,
            metadata={"thread_id": "omt_topic"},
        )
        return first, second

    first, second = asyncio.run(run())

    assert first.success is True
    assert second.success is True
    assert adapter.text_sent == []
    assert len(posted) == 2
    started_payload = posted[0][1]
    completed_payload = posted[1][1]
    assert started_payload["event"] == "system.notice"
    assert started_payload["message_id"] == "om_background_request"
    assert completed_payload["message_id"] == "om_background_request"
    assert started_payload["data"]["content"] == started
    assert started_payload["data"]["title"] == "后台任务已启动"
    assert started_payload["data"]["level"] == "info"
    assert started_payload["data"]["notice_kind"] == "background-task"
    assert (
        started_payload["data"]["notice_id"]
        == "background-task:bg_123456_abcdef"
    )
    assert started_payload["data"]["notice_scope"] == "independent"
    assert started_payload["data"]["notice_terminal"] is False
    assert completed_payload["data"]["notice_scope"] == "independent"
    assert completed_payload["data"]["notice_terminal"] is True


def test_identical_background_task_results_use_distinct_independent_message_ids(
    monkeypatch,
):
    adapter, posted = _install_background_notice_probe(monkeypatch)
    content = '✅ Background task complete\nPrompt: "Clone repositories"\n\nDone.'

    async def run():
        first = await adapter.send("oc_abc", content)
        second = await adapter.send("oc_abc", content)
        return first, second

    first, second = asyncio.run(run())

    assert first.success is True
    assert second.success is True
    assert first.message_id != second.message_id
    assert adapter.text_sent == []
    assert len(posted) == 2
    first_payload = posted[0][1]
    second_payload = posted[1][1]
    assert first_payload["data"]["notice_id"] == second_payload["data"]["notice_id"]
    assert first_payload["message_id"] != second_payload["message_id"]


def test_background_notice_timeout_uses_uncertain_warning(monkeypatch):
    adapter, posted = _install_background_notice_probe(
        monkeypatch,
        post_error=TimeoutError("timed out"),
    )
    content = (
        "[Background process proc_444444444444 finished with exit code 0~ "
        "Here's the final output:\ndone\n]"
    )

    result = asyncio.run(adapter.send("oc_abc", content))

    assert result.success is True
    assert result.message_id == "om_native_text"
    assert len(posted) == 1
    assert adapter.text_sent == [
        (
            "oc_abc",
            "⚠️ 一条运行提示的卡片投递结果无法确认，请稍后查看 /hfc status。",
            None,
            None,
        )
    ]


@pytest.mark.parametrize(
    "content",
    [
        "[Background process proc_abc finished somehow]",
        "[Background process proc_555555555555 finished with exit code zero~ "
        "Here's the final output:\ndone\n]",
        "[Background process proc_555555555555 finished with exit code 0~ "
        "Here's the final output:\ndone\n] trailing text",
        "ordinary prefix [Background process proc_555555555555 is still running~ "
        "New output:\n42%]",
        "✅ Background task",
        "✅ Background task complete\nordinary text without a Prompt envelope",
        '🔄 Background task started: "Check the release"\nTask ID: bad-id\n'
        "You can keep chatting — results will appear when done.",
        '🔄 Background task started: "Check the release"\n'
        "Task ID: bg_123456_abcdef\n"
        "You can keep chatting — results will appear when done. trailing text",
        "❌ Background task failed without an id",
    ],
)
def test_malformed_background_notice_fails_open(monkeypatch, content):
    adapter, posted = _install_background_notice_probe(monkeypatch)

    result = asyncio.run(adapter.send("oc_abc", content))

    assert result.success is True
    assert result.message_id == "om_native_text"
    assert posted == []
    assert adapter.text_sent == [("oc_abc", content, None, None)]


def test_gateway_platform_notice_posts_sidecar_and_suppresses_native_text(monkeypatch):
    posted = []

    async def fake_post_json_ordered_response(url, payload, timeout):
        posted.append((url, payload, timeout))
        return {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered"},
        }

    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:8765/events")
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_notice")

    class DummyGatewayRunner:
        def __init__(self, adapter):
            self.adapters = {"feishu": adapter}
            self.native_notices = []

        async def _deliver_platform_notice(self, source, content):
            self.native_notices.append((source, content))
            return await self.adapters["feishu"].send(source.chat_id, content)

    adapter = DummyFeishuAdapter()
    runner = DummyGatewayRunner(adapter)
    source = SimpleNamespace(
        platform="feishu",
        chat_id="oc_topic",
        message_id="om_topic_user",
        thread_id="omt_topic",
    )

    installed = hook_runtime.install_feishu_command_card_adapter_methods(runner)

    async def run():
        result = await runner._deliver_platform_notice(
            source,
            "ℹ️ Codex gpt-5.5 caps context at 272K, so auto-compaction was raised to 85%.",
        )
        await drain_tasks()
        return result

    result = asyncio.run(run())

    assert installed is True
    assert result.success is True
    assert result.message_id == "om_topic_user"
    assert adapter.text_sent == []
    assert runner.native_notices == []
    assert len(posted) == 1
    url, payload, timeout = posted[0]
    assert url == "http://127.0.0.1:8765/events"
    assert timeout == hook_runtime.TERMINAL_TIMEOUT_SECONDS
    assert payload["event"] == "system.notice"
    assert payload["chat_id"] == "oc_topic"
    assert payload["message_id"] == "om_topic_user"
    assert payload["thread_id"] == "omt_topic"
    assert payload["conversation_id"] == "omt_topic"
    assert payload["data"]["notice_scope"] == "session"
    assert payload["data"]["reply_to_message_id"] == "om_topic_user"


def test_handle_platform_notice_from_hermes_schedules_card(monkeypatch):
    posted = []

    async def fake_post_json_ordered_response(url, payload, timeout):
        posted.append((url, payload, timeout))
        return {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered"},
        }

    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:8765/events")
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )

    class DummyFeishuAdapter:
        name = "feishu"

    class DummyGatewayRunner:
        def __init__(self, adapter):
            self.adapters = {"feishu": adapter}

    source = SimpleNamespace(
        platform="feishu",
        chat_id="oc_topic",
        message_id="om_topic_user",
        thread_id="omt_topic",
    )

    handled = hook_runtime.handle_platform_notice_from_hermes(
        DummyGatewayRunner(DummyFeishuAdapter()),
        source,
        "ℹ️ Codex gpt-5.5 caps context at 272K, so auto-compaction was raised to 85%.",
    )

    assert handled is True
    assert len(posted) == 1
    _, payload, timeout = posted[0]
    assert timeout == hook_runtime.TERMINAL_TIMEOUT_SECONDS
    assert payload["event"] == "system.notice"
    assert payload["chat_id"] == "oc_topic"
    assert payload["message_id"] == "om_topic_user"
    assert payload["thread_id"] == "omt_topic"


def test_gateway_platform_notice_suppresses_native_text_when_card_attempt_fails(monkeypatch):
    attempts = []

    async def fake_post_json_ordered_response(url, payload, timeout):
        attempts.append((url, payload, timeout))
        raise TimeoutError("timed out")

    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://127.0.0.1:8765/events")
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_notice")

    class DummyGatewayRunner:
        def __init__(self, adapter):
            self.adapters = {"feishu": adapter}
            self.native_notices = []

        async def _deliver_platform_notice(self, source, content):
            self.native_notices.append((source, content))
            return await self.adapters["feishu"].send(source.chat_id, content)

    adapter = DummyFeishuAdapter()
    runner = DummyGatewayRunner(adapter)
    source = SimpleNamespace(
        platform="feishu",
        chat_id="oc_topic",
        message_id="om_topic_user",
        thread_id="omt_topic",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner)

    async def run():
        result = await runner._deliver_platform_notice(
            source,
            "ℹ️ Codex gpt-5.5 caps context at 272K, so auto-compaction was raised to 85%.",
        )
        await drain_tasks()
        return result

    result = asyncio.run(run())

    assert result.success is True
    assert result.message_id == "om_topic_user"
    assert len(attempts) == 1
    assert runner.native_notices == []
    assert adapter.text_sent == []


def test_gateway_platform_notice_falls_back_for_non_system_notice(monkeypatch):
    posted = []

    async def fake_post_json_ordered_response(url, payload, timeout):
        posted.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_notice")

    class DummyGatewayRunner:
        def __init__(self, adapter):
            self.adapters = {"feishu": adapter}
            self.native_notices = []

        async def _deliver_platform_notice(self, source, content):
            self.native_notices.append((source, content))
            return await self.adapters["feishu"].send(source.chat_id, content)

    adapter = DummyFeishuAdapter()
    runner = DummyGatewayRunner(adapter)
    source = SimpleNamespace(
        platform="feishu",
        chat_id="oc_topic",
        message_id="om_topic_user",
        thread_id="omt_topic",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner)

    async def run():
        return await runner._deliver_platform_notice(source, "ordinary native notice")

    result = asyncio.run(run())

    assert result.success is True
    assert result.message_id == "om_native_notice"
    assert posted == []
    assert runner.native_notices == [(source, "ordinary native notice")]
    assert adapter.text_sent == [
        ("oc_topic", "ordinary native notice", None, None),
    ]


@pytest.mark.parametrize(
    ("content", "expected_title"),
    [
        ("📚 Reading skill hermes-agent", "技能加载"),
        ("💾 Self-improvement review: Memory updated", "自我改进"),
    ],
)
def test_native_feishu_system_notice_retries_as_independent_card_when_session_missing(
    monkeypatch,
    content,
    expected_title,
):
    posted = []

    async def fake_post_json_ordered_response(url, payload, timeout):
        posted.append(payload)
        if len(posted) == 1:
            return {"ok": True, "applied": False}
        return {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered"},
        }

    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append(content)
            return SimpleNamespace(success=True, message_id="om_native_text")

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="查一下广州明天天气",
        message_id="om_user_weather",
        get_command=lambda: "",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)

    async def run():
        return await adapter.send(
            "oc_abc",
            content,
            reply_to="om_user_weather",
        )

    result = asyncio.run(run())

    assert result.success is True
    assert adapter.text_sent == []
    assert len(posted) == 2
    assert posted[0]["message_id"] == "om_user_weather"
    assert posted[0]["data"]["notice_scope"] == "session"
    assert posted[1]["message_id"].startswith("notice_")
    assert posted[1]["data"]["notice_scope"] == "independent"
    assert posted[1]["data"]["delivery_kind"] == "notice"
    assert posted[1]["data"]["title"] == expected_title
    assert result.message_id == posted[1]["message_id"]


def test_native_feishu_system_notice_edit_updates_same_card(monkeypatch):
    posted = []

    async def fake_post_json_ordered_response(url, payload, timeout):
        posted.append(payload)
        return {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered"},
        }

    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []
            self.edited = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append(content)
            return SimpleNamespace(success=True, message_id="om_native_text")

        async def edit_message(self, chat_id, message_id, content, metadata=None):
            self.edited.append((chat_id, message_id, content, metadata))
            return SimpleNamespace(success=True, message_id=message_id)

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="安装 ripgrep",
        message_id="om_user_task",
        get_command=lambda: "",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)

    async def run():
        sent = await adapter.send(
            "oc_abc",
            "⏳ Working — 2 min — iteration 1/90, terminal",
            reply_to="om_user_task",
        )
        edited = await adapter.edit_message(
            "oc_abc",
            sent.message_id,
            "⏳ Working — 3 min — iteration 2/90, terminal",
        )
        return sent, edited

    sent, edited = asyncio.run(run())

    assert sent.message_id == "om_user_task"
    assert edited.message_id == "om_user_task"
    assert adapter.text_sent == []
    assert adapter.edited == []
    assert len(posted) == 2
    assert posted[0]["message_id"] == posted[1]["message_id"] == "om_user_task"
    assert posted[0]["data"]["notice_id"] == posted[1]["data"]["notice_id"]
    assert "iteration 2/90" in posted[1]["data"]["content"]


def test_heartbeat_after_unknown_delivery_reuses_independent_card(monkeypatch):
    posted = []
    responses = [
        {"ok": True, "applied": False},
        {
            "ok": False,
            "error": "feishu send failed",
            "delivery": {"outcome": "unknown"},
        },
        {"ok": True, "applied": False},
        {
            "ok": True,
            "applied": True,
            "delivery": {"outcome": "delivered"},
        },
    ]

    async def fake_post_json_ordered_response(url, payload, timeout):
        posted.append(payload)
        return responses.pop(0)

    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_post_json_ordered_response,
    )

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.text_sent = []
            self.edited = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.text_sent.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_warning")

        async def edit_message(self, chat_id, message_id, content, metadata=None):
            self.edited.append((chat_id, message_id, content, metadata))
            return SimpleNamespace(success=True, message_id=message_id)

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    event = SimpleNamespace(
        source=SimpleNamespace(platform="feishu", chat_id="oc_abc"),
        text="执行长任务",
        message_id="om_user_task",
        get_command=lambda: "",
    )
    hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)

    async def run():
        sent = await adapter.send(
            "oc_abc",
            "⏳ Working — 6 min — iteration 10/90, terminal",
            reply_to="om_user_task",
        )
        edited = await adapter.edit_message(
            "oc_abc",
            sent.message_id,
            "⏳ Working — 9 min — iteration 14/90, terminal",
        )
        return sent, edited

    sent, edited = asyncio.run(run())

    assert sent.message_id == "om_native_warning"
    assert edited.message_id.startswith("notice_")
    assert len(posted) == 4
    independent = [
        payload
        for payload in posted
        if payload["data"]["notice_scope"] == "independent"
    ]
    assert len(independent) == 2
    assert independent[0]["message_id"] == independent[1]["message_id"]


def test_install_feishu_command_card_methods_repairs_stale_install_marker():
    class DummyFeishuAdapter:
        name = "feishu"
        _hfc_command_card_methods_installed = True

        def __init__(self):
            self._client = object()
            self.sent = None

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent = kwargs
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id="om_slash_card"),
            )

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})

    installed = hook_runtime.install_feishu_command_card_adapter_methods(runner)

    assert installed is True
    assert callable(getattr(adapter, "send_slash_confirm", None))

    async def run():
        return await adapter.send_slash_confirm(
            chat_id="oc_abc",
            title="Confirm /new",
            message="This starts a fresh session.",
            session_key="feishu:oc_abc",
            confirm_id="cf-1",
            metadata=None,
        )

    result = asyncio.run(run())

    assert result.success is True
    assert adapter.sent["msg_type"] == "interactive"


def test_existing_slash_confirm_is_wrapped_and_native_calls_original_once(
    monkeypatch,
):
    original_calls = []
    sentinel = object()

    class DummyFeishuAdapter:
        name = "feishu"

        async def send_slash_confirm(
            self,
            chat_id,
            title,
            message,
            session_key,
            confirm_id,
            metadata=None,
        ):
            original_calls.append(
                (chat_id, title, message, session_key, confirm_id, metadata)
            )
            return sentinel

    monkeypatch.setattr(
        hook_runtime,
        "_fetch_delivery_policy_sync",
        lambda *_args, **_kwargs: {
            "ok": True,
            "disposition": "native",
            "ttl_ms": 1000,
        },
    )
    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    assert type(adapter).send_slash_confirm is hook_runtime._hfc_send_native_slash_confirm
    assert callable(type(adapter).__dict__.get("_hfc_original_send_slash_confirm"))

    result = asyncio.run(
        adapter.send_slash_confirm(
            "oc_native_original",
            "Confirm",
            "Details",
            "session-1",
            "confirm-1",
            metadata={"reply_to_message_id": "om_reply"},
        )
    )

    assert result is sentinel
    assert original_calls == [
        (
            "oc_native_original",
            "Confirm",
            "Details",
            "session-1",
            "confirm-1",
            {"reply_to_message_id": "om_reply"},
        )
    ]


def test_inherited_slash_confirm_is_preserved_for_native_policy(monkeypatch):
    original_calls = []
    sentinel = object()

    class BaseAdapter:
        async def send_slash_confirm(self, *args, **kwargs):
            original_calls.append((args, kwargs))
            return sentinel

    class DummyFeishuAdapter(BaseAdapter):
        name = "feishu"

    monkeypatch.setattr(
        hook_runtime,
        "_fetch_delivery_policy_sync",
        lambda *_args, **_kwargs: {
            "ok": True,
            "disposition": "native",
            "ttl_ms": 1000,
        },
    )
    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    result = asyncio.run(
        adapter.send_slash_confirm(
            "oc_inherited",
            "Confirm",
            "Details",
            "session-2",
            "confirm-2",
        )
    )

    assert result is sentinel
    assert len(original_calls) == 1


def test_existing_model_and_resume_pickers_call_original_for_native_policy(
    monkeypatch,
):
    model_calls = []
    resume_calls = []
    model_sentinel = object()
    resume_sentinel = object()

    class DummyFeishuAdapter:
        name = "feishu"

        async def send_model_picker(self, *args, **kwargs):
            model_calls.append((args, kwargs))
            return model_sentinel

        async def send_resume_picker(self, **kwargs):
            resume_calls.append(kwargs)
            return resume_sentinel

    monkeypatch.setattr(
        hook_runtime,
        "_fetch_delivery_policy_sync",
        lambda *_args, **_kwargs: {
            "ok": True,
            "disposition": "native",
            "ttl_ms": 1000,
        },
    )
    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    event = object()
    original_handler = object()

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    assert type(adapter).send_model_picker is hook_runtime._hfc_send_native_model_picker
    assert type(adapter).send_resume_picker is hook_runtime._hfc_send_native_resume_picker
    assert callable(type(adapter).__dict__.get("_hfc_original_send_model_picker"))
    assert callable(type(adapter).__dict__.get("_hfc_original_send_resume_picker"))
    model_result = asyncio.run(
        adapter.send_model_picker(
            "oc_model_native",
            [{"slug": "provider", "models": ["model"]}],
            current_model="model",
            current_provider="provider",
            session_key="session-model",
            on_model_selected=None,
            metadata={"k": "v"},
        )
    )
    resume_result = asyncio.run(
        adapter.send_resume_picker(
            chat_id="oc_resume_native",
            sessions=[{"id": "session-1", "title": "One"}],
            current_session_id="session-1",
            runner=runner,
            event=event,
            original_handler=original_handler,
            metadata={"reply_to_message_id": "om_resume"},
        )
    )

    assert model_result is model_sentinel
    assert resume_result is resume_sentinel
    assert len(model_calls) == 1
    assert resume_calls == [
        {
            "chat_id": "oc_resume_native",
            "sessions": [{"id": "session-1", "title": "One"}],
            "current_session_id": "session-1",
            "runner": runner,
            "event": event,
            "original_handler": original_handler,
            "metadata": {"reply_to_message_id": "om_resume"},
        }
    ]


def test_existing_slash_confirm_uses_hfc_card_for_card_policy():
    original_calls = []

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.sent = []

        async def send_slash_confirm(self, *args, **kwargs):
            original_calls.append((args, kwargs))
            return object()

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent.append(kwargs)
            return SimpleNamespace(success=True, message_id="om_hfc_confirm")

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    result = asyncio.run(
        adapter.send_slash_confirm(
            "oc_card_policy",
            "Confirm",
            "Details",
            "session-card",
            "confirm-card",
        )
    )

    assert result.success is True
    assert result.message_id == "om_hfc_confirm"
    assert original_calls == []
    assert len(adapter.sent) == 1
    assert adapter.sent[0]["msg_type"] == "interactive"


def test_invalid_profile_delivery_context_keeps_direct_command_and_notice_native(
    monkeypatch,
):
    policy_calls = []
    original_calls = []

    def unexpected_policy(*_args, **_kwargs):
        policy_calls.append(True)
        return {"ok": True, "disposition": "card", "ttl_ms": 1000}

    class DummyFeishuAdapter:
        name = "feishu"

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            original_calls.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native_direct")

    source = SimpleNamespace(
        platform="feishu",
        chat_id="oc_invalid_direct",
        message_id="om_invalid_direct",
        profile_id="../work",
    )
    event = SimpleNamespace(
        source=source,
        message_id="om_invalid_direct",
        get_command=lambda: "/new",
    )
    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    monkeypatch.setattr(
        hook_runtime,
        "_fetch_delivery_policy_sync",
        unexpected_policy,
    )

    assert hook_runtime.install_feishu_command_card_adapter_methods(
        runner,
        event=event,
    )
    context = hook_runtime._HFC_FEISHU_DELIVERY_CONTEXT.get()
    assert context["profile_invalid"] is True

    command_result = asyncio.run(
        adapter.send("oc_invalid_direct", "Session reset", reply_to="om_reply")
    )
    notice_result = asyncio.run(
        adapter.send(
            "oc_invalid_direct",
            "⏳ Working — 1 min — terminal",
            reply_to="om_reply",
        )
    )

    assert command_result.success is True
    assert notice_result.success is True
    assert policy_calls == []
    assert original_calls == [
        ("oc_invalid_direct", "Session reset", "om_reply", None),
        (
            "oc_invalid_direct",
            "⏳ Working — 1 min — terminal",
            "om_reply",
            None,
        ),
    ]


def test_install_feishu_command_card_methods_refreshes_live_callback_without_replacing_handler():
    class FakeWsLoop:
        def __init__(self):
            self.callbacks = []

        def is_closed(self):
            return False

        def call_soon_threadsafe(self, callback):
            self.callbacks.append(callback)

    class CardActionProcessor:
        def __init__(self, callback):
            self.f = callback

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self._event_handler = SimpleNamespace(
                _callback_processor_map={
                    "p2.card.action.trigger": CardActionProcessor(
                        self._on_card_action_trigger
                    )
                }
            )
            self._ws_client = SimpleNamespace(_event_handler=self._event_handler)
            self._ws_thread_loop = FakeWsLoop()
            self.rebuild_count = 0

        def _on_card_action_trigger(self, data):
            return "original"

        async def _handle_card_action_event(self, data):
            return None

        def _build_event_handler(self):
            self.rebuild_count += 1
            return SimpleNamespace(
                name="rebuilt",
                callback=getattr(self, "_on_card_action_trigger"),
            )

    adapter = DummyFeishuAdapter()
    live_handler = adapter._event_handler
    original_callback = live_handler._callback_processor_map[
        "p2.card.action.trigger"
    ].f
    runner = SimpleNamespace(adapters={"feishu": adapter})

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True

    assert adapter.rebuild_count == 0
    assert adapter._event_handler is live_handler
    assert adapter._ws_client._event_handler is live_handler
    assert len(adapter._ws_thread_loop.callbacks) == 1
    assert live_handler._callback_processor_map["p2.card.action.trigger"].f is original_callback

    adapter._ws_thread_loop.callbacks[0]()

    refreshed_callback = live_handler._callback_processor_map[
        "p2.card.action.trigger"
    ].f
    assert refreshed_callback.__func__ is hook_runtime._hfc_on_feishu_card_action_trigger


def test_refresh_feishu_event_handler_fails_open_when_ws_loop_state_raises():
    class BrokenWsLoop:
        def is_closed(self):
            raise RuntimeError("loop state unavailable")

        def call_soon_threadsafe(self, callback):
            raise AssertionError("callback must not be scheduled")

    live_handler = SimpleNamespace(_callback_processor_map={})
    adapter = SimpleNamespace(
        _event_handler=live_handler,
        _ws_client=SimpleNamespace(_event_handler=live_handler),
        _ws_thread_loop=BrokenWsLoop(),
        _on_card_action_trigger=lambda data: data,
    )

    assert hook_runtime._hfc_refresh_feishu_event_handler(adapter) is False
    assert adapter._event_handler is live_handler
    assert adapter._ws_client._event_handler is live_handler


def test_feishu_command_card_action_resolves_native_slash_confirm_in_background(monkeypatch):
    class FakeCallBackCard:
        def __init__(self):
            self.type = None
            self.data = None

    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = SimpleNamespace(
                im=SimpleNamespace(
                    v1=SimpleNamespace(message=SimpleNamespace(update=lambda request: None))
                )
            )
            self._loop = object()
            self._allowed_group_users = {"ou_user"}
            self.updated = None
            self.sent = None

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return sender_id.open_id == "ou_user" and chat_id == "oc_abc"

        def _get_cached_sender_name(self, open_id):
            return "Bailey" if open_id == "ou_user" else ""

        def _submit_on_loop(self, loop, coro):
            assert loop is self._loop
            asyncio.run(coro)
            return True

        def _build_update_message_body(self, *, msg_type, content):
            return SimpleNamespace(msg_type=msg_type, content=content)

        def _build_update_message_request(self, message_id, request_body):
            return SimpleNamespace(message_id=message_id, request_body=request_body)

        async def _run_blocking(self, func, request):
            self.updated = request
            return SimpleNamespace(success=lambda: True)

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent = kwargs
            return SimpleNamespace(success=True, message_id="om_fallback")

        def _on_card_action_trigger(self, data):
            return "original"

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(hook_runtime, "CallBackCard", FakeCallBackCard, raising=False)

    resolved = []
    slash_confirm_module = types.ModuleType("tools.slash_confirm")

    async def fake_resolve(session_key, confirm_id, choice):
        resolved.append((session_key, confirm_id, choice))
        return "New session started."

    def fake_resolve_sync_compat(loop, session_key, confirm_id, choice):
        resolved.append((loop, session_key, confirm_id, choice))
        return "New session started."

    slash_confirm_module.resolve = fake_resolve
    slash_confirm_module.resolve_sync_compat = fake_resolve_sync_compat
    tools_module = types.ModuleType("tools")
    tools_module.slash_confirm = slash_confirm_module
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.slash_confirm", slash_confirm_module)

    adapter = DummyFeishuAdapter()
    adapter._hfc_slash_confirm_state = {
        "cf-1": {
            "session_key": "feishu:oc_abc",
            "chat_id": "oc_abc",
            "message_id": "om_slash_card",
        }
    }
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True

    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "slash_confirm",
                    "hfc_confirm_id": "cf-1",
                    "hfc_choice": "once",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user", user_id="u_1"),
        )
    )

    response = adapter._on_card_action_trigger(data)

    # The callback returns an empty ack immediately (within Feishu's 3s
    # callback timeout) instead of blocking on the slow confirm handler.
    assert resolved == [("feishu:oc_abc", "cf-1", "once")]
    assert "cf-1" not in adapter._hfc_slash_confirm_state
    assert response.card is None
    # The result card is pushed via a background PATCH update instead of the
    # callback response.
    assert adapter.updated is not None
    card = json.loads(adapter.updated.request_body.content)
    assert card["header"]["template"] == "green"
    assert "允许一次" in card["header"]["title"]["content"]
    assert "New session started." in card["elements"][0]["content"]


def test_feishu_command_card_action_slash_confirm_fallback_sends_result_when_patch_fails(monkeypatch):
    """When the background PATCH update fails, a result card is still sent.

    The confirm must never be lost silently: if the original message cannot
    be updated (e.g. message too old, update_multi not honoured), the result
    card is delivered as a new follow-up message.
    """
    class FakeCallBackCard:
        def __init__(self):
            self.type = None
            self.data = None

    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = SimpleNamespace(
                im=SimpleNamespace(
                    v1=SimpleNamespace(message=SimpleNamespace(update=lambda request: None))
                )
            )
            self._loop = object()
            self._allowed_group_users = {"ou_user"}
            self.sent = None

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return sender_id.open_id == "ou_user" and chat_id == "oc_abc"

        def _get_cached_sender_name(self, open_id):
            return "Bailey" if open_id == "ou_user" else ""

        def _submit_on_loop(self, loop, coro):
            assert loop is self._loop
            asyncio.run(coro)
            return True

        def _build_update_message_body(self, *, msg_type, content):
            return SimpleNamespace(msg_type=msg_type, content=content)

        def _build_update_message_request(self, message_id, request_body):
            return SimpleNamespace(message_id=message_id, request_body=request_body)

        async def _run_blocking(self, func, request):
            # PATCH update fails.
            return SimpleNamespace(success=lambda: False)

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent = kwargs
            return SimpleNamespace(success=True, message_id="om_result_card")

        def _on_card_action_trigger(self, data):
            return "original"

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(hook_runtime, "CallBackCard", FakeCallBackCard, raising=False)

    async def fake_resolve(session_key, confirm_id, choice):
        return "New session started."

    slash_confirm_module = types.ModuleType("tools.slash_confirm")
    slash_confirm_module.resolve = fake_resolve
    tools_module = types.ModuleType("tools")
    tools_module.slash_confirm = slash_confirm_module
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.slash_confirm", slash_confirm_module)

    adapter = DummyFeishuAdapter()
    adapter._hfc_slash_confirm_state = {
        "cf-1": {
            "session_key": "feishu:oc_abc",
            "chat_id": "oc_abc",
            "message_id": "om_slash_card",
        }
    }
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True

    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "slash_confirm",
                    "hfc_confirm_id": "cf-1",
                    "hfc_choice": "once",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user", user_id="u_1"),
        )
    )

    response = adapter._on_card_action_trigger(data)

    assert response.card is None
    assert "cf-1" not in adapter._hfc_slash_confirm_state
    assert adapter.sent is not None
    assert adapter.sent["chat_id"] == "oc_abc"
    assert adapter.sent["msg_type"] == "interactive"
    assert adapter.sent["reply_to"] == "om_slash_card"
    card = json.loads(adapter.sent["payload"])
    assert card["header"]["template"] == "green"
    assert "New session started." in card["elements"][0]["content"]


def test_stale_feishu_card_action_handler_resolves_native_slash_confirm(monkeypatch):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = SimpleNamespace(
                im=SimpleNamespace(
                    v1=SimpleNamespace(message=SimpleNamespace(update=lambda request: None))
                )
            )
            self._loop = object()
            self._allowed_group_users = {"ou_user"}
            self.updated = None
            self.routed = []

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return sender_id.open_id == "ou_user" and chat_id == "oc_abc"

        def _get_cached_sender_name(self, open_id):
            return "Bailey" if open_id == "ou_user" else ""

        def _on_card_action_trigger(self, data):
            self._submit_on_loop(self._loop, self._handle_card_action_event(data))
            return "empty"

        def _submit_on_loop(self, loop, coro):
            assert loop is self._loop
            asyncio.run(coro)
            return True

        async def _handle_card_action_event(self, data):
            self.routed.append(data)

        def _build_update_message_body(self, *, msg_type, content):
            return SimpleNamespace(msg_type=msg_type, content=content)

        def _build_update_message_request(self, message_id, request_body):
            return SimpleNamespace(message_id=message_id, request_body=request_body)

        async def _run_blocking(self, func, request):
            self.updated = request
            return SimpleNamespace(success=lambda: True)

    DummyFeishuAdapter.__module__ = hook_runtime.__name__

    resolved = []
    slash_confirm_module = types.ModuleType("tools.slash_confirm")

    async def fake_resolve(session_key, confirm_id, choice):
        resolved.append((session_key, confirm_id, choice))
        return "New session started."

    def fail_resolve_sync_compat(loop, session_key, confirm_id, choice):
        raise AssertionError("stale Feishu card action path must use async resolve")

    slash_confirm_module.resolve = fake_resolve
    slash_confirm_module.resolve_sync_compat = fail_resolve_sync_compat
    tools_module = types.ModuleType("tools")
    tools_module.slash_confirm = slash_confirm_module
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.slash_confirm", slash_confirm_module)

    adapter = DummyFeishuAdapter()
    stale_handler = adapter._on_card_action_trigger
    adapter._hfc_slash_confirm_state = {
        "cf-1": {
            "session_key": "feishu:oc_abc",
            "chat_id": "oc_abc",
            "message_id": "om_slash_card",
        }
    }
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True

    data = SimpleNamespace(
        event=SimpleNamespace(
            token="tok-slash-1",
            action=SimpleNamespace(
                tag="button",
                value={
                    "hfc_action": "slash_confirm",
                    "hfc_confirm_id": "cf-1",
                    "hfc_choice": "once",
                },
            ),
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user", user_id="u_1"),
        )
    )

    assert stale_handler(data) == "empty"

    assert adapter.routed == []
    assert resolved == [("feishu:oc_abc", "cf-1", "once")]
    assert "cf-1" not in adapter._hfc_slash_confirm_state
    assert adapter.updated is None


def test_install_feishu_command_card_methods_adds_model_picker(monkeypatch):
    class DummyFeishuAdapter:
        name = "feishu"

    posted = []
    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    async def fake_post(url, payload, timeout):
        posted.append((url, payload, timeout))
        return {"ok": True, "applied": True, "interaction_mode": "card"}

    async def fake_get(url, timeout):
        assert url.startswith("http://sidecar.test/interactions/model_")
        requested_payload = posted[0][1]
        option_value = requested_payload["data"]["options"][0]["value"]
        return {
            "ok": True,
            "status": "completed",
            "interaction_id": requested_payload["data"]["interaction_id"],
            "choice": option_value,
            "choice_label": requested_payload["data"]["options"][0]["label"],
        }

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post)
    monkeypatch.setattr(hook_runtime, "_get_json", fake_get)

    installed = hook_runtime.install_feishu_command_card_adapter_methods(runner)

    selected = []

    async def on_model_selected(chat_id, model_id, provider_slug):
        selected.append((chat_id, model_id, provider_slug))
        return f"Switched to {provider_slug}/{model_id}"

    async def run():
        return await adapter.send_model_picker(
            chat_id="oc_abc",
            providers=[
                {
                    "name": "OpenRouter",
                    "slug": "openrouter",
                    "models": ["deepseek/deepseek-v4-pro"],
                    "is_current": False,
                }
            ],
            current_model="deepseek/deepseek-v4-flash",
            current_provider="openrouter",
            session_key="feishu:oc_abc",
            on_model_selected=on_model_selected,
            metadata={"reply_to_message_id": "om_model_command"},
        )

    result = asyncio.run(run())

    assert installed is True
    assert result.success is True
    assert selected == [("oc_abc", "deepseek/deepseek-v4-pro", "openrouter")]
    assert [payload["event"] for _, payload, _ in posted] == [
        "interaction.requested",
        "message.completed",
    ]
    requested = posted[0][1]
    assert requested["message_id"] == "om_model_command"
    assert requested["data"]["kind"] == "model_picker"
    assert requested["data"]["fallback_policy"] == "native_text"
    assert requested["data"]["prompt"] == "选择模型"
    option_value = json.loads(requested["data"]["options"][0]["value"])
    assert option_value == {
        "provider": "openrouter",
        "model": "deepseek/deepseek-v4-pro",
    }
    completed = posted[1][1]
    assert completed["message_id"] == "om_model_command"
    assert completed["data"]["answer"] == "Switched to openrouter/deepseek/deepseek-v4-pro"


def test_model_picker_provider_tree_preserves_cli_order_and_deduplicates():
    providers = [
        {
            "name": "DeepSeek",
            "slug": "deepseek",
            "is_current": True,
            "models": [
                "deepseek-v4-pro",
                "",
                "deepseek-v4-pro",
                "deepseek-v4-flash",
            ],
        },
        {"name": "Broken", "slug": "", "models": ["ignored"]},
        {
            "name": "DeepSeek duplicate",
            "slug": "deepseek",
            "models": ["deepseek-v4-flash"],
        },
        {
            "name": "OpenRouter",
            "provider": "openrouter",
            "total_models": 36,
            "models": ["openai/gpt-5.5"],
            "base_url": "https://must-not-appear.invalid/v1",
            "api_key": "must-not-appear",
        },
        "not-a-provider-row",
    ]

    assert hook_runtime._model_picker_provider_tree(providers) == [
        {
            "slug": "deepseek",
            "name": "DeepSeek",
            "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
            "total_models": 2,
            "is_current": True,
        },
        {
            "slug": "openrouter",
            "name": "OpenRouter",
            "models": ["openai/gpt-5.5"],
            "total_models": 36,
            "is_current": False,
        },
    ]


def test_model_picker_provider_card_marks_current_provider_and_counts_models():
    providers = [
        {
            "slug": "deepseek",
            "name": "DeepSeek",
            "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
            "total_models": 4,
            "is_current": True,
        },
        {
            "slug": "openrouter",
            "name": "OpenRouter",
            "models": ["openai/gpt-5.5"],
            "total_models": 36,
            "is_current": False,
        },
    ]

    card = hook_runtime._hfc_native_model_picker_card(
        picker_id="model-1",
        providers=providers,
        current_provider="",
        current_model="deepseek-v4-flash",
    )

    assert card["header"]["title"]["content"] == "选择模型"
    select = card["elements"][1]["actions"][0]
    assert select["value"] == {
        "hfc_action": "model_picker",
        "hfc_model_picker_id": "model-1",
        "hfc_model_picker_view": "providers",
    }
    assert select["initial_option"] == "deepseek"
    assert [option["text"]["content"] for option in select["options"]] == [
        "当前 · DeepSeek (4 个模型)",
        "OpenRouter (36 个模型)",
    ]
    assert [option["value"] for option in select["options"]] == [
        "deepseek",
        "openrouter",
    ]
    cancel = card["elements"][2]["actions"][0]
    assert cancel["text"]["content"] == "取消"
    assert cancel["value"]["hfc_model_picker_nav"] == "cancel"
    assert "must-not-appear" not in json.dumps(card)


def test_model_picker_model_card_shows_only_selected_provider_models():
    providers = [
        {
            "slug": "deepseek",
            "name": "DeepSeek",
            "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
        },
        {
            "slug": "openrouter",
            "name": "OpenRouter",
            "models": ["openai/gpt-5.5"],
        },
    ]

    card = hook_runtime._hfc_native_model_picker_card(
        picker_id="model-1",
        providers=providers,
        current_provider="deepseek",
        current_model="deepseek-v4-flash",
        selected_provider="deepseek",
    )

    assert card["header"]["title"]["content"] == "选择模型 · DeepSeek"
    select = card["elements"][1]["actions"][0]
    assert select["value"]["hfc_model_picker_view"] == "models"
    assert [option["text"]["content"] for option in select["options"]] == [
        "deepseek-v4-pro",
        "当前 · deepseek-v4-flash",
    ]
    assert [json.loads(option["value"]) for option in select["options"]] == [
        {"provider": "deepseek", "model": "deepseek-v4-pro"},
        {"provider": "deepseek", "model": "deepseek-v4-flash"},
    ]
    assert json.loads(select["initial_option"]) == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
    }
    assert "openai/gpt-5.5" not in json.dumps(card)
    back, cancel = card["elements"][2]["actions"]
    assert back["value"]["hfc_model_picker_nav"] == "back"
    assert cancel["value"]["hfc_model_picker_nav"] == "cancel"


def test_native_feishu_model_picker_uses_websocket_card_when_connected():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.sent = None

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent = kwargs
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(message_id="om_model_card"),
            )

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True

    async def run():
        return await adapter.send_model_picker(
            chat_id="oc_abc",
            providers=[
                {
                    "name": "OpenRouter",
                    "slug": "openrouter",
                    "models": ["deepseek/deepseek-v4-pro"],
                }
            ],
            current_model="deepseek/deepseek-v4-flash",
            current_provider="openrouter",
            session_key="feishu:oc_abc",
            metadata={"reply_to_message_id": "om_model_command"},
        )

    result = asyncio.run(run())

    assert result.success is True
    assert result.message_id == "om_model_card"
    assert adapter.sent["msg_type"] == "interactive"
    assert adapter.sent["reply_to"] == "om_model_command"
    card = json.loads(adapter.sent["payload"])
    assert card["header"]["title"]["content"] == "选择模型"
    actions = card["elements"][1]["actions"]
    assert len(actions) == 1
    action = actions[0]
    assert action["tag"] == "select_static"
    assert action["value"]["hfc_action"] == "model_picker"
    assert action["value"]["hfc_model_picker_view"] == "providers"
    assert action["options"][0]["value"] == "openrouter"
    assert action["options"][0]["text"]["content"] == "当前 · OpenRouter (1 个模型)"
    picker_id = action["value"]["hfc_model_picker_id"]
    picker_state = adapter._hfc_model_picker_state[picker_id]
    assert picker_state["session_key"] == "feishu:oc_abc"
    assert picker_state["current_provider"] == "openrouter"
    assert picker_state["current_model"] == "deepseek/deepseek-v4-flash"
    assert picker_state["providers"] == [
        {
            "name": "OpenRouter",
            "slug": "openrouter",
            "models": ["deepseek/deepseek-v4-pro"],
            "total_models": 1,
            "is_current": False,
        }
    ]


def test_native_feishu_model_picker_model_view_does_not_truncate_to_eight():
    card = hook_runtime._hfc_native_model_picker_card(
        picker_id="model-many",
        providers=[
            {
                "name": "OpenAI Codex",
                "slug": "openai-codex",
                "models": [f"gpt-5.{index}" for index in range(12)],
            }
        ],
        current_model="gpt-5.5",
        current_provider="openai-codex",
        selected_provider="openai-codex",
    )

    select = card["elements"][1]["actions"][0]
    assert select["tag"] == "select_static"
    assert len(select["options"]) == 12
    assert "仅展示前 8 个" not in card["elements"][0]["content"]


def test_native_model_picker_navigation_moves_between_provider_and_model_views(
    monkeypatch,
):
    monkeypatch.setattr(
        hook_runtime,
        "_hfc_raw_feishu_callback_response",
        lambda _adapter, card: card,
    )

    class DummyAdapter:
        def __init__(self):
            self._loop = object()

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return sender_id.open_id == "ou_user" and chat_id == "oc_abc"

    providers = [
        {
            "slug": "deepseek",
            "name": "DeepSeek",
            "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
        },
        {
            "slug": "openrouter",
            "name": "OpenRouter",
            "models": ["openai/gpt-5.5"],
        },
    ]
    adapter = DummyAdapter()
    adapter._hfc_model_picker_state = {
        "model-nav": {
            "chat_id": "oc_abc",
            "message_id": "om_model_card",
            "providers": providers,
            "current_provider": "deepseek",
            "current_model": "deepseek-v4-flash",
            "selected_provider": "",
            "on_model_selected": None,
        }
    }
    data = SimpleNamespace(
        event=SimpleNamespace(
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user", user_id="u_1"),
        )
    )

    model_card = hook_runtime._hfc_handle_native_model_action(
        adapter,
        data,
        {
            "hfc_model_picker_id": "model-nav",
            "hfc_model_picker_view": "providers",
            "hfc_choice": "deepseek",
        },
    )

    assert model_card["header"]["title"]["content"] == "选择模型 · DeepSeek"
    model_select = model_card["elements"][1]["actions"][0]
    assert len(model_select["options"]) == 2
    assert adapter._hfc_model_picker_state["model-nav"]["selected_provider"] == "deepseek"

    provider_card = hook_runtime._hfc_handle_native_model_action(
        adapter,
        data,
        {
            "hfc_model_picker_id": "model-nav",
            "hfc_model_picker_nav": "back",
        },
    )

    assert provider_card["header"]["title"]["content"] == "选择模型"
    assert len(provider_card["elements"][1]["actions"][0]["options"]) == 2
    assert adapter._hfc_model_picker_state["model-nav"]["selected_provider"] == ""


def test_native_model_picker_cancel_does_not_switch_model(monkeypatch):
    monkeypatch.setattr(
        hook_runtime,
        "_hfc_raw_feishu_callback_response",
        lambda _adapter, card: card,
    )

    class DummyAdapter:
        def __init__(self):
            self._loop = object()

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return True

    selected = []

    async def on_model_selected(*args):
        selected.append(args)

    adapter = DummyAdapter()
    adapter._hfc_model_picker_state = {
        "model-cancel": {
            "chat_id": "oc_abc",
            "message_id": "om_model_card",
            "providers": [
                {
                    "slug": "deepseek",
                    "name": "DeepSeek",
                    "models": ["deepseek-v4-pro"],
                }
            ],
            "current_provider": "deepseek",
            "current_model": "deepseek-v4-flash",
            "on_model_selected": on_model_selected,
        }
    }
    data = SimpleNamespace(
        event=SimpleNamespace(
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user", user_id="u_1"),
        )
    )

    card = hook_runtime._hfc_handle_native_model_action(
        adapter,
        data,
        {
            "hfc_model_picker_id": "model-cancel",
            "hfc_model_picker_nav": "cancel",
        },
    )

    assert card["header"]["title"]["content"] == "模型选择已取消"
    assert selected == []
    assert "model-cancel" not in adapter._hfc_model_picker_state


def test_native_model_picker_rejects_unknown_provider_without_switching(monkeypatch):
    monkeypatch.setattr(
        hook_runtime,
        "_hfc_raw_feishu_callback_response",
        lambda _adapter, card: card,
    )

    class DummyAdapter:
        def __init__(self):
            self._loop = object()

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return True

    adapter = DummyAdapter()
    adapter._hfc_model_picker_state = {
        "model-invalid": {
            "chat_id": "oc_abc",
            "message_id": "om_model_card",
            "providers": [
                {
                    "slug": "deepseek",
                    "name": "DeepSeek",
                    "models": ["deepseek-v4-pro"],
                }
            ],
            "current_provider": "deepseek",
            "current_model": "deepseek-v4-flash",
            "on_model_selected": None,
        }
    }
    data = SimpleNamespace(
        event=SimpleNamespace(
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user", user_id="u_1"),
        )
    )

    card = hook_runtime._hfc_handle_native_model_action(
        adapter,
        data,
        {
            "hfc_model_picker_id": "model-invalid",
            "hfc_model_picker_view": "providers",
            "hfc_choice": "not-configured",
        },
    )

    assert card["header"]["title"]["content"] == "模型选择无效"
    assert "model-invalid" in adapter._hfc_model_picker_state


def test_native_feishu_model_picker_tracks_send_result_message_id():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()

        async def _feishu_send_with_retry(self, **kwargs):
            return SimpleNamespace(success=True, message_id="om_direct_model_card")

    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True

    async def run():
        return await adapter.send_model_picker(
            chat_id="oc_abc",
            providers=[
                {
                    "name": "DeepSeek",
                    "slug": "deepseek",
                    "models": ["deepseek-v4-pro"],
                }
            ],
            current_model="deepseek-v4-flash",
            current_provider="deepseek",
            session_key="feishu:oc_abc",
            metadata=None,
        )

    result = asyncio.run(run())

    assert result.success is True
    assert result.message_id == "om_direct_model_card"
    picker_state = next(iter(adapter._hfc_model_picker_state.values()))
    assert picker_state["message_id"] == "om_direct_model_card"


def test_bare_resume_uses_native_picker_and_preserves_topic_metadata():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.sent = None

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent = kwargs
            return SimpleNamespace(success=True, message_id="om_resume_card")

    class DummySessionDB:
        async def list_sessions_rich(self, *, source, limit):
            assert source == "feishu"
            assert limit == 10
            return [
                {
                    "id": "session-current",
                    "title": "Current project",
                    "preview": "current preview",
                },
                {
                    "id": "session-target",
                    "title": "Release planning",
                    "preview": "x" * 80,
                },
                {"id": "untitled", "title": "", "preview": "hidden"},
            ]

    class DummyRunner:
        def __init__(self, adapter):
            self.adapters = {"feishu": adapter}
            self._session_db = DummySessionDB()
            self.original_calls = []
            self.session_store = SimpleNamespace(
                get_or_create_session=lambda source: SimpleNamespace(
                    session_id="session-current"
                )
            )

        async def _handle_resume_command(self, event):
            self.original_calls.append(event.text)
            return "native resume fallback"

        async def _resume_row_visible(self, source, row, allow_all):
            assert allow_all is False
            return True

        def _reply_anchor_for_event(self, event):
            return "om_topic_command"

        def _thread_metadata_for_source(self, source, reply_anchor):
            return {
                "thread_id": source.thread_id,
            }

    adapter = DummyFeishuAdapter()
    runner = DummyRunner(adapter)
    event = SimpleNamespace(
        text="/resume",
        message_id="om_topic_command",
        source=SimpleNamespace(
            platform="feishu",
            chat_id="oc_topic",
            chat_type="thread",
            thread_id="omt_thread",
            user_id="tenant_user_1",
        ),
        raw_message=SimpleNamespace(
            event=SimpleNamespace(
                sender=SimpleNamespace(
                    sender_id=SimpleNamespace(open_id="ou_initiator")
                )
            )
        ),
        get_command_args=lambda: "",
    )

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)
    result = asyncio.run(runner._handle_resume_command(event))

    assert result is None
    assert runner.original_calls == []
    assert adapter.sent["reply_to"] == "om_topic_command"
    assert adapter.sent["metadata"] == {
        "thread_id": "omt_thread",
        "reply_to_message_id": "om_topic_command",
    }
    card = json.loads(adapter.sent["payload"])
    select = card["elements"][1]["actions"][0]
    assert select["value"]["hfc_action"] == "resume_picker"
    assert [option["value"] for option in select["options"]] == [
        "session-current",
        "session-target",
    ]
    labels = [option["text"]["content"] for option in select["options"]]
    assert "当前" in labels[0]
    assert "x" * 40 in labels[1]
    assert "x" * 41 not in labels[1]
    picker_id = select["value"]["hfc_resume_picker_id"]
    state = adapter._hfc_resume_picker_state[picker_id]
    assert state["allowed_session_ids"] == {
        "session-current",
        "session-target",
    }
    assert state["operator_open_id"] == "ou_initiator"


def test_bare_resume_accepts_exact_topic_session_key_when_hermes_alt_id_check_fails():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.sent = None

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent = kwargs
            return SimpleNamespace(success=True, message_id="om_resume_card")

    exact_key = "agent:main:feishu:group:oc_topic:om_topic_root"

    class DummySessionDB:
        async def list_sessions_rich(self, *, source, limit):
            return [
                {
                    "id": "session-topic",
                    "title": "Topic session",
                    "session_key": exact_key,
                },
                {
                    "id": "session-other-topic",
                    "title": "Other topic",
                    "session_key": "agent:main:feishu:group:oc_topic:om_other_root",
                },
            ]

    class DummyRunner:
        def __init__(self, adapter):
            self.adapters = {"feishu": adapter}
            self._session_db = DummySessionDB()
            self.session_store = SimpleNamespace(
                get_or_create_session=lambda source: SimpleNamespace(
                    session_id="session-topic"
                )
            )

        async def _handle_resume_command(self, event):
            raise AssertionError("exact topic session should use the picker")

        async def _resume_row_visible(self, source, row, allow_all):
            return False

        def _session_key_for_source(self, source):
            return exact_key

    adapter = DummyFeishuAdapter()
    runner = DummyRunner(adapter)
    event = SimpleNamespace(
        text="/resume",
        message_id="om_topic_command",
        source=SimpleNamespace(
            platform="feishu",
            chat_id="oc_topic",
            chat_type="group",
            thread_id="om_topic_root",
            user_id="ou_initiator",
            user_id_alt="on_initiator",
        ),
        get_command_args=lambda: "",
    )

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)
    assert asyncio.run(runner._handle_resume_command(event)) is None

    card = json.loads(adapter.sent["payload"])
    select = card["elements"][1]["actions"][0]
    assert [option["value"] for option in select["options"]] == ["session-topic"]


def test_resume_picker_fails_open_to_original_handler():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = object()
            self.send_count = 0

        async def _feishu_send_with_retry(self, **kwargs):
            self.send_count += 1
            return SimpleNamespace(success=False, message_id="")

    class DummyRunner:
        def __init__(self, rows):
            self.adapters = {"feishu": DummyFeishuAdapter()}
            self.rows = rows
            self.original_calls = []
            self._session_db = SimpleNamespace(list_sessions_rich=self._list_sessions)
            self.session_store = SimpleNamespace(
                get_or_create_session=lambda source: SimpleNamespace(session_id="current")
            )

        async def _list_sessions(self, *, source, limit):
            return self.rows

        async def _resume_row_visible(self, source, row, allow_all):
            return True

        async def _handle_resume_command(self, event):
            self.original_calls.append(event.text)
            return "native resume fallback"

    async def exercise(text, platform, rows, *, chat_type="dm", user_id="ou_user"):
        runner = DummyRunner(rows)
        event = SimpleNamespace(
            text=text,
            source=SimpleNamespace(
                platform=platform,
                chat_id="oc_abc",
                chat_type=chat_type,
                user_id=user_id,
            ),
            get_command_args=lambda: text.partition(" ")[2],
        )
        hook_runtime.install_feishu_command_card_adapter_methods(runner, event=event)
        result = await runner._handle_resume_command(event)
        return runner, result

    cases = [
        ("/resume session-1", "feishu", [{"id": "session-1", "title": "One"}]),
        ("/resume", "telegram", [{"id": "session-1", "title": "One"}]),
        ("/resume", "feishu", []),
        ("/resume", "feishu", [{"id": "session-1", "title": "One"}]),
    ]
    for text, platform, rows in cases:
        runner, result = asyncio.run(exercise(text, platform, rows))
        assert result == "native resume fallback"
        assert runner.original_calls == [text]

    runner, result = asyncio.run(
        exercise(
            "/resume",
            "feishu",
            [{"id": "session-1", "title": "One"}],
            chat_type="group",
            user_id="tenant_user_without_open_id",
        )
    )
    assert result == "native resume fallback"
    assert runner.original_calls == ["/resume"]


def test_resume_picker_callback_acks_then_uses_original_security_path(monkeypatch):
    class FakeCallBackCard:
        def __init__(self):
            self.type = None
            self.data = None

    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = SimpleNamespace(
                im=SimpleNamespace(
                    v1=SimpleNamespace(message=SimpleNamespace(update=lambda request: None))
                )
            )
            self._loop = object()
            self.submitted = []
            self.updated = None

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return sender_id.open_id == "ou_initiator" and chat_id == "oc_group"

        def _submit_on_loop(self, loop, coro):
            assert loop is self._loop
            self.submitted.append(coro)
            return True

        def _build_update_message_body(self, *, msg_type, content):
            return SimpleNamespace(msg_type=msg_type, content=content)

        def _build_update_message_request(self, message_id, request_body):
            return SimpleNamespace(message_id=message_id, request_body=request_body)

        async def _run_blocking(self, func, request):
            self.updated = request
            return SimpleNamespace(success=lambda: True)

        def _on_card_action_trigger(self, data):
            return "original"

    class DummyRunner:
        def __init__(self, adapter):
            self.adapters = {"feishu": adapter}
            self.original_calls = []

        async def _handle_resume_command(self, event):
            self.original_calls.append(event.text)
            return "Resumed: Release planning"

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(hook_runtime, "CallBackCard", FakeCallBackCard, raising=False)

    adapter = DummyFeishuAdapter()
    runner = DummyRunner(adapter)
    original_event = SimpleNamespace(
        text="/resume",
        source=SimpleNamespace(
            platform="feishu",
            chat_id="oc_group",
            chat_type="group",
            user_id="ou_initiator",
        ),
        get_command_args=lambda: "",
    )
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        runner, event=original_event
    )
    adapter._hfc_resume_picker_state = {
        "resume-1": {
            "allowed_session_ids": {"session-target"},
            "chat_id": "oc_group",
            "chat_type": "group",
            "operator_open_id": "ou_initiator",
            "message_id": "om_resume_card",
            "runner": runner,
            "event": original_event,
            "original_handler": type(runner)._hfc_original_handle_resume_command,
            "expires_at": time.time() + 60,
        }
    }
    data = SimpleNamespace(
        event=SimpleNamespace(
            token="resume-token-1",
            action=SimpleNamespace(
                value={
                    "hfc_action": "resume_picker",
                    "hfc_resume_picker_id": "resume-1",
                },
                option="session-target",
            ),
            context=SimpleNamespace(open_chat_id="oc_group"),
            operator=SimpleNamespace(open_id="ou_initiator", user_id="u_1"),
        )
    )

    started = time.monotonic()
    response = adapter._on_card_action_trigger(data)
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert response.card.type == "raw"
    assert response.card.data["header"]["title"]["content"] == "会话恢复中"
    assert runner.original_calls == []
    assert "resume-1" not in adapter._hfc_resume_picker_state
    assert len(adapter.submitted) == 1

    asyncio.run(adapter.submitted.pop())

    assert runner.original_calls == ["/resume session-target"]
    assert original_event.text == "/resume"
    assert adapter.updated.message_id == "om_resume_card"
    result_card = json.loads(adapter.updated.request_body.content)
    assert result_card["header"]["title"]["content"] == "会话已恢复"
    assert "Release planning" in result_card["elements"][0]["content"]


def test_resume_picker_group_rejects_different_operator(monkeypatch):
    class FakeCallBackCard:
        def __init__(self):
            self.type = None
            self.data = None

    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._loop = object()
            self.submitted = []

        def _loop_accepts_callbacks(self, loop):
            return True

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return True

        def _submit_on_loop(self, loop, coro):
            self.submitted.append(coro)
            return True

        def _on_card_action_trigger(self, data):
            return "original"

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(hook_runtime, "CallBackCard", FakeCallBackCard, raising=False)
    adapter = DummyFeishuAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    hook_runtime.install_feishu_command_card_adapter_methods(runner)
    adapter._hfc_resume_picker_state = {
        "resume-1": {
            "allowed_session_ids": {"session-target"},
            "chat_id": "oc_group",
            "chat_type": "group",
            "operator_open_id": "ou_initiator",
            "expires_at": time.time() + 60,
        }
    }
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "resume_picker",
                    "hfc_resume_picker_id": "resume-1",
                },
                option="session-target",
            ),
            context=SimpleNamespace(open_chat_id="oc_group"),
            operator=SimpleNamespace(open_id="ou_other", user_id="u_2"),
        )
    )

    response = adapter._on_card_action_trigger(data)

    assert response.card.type == "raw"
    assert response.card.data["header"]["template"] == "red"
    assert adapter.submitted == []
    assert "resume-1" in adapter._hfc_resume_picker_state


def test_resume_picker_private_chat_does_not_compare_operator():
    class DummyFeishuAdapter:
        def __init__(self):
            self._loop = object()
            self._hfc_resume_picker_state = {
                "resume-dm": {
                    "allowed_session_ids": {"session-target"},
                    "chat_id": "oc_dm",
                    "chat_type": "dm",
                    "operator_open_id": "ou_original",
                    "expires_at": time.time() + 60,
                }
            }

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return True

    adapter = DummyFeishuAdapter()
    data = SimpleNamespace(
        event=SimpleNamespace(
            context=SimpleNamespace(open_chat_id="oc_dm"),
            operator=SimpleNamespace(open_id="ou_callback", user_id="u_2"),
        )
    )

    prepared = hook_runtime._hfc_prepare_native_resume_action(
        adapter,
        data,
        {
            "hfc_resume_picker_id": "resume-dm",
            "hfc_choice": "session-target",
        },
    )

    assert prepared is not None
    assert prepared["choice"] == "session-target"
    assert adapter._hfc_resume_picker_state == {}


def test_resume_picker_expired_state_is_consumed_without_execution():
    adapter = SimpleNamespace(
        _loop=object(),
        _loop_accepts_callbacks=lambda loop: True,
        _hfc_resume_picker_state={
            "resume-expired": {
                "allowed_session_ids": {"session-target"},
                "chat_id": "oc_dm",
                "chat_type": "dm",
                "expires_at": time.time() - 1,
            }
        },
    )
    data = SimpleNamespace(
        event=SimpleNamespace(
            context=SimpleNamespace(open_chat_id="oc_dm"),
            operator=SimpleNamespace(open_id="ou_user", user_id="u_1"),
        )
    )

    prepared = hook_runtime._hfc_prepare_native_resume_action(
        adapter,
        data,
        {
            "hfc_resume_picker_id": "resume-expired",
            "hfc_choice": "session-target",
        },
    )

    assert prepared is None
    assert adapter._hfc_resume_picker_state == {}


def test_feishu_command_card_action_resolves_native_model_picker(monkeypatch):
    class FakeCallBackCard:
        def __init__(self):
            self.type = None
            self.data = None

    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = SimpleNamespace(
                im=SimpleNamespace(
                    v1=SimpleNamespace(message=SimpleNamespace(update=lambda request: None))
                )
            )
            self._loop = object()
            self.updated = None
            self.submitted = []
            self.seen_tokens = set()

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return sender_id.open_id == "ou_user" and chat_id == "oc_abc"

        def _is_card_action_duplicate(self, token):
            duplicate = token in self.seen_tokens
            self.seen_tokens.add(token)
            return duplicate

        def _submit_on_loop(self, loop, coro):
            assert loop is self._loop
            self.submitted.append(coro)
            return True

        def _build_update_message_body(self, *, msg_type, content):
            return SimpleNamespace(msg_type=msg_type, content=content)

        def _build_update_message_request(self, message_id, request_body):
            return SimpleNamespace(message_id=message_id, request_body=request_body)

        async def _run_blocking(self, func, request):
            self.updated = request
            return SimpleNamespace(success=lambda: True)

        def _on_card_action_trigger(self, data):
            return "original"

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(hook_runtime, "CallBackCard", FakeCallBackCard, raising=False)

    selected = []

    async def on_model_selected(chat_id, model_id, provider_slug):
        selected.append((chat_id, model_id, provider_slug))
        return f"Switched to {provider_slug}/{model_id}"

    adapter = DummyFeishuAdapter()
    adapter._hfc_model_picker_state = {
        "model-1": {
            "session_key": "feishu:oc_abc",
            "chat_id": "oc_abc",
            "message_id": "om_model_card",
            "on_model_selected": on_model_selected,
            "providers": [
                {
                    "slug": "openrouter",
                    "name": "OpenRouter",
                    "models": ["deepseek/deepseek-v4-pro"],
                }
            ],
            "current_provider": "openrouter",
            "current_model": "deepseek/deepseek-v4-flash",
            "selected_provider": "openrouter",
        }
    }
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True

    data = SimpleNamespace(
        event=SimpleNamespace(
            token="token-model-picker-once",
            action=SimpleNamespace(
                tag="select_static",
                value={
                    "hfc_action": "model_picker",
                    "hfc_model_picker_id": "model-1",
                },
                option=json.dumps(
                    {"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"}
                ),
            ),
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user", user_id="u_1"),
        )
    )

    started = time.monotonic()
    response = adapter._on_card_action_trigger(data)
    elapsed = time.monotonic() - started
    duplicate_response = adapter._on_card_action_trigger(data)

    assert elapsed < 0.1
    assert selected == []
    assert "model-1" in adapter._hfc_model_picker_state
    assert adapter.updated is None
    assert response.card.type == "raw"
    card = response.card.data
    assert card["header"]["template"] == "blue"
    assert card["header"]["title"]["content"] == "模型切换中"
    assert "openrouter/deepseek/deepseek-v4-pro" in card["elements"][0]["content"]
    assert len(adapter.submitted) == 1
    assert duplicate_response.card is None

    asyncio.run(adapter.submitted.pop())

    assert selected == [("oc_abc", "deepseek/deepseek-v4-pro", "openrouter")]
    assert "model-1" not in adapter._hfc_model_picker_state
    assert adapter.updated.message_id == "om_model_card"
    updated_card = json.loads(adapter.updated.request_body.content)
    assert updated_card["header"]["template"] == "green"
    assert updated_card["header"]["title"]["content"] == "模型已更新"


def test_model_picker_background_fallback_preserves_action_metadata():
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = None
            self._loop = object()
            self.submitted = []
            self.sent = []

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return True

        def _submit_on_loop(self, loop, coro):
            assert loop is self._loop
            self.submitted.append(coro)
            return True

        async def _feishu_send_with_retry(self, **kwargs):
            self.sent.append(kwargs)
            return SimpleNamespace(success=True, message_id="om_model_result")

    adapter = DummyFeishuAdapter()
    adapter._hfc_model_picker_state = {
        "model-metadata": {
            "chat_id": "oc_topic",
            "message_id": "om_picker",
            "on_model_selected": None,
        }
    }
    metadata = {"thread_id": "omt_thread", "reply_to_message_id": "om_root"}
    data = SimpleNamespace(
        event=SimpleNamespace(
            message={"metadata": metadata},
            action=SimpleNamespace(),
            context=SimpleNamespace(open_chat_id="oc_topic"),
            operator=SimpleNamespace(open_id="ou_user", user_id="u_1"),
        )
    )
    action_value = {
        "hfc_action": "model_picker",
        "hfc_model_picker_id": "model-metadata",
        "hfc_choice": json.dumps({"provider": "openrouter", "model": "gpt-5"}),
    }

    hook_runtime._hfc_switch_model_background_task(
        adapter,
        data,
        action_value,
        "om_picker",
    )
    asyncio.run(adapter.submitted.pop())

    assert len(adapter.sent) == 1
    assert adapter.sent[0]["metadata"] == metadata
    assert adapter.sent[0]["reply_to"] == "om_picker"
    assert adapter.sent[0]["msg_type"] == "interactive"


def test_native_command_card_update_uses_patch_without_msg_type(monkeypatch):
    calls = []

    class MessageAPI:
        def patch(self, request):
            calls.append(("patch", request))

        def update(self, request):
            calls.append(("update", request))

    class DummyAdapter:
        def __init__(self):
            self._client = SimpleNamespace(
                im=SimpleNamespace(v1=SimpleNamespace(message=MessageAPI()))
            )

        async def _run_blocking(self, func, request):
            func(request)
            return SimpleNamespace(success=lambda: True)

        def _build_update_message_body(self, *, msg_type, content):
            return SimpleNamespace(msg_type=msg_type, content=content)

        def _build_update_message_request(self, message_id, request_body):
            return SimpleNamespace(message_id=message_id, request_body=request_body)

    monkeypatch.setattr(
        hook_runtime,
        "_hfc_build_patch_message_request",
        lambda message_id, content: SimpleNamespace(
            message_id=message_id,
            request_body=SimpleNamespace(content=content),
        ),
        raising=False,
    )

    result = asyncio.run(
        hook_runtime._hfc_update_native_command_card(
            DummyAdapter(),
            "om_card",
            {"elements": [{"tag": "markdown", "content": "done"}]},
        )
    )

    assert result is True
    assert [name for name, _request in calls] == ["patch"]
    request_body = calls[0][1].request_body
    assert not hasattr(request_body, "msg_type")
    assert json.loads(request_body.content)["elements"][0]["content"] == "done"


def test_stale_feishu_card_action_handler_updates_native_model_picker(monkeypatch):
    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._client = SimpleNamespace(
                im=SimpleNamespace(
                    v1=SimpleNamespace(message=SimpleNamespace(update=lambda request: None))
                )
            )
            self._loop = object()
            self.updated = None
            self.routed = []

        def _loop_accepts_callbacks(self, loop):
            return loop is self._loop

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return sender_id.open_id == "ou_user" and chat_id == "oc_abc"

        def _on_card_action_trigger(self, data):
            self._submit_on_loop(self._loop, self._handle_card_action_event(data))
            return "empty"

        def _submit_on_loop(self, loop, coro):
            assert loop is self._loop
            asyncio.run(coro)
            return True

        async def _handle_card_action_event(self, data):
            self.routed.append(data)

        def _build_update_message_body(self, *, msg_type, content):
            return SimpleNamespace(msg_type=msg_type, content=content)

        def _build_update_message_request(self, message_id, request_body):
            return SimpleNamespace(message_id=message_id, request_body=request_body)

        async def _run_blocking(self, func, request):
            self.updated = request
            return SimpleNamespace(success=lambda: True)

    DummyFeishuAdapter.__module__ = hook_runtime.__name__

    selected = []

    async def on_model_selected(chat_id, model_id, provider_slug):
        selected.append((chat_id, model_id, provider_slug))
        return f"Switched to {provider_slug}/{model_id}"

    def fail_run_coroutine_threadsafe(coro, loop):
        raise AssertionError("stale Feishu model picker path must await callback directly")

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fail_run_coroutine_threadsafe)

    adapter = DummyFeishuAdapter()
    stale_handler = adapter._on_card_action_trigger
    adapter._hfc_model_picker_state = {
        "model-1": {
            "session_key": "feishu:oc_abc",
            "chat_id": "oc_abc",
            "message_id": "om_model_card",
            "on_model_selected": on_model_selected,
        }
    }
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True

    data = SimpleNamespace(
        event=SimpleNamespace(
            token="tok-model-1",
            action=SimpleNamespace(
                tag="select_static",
                value={
                    "hfc_action": "model_picker",
                    "hfc_model_picker_id": "model-1",
                },
                option=json.dumps(
                    {"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"}
                ),
            ),
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user", user_id="u_1"),
        )
    )

    assert stale_handler(data) == "empty"

    assert adapter.routed == []
    assert selected == [("oc_abc", "deepseek/deepseek-v4-pro", "openrouter")]
    assert "model-1" not in adapter._hfc_model_picker_state
    assert adapter.updated is None


def test_complete_command_card_async_posts_completed_event(monkeypatch):
    posted = []
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    async def fake_post(url, payload, timeout):
        posted.append((url, payload, timeout))
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post)

    async def run():
        return await hook_runtime.complete_command_card_from_hermes_locals_async(
            {
                "chat_id": "oc_abc",
                "message_id": "om_command",
                "conversation_id": "feishu:oc_abc",
            },
            answer="New session started.",
        )

    assert asyncio.run(run()) is True
    assert posted[0][0] == "http://sidecar.test/events"
    payload = posted[0][1]
    assert payload["event"] == "message.completed"
    assert payload["message_id"] == "om_command"
    assert payload["conversation_id"] == "feishu:oc_abc"
    assert payload["data"]["answer"] == "New session started."
    assert payload["data"]["delivery_kind"] == "command"


@pytest.mark.parametrize("sidecar_result", [None, "", {"ok": True}])
def test_command_completion_does_not_suppress_without_explicit_commit(
    monkeypatch,
    sidecar_result,
):
    async def fake_post(_url, _payload, _timeout):
        return sidecar_result

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post)

    async def run():
        return await hook_runtime.complete_command_card_from_hermes_locals_async(
            {
                "chat_id": "oc_abc",
                "message_id": "om_command",
                "conversation_id": "feishu:oc_abc",
            },
            answer="command result",
        )

    assert asyncio.run(run()) is False


def test_request_interaction_retries_when_sidecar_reports_not_applied(monkeypatch):
    posted = []
    polls = iter(
        [
            {
                "ok": True,
                "status": "completed",
                "interaction_id": "clarify-1",
                "choice": "保留",
            },
        ]
    )
    post_results = iter(
        [
            {"ok": True, "applied": False},
            {"ok": True, "applied": True},
        ]
    )
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    def fake_post(local_vars, url, payload, timeout):
        posted.append(payload)
        return next(post_results)

    def fake_get(url, timeout):
        assert url == "http://sidecar.test/interactions/clarify-1"
        return next(polls)

    monkeypatch.setattr(hook_runtime, "_post_interaction_event", fake_post)
    monkeypatch.setattr(hook_runtime, "_get_json_sync", fake_get)

    result = hook_runtime.request_interaction_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        kind="clarify",
        interaction_id="clarify-1",
        prompt="怎么处理？",
        options=[{"label": "保留", "value": "保留"}],
        timeout_seconds=1,
        poll_interval_seconds=0,
    )

    assert result["status"] == "completed"
    assert result["choice"] == "保留"
    assert [payload["sequence"] for payload in posted] == [0, 1]


def test_request_interaction_returns_none_for_text_fallback_mode(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    def fake_post(local_vars, url, payload, timeout):
        return {"ok": True, "applied": True, "interaction_mode": "text"}

    def fail_get(url, timeout):
        raise AssertionError("text fallback should not poll card action state")

    monkeypatch.setattr(hook_runtime, "_post_interaction_event", fake_post)
    monkeypatch.setattr(hook_runtime, "_get_json_sync", fail_get)

    result = hook_runtime.request_interaction_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        kind="clarify",
        interaction_id="clarify-1",
        prompt="怎么处理？",
        options=[{"label": "保留", "value": "保留"}],
        timeout_seconds=0,
        poll_interval_seconds=0,
    )

    assert result is None


def test_request_interaction_polls_through_transient_not_found(monkeypatch):
    polls = iter(
        [
            error.HTTPError("http://sidecar.test/interactions/clarify-1", 404, "not found", {}, None),
            {
                "ok": True,
                "status": "completed",
                "interaction_id": "clarify-1",
                "choice": "删除",
            },
        ]
    )
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    def fake_post(local_vars, url, payload, timeout):
        return {"ok": True, "applied": True}

    def fake_get(url, timeout):
        result = next(polls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(hook_runtime, "_post_interaction_event", fake_post)
    monkeypatch.setattr(hook_runtime, "_get_json_sync", fake_get)

    result = hook_runtime.request_interaction_from_hermes_locals(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        kind="clarify",
        interaction_id="clarify-1",
        prompt="怎么处理？",
        options=[{"label": "删除", "value": "删除"}],
        timeout_seconds=1,
        poll_interval_seconds=0,
    )

    assert result["status"] == "completed"
    assert result["choice"] == "删除"


def test_completed_event_extracts_attachment_summaries_from_response():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_1",
            "message_id": "m_1",
            "answer": "结果见附件 MEDIA:/tmp/report.pdf\n还有 /tmp/chart.png",
        },
    )

    attachments = payload["data"]["attachments"]
    assert {"kind": "file", "name": "report.pdf", "summary": "report.pdf"} in attachments
    assert {"kind": "image", "name": "chart.png", "summary": "chart.png"} in attachments


def test_completed_event_hides_media_directive_from_card_answer():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_1",
            "message_id": "m_1",
            "answer": (
                "发你一张之前生成的咖啡馆坐姿图：\n\n"
                "MEDIA:/opt/data/image_cache/continue_ani_cafe_leaning_table_00001_.png"
            ),
        },
    )

    assert payload["data"]["answer"] == "发你一张之前生成的咖啡馆坐姿图："
    assert payload["data"]["native_delivery"] == "required"
    assert payload["data"]["attachments"] == [
        {
            "kind": "image",
            "name": "continue_ani_cafe_leaning_table_00001_.png",
            "summary": "continue_ani_cafe_leaning_table_00001_.png",
        }
    ]


@pytest.mark.parametrize(
    "answer",
    [
        "说明语法时请保留 `MEDIA:` 字面量。",
        "```text\nMEDIA:/tmp/example.png\n```\n以上只是示例。",
    ],
)
def test_completed_event_ignores_media_directives_inside_markdown_code(answer):
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_1",
            "message_id": "m_1",
            "answer": answer,
        },
    )

    assert payload["data"]["answer"] == answer
    assert payload["data"]["attachments"] == []
    assert payload["data"]["native_delivery"] == "allowed"


def test_completed_event_extracts_attachment_summaries_from_response_field():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_1",
            "message_id": "m_1",
            "response": "生成完成 MEDIA:/tmp/audio.mp3",
        },
    )

    assert {
        "kind": "audio",
        "name": "audio.mp3",
        "summary": "audio.mp3",
    } in payload["data"]["attachments"]


def test_completed_event_extracts_structured_attachment_fields():
    class AttachmentObject:
        file_name = "diagram.webp"
        path = "/tmp/diagram.webp"
        mime_type = "image/webp"

    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_1",
            "message_id": "m_1",
            "answer": "生成完成",
            "attachments": [
                {"name": "report.pdf", "summary": "季度报告.pdf", "kind": "file"},
                {"path": "/tmp/photo.jpg"},
                "/tmp/audio.wav",
                AttachmentObject(),
            ],
            "files": [{"file_path": "/tmp/archive.zip"}],
        },
    )

    attachments = payload["data"]["attachments"]
    assert {"kind": "file", "name": "report.pdf", "summary": "季度报告.pdf"} in attachments
    assert {"kind": "image", "name": "photo.jpg", "summary": "photo.jpg"} in attachments
    assert {"kind": "audio", "name": "audio.wav", "summary": "audio.wav"} in attachments
    assert {"kind": "image", "name": "diagram.webp", "summary": "diagram.webp"} in attachments
    assert {"kind": "file", "name": "archive.zip", "summary": "archive.zip"} in attachments


def test_completed_event_allows_card_only_for_generic_attachment_summaries():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_1",
            "message_id": "m_1",
            "answer": "已整理配色表，见卡片附件摘要。",
            "attachments": [
                {"name": "colors.csv", "summary": "colors.csv", "kind": "file"},
                {"name": "styles.csv", "summary": "styles.csv", "kind": "file"},
            ],
        },
    )

    attachments = payload["data"]["attachments"]
    assert {"kind": "file", "name": "colors.csv", "summary": "colors.csv"} in attachments
    assert {"kind": "file", "name": "styles.csv", "summary": "styles.csv"} in attachments
    assert payload["data"]["native_delivery"] == "allowed"
    assert (
        hook_runtime.should_suppress_native_response(
            "feishu", True, attachments, payload["data"]["native_delivery"]
        )
        is True
    )


def test_completed_event_allows_card_only_for_input_file_context():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_1",
            "message_id": "m_1",
            "answer": "我读完了你修改的简历。几个观察：",
            "files": [
                {
                    "file_path": "/tmp/resume_260709.docx",
                    "filename": "resume_260709.docx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }
            ],
        },
    )

    attachments = payload["data"]["attachments"]
    assert {
        "kind": "file",
        "name": "resume_260709.docx",
        "summary": "resume_260709.docx",
    } in attachments
    assert payload["data"]["native_delivery"] == "allowed"
    assert (
        hook_runtime.should_suppress_native_response(
            "feishu", True, attachments, payload["data"]["native_delivery"]
        )
        is True
    )


def test_completed_event_extracts_hermes_media_files_for_native_delivery_guard():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_1",
            "message_id": "m_1",
            "answer": "视频已生成",
            "media_files": [
                {"path": "/tmp/demo.mp4", "mime_type": "video/mp4"},
                {"filename": "cover.png", "type": "image"},
            ],
        },
    )

    attachments = payload["data"]["attachments"]
    assert {"kind": "video", "name": "demo.mp4", "summary": "demo.mp4"} in attachments
    assert {"kind": "image", "name": "cover.png", "summary": "cover.png"} in attachments
    assert payload["data"]["native_delivery"] == "required"
    assert (
        hook_runtime.should_suppress_native_response(
            "feishu", True, attachments, payload["data"]["native_delivery"]
        )
        is False
    )


def test_completed_event_does_not_extract_url_paths_as_local_attachments():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_1",
            "message_id": "m_1",
            "answer": "参考 https://example.com/tmp/chart.png 和 /tmp/local.png",
        },
    )

    attachments = payload["data"]["attachments"]
    assert {"kind": "image", "name": "local.png", "summary": "local.png"} in attachments
    assert {"kind": "image", "name": "chart.png", "summary": "chart.png"} not in attachments


def test_open_request_uses_no_proxy_opener_for_local_sidecar(monkeypatch):
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b""

    class FakeOpener:
        def open(self, req, timeout):
            calls.append((req.full_url, timeout))
            return FakeResponse()

    def fail_urlopen(req, timeout):
        raise AssertionError("request.urlopen should not be used for sidecar calls")

    monkeypatch.setattr(hook_runtime, "_NO_PROXY_OPENER", FakeOpener(), raising=False)
    monkeypatch.setattr(hook_runtime.request, "urlopen", fail_urlopen)

    hook_runtime._open_request(
        hook_runtime.request.Request("http://127.0.0.1:8765/events"),
        0.8,
    )

    assert calls == [("http://127.0.0.1:8765/events", 0.8)]


def test_open_json_request_uses_default_urlopen_for_remote_sidecar(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true}'

    class FailingOpener:
        def open(self, req, timeout):
            raise AssertionError("remote sidecar requests may need the default proxy")

    def fake_urlopen(req, timeout):
        return FakeResponse()

    monkeypatch.setattr(hook_runtime, "_NO_PROXY_OPENER", FailingOpener())
    monkeypatch.setattr(hook_runtime.request, "urlopen", fake_urlopen)

    result = hook_runtime._open_json_request(
        hook_runtime.request.Request("https://sidecar.example.com/events"),
        0.8,
    )

    assert result == {"ok": True}


def test_completed_event_strips_trailing_attachment_punctuation_and_deduplicates():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_1",
            "message_id": "m_1",
            "answer": "附件 MEDIA:/tmp/report.pdf, 还有 MEDIA:/tmp/report.pdf）",
        },
    )

    assert payload["data"]["attachments"] == [
        {"kind": "file", "name": "report.pdf", "summary": "report.pdf"}
    ]


def test_native_media_only_response_removes_duplicate_text_but_keeps_directives():
    response = (
        "发你一张之前生成的图片。\n"
        "[[as_document]]\n"
        "MEDIA:/opt/data/image_cache/cafe.png\n"
        "/opt/data/report.pdf"
    )

    assert hook_runtime.native_media_only_response(response) == (
        "[[as_document]]\n"
        "MEDIA:/opt/data/image_cache/cafe.png\n"
        "/opt/data/report.pdf"
    )


def test_native_media_only_response_keeps_original_when_no_explicit_delivery_path():
    response = "视频已生成"

    assert hook_runtime.native_media_only_response(response) == response


def test_native_media_only_response_keeps_markdown_media_literal():
    response = "解释语法：`MEDIA:/tmp/example.png` 只是代码示例。"

    assert hook_runtime.native_media_only_response(response) == response


@pytest.mark.parametrize(
    ("platform", "delivered", "attachments", "expected"),
    [
        ("feishu", True, None, True),
        ("feishu", True, [], True),
        ("feishu", False, None, False),
        ("slack", True, None, False),
        ("feishu", True, [{"kind": "image", "name": "chart.png"}], False),
    ],
)
def test_should_suppress_native_response_requires_feishu_delivery_without_attachments(
    platform, delivered, attachments, expected
):
    assert (
        hook_runtime.should_suppress_native_response(platform, delivered, attachments)
        is expected
    )


def test_build_cron_event_from_feishu_job_origin():
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-1",
                "origin": {"platform": "feishu", "chat_id": "oc_cron"},
            },
            "delivery_content": "定时结果 MEDIA:/tmp/report.pdf",
        }
    )

    assert payload["event"] == "message.completed"
    assert payload["conversation_id"] == "job-1"
    assert payload["message_id"].startswith("cron_")
    assert payload["chat_id"] == "oc_cron"
    assert payload["platform"] == "feishu"
    assert payload["sequence"] == 0
    assert payload["data"]["answer"] == "定时结果 MEDIA:/tmp/report.pdf"
    assert payload["data"]["delivery_kind"] == "cron"
    assert payload["data"]["profile_id"] == "default"
    assert payload["data"]["profile_source"] == "fallback_default"
    assert {"kind": "file", "name": "report.pdf", "summary": "report.pdf"} in payload[
        "data"
    ]["attachments"]


def test_build_cron_event_extracts_chat_id_from_deliver_string():
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-deliver",
                "deliver": "feishu:oc_cron_from_deliver",
            },
            "delivery_content": "定时结果",
        }
    )

    assert payload is not None
    assert payload["chat_id"] == "oc_cron_from_deliver"
    assert payload["platform"] == "feishu"
    assert payload["data"]["delivery_kind"] == "cron"


def test_build_cron_event_prefers_cleaned_delivery_content():
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-1",
                "origin": {"platform": "feishu", "chat_id": "oc_cron"},
            },
            "content": "raw",
            "delivery_content": "delivery",
            "cleaned_delivery_content": "cleaned",
        }
    )

    assert payload["data"]["answer"] == "cleaned"


def test_build_cron_event_uses_auto_deliver_chat_id(monkeypatch):
    monkeypatch.setenv("HERMES_CRON_AUTO_DELIVER_CHAT_ID", "oc_env")

    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-env",
                "origin": {"platform": "feishu"},
            },
            "content": "定时结果",
        }
    )

    assert payload["chat_id"] == "oc_env"


def test_build_cron_event_prefers_explicit_deliver_and_resolved_feishu_target():
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-migrated",
                "deliver": "feishu",
                "origin": {"platform": "discord", "chat_id": "discord-channel"},
                "_hfc_resolved_targets": [
                    {"platform": "feishu", "chat_id": "oc_resolved"}
                ],
            },
            "content": "迁移后的定时任务结果",
        }
    )

    assert payload is not None
    assert payload["chat_id"] == "oc_resolved"
    assert payload["platform"] == "feishu"
    assert payload["data"]["answer"] == "迁移后的定时任务结果"


def test_build_cron_event_returns_none_for_non_feishu_or_missing_chat(monkeypatch):
    assert (
        hook_runtime.build_cron_event(
            {
                "job": {
                    "id": "job-slack",
                    "origin": {"platform": "slack", "chat_id": "oc_cron"},
                },
                "content": "result",
            }
        )
        is None
    )

    monkeypatch.delenv("HERMES_CRON_AUTO_DELIVER_CHAT_ID", raising=False)
    assert (
        hook_runtime.build_cron_event(
            {
                "job": {"id": "job-no-chat", "origin": {"platform": "feishu"}},
                "content": "result",
            }
        )
        is None
    )


def test_build_cron_event_deliver_origin_resolves_via_origin():
    """deliver="origin" should resolve through origin, not short-circuit."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-origin",
                "deliver": "origin",
                "origin": {"platform": "feishu", "chat_id": "oc_from_origin"},
            },
            "content": "定时结果",
        }
    )

    assert payload is not None
    assert payload["platform"] == "feishu"
    assert payload["chat_id"] == "oc_from_origin"
    assert payload["data"]["answer"] == "定时结果"


def test_build_cron_event_deliver_all_resolves_via_origin():
    """deliver="all" should resolve through origin when no resolved targets."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-all",
                "deliver": "all",
                "origin": {"platform": "feishu", "chat_id": "oc_from_all"},
            },
            "content": "all deliver result",
        }
    )

    assert payload is not None
    assert payload["platform"] == "feishu"
    assert payload["chat_id"] == "oc_from_all"


def test_build_cron_event_preserves_extracted_media_for_native_delivery():
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-attachment",
                "deliver": "origin",
                "origin": {"platform": "feishu", "chat_id": "oc_attachment"},
            },
            "content": "报告已生成 MEDIA:/tmp/report.pdf",
            "delivery_content": (
                "Cronjob Response: report\n\n"
                "报告已生成 MEDIA:/tmp/report.pdf"
            ),
            "cleaned_delivery_content": (
                "Cronjob Response: report\n\n报告已生成"
            ),
            "media_files": [("/tmp/report.pdf", False)],
        }
    )

    assert payload is not None
    assert payload["data"]["answer"] == "Cronjob Response: report\n\n报告已生成"
    assert payload["data"]["attachments"] == [
        {"kind": "file", "name": "report.pdf", "summary": "report.pdf"}
    ]
    assert payload["data"]["native_delivery"] == "required"


def test_build_cron_event_deliver_origin_all_comma_resolves_via_origin():
    """deliver="origin,all" should resolve through origin."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-combo",
                "deliver": "origin,all",
                "origin": {"platform": "feishu", "chat_id": "oc_combo"},
            },
            "content": "combo result",
        }
    )

    assert payload is not None
    assert payload["platform"] == "feishu"
    assert payload["chat_id"] == "oc_combo"


def test_build_cron_event_deliver_origin_with_resolved_targets():
    """deliver="origin" with explicit resolved targets should prefer targets."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-resolved",
                "deliver": "origin",
                "origin": {"platform": "feishu", "chat_id": "oc_origin"},
                "_hfc_resolved_targets": [
                    {"platform": "feishu", "chat_id": "oc_resolved"}
                ],
            },
            "content": "resolved result",
        }
    )

    assert payload is not None
    assert payload["platform"] == "feishu"
    assert payload["chat_id"] == "oc_resolved"


def test_build_cron_event_accepts_deliver_dict():
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-deliver-dict",
                "deliver": {"platform": "feishu", "chat_id": "oc_from_dict"},
                "origin": {"platform": "discord", "chat_id": "dc_should_not_leak"},
            },
            "content": "dict deliver result",
        }
    )

    assert payload is not None
    assert payload["platform"] == "feishu"
    assert payload["chat_id"] == "oc_from_dict"


def test_build_cron_event_ignores_non_feishu_origin_chat_for_feishu_platform(monkeypatch):
    monkeypatch.delenv("HERMES_CRON_AUTO_DELIVER_CHAT_ID", raising=False)
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-non-feishu-origin",
                "deliver": "feishu",
                "origin": {"platform": "discord", "chat_id": "dc_should_not_leak"},
            },
            "content": "non-feishu origin result",
        }
    )

    assert payload is None


def test_build_cron_event_deliver_local_returns_none():
    """deliver="local" should return None (no delivery)."""
    assert (
        hook_runtime.build_cron_event(
            {
                "job": {
                    "id": "job-local",
                    "deliver": "local",
                    "origin": {"platform": "feishu", "chat_id": "oc_local"},
                },
                "content": "local result",
            }
        )
        is None
    )


def test_build_cron_event_mixed_intent_and_platform():
    """deliver="origin,feishu:oc_explicit" keeps the real platform."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-mixed",
                "deliver": "origin,feishu:oc_explicit",
                "origin": {"platform": "discord", "chat_id": "dc_123"},
            },
            "content": "mixed result",
        }
    )

    assert payload is not None
    assert payload["platform"] == "feishu"
    assert payload["chat_id"] == "oc_explicit"


# --- build_cron_event thread_id tests (issue #90) ---


def test_build_cron_event_carries_thread_id_from_origin():
    """thread_id from job origin should propagate to the cron event payload."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-thread",
                "deliver": "origin",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_topic_group",
                    "thread_id": "omt_abc123",
                },
            },
            "content": "cron output in thread",
        }
    )

    assert payload is not None
    assert payload["thread_id"] == "omt_abc123"
    assert payload["chat_id"] == "oc_topic_group"
    # conversation_id should use thread_id when available
    assert payload["conversation_id"] == "omt_abc123"


def test_build_cron_event_carries_thread_id_from_resolved_targets():
    """thread_id from resolved delivery targets should propagate."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-resolved-thread",
                "deliver": "origin",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_group",
                },
                "_hfc_resolved_targets": [
                    {
                        "platform": "feishu",
                        "chat_id": "oc_group",
                        "thread_id": "omt_from_target",
                    }
                ],
            },
            "content": "resolved target thread",
        }
    )

    assert payload is not None
    assert payload["thread_id"] == "omt_from_target"
    assert payload["chat_id"] == "oc_group"
    assert payload["conversation_id"] == "omt_from_target"


def test_build_cron_event_resolved_target_thread_takes_priority_over_origin():
    """Resolved target thread_id should take priority over origin thread_id."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-priority",
                "deliver": "origin",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_group",
                    "thread_id": "omt_origin_thread",
                },
                "_hfc_resolved_targets": [
                    {
                        "platform": "feishu",
                        "chat_id": "oc_group",
                        "thread_id": "omt_resolved_thread",
                    }
                ],
            },
            "content": "priority test",
        }
    )

    assert payload is not None
    assert payload["thread_id"] == "omt_resolved_thread"


def test_build_cron_event_no_thread_id_without_origin_thread():
    """When origin has no thread_id, event should have empty thread_id."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-no-thread",
                "deliver": "origin",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_dm_group",
                },
            },
            "content": "no thread",
        }
    )

    assert payload is not None
    assert payload["thread_id"] == ""
    assert payload["chat_id"] == "oc_dm_group"
    # conversation_id falls back to job id when no thread_id
    assert payload["conversation_id"] == "job-no-thread"


def test_build_cron_event_thread_id_from_env_var(monkeypatch):
    """HERMES_CRON_AUTO_DELIVER_THREAD_ID env var should be used as fallback."""
    monkeypatch.setenv("HERMES_CRON_AUTO_DELIVER_THREAD_ID", "omt_env_thread")

    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-env-thread",
                "deliver": "feishu:oc_group",
                "origin": {},
            },
            "content": "env thread test",
        }
    )

    assert payload is not None
    assert payload["thread_id"] == "omt_env_thread"
    assert payload["conversation_id"] == "omt_env_thread"


def test_build_cron_event_origin_thread_takes_priority_over_env(monkeypatch):
    """Origin thread_id should take priority over env var."""
    monkeypatch.setenv("HERMES_CRON_AUTO_DELIVER_THREAD_ID", "omt_env")

    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-origin-vs-env",
                "deliver": "origin",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_group",
                    "thread_id": "omt_origin",
                },
            },
            "content": "origin beats env",
        }
    )

    assert payload is not None
    assert payload["thread_id"] == "omt_origin"


def test_build_cron_event_non_feishu_thread_in_resolved_targets():
    """Non-feishu platform targets should not contribute thread_id to feishu event."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-non-feishu-thread",
                "deliver": "feishu:oc_group",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_group",
                },
                "_hfc_resolved_targets": [
                    {
                        "platform": "telegram",
                        "chat_id": "-1001234",
                        "thread_id": "12345",
                    },
                    {
                        "platform": "feishu",
                        "chat_id": "oc_group",
                    },
                ],
            },
            "content": "multi-platform",
        }
    )

    assert payload is not None
    # Telegram thread_id should NOT leak into feishu event
    assert payload["thread_id"] == ""


def test_build_cron_event_non_feishu_origin_thread_does_not_leak():
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-telegram-origin-thread",
                "deliver": "feishu:oc_group",
                "origin": {
                    "platform": "telegram",
                    "chat_id": "-1001234",
                    "thread_id": "12345",
                },
            },
            "content": "deliver to feishu",
        }
    )

    assert payload is not None
    assert payload["chat_id"] == "oc_group"
    assert payload["thread_id"] == ""
    assert payload["conversation_id"] == "job-telegram-origin-thread"


def test_build_cron_event_om_prefix_thread_id():
    """thread_id with om_ prefix (older format) should also work."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-om-thread",
                "deliver": "origin",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_group",
                    "thread_id": "om_older_format_123",
                },
            },
            "content": "om prefix test",
        }
    )

    assert payload is not None
    assert payload["thread_id"] == "om_older_format_123"
    assert payload["conversation_id"] == "om_older_format_123"


def test_build_cron_event_empty_thread_id_in_origin():
    """Empty string thread_id in origin should result in empty thread_id."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-empty-thread",
                "deliver": "origin",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_group",
                    "thread_id": "",
                },
            },
            "content": "empty thread",
        }
    )

    assert payload is not None
    assert payload["thread_id"] == ""
    assert payload["conversation_id"] == "job-empty-thread"


def test_build_cron_event_none_thread_id_in_origin():
    """None thread_id in origin should result in empty thread_id."""
    payload = hook_runtime.build_cron_event(
        {
            "job": {
                "id": "job-none-thread",
                "deliver": "origin",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_group",
                    "thread_id": None,
                },
            },
            "content": "none thread",
        }
    )

    assert payload is not None
    assert payload["thread_id"] == ""
    assert payload["conversation_id"] == "job-none-thread"


def test_is_routing_intent():
    assert hook_runtime._is_routing_intent("origin") is True
    assert hook_runtime._is_routing_intent("all") is True
    # "local" is NOT a routing intent — it's a delivery target
    assert hook_runtime._is_routing_intent("local") is False
    assert hook_runtime._is_routing_intent("origin,all") is True
    assert hook_runtime._is_routing_intent("all,origin") is True
    assert hook_runtime._is_routing_intent("feishu") is False
    assert hook_runtime._is_routing_intent("feishu:oc_123") is False
    assert hook_runtime._is_routing_intent("") is False
    # Mixed combo with a real platform should NOT be a routing intent
    assert hook_runtime._is_routing_intent("origin,feishu:oc_123") is False


def test_extract_real_platform():
    assert hook_runtime._extract_real_platform("origin") == ""
    assert hook_runtime._extract_real_platform("all") == ""
    assert hook_runtime._extract_real_platform("local") == "local"
    assert hook_runtime._extract_real_platform("feishu") == "feishu"
    assert hook_runtime._extract_real_platform("feishu:oc_123") == "feishu"
    assert hook_runtime._extract_real_platform({"platform": "feishu"}) == "feishu"
    assert hook_runtime._extract_real_platform("origin,feishu:oc_123") == "feishu"
    assert hook_runtime._extract_real_platform("origin,all") == ""
    assert hook_runtime._extract_real_platform("") == ""
    assert hook_runtime._extract_real_platform(None) == ""


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


def test_completed_event_uses_agent_result_final_response_when_response_is_empty():
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "chat_id": "oc_abc",
            "message_id": "msg_1",
            "response": "",
            "agent_result": {"final_response": "DeepSeek 一次性返回的最终答案"},
        },
    )

    assert payload["data"]["answer"] == "DeepSeek 一次性返回的最终答案"


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


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\USER493274\AppData\Local\hermes\profiles\thinking",
        "C:/Users/USER493274/AppData/Local/hermes/profiles/thinking",
        r"C:\Users\USER493274\.hermes\profiles\thinking",
    ],
)
def test_profile_from_path_supports_windows_hermes_profile_paths(path):
    assert hook_runtime._profile_from_path(path) == "thinking"


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


def test_build_event_preview_does_not_advance_sequence_or_retire_fallback():
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}

    started = hook_runtime.build_event("message.started", local_vars)
    preview = hook_runtime.build_event(
        "message.completed",
        {**local_vars, "answer": "结果 MEDIA:/tmp/report.pdf"},
        preview=True,
    )
    completed = hook_runtime.build_event(
        "message.completed", {**local_vars, "answer": "结果"}
    )

    assert preview is not None
    assert preview["message_id"] == started["message_id"]
    assert preview["sequence"] == 1
    assert {"kind": "file", "name": "report.pdf", "summary": "report.pdf"} in preview[
        "data"
    ]["attachments"]
    assert completed is not None
    assert completed["message_id"] == started["message_id"]
    assert completed["sequence"] == 1


def test_preview_fallback_matches_active_fallback_without_created_at():
    key = ("conv_abc", "oc_abc")
    cache_key = hook_runtime._new_fallback_cache_key(key, None)

    active = hook_runtime._create_active_fallback_message_id(
        key, cache_key, "conv_abc", "oc_abc", None
    )
    preview = hook_runtime._preview_fallback_message_id(
        key, "conv_abc", "oc_abc", None
    )

    assert preview == active


def test_attachment_guard_uses_preview_before_terminal_emit_retires_fallback():
    local_vars = {"chat_id": "oc_abc", "conversation_id": "conv_abc"}
    payload_locals = {**local_vars, "answer": "结果 MEDIA:/tmp/report.pdf"}

    hook_runtime.build_event("message.started", local_vars)
    preview = hook_runtime.build_event(
        "message.completed", payload_locals, preview=True
    )
    delivered = hook_runtime.build_event("message.completed", payload_locals) is not None
    attachments = preview["data"]["attachments"] if preview is not None else []

    assert attachments == [
        {"kind": "file", "name": "report.pdf", "summary": "report.pdf"}
    ]
    assert preview["data"]["native_delivery"] == "required"
    assert (
        hook_runtime.should_suppress_native_response(
            "feishu", delivered, attachments, preview["data"]["native_delivery"]
        )
        is False
    )


@pytest.mark.asyncio
async def test_async_terminal_emit_uses_sidecar_applied_response(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    responses = iter(
        [
            {"ok": True, "applied": True},
            {"ok": True, "applied": False},
            {"ok": False, "error": "session not found"},
        ]
    )

    async def fake_post_response(url, payload, timeout):
        assert url == "http://sidecar.test/events"
        assert payload["event"] == "message.completed"
        return next(responses)

    async def fail_legacy_post(url, payload, timeout):
        raise AssertionError("terminal emit should read sidecar JSON response")

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post_response)
    monkeypatch.setattr(hook_runtime, "_post_json_ordered", fail_legacy_post)

    local_vars = {
        "chat_id": "oc_abc",
        "message_id": "msg_1",
        "answer": "最终答案",
    }

    assert await hook_runtime.emit_from_hermes_locals_async(
        local_vars, event_name="message.completed"
    )
    assert not await hook_runtime.emit_from_hermes_locals_async(
        local_vars, event_name="message.completed"
    )
    assert not await hook_runtime.emit_from_hermes_locals_async(
        local_vars, event_name="message.completed"
    )


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


def test_completed_event_defers_native_ack_until_exact_base_finalization():
    event = SimpleNamespace()
    payload = hook_runtime.build_event(
        "message.completed",
        {
            "platform": "feishu",
            "chat_id": "oc_private",
            "conversation_id": "conversation-private",
            "message_id": "message-private",
            "event": event,
            "answer": "final answer",
            "_hfc_delivery_obligation_id": "obligation-private",
            "created_at": 1777017600.0,
        },
    )

    metadata = payload["data"]["native_handoff"]
    assert metadata["generation"] == getattr(
        event, "_hfc_native_handoff_generation"
    )
    assert len(metadata["generation"]) == 32
    assert set(metadata) == {"generation"}


@pytest.mark.parametrize(
    ("local_vars", "expected"),
    [
        ({"platform": "feishu", "answer": "final", "agent_result": {}}, True),
        (
            {
                "platform": "feishu",
                "answer": "final",
                "agent_result": {"already_sent": True},
            },
            False,
        ),
        ({"platform": "telegram", "answer": "final", "agent_result": {}}, False),
        ({"platform": "feishu", "answer": "", "agent_result": {}}, False),
    ],
)
def test_exact_base_staging_is_limited_to_unsent_feishu_final_text(
    monkeypatch,
    local_vars,
    expected,
):
    monkeypatch.setattr(
        hook_runtime,
        "_exact_base_delivery_hook_available",
        lambda: True,
    )
    assert hook_runtime.can_stage_exact_base_completion(local_vars) is expected


@pytest.mark.asyncio
async def test_exact_base_completion_stages_terminal_without_posting(monkeypatch):
    posted = []

    async def fake_post(*args, **kwargs):
        posted.append((args, kwargs))
        return {"ok": True, "applied": True}

    async def no_pending(_local_vars):
        return None

    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_async",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=hook_runtime._PolicyGateResult(True, None),
        ),
    )
    monkeypatch.setattr(
        hook_runtime,
        "_flush_pending_deltas_for_local_vars",
        no_pending,
    )
    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post)

    staged = await hook_runtime.stage_message_completed_from_hermes_locals_async(
        {
            "platform": "feishu",
            "chat_id": "oc_exact",
            "conversation_id": "conversation-exact",
            "message_id": "message-exact",
            "answer": "raw MEDIA:/tmp/private.png",
            "created_at": 1777017600.0,
        }
    )

    assert staged is True
    assert posted == []
    stage = hook_runtime._HFC_EXACT_COMPLETION_STAGE.get()
    assert stage["payload"]["data"]["answer"] == "raw"


@pytest.mark.asyncio
async def test_exact_base_text_only_finalizer_posts_same_ledger_text_and_obligation(
    monkeypatch,
):
    async def no_pending(_local_vars):
        return None

    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_async",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=hook_runtime._PolicyGateResult(True, None),
        ),
    )
    monkeypatch.setattr(
        hook_runtime,
        "_flush_pending_deltas_for_local_vars",
        no_pending,
    )
    assert await hook_runtime.stage_message_completed_from_hermes_locals_async(
        {
            "platform": "feishu",
            "chat_id": "oc_exact",
            "conversation_id": "conversation-exact",
            "message_id": "message-exact",
            "answer": "raw MEDIA:/tmp/private.png",
            "created_at": 1777017600.0,
        }
    )
    posted = []

    async def fake_post(_url, payload, _timeout):
        posted.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post)
    monkeypatch.setattr(
        hook_runtime,
        "_native_handoff_plan_fingerprint",
        lambda _adapter: "f" * 64,
    )
    monkeypatch.setattr(
        hook_runtime,
        "_native_handoff_runtime_wrappers_ready",
        lambda _adapter: True,
    )
    adapter = SimpleNamespace(name="feishu")
    returned = await hook_runtime.prepare_exact_base_final_delivery(
        {
            "delivery_adapter": adapter,
            "content": "exact ledger text",
            "obligation_id": "obligation-private",
            "reply_to": "om_parent",
            "metadata": {"thread_id": "omt_topic", "notify": True},
            "images": [],
            "local_files": [],
            "media_files": [],
        }
    )

    returned_adapter, content, reply_to, metadata = returned
    exact = posted[0]
    handoff = exact["data"]["native_handoff"]
    assert content == "exact ledger text"
    assert reply_to == "om_parent"
    assert metadata == {"thread_id": "omt_topic", "notify": True}
    assert returned_adapter is not adapter
    assert (await returned_adapter._send_with_retry()).success is True
    assert exact["data"]["answer"] == "exact ledger text"
    assert exact["data"]["native_delivery"] == "allowed"
    assert exact["data"]["attachments"] == []
    assert handoff["capabilities"] == [
        "native-ack-v2",
        "stable-feishu-uuid-v2",
        "exact-base-delivery-v1",
    ]
    assert handoff["obligation_key"] == hook_runtime._native_handoff_obligation_key(
        "obligation-private"
    )
    assert handoff["content_hash"] == hook_runtime._native_handoff_content_hash(
        "exact ledger text"
    )
    assert handoff["plan_fingerprint"] == "f" * 64
    assert handoff["route"] == "thread-create"
    assert hook_runtime._HFC_EXACT_COMPLETION_STAGE.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("images", [("https://private.test/chart.png", "chart")]),
        ("local_files", ["/tmp/private/report.pdf"]),
        ("media_files", [("/tmp/private/demo.mp4", False)]),
    ],
)
async def test_exact_base_attachments_never_advertise_ack_capability(
    monkeypatch,
    field,
    value,
):
    async def no_pending(_local_vars):
        return None

    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_async",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=hook_runtime._PolicyGateResult(True, None),
        ),
    )
    monkeypatch.setattr(
        hook_runtime,
        "_flush_pending_deltas_for_local_vars",
        no_pending,
    )
    assert await hook_runtime.stage_message_completed_from_hermes_locals_async(
        {
            "platform": "feishu",
            "chat_id": "oc_exact",
            "conversation_id": "conversation-exact",
            "message_id": "message-exact",
            "answer": "final with attachment",
            "created_at": 1777017600.0,
        }
    )
    posted = []

    async def fake_terminal_post(_url, payload, _timeout):
        posted.append(payload)
        return {"ok": True, "applied": False, "disposition": "native"}

    async def absent_recovery(_url, _payload, _timeout):
        return {"ok": True, "found": False}

    monkeypatch.setattr(
        hook_runtime,
        "_post_json_ordered_response",
        fake_terminal_post,
    )
    monkeypatch.setattr(hook_runtime, "_post_json_response", absent_recovery)
    monkeypatch.setattr(
        hook_runtime,
        "_native_handoff_plan_fingerprint",
        lambda _adapter: "f" * 64,
    )
    monkeypatch.setattr(
        hook_runtime,
        "_native_handoff_runtime_wrappers_ready",
        lambda _adapter: True,
    )
    adapter = SimpleNamespace(name="feishu")

    returned = await hook_runtime.prepare_exact_base_final_delivery(
        {
            "delivery_adapter": adapter,
            "content": "exact ledger text",
            "obligation_id": "obligation-private",
            "reply_to": None,
            "metadata": {},
            "images": [],
            "local_files": [],
            "media_files": [],
            field: value,
        }
    )

    assert returned[0] is adapter
    handoff = posted[0]["data"]["native_handoff"]
    assert set(handoff) == {"generation"}
    assert posted[0]["data"]["native_delivery"] == "required"
    assert posted[0]["data"]["attachments"]
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile_id", "profile_source", "delivery_kind", "expected_ack"),
    [
        ("default", None, "", True),
        ("default", None, "cron", False),
        ("work", None, "", False),
        ("default", "sanitized_locals", "", False),
    ],
)
async def test_exact_base_ack_is_limited_to_verified_default_profile(
    monkeypatch,
    profile_id,
    profile_source,
    delivery_kind,
    expected_ack,
):
    async def no_pending(_local_vars):
        return None

    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_async",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=hook_runtime._PolicyGateResult(True, None),
        ),
    )
    monkeypatch.setattr(
        hook_runtime,
        "_flush_pending_deltas_for_local_vars",
        no_pending,
    )
    assert await hook_runtime.stage_message_completed_from_hermes_locals_async(
        {
            "platform": "feishu",
            "chat_id": "oc_exact",
            "conversation_id": "conversation-exact",
            "message_id": "message-exact",
            "profile_id": profile_id,
            "answer": "profile-scoped final",
            "created_at": 1777017600.0,
            "delivery_kind": delivery_kind,
        }
    )
    if profile_source is not None:
        hook_runtime._HFC_EXACT_COMPLETION_STAGE.get()["payload"]["data"][
            "profile_source"
        ] = profile_source
    posted = []
    registrations = []

    async def fake_post(_url, payload, _timeout):
        posted.append(payload)
        return {"ok": True, "applied": False, "disposition": "native"}

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post)
    monkeypatch.setattr(
        hook_runtime,
        "_native_handoff_plan_fingerprint",
        lambda _adapter: "f" * 64,
    )
    monkeypatch.setattr(
        hook_runtime,
        "_native_handoff_runtime_wrappers_ready",
        lambda _adapter: True,
    )
    monkeypatch.setattr(
        hook_runtime,
        "_register_native_handoff_descriptor",
        lambda payload, result: registrations.append((payload, result)) or True,
    )

    await hook_runtime.prepare_exact_base_final_delivery(
        {
            "delivery_adapter": SimpleNamespace(name="feishu"),
            "content": "profile-scoped final",
            "obligation_id": "obligation-private",
            "metadata": {},
            "images": [],
            "local_files": [],
            "media_files": [],
            "delivery_kind": delivery_kind,
        }
    )

    handoff = posted[0]["data"]["native_handoff"]
    assert ("capabilities" in handoff) is expected_ack
    assert len(registrations) == int(expected_ack)
    if not expected_ack:
        assert set(handoff) == {"generation"}


@pytest.mark.parametrize(
    "scope_mutation",
    [
        {"event": "message.failed"},
        {"delivery_kind": "cron"},
        {"delivery_kind": "command"},
        {"attachments": [{"kind": "image", "name": "private.png"}]},
        {"native_delivery": "required"},
        {"attachments": None},
        {"native_delivery": None},
    ],
)
def test_gateway_rejects_exact_binding_outside_ordinary_text_scope(
    scope_mutation,
):
    answer = "ordinary exact final"
    obligation_key = "a" * 64
    content_hash = hook_runtime._native_handoff_content_hash(answer)
    plan_fingerprint = "b" * 64
    route = "create"
    target_hash = hook_runtime.derive_native_handoff_target_hash(
        profile_id="default",
        chat_id="oc_exact",
        thread_id="",
        route=route,
    )
    metadata = {
        "generation": "c" * 32,
        "capabilities": [
            "native-ack-v2",
            "stable-feishu-uuid-v2",
            "exact-base-delivery-v1",
        ],
        "obligation_key": obligation_key,
        "content_hash": content_hash,
        "plan_fingerprint": plan_fingerprint,
        "route": route,
        "target_hash": target_hash,
        "provisional_uuid_seed": hook_runtime.derive_native_handoff_uuid_seed(
            obligation_key=obligation_key,
            content_hash=content_hash,
            plan_fingerprint=plan_fingerprint,
            route=route,
            target_hash=target_hash,
        ),
    }
    data = {
        "answer": answer,
        "attachments": [],
        "native_delivery": "allowed",
        "profile_id": "default",
        "profile_source": "fallback_default",
        "native_handoff": metadata,
    }
    for field, value in scope_mutation.items():
        if field == "event":
            continue
        if value is None:
            data.pop(field, None)
        else:
            data[field] = value
    payload = {
        "event": scope_mutation.get("event", "message.completed"),
        "chat_id": "oc_exact",
        "thread_id": "",
        "data": data,
    }

    assert hook_runtime._native_handoff_binding_from_payload(payload) is None


@pytest.mark.asyncio
async def test_exact_base_no_text_finalizer_never_advertises_ack(monkeypatch):
    async def no_pending(_local_vars):
        return None

    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_async",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=hook_runtime._PolicyGateResult(True, None),
        ),
    )
    monkeypatch.setattr(
        hook_runtime,
        "_flush_pending_deltas_for_local_vars",
        no_pending,
    )
    assert await hook_runtime.stage_message_completed_from_hermes_locals_async(
        {
            "platform": "feishu",
            "chat_id": "oc_exact",
            "conversation_id": "conversation-exact",
            "message_id": "message-exact",
            "answer": "MEDIA:/tmp/private.png",
            "created_at": 1777017600.0,
        }
    )
    posted = []

    async def fake_post(_url, payload, _timeout):
        posted.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post)
    await hook_runtime.finalize_exact_base_no_text(
        {
            "text_content": "",
            "images": [],
            "local_files": [],
            "media_files": [("/tmp/private.png", False)],
        }
    )

    handoff = posted[0]["data"]["native_handoff"]
    assert set(handoff) == {"generation"}
    assert posted[0]["data"]["answer"] == ""
    assert posted[0]["data"]["native_delivery"] == "required"
    assert posted[0]["data"]["attachments"][0]["name"] == "private.png"
    assert "/tmp/private" not in json.dumps(posted[0])
    assert hook_runtime._HFC_EXACT_COMPLETION_STAGE.get() is None


class _NativeAckResponse:
    def __init__(self, success=True, *, code=0, msg="", message_id="om_result"):
        self._success = success
        self.code = code
        self.msg = msg
        self.data = SimpleNamespace(message_id=message_id)

    def success(self):
        return self._success


class _NativeAckAdapter:
    name = "feishu"
    MAX_MESSAGE_LENGTH = 5

    def __init__(self, outcomes=None):
        self._client = object()
        self.outcomes = list(outcomes or [])
        self.raw_calls = []

    def format_message(self, content):
        return content

    def truncate_message(self, content, size):
        return [content[index : index + size] for index in range(0, len(content), size)]

    def _build_outbound_payload(self, chunk, *, prefer_post=False):
        msg_type = "post" if prefer_post else "text"
        return msg_type, json.dumps({"text": chunk})

    @staticmethod
    def _build_reply_message_body(*, content, msg_type, reply_in_thread, uuid_value):
        return SimpleNamespace(
            content=content,
            msg_type=msg_type,
            reply_in_thread=reply_in_thread,
            uuid=uuid_value,
        )

    @staticmethod
    def _build_create_message_body(*, receive_id, msg_type, content, uuid_value):
        return SimpleNamespace(
            receive_id=receive_id,
            msg_type=msg_type,
            content=content,
            uuid=uuid_value,
        )

    async def _send_raw_message(self, *, chat_id, msg_type, payload, reply_to, metadata):
        if reply_to:
            body = self._build_reply_message_body(
                content=payload,
                msg_type=msg_type,
                reply_in_thread=bool((metadata or {}).get("thread_id")),
                uuid_value="random-reply",
            )
            route = "thread" if (metadata or {}).get("thread_id") else "reply"
        else:
            receive_id = (metadata or {}).get("thread_id") or chat_id
            body = self._build_create_message_body(
                receive_id=receive_id,
                msg_type=msg_type,
                content=payload,
                uuid_value="random-create",
            )
            route = "thread" if (metadata or {}).get("thread_id") else "create"
        self.raw_calls.append((payload, msg_type, route, body.uuid))
        outcome = self.outcomes.pop(0) if self.outcomes else True
        return _NativeAckResponse(success=outcome)

    async def _feishu_send_with_retry(
        self, *, chat_id, msg_type, payload, reply_to, metadata
    ):
        return await self._send_raw_message(
            chat_id=chat_id,
            msg_type=msg_type,
            payload=payload,
            reply_to=reply_to,
            metadata=metadata,
        )

    def _response_succeeded(self, response):
        return response.success()

    def _finalize_send_result(self, response, _message):
        return SimpleNamespace(
            success=bool(response and response.success()),
            message_id="om_result" if response and response.success() else None,
            error="" if response and response.success() else "send failed",
        )

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        chunks = self.truncate_message(self.format_message(content), self.MAX_MESSAGE_LENGTH)
        last_response = None
        for chunk in chunks:
            msg_type, payload = self._build_outbound_payload(chunk, prefer_post=False)
            last_response = await self._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type=msg_type,
                payload=payload,
                reply_to=reply_to,
                metadata=metadata,
            )
        return self._finalize_send_result(last_response, "send failed")


def test_native_handoff_plan_fingerprint_binds_loaded_runtime_semantics(monkeypatch):
    adapter = _NativeAckAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)

    first = hook_runtime._native_handoff_plan_fingerprint(adapter)
    adapter_module = sys.modules[type(adapter).__module__]
    monkeypatch.setattr(
        adapter_module,
        "_NATIVE_ACK_PLAN_RE",
        re.compile(r"changed runtime regex", re.IGNORECASE),
    )
    second = hook_runtime._native_handoff_plan_fingerprint(adapter)

    helpers = sys.modules["gateway.platforms.helpers"]

    def changed_strip_markdown(text):
        return str(text).replace("*", "").strip()

    changed_strip_markdown.__module__ = helpers.__name__
    helpers.strip_markdown = changed_strip_markdown
    third = hook_runtime._native_handoff_plan_fingerprint(adapter)

    assert all(len(value) == 64 for value in (first, second, third))
    assert len({first, second, third}) == 3
    assert len(hook_runtime._NATIVE_HANDOFF_PLAN_FINGERPRINTS) >= 3


def test_native_handoff_plan_fingerprint_fails_closed_without_loaded_helper(
    monkeypatch,
):
    adapter = _NativeAckAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    monkeypatch.delitem(sys.modules, "gateway.platforms.helpers", raising=False)

    assert hook_runtime._native_handoff_plan_fingerprint(adapter) == ""


class _NativeAckPostFallbackAdapter(_NativeAckAdapter):
    async def _feishu_send_with_retry(
        self, *, chat_id, msg_type, payload, reply_to, metadata
    ):
        response = await self._send_raw_message(
            chat_id=chat_id,
            msg_type=msg_type,
            payload=payload,
            reply_to=reply_to,
            metadata=metadata,
        )
        if msg_type == "post" and not response.success():
            response.msg = "invalid post payload"
        return response

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        post = await self._feishu_send_with_retry(
            chat_id=chat_id,
            msg_type="post",
            payload=json.dumps({"post": content}),
            reply_to=reply_to,
            metadata=metadata,
        )
        if not post.success():
            text = await self._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type="text",
                payload=json.dumps({"text": content}),
                reply_to=reply_to,
                metadata=metadata,
            )
            return self._finalize_send_result(text, "send failed")
        return self._finalize_send_result(post, "send failed")


class _NativeAckRaisingAdapter(_NativeAckAdapter):
    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise RuntimeError("adapter escaped")


def _install_native_ack_context(
    adapter,
    content,
    *,
    expires_at=None,
    obligation_key="",
    thread_id="",
):
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    obligation_key = obligation_key or hook_runtime._native_handoff_obligation_key(
        "test-obligation"
    )
    plan_fingerprint = hook_runtime._native_handoff_plan_fingerprint(adapter)
    assert len(plan_fingerprint) == 64
    route = "thread-create" if thread_id else "create"
    target_hash = hook_runtime.derive_native_handoff_target_hash(
        profile_id="default",
        chat_id="oc_native",
        thread_id=thread_id,
        route=route,
    )
    content_hash = hook_runtime._native_handoff_content_hash(content)
    uuid_seed = hook_runtime.derive_native_handoff_uuid_seed(
        obligation_key=obligation_key,
        content_hash=content_hash,
        plan_fingerprint=plan_fingerprint,
        route=route,
        target_hash=target_hash,
    )
    descriptor = {
        "protocol": "hfc-native-handoff-v2",
        "id": "a" * 64,
        "uuid_seed": uuid_seed,
        "expires_at": expires_at or (time.time() + 3600),
    }
    payload = {
        "event": "message.completed",
        "chat_id": "oc_native",
        "thread_id": thread_id,
        "data": {
            "answer": content,
            "attachments": [],
            "native_delivery": "allowed",
            "profile_id": "default",
            "profile_source": "fallback_default",
            "native_handoff": {
                "capabilities": [
                    "native-ack-v2",
                    "stable-feishu-uuid-v2",
                    "exact-base-delivery-v1",
                ],
                "obligation_key": obligation_key,
                "content_hash": content_hash,
                "plan_fingerprint": plan_fingerprint,
                "route": route,
                "target_hash": target_hash,
                "provisional_uuid_seed": uuid_seed,
            },
        },
    }
    registered = hook_runtime._register_native_handoff_descriptor(
        payload,
        {
            "ok": True,
            "applied": False,
            "disposition": "native",
            "native_handoff": descriptor,
        },
    )
    assert registered is (descriptor["expires_at"] > time.time())
    return descriptor


def test_exact_native_handoff_rejects_old_sidecar_v1_descriptor():
    adapter = _NativeAckAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    content = "rolling-upgrade answer"
    payload = {
        "event": "message.completed",
        "chat_id": "oc_native",
        "data": {
            "answer": content,
            "native_handoff": {
                "capabilities": [
                    "native-ack-v2",
                    "stable-feishu-uuid-v2",
                    "exact-base-delivery-v1",
                ],
                "obligation_key": hook_runtime._native_handoff_obligation_key(
                    "rolling-upgrade-obligation"
                ),
                "content_hash": hook_runtime._native_handoff_content_hash(content),
                "plan_fingerprint": hook_runtime._native_handoff_plan_fingerprint(
                    adapter
                ),
                "route": "create",
            },
        },
    }

    registered = hook_runtime._register_native_handoff_descriptor(
        payload,
        {
            "ok": True,
            "applied": False,
            "disposition": "native",
            "native_handoff": {
                "protocol": "hfc-native-handoff-v1",
                "id": "a" * 64,
                "uuid_seed": "b" * 32,
                "expires_at": time.time() + 3600,
            },
        },
    )

    assert registered is False
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is None


@pytest.mark.parametrize(
    "ok_value",
    [pytest.param(None, id="missing"), False, "true", 1],
)
def test_exact_native_handoff_requires_explicit_boolean_ok(ok_value):
    adapter = _NativeAckAdapter()
    runner = SimpleNamespace(adapters={"feishu": adapter})
    assert hook_runtime.install_feishu_command_card_adapter_methods(runner)
    content = "explicit commit answer"
    payload = {
        "event": "message.completed",
        "chat_id": "oc_native",
        "data": {
            "answer": content,
            "native_handoff": {
                "capabilities": [
                    "native-ack-v2",
                    "stable-feishu-uuid-v2",
                    "exact-base-delivery-v1",
                ],
                "obligation_key": hook_runtime._native_handoff_obligation_key(
                    "explicit-commit-obligation"
                ),
                "content_hash": hook_runtime._native_handoff_content_hash(content),
                "plan_fingerprint": hook_runtime._native_handoff_plan_fingerprint(
                    adapter
                ),
                "route": "create",
            },
        },
    }
    result = {
        "applied": False,
        "disposition": "native",
        "native_handoff": {
            "protocol": "hfc-native-handoff-v2",
            "id": "a" * 64,
            "uuid_seed": "b" * 32,
            "expires_at": time.time() + 3600,
        },
    }
    if ok_value is not None:
        result["ok"] = ok_value

    assert hook_runtime._register_native_handoff_descriptor(payload, result) is False
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is None


@pytest.mark.asyncio
async def test_native_handoff_multichunk_stays_stable_until_ledger_ack(monkeypatch):
    content = "abcdeabcde"
    adapter = _NativeAckAdapter()
    descriptor = _install_native_ack_context(adapter, content)
    acked = []

    async def fake_ack(value):
        acked.append(value)
        return len(acked) > 1

    monkeypatch.setattr(hook_runtime, "_ack_native_handoff", fake_ack)
    first = await adapter.send("oc_native", content)
    first_uuids = [call[3] for call in adapter.raw_calls]
    adapter.raw_calls.clear()
    second = await adapter.send("oc_native", content)
    second_uuids = [call[3] for call in adapter.raw_calls]
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is not None
    hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.set(None)
    adapter.raw_calls.clear()
    third = await adapter.send("oc_native", content)
    third_uuids = [call[3] for call in adapter.raw_calls]

    assert first.success is True
    assert second.success is True
    assert len(first_uuids) == 2
    assert first_uuids[0] != first_uuids[1]
    assert first_uuids == second_uuids
    assert all(len(value) <= 50 for value in first_uuids)
    assert third.success is True
    assert third_uuids == ["random-create", "random-create"]
    assert descriptor["protocol"] == "hfc-native-handoff-v2"
    assert acked == []


@pytest.mark.asyncio
async def test_native_handoff_middle_chunk_failure_is_not_masked_and_does_not_ack(monkeypatch):
    content = "abcdefghijklmno"
    adapter = _NativeAckAdapter(outcomes=[True, False, True])
    _install_native_ack_context(adapter, content)
    acked = []
    monkeypatch.setattr(
        hook_runtime,
        "_ack_native_handoff",
        lambda value: acked.append(value),
    )

    result = await adapter.send("oc_native", content)

    assert result.success is False
    assert len(adapter.raw_calls) == 3
    assert acked == []


@pytest.mark.asyncio
async def test_delivery_ledger_is_durable_before_native_ack(monkeypatch):
    order = []
    gateway_module = types.ModuleType("gateway")
    gateway_module.__path__ = []
    ledger_module = types.ModuleType("gateway.delivery_ledger")

    def mark_delivered(obligation_id):
        order.append(("ledger", obligation_id))

    ledger_module.mark_delivered = mark_delivered
    ledger_module.mark_failed = lambda _obligation_id, _error="": None
    gateway_module.delivery_ledger = ledger_module
    monkeypatch.setitem(sys.modules, "gateway", gateway_module)
    monkeypatch.setitem(sys.modules, "gateway.delivery_ledger", ledger_module)
    raw_obligation = "obligation-private"
    obligation_key = hook_runtime._native_handoff_obligation_key(raw_obligation)
    adapter = _NativeAckAdapter()
    descriptor = _install_native_ack_context(
        adapter,
        "terminal",
        obligation_key=obligation_key,
    )

    async def fake_ack(value):
        order.append(("ack", value))
        return True

    monkeypatch.setattr(hook_runtime, "_ack_native_handoff", fake_ack)
    result = await adapter.send("oc_native", "terminal")

    # Crash here: platform succeeded, but the ledger has not transitioned.
    # The sidecar must still be pending so startup recovery can reuse UUIDs.
    assert result.success is True
    assert order == []
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is not None

    ledger_module.mark_delivered("unrelated-obligation")
    await asyncio.sleep(0)
    assert order == [("ledger", "unrelated-obligation")]
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is not None

    ledger_module.mark_delivered(raw_obligation)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert order == [
        ("ledger", "unrelated-obligation"),
        ("ledger", raw_obligation),
        ("ack", descriptor),
    ]
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is None


@pytest.mark.asyncio
async def test_delivery_ledger_mark_failure_forbids_native_ack(monkeypatch):
    gateway_module = types.ModuleType("gateway")
    gateway_module.__path__ = []
    ledger_module = types.ModuleType("gateway.delivery_ledger")

    def mark_delivered(_obligation_id):
        raise OSError("ledger fsync failed")

    ledger_module.mark_delivered = mark_delivered
    ledger_module.mark_failed = lambda _obligation_id, _error="": None
    gateway_module.delivery_ledger = ledger_module
    monkeypatch.setitem(sys.modules, "gateway", gateway_module)
    monkeypatch.setitem(sys.modules, "gateway.delivery_ledger", ledger_module)
    raw_obligation = "obligation-private"
    adapter = _NativeAckAdapter()
    _install_native_ack_context(
        adapter,
        "terminal",
        obligation_key=hook_runtime._native_handoff_obligation_key(raw_obligation),
    )
    acked = []

    async def fake_ack(value):
        acked.append(value)
        return True

    monkeypatch.setattr(hook_runtime, "_ack_native_handoff", fake_ack)
    assert (await adapter.send("oc_native", "terminal")).success is True

    with pytest.raises(OSError, match="fsync failed"):
        ledger_module.mark_delivered(raw_obligation)
    await asyncio.sleep(0)

    assert acked == []
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("mark_failed_raises", [False, True])
async def test_delivery_ledger_failed_row_clears_descriptor_without_ack(
    monkeypatch,
    mark_failed_raises,
):
    gateway_module = types.ModuleType("gateway")
    gateway_module.__path__ = []
    ledger_module = types.ModuleType("gateway.delivery_ledger")
    ledger_module.mark_delivered = lambda _obligation_id: None

    def mark_failed(_obligation_id, _error=""):
        if mark_failed_raises:
            raise OSError("ledger failure transition failed")

    ledger_module.mark_failed = mark_failed
    gateway_module.delivery_ledger = ledger_module
    monkeypatch.setitem(sys.modules, "gateway", gateway_module)
    monkeypatch.setitem(sys.modules, "gateway.delivery_ledger", ledger_module)
    raw_obligation = "obligation-private"
    adapter = _NativeAckAdapter(outcomes=[False])
    _install_native_ack_context(
        adapter,
        "same",
        obligation_key=hook_runtime._native_handoff_obligation_key(raw_obligation),
    )
    acked = []

    async def fake_ack(value):
        acked.append(value)
        return True

    monkeypatch.setattr(hook_runtime, "_ack_native_handoff", fake_ack)
    assert (await adapter.send("oc_native", "same")).success is False
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is not None

    if mark_failed_raises:
        with pytest.raises(OSError, match="failure transition failed"):
            ledger_module.mark_failed(raw_obligation, "send failed")
    else:
        ledger_module.mark_failed(raw_obligation, "send failed")
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is None

    adapter.outcomes = [True]
    adapter.raw_calls.clear()
    assert (await adapter.send("oc_native", "same")).success is True
    assert adapter.raw_calls[0][3] == "random-create"
    assert acked == []


@pytest.mark.asyncio
async def test_native_handoff_uses_canonical_create_routes_and_format_scoped_uuid(monkeypatch):
    content = "same"
    adapter = _NativeAckAdapter()
    _install_native_ack_context(adapter, content)

    async def retain_for_retry(_descriptor):
        return False

    monkeypatch.setattr(hook_runtime, "_ack_native_handoff", retain_for_retry)
    await adapter.send("oc_native", content)
    create_uuid = adapter.raw_calls[-1][3]
    await adapter.send("oc_native", content, reply_to="om_parent")
    canonical_create_uuid = adapter.raw_calls[-1][3]
    assert adapter.raw_calls[-1][2] == "create"

    thread_adapter = _NativeAckAdapter()
    _install_native_ack_context(thread_adapter, content, thread_id="omt_topic")
    await thread_adapter.send(
        "oc_native",
        content,
        reply_to="om_parent",
        metadata={"thread_id": "omt_topic"},
    )
    thread_uuid = thread_adapter.raw_calls[-1][3]

    assert create_uuid == canonical_create_uuid
    assert thread_adapter.raw_calls[-1][2] == "thread"
    assert create_uuid != thread_uuid

    fallback = _NativeAckPostFallbackAdapter(
        outcomes=[False, True, False, True]
    )
    _install_native_ack_context(fallback, content)
    await fallback.send("oc_native", content)
    first = [call[3] for call in fallback.raw_calls]
    fallback.raw_calls.clear()
    await fallback.send("oc_native", content)
    second = [call[3] for call in fallback.raw_calls]

    assert len(first) == 2
    assert first[0] != first[1]
    assert first == second


@pytest.mark.asyncio
async def test_native_handoff_context_clears_after_exception_and_unrelated_send(monkeypatch):
    adapter = _NativeAckRaisingAdapter()
    _install_native_ack_context(adapter, "terminal")
    with pytest.raises(RuntimeError, match="adapter escaped"):
        await adapter.send("oc_native", "terminal")
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is None

    ordinary = _NativeAckAdapter()
    _install_native_ack_context(ordinary, "terminal")
    result = await ordinary.send("oc_native", "unrelated")
    assert result.success is True
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is None
    assert [call[3] for call in ordinary.raw_calls] == ["random-create", "random-create"]


@pytest.mark.asyncio
async def test_expired_native_handoff_descriptor_fails_open_without_ack(monkeypatch):
    adapter = _NativeAckAdapter()
    descriptor = _install_native_ack_context(
        adapter,
        "terminal",
        expires_at=time.time() - 1,
    )
    acked = []
    monkeypatch.setattr(
        hook_runtime,
        "_ack_native_handoff",
        lambda value: acked.append(value),
    )

    result = await adapter.send("oc_native", "terminal")

    assert result.success is True
    assert [call[3] for call in adapter.raw_calls] == ["random-create", "random-create"]
    assert descriptor["expires_at"] < time.time()
    assert acked == []


def test_native_handoff_descriptor_requires_exact_base_content():
    adapter = _NativeAckAdapter()
    _install_native_ack_context(adapter, "answer")
    assert hook_runtime._native_handoff_for_send(
        adapter,
        "oc_native",
        "answer",
        None,
    ) is not None
    _install_native_ack_context(adapter, "answer")
    assert hook_runtime._native_handoff_for_send(
        adapter,
        "oc_native",
        "answer\nMEDIA:/tmp/private.png",
        None,
    ) is None


@pytest.mark.parametrize("result", [None, "", {"ok": True}, {"applied": True}])
def test_terminal_delivery_requires_explicit_applied_commit(result):
    assert hook_runtime._event_was_applied(result, strict=True) is False


def test_terminal_delivery_accepts_only_explicit_applied_commit():
    assert hook_runtime._event_was_applied(
        {"ok": True, "applied": True},
        strict=True,
    ) is True


async def _stage_exact_terminal(monkeypatch, *, answer="exact terminal"):
    async def no_pending(_local_vars):
        return None

    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_async",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=hook_runtime._PolicyGateResult(True, None),
        ),
    )
    monkeypatch.setattr(
        hook_runtime,
        "_flush_pending_deltas_for_local_vars",
        no_pending,
    )
    assert await hook_runtime.stage_message_completed_from_hermes_locals_async(
        {
            "platform": "feishu",
            "chat_id": "oc_native",
            "conversation_id": "conversation-native",
            "message_id": "message-native",
            "answer": answer,
            "created_at": 1777017600.0,
        }
    )


def _install_test_delivery_ledger(monkeypatch, *, debug_rows=None):
    gateway_module = types.ModuleType("gateway")
    gateway_module.__path__ = []
    ledger_module = types.ModuleType("gateway.delivery_ledger")
    ledger_module.mark_delivered = lambda _obligation_id: None
    ledger_module.mark_failed = lambda _obligation_id, _error="": None
    if debug_rows is not None:
        ledger_module.debug_rows = debug_rows
    gateway_module.delivery_ledger = ledger_module
    monkeypatch.setitem(sys.modules, "gateway", gateway_module)
    monkeypatch.setitem(sys.modules, "gateway.delivery_ledger", ledger_module)
    return ledger_module


@pytest.mark.asyncio
async def test_terminal_native_without_descriptor_recovers_full_exact_handoff(
    monkeypatch,
):
    await _stage_exact_terminal(monkeypatch)
    ledger = _install_test_delivery_ledger(monkeypatch)
    adapter = _NativeAckAdapter()
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter})
    )
    terminal_payloads = []

    async def terminal_post(_url, payload, _timeout):
        terminal_payloads.append(payload)
        return {"ok": True, "applied": False, "disposition": "native"}

    async def recovery_post(url, payload, _timeout):
        assert url.endswith("/native-handoff/recover")
        return {
            "ok": True,
            "found": True,
            "native_handoff": {
                "protocol": "hfc-native-handoff-v2",
                "id": "a" * 64,
                "uuid_seed": hook_runtime.derive_native_handoff_uuid_seed(
                    obligation_key=payload["obligation_key"],
                    content_hash=payload["content_hash"],
                    plan_fingerprint=payload["plan_fingerprint"],
                    route=payload["route"],
                    target_hash=payload["target_hash"],
                ),
                "expires_at": time.time() + 3600,
            },
        }

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", terminal_post)
    monkeypatch.setattr(hook_runtime, "_post_json_response", recovery_post)
    returned = await hook_runtime.prepare_exact_base_final_delivery(
        {
            "delivery_adapter": adapter,
            "content": "exact terminal",
            "obligation_id": "obligation-private",
            "metadata": {},
            "images": [],
            "local_files": [],
            "media_files": [],
        }
    )

    context = hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get()
    assert returned[0] is adapter
    assert context is not None and context.get("provisional") is not True
    assert context["descriptor"]["uuid_seed"] == terminal_payloads[0]["data"][
        "native_handoff"
    ]["provisional_uuid_seed"]
    assert ledger.mark_delivered is hook_runtime._hfc_mark_delivery_ledger_delivered_then_ack


@pytest.mark.asyncio
async def test_double_response_loss_uses_target_bound_seed_then_relooks_up_for_ack(
    monkeypatch,
):
    await _stage_exact_terminal(monkeypatch)
    ledger = _install_test_delivery_ledger(monkeypatch)
    adapter = _NativeAckAdapter()
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter})
    )
    terminal_payloads = []
    recovery_calls = []
    acked = []

    async def lost_terminal(_url, payload, _timeout):
        terminal_payloads.append(payload)
        raise TimeoutError("terminal response lost")

    async def recovery_then_ack(url, payload, _timeout):
        if url.endswith("/native-handoff/recover"):
            recovery_calls.append(payload)
            if len(recovery_calls) == 1:
                raise TimeoutError("recovery response lost")
            return {
                "ok": True,
                "found": True,
                "native_handoff": {
                    "protocol": "hfc-native-handoff-v2",
                    "id": "a" * 64,
                    "uuid_seed": hook_runtime.derive_native_handoff_uuid_seed(
                        obligation_key=payload["obligation_key"],
                        content_hash=payload["content_hash"],
                        plan_fingerprint=payload["plan_fingerprint"],
                        route=payload["route"],
                        target_hash=payload["target_hash"],
                    ),
                    "expires_at": time.time() + 3600,
                },
            }
        acked.append(payload)
        return {"ok": True, "acknowledged": True}

    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", lost_terminal)
    monkeypatch.setattr(hook_runtime, "_post_json_response", recovery_then_ack)
    returned = await hook_runtime.prepare_exact_base_final_delivery(
        {
            "delivery_adapter": adapter,
            "content": "exact terminal",
            "obligation_id": "obligation-private",
            "metadata": {},
            "images": [],
            "local_files": [],
            "media_files": [],
        }
    )
    provisional = hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get()
    result = await adapter.send("oc_native", "exact terminal")
    sent_uuids = [call[3] for call in adapter.raw_calls]
    ledger.mark_delivered("obligation-private")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    metadata = terminal_payloads[0]["data"]["native_handoff"]
    assert returned[0] is adapter
    assert provisional is not None and provisional["provisional"] is True
    assert provisional["descriptor"] == {
        "uuid_seed": metadata["provisional_uuid_seed"]
    }
    assert result.success is True
    assert sent_uuids and all(value != "random-create" for value in sent_uuids)
    assert len(recovery_calls) == 2
    assert acked and acked[0]["uuid_seed"] == metadata["provisional_uuid_seed"]
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("age", "expected_provisional"), [(10.0, True), (3601.0, False)])
async def test_startup_transport_unknown_provisional_is_bounded_by_ledger_age(
    monkeypatch,
    age,
    expected_provisional,
):
    raw_obligation = "obligation-private"
    now = time.time()
    ledger = _install_test_delivery_ledger(
        monkeypatch,
        debug_rows=lambda limit=20: json.dumps(
            [{"id": raw_obligation, "created_at": now - age}]
        ),
    )
    adapter = _NativeAckAdapter()
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter})
    )

    async def lost_recovery(_url, _payload, _timeout):
        raise TimeoutError("recovery response lost")

    async def native_only(_chat_id):
        return False

    monkeypatch.setattr(hook_runtime, "_post_json_response", lost_recovery)
    monkeypatch.setattr(hook_runtime, "_hfc_direct_card_allowed_async", native_only)
    marker = "RECOVERED MARKER: exact terminal"
    scope = await hook_runtime.prepare_native_handoff_recovery(
        adapter=adapter,
        obligation_id=raw_obligation,
        chat_id="oc_native",
        content=marker,
        original_content="exact terminal",
    )
    result = await adapter.send("oc_native", marker)

    assert (scope is not None) is expected_provisional
    assert result.success is True
    if expected_provisional:
        context = hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get()
        assert context is not None and context["provisional"] is True
        assert "send_content" not in context
        assert all(call[3] != "random-create" for call in adapter.raw_calls)
    else:
        assert all(call[3] == "random-create" for call in adapter.raw_calls)
    assert ledger.mark_delivered is hook_runtime._hfc_mark_delivery_ledger_delivered_then_ack


@pytest.mark.asyncio
async def test_delivery_ledger_recovery_uses_opaque_lookup_stable_uuid_and_ack(
    monkeypatch,
):
    posts = []

    async def fake_post(url, payload, timeout):
        posts.append((url, payload, timeout))
        if url.endswith("/native-handoff/recover"):
            uuid_seed = hook_runtime.derive_native_handoff_uuid_seed(
                obligation_key=payload["obligation_key"],
                content_hash=payload["content_hash"],
                plan_fingerprint=payload["plan_fingerprint"],
                route=payload["route"],
                target_hash=payload["target_hash"],
            )
            return {
                "ok": True,
                "found": True,
                "native_handoff": {
                    "protocol": "hfc-native-handoff-v2",
                    "id": "a" * 64,
                    "uuid_seed": uuid_seed,
                    "expires_at": time.time() + 3600,
                },
            }
        return {"ok": True, "acknowledged": True}

    monkeypatch.setattr(hook_runtime, "_post_json_response", fake_post)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    gateway_module = types.ModuleType("gateway")
    gateway_module.__path__ = []
    ledger_module = types.ModuleType("gateway.delivery_ledger")
    ledger_module.mark_delivered = lambda _obligation_id: None
    ledger_module.mark_failed = lambda _obligation_id, _error="": None
    gateway_module.delivery_ledger = ledger_module
    monkeypatch.setitem(sys.modules, "gateway", gateway_module)
    monkeypatch.setitem(sys.modules, "gateway.delivery_ledger", ledger_module)
    adapter = _NativeAckAdapter()
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter})
    )

    scope = await hook_runtime.prepare_native_handoff_recovery(
        adapter=adapter,
        obligation_id="obligation-private",
        chat_id="oc_private",
        content="recovered final",
        original_content="recovered final",
    )
    result = await adapter.send("oc_private", "recovered final")
    assert len(posts) == 1
    ledger_module.mark_delivered("obligation-private")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert scope is not None
    assert result.success is True
    assert adapter.raw_calls[0][3] != "random-create"
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is None
    recovery_payload = posts[0][1]
    assert recovery_payload["protocol"] == "hfc-native-handoff-recovery-v2"
    assert len(recovery_payload["obligation_key"]) == 64
    assert len(recovery_payload["content_hash"]) == 64
    assert len(recovery_payload["plan_fingerprint"]) == 64
    assert len(recovery_payload["target_hash"]) == 64
    assert recovery_payload["route"] == "create"
    assert "private" not in json.dumps(recovery_payload)
    assert posts[1][0].endswith("/native-handoff/ack")


@pytest.mark.asyncio
async def test_partial_chunk_recovery_reuses_exact_ledger_content_and_uuid_plan(
    monkeypatch,
):
    original_content = "abcdefghij"
    recovered_marker = "RECOVERED MARKER: "
    raw_obligation = "obligation-private"
    obligation_key = hook_runtime._native_handoff_obligation_key(raw_obligation)
    gateway_module = types.ModuleType("gateway")
    gateway_module.__path__ = []
    ledger_module = types.ModuleType("gateway.delivery_ledger")
    ledger_module.RECOVERED_MARKER = recovered_marker
    ledger_module.mark_delivered = lambda _obligation_id: None
    ledger_module.mark_failed = lambda _obligation_id, _error="": None
    gateway_module.delivery_ledger = ledger_module
    monkeypatch.setitem(sys.modules, "gateway", gateway_module)
    monkeypatch.setitem(sys.modules, "gateway.delivery_ledger", ledger_module)

    initial = _NativeAckAdapter(outcomes=[True, False])
    descriptor = _install_native_ack_context(
        initial,
        original_content,
        obligation_key=obligation_key,
    )
    initial_result = await initial.send("oc_native", original_content)
    initial_calls = list(initial.raw_calls)
    assert initial_result.success is False
    assert len(initial_calls) == 2
    # Simulate the process dying after the first chunk may already have landed.
    hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.set(None)

    posts = []

    async def fake_post(url, payload, timeout):
        posts.append((url, payload, timeout))
        if url.endswith("/native-handoff/recover"):
            return {
                "ok": True,
                "found": True,
                "native_handoff": descriptor,
            }
        return {"ok": True, "acknowledged": True}

    monkeypatch.setattr(hook_runtime, "_post_json_response", fake_post)
    recovered = _NativeAckAdapter()
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": recovered})
    )
    scope = await hook_runtime.prepare_native_handoff_recovery(
        adapter=recovered,
        obligation_id=raw_obligation,
        chat_id="oc_native",
        content=recovered_marker + original_content,
        original_content=original_content,
    )
    recovered_result = await recovered.send(
        "oc_native", recovered_marker + original_content
    )

    assert scope is not None
    assert recovered_result.success is True
    assert recovered.raw_calls == initial_calls
    assert recovered_marker not in "".join(call[0] for call in recovered.raw_calls)
    assert posts[0][0].endswith("/native-handoff/recover")


@pytest.mark.asyncio
async def test_expired_recovery_descriptor_preserves_upstream_marker_and_random_uuid(
    monkeypatch,
):
    recovered_marker = "RECOVERED MARKER: "
    original_content = "abcdefghij"

    async def fake_post(_url, _payload, _timeout):
        return {
            "ok": True,
            "found": True,
            "native_handoff": {
                "protocol": "hfc-native-handoff-v2",
                "id": "a" * 64,
                "uuid_seed": "b" * 32,
                "expires_at": time.time() - 1,
            },
        }

    async def native_only(_chat_id):
        return False

    monkeypatch.setattr(hook_runtime, "_post_json_response", fake_post)
    monkeypatch.setattr(hook_runtime, "_hfc_direct_card_allowed_async", native_only)
    adapter = _NativeAckAdapter()
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter})
    )
    assert await hook_runtime.prepare_native_handoff_recovery(
        adapter=adapter,
        obligation_id="obligation-private",
        chat_id="oc_native",
        content=recovered_marker + original_content,
        original_content=original_content,
    ) is None

    result = await adapter.send("oc_native", recovered_marker + original_content)

    assert result.success is True
    assert all(call[3] == "random-create" for call in adapter.raw_calls)
    delivered_text = "".join(json.loads(call[0])["text"] for call in adapter.raw_calls)
    assert delivered_text == recovered_marker + original_content


@pytest.mark.asyncio
async def test_delivery_ledger_recovery_rejects_expired_descriptor(monkeypatch):
    async def fake_post(_url, _payload, _timeout):
        return {
            "ok": True,
            "found": True,
            "native_handoff": {
                "protocol": "hfc-native-handoff-v2",
                "id": "a" * 64,
                "uuid_seed": "b" * 32,
                "expires_at": time.time() - 1,
            },
        }

    monkeypatch.setattr(hook_runtime, "_post_json_response", fake_post)
    adapter = _NativeAckAdapter()
    assert hook_runtime.install_feishu_command_card_adapter_methods(
        SimpleNamespace(adapters={"feishu": adapter})
    )
    assert await hook_runtime.prepare_native_handoff_recovery(
        adapter=adapter,
        obligation_id="obligation-private",
        chat_id="oc_private",
        content="recovered final",
        original_content="recovered final",
    ) is None
    assert hook_runtime._HFC_NATIVE_HANDOFF_CONTEXT.get() is None


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
    assert payload["data"] == {
        "profile_id": "default",
        "profile_source": "fallback_default",
        "text": "",
    }


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


def test_quoted_turn_uses_one_sequence_across_started_stream_and_terminal():
    source = SimpleNamespace(platform="feishu", chat_id="oc_quoted")
    handler_event = SimpleNamespace(message_id="om_turn_a")
    started = hook_runtime.build_event(
        "message.started",
        {
            "source": source,
            "event": handler_event,
            "message_id": "om_turn_a",
            "conversation_id": "omt_topic",
        },
    )
    delta = hook_runtime.build_event(
        "answer.delta",
        {
            "source": source,
            "message_id": "om_shared_quote",
            "conversation_id": "omt_topic",
            "text": "first",
        },
    )
    completed = hook_runtime.build_event(
        "message.completed",
        {
            "source": source,
            "event": handler_event,
            "message_id": "om_shared_quote",
            "conversation_id": "omt_topic",
            "answer": "done",
        },
    )

    assert [started["turn_id"], delta["turn_id"], completed["turn_id"]] == [
        "om_turn_a",
        "om_turn_a",
        "om_turn_a",
    ]
    assert [started["sequence"], delta["sequence"], completed["sequence"]] == [
        0,
        1,
        2,
    ]


def test_source_that_rejects_private_binding_uses_legacy_identity():
    class SlottedSource:
        __slots__ = ("platform", "chat_id")

        def __init__(self):
            self.platform = "feishu"
            self.chat_id = "oc_slotted"

    source = SlottedSource()
    started = hook_runtime.build_event(
        "message.started",
        {
            "source": source,
            "event": SimpleNamespace(message_id="om_real"),
            "message_id": "om_real",
        },
    )
    delta = hook_runtime.build_event(
        "answer.delta",
        {"source": source, "message_id": "om_quote", "text": "x"},
    )

    assert started["turn_id"] == "om_real"
    assert "turn_id" not in delta
    assert delta["message_id"] == "om_quote"
    assert delta["sequence"] == 0


def test_turn_id_raw_terminal_locals_use_bound_turn_before_reply_anchor():
    source = SimpleNamespace(_hfc_turn_id="om_turn_a")
    raw_locals = {"source": source, "message_id": "om_shared_quote"}

    assert hook_runtime._canonical_id_from_local_vars(raw_locals) == "om_turn_a"


def test_build_event_allocates_unique_sequences_across_threads(monkeypatch):
    class SlowSequenceStore(dict):
        def get(self, key, default=None):
            value = super().get(key, default)
            time.sleep(0.02)
            return value

    monkeypatch.setattr(hook_runtime, "_SEQUENCES", SlowSequenceStore())
    local_vars = {"chat_id": "oc_abc", "message_id": "msg_seq", "text": "hi"}

    with ThreadPoolExecutor(max_workers=2) as executor:
        payloads = list(
            executor.map(
                lambda _: hook_runtime.build_event("answer.delta", local_vars),
                range(2),
            )
        )

    assert sorted(payload["sequence"] for payload in payloads) == [0, 1]


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
async def test_quoted_turn_send_lock_and_delta_queue_use_canonical_turn_id(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_DELTA_COALESCE_MS", "1000")
    source = SimpleNamespace(
        platform="feishu",
        chat_id="oc_quote",
        _hfc_turn_id="om_turn",
    )
    loop = asyncio.get_running_loop()

    assert hook_runtime.emit_from_hermes_locals_threadsafe(
        {
            "_hfc_loop": loop,
            "source": source,
            "message_id": "om_quote",
            "text": "x",
        },
        "answer.delta",
    )
    await drain_tasks()
    await hook_runtime.flush_pending_deltas_for_message("om_turn")

    assert len(sender.payloads) == 1
    payload = sender.payloads[0][1]
    assert payload["turn_id"] == "om_turn"
    assert payload["sequence"] == 0
    assert hook_runtime._send_lock("http://sidecar.test/events", payload) is (
        hook_runtime._send_lock(
            "http://sidecar.test/events",
            {**payload, "message_id": "different-anchor"},
        )
    )


@pytest.mark.asyncio
async def test_turn_id_raw_locals_drive_pending_presence_flush_and_discard():
    loop = object()
    key = (
        id(loop),
        "http://sidecar.test/events",
        "om_turn_a",
        "answer.delta",
        "default",
    )
    pending = hook_runtime._PendingDelta(
        event_name="answer.delta",
        event_url="http://sidecar.test/events",
        timeout_seconds=1.0,
        loop=loop,
        base_locals={},
        text_parts=[],
    )
    raw_locals = {
        "source": SimpleNamespace(_hfc_turn_id="om_turn_a"),
        "message_id": "om_shared_quote",
    }

    try:
        with hook_runtime._PENDING_DELTAS_LOCK:
            hook_runtime._PENDING_DELTAS[key] = pending
        assert hook_runtime._has_pending_deltas_for_local_vars(raw_locals)
        await hook_runtime._flush_pending_deltas_for_local_vars(raw_locals)
        with hook_runtime._PENDING_DELTAS_LOCK:
            assert key not in hook_runtime._PENDING_DELTAS
            hook_runtime._PENDING_DELTAS[key] = pending
        hook_runtime._discard_pending_deltas_for_local_vars(raw_locals)
        with hook_runtime._PENDING_DELTAS_LOCK:
            assert key not in hook_runtime._PENDING_DELTAS
    finally:
        with hook_runtime._PENDING_DELTAS_LOCK:
            hook_runtime._PENDING_DELTAS.pop(key, None)


@pytest.mark.asyncio
async def test_threadsafe_answer_delta_coalesces_many_tokens(monkeypatch):
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    monkeypatch.setenv("HERMES_FEISHU_CARD_DELTA_COALESCE_MS", "1000")
    monkeypatch.setenv("HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS", "2000")

    loop = asyncio.get_running_loop()
    local_vars = {
        "_hfc_loop": loop,
        "source": SourceObject(),
        "message_id": "msg_burst",
    }

    for _ in range(1000):
        assert hook_runtime.emit_from_hermes_locals_threadsafe(
            {**local_vars, "text": "x"},
            event_name="answer.delta",
        )

    await drain_tasks()
    assert sender.payloads == []

    await hook_runtime.flush_pending_deltas_for_message("msg_burst")

    assert len(sender.payloads) == 1
    _url, payload, _timeout = sender.payloads[0]
    assert payload["event"] == "answer.delta"
    assert payload["message_id"] == "msg_burst"
    assert payload["data"]["text"] == "x" * 1000


@pytest.mark.asyncio
async def test_async_terminal_flushes_pending_delta_before_completed(monkeypatch):
    posted = []
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    monkeypatch.setenv("HERMES_FEISHU_CARD_DELTA_COALESCE_MS", "1000")
    monkeypatch.setenv("HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS", "2000")

    async def fake_post(url, payload, timeout):
        posted.append(payload)

    async def fake_post_response(url, payload, timeout):
        posted.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_json", fake_post)
    monkeypatch.setattr(hook_runtime, "_post_json_response", fake_post_response)

    loop = asyncio.get_running_loop()
    local_vars = {
        "_hfc_loop": loop,
        "source": SourceObject(),
        "message_id": "msg_terminal",
    }

    assert hook_runtime.emit_from_hermes_locals_threadsafe(
        {**local_vars, "text": "thinking"},
        event_name="thinking.delta",
    )
    await drain_tasks()
    assert posted == []

    delivered = await hook_runtime.emit_from_hermes_locals_async(
        {**local_vars, "answer": "done"},
        event_name="message.completed",
    )

    assert delivered is True
    assert [payload["event"] for payload in posted] == [
        "thinking.delta",
        "message.completed",
    ]
    assert posted[0]["data"]["text"] == "thinking"


@pytest.mark.asyncio
async def test_threadsafe_non_delta_flushes_pending_delta_before_tool(monkeypatch):
    posted = []
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    monkeypatch.setenv("HERMES_FEISHU_CARD_DELTA_COALESCE_MS", "1000")
    monkeypatch.setenv("HERMES_FEISHU_CARD_DELTA_COALESCE_CHARS", "2000")

    async def fake_post(url, payload, timeout):
        posted.append(payload)

    monkeypatch.setattr(hook_runtime, "_post_json", fake_post)

    loop = asyncio.get_running_loop()
    local_vars = {
        "_hfc_loop": loop,
        "source": SourceObject(),
        "message_id": "msg_tool_order",
    }

    assert hook_runtime.emit_from_hermes_locals_threadsafe(
        {**local_vars, "text": "thinking"},
        event_name="thinking.delta",
    )
    await drain_tasks()
    assert posted == []

    assert hook_runtime.emit_from_hermes_locals_threadsafe(
        {
            **local_vars,
            "tool_id": "tool_1",
            "name": "search",
            "status": "completed",
        },
        event_name="tool.updated",
    )
    await drain_tasks()

    assert [payload["event"] for payload in posted] == [
        "thinking.delta",
        "tool.updated",
    ]
    assert [payload["sequence"] for payload in posted] == [0, 1]
    assert posted[0]["data"]["text"] == "thinking"


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
    monkeypatch.setenv("HERMES_FEISHU_CARD_DELTA_COALESCE_MS", "0")

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
    assert payload["data"] == {
        "profile_id": "default",
        "profile_source": "fallback_default",
        "policy_new_turn": True,
        "text": "hello",
    }
    assert timeout == 0.8


@pytest.mark.asyncio
async def test_emit_from_hermes_locals_async_serializes_same_message_deltas(monkeypatch):
    completed: list[tuple[int, str]] = []

    async def slow_first_sender(url, payload, timeout):
        sequence = payload["sequence"]
        if sequence == 0:
            await asyncio.sleep(0.05)
        completed.append((sequence, payload["data"]["text"]))

    monkeypatch.setattr(hook_runtime, "_post_json_response", slow_first_sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    first = asyncio.create_task(
        hook_runtime.emit_from_hermes_locals_async(
            {
                "chat_id": "oc_abc",
                "message_id": "msg_stream_order",
                "text": "查当前安装的版本：`hermes-feishu-streaming-card` ",
            },
            event_name="answer.delta",
        )
    )
    await asyncio.sleep(0)
    second = asyncio.create_task(
        hook_runtime.emit_from_hermes_locals_async(
            {
                "chat_id": "oc_abc",
                "message_id": "msg_stream_order",
                "text": "V3.5.0。",
            },
            event_name="answer.delta",
        )
    )

    assert await asyncio.gather(first, second) == [True, True]
    assert completed == [
        (0, "查当前安装的版本：`hermes-feishu-streaming-card` "),
        (1, "V3.5.0。"),
    ]


@pytest.mark.asyncio
async def test_interaction_event_uses_same_message_send_lock(monkeypatch):
    completed: list[int] = []

    async def slow_delta_sender(url, payload, timeout):
        await asyncio.sleep(0.05)
        completed.append(payload["sequence"])

    async def interaction_sender(url, payload, timeout):
        completed.append(payload["sequence"])
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_json", slow_delta_sender)
    monkeypatch.setattr(hook_runtime, "_post_json_response", interaction_sender)
    loop = asyncio.get_running_loop()
    first = asyncio.create_task(
        hook_runtime._post_json_ordered(
            "http://sidecar.test/events",
            {"message_id": "msg_stream_order", "sequence": 0},
            0.8,
        )
    )
    await asyncio.sleep(0)

    result = await asyncio.to_thread(
        hook_runtime._post_interaction_event,
        {"_hfc_loop": loop},
        "http://sidecar.test/events",
        {"message_id": "msg_stream_order", "sequence": 1},
        0.8,
    )
    await first

    assert result == {"ok": True, "applied": True}
    assert completed == [0, 1]


@pytest.mark.asyncio
async def test_emit_cron_delivery_posts_from_running_loop_without_unawaited_warning(
    monkeypatch,
    recwarn,
):
    payloads = []

    def sender(url, payload, timeout):
        payloads.append((url, payload, timeout))
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    result = hook_runtime.emit_cron_delivery(
        {
            "job": {
                "id": "job-1",
                "origin": {"platform": "feishu", "chat_id": "oc_cron"},
            },
            "content": "定时结果",
        }
    )

    assert result is True
    assert len(payloads) == 1
    url, payload, timeout = payloads[0]
    assert url == "http://sidecar.test/events"
    assert payload["event"] == "message.completed"
    assert payload["chat_id"] == "oc_cron"
    assert payload["data"]["delivery_kind"] == "cron"
    assert timeout == hook_runtime.TERMINAL_TIMEOUT_SECONDS
    assert [
        warning
        for warning in recwarn
        if "was never awaited" in str(warning.message)
    ] == []


@pytest.mark.asyncio
async def test_emit_cron_delivery_reports_sender_failure_from_running_loop(monkeypatch):
    payloads = []

    def sender(url, payload, timeout):
        payloads.append((url, payload, timeout))
        raise RuntimeError("network failed")

    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", sender)

    result = hook_runtime.emit_cron_delivery(
        {
            "job": {
                "id": "job-1",
                "origin": {"platform": "feishu", "chat_id": "oc_cron"},
            },
            "content": "定时结果",
        }
    )

    assert result is False
    assert len(payloads) == 1
def test_emit_cron_delivery_falls_through_when_sidecar_requests_native(monkeypatch):
    posted = []

    def native_response(url, payload, timeout):
        posted.append((url, payload, timeout))
        return {"ok": True, "applied": False, "disposition": "native"}

    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")
    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", native_response)

    result = hook_runtime.emit_cron_delivery(
        {
            "job": {
                "id": "job-native",
                "origin": {"platform": "feishu", "chat_id": "oc_cron"},
            },
            "content": "定时结果",
        }
    )

    assert result is False
    assert len(posted) == 1


@pytest.mark.parametrize("sidecar_result", [None, "", {"ok": True}])
def test_cron_completion_does_not_suppress_without_explicit_commit(
    monkeypatch,
    sidecar_result,
):
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_sync_response",
        lambda _url, _payload, _timeout: sidecar_result,
    )

    assert hook_runtime.emit_cron_delivery(
        {
            "job": {
                "id": "job-fail-open",
                "origin": {"platform": "feishu", "chat_id": "oc_cron"},
            },
            "content": "定时结果",
        }
    ) is False


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
        "profile_id": "default",
        "profile_source": "fallback_default",
        "policy_new_turn": True,
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
    monkeypatch.setattr(hook_runtime, "_post_json_response", sender)
    monkeypatch.setenv("HERMES_FEISHU_CARD_EVENT_URL", "http://sidecar.test/events")

    result = await hook_runtime.emit_from_hermes_locals_async(
        {"chat_id": "oc_abc", "message_id": "msg_1"},
        event_name="message.completed",
    )

    assert result is False
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
    monkeypatch.setattr(hook_runtime, "_post_json_response", sender)

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
    monkeypatch.setattr(hook_runtime, "read_transport_root_secret", lambda: None)

    await hook_runtime._post_json(
        "http://sidecar.test/events",
        {"event": "message.started", "data": {"text": "对象文本"}},
        0.25,
    )

    assert opened["url"] == "http://sidecar.test/events"
    assert opened["method"] == "POST"
    assert opened["headers"]["Content-type"] == "application/json"
    assert "X-hfc-event-signature" not in opened["headers"]
    assert json.loads(opened["body"].decode("utf-8")) == {
        "event": "message.started",
        "data": {"text": "对象文本"},
    }
    assert opened["timeout"] == 0.25


@pytest.mark.asyncio
async def test_post_json_signs_event_body_with_private_transport_root(monkeypatch):
    opened = {}
    secret = b"r" * 32

    def fake_open_request(req, timeout):
        opened["headers"] = dict(req.header_items())
        opened["body"] = req.data

    monkeypatch.setattr(hook_runtime, "_open_request", fake_open_request)
    monkeypatch.setattr(
        hook_runtime,
        "read_transport_root_secret",
        lambda: secret,
    )

    await hook_runtime._post_json(
        "http://sidecar.test/events",
        {"event": "message.started"},
        0.25,
    )

    normalized_headers = {key.lower(): value for key, value in opened["headers"].items()}
    EventProofVerifier(secret).verify(normalized_headers, opened["body"])


@pytest.mark.asyncio
async def test_post_json_does_not_add_event_proof_to_other_sidecar_paths(monkeypatch):
    opened = {}

    def fake_open_request(req, timeout):
        opened["headers"] = dict(req.header_items())

    monkeypatch.setattr(hook_runtime, "_open_request", fake_open_request)
    monkeypatch.setattr(
        hook_runtime,
        "read_transport_root_secret",
        lambda: b"r" * 32,
    )

    await hook_runtime._post_json(
        "http://sidecar.test/commands",
        {"command": "status"},
        0.25,
    )

    assert not any("hfc-event" in key.lower() for key in opened["headers"])


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


@pytest.mark.asyncio
async def test_lookup_card_summary_gets_sidecar_summary(monkeypatch):
    opened = {}

    def fake_open_json(req, timeout):
        opened["url"] = req.full_url
        opened["method"] = req.get_method()
        opened["timeout"] = timeout
        return {
            "ok": True,
            "summary": "最终答案",
            "profile_id": "work",
            "chat_id": "oc_abc",
            "message_id": "feishu-message-1",
        }

    monkeypatch.setattr(hook_runtime, "_open_json_request", fake_open_json)

    result = await hook_runtime.lookup_card_summary(
        "feishu-message-1",
        event_url="http://sidecar.test/events",
        timeout=0.25,
    )

    assert opened == {
        "url": "http://sidecar.test/messages/feishu-message-1/summary",
        "method": "GET",
        "timeout": 0.25,
    }
    assert result == "最终答案"


@pytest.mark.parametrize(
    "response",
    [
        {"ok": False, "summary": "最终答案"},
        {"ok": True},
        {"ok": True, "summary": ""},
        {"ok": True, "summary": "   "},
        {"ok": True, "summary": 123},
        ["not", "a", "dict"],
    ],
)
@pytest.mark.asyncio
async def test_lookup_card_summary_returns_none_for_invalid_payloads(monkeypatch, response):
    def fake_open_json(_req, _timeout):
        return response

    monkeypatch.setattr(hook_runtime, "_open_json_request", fake_open_json)

    result = await hook_runtime.lookup_card_summary(
        "feishu-message-1",
        event_url="http://sidecar.test/events",
    )

    assert result is None


@pytest.mark.parametrize(
    "exc",
    [
        error.URLError("sidecar unavailable"),
        error.HTTPError("http://sidecar.test/summary", 404, "not found", {}, None),
        json.JSONDecodeError("bad json", "}", 0),
    ],
    ids=["url-error", "http-404", "bad-json"],
)
@pytest.mark.asyncio
async def test_lookup_card_summary_fails_open_on_sidecar_errors(monkeypatch, exc):
    def fake_open_json(_req, _timeout):
        raise exc

    monkeypatch.setattr(hook_runtime, "_open_json_request", fake_open_json)

    result = await hook_runtime.lookup_card_summary(
        "feishu-message-1",
        event_url="http://sidecar.test/events",
    )

    assert result is None


def test_build_event_includes_routing_context_from_local_vars():
    payload = hook_runtime.build_event(
        "message.started",
        {
            "chat_id": "oc_group",
            "conversation_id": "conv_group",
            "chat_type": "group",
            "tenant_key": "tenant_a",
            "agent_id": "reserved-agent",
            "profile_id": "reserved-profile",
        },
    )

    assert payload["data"]["chat_type"] == "group"
    assert payload["data"]["tenant_key"] == "tenant_a"
    assert payload["data"]["agent_id"] == "reserved-agent"
    assert payload["data"]["profile_id"] == "reserved-profile"


def test_build_event_profile_id_prefers_env(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_PROFILE_ID", "work")

    payload = hook_runtime.build_event(
        "message.started",
        {"chat_id": "oc_1", "message_id": "m_1", "profile_id": "default"},
    )

    assert payload["data"]["profile_id"] == "work"
    assert payload["data"]["profile_source"] == "env"


def test_build_event_profile_id_uses_hermes_home(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/home/user/.hermes/profiles/sales")

    payload = hook_runtime.build_event(
        "message.started",
        {"chat_id": "oc_1", "message_id": "m_1"},
    )

    assert payload["data"]["profile_id"] == "sales"
    assert payload["data"]["profile_source"] == "hermes_home"


@pytest.mark.parametrize("event_name", ["answer.delta", "thinking.delta"])
def test_build_delta_events_include_profile_identity(monkeypatch, event_name):
    monkeypatch.setenv("HERMES_FEISHU_CARD_PROFILE_ID", "work")

    payload = hook_runtime.build_event(
        event_name,
        {"chat_id": "oc_1", "message_id": "m_1", "text": "hello"},
    )

    assert payload["data"]["profile_id"] == "work"
    assert payload["data"]["profile_source"] == "env"


def test_build_event_profile_id_sanitizes_env(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_CARD_PROFILE_ID", "bad:profile/path")

    payload = hook_runtime.build_event(
        "message.started",
        {"chat_id": "oc_1", "message_id": "m_1"},
    )

    assert payload["data"]["profile_id"] == "default"
    assert payload["data"]["profile_source"] == "sanitized_env"


def test_build_event_profile_id_sanitizes_locals(monkeypatch):
    payload = hook_runtime.build_event(
        "message.started",
        {"chat_id": "oc_1", "message_id": "m_1", "profile_id": "bad:profile"},
    )

    assert payload["data"]["profile_id"] == "default"
    assert payload["data"]["profile_source"] == "sanitized_locals"


def test_build_event_profile_id_sanitizes_hermes_home(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/home/user/.hermes/profiles/bad:profile")

    payload = hook_runtime.build_event(
        "message.started", {"chat_id": "oc_1", "message_id": "m_1"}
    )

    assert payload["data"]["profile_id"] == "default"
    assert payload["data"]["profile_source"] == "sanitized_hermes_home"


def test_build_event_profile_id_ignores_unrelated_profiles_path(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/tmp/profiles/not-hermes")

    payload = hook_runtime.build_event(
        "message.started",
        {"chat_id": "oc_1", "message_id": "m_1"},
    )

    assert payload["data"]["profile_id"] == "default"
    assert payload["data"]["profile_source"] == "fallback_default"


def test_build_event_profile_id_ignores_hermes_home_with_extra_segments(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/home/user/.hermes/profiles/sales/extra")

    payload = hook_runtime.build_event(
        "message.started",
        {"chat_id": "oc_1", "message_id": "m_1"},
    )

    assert payload["data"]["profile_id"] == "default"
    assert payload["data"]["profile_source"] == "fallback_default"


def test_interaction_select_forwards_to_sidecar_and_returns_card(monkeypatch):
    """A WS-native interaction.select click is forwarded to the sidecar
    /card/actions endpoint and the returned card is surfaced in place."""

    class FakeCallBackCard:
        def __init__(self):
            self.type = None
            self.data = None

    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._loop = object()

        def _on_card_action_trigger(self, data):
            return "original"

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(hook_runtime, "CallBackCard", FakeCallBackCard, raising=False)

    posted = {}

    def fake_post(url, payload, timeout):
        posted["url"] = url
        posted["payload"] = payload
        posted["timeout"] = timeout
        return {"ok": True, "card": {"header": {"template": "green"}, "elements": []}}

    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", fake_post)
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )

    adapter = DummyFeishuAdapter()
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "interaction.select",
                    "interaction_id": "int-1",
                    "choice": "opt_b",
                    "choice_label": "Option B",
                    "token": "tok-1",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user", user_name="Bailey"),
        )
    )

    response = hook_runtime._hfc_on_feishu_card_action_trigger(adapter, data)

    assert posted["url"] == "http://127.0.0.1:8765/card/actions"
    assert posted["timeout"] == 5.0
    sent = posted["payload"]["event"]
    assert sent["action"]["value"] == {
        "hfc_action": "interaction.select",
        "interaction_id": "int-1",
        "choice": "opt_b",
        "choice_label": "Option B",
        "token": "tok-1",
    }
    assert sent["context"]["open_chat_id"] == "oc_abc"
    assert sent["operator"] == {"name": "Bailey", "open_id": "ou_user"}
    assert response.card.type == "raw"
    assert response.card.data["header"]["template"] == "green"


def test_interaction_select_ignores_incomplete_action(monkeypatch):
    """Missing interaction_id/token/choice must not POST to the sidecar."""

    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def _on_card_action_trigger(self, data):
            return "original"

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )

    called = {"posted": False}

    def fake_post(url, payload, timeout):
        called["posted"] = True
        return {"ok": True, "card": {}}

    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", fake_post)

    adapter = DummyFeishuAdapter()
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "interaction.select",
                    "interaction_id": "int-1",
                    # missing token + choice
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user"),
        )
    )

    response = hook_runtime._hfc_on_feishu_card_action_trigger(adapter, data)

    assert called["posted"] is False
    assert response.card is None


def test_native_policy_stops_before_event_sequence_and_delta_queue(monkeypatch):
    monkeypatch.setattr(
        hook_runtime,
        "_fetch_delivery_policy_sync",
        lambda *_args, **_kwargs: {
            "ok": True,
            "disposition": "native",
            "ttl_ms": 1000,
        },
    )
    monkeypatch.setenv("HERMES_FEISHU_CARD_DELTA_COALESCE_MS", "1000")

    handled = hook_runtime.emit_from_hermes_locals_threadsafe(
        {
            "chat_id": "oc_native",
            "message_id": "om_native",
            "text": "must stay native",
        },
        event_name="answer.delta",
    )

    assert handled is False
    assert hook_runtime._SEQUENCES == {}
    assert hook_runtime._PENDING_DELTAS == {}


async def test_policy_failure_is_native_and_card_decision_is_pinned_for_turn(monkeypatch):
    calls = []

    def changing_policy(_url, _payload, _timeout):
        calls.append(True)
        if len(calls) == 1:
            return {"ok": True, "disposition": "card", "ttl_ms": 0}
        raise TimeoutError("policy unavailable")

    posted = []

    async def post(_url, payload, _timeout):
        posted.append(payload["event"])
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_fetch_delivery_policy_sync", changing_policy)
    monkeypatch.setattr(hook_runtime, "_post_json_response", post)
    local_vars = {"chat_id": "oc_pin", "message_id": "om_pin"}

    assert await hook_runtime.emit_from_hermes_locals_async(
        local_vars, "message.started"
    )
    assert await hook_runtime.emit_from_hermes_locals_async(
        {**local_vars, "text": "x"}, "answer.delta"
    )
    assert calls == [True]
    assert posted == ["message.started", "answer.delta"]

    # A different new turn must query again; timeout fails open to Hermes native.
    assert not await hook_runtime.emit_from_hermes_locals_async(
        {"chat_id": "oc_pin", "message_id": "om_next"},
        "message.started",
    )
    assert len(calls) == 2


async def test_quoted_turn_policy_decision_is_pinned_to_canonical_turn_id(monkeypatch):
    calls = []

    def policy(_url, _payload, _timeout):
        calls.append(True)
        return {"ok": True, "disposition": "card", "ttl_ms": 0}

    posted = []

    async def post(_url, payload, _timeout):
        posted.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_fetch_delivery_policy_sync", policy)
    monkeypatch.setattr(hook_runtime, "_post_json_response", post)
    source = SimpleNamespace(platform="feishu", chat_id="oc_quote")
    handler_event = SimpleNamespace(message_id="om_turn")

    assert await hook_runtime.emit_from_hermes_locals_async(
        {"source": source, "event": handler_event, "message_id": "om_turn"},
        "message.started",
    )
    assert await hook_runtime.emit_from_hermes_locals_async(
        {"source": source, "message_id": "om_quote", "text": "x"},
        "answer.delta",
    )

    assert calls == [True]
    assert [item["turn_id"] for item in posted] == ["om_turn", "om_turn"]
    assert [item["sequence"] for item in posted] == [0, 1]


async def test_completed_topic_message_id_is_requeried_for_the_next_started_turn(
    monkeypatch,
):
    calls = []

    def policy(_url, _payload, _timeout):
        calls.append(True)
        return {
            "ok": True,
            "disposition": "card" if len(calls) == 1 else "native",
            "ttl_ms": 1000,
        }

    async def post(_url, _payload, _timeout):
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_fetch_delivery_policy_sync", policy)
    monkeypatch.setattr(hook_runtime, "_post_json_response", post)
    local_vars = {"chat_id": "oc_topic", "message_id": "om_reused"}

    assert await hook_runtime.emit_from_hermes_locals_async(
        local_vars, "message.started"
    )
    assert await hook_runtime.emit_from_hermes_locals_async(
        {**local_vars, "answer": "first"}, "message.completed"
    )
    assert not await hook_runtime.emit_from_hermes_locals_async(
        local_vars, "message.started"
    )
    assert len(calls) == 2


async def test_first_event_without_message_id_requeries_policy_for_new_turn(
    monkeypatch,
):
    calls = []

    def policy(_url, _payload, _timeout):
        calls.append(True)
        return {
            "ok": True,
            "disposition": "card" if len(calls) == 1 else "native",
            "ttl_ms": 1000,
        }

    async def post(_url, _payload, _timeout):
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_fetch_delivery_policy_sync", policy)
    monkeypatch.setattr(hook_runtime, "_post_json_response", post)
    first_turn = {"chat_id": "oc_missing_started", "message_id": "om_first"}

    assert await hook_runtime.emit_from_hermes_locals_async(
        first_turn, "message.started"
    )
    assert await hook_runtime.emit_from_hermes_locals_async(
        {**first_turn, "answer": "first"}, "message.completed"
    )
    assert not await hook_runtime.emit_from_hermes_locals_async(
        {"chat_id": "oc_missing_started", "text": "next"},
        "answer.delta",
    )
    assert len(calls) == 2


async def test_reused_message_id_nonterminal_event_starts_new_policy_turn(
    monkeypatch,
):
    calls = []

    def policy(_url, _payload, _timeout):
        calls.append(True)
        return {
            "ok": True,
            "disposition": "card" if len(calls) == 1 else "native",
            "ttl_ms": 1000,
        }

    async def post(_url, _payload, _timeout):
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_fetch_delivery_policy_sync", policy)
    monkeypatch.setattr(hook_runtime, "_post_json_response", post)
    reused = {"chat_id": "oc_reused_without_started", "message_id": "om_reused"}

    assert await hook_runtime.emit_from_hermes_locals_async(
        reused, "message.started"
    )
    assert await hook_runtime.emit_from_hermes_locals_async(
        {**reused, "answer": "first"}, "message.completed"
    )
    assert not await hook_runtime.emit_from_hermes_locals_async(
        {**reused, "text": "next"}, "answer.delta"
    )
    assert not await hook_runtime.emit_from_hermes_locals_async(
        {**reused, "text": "next again"}, "answer.delta"
    )
    assert len(calls) == 2


async def test_reused_message_id_card_event_marks_new_turn_for_server(monkeypatch):
    posted = []

    async def post(_url, payload, _timeout):
        posted.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_json_response", post)
    reused = {"chat_id": "oc_reused_card", "message_id": "om_reused_card"}

    assert await hook_runtime.emit_from_hermes_locals_async(
        reused, "message.started"
    )
    assert await hook_runtime.emit_from_hermes_locals_async(
        {**reused, "answer": "first"}, "message.completed"
    )
    assert await hook_runtime.emit_from_hermes_locals_async(
        {**reused, "text": "next"}, "answer.delta"
    )

    assert posted[-1]["event"] == "answer.delta"
    assert posted[-1]["data"]["policy_new_turn"] is True


async def test_invalid_explicit_profile_fails_native_before_policy_query(monkeypatch):
    calls = []

    def unexpected_policy(*_args, **_kwargs):
        calls.append(True)
        return {"ok": True, "disposition": "card", "ttl_ms": 1000}

    monkeypatch.setattr(
        hook_runtime,
        "_fetch_delivery_policy_sync",
        unexpected_policy,
    )

    async def post(*_args, **_kwargs):
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_json_response", post)

    handled = await hook_runtime.emit_from_hermes_locals_async(
        {
            "chat_id": "oc_invalid_profile",
            "message_id": "om_invalid_profile",
            "profile_id": "../work",
        },
        "message.started",
    )

    assert handled is False
    assert calls == []


async def test_terminal_policy_tombstone_remains_bounded_but_does_not_expire(
    monkeypatch,
):
    now = [100.0]
    calls = []

    def policy(_url, _payload, _timeout):
        calls.append(True)
        return {
            "ok": True,
            "disposition": "card" if len(calls) == 1 else "native",
            "ttl_ms": 1000,
        }

    async def post(_url, _payload, _timeout):
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(hook_runtime, "_fetch_delivery_policy_sync", policy)

    monkeypatch.setattr(hook_runtime, "_post_json_response", post)
    turn = {"chat_id": "oc_terminal_replay", "message_id": "om_terminal_replay"}

    assert await hook_runtime.emit_from_hermes_locals_async(
        turn, "message.started"
    )
    assert await hook_runtime.emit_from_hermes_locals_async(
        {**turn, "answer": "done"}, "message.completed"
    )
    now[0] += 3600.0
    assert await hook_runtime.emit_from_hermes_locals_async(
        {**turn, "answer": "done"}, "message.completed"
    )
    assert calls == [True]
    assert len(hook_runtime._TERMINAL_POLICY_DECISIONS) == 1


async def test_async_policy_gate_delegates_singleflight_off_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    worker_threads = []

    def sync_gate(_config, _local_vars, _event_name):
        worker_threads.append(threading.get_ident())
        return hook_runtime._PolicyGateResult(True, None)

    monkeypatch.setattr(hook_runtime, "_policy_gate_sync", sync_gate)

    result = await hook_runtime._policy_gate_async(
        hook_runtime.load_runtime_config(),
        {"chat_id": "oc_thread_handoff"},
        "answer.delta",
    )

    assert result.card is True
    assert worker_threads and worker_threads[0] != event_loop_thread


async def test_async_policy_gate_reuses_pinned_turn_without_worker_handoff(
    monkeypatch,
):
    config = hook_runtime.load_runtime_config()
    local_vars = {"chat_id": "oc_pinned_fast", "message_id": "om_pinned_fast"}

    assert hook_runtime._policy_gate_sync(
        config,
        local_vars,
        "message.started",
    ).card

    async def unexpected_to_thread(*_args, **_kwargs):
        raise AssertionError("pinned decisions must stay on the fast path")

    monkeypatch.setattr(hook_runtime.asyncio, "to_thread", unexpected_to_thread)

    result = await hook_runtime._policy_gate_async(
        config,
        {**local_vars, "text": "delta"},
        "answer.delta",
    )

    assert result.card is True


async def test_async_policy_lock_is_not_idle_while_waiter_is_waking():
    lock = asyncio.Lock()
    await lock.acquire()
    waiter = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0)

    lock.release()

    assert lock.locked() is False
    assert hook_runtime._async_policy_lock_is_idle(lock) is False

    await waiter
    lock.release()
    assert hook_runtime._async_policy_lock_is_idle(lock) is True


def test_policy_headers_use_distinct_signed_domain(monkeypatch):
    root = b"p" * 32
    body = b'{"schema_version":"1"}'
    monkeypatch.setattr(hook_runtime, "read_transport_root_secret", lambda: root)

    headers = hook_runtime._post_headers(
        "http://127.0.0.1:8765/delivery/policy",
        body,
    )

    PolicyProofVerifier(root).verify(headers, body)
    with pytest.raises(Exception):
        EventProofVerifier(root).verify(headers, body)


def test_policy_cache_is_bounded_and_reset_clears_all_policy_state():
    config = hook_runtime.load_runtime_config()
    for index in range(hook_runtime.POLICY_CACHE_LIMIT + 25):
        identity = hook_runtime._policy_identity(
            config,
            {
                "chat_id": f"oc_{index}",
                "message_id": f"om_{index}",
            },
            "message.started",
        )
        assert identity is not None
        hook_runtime._cache_policy_disposition(identity, "card", 1.0)
        hook_runtime._pin_policy_disposition(identity, "card")

    assert len(hook_runtime._POLICY_CACHE) == hook_runtime.POLICY_CACHE_LIMIT
    assert len(hook_runtime._TURN_POLICY_DECISIONS) == hook_runtime.POLICY_CACHE_LIMIT

    hook_runtime.reset_runtime_state()

    assert hook_runtime._POLICY_CACHE == {}
    assert hook_runtime._TURN_POLICY_DECISIONS == {}
    assert hook_runtime._ACTIVE_POLICY_TURNS == {}
    assert hook_runtime._TERMINAL_POLICY_DECISIONS == {}


def test_policy_query_is_thread_safe_and_pins_one_decision_per_turn(monkeypatch):
    calls = []
    calls_lock = threading.Lock()

    def slow_policy(*_args, **_kwargs):
        with calls_lock:
            calls.append(True)
        time.sleep(0.02)
        return {"ok": True, "disposition": "card", "ttl_ms": 0}

    monkeypatch.setattr(hook_runtime, "_fetch_delivery_policy_sync", slow_policy)
    config = hook_runtime.load_runtime_config()
    local_vars = {"chat_id": "oc_thread", "message_id": "om_thread"}

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(
            executor.map(
                lambda _index: hook_runtime._policy_gate_sync(
                    config,
                    local_vars,
                    "answer.delta",
                ).card,
                range(24),
            )
        )

    assert results == [True] * 24
    assert calls == [True]


async def test_threadsafe_pending_flush_preserves_policy_new_turn_marker(
    monkeypatch,
):
    flushed = []

    async def flush(_config, event_locals, event_name):
        flushed.append((event_locals, event_name))

    monkeypatch.setattr(hook_runtime, "_queue_coalesced_delta", lambda *_args: False)
    monkeypatch.setattr(
        hook_runtime,
        "_has_pending_deltas_for_local_vars",
        lambda *_args: True,
    )
    monkeypatch.setattr(hook_runtime, "_flush_build_send_ordered", flush)

    assert hook_runtime.emit_from_hermes_locals_threadsafe(
        {"chat_id": "oc_pending_new", "message_id": "om_pending_new"},
        "message.started",
    )
    await asyncio.sleep(0)

    assert flushed[0][1] == "message.started"
    assert flushed[0][0]["_hfc_policy_new_turn"] is True


def test_cron_requires_applied_true_not_only_http_success(monkeypatch):
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_sync_response",
        lambda *_args, **_kwargs: {"ok": True, "applied": False},
    )

    assert not hook_runtime.emit_cron_delivery(
        {
            "job": {
                "id": "job-native",
                "origin": {"platform": "feishu", "chat_id": "oc_cron"},
            },
            "content": "native cron result",
        }
    )


async def test_native_direct_send_uses_original_content_once_and_clears_media_state(
    monkeypatch,
):
    def native_policy(*_args, **_kwargs):
        return {"ok": True, "disposition": "native", "ttl_ms": 1000}

    calls = []

    class Adapter:
        async def original(self, chat_id, content, reply_to=None, metadata=None):
            calls.append((chat_id, content, reply_to, metadata))
            return SimpleNamespace(success=True, message_id="om_native")

    Adapter._hfc_original_send = Adapter.original
    hook_runtime._HFC_NATIVE_MEDIA_TEXT_SUPPRESSION.set(
        hook_runtime._NativeMediaTextSuppression("oc_native", "answer")
    )
    monkeypatch.setattr(hook_runtime, "_fetch_delivery_policy_sync", native_policy)

    result = await hook_runtime._hfc_send_with_native_command_result_card(
        Adapter(),
        "oc_native",
        "answer",
    )

    assert result.success is True
    assert calls == [("oc_native", "answer", None, None)]
    assert hook_runtime._HFC_NATIVE_MEDIA_TEXT_SUPPRESSION.get() is None


def test_native_platform_notice_falls_through_without_scheduling_card(monkeypatch):
    monkeypatch.setattr(
        hook_runtime,
        "_fetch_delivery_policy_sync",
        lambda *_args, **_kwargs: {
            "ok": True,
            "disposition": "native",
            "ttl_ms": 1000,
        },
    )
    scheduled = []
    monkeypatch.setattr(
        hook_runtime,
        "_hfc_schedule_platform_notice_card",
        lambda **kwargs: scheduled.append(kwargs),
    )
    source = SimpleNamespace(
        platform="feishu",
        chat_id="oc_native_notice",
        message_id="om_notice",
    )
    runner = SimpleNamespace(adapters={"feishu": object()})

    handled = hook_runtime.handle_platform_notice_from_hermes(
        runner,
        source,
        "⏳ Working — native notice",
    )

    assert handled is False
    assert scheduled == []


def test_interaction_select_returns_empty_response_when_sidecar_rejects(monkeypatch):
    """Expired/rejected interactions should not crash or fall through."""

    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def _on_card_action_trigger(self, data):
            raise AssertionError("interaction.select should be handled by HFC")

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )

    def fake_post(url, payload, timeout):
        raise error.HTTPError(url, 404, "not found", {}, None)

    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", fake_post)

    adapter = DummyFeishuAdapter()
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "interaction.select",
                    "interaction_id": "int-1",
                    "choice": "opt_b",
                    "choice_label": "Option B",
                    "token": "tok-1",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user"),
        )
    )

    response = hook_runtime._hfc_on_feishu_card_action_trigger(adapter, data)

    assert response.card is None


@pytest.mark.asyncio
async def test_stale_bound_card_action_callback_forwards_interaction_select(monkeypatch):
    """A callback captured before HFC patches the class must still reach sidecar."""

    posted = threading.Event()
    native_actions = []

    def fake_post(url, payload, timeout):
        assert url == "http://127.0.0.1:8765/card/actions"
        assert payload["event"]["action"]["value"]["hfc_action"] == "interaction.select"
        posted.set()
        return {"ok": True, "card": {"header": {"template": "green"}}}

    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", fake_post)
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self._loop = asyncio.get_running_loop()
            self.sdk_callback = self._on_card_action_trigger

        def _on_card_action_trigger(self, data):
            self._loop.create_task(self._handle_card_action_event(data))
            return "sdk-ack"

        async def _handle_card_action_event(self, data):
            native_actions.append(data)

    adapter = DummyFeishuAdapter()
    captured_callback = adapter.sdk_callback
    runner = SimpleNamespace(adapters={"feishu": adapter})
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "interaction.select",
                    "interaction_id": "int-stale-bound",
                    "choice": "approve_once",
                    "choice_label": "允许一次",
                    "token": "tok-stale-bound",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_abc"),
            operator=SimpleNamespace(open_id="ou_user", user_name="Bailey"),
        )
    )

    assert hook_runtime.install_feishu_command_card_adapter_methods(runner) is True
    assert captured_callback.__func__ is not hook_runtime._hfc_on_feishu_card_action_trigger

    assert captured_callback(data) == "sdk-ack"
    assert await asyncio.to_thread(posted.wait, 1.0)
    assert native_actions == []


def test_operations_select_passes_admission_and_forwards_profile_context(monkeypatch):
    class FakeCallBackCard:
        def __init__(self):
            self.type = None
            self.data = None

    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def __init__(self):
            self.allowed = []

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            self.allowed.append((sender_id.open_id, chat_id, is_bot))
            return True

        def _on_card_action_trigger(self, data):
            raise AssertionError("recognized operations action fell through")

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setenv("HERMES_FEISHU_CARD_PROFILE_ID", "work")
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(hook_runtime, "CallBackCard", FakeCallBackCard, raising=False)
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )
    posted = {}
    posted_event = threading.Event()

    def fake_post(url, payload, timeout):
        posted.update(url=url, payload=payload, timeout=timeout)
        posted_event.set()
        return {
            "ok": True,
            "operation_id": "operation-successor",
            "card": {
                "header": {"template": "orange"},
                "body": {
                    "elements": [{"tag": "markdown", "content": "正在重新检测"}]
                },
            },
        }

    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", fake_post)
    token = _operation_token()
    hook_runtime._remember_operation_transport(
        "operation-1", "process-local-secret", "work"
    )
    adapter = DummyFeishuAdapter()
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "operations.select",
                    "operation_action": "repair",
                    "token": token,
                    "profile_scope": "opaque-scope",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_group"),
            operator=SimpleNamespace(open_id="ou_owner", user_id="user-1"),
        )
    )

    response = hook_runtime._hfc_on_feishu_card_action_trigger(adapter, data)

    assert posted_event.wait(1.0)
    assert adapter.allowed == [("ou_owner", "oc_group", False)]
    assert posted["url"] == "http://127.0.0.1:8765/card/actions"
    assert posted["payload"]["event"]["context"] == {
        "open_chat_id": "oc_group",
        "profile_id": "work",
    }
    assert posted["payload"]["event"]["operator"] == {"open_id": "ou_owner"}
    assert posted["payload"]["event"]["action"]["value"] == {
        "hfc_action": "operations.select",
        "operation_action": "repair",
        "token": token,
        "profile_scope": "opaque-scope",
    }
    assert posted["payload"]["adapter_transport_proof"]["signature"]
    assert posted["payload"]["adapter_transport_proof"]["timestamp"] > 0
    assert posted["timeout"] == hook_runtime.OPERATIONS_ACTION_TIMEOUT_SECONDS
    assert posted["timeout"] >= 10.0
    assert response.card is None
    assert hook_runtime._operation_transport_context("operation-successor") == (
        b"process-local-secret",
        "work",
    )


def test_operations_select_acks_before_daemon_forward_and_remembers_successor_transport(
    monkeypatch,
):
    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return True

    class CapturedDispatcher:
        def __init__(self):
            self.tasks = []

        def submit(self, task):
            self.tasks.append(task)
            return True

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    dispatcher = CapturedDispatcher()
    monkeypatch.setattr(
        hook_runtime, "_OPERATIONS_ACTION_DISPATCHER", dispatcher
    )
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )
    posted = []

    def fake_post(url, payload, timeout):
        posted.append((url, payload, timeout))
        if len(posted) == 1:
            raise TimeoutError("slow sidecar")
        return {"ok": True, "operation_id": "operation-successor"}

    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", fake_post)
    token = _operation_token()
    hook_runtime._remember_operation_transport(
        "operation-1", "process-local-secret", "work"
    )
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "operations.select",
                    "operation_action": "repair",
                    "token": token,
                    "profile_scope": "opaque-scope",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_group"),
            operator=SimpleNamespace(open_id="ou_owner"),
        )
    )

    response = hook_runtime._hfc_on_feishu_card_action_trigger(
        DummyFeishuAdapter(), data
    )

    assert response.card is None
    assert posted == []
    assert len(dispatcher.tasks) == 1

    dispatcher.tasks[0]()

    assert len(posted) == 2
    assert posted[-1][0] == "http://127.0.0.1:8765/card/actions"
    assert posted[-1][2] == hook_runtime.OPERATIONS_ACTION_TIMEOUT_SECONDS
    assert posted[-1][2] >= 10.0
    assert hook_runtime._operation_transport_context("operation-successor") == (
        b"process-local-secret",
        "work",
    )


def test_operations_select_forwards_update_evidence_fingerprint(monkeypatch):
    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return True

    class CapturedDispatcher:
        def __init__(self):
            self.tasks = []

        def submit(self, task):
            self.tasks.append(task)
            return True

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    dispatcher = CapturedDispatcher()
    monkeypatch.setattr(
        hook_runtime, "_OPERATIONS_ACTION_DISPATCHER", dispatcher
    )
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )
    posted = []
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_sync_response",
        lambda url, payload, timeout: posted.append((url, payload, timeout))
        or {"ok": True},
    )
    token = _operation_token()
    hook_runtime._remember_operation_transport(
        "operation-1", "process-local-secret", "work"
    )
    fingerprint = "e" * 64
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "operations.select",
                    "operation_action": "confirm_update",
                    "token": token,
                    "profile_scope": "opaque-scope",
                    "update_evidence_fingerprint": fingerprint,
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_private"),
            operator=SimpleNamespace(open_id="ou_owner"),
        )
    )

    response = hook_runtime._hfc_on_feishu_card_action_trigger(
        DummyFeishuAdapter(), data
    )
    dispatcher.tasks[0]()

    forwarded_value = posted[0][1]["event"]["action"]["value"]
    assert forwarded_value["update_evidence_fingerprint"] == fingerprint
    assert response.card is None


def test_operations_select_slow_forward_does_not_delay_callback(monkeypatch):
    class FakeP2Response:
        def __init__(self):
            self.card = None
            self.toast = None

    class DummyFeishuAdapter:
        name = "feishu"

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return True

    release = threading.Event()
    completed = threading.Event()

    def slow_post(*_args):
        release.wait(1.0)
        completed.set()
        return {"ok": True}

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )
    monkeypatch.setattr(hook_runtime, "_post_json_sync_response", slow_post)
    token = _operation_token()
    hook_runtime._remember_operation_transport(
        "operation-1", "process-local-secret", "work"
    )
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "operations.select",
                    "operation_action": "repair",
                    "token": token,
                    "profile_scope": "opaque-scope",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_group"),
            operator=SimpleNamespace(open_id="ou_owner"),
        )
    )

    started = time.monotonic()
    response = hook_runtime._hfc_on_feishu_card_action_trigger(
        DummyFeishuAdapter(), data
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert response.card is None
    release.set()
    assert completed.wait(1.0)


def test_operations_select_full_dispatcher_returns_retry_toast(monkeypatch):
    class FakeToast:
        def __init__(self):
            self.type = None
            self.content = None

    class FakeP2Response:
        _types = {"toast": FakeToast}

        def __init__(self):
            self.card = None
            self.toast = None

    class DummyFeishuAdapter:
        name = "feishu"

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return True

    class FullDispatcher:
        def submit(self, _task):
            return False

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(
        hook_runtime, "_OPERATIONS_ACTION_DISPATCHER", FullDispatcher()
    )
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: SimpleNamespace(event_url="http://127.0.0.1:8765/events"),
    )
    token = _operation_token()
    hook_runtime._remember_operation_transport(
        "operation-1", "process-local-secret", "work"
    )
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "operations.select",
                    "operation_action": "repair",
                    "token": token,
                    "profile_scope": "opaque-scope",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_group"),
            operator=SimpleNamespace(open_id="ou_owner"),
        )
    )

    response = hook_runtime._hfc_on_feishu_card_action_trigger(
        DummyFeishuAdapter(), data
    )

    assert response.card is None
    assert response.toast.type == "warning"
    assert "稍后重试" in response.toast.content


def test_operations_action_dispatcher_queues_beyond_active_workers_and_bounds_pending():
    dispatcher = hook_runtime._OperationsActionDispatcher(
        workers=1, max_pending=1
    )
    started = threading.Event()
    release = threading.Event()
    completed = []

    def blocked_task():
        started.set()
        release.wait(1.0)
        completed.append("blocked")

    assert dispatcher.submit(blocked_task) is True
    assert started.wait(1.0)
    assert dispatcher.submit(lambda: completed.append("queued")) is True
    assert dispatcher.submit(lambda: completed.append("overflow")) is False

    release.set()
    dispatcher.wait()

    assert completed == ["blocked", "queued"]


def test_operations_select_rejected_admission_is_claimed_without_forward(monkeypatch):
    class FakeP2Response:
        def __init__(self):
            self.card = None

    class DummyFeishuAdapter:
        name = "feishu"

        def _allow_group_message(self, sender_id, chat_id, is_bot=False):
            return False

        def _on_card_action_trigger(self, data):
            raise AssertionError("recognized operations action fell through")

    DummyFeishuAdapter.__module__ = hook_runtime.__name__
    DummyFeishuAdapter._hfc_original_on_card_action_trigger = (
        lambda self, data: (_ for _ in ()).throw(
            AssertionError("recognized operations action fell through")
        )
    )
    monkeypatch.setattr(
        hook_runtime, "P2CardActionTriggerResponse", FakeP2Response, raising=False
    )
    monkeypatch.setattr(
        hook_runtime,
        "_post_json_sync_response",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not forward")),
    )
    data = SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "hfc_action": "operations.select",
                    "operation_action": "repair",
                    "token": "opaque-token",
                }
            ),
            context=SimpleNamespace(open_chat_id="oc_group"),
            operator=SimpleNamespace(open_id="ou_denied", user_id="user-2"),
        )
    )

    response = hook_runtime._hfc_on_feishu_card_action_trigger(
        DummyFeishuAdapter(), data
    )

    assert response.card is None


def test_hook_runtime_routes_every_card_serializer_through_delivery_limits():
    source = inspect.getsource(hook_runtime)

    assert "json.dumps(card, ensure_ascii=False)" not in source
    assert source.count("serialize_card_for_delivery(card)") >= 7


def test_native_command_card_update_rejects_oversize_before_sdk(monkeypatch):
    calls = []

    class DummyAdapter:
        _client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(message=SimpleNamespace(patch=lambda request: None))
            )
        )

        async def _run_blocking(self, func, request):
            calls.append((func, request))
            return SimpleNamespace(success=lambda: True)

    monkeypatch.setattr(
        hook_runtime,
        "_hfc_build_patch_message_request",
        lambda supplied_id, content: SimpleNamespace(
            message_id=supplied_id,
            request_body=SimpleNamespace(content=content),
        ),
    )

    result = asyncio.run(
        hook_runtime._hfc_update_native_command_card(
            DummyAdapter(),
            "om_sensitive_update_id",
            {"elements": [{"tag": "markdown", "content": "x" * 40_000}]},
        )
    )

    assert result is False
    assert calls == []


def test_native_command_card_logs_never_expose_raw_identifiers(
    monkeypatch, caplog, capsys
):
    message_id = "om_sensitive_message_123"

    class DummyAdapter:
        _client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(message=SimpleNamespace(patch=lambda request: None))
            )
        )

        async def _run_blocking(self, func, request):
            raise RuntimeError(f"remote rejected {message_id}")

    monkeypatch.setattr(
        hook_runtime,
        "_hfc_build_patch_message_request",
        lambda supplied_id, content: SimpleNamespace(
            message_id=supplied_id,
            request_body=SimpleNamespace(content=content),
        ),
    )
    caplog.set_level("INFO", logger=hook_runtime.__name__)

    result = asyncio.run(
        hook_runtime._hfc_update_native_command_card(
            DummyAdapter(),
            message_id,
            {"elements": [{"tag": "markdown", "content": "done"}]},
        )
    )

    output = caplog.text + capsys.readouterr().err
    assert result is False
    assert message_id not in output
    assert "message#" in output
    assert "RuntimeError" in output
