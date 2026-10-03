"""Task clarify rows name the question while the complete call stays accessible."""
import copy
import json

import pytest

from hermes_feishu_card.render import render_card
from hermes_feishu_card.card_timeline import CardTimeline
from hermes_feishu_card.session import CardSession, ToolState


def fixture(*, questions=None, name="clarify", status="completed"):
    arguments = {"questions": questions or [{"question": "哪些内容需要保留？", "choices": ["完整答案", "工具参数"],
                                           "multi_select": True}]}
    detail = "参数: " + json.dumps(arguments, ensure_ascii=False) + "\n耗时: 12s"
    session = CardSession("conversation", "source", "chat", status=status, answer_text="FINAL_ANSWER")
    session.tools["tool"] = ToolState("tool", name, status, detail, ordinal=1, duration_ms=12000)
    session.timeline.record_tool("tool", name, status, detail)
    return session, detail


def activity(card):
    return next(e["content"] for e in card["body"]["elements"]
                if e.get("element_id", "").startswith("tool_activity_"))


@pytest.mark.parametrize("status", ["completed", "failed", "running"])
def test_task_clarify_summarizes_known_question_without_json_or_state_mutation(status):
    session, detail = fixture(status=status)
    before = copy.deepcopy((session.tools, session.timeline, session.answer_text))
    card = render_card(session, presentation="task")
    text = activity(card)
    assert "澄清问题：哪些内容需要保留？" in text
    assert '"questions"' not in text and "完整答案" not in text and "multi_select" not in text
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "auxiliary_timeline")
    assert '"questions"' in str(panel) and "完整答案" in str(panel) and "工具参数" in str(panel)
    assert (session.tools, session.timeline, session.answer_text) == before
    assert session.tools["tool"].detail == detail


def test_classic_clarify_keeps_existing_json_rendering():
    session, _ = fixture()
    assert '"questions"' in activity(render_card(session, presentation="classic"))


@pytest.mark.parametrize("missing", ["disabled", "absent", "truncated", "different_call"])
def test_task_keeps_argument_fallback_without_complete_current_process_detail(missing):
    session, _ = fixture()
    options = {}
    if missing == "disabled":
        options["show_reasoning"] = False
    elif missing == "absent":
        session.timeline = CardTimeline()
    elif missing == "truncated":
        options["max_tool_result_chars"] = 45
    else:
        session.timeline = CardTimeline()
        session.timeline.record_tool("tool", "clarify", "completed", '参数: {"question":"other call"}')
    text = activity(render_card(session, presentation="task", **options))
    assert '"questions"' in text
    assert "澄清问题：" not in text


@pytest.mark.parametrize("name,arguments", [
    ("custom_clarify", '{"questions":[{"question":"Known?"}]}'),
    ("clarify", '{"questions":['),
    ("clarify", '{"questions":"unknown"}'),
    ("clarify", '{"questions":[{"question":""}]}'),
    ("clarify", '{"questions":[{"question":"Known?"},null]}'),
])
def test_unknown_or_invalid_question_shape_uses_existing_fallback(name, arguments):
    session, _ = fixture(name=name)
    detail = "参数: " + arguments
    session.tools["tool"].detail = detail
    session.timeline = CardTimeline()
    session.timeline.record_tool("tool", name, "completed", detail)
    text = activity(render_card(session, presentation="task"))
    assert "澄清问题：" not in text and arguments in text


def test_task_batch_question_summary_is_bounded_and_full_questions_remain_in_process():
    question = "第一问的完整内容 " * 100
    session, _ = fixture(questions=[{"question": question}, {"question": "第二问保持完整"}])
    card = render_card(session, presentation="task", max_tool_result_chars=5000)
    text = activity(card)
    assert "澄清 2 个问题：" in text and len(text.splitlines()[-1]) <= 180
    assert text.endswith("…")
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "auxiliary_timeline")
    assert question in str(panel) and "第二问保持完整" in str(panel)
