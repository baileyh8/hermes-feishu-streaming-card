from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "server": {"host": "127.0.0.1", "port": 8765},
    "feishu": {"app_id": "", "app_secret": ""},
    "card": {"max_wait_ms": 800, "max_chars": 240},
}


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

    _apply_env_overrides(config)
    return config


def _merge_sections(config: dict[str, dict[str, Any]], loaded: dict[str, Any]) -> None:
    for section, value in loaded.items():
        if isinstance(value, dict) and isinstance(config.get(section), dict):
            config[section].update(value)
        else:
            config[section] = value


def _apply_env_overrides(config: dict[str, dict[str, Any]]) -> None:
    if "HERMES_FEISHU_CARD_HOST" in os.environ:
        config.setdefault("server", {})["host"] = os.environ["HERMES_FEISHU_CARD_HOST"]

    if "HERMES_FEISHU_CARD_PORT" in os.environ:
        raw_port = os.environ["HERMES_FEISHU_CARD_PORT"]
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("HERMES_FEISHU_CARD_PORT must be an integer") from exc
        config.setdefault("server", {})["port"] = port

    if "FEISHU_APP_ID" in os.environ:
        config.setdefault("feishu", {})["app_id"] = os.environ["FEISHU_APP_ID"]

    if "FEISHU_APP_SECRET" in os.environ:
        config.setdefault("feishu", {})["app_secret"] = os.environ["FEISHU_APP_SECRET"]
