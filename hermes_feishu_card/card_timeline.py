from __future__ import annotations

from dataclasses import dataclass, field

TERMINAL_TOOL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "canceled",
    # The card also renders localized statuses (see _render_tool_timeline_row), so a Chinese
    # terminal status must count as terminal here too — otherwise a "已完成" tool is treated as
    # still running and the row gets labelled 执行中 / 已中断.
    "已完成",
    "完成",
    "成功",
    "已失败",
    "失败",
    "错误",
    "已取消",
    "取消",
}
TERMINAL_SUBAGENT_STATUSES = {
    "completed",
    "success",
    "succeeded",
    "failed",
    "error",
    "cancelled",
    "canceled",
    "timeout",
    "blocked",
    "interrupted",
}


@dataclass
class TimelineEntry:
    kind: str
    title: str
    status: str
    content: str = ""
    detail: str = ""
    tool_id: str = ""
    notice_id: str = ""
    subagent_id: str = ""


@dataclass
class CardTimeline:
    _entries: list[TimelineEntry] = field(default_factory=list)
    _open_reasoning_index: int | None = None
    _reasoning_count: int = 0
    _tool_entry_by_id: dict[str, int] = field(default_factory=dict)
    _notice_entry_by_id: dict[str, int] = field(default_factory=dict)
    _subagent_entry_by_id: dict[str, int] = field(default_factory=dict)

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
        self._reindex_entries()

    def record_answer_started(self) -> None:
        self._finish_open_reasoning()

    def record_tool(self, tool_id: str, name: str, status: str, detail: str = "", *, replace_terminal: bool = False) -> None:
        if not tool_id:
            return
        self._finish_open_reasoning()
        title = name or tool_id
        normalized_status = status or "running"
        if tool_id in self._tool_entry_by_id:
            entry = self._entries[self._tool_entry_by_id[tool_id]]
            if replace_terminal or str(entry.status or "").lower() not in TERMINAL_TOOL_STATUSES:
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

    def record_notice(
        self,
        notice_id: str,
        title: str,
        status: str,
        content: str,
    ) -> None:
        if not content and not title:
            return
        self._finish_open_reasoning()
        resolved_id = notice_id.strip() if isinstance(notice_id, str) else ""
        resolved_title = title.strip() if isinstance(title, str) and title.strip() else "运行提示"
        resolved_status = status.strip() if isinstance(status, str) and status.strip() else "info"
        if resolved_id and resolved_id in self._notice_entry_by_id:
            entry = self._entries[self._notice_entry_by_id[resolved_id]]
            entry.title = resolved_title
            entry.status = resolved_status
            entry.content = content
            return
        self._entries.append(
            TimelineEntry(
                kind="notice",
                title=resolved_title,
                status=resolved_status,
                content=content,
                notice_id=resolved_id,
            )
        )
        if resolved_id:
            self._notice_entry_by_id[resolved_id] = len(self._entries) - 1

    def record_subagent(
        self,
        child_id: str,
        role: str,
        status: str,
        detail: str = "",
    ) -> None:
        if not child_id:
            return
        self._finish_open_reasoning()
        title = role or "子代理"
        normalized_status = status or "running"
        index = self._subagent_entry_by_id.get(child_id)
        if index is not None:
            entry = self._entries[index]
            if str(entry.status or "").lower() in TERMINAL_SUBAGENT_STATUSES:
                return
            entry.title = title
            entry.status = normalized_status
            entry.detail = detail or entry.detail
            return
        self._entries.append(
            TimelineEntry(
                kind="subagent",
                title=title,
                status=normalized_status,
                detail=detail,
                subagent_id=child_id,
            )
        )
        self._subagent_entry_by_id[child_id] = len(self._entries) - 1

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

    def _reindex_entries(self) -> None:
        self._tool_entry_by_id = {
            entry.tool_id: index
            for index, entry in enumerate(self._entries)
            if entry.kind == "tool" and entry.tool_id
        }
        self._notice_entry_by_id = {
            entry.notice_id: index
            for index, entry in enumerate(self._entries)
            if entry.kind == "notice" and entry.notice_id
        }
        self._subagent_entry_by_id = {
            entry.subagent_id: index
            for index, entry in enumerate(self._entries)
            if entry.kind == "subagent" and entry.subagent_id
        }
