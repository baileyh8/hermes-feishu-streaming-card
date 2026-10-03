import copy
import json
import time

import pytest

from hermes_feishu_card.card_limits import inspect_card_limits
from hermes_feishu_card.presentation import task_presentation
from hermes_feishu_card.render import render_card, render_card_result
from hermes_feishu_card.session import CardSession, InteractionState, InteractionOption
from hermes_feishu_card.session import ToolState
from hermes_feishu_card.card_timeline import CardTimeline


def session(**values):
    value = CardSession(conversation_id="fixture-conversation", message_id="fixture-message", chat_id="fixture-chat")
    for key, item in values.items():
        setattr(value, key, item)
    return value


def snapshot(value):
    return copy.deepcopy({key: item for key, item in vars(value).items()
                          if key not in {"thinking_normalizer", "answer_normalizer"}})


def test_task_state_separates_silence_from_execution_failure():
    value = session(updated_at=100)
    result = task_presentation(value, now=225)
    assert result.label == "等待新进展"
    assert result.color == "grey"
    assert "2 分钟未收到新事件" in result.observation
    assert "尚待确认" in result.observation
    assert value.status == "thinking"
    assert task_presentation(value, now=110).observation == ""


@pytest.mark.parametrize("status,label", [("completed", "已完成"), ("failed", "已停止")])
def test_terminal_task_state_uses_event_state_not_answer_wording(status, label):
    value = session(status=status, answer_text="正在收集，到位后我会继续", updated_at=1)
    view = task_presentation(value, now=500)
    assert view.label == label
    assert view.observation == ""


def test_reconnecting_presentation_does_not_claim_resumed_execution():
    value = session(status="failed", presentation_state="reconnecting")
    view = task_presentation(value)
    assert view.label == "等待状态同步"
    assert "原授权不会恢复" in view.observation
    card = render_card(value, presentation="task")
    assert card["header"]["template"] == "default"
    assert card["header"]["title"]["content"].endswith("等待状态同步")
    assert not any(e.get("element_id") == "footer" for e in card["body"]["elements"])


def test_task_layout_preserves_complete_answer_and_does_not_mutate_session():
    value = session(status="completed", answer_text="结论\n\n" + "完整内容 " * 150, model="deepseek/model")
    before = snapshot(value)
    card = render_card(value, presentation="task")
    assert snapshot(value) == before
    assert card["header"]["title"]["content"] == "Hermes Agent · 已完成"
    assert "subtitle" not in card["header"]
    assert value.answer_text in "".join(item.get("content", "") for item in card["body"]["elements"])
    assert inspect_card_limits(card).safe
    assert "↑0" not in card["body"]["elements"][-1]["content"]
    assert "ctx 0/0" not in card["body"]["elements"][-1]["content"]


def test_task_layout_uses_explicit_text_sizes_and_keeps_classic_available():
    value = session(status="completed", answer_text="正文")
    classic = render_card(value)
    task = render_card(value, presentation="task", text_sizes={"body": "large"})
    body = next(item for item in task["body"]["elements"] if item.get("element_id") == "main_content")
    assert body["text_size"] == "large"
    assert classic["header"] != task["header"]
    assert render_card(value, presentation="classic") == classic


def test_task_layout_empty_loading_and_long_wait_have_useful_content():
    value = session(updated_at=time.time() - 130)
    card = render_card(value, presentation="task", stream_thinking_to_body=False)
    elements = card["body"]["elements"]
    assert elements[0]["element_id"] == "task_observation"
    assert "尚待确认" in elements[0]["content"]
    assert not any(item.get("element_id") == "main_content" for item in elements)


def running_tool_session():
    now = time.time()
    value = session(status="tool_running", created_at=now - 20, updated_at=now)
    detail = 'python3 acceptance.py\n参数: {"command": "python3 acceptance.py", "timeout": 120}'
    value.tools["fixture-tool"] = ToolState("fixture-tool", "terminal", "running", detail, now - 10, 1)
    value.timeline.record_tool("fixture-tool", "terminal", "running", detail)
    return value


def test_task_running_card_has_one_state_and_one_visible_action():
    value = running_tool_session()
    before = snapshot(value)
    card = render_card(value, presentation="task", stream_thinking_to_body=False)
    elements = card["body"]["elements"]
    visible = json.dumps({"header": card["header"], "elements": [e for e in elements if e.get("tag") != "collapsible_panel"]}, ensure_ascii=False)
    assert visible.count("执行中") == 1
    assert visible.count("执行命令：python3 acceptance.py") == 1
    assert "subtitle" not in card["header"]
    assert "工具 #" not in card["header"]["title"]["content"]
    activity = next(e["content"] for e in elements if e.get("element_id", "").startswith("tool_activity_"))
    assert "参数:" not in activity
    panel = next(e for e in elements if e.get("element_id") == "auxiliary_timeline")
    assert 'timeout' in str(panel) and '120' in str(panel)
    footer = next(e["content"] for e in elements if e.get("element_id") == "footer")
    assert "20s" in footer
    assert "执行" not in footer and "工具 #" not in footer
    assert snapshot(value) == before


@pytest.mark.parametrize("show_reasoning,has_timeline", [(False, True), (True, False)])
def test_task_does_not_hide_parameters_when_no_detail_panel_exists(show_reasoning, has_timeline):
    value = running_tool_session()
    if not has_timeline:
        value.timeline = CardTimeline()
    card = render_card(value, presentation="task", show_reasoning=show_reasoning)
    activity = next(e["content"] for e in card["body"]["elements"] if e.get("element_id", "").startswith("tool_activity_"))
    assert "timeout=120" in activity


def test_task_silent_tool_is_last_observed_action_not_live_claim():
    value = running_tool_session()
    value.updated_at = time.time() - 130
    card = render_card(value, presentation="task")
    assert "等待新进展" in card["header"]["title"]["content"]
    assert card["header"]["template"] == "default"
    activity = next(e["content"] for e in card["body"]["elements"] if e.get("element_id", "").startswith("tool_activity_"))
    assert "上次动作" in activity and "执行中" not in activity
    assert "10s" not in activity


@pytest.mark.parametrize("reply_anchor", ["", "fixture-reply"])
def test_task_completion_retains_exactly_one_completion_marker(reply_anchor):
    value = session(status="completed", answer_text="验收结果", duration=12, reply_to_message_id=reply_anchor, model="deepseek/model")
    card = render_card(value, presentation="task")
    assert json.dumps(card, ensure_ascii=False).count("已完成") == 1
    assert "12s" in str(card) and "deepseek/model" in str(card)


def test_task_phase_without_tool_stays_visible_in_body():
    value = session(runtime_phase_text="正在压缩上下文", updated_at=time.time())
    card = render_card(value, presentation="task", stream_thinking_to_body=False)
    assert "subtitle" not in card["header"]
    assert any(e.get("element_id") == "task_action" and e["content"] == "正在压缩上下文" for e in card["body"]["elements"])


def test_task_parallel_tools_only_move_parameters_that_are_in_the_rendered_panel():
    value = running_tool_session()
    value.tools["parallel"] = ToolState("parallel", "file_read", "running", 'example.txt\n参数: {"path": "example.txt", "limit": 50}', time.time(), 2)
    value.timeline.record_tool("parallel", "file_read", "running", value.tools["parallel"].detail)
    card = render_card(value, presentation="task", max_timeline_items=1)
    rows = [e["content"] for e in card["body"]["elements"] if e.get("element_id", "").startswith("tool_activity_")]
    assert len(rows) == 2
    assert sum("参数:" in row for row in rows) == 1
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "auxiliary_timeline")
    assert "timeout" in str(rows) + str(panel) and "limit" in str(rows) + str(panel)


@pytest.mark.parametrize("max_chars", [80, 600])
def test_task_truncated_process_detail_keeps_parameters_in_body(max_chars):
    value = running_tool_session()
    command = "python3 " + "x" * (max_chars + 50)
    detail = command + "\n参数: " + json.dumps({
        "cwd": "/example/complete-scope", "command": command, "timeout": 120,
    })
    value.tools["fixture-tool"].detail = detail
    value.timeline.record_tool("fixture-tool", "terminal", "running", detail)

    card = render_card(value, presentation="task", max_tool_result_chars=max_chars)
    activity = next(e["content"] for e in card["body"]["elements"]
                    if e.get("element_id", "").startswith("tool_activity_"))
    panel = next(e for e in card["body"]["elements"]
                 if e.get("element_id") == "auxiliary_timeline")
    assert "工具详情过长，已截断" in str(panel)
    assert "/example/complete-scope" not in str(panel)
    assert "cwd=/example/complete-scope" in activity
    assert "timeout=120" in activity
    assert inspect_card_limits(card).safe


@pytest.mark.parametrize("order", ["newest_first", "chronological"])
def test_task_reused_tool_id_cannot_hide_current_parameters_using_old_detail(order):
    value = running_tool_session()
    value.timeline.record_tool("fixture-tool", "terminal", "completed", "old command")
    command = "python3 " + "x" * 700
    detail = command + '\n参数: {"cwd": "/example/current-scope", "timeout": 120}'
    value.tools["fixture-tool"].detail = detail
    value.timeline.record_tool("fixture-tool", "terminal", "running", detail)

    card = render_card(value, presentation="task", max_tool_result_chars=600,
                       timeline_order=order)
    activity = next(e["content"] for e in card["body"]["elements"]
                    if e.get("element_id", "").startswith("tool_activity_"))
    panel = next(e for e in card["body"]["elements"]
                 if e.get("element_id") == "auxiliary_timeline")
    assert "old command" in str(panel)
    assert "/example/current-scope" not in str(panel)
    assert "cwd=/example/current-scope" in activity and "timeout=120" in activity
    assert inspect_card_limits(card).safe


def test_task_current_action_keeps_identifiable_target_when_upstream_preview_was_shortened():
    value = running_tool_session()
    command = "python3 /example/acceptance/work/long_task.py"
    detail = 'python3 /example/acceptance/...\n参数: ' + json.dumps({"command": command, "timeout": 120})
    value.tools["fixture-tool"].detail = detail
    value.timeline.record_tool("fixture-tool", "terminal", "running", detail)
    card = render_card(value, presentation="task")
    activity = next(e["content"] for e in card["body"]["elements"] if e.get("element_id", "").startswith("tool_activity_"))
    assert command in activity
    assert "参数:" not in activity


def test_task_approval_keeps_full_prompt_scope_and_callback_values():
    value = session()
    value.active_interaction = InteractionState(
        interaction_id="fixture-approval", kind="approval", prompt="确认执行操作？",
        description="完整范围：只读取示例目录，不写入任何文件。",
        options=[InteractionOption(label="允许", value="approve"), InteractionOption(label="拒绝", value="deny")],
    )
    before = snapshot(value)
    classic = render_card(value)
    task = render_card(value, presentation="task")
    assert "schema" not in task
    assert task["elements"][0]["content"] == "确认执行操作？"
    assert value.active_interaction.description in str(task)
    def controls(card):
        found = []
        def visit(node):
            if isinstance(node, dict):
                if node.get("tag") == "button":
                    found.append(node.get("value"))
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)
        visit(card)
        return found
    assert controls(task) == controls(classic)
    def button_labels(node):
        if isinstance(node, dict):
            if node.get("tag") == "button":
                return [node["text"]["content"]]
            return [label for child in node.values() for label in button_labels(child)]
        if isinstance(node, list):
            return [label for child in node for label in button_labels(child)]
        return []
    assert button_labels(task) == ["允许", "拒绝"]
    assert snapshot(value) == before
    assert inspect_card_limits(task).safe


@pytest.mark.parametrize("multi_select", [False, True])
def test_task_clarify_controls_name_choices_without_changing_values(multi_select):
    value = session()
    value.active_interaction = InteractionState(
        interaction_id="fixture-clarify", kind="clarify", prompt="请选择保留的内容",
        multi_select=multi_select,
        options=[InteractionOption("完整答案", "answer"), InteractionOption("完整长选项 " * 30, "long")],
    )
    before = snapshot(value)
    card = render_card(value, presentation="task")
    assert "等待选择…" not in str(card)
    assert card["elements"][-1].get("tag") != "hr"
    assert value.active_interaction.options[1].label.strip() in str(card)
    def controls(node):
        if isinstance(node, dict):
            if node.get("tag") == "button" and isinstance(node.get("value"), dict) and "choice" in node["value"]:
                yield node["text"]["content"], node["value"]["choice"]
            if node.get("tag") == "multi_select_static":
                yield from ((o["text"]["content"], o["value"]) for o in node["options"])
            for child in node.values():
                yield from controls(child)
        elif isinstance(node, list):
            for child in node:
                yield from controls(child)
    choices = list(controls(card))
    assert "完整答案" in choices[0][0]
    assert [value for _, value in choices] == ["answer", "long"]
    if multi_select:
        assert "完整长选项" in choices[1][0] and len(choices[1][0]) <= 50
    assert snapshot(value) == before


@pytest.mark.parametrize("status,color", [("completed", "green"), ("failed", "red")])
def test_task_interaction_receipt_color_matches_its_own_state(status, color):
    from hermes_feishu_card.render import render_legacy_interaction_callback_card
    value = session(status="tool_running")
    value.active_interaction = InteractionState(
        interaction_id="fixture", kind="clarify", prompt="请选择", status=status,
        options=[InteractionOption("继续", "yes")],
    )
    card = render_legacy_interaction_callback_card(value, presentation="task")
    assert card["header"]["template"] == color


def test_task_failed_approval_marks_prompt_as_history_without_losing_scope():
    from hermes_feishu_card.render import render_legacy_interaction_callback_card
    value = session(status="failed")
    value.active_interaction = InteractionState(
        interaction_id="fixture", kind="approval", prompt="需要授权后继续执行",
        description="完整操作范围：只允许修改 example.txt。", status="failed",
        error="本轮任务已结束，未提交的选择已失效。",
        options=[InteractionOption("允许一次", "once"), InteractionOption("拒绝", "deny")],
    )
    before = snapshot(value)
    classic = render_legacy_interaction_callback_card(value)
    task = render_legacy_interaction_callback_card(value, presentation="task")
    assert task["elements"][0]["content"] == "**原审批请求**\n\n需要授权后继续执行"
    assert classic["elements"][0]["content"] == "需要授权后继续执行"
    assert value.active_interaction.description in str(task)
    assert "允许一次" in str(task) and "拒绝" in str(task)
    assert value.active_interaction.error in str(task)
    assert "interaction.select" not in str(task)
    assert snapshot(value) == before


@pytest.mark.parametrize("kind", ["approval", "clarify"])
def test_task_short_single_choices_remove_only_generated_duplicate_list(kind):
    from hermes_feishu_card.render import render_legacy_interaction_callback_card
    value = session()
    # The scope deliberately matches the generated options. It must survive.
    option_list = "1. 允许一次\n\n2. 拒绝"
    value.active_interaction = InteractionState(
        interaction_id="fixture", kind=kind, prompt="确认操作？", description=option_list,
        options=[InteractionOption("允许一次", "once"), InteractionOption("拒绝", "deny")],
    )
    classic = render_legacy_interaction_callback_card(value)
    task = render_legacy_interaction_callback_card(value, presentation="task")
    assert sum(e.get("content") == option_list for e in classic["elements"]) == 2
    assert sum(e.get("content") == option_list for e in task["elements"]) == 1
    def buttons(node):
        if isinstance(node, dict):
            if node.get("tag") == "button":
                yield node["text"]["content"], node["value"]
            for child in node.values():
                yield from buttons(child)
        elif isinstance(node, list):
            for child in node:
                yield from buttons(child)
    assert [text for text, _ in buttons(task)] == ["允许一次", "拒绝"]
    assert [value for _, value in buttons(task)] == [value for _, value in buttons(classic)]


@pytest.mark.parametrize("multi_select,long_label", [(True, False), (False, True)])
def test_task_choices_retain_list_when_controls_do_not_carry_complete_labels(multi_select, long_label):
    from hermes_feishu_card.render import render_legacy_interaction_callback_card
    label = "仅允许本次请求操作指定目录并保留其他所有文件" if long_label else "允许一次"
    value = session()
    value.active_interaction = InteractionState(
        interaction_id="fixture", kind="approval", prompt="确认操作？",
        multi_select=multi_select,
        options=[InteractionOption(label, "once"), InteractionOption("拒绝", "deny")],
    )
    task = render_legacy_interaction_callback_card(value, presentation="task")
    assert any(e.get("content") == f"1. {label}\n\n2. 拒绝" for e in task["elements"])


def test_task_event_sourced_generic_interrupt_uses_header_and_keeps_tool_evidence():
    from hermes_feishu_card.events import SidecarEvent
    value = running_tool_session()
    value.thinking_text = "已读取文件，正在检查结果。"
    value.apply(SidecarEvent(
        schema_version="1", event="message.failed", sequence=1,
        conversation_id=value.conversation_id, message_id=value.message_id, chat_id=value.chat_id,
        platform="feishu", created_at=time.time(), data={"error": "任务已中断"},
    ))
    assert value.terminal_reasoning_notice == "任务已中断"
    before = snapshot(value)
    classic = render_card(value)
    result = render_card_result(value, presentation="task")
    task = result.card
    assert task["header"]["title"]["content"].endswith("已停止")
    assert not any(e.get("element_id", "").startswith("main_content") for e in task["body"]["elements"])
    assert result.primary_text == ""
    assert "任务已中断" in str(classic)
    assert value.thinking_text in str(task)
    assert any("已中断" in e.get("content", "") and "terminal" in e.get("content", "")
               for e in task["body"]["elements"] if e.get("element_id", "").startswith("tool_activity_"))
    assert snapshot(value) == before


@pytest.mark.parametrize("notice", ["", "任务已中断：连接已断开", "读取文件失败：权限不足"])
def test_task_does_not_merge_unsourced_or_specific_failure_content(notice):
    value = session(status="failed", answer_text=notice or "任务已中断",
                    terminal_reasoning_notice=notice)
    task = render_card(value, presentation="task")
    assert any(e.get("content") == value.answer_text for e in task["body"]["elements"]
               if e.get("element_id", "").startswith("main_content"))


def test_task_style_never_evades_final_card_capacity_gate():
    value = session(status="completed", answer_text="完整答案" * 12000)
    result = render_card_result(value, presentation="task")
    assert result.disposition == "native"
    assert value.answer_text == "完整答案" * 12000
    assert result.inspection.safe is False


@pytest.mark.parametrize("status", ["pending", "completed", "failed"])
def test_near_capacity_callback_preserves_scope_using_safe_original_layout(status):
    from hermes_feishu_card.card_limits import SAFE_CARD_JSON_BYTES
    from hermes_feishu_card.render import render_legacy_interaction_callback_card
    value = session()
    value.active_interaction = InteractionState(
        interaction_id="fixture", kind="approval", status=status,
        prompt="确认执行？", description="完整范围： ",
        options=[InteractionOption("允许", "once"), InteractionOption("拒绝", "deny")],
    )
    original = render_legacy_interaction_callback_card(value)
    room = SAFE_CARD_JSON_BYTES - inspect_card_limits(original).json_bytes
    value.active_interaction.description += "x " * (room // 2)
    original = render_legacy_interaction_callback_card(value)
    assert inspect_card_limits(original).safe
    assert inspect_card_limits(original).json_bytes >= SAFE_CARD_JSON_BYTES - 3
    # A long configured title must still trigger the original-layout fallback,
    # even when removing a redundant pending footer frees a few bytes.
    task = render_legacy_interaction_callback_card(value, title="验收任务 " * 30, presentation="task")
    assert inspect_card_limits(task).safe
    assert task == original
    assert value.active_interaction.description.strip() in str(task)
