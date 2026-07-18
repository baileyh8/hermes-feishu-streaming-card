from __future__ import annotations

import copy
from collections.abc import Mapping
import os
from pathlib import Path
import shlex
from typing import Any

import yaml


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
        "group_rules": {"enabled": False},
    },
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
    for name in ("HERMES_DIR", "HFC_HERMES_DIR", "HERMES_AGENT_ROOT"):
        value = os.environ.get(name, "").strip()
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
    _normalize_config_text_sizes(config)
    config["server"]["port"] = _normalize_port(config["server"]["port"], "server.port")
    return config


def _normalize_config_text_sizes(config: dict[str, Any]) -> None:
    _normalize_card_text_sizes(config.get("card"), path="card")
    _normalize_bot_text_sizes(config.get("bots"), path="bots")
    profiles = config.get("profiles")
    if not isinstance(profiles, Mapping):
        return
    for profile_id, profile in profiles.items():
        if not isinstance(profile, Mapping):
            continue
        profile_path = f"profiles.{profile_id}"
        _normalize_card_text_sizes(profile.get("card"), path=f"{profile_path}.card")
        _normalize_bot_text_sizes(profile.get("bots"), path=f"{profile_path}.bots")


def _normalize_bot_text_sizes(value: object, *, path: str) -> None:
    if not isinstance(value, Mapping):
        return
    items = value.get("items")
    if not isinstance(items, Mapping):
        return
    for bot_id, bot in items.items():
        if not isinstance(bot, Mapping):
            continue
        _normalize_card_text_sizes(
            bot.get("card"), path=f"{path}.items.{bot_id}.card"
        )


def _normalize_card_text_sizes(value: object, *, path: str) -> None:
    if not isinstance(value, dict) or "text_sizes" not in value:
        return
    value["text_sizes"] = normalize_text_sizes(
        value["text_sizes"], path=f"{path}.text_sizes"
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
