from __future__ import annotations

import copy
from collections.abc import Mapping
import os
from pathlib import Path
import shlex
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "server": {"host": "127.0.0.1", "port": 8765},
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
        "title": "Hermes Agent",
        "interaction_mode": "auto",
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


def load_config(path: str | Path) -> dict[str, dict[str, Any]]:
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
            profile_card = copy.deepcopy(config.get("card", DEFAULT_CONFIG["card"]))
            profile_card.update(raw_profile_card)
            profile_cfg.setdefault("feishu", copy.deepcopy(DEFAULT_CONFIG["feishu"]))
            profile_cfg.setdefault("bots", copy.deepcopy(DEFAULT_CONFIG["bots"]))
            profile_cfg.setdefault("bindings", copy.deepcopy(DEFAULT_CONFIG["bindings"]))
            profile_cfg["card"] = profile_card

    _apply_env_file_overrides(config, config_path)
    _apply_env_overrides(config)
    config["server"]["port"] = _normalize_port(config["server"]["port"], "server.port")
    return config


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
    dotenv_path = config_path.parent / ".env"
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
