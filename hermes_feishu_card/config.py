from __future__ import annotations

import copy
from collections.abc import Mapping
import os
from pathlib import Path
import shlex
from typing import Any

import yaml

from .config_rules import (
    DEFAULT_CONFIG as DEFAULT_CONFIG,
    KNOWN_SECTIONS as KNOWN_SECTIONS,
    CARD_TEXT_SIZE_VALUES as CARD_TEXT_SIZE_VALUES,
    CARD_TEXT_SIZE_DEFAULTS as CARD_TEXT_SIZE_DEFAULTS,
    CARD_TEXT_SIZE_DEVICE_KEYS as CARD_TEXT_SIZE_DEVICE_KEYS,
    CARD_TABLE_OVERFLOW_MODES as CARD_TABLE_OVERFLOW_MODES,
    SERVICE_MANAGER_VALUES as SERVICE_MANAGER_VALUES,
    normalize_text_sizes as normalize_text_sizes,
    normalize_table_overflow_mode as normalize_table_overflow_mode,
    normalize_width_mode as normalize_width_mode,
    card_width_config as card_width_config,
    merge_card_config as merge_card_config,
    card_interaction_mention_enabled as card_interaction_mention_enabled,
    card_completion_mention_enabled as card_completion_mention_enabled,
    expand_reading_preset as expand_reading_preset,
    normalize_reading_preset as normalize_reading_preset,
    normalize_native_chats as normalize_native_chats,
    normalize_bindings as normalize_bindings,
    normalize_boolean as normalize_boolean,
    normalize_config_card_options as normalize_config_card_options,
)


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
    normalize_config_card_options(config)
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
    if "shared_source" in service and type(service["shared_source"]) is not bool:
        raise ValueError("service.shared_source must be a boolean")
    if "hermes_home" in service:
        home = service["hermes_home"]
        if not isinstance(home, str) or not home.strip() or not Path(home).is_absolute():
            raise ValueError("service.hermes_home must be an absolute Hermes HOME")
    if "python_executable" in service:
        selected = service["python_executable"]
        if (not isinstance(selected, str) or not selected.strip()
                or not Path(selected).expanduser().is_absolute()):
            raise ValueError("service.python_executable must be an absolute Python path")



def _normalize_config_native_chats(config: dict[str, Any]) -> None:
    bindings = config.get("bindings")
    if isinstance(bindings, dict):
        normalize_bindings(bindings, path="bindings")
    profiles = config.get("profiles")
    if not isinstance(profiles, Mapping):
        return
    for profile_id, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        profile_bindings = profile.get("bindings")
        if not isinstance(profile_bindings, dict):
            continue
        normalize_bindings(
            profile_bindings, path=f"profiles.{profile_id}.bindings"
        )


def _merge_sections(config: dict[str, dict[str, Any]], loaded: dict[str, Any]) -> None:
    for section, value in loaded.items():
        if section in KNOWN_SECTIONS and not isinstance(value, dict):
            raise ValueError(f"Config section {section} must be a mapping")

        if section == "card" and isinstance(value, dict):
            config[section] = merge_card_config(config.get(section), value)
        elif isinstance(value, dict) and isinstance(config.get(section), dict):
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
        config.setdefault("server", {})["allow_non_loopback"] = normalize_boolean(
            values["HERMES_FEISHU_CARD_ALLOW_NON_LOOPBACK"],
            "HERMES_FEISHU_CARD_ALLOW_NON_LOOPBACK",
        )

    if "HERMES_FEISHU_CARD_HERMES_HOME" in values:
        config.setdefault("service", {})["hermes_home"] = values["HERMES_FEISHU_CARD_HERMES_HOME"]

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
