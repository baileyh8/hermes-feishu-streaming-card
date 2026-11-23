"""Tests for the opt-in header sub-title emoji theme (``card.emoji_theme``).

The contract worth protecting:

* ``off`` (default) renders byte-identical sub-titles — an upgrade must change nothing.
* The glyph is prepended AFTER truncation, so a long command cannot eat it.
* Only the LIVE action row is decorated: terminal sub-titles ("本轮回复结束" and a failed
  turn's "停在哪") keep their wording, and system notices stay undecorated.
* ``tool_activity: summary`` gets the neutral glyph — mapping it through the phrase table
  would leak the tool name that setting exists to hide.
"""
from hermes_feishu_card.emoji_theme import (
    decorate_action,
    emoji_for_action,
    normalize_emoji_theme,
    state_glyph,
)
from hermes_feishu_card.events import SidecarEvent
from hermes_feishu_card.render import render_card
from hermes_feishu_card.session import CardSession


def _session_with_tool(*, name: str, detail: str, status: str = "running") -> CardSession:
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    assert session.apply(
        SidecarEvent(
            schema_version="1",
            event="tool.updated",
            conversation_id="c",
            message_id="m",
            chat_id="oc",
            platform="feishu",
            sequence=1,
            created_at=0.0,
            data={"tool_id": "tool-1", "name": name, "status": status, "detail": detail},
        )
    )
    return session


# ---------------------------------------------------------------- pure mapping


def test_normalize_accepts_known_theme_and_falls_back_for_typos():
    assert normalize_emoji_theme("classic") == "classic"
    assert normalize_emoji_theme("  CLASSIC  ") == "classic"
    assert normalize_emoji_theme("off") == "off"
    # Cosmetic switch: a typo must not stop the sidecar from starting.
    assert normalize_emoji_theme("classicc") == "off"
    assert normalize_emoji_theme("") == "off"
    assert normalize_emoji_theme(None) == "off"
    assert normalize_emoji_theme(123) == "off"


def test_emoji_for_action_uses_the_phrase_layer_not_tool_names():
    assert emoji_for_action("读取文件：render.py") == "📖"
    assert emoji_for_action("执行命令：pytest -q") == "⌨️"
    assert emoji_for_action("搜索网页：飞书卡片限流") == "🔍"
    assert emoji_for_action("搜索历史会话：上次讨论") == "🗂"
    assert emoji_for_action("正在压缩上下文") == "🗜"
    assert emoji_for_action("") == ""
    # Unknown phrase: the caller decides the fallback, not this helper.
    assert emoji_for_action("frobnicate：x") == ""


def test_keyword_table_covers_tools_the_phrase_layer_does_not_know():
    """Phrase-less tools render as "使用 vision analyze：…" — keywords catch the frequent ones."""
    cases = {
        "使用 vision analyze：screenshot.png": "👁",
        "使用 image generate：海报": "🎨",
        "使用 cronjob manage：每天 09:00": "⏰",
        "使用 memory：用户偏好": "🧠",
        "使用 clarify：确认吗": "❓",
        "使用 mcp mijia mijia get devices：客厅": "🏠",
        "使用 device control：台灯": "🏠",
        "使用 subagent start：调研": "👥",
    }
    for text, expected in cases.items():
        assert emoji_for_action(text) == expected, text
    # Phrase matches must still win over keywords.
    assert emoji_for_action("搜索网页：image 素材") == "🔍"


def test_lobster_theme_swaps_the_state_glyph_family():
    assert normalize_emoji_theme("lobster") == "lobster"
    assert state_glyph("classic", "running") == "⏳"
    assert state_glyph("lobster", "running") == "🦞"
    assert state_glyph("lobster", "failed") == "😵"
    assert state_glyph("lobster", "completed") == "✅"
    assert state_glyph("lobster", "pending") == "🛎"
    # off must restore the pre-emoji literals, not blank them.
    assert state_glyph("off", "running") == "⏳"
    assert state_glyph("off", "failed") == "⛔"
    assert state_glyph("classic", "pending") == ""


def test_decorate_action_is_idempotent_and_off_is_a_no_op():
    assert decorate_action("执行命令：pytest", theme="off") == "执行命令：pytest"
    assert decorate_action("执行命令：pytest", theme="classic") == "⌨️ 执行命令：pytest"
    # Running it twice must not stack two glyphs.
    once = decorate_action("执行命令：pytest", theme="classic")
    assert decorate_action(once, theme="classic") == once
    # Empty lines stay empty rather than becoming a lone glyph.
    assert decorate_action("", theme="classic") == ""
    # summary mode: neutral glyph, no tool name derived from the phrase table.
    assert decorate_action("执行中 · 第 2 步", theme="classic", generic=True) == "🔧 执行中 · 第 2 步"


# ------------------------------------------------------------- render integration


def test_default_off_keeps_the_existing_subtitle_unchanged():
    session = _session_with_tool(name="terminal", detail="pytest")
    card = render_card(session, title="研发助手")
    assert card["header"]["subtitle"]["content"] == "执行命令：pytest"


def test_classic_theme_prefixes_the_live_action_row():
    session = _session_with_tool(name="read_file", detail="render.py")
    card = render_card(session, title="研发助手", emoji_theme="classic")
    assert card["header"]["subtitle"]["content"] == "📖 读取文件：render.py"
    # The title keeps its own state glyph: state and action are different facts.
    assert card["header"]["title"]["content"].startswith("⏳")
    # The content-area tool row is NOT decorated twice.
    row = next(
        item for item in card["body"]["elements"]
        if item.get("element_id") == "tool_activity_0"
    )
    assert not row["content"].startswith("📖")


def test_completed_turn_subtitle_is_not_decorated():
    session = _session_with_tool(name="terminal", detail="pytest", status="running")
    session.status = "completed"
    card = render_card(session, emoji_theme="classic")
    assert card["header"]["subtitle"]["content"] == "本轮回复结束"


def test_notice_cards_are_not_decorated():
    session = _session_with_tool(name="terminal", detail="pytest")
    session.delivery_kind = "notice"
    card = render_card(session, emoji_theme="classic")
    subtitle = card["header"].get("subtitle")
    assert subtitle is None or not subtitle["content"].startswith(("⌨️", "🔧"))


def test_summary_mode_gets_the_neutral_glyph_only():
    session = _session_with_tool(name="terminal", detail="pytest")
    card = render_card(session, emoji_theme="classic", tool_activity="summary")
    assert card["header"]["subtitle"]["content"] == "🔧 执行中 · 第 1 步"
    assert "terminal" not in str(card["header"])


def test_glyph_survives_truncation_of_a_long_command():
    long_command = "python3 -m pytest " + "tests/unit/test_render.py " * 12
    session = _session_with_tool(name="terminal", detail=long_command)
    card = render_card(session, emoji_theme="classic")
    subtitle = card["header"]["subtitle"]["content"]
    assert subtitle.startswith("⌨️ ")
    # 100-char cap (the truncation happens BEFORE decoration) + the glyph and its space.
    assert len(subtitle) <= 103


def test_compaction_phase_line_is_decorated():
    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.runtime_phase_text = "正在压缩上下文"
    card = render_card(session, emoji_theme="classic")
    assert card["header"]["subtitle"]["content"] == "🗜 正在压缩上下文"


def test_off_theme_keeps_the_legacy_state_glyphs():
    """Switching the theme off must restore ⏳/⛔, not blank the header."""
    session = _session_with_tool(name="terminal", detail="pytest")
    running = render_card(session, emoji_theme="off")
    assert running["header"]["title"]["content"].startswith("⏳")
    session.status = "failed"
    failed = render_card(session, emoji_theme="off")
    assert failed["header"]["title"]["content"].startswith("⛔")


def test_lobster_theme_themes_the_state_and_the_action():
    session = _session_with_tool(name="terminal", detail="pytest -q")
    card = render_card(session, emoji_theme="lobster")
    assert card["header"]["title"]["content"].startswith("🦞")
    assert card["header"]["subtitle"]["content"] == "⌨️ 执行命令：pytest -q"


def test_lobster_theme_marks_a_pending_decision():
    from hermes_feishu_card.session import InteractionState
    import time as _time

    session = CardSession(conversation_id="c", message_id="m", chat_id="oc")
    session.active_interaction = InteractionState(
        interaction_id="approval", kind="approval", prompt="是否执行该命令？",
        timeout_seconds=120.0, requested_at=_time.time(),
    )
    card = render_card(session, emoji_theme="lobster", interaction_mode="text")
    title = card["header"]["title"]["content"]
    assert title.startswith("🛎 待审批："), title


def test_phrase_less_tool_gets_a_keyword_glyph_end_to_end():
    session = _session_with_tool(name="vision_analyze", detail="screenshot.png")
    card = render_card(session, emoji_theme="classic")
    assert card["header"]["subtitle"]["content"].startswith("👁 "), card["header"]["subtitle"]
