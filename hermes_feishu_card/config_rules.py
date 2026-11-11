"""Pure configuration defaults, normalization and scope-merge rules.

File/env loading belongs to config; reporting belongs to reading; runtime chat
policy belongs to delivery_policy. These rules do not import any of those owners.
Normalization retains the existing in-place contract for caller-owned mappings.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

# This derived field is internal: the legacy hide switch still covers both
# successful and failed terminals, including an explicit False override.
HIDE_SUCCESSFUL_TOOL_ACTIVITY = "_hide_successful_tool_activity"
PRESENTATION_MODE = "_presentation_mode"
# Live tool activity in the card: ``full`` keeps the current per-tool rows,
# ``summary`` shows only generic progress, ``hidden`` drops the live surfaces.
# Terminal rendering stays governed by the hide-completed switches so this
# setting cannot change a finished card.
TOOL_ACTIVITY_MODES = ("full", "summary", "hidden")
READING_PRESETS = {
    "classic": {
        PRESENTATION_MODE: "classic",
        "show_reasoning": True,
        "stream_thinking_to_body": True,
        "hide_completed_tool_activity": False,
        "reasoning_format": "panel",
        "timeline_expanded": False,
        HIDE_SUCCESSFUL_TOOL_ACTIVITY: False,
    },
    "focused": {
        PRESENTATION_MODE: "classic",
        "show_reasoning": True,
        "stream_thinking_to_body": False,
        "hide_completed_tool_activity": False,
        "reasoning_format": "panel",
        "timeline_expanded": False,
        HIDE_SUCCESSFUL_TOOL_ACTIVITY: True,
    },
    "detailed": {
        PRESENTATION_MODE: "classic",
        "show_reasoning": True,
        "stream_thinking_to_body": False,
        "hide_completed_tool_activity": False,
        "reasoning_format": "panel",
        "timeline_expanded": True,
        HIDE_SUCCESSFUL_TOOL_ACTIVITY: False,
    },
    "task": {
        PRESENTATION_MODE: "task",
        "show_reasoning": True,
        "stream_thinking_to_body": False,
        "hide_completed_tool_activity": False,
        "reasoning_format": "panel",
        "timeline_expanded": True,
        HIDE_SUCCESSFUL_TOOL_ACTIVITY: True,
    },
}
READING_FIELDS = (
    "reading_preset", "show_reasoning", "stream_thinking_to_body",
    "hide_completed_tool_activity", "reasoning_format", "timeline_expanded",
    "tool_activity",
    "max_timeline_items", "max_reasoning_chars", "max_tool_result_chars",
    "table_overflow_mode",
    "native_tables",
    "over_limit",
    "timeline_order", "timeline_tools_per_reasoning", "thinking_body_tail_chars",
    "width_mode", "text_sizes", "footer_fields", "interaction_mode", "streaming_mode",
)


def normalize_reading_preset(value: object, *, path: str = "card.reading_preset") -> str:
    if not isinstance(value, str) or value.strip().lower() not in READING_PRESETS:
        raise ValueError(f"{path} must be classic, focused, detailed or task")
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


DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
        "allow_non_loopback": False,
    },
    "feishu": {"app_id": "", "app_secret": ""},
    "profiles": {},
    "bots": {"default": "default", "items": {}},
    "bindings": {
        "chats": {},
        "native_chats": [],
        "card_chats": [],
        "default_delivery": "card",
        "group_rules": {"enabled": False},
    },
    # Missing values stay notification-only for upgraded existing configs.
    # New setup templates explicitly write ``safe`` after validation.
    "integrity": {"mode": "notify"},
    "service": {"manager": "auto"},
    "card": {
        "max_wait_ms": 800,
        "max_chars": 240,
        "flush_interval_ms": 200,
        "final_drain_timeout_ms": 900,
        "title": "Hermes Agent",
        "interaction_mode": "auto",
        "streaming_mode": False,
        "show_reasoning": True,
        "stream_thinking_to_body": True,
        "thinking_body_tail_chars": 0,
        "hide_completed_tool_activity": False,
        "tool_activity": "full",
        "reasoning_format": "panel",
        "timeline_expanded": False,
        "timeline_order": "newest_first",
        "timeline_tools_per_reasoning": 0,
        "max_timeline_items": 12,
        "max_reasoning_chars": 1200,
        "max_tool_result_chars": 600,
        "table_overflow_mode": "compact",
        "native_tables": False,
        # Over-limit terminal strategy: "native" keeps the limit handoff
        # (placeholder card + full native message); "segmented" opts into a
        # main card plus bounded continuation cards, falling back to the same
        # handoff whenever a segment cannot be planned or delivered.
        "over_limit": "native",
        "completion_notify": {
            "enabled": False,
            # Seconds a turn must last before the completion notification is
            # pushed. 0 (default) keeps the notification on every turn.
            "min_duration_seconds": 0,
        },
        # Per-kind @ mention switches in interaction cards (clarify /
        # approval). The default is UNSET (None): the resolving helpers fall
        # back to the legacy global ``mentions_in_cards`` key (when present)
        # and then to enabled. Injecting an explicit default mapping here
        # would shadow ``mentions_in_cards: false`` after the config merge.
        "interaction_mentions": None,
        "footer_fields": [
            "duration",
            "model",
            "input_tokens",
            "output_tokens",
            "context",
        ],
    },
}
KNOWN_SECTIONS = frozenset(DEFAULT_CONFIG)
CARD_TEXT_SIZE_VALUES = frozenset(
    {
        "heading-0",
        "heading-1",
        "heading-2",
        "heading-3",
        "heading-4",
        "heading",
        "normal",
        "notation",
        "xxxx-large",
        "xxx-large",
        "xx-large",
        "x-large",
        "large",
        "medium",
        "small",
        "x-small",
    }
)
CARD_TEXT_SIZE_DEFAULTS = {
    "body": "normal",
    "reasoning": "small",
    "tool": "x-small",
    "notice": "x-small",
    "footer": "x-small",
}
CARD_TEXT_SIZE_DEVICE_KEYS = frozenset({"default", "pc", "mobile"})
CARD_TABLE_OVERFLOW_MODES = frozenset({"compact", "truncate"})
CARD_OVER_LIMIT_MODES = frozenset({"native", "segmented"})
SERVICE_MANAGER_VALUES = frozenset(
    {"auto", "systemd-user", "systemd-system", "detached"}
)


def normalize_text_sizes(
    value: object, *, path: str = "card.text_sizes"
) -> dict[str, str | dict[str, str]]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a mapping")
    normalized: dict[str, str | dict[str, str]] = {}
    for raw_role, raw_size in value.items():
        role = str(raw_role)
        role_path = f"{path}.{role}"
        if role not in CARD_TEXT_SIZE_DEFAULTS:
            raise ValueError(f"{role_path} is not a supported text size role")
        if isinstance(raw_size, str):
            normalized[role] = _normalize_text_size_value(raw_size, role_path)
            continue
        if not isinstance(raw_size, Mapping) or not raw_size:
            raise ValueError(f"{role_path} must be a text size or non-empty mapping")
        device_values: dict[str, str] = {}
        for raw_device, raw_device_size in raw_size.items():
            device = str(raw_device)
            device_path = f"{role_path}.{device}"
            if device not in CARD_TEXT_SIZE_DEVICE_KEYS:
                raise ValueError(f"{device_path} is not a supported device field")
            device_values[device] = _normalize_text_size_value(
                raw_device_size, device_path
            )
        fallback = device_values.get("default", CARD_TEXT_SIZE_DEFAULTS[role])
        normalized[role] = {
            "default": fallback,
            "pc": device_values.get("pc", fallback),
            "mobile": device_values.get("mobile", fallback),
        }
    return normalized


def normalize_table_overflow_mode(
    value: object, *, path: str = "card.table_overflow_mode"
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be compact or truncate")
    normalized = value.strip().lower()
    if normalized not in CARD_TABLE_OVERFLOW_MODES:
        raise ValueError(f"{path} must be compact or truncate")
    return normalized


def normalize_over_limit_mode(
    value: object, *, path: str = "card.over_limit"
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be native or segmented")
    normalized = value.strip().lower()
    if normalized not in CARD_OVER_LIMIT_MODES:
        raise ValueError(f"{path} must be native or segmented")
    return normalized


def normalize_width_mode(value: object, *, path: str = "card.width_mode") -> str:
    if not isinstance(value, str) or value.strip().lower() not in {"default", "compact", "fill"}:
        raise ValueError(f"{path} must be default, compact or fill")
    return value.strip().lower()


def normalize_tool_activity(
    value: object, *, path: str = "card.tool_activity"
) -> str:
    if not isinstance(value, str) or value.strip().lower() not in TOOL_ACTIVITY_MODES:
        raise ValueError(f"{path} must be full, summary or hidden")
    return value.strip().lower()


# Only display fields may be overlaid per chat. Delivery, credentials, routing
# and runtime ownership stay in their existing scopes.
CHAT_CARD_FIELDS = frozenset({
    "tool_activity", "show_reasoning", "stream_thinking_to_body",
    "hide_completed_tool_activity",
})
_GROUP_CHAT_TYPES = frozenset({"group", "forum", "channel", "thread"})
_P2P_CHAT_TYPES = frozenset({"p2p", "dm", "private"})


def card_chat_kind(chat_type: object) -> str:
    """Normalize Feishu chat types; an unknown type has no type-level overlay."""
    normalized = str(chat_type or "").strip().lower() if isinstance(chat_type, str) else ""
    if normalized in _GROUP_CHAT_TYPES:
        return "group"
    return "p2p" if normalized in _P2P_CHAT_TYPES else ""


def normalize_default_delivery(
    value: object, *, path: str = "bindings.default_delivery"
) -> str | dict[str, str]:
    """Scalar card/native, or a per-chat-type mapping. Unset stays card."""
    if value is None:
        return "card"
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"card", "native"}:
            return normalized
        raise ValueError(f"{path} must be card or native")
    if not isinstance(value, Mapping) or set(value) - {"group", "p2p"}:
        raise ValueError(f"{path} must be card, native, or a group/p2p mapping")
    result: dict[str, str] = {}
    for kind, raw in value.items():
        if not isinstance(raw, str) or raw.strip().lower() not in {"card", "native"}:
            raise ValueError(f"{path}.{kind} must be card or native")
        result[str(kind)] = raw.strip().lower()
    return result


def normalize_bindings(value: dict[str, Any], *, path: str = "bindings") -> None:
    """Normalize delivery bindings in place and reject ambiguous single chats."""
    native = normalize_native_chats(value.get("native_chats", []), path=f"{path}.native_chats")
    cards = normalize_native_chats(value.get("card_chats", []), path=f"{path}.card_chats")
    if set(native) & set(cards):
        raise ValueError(f"{path}: chat id cannot be in both native_chats and card_chats")
    value["native_chats"] = native
    value["card_chats"] = cards
    value["default_delivery"] = normalize_default_delivery(
        value.get("default_delivery"), path=f"{path}.default_delivery"
    )


def _normalize_chat_fields(fields: object, *, path: str) -> dict[str, Any]:
    if not isinstance(fields, Mapping) or set(fields) - CHAT_CARD_FIELDS:
        raise ValueError(f"{path} contains unsupported display fields")
    resolved = copy.deepcopy(dict(fields))
    normalize_card_config(resolved, path=path)
    for key in CHAT_CARD_FIELDS - {"tool_activity"}:
        if key in resolved:
            resolved[key] = normalize_boolean(resolved[key], f"{path}.{key}")
    return resolved


def normalize_chat_overrides(
    value: object, *, path: str = "card.chat_overrides"
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - {"group", "p2p", "chats"}:
        raise ValueError(f"{path} must contain only group, p2p and chats")
    resolved: dict[str, Any] = {}
    for kind, entries in value.items():
        overlay_path = f"{path}.{kind}"
        if not isinstance(entries, Mapping):
            raise ValueError(f"{overlay_path} must be a mapping")
        if kind != "chats":
            resolved[kind] = _normalize_chat_fields(entries, path=overlay_path)
            continue
        chats: dict[str, Any] = {}
        for raw_chat_id, fields in entries.items():
            chat_id = normalize_native_chats([raw_chat_id], path=overlay_path)[0]
            if chat_id in chats:
                raise ValueError(f"{overlay_path} contains a duplicate chat id")
            chats[chat_id] = _normalize_chat_fields(fields, path=overlay_path)
        resolved[kind] = chats
    return resolved


def apply_chat_card_overrides(
    card: Mapping[str, Any], *, chat_type: object = None, chat_id: str | None = None,
) -> dict[str, Any]:
    """Resolve display overlays once for a turn, without retaining selectors."""
    resolved = copy.deepcopy(dict(card))
    overlays = resolved.pop("chat_overrides", {})
    if not isinstance(overlays, Mapping):
        return resolved
    type_overlay = overlays.get(card_chat_kind(chat_type), {})
    chat_overlay = overlays.get("chats", {}).get(chat_id, {}) if chat_id else {}
    for overlay in (type_overlay, chat_overlay):
        if isinstance(overlay, Mapping) and overlay:
            resolved = merge_card_config(resolved, overlay)
    return resolved


def card_width_config(width_mode: str = "default") -> dict[str, str]:
    """JSON 2.0 width settings; default preserves the client's existing layout."""
    mode = normalize_width_mode(width_mode)
    return {} if mode == "default" else {"width_mode": mode}


def merge_card_config(
    base: Mapping[str, Any] | None,
    override: Mapping[str, Any] | None,
) -> dict[str, Any]:
    resolved = copy.deepcopy(dict(base or {}))
    incoming = expand_reading_preset(override)
    has_incoming_sizes = "text_sizes" in incoming
    incoming_sizes = incoming.pop("text_sizes", None)
    if "chat_overrides" in incoming:
        normalized = normalize_chat_overrides(incoming.pop("chat_overrides"))
        existing = resolved.get("chat_overrides")
        overlays = copy.deepcopy(dict(existing)) if isinstance(existing, Mapping) else {}
        for kind, overlay in normalized.items():
            if kind == "chats":
                chats = overlays.setdefault("chats", {})
                for chat_id, fields in overlay.items():
                    chats[chat_id] = {**chats.get(chat_id, {}), **copy.deepcopy(fields)}
            else:
                overlays[kind] = {**overlays.get(kind, {}), **copy.deepcopy(overlay)}
        resolved["chat_overrides"] = overlays
    resolved.update(incoming)
    if has_incoming_sizes:
        if isinstance(incoming_sizes, Mapping):
            existing_sizes = resolved.get("text_sizes")
            sizes = (
                copy.deepcopy(dict(existing_sizes))
                if isinstance(existing_sizes, Mapping)
                else {}
            )
            sizes.update(copy.deepcopy(dict(incoming_sizes)))
            resolved["text_sizes"] = sizes
        else:
            resolved["text_sizes"] = copy.deepcopy(incoming_sizes)
    return resolved


def _mention_switch_value(raw: object, *, path: str) -> bool | None:
    """Normalize a mention switch to bool, or ``None`` when unset/malformed.

    ``None`` keeps the caller on the next resolution step, so malformed
    values degrade gracefully instead of crashing card rendering.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        try:
            return normalize_boolean(raw, path)
        except ValueError:
            return None
    return None


def card_interaction_mention_enabled(
    card_config: Mapping[str, Any] | None, *, kind: str
) -> bool:
    """Whether interaction cards may @ mention the requester for a given kind.

    Resolution order:

    1. Legacy global ``card.mentions_in_cards`` when explicitly ``false``:
       THE GLOBAL OFF SWITCH — it disables every @ mention regardless of
       per-kind settings (so ``mentions_in_cards: false`` always wins).
    2. ``card.interaction_mentions.<kind>`` when the mapping is present and
       the key resolves to a boolean (explicit per-kind switch; an absent
       kind falls through to the global / default).
    3. Legacy global ``card.mentions_in_cards`` when explicitly ``true``.
    4. Default: enabled for ``approval`` / ``clarify`` kinds, disabled for
       anything else (defense in depth: callers should gate on kind too).
    """
    if not isinstance(card_config, Mapping):
        return _default_interaction_mention(kind)
    legacy = _mention_switch_value(
        card_config.get("mentions_in_cards"),
        path="card.mentions_in_cards",
    )
    if legacy is False:
        return False
    raw_mentions = card_config.get("interaction_mentions")
    if isinstance(raw_mentions, Mapping) and kind in raw_mentions:
        resolved = _mention_switch_value(
            raw_mentions.get(kind),
            path=f"card.interaction_mentions.{kind}",
        )
        if resolved is not None:
            return resolved
    if legacy is True:
        return True
    return _default_interaction_mention(kind)


def _default_interaction_mention(kind: str) -> bool:
    return kind in {"approval", "clarify"}


def card_completion_mention_enabled(
    card_config: Mapping[str, Any] | None,
) -> bool:
    """Whether the completion notification may @ mention the requester.

    Resolution order:

    1. Legacy global ``card.mentions_in_cards`` when explicitly ``false``:
       THE GLOBAL OFF SWITCH.
    2. ``card.completion_notify.mention`` when set and boolean (explicit).
    3. Legacy global ``card.mentions_in_cards`` when explicitly ``true``.
    4. Default: enabled (matches the pre-configuration behaviour).
    """
    if not isinstance(card_config, Mapping):
        return True
    legacy = _mention_switch_value(
        card_config.get("mentions_in_cards"),
        path="card.mentions_in_cards",
    )
    if legacy is False:
        return False
    notify = card_config.get("completion_notify")
    if isinstance(notify, Mapping) and "mention" in notify:
        resolved = _mention_switch_value(
            notify.get("mention"),
            path="card.completion_notify.mention",
        )
        if resolved is not None:
            return resolved
    if legacy is True:
        return True
    return True


def _normalize_text_size_value(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a supported text size")
    normalized = value.strip()
    if normalized not in CARD_TEXT_SIZE_VALUES:
        raise ValueError(f"{path} must be a supported text size")
    return normalized


def normalize_config_card_options(config: dict[str, Any]) -> None:
    normalize_card_config(config.get("card"), path="card")
    _normalize_bot_card_configs(config.get("bots"), path="bots")
    profiles = config.get("profiles")
    if not isinstance(profiles, Mapping):
        return
    for profile_id, profile in profiles.items():
        if not isinstance(profile, Mapping):
            continue
        profile_path = f"profiles.{profile_id}"
        normalize_card_config(profile.get("card"), path=f"{profile_path}.card")
        _normalize_bot_card_configs(profile.get("bots"), path=f"{profile_path}.bots")


def _normalize_bot_card_configs(value: object, *, path: str) -> None:
    if not isinstance(value, Mapping):
        return
    items = value.get("items")
    if not isinstance(items, Mapping):
        return
    for bot_id, bot in items.items():
        if not isinstance(bot, Mapping):
            continue
        normalize_card_config(
            bot.get("card"), path=f"{path}.items.{bot_id}.card"
        )


def normalize_card_config(value: object, *, path: str) -> None:
    if not isinstance(value, dict):
        return
    if "chat_overrides" in value:
        value["chat_overrides"] = normalize_chat_overrides(
            value["chat_overrides"], path=f"{path}.chat_overrides"
        )
    if "reading_preset" in value:
        value["reading_preset"] = normalize_reading_preset(
            value["reading_preset"], path=f"{path}.reading_preset"
        )
    if "width_mode" in value:
        value["width_mode"] = normalize_width_mode(
            value["width_mode"], path=f"{path}.width_mode"
        )
    if "text_sizes" in value:
        value["text_sizes"] = normalize_text_sizes(
            value["text_sizes"], path=f"{path}.text_sizes"
        )
    if "table_overflow_mode" in value:
        value["table_overflow_mode"] = normalize_table_overflow_mode(
            value["table_overflow_mode"], path=f"{path}.table_overflow_mode"
        )
    if "tool_activity" in value:
        value["tool_activity"] = normalize_tool_activity(
            value["tool_activity"], path=f"{path}.tool_activity"
        )
    if "over_limit" in value:
        value["over_limit"] = normalize_over_limit_mode(
            value["over_limit"], path=f"{path}.over_limit"
        )
    if "native_tables" in value:
        value["native_tables"] = normalize_boolean(
            value["native_tables"], f"{path}.native_tables"
        )
    if "streaming_mode" in value:
        value["streaming_mode"] = normalize_boolean(
            value["streaming_mode"], f"{path}.streaming_mode"
        )
    if "reasoning_format" in value:
        raw_format = value["reasoning_format"]
        if not isinstance(raw_format, str) or raw_format.strip().lower() not in {"panel", "code"}:
            raise ValueError(f"{path}.reasoning_format must be panel or code")
        value["reasoning_format"] = raw_format.strip().lower()
    if "timeline_order" in value:
        raw_order = value["timeline_order"]
        if not isinstance(raw_order, str) or raw_order.strip().lower() not in {"newest_first", "chronological"}:
            raise ValueError(f"{path}.timeline_order must be newest_first or chronological")
        value["timeline_order"] = raw_order.strip().lower()
    if "thinking_body_tail_chars" in value:
        limit = value["thinking_body_tail_chars"]
        if type(limit) is not int or not 0 <= limit <= 1_000_000:
            raise ValueError(f"{path}.thinking_body_tail_chars must be an integer from 0 to 1000000")
    if "timeline_tools_per_reasoning" in value:
        limit = value["timeline_tools_per_reasoning"]
        if type(limit) is not int or not 0 <= limit <= 100:
            raise ValueError(f"{path}.timeline_tools_per_reasoning must be an integer from 0 to 100")
    if "mentions_in_cards" in value and value["mentions_in_cards"] is not None:
        value["mentions_in_cards"] = normalize_boolean(
            value["mentions_in_cards"], f"{path}.mentions_in_cards"
        )
    if "interaction_mentions" in value and value["interaction_mentions"] is not None:
        raw_mentions = value["interaction_mentions"]
        if not isinstance(raw_mentions, Mapping):
            raise ValueError(f"{path}.interaction_mentions must be a mapping")
        normalized = {}
        for kind, raw in raw_mentions.items():
            normalized[str(kind)] = normalize_boolean(
                raw, f"{path}.interaction_mentions.{kind}"
            )
        value["interaction_mentions"] = normalized
    if "completion_notify" in value and isinstance(value["completion_notify"], dict):
        notify = value["completion_notify"]
        if "placement" in notify and (not isinstance(notify["placement"], str)
                                       or notify["placement"] not in {"card", "message"}):
            raise ValueError(f"{path}.completion_notify.placement must be card or message")
        if "mention" in notify and notify["mention"] is not None:
            notify["mention"] = normalize_boolean(
                notify["mention"], f"{path}.completion_notify.mention"
            )
        if "min_duration_seconds" in notify and notify["min_duration_seconds"] is not None:
            threshold = notify["min_duration_seconds"]
            # bool is an int subclass: reject it explicitly instead of letting
            # ``true`` silently mean one second.
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
                raise ValueError(
                    f"{path}.completion_notify.min_duration_seconds "
                    "must be a non-negative number"
                )
            # This module only imports ``copy`` from the standard library, so
            # spell out the finite check rather than importing math.
            if threshold != threshold or threshold in (float("inf"), float("-inf")):
                raise ValueError(
                    f"{path}.completion_notify.min_duration_seconds must be finite"
                )
            if threshold < 0 or threshold > 86_400:
                raise ValueError(
                    f"{path}.completion_notify.min_duration_seconds "
                    "must be between 0 and 86400"
                )


def normalize_boolean(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{name} must be a boolean")


_MAX_CHAT_ID_CHARS = 512
_WILDCARD_MARKERS = ("*", "?", "[", "]")


def normalize_native_chats(
    value: object,
    *,
    path: str = "bindings.native_chats",
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array of exact chat ids")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, raw_chat_id in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(raw_chat_id, str):
            raise ValueError(f"{item_path} must be a non-empty exact chat id")
        chat_id = raw_chat_id.strip()
        if (
            not chat_id
            or len(chat_id) > _MAX_CHAT_ID_CHARS
            or any(ord(character) < 32 or ord(character) == 127 for character in chat_id)
            or any(marker in chat_id for marker in _WILDCARD_MARKERS)
            or chat_id.lower().startswith("re:")
        ):
            raise ValueError(f"{item_path} must be a non-empty exact chat id")
        if chat_id not in seen:
            seen.add(chat_id)
            normalized.append(chat_id)
    return normalized
