from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from hashlib import sha256
from ipaddress import ip_address
from numbers import Real
from typing import Any, Dict, Optional, Union
from urllib.parse import quote, urlparse

import aiohttp

from .delivery import (
    DeliveryFailureOutcome as DeliveryFailureOutcome, FeishuAPIError as FeishuAPIError,
)
from .card_limits import serialize_card_for_delivery
from .message_navigation import MessageNavigation, navigation_from_response

logger = logging.getLogger(__name__)


_RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}
_SEND_MAX_ATTEMPTS = 3
_SEND_RETRY_DELAYS_SECONDS = (0.4, 1.2)
_MAX_RETRY_AFTER_SECONDS = 2.0
# Bound on the tenant user_id -> open_id memo (see FeishuClient.resolve_open_id).
_OPEN_ID_CACHE_MAX = 512
_FEISHU_OPEN_ID_RE = re.compile(r"ou_[A-Za-z0-9_-]{1,128}")
_sleep = asyncio.sleep


def _should_bypass_proxy(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").strip().lower()
    if host == "localhost":
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
    )


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


@dataclass(frozen=True)
class FeishuSendResult:
    message_id: str
    retry_count: int = 0
    navigation: MessageNavigation | None = field(default=None, repr=False)


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
        # A remote Feishu endpoint may only be reachable through HTTP(S)_PROXY,
        # which aiohttp ignores unless the session trusts the environment.  A
        # loopback or intranet endpoint must stay direct, matching how the
        # sidecar's own urllib callers bypass the proxy.
        self._trust_env = not _should_bypass_proxy(config.base_url)
        self._tenant_access_token: str | None = None
        self._tenant_access_token_expires_at = 0.0
        # tenant user_id -> app open_id ("" = looked up, unknown). One entry per
        # distinct requester; bounded so a long-lived sidecar cannot grow it.
        self._open_id_cache: dict[str, str] = {}
        from .cardkit import CardKitTransport
        self.cardkit = CardKitTransport(self)

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

        return {
            # Feishu's create API does not accept thread_id as a receive target.
            # Topic placement requires the reply API and a message anchor; when
            # no anchor exists, deliver to the parent chat instead.
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": serialize_card_for_delivery(card),
        }

    async def send_card(
        self,
        chat_id: str,
        card: Dict[str, Any],
        thread_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        delivery_uuid: Optional[str] = None,
        reply_in_thread: bool = False,
    ) -> str:
        result = await self.send_card_delivery(
            chat_id,
            card,
            thread_id=thread_id,
            reply_to_message_id=reply_to_message_id,
            reply_in_thread=reply_in_thread,
            delivery_uuid=delivery_uuid,
        )
        return result.message_id

    async def _resolve_thread_anchor(self, thread_id: str) -> str:
        """Newest-known message inside ``thread_id``, usable as a reply anchor.

        Feishu cannot address a topic directly: ``message.create`` takes a ``chat_id`` receive id
        and, in a topic group, an unanchored create becomes a NEW topic instead of landing in the
        existing one. The gateway adapter already resolves an anchor for its own sends
        (``_fetch_last_message_in_thread``); this client serves the sidecar's card deliveries
        (notice and interaction cards) and did not — so a topic-bound card that arrived without a
        reply anchor (a heartbeat/notice whose event carried ``thread_id`` but no
        ``reply_to_message_id``) started its own topic and detached from the conversation.

        Returns ``""`` on any failure so the caller keeps its previous routing, and logs why:
        the failure path used to be silent, which is why this went unnoticed for so long.
        """
        if not thread_id:
            return ""
        try:
            token = await self._tenant_token()
            body = await self._request_json(
                "GET",
                "/im/v1/messages",
                token=token,
                params={
                    "container_id_type": "thread",
                    "container_id": thread_id,
                    "page_size": "1",
                },
            )
        except Exception as exc:
            logger.warning(
                "[feishu-card] thread anchor lookup failed: thread_hash=%s error_kind=%s",
                sha256(thread_id.encode()).hexdigest()[:12], type(exc).__name__
            )
            return ""
        items = (body.get("data") or {}).get("items") or []
        for item in items:
            message_id = item.get("message_id") if isinstance(item, dict) else None
            if isinstance(message_id, str) and message_id.startswith("om_"):
                return message_id
        logger.warning(
            "[feishu-card] no anchor inside topic: thread_hash=%s; unanchored fallback",
            sha256(thread_id.encode()).hexdigest()[:12],
        )
        return ""

    async def send_card_delivery(
        self,
        chat_id: str,
        card: Dict[str, Any],
        thread_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        delivery_uuid: Optional[str] = None,
        reply_in_thread: bool = False,
    ) -> FeishuSendResult:
        if thread_id and not reply_to_message_id:
            # A topic-bound card MUST reply to a message inside the topic; without an anchor the
            # create below silently starts a new topic.
            reply_to_message_id = await self._resolve_thread_anchor(thread_id) or None
        if reply_in_thread and not reply_to_message_id:
            raise ValueError("reply_to_message_id is required for reply_in_thread")
        if delivery_uuid is not None:
            if not isinstance(delivery_uuid, str) or not delivery_uuid.strip():
                raise ValueError("delivery_uuid must be a non-empty string")
            if len(delivery_uuid) > 50:
                raise ValueError("delivery_uuid must not exceed 50 characters")

        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("chat_id is required")
        if not isinstance(card, dict):
            raise TypeError("card must be a dict")

        if (isinstance(card, dict) and card.get("schema") == "2.0"
                and isinstance(card.get("config"), dict)
                and card["config"].get("streaming_mode") is True):
            return await self.cardkit.send(
                chat_id, card, thread_id=thread_id,
                reply_to_message_id=reply_to_message_id,
                reply_in_thread=reply_in_thread, delivery_uuid=delivery_uuid,
            )

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
                            "reply_in_thread": bool(thread_id or reply_in_thread),
                            **(
                                {"uuid": delivery_uuid}
                                if delivery_uuid is not None
                                else {}
                            ),
                        },
                    )
                else:
                    body = await self._request_json(
                        "POST",
                        "/im/v1/messages",
                        token=token,
                        params={"receive_id_type": "chat_id"},
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
                    navigation=navigation_from_response(
                        data, api_base_url=self.config.base_url, chat_id=chat_id,
                        thread_id=thread_id, reply_in_thread=reply_in_thread,
                    ),
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
        if await self.cardkit.update(message_id, card):
            return
        content = serialize_card_for_delivery(card)
        token = await self._tenant_token()
        await self._request_json(
            "PATCH",
            f"/im/v1/messages/{quote(message_id, safe='')}",
            token=token,
            json_body={"content": content},
        )

    async def delete_message(self, message_id: str) -> None:
        """Recall a message the bot itself posted.

        Used to withdraw a dead approval card: once the runtime that asked for consent is gone,
        the card can never be completed, and leaving it on screen invites a second dead click.
        """
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("message_id is required")
        token = await self._tenant_token()
        await self._request_json(
            "DELETE",
            f"/im/v1/messages/{quote(message_id, safe='')}",
            token=token,
        )

    async def send_text_message(
        self,
        chat_id: str,
        text: str,
        thread_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        reply_in_thread: bool = False,
    ) -> str:
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("chat_id is required")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        if (thread_id or reply_in_thread) and (
            not isinstance(reply_to_message_id, str) or not reply_to_message_id.strip()
        ):
            # Feishu can only place a message inside a topic by replying to an
            # anchor. Without one, deliver to the chat rather than dropping it.
            thread_id = None
            reply_in_thread = False

        token = await self._tenant_token()
        content = json.dumps({"text": text}, ensure_ascii=False)
        if reply_to_message_id:
            body = await self._request_json(
                "POST",
                f"/im/v1/messages/{quote(reply_to_message_id, safe='')}/reply",
                token=token,
                json_body={
                    "msg_type": "text",
                    "content": content,
                    "reply_in_thread": bool(thread_id) or reply_in_thread,
                },
            )
        else:
            body = await self._request_json(
                "POST",
                "/im/v1/messages",
                token=token,
                params={"receive_id_type": "chat_id"},
                json_body={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": content,
                },
            )
        data = body.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("message_id"), str):
            raise FeishuAPIError(
                "Feishu send response missing message_id",
                retryable=False,
                outcome="unknown",
            )
        return data["message_id"]

    async def lookup_message_navigation(
        self, message_id: str, chat_id: str, *,
        thread_id: str | None = None, reply_in_thread: bool = False,
    ) -> MessageNavigation | None:
        """Read only the confirmed continuation message; failure is optional."""
        if not isinstance(message_id, str) or not message_id.startswith("om_") or not chat_id:
            return None

        async def lookup():
            token = await self._tenant_token()
            body = await self._request_json(
                "GET", f"/im/v1/messages/{quote(message_id, safe='')}",
                token=token, max_response_bytes=65536,
            )
            data = body.get("data")
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list) or len(items) != 1:
                return None
            item = items[0]
            if (not isinstance(item, dict) or item.get("message_id") != message_id
                    or item.get("chat_id") != chat_id or item.get("deleted") is not False):
                return None
            return navigation_from_response(
                item, api_base_url=self.config.base_url, chat_id=chat_id,
                thread_id=thread_id, reply_in_thread=reply_in_thread,
            )

        try:
            return await asyncio.wait_for(lookup(), timeout=1.5)
        except Exception:
            # A metadata read must never turn a confirmed send into a retry.
            return None

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

    async def resolve_open_id(self, user_id: str) -> str:
        """Map a tenant user_id to the app-scoped open_id ("" when unknown).

        A mention needs ``ou_`` while newer gateway events only carry the
        tenant id, so the completion notice resolves it once and caches the
        answer. Failures are non-fatal: the caller sends without the mention.
        """
        key = user_id.strip() if isinstance(user_id, str) else ""
        if not key:
            return ""
        if key.startswith("ou_"):
            return key
        cached = self._open_id_cache.get(key)
        if cached is not None:
            return cached
        if len(self._open_id_cache) >= _OPEN_ID_CACHE_MAX:
            self._open_id_cache.clear()
        open_id = ""
        try:
            token = await self._tenant_token()
            body = await self._request_json(
                "GET",
                f"/contact/v3/users/{quote(key, safe='')}",
                token=token,
                params={"user_id_type": "user_id"},
            )
            user = (body.get("data") or {}).get("user")
            candidate = user.get("open_id") if isinstance(user, dict) else None
            if isinstance(candidate, str) and _FEISHU_OPEN_ID_RE.fullmatch(candidate):
                open_id = candidate
        except Exception:
            open_id = ""
        if open_id:
            # Failures stay uncached: a timeout or 429 must not pin the
            # requester as permanently unmentionable for this process.
            self._open_id_cache[key] = open_id
        return open_id

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        max_response_bytes: int | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {"Content-Type": "application/json; charset=utf-8", "Accept-Encoding": "gzip, deflate"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        timeout = aiohttp.ClientTimeout(total=float(self.config.timeout_seconds))
        try:
            async with aiohttp.ClientSession(
                timeout=timeout, trust_env=self._trust_env
            ) as session:
                async with session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    **({"allow_redirects": False} if max_response_bytes is not None else {}),
                ) as response:
                    if max_response_bytes is not None and response.status != 200:
                        raise FeishuAPIError("Feishu metadata HTTP failure", status_code=response.status)
                    try:
                        if max_response_bytes is None:
                            payload = await response.json(content_type=None)
                        else:
                            chunks = bytearray()
                            async for chunk in response.content.iter_chunked(8192):
                                chunks.extend(chunk)
                                if len(chunks) > max_response_bytes:
                                    raise FeishuAPIError("Feishu metadata response too large", retryable=False)
                            payload = json.loads(chunks)
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
