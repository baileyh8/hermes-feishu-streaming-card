from __future__ import annotations

from dataclasses import dataclass, field, replace
from copy import deepcopy
import json
import math
import re
import secrets
import time
from typing import Any, Dict, Optional
from types import MappingProxyType
from urllib.parse import urlsplit

from .card_timeline import CardTimeline, TERMINAL_TOOL_STATUSES
from .events import SidecarEvent
from .native_handoff import NativeHandoffRecord
from .status import StatusConfig, resolve_display_status
from .text import StreamingTextNormalizer, normalize_stream_text


MIN_COMPLETED_SUFFIX_CHARS = 20
MIN_COMPLETED_SUFFIX_RATIO_DENOMINATOR = 5
MIN_PRESERVED_STREAMED_ANSWER_CHARS = 64
MAX_SHORT_COMPLETION_POSTSCRIPT_CHARS = 240
MIN_STREAMED_ANSWER_TO_POSTSCRIPT_RATIO = 3

_RUNTIME_ACTION_PREFIX_RE = re.compile(
    r"^(?:正在)?(?:读取|执行(?:终端)?|编辑|写入|搜索|查询|浏览|访问|打开)\s*[:：]?\s*",
    re.IGNORECASE,
)
_SEARCH_SITE_OPERATOR_RE = re.compile(r"(?:^|\s)site:\S+", re.IGNORECASE)
_FEISHU_OPEN_ID_RE = re.compile(r"ou_[A-Za-z0-9_-]{1,128}")


def _now() -> float:
    return time.time()


def _truthy_flag(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


def _exact_feishu_open_id(value: object) -> str:
    return (
        value
        if type(value) is str and _FEISHU_OPEN_ID_RE.fullmatch(value)
        else ""
    )


@dataclass
class ToolState:
    tool_id: str
    name: str
    status: str
    detail: str = ""
    started_at: float | None = None


@dataclass
class InteractionOption:
    label: str
    value: str
    style: str = "default"


@dataclass
class InteractionState:
    interaction_id: str
    kind: str
    prompt: str
    description: str = ""
    status: str = "pending"
    options: list[InteractionOption] = field(default_factory=list)
    callback_token: str = ""
    multi_select: bool = False
    allow_custom_input: bool = False
    timeout_seconds: float = 300.0
    requested_at: float = field(default_factory=_now)
    choice: str = ""
    choice_label: str = ""
    user_name: str = ""
    error: str = ""
    runtime_admission: object | None = field(default=None, repr=False)
    runtime_turn_id: str = field(default="", repr=False)

    def __deepcopy__(self, memo: dict[int, object]) -> "InteractionState":
        admission = self.runtime_admission
        copied_admission = (
            MappingProxyType(deepcopy(dict(admission), memo))
            if admission is not None
            else None
        )
        copied = replace(
            self,
            options=deepcopy(self.options, memo),
            runtime_admission=copied_admission,
        )
        memo[id(self)] = copied
        return copied

    @property
    def expires_at(self) -> float:
        return self.requested_at + max(0.0, float(self.timeout_seconds))

    def is_expired(self, now: float | None = None) -> bool:
        checked_at = _now() if now is None else float(now)
        return self.status == "pending" and checked_at >= self.expires_at

    def expire(self, now: float | None = None) -> bool:
        checked_at = _now() if now is None else float(now)
        if not self.is_expired(checked_at):
            return False
        self.status = "failed"
        self.error = "交互已过期"
        self.runtime_admission = None
        return True


@dataclass
class CardSession:
    conversation_id: str
    message_id: str
    chat_id: str
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    status: str = "thinking"
    display_status: str = ""
    display_status_source: str = "session"
    last_sequence: int = -1
    thinking_text: str = ""
    answer_text: str = ""
    latest_tool_preview: str = ""
    runtime_phase_text: str = ""
    tools: Dict[str, ToolState] = field(default_factory=dict)
    tokens: Dict[str, Any] = field(default_factory=dict)
    model: str = "Unknown"
    provider: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0
    subscription_usage: str = ""
    subscription_usage_checked: bool = False
    attachments: list[dict[str, str]] = field(default_factory=list)
    active_interaction: InteractionState | None = None
    delivery_kind: str = "chat"
    reply_to_message_id: str = ""
    reply_in_thread: bool = False
    sender_open_id: str = ""
    completion_notify_state: str = "idle"
    notice_title: str = ""
    notice_level: str = "info"
    terminal_disposition: str = ""
    terminal_limit_reason: str = ""
    terminal_handoff_record: NativeHandoffRecord | None = field(
        default=None,
        repr=False,
    )
    _tool_call_count: int = field(default=0)
    _answer_archive_index: int | None = None
    timeline: CardTimeline = field(default_factory=CardTimeline)
    thinking_normalizer: StreamingTextNormalizer = field(default_factory=StreamingTextNormalizer)
    answer_normalizer: StreamingTextNormalizer = field(default_factory=StreamingTextNormalizer)

    @property
    def tool_count(self) -> int:
        return self._tool_call_count

    @property
    def runtime_header_text(self) -> str:
        interaction = self.active_interaction
        if interaction is not None and interaction.status == "pending":
            return normalize_stream_text(interaction.prompt).strip()
        if self.status == "completed":
            return ""
        if self.runtime_phase_text:
            return self.runtime_phase_text
        return self.latest_tool_preview

    @property
    def visible_main_text(self) -> str:
        if self.status in {"completed", "failed"}:
            return self.answer_text
        if self.answer_text:
            return self.answer_text
        return self.thinking_text

    def refresh_display_status_source(
        self, config: Optional[StatusConfig] = None
    ) -> None:
        resolved = resolve_display_status(self, config or StatusConfig.defaults())
        self.display_status_source = resolved.source

    def apply(
        self,
        event: SidecarEvent,
        *,
        advance_sequence: bool = True,
    ) -> bool:
        if (
            event.conversation_id != self.conversation_id
            or event.message_id != self.message_id
            or event.chat_id != self.chat_id
        ):
            return False
        is_terminal_event = _is_terminal_session_event(event)
        if event.sequence <= self.last_sequence and not is_terminal_event:
            return False
        if self.status in {"completed", "failed"}:
            return False
        # Card-action callbacks are authenticated out-of-band transitions. They
        # may complete an interaction while Hermes is already preparing the next
        # batch clarify request, so they must not consume the transport sequence
        # that the next gateway event will use.
        if advance_sequence:
            self.last_sequence = max(self.last_sequence, event.sequence)
        if _truthy_flag(event.data.get("reply_in_thread")):
            self.reply_in_thread = True

        self.display_status = event.display_status
        self.display_status_source = "explicit" if event.display_status else "session"
        if event.event in {
            "thinking.delta",
            "answer.delta",
            "tool.updated",
            "message.completed",
            "message.failed",
        }:
            self.runtime_phase_text = ""

        if event.event == "thinking.delta":
            mode = str(event.data.get("mode") or "delta").strip().lower()
            raw_text = str(event.data.get("text", ""))
            if mode == "replace":
                normalized = normalize_stream_text(raw_text)
                self.thinking_text = normalized
            elif mode == "append_block":
                text = normalize_stream_text(raw_text).strip()
                if text:
                    if self.thinking_text:
                        self.thinking_text = self.thinking_text.rstrip() + "\n\n" + text
                    else:
                        self.thinking_text = text
            else:
                delta = self.thinking_normalizer.feed(raw_text)
                if delta:
                    self.thinking_text += delta
        elif event.event == "answer.delta":
            delta = self.answer_normalizer.feed(str(event.data.get("text", "")))
            if delta:
                if self._answer_archive_index is not None:
                    self._archive_current_answer_to_reasoning()
                self.answer_text += delta
        elif event.event == "tool.updated":
            raw_preview = event.data.get("detail")
            if isinstance(raw_preview, str):
                normalized_preview = normalize_stream_text(raw_preview).strip()
                if normalized_preview:
                    self.latest_tool_preview = _runtime_tool_summary(
                        event.data.get("name"), normalized_preview
                    )
            tool_id = event.data.get("tool_id")
            if not isinstance(tool_id, str) or not tool_id:
                self.updated_at = time.time()
                self.refresh_display_status_source()
                return True
            if self.answer_text and self._answer_archive_index is None:
                self._answer_archive_index = self.timeline.entry_count
            name = event.data.get("name")
            status = event.data.get("status")
            resolved_name = name if isinstance(name, str) else tool_id
            resolved_status = status if isinstance(status, str) else "running"
            normalized_status = resolved_status.strip().lower()
            is_terminal = normalized_status in TERMINAL_TOOL_STATUSES
            previous_tool = self.tools.get(tool_id)
            previous_is_terminal = (
                previous_tool is not None
                and previous_tool.status.strip().lower() in TERMINAL_TOOL_STATUSES
            )
            if previous_tool is None or (previous_is_terminal and not is_terminal):
                started_at = None if is_terminal else event.created_at
            else:
                started_at = previous_tool.started_at
            detail_data = event.data
            if (
                is_terminal
                and _tool_duration_milliseconds(event.data) is None
                and started_at is not None
                and event.created_at >= started_at
            ):
                detail_data = dict(event.data)
                detail_data["duration_ms"] = (event.created_at - started_at) * 1000
            resolved_detail = _tool_detail_from_event_data(detail_data)
            if (
                is_terminal
                and previous_tool is not None
                and not _tool_event_has_primary_detail(event.data)
            ):
                resolved_detail = _merge_tool_details(
                    previous_tool.detail,
                    resolved_detail,
                )
            self.tools[tool_id] = ToolState(
                tool_id=tool_id,
                name=resolved_name,
                status=resolved_status,
                detail=resolved_detail,
                started_at=started_at,
            )
            self.timeline.record_tool(tool_id, resolved_name, resolved_status, resolved_detail)
            if previous_tool is None or previous_is_terminal:
                self._tool_call_count += 1
        elif event.event == "subagent.updated":
            child_id = event.data.get("child_id")
            if type(child_id) is str and child_id.strip():
                role = event.data.get("role")
                status = event.data.get("status")
                resolved_role = (
                    normalize_stream_text(role).strip()[:240]
                    if type(role) is str
                    else ""
                )
                resolved_status = (
                    status.strip().lower()[:64]
                    if type(status) is str and status.strip()
                    else "running"
                )
                detail_lines: list[str] = []
                preview_key = (
                    "summary_preview"
                    if type(event.data.get("summary_preview")) is str
                    else "goal_preview"
                )
                preview = event.data.get(preview_key)
                if type(preview) is str:
                    safe_preview = normalize_stream_text(preview).strip()[:240]
                    if safe_preview:
                        detail_lines.append(safe_preview)
                duration_ms = _subagent_duration_milliseconds(
                    event.data.get("duration_ms")
                )
                if duration_ms is not None:
                    detail_lines.append(f"耗时: {_duration_milliseconds_text(duration_ms)}")
                self.timeline.record_subagent(
                    child_id.strip(),
                    resolved_role,
                    resolved_status,
                    "\n".join(detail_lines),
                )
        elif event.event == "message.started":
            delivery_kind = event.data.get("delivery_kind")
            if isinstance(delivery_kind, str) and delivery_kind.strip():
                self.delivery_kind = delivery_kind.strip()
            sender_open_id = _exact_feishu_open_id(event.data.get("sender_open_id"))
            if sender_open_id:
                self.sender_open_id = sender_open_id
            reply_to_message_id = event.data.get("reply_to_message_id")
            if isinstance(reply_to_message_id, str):
                self.reply_to_message_id = reply_to_message_id
            elif event.message_id.startswith("om_"):
                self.reply_to_message_id = event.message_id
        elif event.event == "interaction.requested":
            self.active_interaction = _interaction_from_event_data(
                event.data, runtime_turn_id=event.turn_id
            )
        elif event.event == "interaction.completed":
            self._complete_interaction(event.data)
        elif event.event == "interaction.failed":
            self._fail_interaction(event.data)
        elif event.event == "system.notice":
            title = str(event.data.get("title") or "运行提示").strip() or "运行提示"
            is_runtime_phase = (
                str(event.data.get("notice_kind") or "") == "context-compaction"
                and str(event.data.get("phase") or "") == "started"
            )
            if is_runtime_phase:
                self.runtime_phase_text = title
            content = normalize_stream_text(
                str(event.data.get("content") or event.data.get("text") or "")
            ).strip()
            level = _notice_level(event.data.get("level"))
            notice_id = str(event.data.get("notice_id") or "").strip()
            scope = str(event.data.get("notice_scope") or "session").strip().lower()
            delivery_kind = event.data.get("delivery_kind")
            if isinstance(delivery_kind, str) and delivery_kind.strip():
                self.delivery_kind = delivery_kind.strip()
            reply_to_message_id = event.data.get("reply_to_message_id")
            if isinstance(reply_to_message_id, str):
                self.reply_to_message_id = reply_to_message_id
            if scope == "independent" or self.delivery_kind == "notice":
                self.delivery_kind = "notice"
                self.notice_title = title
                self.notice_level = level
                self.answer_text = content or title
                self.status = (
                    "completed"
                    if _notice_is_terminal(event.data.get("notice_terminal"))
                    else "running"
                )
                self.updated_at = time.time()
                self.refresh_display_status_source()
                return True
            if not is_runtime_phase:
                self.timeline.record_notice(notice_id, title, level, content)
        elif event.event == "message.completed":
            if self.active_interaction is not None:
                self.active_interaction.runtime_admission = None
            completed_answer = normalize_stream_text(str(event.data.get("answer") or ""))
            if completed_answer.strip():
                completed_answer = self._prepare_completed_answer(completed_answer)
            self.timeline.complete()
            self.status = "completed"
            self.latest_tool_preview = ""
            if completed_answer.strip():
                self.answer_text = completed_answer
            sender_open_id = _exact_feishu_open_id(event.data.get("sender_open_id"))
            if sender_open_id:
                self.sender_open_id = sender_open_id
            delivery_kind = event.data.get("delivery_kind")
            if isinstance(delivery_kind, str) and delivery_kind.strip():
                self.delivery_kind = delivery_kind.strip()
            reply_to_message_id = event.data.get("reply_to_message_id")
            if isinstance(reply_to_message_id, str):
                self.reply_to_message_id = reply_to_message_id
            tokens = event.data.get("tokens", {})
            self.tokens = dict(tokens) if isinstance(tokens, dict) else {}
            model = event.data.get("model")
            self.model = model if isinstance(model, str) and model.strip() else "Unknown"
            provider = event.data.get("provider")
            self.provider = provider.strip() if isinstance(provider, str) else ""
            context = event.data.get("context", {})
            self.context = dict(context) if isinstance(context, dict) else {}
            try:
                self.duration = float(event.data.get("duration", 0.0))
            except (TypeError, ValueError):
                self.duration = 0.0
            attachments = event.data.get("attachments", [])
            if isinstance(attachments, list):
                self.attachments = [
                    attachment
                    for attachment in attachments
                    if isinstance(attachment, dict) and isinstance(attachment.get("name"), str)
                ]
        elif event.event == "message.failed":
            if self.active_interaction is not None:
                self.active_interaction.runtime_admission = None
            self._archive_current_answer_to_reasoning()
            self.timeline.complete()
            self.status = "failed"
            error = event.data.get("error")
            self.answer_text = error if isinstance(error, str) else "消息处理失败"
        self.updated_at = time.time()
        self.refresh_display_status_source()
        return True

    def _archive_current_answer_to_reasoning(self, final_answer: str = "") -> None:
        preface = normalize_stream_text(self.answer_text).strip()
        if not preface:
            return
        final = normalize_stream_text(final_answer).strip()
        if final and (final == preface or final.startswith(preface)):
            return
        self.answer_text = ""
        self.answer_normalizer = StreamingTextNormalizer()
        self.timeline.insert_completed_reasoning(preface, self._answer_archive_index)
        self._answer_archive_index = None

    def _prepare_completed_answer(self, completed_answer: str) -> str:
        preface = normalize_stream_text(self.answer_text).strip()
        final = normalize_stream_text(completed_answer).strip()
        if not preface or final == preface:
            return final

        if self._answer_archive_index is not None:
            stripped = _strip_preface_prefix(final, preface)
            # Guard: if final merely extends preface by a tiny suffix (e.g.
            # trailing punctuation), the preface IS the answer — don't
            # archive it into reasoning.  (#96)
            if final.startswith(preface) and (
                stripped == final
                or not _has_substantial_completed_suffix(final, stripped)
            ):
                self._answer_archive_index = None
                return final
            if _short_completion_would_replace_substantive_answer(preface, final):
                self._answer_archive_index = None
                return f"{preface}\n\n---\n\n{final}"
            self._archive_current_answer_to_reasoning()
            return stripped

        return final

    def _complete_interaction(self, data: dict[str, Any]) -> None:
        interaction_id = str(data.get("interaction_id") or "").strip()
        if self.active_interaction is None or (
            interaction_id and interaction_id != self.active_interaction.interaction_id
        ):
            return
        if self.active_interaction.status != "pending":
            return
        self.active_interaction.status = "completed"
        self.active_interaction.choice = str(data.get("choice") or "").strip()
        self.active_interaction.choice_label = str(
            data.get("choice_label") or self.active_interaction.choice
        ).strip()
        self.active_interaction.user_name = str(data.get("user_name") or "").strip()
        self.active_interaction.runtime_admission = None

    def _fail_interaction(self, data: dict[str, Any]) -> None:
        interaction_id = str(data.get("interaction_id") or "").strip()
        if self.active_interaction is None or (
            interaction_id and interaction_id != self.active_interaction.interaction_id
        ):
            return
        if self.active_interaction.status != "pending":
            return
        self.active_interaction.status = "failed"
        self.active_interaction.error = str(data.get("error") or "交互请求失败").strip()
        self.active_interaction.runtime_admission = None


def _interaction_from_event_data(
    data: dict[str, Any], *, runtime_turn_id: str = ""
) -> InteractionState:
    interaction_id = str(data.get("interaction_id") or "").strip()
    if not interaction_id:
        interaction_id = secrets.token_hex(8)
    kind = str(data.get("kind") or "choice").strip() or "choice"
    allow_custom_input = data.get("allow_custom_input")
    if not isinstance(allow_custom_input, bool):
        # Backward compatibility for interaction events emitted before the
        # capability became explicit. Hermes clarify has always exposed an
        # Other/free-text path; fixed-choice interactions have not.
        allow_custom_input = kind == "clarify"
    runtime_admission = data.get("_hfc_runtime_admission")
    frozen_runtime_admission = (
        MappingProxyType(deepcopy(runtime_admission))
        if type(runtime_admission) is dict
        else None
    )
    return InteractionState(
        interaction_id=interaction_id,
        kind=kind,
        prompt=str(data.get("prompt") or "").strip(),
        description=str(data.get("description") or "").strip(),
        options=_interaction_options(data.get("options")),
        callback_token=str(data.get("callback_token") or secrets.token_urlsafe(16)),
        multi_select=bool(data.get("multi_select", False)),
        allow_custom_input=allow_custom_input,
        timeout_seconds=_safe_timeout_seconds(data.get("timeout_seconds")),
        runtime_admission=frozen_runtime_admission,
        runtime_turn_id=(
            runtime_turn_id
            if frozen_runtime_admission is not None and type(runtime_turn_id) is str
            else ""
        ),
    )


def _safe_timeout_seconds(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 300.0
    if not math.isfinite(parsed) or parsed < 0:
        return 300.0
    return parsed


def _interaction_options(value: Any) -> list[InteractionOption]:
    if not isinstance(value, list):
        return []
    options: list[InteractionOption] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("text") or item.get("value") or "").strip()
        option_value = str(item.get("value") or label or index).strip()
        if not label or not option_value:
            continue
        style = str(item.get("style") or item.get("type") or "default").strip() or "default"
        options.append(InteractionOption(label=label, value=option_value, style=style))
    return options


def _runtime_tool_summary(name: Any, preview: str) -> str:
    text = normalize_stream_text(preview).strip()
    if not text:
        return ""
    if text.startswith("正在"):
        return text

    tool_name = str(name or "").strip().lower()
    is_url = text.startswith(("http://", "https://"))
    is_search = bool(_SEARCH_SITE_OPERATOR_RE.search(text))

    if is_search or "search" in tool_name or "query" in tool_name:
        action = "正在搜索"
    elif is_url or any(
        marker in tool_name for marker in ("browser", "fetch", "web", "http")
    ):
        action = "正在浏览"
    elif any(
        marker in tool_name for marker in ("terminal", "shell", "exec", "command", "code")
    ):
        action = "正在执行终端"
    elif any(marker in tool_name for marker in ("write", "edit", "patch", "replace")):
        action = "正在编辑"
    elif any(marker in tool_name for marker in ("read", "open", "list", "glob")):
        action = "正在读取"
    else:
        readable_name = tool_name.replace("_", " ").strip() or "工具"
        return f"正在使用 {readable_name}"

    target = _runtime_preview_target(text, action=action, is_url=is_url)
    return f"{action}：{target}" if target else action


def _runtime_preview_target(text: str, *, action: str, is_url: bool) -> str:
    if is_url:
        parsed = urlsplit(text)
        host = parsed.netloc.removeprefix("www.")
        path = parsed.path.rstrip("/")
        return f"{host}{path}" if host else ""

    target = _RUNTIME_ACTION_PREFIX_RE.sub("", text).strip()
    if action == "正在搜索":
        target = _SEARCH_SITE_OPERATOR_RE.sub("", target).strip()
        target = " ".join(target.split())
    if action in {"正在读取", "正在编辑"} and target.startswith(("/", "~/")):
        path = target.split(maxsplit=1)[0]
        target = path.rstrip("/").rsplit("/", 1)[-1]
    if target.lower().startswith(("参数:", "参数：", "args:", "arguments:")):
        return ""
    return target


def _tool_detail_from_event_data(data: dict[str, Any]) -> str:
    lines: list[str] = []
    detail = data.get("detail")
    if isinstance(detail, str) and detail.strip():
        lines.append(normalize_stream_text(detail).strip())

    arguments = _first_tool_value(data, ("arguments", "parameters", "args", "input"))
    if arguments is not None:
        rendered = _compact_tool_value(arguments)
        if rendered:
            lines.append(f"参数: {rendered}")

    duration = _tool_duration_text(data)
    if duration:
        lines.append(f"耗时: {duration}")

    error = _first_tool_value(data, ("error", "error_message", "failure_reason"))
    if error is not None:
        rendered_error = normalize_stream_text(str(error)).strip()
        if rendered_error:
            lines.append(f"失败: {rendered_error}")

    return "\n".join(lines)


def _tool_event_has_primary_detail(data: dict[str, Any]) -> bool:
    detail = data.get("detail")
    if isinstance(detail, str) and detail.strip():
        return True
    return _first_tool_value(data, ("arguments", "parameters", "args", "input")) is not None


def _merge_tool_details(previous: str, current: str) -> str:
    lines: list[str] = []
    for detail in (previous, current):
        for line in str(detail or "").splitlines():
            if line and line not in lines:
                lines.append(line)
    return "\n".join(lines)


def _first_tool_value(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name not in data:
            continue
        value = data.get(name)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _compact_tool_value(value: Any) -> str:
    if isinstance(value, str):
        return normalize_stream_text(value).strip()
    try:
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    except (TypeError, ValueError):
        return normalize_stream_text(str(value)).strip()


def _tool_duration_text(data: dict[str, Any]) -> str:
    milliseconds = _tool_duration_milliseconds(data)
    if milliseconds is None:
        return ""
    if milliseconds < 1000:
        return f"{int(round(milliseconds))}ms"
    seconds = milliseconds / 1000.0
    return f"{seconds:.2f}".rstrip("0").rstrip(".") + "s"


def _tool_duration_milliseconds(data: dict[str, Any]) -> float | None:
    for name in ("duration_ms", "elapsed_ms", "tool_duration_ms"):
        try:
            value = float(data.get(name))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    for name in ("duration", "elapsed", "tool_duration"):
        try:
            value = float(data.get(name))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value * 1000
    return None


def _subagent_duration_milliseconds(value: Any) -> float | None:
    if type(value) not in {int, float}:
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _duration_milliseconds_text(milliseconds: float) -> str:
    if milliseconds < 1000:
        return f"{int(round(milliseconds))} ms"
    seconds = milliseconds / 1000.0
    return f"{seconds:.2f}".rstrip("0").rstrip(".") + " s"


def _notice_level(value: Any) -> str:
    level = str(value or "info").strip().lower()
    if level in {"success", "warning", "error", "info"}:
        return level
    if level in {"warn", "orange"}:
        return "warning"
    if level in {"failed", "danger", "red"}:
        return "error"
    if level in {"ok", "done", "green"}:
        return "success"
    return "info"


def _notice_is_terminal(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return True


def _is_terminal_session_event(event: SidecarEvent) -> bool:
    if event.event in {"message.completed", "message.failed"}:
        return True
    if event.event != "system.notice":
        return False
    scope = str(event.data.get("notice_scope") or "session").strip().lower()
    delivery_kind = str(event.data.get("delivery_kind") or "").strip().lower()
    return (
        scope == "independent" or delivery_kind == "notice"
    ) and _notice_is_terminal(event.data.get("notice_terminal"))


def _has_substantial_completed_suffix(final: str, stripped: str) -> bool:
    threshold = max(
        MIN_COMPLETED_SUFFIX_CHARS,
        len(final) // MIN_COMPLETED_SUFFIX_RATIO_DENOMINATOR,
    )
    return len(stripped) > threshold


def _short_completion_would_replace_substantive_answer(
    streamed_answer: str,
    completed_answer: str,
) -> bool:
    """Keep a long streamed answer visible when completion is a short postscript.

    This is intentionally content-agnostic: validators and custom skills can
    append many different status messages, so recognizing private marker text
    would be brittle.  The size and ratio bounds preserve an actual answer while
    still letting a normal authoritative completion replace a brief preface.
    """
    streamed_length = len(streamed_answer.strip())
    completed_length = len(completed_answer.strip())
    return (
        streamed_length >= MIN_PRESERVED_STREAMED_ANSWER_CHARS
        and 0 < completed_length <= MAX_SHORT_COMPLETION_POSTSCRIPT_CHARS
        and streamed_length
        >= completed_length * MIN_STREAMED_ANSWER_TO_POSTSCRIPT_RATIO
    )


def _strip_preface_prefix(final: str, preface: str) -> str:
    if not final.startswith(preface):
        return final
    tail = final[len(preface):].strip()
    if not tail:
        return final
    if tail.startswith("---"):
        tail = tail[3:].strip()
    return tail or final
