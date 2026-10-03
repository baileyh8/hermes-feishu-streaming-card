"""Shared config-to-renderer boundary for the server and offline previews."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .status import StatusConfig


def _boolean(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value) if value in (0, 1) else default
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"true", "1", "yes", "on"}:
            return True
        if value in {"false", "0", "no", "off"}:
            return False
    return default


def _positive(value: Any, default: int) -> int:
    try:
        number = int(value)
        return number if number > 0 else default
    except (ValueError, TypeError, OverflowError):
        return default


def card_render_options(config: Mapping[str, Any]) -> dict[str, Any]:
    mode = config.get("table_overflow_mode", "compact")
    mode = mode.strip().lower() if isinstance(mode, str) else "compact"
    result = {
        key: _boolean(config.get(key), default)
        for key, default in (
            ("show_reasoning", True), ("stream_thinking_to_body", True),
            ("hide_completed_tool_activity", False), ("timeline_expanded", False),
        )
    }
    result.update({
        key: _positive(config.get(key), default)
        for key, default in (
            ("max_timeline_items", 12), ("max_reasoning_chars", 1200), ("max_tool_result_chars", 600),
        )
    })
    result.update(
        hide_successful_tool_activity=_boolean(config.get("_hide_successful_tool_activity"), False),
        reasoning_format=config.get("reasoning_format", "panel"),
        timeline_order=config.get("timeline_order", "newest_first"),
        timeline_tools_per_reasoning=config.get("timeline_tools_per_reasoning", 0),
        thinking_body_tail_chars=config.get("thinking_body_tail_chars", 0),
        status_config=StatusConfig.from_mapping(config.get("status")),
        text_sizes=config.get("text_sizes") if isinstance(config.get("text_sizes"), dict) else None,
        table_overflow_mode=mode if mode in {"compact", "truncate"} else "compact",
        width_mode=config.get("width_mode", "default"),
        presentation=config.get("_presentation_mode", "classic"),
    )
    return result
