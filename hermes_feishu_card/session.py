from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from .events import SidecarEvent
from .text import StreamingTextNormalizer, normalize_stream_text


@dataclass
class ToolState:
    tool_id: str
    name: str
    status: str
    detail: str = ""


@dataclass
class CardSession:
    conversation_id: str
    message_id: str
    chat_id: str
    status: str = "thinking"
    last_sequence: int = -1
    thinking_text: str = ""
    answer_text: str = ""
    tools: Dict[str, ToolState] = field(default_factory=dict)
    tokens: Dict[str, Any] = field(default_factory=dict)
    model: str = "Unknown"
    context: Dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0
    attachments: list[dict[str, str]] = field(default_factory=list)
    delivery_kind: str = "chat"
    reply_to_message_id: str = ""
    _tool_call_count: int = field(default=0)
    thinking_normalizer: StreamingTextNormalizer = field(default_factory=StreamingTextNormalizer)
    answer_normalizer: StreamingTextNormalizer = field(default_factory=StreamingTextNormalizer)
    _had_delta: bool = field(default=False)

    @property
    def tool_count(self) -> int:
        return self._tool_call_count

    @property
    def visible_main_text(self) -> str:
        if self.status in {"completed", "failed"}:
            return self.answer_text
        if self.answer_text:
            return self.answer_text
        return self.thinking_text

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

        if event.event == "thinking.delta":
            self.thinking_text += self.thinking_normalizer.feed(str(event.data.get("text", "")))
        elif event.event == "answer.delta":
            self.answer_text += self.answer_normalizer.feed(str(event.data.get("text", "")))
            self._had_delta = True
        elif event.event == "tool.updated":
            tool_id = event.data.get("tool_id")
            if not isinstance(tool_id, str) or not tool_id:
                return True
            name = event.data.get("name")
            status = event.data.get("status")
            detail = event.data.get("detail")
            self.tools[tool_id] = ToolState(
                tool_id=tool_id,
                name=name if isinstance(name, str) else tool_id,
                status=status if isinstance(status, str) else "running",
                detail=detail if isinstance(detail, str) else "",
            )
            self._tool_call_count += 1
        elif event.event == "message.started":
            delivery_kind = event.data.get("delivery_kind")
            if isinstance(delivery_kind, str) and delivery_kind.strip():
                self.delivery_kind = delivery_kind.strip()
            reply_to_message_id = event.data.get("reply_to_message_id")
            if isinstance(reply_to_message_id, str):
                self.reply_to_message_id = reply_to_message_id
        elif event.event == "message.completed":
            self.status = "completed"
            # Use streamed text from deltas, but fall back to completed answer
            # if it's significantly longer (handles partial streaming)
            completed_answer = normalize_stream_text(str(event.data.get("answer") or ""))
            if completed_answer and len(completed_answer) > len(self.answer_text):
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
            self.status = "failed"
            error = event.data.get("error")
            self.answer_text = error if isinstance(error, str) else "消息处理失败"
        return True
