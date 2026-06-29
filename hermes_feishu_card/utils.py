"""Synchronous Feishu API helpers for standalone scripts.

This module provides a lightweight, dependency-free (stdlib only) interface
for sending and updating Feishu interactive cards from cron jobs, monitoring
scripts, and other standalone contexts that don't run inside the sidecar's
async event loop.

Quick start::

    from hermes_feishu_card.utils import send_card

    card = {
        "header": {"title": {"tag": "plain_text", "content": "Hello"}, "template": "green"},
        "elements": [{"tag": "markdown", "content": "It works!"}],
    }
    message_id = send_card("oc_xxxxxxxxxxxx", card)
    print(f"Sent: {message_id}")

For environments where ``hermes_feishu_card`` is not importable (e.g. scripts
running outside the Hermes venv), copy this single file — it has no internal
dependencies.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

__all__ = [
    "FeishuAPIError",
    "send_card",
    "update_card",
    "get_token",
    "load_env",
]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FeishuAPIError(RuntimeError):
    """Raised when a Feishu API call fails."""


# ---------------------------------------------------------------------------
# Module-level token cache
# ---------------------------------------------------------------------------

_token_cache: str | None = None
_token_expires_at: float = 0.0

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

_DEFAULT_ENV_PATH = os.path.expanduser("~/.hermes/.env")
_DEFAULT_BASE_URL = "https://open.feishu.cn/open-apis"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    """Parse a single ``.env`` line into a ``(key, value)`` pair.

    Handles ``export`` prefix, comments, and quoted values.  Returns ``None``
    for blank lines and comments.
    """
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
    value = raw_value.strip()
    if not value:
        return key, ""
    if value[0] in {"'", '"'}:
        quote_char = value[0]
        # strip matching quotes
        if len(value) >= 2 and value[-1] == quote_char:
            value = value[1:-1]
        else:
            value = value[1:]
    return key, value


def load_env(path: str | Path | None = None) -> Dict[str, str]:
    """Read a ``.env`` file and return a dict of key-value pairs.

    Parameters
    ----------
    path:
        Path to the ``.env`` file.  Defaults to ``~/.hermes/.env``.
    """
    env_path = Path(path) if path else Path(_DEFAULT_ENV_PATH)
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def _get_credentials(
    env_path: str | Path | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> tuple[str, str]:
    """Resolve ``(app_id, app_secret)`` from explicit args or ``.env``."""
    if app_id and app_secret:
        return app_id, app_secret
    env = load_env(env_path)
    eid = app_id or env.get("FEISHU_APP_ID", "")
    esecret = app_secret or env.get("FEISHU_APP_SECRET", "")
    if not eid or not esecret:
        raise FeishuAPIError(
            "Missing FEISHU_APP_ID / FEISHU_APP_SECRET. "
            "Pass them explicitly or set them in the .env file."
        )
    return eid, esecret


def _api_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    base_url: str = _DEFAULT_BASE_URL,
    json_body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Low-level sync HTTP request to the Feishu Open API."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    headers: Dict[str, str] = {
        "Content-Type": "application/json; charset=utf-8",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(json_body, ensure_ascii=False).encode("utf-8") if json_body else None
    req = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:
        raise FeishuAPIError(f"Feishu API request failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise FeishuAPIError("Feishu API returned non-object response")
    code = payload.get("code")
    if code != 0:
        msg = payload.get("msg", "")
        if not isinstance(msg, str):
            msg = ""
        raise FeishuAPIError(f"Feishu API error {code}: {msg}")
    return payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_token(
    env_path: str | Path | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    base_url: str = _DEFAULT_BASE_URL,
) -> str:
    """Return a cached ``tenant_access_token``, refreshing if expired.

    Parameters
    ----------
    env_path:
        Path to the ``.env`` file.  Defaults to ``~/.hermes/.env``.
    app_id:
        Explicit Feishu app ID (overrides ``.env``).
    app_secret:
        Explicit Feishu app secret (overrides ``.env``).
    base_url:
        Feishu API base URL.
    """
    global _token_cache, _token_expires_at

    now = time.time()
    if _token_cache and now < _token_expires_at:
        return _token_cache

    eid, esecret = _get_credentials(env_path, app_id, app_secret)
    body = _api_request(
        "POST",
        "/auth/v3/tenant_access_token/internal",
        base_url=base_url,
        json_body={"app_id": eid, "app_secret": esecret},
    )
    token = body.get("tenant_access_token")
    if not isinstance(token, str) or not token:
        raise FeishuAPIError("Token response missing tenant_access_token")
    expire = body.get("expire", 7200)
    if not isinstance(expire, int) or expire <= 0:
        expire = 7200

    _token_cache = token
    _token_expires_at = now + max(0, expire - 60)
    return token


def send_card(
    chat_id: str,
    card: Dict[str, Any],
    *,
    env_path: str | Path | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    base_url: str = _DEFAULT_BASE_URL,
    timeout: int = 30,
) -> str:
    """Send an interactive card to a Feishu chat.

    Parameters
    ----------
    chat_id:
        Target chat ID (e.g. ``oc_xxxxxxxxxxxx``).
    card:
        Feishu interactive card payload dict.
    env_path:
        Path to the ``.env`` file.  Defaults to ``~/.hermes/.env``.
    app_id:
        Explicit Feishu app ID (overrides ``.env``).
    app_secret:
        Explicit Feishu app secret (overrides ``.env``).
    base_url:
        Feishu API base URL.
    timeout:
        HTTP timeout in seconds.

    Returns
    -------
    str
        The ``message_id`` of the sent message.
    """
    token = get_token(env_path, app_id, app_secret, base_url)
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    body = _api_request(
        "POST",
        "/im/v1/messages",
        token=token,
        base_url=base_url,
        json_body=payload,
        timeout=timeout,
    )
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("message_id"), str):
        raise FeishuAPIError("Send response missing message_id")
    return data["message_id"]


def update_card(
    message_id: str,
    card: Dict[str, Any],
    *,
    env_path: str | Path | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    base_url: str = _DEFAULT_BASE_URL,
    timeout: int = 30,
) -> None:
    """Update (PATCH) an existing card message.

    Parameters
    ----------
    message_id:
        The message ID returned by :func:`send_card`.
    card:
        New card payload.
    env_path:
        Path to the ``.env`` file.  Defaults to ``~/.hermes/.env``.
    app_id:
        Explicit Feishu app ID (overrides ``.env``).
    app_secret:
        Explicit Feishu app secret (overrides ``.env``).
    base_url:
        Feishu API base URL.
    timeout:
        HTTP timeout in seconds.
    """
    token = get_token(env_path, app_id, app_secret, base_url)
    from urllib.parse import quote

    _api_request(
        "PATCH",
        f"/im/v1/messages/{quote(message_id, safe='')}",
        token=token,
        base_url=base_url,
        json_body={"content": json.dumps(card, ensure_ascii=False)},
        timeout=timeout,
    )
