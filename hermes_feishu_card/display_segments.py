"""Bounded presentation state. Execution and interaction identities stay in CardSession.

The canonical answer, tools, timeline and attachments are never reset here. A
selection starts a lazy display boundary; only real subsequent output needs a
new schema-2 message. Inspired by the continuation proposal in PR #331.
"""
from __future__ import annotations

import copy

from .card_timeline import TERMINAL_TOOL_STATUSES
from .text import normalize_stream_text

MAX_DISPLAY_SEGMENTS = 32


def begin_continuation(session) -> None:
    interaction = session.active_interaction
    if interaction is None or not interaction.feishu_message_id:
        return
    previous = session.display_segment
    generation = previous.get("generation", 0)
    session.display_segment = {
        "generation": generation,
        "interaction_id": interaction.interaction_id,
        "boundary_sequence": session.last_sequence,
        "pending": generation < MAX_DISPLAY_SEGMENTS,
        "active": False,
        "failed": generation >= MAX_DISPLAY_SEGMENTS,
        "answer": "",
        "thinking": "",
        "tool_floor": session.tool_count,
        "running_tools": [key for key, tool in session.tools.items()
                          if tool.status.lower() not in TERMINAL_TOOL_STATUSES],
        "thread_id": interaction.thread_id,
        "reply_to_message_id": interaction.reply_to_message_id,
        "reply_in_thread": interaction.reply_in_thread,
        "full_result": False,
        "has_output": False,
    }


def append_answer(session, delta: str) -> None:
    if session.display_segment:
        session.display_segment["answer"] += delta
        session.display_segment["has_output"] = True


def update_thinking(session, text: str, mode: str) -> None:
    state = session.display_segment
    if not state:
        return
    state["has_output"] = state["has_output"] or bool(text.strip())
    if mode == "replace":
        state["thinking"] = text
    elif mode == "append_block":
        state["thinking"] = (state["thinking"].rstrip() + "\n\n" + text).lstrip()
    else:
        state["thinking"] += text


def record_terminal(session, event) -> None:
    state = session.display_segment
    if not state:
        return
    if event.event == "message.completed" and str(event.data.get("answer") or "").strip():
        # A terminal snapshot is an authoritative full result, not a delta.
        # Keep it intact rather than guessing/removing an old textual prefix.
        state["answer"] = (session.answer_text if session.status == "failed" else
                           normalize_stream_text(str(event.data["answer"])))
        state["full_result"] = True
        state["has_output"] = True
    elif event.event == "message.failed":
        error = event.data.get("error")
        error = error if isinstance(error, str) and error.strip() else "消息处理失败"
        partial = state["answer"].rstrip() or state["thinking"].rstrip()
        state["answer"] = partial + "\n\n> " + error if partial else error
        state["has_output"] = True
    elif session.status == "failed":
        state["answer"] = session.answer_text
        state["full_result"] = True
        state["has_output"] = True


def needs_continuation(session, event) -> bool:
    state = session.display_segment
    interaction = session.active_interaction
    if not state.get("pending") or (interaction is not None and interaction.status in {"pending", "paused"}):
        return False
    if event.event in {"answer.delta", "thinking.delta"}:
        return bool(state["answer"].strip() or state["thinking"].strip())
    if event.event in {"tool.updated", "subagent.updated"}:
        return bool(event.data.get("tool_id") or event.data.get("child_id"))
    return event.event in {"message.completed", "message.failed"} and bool(state["answer"].strip())


def display_view(session):
    state = session.display_segment
    if not state:
        return session
    interaction = session.active_interaction
    if interaction is not None and interaction.status in {"pending", "paused"}:
        return session
    if state.get("failed"):
        view = copy.copy(session)
        view.answer_text = (
            "续答卡未能确认送达，后续内容继续保留在此卡。\n\n"
            + session.answer_text
        )
        return view
    if state.get("pending") and not state["has_output"]:
        return session
    view = copy.copy(session)
    view.answer_text = state["answer"]
    if state.get("full_result") and view.answer_text:
        view.answer_text = "**本轮完整结果**\n\n" + view.answer_text
    view.thinking_text = state["thinking"]
    view.active_interaction = None  # the completed receipt has its own message
    view.tools = {
        key: tool for key, tool in session.tools.items()
        if tool.ordinal > state["tool_floor"] or key in state["running_tools"]
    }
    if not view.tools:
        view.latest_tool_preview = ""
    return view


def valid_checkpoint_state(state) -> bool:
    if state == {}:
        return True
    if not isinstance(state, dict):
        return False
    required = {"generation", "interaction_id", "boundary_sequence", "pending", "active", "failed", "answer",
                "thinking", "tool_floor", "running_tools", "thread_id", "reply_to_message_id",
                "reply_in_thread", "full_result", "has_output"}
    return (
        set(state) == required
        and type(state["generation"]) is int and 0 <= state["generation"] <= MAX_DISPLAY_SEGMENTS
        and type(state["tool_floor"]) is int and state["tool_floor"] >= 0
        and type(state["boundary_sequence"]) is int and state["boundary_sequence"] >= 0
        and all(type(state[k]) is bool for k in ("pending", "active", "failed", "reply_in_thread", "full_result", "has_output"))
        and all(type(state[k]) is str for k in ("interaction_id", "answer", "thinking", "thread_id", "reply_to_message_id"))
        and isinstance(state["running_tools"], list)
        and all(type(k) is str for k in state["running_tools"])
    )
