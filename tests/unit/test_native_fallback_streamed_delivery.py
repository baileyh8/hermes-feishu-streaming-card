"""Native/handoff fallback must resume Hermes' real streamed-delivery method.

The bundled, hash-checked upstream source supplies the entire method (including
media/footer/failed-turn decisions); only HFC's generated completion block varies.
No installed Hermes import, Gateway, model, network or production state is used.
"""
import ast
import asyncio
from contextlib import suppress
from enum import Enum
import gzip
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from hermes_feishu_card import hook_runtime
from hermes_feishu_card.install import patcher


class Platform(Enum):
    FEISHU = "feishu"
    TELEGRAM = "telegram"


RENDERERS = [
    patcher._render_complete_hook_block,
    patcher._render_complete_hook_block_with_reply_anchor,
    patcher._render_decomposed_complete_hook_block,
]
TARGET = "gateway/run_turn_persistence.py"
ANSWER = "complete answer\nMEDIA:/example/report.png\nadditional guidance"


@pytest.fixture(scope="module")
def upstream_sources():
    manifest_path = Path(__file__).parents[1] / "fixtures/session_authority_0226.json"
    manifest = json.loads(manifest_path.read_text())
    archive = manifest_path.with_suffix(".json.gz").read_bytes()
    assert hashlib.sha256(archive).hexdigest() == manifest["archive_sha256"]
    sources = json.loads(gzip.decompress(archive))["files"]
    assert hashlib.sha256(sources[TARGET].encode()).hexdigest() == manifest["files"][TARGET]
    return sources


def rendered_method(sources, renderer, bot_reply_note=False, historical=False):
    original = sources[TARGET]
    patched = patcher._apply_complete_patch(
        original, completion_sources=sources, target=TARGET,
    )
    assert patched != original
    assert patcher.remove_patch(patched) == original
    assert patcher._apply_complete_patch(
        patched, completion_sources=sources, target=TARGET,
    ) == patched
    lines = patched.splitlines(keepends=True)
    owned = patcher._find_owned_complete_block(patched)
    assert owned is not None
    begin, end = owned
    block = renderer("        ", "\n", bot_reply_note=bot_reply_note)
    if historical:
        # Exact pre-fix scoped rendering is a negative control, not a copied
        # simulation of Hermes' native delivery tail.
        block = patcher._scope_complete_hook_to_feishu(
            patcher._render_unscoped_complete_hook_block(
                "        ", "\n", bot_reply_note=bot_reply_note,
            ),
            "        ", "\n",
        )
    rendered = "".join(lines[:begin] + block + lines[end + 1:])
    tree = ast.parse(rendered)
    method = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_hmwa_deliver_turn_response"
    )
    namespace = {
        "diagnostic_wake_muted": lambda event: False,
        "logger": logging.getLogger(__name__),
        "suppress": suppress,
        "_response_time": 1.0,
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), "<rendered-delivery>", "exec"), namespace)
    return namespace[method.name]


def ports(monkeypatch, mode="native", can_stage=True, staged=False, stage_error=False):
    admission = Mock(return_value=mode)
    can = Mock(return_value=can_stage)
    stage = AsyncMock(return_value=staged)
    if stage_error:
        stage.side_effect = RuntimeError("stage unavailable")
    build = Mock(return_value={"data": {"native_delivery": "allowed"}})
    emit = AsyncMock(return_value=True)
    native_suppression = Mock(return_value=True)
    media_only = Mock(wraps=hook_runtime.native_media_only_response)
    note = Mock()
    for name, port in {
        "admission_completion_mode": admission,
        "can_stage_exact_base_completion": can,
        "stage_message_completed_from_hermes_locals_async": stage,
        "build_event": build,
        "emit_from_hermes_locals_async": emit,
        "should_suppress_native_response": native_suppression,
        "native_media_only_response": media_only,
        "note_bot_reply_for_delivered_card": note,
    }.items():
        monkeypatch.setattr(hook_runtime, name, port)
    adapter = SimpleNamespace(send=AsyncMock())
    runner = SimpleNamespace(
        _delivery_adapter_for=Mock(return_value=adapter),
        _should_send_voice_reply=Mock(return_value=False),
        _send_voice_reply=AsyncMock(),
        _deliver_media_from_response=AsyncMock(),
        _event_thread_metadata=Mock(return_value={"thread_id": "thread"}),
        _reply_anchor_for_event=Mock(return_value="reply"),
    )
    return SimpleNamespace(
        admission=admission, can=can, stage=stage, build=build, emit=emit,
        suppress=native_suppression, media_only=media_only, note=note,
        adapter=adapter, runner=runner,
    )


def deliver(method, p, result, platform=Platform.FEISHU, answer=ANSWER, footer="runtime footer", silent=False):
    event = SimpleNamespace(message_id="inbound")
    source = SimpleNamespace(platform=platform, chat_id="chat")
    actual = asyncio.run(method(
        p.runner, event, source, SimpleNamespace(session_id="session"),
        "session-key", 1, result, [], answer, footer, silent,
    ))
    return actual, event


@pytest.mark.parametrize("renderer", RENDERERS)
@pytest.mark.parametrize("bot_reply_note", [False, True])
@pytest.mark.parametrize("fallback", ["native", "handoff_unavailable", "handoff_rejected", "handoff_error"])
@pytest.mark.parametrize("already_sent,failed", [(True, False), (False, False), (True, True)])
@pytest.mark.parametrize("media_already_delivered", [False, True])
def test_fallback_resumes_original_streaming_decisions(
    monkeypatch, upstream_sources, renderer, bot_reply_note, fallback,
    already_sent, failed, media_already_delivered,
):
    p = ports(
        monkeypatch, mode="native" if fallback == "native" else "handoff",
        can_stage=fallback != "handoff_unavailable", stage_error=fallback == "handoff_error",
    )
    result = {"already_sent": already_sent, "failed": failed,
              "media_already_delivered": media_already_delivered}
    original_result = dict(result)
    method = rendered_method(upstream_sources, renderer, bot_reply_note)
    actual, event = deliver(method, p, result)
    streamed = already_sent and not failed
    assert actual == (None if streamed else ANSWER)
    assert result == original_result
    if streamed:
        assert event._streamed_final_response == ANSWER
        p.adapter.send.assert_awaited_once_with("chat", "runtime footer", metadata={"thread_id": "thread"})
    else:
        assert not hasattr(event, "_streamed_final_response")
        p.adapter.send.assert_not_called()
    if streamed and not media_already_delivered:
        p.runner._deliver_media_from_response.assert_awaited_once_with(ANSWER, event, p.adapter)
    else:
        p.runner._deliver_media_from_response.assert_not_called()
    if fallback == "native":
        p.can.assert_not_called()
    if fallback in {"native", "handoff_unavailable"}:
        p.stage.assert_not_called()
    else:
        p.stage.assert_awaited_once()
    for unused in (p.build, p.emit, p.suppress, p.media_only, p.note):
        unused.assert_not_called()


@pytest.mark.parametrize("platform", ["telegram", Platform.TELEGRAM, "unknown", None])
@pytest.mark.parametrize("already_sent,failed", [(True, False), (False, False), (True, True)])
def test_non_feishu_keeps_original_media_and_footer(monkeypatch, upstream_sources, platform, already_sent, failed):
    p = ports(monkeypatch)
    actual, event = deliver(
        rendered_method(upstream_sources, RENDERERS[2]), p,
        {"already_sent": already_sent, "failed": failed}, platform=platform,
    )
    assert actual == (None if already_sent and not failed else ANSWER)
    if already_sent and not failed:
        p.runner._deliver_media_from_response.assert_awaited_once_with(ANSWER, event, p.adapter)
        p.adapter.send.assert_awaited_once()
    else:
        p.runner._deliver_media_from_response.assert_not_called()
        p.adapter.send.assert_not_called()
    p.admission.assert_not_called()
    p.stage.assert_not_called()
    p.emit.assert_not_called()
    p.runner._reply_anchor_for_event.assert_not_called()


@pytest.mark.parametrize("silent", [False, True])
@pytest.mark.parametrize("footer_fails", [False, True])
def test_footer_failure_and_silence_do_not_resend_body(monkeypatch, upstream_sources, silent, footer_fails):
    p = ports(monkeypatch)
    if footer_fails:
        p.adapter.send.side_effect = RuntimeError("footer unavailable")
    actual, event = deliver(rendered_method(upstream_sources, RENDERERS[2]), p, {"already_sent": True}, silent=silent)
    assert actual is None
    assert event._streamed_final_response == ("" if silent else ANSWER)
    if silent:
        p.adapter.send.assert_not_called()
        p.runner._deliver_media_from_response.assert_not_called()
    else:
        p.adapter.send.assert_awaited_once()
        p.runner._deliver_media_from_response.assert_awaited_once()


@pytest.mark.parametrize("scenario", ["staged", "card", "card_media", "card_rejected", "card_error"])
def test_accepted_hfc_paths_keep_card_and_attachment_semantics(monkeypatch, upstream_sources, scenario):
    p = ports(monkeypatch, mode="handoff" if scenario == "staged" else "legacy",
              can_stage=scenario == "staged", staged=scenario == "staged")
    if scenario == "card_media":
        p.build.return_value = {"data": {"native_delivery": "required", "attachments": ["file"]}}
        p.suppress.return_value = False
    if scenario == "card_rejected":
        p.emit.return_value = False
        p.suppress.return_value = False
    if scenario == "card_error":
        p.emit.side_effect = RuntimeError("card unavailable")
    actual, event = deliver(rendered_method(upstream_sources, RENDERERS[2], True), p, {})
    expected = (None if scenario == "card" else
                hook_runtime.native_media_only_response(ANSWER) if scenario == "card_media" else ANSWER)
    assert actual == expected
    assert not hasattr(event, "_streamed_final_response")
    p.runner._deliver_media_from_response.assert_not_called()
    p.adapter.send.assert_not_called()
    if scenario == "staged":
        p.stage.assert_awaited_once()
        p.emit.assert_not_called()
    else:
        p.emit.assert_awaited_once()
    if scenario in {"card", "card_media", "card_rejected"}:
        p.note.assert_called_once()


@pytest.mark.parametrize("queued", ["failed", "delivered", "media"])
def test_queued_receipts_keep_existing_ownership(monkeypatch, upstream_sources, queued):
    p = ports(monkeypatch)
    if queued == "media":
        p.build.return_value = {"data": {"native_delivery": "required"}}
    result = {"already_sent": True, "_hfc_queued_final_attempted": True,
              "_hfc_queued_final_delivered": queued != "failed"}
    actual, event = deliver(rendered_method(upstream_sources, RENDERERS[2], True), p, result)
    assert actual == (ANSWER if queued == "failed" else
                      hook_runtime.native_media_only_response(ANSWER) if queued == "media" else None)
    assert not hasattr(event, "_streamed_final_response")
    p.admission.assert_not_called()
    p.emit.assert_not_called()
    p.runner._deliver_media_from_response.assert_not_called()
    p.adapter.send.assert_not_called()
    if queued == "delivered":
        p.note.assert_called_once()


def test_historical_native_early_return_reproduces_duplicate(monkeypatch, upstream_sources):
    p = ports(monkeypatch)
    actual, event = deliver(
        rendered_method(upstream_sources, RENDERERS[0], historical=True),
        p, {"already_sent": True},
    )
    assert actual == ANSWER  # The adapter would send the streamed body again.
    assert not hasattr(event, "_streamed_final_response")
    p.runner._deliver_media_from_response.assert_not_called()
    p.adapter.send.assert_not_called()


@pytest.mark.parametrize("renderer", RENDERERS)
@pytest.mark.parametrize("bot_reply_note", [False, True])
@pytest.mark.parametrize("newline,indent", [("\n", "        "), ("\r\n", "\t\t")])
def test_previous_scoped_bytes_upgrade_exactly(renderer, bot_reply_note, newline, indent):
    if renderer is RENDERERS[0]:
        historical = patcher._render_unscoped_complete_hook_block(indent, newline, bot_reply_note=bot_reply_note)
    else:
        historical = patcher._render_complete_hook_block_with_reply_anchor(
            indent, newline, _scoped=False, bot_reply_note=bot_reply_note,
        )
        if renderer is RENDERERS[2]:
            historical = [line.replace('"duration": _response_time,', '"duration": agent_result.get("_hfc_turn_seconds"),') for line in historical]
    old = "".join(patcher._scope_complete_hook_to_feishu(historical, indent, newline))
    current = "".join(renderer(indent, newline, bot_reply_note=bot_reply_note))
    assert old != current
    assert patcher.remove_patch(old) == ""
    assert patcher.remove_patch(current) == ""
    with pytest.raises(ValueError, match="corrupt completion"):
        patcher.remove_patch(old.replace("return response", "return 'unowned'", 1))
    compile("async def run():" + newline + indent[:len(indent)//2] + "if True:" + newline + current, "<hook>", "exec")
