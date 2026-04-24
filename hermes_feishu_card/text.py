from __future__ import annotations

import re

THINK_TAG_RE = re.compile(r"</?think>", re.IGNORECASE)
SENTENCE_END_RE = re.compile(r"[。！？!?\.]$")


def normalize_stream_text(text: str) -> str:
    """移除模型 thinking 标签，保留用户可读内容。"""
    return THINK_TAG_RE.sub("", text or "")


def should_flush_text(
    buffer: str,
    *,
    elapsed_ms: int,
    max_wait_ms: int,
    max_chars: int,
    force: bool = False,
) -> bool:
    if force:
        return True
    if not buffer:
        return False
    if len(buffer) >= max_chars:
        return True
    if elapsed_ms >= max_wait_ms:
        return True
    if buffer.endswith(("\n", "\r\n")):
        return True
    return bool(SENTENCE_END_RE.search(buffer.rstrip()))
