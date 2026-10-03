import copy
import hashlib
import json

import pytest

from hermes_feishu_card.display_segments import begin_continuation, display_view
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.render import render_card, render_card_result
from hermes_feishu_card.session import CardSession, InteractionOption, InteractionState
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


@pytest.mark.parametrize("approval_status", ["pending", "paused"])
@pytest.mark.parametrize("thinking", ["", "THINKING_SENTINEL"])
@pytest.mark.parametrize("show_reasoning", [False, True])
def test_task_approval_stop_merges_only_event_notice(approval_status, thinking, show_reasoning):
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("thinking.delta", 0, {"text": thinking}))
    assert session.apply(event("tool.updated", 1, {
        "tool_id": "fixture-tool", "name": "terminal", "status": "running",
        "detail": "TOOL_SCOPE_SENTINEL",
    }))
    session.active_interaction = InteractionState(
        "fixture-approval", "approval", "APPROVAL_PROMPT_SENTINEL",
        description="APPROVAL_SCOPE_SENTINEL", status=approval_status,
        options=[InteractionOption("允许一次", "once"), InteractionOption("拒绝", "deny")],
        pause_on_timeout=True, feishu_message_id="om_fixture_approval",
    )
    assert session.apply(event("message.failed", 2, {"error": "任务已中断"}))
    canonical = session.answer_text
    assert session.terminal_reasoning_notice == "任务已中断"
    result = render_card_result(session, presentation="task", show_reasoning=show_reasoning)
    assert result.disposition == "card" and result.inspection.safe
    assert result.primary_text == main_text(result.card) == ""
    assert_generic_interrupt_uses_stopped_header(result.card)
    assert any("已中断" in item.get("content", "") and "terminal" in item.get("content", "")
               for item in result.card["body"]["elements"]
               if item.get("element_id", "").startswith("tool_activity_"))
    assert session.active_interaction.status == "failed"
    assert session.active_interaction.prompt == "APPROVAL_PROMPT_SENTINEL"
    assert session.active_interaction.description == "APPROVAL_SCOPE_SENTINEL"
    assert [option.value for option in session.active_interaction.options] == ["once", "deny"]
    assert session.answer_text == canonical and session.thinking_text == thinking
    assert main_text(render_card(session, presentation="classic")) == canonical
    if thinking:
        assert (thinking in str(result.card)) is show_reasoning


@pytest.mark.parametrize("thinking", ["", "THINKING_SENTINEL"])
@pytest.mark.parametrize("answer_kind", ["delta", "terminal"])
def test_task_real_answer_equal_to_generic_notice_is_never_merged(thinking, answer_kind):
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("thinking.delta", 0, {"text": thinking}))
    if answer_kind == "delta":
        assert session.apply(event("answer.delta", 1, {"text": "任务已中断"}))
        assert session.apply(event("message.failed", 2, {"error": "任务已中断"}))
    else:
        assert session.apply(event("message.completed", 1, {
            "answer": "任务已中断", "turn_outcome": "interrupted"}))
    canonical = session.answer_text
    assert not session.terminal_reasoning_notice
    result = render_card_result(session, presentation="task")
    assert "任务已中断" in result.primary_text
    assert result.primary_text == main_text(result.card) == canonical
    assert main_text(render_card(session, presentation="classic")) == canonical
    assert session.answer_text == canonical


@pytest.mark.parametrize("thinking", ["", "THINKING_SENTINEL"])
@pytest.mark.parametrize("error", ["任务已中断：连接已断开", "读取文件失败：权限不足", ""])
def test_task_event_notice_keeps_specific_or_default_failure_reason(thinking, error):
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("thinking.delta", 0, {"text": thinking}))
    assert session.apply(event("message.failed", 1, {"error": error}))
    reason = error or "消息处理失败"
    canonical = session.answer_text
    assert session.terminal_reasoning_notice == reason
    result = render_card_result(session, presentation="task")
    assert result.primary_text == main_text(result.card) == reason
    assert main_text(render_card(session, presentation="classic")) == canonical
    assert session.answer_text == canonical


@pytest.mark.parametrize("outcome,notice", [
    ("failed", "本轮执行失败，任务完成情况请以实际结果为准。"),
    ("interrupted", "本轮已中断，任务尚未确认完成。"),
    ("incomplete", "本轮已结束，但 Hermes 未报告执行完成。"),
])
def test_task_empty_unsuccessful_completion_keeps_its_specific_notice(outcome, notice):
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("message.completed", 0, {"turn_outcome": outcome}))
    canonical = session.answer_text
    assert session.terminal_reasoning_notice == notice
    result = render_card_result(session, presentation="task")
    assert result.primary_text == main_text(result.card) == notice
    assert main_text(render_card(session, presentation="classic")) == canonical
    assert session.answer_text == canonical


def test_task_pure_notice_checkpoint_roundtrip_does_not_infer_old_record_source(tmp_path):
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("message.failed", 0, {"error": "任务已中断"}))
    store = SessionStore(tmp_path)
    store.save("turn", session, "om_fixture", None, "", {}, "fixture_client")
    restored = store.load()[0]["session"]
    assert restored.terminal_reasoning_notice == "任务已中断"
    assert restored.answer_text == session.answer_text == "任务已中断"
    assert not restored.thinking_text
    assert_generic_interrupt_uses_stopped_header(render_card(restored, presentation="task"))
    path = next(store.root.glob("*.json"))
    payload = json.loads(path.read_text())
    payload["record"]["session"].pop("terminal_reasoning_notice")
    payload["digest"] = hashlib.sha256(json.dumps(payload["record"], ensure_ascii=False,
        allow_nan=False, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(payload))
    older = store.load()[0]["session"]
    assert not older.terminal_reasoning_notice
    assert older.answer_text == session.answer_text
    assert main_text(render_card(older, presentation="task")) == older.answer_text


def test_task_empty_continuation_stop_preserves_prior_answer_and_failed_delivery(tmp_path):
    session = CardSession("conversation", "turn", "chat")
    assert session.apply(event("answer.delta", 0, {"text": "PRIOR_REAL_ANSWER"}))
    session.active_interaction = InteractionState(
        "fixture-clarify", "clarify", "Question", status="completed", feishu_message_id="om_choice")
    begin_continuation(session)
    session.display_segment["active"] = True
    session.display_segment["pending"] = False
    assert session.apply(event("message.failed", 1, {"error": "任务已中断"}))
    canonical = session.answer_text
    assert not session.terminal_reasoning_notice
    view = display_view(session)
    assert view.terminal_reasoning_notice == "任务已中断" and not view.thinking_text
    assert_generic_interrupt_uses_stopped_header(render_card(view, presentation="task"))
    assert "PRIOR_REAL_ANSWER" in main_text(render_card(session, presentation="task"))
    store = SessionStore(tmp_path)
    store.save("turn", session, "om_fixture", None, "", {}, "fixture_client")
    restored = store.load()[0]["session"]
    assert_generic_interrupt_uses_stopped_header(render_card(display_view(restored), presentation="task"))
    assert restored.answer_text == canonical
    session.display_segment["failed"] = True
    fallback = render_card_result(display_view(session), presentation="task")
    assert "续答卡未能确认送达" in fallback.primary_text
    assert "PRIOR_REAL_ANSWER" in fallback.primary_text
    assert session.answer_text == canonical


@pytest.mark.parametrize("thinking", ["", "THINKING_SENTINEL"])
def test_task_reconnecting_header_does_not_hide_terminal_notice(thinking):
    session = failed_thinking(thinking=thinking)
    session.presentation_state = "reconnecting"
    canonical = session.answer_text
    assert session.terminal_reasoning_notice == "任务已中断"
    result = render_card_result(session, presentation="task")
    assert result.card["header"]["title"]["content"].endswith("等待状态同步")
    assert result.primary_text == main_text(result.card) == "任务已中断"
    assert session.answer_text == canonical
