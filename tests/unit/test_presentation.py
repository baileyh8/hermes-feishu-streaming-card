import copy
import json
import time

import pytest

from hermes_feishu_card.card_limits import inspect_card_limits
from hermes_feishu_card.presentation import task_presentation
from hermes_feishu_card.render import render_card, render_card_result
from hermes_feishu_card.session import CardSession, InteractionState, InteractionOption


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
    footer = next(e["content"] for e in card["body"]["elements"] if e.get("element_id") == "footer")
    assert "color='neutral'" in footer
    assert "已停止" not in footer


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
    task = render_legacy_interaction_callback_card(value, presentation="task")
    assert inspect_card_limits(task).safe
    assert task == original
    assert value.active_interaction.description.strip() in str(task)
