"""Issue #479: HFC must not short-circuit another platform's delivery tail."""
import asyncio
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.install import patcher


class Platform(Enum):
    TELEGRAM = "telegram"
    FEISHU = "feishu"


RENDERERS = [
    patcher._render_complete_hook_block,
    patcher._render_complete_hook_block_with_reply_anchor,
    patcher._render_decomposed_complete_hook_block,
]


def completion(renderer):
    # Execute the actual generated hook before the native already_sent/media
    # tail, as in Hermes' _hmwa_deliver_turn_response. Track reaching the tail
    # separately: returning None without delivering media is also a regression.
    namespace = {}
    exec(
        "async def deliver(self, event, source, agent_result, response, native):\n"
        "    _response_time = 1\n"
        + "".join(renderer("    ", "\n"))
        + "    native.append(('tail', response))\n"
        "    if agent_result.get('already_sent') and not agent_result.get('failed'):\n"
        "        native.append(('media', response))\n"
        "        return None\n"
        "    return response\n",
        namespace,
    )
    return namespace["deliver"]


@pytest.mark.parametrize("renderer", RENDERERS)
@pytest.mark.parametrize("platform", ["telegram", Platform.TELEGRAM, "discord", "slack", "local", "unknown", None])
@pytest.mark.parametrize("mode", ["native", "handoff", "legacy"])
@pytest.mark.parametrize("result", [
    {"already_sent": True},
    {"already_sent": False},
    {"already_sent": True, "failed": True},
    {"already_sent": True, "_hfc_queued_final_attempted": True},
    {"already_sent": True, "_hfc_queued_final_delivered": True},
])
def test_non_feishu_preserves_native_delivery(monkeypatch, renderer, platform, mode, result):
    admission = Mock(return_value=mode)
    stage = AsyncMock(return_value=False)
    emit = AsyncMock(return_value=False)
    build = Mock(return_value=None)
    monkeypatch.setattr(hook_runtime, "admission_completion_mode", admission)
    monkeypatch.setattr(hook_runtime, "can_stage_exact_base_completion", Mock(return_value=False))
    monkeypatch.setattr(hook_runtime, "stage_message_completed_from_hermes_locals_async", stage)
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
    monkeypatch.setattr(hook_runtime, "build_event", build)
    runner = SimpleNamespace(_reply_anchor_for_event=Mock(return_value="reply"))
    source = SimpleNamespace(platform=platform)
    native = []
    answer = "answer\nMEDIA:/tmp/example.png\nfooter"
    original = dict(result)
    actual = asyncio.run(completion(renderer)(runner, SimpleNamespace(), source, result, answer, native))
    suppressed = result.get("already_sent") and not result.get("failed")
    assert actual == (None if suppressed else answer)
    assert native == [("tail", answer)] + ([("media", answer)] if suppressed else [])
    assert result == original
    admission.assert_not_called()
    stage.assert_not_called()
    emit.assert_not_called()
    build.assert_not_called()
    runner._reply_anchor_for_event.assert_not_called()


@pytest.mark.parametrize("renderer", RENDERERS)
@pytest.mark.parametrize("platform", ["feishu", Platform.FEISHU, "FEISHU"])
@pytest.mark.parametrize("scenario", ["staged", "stage_rejected", "stage_error", "native", "card", "card_media", "card_rejected", "card_error", "queued_failed", "queued_delivered", "queued_media"])
def test_feishu_completion_and_native_fallback(monkeypatch, renderer, platform, scenario):
    answer = "answer\nMEDIA:/tmp/example.png"
    result = {}
    if scenario.startswith("queued_"):
        result["_hfc_queued_final_attempted"] = True
        result["_hfc_queued_final_delivered"] = scenario != "queued_failed"
    mode = "native" if scenario == "native" else "handoff" if scenario.startswith("stag") else "legacy"
    stage = AsyncMock(return_value=scenario == "staged")
    emit = AsyncMock(return_value=scenario in {"card", "card_media"})
    if scenario == "stage_error":
        stage.side_effect = RuntimeError("stage unavailable")
    if scenario == "card_error":
        emit.side_effect = RuntimeError("sidecar unavailable")
    monkeypatch.setattr(hook_runtime, "admission_completion_mode", Mock(return_value=mode))
    monkeypatch.setattr(hook_runtime, "can_stage_exact_base_completion", Mock(return_value=scenario.startswith("stag")))
    monkeypatch.setattr(hook_runtime, "stage_message_completed_from_hermes_locals_async", stage)
    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
    delivery = "required" if scenario in {"queued_media", "card_media"} else "allowed"
    monkeypatch.setattr(hook_runtime, "build_event", Mock(return_value={"data": {"native_delivery": delivery}}))
    runner = SimpleNamespace(_reply_anchor_for_event=lambda event: "reply")
    native = []
    actual = asyncio.run(completion(renderer)(runner, SimpleNamespace(), SimpleNamespace(platform=platform), result, answer, native))
    if scenario in {"card", "queued_delivered"}:
        assert actual is None
    elif scenario in {"queued_media", "card_media"}:
        assert actual == hook_runtime.native_media_only_response(answer)
    else:
        assert actual == answer
    if scenario == "staged":
        stage.assert_awaited_once()
        emit.assert_not_called()
        assert native == [("tail", answer)]
    if scenario in {"stage_rejected", "stage_error", "native", "queued_failed", "queued_delivered", "queued_media"}:
        emit.assert_not_called()


@pytest.mark.parametrize("already_sent,failed", [(True, False), (False, False), (True, True)])
def test_patched_decomposed_handler_keeps_telegram_media(monkeypatch, already_sent, failed):
    from pathlib import Path

    original = (Path(__file__).parents[1] / "fixtures/hermes_decomposed/gateway/run_turn.py").read_text()
    patched = patcher.apply_gateway_fragment(original, "gateway/run_turn.py")
    namespace = {}
    exec(patched, namespace)
    runner = namespace["GatewayTurnMixin"]()
    runner._deliver_media_from_response = AsyncMock()
    admission = Mock(return_value="handoff")
    monkeypatch.setattr(hook_runtime, "admission_completion_mode", admission)
    source = SimpleNamespace(platform=Platform.TELEGRAM)
    answer = "answer\nMEDIA:/tmp/example.png"
    actual = asyncio.run(runner._hmwa_deliver_turn_response(
        SimpleNamespace(), source, None, "session", 1,
        {"already_sent": already_sent, "failed": failed}, [], answer, "", False,
    ))
    if already_sent and not failed:
        assert actual is None
        runner._deliver_media_from_response.assert_awaited_once_with(answer, SimpleNamespace(), None)
    else:
        assert actual == answer
        runner._deliver_media_from_response.assert_not_called()
    admission.assert_not_called()
    assert patcher.remove_patch(patched) == original
    assert patcher.apply_gateway_fragment(patched, "gateway/run_turn.py") == patched


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("indent", ["        ", "\t\t"])
@pytest.mark.parametrize("variant", ["plain", "anchor", "decomposed"])
def test_old_completion_blocks_remain_exactly_removable(newline, indent, variant):
    if variant == "plain":
        old = patcher._render_unscoped_complete_hook_block(indent, newline)
    else:
        old = patcher._render_complete_hook_block_with_reply_anchor(indent, newline, _scoped=False)
        if variant == "decomposed":
            old = [line.replace('"duration": _response_time,', '"duration": agent_result.get("_hfc_turn_seconds"),') for line in old]
    content = "".join(old)
    assert patcher._find_owned_complete_block(content) == (0, len(old) - 1)
    assert patcher.remove_patch(content) == ""
    with pytest.raises(ValueError, match="corrupt completion"):
        patcher.remove_patch(content.replace("return response", "return 'drift'", 1))
    for renderer in RENDERERS:
        block = "".join(renderer(indent, newline))
        # Both newline styles and tab-indented Hermes sources remain valid.
        compile("async def run():" + newline + indent[:len(indent)//2] + "if True:" + newline + block, "<hook>", "exec")


def test_upgrade_old_decomposed_completion_is_idempotent():
    from pathlib import Path

    original = (Path(__file__).parents[1] / "fixtures/hermes_decomposed/gateway/run_turn.py").read_text()
    current = patcher.apply_gateway_fragment(original, "gateway/run_turn.py")
    old = "".join(patcher._render_complete_hook_block_with_reply_anchor("        ", "\n", _scoped=False))
    old = old.replace('"duration": _response_time,', '"duration": agent_result.get("_hfc_turn_seconds"),')
    installed = current.replace("".join(patcher._render_decomposed_complete_hook_block("        ", "\n")), old)
    assert installed != current
    assert patcher.remove_patch(installed) == original
    assert patcher.apply_gateway_fragment(installed, "gateway/run_turn.py") == current


@pytest.mark.parametrize("renderer,expected", [
    (patcher._render_unscoped_complete_hook_block, "9b716be083416ad0e5045ad9cdea55c4596d27ac99fb2f8fac524da1e23eb3ad"),
    (lambda indent, newline: patcher._render_complete_hook_block_with_reply_anchor(indent, newline, _scoped=False), "23de47a4df32489839d883158da38bf209f138d30cd40672f166113ba61a95b1"),
    (patcher._render_v400_complete_hook_block, "def95b66d3655db71c1b9300222432b3f1627e009f2c1b85fb43d10066b651f4"),
])
def test_historical_completion_bytes_are_frozen(renderer, expected):
    from hashlib import sha256

    # Captured from main fd3881205d10a3c898d0724aee8b482d1a0e0c71.
    assert sha256("".join(renderer("    ", "\n")).encode()).hexdigest() == expected
