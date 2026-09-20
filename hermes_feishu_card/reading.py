"""Opt-in reading defaults and a credential-free explanation of their origins."""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


# This derived field is internal: the legacy hide switch still covers both
# successful and failed terminals, including an explicit False override.
HIDE_SUCCESSFUL_TOOL_ACTIVITY = "_hide_successful_tool_activity"
READING_PRESETS = {
    "classic": {
        "show_reasoning": True,
        "stream_thinking_to_body": True,
        "hide_completed_tool_activity": False,
        "reasoning_format": "panel",
        "timeline_expanded": False,
        HIDE_SUCCESSFUL_TOOL_ACTIVITY: False,
    },
    "focused": {
        "show_reasoning": True,
        "stream_thinking_to_body": False,
        "hide_completed_tool_activity": False,
        "reasoning_format": "panel",
        "timeline_expanded": False,
        HIDE_SUCCESSFUL_TOOL_ACTIVITY: True,
    },
    "detailed": {
        "show_reasoning": True,
        "stream_thinking_to_body": False,
        "hide_completed_tool_activity": False,
        "reasoning_format": "panel",
        "timeline_expanded": True,
        HIDE_SUCCESSFUL_TOOL_ACTIVITY: False,
    },
}
READING_FIELDS = (
    "reading_preset", "show_reasoning", "stream_thinking_to_body",
    "hide_completed_tool_activity", "reasoning_format", "timeline_expanded",
    "max_timeline_items", "max_reasoning_chars", "max_tool_result_chars",
    "table_overflow_mode",
    "timeline_order", "timeline_tools_per_reasoning",
)


def normalize_reading_preset(value: object, *, path: str = "card.reading_preset") -> str:
    if not isinstance(value, str) or value.strip().lower() not in READING_PRESETS:
        raise ValueError(f"{path} must be classic, focused or detailed")
    return value.strip().lower()


def expand_reading_preset(card: Mapping[str, Any] | None) -> dict[str, Any]:
    """Expand one scope before merging it over its parent scope.

    Already resolved scopes carry the derived terminal flag. Preserve it when
    they travel through the existing profile/factory merge a second time.
    """
    incoming = copy.deepcopy(dict(card or {}))
    result: dict[str, Any] = {}
    if "reading_preset" in incoming:
        preset = normalize_reading_preset(incoming["reading_preset"])
        incoming["reading_preset"] = preset
        result.update(READING_PRESETS[preset])
    if "hide_completed_tool_activity" in incoming and HIDE_SUCCESSFUL_TOOL_ACTIVITY not in incoming:
        # A legacy explicit value has higher priority than preset behavior.
        result[HIDE_SUCCESSFUL_TOOL_ACTIVITY] = False
    result.update(incoming)
    return result


def explain_reading_config(
    raw: Mapping[str, Any], *, profile_id: str | None = None, bot_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the same config scopes as the runner, returning only safe values.

    Callers validate the YAML with load_config first. No credentials, paths,
    route identifiers, user titles, or arbitrary config objects are returned.
    """
    from .config import DEFAULT_CONFIG, merge_card_config

    resolved = copy.deepcopy(DEFAULT_CONFIG["card"])
    resolved["reading_preset"] = "classic"
    sources = {key: "default" for key in (*READING_FIELDS, HIDE_SUCCESSFUL_TOOL_ACTIVITY)}

    def mapping(value: object, label: str) -> Mapping[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} must be a mapping")
        return value

    def apply_scope(value: object, label: str) -> None:
        nonlocal resolved
        card = dict(value) if isinstance(value, Mapping) else {}
        if "reading_preset" in card:
            preset = normalize_reading_preset(card["reading_preset"])
            for key in ("reading_preset", *READING_PRESETS[preset]):
                sources[key] = f"{label}.reading_preset ({preset})"
        for key in READING_FIELDS:
            if key in card and key != "reading_preset":
                sources[key] = f"{label}.{key} (explicit)"
        if "hide_completed_tool_activity" in card:
            sources[HIDE_SUCCESSFUL_TOOL_ACTIVITY] = f"{label}.hide_completed_tool_activity (explicit)"
        resolved = merge_card_config(resolved, card)

    apply_scope(raw.get("card"), "global.card")
    profiles = mapping(raw.get("profiles"), "profiles")
    selected = raw
    if profiles:
        if profile_id is None:
            if "default" in profiles:
                profile_id = "default"
            elif len(profiles) == 1:
                profile_id = next(iter(profiles))
            else:
                raise ValueError("Multiple profiles: select --profile-id")
        if profile_id not in profiles:
            raise ValueError("Unknown profile: select a configured --profile-id")
        selected = mapping(profiles[profile_id], "profile")
        apply_scope(selected.get("card"), "profile.card")
    elif profile_id not in {None, "default"}:
        raise ValueError("Unknown profile: select a configured --profile-id")
    bots = mapping(selected.get("bots"), "bots")
    items = mapping(bots.get("items"), "bots.items")
    bindings = mapping(selected.get("bindings"), "bindings")
    selected_bot = bot_id or bindings.get("fallback_bot") or bots.get("default") or "default"
    if selected_bot in items:
        apply_scope(mapping(items[selected_bot], "bot").get("card"), "bot.card")
    elif bot_id is not None and not (bot_id == "default" and selected.get("feishu")):
        raise ValueError("Unknown bot: select a configured --bot-id")

    # Match the renderer's accepted boolean inputs without changing legacy
    # validation semantics elsewhere in the config loader.
    for key, fallback in (
        ("show_reasoning", True), ("stream_thinking_to_body", True),
        ("hide_completed_tool_activity", False), ("timeline_expanded", False),
    ):
        value = resolved.get(key, fallback)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                value = True
            elif normalized in {"false", "0", "no", "off"}:
                value = False
            else:
                value = fallback
        elif not isinstance(value, bool):
            if isinstance(value, (int, float)) and value in {0, 1}:
                value = bool(value)
            else:
                value = fallback
        resolved[key] = value
    for key, fallback in (
        ("max_timeline_items", 12), ("max_reasoning_chars", 1200),
        ("max_tool_result_chars", 600),
    ):
        try:
            value = int(resolved.get(key, fallback))
            resolved[key] = value if value > 0 else fallback
        except (TypeError, ValueError, OverflowError):
            resolved[key] = fallback
    # The runtime loader normalizes these at every scope.
    resolved["reasoning_format"] = str(resolved["reasoning_format"]).strip().lower()
    resolved["table_overflow_mode"] = str(resolved["table_overflow_mode"]).strip().lower()
    terminal_policy = (
        "hidden" if resolved["hide_completed_tool_activity"] else
        "failed_only" if resolved.get(HIDE_SUCCESSFUL_TOOL_ACTIVITY) else "visible"
    )
    values = {key: resolved[key] for key in READING_FIELDS}
    values["terminal_tool_activity"] = terminal_policy
    sources["terminal_tool_activity"] = (
        sources["hide_completed_tool_activity"] if resolved["hide_completed_tool_activity"]
        else sources[HIDE_SUCCESSFUL_TOOL_ACTIVITY]
    )
    return {
        "values": values,
        "sources": {key: sources[key] for key in values},
        "note": "Read-only. Scope order: global, profile, bot; explicit fields win within each scope. Restart the sidecar after editing YAML.",
    }
