from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .feishu_client import FeishuClient, FeishuClientConfig


BOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class BotConfig:
    bot_id: str
    name: str
    app_id: str
    app_secret: str
    base_url: str = FeishuClientConfig.base_url
    timeout_seconds: int | float = FeishuClientConfig.timeout_seconds


@dataclass(frozen=True)
class RoutingContext:
    chat_id: str
    chat_type: str = ""
    tenant_key: str = ""
    agent_id: str = ""
    profile_id: str = ""


@dataclass(frozen=True)
class RouteResult:
    bot_id: str
    reason: str


class BotRegistry:
    def __init__(
        self,
        *,
        bots: dict[str, BotConfig],
        default_bot_id: str,
        chat_bindings: dict[str, str] | None = None,
        default_reason: str = "default",
    ) -> None:
        if not bots:
            raise ValueError("at least one bot is required")

        normalized_default = _normalize_bot_id(default_bot_id)
        if normalized_default not in bots:
            raise ValueError(f"default bot {normalized_default!r} is not defined")

        self._bots = dict(bots)
        self.default_bot_id = normalized_default
        self.chat_bindings = dict(chat_bindings or {})
        self._default_reason = default_reason

        for chat_id, bot_id in self.chat_bindings.items():
            if bot_id not in self._bots:
                raise ValueError(
                    f"chat binding {chat_id!r} references unknown bot {bot_id!r}"
                )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "BotRegistry":
        feishu = config.get("feishu")
        bots_section = _mapping_or_empty(config.get("bots"))
        bindings = _mapping_or_empty(config.get("bindings"))
        items = _mapping_or_empty(bots_section.get("items"))

        bots: dict[str, BotConfig] = {}
        explicit_default_configured = False
        for raw_bot_id in items:
            if _normalize_bot_id(raw_bot_id) == "default":
                explicit_default_configured = True
                break

        if isinstance(feishu, dict) and (
            feishu.get("app_id") or feishu.get("app_secret")
        ) and not explicit_default_configured:
            bots["default"] = _bot_from_mapping("default", "Default", feishu)

        for raw_bot_id, value in items.items():
            bot_id = _normalize_bot_id(raw_bot_id)
            if bot_id in bots:
                raise ValueError(f"duplicate bot id: {bot_id}")
            if not isinstance(value, dict):
                raise ValueError(f"bot {bot_id} must be a mapping")
            bots[bot_id] = _bot_from_mapping(
                bot_id, str(value.get("name") or bot_id), value
            )

        default_bot_id, default_reason = _select_default_bot_id(bots_section, bindings)
        chat_bindings = {
            str(chat_id): _normalize_bot_id(bot_id)
            for chat_id, bot_id in _mapping_or_empty(bindings.get("chats")).items()
        }

        return cls(
            bots=bots,
            default_bot_id=default_bot_id,
            chat_bindings=chat_bindings,
            default_reason=default_reason,
        )

    def get(self, bot_id: str) -> BotConfig:
        normalized = _normalize_bot_id(bot_id)
        try:
            return self._bots[normalized]
        except KeyError as exc:
            raise KeyError(f"unknown bot: {normalized}") from exc

    def list_bots(self) -> list[BotConfig]:
        return [self._bots[bot_id] for bot_id in sorted(self._bots)]

    def resolve(self, context: RoutingContext) -> RouteResult:
        if context.chat_id in self.chat_bindings:
            return RouteResult(self.chat_bindings[context.chat_id], "bindings.chats")
        return RouteResult(self.default_bot_id, self._default_reason)

    def safe_diagnostics(self) -> dict[str, Any]:
        return {
            "default_bot": self.default_bot_id,
            "bot_count": len(self._bots),
            "chat_binding_count": len(self.chat_bindings),
            "bots": [
                {"bot_id": bot.bot_id, "name": bot.name, "app_id": bot.app_id}
                for bot in self.list_bots()
            ],
        }


class FeishuClientFactory:
    def __init__(
        self,
        registry: BotRegistry,
        client_builder: Callable[[FeishuClientConfig], Any] | None = None,
    ) -> None:
        self.registry = registry
        self._client_builder = client_builder or FeishuClient
        self._clients: dict[str, Any] = {}

    def get_client(self, bot_id: str) -> Any:
        normalized = _normalize_bot_id(bot_id)
        if normalized not in self._clients:
            bot = self.registry.get(normalized)
            self._clients[normalized] = self._client_builder(
                FeishuClientConfig(
                    app_id=bot.app_id,
                    app_secret=bot.app_secret,
                    base_url=bot.base_url,
                    timeout_seconds=bot.timeout_seconds,
                )
            )
        return self._clients[normalized]


def _select_default_bot_id(
    bots_section: dict[str, Any], bindings: dict[str, Any]
) -> tuple[str, str]:
    if bindings.get("fallback_bot"):
        return _normalize_bot_id(bindings["fallback_bot"]), "bindings.fallback_bot"
    if bots_section.get("default"):
        return _normalize_bot_id(bots_section["default"]), "bots.default"
    return "default", "default"


def _bot_from_mapping(bot_id: str, name: str, value: dict[str, Any]) -> BotConfig:
    normalized = _normalize_bot_id(bot_id)
    app_id = str(value.get("app_id") or "").strip()
    app_secret = str(value.get("app_secret") or "").strip()
    if not app_id:
        raise ValueError(f"bot {normalized} app_id is required")
    if not app_secret:
        raise ValueError(f"bot {normalized} app_secret is required")
    return BotConfig(
        bot_id=normalized,
        name=name,
        app_id=app_id,
        app_secret=app_secret,
        base_url=str(value.get("base_url") or FeishuClientConfig.base_url),
        timeout_seconds=value.get(
            "timeout_seconds", FeishuClientConfig.timeout_seconds
        ),
    )


def _normalize_bot_id(value: object) -> str:
    bot_id = str(value).strip()
    if not BOT_ID_PATTERN.fullmatch(bot_id):
        raise ValueError(f"invalid bot id: {bot_id!r}")
    return bot_id


def _mapping_or_empty(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
