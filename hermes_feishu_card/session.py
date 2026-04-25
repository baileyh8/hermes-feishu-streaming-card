from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from .events import SidecarEvent
from .text import normalize_stream_text


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
    duration: float = 0.0

    @property
    def tool_count(self) -> int:
        return len(self.tools)

    @property
    def visible_main_text(self) -> str:
        if self.status == "completed":
            return self.answer_text
        return self.thinking_text

    def apply(self, event: SidecarEvent) -> bool:
        if event.message_id != self.message_id:
            return False
        if event.sequence <= self.last_sequence:
            return False
        self.last_sequence = event.sequence

        if event.event == "thinking.delta":
            self.thinking_text += normalize_stream_text(str(event.data.get("text", "")))
        elif event.event == "answer.delta":
            self.answer_text += normalize_stream_text(str(event.data.get("text", "")))
        elif event.event == "tool.updated":
            tool_id = str(event.data.get("tool_id") or event.data.get("name") or f"tool-{self.tool_count + 1}")
            self.tools[tool_id] = ToolState(
                tool_id=tool_id,
                name=str(event.data.get("name", tool_id)),
                status=str(event.data.get("status", "running")),
                detail=str(event.data.get("detail", "")),
            )
        elif event.event == "message.completed":
            self.status = "completed"
            self.answer_text = normalize_stream_text(str(event.data.get("answer") or self.answer_text))
            self.tokens = dict(event.data.get("tokens", {}))
            self.duration = float(event.data.get("duration", 0.0))
        elif event.event == "message.failed":
            self.status = "failed"
            self.answer_text = str(event.data.get("error", "消息处理失败"))
        return True
