from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional, Sequence

from .text import normalize_stream_text

if TYPE_CHECKING:
    from .session import CardSession


DISPLAY_STATUSES = frozenset(
    {"thinking", "in_progress", "waiting", "completed", "failed"}
)

DEFAULT_ACTIVE_MARKERS = (
    "正在收集",
    "数据收集中",
    "资料收集中",
    "处理中",
    "working on",
    "gathering",
    "collecting",
)
DEFAULT_FUTURE_MARKERS = (
    "到位后",
    "完成后我会继续",
    "我会继续",
    "will continue",
    "once available",
    "when it arrives",
    "when ready",
)


@dataclass(frozen=True)
class StatusConfig:
    active_markers: tuple[str, ...]
    future_markers: tuple[str, ...]

    @classmethod
    def defaults(cls) -> "StatusConfig":
        return cls(
            active_markers=DEFAULT_ACTIVE_MARKERS,
            future_markers=DEFAULT_FUTURE_MARKERS,
        )

    @classmethod
    def from_mapping(cls, value: Optional[Mapping[str, object]]) -> "StatusConfig":
        defaults = cls.defaults()
        if not isinstance(value, Mapping):
            return defaults
        return cls(
            active_markers=_normalize_markers(
                value.get("active_markers"), defaults.active_markers
            ),
            future_markers=_normalize_markers(
                value.get("future_markers"), defaults.future_markers
            ),
        )


@dataclass(frozen=True)
class DisplayStatus:
    value: str
    source: str


def normalize_display_status(value: object) -> str:
    if isinstance(value, str) and value in DISPLAY_STATUSES:
        return value
    return ""


def infer_progress_handoff(answer: str, config: StatusConfig) -> bool:
    text = normalize_stream_text(answer).strip().lower()
    return bool(text) and any(marker in text for marker in config.active_markers) and any(
        marker in text for marker in config.future_markers
    )


def resolve_display_status(session: "CardSession", config: StatusConfig) -> DisplayStatus:
    explicit = normalize_display_status(getattr(session, "display_status", ""))
    if explicit:
        return DisplayStatus(explicit, "explicit")

    if session.status == "failed":
        return DisplayStatus("failed", "session")

    interaction = session.active_interaction
    if interaction is not None and interaction.status in {"pending", "paused"}:
        return DisplayStatus("waiting", "session")

    if session.status == "completed" and infer_progress_handoff(session.answer_text, config):
        return DisplayStatus("in_progress", "inferred")

    session_status = normalize_display_status(session.status)
    if session_status and session_status != "thinking":
        return DisplayStatus(session_status, "session")
    if normalize_stream_text(session.answer_text).strip():
        return DisplayStatus("in_progress", "session")
    return DisplayStatus("thinking", "session")


def _normalize_markers(
    value: object, defaults: tuple[str, ...]
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return defaults
    markers = tuple(
        marker.strip().lower()
        for marker in value
        if isinstance(marker, str) and marker.strip()
    )
    return markers or defaults
