from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from numbers import Real
from typing import Any, Dict, Union
from urllib.parse import quote, urlparse

import aiohttp


class FeishuAPIError(RuntimeError):
    pass


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
        if any(char.isspace() for char in self.base_url):
            raise ValueError("base_url must not contain whitespace")
        parsed_base_url = urlparse(self.base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.hostname:
            raise ValueError("base_url must be an http(s) URL with a host")
        if parsed_base_url.username or parsed_base_url.password:
            raise ValueError("base_url must not include userinfo")
        try:
            parsed_base_url.port
        except ValueError as exc:
            raise ValueError("base_url must include a valid port") from exc
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
        self._tenant_access_token: str | None = None
        self._tenant_access_token_expires_at = 0.0

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
        token = await self._tenant_token()
        payload = self.build_message_payload(chat_id, card)
        body = await self._request_json(
            "POST",
            "/im/v1/messages",
            token=token,
            params={"receive_id_type": "chat_id"},
            json_body=payload,
        )
        data = body.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("message_id"), str):
            raise FeishuAPIError("Feishu send message response missing message_id")
        return data["message_id"]

    async def update_card_message(self, message_id: str, card: Dict[str, Any]) -> None:
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("message_id is required")
        if not isinstance(card, dict):
            raise TypeError("card must be a dict")
        token = await self._tenant_token()
        content = json.dumps(card, ensure_ascii=False)
        await self._request_json(
            "PATCH",
            f"/im/v1/messages/{quote(message_id, safe='')}",
            token=token,
            json_body={"content": content},
        )

    async def _tenant_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._tenant_access_token_expires_at:
            return self._tenant_access_token

        body = await self._request_json(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            json_body={
                "app_id": self.config.app_id,
                "app_secret": self.config.app_secret,
            },
        )
        token = body.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise FeishuAPIError("Feishu token response missing tenant_access_token")
        expire = body.get("expire", 0)
        if not isinstance(expire, int) or expire <= 0:
            expire = 7200
        self._tenant_access_token = token
        self._tenant_access_token_expires_at = now + max(0, expire - 60)
        return token

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        timeout = aiohttp.ClientTimeout(total=float(self.config.timeout_seconds))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                ) as response:
                    try:
                        payload = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, json.JSONDecodeError) as exc:
                        raise FeishuAPIError(
                            f"Feishu API returned non-json response: HTTP {response.status}"
                        ) from exc
        except aiohttp.ClientError as exc:
            raise FeishuAPIError(f"Feishu API request failed: {exc.__class__.__name__}") from exc

        if not isinstance(payload, dict):
            raise FeishuAPIError("Feishu API returned non-object response")
        if response.status >= 400:
            detail = self._format_error_payload(payload)
            suffix = f": {detail}" if detail else ""
            raise FeishuAPIError(f"Feishu API HTTP {response.status}{suffix}")
        code = payload.get("code")
        if code != 0:
            msg = payload.get("msg", "")
            if not isinstance(msg, str):
                msg = ""
            msg = self._redact_sensitive_text(msg)
            raise FeishuAPIError(f"Feishu API error {code}: {msg}")
        return payload

    def _format_error_payload(self, payload: dict[str, Any]) -> str:
        parts = []
        code = payload.get("code")
        if isinstance(code, (int, str)) and not isinstance(code, bool):
            parts.append(f"code={code}")
        msg = payload.get("msg")
        if isinstance(msg, str) and msg:
            parts.append(f"msg={self._redact_sensitive_text(msg)}")
        return " ".join(parts)

    def _redact_sensitive_text(self, text: str) -> str:
        if self._tenant_access_token:
            text = text.replace(self._tenant_access_token, "[redacted-token]")
        return text
