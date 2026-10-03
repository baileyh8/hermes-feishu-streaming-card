import copy
import hashlib
import json

import pytest

from hermes_feishu_card.display_segments import begin_continuation, display_view
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.render import render_card, render_card_result
from hermes_feishu_card.session import CardSession, InteractionState
from hermes_feishu_card.session_store import SessionStore


def event(name, sequence, data):
    return SidecarEvent.from_dict(dict(schema_version="1", event=name, sequence=sequence,
        platform="feishu", conversation_id="conversation", message_id="turn", chat_id="chat",
        created_at=1700000000 + sequence, data=data))


def failed_thinking(*, outcome=None, thinking="THINKING_SENTINEL"):
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("thinking.delta", 0, {"text": thinking}))
    data = {"error": "任务已中断"} if outcome is None else {"turn_outcome": outcome}
    assert session.apply(event("message.failed" if outcome is None else "message.completed", 1, data))
    return session


def main_text(card):
    return "".join(item.get("content", "") for item in card["body"]["elements"]
        if str(item.get("element_id", "")).startswith("main_content"))


def assert_generic_interrupt_uses_stopped_header(card):
    assert main_text(card) == ""
    assert card["header"]["title"] == {"tag": "plain_text", "content": "Hermes Agent · 已停止"}
    assert card["header"]["template"] == "red"


@pytest.mark.parametrize("outcome", [None, "failed", "interrupted", "incomplete"])
def test_task_promoted_thinking_moves_to_process_with_terminal_notice_first(outcome):
    session = failed_thinking(outcome=outcome)
    canonical = session.answer_text
    before = copy.deepcopy(session.__dict__)
    result = render_card_result(session, presentation="task", stream_thinking_to_body=False)
    assert "THINKING_SENTINEL" not in main_text(result.card)
    assert main_text(result.card) == result.primary_text
    if outcome is None:
        assert session.terminal_reasoning_notice == "任务已中断"
        assert_generic_interrupt_uses_stopped_header(result.card)
    else:
        notices = {
            "failed": "本轮执行失败，任务完成情况请以实际结果为准。",
            "interrupted": "本轮已中断，任务尚未确认完成。",
            "incomplete": "本轮已结束，但 Hermes 未报告执行完成。",
        }
        assert main_text(result.card) == notices[outcome]
    panel = next(item for item in result.card["body"]["elements"] if item.get("tag") == "collapsible_panel")
    assert "THINKING_SENTINEL" in str(panel) and "running" not in str(panel)
    assert panel["expanded"] is False
    assert session.answer_text == canonical
    assert session.thinking_text == before["thinking_text"]
    assert session.timeline.snapshot() == before["timeline"].snapshot()
    assert "THINKING_SENTINEL" in main_text(render_card(session, presentation="classic"))


@pytest.mark.parametrize("answer", ["REAL_ANSWER_SENTINEL", "THINKING_SENTINEL"])
def test_task_never_guesses_a_real_answer_is_promoted_thinking(answer):
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("thinking.delta", 0, {"text": "THINKING_SENTINEL"}))
    assert session.apply(event("answer.delta", 1, {"text": answer}))
    assert session.apply(event("message.failed", 2, {"error": "任务已中断"}))
    assert main_text(render_card(session, presentation="task")) == session.answer_text


def test_unsuccessful_completion_with_explicit_answer_keeps_that_answer():
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("thinking.delta", 0, {"text": "THINKING_SENTINEL"}))
    assert session.apply(event("message.completed", 1, {
        "answer": "EXPLICIT_FINAL_SENTINEL", "turn_outcome": "interrupted"}))
    assert not session.terminal_reasoning_notice
    assert main_text(render_card(session, presentation="task")) == session.answer_text
    assert "EXPLICIT_FINAL_SENTINEL" in session.answer_text


def test_long_promoted_thinking_does_not_consume_task_primary_payload_budget():
    session = failed_thinking(thinking="THINKING_SENTINEL " * 2400)
    canonical = session.answer_text
    result = render_card_result(session, presentation="task", max_reasoning_chars=100)
    assert result.disposition == "card" and result.inspection.safe
    assert result.primary_text == ""
    assert_generic_interrupt_uses_stopped_header(result.card)
    panel = next(item for item in result.card["body"]["elements"] if item.get("tag") == "collapsible_panel")
    assert "THINKING_SENTINEL" in str(panel)
    assert session.answer_text == canonical
    assert session.thinking_text == "THINKING_SENTINEL " * 2400
    assert render_card_result(session, presentation="classic").disposition == "native"


def test_task_terminal_honors_explicit_hidden_reasoning_without_erasing_it():
    session = failed_thinking()
    canonical = session.answer_text
    card = render_card(session, presentation="task", show_reasoning=False)
    assert_generic_interrupt_uses_stopped_header(card)
    assert "THINKING_SENTINEL" not in str(card)
    assert session.thinking_text == "THINKING_SENTINEL"
    assert session.answer_text == canonical


def test_task_terminal_source_survives_checkpoint_and_old_records_remain_readable(tmp_path):
    store = SessionStore(tmp_path)
    session = failed_thinking()
    store.save("turn", session, "om_fixture", None, "", {}, "fixture_client")
    restored = store.load()[0]["session"]
    card = render_card(restored, presentation="task")
    assert restored.terminal_reasoning_notice == "任务已中断"
    assert_generic_interrupt_uses_stopped_header(card)
    assert "THINKING_SENTINEL" in str(card)
    assert restored.thinking_text == session.thinking_text
    assert restored.answer_text == session.answer_text
    path = next(store.root.glob("*.json"))
    payload = json.loads(path.read_text())
    payload["record"]["session"].pop("terminal_reasoning_notice", None)
    payload["digest"] = hashlib.sha256(json.dumps(payload["record"], ensure_ascii=False,
        allow_nan=False, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(payload))
    older = store.load()[0]["session"]
    assert older.answer_text == session.answer_text
    assert main_text(render_card(older, presentation="task")) == session.answer_text


def test_checkpoint_omits_unused_terminal_source_and_rejects_wrong_type(tmp_path):
    store = SessionStore(tmp_path)
    session = CardSession("conversation", "turn", "chat", answer_text="REAL_ANSWER")
    store.save("turn", session, "om_fixture", None, "", {}, "fixture_client")
    path = next(store.root.glob("*.json"))
    payload = json.loads(path.read_text())
    assert "terminal_reasoning_notice" not in payload["record"]["session"]
    payload["record"]["session"]["terminal_reasoning_notice"] = {"text": "fake"}
    payload["digest"] = hashlib.sha256(json.dumps(payload["record"], ensure_ascii=False,
        allow_nan=False, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(payload))
    assert store.load() == []


def test_continuation_thinking_failure_does_not_hide_prior_canonical_answer(tmp_path):
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("answer.delta", 0, {"text": "PRIOR_REAL_ANSWER"}))
    session.active_interaction = InteractionState("clarify", "clarify", "Question", status="completed",
                                                feishu_message_id="om_choice")
    begin_continuation(session)
    session.display_segment["active"] = True
    session.display_segment["pending"] = False
    assert session.apply(event("thinking.delta", 1, {"text": "CONTINUATION_THINKING"}))
    assert session.apply(event("message.failed", 2, {"error": "任务已中断"}))
    view = display_view(session)
    card = render_card(view, presentation="task")
    assert view.terminal_reasoning_notice == "任务已中断"
    assert_generic_interrupt_uses_stopped_header(card)
    assert "CONTINUATION_THINKING" in str(card)
    assert "PRIOR_REAL_ANSWER" in main_text(render_card(session, presentation="task"))
    store = SessionStore(tmp_path)
    store.save("turn", session, "om_fixture", None, "", {}, "fixture_client")
    restored = store.load()[0]["session"]
    restored_card = render_card(display_view(restored), presentation="task")
    assert_generic_interrupt_uses_stopped_header(restored_card)
    assert "CONTINUATION_THINKING" in str(restored_card)
    assert restored.answer_text == session.answer_text
    # A failed continuation delivery still explains where the retained content
    # lives; a display-only projection must not erase that fallback warning.
    session.display_segment["failed"] = True
    fallback = main_text(render_card(display_view(session), presentation="task"))
    assert "续答卡未能确认送达" in fallback and "PRIOR_REAL_ANSWER" in fallback


def test_full_result_continuation_keeps_the_thinking_its_terminal_promoted():
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("thinking.delta", 0, {"text": "PREVIOUS_SEGMENT_THINKING"}))
    session.active_interaction = InteractionState("clarify", "clarify", "Question", status="completed",
                                                feishu_message_id="om_choice")
    begin_continuation(session)
    assert session.apply(event("message.completed", 1, {"turn_outcome": "interrupted"}))
    result = render_card_result(display_view(session), presentation="task")
    assert "PREVIOUS_SEGMENT_THINKING" not in result.primary_text
    panel = next(item for item in result.card["body"]["elements"] if item.get("tag") == "collapsible_panel")
    assert "PREVIOUS_SEGMENT_THINKING" in str(panel)
