"""Regression tests for #552: duplicate Feishu native interim commentary."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.install import patcher


def _make_runtime_config(enabled: bool = True) -> hook_runtime.RuntimeConfig:
    return hook_runtime.RuntimeConfig(
        enabled=enabled,
        event_url="http://127.0.0.1:18765/events",
        timeout_seconds=1.0,
        delta_coalesce_ms=0,
        delta_coalesce_chars=4096,
        delta_coalesce_max_pending=32,
    )


def _feishu_source(chat_id: str = "oc_chat_552") -> SimpleNamespace:
    return SimpleNamespace(
        platform="feishu",
        chat_id=chat_id,
        chat_type="p2p",
        user_id="ou_user_552",
        thread_id="",
        profile_id="default",
    )


def test_thinking_delta_patch_enters_hook_when_already_streamed_and_restores_v2_template():
    content = (
        "async def _handle_message_with_agent(self, event, source, _quick_key, run_generation):\n"
        "    return await self._run_agent(event_message_id=event.message_id)\n"
        "\n"
        "async def _run_agent(self, source, event_message_id=None):\n"
        "    _loop_for_step = asyncio.get_running_loop()\n"
        "    session_key = 'sess-552'\n"
        "    _status_chat_id = source.chat_id\n"
        "    _approval_session_key = session_key\n"
        "    def _run_still_current():\n"
        "        return True\n"
        "    def _stream_delta_cb(text: str) -> None:\n"
        "        if _run_still_current():\n"
        "            _stream_consumer.on_delta(text)\n"
        "    def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:\n"
        "        if already_streamed and _stream_consumer.stream_deltas_enabled:\n"
        "            _stream_consumer.on_segment_break()\n"
        "        else:\n"
        "            _stream_consumer.on_commentary(text)\n"
    )
    patched = patcher.apply_patch(content)
    start = patched.index(patcher.THINKING_DELTA_PATCH_BEGIN)
    end = patched.index(patcher.THINKING_DELTA_PATCH_END)
    block = patched[start:end]
    assert "if text and _run_still_current():" in block
    assert "not already_streamed" not in block
    assert patcher.remove_patch(patched) == content

    # Upgrading or restoring a pre-fix v5.0.30 (v2) rendered block must still
    # recover the exact original Hermes source byte-for-byte.
    v2_patched = patched.replace(
        block,
        block.replace(
            "if text and _run_still_current():",
            "if text and not already_streamed and _run_still_current():",
            1,
        ),
        1,
    )
    assert v2_patched != patched
    assert patcher.remove_patch(v2_patched) == content

    # Decomposed TurnRunner (ctx._run_still_current) v5.0.30 block must also restore cleanly.
    decomposed_src = (
        "class TurnRunner:\n"
        "    def _setup_stream_consumer(self, ctx, stts, held):\n"
        "        def stream_delta_cb(text):\n"
        "            if not ctx._run_still_current():\n"
        "                return\n"
        "            pass\n"
        "        def interim_assistant_cb(text, *, already_streamed=False):\n"
        "            if not ctx._run_still_current():\n"
        "                return\n"
        "            stream_consumer.on_commentary(text)\n"
        "        return stream_consumer, stream_delta_cb, interim_assistant_cb, True\n"
    )
    v2_turn_block = "".join(
        patcher._render_turn_context_hook_block(
            patcher._render_v5030_thinking_delta_hook_block, "            ", "\n"
        )
    )
    decomposed_v2 = decomposed_src.replace(
        "        def interim_assistant_cb(text, *, already_streamed=False):\n",
        "        def interim_assistant_cb(text, *, already_streamed=False):\n" + v2_turn_block,
        1,
    )
    assert patcher.remove_patch(decomposed_v2) == decomposed_src


def test_already_streamed_thinking_delta_suppresses_without_duplicate_emit_when_card_owns_turn(
    monkeypatch,
):
    sent = []
    source = _feishu_source()
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: _make_runtime_config(True))
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda _cfg: None)
    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_sync",
        lambda _cfg, _lv, _ev: hook_runtime._PolicyGateResult(True, None),
    )
    monkeypatch.setattr(hook_runtime, "_queue_coalesced_delta", lambda *_a, **_k: sent.append("queued") or False)
    monkeypatch.setattr(
        hook_runtime,
        "_send_fail_open_ordered",
        lambda *_a, **_k: sent.append("sent"),
    )

    seq_before = hook_runtime._peek_next_sequence("om_552_card")
    result = hook_runtime.emit_from_hermes_locals_threadsafe(
        {
            "source": source,
            "message_id": "om_552_card",
            "text": "先查一下配置。",
            "already_streamed": True,
            "mode": "append_block",
        },
        event_name="thinking.delta",
    )
    assert result is True
    assert sent == []
    assert hook_runtime._peek_next_sequence("om_552_card") == seq_before


def test_already_streamed_thinking_delta_async_helper_also_suppresses_without_duplicate_emit(
    monkeypatch,
):
    sent = []
    source = _feishu_source()
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: _make_runtime_config(True))
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda _cfg: None)
    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_sync",
        lambda _cfg, _lv, _ev: hook_runtime._PolicyGateResult(True, None),
    )
    monkeypatch.setattr(hook_runtime, "_send_fail_open_ordered", lambda *_a, **_k: sent.append("sent"))

    result = hook_runtime.emit_from_hermes_locals(
        {
            "source": source,
            "message_id": "om_552_async",
            "text": "先查一下配置。",
            "already_streamed": True,
            "mode": "append_block",
        },
        event_name="thinking.delta",
    )
    assert result is True
    assert sent == []


@pytest.mark.parametrize(
    "case,policy_card,source,text",
    [
        ("policy_native_fail_open", False, _feishu_source(), "先查一下配置。"),
        ("non_feishu_platform", True, SimpleNamespace(platform="telegram", chat_id="tg_1"), "先查一下配置。"),
        ("local_platform", True, SimpleNamespace(platform="local", chat_id="local_1"), "先查一下配置。"),
        ("missing_chat_id", True, SimpleNamespace(platform="feishu", chat_id=""), "先查一下配置。"),
    ],
)
def test_already_streamed_thinking_delta_preserves_fail_open_outside_card_turns(
    monkeypatch, case, policy_card, source, text
):
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: _make_runtime_config(True))
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda _cfg: None)
    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_sync",
        lambda _cfg, _lv, _ev: hook_runtime._PolicyGateResult(policy_card, None),
    )
    result = hook_runtime.emit_from_hermes_locals_threadsafe(
        {
            "source": source,
            "message_id": f"om_552_{case}",
            "text": text,
            "already_streamed": True,
            "mode": "append_block",
        },
        event_name="thinking.delta",
    )
    assert result is False, case


def test_unstreamed_thinking_delta_still_emits_append_block_when_card_owns_turn(monkeypatch):
    queued = []
    source = _feishu_source()
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: _make_runtime_config(True))
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda _cfg: None)
    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_sync",
        lambda _cfg, _lv, _ev: hook_runtime._PolicyGateResult(True, None),
    )
    monkeypatch.setattr(
        hook_runtime,
        "_queue_coalesced_delta",
        lambda _cfg, ev_locals, ev_name: queued.append((ev_name, ev_locals.get("text"), ev_locals.get("mode"))) or True,
    )

    result = hook_runtime.emit_from_hermes_locals_threadsafe(
        {
            "source": source,
            "message_id": "om_552_nonstream",
            "text": "非流式模型的过程说明。",
            "already_streamed": False,
            "mode": "append_block",
        },
        event_name="thinking.delta",
    )
    assert result is True
    assert queued == [("thinking.delta", "非流式模型的过程说明。", "append_block")]


@pytest.mark.parametrize(
    "policy_card,stream_deltas_enabled,streaming_provider,expected_answer_deltas,expected_thinking_deltas,expected_native_commentary",
    [
        # Case A/B (#552): show_reasoning=True or global streaming=False leaves
        # stream_deltas_enabled=False, while ANSWER_DELTA_PATCH delivers streamed
        # deltas to the card and suppresses _NoStreamConsumer -> already_streamed=True.
        # Patched interim_assistant_cb must suppress native commentary and avoid
        # emitting duplicate thinking.delta.
        (True, False, True, ["第一步，检查配置。", "第二步，检查日志。"], [], []),
        # Case C: global streaming=True + show_reasoning=False -> stream_deltas_enabled=True.
        (True, True, True, ["第一步，检查配置。", "第二步，检查日志。"], [], []),
        # Case D: non-streaming provider -> already_streamed=False -> emits thinking.delta.
        (True, False, False, [], ["第一步，检查配置。", "第二步，检查日志。"], []),
        # Case F: policy=native fail-open -> raises _NoStreamConsumer -> native commentary once.
        (False, False, True, [], [], ["第一步，检查配置。", "第二步，检查日志。"]),
    ],
)
def test_patched_stream_and_interim_callbacks_deduplicate_across_turn_modes(
    monkeypatch,
    policy_card,
    stream_deltas_enabled,
    streaming_provider,
    expected_answer_deltas,
    expected_thinking_deltas,
    expected_native_commentary,
):
    """Execute the real patched callback code for stream_delta_cb and interim_assistant_cb."""
    answer_deltas = []
    thinking_deltas = []
    native_commentary = []
    segment_breaks = []

    class _NoStreamConsumer(Exception):
        pass

    class _FakeStreamConsumer:
        def __init__(self, enabled: bool):
            self.stream_deltas_enabled = enabled

        def on_delta(self, text):
            pass

        def on_segment_break(self):
            segment_breaks.append(True)

        def on_commentary(self, text):
            native_commentary.append(text)

    source = _feishu_source()
    monkeypatch.setattr(hook_runtime, "load_runtime_config", lambda: _make_runtime_config(True))
    monkeypatch.setattr(hook_runtime, "_ensure_runtime_control_started", lambda _cfg: None)
    monkeypatch.setattr(
        hook_runtime,
        "_policy_gate_sync",
        lambda _cfg, _lv, _ev: hook_runtime._PolicyGateResult(policy_card, None),
    )

    def _capture_queue(_cfg, ev_locals, ev_name):
        if ev_name == "answer.delta":
            answer_deltas.append(ev_locals.get("text"))
            return True
        if ev_name == "thinking.delta":
            thinking_deltas.append(ev_locals.get("text"))
            return True
        return False

    monkeypatch.setattr(hook_runtime, "_queue_coalesced_delta", _capture_queue)

    fragment = (
        "def make_callbacks(source, event_message_id, _loop_for_step, stream_consumer, delta_sinks, local_viewer_route, _run_still_current):\n"
        "    def stream_delta_cb(text):\n"
        "        if not _run_still_current():\n"
        "            return\n"
        "        delivered = bool(delta_sinks)\n"
        "        for sink in delta_sinks:\n"
        "            sink.on_delta(text)\n"
        "        if text is not None and not delivered:\n"
        "            raise _NoStreamConsumer()\n"
        "\n"
        "    def interim_assistant_cb(text, *, already_streamed=False):\n"
        "        if not _run_still_current():\n"
        "            return\n"
        "        if stream_consumer is not None:\n"
        "            if already_streamed and (stream_consumer.stream_deltas_enabled or local_viewer_route):\n"
        "                stream_consumer.on_segment_break()\n"
        "            else:\n"
        "                stream_consumer.on_commentary(text)\n"
        "\n"
        "    return stream_delta_cb, interim_assistant_cb\n"
    )
    # Apply the exact rendered hook blocks from patcher.py into the callbacks.
    answer_block = "".join(patcher._render_answer_delta_hook_block("        ", "\n"))
    thinking_block = "".join(patcher._render_thinking_delta_hook_block("        ", "\n"))
    patched_fragment = fragment.replace(
        "    def stream_delta_cb(text):\n",
        "    def stream_delta_cb(text):\n" + answer_block,
        1,
    ).replace(
        "    def interim_assistant_cb(text, *, already_streamed=False):\n",
        "    def interim_assistant_cb(text, *, already_streamed=False):\n" + thinking_block,
        1,
    )

    namespace = {"_NoStreamConsumer": _NoStreamConsumer}
    exec(compile(patched_fragment, "<synthetic_turn_runner>", "exec"), namespace)

    consumer = _FakeStreamConsumer(stream_deltas_enabled)
    sinks = [consumer] if stream_deltas_enabled else []
    loop = asyncio.new_event_loop()
    try:
        stream_delta_cb, interim_assistant_cb = namespace["make_callbacks"](
            source,
            f"om_552_{policy_card}_{stream_deltas_enabled}_{streaming_provider}",
            loop,
            consumer,
            sinks,
            False,
            lambda: True,
        )
        for text in ("第一步，检查配置。", "第二步，检查日志。"):
            already_streamed = False
            if streaming_provider:
                try:
                    stream_delta_cb(text)
                    already_streamed = True
                except _NoStreamConsumer:
                    already_streamed = False
            interim_assistant_cb(text, already_streamed=already_streamed)

        # Stale/cancelled turn must neither emit nor send native commentary.
        _, stale_interim_cb = namespace["make_callbacks"](
            source,
            "om_552_stale",
            loop,
            consumer,
            sinks,
            False,
            lambda: False,
        )
        stale_interim_cb("已过期回合。", already_streamed=True)
    finally:
        loop.close()

    assert answer_deltas == expected_answer_deltas
    assert thinking_deltas == expected_thinking_deltas
    assert native_commentary == expected_native_commentary
    assert segment_breaks == []
