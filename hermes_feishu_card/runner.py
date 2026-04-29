from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from .bots import BotRegistry, FeishuClientFactory, RoutingContext
from .config import load_config
from .feishu_client import FeishuClient, FeishuClientConfig
from .server import create_app


class NoopFeishuClient:
    def __init__(self) -> None:
        self._sent_count = 0

    async def send_card(self, chat_id: str, card: dict[str, Any]) -> str:
        self._sent_count += 1
        return f"noop-feishu-message-{self._sent_count}"

    async def update_card_message(self, message_id: str, card: dict[str, Any]) -> None:
        return None


@dataclass(frozen=True)
class FeishuBoundary:
    client: Any
    router: Any


def build_feishu_client(config: dict[str, Any]) -> NoopFeishuClient | FeishuClient:
    feishu = config.get("feishu", {})
    app_id = feishu.get("app_id", "")
    app_secret = feishu.get("app_secret", "")
    if not app_id or not app_secret:
        return NoopFeishuClient()

    client_config = FeishuClientConfig(
        app_id=app_id,
        app_secret=app_secret,
        base_url=feishu.get("base_url", FeishuClientConfig.base_url),
        timeout_seconds=feishu.get(
            "timeout_seconds",
            FeishuClientConfig.timeout_seconds,
        ),
    )
    return FeishuClient(client_config)


def build_feishu_boundary(config: dict[str, Any]) -> FeishuBoundary:
    registry = BotRegistry.from_config(_normalize_feishu_boundary_config(config))
    factory = FeishuClientFactory(registry)

    def router(event: Any) -> Any:
        data = getattr(event, "data", {})
        if not isinstance(data, dict):
            data = {}
        return registry.resolve(
            RoutingContext(
                chat_id=str(getattr(event, "chat_id", "") or ""),
                chat_type=str(data.get("chat_type") or ""),
                tenant_key=str(data.get("tenant_key") or ""),
                agent_id=str(data.get("agent_id") or ""),
                profile_id=str(data.get("profile_id") or ""),
            )
        )

    return FeishuBoundary(client=factory, router=router)


def _has_any_feishu_credentials(config: dict[str, Any]) -> bool:
    feishu = config.get("feishu", {})
    if isinstance(feishu, dict) and feishu.get("app_id") and feishu.get("app_secret"):
        return True

    return _has_any_named_bot_credentials(config)


def _has_any_named_bot_credentials(config: dict[str, Any]) -> bool:
    bots = config.get("bots", {})
    items = bots.get("items", {}) if isinstance(bots, dict) else {}
    if not isinstance(items, dict):
        return False
    return any(
        isinstance(bot, dict) and bot.get("app_id") and bot.get("app_secret")
        for bot in items.values()
    )


def _normalize_feishu_boundary_config(config: dict[str, Any]) -> dict[str, Any]:
    feishu = config.get("feishu", {})
    if (
        isinstance(feishu, dict)
        and _has_any_named_bot_credentials(config)
        and not (feishu.get("app_id") and feishu.get("app_secret"))
    ):
        return {**config, "feishu": {}}
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes-feishu-card-sidecar")
    parser.add_argument("--config", default="config.yaml.example")
    parser.add_argument("--token", default="")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    server = config["server"]
    if _has_any_feishu_credentials(config):
        boundary = build_feishu_boundary(config)
    else:
        boundary = FeishuBoundary(client=NoopFeishuClient(), router=None)
    web.run_app(
        create_app(
            boundary.client,
            process_token=args.token,
            card_config=config.get("card", {}),
            bot_router=boundary.router,
        ),
        host=server["host"],
        port=server["port"],
        print=None,
        access_log=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
