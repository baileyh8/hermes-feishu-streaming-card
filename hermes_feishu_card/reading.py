"""Opt-in reading defaults and a credential-free explanation of their origins."""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .config_rules import (
    HIDE_SUCCESSFUL_TOOL_ACTIVITY as HIDE_SUCCESSFUL_TOOL_ACTIVITY,
    PRESENTATION_MODE as PRESENTATION_MODE,
    READING_PRESETS as READING_PRESETS,
    READING_FIELDS as READING_FIELDS,
    normalize_reading_preset as normalize_reading_preset,
    expand_reading_preset as expand_reading_preset,
)


def explain_reading_config(
    raw: Mapping[str, Any], *, profile_id: str | None = None, bot_id: str | None = None,
    include_scope: bool = False,
) -> dict[str, Any]:
    """Resolve the same config scopes as the runner, returning only safe values.

    Callers validate the YAML with load_config first. No credentials, paths,
    route identifiers, user titles, or arbitrary config objects are returned.
    """
    from .config_rules import DEFAULT_CONFIG, CARD_TEXT_SIZE_DEFAULTS, merge_card_config, normalize_text_sizes, normalize_width_mode
    from .card_view import TASK_TEXT_SIZE_DEFAULTS

    resolved = copy.deepcopy(DEFAULT_CONFIG["card"])
    resolved["reading_preset"] = "classic"
    resolved[PRESENTATION_MODE] = "classic"
    sources = {key: "default" for key in (*READING_FIELDS, HIDE_SUCCESSFUL_TOOL_ACTIVITY, PRESENTATION_MODE)}
    text_size_sources: dict[str, str] = {}

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
        if isinstance(card.get("text_sizes"), Mapping):
            for role in CARD_TEXT_SIZE_DEFAULTS:
                if role in card["text_sizes"]:
                    text_size_sources[role] = f"{label}.text_sizes.{role} (explicit)"
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
        ("streaming_mode", False),
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
    resolved["width_mode"] = normalize_width_mode(resolved.get("width_mode", "default"))
    presentation = "task" if resolved.get(PRESENTATION_MODE) == "task" else "classic"
    size_defaults = TASK_TEXT_SIZE_DEFAULTS if presentation == "task" else CARD_TEXT_SIZE_DEFAULTS
    resolved["text_sizes"] = {
        **size_defaults, **normalize_text_sizes(resolved.get("text_sizes") or {}),
    }
    size_source = sources[PRESENTATION_MODE] if presentation == "task" else "default"
    text_size_sources = {role: text_size_sources.get(role, size_source) for role in size_defaults}
    sources["text_sizes"] = "per-role (see text_size_sources)"
    fields = resolved.get("footer_fields")
    if not isinstance(fields, list):
        fields = DEFAULT_CONFIG["card"]["footer_fields"]
    allowed_fields = {"duration", "model", "input_tokens", "output_tokens", "context", "subscription_usage", "output_rate"}
    resolved["footer_fields"] = [key for key in fields if isinstance(key, str) and key in allowed_fields]
    mode = resolved.get("interaction_mode", "auto")
    mode = mode.strip().lower() if isinstance(mode, str) else "callback"
    resolved["interaction_mode"] = mode if mode in {"auto", "callback", "text", "markdown", "reply"} else "callback"
    from .render_options import card_render_options
    # Reuse the runtime's handling of accepted legacy booleans and limits.
    options = card_render_options(resolved)
    for key in ("show_reasoning", "stream_thinking_to_body", "hide_completed_tool_activity",
                "timeline_expanded", "max_timeline_items", "max_reasoning_chars", "max_tool_result_chars",
                "native_tables"):
        resolved[key] = options[key]
    terminal_policy = (
        "unsuccessful_only" if resolved["hide_completed_tool_activity"] else
        "failed_only" if resolved.get(HIDE_SUCCESSFUL_TOOL_ACTIVITY) else "visible"
    )
    values = {key: resolved[key] for key in READING_FIELDS}
    values["terminal_tool_activity"] = terminal_policy
    values["presentation"] = presentation
    sources["presentation"] = sources[PRESENTATION_MODE]
    sources["terminal_tool_activity"] = (
        sources["hide_completed_tool_activity"] if resolved["hide_completed_tool_activity"]
        else sources[HIDE_SUCCESSFUL_TOOL_ACTIVITY]
    )
    report = {
        "values": values,
        "sources": {key: sources[key] for key in values},
        "text_size_sources": text_size_sources,
        "effective_for": "next_load",
        "running_config": "not_checked",
        "note": "Read-only. Scope order: global, profile, bot; explicit fields win within each scope. These are next-load values, not proof of the running configuration. Restart the sidecar after editing YAML.",
    }
    if include_scope:
        # Private selector for the local control request, removed by the CLI
        # before output. Do not publish configured profile/bot identifiers.
        report["_selected_scope"] = {"profile_id": profile_id or "default", "bot_id": selected_bot}
    return report
