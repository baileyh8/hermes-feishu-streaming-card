from __future__ import annotations

import copy
from collections.abc import Mapping
import os
from pathlib import Path
import shlex
from typing import Any

import yaml

from .delivery_policy import normalize_native_chats


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
        "show_reasoning": True,
        "timeline_expanded": False,
        "max_timeline_items": 12,
        "max_reasoning_chars": 1200,
        "max_tool_result_chars": 600,
        "table_overflow_mode": "compact",
        "completion_notify": {"enabled": False, "mention": True},
        # Per-kind @ mention switches in interaction cards (clarify /
        # approval). ``clarify`` covers clarify cards, ``approval`` covers
        # approval cards. Missing/null/malformed values fall back to enabled
        # for both kinds (backwards compatible: the old boolean
        # ``mentions_in_cards`` key is still honored as the global default
        # when present).
        "interaction_mentions": {
            "clarify": True,
            "approval": True,
        },
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


def merge_card_config(
    base: Mapping[str, Any] | None,
    override: Mapping[str, Any] | None,
) -> dict[str, Any]:
    resolved = copy.deepcopy(dict(base or {}))
    incoming = copy.deepcopy(dict(override or {}))
    has_incoming_sizes = "text_sizes" in incoming
    incoming_sizes = incoming.pop("text_sizes", None)
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


def card_interaction_mention_enabled(
    card_config: Mapping[str, Any] | None, *, kind: str
) -> bool:
    """Whether card content may include @ mentions for a given interaction kind.

    Controlled by ``card.interaction_mentions.clarify`` and
    ``card.interaction_mentions.approval`` (defaults: both True). Missing,
    null, or malformed values fall back to per-kind defaults.

    Backwards compatibility: the old ``card.mentions_in_cards`` boolean key is
    still honored as a global default when present -- True enables mentions
    for every interaction kind, False disables them all.
    """
    if not isinstance(card_config, Mapping):
        return _default_interaction_mention(kind)
    raw_mentions = card_config.get("interaction_mentions")
    if isinstance(raw_mentions, Mapping):
        raw = raw_mentions.get(kind)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            try:
                return _normalize_boolean(raw, f"card.interaction_mentions.{kind}")
            except ValueError:
                return _default_interaction_mention(kind)
        return _default_interaction_mention(kind)
    # Legacy global switch: maps any interaction kind onto itself.
    legacy = card_config.get("mentions_in_cards")
    if legacy is not None:
        if isinstance(legacy, bool):
            return legacy
        if isinstance(legacy, str):
            try:
                return _normalize_boolean(legacy, "card.mentions_in_cards")
            except ValueError:
                return _default_interaction_mention(kind)
    return _default_interaction_mention(kind)


def _default_interaction_mention(kind: str) -> bool:
    return True


def card_completion_mention_enabled(
    card_config: Mapping[str, Any] | None,
) -> bool:
    """Whether the completion notification may @ mention the requester.

    Controlled by ``card.completion_notify.mention`` (default True when the
    key is absent, preserving the historical behaviour). The legacy
    ``card.mentions_in_cards`` boolean is honored as the global default.
    """
    if not isinstance(card_config, Mapping):
        return True
    notify = card_config.get("completion_notify")
    if isinstance(notify, Mapping):
        raw = notify.get("mention")
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            try:
                return _normalize_boolean(raw, "card.completion_notify.mention")
            except ValueError:
                return True
        return True
    legacy = card_config.get("mentions_in_cards")
    if legacy is not None:
        if isinstance(legacy, bool):
            return legacy
        if isinstance(legacy, str):
            try:
                return _normalize_boolean(legacy, "card.mentions_in_cards")
            except ValueError:
                return True
    return True


def _normalize_text_size_value(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a supported text size")
    normalized = value.strip()
    if normalized not in CARD_TEXT_SIZE_VALUES:
        raise ValueError(f"{path} must be a supported text size")
    return normalized


def resolve_operations_hermes_root(
    explicit: str | Path | None = None,
    *,
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
) -> Path:
    """Resolve the local Hermes source root without adding user configuration."""
    if explicit:
        return Path(explicit).expanduser()
    for name in ("HERMES_DIR", "HFC_HERMES_DIR", "HERMES_AGENT_ROOT"):
        value = os.environ.get(name, "").strip()
        if value:
            return Path(value).expanduser()
    if env_file is not None:
        dotenv = _read_dotenv(Path(env_file).expanduser())
        value = dotenv.get("HERMES_DIR", "").strip()
        if value:
            return Path(value).expanduser()
    if config_path is not None:
        dotenv = _read_dotenv(Path(config_path).expanduser().parent / ".env")
        value = dotenv.get("HERMES_DIR", "").strip()
        if value:
            return Path(value).expanduser()
    current = Path.cwd()
    for candidate in (current, *current.parents):
        if (candidate / "gateway" / "run.py").is_file():
            return candidate
    return Path.home() / ".hermes" / "hermes-agent"


def load_config(
    path: str | Path,
    *,
    env_file: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config_path = Path(path).expanduser()

    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as file:
            loaded = yaml.safe_load(file)

        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError("Config top-level YAML value must be a mapping")

        _merge_sections(config, loaded)

    # 展开 profiles：将默认值 deep-merge 到每个 profile 的子配置中
    profiles = config.get("profiles")
    if isinstance(profiles, dict) and profiles:
        for profile_id, profile_cfg in profiles.items():
            if not isinstance(profile_cfg, dict):
                raise ValueError(f"profile {profile_id!r} must be a mapping")
            raw_profile_card = profile_cfg.get("card", {})
            if raw_profile_card is None:
                raw_profile_card = {}
            if not isinstance(raw_profile_card, dict):
                raise ValueError(f"profile {profile_id!r} card must be a mapping")
            profile_card = merge_card_config(
                config.get("card", DEFAULT_CONFIG["card"]), raw_profile_card
            )
            profile_cfg.setdefault("feishu", copy.deepcopy(DEFAULT_CONFIG["feishu"]))
            profile_cfg.setdefault("bots", copy.deepcopy(DEFAULT_CONFIG["bots"]))
            profile_cfg.setdefault("bindings", copy.deepcopy(DEFAULT_CONFIG["bindings"]))
            profile_cfg["card"] = profile_card

    _apply_env_file_overrides(config, config_path)
    if env_file is not None:
        selected_env_path = Path(env_file).expanduser()
        if selected_env_path != config_path.parent / ".env":
            _apply_env_path_overrides(config, selected_env_path)
    _apply_env_overrides(config)
    _normalize_config_card_options(config)
    _normalize_integrity_mode(config)
    _normalize_config_native_chats(config)
    config["server"]["port"] = _normalize_port(config["server"]["port"], "server.port")
    _validate_service_manager(config)
    return config


def _normalize_integrity_mode(config: dict[str, Any]) -> None:
    integrity = config.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("Config section integrity must be a mapping")
    raw_mode = integrity.get("mode", "notify")
    # PyYAML follows YAML 1.1 and parses an unquoted ``off`` as False.
    if raw_mode is False:
        mode = "off"
    elif isinstance(raw_mode, str):
        mode = raw_mode.strip().lower()
    else:
        mode = ""
    if mode not in {"safe", "notify", "off"}:
        raise ValueError("integrity.mode must be safe, notify, or off")
    integrity["mode"] = mode


def _validate_service_manager(config: dict[str, Any]) -> None:
    service = config.get("service")
    manager = service.get("manager") if isinstance(service, Mapping) else None
    if not isinstance(manager, str) or manager not in SERVICE_MANAGER_VALUES:
        values = ", ".join(sorted(SERVICE_MANAGER_VALUES))
        raise ValueError(f"service.manager must be one of: {values}")


def _normalize_config_native_chats(config: dict[str, Any]) -> None:
    bindings = config.get("bindings")
    if isinstance(bindings, dict):
        bindings["native_chats"] = normalize_native_chats(
            bindings.get("native_chats", []),
            path="bindings.native_chats",
        )
    profiles = config.get("profiles")
    if not isinstance(profiles, Mapping):
        return
    for profile_id, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        profile_bindings = profile.get("bindings")
        if not isinstance(profile_bindings, dict):
            continue
        profile_bindings["native_chats"] = normalize_native_chats(
            profile_bindings.get("native_chats", []),
            path=f"profiles.{profile_id}.bindings.native_chats",
        )


def _normalize_config_card_options(config: dict[str, Any]) -> None:
    _normalize_card_config(config.get("card"), path="card")
    _normalize_bot_card_configs(config.get("bots"), path="bots")
    profiles = config.get("profiles")
    if not isinstance(profiles, Mapping):
        return
    for profile_id, profile in profiles.items():
        if not isinstance(profile, Mapping):
            continue
        profile_path = f"profiles.{profile_id}"
        _normalize_card_config(profile.get("card"), path=f"{profile_path}.card")
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
        _normalize_card_config(
            bot.get("card"), path=f"{path}.items.{bot_id}.card"
        )


def _normalize_card_config(value: object, *, path: str) -> None:
    if not isinstance(value, dict):
        return
    if "text_sizes" in value:
        value["text_sizes"] = normalize_text_sizes(
            value["text_sizes"], path=f"{path}.text_sizes"
        )
    if "table_overflow_mode" in value:
        value["table_overflow_mode"] = normalize_table_overflow_mode(
            value["table_overflow_mode"], path=f"{path}.table_overflow_mode"
        )
    if "mentions_in_cards" in value:
        value["mentions_in_cards"] = _normalize_boolean(
            value["mentions_in_cards"], f"{path}.mentions_in_cards"
        )
    if "interaction_mentions" in value:
        raw_mentions = value["interaction_mentions"]
        if not isinstance(raw_mentions, dict):
            raise ValueError(f"{path}.interaction_mentions must be a mapping")
        normalized = {}
        for kind, raw in raw_mentions.items():
            normalized[str(kind)] = _normalize_boolean(
                raw, f"{path}.interaction_mentions.{kind}"
            )
        value["interaction_mentions"] = normalized
    if "completion_notify" in value and isinstance(value["completion_notify"], dict):
        notify = value["completion_notify"]
        if "mention" in notify:
            notify["mention"] = _normalize_boolean(
                notify["mention"], f"{path}.completion_notify.mention"
            )


def _merge_sections(config: dict[str, dict[str, Any]], loaded: dict[str, Any]) -> None:
    for section, value in loaded.items():
        if section in KNOWN_SECTIONS and not isinstance(value, dict):
            raise ValueError(f"Config section {section} must be a mapping")

        if isinstance(value, dict) and isinstance(config.get(section), dict):
            config[section].update(value)
        else:
            config[section] = value


def _apply_env_overrides(config: dict[str, dict[str, Any]]) -> None:
    _apply_env_mapping_overrides(config, os.environ)


def _apply_env_file_overrides(config: dict[str, dict[str, Any]], config_path: Path) -> None:
    _apply_env_path_overrides(config, config_path.parent / ".env")


def _apply_env_path_overrides(
    config: dict[str, dict[str, Any]], dotenv_path: Path
) -> None:
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return
    values = _read_dotenv(dotenv_path)
    if values:
        _apply_env_mapping_overrides(config, values)


def _apply_env_mapping_overrides(
    config: dict[str, dict[str, Any]], values: Mapping[str, str]
) -> None:
    if "HERMES_FEISHU_CARD_HOST" in values:
        config.setdefault("server", {})["host"] = values["HERMES_FEISHU_CARD_HOST"]

    if "HERMES_FEISHU_CARD_PORT" in values:
        raw_port = values["HERMES_FEISHU_CARD_PORT"]
        port = _normalize_port(raw_port, "HERMES_FEISHU_CARD_PORT")
        config.setdefault("server", {})["port"] = port

    if "HERMES_FEISHU_CARD_ALLOW_NON_LOOPBACK" in values:
        config.setdefault("server", {})["allow_non_loopback"] = _normalize_boolean(
            values["HERMES_FEISHU_CARD_ALLOW_NON_LOOPBACK"],
            "HERMES_FEISHU_CARD_ALLOW_NON_LOOPBACK",
        )

    if "HERMES_FEISHU_CARD_SERVICE_MANAGER" in values:
        config.setdefault("service", {})["manager"] = values[
            "HERMES_FEISHU_CARD_SERVICE_MANAGER"
        ]

    if "HERMES_FEISHU_CARD_INTEGRITY_MODE" in values:
        config.setdefault("integrity", {})["mode"] = values[
            "HERMES_FEISHU_CARD_INTEGRITY_MODE"
        ]

    # profiles 模式下跳过顶层 feishu 凭据的环境变量覆盖
    profiles = config.get("profiles")
    if isinstance(profiles, dict) and profiles:
        return

    if "FEISHU_APP_ID" in values:
        config.setdefault("feishu", {})["app_id"] = values["FEISHU_APP_ID"]

    if "FEISHU_APP_SECRET" in values:
        config.setdefault("feishu", {})["app_secret"] = values["FEISHU_APP_SECRET"]


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        parsed = _parse_dotenv_line(line)
        if parsed is None:
            continue
        key, value = parsed
        values[key] = value
    return values


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith("export "):
        text = text[7:].lstrip()
    if "=" not in text:
        return None
    key, raw_value = text.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, _parse_dotenv_value(raw_value)


def _parse_dotenv_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        try:
            parts = shlex.split(value, posix=True)
        except ValueError:
            return value.strip(value[0])
        if parts:
            return parts[0]
        return ""
    return value


def _normalize_port(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer from 1 to 65535")

    if isinstance(value, int):
        port = value
    elif isinstance(value, str):
        text = value.strip()
        if not text.isdecimal():
            raise ValueError(f"{name} must be an integer from 1 to 65535")
        port = int(text)
    else:
        raise ValueError(f"{name} must be an integer from 1 to 65535")

    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be in range 1..65535")
    return port


def _normalize_boolean(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{name} must be a boolean")
