"""Optional leading glyph for the card's header sub-title (config: ``card.emoji_theme``).

Presentation only: the theme never adds information, never changes the wording of the
line it decorates, and never adds an API call (the sub-title already changes whenever the
running tool changes). ``off`` (the default) leaves the sub-title byte-identical to the
pre-emoji card, so the switch is a pure opt-in.

Why the mapping keys are action PHRASES and not tool names
-----------------------------------------------------------
``session._TOOL_ACTION_PHRASES`` / ``_TOOL_ACTION_FAMILIES`` already normalize every
tool — core, plugin, and MCP alike — into a short phrase ("读取文件", "执行命令"). Hanging
the glyphs off that phrase layer means a tool this plugin has never seen still gets a
sensible glyph, and no whitelist has to be maintained when a new tool ships.

Why the decorator runs AFTER truncation
---------------------------------------
The action line is capped at ``_HEADER_ACTION_TEXT_MAX_CHARS`` inside
``_latest_running_action_text``. The glyph therefore has to be prepended to the ALREADY
truncated string: prefixing earlier would let a long command eat the glyph, which is the
one thing this feature exists to keep visible.
"""
from __future__ import annotations

from typing import Final

EMOJI_THEME_OFF: Final = "off"
EMOJI_THEME_DEFAULT: Final = EMOJI_THEME_OFF
# ``classic``: action glyph on the live sub-title only (the shipped behaviour).
# ``lobster``: classic + the header STATE glyph becomes a themed family (🦞 running /
# ✅ done / 😵 failed / 🛎 pending), so the reaction and the card read as the same creature.
EMOJI_THEME_VALUES: Final = (EMOJI_THEME_OFF, "classic", "lobster")

# Longest phrase wins (dict order is the tie-break), so "搜索历史会话" is not read as
# "搜索" and "读取终端输出" is not read as "读取文件"-family.
_PHRASE_EMOJI: Final[dict[str, str]] = {
    "搜索历史会话": "🗂",
    "读取终端输出": "🖥",
    "搜索文件": "🗂",
    "读取文件": "📖",
    "读取窗口": "📖",
    "浏览网页": "🖼",
    "搜索网页": "🔍",
    "搜索推文": "🔍",
    "搜索工具": "🔍",
    "执行命令": "⌨️",
    "写入文件": "✍️",
    "编辑文件": "✍️",
    "分派子任务": "👥",
    "查看技能列表": "🧩",
    "修改技能": "🧩",
    "查看技能": "🧩",
    "整理待办": "📋",
}

# Runtime PHASE lines — only context compaction produces one today
# (``session.runtime_phase_text``). Kept separate from tool phrases on purpose.
_PHASE_EMOJI: Final[dict[str, str]] = {"压缩上下文": "🗜"}

# Tools the phrase layer does not know render as "使用 vision analyze：…" (the tool name
# with underscores flattened). Keywords catch the high-frequency ones instead of adding a
# tool-name whitelist: the text is the only thing this module gets, and it stays correct
# for plugin/MCP tools nobody enumerated.
_KEYWORD_EMOJI: Final[tuple[tuple[str, str], ...]] = (
    ("vision", "👁"),
    ("image", "🎨"),
    ("cron", "⏰"),
    ("memory", "🧠"),
    ("clarify", "❓"),
    ("mijia", "🏠"),
    ("device", "🏠"),
    ("subagent", "👥"),
)

# Header STATE glyphs per theme. ``classic`` keeps the pre-emoji literal glyphs so the
# shipped look never changes underneath the user; ``lobster`` is the opt-in family.
_STATE_GLYPHS: Final[dict[str, dict[str, str]]] = {
    "classic": {"running": "⏳", "completed": "✅", "failed": "⛔", "pending": ""},
    "lobster": {"running": "🦞", "completed": "✅", "failed": "😵", "pending": "🛎"},
}

# A tool whose phrase this module does not know, or a generic progress line such as
# "执行中 · 第 N 步" (``tool_activity: summary``), gets this instead of nothing.
_GENERIC_EMOJI: Final = "🔧"

# Anything already decorated (a caller that runs this twice) is left alone.
# Both the full and the stripped presentation form are matched: "⌨️" is U+2328 plus a
# variation selector, so a bare ``line[0]`` test would miss it and stack a second glyph.
_KNOWN_GLYPHS: Final[frozenset[str]] = frozenset(
    {*_PHRASE_EMOJI.values(), *_PHASE_EMOJI.values(), _GENERIC_EMOJI}
)
_GLYPH_PREFIXES: Final[tuple[str, ...]] = tuple(
    {glyph for glyph in _KNOWN_GLYPHS} | {glyph.replace("\ufe0f", "") for glyph in _KNOWN_GLYPHS}
)


def normalize_emoji_theme(value: object) -> str:
    """Tolerant read of the setting.

    An unknown value falls back to ``off`` instead of raising: this is a cosmetic
    switch, and a typo in a hand-edited ``config.yaml`` must not be able to stop the
    sidecar from starting. The cost is that a misspelled theme looks "not applied"
    rather than erroring — that is the deliberate trade.
    """
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if text in EMOJI_THEME_VALUES else EMOJI_THEME_DEFAULT


def emoji_for_action(text: str) -> str:
    """The glyph for an action/phase line, or ``""`` when the theme has nothing better.

    Matching is a substring test over the head of the line (the first 40 characters),
    because a phase line arrives as "正在压缩上下文" while the phrase table stores the
    bare noun. Longest phrase first so a shorter phrase cannot shadow a longer one.
    """
    head = str(text or "").strip()[:40]
    if not head:
        return ""
    for phrase in sorted(_PHRASE_EMOJI, key=len, reverse=True):
        if phrase in head:
            return _PHRASE_EMOJI[phrase]
    for phrase in sorted(_PHASE_EMOJI, key=len, reverse=True):
        if phrase in head:
            return _PHASE_EMOJI[phrase]
    lowered = head.lower()
    for keyword, glyph in _KEYWORD_EMOJI:
        if keyword in lowered:
            return glyph
    return ""


def state_glyph(theme: str, state: str) -> str:
    """Header STATE glyph for a theme (running/completed/failed/pending).

    ``off`` resolves to the ``classic`` glyphs on purpose: those literals (⏳/✅/⛔) predate
    this module and must survive when the theme is switched off, or "off" would silently
    change the card instead of restoring it.
    """
    resolved = normalize_emoji_theme(theme)
    table = _STATE_GLYPHS["classic" if resolved == EMOJI_THEME_OFF else resolved]
    return table.get(state, "")


def decorate_action(text: str, *, theme: str, generic: bool = False) -> str:
    """Prefix ``text`` with the theme's glyph. Returns ``text`` unchanged for ``off``.

    ``generic`` is for ``tool_activity: summary``, whose line is deliberately free of
    tool names: mapping it through the phrase table would leak the very detail that
    setting exists to hide, so it takes the neutral glyph instead.
    """
    resolved = normalize_emoji_theme(theme)
    line = str(text or "").strip()
    if resolved == EMOJI_THEME_OFF or not line:
        return text
    if line.startswith(_GLYPH_PREFIXES):
        return line
    glyph = _GENERIC_EMOJI if generic else (emoji_for_action(line) or _GENERIC_EMOJI)
    return f"{glyph} {line}"
