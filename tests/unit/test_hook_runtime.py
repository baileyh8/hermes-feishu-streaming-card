import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import math
import threading
import time
from urllib import error

import pytest

from hermes_feishu_card import hook_runtime


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
    assert payload["data"] == {
        "profile_id": "default",
        "profile_source": "fallback_default",
    }


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
    assert payload["conversation_id"] == "oc_source"
    assert payload["message_id"].startswith("hfc_")


def test_build_event_uses_feishu_thread_id_as_conversation_id():
    class ThreadSourceObject:
        platform = "feishu"
        chat_id = "oc_source"
        thread_id = "omt_thread"

    payload = hook_runtime.build_event(
        "message.started",
        {
            "source": ThreadSourceObject(),
            "session_id": "agent:main:feishu:dm:oc_source:omt_thread",
        },
    )

    assert payload["chat_id"] == "oc_source"
    assert payload["conversation_id"] == "omt_thread"


def test_build_event_ignores_hermes_session_key_as_conversation_id():
    payload = hook_runtime.build_event(
        "message.started",
        {
            "chat_id": "oc_direct",
            "session_id": "agent:main:feishu:dm:oc_direct:omt_thread",
        },
    )

    assert payload["conversation_id"] == "oc_direct"


def test_build_event_prefers_feishu_thread_over_hermes_conversation_key():
    class ThreadSourceObject:
        platform = "feishu"
        chat_id = "oc_source"
        thread_id = "omt_thread"

    payload = hook_runtime.build_event(
        "message.started",
        {
            "source": ThreadSourceObject(),
            "conversation_id": "agent:main:feishu:dm:oc_source:omt_thread",
        },
    )

    assert payload["conversation_id"] == "omt_thread"


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

    assert payload["data"] == {
        "profile_id": "default",
        "profile_source": "fallback_default",
        "answer": "最终答案",
        "attachments": [],
        "duration": 2.75,
        "model": "MiniMax M2.7",
        "tokens": {"input_tokens": 12, "output_tokens": 34},
        "context": {"used_tokens": 182_000, "max_tokens": 204_000},
    }


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
    assert (
        hook_runtime.should_suppress_native_response("feishu", True, attachments)
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
    assert (
        hook_runtime.should_suppress_native_response("feishu", delivered, attachments)
        is False
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
    assert payload["data"] == {
        "profile_id": "default",
        "profile_source": "fallback_default",
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

    monkeypatch.setattr(hook_runtime, "_post_json", slow_first_sender)
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
    sender = SenderProbe()
    monkeypatch.setattr(hook_runtime, "_post_json", sender)
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
    assert len(sender.payloads) == 1
    url, payload, timeout = sender.payloads[0]
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
    sender = SenderProbe()
    sender.raise_error = True
    monkeypatch.setattr(hook_runtime, "_post_json", sender)

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
    assert len(sender.payloads) == 1


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
