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
from .session import CardSession, _exact_feishu_open_id
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
    pending_interaction = session.active_interaction
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
    if primary_text:
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
    elements.extend(
        _render_interaction_elements(
            session,
            interaction_mode=interaction_mode,
            mentions_enabled=mentions_enabled,
        )
    )
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
    """Whether the session card must render in the legacy (schema-V1) layout.

    An interaction card is ALWAYS sent as a legacy card (CardKit v2 ``action``
    containers are rejected by Feishu when embedded in a schema-2.0 card). The
    card is then updated in place after the choice by PATCHing the same
    message -- Feishu refuses a schema-2.0 card onto a schema-V1 message
    (api_code 230099, ext 200830: "schemaV2 card can not change schemaV1"), so
    the post-choice render MUST stay on the legacy layout too, while the
    interaction is still carrying that message.
    """
    interaction = session.active_interaction
    if interaction is None:
        return False
    if interaction.kind not in {"approval", "clarify"}:
        return False
    return _normalize_interaction_mode(interaction_mode) == "callback"


def _render_legacy_callback_card(
    session: CardSession,
    *,
    header: Mapping[str, Any],
    profile_id: str,
    mentions_enabled: bool = True,
) -> Dict[str, Any]:
    """Render an interaction card in the legacy (schema-V1) layout.

    CardKit v2 ``behaviors`` callbacks are client-side interactions and do not
    reach Hermes' ``p2.card.action.trigger`` WebSocket handler.  Conversely,
    the legacy ``action`` container is rejected when embedded in a schema-2.0
    card.  An interaction therefore uses one ordinary legacy card, and stays
    on the legacy layout for any in-place update on the same message (Feishu
    rejects a schema-2.0 card onto a schema-V1 message: 230099/200830
    "schemaV2 card can not change schemaV1").
    """
    interaction = session.active_interaction
    if interaction is None:  # Defensive: caller already checked the state.
        return {}

    elements: list[Dict[str, Any]] = []
    mention = _interaction_mention_content(
        session,
        interaction,
        mentions_enabled=mentions_enabled,
    )
    description = normalize_stream_text(interaction.description).strip()
    if description:
        elements.append({"tag": "markdown", "content": description})

    if interaction.status == "completed":
        choice = interaction.choice_label or interaction.choice or "已完成"
        user = f" by {interaction.user_name}" if interaction.user_name else ""
        elements.extend(
            [
                {"tag": "markdown", "content": f"已选择：{choice}{user}"},
                {"tag": "hr"},
                {"tag": "markdown", "content": "✅ 选择已确认"},
            ]
        )
    elif interaction.status == "failed":
        error_text = interaction.error or "交互请求失败"
        elements.extend(
            [
                {"tag": "markdown", "content": error_text},
                {"tag": "hr"},
                {"tag": "markdown", "content": "❌ 交互已失效"},
            ]
        )
    elif interaction.multi_select:
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

    The @ mention of the requester (approval) / user being clarified is
    returned as a bare ``<at id=...>`` prefix (no trailing text) so callers
    can merge it into the interaction hint line instead of rendering a
    separate mention row. Only pending approval/clarify interactions are
    mentioned, only when the per-kind mention flag is enabled, and only
    when the session carries a valid Feishu open_id for the requester.
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
        return elements

    if interaction.status == "pending":
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
            hint = "请选择一个选项"
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
        original_hover = _render_interaction_original_hover(interaction, content)
        if original_hover is not None:
            elements.append(original_hover)
        else:
            elements.append(
                {
                    "tag": "markdown",
                    "element_id": "interaction_result",
                    "content": content,
                }
            )
        return elements

    content = interaction.error or "交互请求失败"
    elements.append(
        {
            "tag": "markdown",
            "element_id": "interaction_result",
            "content": content,
        }
    )
    return elements


_HOVER_ORDINALS = ("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩")


def _render_interaction_original_hover(
    interaction: Any,
    content: str,
) -> Dict[str, Any] | None:
    lines: list[str] = []
    question = _hover_plain_line(interaction.prompt or interaction.description)
    if question:
        lines.append(f"❓ {question}")
    option_texts: list[str] = []
    for index, option in enumerate(interaction.options or [], start=1):
        label = _hover_plain_line(getattr(option, "label", ""))
        if not label:
            continue
        ordinal = (
            _HOVER_ORDINALS[index - 1]
            if index <= len(_HOVER_ORDINALS)
            else f"{index}."
        )
        option_texts.append(f"{ordinal} {label}")
    if option_texts:
        lines.append("📋 " + "  ".join(option_texts))
    if not lines:
        return None
    tooltip = "\n".join(lines)
    if len(tooltip) > 500:
        tooltip = tooltip[:497].rstrip() + "…"
    return {
        "tag": "button",
        "element_id": "interaction_hover",
        "type": "text",
        "size": "small",
        "text": {"tag": "plain_text", "content": content},
        "hover_tips": {"tag": "plain_text", "content": tooltip},
    }


def _hover_plain_line(text: Any) -> str:
    return " ".join(normalize_stream_text(str(text or "")).strip().split())


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
            "content": f"{index + 1}. {option.label}",
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
                "content": f"{index}. {option.label}",
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
        empty_content = (
            '<font color="grey">等待工具事件…</font>'
            if _is_initial_loading(session)
            else '<font color="grey">暂无可展示的思考或工具记录。</font>'
        )
        panel_elements = _timeline_markdown_elements(
            empty_content,
            "auxiliary_timeline_loading",
            text_size=_role_text_size(
                text_sizes,
                "tool",
                default="x-small",
                used_roles=used_text_size_roles,
            ),
        )
        return [_timeline_panel(session, panel_elements, expanded=expanded)]
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
    if not panel_elements:
        return []
    return [_timeline_panel(session, panel_elements, expanded=expanded)]


def _timeline_panel(
    session: CardSession,
    elements: list[Dict[str, Any]],
    *,
    expanded: bool,
) -> Dict[str, Any]:
    return {
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


def _render_tool_timeline_row(
    title: str,
    status: str,
    detail: str,
    duration: str,
) -> str:
    normalized_status = str(status or "running").strip().lower()
    safe_title = html.escape(str(title or "工具"), quote=False)
    duration_suffix = f" · {duration}" if duration else ""
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
        headline = f"✓ **{safe_title}**{duration_suffix}"
    elif normalized_status in {"failed", "error", "失败", "已失败", "错误"}:
        color = "red"
        headline = f"✕ **{safe_title}**{duration_suffix} · 失败"
    elif normalized_status in {"cancelled", "canceled", "已取消", "取消"}:
        color = "grey"
        headline = f"⊘ **{safe_title}**{duration_suffix} · 已取消"
    elif normalized_status in {"queued", "waiting", "排队中", "等待中"}:
        color = "grey"
        headline = f"○ **{safe_title}**{duration_suffix} · 等待中"
    else:
        color = "blue"
        headline = f"{_spinner_frame()} **{safe_title}**{duration_suffix} · 进行中"
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
        color, headline = "blue", f"{_spinner_frame()} **{label}** · 进行中"
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
) -> str:
    if session.status == "failed" or display_status == "failed":
        return "已停止"
    if display_status == "waiting":
        interaction = session.active_interaction
        remaining_seconds = (
            max(0.0, float(interaction.expires_at) - _time.time())
            if interaction is not None
            else 300.0
        )
        minutes = max(1, int(math.ceil(remaining_seconds / 60.0)))
        return f"等待选择 · ⏳ {minutes} 分钟后过期"
    if session.status != "completed" and display_status != "completed":
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
