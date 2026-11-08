"""Terminal over-limit segmentation planning (issue #511).

Pure text planning for the opt-in ``card.over_limit: segmented`` mode: split a
terminal answer into a bounded lead for the main card plus continuation
segments that each respect the serialized card budgets. Card assembly and
limit verification stay in ``render``/``card_limits``; this module only decides
where to cut, on block boundaries that never break Markdown tables or fenced
code. When the text cannot be split within the budgets, planning returns
``None`` and the caller keeps the existing native limit handoff.
"""
from __future__ import annotations

from dataclasses import dataclass

from .card_limits import FEISHU_MAX_TABLES, SAFE_CARD_JSON_BYTES
from .text import MarkdownBlock, scan_markdown_blocks, split_markdown_blocks

# Segmented delivery is a bounded continuation of the process record, not a
# channel dump of the full body: a few cards cover the "one card is not
# enough, a document is too heavy" middle band, and anything beyond the cap
# falls back to the native handoff so longer content keeps its dedicated
# document delivery instead of a wall of cards.
MAX_SEGMENT_CARDS = 5
# Fixed continuation-card chrome (header, footer, element scaffolding) kept
# below the real serialized overhead; every planned card is still fully
# verified by ``inspect_card_limits`` before the disposition commits.
SEGMENT_CHROME_BYTES = 2_000
DEFAULT_LEAD_BYTES = 8_000
# UTF-8 needs at most 4 bytes per character, so a character budget of
# bytes // 4 can never overrun the byte budget before verification.
_MAX_UTF8_BYTES_PER_CHAR = 4


@dataclass(frozen=True)
class TerminalTextSegments:
    lead_text: str
    segments: tuple[str, ...]

    @property
    def total_cards(self) -> int:
        return 1 + len(self.segments)


def _utf8_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def _table_count(block: MarkdownBlock) -> int:
    return 1 if block.kind == "table" else 0


def _split_oversized_blocks(
    blocks: list[MarkdownBlock], *, segment_bytes: int
) -> list[MarkdownBlock] | None:
    """Pre-split blocks that alone exceed one segment, or None if impossible.

    ``split_markdown_blocks`` repeats table headers and completes fences, so a
    piece never breaks structure. A piece that still exceeds the budget (an
    unbreakable single token, for example) makes the whole plan infeasible.
    """
    char_budget = max(1, segment_bytes // _MAX_UTF8_BYTES_PER_CHAR)
    pieces: list[MarkdownBlock] = []
    for block in blocks:
        if _utf8_bytes(block.text) <= segment_bytes:
            pieces.append(block)
            continue
        for chunk in split_markdown_blocks(block.text, char_budget):
            if _utf8_bytes(chunk) > segment_bytes:
                return None
            # Re-scan so split tables stay table blocks with repeated headers.
            for piece in scan_markdown_blocks(chunk):
                if piece.text:
                    pieces.append(piece)
    return pieces


def _pack_lead(
    pieces: list[MarkdownBlock], *, byte_budget: int, table_budget: int
) -> tuple[str, list[MarkdownBlock]]:
    """Take the maximal fitting prefix of pieces as the lead text.

    The lead may be empty when the answer opens with a structure larger than
    the main-card budget; the opener then travels on the first continuation
    card instead of overflowing the main card.
    """
    lead: list[str] = []
    lead_bytes = 0
    lead_tables = 0
    index = 0
    for piece in pieces:
        piece_bytes = _utf8_bytes(piece.text)
        piece_tables = _table_count(piece)
        if lead_bytes + piece_bytes > byte_budget or lead_tables + piece_tables > table_budget:
            break
        lead.append(piece.text)
        lead_bytes += piece_bytes
        lead_tables += piece_tables
        index += 1
    return "".join(lead), pieces[index:]


def _pack_segments(
    pieces: list[MarkdownBlock], *, byte_budget: int, table_budget: int
) -> list[str]:
    """Greedily pack pieces in order; every piece already fits one segment."""
    segments: list[str] = []
    current: list[str] = []
    current_bytes = 0
    current_tables = 0
    for piece in pieces:
        piece_bytes = _utf8_bytes(piece.text)
        piece_tables = _table_count(piece)
        byte_overflow = current_bytes + piece_bytes > byte_budget
        table_overflow = current_tables + piece_tables > table_budget
        if current and (byte_overflow or table_overflow):
            segments.append("".join(current))
            current = [piece.text]
            current_bytes = piece_bytes
            current_tables = piece_tables
            continue
        current.append(piece.text)
        current_bytes += piece_bytes
        current_tables += piece_tables
    if current:
        segments.append("".join(current))
    return segments


def plan_terminal_segments(
    primary_text: str,
    *,
    lead_bytes: int = DEFAULT_LEAD_BYTES,
    segment_bytes: int = SAFE_CARD_JSON_BYTES - SEGMENT_CHROME_BYTES,
    max_cards: int = MAX_SEGMENT_CARDS,
) -> TerminalTextSegments | None:
    """Plan the lead/continuation split, or None when it cannot stay in budget.

    The lead keeps whole block boundaries. Continuations pack whole pieces and
    never exceed the byte or per-card table budgets. Failing the card-count cap
    returns None: content too large for a few continuation cards keeps the
    native limit handoff, where a dedicated document/native delivery can carry
    the full body instead of flooding the chat with cards.
    """
    blocks = [block for block in scan_markdown_blocks(primary_text) if block.text]
    if not blocks:
        return None
    pieces = _split_oversized_blocks(blocks, segment_bytes=segment_bytes)
    if pieces is None:
        return None
    lead_text, remaining = _pack_lead(
        pieces, byte_budget=lead_bytes, table_budget=FEISHU_MAX_TABLES
    )
    segments = _pack_segments(
        remaining, byte_budget=segment_bytes, table_budget=FEISHU_MAX_TABLES
    )
    if not segments:
        return None
    if 1 + len(segments) > max_cards:
        return None
    return TerminalTextSegments(lead_text=lead_text, segments=tuple(segments))
