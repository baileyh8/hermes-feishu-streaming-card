from __future__ import annotations

from dataclasses import dataclass, field

TERMINAL_TOOL_STATUSES = {"completed", "failed", "cancelled", "canceled"}


@dataclass
class TimelineEntry:
    kind: str
    title: str
    status: str
    content: str = ""
    detail: str = ""
    tool_id: str = ""


@dataclass
class CardTimeline:
    _entries: list[TimelineEntry] = field(default_factory=list)
    _open_reasoning_index: int | None = None
    _reasoning_count: int = 0
    _tool_entry_by_id: dict[str, int] = field(default_factory=dict)

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def record_reasoning(self, text: str, replace: bool = False) -> None:
        if not text and not replace:
            return
        if replace and self._open_reasoning_index is not None:
            self._entries[self._open_reasoning_index].content = text
            return
        if not text:
            return
        if self._open_reasoning_index is None:
            self._reasoning_count += 1
            self._entries.append(
                TimelineEntry(
                    kind="reasoning",
                    title=f"思考 {self._reasoning_count}",
                    status="running",
                    content=text,
                )
            )
            self._open_reasoning_index = len(self._entries) - 1
            return
        self._entries[self._open_reasoning_index].content += text

    def insert_completed_reasoning(self, text: str, index: int | None = None) -> None:
        if not text:
            return
        self._finish_open_reasoning()
        self._reasoning_count += 1
        insert_at = len(self._entries) if index is None else max(0, min(index, len(self._entries)))
        self._entries.insert(
            insert_at,
            TimelineEntry(
                kind="reasoning",
                title=f"思考 {self._reasoning_count}",
                status="completed",
                content=text,
            ),
        )
        self._reindex_tools()

    def record_answer_started(self) -> None:
        self._finish_open_reasoning()

    def record_tool(self, tool_id: str, name: str, status: str, detail: str = "") -> None:
        if not tool_id:
            return
        self._finish_open_reasoning()
        title = name or tool_id
        normalized_status = status or "running"
        if tool_id in self._tool_entry_by_id:
            entry = self._entries[self._tool_entry_by_id[tool_id]]
            if str(entry.status or "").lower() not in TERMINAL_TOOL_STATUSES:
                entry.title = title
                entry.status = normalized_status
                entry.detail = detail or entry.detail
                return
        self._entries.append(
            TimelineEntry(
                kind="tool",
                title=title,
                status=normalized_status,
                detail=detail,
                tool_id=tool_id,
            )
        )
        self._tool_entry_by_id[tool_id] = len(self._entries) - 1

    def complete(self) -> None:
        self._finish_open_reasoning()

    def snapshot(self, max_items: int | None = None) -> list[TimelineEntry]:
        if max_items is None or max_items <= 0 or len(self._entries) <= max_items:
            return list(self._entries)
        return list(self._entries[-max_items:])

    def folded_count(self, max_items: int | None = None) -> int:
        if max_items is None or max_items <= 0:
            return 0
        return max(0, len(self._entries) - max_items)

    def _finish_open_reasoning(self) -> None:
        if self._open_reasoning_index is None:
            return
        self._entries[self._open_reasoning_index].status = "completed"
        self._open_reasoning_index = None

    def _reindex_tools(self) -> None:
        self._tool_entry_by_id = {
            entry.tool_id: index
            for index, entry in enumerate(self._entries)
            if entry.kind == "tool" and entry.tool_id
        }
