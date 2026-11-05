"""Live tool activity visibility: full, summary and hidden.

The setting exists so a group chat can stop seeing commands, parameters and
paths while a turn is still running. Terminal cards stay governed by the
existing hide-completed switches, so this file also pins that a finished card
does not change when the mode changes.
"""
import json
from types import SimpleNamespace

import pytest

from hermes_feishu_card import render
from hermes_feishu_card.config import DEFAULT_CONFIG
from hermes_feishu_card.config_rules import normalize_tool_activity
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.reading import explain_reading_config
from hermes_feishu_card.render import render_card
from hermes_feishu_card.render_options import card_render_options
from hermes_feishu_card.session import CardSession

SECRET = "sleep 300 /secret/path/run.sh"


def _tool_event(sequence, tool_id, status, detail, created_at):
    return SidecarEvent.from_dict({
        "schema_version": "1", "event": "tool.updated", "platform": "feishu",
        "conversation_id": "fixture", "message_id": "fixture", "chat_id": "fixture",
        "sequence": sequence, "created_at": created_at,
        "data": {
            "tool_id": tool_id, "name": "terminal", "status": status, "detail": detail,
        },
    })


def live_session():
    session = CardSession(conversation_id="fixture", message_id="fixture", chat_id="fixture")
    session.created_at = 10.0
    session.apply(_tool_event(1, "done", "completed", "pytest -q " + SECRET, 20.0))
    session.apply(_tool_event(2, "live", "running", SECRET, 60.0))
    session.answer_text = "Preserved answer"
    return session


def tool_rows(card):
    return [
        element for element in card["body"]["elements"]
        if str(element.get("element_id", "")).startswith("tool_activity")
        or element.get("element_id") == "tool_summary"
    ]


def footer_text(card):
    return " ".join(
        element.get("content", "") for element in card["body"]["elements"]
        if element.get("element_id") == "footer"
    )


def test_default_is_full_and_unknown_values_are_rejected():
    assert DEFAULT_CONFIG["card"]["tool_activity"] == "full"
    assert normalize_tool_activity(" Hidden ") == "hidden"
    with pytest.raises(ValueError):
        normalize_tool_activity("compact")
    with pytest.raises(ValueError):
        normalize_tool_activity(True)


def test_render_options_normalize_and_fall_back():
    assert card_render_options({"tool_activity": "SUMMARY"})["tool_activity"] == "summary"
    assert card_render_options({"tool_activity": "nonsense"})["tool_activity"] == "full"
    assert card_render_options({})["tool_activity"] == "full"


@pytest.mark.parametrize("mode", ["full", "summary", "hidden"])
def test_terminal_cards_are_unchanged_by_the_live_setting(mode):
    for status in ("completed", "failed"):
        session = live_session()
        session.status = status
        baseline = render_card(session)
        assert render_card(session, tool_activity=mode) == baseline


def test_hidden_removes_every_live_tool_surface(monkeypatch):
    monkeypatch.setattr(render, "_time", SimpleNamespace(time=lambda: 70.0))
    session = live_session()
    card = render_card(session, tool_activity="hidden")
    assert "subtitle" not in card["header"]
    assert tool_rows(card) == []
    assert SECRET not in json.dumps(card, ensure_ascii=False)
    assert "/secret/path" not in json.dumps(card, ensure_ascii=False)
    assert "工具 #" not in footer_text(card)
    assert "正在执行命令" not in footer_text(card)
    # The state and elapsed time survive: the card still says the turn is alive.
    assert "执行中" in footer_text(card)


def test_summary_keeps_only_generic_progress(monkeypatch):
    monkeypatch.setattr(render, "_time", SimpleNamespace(time=lambda: 70.0))
    session = live_session()
    card = render_card(session, tool_activity="summary")
    rows = tool_rows(card)
    assert len(rows) == 1
    assert "第 2 步" in rows[0]["content"]
    assert "terminal" not in rows[0]["content"]
    assert card["header"]["subtitle"]["content"] == "执行中 · 第 2 步"
    assert SECRET not in json.dumps(card, ensure_ascii=False)
    footer = footer_text(card)
    assert "工具 #2" in footer
    assert "正在执行命令" not in footer


def test_full_mode_still_shows_the_live_command(monkeypatch):
    monkeypatch.setattr(render, "_time", SimpleNamespace(time=lambda: 70.0))
    card = render_card(live_session())
    assert SECRET in json.dumps(card, ensure_ascii=False)
    assert "正在执行命令" in footer_text(card)


def test_card_config_explains_the_effective_mode():
    report = explain_reading_config(
        {"card": {"tool_activity": "hidden"}}, profile_id=None, bot_id=None,
    )
    assert report["values"]["tool_activity"] == "hidden"
    assert "global.card.tool_activity" in report["sources"]["tool_activity"]
