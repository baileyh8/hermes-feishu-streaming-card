"""Shared display redaction, independent of sessions and card rendering.

Preserve structured value redaction and unstructured flag/query handling. Callers
apply their existing normalization and clipping after this boundary.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any

_REDACTABLE_TOOL_DETAIL_KEYS = (
    "tenant_access_token",
    "app_secret",
    "chat_id",
    "open_id",
    "message_id",
    "password",
    "token",
    "secret",
)
_TOOL_DETAIL_KEY_PATTERN = (
    r"[A-Za-z0-9_]*(?:"
    + "|".join(re.escape(key) for key in _REDACTABLE_TOOL_DETAIL_KEYS)
    + r")[A-Za-z0-9_]*"
)
KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)([\"']?"
    + _TOOL_DETAIL_KEY_PATTERN
    + r"[\"']?\s*[:=]\s*)([^\s,;&}\]]+)"
)
_TOOL_DETAIL_QUOTED_REDACTION_RE = re.compile(
    r"(?is)([\"']?"
    + _TOOL_DETAIL_KEY_PATTERN
    + r"[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_TOOL_DETAIL_REDACTED = "[REDACTED]"
SECRET_FLAG_RE = re.compile(
    r"(?i)(--(?:token|password|secret|api-key|app-secret)(?:=|\s+))([^\s]+)"
)
URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|password|secret|api_key|api-key|app_secret)=)([^&#\s]+)"
)
_DIAGNOSTIC_KEY_PATTERN = (
    r"[A-Za-z0-9_-]*(?:api[_-]?key|token|password|secret|authorization|credential)"
    r"[A-Za-z0-9_-]*"
)
_DIAGNOSTIC_QUOTED_REDACTION_RE = re.compile(
    r"(?is)([\"']?"
    + _DIAGNOSTIC_KEY_PATTERN
    + r"[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_DIAGNOSTIC_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)([\"']?"
    + _DIAGNOSTIC_KEY_PATTERN
    + r"[\"']?\s*[:=]\s*)([^\s,;&}]+)"
)
AUTH_HEADER_SECRET_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:(?:bearer|negotiate|basic)\s+)?)([^\s,;&}]+)"
)
AUTH_SCHEME_SECRET_RE = re.compile(
    r"(?i)\b((?:bearer|negotiate|basic)\s+)([^\s,;&}]+)"
)
URL_USERINFO_SECRET_RE = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)([^\s/@]+)@"
)


def redact_tool_detail(text: str) -> str:
    if not text:
        return text
    structured = _parse_tool_detail(text)
    if structured is not None:
        return _dump_redacted_tool_detail(structured)
    redacted = _TOOL_DETAIL_QUOTED_REDACTION_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_TOOL_DETAIL_REDACTED}{match.group(4)}",
        text,
    )
    redacted = KEY_VALUE_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    # CLI-flag and query-string shapes (--password secret, ?token=abc) slip past the
    # key/value scanners above. The header sanitizer already sweeps them; tool detail is
    # rendered in the timeline and in the tool-activity rows too, so sweep here as well.
    redacted = SECRET_FLAG_RE.sub(r"\1[REDACTED]", redacted)
    return URL_SECRET_RE.sub(r"\1[REDACTED]", redacted)


def _redact_quoted_diagnostic(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}{_TOOL_DETAIL_REDACTED}{match.group(4)}"


def redact_diagnostic_text(text: str) -> str:
    if not text:
        return text
    redacted = redact_tool_detail(text)
    redacted = _DIAGNOSTIC_QUOTED_REDACTION_RE.sub(_redact_quoted_diagnostic, redacted)
    redacted = AUTH_HEADER_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    redacted = AUTH_SCHEME_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    redacted = URL_USERINFO_SECRET_RE.sub(r"\1[REDACTED]@", redacted)
    redacted = _DIAGNOSTIC_KEY_VALUE_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    redacted = SECRET_FLAG_RE.sub(r"\1[REDACTED]", redacted)
    return URL_SECRET_RE.sub(r"\1[REDACTED]", redacted)


def _parse_tool_detail(text: str) -> tuple[str, Any] | None:
    try:
        return ("json", _redact_tool_detail_value(json.loads(text)))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    try:
        return ("python", _redact_tool_detail_value(ast.literal_eval(text)))
    except (SyntaxError, ValueError):
        return None


def _redact_tool_detail_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_tool_detail_key(str(key)):
                redacted[key] = _TOOL_DETAIL_REDACTED
            else:
                redacted[key] = _redact_tool_detail_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_tool_detail_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_tool_detail_value(item) for item in value)
    return value


def _dump_redacted_tool_detail(parsed: tuple[str, Any]) -> str:
    format_name, value = parsed
    if format_name == "json":
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return repr(value)


def _is_sensitive_tool_detail_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _REDACTABLE_TOOL_DETAIL_KEYS)
