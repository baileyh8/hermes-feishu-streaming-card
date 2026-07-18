from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from hashlib import sha256
from numbers import Real
from typing import Any, Dict, Literal, Optional, Union
from urllib.parse import quote, urlparse

import aiohttp


_RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}
_SEND_MAX_ATTEMPTS = 3
_SEND_RETRY_DELAYS_SECONDS = (0.4, 1.2)
_MAX_RETRY_AFTER_SECONDS = 2.0
_sleep = asyncio.sleep


def _safe_api_code(value: Any) -> int | str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if (
            normalized
            and len(normalized) <= 32
            and all(char.isalnum() or char in {"_", "-"} for char in normalized)
        ):
            return normalized
    return None


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


DeliveryFailureOutcome = Literal["not_sent", "unknown"]


class FeishuAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        api_code: int | str | None = None,
        retryable: bool = False,
        outcome: DeliveryFailureOutcome = "not_sent",
        retry_after_seconds: float | None = None,
        retry_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.api_code = api_code
        self.retryable = retryable
        self.outcome = outcome
        self.retry_after_seconds = retry_after_seconds
        self.retry_count = retry_count


@dataclass(frozen=True)
class FeishuSendResult:
    message_id: str
    retry_count: int = 0


def build_delivery_uuid(
    *,
    bot_id: str,
    chat_id: str,
    reply_to_message_id: str,
    session_key: str,
    delivery_kind: str,
) -> str:
    raw = "\x1f".join(
        (bot_id, chat_id, reply_to_message_id, session_key, delivery_kind)
    ).encode("utf-8")
    return "hfc_" + sha256(raw).hexdigest()[:40]


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

    def build_message_payload(
        self,
        chat_id: str,
        card: Dict[str, Any],
        thread_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> Dict[str, str]:
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("chat_id is required")
        if not isinstance(card, dict):
            raise TypeError("card must be a dict")

        receive_id = chat_id
        if thread_id and not reply_to_message_id:
            receive_id = thread_id
        return {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }

    async def send_card(
        self,
        chat_id: str,
        card: Dict[str, Any],
        thread_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        delivery_uuid: Optional[str] = None,
    ) -> str:
        result = await self.send_card_delivery(
            chat_id,
            card,
            thread_id=thread_id,
            reply_to_message_id=reply_to_message_id,
            delivery_uuid=delivery_uuid,
        )
        return result.message_id

    async def send_card_delivery(
        self,
        chat_id: str,
        card: Dict[str, Any],
        thread_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        delivery_uuid: Optional[str] = None,
    ) -> FeishuSendResult:
        if delivery_uuid is not None:
            if not isinstance(delivery_uuid, str) or not delivery_uuid.strip():
                raise ValueError("delivery_uuid must be a non-empty string")
            if len(delivery_uuid) > 50:
                raise ValueError("delivery_uuid must not exceed 50 characters")

        payload = self.build_message_payload(
            chat_id,
            card,
            thread_id=thread_id,
            reply_to_message_id=reply_to_message_id,
        )
        if delivery_uuid is not None and not reply_to_message_id:
            payload["uuid"] = delivery_uuid

        max_attempts = _SEND_MAX_ATTEMPTS if delivery_uuid is not None else 1
        for attempt_index in range(max_attempts):
            try:
                token = await self._tenant_token()
                if reply_to_message_id:
                    body = await self._request_json(
                        "POST",
                        f"/im/v1/messages/{quote(reply_to_message_id, safe='')}/reply",
                        token=token,
                        json_body={
                            "msg_type": payload["msg_type"],
                            "content": payload["content"],
                            "reply_in_thread": bool(thread_id),
                            **(
                                {"uuid": delivery_uuid}
                                if delivery_uuid is not None
                                else {}
                            ),
                        },
                    )
                else:
                    receive_id_type = "thread_id" if thread_id else "chat_id"
                    body = await self._request_json(
                        "POST",
                        "/im/v1/messages",
                        token=token,
                        params={"receive_id_type": receive_id_type},
                        json_body=payload,
                    )
                data = body.get("data")
                if not isinstance(data, dict) or not isinstance(
                    data.get("message_id"), str
                ):
                    raise FeishuAPIError(
                        "Feishu send response missing message_id",
                        retryable=False,
                        outcome="unknown",
                    )
                return FeishuSendResult(
                    message_id=data["message_id"],
                    retry_count=attempt_index,
                )
            except FeishuAPIError as exc:
                error = FeishuAPIError(
                    str(exc),
                    status_code=exc.status_code,
                    api_code=exc.api_code,
                    retryable=exc.retryable,
                    outcome=exc.outcome,
                    retry_after_seconds=exc.retry_after_seconds,
                    retry_count=attempt_index,
                )
                if not exc.retryable or attempt_index + 1 >= max_attempts:
                    raise error from exc

                configured_delay = _SEND_RETRY_DELAYS_SECONDS[attempt_index]
                retry_after = exc.retry_after_seconds
                delay = configured_delay
                if retry_after is not None:
                    delay = min(
                        max(retry_after, configured_delay),
                        _MAX_RETRY_AFTER_SECONDS,
                    )
                await _sleep(delay)

        raise AssertionError("unreachable")

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
        headers = {"Content-Type": "application/json; charset=utf-8", "Accept-Encoding": "gzip, deflate"}
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
                        retryable = response.status in _RETRYABLE_HTTP_STATUSES
                        raise FeishuAPIError(
                            "Feishu API returned non-json response",
                            status_code=response.status,
                            retryable=retryable,
                            outcome="unknown" if retryable or response.status < 400 else "not_sent",
                            retry_after_seconds=_retry_after_seconds(
                                response.headers.get("Retry-After")
                            ),
                        ) from exc
        except FeishuAPIError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise FeishuAPIError(
                f"Feishu API request failed: {exc.__class__.__name__}",
                retryable=True,
                outcome="unknown",
            ) from exc

        if not isinstance(payload, dict):
            raise FeishuAPIError(
                "Feishu API returned non-object response",
                retryable=False,
                outcome="unknown",
            )
        if response.status >= 400:
            retryable = response.status in _RETRYABLE_HTTP_STATUSES
            raise FeishuAPIError(
                "Feishu API HTTP failure",
                status_code=response.status,
                api_code=_safe_api_code(payload.get("code")),
                retryable=retryable,
                outcome="unknown" if retryable else "not_sent",
                retry_after_seconds=_retry_after_seconds(
                    response.headers.get("Retry-After")
                ),
            )
        code = payload.get("code")
        if code != 0:
            raise FeishuAPIError(
                "Feishu API application failure",
                api_code=_safe_api_code(code),
                retryable=False,
                outcome="not_sent",
            )
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
