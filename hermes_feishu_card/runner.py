from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
from typing import Any

from aiohttp import web

from .bots import BotRegistry, FeishuClientFactory, RoutingContext
from .bots import resolve_card_config as _resolve_card_config
from .config import load_config, resolve_operations_hermes_root
from .event_auth import is_loopback_host
from .feishu_client import FeishuAPIError, FeishuClient, FeishuClientConfig
from .server import create_app
from .operations_transport import ensure_transport_root_secret


logger = logging.getLogger(__name__)


class NoopFeishuClient:
    async def send_card(
        self,
        chat_id: str,
        card: dict[str, Any],
        thread_id: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> str:
        raise FeishuAPIError(
            "Feishu delivery is disabled in no-op mode",
            retryable=False,
            outcome="not_sent",
        )

    async def update_card_message(self, message_id: str, card: dict[str, Any]) -> None:
        raise FeishuAPIError(
            "Feishu delivery is disabled in no-op mode",
            retryable=False,
            outcome="not_sent",
        )


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


def resolve_card_config(
    global_card: dict[str, Any] | None,
    profile_card: dict[str, Any] | None,
    bot_card: dict[str, Any] | None,
) -> dict[str, Any]:
    return _resolve_card_config(global_card, profile_card, bot_card)


def build_feishu_boundary(config: dict[str, Any]) -> FeishuBoundary:
    profiles = config.get("profiles")
    if isinstance(profiles, dict) and profiles:
        return _build_multi_profile_boundary(config, profiles)

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


def _build_multi_profile_boundary(
    config: dict[str, Any], profiles: dict[str, Any]
) -> FeishuBoundary:
    """Build a FeishuBoundary with per-profile clients and router."""
    factories: dict[str, FeishuClientFactory] = {}
    for profile_id, profile_cfg in profiles.items():
        sub_config = {**config, **profile_cfg}
        registry = BotRegistry.from_config(
            _normalize_feishu_boundary_config(sub_config)
        )
        factories[profile_id] = FeishuClientFactory(
            registry,
            profile_card=profile_cfg.get("card") if isinstance(profile_cfg, dict) else {},
        )

    def profile_router(event: Any) -> Any:
        """Route event to the appropriate profile's bot."""
        profile_id = "default"
        data = getattr(event, "data", {})
        if isinstance(data, dict):
            profile_id = str(data.get("profile_id") or "default")
        if profile_id not in factories:
            profile_id = "default"
        registry = factories[profile_id].registry
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

    return FeishuBoundary(client=factories, router=profile_router)


def _has_any_feishu_credentials(config: dict[str, Any]) -> bool:
    feishu = config.get("feishu", {})
    if isinstance(feishu, dict) and feishu.get("app_id") and feishu.get("app_secret"):
        return True

    if _has_any_named_bot_credentials(config):
        return True

    profiles = config.get("profiles")
    if isinstance(profiles, dict):
        return any(
            _has_any_feishu_credentials(profile_cfg)
            for profile_cfg in profiles.values()
            if isinstance(profile_cfg, dict)
        )

    return False


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


def _card_config_for_server(config: dict[str, Any]) -> dict[str, Any]:
    card_config = dict(config.get("card", {}))
    mode = str(card_config.get("interaction_mode") or "callback").strip().lower()
    if mode == "auto":
        card_config["interaction_mode"] = "callback"
    elif mode not in {"callback", "text", "markdown", "reply"}:
        card_config["interaction_mode"] = "callback"
    return card_config
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes-feishu-card-sidecar")
    parser.add_argument("--config", default="config.yaml.example")
    parser.add_argument("--env-file")
    parser.add_argument("--token", default="")
    args = parser.parse_args(argv)

    config = (
        load_config(args.config, env_file=args.env_file)
        if args.env_file is not None
        else load_config(args.config)
    )
    server = config["server"]
    allow_non_loopback = server.get("allow_non_loopback", False)
    if not isinstance(allow_non_loopback, bool):
        raise ValueError("server.allow_non_loopback must be a boolean")
    event_auth_required = not is_loopback_host(str(server["host"]))
    if event_auth_required and not allow_non_loopback:
        raise ValueError(
            "non-loopback sidecar binding requires "
            "server.allow_non_loopback: true"
        )
    try:
        operations_transport_root_secret = ensure_transport_root_secret()
    except OSError:
        operations_transport_root_secret = None
    if event_auth_required and operations_transport_root_secret is None:
        raise RuntimeError(
            "non-loopback sidecar binding requires event authentication"
        )
    noop_mode = not _has_any_feishu_credentials(config)
    if not noop_mode:
        boundary = build_feishu_boundary(config)
    else:
        logger.warning(
            "No Feishu credentials found; sidecar is running in no-op delivery "
            "mode. Configure FEISHU_APP_ID and FEISHU_APP_SECRET in the selected "
            "config or env source."
        )
        boundary = FeishuBoundary(client=NoopFeishuClient(), router=None)
    web.run_app(
        create_app(
            boundary.client,
            process_token=args.token,
            card_config=_card_config_for_server(config),
            bot_router=boundary.router,
            noop_mode=noop_mode,
            operations_config_path=args.config,
            operations_env_file=args.env_file,
            operations_hermes_root=resolve_operations_hermes_root(
                config_path=args.config,
                env_file=args.env_file,
            ),
            operations_transport_root_secret=operations_transport_root_secret,
            event_auth_required=event_auth_required,
        ),
        host=server["host"],
        port=server["port"],
        print=None,
        access_log=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
