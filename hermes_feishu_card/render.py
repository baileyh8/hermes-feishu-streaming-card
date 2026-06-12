from __future__ import annotations

from typing import Any, Dict

from .session import CardSession
from .text import normalize_stream_text, split_markdown_blocks
import time as _time

DEFAULT_FOOTER_FIELDS = (
    "duration",
    "model",
    "input_tokens",
    "output_tokens",
    "context",
)
MAIN_CONTENT_CHUNK_CHARS = 2400
DEFAULT_TITLE = "Hermes Agent"

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

def _spinner_text(label: str = "生成中") -> str:
    frame = _SPINNER_FRAMES[int(_time.time() * 8) % len(_SPINNER_FRAMES)]
    return f"{frame} {label}"

def render_card(
    session: CardSession,
    footer_fields: list[str] | tuple[str, ...] | None = None,
    title: str = DEFAULT_TITLE,
    hide_footer: bool = False,
) -> Dict[str, Any]:
    status = _render_status(session)
    main_text = normalize_stream_text(session.visible_main_text) or ("正在思考..." if session.status == "thinking" else "")
    tool_summary = _render_tool_summary(session)
    attachment_summary = _render_attachment_summary(session)
    footer = _render_footer(session, footer_fields)
    header_title = title.strip() if isinstance(title, str) and title.strip() else DEFAULT_TITLE
    elements = _render_main_content_elements(main_text)
    elements.extend(_render_interaction_elements(session))
    if attachment_summary:
        elements.append(
            {
                "tag": "markdown",
                "element_id": "attachment_summary",
                "content": attachment_summary,
            }
        )
    if not hide_footer:
        elements.extend(
            [
                {"tag": "hr", "element_id": "main_divider"},
                {"tag": "markdown", "element_id": "tool_summary", "content": tool_summary},
                {"tag": "markdown", "element_id": "footer", "content": footer, "text_size": "x-small"},
            ]
        )
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": status["subtitle"]},
        },
        "header": {
            "template": status["template"],
            "title": {"tag": "plain_text", "content": header_title},
            "subtitle": {"tag": "plain_text", "content": status["subtitle"]},
        },
        "body": {
            "elements": elements
        },
    }


def _render_status(session: CardSession) -> Dict[str, str]:
    if session.status == "completed":
        if getattr(session, "delivery_kind", "") == "cron":
            return {"subtitle": "", "template": "green"}
        return {"subtitle": "已完成", "template": "green"}
    if session.status == "failed":
        return {"subtitle": "处理失败", "template": "red"}
    if session.active_interaction is not None and session.active_interaction.status == "pending":
        return {"subtitle": "等待选择", "template": "orange"}
    return {"subtitle": "思考中", "template": "indigo"}


def _render_main_content_elements(main_text: str) -> list[Dict[str, Any]]:
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
        elements.append({"tag": "markdown", "element_id": element_id, "content": chunk})
    return elements


def _render_interaction_elements(session: CardSession) -> list[Dict[str, Any]]:
    interaction = session.active_interaction
    if interaction is None:
        return []

    prompt = interaction.prompt or "请选择下一步"
    lines = [f"**{prompt}**"]
    if interaction.description:
        lines.append("")
        lines.append(interaction.description)

    elements: list[Dict[str, Any]] = [
        {
            "tag": "markdown",
            "element_id": "interaction_prompt",
            "content": "\n".join(lines),
        }
    ]
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
) -> str:
    if session.status == "failed":
        return "已停止"
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
        "model": model,
        "input_tokens": f"↑{_format_count(input_tokens)}",
        "output_tokens": f"↓{_format_count(output_tokens)}",
        "context": (
            f"ctx {_format_count(used_context)}/"
            f"{_format_count(max_context)} {context_percent}%"
        ),
    }
    selected = []
    fields = DEFAULT_FOOTER_FIELDS if footer_fields is None else footer_fields
    for field in fields:
        value = values.get(field)
        if value:
            selected.append(value)
    return " · ".join(selected) if selected else values["duration"]


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
