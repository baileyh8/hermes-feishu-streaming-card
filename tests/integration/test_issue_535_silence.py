"""Intentional silence suppression and card settlement contracts (issue #535)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer
import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card import server as sidecar_server
from hermes_feishu_card.native_handoff import NativeHandoffStore
from hermes_feishu_card.server import (
    FEISHU_MESSAGE_IDS_KEY,
    SESSIONS_KEY,
)

from tests.integration.test_server import (
    FakeFeishuClient,
    FakeFeishuClientFactory,
    create_app,
    event_payload,
    identified,
    wait_for_condition,
)


def _card_text(card: dict) -> str:
    return json.dumps(card, ensure_ascii=False)


def _runtime_config(**overrides) -> hook_runtime.RuntimeConfig:
    defaults = {
        "enabled": True,
        "event_url": "http://127.0.0.1:19999/events",
        "timeout_seconds": 2.0,
        "delta_coalesce_ms": 0,
        "delta_coalesce_chars": 0,
        "delta_coalesce_max_pending": 16,
    }
    defaults.update(overrides)
    return hook_runtime.RuntimeConfig(**defaults)


async def _make_silence_client(tmp_path, feishu_client=None, **app_kwargs):
    client = feishu_client or FakeFeishuClient()
    app = create_app(
        client,
        native_handoff_store=NativeHandoffStore(tmp_path / "handoff-state"),
        **app_kwargs,
    )
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    return test_client, client, app


def test_silence_stream_hold_suppresses_markers_and_stays_bounded():
    hold = hook_runtime._SilenceMarkerStreamHold()
    assert hold.feed("NO") == ""
    assert hold.feed("_REPLY") == ""
    assert hold.take_non_silence() == ""

    hold_prose = hook_runtime._SilenceMarkerStreamHold()
    assert hold_prose.feed("NO") == ""
    assert hold_prose.feed(" problem") == "NO problem"
    assert hold_prose.feed(" at all") == " at all"

    hold_trailing = hook_runtime._SilenceMarkerStreamHold()
    assert hold_trailing.feed("Here is the answer.\n") == "Here is the answer.\n"
    assert hold_trailing.feed("NO") == ""
    assert hold_trailing.feed("_REPLY") == ""
    assert hold_trailing.take_non_silence() == ""

    hold_trailing_prose = hook_runtime._SilenceMarkerStreamHold()
    assert hold_trailing_prose.feed("Hello\nNO") == "Hello\n"
    assert hold_trailing_prose.feed(" problem") == "NO problem"

    hold_multiline = hook_runtime._SilenceMarkerStreamHold()
    assert hold_multiline.feed("Hello\nNO_REPLY\n") == "Hello\n"
    assert hold_multiline.feed("More") == "NO_REPLY\nMore"

    # Long single-line text without newlines must not accumulate in _line_buf.
    hold_long = hook_runtime._SilenceMarkerStreamHold()
    assert hold_long.feed("Hello ") == "Hello "
    for _ in range(200):
        chunk = "x" * 100
        assert hold_long.feed(chunk) == chunk
    assert hold_long._line_buf == ""


def test_hook_runtime_delta_hold_flushes_before_tool_and_clears_across_turns(monkeypatch):
    hook_runtime.reset_runtime_state()
    sent = []
    loop = asyncio.new_event_loop()
    try:
        monkeypatch.setattr(
            hook_runtime,
            "load_runtime_config",
            lambda: _runtime_config(),
        )
        monkeypatch.setattr(
            hook_runtime,
            "_policy_gate_sync",
            lambda config, lv, ev: hook_runtime._PolicyGateResult(
                card=True,
                identity=hook_runtime._policy_identity(config, lv, ev),
            ),
        )
        monkeypatch.setattr(
            hook_runtime,
            "_ensure_runtime_control_started",
            lambda *_a, **_k: None,
        )

        async def fake_send(_url, payload, _timeout):
            sent.append(payload)
            return True

        monkeypatch.setattr(hook_runtime, "_send_fail_open_ordered", fake_send)
        source = SimpleNamespace(platform="feishu", chat_id="oc_1", thread_id="", user_id="ou_1")
        base_locals = {
            "source": source,
            "message_id": "om_turn_1",
            "chat_id": "oc_1",
            "_hfc_loop": loop,
        }
        assert hook_runtime.emit_from_hermes_locals_threadsafe(
            {**base_locals, "text": "NO"}, "answer.delta"
        ) is True
        loop.run_until_complete(asyncio.sleep(0))
        assert sent == []

        # Tool call flushes non-silence prefix ("NO") before tool.updated.
        assert hook_runtime.emit_from_hermes_locals_threadsafe(
            {**base_locals, "tool_id": "t1", "name": "terminal", "status": "running"},
            "tool.updated",
        ) is True
        loop.run_until_complete(asyncio.sleep(0))
        assert [p["event"] for p in sent] == ["answer.delta", "tool.updated"]
        assert sent[0]["data"]["text"] == "NO"
        assert hook_runtime.emit_from_hermes_locals_threadsafe(
            {**base_locals, "answer": "NO"}, "message.completed"
        ) is True
        loop.run_until_complete(asyncio.sleep(0))
        assert hook_runtime._ANSWER_DELTA_HOLDS == {}

        # A new message.started clears any held buffer for that canonical id.
        sent.clear()
        turn2_locals = {**base_locals, "message_id": "om_turn_2"}
        assert hook_runtime.emit_from_hermes_locals_threadsafe(
            {**turn2_locals, "text": "NO"}, "answer.delta"
        ) is True
        loop.run_until_complete(asyncio.sleep(0))
        assert sent == []
        assert len(hook_runtime._ANSWER_DELTA_HOLDS) == 1
        assert hook_runtime.emit_from_hermes_locals_threadsafe(
            turn2_locals, "message.started"
        ) is True
        loop.run_until_complete(asyncio.sleep(0))
        assert [p["event"] for p in sent] == ["message.started"]
        assert hook_runtime._ANSWER_DELTA_HOLDS == {}
    finally:
        hook_runtime.reset_runtime_state()
        loop.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "internal,reply_expected,display_kind,is_bot,expected_verdict",
    [
        (True, None, "internal_notification", False, "silence_allowed"),
        (False, False, None, False, "silence_allowed"),
        (False, None, "process_complete", False, "silence_allowed"),
        (False, None, None, True, "silence_allowed"),
        (False, True, None, False, "silence_rejected"),
    ],
)
async def test_queued_silence_verdict_preserves_human_fallback_and_suppresses_machinery(
    monkeypatch, internal, reply_expected, display_kind, is_bot, expected_verdict,
):
    hook_runtime.reset_runtime_state()
    posted = []
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: _runtime_config(),
    )
    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_async",
        lambda config, lv, ev: asyncio.sleep(
            0,
            result=hook_runtime._PolicyGateResult(
                card=True,
                identity=hook_runtime._policy_identity(config, lv, ev),
            ),
        ),
    )
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *_a, **_k: None)

    async def fake_post(_config, payload, _event_name):
        posted.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_terminal_with_retry", fake_post)

    raw_source = SimpleNamespace(platform="feishu", chat_id="oc_1", thread_id="", user_id="ou_1", is_bot=is_bot)
    queued_event = SimpleNamespace(
        message_id="om_q1",
        internal=internal,
        reply_expected=reply_expected,
        metadata={"display_kind": display_kind} if display_kind else {},
    )
    source = hook_runtime.queued_followup_source(raw_source, queued_event)
    local_vars = {
        "source": source,
        "message_id": "om_q1",
        "answer": "NO_REPLY",
        "agent_result": {"final_response": "NO_REPLY", "failed": False},
    }
    assert hook_runtime._queued_silence_verdict(local_vars) == expected_verdict
    applied = await hook_runtime.emit_from_hermes_locals_async(local_vars, event_name="message.completed")
    assert len(posted) == 1
    data = posted[0]["data"]
    if expected_verdict == "silence_allowed":
        assert applied is True
        assert data.get("intentional_silence") is True
        assert data.get("native_fallback") is not True
        assert data["answer"] == ""
    else:
        assert applied is False
        assert data.get("intentional_silence") is not True
        assert data.get("native_fallback") is True
        assert data["native_delivery"] == "required"


@pytest.mark.asyncio
async def test_handoff_silence_keeps_admission_gate_and_posts_without_native_ack(monkeypatch):
    hook_runtime.reset_runtime_state()
    posted = []
    monkeypatch.setattr(
        hook_runtime,
        "load_runtime_config",
        lambda: _runtime_config(),
    )
    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_async",
        lambda config, lv, ev: asyncio.sleep(
            0,
            result=hook_runtime._PolicyGateResult(
                card=True,
                identity=hook_runtime._policy_identity(config, lv, ev),
            ),
        ),
    )
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *_a, **_k: None)
    monkeypatch.setattr(hook_runtime, "_exact_base_delivery_hook_available", lambda: True)
    from hermes_feishu_card import admission_completion as ac
    monkeypatch.setattr(ac, "mode", lambda _lv: "handoff")
    monkeypatch.setattr(ac, "admission_identity_ready", lambda _lv: True)
    monkeypatch.setattr(ac, "extractor_available", lambda _ad: True)
    monkeypatch.setattr(hook_runtime, "_admission_default_profile_unverified", lambda _lv: False)

    async def fake_post(_config, payload, _event_name):
        posted.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_terminal_with_retry", fake_post)

    aborted = []
    fake_offer = SimpleNamespace(abort=lambda: aborted.append(True))
    monkeypatch.setattr(ac, "prepare", lambda *_a, **_k: fake_offer)

    adapter = SimpleNamespace(_app_id="cli_app")
    runner = SimpleNamespace(_adapter_for_source=lambda _s: adapter)
    source = SimpleNamespace(platform="feishu", chat_id="oc_1", thread_id="", user_id="ou_1")
    event = SimpleNamespace(message_id="om_h1", source=source, internal=True)
    local_vars = {
        "self": runner,
        "source": source,
        "event": event,
        "message_id": "om_h1",
        "answer": "",
        "_intentional_silence": True,
        "agent_result": {"final_response": "", "turn_exit_reason": "notification_no_reply"},
    }
    assert hook_runtime.can_stage_exact_base_completion(local_vars) is True
    staged = await hook_runtime.stage_message_completed_from_hermes_locals_async(local_vars)
    assert staged is True
    assert aborted == [True]
    assert getattr(event, "_hfc_admission_display_completion", None) is None
    assert hook_runtime._HFC_EXACT_COMPLETION_STAGE.get() is None
    assert len(posted) == 1
    assert posted[0]["data"]["intentional_silence"] is True

    # If prepare() refuses in handoff mode, fail open without posting.
    posted.clear()
    monkeypatch.setattr(ac, "prepare", lambda *_a, **_k: None)
    assert await hook_runtime.stage_message_completed_from_hermes_locals_async(local_vars) is False
    assert posted == []


@pytest.mark.asyncio
async def test_server_silence_settles_empty_card_in_place_without_notify_or_delete(tmp_path):
    test_client, feishu_client, app = await _make_silence_client(
        tmp_path,
        card_config={"completion_notify": {"enabled": True, "placement": "message"}},
    )
    try:
        started = await test_client.post(
            "/events",
            json=identified(
                event_payload("message.started", 0, {"sender_open_id": "ou_1"}),
                "turn:turn-1:started",
            ),
        )
        assert started.status == 200
        await wait_for_condition(lambda: len(feishu_client.sent) == 1)

        completed = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.completed",
                    1,
                    {"answer": "", "intentional_silence": True, "duration": 1.2},
                ),
                "turn:turn-1:completed",
                phase="terminal",
            ),
        )
        assert completed.status == 200
        assert await completed.json() == {"ok": True, "applied": True}
        await wait_for_condition(lambda: len(feishu_client.updated) >= 1)

        assert feishu_client.deleted == []
        assert feishu_client.texts == []
        assert len(feishu_client.sent) == 1
        final_card = feishu_client.updated[-1][1]
        text = _card_text(final_card)
        assert "NO_REPLY" not in text
        assert "本轮无需回复（已静默结束）" in text
        assert "已完成" in text

        # Duplicate terminal event is idempotent and triggers no extra PATCH or notify.
        before_updates = len(feishu_client.updated)
        dup = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.completed",
                    2,
                    {"answer": "", "intentional_silence": True},
                ),
                "turn:turn-1:completed-dup",
                phase="terminal",
            ),
        )
        assert dup.status == 200
        assert await dup.json() == {"ok": True, "applied": True}
        assert len(feishu_client.updated) == before_updates
        assert feishu_client.texts == []

        # Next turn in the same topic opens a fresh card cleanly.
        next_turn = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.started",
                    0,
                    {"sender_open_id": "ou_1"},
                    message_id="hermes-message-2",
                    turn_id="turn-2",
                ),
                "turn:turn-2:started",
            ),
        )
        assert next_turn.status == 200
        await wait_for_condition(lambda: len(feishu_client.sent) == 2)
    finally:
        await test_client.close()


@pytest.mark.asyncio
async def test_server_silence_preserves_existing_body_attachments_and_approval_receipt(tmp_path):
    test_client, feishu_client, app = await _make_silence_client(tmp_path)
    try:
        await test_client.post(
            "/events",
            json=identified(
                event_payload("message.started", 0, {"sender_open_id": "ou_1"}),
                "turn:turn-1:started",
            ),
        )
        await wait_for_condition(lambda: len(feishu_client.sent) == 1)
        await test_client.post(
            "/events",
            json=identified(
                event_payload("answer.delta", 1, {"text": "已生成阶段性结果"}),
                "turn:turn-1:delta",
                phase="update",
            ),
        )
        await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "interaction.requested",
                    2,
                    {
                        "interaction_id": "int-1",
                        "kind": "approval",
                        "prompt": "Allow command?",
                        "options": [{"id": "allow", "label": "Allow"}],
                    },
                ),
                "turn:turn-1:int",
                phase="update",
            ),
        )
        await wait_for_condition(lambda: len(feishu_client.sent) == 2)

        session = app[SESSIONS_KEY]["turn-1"]
        session.attachments = [{"name": "report.txt", "kind": "file"}]

        completed = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.completed",
                    3,
                    {"answer": "", "intentional_silence": True},
                ),
                "turn:turn-1:completed",
                phase="terminal",
            ),
        )
        assert completed.status == 200
        assert await completed.json() == {"ok": True, "applied": True}
        await wait_for_condition(lambda: session.terminal_delivery_state == "delivered")

        assert feishu_client.deleted == []
        assert session.answer_text == "已生成阶段性结果"
        assert session.attachments == [{"name": "report.txt", "kind": "file"}]
        assert session.active_interaction.status == "failed"
        assert "已生成阶段性结果" in _card_text(feishu_client.updated[-1][1])
    finally:
        await test_client.close()


@pytest.mark.asyncio
async def test_server_silence_without_owner_or_on_patch_failure_never_creates_empty_card(tmp_path, monkeypatch):
    async def fast_retry(*_args, **_kwargs):
        return False

    monkeypatch.setattr(sidecar_server, "_retry_terminal_update", fast_retry)
    test_client, feishu_client, app = await _make_silence_client(tmp_path)
    try:
        # 1. No existing session -> applied=False, no card sent.
        orphan = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.completed",
                    0,
                    {"answer": "", "intentional_silence": True},
                    message_id="hermes-orphan",
                    turn_id="turn-orphan",
                ),
                "turn:turn-orphan:completed",
                phase="terminal",
            ),
        )
        assert orphan.status == 200
        assert await orphan.json() == {"ok": True, "applied": False}
        assert feishu_client.sent == []

        # 2. Session exists without feishu_message_id -> applied=False, no card sent.
        await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.started",
                    0,
                    {"sender_open_id": "ou_1"},
                    message_id="hermes-no-msg",
                    turn_id="turn-no-msg",
                ),
                "turn:turn-no-msg:started",
            ),
        )
        await wait_for_condition(lambda: len(feishu_client.sent) == 1)
        app[FEISHU_MESSAGE_IDS_KEY].pop("turn-no-msg", None)
        no_msg = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.completed",
                    1,
                    {"answer": "", "intentional_silence": True},
                    message_id="hermes-no-msg",
                    turn_id="turn-no-msg",
                ),
                "turn:turn-no-msg:completed",
                phase="terminal",
            ),
        )
        assert no_msg.status == 200
        assert await no_msg.json() == {"ok": True, "applied": False}
        assert len(feishu_client.sent) == 1

        # 3. Terminal PATCH failure on silent turn must NOT call recover() to send a new card.
        await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.started",
                    0,
                    {"sender_open_id": "ou_1"},
                    message_id="hermes-fail-patch",
                    turn_id="turn-fail-patch",
                ),
                "turn:turn-fail-patch:started",
            ),
        )
        await wait_for_condition(lambda: len(feishu_client.sent) == 2)
        feishu_client.update_failures_remaining = 10
        failed_patch = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.completed",
                    1,
                    {"answer": "", "intentional_silence": True},
                    message_id="hermes-fail-patch",
                    turn_id="turn-fail-patch",
                ),
                "turn:turn-fail-patch:completed",
                phase="terminal",
            ),
        )
        assert failed_patch.status == 200
        assert await failed_patch.json() == {"ok": True, "applied": True}
        session = app[SESSIONS_KEY]["turn-fail-patch"]
        await wait_for_condition(lambda: session.terminal_delivery_state == "failed")
        assert len(feishu_client.sent) == 2
    finally:
        await test_client.close()


@pytest.mark.asyncio
async def test_server_silence_and_native_fallback_isolate_profiles(tmp_path):
    factories = {
        "default": FakeFeishuClientFactory(),
        "sales": FakeFeishuClientFactory(),
    }
    test_client, _, app = await _make_silence_client(
        tmp_path,
        feishu_client=factories,
        bot_router=lambda _ev: ("default", "bindings.chats"),
    )
    try:
        # Profile default starts a turn; profile sales starts a turn with same turn_id.
        for profile in ("default", "sales"):
            resp = await test_client.post(
                "/events",
                json=identified(
                    event_payload(
                        "message.started",
                        0,
                        {"profile_id": profile, "profile_source": "hermes_home"},
                        turn_id="shared-turn",
                    ),
                    f"turn:{profile}:started",
                ),
            )
            assert resp.status == 200
        await wait_for_condition(
            lambda: len(factories["default"].clients["default"].sent) == 1
            and len(factories["sales"].clients["default"].sent) == 1
        )

        # Profile default ends with native_fallback (silence_rejected on human turn).
        fallback = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.completed",
                    1,
                    {
                        "profile_id": "default",
                        "profile_source": "hermes_home",
                        "answer": "",
                        "native_fallback": True,
                    },
                    turn_id="shared-turn",
                ),
                "turn:default:completed",
                phase="terminal",
            ),
        )
        assert fallback.status == 200
        assert await fallback.json() == {"ok": True, "applied": True}
        await wait_for_condition(lambda: len(factories["default"].clients["default"].updated) >= 1)
        assert app[SESSIONS_KEY]["default:shared-turn"].terminal_disposition == "native_fallback"
        assert "已转原生回复" in _card_text(factories["default"].clients["default"].updated[-1][1])
        assert app[SESSIONS_KEY]["sales:shared-turn"].status == "thinking"
        assert factories["sales"].clients["default"].updated == []
    finally:
        await test_client.close()


@pytest.mark.asyncio
async def test_server_native_fallback_duplicate_and_late_terminal_do_not_trigger_missing_fence(tmp_path):
    test_client, feishu_client, app = await _make_silence_client(tmp_path)
    try:
        generation = "a" * 32
        started = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.started",
                    0,
                    {
                        "sender_open_id": "ou_1",
                        "native_handoff": {"generation": generation},
                    },
                ),
                "turn:turn-1:started",
            ),
        )
        assert started.status == 200
        await wait_for_condition(lambda: len(feishu_client.sent) == 1)

        fallback = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.completed",
                    1,
                    {
                        "answer": "",
                        "native_fallback": True,
                        "native_handoff": {"generation": generation},
                    },
                ),
                "turn:turn-1:completed",
                phase="terminal",
            ),
        )
        assert fallback.status == 200
        assert await fallback.json() == {"ok": True, "applied": True}
        await wait_for_condition(lambda: len(feishu_client.updated) >= 1)
        assert app[SESSIONS_KEY]["turn-1"].terminal_disposition == "native_fallback"
        before_updates = len(feishu_client.updated)

        # Duplicate native_fallback and late message.failed must stay idempotent (never 503 missing_fence).
        dup = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.completed",
                    2,
                    {
                        "answer": "",
                        "native_fallback": True,
                        "native_handoff": {"generation": generation},
                    },
                ),
                "turn:turn-1:completed-dup",
                phase="terminal",
            ),
        )
        assert dup.status == 200
        assert await dup.json() == {"ok": True, "applied": True}

        late_failed = await test_client.post(
            "/events",
            json=identified(
                event_payload(
                    "message.failed",
                    3,
                    {
                        "error": "late failure",
                        "native_handoff": {"generation": generation},
                    },
                ),
                "turn:turn-1:late-failed",
                phase="terminal",
            ),
        )
        assert late_failed.status == 200
        assert await late_failed.json() == {"ok": True, "applied": True}
        assert len(feishu_client.updated) == before_updates
        assert app[sidecar_server.METRICS_KEY].events_rejected == 0
        assert app[sidecar_server.METRICS_KEY].native_handoff_fence_restore_refusals == 0
    finally:
        await test_client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_silence", [True, False])
async def test_generated_queued_complete_hook_handles_empty_notification_no_reply(monkeypatch, allow_silence):
    from hermes_feishu_card.install import patcher

    hook_runtime.reset_runtime_state()
    posted = []
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: _runtime_config())
    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_async",
        lambda config, lv, ev: asyncio.sleep(
            0,
            result=hook_runtime._PolicyGateResult(
                card=True,
                identity=hook_runtime._policy_identity(config, lv, ev),
            ),
        ),
    )
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *_a, **_k: None)

    async def fake_post(_config, payload, _event_name):
        posted.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_terminal_with_retry", fake_post)
    async def fake_post_ordered(_url, payload, _timeout):
        posted.append(payload)
        return {"ok": True, "applied": True}
    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post_ordered)

    block = "".join(patcher._render_queued_complete_hook_block("    ", "\n"))
    namespace = {}
    exec(
        "async def run(self, turn_ctx, _delivery_result, first_response, _already_streamed, result):\n"
        + block
        + "    return _already_streamed, first_response\n",
        namespace,
    )
    runner = SimpleNamespace(
        _is_intentional_silence=lambda dr, resp: hook_runtime.is_intentional_silence_result(dr, resp),
        _silence_allowed_for_source=lambda *_a: allow_silence,
    )
    source = SimpleNamespace(platform="feishu", chat_id="oc_1", thread_id="", user_id="ou_1")
    turn_ctx = SimpleNamespace(
        source=source,
        event_message_id="om_qfirst",
        persist_user_display_kind="internal_notification" if allow_silence else None,
        reply_expected=False if allow_silence else True,
    )
    delivery_result = {"final_response": "", "turn_exit_reason": "notification_no_reply", "failed": False}
    streamed, response = await namespace["run"](runner, turn_ctx, delivery_result, "", False, delivery_result)
    assert len(posted) == 1
    assert posted[0]["message_id"] == "om_qfirst"
    if allow_silence:
        assert streamed is True
        assert posted[0]["data"].get("intentional_silence") is True
    else:
        assert streamed is False
        assert posted[0]["data"].get("native_fallback") is True
    assert response == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,allow_silence,expected_silence",
    [
        ("room_opt_in", True, True),
        ("human_rejected", False, False),
        ("heartbeat_no_id", False, True),
    ],
)
async def test_generated_queued_followup_and_final_hooks_preserve_verdict_and_turn_ownership(
    monkeypatch, scenario, allow_silence, expected_silence,
):
    from hermes_feishu_card.install import patcher

    hook_runtime.reset_runtime_state()
    posted = []
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: _runtime_config())
    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_async",
        lambda config, lv, ev: asyncio.sleep(
            0,
            result=hook_runtime._PolicyGateResult(
                card=True,
                identity=hook_runtime._policy_identity(config, lv, ev),
            ),
        ),
    )
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *_a, **_k: None)

    async def fake_post(_config, payload, _event_name):
        posted.append(payload)
        return {"ok": True, "applied": True}

    monkeypatch.setattr(hook_runtime, "_post_terminal_with_retry", fake_post)
    async def fake_post_ordered(_url, payload, _timeout):
        posted.append(payload)
        return {"ok": True, "applied": True}
    monkeypatch.setattr(hook_runtime, "_post_json_ordered_response", fake_post_ordered)

    followup_block = "".join(patcher._render_queued_followup_hook_block("    ", "\n"))
    final_block = "".join(patcher._render_queued_final_hook_block("    ", "\n"))
    namespace = {}
    exec(
        "async def run(self, source, next_source, pending_event, next_message_id, next_display_kind, next_reply_expected, result, followup_result):\n"
        + followup_block
        + final_block
        + "    return next_source, followup_result\n",
        namespace,
    )
    parent_source = SimpleNamespace(
        platform="feishu",
        chat_id="oc_1",
        thread_id="",
        user_id="ou_1",
        _hfc_turn_id="om_parent",
        _hfc_internal_turn_id="hfc-internal:parent",
    )
    if scenario == "heartbeat_no_id":
        pending_event = SimpleNamespace(
            message_id="",
            internal=False,
            reply_expected=None,
            metadata={},
            _heartbeat_session_id="hb_1",
        )
        next_display_kind = "internal_notification"
        next_reply_expected = None
    else:
        pending_event = SimpleNamespace(
            message_id="om_child",
            internal=False,
            reply_expected=True,
            metadata={},
        )
        next_display_kind = None
        next_reply_expected = True

    runner = SimpleNamespace(
        _silence_allowed_for_source=lambda _src, dk, re, _res: (
            dk == "internal_notification" or re is False or allow_silence
        ),
    )
    next_source, followup_result = await namespace["run"](
        runner,
        parent_source,
        parent_source,
        pending_event,
        None,
        next_display_kind,
        next_reply_expected,
        {"interrupted": False},
        {"final_response": "NO_REPLY", "failed": False},
    )
    assert next_source._hfc_turn_id != "om_parent"
    assert next_source._hfc_internal_turn_id != "hfc-internal:parent"
    assert [p["event"] for p in posted] == ["message.started", "message.completed"]
    assert posted[0]["message_id"] == posted[1]["message_id"]
    if scenario == "heartbeat_no_id":
        assert posted[0]["message_id"].startswith("hfc-internal:")
    else:
        assert posted[0]["message_id"] == "om_child"
    if expected_silence:
        assert followup_result.get("_hfc_queued_final_delivered") is True
        assert posted[1]["data"].get("intentional_silence") is True
    else:
        assert followup_result.get("_hfc_queued_final_attempted") is True
        assert not followup_result.get("_hfc_queued_final_delivered")
        assert posted[1]["data"].get("native_fallback") is True


def test_emit_from_hermes_locals_clears_answer_delta_hold(monkeypatch):
    hook_runtime.reset_runtime_state()
    loop = asyncio.new_event_loop()
    try:
        monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: _runtime_config())
        monkeypatch.setattr(
            hook_runtime,
            "_policy_gate_sync",
            lambda config, lv, ev: hook_runtime._PolicyGateResult(
                card=True,
                identity=hook_runtime._policy_identity(config, lv, ev),
            ),
        )
        monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda *_a, **_k: None)

        async def fake_send(*_a, **_k):
            return True

        monkeypatch.setattr(hook_runtime, "_send_fail_open_ordered", fake_send)
        source = SimpleNamespace(platform="feishu", chat_id="oc_1", thread_id="", user_id="ou_1")
        base_locals = {"source": source, "message_id": "om_sync_clear", "chat_id": "oc_1", "_hfc_loop": loop}
        assert hook_runtime.emit_from_hermes_locals_threadsafe({**base_locals, "text": "NO"}, "answer.delta") is True
        assert len(hook_runtime._ANSWER_DELTA_HOLDS) == 1
        loop.run_until_complete(
            asyncio.sleep(0)
        )
        # Sync message.started (used by PATCH_BEGIN) must clear any stale hold for that turn.
        async def run_sync_emit():
            assert hook_runtime.emit_from_hermes_locals(base_locals, "message.started") is True
        loop.run_until_complete(run_sync_emit())
        assert hook_runtime._ANSWER_DELTA_HOLDS == {}
    finally:
        hook_runtime.reset_runtime_state()
        loop.close()
