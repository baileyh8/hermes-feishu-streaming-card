from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class FeishuClientConfig:
    app_id: str
    app_secret: str
    base_url: str = "https://open.feishu.cn/open-apis"
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if not self.app_id:
            raise ValueError("app_id is required")
        if not self.app_secret:
            raise ValueError("app_secret is required")


class FeishuClient:
    def __init__(self, config: FeishuClientConfig):
        self.config = config

    def build_message_payload(self, chat_id: str, card: Dict[str, Any]) -> Dict[str, str]:
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
