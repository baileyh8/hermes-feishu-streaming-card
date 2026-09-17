"""The queued-completion hook must pass the turn result through, so the sidecar can tell a failed
turn from a successful one.

Without it the emitted `message.completed` carries no `turn_outcome`, the sidecar takes its success
branch, and the answer that already streamed to the user is archived into the reasoning panel with
the raw provider error left as the card body — while the card claims the run succeeded. That is the
reported bug: a mid-turn provider failure replaced what the user was reading.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from hermes_feishu_card import hook_runtime, session as session_mod
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.install import patcher

CHAT_ID = "oc_topic"
CONV_ID = "omt_topic"
MSG_ID = "om_old"


def _run_block(monkeypatch, block: str, *, first_response: str, result: dict):
    """Execute the generated block inside a host shaped like the upstream call site.

    The emit override goes through monkeypatch, not a bare assignment: a module attribute left
    patched here would be inherited by every later test file in the same pytest process, and
    their own overrides would then never run.
    """
    seen = []

    async def emit(local_vars, *, event_name):
        seen.append(hook_runtime.build_event(event_name, local_vars))
        return True

    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
    source = SimpleNamespace(
        platform="feishu", chat_id=CHAT_ID, thread_id=CONV_ID, _hfc_turn_id=MSG_ID
    )
    context = SimpleNamespace(source=source, event_message_id=MSG_ID)
    namespace = {}
    exec(
        "async def run(turn_ctx):\n"
        f"    first_response = {first_response!r}\n"
        "    _already_streamed = False\n"
        f"    result = {result!r}\n"
        + block
        + "    return _already_streamed\n",
        namespace,
    )
    asyncio.run(namespace["run"](context))
    assert seen, "the block did not emit a completion event"
    return seen[0]


def _drive(payload: dict, *, event_name: str = "message.completed"):
    """Feed a streamed answer, a tool, then the terminal event, into a real session."""
    sess = session_mod.CardSession(
        conversation_id=CONV_ID, message_id=MSG_ID, chat_id=CHAT_ID
    )
    seq = {"n": 0}

    def ev(name: str, data: dict) -> SidecarEvent:
        seq["n"] += 1
        return SidecarEvent(
            schema_version="1", event=name, conversation_id=CONV_ID, message_id=MSG_ID,
            chat_id=CHAT_ID, platform="feishu", sequence=seq["n"], created_at=0.0, data=data,
        )

    sess.apply(ev("answer.delta", {"text": "已流出的正文。"}))
    sess.apply(ev("tool.updated", {"tool_id": "t1", "name": "terminal",
                                   "status": "completed", "elapsed": 0.25}))
    streamed = sess.answer_text
    sess.apply(ev(event_name, payload))
    return sess, streamed


def test_queued_completion_reports_the_failed_outcome(monkeypatch):
    """A failed turn must be announced as failed, not inferred from the presence of text."""
    block = "".join(patcher._render_queued_complete_hook_block("    ", "\n"))
    event = _run_block(
        monkeypatch, block,
        first_response="HTTP 403: 预扣费额度失败",
        result={"failed": True, "final_response": "HTTP 403: 预扣费额度失败",
                "duration": 12.5, "model": "deepseek-flash",
                "input_tokens": 11, "output_tokens": 22,
                "last_prompt_tokens": 33, "context_length": 131072},
    )
    assert event["data"]["turn_outcome"] == "failed"


def test_queued_completion_keeps_the_answer_the_user_was_reading(monkeypatch):
    """The failure must be appended to the streamed answer, and the answer must not be archived."""
    block = "".join(patcher._render_queued_complete_hook_block("    ", "\n"))
    event = _run_block(
        monkeypatch, block,
        first_response="HTTP 403: 预扣费额度失败",
        result={"failed": True, "final_response": "HTTP 403: 预扣费额度失败",
                "duration": 12.5, "model": "deepseek-flash",
                "input_tokens": 0, "output_tokens": 0,
                "last_prompt_tokens": 0, "context_length": 0},
    )
    sess, streamed = _drive(event["data"])
    assert streamed.strip() in sess.answer_text
    assert sess.status == "failed"
    assert not any(e.kind == "reasoning" for e in sess.timeline._entries)


def test_queued_completion_leaves_a_successful_turn_alone(monkeypatch):
    """Guard against over-reaching: a successful turn gains no outcome and keeps its answer."""
    block = "".join(patcher._render_queued_complete_hook_block("    ", "\n"))
    event = _run_block(
        monkeypatch, block,
        first_response="正常回答",
        result={"duration": 1.0, "model": "deepseek-flash", "input_tokens": 1,
                "output_tokens": 2, "last_prompt_tokens": 3, "context_length": 9},
    )
    assert event["data"].get("turn_outcome") is None
    sess, _ = _drive(event["data"])
    assert sess.status == "completed"
    assert sess.answer_text.strip() == "正常回答"


def test_queued_completion_passes_the_result_it_was_given(monkeypatch):
    """The block must forward the real result object, not a synthesised one."""
    block = "".join(patcher._render_queued_complete_hook_block("    ", "\n"))
    captured = {}

    async def emit(local_vars, *, event_name):
        captured["agent_result"] = local_vars.get("agent_result")
        return True

    monkeypatch.setattr(hook_runtime, "emit_from_hermes_locals_async", emit)
    source = SimpleNamespace(
        platform="feishu", chat_id=CHAT_ID, thread_id=CONV_ID, _hfc_turn_id=MSG_ID
    )
    context = SimpleNamespace(source=source, event_message_id=MSG_ID)
    namespace = {}
    exec(
        "async def run(turn_ctx):\n"
        "    first_response = 'x'\n"
        "    _already_streamed = False\n"
        "    result = {'failed': True, 'marker': 'unique'}\n"
        + block
        + "    return _already_streamed\n",
        namespace,
    )
    asyncio.run(namespace["run"](context))
    assert captured.get("agent_result", {}).get("marker") == "unique"
