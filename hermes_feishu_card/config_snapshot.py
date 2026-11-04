"""Read-only projections of loaded card options and next-load differences."""
from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .reading import explain_reading_config
from .config_rules import READING_FIELDS, apply_chat_card_overrides

CONTROL_CARD_CONFIG_PATH = "/control/card-config"
SNAPSHOT_SCHEMA = "hfc-card-config-v1"


class ConfigSnapshotUnavailable(ValueError):
    pass


def config_path_identity(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve(strict=False)
    return sha256(str(resolved).encode("utf-8")).hexdigest()


def scope_identity(
    profile_id: str, bot_id: str, *, chat_type: str | None = None, chat_id: str | None = None,
) -> str:
    selectors: list[Any] = [profile_id, bot_id]
    if chat_type is not None or chat_id is not None:
        selectors.append({key: value for key, value in (("chat_type", chat_type), ("chat_id", chat_id)) if value is not None})
    material = json.dumps(selectors, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


def loaded_reading_values(
    base_card: dict[str, Any], boundary: Any, *, profile_id: str, bot_id: str,
    chat_type: str | None = None, chat_id: str | None = None,
) -> dict[str, Any]:
    """Read the factory's existing merge result, without making a Feishu client.

    These are defaults for new turns. Per-event overrides and already accepted
    sessions intentionally remain outside this configuration snapshot.
    """
    factory = boundary
    if isinstance(boundary, dict):
        if profile_id not in boundary:
            raise ConfigSnapshotUnavailable("scope_unavailable")
        factory = boundary[profile_id]
    elif profile_id != "default":
        raise ConfigSnapshotUnavailable("scope_unavailable")
    resolver = getattr(factory, "card_config_for_bot", None)
    if callable(resolver):
        try:
            effective = resolver(bot_id, base_card=base_card, profile_card={})
        except (KeyError, ValueError, TypeError) as exc:
            raise ConfigSnapshotUnavailable("scope_unavailable") from exc
    elif bot_id == "default":
        effective = base_card
    else:
        raise ConfigSnapshotUnavailable("scope_unavailable")
    if not isinstance(effective, dict):
        raise ConfigSnapshotUnavailable("scope_unavailable")
    effective = apply_chat_card_overrides(effective, chat_type=chat_type, chat_id=chat_id)
    return dict(explain_reading_config({"card": effective})["values"])


def _effective_mode(value: Any) -> str:
    return "text" if isinstance(value, str) and value in {"text", "markdown", "reply"} else "callback"


def validated_reading_values(value: object) -> dict[str, Any] | None:
    """Reuse runtime normalization and the explanation allowlist for responses."""
    from .config_rules import normalize_card_config

    if not isinstance(value, dict) or set(value) != {*READING_FIELDS, "terminal_tool_activity", "presentation"}:
        return None
    if value.get("terminal_tool_activity") not in ("visible", "failed_only", "unsuccessful_only"):
        return None
    if value.get("presentation") not in ("classic", "task"):
        return None
    try:
        card = copy.deepcopy({field: value[field] for field in READING_FIELDS})
        normalize_card_config(card, path="card")
        projected = explain_reading_config({"card": card})["values"]
    except (TypeError, ValueError, OverflowError):
        return None
    if any(projected[field] != value[field] for field in READING_FIELDS):
        return None
    projected["terminal_tool_activity"] = value["terminal_tool_activity"]
    projected["presentation"] = value["presentation"]
    return dict(projected)


def compare_reading_values(
    next_load: dict[str, Any], loaded: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare rendered values, not syntax aliases such as auto/callback."""
    differences = []
    for field in (*READING_FIELDS, "terminal_tool_activity", "presentation"):
        before, after = loaded.get(field), next_load.get(field)
        if field == "interaction_mode":
            before, after = _effective_mode(before), _effective_mode(after)
        if before != after:
            differences.append({"field": field, "loaded": before, "next_load": after})
    return differences


def minimal_reading_config(
    raw: dict[str, Any], *, profile_id: str | None = None, bot_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return a minimal reading-field replacement for the selected YAML scope.

    Every deletion is checked by the existing resolver in the unchanged parent
    scopes. This is not a second merge algorithm or a standalone configuration;
    unrelated fields, credentials and scope identifiers are never exported.
    """
    report = explain_reading_config(
        raw, profile_id=profile_id, bot_id=bot_id, include_scope=True,
    )
    expected = report["values"]
    selected = report["_selected_scope"]
    working = copy.deepcopy(raw)
    scope = working
    scope_kind = "global"
    profiles = working.get("profiles")
    if isinstance(profiles, dict) and profiles:
        scope = profiles[selected["profile_id"]]
        scope_kind = "profile"
    bots = scope.get("bots") or {}
    items = bots.get("items") or {}
    if selected["bot_id"] in items:
        scope = items[selected["bot_id"]]
        scope_kind = "bot"

    original_card = scope.get("card") or {}
    # Keep unrelated card options only inside the in-memory round-trip context.
    retained = {key: value for key, value in original_card.items() if key not in READING_FIELDS}
    candidate = {field: copy.deepcopy(expected[field]) for field in READING_FIELDS}
    if expected["terminal_tool_activity"] == "failed_only":
        # An explicit legacy False disables the preset's derived successful-only
        # hiding, even though the public boolean itself also defaults to False.
        candidate.pop("hide_completed_tool_activity")

    def reproduces(options: dict[str, Any]) -> bool:
        scope["card"] = {**retained, **options}
        projected = explain_reading_config(
            working, profile_id=selected["profile_id"], bot_id=bot_id,
        )
        return bool(projected["values"] == expected)

    if not reproduces(candidate):
        raise ValueError("Reading options cannot be represented safely in YAML.")
    for field in READING_FIELDS:
        if field == "reading_preset" or field not in candidate:
            continue
        if field == "text_sizes":
            # A role override must survive if it cancels an inherited size.
            for role in list(candidate[field]):
                trial = copy.deepcopy(candidate)
                del trial[field][role]
                if reproduces(trial):
                    candidate = trial
        trial = copy.deepcopy(candidate)
        del trial[field]
        if reproduces(trial):
            candidate = trial
    return scope_kind, {"card": candidate}
