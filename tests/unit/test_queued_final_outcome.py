"""A queued follow-up that failed must still produce a card that says where it stopped.

The queued follow-up used to end with `message.failed`, and that branch of the completion envelope
only carries `error`. The card therefore read `已停止 · 0s · Unknown · ↑0 · ↓0` — no duration, no
model, no tokens — which is the context-free ⛔ card reported from real usage.

The fix has two halves, and this file pins both: the terminal event uses the completed envelope
(the only branch that reads duration/model/tokens/context), while the failure still reaches the
card because the turn result travels as `agent_result` and the sidecar derives `turn_outcome`
from it. Nothing here infers success from the presence of text.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from hermes_feishu_card import hook_runtime, render
from hermes_feishu_card import session as session_mod
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.install import patcher

RAW_ERROR = "HTTP 403: 预扣费额度失败 (request id: 20260917021219734208412JVeR1EFwnqRrqOif)"
CHAT_ID = "oc_topic"
CONV_ID = "omt_topic"
QUEUED_MESSAGE_ID = "om_queued_user_msg"

BASE_RESULT = {
    "final_response": RAW_ERROR,
    "failed": True,
    "error": RAW_ERROR,
    "input_tokens": 0,
    "output_tokens": 44,
    "last_prompt_tokens": 0,
    "context_length": 128000,
    "model": "deepseek-flash",
    "_hfc_turn_seconds": 2173.4,
}


def _emit_payload(result: dict) -> dict:
    """Run the real generated block against a host shaped like the upstream call site."""
    block = "".join(patcher._render_queued_final_hook_block("    ", "\n"))
    seen: list[dict] = []

    async def emit(local_vars, *, event_name):
        seen.append(hook_runtime.build_event(event_name, local_vars))
        return True

    source = SimpleNamespace(
        platform="feishu", chat_id=CHAT_ID, thread_id=CONV_ID, user_id="ou_x", user_name="mac"
    )
    pending_event = SimpleNamespace(message_id=QUEUED_MESSAGE_ID, reply_to_message_id="")
    namespace = {
        "PENDING_EVENT": pending_event,
        "FOLLOWUP_RESULT": result,
        "SOURCE": source,
    }
    original = hook_runtime.emit_from_hermes_locals_async
    hook_runtime.emit_from_hermes_locals_async = emit
    try:
        exec(  # noqa: S102
            compile(
                "async def run():\n"
                "    pending_event = PENDING_EVENT\n"
                "    followup_result = dict(FOLLOWUP_RESULT)\n"
                "    next_message_id = 'om_internal'\n"
                "    next_source = SOURCE\n"
                + block
                + "    return followup_result\n",
                "<host>",
                "exec",
            ),
            namespace,
        )
        asyncio.run(namespace["run"]())
    finally:
        hook_runtime.emit_from_hermes_locals_async = original
    assert seen, "the block emitted no terminal event"
    return seen[0]


def _render(payload: dict) -> tuple[str, str, str]:
    """Apply the payload to a real session and render, returning (header, body, footer)."""
    session = session_mod.CardSession(
        conversation_id=CONV_ID, message_id=payload["message_id"], chat_id=CHAT_ID
    )
    session.apply(
        SidecarEvent.from_dict(
            {
                "schema_version": "1",
                "event": payload["event"],
                "conversation_id": CONV_ID,
                "message_id": payload["message_id"],
                "chat_id": CHAT_ID,
                "platform": "feishu",
                "sequence": 1,
                "created_at": 1.0,
                "thread_id": CONV_ID,
                "data": payload["data"],
            }
        )
    )
    card = render.render_card(session, title="Hermes Agent")
    elements = card["body"]["elements"]

    def element(element_id: str) -> str:
        found = next((e for e in elements if e.get("element_id") == element_id), None)
        return (found or {}).get("content", "") or ""

    header = card.get("header", {}).get("title", {}).get("content", "")
    return header, element("main_content"), element("footer")


def test_a_failed_followup_reports_where_it_stopped():
    """The card must carry the duration, model and tokens — the reported card carried none."""
    payload = _emit_payload(dict(BASE_RESULT))
    data = payload["data"]
    assert data["duration"] == 2173.4
    assert data["model"] == "deepseek-flash"
    assert data["tokens"]["output_tokens"] == 44
    assert data["context"]["max_tokens"] == 128000

    header, _body, footer = _render(payload)
    assert "36m13s" in header
    assert "36m13s" in footer
    assert "deepseek-flash" in footer
    assert "Unknown" not in footer
    assert "0s" not in footer


def test_a_failed_followup_is_still_a_failure():
    """Passing metrics must not turn the turn into a success."""
    payload = _emit_payload(dict(BASE_RESULT))
    assert payload["event"] == "message.completed"
    assert payload["data"]["turn_outcome"] == "failed"

    _header, body, footer = _render(payload)
    assert "已停止" in footer
    assert "已完成" not in footer
    assert RAW_ERROR[:30] in body


def test_the_completed_envelope_is_used_for_every_queued_followup():
    """Both outcomes take the same branch; only the derived outcome differs."""
    failed = _emit_payload(dict(BASE_RESULT))
    succeeded = _emit_payload({**BASE_RESULT, "failed": False, "final_response": "正常完成"})
    assert failed["event"] == "message.completed"
    assert succeeded["event"] == "message.completed"


def test_a_successful_followup_keeps_no_outcome_and_a_completed_card():
    payload = _emit_payload({**BASE_RESULT, "failed": False, "final_response": "正常完成"})
    assert payload["data"].get("turn_outcome") is None

    _header, body, footer = _render(payload)
    assert "已完成" in footer
    assert "已停止" not in footer
    assert body.strip() == "正常完成"


def test_a_missing_answer_falls_back_to_the_error_instead_of_an_empty_card():
    """Without a response text the error must still reach the body, not vanish."""
    payload = _emit_payload({**BASE_RESULT, "final_response": ""})
    _header, body, _footer = _render(payload)
    assert RAW_ERROR[:30] in body
    assert body.strip()
