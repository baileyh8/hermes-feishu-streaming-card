from __future__ import annotations

import argparse
from typing import Any

from aiohttp import web

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes-feishu-card-sidecar")
    parser.add_argument("--config", default="config.yaml.example")
    parser.add_argument("--token", default="")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    server = config["server"]
    web.run_app(
        create_app(
            build_feishu_client(config),
            process_token=args.token,
            card_config=config.get("card", {}),
        ),
        host=server["host"],
        port=server["port"],
        print=None,
        access_log=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
