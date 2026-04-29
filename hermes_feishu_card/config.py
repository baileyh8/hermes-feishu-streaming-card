from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "server": {"host": "127.0.0.1", "port": 8765},
    "feishu": {"app_id": "", "app_secret": ""},
    "bots": {"default": "default", "items": {}},
    "bindings": {
        "chats": {},
        "group_rules": {"enabled": False},
    },
    "card": {
        "max_wait_ms": 800,
        "max_chars": 240,
        "title": "Hermes Agent",
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

    config["server"]["port"] = _normalize_port(config["server"]["port"], "server.port")
    _apply_env_overrides(config)
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
    if "HERMES_FEISHU_CARD_HOST" in os.environ:
        config.setdefault("server", {})["host"] = os.environ["HERMES_FEISHU_CARD_HOST"]

    if "HERMES_FEISHU_CARD_PORT" in os.environ:
        raw_port = os.environ["HERMES_FEISHU_CARD_PORT"]
        port = _normalize_port(raw_port, "HERMES_FEISHU_CARD_PORT")
        config.setdefault("server", {})["port"] = port

    if "FEISHU_APP_ID" in os.environ:
        config.setdefault("feishu", {})["app_id"] = os.environ["FEISHU_APP_ID"]

    if "FEISHU_APP_SECRET" in os.environ:
        config.setdefault("feishu", {})["app_secret"] = os.environ["FEISHU_APP_SECRET"]


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
