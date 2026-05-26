from __future__ import annotations

import re

THINK_TAG_RE = re.compile(r"</?think>|</?thinking>", re.IGNORECASE)
SENTENCE_END_RE = re.compile(r"[。！？!?\.]$")
THINK_TAGS = ("<think>", "</think>", "<thinking>", "</thinking>")

# Markdown structure detection for smart content splitting
TABLE_SEP_RE = re.compile(r"^\|[-: ]+\|$")
TABLE_ROW_RE = re.compile(r"^\|.*\|$")
FENCE_RE = re.compile(r"^```")
PARA_BREAK_RE = re.compile(r"\n\n+")


def normalize_stream_text(text: str) -> str:
    """移除模型 thinking 标签，保留用户可读内容。"""
    return THINK_TAG_RE.sub("", text or "")


class StreamingTextNormalizer:
    """Filter thinking tags that may be split across streaming chunks."""

    def __init__(self) -> None:
        self._pending = ""

    def feed(self, delta: str) -> str:
        text = self._pending + (delta or "")
        safe_text, self._pending = self._split_safe_text(text)
        return normalize_stream_text(safe_text)

    @staticmethod
    def _split_safe_text(text: str) -> tuple[str, str]:
        lower_text = text.lower()
        pending_len = 0

        for tag in THINK_TAGS:
            for prefix_len in range(1, len(tag)):
                if lower_text.endswith(tag[:prefix_len]):
                    pending_len = max(pending_len, prefix_len)

        if not pending_len:
            return text, ""
        return text[:-pending_len], text[-pending_len:]


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


def count_markdown_tables(text: str) -> int:
    """统计 Markdown 文本中的表格数量（以 | --- | 分隔行为标志）。"""
    return len(re.findall(r"^\|[-: ]+\|", text, re.MULTILINE))


MAX_CARD_TABLES = 5


# ── Smart content splitting ────────────────────────────────────────────────

def split_markdown_blocks(text: str, max_block_size: int) -> list[str]:
    """Split markdown text at structure boundaries, never inside tables or code blocks.

    Splitting strategy (in order of preference):

    1. Paragraph boundaries (double newlines) — cleanest split point.
    2. Line boundaries — only when a single paragraph exceeds *max_block_size*.
       Lines inside fenced code blocks or markdown tables are kept together
       to avoid breaking those structures across card elements.

    Returns a list of strings, each suitable for a Feishu card ``markdown`` element.
    """
    if not text:
        return [""]
    if len(text) <= max_block_size:
        return [text]

    blocks = _split_paragraphs(text)
    chunks: list[str] = []
    carry: list[str] = []
    carry_sz = 0

    def _flush() -> None:
        nonlocal carry, carry_sz
        if carry:
            chunks.append("".join(carry))
            carry = []
            carry_sz = 0

    for block in blocks:
        bsz = len(block)
        if bsz > max_block_size:
            _flush()
            for sub in _split_oversized_block(block, max_block_size):
                chunks.append(sub)
        elif carry_sz + bsz > max_block_size:
            _flush()
            carry.append(block)
            carry_sz = bsz
        else:
            carry.append(block)
            carry_sz += bsz

    _flush()
    return chunks


def _split_paragraphs(text: str) -> list[str]:
    """Split *text* at paragraph boundaries (2+ consecutive newlines)."""
    return [seg for seg in PARA_BREAK_RE.split(text) if seg]


def _split_oversized_block(block: str, max_size: int) -> list[str]:
    """Split a single long paragraph at line boundaries.

    Lines inside fenced code blocks (````` ``` ``) and markdown tables
    (pipe-delimited rows with a ``|---|`` separator) are never split.

    If an individual line exceeds *max_size*, it is further split at word
    boundaries (spaces) as a last resort.
    """
    lines = block.split("\n")
    chunks: list[str] = []
    carry: list[str] = []
    carry_sz = 0
    in_code = False
    in_table = False

    for i, line in enumerate(lines):
        line_w_nl = line + ("\n" if i < len(lines) - 1 else "")
        lsz = len(line_w_nl)

        # Track code-fence state
        if FENCE_RE.match(line):
            in_code = not in_code

        # Track table state
        if not in_code:
            if TABLE_SEP_RE.match(line):
                in_table = True
            elif in_table and (not TABLE_ROW_RE.match(line) or not line.strip()):
                in_table = False

        if in_code or in_table:
            # Never split here — keep the whole structure together
            carry.append(line_w_nl)
            carry_sz += lsz
            continue

        # If this single line alone exceeds max_size, split it word-wise
        if lsz > max_size and not carry:
            for sub_line in _split_long_line(line, max_size):
                chunks.append(sub_line)
            continue

        # Safe split point between lines
        if carry_sz + lsz > max_size and carry:
            chunks.append("".join(carry))
            carry = [line_w_nl]
            carry_sz = lsz
        else:
            carry.append(line_w_nl)
            carry_sz += lsz

    if carry:
        chunks.append("".join(carry))

    return chunks


def _split_long_line(line: str, max_size: int) -> list[str]:
    """Split a single long line at word boundaries."""
    result: list[str] = []
    while len(line) > max_size:
        # Find the last space within max_size
        split_at = line.rfind(" ", 0, max_size)
        if split_at <= 0:
            # No space found — hard split at max_size
            split_at = max_size
        result.append(line[:split_at].rstrip())
        line = line[split_at:].lstrip()
    if line:
        result.append(line)
    return result
