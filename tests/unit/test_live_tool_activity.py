"""Live display modes must cover every surface without altering terminal evidence."""
import json
from types import SimpleNamespace

import pytest

from hermes_feishu_card import render
from hermes_feishu_card.config_rules import DEFAULT_CONFIG
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.render_options import card_render_options
from hermes_feishu_card.session import CardSession, InteractionState


@pytest.fixture
def activity(monkeypatch):
    monkeypatch.setattr(render, "_time", SimpleNamespace(time=lambda: 30.0))
    session = CardSession(conversation_id="fixture", message_id="fixture", chat_id="oc_example")
    session.created_at = 5.0
    for sequence, status in enumerate(("completed", "running"), 1):
        session.apply(SidecarEvent.from_dict({
            "schema_version": "1", "event": "tool.updated", "platform": "feishu",
            "conversation_id": "fixture", "message_id": "fixture", "chat_id": "oc_example",
            "sequence": sequence, "created_at": 10.0 + sequence,
            "data": {"tool_id": f"tool-{sequence}", "name": "synthetic_terminal", "status": status,
                     "detail": "synthetic-command /tmp/synthetic-path\n参数: synthetic-parameter"},
        }))
    session.answer_text = "Preserved answer"
    return session


@pytest.mark.parametrize("mode", ["full", "summary", "hidden"])
@pytest.mark.parametrize("state", ["running", "waiting", "completed", "failed"])
@pytest.mark.parametrize("reasoning", [True, False])
@pytest.mark.parametrize("layout", ["classic", "task"])
def test_live_activity_all_surfaces(activity, mode, state, reasoning, layout):
    activity.status = state
    if state == "waiting":
        activity.active_interaction = InteractionState("choice", "clarify", "Pick an option", requested_at=15)
    options = dict(show_reasoning=reasoning, presentation=layout, interaction_mode="text")
    card = render.render_card(activity, live_tool_activity=mode, **options)
    full = render.render_card(activity, **options)
    if state in {"completed", "failed"} or mode == "full":
        assert card == full
        return
    serialized = json.dumps(card, ensure_ascii=False)
    for detail in ("synthetic_terminal", "synthetic-command", "/tmp/synthetic-path", "synthetic-parameter"):
        assert detail not in serialized
    rows = [e for e in card["body"]["elements"] if e.get("element_id", "").startswith("tool_activity_")]
    assert "tool_summary" not in serialized
    if mode == "summary":
        assert len(rows) == 1
        assert "第 2 步" in rows[0]["content"]
        assert "已完成" not in rows[0]["content"]
        assert "18s" in rows[0]["content"]
        if state == "running" and layout == "classic":
            assert "正在执行第 2 步" in serialized
    else:
        assert not rows
        assert "工具 #" not in serialized


@pytest.mark.parametrize("mode", ["summary", "hidden"])
@pytest.mark.parametrize("reasoning", [True, False])
@pytest.mark.parametrize("keep_finished", [True, False])
def test_live_summary_fallback_and_finished_predecessor_are_suppressed(activity, mode, reasoning, keep_finished):
    if keep_finished:
        for tool in activity.tools.values():
            tool.status = "completed"
    else:
        activity.tools.clear()
    card = render.render_card(activity, live_tool_activity=mode, show_reasoning=reasoning)
    assert "tool_summary" not in json.dumps(card)
    assert "synthetic" not in json.dumps(card)


@pytest.mark.parametrize("mode", ["summary", "hidden"])
@pytest.mark.parametrize("layout", ["classic", "task"])
def test_clarify_callback_has_no_generated_tool_details(activity, mode, layout):
    activity.active_interaction = InteractionState("choice", "clarify", "Pick an option")
    serialized = json.dumps(render.render_card(activity, live_tool_activity=mode, presentation=layout))
    assert 'synthetic' not in serialized


def test_hidden_does_not_redact_model_authored_content(activity):
    activity.answer_text = 'Model explanation: synthetic-command /tmp/synthetic-path'
    card = render.render_card(activity, live_tool_activity='hidden')
    assert activity.answer_text in json.dumps(card)


@pytest.mark.parametrize('display_status', ['completed', 'failed'])
@pytest.mark.parametrize('mode', ['summary', 'hidden'])
@pytest.mark.parametrize('layout', ['classic', 'task'])
def test_display_status_override_cannot_reveal_live_tool_details(activity, display_status, mode, layout):
    activity.display_status = display_status
    serialized = json.dumps(render.render_card(activity, live_tool_activity=mode, presentation=layout), ensure_ascii=False)
    assert 'synthetic' not in serialized
    if mode == 'hidden':
        assert '工具 #' not in serialized


@pytest.mark.parametrize("mode", ["summary", "hidden"])
@pytest.mark.parametrize("layout", ["classic", "task"])
@pytest.mark.parametrize("interaction_mode", ["text", "callback"])
def test_approval_scope_and_layout_are_unchanged(activity, mode, layout, interaction_mode):
    activity.active_interaction = InteractionState("approval", "approval", "Approve synthetic-command /tmp/synthetic-path")
    options = dict(presentation=layout, interaction_mode=interaction_mode)
    assert render.render_card(activity, live_tool_activity=mode, **options) == render.render_card(activity, **options)


@pytest.mark.parametrize("state", ["completed", "failed"])
@pytest.mark.parametrize("flag", ["hide_completed_tool_activity", "hide_successful_tool_activity"])
@pytest.mark.parametrize("mode", ["summary", "hidden"])
def test_terminal_visibility_switches_are_independent(activity, state, flag, mode):
    activity.status = state
    assert render.render_card(activity, live_tool_activity=mode, **{flag: True}) == render.render_card(activity, **{flag: True})


def test_default_and_invalid_render_option_preserve_full(activity):
    assert DEFAULT_CONFIG["card"]["live_tool_activity"] == "full"
    assert render.render_card(activity) == render.render_card(activity, live_tool_activity="full")
    for invalid in (None, True, 1, "off", [], {}):
        assert card_render_options({"live_tool_activity": invalid})["live_tool_activity"] == "full"
