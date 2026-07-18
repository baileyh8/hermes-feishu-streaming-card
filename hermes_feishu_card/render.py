from __future__ import annotations

import ast
import html
import json
import re
import time as _time
from collections.abc import Mapping
from typing import Any, Dict, Optional

from .session import CardSession
from .status import StatusConfig, resolve_display_status
from .text import normalize_stream_text, split_markdown_blocks

DEFAULT_FOOTER_FIELDS = (
    "duration",
    "model",
    "input_tokens",
    "output_tokens",
    "context",
)
MAIN_CONTENT_CHUNK_CHARS = 2400
DEFAULT_TITLE = "Hermes Agent"
RUNTIME_HEADER_MAX_CHARS = 120
TEXT_SIZE_ROLE_ORDER = ("body", "reasoning", "tool", "notice", "footer")
MODEL_COLOR_PREFIXES = (
    (("gpt-", "o1", "o3"), "blue"),
    (("claude-",), "orange"),
    (("deepseek-", "deepseek/"), "indigo"),
    (("kimi-", "kimi/", "moonshot-"), "purple"),
    (("glm-",), "green"),
    (("hy3", "tencent/", "hunyuan"), "teal"),
)

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
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
_TOOL_DETAIL_REDACTION_RE = re.compile(
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
_RUNTIME_FENCE_RE = re.compile(r"```[A-Za-z0-9_-]*")
_RUNTIME_SECRET_FLAG_RE = re.compile(
    r"(?i)(--(?:token|password|secret|api-key|app-secret)(?:=|\s+))([^\s]+)"
)
_RUNTIME_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|password|secret|api_key|api-key|app_secret)=)([^&#\s]+)"
)

def _spinner_text(label: str = "生成中") -> str:
    return f"{_spinner_frame()} {label}"


def _spinner_frame() -> str:
    frame = _SPINNER_FRAMES[int(_time.time() * 8) % len(_SPINNER_FRAMES)]
    return frame

def render_card(
    session: CardSession,
    footer_fields: list[str] | tuple[str, ...] | None = None,
    title: str = DEFAULT_TITLE,
    interaction_mode: str = "callback",
    show_reasoning: bool = True,
    timeline_expanded: bool = False,
    max_timeline_items: int = 12,
    max_reasoning_chars: int = 1200,
    max_tool_result_chars: int = 600,
    status_config: Optional[StatusConfig] = None,
    text_sizes: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    used_text_size_roles: set[str] = set()
    status = _render_status(session, status_config=status_config)
    display_status = resolve_display_status(
        session, status_config or StatusConfig.defaults()
    ).value
    native_reply_completed = (
        session.status == "completed"
        and session.delivery_kind == "chat"
        and bool(session.reply_to_message_id)
    )
    if session.status in {"completed", "failed"}:
        primary_text = normalize_stream_text(session.answer_text)
    elif session.answer_text:
        primary_text = normalize_stream_text(session.answer_text)
    elif session.thinking_text:
        primary_text = normalize_stream_text(session.thinking_text)
    else:
        primary_text = _spinner_frame()
    attachment_summary = _render_attachment_summary(session)
    footer = _render_footer(
        session,
        footer_fields,
        display_status=display_status,
    )
    if native_reply_completed:
        footer = f"已完成 · {footer}"
    if session.delivery_kind == "notice" and session.notice_title:
        configured_title = session.notice_title
    else:
        configured_title = (
            title.strip() if isinstance(title, str) and title.strip() else DEFAULT_TITLE
        )
    runtime_summary = _runtime_header_summary(session)
    header_title = (
        configured_title
        if runtime_summary
        else _runtime_header_title(session, configured_title)
    )
    main_role = "notice" if session.delivery_kind == "notice" else "body"
    elements = _render_main_content_elements(
        primary_text,
        text_size=_role_text_size(
            text_sizes,
            main_role,
            default=None,
            used_roles=used_text_size_roles,
        ),
    )
    timeline_elements: list[Dict[str, Any]] = []
    if show_reasoning:
        timeline_elements = _render_timeline_elements(
            session,
            expanded=timeline_expanded,
            max_items=max_timeline_items,
            max_reasoning_chars=max_reasoning_chars,
            max_tool_result_chars=max_tool_result_chars,
            text_sizes=text_sizes,
            used_text_size_roles=used_text_size_roles,
        )
        elements.extend(timeline_elements)
    elements.extend(_render_interaction_elements(session, interaction_mode=interaction_mode))
    if attachment_summary:
        elements.append(
            {
                "tag": "markdown",
                "element_id": "attachment_summary",
                "content": attachment_summary,
            }
        )
    elements.append({"tag": "hr", "element_id": "main_divider"})
    if not timeline_elements:
        tool_summary = {
            "tag": "markdown",
            "element_id": "tool_summary",
            "content": _render_tool_summary(session),
        }
        _set_text_size(
            tool_summary,
            _role_text_size(
                text_sizes,
                "tool",
                default=None,
                used_roles=used_text_size_roles,
            ),
        )
        elements.append(tool_summary)
    footer_element = {
        "tag": "markdown",
        "element_id": "footer",
        "content": footer,
        "text_size": _role_text_size(
            text_sizes,
            "footer",
            default="x-small",
            used_roles=used_text_size_roles,
        ),
    }
    elements.append(footer_element)
    header = {
        "template": status["template"],
        "title": {"tag": "plain_text", "content": header_title},
    }
    if runtime_summary:
        header["subtitle"] = {"tag": "plain_text", "content": runtime_summary}
    elif status["subtitle"]:
        header["subtitle"] = {"tag": "plain_text", "content": status["subtitle"]}

    card = {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": status.get("summary", status["subtitle"])},
        },
        "body": {
            "elements": elements
        },
    }
    mapped_styles = {
        f"hfc_{role}": dict(text_sizes[role])
        for role in TEXT_SIZE_ROLE_ORDER
        if role in used_text_size_roles
        and isinstance(text_sizes, Mapping)
        and isinstance(text_sizes.get(role), Mapping)
    }
    if mapped_styles:
        card["config"]["style"] = {"text_size": mapped_styles}
    if not native_reply_completed:
        card["header"] = header
    return card


def _render_status(
    session: CardSession, *, status_config: Optional[StatusConfig] = None
) -> Dict[str, str]:
    if session.delivery_kind == "notice":
        return {
            "subtitle": "已完成" if session.status == "completed" else "",
            "template": _notice_template(session.notice_level),
        }
    display_status = resolve_display_status(session, status_config or StatusConfig.defaults()).value
    if display_status == "completed":
        return {"subtitle": "已完成", "template": "green"}
    if display_status == "failed":
        return {"subtitle": "", "summary": "处理失败", "template": "red"}
    if display_status == "waiting":
        return {"subtitle": "", "summary": "等待选择", "template": "orange"}
    if display_status == "in_progress":
        return {"subtitle": "", "summary": "生成中", "template": "blue"}
    return {"subtitle": "", "summary": "思考中", "template": "indigo"}


def _runtime_header_title(session: CardSession, configured_title: str) -> str:
    if session.delivery_kind == "notice" and session.notice_title:
        return session.notice_title
    if session.status == "completed":
        return configured_title
    runtime_title = _sanitize_runtime_header(session.runtime_header_text)
    return runtime_title or configured_title


def _runtime_header_summary(session: CardSession) -> str:
    interaction = session.active_interaction
    if interaction is not None and interaction.status == "pending":
        return ""
    if session.status == "completed":
        return ""
    if session.runtime_phase_text:
        return ""
    return _sanitize_runtime_header(session.latest_tool_preview)


def _sanitize_runtime_header(text: str) -> str:
    normalized = normalize_stream_text(str(text or ""))
    normalized = _RUNTIME_FENCE_RE.sub("", normalized)
    normalized = " ".join(normalized.split())
    normalized = _redact_tool_detail(normalized)
    normalized = _RUNTIME_SECRET_FLAG_RE.sub(r"\1[REDACTED]", normalized)
    normalized = _RUNTIME_URL_SECRET_RE.sub(r"\1[REDACTED]", normalized)
    if len(normalized) <= RUNTIME_HEADER_MAX_CHARS:
        return normalized
    return normalized[: RUNTIME_HEADER_MAX_CHARS - 1].rstrip() + "…"


def _render_main_content_elements(
    main_text: str, *, text_size: str | None = None
) -> list[Dict[str, Any]]:
    import re

    from .text import count_markdown_tables, MAX_CARD_TABLES
    table_count = count_markdown_tables(main_text)
    if table_count > MAX_CARD_TABLES:
        matches = list(re.finditer(r'^\|[-: ]+\|', main_text, re.MULTILINE))
        cutoff = matches[MAX_CARD_TABLES - 1].end()
        rest = main_text[cutoff:]
        next_para = re.search(r'\n\n', rest)
        if next_para:
            cutoff += next_para.start()
        main_text = main_text[:cutoff].rstrip() + (
            "\n\n> 内容含超过 5 个表格，超出部分已省略。"
        )
    chunks = split_markdown_blocks(main_text, MAIN_CONTENT_CHUNK_CHARS)
    elements = []
    for index, chunk in enumerate(chunks):
        element_id = "main_content" if index == 0 else f"main_content_{index}"
        element = {"tag": "markdown", "element_id": element_id, "content": chunk}
        _set_text_size(element, text_size)
        elements.append(element)
    return elements


def _render_interaction_elements(
    session: CardSession, *, interaction_mode: str = "callback"
) -> list[Dict[str, Any]]:
    interaction = session.active_interaction
    if interaction is None:
        return []

    elements: list[Dict[str, Any]] = []
    if interaction.status == "pending" and interaction.description:
        elements.append(
            {
                "tag": "markdown",
                "element_id": "interaction_description",
                "content": interaction.description,
            }
        )
    if interaction.status == "pending" and _normalize_interaction_mode(interaction_mode) == "text":
        choice_lines = [
            f"{index}. {option.label}"
            for index, option in enumerate(interaction.options, start=1)
        ]
        if choice_lines:
            elements.append(
                {
                    "tag": "markdown",
                    "element_id": "interaction_text_choices",
                    "content": "\n".join(
                        choice_lines
                        + [
                            "",
                            "Reply with the number, the option text, or your own answer.",
                        ]
                    ),
                }
            )
        return elements

    if interaction.status == "pending":
        for index, option in enumerate(interaction.options):
            elements.append(
                {
                    "tag": "button",
                    "element_id": f"hfc_btn_{index}",
                    "text": {"tag": "plain_text", "content": option.label},
                    "type": _button_type(option.style),
                    "size": "medium",
                    "width": "default",
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": {
                                "hfc_action": "interaction.select",
                                "interaction_id": interaction.interaction_id,
                                "choice": option.value,
                                "choice_label": option.label,
                                "token": interaction.callback_token,
                            },
                        }
                    ],
                }
            )
        return elements

    if interaction.status == "completed":
        choice = interaction.choice_label or interaction.choice or "已完成"
        user = f" by {interaction.user_name}" if interaction.user_name else ""
        content = f"已选择：{choice}{user}"
    else:
        content = interaction.error or "交互请求失败"
    elements.append(
        {
            "tag": "markdown",
            "element_id": "interaction_result",
            "content": content,
        }
    )
    return elements


def _normalize_interaction_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"text", "markdown", "reply"}:
        return "text"
    return "callback"


def _button_type(style: str) -> str:
    normalized = str(style or "").strip().lower()
    if normalized in {"primary", "danger", "default"}:
        return normalized
    if normalized in {"red", "warning", "destructive"}:
        return "danger"
    if normalized in {"green", "success"}:
        return "primary"
    return "default"


def _render_tool_summary(session: CardSession) -> str:
    if not session.tools:
        return "工具调用 0 次"
    lines = [f"工具调用 {session.tool_count} 次"]
    for tool in session.tools.values():
        lines.append(f"- `{tool.name}`: {tool.status}")
    return "\n".join(lines)


def _render_timeline_elements(
    session: CardSession,
    *,
    expanded: bool,
    max_items: int,
    max_reasoning_chars: int,
    max_tool_result_chars: int,
    text_sizes: Mapping[str, Any] | None = None,
    used_text_size_roles: set[str] | None = None,
) -> list[Dict[str, Any]]:
    if not getattr(session, "timeline", None):
        return []
    all_entries = session.timeline.snapshot()
    entries = _select_timeline_entries(all_entries, max_items=max_items)
    folded = max(0, len(all_entries) - len(entries))
    if not entries and not folded:
        return []
    panel_elements: list[Dict[str, Any]] = []
    if folded:
        panel_elements.extend(
            _timeline_markdown_elements(
                f"> 已折叠 {folded} 条早期思考/工具记录",
                "auxiliary_timeline_folded",
                text_size=_role_text_size(
                    text_sizes,
                    "notice",
                    default="x-small",
                    used_roles=used_text_size_roles,
                ),
            )
        )
    for index, item in enumerate(entries):
        if item.kind == "reasoning":
            content = _limit_text(
                item.content,
                max_reasoning_chars,
                overflow_label="思考内容过长，已截断",
            )
            lines = [f"**{item.title}** · {item.status}"]
            if content:
                lines.append(content)
            panel_elements.extend(
                _timeline_markdown_elements(
                    "\n".join(lines),
                    f"auxiliary_timeline_reasoningentry_{index}",
                    text_size=_role_text_size(
                        text_sizes,
                        "reasoning",
                        default="small",
                        used_roles=used_text_size_roles,
                    ),
                )
            )
        elif item.kind == "tool":
            detail = _limit_text(
                _redact_tool_detail(item.detail),
                max_tool_result_chars,
                overflow_label="工具详情过长，已截断",
            )
            lines = [f"`{item.title}` · {item.status}"]
            if detail:
                lines.append(detail)
            panel_elements.extend(
                _timeline_markdown_elements(
                    _quote_markdown("\n".join(lines)),
                    f"auxiliary_timeline_toolentry_{index}",
                    text_size=_role_text_size(
                        text_sizes,
                        "tool",
                        default="x-small",
                        used_roles=used_text_size_roles,
                    ),
                )
            )
        elif item.kind == "notice":
            content = _limit_text(
                normalize_stream_text(item.content),
                max_tool_result_chars,
                overflow_label="提示内容过长，已截断",
            )
            lines = [f"**{item.title}** · {item.status}"]
            if content:
                lines.append(content)
            panel_elements.extend(
                _timeline_markdown_elements(
                    _quote_markdown("\n".join(lines)),
                    f"auxiliary_timeline_noticeentry_{index}",
                    text_size=_role_text_size(
                        text_sizes,
                        "notice",
                        default="x-small",
                        used_roles=used_text_size_roles,
                    ),
                )
            )
    if not panel_elements:
        return []
    return [
        {
            "tag": "collapsible_panel",
            "element_id": "auxiliary_timeline",
            "expanded": expanded,
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"思考与工具 · {session.tool_count} 次工具调用",
                },
                "vertical_align": "center",
            },
            "border": {"color": "grey", "corner_radius": "5px"},
            "padding": "8px 8px 8px 8px",
            "elements": panel_elements,
        }
    ]


def _timeline_markdown_elements(
    content: str, element_id_prefix: str, *, text_size: str | None
) -> list[Dict[str, Any]]:
    elements = [
        {
            "tag": "markdown",
            "element_id": element_id_prefix
            if index == 0
            else f"{element_id_prefix}_{index}",
            "content": chunk,
        }
        for index, chunk in enumerate(
            split_markdown_blocks(content, MAIN_CONTENT_CHUNK_CHARS)
        )
        if chunk.strip()
    ]
    for element in elements:
        _set_text_size(element, text_size)
    return elements


def _role_text_size(
    text_sizes: Mapping[str, Any] | None,
    role: str,
    *,
    default: str | None,
    used_roles: set[str] | None = None,
) -> str | None:
    value = (text_sizes or {}).get(role)
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if used_roles is not None:
            used_roles.add(role)
        return f"hfc_{role}"
    return default


def _set_text_size(element: dict[str, Any], text_size: str | None) -> None:
    if text_size is not None:
        element["text_size"] = text_size


def _quote_markdown(content: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in content.splitlines())


def _select_timeline_entries(entries: list[Any], *, max_items: int) -> list[Any]:
    if max_items <= 0 or len(entries) <= max_items:
        return list(entries)

    selected_indexes = list(range(len(entries) - max_items, len(entries)))
    if max_items <= 1:
        return [entries[index] for index in selected_indexes]
    if any(entries[index].kind == "reasoning" for index in selected_indexes):
        return [entries[index] for index in selected_indexes]

    latest_reasoning_index = next(
        (
            index
            for index in range(len(entries) - 1, -1, -1)
            if entries[index].kind == "reasoning"
        ),
        None,
    )
    if latest_reasoning_index is None:
        return [entries[index] for index in selected_indexes]

    selected_indexes = [latest_reasoning_index] + selected_indexes[1:]
    selected_indexes = sorted(dict.fromkeys(selected_indexes))
    return [entries[index] for index in selected_indexes]


def _notice_template(level: str) -> str:
    normalized = str(level or "").strip().lower()
    if normalized == "success":
        return "green"
    if normalized == "warning":
        return "orange"
    if normalized == "error":
        return "red"
    return "blue"


def _render_attachment_summary(session: CardSession) -> str:
    items = []
    for item in session.attachments:
        if not isinstance(item, dict):
            continue
        name = str(item.get("summary") or item.get("name") or "").strip()
        if name:
            items.append(name)
    if not items:
        return ""
    return "附件：" + "、".join(items[:8])


def _render_footer(
    session: CardSession,
    footer_fields: list[str] | tuple[str, ...] | None = None,
    *,
    display_status: str = "",
) -> str:
    if session.status == "failed" or display_status == "failed":
        return "已停止"
    if display_status == "waiting":
        return "等待选择"
    if session.status != "completed":
        return _spinner_text("生成中")
    tokens = session.tokens if isinstance(session.tokens, dict) else {}
    input_tokens = _safe_int(tokens.get("input_tokens"))
    output_tokens = _safe_int(tokens.get("output_tokens"))
    try:
        duration = float(session.duration)
    except (TypeError, ValueError):
        duration = 0.0
    model = session.model if isinstance(session.model, str) and session.model.strip() else "Unknown"
    context = session.context if isinstance(session.context, dict) else {}
    used_context = _safe_int(context.get("used_tokens"))
    max_context = _safe_int(context.get("max_tokens"))
    context_percent = round(used_context / max_context * 100) if max_context > 0 else 0
    values = {
        "duration": _format_duration(duration),
        "model": _colored_model_label(model),
        "input_tokens": f"↑{_format_count(input_tokens)}",
        "output_tokens": f"↓{_format_count(output_tokens)}",
        "context": (
            f"ctx {_format_count(used_context)}/"
            f"{_format_count(max_context)} {context_percent}%"
        ),
        "subscription_usage": session.subscription_usage,
    }
    selected = []
    fields = DEFAULT_FOOTER_FIELDS if footer_fields is None else footer_fields
    for field in fields:
        value = values.get(field)
        if value:
            selected.append(value)
    return " · ".join(selected) if selected else values["duration"]


def _colored_model_label(model: str) -> str:
    text = str(model or "")
    safe = html.escape(text, quote=True)
    normalized = text.lower()
    for prefixes, color in MODEL_COLOR_PREFIXES:
        if normalized.startswith(prefixes):
            return f'<font color="{color}">{safe}</font>'
    return safe


def _safe_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    hours, remaining_minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{remaining_minutes}m{remaining_seconds}s"
    if minutes:
        return f"{minutes}m{remaining_seconds}s"
    return f"{remaining_seconds}s"


def _format_count(value: int) -> str:
    if value >= 1_000_000:
        return _format_scaled(value, 1_000_000, "m")
    if value >= 1_000:
        return _format_scaled(value, 1_000, "k")
    return str(value)


def _format_scaled(value: int, factor: int, suffix: str) -> str:
    scaled = value / factor
    if scaled >= 100 or scaled.is_integer():
        return f"{int(round(scaled))}{suffix}"
    return f"{scaled:.1f}".rstrip("0").rstrip(".") + suffix


def _limit_text(text: str, limit: int, *, overflow_label: str = "内容已折叠") -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    suffix = f"\n> {overflow_label}"
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def _redact_tool_detail(text: str) -> str:
    if not text:
        return text
    structured = _parse_tool_detail(text)
    if structured is not None:
        return _dump_redacted_tool_detail(structured)
    redacted = _TOOL_DETAIL_QUOTED_REDACTION_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_TOOL_DETAIL_REDACTED}{match.group(4)}",
        text,
    )
    return _TOOL_DETAIL_REDACTION_RE.sub(r"\1[REDACTED]", redacted)


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
