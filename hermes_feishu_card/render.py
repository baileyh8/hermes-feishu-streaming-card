from __future__ import annotations

import ast
from dataclasses import dataclass
import html
import json
import math
import re
import time as _time
from collections.abc import Mapping
from typing import Any, Dict, Literal, Optional

from .card_limits import CardLimitInspection, inspect_card_limits
from .card_timeline import TERMINAL_TOOL_STATUSES
from .session import (
    CardSession,
    ToolState,
    _exact_feishu_open_id,
    _runtime_tool_summary,
)
from .status import StatusConfig, resolve_display_status
from .text import (
    TableOverflowResult,
    normalize_stream_text,
    split_markdown_blocks,
    transform_table_overflow,
)

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
CARD_QUOTE_SUMMARY_MAX_CHARS = 120
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
_TOOL_DURATION_LINE_RE = re.compile(r"^耗时:\s*(.+?)\s*$")


@dataclass(frozen=True)
class CardRenderResult:
    card: Dict[str, Any]
    disposition: Literal["card", "deferred_native", "native"]
    inspection: CardLimitInspection
    table_overflow: TableOverflowResult
    limit_reason: str = ""


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
    table_overflow_mode: str = "compact",
    interaction_profile_id: str = "default",
    mentions_enabled: bool = True,
    reasoning_format: str = "panel",
    completion_mention: bool = False,
) -> Dict[str, Any]:
    return render_card_result(
        session,
        footer_fields=footer_fields,
        title=title,
        interaction_mode=interaction_mode,
        show_reasoning=show_reasoning,
        timeline_expanded=timeline_expanded,
        max_timeline_items=max_timeline_items,
        max_reasoning_chars=max_reasoning_chars,
        max_tool_result_chars=max_tool_result_chars,
        status_config=status_config,
        text_sizes=text_sizes,
        table_overflow_mode=table_overflow_mode,
        interaction_profile_id=interaction_profile_id,
        mentions_enabled=mentions_enabled,
        reasoning_format=reasoning_format,
        completion_mention=completion_mention,
    ).card


def render_card_result(
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
    table_overflow_mode: str = "compact",
    interaction_profile_id: str = "default",
    mentions_enabled: bool = True,
    reasoning_format: str = "panel",
    completion_mention: bool = False,
) -> CardRenderResult:
    primary_text = _primary_text_for_session(session)
    table_overflow = transform_table_overflow(
        primary_text,
        mode=table_overflow_mode,
    )
    card = _render_card_unchecked(
        session,
        footer_fields=footer_fields,
        title=title,
        interaction_mode=interaction_mode,
        show_reasoning=show_reasoning,
        timeline_expanded=timeline_expanded,
        max_timeline_items=max_timeline_items,
        max_reasoning_chars=max_reasoning_chars,
        max_tool_result_chars=max_tool_result_chars,
        status_config=status_config,
        text_sizes=text_sizes,
        table_overflow_mode=table_overflow_mode,
        interaction_profile_id=interaction_profile_id,
        mentions_enabled=mentions_enabled,
        reasoning_format=reasoning_format,
        completion_mention=completion_mention,
    )
    inspection = inspect_card_limits(card)
    if inspection.safe:
        return CardRenderResult(
            card=card,
            disposition="card",
            inspection=inspection,
            table_overflow=table_overflow,
        )

    terminal = session.status in {"completed", "failed"}
    disposition: Literal["deferred_native", "native"] = (
        "native" if terminal else "deferred_native"
    )
    return CardRenderResult(
        card=_render_limit_handoff_card(
            title=title,
            terminal=terminal,
        ),
        disposition=disposition,
        inspection=inspection,
        table_overflow=table_overflow,
        limit_reason=inspection.primary_reason,
    )


def _render_card_unchecked(
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
    table_overflow_mode: str = "compact",
    interaction_profile_id: str = "default",
    mentions_enabled: bool = True,
    reasoning_format: str = "panel",
    completion_mention: bool = False,
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
    primary_text = _primary_text_for_session(session)
    attachment_summary = _render_attachment_summary(session)
    footer = _render_footer(
        session,
        footer_fields,
        display_status=display_status,
        # Maintainer note (contract change): the "本轮回复结束" note used to be prefixed OUTSIDE
        # this call, which buried the state pill behind it
        # ("本轮回复结束 · 已完成 · 工具 #2 · …"). The user asked for the pill to lead
        # ("已完成 · 本轮回复结束 · 工具 #2 · …"), so the note is handed in and inserted AFTER
        # the pill — see _render_footer.
        completion_note="本轮回复结束" if native_reply_completed else "",
    )
    if session.delivery_kind == "notice" and session.notice_title:
        configured_title = session.notice_title
    else:
        configured_title = (
            title.strip() if isinstance(title, str) and title.strip() else DEFAULT_TITLE
        )
    runtime_summary = _runtime_header_summary(session)
    header_action = _header_action_phrase(session, display_status=display_status)
    header_title = _header_title_with_state(
        session,
        configured_title,
        display_status=display_status,
    )
    pending_interaction = session.active_interaction
    pending_approval = (
        pending_interaction is not None
        and pending_interaction.status == "pending"
        and pending_interaction.kind == "approval"
    )
    if (
        pending_interaction is not None
        and pending_interaction.status == "pending"
        and pending_interaction.kind in {"approval", "clarify"}
    ):
        prefix = (
            "待审批："
            if pending_interaction.kind == "approval"
            else "待选择："
        )
        header_title = f"{prefix}{header_title}"
    main_role = "notice" if session.delivery_kind == "notice" else "body"
    elements = []
    if pending_approval:
        # Keep the authorization scope and choices together. Prior answers and
        # the execution timeline must not push this decision below old output.
        primary_text = pending_interaction.prompt
    if primary_text:
        if mentions_enabled and session.delivery_kind == "chat" and not pending_approval:
            primary_text = _render_known_requester_mentions(primary_text, session)
        if completion_mention and session.status == "completed" and session.delivery_kind == "chat":
            requester = _exact_feishu_open_id(session.sender_open_id)
            mention = f'<at id="{requester}"></at>' if requester else ""
            if mention and mention not in primary_text:
                primary_text = mention + "\n\n" + primary_text
        elements = _render_main_content_elements(
            primary_text,
            table_overflow_mode=table_overflow_mode,
            text_size=_role_text_size(
                text_sizes,
                main_role,
                default=None,
                used_roles=used_text_size_roles,
            ),
        )
    tool_activity_elements = (
        []
        if pending_approval
        else _render_tool_activity_elements(
            session,
            text_sizes=text_sizes,
            used_text_size_roles=used_text_size_roles,
            display_status=display_status,
        )
    )
    elements.extend(tool_activity_elements)
    timeline_elements: list[Dict[str, Any]] = []
    if show_reasoning and not pending_approval:
        timeline_elements = _render_timeline_elements(
            session,
            expanded=timeline_expanded,
            max_items=max_timeline_items,
            max_reasoning_chars=max_reasoning_chars,
            max_tool_result_chars=max_tool_result_chars,
            text_sizes=text_sizes,
            used_text_size_roles=used_text_size_roles,
            reasoning_format=reasoning_format,
        )
        elements.extend(timeline_elements)
    elements.extend(
        _render_interaction_elements(
            session,
            interaction_mode=interaction_mode,
            mentions_enabled=mentions_enabled,
        )
    )
    if attachment_summary and not pending_approval:
        elements.append(
            {
                "tag": "markdown",
                "element_id": "attachment_summary",
                "content": attachment_summary,
            }
        )
    elements.append({"tag": "hr", "element_id": "main_divider"})
    if (
        not timeline_elements
        and not tool_activity_elements
        and not pending_approval
        and session.tool_count
    ):
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
    # Maintainer note (contract change): the sub-title now prefers the ACTION phrase, so the title's
    # last segment ("正在读取文件") sits on the row beneath it — the user asked for that split
    # ("标题的正在使用之类的，放到第二行"). The phase ("生成中"/"思考中") only takes the slot when no
    # tool is running, and "本轮回复结束" keeps it for a completed turn (both are mutually exclusive
    # with a running tool by construction — see ``_header_action_phrase``).
    if header_action:
        header["subtitle"] = {"tag": "plain_text", "content": header_action}
    elif runtime_summary:
        header["subtitle"] = {"tag": "plain_text", "content": runtime_summary}
    elif status["subtitle"]:
        header["subtitle"] = {"tag": "plain_text", "content": status["subtitle"]}

    card = {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {
                "content": _card_quote_summary(
                    session,
                    status,
                    display_status=display_status,
                )
            },
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
    if _uses_legacy_callback_card(session, interaction_mode=interaction_mode):
        return _render_legacy_callback_card(
            session,
            header=header,
            profile_id=_normalize_interaction_profile_id(interaction_profile_id),
            mentions_enabled=mentions_enabled,
        )
    return card


def _uses_legacy_callback_card(
    session: CardSession, *, interaction_mode: str
) -> bool:
    interaction = session.active_interaction
    return (
        interaction is not None
        and interaction.status == "pending"
        and _normalize_interaction_mode(interaction_mode) == "callback"
    )


def render_legacy_interaction_callback_card(
    session: CardSession,
    *,
    title: str = DEFAULT_TITLE,
    interaction_profile_id: str = "default",
    mentions_enabled: bool = True,
) -> Dict[str, Any]:
    """Render one interaction entirely on Feishu's legacy callback rail.

    This is the dedicated legacy auxiliary renderer used for interaction
    messages that are NOT the session's streaming card: the streaming card
    (schema 2.0) keeps its stable owner, and the interaction replies on this
    legacy rail instead of switching the session card's dialect.
    """
    interaction = session.active_interaction
    if interaction is None:
        raise ValueError("active interaction is required")
    template = {
        "completed": "green",
        "failed": "red",
    }.get(interaction.status, "blue")
    header_title = interaction.prompt or title
    if (
        interaction.status == "pending"
        and interaction.kind in {"approval", "clarify"}
    ):
        prefix = "待审批：" if interaction.kind == "approval" else "待选择："
        header_title = f"{prefix}{header_title}"
    header = {
        "template": template,
        "title": {
            "tag": "plain_text",
            "content": header_title,
        },
    }
    return _render_legacy_callback_card(
        session,
        header=header,
        profile_id=_normalize_interaction_profile_id(interaction_profile_id),
        mentions_enabled=mentions_enabled,
    )


def _render_legacy_callback_card(
    session: CardSession,
    *,
    header: Mapping[str, Any],
    profile_id: str,
    mentions_enabled: bool = True,
) -> Dict[str, Any]:
    """Render an interaction on Feishu's server-callback card rail.

    CardKit v2 ``behaviors`` callbacks are client-side interactions and do not
    reach Hermes' ``p2.card.action.trigger`` WebSocket handler.  Conversely,
    the legacy ``action`` container is rejected when embedded in a schema-2.0
    card.  Pending and terminal renders therefore stay in the legacy dialect.
    """
    interaction = session.active_interaction
    if interaction is None:  # Defensive: caller already checked the state.
        return {}

    elements: list[Dict[str, Any]] = []
    if interaction.status == "completed":
        elements.extend(_interaction_review_elements(interaction))
        choice = interaction.choice_label or interaction.choice or "已完成"
        user = f" by {interaction.user_name}" if interaction.user_name else ""
        elements.append(
            {"tag": "markdown", "content": f"已选择：{choice}{user}"}
        )
        return {
            "config": {"wide_screen_mode": True, "update_multi": True},
            "header": dict(header),
            "elements": elements,
        }
    if interaction.status == "paused":
        elements.extend(_interaction_review_elements(interaction))
        elements.append({"tag": "markdown", "content": interaction.error})
        elements.append({"tag": "action", "actions": [{
            "tag": "button", "type": "primary",
            "text": {"tag": "plain_text", "content": "查看并继续审批"},
            "value": {"hfc_action": "interaction.select", "interaction_id": interaction.interaction_id,
                      "token": interaction.callback_token, "choice": "__hfc_resume_approval__",
                      "profile_id": profile_id},
        }]})
        return {"config": {"wide_screen_mode": True, "update_multi": True},
                "header": {"template": "orange", "title": {"tag": "plain_text", "content": "任务已暂停，等待审批"}},
                "elements": elements}
    if interaction.status != "pending":
        elements.extend(_interaction_review_elements(interaction))
        elements.append(
            {
                "tag": "markdown",
                "content": interaction.error or "交互请求失败",
            }
        )
        return {
            "config": {"wide_screen_mode": True, "update_multi": True},
            "header": dict(header),
            "elements": elements,
        }

    # Mobile clients truncate long headers without exposing their full text.
    # Keep the complete question in the body before options and controls.
    prompt = normalize_stream_text(interaction.prompt).strip()
    if len(prompt) > 40:
        elements.append({"tag": "markdown", "content": prompt})
    description = mask_approval_scope(normalize_stream_text(interaction.description).strip())
    if description:
        elements.append({"tag": "markdown", "content": description})

    if interaction.kind in {"approval", "clarify"}:
        elements.extend(_interaction_option_descriptions(interaction))

    mention = _interaction_mention_content(
        session,
        interaction,
        mentions_enabled=mentions_enabled,
    )
    if interaction.multi_select:
        if mention:
            hint = f"{mention} 请选择（可多选）"
            if interaction.allow_custom_input:
                hint += "，或输入自定义内容"
            elements.append({"tag": "markdown", "content": hint})
        elements.append(
            _legacy_form(
                _render_multi_select_form(interaction, profile_id=profile_id)
            )
        )
    else:
        hint = "请选择一个选项"
        if interaction.allow_custom_input:
            hint += "，或输入自定义内容"
        if mention:
            hint = f"{mention} {hint}"
        elements.append({"tag": "markdown", "content": hint})
        buttons = [
            _legacy_button(
                _render_choice_button(
                    interaction,
                    index,
                    option,
                    profile_id=profile_id,
                )
            )
            for index, option in enumerate(interaction.options)
        ]
        for offset in range(0, len(buttons), 5):
            elements.append({"tag": "action", "actions": buttons[offset : offset + 5]})
        if interaction.allow_custom_input:
            elements.append(
                _legacy_form(_render_other_form(interaction, profile_id=profile_id))
            )

    elements.extend(
        [
            {"tag": "hr"},
            {"tag": "markdown", "content": "等待选择…"},
        ]
    )
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": dict(header),
        "elements": elements,
    }


def _legacy_button(button: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in button.items()
        if key not in {"element_id", "size", "width", "behaviors"}
    }


def _legacy_form(form: Mapping[str, Any]) -> Dict[str, Any]:
    elements: list[Dict[str, Any]] = []
    for raw_element in form.get("elements", []):
        if not isinstance(raw_element, Mapping):
            continue
        element = {
            key: value
            for key, value in raw_element.items()
            if key not in {"element_id", "size", "width", "behaviors"}
        }
        if element.pop("form_action_type", None) == "submit":
            element["action_type"] = "form_submit"
        elements.append(element)
    return {
        "tag": "form",
        "name": form.get("name", "hfc_interaction_form"),
        "elements": elements,
    }


def _normalize_interaction_profile_id(value: Any) -> str:
    if type(value) is not str:
        return "default"
    candidate = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", candidate) is None:
        return "default"
    return candidate


def _card_quote_summary(
    session: CardSession,
    status: Mapping[str, str],
    *,
    display_status: str,
) -> str:
    if display_status == "completed":
        answer = normalize_stream_text(session.answer_text).strip()
        if answer:
            normalized = " ".join(answer.split())
            if len(normalized) <= CARD_QUOTE_SUMMARY_MAX_CHARS:
                return normalized
            return (
                normalized[: CARD_QUOTE_SUMMARY_MAX_CHARS - 1].rstrip()
                + "…"
            )
    return status.get("summary", status.get("subtitle", ""))


def _primary_text_for_session(session: CardSession) -> str:
    if session.status in {"completed", "failed"}:
        return normalize_stream_text(session.answer_text)
    if session.answer_text:
        return normalize_stream_text(session.answer_text)
    if session.thinking_text:
        return normalize_stream_text(session.thinking_text)
    if session.latest_tool_preview or session.tools:
        return ""
    return _spinner_text("正在加载上下文…")


def _render_limit_handoff_card(*, title: str, terminal: bool) -> Dict[str, Any]:
    configured_title = (
        title.strip()
        if isinstance(title, str) and title.strip()
        else DEFAULT_TITLE
    )
    if len(configured_title) > 80:
        configured_title = configured_title[:79].rstrip() + "…"
    if terminal:
        content = "完整内容已切换为 Hermes 原生消息发送。"
        summary = "已切换为原生消息"
        template = "green"
        footer = "已完成"
    else:
        content = "内容较长，完成后将由 Hermes 原生消息发送。"
        summary = "等待原生消息"
        template = "orange"
        footer = "生成中"
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": summary},
        },
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": configured_title},
        },
        "body": {
            "elements": [
                {"tag": "markdown", "element_id": "main_content", "content": content},
                {"tag": "hr", "element_id": "main_divider"},
                {
                    "tag": "markdown",
                    "element_id": "footer",
                    "content": footer,
                    "text_size": "x-small",
                },
            ]
        },
    }


def render_terminal_limit_handoff_card(
    title: str = DEFAULT_TITLE,
) -> Dict[str, Any]:
    """Render the fixed, answer-free terminal handoff used for repair retries."""

    return _render_limit_handoff_card(title=title, terminal=True)


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
        return {"subtitle": "本轮回复结束", "template": "green"}
    if display_status == "failed":
        return {"subtitle": "", "summary": "处理失败", "template": "red"}
    if display_status == "waiting":
        return {"subtitle": "", "summary": "等待选择", "template": "orange"}
    if display_status == "in_progress":
        return {"subtitle": "", "summary": "生成中", "template": "blue"}
    return {"subtitle": "", "summary": "思考中", "template": "indigo"}


def _tool_action_phrase(tool: ToolState) -> str:
    """The ACTION phrase for the title, PREFIXED with 正在 — "正在读取文件", "正在执行命令".

    Maintainer note (contract change): the 正在 prefix now lives here and only here. The user
    asked for it in the title (it is the status line: "⏳ Sales Bot · 工具 3 · 1m12s · 正在读取文件")
    and removed from the content-area row, where the 运行中/已完成 status pill already says it.
    `_runtime_tool_summary` therefore returns the phrase WITHOUT the prefix.
    """
    summary = _runtime_tool_summary(tool.name, _tool_detail_lines(tool.detail)[0])
    if summary:
        phrase = summary.split("：", 1)[0].split(":", 1)[0].strip()
        if phrase:
            return f"正在{phrase}" if not phrase.startswith("正在") else phrase
    return ""


def _latest_running_action_phrase(session: CardSession) -> str:
    """The phrase for the tool running right now, so the header can say what it is doing.

    Only the verb phrase, never the target: the target is what made the old header unreadable
    (truncated mid-word after 120 chars). The full line lives in the content-area tool block.
    """
    running = [tool for tool in session.tools.values() if _tool_is_running(tool)]
    if not running:
        return ""
    latest = max(running, key=lambda tool: tool.started_at or 0.0)
    return _tool_action_phrase(latest)


def _last_tool_action_phrase(session: CardSession) -> str:
    """The phrase for the most recent tool REGARDLESS of state — used for a stopped turn.

    A stopped run usually has no tool still running, yet the point of the phrase is to say where
    it stopped; the last tool seen is that answer.
    """
    tools = list(session.tools.values())
    if not tools:
        return ""
    latest = max(tools, key=lambda tool: (tool.ordinal or 0, tool.started_at or 0.0))
    return _tool_action_phrase(latest)


def _header_action_phrase(session: CardSession, *, display_status: str) -> str:
    """The ACTION phrase for the header's SUB-TITLE — "正在读取文件", "正在执行命令".

    Maintainer note (contract change): this phrase used to be the LAST segment of the title
    ("⏳ Sales Bot · 工具 #2 · 1m12s · 正在读取文件"). The user asked for it on its own row
    ("标题的正在使用之类的，放到第二行"), so the title keeps only the identity (name + metrics) and
    this moves to the header sub-title — the line that sits directly under the title.

    Empty while an interaction is pending, matching ``_runtime_header_summary``: the title is then
    the prompt itself ("待审批：…"), and a tool phrase underneath it would read as a second subject.
    A COMPLETED turn is also empty — its sub-title slot belongs to "本轮回复结束".
    """
    interaction = session.active_interaction
    if interaction is not None and interaction.status == "pending":
        return ""
    if display_status == "completed" or session.status == "completed":
        return ""
    if display_status == "failed" or session.status == "failed":
        return _last_tool_action_phrase(session)
    return _latest_running_action_phrase(session)


def _header_title_with_state(
    session: CardSession,
    configured_title: str,
    *,
    display_status: str,
) -> str:
    """Answer "is it still working, and at what?" in the header — the card's most-read line.

    The title used to BE the full tool preview while running: the session name disappeared and
    long previews were cut off mid-word. It now leads with the state, keeps the action PHRASE
    ("读取") which is what made the old line useful, and drops the target (command/path)
    to the content area, which has room for it.

    Pending interactions keep their own wording (待审批：/待选择： is prefixed by the caller).
    """
    if session.delivery_kind == "notice":
        return configured_title
    interaction = session.active_interaction
    if interaction is not None and interaction.status == "pending":
        return _sanitize_runtime_header(interaction.prompt) or configured_title
    metrics = _runtime_header_metrics(session, display_status=display_status)
    if display_status == "completed":
        return f"✅ {configured_title}" + (f" · {metrics}" if metrics else "")
    if display_status == "failed":
        # Maintainer note (contract change): this used to collapse to "⛔ <name>", which hid where
        # the run stopped. The metrics stay for that reason; the action phrase moved to the
        # sub-title (see ``_header_action_phrase``) so the title stays an identity line.
        parts = [f"⛔ {configured_title}"]
        if metrics:
            parts.append(metrics)
        return " · ".join(parts)
    if not metrics:
        # Nothing measured: keep the legacy wording rather than a bare "⏳ <name>", which reads as
        # a label instead of a state. What it is doing now lives on the sub-title.
        return f"⏳ 执行中 · {configured_title}"
    # Maintainer note (contract change): the reader's order is name → metrics → prose. The title
    # used to lead with the phrase ("⏳ 正在读取 · Sales Bot") and carry no numbers at all, so the
    # elapsed time and tool count lived only in the footer. The user asked for both in the title,
    # both AFTER the name: "⏳ Sales Bot · 工具 3 · 1m12s". The name stays first because it is what
    # identifies WHICH conversation is still working.
    # Maintainer note (contract change 2): the action phrase was then the title's LAST segment
    # ("… · 正在读取文件"). The user asked for it on the next row ("标题的正在使用之类的，放到第二行"),
    # so it is rendered as the header sub-title instead.
    return f"⏳ {configured_title} · {metrics}"


def _runtime_header_metrics(session: CardSession, *, display_status: str) -> str:
    """``"工具 3 · 1m12s"`` for the header — the numbers the user asked to see up there.

    Maintainer note (contract change): the order used to be elapsed then tool count. The user
    asked for the COUNT first, so the title reads name → how many tools → how long → what it is
    doing. Elapsed comes from the same source the footer uses (live clock while running, the
    recorded duration once done). Sub-second elapsed is omitted: a freshly started turn would
    otherwise open with "0s", which reads as a broken clock rather than "just started".
    """
    parts: list[str] = []
    if session.tool_count:
        # Maintainer note (contract change): the user asked for a hash before the count
        # ("工具 #3"), so it reads as a numbered tally rather than prose.
        parts.append(f"工具 #{session.tool_count}")
    # A stopped turn is over too: report the recorded duration instead of a live clock that keeps
    # climbing after the run ended.
    finished = (
        display_status in {"completed", "failed"}
        or session.status in {"completed", "failed"}
    )
    if finished:
        try:
            duration = float(session.duration)
        except (TypeError, ValueError):
            duration = 0.0
        if duration >= 1.0:
            parts.append(_format_duration(duration))
    else:
        created_at = session.created_at
        if created_at:
            elapsed = max(0.0, _time.time() - float(created_at))
            if elapsed >= 1.0:
                parts.append(_format_duration(elapsed))
    return " · ".join(parts)


def _runtime_header_summary(session: CardSession) -> str:
    """The sub-title carries the runtime PHASE only.

    It used to fall back to the latest tool preview; that moved to the content area along
    with the rest of the tool activity, so a phase is all that is left to say here.
    """
    interaction = session.active_interaction
    if interaction is not None and interaction.status == "pending":
        return ""
    if session.status == "completed":
        return ""
    return _sanitize_runtime_header(session.runtime_phase_text)


def _is_initial_loading(session: CardSession) -> bool:
    return (
        session.status not in {"completed", "failed"}
        and session.delivery_kind == "chat"
        and session.active_interaction is None
        and not session.answer_text
        and not session.thinking_text
        and not session.runtime_phase_text
        and not session.latest_tool_preview
        and not session.tools
    )


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


def _render_known_requester_mentions(text: str, session: CardSession) -> str:
    """Resolve only the authenticated requester name, never guess other users."""
    name = getattr(session, "sender_name", "")
    open_id = _exact_feishu_open_id(getattr(session, "sender_open_id", ""))
    if not name or not open_id or len(name) > 80 or any(ord(c) < 32 or c in "<>" for c in name):
        return text
    # Preserve code, links, and existing markup literally.
    protected = re.compile(r"(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*(?:`|$)|<at\b[^>]*>[\s\S]*?</at>|<[^>]*>|https?://[^\s]+|\[[^\]]*\]\([^)]*\))")
    mention = re.compile(r"(?<![\w@])@" + re.escape(name) + r"(?=$|[\s，。！？、,.!?;:：；）)\]】*])")
    parts = protected.split(text)
    for index in range(0, len(parts), 2):
        parts[index] = mention.sub(lambda _: f'<at id="{open_id}"></at>', parts[index])
    return "".join(parts)


def _render_main_content_elements(
    main_text: str,
    *,
    text_size: str | None = None,
    table_overflow_mode: str = "compact",
) -> list[Dict[str, Any]]:
    main_text = transform_table_overflow(
        main_text,
        mode=table_overflow_mode,
    ).text
    chunks = split_markdown_blocks(main_text, MAIN_CONTENT_CHUNK_CHARS)
    elements = []
    for index, chunk in enumerate(chunks):
        element_id = "main_content" if index == 0 else f"main_content_{index}"
        element = {"tag": "markdown", "element_id": element_id, "content": chunk}
        _set_text_size(element, text_size)
        elements.append(element)
    return elements


def _interaction_mention_content(
    session: CardSession,
    interaction: Any,
    *,
    mentions_enabled: bool = True,
) -> str:
    """Return the in-card @ mention prefix for an approval/clarify card, or ``""``.

    The @ mention of the requester / clarified user is returned as a bare
    ``<at id=...>`` prefix (no trailing text) so callers can merge it into
    the interaction hint line instead of rendering a separate mention row.
    Only pending approval/clarify interactions are mentioned, only when the
    per-kind mention flag is enabled, and only when the session carries a
    valid Feishu open_id for the requester.
    """
    if not mentions_enabled:
        return ""
    if getattr(interaction, "status", "") != "pending":
        return ""
    if getattr(interaction, "kind", "") not in {"approval", "clarify"}:
        return ""
    open_id = _exact_feishu_open_id(getattr(session, "sender_open_id", ""))
    if not open_id:
        return ""
    return f'<at id="{open_id}"></at>'


def _render_interaction_elements(
    session: CardSession,
    *,
    interaction_mode: str = "callback",
    mentions_enabled: bool = True,
) -> list[Dict[str, Any]]:
    interaction = session.active_interaction
    if interaction is None:
        return []

    elements: list[Dict[str, Any]] = []
    mention = _interaction_mention_content(
        session,
        interaction,
        mentions_enabled=mentions_enabled,
    )
    if interaction.status == "pending" and interaction.description:
        # Masked even here: this card lives in a chat other members can read for as long as the
        # approval is open, so a credential in the scope leaks there just as it would on the
        # retained (decided) card. The command stays readable.
        elements.append(
            {
                "tag": "markdown",
                "element_id": "interaction_description",
                "content": mask_approval_scope(interaction.description),
            }
        )
    if interaction.status == "pending" and _normalize_interaction_mode(interaction_mode) == "text":
        choice_lines = [
            f"{index}. {option.label}"
            for index, option in enumerate(interaction.options, start=1)
        ]
        if choice_lines:
            if interaction.multi_select:
                instruction = "Reply with numbers separated by commas or the option text."
            else:
                instruction = "Reply with the number or the option text."
            if interaction.allow_custom_input:
                instruction = instruction[:-1] + ", or your own answer."
            choice_lines += ["", instruction]
            elements.append(
                {
                    "tag": "markdown",
                    "element_id": "interaction_text_choices",
                    "content": "\n".join(choice_lines),
                }
            )
        if mention:
            hint = (
                f"{mention} 请选择（可多选）"
                if interaction.multi_select
                else f"{mention} 请选择一个选项"
            )
            elements.append(
                {
                    "tag": "markdown",
                    "element_id": "interaction_hint",
                    "content": hint,
                }
            )
        return elements

    if interaction.status == "pending":
        if interaction.kind in {"approval", "clarify"}:
            elements.extend(_interaction_option_descriptions(interaction))
        if interaction.multi_select:
            if mention:
                hint = f"{mention} 请选择（可多选）"
                if interaction.allow_custom_input:
                    hint += "，或输入自定义内容"
                elements.append(
                    {
                        "tag": "markdown",
                        "element_id": "interaction_hint",
                        "content": hint,
                    }
                )
            elements.append(_render_multi_select_form(interaction))
        else:
            hint = "（单选）请选择"
            if interaction.allow_custom_input:
                hint += "，或输入自定义内容"
            if mention:
                hint = f"{mention} {hint}"
            elements.append(
                {
                    "tag": "markdown",
                    "element_id": "interaction_hint",
                    "content": hint,
                }
            )
            choice_buttons = [
                _render_choice_button(interaction, index, option)
                for index, option in enumerate(interaction.options)
            ]
            # The Feishu long-connection p2.card.action.trigger channel only
            # dispatches server-side button events from an ``action`` element.
            # Keep groups within the legacy five-action limit while retaining
            # the surrounding CardKit v2 streaming card.
            for offset in range(0, len(choice_buttons), 5):
                elements.append(
                    {
                        "tag": "action",
                        "element_id": f"hfc_choice_actions_{offset // 5}",
                        "actions": choice_buttons[offset : offset + 5],
                    }
                )
            if interaction.allow_custom_input:
                elements.append(_render_other_form(interaction))
        return elements

    if interaction.status == "completed":
        choice = interaction.choice_label or interaction.choice or "已完成"
        user = f" by {interaction.user_name}" if interaction.user_name else ""
        content = f"已选择：{choice}{user}"
        elements.extend(_interaction_review_elements(interaction))
        elements.append({
            "tag": "markdown", "element_id": "interaction_result",
            "content": content,
        })
        return elements

    content = interaction.error or "交互请求失败"
    elements.extend(_interaction_review_elements(interaction))
    elements.append(
        {
            "tag": "markdown",
            "element_id": "interaction_result",
            "content": content,
        }
    )
    return elements


# ---------------------------------------------------------------------------
# Maintainer note — why a decided approval now KEEPS its operation scope, masked
# ---------------------------------------------------------------------------
# Upstream deliberately dropped ``description`` (the operation scope: what the command does plus
# the exact command line) as soon as a decision was taken, and a test froze that
# ("敏感命令详情不应保留在完成态"). The threat behind it is real: the card is one message edited in
# place, so not rendering the scope on the completed card removes the command from the group's
# history — useful when a command carries a credential and the chat has other members.
#
# We changed the contract because the same edit destroyed the audit trail. After a click the card
# showed the question and the choice but NOT what had actually been approved, so neither the
# approver nor anyone reviewing the chat later could reconstruct the decision. Keeping the scope
# and masking credentials gets both properties: the command stays identifiable, the secret never
# appears on a group-visible card (the pending card is masked too — a secret pasted while the
# approval is open leaks exactly the same way).
#
# If upstream prefers the original behaviour, the only thing to change is whether
# ``_interaction_review_elements`` renders ``description``; do NOT re-gate it on
# ``status == "paused"`` (that is what made the card unauditable).
#
# What the mask does NOT cover, stated plainly so this is not mistaken for a guarantee: a secret
# passed as a bare positional argument (``redis-cli -a hunter2``) or any credential shape not
# listed below stays visible. Masking is defence in depth for the command scope of an approval;
# it is not a reason to put credentials on a command line in the first place.
_APPROVAL_SCOPE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|ACCESS_KEY|PRIVATE_KEY|"
    r"CREDENTIAL|AUTHORIZATION)[A-Za-z0-9_]*\s*=\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s;&|]+)"
)
_APPROVAL_SCOPE_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9._~+/=\-]{8,})")
_APPROVAL_SCOPE_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s:@]+):([^/\s@]+)@")
_APPROVAL_SCOPE_TOKEN_LITERAL_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{8,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}"
    r"|glpat-[A-Za-z0-9_\-]{16,})\b"
)
_APPROVAL_SCOPE_REDACTED = "[REDACTED]"


def mask_approval_scope(text: str) -> str:
    """Mask credentials inside an approval's operation scope, keeping the command readable.

    Only value positions are replaced (``KEY=``, ``--flag``, URL userinfo/query, ``Bearer``, known
    token shapes), so a reviewer still sees which command ran with which flags — the audit value —
    without exposing the credential itself. Applied to every render of the scope, pending included.
    """
    if not text:
        return text
    masked = _RUNTIME_SECRET_FLAG_RE.sub(rf"\1{_APPROVAL_SCOPE_REDACTED}", str(text))
    masked = _RUNTIME_URL_SECRET_RE.sub(rf"\1{_APPROVAL_SCOPE_REDACTED}", masked)
    masked = _APPROVAL_SCOPE_ASSIGNMENT_RE.sub(rf"\1{_APPROVAL_SCOPE_REDACTED}", masked)
    masked = _APPROVAL_SCOPE_BEARER_RE.sub(rf"\1{_APPROVAL_SCOPE_REDACTED}", masked)
    masked = _APPROVAL_SCOPE_URL_USERINFO_RE.sub(rf"\1\2:{_APPROVAL_SCOPE_REDACTED}@", masked)
    masked = _APPROVAL_SCOPE_TOKEN_LITERAL_RE.sub(_APPROVAL_SCOPE_REDACTED, masked)
    return _TOOL_DETAIL_REDACTION_RE.sub(rf"\1{_APPROVAL_SCOPE_REDACTED}", masked)


def _interaction_review_elements(interaction: Any) -> list[Dict[str, Any]]:
    # Mobile has no hover: keep the original question, the full operation scope and the options in
    # the card body after submission/expiry, without retaining callback credentials. An approval
    # must stay auditable afterwards — what was asked, what was chosen, and what would run — so the
    # result is APPENDED below these, never swapped in for them. Two rules matter here:
    #   1. never gate the description on ``status == "paused"`` — that hid the command as soon as a
    #      decision was taken and left the card unauditable (see the maintainer note above);
    #   2. the scope is always masked, so retention does not put credentials into group history.
    elements = []
    if interaction.prompt:
        elements.append({"tag": "markdown", "content": interaction.prompt})
    if interaction.description:
        elements.append(
            {
                "tag": "markdown",
                "content": mask_approval_scope(normalize_stream_text(interaction.description)),
            }
        )
    elements.extend(_interaction_option_descriptions(interaction))
    return elements


def _interaction_callback_value(
    interaction: Any, **extra: Any
) -> Dict[str, Any]:
    """Base callback value for an interaction action — always carries the
    interaction id + token so the sidecar can authenticate the click."""
    value: Dict[str, Any] = {
        "hfc_action": "interaction.select",
        "interaction_id": interaction.interaction_id,
        "token": interaction.callback_token,
    }
    value.update(extra)
    return value


def _interaction_option_descriptions(interaction: Any) -> list[Dict[str, Any]]:
    # Labels were plain_text on buttons. Preserve that meaning in the body:
    # do not let Markdown links or tags hide any part of a decision.
    lines = []
    for index, option in enumerate(interaction.options, start=1):
        label = re.sub(
            r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1",
            html.escape(option.label, quote=False),
        )
        lines.append(f"{index}. {label}")
    if not lines:
        return []
    return [{"tag": "markdown", "content": "\n\n".join(lines)}]


def _render_choice_button(
    interaction: Any,
    index: int,
    option: Any,
    *,
    profile_id: str = "default",
) -> Dict[str, Any]:
    return {
        "tag": "button",
        "element_id": f"hfc_btn_{index}",
        # Sequence number is display-only (like the multi-select dropdown);
        # the submitted value stays the clean option value.
        "text": {
            "tag": "plain_text",
            "content": (
                str(index + 1) if interaction.kind in {"approval", "clarify"}
                else f"{index + 1}. {option.label}"
            ),
        },
        "type": _button_type(option.style),
        "size": "medium",
        "width": "default",
        # Hermes Feishu receives card actions through the server-side
        # p2.card.action.trigger WebSocket callback.  That callback exposes the
        # button's top-level ``value`` as event.action.value.  A CardKit
        # ``behaviors: callback`` entry only drives client callback behavior and
        # does not reach this long-connection handler.
        "value": _interaction_callback_value(
            interaction,
            choice=option.value,
            choice_label=option.label,
            profile_id=_normalize_interaction_profile_id(profile_id),
        ),
    }


def _render_other_input() -> Dict[str, Any]:
    """Free-text input used for the 'Other' answer path (Hermes native clarify
    always appends an 'Other (type your answer)' option)."""
    return {
        "tag": "input",
        "element_id": "hfc_other",
        "name": "hfc_other",
        "input_type": "text",
        "placeholder": {"tag": "plain_text", "content": "或输入自定义答案…"},
        "width": "fill",
    }


def _render_other_form(
    interaction: Any, *, profile_id: str = "default"
) -> Dict[str, Any]:
    """Single-select card footer: a form with the free-text input + submit
    button. On submit, Feishu returns action.form_value.hfc_other with the
    user's typed answer and action.name = hfc_other_<callback_token>.
    Form-submit buttons must not carry behaviors callbacks, so the unguessable
    token in the button name preserves the normal callback authentication
    boundary without exposing the interaction id as a credential."""
    return {
        "tag": "form",
        "name": "hfc_other_form",
        "elements": [
            _render_other_input(),
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "✏️ 提交自定义答案"},
                "type": "default",
                "width": "default",
                "form_action_type": "submit",
                "name": f"hfc_other_{interaction.callback_token}",
                "value": {
                    "profile_id": _normalize_interaction_profile_id(profile_id)
                },
            },
        ],
    }


def _render_multi_select_form(
    interaction: Any, *, profile_id: str = "default"
) -> Dict[str, Any]:
    """Multi-select card body: a form with a native multi-select dropdown
    (multi_select_static) + a single confirm button, plus the free-text
    'Other' input.

    One submit button only: if the user typed into hfc_other the typed text
    wins (custom answer); otherwise the selected options are submitted.
    On submit, Feishu returns action.form_value (hfc_multi list /
    hfc_other text) and action.name = hfc_confirm_<callback_token>."""
    options = [
        {
            "text": {
                "tag": "plain_text",
                "content": (
                    str(index) if interaction.kind in {"approval", "clarify"}
                    else f"{index}. {option.label}"
                ),
            },
            "value": option.value,
        }
        for index, option in enumerate(interaction.options, start=1)
    ]
    elements = [
        {
            "tag": "multi_select_static",
            "element_id": "hfc_multi",
            "name": "hfc_multi",
            "type": "default",
            "width": "fill",
            "required": False,
            "placeholder": {
                "tag": "plain_text",
                "content": "请选择（可多选）",
            },
            "options": options,
            "behaviors": [
                {
                    "type": "callback",
                    "value": {"hfc_action": "interaction.noop"},
                }
            ],
        }
    ]
    if interaction.allow_custom_input:
        elements.append(_render_other_input())
    elements.append(
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "✅ 确认选择"},
            "type": "primary",
            "width": "fill",
            "form_action_type": "submit",
            "name": f"hfc_confirm_{interaction.callback_token}",
            "value": {
                "profile_id": _normalize_interaction_profile_id(profile_id)
            },
        }
    )
    return {
        "tag": "form",
        "name": "hfc_clarify_form",
        "elements": elements,
    }


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
        lines.append(f"- {_name_tag(tool.name)}: {tool.status}")
    return "\n".join(lines)


# One word for "still working", wherever the card says it. It used to differ per surface —
# 运行中 on the tool row, 进行中 in the panel, 执行中 in the footer — which read as three
# different states on one card. The user settled on 执行中.
_RUNNING_TOOL_PILL = ("执行中", "blue")
_FINISHED_TOOL_PILL = ("已完成", "green")
_FAILED_TOOL_PILL = ("失败", "red")
# The turn ended while this tool was still going: it never reported a result, so calling it
# 已完成 would be a lie, and it is the row a reader looks at to see WHERE the run stopped.
_INTERRUPTED_TOOL_PILL = ("已中断", "orange")
# A row, not a paragraph: this is the live action line, not a place to dump a whole command.
_TOOL_ACTIVITY_TEXT_MAX_CHARS = 100
# A tool's stored detail is MULTI-LINE: the tool preview, then "参数: …", then "耗时: …" (see
# session._tool_detail_from_event_data). The action line must read the part that names the work.
# Maintainer note (contract change): the argument line used to be treated as "no target" and the
# whole row's action was dropped, so a run whose preview was missing rendered as bare
# "执行中 · terminal · #4" — the user could not tell what the agent was doing. The arguments are now
# the fallback target instead: naming the work beats an empty row.
_TOOL_ARGUMENT_LINE_RE = re.compile(r"^(?:参数|args|arguments)\s*[:：]\s*(.*)$", re.IGNORECASE)
# Meta lines already rendered elsewhere in the row (duration in the row, failure in its own pill).
_TOOL_META_LINE_RE = re.compile(r"^(?:耗时|用时|失败|错误|duration|elapsed|error)\s*[:：]", re.IGNORECASE)
# Which argument names the work. First match wins; the rest is a JSON blob the reader does not need.
_TOOL_ARGUMENT_KEY_PRIORITY = (
    "command", "cmd", "file_path", "path", "file", "pattern", "query", "url", "text",
    "code", "prompt", "task", "name", "goal", "message",
)


def _status_tag(label: str, color: str) -> str:
    """A coloured pill. Feishu rejects a standalone text_tag element (230099/200621) but
    renders the <text_tag> form inside lark_md, which is what every caller here produces."""
    return f"<text_tag color='{color}'>{label}</text_tag>"


def _name_tag(name: str) -> str:
    """A grey-background pill for an identifier (tool name).

    Rendered as a neutral text_tag rather than `code`: the grey block separates the name from
    surrounding prose far more clearly than inline-code styling does.
    """
    safe = html.escape(str(name or ""), quote=False)
    return f"<text_tag color='neutral'>{safe}</text_tag>"


def _tool_is_running(tool: ToolState) -> bool:
    return str(tool.status or "").strip().lower() not in TERMINAL_TOOL_STATUSES


def _render_tool_activity_elements(
    session: CardSession,
    *,
    text_sizes: Mapping[str, Any] | None = None,
    used_text_size_roles: set[str] | None = None,
    display_status: str = "",
) -> list[Dict[str, Any]]:
    """Show what the agent is doing RIGHT NOW, right under the answer.

    This used to be squeezed into the header as a truncated one-liner, where the session name
    was the thing that got dropped. A row per tool with a coloured status pill instead: one
    glance at the content area answers "still working?". Once nothing is running, the most
    recent tool stays as a single row so a finished card still shows what it did (the full
    history lives in 思考过程, the count in the footer).
    """
    if not session.tools:
        return []
    # A finished turn cannot have a running tool: without this, a tool whose terminal event
    # never arrived sat on a "✅ 已完成" card labelled 运行中.
    turn_is_live = display_status not in {"completed", "failed"} and session.status not in {
        "completed",
        "failed",
    }
    running = [
        tool for tool in session.tools.values() if turn_is_live and _tool_is_running(tool)
    ]
    if running:
        running.sort(key=lambda tool: tool.started_at or 0.0)
        selected = running
    else:
        # tools is insertion-ordered: the last entry is the most recently started tool.
        selected = list(session.tools.values())[-1:]
    now = _time.time()
    text_size = _role_text_size(
        text_sizes,
        "tool",
        default="x-small",
        used_roles=used_text_size_roles,
    )
    return [
        _tool_activity_row(
            tool,
            index=index,
            now=now,
            text_size=text_size,
            running=turn_is_live and _tool_is_running(tool),
            turn_over=not turn_is_live,
        )
        for index, tool in enumerate(selected)
    ]


def _tool_detail_lines(detail: str) -> tuple[str, str]:
    """Split a stored detail into (the line naming the work, leftover parameters).

    A stored detail is MULTI-LINE: the tool's own preview, then "参数: {json}", then "耗时: 12s"
    (see session._tool_detail_from_event_data). The preview names the work; the arguments line
    carries the rest. Both halves are returned so each can have its own row — a single joined line
    read as noise.

    Maintainer note (contract change): the arguments used to be treated as "no target" and the whole
    row's action was dropped, so a run whose preview was missing rendered as a bare
    "执行中 · terminal · #4" and the user could not tell what the agent was doing. Arguments now name
    the work when there is no preview, and whatever is left over becomes the parameter row.
    """
    preview = ""
    arguments = ""
    for raw in str(detail or "").splitlines():
        line = raw.strip()
        if not line or _TOOL_META_LINE_RE.match(line):
            continue
        match = _TOOL_ARGUMENT_LINE_RE.match(line)
        if match:
            arguments = arguments or match.group(1)
            continue
        preview = preview or line

    pairs = _tool_argument_pairs(arguments)
    if preview:
        # The preview is the work; the parameters are everything it does not already say.
        return preview, _format_tool_arguments([pair for pair in pairs if pair[1] != preview])
    for key, value in pairs:
        if key.lower() in _TOOL_ARGUMENT_KEY_PRIORITY:
            return value, _format_tool_arguments(
                [pair for pair in pairs if pair[1] != value]
            )
    # Unrecognised shape: keep the text as the work, without also echoing it as "parameters".
    return arguments, ""


def _tool_argument_pairs(raw: str) -> list[tuple[str, str]]:
    """The scalar entries of a stored arguments object, in event order.

    The stored arguments are a compact JSON object. Dumped verbatim they are noise in a chat line
    ('{"command": "pytest -q", "timeout": 120}'), so the entries are unpacked into key=value pairs;
    nested/complex values are skipped rather than printed as a blob.
    """
    text = str(raw or "").strip()
    if not text.startswith("{"):
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, dict):
        return []
    return [
        (str(key), str(value))
        for key, value in parsed.items()
        if isinstance(value, (str, int, float, bool)) and str(value).strip()
    ]


def _format_tool_arguments(pairs: list[tuple[str, str]]) -> str:
    return " ".join(f"{key}={value}" for key, value in pairs)


def _tool_activity_text(tool: ToolState) -> str:
    """The live action line for a tool: its friendly action plus target ("读取文件：session.py").

    This is the text the header used to carry as a truncated one-liner. It belongs here, in the
    content area, where there is room for it — the header only answers "still working?".
    Sanitized through the header sanitizer (untrusted paths/commands/secrets) and capped.

    Maintainer note: the target comes from `_tool_detail_lines`, not from the raw detail — a stored
    detail is multi-line, and only one of those lines belongs on this row. A line with NO TARGET is
    still dropped: `_runtime_tool_summary` joins phrase and target with "：", so a summary without it
    is a verb-only line ("执行命令") — the name pill already says `terminal`, so the phrase repeated
    it and pushed the line's real information (status + name + ordinal) apart. Testing the separator
    rather than a hardcoded phrase list keeps this correct as phrases change.
    """
    target, _ = _tool_detail_lines(tool.detail)
    if not target:
        return ""
    summary = _runtime_tool_summary(tool.name, target)
    if not summary or "：" not in summary:
        return ""
    return _cap_activity_text(summary)


def _tool_activity_params(tool: ToolState) -> str:
    """The parameter row for a tool, if it has any the action line does not already carry."""
    _, params = _tool_detail_lines(tool.detail)
    if not params:
        return ""
    return f"参数: {_cap_activity_text(params)}"


def _cap_activity_text(text: str) -> str:
    text = _sanitize_runtime_header(text)
    if len(text) > _TOOL_ACTIVITY_TEXT_MAX_CHARS:
        return text[: _TOOL_ACTIVITY_TEXT_MAX_CHARS - 1].rstrip() + "…"
    return text


def _tool_activity_row(
    tool: ToolState,
    *,
    index: int,
    now: float,
    text_size: str | None = None,
    running: bool | None = None,
    turn_over: bool = False,
) -> Dict[str, Any]:
    if running is None:
        running = _tool_is_running(tool)
    if running:
        label, color = _RUNNING_TOOL_PILL
    elif turn_over and _tool_is_running(tool):
        # Non-terminal status on a finished turn = the turn ended mid-tool. Report that honestly
        # instead of 已完成 (a reader checking "where did it stop" reads exactly this pill).
        label, color = _INTERRUPTED_TOOL_PILL
    else:
        label, color = _tool_terminal_pill(tool)
    parts = [_status_tag(label, color)]
    if tool.name:
        parts.append(_name_tag(tool.name))
    # Maintainer note (contract change): the numbers now precede the phrase. The row used to end
    # with the elapsed time ("… #4 · 正在执行终端：pytest -q · 12s"), which put the one thing that
    # changes between renders furthest from the status it belongs to. The user asked for time and
    # count ahead of the phrase: "进行中 · terminal · 12s · #4 · 读取文件：pytest -q".
    # Maintainer note (contract change 2): the ordinal now precedes the duration, so the row reads
    # "执行中 · terminal · #4 · 12s". The user asked for the time to sit after the number, matching
    # the header ("工具 #N · 1m12s") — the count identifies the tool, the duration qualifies it, and
    # keeping both in the same order across surfaces avoids re-reading the same pair twice.
    if tool.ordinal:
        parts.append(f"#{tool.ordinal}")
    if running and tool.started_at:
        parts.append(_format_duration(max(0.0, now - float(tool.started_at))))
    # Maintainer note (contract change): this was ONE line — status, tool name, duration, ordinal and
    # the action all joined by " · ". The user's report was that cramming them together is confusing
    # ("不然都挤在一行 很混乱"), and asked for three rows: status information, then the action, then
    # the parameters. Only the first row is unconditional; the action row is dropped when the tool
    # has no target to name, and the parameter row when its arguments carry nothing new.
    lines = [" · ".join(parts)]
    action = _tool_activity_text(tool)
    if action:
        lines.append(action)
    params = _tool_activity_params(tool)
    if params:
        lines.append(params)
    element: Dict[str, Any] = {
        "tag": "markdown",
        "element_id": f"tool_activity_{index}",
        "content": "\n".join(lines),
    }
    _set_text_size(element, text_size)
    return element


def _tool_terminal_pill(tool: ToolState) -> tuple[str, str]:
    status = str(tool.status or "").strip().lower()
    if status in {"failed", "cancelled", "canceled"}:
        return _FAILED_TOOL_PILL
    return _FINISHED_TOOL_PILL


_TIMELINE_WORK_KINDS = frozenset({"reasoning", "tool", "subagent"})


def _render_timeline_elements(
    session: CardSession,
    *,
    expanded: bool,
    max_items: int,
    max_reasoning_chars: int,
    max_tool_result_chars: int,
    text_sizes: Mapping[str, Any] | None = None,
    used_text_size_roles: set[str] | None = None,
    reasoning_format: str = "panel",
) -> list[Dict[str, Any]]:
    if not getattr(session, "timeline", None):
        return []
    all_entries = session.timeline.snapshot()
    if not all_entries:
        return []
    entries = _select_timeline_entries(all_entries, max_items=max_items)
    folded = max(0, len(all_entries) - len(entries))
    panel_elements: list[Dict[str, Any]] = []
    reasoning_elements: list[Dict[str, Any]] = []
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
                if reasoning_format == "code":
                    # A longer fence preserves embedded backticks literally.
                    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", content)), default=0))
                    lines.append(f"{fence}text\n{content}\n{fence}")
                else:
                    lines.append(content)
            target_elements = reasoning_elements if reasoning_format == "code" else panel_elements
            target_elements.extend(
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
            detail, duration = _split_tool_timeline_detail(
                _redact_tool_detail(item.detail)
            )
            detail = _limit_text(
                detail,
                max_tool_result_chars,
                overflow_label="工具详情过长，已截断",
            )
            panel_elements.extend(
                _timeline_markdown_elements(
                    _render_tool_timeline_row(
                        item.title,
                        item.status,
                        detail,
                        duration,
                        # The panel row carries the same #N tally as the content-area row, so the
                        # two can be matched up. Resolved from the live tool state (the timeline
                        # entry itself does not track an ordinal).
                        ordinal=_timeline_tool_ordinal(session, item),
                    ),
                    f"auxiliary_timeline_toolentry_{index}",
                    text_size=_role_text_size(
                        text_sizes,
                        "tool",
                        default="x-small",
                        used_roles=used_text_size_roles,
                    ),
                )
            )
        elif item.kind == "subagent":
            detail = _limit_text(
                normalize_stream_text(item.detail),
                max_tool_result_chars,
                overflow_label="子代理详情过长，已截断",
            )
            panel_elements.extend(
                _timeline_markdown_elements(
                    _render_subagent_timeline_row(
                        item.title,
                        item.status,
                        detail,
                    ),
                    f"auxiliary_timeline_subagententry_{index}",
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
    if panel_elements:
        # The panel is named for thinking and tool work. A timeline holding only notices (a deferred
        # compression hint, a skill-loading note) is neither, and folding those into it produced
        # "思考与工具 · 0 次工具调用" — a panel advertising zero of the thing it is named after while
        # displaying unrelated content. Notices render standalone in that case, so the hint stays
        # visible without a header that contradicts it.
        if any(item.kind in _TIMELINE_WORK_KINDS for item in all_entries):
            reasoning_elements.append(_timeline_panel(session, panel_elements, expanded=expanded))
        else:
            reasoning_elements.extend(panel_elements)
    return reasoning_elements


_TIMELINE_PANEL_TITLE = "▸ 思考过程（点开查看）"


def _timeline_panel(
    session: CardSession,
    elements: list[Dict[str, Any]],
    *,
    expanded: bool,
) -> Dict[str, Any]:
    """The collapsible 思考过程 panel. Its header must LOOK tappable.

    Maintainer note (contract change): the header used to read just "思考过程" — plain prose with
    no affordance at all, so the user reported they could not tell the panel opens
    ("思考过程这几个字目前看不出来 可以点开折叠"). The title now carries a leading triangle and an
    explicit hint. Deliberately text-only: Feishu rejects a standalone text_tag element
    (230099/200621) and no collapsible_panel header in this codebase has ever sent an `icon`, so a
    new element type would be unverified against the live API — a plain_string hint cannot break
    the card.
    """
    return {
        "tag": "collapsible_panel",
        "element_id": "auxiliary_timeline",
        "expanded": expanded,
        "header": {
            "title": {
                "tag": "plain_text",
                "content": _TIMELINE_PANEL_TITLE,
            },
            "vertical_align": "center",
        },
        "border": {"color": "grey", "corner_radius": "8px"},
        "padding": "8px 8px 8px 8px",
        "elements": elements,
    }


def _split_tool_timeline_detail(detail: str) -> tuple[str, str]:
    duration = ""
    lines: list[str] = []
    for line in str(detail or "").splitlines():
        match = _TOOL_DURATION_LINE_RE.fullmatch(line.strip())
        if match:
            if not duration:
                duration = match.group(1)
            continue
        lines.append(line)
    return "\n".join(lines).strip(), duration


def _timeline_tool_ordinal(session: CardSession, item: Any) -> int:
    """The ``#N`` for a 思考过程 tool row: that tool's call number within the turn.

    Prefers the live tool state, which is authoritative. Falls back to the row's position among
    the timeline's tool entries (``record_tool`` appends them in call order) for rows whose tool
    state is no longer around.
    """
    tool_id = str(getattr(item, "tool_id", "") or "")
    if tool_id:
        tool = session.tools.get(tool_id)
        ordinal = getattr(tool, "ordinal", 0) if tool is not None else 0
        if ordinal:
            return int(ordinal)
    timeline = getattr(session, "timeline", None)
    snapshot = timeline.snapshot() if timeline is not None else []
    position = 0
    seen: set[str] = set()
    for entry in snapshot:
        if getattr(entry, "kind", "") != "tool":
            continue
        entry_id = str(getattr(entry, "tool_id", "") or "")
        if entry_id and entry_id in seen:
            continue
        if entry_id:
            seen.add(entry_id)
        position += 1
        if entry_id and entry_id == tool_id:
            return position
    return 0


def _render_tool_timeline_row(
    title: str,
    status: str,
    detail: str,
    duration: str,
    ordinal: int = 0,
) -> str:
    """One row inside 思考过程. `ordinal` is the tool's call number, shown as #N.

    Maintainer note: the user asked the panel's tool rows to carry the same 井号 tally the
    content-area rows use, so a row can be matched to the "#4" it refers to. It sits right after
    the name and ahead of the duration, mirroring the header order (name → count → time → phrase).
    """
    normalized_status = str(status or "running").strip().lower()
    safe_title = html.escape(str(title or "工具"), quote=False)
    meta = " · ".join(part for part in (f"#{ordinal}" if ordinal else "", duration) if part)
    meta_suffix = f" · {meta}" if meta else ""
    if normalized_status in {
        "completed",
        "success",
        "succeeded",
        "ok",
        "已完成",
        "完成",
        "成功",
    }:
        color = "green"
        headline = f"✓ **{safe_title}**{meta_suffix}"
    elif normalized_status in {"failed", "error", "失败", "已失败", "错误"}:
        color = "red"
        headline = f"✕ **{safe_title}**{meta_suffix} · 失败"
    elif normalized_status in {"cancelled", "canceled", "已取消", "取消"}:
        color = "grey"
        headline = f"⊘ **{safe_title}**{meta_suffix} · 已取消"
    elif normalized_status in {"queued", "waiting", "排队中", "等待中"}:
        color = "grey"
        headline = f"○ **{safe_title}**{meta_suffix} · 等待中"
    else:
        color = "blue"
        headline = f"{_spinner_frame()} **{safe_title}**{meta_suffix} · 执行中"
    lines = [f'<font color="{color}">{headline}</font>']
    for line in str(detail or "").splitlines():
        safe_line = html.escape(line, quote=False)
        lines.append(f'<font color="grey">　{safe_line}</font>')
    return "\n".join(lines)


def _render_subagent_timeline_row(title: str, status: str, detail: str) -> str:
    normalized_status = str(status or "running").strip().lower()
    safe_title = html.escape(str(title or "子代理"), quote=False)
    label = f"子代理：{safe_title}"
    if normalized_status in {"completed", "success", "succeeded"}:
        color, headline = "green", f"✓ **{label}** · 已完成"
    elif normalized_status in {"failed", "error", "timeout", "blocked"}:
        color, headline = "red", f"✕ **{label}** · 失败"
    elif normalized_status in {"cancelled", "canceled"}:
        color, headline = "grey", f"⊘ **{label}** · 已取消"
    elif normalized_status == "interrupted":
        color, headline = "grey", f"⊘ **{label}** · 已中断"
    elif normalized_status in {"queued", "waiting"}:
        color, headline = "grey", f"○ **{label}** · 等待中"
    else:
        color, headline = "blue", f"{_spinner_frame()} **{label}** · 执行中"
    lines = [f'<font color="{color}">{headline}</font>']
    for line in str(detail or "").splitlines():
        lines.append(f'<font color="grey">　{html.escape(line, quote=False)}</font>')
    return "\n".join(lines)


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
    completion_note: str = "",
) -> str:
    # Maintainer note (contract change): a stopped or failed turn used to replace the WHOLE footer
    # with just "已停止", throwing away the tool count, elapsed time, model and token counts —
    # exactly the context someone needs to see WHERE the run stopped. The stop is now a pill on
    # top of the same fields; the user asked for the state to survive a stop.
    failed = session.status == "failed" or display_status == "failed"
    if display_status == "waiting":
        interaction = session.active_interaction
        remaining_seconds = (
            max(0.0, float(interaction.expires_at) - _time.time())
            if interaction is not None
            else 300.0
        )
        minutes = max(1, int(math.ceil(remaining_seconds / 60.0)))
        # Maintainer note (contract change): the waiting footer used to show ONLY the expiry
        # countdown, which hid how much work the paused turn had already done. The user asked for
        # the consumption line in EVERY state — running, stopped, and waiting on a decision — so
        # the tool count and elapsed time lead here too (matching the other two footers), and the
        # token figures follow when the turn has already reported them (they are usually absent
        # mid-turn: the core only sends tokens with turn.completed, so an approval that fires
        # during the run legitimately shows no counts rather than a fake ↑0 ↓0).
        waiting: list[str] = []
        if session.tool_count:
            waiting.append(f"工具 #{session.tool_count}")
        if session.created_at:
            # Only once there is a second to show: a freshly-armed approval would otherwise read
            # "0s · 等待选择 · ⏳ 5 分钟后过期", which is noise, not information.
            elapsed = max(0.0, _time.time() - float(session.created_at))
            if elapsed >= 1.0:
                waiting.append(_format_duration(elapsed))
        waiting.append("等待选择")
        waiting.append(f"⏳ {minutes} 分钟后过期")
        tokens = session.tokens if isinstance(session.tokens, dict) else {}
        input_tokens = _safe_int(tokens.get("input_tokens"))
        output_tokens = _safe_int(tokens.get("output_tokens"))
        if input_tokens or output_tokens:
            waiting.append(f"↑{_format_count(input_tokens)} · ↓{_format_count(output_tokens)}")
        return " · ".join(waiting)
    if session.status != "completed" and display_status != "completed" and not failed:
        # A live clock: elapsed since the turn started, so a long silent stretch reads as
        # "it has been going 4 minutes" instead of an apparently frozen card. Feishu only
        # re-renders on an event (or during the ~12s animation window), so this stills
        # between events — each render shows the true elapsed time at that moment.
        # Maintainer note (contract change): the tool count now leads the elapsed time, matching
        # the title — the user asked for "工具 N · <time>" order in both places (it used to be
        # time then count here).
        running = [f"{_spinner_frame()} {_status_tag('执行中', 'blue')}"]
        if session.tool_count:
            # Maintainer note (contract change): hash before the count, matching the title.
            running.append(f"工具 #{session.tool_count}")
        running.append(_format_duration(max(0.0, _time.time() - float(session.created_at))))
        # Maintainer note (contract change): the footer repeats the title's action phrase
        # ("正在读取文件") so the state line reads the same wherever the eye lands — the user
        # asked for it explicitly.
        phrase = _latest_running_action_phrase(session)
        if phrase:
            running.append(phrase)
        return " · ".join(running)
    tokens = session.tokens if isinstance(session.tokens, dict) else {}
    input_tokens = _safe_int(tokens.get("input_tokens"))
    output_tokens = _safe_int(tokens.get("output_tokens"))
    try:
        duration = float(session.duration)
    except (TypeError, ValueError):
        duration = 0.0
    model = session.model if isinstance(session.model, str) and session.model.strip() else "Unknown"
    provider = getattr(session, "provider", "")
    if isinstance(provider, str) and provider.strip() and model != "Unknown":
        provider = provider.strip()
        if not model.startswith(provider + "/"):
            model = f"{provider}/{model}"
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
    if session.tool_count:
        # Maintainer note (contract change): the count leads the other metrics, matching the title
        # (it used to be appended last, after ctx); it carries a hash, per the user's request.
        selected.append(f"工具 #{session.tool_count}")
    fields = DEFAULT_FOOTER_FIELDS if footer_fields is None else footer_fields
    for field in fields:
        value = values.get(field)
        if value:
            selected.append(value)
    detail = " · ".join(selected) if selected else values["duration"]
    pill = _status_tag("已停止", "red") if failed else _status_tag("已完成", "green")
    # Maintainer note (contract change): the state pill must LEAD the footer. The
    # "本轮回复结束" note used to be prefixed outside this function, so the reader saw
    # "本轮回复结束 · 已完成 · 工具 #2 · …" and the state was buried mid-line. The note now
    # lands right after the pill: "已完成 · 本轮回复结束 · 工具 #2 · …". Only the native-reply
    # completion path passes a note, so every other state string is byte-identical to before.
    if completion_note:
        return f"{pill} · {completion_note} · {detail}"
    return f"{pill} · {detail}"


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
    redacted = _TOOL_DETAIL_REDACTION_RE.sub(r"\1[REDACTED]", redacted)
    # CLI-flag and query-string shapes (--password secret, ?token=abc) slip past the
    # key/value scanners above. The header sanitizer already sweeps them; tool detail is
    # rendered in the timeline and in the tool-activity rows too, so sweep here as well.
    redacted = _RUNTIME_SECRET_FLAG_RE.sub(r"\1[REDACTED]", redacted)
    return _RUNTIME_URL_SECRET_RE.sub(r"\1[REDACTED]", redacted)


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
