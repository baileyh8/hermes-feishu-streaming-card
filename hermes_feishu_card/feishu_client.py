from __future__ import annotations

import json
import math
from dataclasses import dataclass
from numbers import Real
from typing import Any, Dict, Union
from urllib.parse import urlparse


@dataclass(frozen=True)
class FeishuClientConfig:
    app_id: str
    app_secret: str
    base_url: str = "https://open.feishu.cn/open-apis"
    timeout_seconds: Union[int, float] = 30

    def __post_init__(self) -> None:
        if not isinstance(self.app_id, str) or not self.app_id.strip():
            raise ValueError("app_id is required")
        if not isinstance(self.app_secret, str) or not self.app_secret.strip():
            raise ValueError("app_secret is required")
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("base_url is required")
        parsed_base_url = urlparse(self.base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.hostname:
            raise ValueError("base_url must be an http(s) URL with a host")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, Real)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive number")


class FeishuClient:
    def __init__(self, config: FeishuClientConfig):
        self.config = config

    def build_message_payload(self, chat_id: str, card: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("chat_id is required")
        if not isinstance(card, dict):
            raise TypeError("card must be a dict")

        return {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }

    async def send_card(self, chat_id: str, card: Dict[str, Any]) -> str:
        raise NotImplementedError(
            "send_card will be implemented with aiohttp in the integration task"
        )

    async def update_card_message(self, message_id: str, card: Dict[str, Any]) -> None:
        raise NotImplementedError(
            "update_card_message will be implemented with aiohttp in the integration task"
        )
