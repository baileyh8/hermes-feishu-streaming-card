from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from .card_timeline import CardTimeline
from .events import SidecarEvent
from .status import StatusConfig, resolve_display_status
from .text import StreamingTextNormalizer, normalize_stream_text


MIN_COMPLETED_SUFFIX_CHARS = 20
MIN_COMPLETED_SUFFIX_RATIO_DENOMINATOR = 5

_RUNTIME_ACTION_PREFIX_RE = re.compile(
    r"^(?:正在)?(?:读取|执行(?:终端)?|编辑|写入|搜索|查询|浏览|访问|打开)\s*[:：]?\s*",
    re.IGNORECASE,
)
_SEARCH_SITE_OPERATOR_RE = re.compile(r"(?:^|\s)site:\S+", re.IGNORECASE)


def _now() -> float:
    return time.time()


@dataclass
class ToolState:
    tool_id: str
    name: str
    status: str
    detail: str = ""


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
    choice: str = ""
    choice_label: str = ""
    user_name: str = ""
    error: str = ""


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
    tools: Dict[str, ToolState] = field(default_factory=dict)
    tokens: Dict[str, Any] = field(default_factory=dict)
    model: str = "Unknown"
    context: Dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0
    attachments: list[dict[str, str]] = field(default_factory=list)
    active_interaction: InteractionState | None = None
    delivery_kind: str = "chat"
    reply_to_message_id: str = ""
    notice_title: str = ""
    notice_level: str = "info"
    _tool_call_count: int = field(default=0)
    _has_seen_tool_event: bool = False
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

    def apply(self, event: SidecarEvent) -> bool:
        if (
            event.conversation_id != self.conversation_id
            or event.message_id != self.message_id
            or event.chat_id != self.chat_id
        ):
            return False
        is_terminal_event = event.event in {"message.completed", "message.failed"}
        if event.sequence <= self.last_sequence and not is_terminal_event:
            return False
        if self.status in {"completed", "failed"}:
            return False
        self.last_sequence = max(self.last_sequence, event.sequence)

        self.display_status = event.display_status
        self.display_status_source = "explicit" if event.display_status else "session"

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
            self._has_seen_tool_event = True
            name = event.data.get("name")
            status = event.data.get("status")
            resolved_name = name if isinstance(name, str) else tool_id
            resolved_status = status if isinstance(status, str) else "running"
            resolved_detail = _tool_detail_from_event_data(event.data)
            self.tools[tool_id] = ToolState(
                tool_id=tool_id,
                name=resolved_name,
                status=resolved_status,
                detail=resolved_detail,
            )
            self.timeline.record_tool(tool_id, resolved_name, resolved_status, resolved_detail)
            self._tool_call_count += 1
        elif event.event == "message.started":
            delivery_kind = event.data.get("delivery_kind")
            if isinstance(delivery_kind, str) and delivery_kind.strip():
                self.delivery_kind = delivery_kind.strip()
            reply_to_message_id = event.data.get("reply_to_message_id")
            if isinstance(reply_to_message_id, str):
                self.reply_to_message_id = reply_to_message_id
            elif event.message_id.startswith("om_"):
                self.reply_to_message_id = event.message_id
        elif event.event == "interaction.requested":
            self.active_interaction = _interaction_from_event_data(event.data)
        elif event.event == "interaction.completed":
            self._complete_interaction(event.data)
        elif event.event == "interaction.failed":
            self._fail_interaction(event.data)
        elif event.event == "system.notice":
            title = str(event.data.get("title") or "运行提示").strip() or "运行提示"
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
                self.status = "completed"
                self.updated_at = time.time()
                self.refresh_display_status_source()
                return True
            self.timeline.record_notice(notice_id, title, level, content)
        elif event.event == "message.completed":
            completed_answer = normalize_stream_text(str(event.data.get("answer") or ""))
            if completed_answer.strip():
                completed_answer = self._prepare_completed_answer(completed_answer)
            self.timeline.complete()
            self.status = "completed"
            self.latest_tool_preview = ""
            if completed_answer.strip():
                self.answer_text = completed_answer
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
            self._archive_current_answer_to_reasoning()
            return stripped

        if self._has_seen_tool_event and final.startswith(preface):
            stripped = _strip_preface_prefix(final, preface)
            # Only archive the preface when the remaining stripped content is
            # substantial — i.e. the preface was a short intro and the real
            # answer follows.  If stripped is tiny relative to final, the
            # "preface" IS the answer and should not be archived.  (#96)
            if stripped != final and _has_substantial_completed_suffix(
                final, stripped
            ):
                self._archive_current_answer_to_reasoning()
                return stripped
            return final

        if self._has_seen_tool_event:
            self._archive_current_answer_to_reasoning()

        return final

    def _complete_interaction(self, data: dict[str, Any]) -> None:
        interaction_id = str(data.get("interaction_id") or "").strip()
        if self.active_interaction is None or (
            interaction_id and interaction_id != self.active_interaction.interaction_id
        ):
            return
        self.active_interaction.status = "completed"
        self.active_interaction.choice = str(data.get("choice") or "").strip()
        self.active_interaction.choice_label = str(
            data.get("choice_label") or self.active_interaction.choice
        ).strip()
        self.active_interaction.user_name = str(data.get("user_name") or "").strip()

    def _fail_interaction(self, data: dict[str, Any]) -> None:
        interaction_id = str(data.get("interaction_id") or "").strip()
        if self.active_interaction is None or (
            interaction_id and interaction_id != self.active_interaction.interaction_id
        ):
            return
        self.active_interaction.status = "failed"
        self.active_interaction.error = str(data.get("error") or "交互请求失败").strip()


def _interaction_from_event_data(data: dict[str, Any]) -> InteractionState:
    interaction_id = str(data.get("interaction_id") or "").strip()
    if not interaction_id:
        interaction_id = secrets.token_hex(8)
    return InteractionState(
        interaction_id=interaction_id,
        kind=str(data.get("kind") or "choice").strip() or "choice",
        prompt=str(data.get("prompt") or "").strip(),
        description=str(data.get("description") or "").strip(),
        options=_interaction_options(data.get("options")),
        callback_token=str(data.get("callback_token") or secrets.token_urlsafe(16)),
    )


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


def _has_substantial_completed_suffix(final: str, stripped: str) -> bool:
    threshold = max(
        MIN_COMPLETED_SUFFIX_CHARS,
        len(final) // MIN_COMPLETED_SUFFIX_RATIO_DENOMINATOR,
    )
    return len(stripped) > threshold


def _strip_preface_prefix(final: str, preface: str) -> str:
    if not final.startswith(preface):
        return final
    tail = final[len(preface):].strip()
    if not tail:
        return final
    if tail.startswith("---"):
        tail = tail[3:].strip()
    return tail or final
